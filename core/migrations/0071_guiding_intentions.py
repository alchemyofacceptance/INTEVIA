"""GI P1: immutable intentions and exact-law citations, with attributed ILC acts."""
from importlib import import_module
from typing import ClassVar

from django.db import migrations

law = import_module("core.migrations.0067_law_acts")

FORWARD = r"""
CREATE TABLE public.core_guidingintention (
 gi_uuid uuid PRIMARY KEY,
 organism_id bigint NOT NULL REFERENCES public.core_livingorganism,
 circle_id bigint REFERENCES public.core_circle,
 creation_act uuid NOT NULL UNIQUE
);
CREATE TABLE public.core_giact (
 act_uuid uuid PRIMARY KEY,
 gi_uuid uuid NOT NULL REFERENCES public.core_guidingintention,
 operation text NOT NULL CHECK (operation IN ('CREATE','REVISE','RETIRE')),
 version_uuid uuid NOT NULL,
 actor_id bigint NOT NULL REFERENCES public.core_identity,
 actor_epoch bigint NOT NULL,
 authority_source jsonb NOT NULL,
 payload jsonb NOT NULL,
 fingerprint text NOT NULL,
 received_at timestamptz NOT NULL,
 effective_at timestamptz NOT NULL,
 recorded_at timestamptz NOT NULL,
 execution_xid xid8 NOT NULL,
 FOREIGN KEY(act_uuid) REFERENCES public.core_livingorganismevent(event_uuid) DEFERRABLE INITIALLY DEFERRED
);
CREATE UNIQUE INDEX gi_once_created ON public.core_giact(gi_uuid) WHERE operation='CREATE';
CREATE UNIQUE INDEX gi_once_retired ON public.core_giact(gi_uuid) WHERE operation='RETIRE';
CREATE TABLE public.core_giversion (
 version_uuid uuid PRIMARY KEY,
 gi_uuid uuid NOT NULL REFERENCES public.core_guidingintention,
 version_number integer NOT NULL CHECK(version_number>0),
 predecessor uuid UNIQUE REFERENCES public.core_giversion,
 act_uuid uuid NOT NULL UNIQUE REFERENCES public.core_giact,
 title text NOT NULL CHECK(btrim(title)<>''),
 content text NOT NULL CHECK(btrim(content)<>''),
 UNIQUE(gi_uuid,version_number)
);
ALTER TABLE public.core_guidingintention ADD FOREIGN KEY(creation_act)
 REFERENCES public.core_giact DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE public.core_giact ADD FOREIGN KEY(version_uuid)
 REFERENCES public.core_giversion DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE public.core_gicitation (
 version_uuid uuid NOT NULL REFERENCES public.core_giversion,
 binding_uuid uuid NOT NULL REFERENCES public.core_organismlawbinding,
 law_version_id bigint NOT NULL REFERENCES public.core_libraryresourceversion,
 PRIMARY KEY(version_uuid,binding_uuid)
);

CREATE FUNCTION public.gi_source(p_lo bigint,p_circle bigint,p_person bigint,p_operation text,
 p_route text,p_cut timestamptz,p_known timestamptz) RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE v_lo public.core_livingorganism; v_office uuid; v_circle public.core_circle; v_source jsonb;
BEGIN
 SELECT * INTO v_lo FROM public.core_livingorganism WHERE id=p_lo;
 IF v_lo.id IS NULL OR p_operation NOT IN ('CREATE','REVISE','RETIRE')
   OR coalesce(public.s015_fold_effective_state('core_livingorganism',p_lo,p_cut,p_known) IN ('ACTIVE','FOUNDING_PENDING'),false) IS NOT TRUE
   OR public.c2_basis_in_force(v_lo.founding_authority_basis_id,p_cut,p_known) IS NOT TRUE
   OR NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
     WHERE i.id=p_person AND i.access_state='active' AND u.is_active) THEN RETURN NULL; END IF;
 IF p_circle IS NULL AND p_route IN ('COORDINATOR_ROUTE','GOVERNANCE_INTERIM') THEN
   IF p_route='GOVERNANCE_INTERIM' AND p_operation<>'CREATE' THEN RETURN NULL; END IF;
   v_source:=public.lib_policy_source(p_lo,p_person,p_route,p_cut,p_known);
   RETURN v_source;
 END IF;
 IF p_circle IS NULL OR p_route IS DISTINCT FROM 'CIRCLE_COORDINATOR' THEN RETURN NULL; END IF;
 SELECT * INTO v_circle FROM public.core_circle WHERE id=p_circle AND parent_organism_id=p_lo;
 IF v_circle.id IS NULL OR public.s015_fold_effective_state('core_circle',p_circle,p_cut,p_known) IS DISTINCT FROM 'ACTIVE'
   OR (v_lo.platform_root AND public.r_circle_root_qualified(p_lo,p_cut,p_known) IS NOT TRUE) THEN RETURN NULL; END IF;
 SELECT a.assignment_uuid INTO v_office FROM public.circle_holders(p_circle,p_cut,p_known) h
 JOIN public.core_organismmembership m ON m.identity_id=h.person AND m.living_organism_id=p_lo
 JOIN public.core_contextualroleassignment a ON a.membership_id=m.id AND a.circle_id=p_circle
 JOIN public.c2_seat_facts(p_lo,p_cut,p_known) f ON f.assignment_id=a.id
 WHERE h.person=p_person AND f.role_scope='CIRCLE' AND f.role_code='COORDINATOR' AND f.standing='QUALIFIED'
 ORDER BY a.id LIMIT 1;
 IF v_office IS NULL THEN RETURN NULL; END IF;
 RETURN jsonb_build_object('kind','OFFICE','reference',v_office,'route',p_route,
   'organism',v_lo.organism_id,'circle',v_circle.circle_uuid);
END $$;

CREATE FUNCTION public.gi_laws_ok(p_organism uuid,p_circle bigint,p_citations jsonb,
 p_cut timestamptz,p_known timestamptz) RETURNS boolean LANGUAGE plpgsql STABLE AS $$
DECLARE v_total integer; v_valid integer; v_local integer;
BEGIN
 IF jsonb_typeof(p_citations) IS DISTINCT FROM 'array' THEN RETURN false; END IF;
 IF EXISTS(SELECT FROM jsonb_array_elements(p_citations) x WHERE jsonb_typeof(x)<>'object'
   OR NOT (x ?& ARRAY['binding','version']) OR (x-'binding'-'version')<>'{}'::jsonb) THEN RETURN false; END IF;
 v_total:=jsonb_array_length(p_citations);
 IF v_total<>(SELECT count(DISTINCT x->>'binding') FROM jsonb_array_elements(p_citations) x) THEN RETURN false; END IF;
 SELECT count(*),count(*) FILTER(WHERE b.scope_kind<>'PLATFORM') INTO v_valid,v_local
 FROM jsonb_array_elements(p_citations) x
 JOIN public.l_laws_at(p_organism,p_cut,p_known) l
   ON l.binding=(x->>'binding')::uuid AND l.version=(x->>'version')::bigint
 JOIN public.core_organismlawbinding b ON b.binding_uuid=l.binding
 WHERE b.scope_kind IN ('PLATFORM','LIVING_ORGANISM') OR (b.scope_kind='CIRCLE' AND b.scope_circle_id=p_circle);
 RETURN v_total>0 AND v_valid=v_total AND v_local>0; -- GI_LOCAL_LAW_REQUIRED
END $$;

CREATE FUNCTION public.gi_payload_fingerprint(p_payload jsonb) RETURNS text LANGUAGE sql IMMUTABLE STRICT AS $$
 SELECT encode(sha256(convert_to('INTEVIA:GI:1:'||public.l_canonical(p_payload),'UTF8')),'hex')
$$;
CREATE FUNCTION public.gi_decision_reference(p_source jsonb,p_fingerprint text) RETURNS text LANGUAGE sql IMMUTABLE STRICT AS $$
 SELECT 's015d1:'||encode(sha256(convert_to('GI-OFFICE:1:'||public.l_canonical(p_source)||':'||p_fingerprint,'UTF8')),'hex')
$$;

CREATE FUNCTION public.gi_input() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_gi public.core_guidingintention; v_act public.core_giact; v_prior public.core_giversion;
 v_org uuid; v_count integer; v_circle uuid; v_stamp timestamptz:=statement_timestamp(); v_source jsonb;
BEGIN
 IF TG_TABLE_NAME='core_guidingintention' THEN
   SELECT organism_id INTO v_org FROM public.core_livingorganism WHERE id=NEW.organism_id;
   PERFORM public.s015_lock_organisms(ARRAY[v_org]);
   IF NEW.circle_id IS NOT NULL AND NOT EXISTS(SELECT FROM public.core_circle c
     WHERE c.id=NEW.circle_id AND c.parent_organism_id=NEW.organism_id) THEN
     RAISE EXCEPTION 'GI_OWNER' USING ERRCODE='23514'; END IF;
   RETURN NEW;
 ELSIF TG_TABLE_NAME='core_giact' THEN
   SELECT * INTO STRICT v_gi FROM public.core_guidingintention WHERE gi_uuid=NEW.gi_uuid;
   SELECT organism_id INTO v_org FROM public.core_livingorganism WHERE id=v_gi.organism_id;
   -- The authenticated service holds the actor first. Direct inserts independently
   -- serialize on the owner; no new actor lock is acquired after this lock.
   PERFORM public.s015_lock_organisms(ARRAY[v_org]);
   IF NEW.execution_xid IS NOT NULL THEN RAISE EXCEPTION 'GI_XID' USING ERRCODE='23514'; END IF;
   NEW.execution_xid:=pg_current_xact_id(); NEW.effective_at:=v_stamp; NEW.recorded_at:=v_stamp;
   SELECT circle_uuid INTO v_circle FROM public.core_circle WHERE id=v_gi.circle_id;
   IF NEW.received_at>v_stamp OR NEW.payload->>'organism' IS DISTINCT FROM v_org::text
     OR NEW.payload->>'circle' IS DISTINCT FROM v_circle::text
     OR NEW.payload->>'gi' IS DISTINCT FROM NEW.gi_uuid::text
     OR NEW.payload->>'operation' IS DISTINCT FROM NEW.operation
     OR NEW.payload->>'request' IS DISTINCT FROM NEW.act_uuid::text
     OR NEW.payload->>'route' IS DISTINCT FROM NEW.authority_source->>'route'
     OR NEW.fingerprint IS DISTINCT FROM public.gi_payload_fingerprint(NEW.payload)
     OR NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
        WHERE i.id=NEW.actor_id AND i.access_epoch=NEW.actor_epoch AND i.access_state='active' AND u.is_active) THEN
     RAISE EXCEPTION 'GI_PAYLOAD' USING ERRCODE='23514'; END IF;
   v_source:=public.gi_source(v_gi.organism_id,v_gi.circle_id,NEW.actor_id,NEW.operation,
     NEW.authority_source->>'route',NEW.received_at,NEW.received_at);
   IF v_source IS NULL OR v_source IS DISTINCT FROM NEW.authority_source
     OR public.gi_source(v_gi.organism_id,v_gi.circle_id,NEW.actor_id,NEW.operation,
       NEW.authority_source->>'route',v_stamp,v_stamp) IS DISTINCT FROM v_source THEN
     RAISE EXCEPTION 'GI_AUTHORITY' USING ERRCODE='23514'; END IF;
   IF EXISTS(SELECT FROM public.core_giact a WHERE a.gi_uuid=NEW.gi_uuid AND a.operation='RETIRE') THEN
     RAISE EXCEPTION 'GI_RETIRED' USING ERRCODE='23514'; END IF;
   SELECT * INTO v_prior FROM public.core_giversion v WHERE v.gi_uuid=NEW.gi_uuid ORDER BY version_number DESC LIMIT 1;
   IF (NEW.operation='CREATE' AND (v_prior.version_uuid IS NOT NULL OR NEW.payload->>'predecessor' IS NOT NULL
         OR v_gi.creation_act<>NEW.act_uuid))
     OR (NEW.operation<>'CREATE' AND (v_prior.version_uuid IS NULL OR
       NEW.payload->>'predecessor' IS DISTINCT FROM v_prior.version_uuid::text)) THEN
     RAISE EXCEPTION 'GI_PREDECESSOR' USING ERRCODE='23514'; END IF;
   IF NEW.operation='RETIRE' THEN
     IF NEW.version_uuid IS DISTINCT FROM v_prior.version_uuid OR NEW.payload->>'title' IS NOT NULL
       OR NEW.payload->>'content' IS NOT NULL OR NEW.payload->'citations' IS DISTINCT FROM '[]'::jsonb THEN
       RAISE EXCEPTION 'GI_RETIRE_SHAPE' USING ERRCODE='23514'; END IF;
   ELSE
     PERFORM r.id FROM public.core_libraryresource r WHERE r.id IN (
       SELECT b.resource_id FROM jsonb_array_elements(NEW.payload->'citations') x
       JOIN public.core_organismlawbinding b ON b.binding_uuid=(x->>'binding')::uuid
     ) ORDER BY r.id FOR SHARE;
     IF public.gi_laws_ok(v_org,v_gi.circle_id,NEW.payload->'citations',NEW.received_at,NEW.received_at) IS NOT TRUE
       OR public.gi_laws_ok(v_org,v_gi.circle_id,NEW.payload->'citations',v_stamp,v_stamp) IS NOT TRUE THEN
       RAISE EXCEPTION 'GI_LAW_REQUIRED' USING ERRCODE='23514'; END IF;
   END IF;
   RETURN NEW;
 ELSIF TG_TABLE_NAME='core_giversion' THEN
   SELECT * INTO STRICT v_act FROM public.core_giact WHERE act_uuid=NEW.act_uuid;
   SELECT count(*) INTO v_count FROM public.core_giversion WHERE gi_uuid=NEW.gi_uuid;
   IF v_act.execution_xid IS DISTINCT FROM pg_current_xact_id() OR v_act.operation NOT IN ('CREATE','REVISE')
     OR v_act.version_uuid<>NEW.version_uuid OR v_act.gi_uuid<>NEW.gi_uuid OR NEW.version_number<>v_count+1
     OR NEW.predecessor::text IS DISTINCT FROM v_act.payload->>'predecessor'
     OR NEW.title IS DISTINCT FROM v_act.payload->>'title' OR NEW.content IS DISTINCT FROM v_act.payload->>'content' THEN
     RAISE EXCEPTION 'GI_VERSION' USING ERRCODE='23514'; END IF;
   RETURN NEW;
 ELSE
   SELECT a.* INTO STRICT v_act FROM public.core_giversion v JOIN public.core_giact a ON a.act_uuid=v.act_uuid
     WHERE v.version_uuid=NEW.version_uuid;
   IF v_act.execution_xid IS DISTINCT FROM pg_current_xact_id() OR NOT EXISTS(
     SELECT FROM jsonb_array_elements(v_act.payload->'citations') x
     JOIN public.core_organismlawbinding b ON b.binding_uuid=(x->>'binding')::uuid
     WHERE b.binding_uuid=NEW.binding_uuid AND b.version_id=NEW.law_version_id
       AND (x->>'version')::bigint=NEW.law_version_id) THEN
     RAISE EXCEPTION 'GI_CITATION' USING ERRCODE='23514'; END IF;
   RETURN NEW;
 END IF;
END $$;

CREATE FUNCTION public.gi_event_matches(p_event public.core_livingorganismevent) RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT EXISTS(SELECT FROM public.core_giact a JOIN public.core_guidingintention g ON g.gi_uuid=a.gi_uuid
 JOIN public.core_livingorganism lo ON lo.id=g.organism_id
 WHERE a.act_uuid=p_event.event_uuid AND a.execution_xid=pg_current_xact_id()
   AND p_event.action='RECORD_GUIDING_INTENTION' AND p_event.living_organism_id=g.organism_id
   AND p_event.actor_state='LIVING_ORGANISM' AND p_event.actor_capacity='STANDING'
   AND p_event.actor_id IS NULL AND p_event.actor_access_epoch IS NULL AND p_event.entry_mode='TRANSPORT'
   AND p_event.prior_state=p_event.resulting_state AND p_event.sequence>1
   AND p_event.enterer_identity_id=a.actor_id AND p_event.authority_basis_id=lo.founding_authority_basis_id
   AND p_event.authority_decision_reference=public.gi_decision_reference(a.authority_source,a.fingerprint)
   AND p_event.evidence_reference='gi-act:'||a.act_uuid AND p_event.payload_fingerprint=a.fingerprint
   AND p_event.request_reference='gi:'||a.act_uuid AND p_event.idempotency_key='gi:'||a.act_uuid
   AND p_event.received_at=a.received_at AND p_event.occurred_at=a.received_at
   AND p_event.effective_at=a.effective_at AND p_event.recorded_at=a.recorded_at
   AND p_event.composing_rule_state='NA_TRANSPORT' AND p_event.composer_state='NA_TRANSPORT'
   AND p_event.effective_until_state='NONE')
$$;

CREATE FUNCTION public.gi_complete() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_key uuid; v_act public.core_giact; v_gi public.core_guidingintention; v_org uuid;
 v_version public.core_giversion; v_cites jsonb; v_stamp timestamptz:=statement_timestamp();
BEGIN
 IF TG_TABLE_NAME='core_livingorganismevent' THEN v_key:=NEW.event_uuid;
 ELSIF TG_TABLE_NAME='core_guidingintention' THEN v_key:=NEW.creation_act;
 ELSIF TG_TABLE_NAME='core_gicitation' THEN
   SELECT act_uuid INTO v_key FROM public.core_giversion WHERE version_uuid=NEW.version_uuid;
 ELSE v_key:=NEW.act_uuid; END IF;
 SELECT * INTO STRICT v_act FROM public.core_giact WHERE act_uuid=v_key;
 SELECT * INTO STRICT v_gi FROM public.core_guidingintention WHERE gi_uuid=v_act.gi_uuid;
 SELECT organism_id INTO v_org FROM public.core_livingorganism WHERE id=v_gi.organism_id;
 SELECT * INTO STRICT v_version FROM public.core_giversion WHERE version_uuid=v_act.version_uuid;
 IF TG_TABLE_NAME='core_guidingintention' THEN
   IF v_act.operation<>'CREATE' OR v_act.gi_uuid<>NEW.gi_uuid THEN
     RAISE EXCEPTION 'GI_COMPLETE' USING ERRCODE='23514'; END IF;
 END IF;
 IF v_version.gi_uuid<>v_gi.gi_uuid OR NOT EXISTS(SELECT FROM public.core_livingorganismevent e
     WHERE e.event_uuid=v_key AND public.gi_event_matches(e))
   OR public.gi_source(v_gi.organism_id,v_gi.circle_id,v_act.actor_id,v_act.operation,
      v_act.authority_source->>'route',v_stamp,v_stamp) IS DISTINCT FROM v_act.authority_source
   OR NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
      WHERE i.id=v_act.actor_id AND i.access_epoch=v_act.actor_epoch AND i.access_state='active' AND u.is_active) THEN
   RAISE EXCEPTION 'GI_COMPLETE' USING ERRCODE='23514'; END IF;
 IF v_act.operation<>'RETIRE' THEN
   SELECT coalesce(jsonb_agg(jsonb_build_object('binding',c.binding_uuid,'version',c.law_version_id)
     ORDER BY c.binding_uuid),'[]'::jsonb) INTO v_cites FROM public.core_gicitation c WHERE c.version_uuid=v_version.version_uuid;
   IF v_version.act_uuid<>v_act.act_uuid OR v_cites IS DISTINCT FROM v_act.payload->'citations'
     OR public.gi_laws_ok(v_org,v_gi.circle_id,v_cites,v_stamp,v_stamp) IS NOT TRUE THEN
     RAISE EXCEPTION 'GI_CITATIONS_COMPLETE' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NULL;
END $$;

CREATE FUNCTION public.gi_execute(p_payload jsonb,p_actor bigint,p_epoch bigint,p_received timestamptz)
 RETURNS jsonb LANGUAGE plpgsql VOLATILE AS $$
DECLARE v_lo public.core_livingorganism; v_circle bigint; v_gi public.core_guidingintention;
 v_old public.core_giact; v_source jsonb; v_key uuid:=(p_payload->>'request')::uuid;
 v_id uuid:=(p_payload->>'gi')::uuid; v_operation text:=p_payload->>'operation';
 v_stamp timestamptz:=statement_timestamp(); v_version uuid; v_number integer;
 v_head public.core_livingorganismevent; v_state text; v_fp text; v_event bigint;
BEGIN
 SELECT * INTO v_lo FROM public.core_livingorganism WHERE organism_id=(p_payload->>'organism')::uuid;
 IF v_lo.id IS NULL THEN RAISE EXCEPTION 'GI_AUTHORITY' USING ERRCODE='23514'; END IF;
 PERFORM public.s015_lock_organisms(ARRAY[v_lo.organism_id]);
 IF p_payload->>'circle' IS NOT NULL THEN
   SELECT id INTO v_circle FROM public.core_circle WHERE circle_uuid=(p_payload->>'circle')::uuid AND parent_organism_id=v_lo.id;
   IF v_circle IS NULL THEN RAISE EXCEPTION 'GI_AUTHORITY' USING ERRCODE='23514'; END IF;
 END IF;
 v_source:=public.gi_source(v_lo.id,v_circle,p_actor,v_operation,p_payload->>'route',v_stamp,v_stamp);
 IF v_source IS NULL OR NOT EXISTS(SELECT FROM public.core_identity i WHERE i.id=p_actor AND i.access_epoch=p_epoch) THEN
   RAISE EXCEPTION 'GI_AUTHORITY' USING ERRCODE='23514'; END IF;
 SELECT * INTO v_old FROM public.core_giact WHERE act_uuid=v_key;
 IF v_old.act_uuid IS NOT NULL THEN
   IF NOT EXISTS(SELECT FROM public.core_guidingintention previous_owner
       WHERE previous_owner.gi_uuid=v_old.gi_uuid AND previous_owner.organism_id=v_lo.id
         AND previous_owner.circle_id IS NOT DISTINCT FROM v_circle) THEN
     RAISE EXCEPTION 'GI_AUTHORITY' USING ERRCODE='23514'; END IF;
   IF v_old.payload IS DISTINCT FROM p_payload OR v_old.actor_id<>p_actor OR v_old.actor_epoch<>p_epoch THEN
     RAISE EXCEPTION 'GI_REQUEST_CONFLICT' USING ERRCODE='23514'; END IF;
   RETURN jsonb_build_object('gi',v_old.gi_uuid,'act',v_key,'version',v_old.version_uuid,'operation',v_old.operation);
 END IF;
 SELECT * INTO v_gi FROM public.core_guidingintention WHERE gi_uuid=v_id;
 IF v_operation='CREATE' THEN
   IF v_gi.gi_uuid IS NOT NULL THEN RAISE EXCEPTION 'GI_PREDECESSOR' USING ERRCODE='23514'; END IF;
   INSERT INTO public.core_guidingintention VALUES(v_id,v_lo.id,v_circle,v_key);
 ELSIF v_gi.gi_uuid IS NULL OR v_gi.organism_id<>v_lo.id OR v_gi.circle_id IS DISTINCT FROM v_circle THEN
   RAISE EXCEPTION 'GI_AUTHORITY' USING ERRCODE='23514';
 END IF;
 v_version:=CASE WHEN v_operation='RETIRE' THEN (p_payload->>'predecessor')::uuid ELSE gen_random_uuid() END;
 v_fp:=public.gi_payload_fingerprint(p_payload);
 INSERT INTO public.core_giact(act_uuid,gi_uuid,operation,version_uuid,actor_id,actor_epoch,authority_source,
   payload,fingerprint,received_at) VALUES(v_key,v_id,v_operation,v_version,p_actor,p_epoch,v_source,p_payload,v_fp,p_received);
 IF v_operation<>'RETIRE' THEN
   SELECT coalesce(max(version_number),0)+1 INTO v_number FROM public.core_giversion WHERE gi_uuid=v_id;
   INSERT INTO public.core_giversion VALUES(v_version,v_id,v_number,(p_payload->>'predecessor')::uuid,v_key,
     p_payload->>'title',p_payload->>'content');
   INSERT INTO public.core_gicitation SELECT v_version,(x->>'binding')::uuid,(x->>'version')::bigint
     FROM jsonb_array_elements(p_payload->'citations') x;
 END IF;
 SELECT * INTO STRICT v_lo FROM public.core_livingorganism WHERE id=v_lo.id FOR UPDATE;
 SELECT * INTO STRICT v_head FROM public.core_livingorganismevent WHERE id=v_lo.head_event_id;
 v_state:=public.s015_fold_effective_state('core_livingorganism',v_lo.id,v_stamp,v_stamp);
 v_event:=public.s015_append_event('core_livingorganism',v_lo.id,'RECORD_GUIDING_INTENTION',v_state,
   v_stamp,p_received,p_received,p_actor,v_lo.founding_authority_basis_id,
   public.gi_decision_reference(v_source,v_fp),'gi-act:'||v_key,'gi:'||v_key,v_fp,'{}'::jsonb,
   NULL,v_key,v_head.id,v_head.sequence::integer,NULL::bigint,NULL::text,'gi:'||v_key);
 RETURN jsonb_build_object('gi',v_id,'act',v_key,'version',v_version,'operation',v_operation);
END $$;
"""

TABLES = ("core_guidingintention", "core_giact", "core_giversion", "core_gicitation")
GUARDS = "\n".join(
    f"""CREATE TRIGGER gi_input BEFORE INSERT ON public.{table}
 FOR EACH ROW EXECUTE FUNCTION public.gi_input();
CREATE TRIGGER gi_immutable BEFORE UPDATE OR DELETE ON public.{table}
 FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE TRIGGER s015_gi_no_truncate BEFORE TRUNCATE ON public.{table}
 FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE CONSTRAINT TRIGGER gi_complete AFTER INSERT ON public.{table}
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.gi_complete();"""
    for table in TABLES
) + """
CREATE CONSTRAINT TRIGGER gi_event_complete AFTER INSERT ON public.core_livingorganismevent
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.action='RECORD_GUIDING_INTENTION')
 EXECUTE FUNCTION public.gi_complete();
"""

WRITER_BRANCH = law.WRITER_BRANCH.replace("ADOPT_LAW", "RECORD_GUIDING_INTENTION").replace(
    "LAW requires", "GI requires")
GATEWAY_BRANCH = """
    IF NEW.action='RECORD_GUIDING_INTENTION' THEN
      IF public.gi_event_matches(NEW) IS NOT TRUE THEN
        RAISE EXCEPTION 'GI_EVENT' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END IF;
"""


def switch(editor, backwards):
    base_writer = law.root.replace_once(law.root.writer_body(), " SELECT string_agg",
                                       law.WRITER_BRANCH + " SELECT string_agg")
    new_writer = law.root.replace_once(base_writer, " SELECT string_agg", WRITER_BRANCH + " SELECT string_agg")
    base_gateway = law.root.replace_once(law.gateway._ENTITLEMENT_ELECTION_GATEWAY_BODY,
                                        "BEGIN\n", "BEGIN\n" + law.GATEWAY_BRANCH)
    new_gateway = law.root.replace_once(base_gateway, "BEGIN\n", "BEGIN\n" + GATEWAY_BRANCH)
    with editor.connection.cursor() as cursor:
        cursor.execute("SELECT prosrc FROM pg_proc WHERE oid='public.s3fu_guard_gateway_effect()'::regprocedure")
        if cursor.fetchone()[0].strip() != (new_gateway if backwards else base_gateway).strip():
            raise RuntimeError("GI stopped: installed gateway differs from the commissioned body.")
    law.writer.switch_writer(editor, new_writer if backwards else base_writer,
                             base_writer if backwards else new_writer)
    editor.execute("CREATE OR REPLACE FUNCTION public.s3fu_guard_gateway_effect() "
                   "RETURNS trigger LANGUAGE plpgsql AS $$" + (base_gateway if backwards else new_gateway) + "$$;", params=None)


def install(apps, editor):
    editor.execute(FORWARD + GUARDS, params=None)
    switch(editor, False)


def reverse(apps, editor):
    editor.execute("LOCK TABLE " + ",".join("public." + t for t in TABLES) +
                   ",public.core_livingorganismevent IN ACCESS EXCLUSIVE MODE", params=None)
    with editor.connection.cursor() as cursor:
        cursor.execute("SELECT " + " OR ".join("EXISTS(SELECT FROM public." + t + ")" for t in TABLES) +
                       " OR EXISTS(SELECT FROM public.core_livingorganismevent WHERE action='RECORD_GUIDING_INTENTION')")
        if cursor.fetchone()[0]:
            raise RuntimeError("GI reversal refused while intention history exists.")
    switch(editor, True)
    editor.execute("""
DROP TRIGGER gi_event_complete ON public.core_livingorganismevent;
DROP FUNCTION public.gi_execute(jsonb,bigint,bigint,timestamptz);
DROP TABLE public.core_gicitation,public.core_giversion,public.core_giact,public.core_guidingintention;
DROP FUNCTION public.gi_complete(),public.gi_input(),public.gi_event_matches(public.core_livingorganismevent),
 public.gi_decision_reference(jsonb,text),public.gi_payload_fingerprint(jsonb),
 public.gi_laws_ok(uuid,bigint,jsonb,timestamptz,timestamptz),
 public.gi_source(bigint,bigint,bigint,text,text,timestamptz,timestamptz);
""", params=None)


class Migration(migrations.Migration):
    dependencies: ClassVar = [("core", "0070_law_route_parameter_qualification")]
    operations: ClassVar = [migrations.RunPython(install, reverse)]
