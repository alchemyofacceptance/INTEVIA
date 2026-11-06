"""DISC1 P1: board/category lineage and subscriber capability projection.

HG DISC1 P0 rulings, 6 November 2026. No CQ, posts, retention or screens.
"""
from importlib import import_module
from typing import ClassVar

from django.db import migrations

gi = import_module("core.migrations.0071_guiding_intentions")
se = import_module("core.migrations.0046_se_entitlement_foundation")

# A derived capability: old grants keep their bytes and gain the newly delivered
# subscriber feature under HD-X-1 D1. No unrelated existing capability is a proxy.
CAPABILITY = "DISCUSSION_CUSTOM_CATEGORIES"
SE_BODY = se._ENTITLEMENT_EFFECTIVE_BODY.replace(
    "AND entitlement.capabilities ? p_capability",
    """AND (entitlement.capabilities ? p_capability OR (
           p_capability='DISCUSSION_CUSTOM_CATEGORIES'
           AND entitlement.capabilities ?| ARRAY["""
    + ",".join("'" + code + "'" for code in se.CAPABILITY_CODES)
    + """ ]::text[]
           AND EXISTS(SELECT FROM public.core_organismentitlementevent granted
             WHERE granted.entitlement_id=entitlement.id AND granted.sequence=1
               AND granted.event_type='GRANTED')) )""",
)
SE_HEADER = """CREATE OR REPLACE FUNCTION public.s015_entitlement_effective(
p_living_organism_uuid uuid,p_capability text,p_at timestamptz)
RETURNS boolean LANGUAGE sql STABLE STRICT AS $$"""

TABLES = ("core_discboard", "core_discact", "core_disccategory", "core_disccategoryversion")
FORWARD = r"""
CREATE TABLE public.core_discboard (
 board_uuid uuid PRIMARY KEY,
 organism_id bigint NOT NULL REFERENCES public.core_livingorganism,
 circle_id bigint REFERENCES public.core_circle,
 creation_act uuid NOT NULL UNIQUE
);
CREATE UNIQUE INDEX disc_lo_board ON public.core_discboard(organism_id) WHERE circle_id IS NULL;
CREATE UNIQUE INDEX disc_circle_board ON public.core_discboard(circle_id) WHERE circle_id IS NOT NULL;
CREATE TABLE public.core_discact (
 act_uuid uuid PRIMARY KEY,
 board_uuid uuid NOT NULL REFERENCES public.core_discboard,
 operation text NOT NULL CHECK(operation IN ('PROVISION','CREATE_CATEGORY','REVISE_CATEGORY','RETIRE_CATEGORY','PROBATION_ACCESS','WELCOME')),
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
ALTER TABLE public.core_discboard ADD FOREIGN KEY(creation_act)
 REFERENCES public.core_discact DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE public.core_disccategory (
 category_uuid uuid PRIMARY KEY,
 board_uuid uuid NOT NULL REFERENCES public.core_discboard,
 functional_code text CHECK(functional_code IN ('COORDINATION','DISCUSSION','ANNOUNCEMENTS','WELCOME')),
 creation_act uuid NOT NULL REFERENCES public.core_discact,
 UNIQUE(board_uuid,functional_code)
);
CREATE TABLE public.core_disccategoryversion (
 version_uuid uuid PRIMARY KEY,
 category_uuid uuid NOT NULL REFERENCES public.core_disccategory,
 act_uuid uuid NOT NULL REFERENCES public.core_discact,
 version_number integer NOT NULL CHECK(version_number>0),
 predecessor uuid UNIQUE REFERENCES public.core_disccategoryversion,
 name text NOT NULL CHECK(length(name) BETWEEN 1 AND 45 AND btrim(name)<>''),
 description text NOT NULL,
 sort_order integer NOT NULL,
 access_level text NOT NULL CHECK(access_level IN ('COORDINATORS','MEMBERS','PROBATIONARY')),
 retired boolean NOT NULL,
 UNIQUE(category_uuid,version_number), UNIQUE(category_uuid,act_uuid)
);
CREATE VIEW public.disc_categories AS
 SELECT c.*,v.version_uuid,v.version_number,v.name,v.description,v.sort_order,v.access_level,v.retired,
        v.act_uuid AS latest_act
 FROM public.core_disccategory c JOIN LATERAL (
   SELECT x.* FROM public.core_disccategoryversion x WHERE x.category_uuid=c.category_uuid
   ORDER BY x.version_number DESC LIMIT 1
 ) v ON true;

CREATE FUNCTION public.disc_member_state(p_lo bigint,p_person bigint,p_cut timestamptz)
 RETURNS text LANGUAGE sql STABLE AS $$
 SELECT public.s015_fold_effective_state('core_organismmembership',m.id,p_cut,p_cut)
 FROM public.core_organismmembership m WHERE m.living_organism_id=p_lo AND m.identity_id=p_person
$$;
CREATE FUNCTION public.disc_office(p_lo bigint,p_circle bigint,p_person bigint,p_cut timestamptz)
 RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE v_assignment uuid; v_organism uuid; v_circle uuid;
BEGIN
 SELECT organism_id INTO v_organism FROM public.core_livingorganism WHERE id=p_lo;
 IF p_circle IS NULL THEN
   v_assignment:=public.circle_lo_office(p_lo,p_person,p_cut,p_cut);
 ELSE
   SELECT a.assignment_uuid,c.circle_uuid INTO v_assignment,v_circle
   FROM public.core_circle c JOIN public.core_livingorganism lo ON lo.id=c.parent_organism_id
   CROSS JOIN LATERAL public.circle_holders(c.id,p_cut,p_cut) h
   JOIN public.core_organismmembership m ON m.identity_id=h.person AND m.living_organism_id=p_lo
   JOIN public.core_contextualroleassignment a ON a.membership_id=m.id AND a.circle_id=c.id
   JOIN public.c2_seat_facts(p_lo,p_cut,p_cut) f ON f.assignment_id=a.id
   WHERE c.id=p_circle AND c.parent_organism_id=p_lo AND h.person=p_person
     AND f.role_code='COORDINATOR' AND f.role_scope='CIRCLE' AND f.standing='QUALIFIED'
     AND public.s015_fold_effective_state('core_circle',c.id,p_cut,p_cut)='ACTIVE'
     AND public.s015_fold_effective_state('core_livingorganism',p_lo,p_cut,p_cut)='ACTIVE'
     AND public.c2_basis_in_force(lo.founding_authority_basis_id,p_cut,p_cut)
     AND (NOT lo.platform_root OR public.r_circle_root_qualified(p_lo,p_cut,p_cut))
   ORDER BY a.id LIMIT 1;
 END IF;
 IF v_assignment IS NULL OR NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
   WHERE i.id=p_person AND i.access_state='active' AND u.is_active) THEN RETURN NULL; END IF;
 RETURN jsonb_build_object('kind','OFFICE','reference',v_assignment,'organism',v_organism,'circle',v_circle,
   'route',CASE WHEN p_circle IS NULL THEN 'COORDINATOR_ROUTE' ELSE 'CIRCLE_COORDINATOR' END);
END $$;
CREATE FUNCTION public.disc_probation_open(p_lo bigint,p_cut timestamptz)
 RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT coalesce((SELECT (a.payload->>'enabled')::boolean FROM public.core_discboard b
   JOIN public.core_discact a ON a.board_uuid=b.board_uuid
   JOIN public.core_livingorganismevent e ON e.event_uuid=a.act_uuid
   WHERE b.organism_id=p_lo AND b.circle_id IS NULL AND a.operation='PROBATION_ACCESS'
     AND a.effective_at<=p_cut AND a.recorded_at<=p_cut
   ORDER BY e.sequence DESC LIMIT 1),false)
$$;
CREATE FUNCTION public.disc_audience(p_lo bigint,p_circle bigint,p_person bigint,p_level text,p_cut timestamptz)
 RETURNS boolean LANGUAGE plpgsql STABLE AS $$
DECLARE v_state text; v_office jsonb;
BEGIN
 IF NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
   WHERE i.id=p_person AND i.access_state='active' AND u.is_active) THEN RETURN false; END IF;
 v_state:=public.disc_member_state(p_lo,p_person,p_cut);
 IF coalesce(v_state IN ('ACTIVE','PROBATIONARY','RESTRICTED','SUSPENDED'),false) IS NOT TRUE THEN RETURN false; END IF;
 IF p_circle IS NOT NULL AND NOT EXISTS(SELECT FROM public.core_circle c WHERE c.id=p_circle
   AND c.parent_organism_id=p_lo) THEN RETURN false; END IF;
 v_office:=public.disc_office(p_lo,p_circle,p_person,p_cut);
 IF p_level='COORDINATORS' THEN RETURN v_office IS NOT NULL; END IF;
 IF p_level NOT IN ('MEMBERS','PROBATIONARY') THEN RETURN false; END IF;
 IF v_state='PROBATIONARY' THEN
   RETURN p_level='PROBATIONARY' AND public.disc_probation_open(p_lo,p_cut)
     AND (p_circle IS NULL OR EXISTS(SELECT FROM public.core_discboard b
       JOIN public.disc_categories c ON c.board_uuid=b.board_uuid
       WHERE b.organism_id=p_lo AND b.circle_id=p_circle AND c.access_level='PROBATIONARY'));
 END IF;
 IF p_circle IS NULL THEN RETURN true; END IF;
 -- Read the continuing recorded place, not ACTIVE-only circle_place_at.
 RETURN EXISTS(SELECT FROM public.core_circleparticipation p
   JOIN public.core_organismmembership m ON m.id=p.membership_id
   WHERE p.circle_id=p_circle AND m.identity_id=p_person AND m.living_organism_id=p_lo
     AND p.effective_at<=p_cut AND p.received_at<=p_cut AND p.recorded_at<=p_cut
     AND NOT EXISTS(SELECT FROM public.core_circleparticipationend x WHERE x.participation_id=p.id
       AND x.effective_at<=p_cut AND x.received_at<=p_cut AND x.recorded_at<=p_cut));
END $$;
CREATE FUNCTION public.disc_source(p_board uuid,p_person bigint,p_operation text,p_cut timestamptz)
 RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE v_board public.core_discboard; v_state text;
BEGIN
 SELECT * INTO v_board FROM public.core_discboard WHERE board_uuid=p_board;
 IF v_board.board_uuid IS NULL THEN RETURN NULL; END IF;
 v_state:=public.disc_member_state(v_board.organism_id,p_person,p_cut);
 IF p_operation='PROVISION' THEN
   IF v_state='ACTIVE' AND public.disc_audience(v_board.organism_id,v_board.circle_id,p_person,'MEMBERS',p_cut) THEN
     RETURN jsonb_build_object('kind','RULE','reference','HG:DISC1:D1:20261106','route','PROVISION');
   END IF;
   RETURN NULL;
 END IF;
 IF p_operation='PROBATION_ACCESS' AND v_board.circle_id IS NOT NULL THEN RETURN NULL; END IF;
 RETURN public.disc_office(v_board.organism_id,v_board.circle_id,p_person,p_cut);
END $$;
CREATE FUNCTION public.disc_fingerprint(p_payload jsonb) RETURNS text LANGUAGE sql IMMUTABLE STRICT AS $$
 SELECT encode(sha256(convert_to('INTEVIA:DISC1:1:'||public.l_canonical(p_payload),'UTF8')),'hex')
$$;
CREATE FUNCTION public.disc_decision(p_source jsonb,p_fingerprint text) RETURNS text LANGUAGE sql IMMUTABLE STRICT AS $$
 SELECT 's015d1:'||encode(sha256(convert_to('DISC1:1:'||public.l_canonical(p_source)||p_fingerprint,'UTF8')),'hex')
$$;

CREATE FUNCTION public.disc_input() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_board public.core_discboard; v_act public.core_discact; v_cat public.disc_categories;
 v_lo uuid; v_circle_uuid uuid; v_stamp timestamptz:=statement_timestamp(); v_source jsonb; v_count integer;
BEGIN
 IF TG_TABLE_NAME='core_discboard' THEN
   SELECT organism_id INTO v_lo FROM public.core_livingorganism WHERE id=NEW.organism_id;
   PERFORM public.s015_lock_organisms(ARRAY[v_lo]);
   IF NEW.circle_id IS NOT NULL AND NOT EXISTS(SELECT FROM public.core_circle c
     WHERE c.id=NEW.circle_id AND c.parent_organism_id=NEW.organism_id) THEN
     RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
   RETURN NEW;
 ELSIF TG_TABLE_NAME='core_discact' THEN
   SELECT * INTO STRICT v_board FROM public.core_discboard WHERE board_uuid=NEW.board_uuid;
   SELECT organism_id INTO v_lo FROM public.core_livingorganism WHERE id=v_board.organism_id;
   PERFORM public.s015_lock_organisms(ARRAY[v_lo]);
   IF NEW.execution_xid IS NOT NULL THEN RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
   NEW.execution_xid:=pg_current_xact_id(); NEW.effective_at:=v_stamp; NEW.recorded_at:=v_stamp;
   v_source:=public.disc_source(NEW.board_uuid,NEW.actor_id,NEW.operation,v_stamp);
   IF v_source IS NULL OR v_source IS DISTINCT FROM NEW.authority_source
     OR public.disc_source(NEW.board_uuid,NEW.actor_id,NEW.operation,NEW.received_at) IS DISTINCT FROM v_source THEN
     RAISE EXCEPTION 'DISC_AUTHORITY' USING ERRCODE='23514'; END IF;
   SELECT circle_uuid INTO v_circle_uuid FROM public.core_circle WHERE id=v_board.circle_id;
   IF NEW.received_at>v_stamp OR NEW.fingerprint IS DISTINCT FROM public.disc_fingerprint(NEW.payload)
     OR NEW.payload->>'organism' IS DISTINCT FROM v_lo::text
     OR NEW.payload->>'circle' IS DISTINCT FROM v_circle_uuid::text
     OR NEW.payload->>'request' IS DISTINCT FROM NEW.act_uuid::text
     OR NEW.payload->>'board' IS DISTINCT FROM NEW.board_uuid::text
     OR NEW.payload->>'operation' IS DISTINCT FROM NEW.operation
     OR NOT EXISTS(SELECT FROM public.core_identity i WHERE i.id=NEW.actor_id AND i.access_epoch=NEW.actor_epoch) THEN
     RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
   IF NEW.operation='PROVISION' THEN
     IF v_board.creation_act<>NEW.act_uuid OR EXISTS(SELECT FROM public.core_discact a WHERE a.board_uuid=NEW.board_uuid) THEN
       RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
   ELSIF NEW.operation='PROBATION_ACCESS' THEN
     IF jsonb_typeof(NEW.payload->'enabled') IS DISTINCT FROM 'boolean' THEN
       RAISE EXCEPTION 'DISC_INPUT' USING ERRCODE='23514'; END IF;
   ELSIF NEW.operation='WELCOME' THEN
     IF EXISTS(SELECT FROM public.core_disccategory c
       WHERE c.board_uuid=NEW.board_uuid AND c.functional_code='WELCOME') THEN
       RAISE EXCEPTION 'DISC_WELCOME_OPEN' USING ERRCODE='23514'; END IF;
     IF NOT public.disc_probation_open(v_board.organism_id,v_stamp) THEN
       RAISE EXCEPTION 'DISC_PROBATION' USING ERRCODE='23514'; END IF;
   ELSE
     SELECT * INTO v_cat FROM public.disc_categories WHERE category_uuid=(NEW.payload->>'category')::uuid;
     IF NEW.operation='CREATE_CATEGORY' THEN
       IF v_cat.category_uuid IS NOT NULL AND v_cat.board_uuid<>NEW.board_uuid THEN
         RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
       IF v_cat.category_uuid IS NOT NULL THEN RAISE EXCEPTION 'DISC_PREDECESSOR' USING ERRCODE='23514'; END IF;
       SELECT count(*) INTO v_count FROM public.disc_categories c WHERE c.board_uuid=NEW.board_uuid
         AND c.functional_code IS NULL AND NOT c.retired;
       IF v_count>=9 AND NOT public.s015_entitlement_effective(v_lo,'DISCUSSION_CUSTOM_CATEGORIES',v_stamp) THEN -- DISC_FREE_CAP
         RAISE EXCEPTION 'DISC_CATEGORY_LIMIT' USING ERRCODE='23514'; END IF;
     ELSE
       IF v_cat.category_uuid IS NULL OR v_cat.board_uuid<>NEW.board_uuid THEN
         RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
       IF v_cat.functional_code IS NOT NULL THEN RAISE EXCEPTION 'DISC_FUNCTIONAL' USING ERRCODE='23514'; END IF;
       IF NEW.payload->>'predecessor' IS DISTINCT FROM v_cat.version_uuid::text OR v_cat.retired THEN
         RAISE EXCEPTION 'DISC_PREDECESSOR' USING ERRCODE='23514'; END IF;
     END IF;
     IF NEW.operation<>'RETIRE_CATEGORY' THEN
       IF jsonb_typeof(NEW.payload->'name') IS DISTINCT FROM 'string' OR length(NEW.payload->>'name') NOT BETWEEN 1 AND 45
         OR btrim(NEW.payload->>'name')='' OR jsonb_typeof(NEW.payload->'description') IS DISTINCT FROM 'string'
         OR NEW.payload->>'access' NOT IN ('COORDINATORS','MEMBERS','PROBATIONARY')
         OR jsonb_typeof(NEW.payload->'order') IS DISTINCT FROM 'number'
         OR NEW.payload->>'order' !~ '^-?[0-9]+$' THEN RAISE EXCEPTION 'DISC_INPUT' USING ERRCODE='23514'; END IF;
       IF NEW.payload->>'access'='PROBATIONARY' AND NOT public.disc_probation_open(v_board.organism_id,v_stamp) THEN
         RAISE EXCEPTION 'DISC_PROBATION' USING ERRCODE='23514'; END IF;
     END IF;
   END IF;
   RETURN NEW;
 ELSIF TG_TABLE_NAME='core_disccategory' THEN
   SELECT * INTO STRICT v_act FROM public.core_discact WHERE act_uuid=NEW.creation_act;
   IF v_act.execution_xid<>pg_current_xact_id() OR v_act.board_uuid<>NEW.board_uuid
     OR NOT ((v_act.operation='PROVISION' AND NEW.functional_code IN ('COORDINATION','DISCUSSION','ANNOUNCEMENTS'))
       OR (v_act.operation='WELCOME' AND NEW.functional_code='WELCOME')
       OR (v_act.operation='CREATE_CATEGORY' AND NEW.functional_code IS NULL
           AND v_act.payload->>'category'=NEW.category_uuid::text)) THEN
     RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
   RETURN NEW;
 ELSE
   SELECT * INTO STRICT v_act FROM public.core_discact WHERE act_uuid=NEW.act_uuid;
   SELECT * INTO v_cat FROM public.disc_categories WHERE category_uuid=NEW.category_uuid;
   SELECT count(*) INTO v_count FROM public.core_disccategoryversion v WHERE v.category_uuid=NEW.category_uuid;
   IF v_act.execution_xid<>pg_current_xact_id() OR NEW.version_number<>v_count+1
     OR NEW.predecessor IS DISTINCT FROM v_cat.version_uuid OR NOT EXISTS(
       SELECT FROM public.core_disccategory c WHERE c.category_uuid=NEW.category_uuid AND c.board_uuid=v_act.board_uuid) THEN
     RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
   IF v_act.operation IN ('CREATE_CATEGORY','REVISE_CATEGORY') THEN
     IF NEW.name IS DISTINCT FROM v_act.payload->>'name' OR NEW.description IS DISTINCT FROM v_act.payload->>'description'
       OR NEW.access_level IS DISTINCT FROM v_act.payload->>'access' OR NEW.sort_order IS DISTINCT FROM (v_act.payload->>'order')::integer
       OR NEW.retired OR NEW.category_uuid::text IS DISTINCT FROM v_act.payload->>'category' THEN
       RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
   ELSIF v_act.operation='RETIRE_CATEGORY' THEN
     IF NOT NEW.retired OR NEW.name<>v_cat.name OR NEW.description<>v_cat.description OR NEW.sort_order<>v_cat.sort_order
       OR NEW.access_level<>v_cat.access_level OR NEW.category_uuid::text IS DISTINCT FROM v_act.payload->>'category' THEN
       RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
   ELSIF v_act.operation IN ('PROVISION','WELCOME') THEN
     IF NEW.predecessor IS NOT NULL OR NEW.retired OR NOT EXISTS(
       SELECT FROM public.core_disccategory c WHERE c.category_uuid=NEW.category_uuid
         AND c.creation_act=v_act.act_uuid AND
         (c.functional_code,NEW.name,NEW.access_level,NEW.sort_order) IN
           (('COORDINATION','Coordination','COORDINATORS',1),('DISCUSSION','Discussion','MEMBERS',2),
            ('ANNOUNCEMENTS','Announcements','MEMBERS',3),('WELCOME','Welcome','PROBATIONARY',4)))
       OR NEW.description<>'' THEN RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
   ELSE RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
   RETURN NEW;
 END IF;
END $$;

CREATE FUNCTION public.disc_event_matches(p_event public.core_livingorganismevent) RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT EXISTS(SELECT FROM public.core_discact a JOIN public.core_discboard b ON b.board_uuid=a.board_uuid
 JOIN public.core_livingorganism lo ON lo.id=b.organism_id WHERE a.act_uuid=p_event.event_uuid
 AND a.execution_xid=pg_current_xact_id() AND p_event.action='RECORD_DISCUSSION'
 AND p_event.living_organism_id=b.organism_id AND p_event.actor_state='LIVING_ORGANISM'
 AND p_event.actor_capacity='STANDING' AND p_event.actor_id IS NULL AND p_event.actor_access_epoch IS NULL
 AND p_event.entry_mode='TRANSPORT' AND p_event.prior_state=p_event.resulting_state AND p_event.sequence>1
 AND p_event.enterer_identity_id=a.actor_id AND p_event.authority_basis_id=lo.founding_authority_basis_id
 AND p_event.authority_decision_reference=public.disc_decision(a.authority_source,a.fingerprint)
 AND p_event.evidence_reference='disc-act:'||a.act_uuid AND p_event.payload_fingerprint=a.fingerprint
 AND p_event.request_reference='disc:'||a.act_uuid AND p_event.idempotency_key='disc:'||a.act_uuid
 AND p_event.received_at=a.received_at AND p_event.occurred_at=a.received_at
 AND p_event.effective_at=a.effective_at AND p_event.recorded_at=a.recorded_at
 AND p_event.composing_rule_state='NA_TRANSPORT' AND p_event.composer_state='NA_TRANSPORT'
 AND p_event.effective_until_state='NONE')
$$;
CREATE FUNCTION public.disc_complete() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_key uuid; v_act public.core_discact; v_expected integer; v_actual integer;
BEGIN
 IF TG_TABLE_NAME='core_discboard' OR TG_TABLE_NAME='core_disccategory' THEN v_key:=NEW.creation_act;
 ELSIF TG_TABLE_NAME='core_livingorganismevent' THEN v_key:=NEW.event_uuid;
 ELSE v_key:=NEW.act_uuid; END IF;
 SELECT * INTO v_act FROM public.core_discact WHERE act_uuid=v_key;
 v_expected:=CASE v_act.operation WHEN 'PROVISION' THEN 3 WHEN 'PROBATION_ACCESS' THEN 0 ELSE 1 END;
 SELECT count(*) INTO v_actual FROM public.core_disccategoryversion v WHERE v.act_uuid=v_key;
 IF v_act.act_uuid IS NULL OR v_actual<>v_expected OR NOT EXISTS(SELECT FROM public.core_livingorganismevent e
   WHERE e.event_uuid=v_key AND public.disc_event_matches(e))
   OR public.disc_source(v_act.board_uuid,v_act.actor_id,v_act.operation,statement_timestamp()) IS DISTINCT FROM v_act.authority_source
   OR NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
      WHERE i.id=v_act.actor_id AND i.access_epoch=v_act.actor_epoch AND i.access_state='active' AND u.is_active) THEN
   RAISE EXCEPTION 'DISC_COMPLETE' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;

CREATE FUNCTION public.disc_execute(p_payload jsonb,p_actor bigint,p_epoch bigint,p_received timestamptz)
 RETURNS jsonb LANGUAGE plpgsql VOLATILE AS $$
DECLARE v_board public.core_discboard; v_lo public.core_livingorganism; v_circle bigint;
 v_old public.core_discact; v_source jsonb; v_stamp timestamptz:=statement_timestamp();
 v_key uuid:=(p_payload->>'request')::uuid; v_board_key uuid:=(p_payload->>'board')::uuid;
 v_op text:=p_payload->>'operation'; v_cat public.disc_categories; v_id uuid; v_fp text;
 v_head public.core_livingorganismevent; v_state text; v_event bigint; v_code text; v_other_board uuid;
 v_conflict_name text; v_conflict_table text; v_conflict_schema text;
BEGIN
 -- Serialize the global request identity before the owning-organism lock.
 PERFORM pg_advisory_xact_lock(hashtextextended('DISC1:request:'||v_key::text,0));
 SELECT * INTO v_lo FROM public.core_livingorganism WHERE organism_id=(p_payload->>'organism')::uuid;
 IF v_lo.id IS NULL THEN RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 PERFORM public.s015_lock_organisms(ARRAY[v_lo.organism_id]);
 IF p_payload->>'circle' IS NOT NULL THEN
   SELECT id INTO v_circle FROM public.core_circle WHERE circle_uuid=(p_payload->>'circle')::uuid AND parent_organism_id=v_lo.id;
   IF v_circle IS NULL THEN RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 END IF;
 IF NOT public.disc_audience(v_lo.id,v_circle,p_actor,'MEMBERS',v_stamp)
   AND NOT public.disc_audience(v_lo.id,v_circle,p_actor,'PROBATIONARY',v_stamp) THEN
   RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 SELECT * INTO v_board FROM public.core_discboard WHERE organism_id=v_lo.id AND circle_id IS NOT DISTINCT FROM v_circle;
 IF (v_board.board_uuid IS NULL AND v_op<>'PROVISION')
   OR (v_board.board_uuid IS NOT NULL AND v_board.board_uuid<>v_board_key) THEN
   RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 IF v_board.board_uuid IS NULL THEN
   -- Resolve D1 without inserting a board before the global reuse checks.
   IF public.disc_member_state(v_lo.id,p_actor,v_stamp)='ACTIVE' THEN
     v_source:=jsonb_build_object('kind','RULE','reference','HG:DISC1:D1:20261106','route','PROVISION');
   END IF;
 ELSE
   v_source:=public.disc_source(v_board_key,p_actor,v_op,v_stamp);
 END IF;
 IF v_source IS NULL THEN RAISE EXCEPTION 'DISC_AUTHORITY' USING ERRCODE='23514'; END IF;
 -- 0072 also works alone; 0073's act table exists only after that migration.
 IF to_regclass('public.core_discpostact') IS NOT NULL THEN
   EXECUTE 'SELECT board_uuid FROM public.core_discpostact WHERE act_uuid=$1' INTO v_other_board USING v_key;
   IF v_other_board IS NOT NULL THEN
     IF v_other_board<>v_board_key THEN RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
     RAISE EXCEPTION 'DISC_REQUEST_CONFLICT' USING ERRCODE='23514';
   END IF;
 END IF;
 SELECT * INTO v_old FROM public.core_discact WHERE act_uuid=v_key;
 IF v_old.act_uuid IS NOT NULL THEN
   IF v_old.board_uuid<>v_board_key THEN RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
   IF v_old.payload IS DISTINCT FROM p_payload OR v_old.actor_id<>p_actor OR v_old.actor_epoch<>p_epoch THEN
     RAISE EXCEPTION 'DISC_REQUEST_CONFLICT' USING ERRCODE='23514'; END IF;
   RETURN jsonb_build_object('board',v_board_key,'act',v_key,'operation',v_op);
 END IF;
 -- A request belonging to any other shared-chain domain is unavailable.
 IF EXISTS(SELECT FROM public.core_livingorganismevent e WHERE e.event_uuid=v_key) THEN
   RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 IF v_op='PROVISION' AND v_board.board_uuid IS NOT NULL THEN
   RETURN jsonb_build_object('board',v_board.board_uuid,'act',v_board.creation_act,'operation',v_op);
 END IF;
 IF v_op='PROVISION' THEN
   INSERT INTO public.core_discboard VALUES(v_board_key,v_lo.id,v_circle,v_key);
 END IF;
 v_fp:=public.disc_fingerprint(p_payload);
 INSERT INTO public.core_discact(act_uuid,board_uuid,operation,actor_id,actor_epoch,authority_source,payload,fingerprint,received_at)
 VALUES(v_key,v_board_key,v_op,p_actor,p_epoch,v_source,p_payload,v_fp,p_received);
 IF v_op IN ('PROVISION','WELCOME') THEN
   FOREACH v_code IN ARRAY (CASE WHEN v_op='PROVISION' THEN ARRAY['COORDINATION','DISCUSSION','ANNOUNCEMENTS'] ELSE ARRAY['WELCOME'] END) LOOP
     v_id:=gen_random_uuid();
     INSERT INTO public.core_disccategory VALUES(v_id,v_board_key,v_code,v_key);
     INSERT INTO public.core_disccategoryversion VALUES(gen_random_uuid(),v_id,v_key,1,NULL,initcap(v_code),'',
       CASE v_code WHEN 'COORDINATION' THEN 1 WHEN 'DISCUSSION' THEN 2 WHEN 'ANNOUNCEMENTS' THEN 3 ELSE 4 END,
       CASE v_code WHEN 'COORDINATION' THEN 'COORDINATORS' WHEN 'WELCOME' THEN 'PROBATIONARY' ELSE 'MEMBERS' END,false);
   END LOOP;
 ELSIF v_op IN ('CREATE_CATEGORY','REVISE_CATEGORY','RETIRE_CATEGORY') THEN
   v_id:=(p_payload->>'category')::uuid;
   SELECT * INTO v_cat FROM public.disc_categories WHERE category_uuid=v_id;
   IF v_op='CREATE_CATEGORY' THEN INSERT INTO public.core_disccategory VALUES(v_id,v_board_key,NULL,v_key); END IF;
   INSERT INTO public.core_disccategoryversion VALUES(gen_random_uuid(),v_id,v_key,coalesce(v_cat.version_number,0)+1,
     v_cat.version_uuid,CASE WHEN v_op='RETIRE_CATEGORY' THEN v_cat.name ELSE p_payload->>'name' END,
     CASE WHEN v_op='RETIRE_CATEGORY' THEN v_cat.description ELSE p_payload->>'description' END,
     CASE WHEN v_op='RETIRE_CATEGORY' THEN v_cat.sort_order ELSE (p_payload->>'order')::integer END,
     CASE WHEN v_op='RETIRE_CATEGORY' THEN v_cat.access_level ELSE p_payload->>'access' END,v_op='RETIRE_CATEGORY');
 END IF;
 SELECT * INTO STRICT v_lo FROM public.core_livingorganism WHERE id=v_lo.id FOR UPDATE;
 SELECT * INTO STRICT v_head FROM public.core_livingorganismevent WHERE id=v_lo.head_event_id;
 v_state:=public.s015_fold_effective_state('core_livingorganism',v_lo.id,v_stamp,v_stamp);
 v_event:=public.s015_append_event('core_livingorganism',v_lo.id,'RECORD_DISCUSSION',v_state,
   v_stamp,p_received,p_received,p_actor,v_lo.founding_authority_basis_id,
   public.disc_decision(v_source,v_fp),'disc-act:'||v_key,'disc:'||v_key,v_fp,'{}'::jsonb,
   NULL,v_key,v_head.id,v_head.sequence::integer,NULL::bigint,NULL::text,'disc:'||v_key);
 RETURN jsonb_build_object('board',v_board_key,'act',v_key,'operation',v_op);
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
    f"""CREATE TRIGGER disc_input BEFORE INSERT ON public.{table}
 FOR EACH ROW EXECUTE FUNCTION public.disc_input();
CREATE TRIGGER disc_immutable BEFORE UPDATE OR DELETE ON public.{table}
 FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE TRIGGER s015_disc_no_truncate BEFORE TRUNCATE ON public.{table}
 FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE CONSTRAINT TRIGGER disc_complete AFTER INSERT ON public.{table}
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.disc_complete();"""
    for table in TABLES
) + """
CREATE CONSTRAINT TRIGGER disc_event_complete AFTER INSERT ON public.core_livingorganismevent
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.action='RECORD_DISCUSSION')
 EXECUTE FUNCTION public.disc_complete();
"""
WRITER_BRANCH = gi.WRITER_BRANCH.replace("RECORD_GUIDING_INTENTION", "RECORD_DISCUSSION").replace("GI requires", "DISC requires")
GATEWAY_BRANCH = gi.GATEWAY_BRANCH.replace("RECORD_GUIDING_INTENTION", "RECORD_DISCUSSION").replace("gi_event_matches", "disc_event_matches").replace("GI_EVENT", "DISC_RECORD")


def bodies():
    writer = gi.law.root.replace_once(gi.law.root.writer_body(), " SELECT string_agg", gi.law.WRITER_BRANCH + " SELECT string_agg")
    writer = gi.law.root.replace_once(writer, " SELECT string_agg", gi.WRITER_BRANCH + " SELECT string_agg")
    gateway = gi.law.root.replace_once(gi.law.gateway._ENTITLEMENT_ELECTION_GATEWAY_BODY, "BEGIN\n", "BEGIN\n" + gi.law.GATEWAY_BRANCH)
    gateway = gi.law.root.replace_once(gateway, "BEGIN\n", "BEGIN\n" + gi.GATEWAY_BRANCH)
    return writer, gateway


def switch(editor, backwards=False):
    old_writer, old_gateway = bodies()
    new_writer = gi.law.root.replace_once(old_writer, " SELECT string_agg", WRITER_BRANCH + " SELECT string_agg")
    new_gateway = gi.law.root.replace_once(old_gateway, "BEGIN\n", "BEGIN\n" + GATEWAY_BRANCH)
    with editor.connection.cursor() as cursor:
        for signature, expected in (("public.s3fu_guard_gateway_effect()", new_gateway if backwards else old_gateway),
                                    ("public.s015_entitlement_effective(uuid,text,timestamptz)", SE_BODY if backwards else se._ENTITLEMENT_EFFECTIVE_BODY)):
            cursor.execute("SELECT prosrc FROM pg_proc WHERE oid=%s::regprocedure", [signature])
            if cursor.fetchone()[0].strip() != expected.strip():
                raise RuntimeError("DISC1 stopped: installed function differs from the commissioned body: " + signature)
    gi.law.writer.switch_writer(editor, new_writer if backwards else old_writer, old_writer if backwards else new_writer)
    editor.execute("CREATE OR REPLACE FUNCTION public.s3fu_guard_gateway_effect() RETURNS trigger LANGUAGE plpgsql AS $$" + (old_gateway if backwards else new_gateway) + "$$;", params=None)
    editor.execute(SE_HEADER + (se._ENTITLEMENT_EFFECTIVE_BODY if backwards else SE_BODY) + "$$;", params=None)


def install(apps, editor):
    editor.execute(FORWARD + GUARDS, params=None)
    switch(editor)


def reverse(apps, editor):
    editor.execute("LOCK TABLE " + ",".join("public." + t for t in TABLES) + ",public.core_livingorganismevent IN ACCESS EXCLUSIVE MODE", params=None)
    with editor.connection.cursor() as cursor:
        cursor.execute("SELECT " + " OR ".join("EXISTS(SELECT FROM public." + t + ")" for t in TABLES))
        if cursor.fetchone()[0]:
            raise RuntimeError("DISC1 reversal refused while discussion history exists.")
    switch(editor, True)
    editor.execute("""DROP TRIGGER disc_event_complete ON public.core_livingorganismevent;
DROP VIEW public.disc_categories;
DROP TABLE public.core_disccategoryversion,public.core_disccategory,public.core_discact,public.core_discboard;
DROP FUNCTION public.disc_input(),public.disc_complete(),
 public.disc_member_state(bigint,bigint,timestamptz),public.disc_office(bigint,bigint,bigint,timestamptz),
 public.disc_probation_open(bigint,timestamptz),public.disc_audience(bigint,bigint,bigint,text,timestamptz),
 public.disc_source(uuid,bigint,text,timestamptz),public.disc_fingerprint(jsonb),public.disc_decision(jsonb,text),
 public.disc_event_matches(public.core_livingorganismevent),public.disc_execute(jsonb,bigint,bigint,timestamptz);
""", params=None)


class Migration(migrations.Migration):
    atomic = True
    dependencies: ClassVar = [("core", "0071_guiding_intentions")]
    operations: ClassVar = [migrations.RunPython(install, reverse)]
