"""DISC1 P2: immutable forum posts, versions and independently recorded moderation."""
from importlib import import_module
from typing import ClassVar

from django.db import migrations

boards = import_module("core.migrations.0072_discussion_boards")
TABLES = ("core_discpostact", "core_discthread", "core_discpost", "core_discpostversion", "core_discbody")
FORWARD = r"""
CREATE TABLE public.core_discpostact (
 act_uuid uuid PRIMARY KEY REFERENCES public.core_livingorganismevent(event_uuid) DEFERRABLE INITIALLY DEFERRED,
 board_uuid uuid NOT NULL REFERENCES public.core_discboard,
 operation text NOT NULL CHECK(operation IN ('START','REPLY','EDIT','PIN','UNPIN','CLOSE','REOPEN','HIDE','RESTORE')),
 actor_id bigint NOT NULL REFERENCES public.core_identity, actor_epoch bigint NOT NULL,
 authority_source jsonb NOT NULL, payload jsonb NOT NULL, fingerprint text NOT NULL,
 received_at timestamptz NOT NULL, effective_at timestamptz NOT NULL, recorded_at timestamptz NOT NULL,
 execution_xid xid8 NOT NULL,
 author_hidden boolean, coordinator_hidden boolean, pinned boolean, closed boolean
);
CREATE TABLE public.core_discthread (
 thread_uuid uuid PRIMARY KEY, category_uuid uuid NOT NULL REFERENCES public.core_disccategory,
 creation_act uuid NOT NULL UNIQUE REFERENCES public.core_discpostact,
 topic_type text NOT NULL CHECK(topic_type IN ('TOPIC','QUESTION','SUGGESTION')),
 title text NOT NULL CHECK(length(title) BETWEEN 1 AND 90 AND btrim(title)<>''),
 record_kind text NOT NULL CHECK(record_kind='ORDINARY')
);
CREATE TABLE public.core_discpost (
 post_uuid uuid PRIMARY KEY, thread_uuid uuid NOT NULL REFERENCES public.core_discthread,
 creation_act uuid NOT NULL UNIQUE REFERENCES public.core_discpostact,
 author_id bigint NOT NULL REFERENCES public.core_identity,
 post_number integer NOT NULL CHECK(post_number>0), UNIQUE(thread_uuid,post_number)
);
CREATE TABLE public.core_discpostversion (
 version_uuid uuid PRIMARY KEY, post_uuid uuid NOT NULL REFERENCES public.core_discpost,
 act_uuid uuid NOT NULL UNIQUE REFERENCES public.core_discpostact,
 version_number integer NOT NULL CHECK(version_number>0),
 predecessor uuid UNIQUE REFERENCES public.core_discpostversion,
 body_fingerprint text NOT NULL, quote_version uuid REFERENCES public.core_discpostversion,
 UNIQUE(post_uuid,version_number)
);
CREATE TABLE public.core_discbody (
 version_uuid uuid PRIMARY KEY REFERENCES public.core_discpostversion,
 markdown text NOT NULL
);
CREATE INDEX discp_act_post ON public.core_discpostact ((payload->>'post'));
CREATE INDEX discp_act_thread ON public.core_discpostact ((payload->>'thread'));
CREATE INDEX discp_thread_category ON public.core_discthread(category_uuid);
CREATE VIEW public.discp_threads AS
 SELECT t.*,c.board_uuid,coalesce(a.pinned,false) AS pinned,coalesce(a.closed,false) AS closed,
   born.effective_at AS created_at,born.actor_id,event.sequence AS created_sequence
 FROM public.core_discthread t JOIN public.core_disccategory c USING(category_uuid)
 JOIN public.core_discpostact born ON born.act_uuid=t.creation_act
 JOIN public.core_livingorganismevent event ON event.event_uuid=born.act_uuid
 LEFT JOIN LATERAL (SELECT x.pinned,x.closed FROM public.core_discpostact x
   JOIN public.core_livingorganismevent e ON e.event_uuid=x.act_uuid
   WHERE x.payload->>'thread'=t.thread_uuid::text AND x.operation IN ('PIN','UNPIN','CLOSE','REOPEN')
   ORDER BY e.sequence DESC LIMIT 1) a ON true;
CREATE VIEW public.discp_posts AS
 SELECT p.*,v.version_uuid,v.version_number,v.body_fingerprint,v.quote_version,
   coalesce(a.author_hidden,false) AS author_hidden,coalesce(a.coordinator_hidden,false) AS coordinator_hidden,
   born.received_at,born.effective_at,born.recorded_at
 FROM public.core_discpost p JOIN public.core_discpostact born ON born.act_uuid=p.creation_act
 JOIN LATERAL (SELECT x.* FROM public.core_discpostversion x WHERE x.post_uuid=p.post_uuid
   ORDER BY x.version_number DESC LIMIT 1) v ON true
 LEFT JOIN LATERAL (SELECT x.author_hidden,x.coordinator_hidden FROM public.core_discpostact x
   JOIN public.core_livingorganismevent e ON e.event_uuid=x.act_uuid
   WHERE x.payload->>'post'=p.post_uuid::text AND x.operation IN ('HIDE','RESTORE')
   ORDER BY e.sequence DESC LIMIT 1) a ON true;
CREATE FUNCTION public.discp_body_fingerprint(p_body text) RETURNS text LANGUAGE sql IMMUTABLE STRICT AS $$
 SELECT encode(sha256(convert_to(replace(replace(p_body,E'\r\n',E'\n'),E'\r',E'\n'),'UTF8')),'hex')
$$;
-- Scope precedes authority, including raw act insert and author routes.
CREATE FUNCTION public.discp_scope(p_payload jsonb) RETURNS void LANGUAGE plpgsql STABLE AS $$
DECLARE v_thread public.core_discthread; v_post public.core_discpost; v_op text:=p_payload->>'operation';
BEGIN
 IF NOT EXISTS(SELECT FROM public.core_disccategory c WHERE c.category_uuid=(p_payload->>'category')::uuid
   AND c.board_uuid=(p_payload->>'board')::uuid) THEN
   RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 SELECT * INTO v_thread FROM public.core_discthread WHERE thread_uuid=(p_payload->>'thread')::uuid;
 SELECT * INTO v_post FROM public.core_discpost WHERE post_uuid=(p_payload->>'post')::uuid;
 IF (v_op<>'START' AND v_thread.thread_uuid IS NULL)
   OR (v_thread.thread_uuid IS NOT NULL AND v_thread.category_uuid::text IS DISTINCT FROM p_payload->>'category')
   OR (v_op IN ('EDIT','HIDE','RESTORE') AND v_post.post_uuid IS NULL)
   OR (v_post.post_uuid IS NOT NULL AND v_post.thread_uuid::text IS DISTINCT FROM p_payload->>'thread')
   OR (v_op='START' AND v_thread.thread_uuid IS NOT NULL
       AND v_thread.creation_act::text IS DISTINCT FROM p_payload->>'request')
   OR (v_op IN ('START','REPLY') AND v_post.post_uuid IS NOT NULL
       AND v_post.creation_act::text IS DISTINCT FROM p_payload->>'request') THEN
   RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
END $$;
CREATE FUNCTION public.discp_source(p_payload jsonb,p_person bigint,p_cut timestamptz)
 RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE v_board public.core_discboard; v_category public.disc_categories; v_author bigint; v_state text;
 v_office jsonb; v_op text:=p_payload->>'operation';
BEGIN
 PERFORM public.discp_scope(p_payload);
 SELECT * INTO v_board FROM public.core_discboard WHERE board_uuid=(p_payload->>'board')::uuid;
 SELECT * INTO v_category FROM public.disc_categories WHERE category_uuid=(p_payload->>'category')::uuid;
 IF v_board.board_uuid IS NULL OR v_category.board_uuid IS DISTINCT FROM v_board.board_uuid
   OR NOT public.disc_audience(v_board.organism_id,v_board.circle_id,p_person,v_category.access_level,p_cut) THEN
   RETURN NULL; END IF;
 v_state:=public.disc_member_state(v_board.organism_id,p_person,p_cut);
 IF coalesce(v_state IN ('ACTIVE','PROBATIONARY'),false) IS NOT TRUE THEN RETURN NULL; END IF;
 v_office:=public.disc_office(v_board.organism_id,v_board.circle_id,p_person,p_cut);
 IF v_op IN ('PIN','UNPIN','CLOSE','REOPEN') OR
    (v_op IN ('HIDE','RESTORE') AND p_payload->>'as_coordinator'='true') OR
    (v_op='START' AND v_category.functional_code='ANNOUNCEMENTS') THEN RETURN v_office; END IF;
 IF v_op IN ('EDIT','HIDE','RESTORE') THEN
   SELECT author_id INTO v_author FROM public.core_discpost WHERE post_uuid=(p_payload->>'post')::uuid;
   IF v_author IS DISTINCT FROM p_person THEN RETURN NULL; END IF;
 END IF;
 RETURN jsonb_build_object('kind','MEMBER','reference',p_person,'state',v_state,'rule','HG:DISC1:D2:20261106');
END $$;
CREATE FUNCTION public.discp_validate(p_payload jsonb,p_person bigint,p_cut timestamptz)
 RETURNS void LANGUAGE plpgsql STABLE AS $$
DECLARE v_category public.disc_categories; v_thread public.discp_threads; v_post public.discp_posts;
 v_quote public.core_discpostversion; v_quoted public.discp_posts; v_first public.discp_posts;
 v_op text:=p_payload->>'operation'; v_office boolean; v_board public.core_discboard;
BEGIN
 SELECT * INTO v_category FROM public.disc_categories WHERE category_uuid=(p_payload->>'category')::uuid;
 SELECT * INTO v_board FROM public.core_discboard WHERE board_uuid=v_category.board_uuid;
 SELECT * INTO v_thread FROM public.discp_threads WHERE thread_uuid=(p_payload->>'thread')::uuid;
 SELECT * INTO v_post FROM public.discp_posts WHERE post_uuid=(p_payload->>'post')::uuid;
 v_office:=public.disc_office(v_board.organism_id,v_board.circle_id,p_person,p_cut) IS NOT NULL;
 IF v_op='START' THEN
   IF v_thread.thread_uuid IS NOT NULL OR EXISTS(SELECT FROM public.core_discpost WHERE post_uuid=(p_payload->>'post')::uuid) THEN
     RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
   IF jsonb_typeof(p_payload->'title') IS DISTINCT FROM 'string' OR length(p_payload->>'title') NOT BETWEEN 1 AND 90
     OR btrim(p_payload->>'title')='' OR coalesce(p_payload->>'type' IN ('TOPIC','QUESTION','SUGGESTION'),false) IS NOT TRUE THEN
     RAISE EXCEPTION 'DISC_POST_INPUT' USING ERRCODE='23514'; END IF;
 ELSE
   IF v_thread.category_uuid IS DISTINCT FROM v_category.category_uuid OR v_thread.thread_uuid IS NULL THEN
     RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
   IF v_op='REPLY' AND EXISTS(SELECT FROM public.core_discpost WHERE post_uuid=(p_payload->>'post')::uuid) THEN
     RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
   IF v_op IN ('EDIT','HIDE','RESTORE') AND (v_post.post_uuid IS NULL OR v_post.thread_uuid<>v_thread.thread_uuid) THEN
     RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 END IF;
 IF v_op IN ('START','REPLY') AND v_category.retired THEN RAISE EXCEPTION 'DISC_CATEGORY_RETIRED' USING ERRCODE='23514'; END IF;
 SELECT * INTO v_first FROM public.discp_posts WHERE thread_uuid=v_thread.thread_uuid AND post_number=1;
 IF v_op='REPLY' AND v_thread.closed THEN RAISE EXCEPTION 'DISC_CLOSED' USING ERRCODE='23514'; END IF;
 IF v_op IN ('REPLY','EDIT') AND (v_first.coordinator_hidden OR
   (v_op='EDIT' AND (v_post.author_hidden OR v_post.coordinator_hidden))) AND NOT v_office THEN
   RAISE EXCEPTION 'DISC_HIDDEN' USING ERRCODE='23514'; END IF;
 IF v_op='EDIT' AND p_payload->>'predecessor' IS DISTINCT FROM v_post.version_uuid::text THEN
   RAISE EXCEPTION 'DISC_POST_PREDECESSOR' USING ERRCODE='23514'; END IF;
 IF v_op IN ('START','REPLY','EDIT') THEN
   IF p_payload->>'body_fingerprint' IS NULL OR p_payload->>'body_fingerprint' !~ '^[0-9a-f]{64}$' THEN
     RAISE EXCEPTION 'DISC_POST_INPUT' USING ERRCODE='23514'; END IF;
   IF p_payload->>'quote' IS NOT NULL THEN
     SELECT * INTO v_quote FROM public.core_discpostversion WHERE version_uuid=(p_payload->>'quote')::uuid;
     SELECT * INTO v_quoted FROM public.discp_posts WHERE post_uuid=v_quote.post_uuid;
     IF v_op='START' OR v_quote.version_uuid IS NULL OR v_quoted.thread_uuid IS DISTINCT FROM v_thread.thread_uuid
       OR (v_op='EDIT' AND v_quoted.post_number>=v_post.post_number)
       OR (NOT v_office AND (v_quoted.author_hidden OR v_quoted.coordinator_hidden)
           AND NOT (v_op='EDIT' AND v_quote.version_uuid IS NOT DISTINCT FROM v_post.quote_version)) THEN
       RAISE EXCEPTION 'DISC_QUOTE' USING ERRCODE='23514'; END IF;
   END IF;
 END IF;
 IF v_op='RESTORE' AND p_payload->>'as_coordinator'='false' AND v_post.coordinator_hidden THEN
   RAISE EXCEPTION 'DISC_MODERATED' USING ERRCODE='23514'; END IF;
END $$;
CREATE FUNCTION public.discp_input() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_act public.core_discpostact; v_board public.core_discboard; v_lo uuid; v_circle uuid;
 v_source jsonb; v_stamp timestamptz:=statement_timestamp(); v_post public.discp_posts;
 v_thread public.discp_threads; v_count integer; v_version public.core_discpostversion;
BEGIN
 IF TG_TABLE_NAME='core_discpostact' THEN
   SELECT * INTO v_board FROM public.core_discboard WHERE board_uuid=NEW.board_uuid;
   SELECT organism_id INTO v_lo FROM public.core_livingorganism WHERE id=v_board.organism_id;
   PERFORM public.s015_lock_organisms(ARRAY[v_lo]);
   v_source:=public.discp_source(NEW.payload,NEW.actor_id,v_stamp);
   IF v_source IS NULL AND NEW.operation IN ('START','REPLY') THEN
     RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
   IF v_source IS NULL OR v_source IS DISTINCT FROM NEW.authority_source OR
     public.discp_source(NEW.payload,NEW.actor_id,NEW.received_at) IS DISTINCT FROM v_source THEN
     RAISE EXCEPTION 'DISC_POST_AUTHORITY' USING ERRCODE='23514'; END IF;
   SELECT circle_uuid INTO v_circle FROM public.core_circle WHERE id=v_board.circle_id;
   IF NEW.execution_xid IS NOT NULL OR NEW.received_at>v_stamp
     OR NEW.payload->>'organism' IS DISTINCT FROM v_lo::text OR NEW.payload->>'circle' IS DISTINCT FROM v_circle::text
     OR NEW.payload->>'board' IS DISTINCT FROM NEW.board_uuid::text OR NEW.payload->>'request' IS DISTINCT FROM NEW.act_uuid::text
     OR NEW.payload->>'operation' IS DISTINCT FROM NEW.operation
     OR NEW.fingerprint IS DISTINCT FROM public.disc_fingerprint(NEW.payload)
     OR NOT EXISTS(SELECT FROM public.core_identity i WHERE i.id=NEW.actor_id AND i.access_epoch=NEW.actor_epoch) THEN
     RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
   PERFORM public.discp_validate(NEW.payload,NEW.actor_id,v_stamp);
   NEW.execution_xid:=pg_current_xact_id(); NEW.effective_at:=v_stamp; NEW.recorded_at:=v_stamp;
   NEW.author_hidden:=NULL; NEW.coordinator_hidden:=NULL; NEW.pinned:=NULL; NEW.closed:=NULL;
   IF NEW.operation IN ('HIDE','RESTORE') THEN
     SELECT * INTO v_post FROM public.discp_posts WHERE post_uuid=(NEW.payload->>'post')::uuid;
     NEW.author_hidden:=v_post.author_hidden; NEW.coordinator_hidden:=v_post.coordinator_hidden;
     IF NEW.payload->>'as_coordinator'='true' THEN
       NEW.coordinator_hidden:=NEW.operation='HIDE';
       IF NEW.operation='RESTORE' THEN NEW.author_hidden:=false; END IF;
     ELSE NEW.author_hidden:=NEW.operation='HIDE'; END IF;
   ELSIF NEW.operation IN ('PIN','UNPIN','CLOSE','REOPEN') THEN
     SELECT * INTO v_thread FROM public.discp_threads WHERE thread_uuid=(NEW.payload->>'thread')::uuid;
     NEW.pinned:=v_thread.pinned; NEW.closed:=v_thread.closed;
     IF NEW.operation IN ('PIN','UNPIN') THEN NEW.pinned:=NEW.operation='PIN'; ELSE NEW.closed:=NEW.operation='CLOSE'; END IF;
   END IF;
   RETURN NEW;
 END IF;
 IF TG_TABLE_NAME='core_discthread' OR TG_TABLE_NAME='core_discpost' THEN
   SELECT * INTO v_act FROM public.core_discpostact WHERE act_uuid=NEW.creation_act;
 ELSIF TG_TABLE_NAME='core_discpostversion' THEN
   SELECT * INTO v_act FROM public.core_discpostact WHERE act_uuid=NEW.act_uuid;
 ELSE
   SELECT * INTO v_version FROM public.core_discpostversion WHERE version_uuid=NEW.version_uuid;
   SELECT * INTO v_act FROM public.core_discpostact WHERE act_uuid=v_version.act_uuid;
 END IF;
 IF v_act.act_uuid IS NULL OR v_act.execution_xid<>pg_current_xact_id() THEN
   RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='core_discthread' THEN
   IF v_act.operation<>'START' OR NEW.thread_uuid::text IS DISTINCT FROM v_act.payload->>'thread'
     OR NEW.category_uuid::text IS DISTINCT FROM v_act.payload->>'category' OR NEW.title IS DISTINCT FROM v_act.payload->>'title'
     OR NEW.topic_type IS DISTINCT FROM v_act.payload->>'type' OR NEW.record_kind<>'ORDINARY' THEN
     RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='core_discpost' THEN
   SELECT count(*) INTO v_count FROM public.core_discpost p WHERE p.thread_uuid=NEW.thread_uuid;
   IF v_act.operation NOT IN ('START','REPLY') OR NEW.post_uuid::text IS DISTINCT FROM v_act.payload->>'post'
     OR NEW.thread_uuid::text IS DISTINCT FROM v_act.payload->>'thread' OR NEW.author_id<>v_act.actor_id
     OR NEW.post_number<>v_count+1 THEN RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='core_discpostversion' THEN
   SELECT * INTO v_post FROM public.discp_posts WHERE post_uuid=NEW.post_uuid;
   IF v_act.operation NOT IN ('START','REPLY','EDIT') OR NEW.version_uuid<>v_act.act_uuid
     OR NEW.post_uuid::text IS DISTINCT FROM v_act.payload->>'post'
     OR NEW.version_number<>coalesce(v_post.version_number,0)+1 OR NEW.predecessor IS DISTINCT FROM v_post.version_uuid
     OR NEW.quote_version::text IS DISTINCT FROM v_act.payload->>'quote'
     OR NEW.body_fingerprint IS DISTINCT FROM v_act.payload->>'body_fingerprint' THEN
     RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
 ELSE
   IF v_version.body_fingerprint IS DISTINCT FROM public.discp_body_fingerprint(NEW.markdown) THEN
     RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION public.discp_event_matches(p_event public.core_livingorganismevent) RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT EXISTS(SELECT FROM public.core_discpostact a JOIN public.core_discboard b ON b.board_uuid=a.board_uuid
 JOIN public.core_livingorganism lo ON lo.id=b.organism_id WHERE a.act_uuid=p_event.event_uuid
 AND a.execution_xid=pg_current_xact_id() AND p_event.action='RECORD_DISCUSSION_POST'
 AND p_event.living_organism_id=b.organism_id AND p_event.actor_state='LIVING_ORGANISM'
 AND p_event.actor_capacity='STANDING' AND p_event.actor_id IS NULL AND p_event.actor_access_epoch IS NULL
 AND p_event.entry_mode='TRANSPORT' AND p_event.prior_state=p_event.resulting_state AND p_event.sequence>1
 AND p_event.enterer_identity_id=a.actor_id AND p_event.authority_basis_id=lo.founding_authority_basis_id
 AND p_event.authority_decision_reference=public.disc_decision(a.authority_source,a.fingerprint)
 AND p_event.evidence_reference='disc-post-act:'||a.act_uuid AND p_event.payload_fingerprint=a.fingerprint
 AND p_event.request_reference='disc-post:'||a.act_uuid AND p_event.idempotency_key='disc-post:'||a.act_uuid
 AND p_event.received_at=a.received_at AND p_event.occurred_at=a.received_at
 AND p_event.effective_at=a.effective_at AND p_event.recorded_at=a.recorded_at
 AND p_event.composing_rule_state='NA_TRANSPORT' AND p_event.composer_state='NA_TRANSPORT'
 AND p_event.effective_until_state='NONE')
$$;
CREATE FUNCTION public.discp_complete() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_key uuid; v_act public.core_discpostact; v_expected integer; v_actual integer;
BEGIN
 IF TG_TABLE_NAME IN ('core_discthread','core_discpost') THEN v_key:=NEW.creation_act;
 ELSIF TG_TABLE_NAME='core_livingorganismevent' THEN v_key:=NEW.event_uuid;
 ELSIF TG_TABLE_NAME='core_discbody' THEN v_key:=NEW.version_uuid;
 ELSE v_key:=NEW.act_uuid; END IF;
 SELECT * INTO v_act FROM public.core_discpostact WHERE act_uuid=v_key;
 v_expected:=CASE WHEN v_act.operation IN ('START','REPLY','EDIT') THEN 1 ELSE 0 END;
 SELECT count(*) INTO v_actual FROM public.core_discpostversion v JOIN public.core_discbody b USING(version_uuid) WHERE v.act_uuid=v_key;
 IF v_act.act_uuid IS NULL OR v_actual<>v_expected
   OR (SELECT count(*) FROM public.core_discpost p WHERE p.creation_act=v_key)<>(CASE WHEN v_act.operation IN ('START','REPLY') THEN 1 ELSE 0 END)
   OR (SELECT count(*) FROM public.core_discthread t WHERE t.creation_act=v_key)<>(CASE WHEN v_act.operation='START' THEN 1 ELSE 0 END)
   OR NOT EXISTS(SELECT FROM public.core_livingorganismevent e WHERE e.event_uuid=v_key AND public.discp_event_matches(e))
   OR public.discp_source(v_act.payload,v_act.actor_id,statement_timestamp()) IS DISTINCT FROM v_act.authority_source
   OR NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
     WHERE i.id=v_act.actor_id AND i.access_epoch=v_act.actor_epoch AND i.access_state='active' AND u.is_active) THEN
   RAISE EXCEPTION 'DISC_COMPLETE' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
CREATE FUNCTION public.discp_execute(p_payload jsonb,p_body text,p_actor bigint,p_epoch bigint,p_received timestamptz)
 RETURNS jsonb LANGUAGE plpgsql VOLATILE AS $$
DECLARE v_board public.core_discboard; v_lo public.core_livingorganism; v_head public.core_livingorganismevent;
 v_old public.core_discpostact; v_source jsonb; v_fp text; v_stamp timestamptz:=statement_timestamp();
 v_key uuid:=(p_payload->>'request')::uuid; v_thread uuid:=(p_payload->>'thread')::uuid;
 v_post uuid:=(p_payload->>'post')::uuid; v_op text:=p_payload->>'operation'; v_version public.discp_posts;
 v_count integer; v_state text; v_other_board uuid; v_quote uuid;
 v_conflict_name text; v_conflict_table text; v_conflict_schema text;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('DISC1:request:'||v_key::text,0));
 SELECT b.* INTO v_board FROM public.core_discboard b JOIN public.core_livingorganism lo ON lo.id=b.organism_id
   LEFT JOIN public.core_circle c ON c.id=b.circle_id
   WHERE b.board_uuid=(p_payload->>'board')::uuid AND lo.organism_id=(p_payload->>'organism')::uuid
     AND c.circle_uuid IS NOT DISTINCT FROM (p_payload->>'circle')::uuid;
 IF v_board.board_uuid IS NULL THEN RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 SELECT * INTO v_lo FROM public.core_livingorganism WHERE id=v_board.organism_id;
 PERFORM public.s015_lock_organisms(ARRAY[v_lo.organism_id]);
 IF NOT EXISTS(SELECT FROM public.disc_categories c WHERE c.category_uuid=(p_payload->>'category')::uuid
   AND c.board_uuid=v_board.board_uuid AND public.disc_audience(v_board.organism_id,v_board.circle_id,p_actor,c.access_level,v_stamp)) THEN
   RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 -- Resolve omitted quote from the immutable predecessor, making replay stable.
 IF v_op='EDIT' AND NOT (p_payload ? 'quote') THEN
   SELECT quote_version INTO v_quote FROM public.core_discpostversion
     WHERE version_uuid=(p_payload->>'predecessor')::uuid AND post_uuid=v_post;
   p_payload:=p_payload||jsonb_build_object('quote',v_quote);
 END IF;
 v_source:=public.discp_source(p_payload,p_actor,v_stamp);
 IF v_source IS NULL THEN
   -- Same refusal for new-ID probes whether those IDs are used or unused.
   IF v_op IN ('START','REPLY') THEN RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
   RAISE EXCEPTION 'DISC_POST_AUTHORITY' USING ERRCODE='23514'; END IF;
 SELECT board_uuid INTO v_other_board FROM public.core_discact WHERE act_uuid=v_key;
 IF v_other_board IS NOT NULL THEN
   IF v_other_board<>v_board.board_uuid THEN RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
   RAISE EXCEPTION 'DISC_REQUEST_CONFLICT' USING ERRCODE='23514';
 END IF;
 SELECT * INTO v_old FROM public.core_discpostact WHERE act_uuid=v_key;
 IF v_old.act_uuid IS NOT NULL THEN
   IF v_old.board_uuid<>v_board.board_uuid THEN RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
   IF v_old.actor_id<>p_actor OR v_old.actor_epoch<>p_epoch OR v_old.payload IS DISTINCT FROM p_payload THEN
     RAISE EXCEPTION 'DISC_REQUEST_CONFLICT' USING ERRCODE='23514'; END IF;
   RETURN jsonb_build_object('act',v_key,'thread',v_thread,'post',v_post,'version',CASE WHEN v_op IN ('START','REPLY','EDIT') THEN v_key END);
 END IF;
 IF EXISTS(SELECT FROM public.core_livingorganismevent e WHERE e.event_uuid=v_key) THEN
   RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 IF v_op IN ('START','REPLY','EDIT') AND (p_body IS NULL OR public.discp_body_fingerprint(p_body) IS DISTINCT FROM p_payload->>'body_fingerprint') THEN
   RAISE EXCEPTION 'DISC_POST_INPUT' USING ERRCODE='23514'; END IF;
 v_fp:=public.disc_fingerprint(p_payload);
 INSERT INTO public.core_discpostact(act_uuid,board_uuid,operation,actor_id,actor_epoch,authority_source,payload,fingerprint,received_at)
 VALUES(v_key,v_board.board_uuid,v_op,p_actor,p_epoch,v_source,p_payload,v_fp,p_received);
 IF v_op='START' THEN
   INSERT INTO public.core_discthread VALUES(v_thread,(p_payload->>'category')::uuid,v_key,p_payload->>'type',p_payload->>'title','ORDINARY');
 END IF;
 IF v_op IN ('START','REPLY') THEN
   SELECT count(*) INTO v_count FROM public.core_discpost p WHERE p.thread_uuid=v_thread;
   INSERT INTO public.core_discpost VALUES(v_post,v_thread,v_key,p_actor,v_count+1);
 END IF;
 IF v_op IN ('START','REPLY','EDIT') THEN
   SELECT * INTO v_version FROM public.discp_posts WHERE post_uuid=v_post;
   INSERT INTO public.core_discpostversion VALUES(v_key,v_post,v_key,coalesce(v_version.version_number,0)+1,
     v_version.version_uuid,p_payload->>'body_fingerprint',(p_payload->>'quote')::uuid);
   INSERT INTO public.core_discbody VALUES(v_key,p_body);
 END IF;
 SELECT * INTO STRICT v_lo FROM public.core_livingorganism WHERE id=v_board.organism_id FOR UPDATE;
 SELECT * INTO STRICT v_head FROM public.core_livingorganismevent WHERE id=v_lo.head_event_id;
 v_state:=public.s015_fold_effective_state('core_livingorganism',v_lo.id,v_stamp,v_stamp);
 PERFORM public.s015_append_event('core_livingorganism',v_lo.id,'RECORD_DISCUSSION_POST',v_state,
   v_stamp,p_received,p_received,p_actor,v_lo.founding_authority_basis_id,
   public.disc_decision(v_source,v_fp),'disc-post-act:'||v_key,'disc-post:'||v_key,v_fp,'{}'::jsonb,
   NULL,v_key,v_head.id,v_head.sequence::integer,NULL::bigint,NULL::text,'disc-post:'||v_key);
 RETURN jsonb_build_object('act',v_key,'thread',v_thread,'post',v_post,'version',CASE WHEN v_op IN ('START','REPLY','EDIT') THEN v_key END);
EXCEPTION WHEN unique_violation THEN
 -- Another domain may race our preflight without the DISC1 request lock.
 -- Translate only the shared event UUID constraint; preserve unrelated faults.
 GET STACKED DIAGNOSTICS v_conflict_name=CONSTRAINT_NAME,
   v_conflict_table=TABLE_NAME,v_conflict_schema=SCHEMA_NAME;
 IF v_conflict_schema='public' AND v_conflict_table='core_livingorganismevent'
   AND EXISTS(SELECT FROM pg_constraint c WHERE c.conrelid='public.core_livingorganismevent'::regclass
     AND c.conname=v_conflict_name AND c.contype='u'
     AND c.conkey=ARRAY[(SELECT a.attnum FROM pg_attribute a
       WHERE a.attrelid=c.conrelid AND a.attname='event_uuid')]) THEN
   RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 RAISE;
END $$;
"""
GUARDS = "\n".join(
    f"""CREATE TRIGGER discp_input BEFORE INSERT ON public.{table}
 FOR EACH ROW EXECUTE FUNCTION public.discp_input();
CREATE TRIGGER discp_immutable BEFORE UPDATE OR DELETE ON public.{table}
 FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE TRIGGER s015_discp_no_truncate BEFORE TRUNCATE ON public.{table}
 FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE CONSTRAINT TRIGGER discp_complete AFTER INSERT ON public.{table}
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.discp_complete();""" for table in TABLES
) + """
CREATE CONSTRAINT TRIGGER discp_event_complete AFTER INSERT ON public.core_livingorganismevent
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.action='RECORD_DISCUSSION_POST')
 EXECUTE FUNCTION public.discp_complete();
"""
WRITER_BRANCH = boards.WRITER_BRANCH.replace("RECORD_DISCUSSION", "RECORD_DISCUSSION_POST")
GATEWAY_BRANCH = boards.GATEWAY_BRANCH.replace("RECORD_DISCUSSION", "RECORD_DISCUSSION_POST").replace("disc_event_matches", "discp_event_matches")


def bodies():
    writer, gateway = boards.bodies()
    return (boards.gi.law.root.replace_once(writer, " SELECT string_agg", boards.WRITER_BRANCH + " SELECT string_agg"),
            boards.gi.law.root.replace_once(gateway, "BEGIN\n", "BEGIN\n" + boards.GATEWAY_BRANCH))


def switch(editor, backwards=False):
    old_writer, old_gateway = bodies()
    new_writer = boards.gi.law.root.replace_once(old_writer, " SELECT string_agg", WRITER_BRANCH + " SELECT string_agg")
    new_gateway = boards.gi.law.root.replace_once(old_gateway, "BEGIN\n", "BEGIN\n" + GATEWAY_BRANCH)
    with editor.connection.cursor() as cursor:
        cursor.execute("SELECT prosrc FROM pg_proc WHERE oid='public.s3fu_guard_gateway_effect()'::regprocedure")
        if cursor.fetchone()[0].strip() != (new_gateway if backwards else old_gateway).strip():
            raise RuntimeError("DISC1 P2 stopped: installed gateway differs from the commissioned body.")
    boards.gi.law.writer.switch_writer(editor, new_writer if backwards else old_writer, old_writer if backwards else new_writer)
    editor.execute("CREATE OR REPLACE FUNCTION public.s3fu_guard_gateway_effect() RETURNS trigger LANGUAGE plpgsql AS $$" + (old_gateway if backwards else new_gateway) + "$$;", params=None)


def install(apps, editor):
    editor.execute(FORWARD + GUARDS, params=None)
    switch(editor)


def reverse(apps, editor):
    editor.execute("LOCK TABLE " + ",".join("public." + t for t in TABLES) + ",public.core_livingorganismevent IN ACCESS EXCLUSIVE MODE", params=None)
    with editor.connection.cursor() as cursor:
        cursor.execute("SELECT " + " OR ".join("EXISTS(SELECT FROM public." + t + ")" for t in TABLES))
        if cursor.fetchone()[0]:
            raise RuntimeError("DISC1 P2 reversal refused while post history exists.")
    switch(editor, True)
    editor.execute("""DROP TRIGGER discp_event_complete ON public.core_livingorganismevent;
DROP VIEW public.discp_posts,public.discp_threads;
DROP TABLE public.core_discbody,public.core_discpostversion,public.core_discpost,public.core_discthread,public.core_discpostact;
DROP FUNCTION public.discp_input(),public.discp_complete(),public.discp_body_fingerprint(text),
 public.discp_scope(jsonb),public.discp_source(jsonb,bigint,timestamptz),public.discp_validate(jsonb,bigint,timestamptz),
 public.discp_event_matches(public.core_livingorganismevent),public.discp_execute(jsonb,text,bigint,bigint,timestamptz);
""", params=None)


class Migration(migrations.Migration):
    atomic = True
    dependencies: ClassVar = [("core", "0072_discussion_boards")]
    operations: ClassVar = [migrations.RunPython(install, reverse)]
