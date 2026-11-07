"""DISC2 P1. Immutable CQ acts, settings, preparation and version-bound Clarity."""

from importlib import import_module
from typing import ClassVar

from django.db import migrations

posts = import_module("core.migrations.0073_discussion_posts")
boards = posts.boards
replace_once = boards.gi.law.root.replace_once
FORWARD = r"""
CREATE TABLE public.core_cqreportingdefault (
 reporting_version text PRIMARY KEY DEFAULT 'DISC2:20261107:D12d:default' CHECK(reporting_version='DISC2:20261107:D12d:default'),
 ruling_basis text NOT NULL DEFAULT 'HG:DISC2:20261107:D12d/H4' CHECK(ruling_basis='HG:DISC2:20261107:D12d/H4'),
 reporting text NOT NULL DEFAULT 'NAMED' CHECK(reporting='NAMED')
);
CREATE TRIGGER cq_default_immutable BEFORE UPDATE OR DELETE ON public.core_cqreportingdefault
 FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE TRIGGER s015_cq_default_no_truncate BEFORE TRUNCATE ON public.core_cqreportingdefault
 FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation();
INSERT INTO public.core_cqreportingdefault DEFAULT VALUES;
CREATE VIEW public.cq_platform_defaults AS
 SELECT reporting_version,ruling_basis,
 '{"enabled":false,"preparation_days":3,"clarity_days":7,"vote_days":7,"cdpt_percent":null,
 "quorum_form":"MAJORITY","quorum_value":null,"threshold_base":"VOTES_CAST","threshold":"MAJORITY",
 "numerator":null,"denominator":null,"tie":"NOT_QUALIFIED","abstention":false,
 "clarity_stage":true,"low_turnout":"NOT_QUALIFIED","reporting":"NAMED",
 "reporting_scope":"ENFORCED","coordinator_initiation":false,"provisions":[]}'::jsonb || jsonb_build_object('reporting',reporting) AS settings FROM public.core_cqreportingdefault;
CREATE TABLE public.core_cqact (
 act_uuid uuid PRIMARY KEY REFERENCES public.core_livingorganismevent(event_uuid) DEFERRABLE INITIALLY DEFERRED,
 organism_id bigint NOT NULL REFERENCES public.core_livingorganism,
 board_uuid uuid REFERENCES public.core_discboard,
 thread_uuid uuid REFERENCES public.core_discthread,
 operation text NOT NULL CHECK(operation IN
 ('SETTINGS','INITIATE','PREPARED','PREPARATION_DUE','CONFIRM','QUESTION','CONFLICT','EXTEND','CLOSE','PROCEED','WITHDRAW')),
 actor_id bigint NOT NULL REFERENCES public.core_identity,
 actor_epoch bigint NOT NULL, received_at timestamptz NOT NULL,
 effective_at timestamptz NOT NULL, recorded_at timestamptz NOT NULL, execution_xid xid8 NOT NULL,
 request jsonb NOT NULL, facts jsonb NOT NULL, authority_source jsonb NOT NULL, fingerprint text NOT NULL,
 ordinal bigint NOT NULL, UNIQUE(organism_id,ordinal),
 CHECK((thread_uuid IS NULL AND board_uuid IS NULL)=(operation='SETTINGS')),
 CHECK((thread_uuid IS NULL)=(board_uuid IS NULL))
);
CREATE UNIQUE INDEX cq_thread_once ON public.core_cqact(thread_uuid) WHERE operation='INITIATE';
CREATE INDEX cq_thread_history ON public.core_cqact(thread_uuid,ordinal DESC);
CREATE INDEX cq_owner_phases ON public.core_cqact(organism_id,thread_uuid,ordinal DESC) WHERE facts ? 'phase';
CREATE INDEX cq_settings_history ON public.core_cqact(organism_id,ordinal DESC) WHERE operation='SETTINGS';
CREATE VIEW public.cq_phases AS
 SELECT DISTINCT ON (thread_uuid) thread_uuid, organism_id, board_uuid, act_uuid, facts->>'phase' AS phase,
 (facts->>'deadline')::timestamptz AS deadline, ordinal, (facts->>'response_deadline')::timestamptz AS response_deadline
 FROM public.core_cqact WHERE facts ? 'phase' ORDER BY thread_uuid,ordinal DESC;

CREATE FUNCTION public.cq_reporting_at(p_lo bigint,p_cut timestamptz) RETURNS jsonb LANGUAGE sql STABLE AS $$
 SELECT coalesce((SELECT jsonb_build_object('version',a.act_uuid,'value',a.facts->'settings'->>'reporting',
   'scope',a.facts->'settings'->>'reporting_scope') FROM public.core_cqact a
   WHERE a.organism_id=p_lo AND a.operation='SETTINGS' AND a.facts->>'reporting_change'='true'
   AND a.recorded_at<=p_cut AND (a.facts->'reporting_rule'->>'effective_from')::timestamptz<=p_cut
   ORDER BY a.ordinal DESC LIMIT 1),(SELECT jsonb_build_object('version',reporting_version,'value',reporting,
     'scope','ENFORCED') FROM public.core_cqreportingdefault))
$$;
CREATE FUNCTION public.cq_settings_at(p_lo bigint,p_cut timestamptz) RETURNS jsonb LANGUAGE sql STABLE AS $$
 SELECT coalesce((SELECT a.facts->'settings' FROM public.core_cqact a WHERE a.organism_id=p_lo
 AND a.operation='SETTINGS' AND a.effective_at<=p_cut AND a.recorded_at<=p_cut ORDER BY a.ordinal DESC LIMIT 1),
 (SELECT settings FROM public.cq_platform_defaults)) || jsonb_build_object('reporting',r->'value','reporting_scope',r->'scope')
 FROM public.cq_reporting_at(p_lo,p_cut) r
$$;
CREATE FUNCTION public.cq_settings(p_lo bigint) RETURNS jsonb LANGUAGE sql STABLE AS $$
 SELECT public.cq_settings_at(p_lo,statement_timestamp())
$$;
CREATE FUNCTION public.cq_refuse(p_code text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION USING MESSAGE=p_code,ERRCODE='23514'; END $$;

-- Only the explicitly supplied changes are merged into a complete immutable version.
CREATE FUNCTION public.cq_deadline(p_start timestamptz,p_days integer) RETURNS timestamptz LANGUAGE sql IMMUTABLE STRICT AS $$
 SELECT p_start + p_days * interval '24 hours'
$$;
CREATE FUNCTION public.cq_response_deadline(p_window_end timestamptz) RETURNS timestamptz LANGUAGE sql IMMUTABLE STRICT AS $$
 SELECT public.cq_deadline(p_window_end,3)
$$;
-- Validate the ordinary rule first, then each explicitly scoped override independently.
CREATE FUNCTION public.cq_check_values(p_values jsonb,p_cut timestamptz,p_setting text,p_waive boolean)
RETURNS void LANGUAGE plpgsql STABLE AS $$
DECLARE v_field text; v_number numeric;
BEGIN
 FOREACH v_field IN ARRAY ARRAY['enabled','clarity_stage','abstention','coordinator_initiation'] LOOP
   IF jsonb_typeof(p_values->v_field)<>'boolean' THEN PERFORM public.cq_refuse('CQ_SETTINGS_INPUT'); END IF;
 END LOOP;
 FOREACH v_field IN ARRAY ARRAY['preparation_days','clarity_days','vote_days','cdpt_percent','quorum_value','numerator','denominator'] LOOP
   IF jsonb_typeof(p_values->v_field)='null' AND v_field NOT IN ('preparation_days','clarity_days','vote_days') THEN CONTINUE; END IF;
   IF jsonb_typeof(p_values->v_field)<>'number' OR (p_values->>v_field) !~ '^[1-9][0-9]*$'
     THEN PERFORM public.cq_refuse('CQ_SETTINGS_INPUT'); END IF;
   v_number:=(p_values->>v_field)::numeric;
   IF v_number>2147483647 THEN PERFORM public.cq_refuse('CQ_SETTINGS_INPUT'); END IF;
   IF v_field IN ('preparation_days','clarity_days','vote_days') THEN
     PERFORM public.cq_deadline(p_cut,v_number::integer);
   END IF;
 END LOOP;
 IF coalesce(p_values->>'quorum_form' IN ('MAJORITY','ABSOLUTE','PERCENT','NO_FLOOR'),false) IS NOT TRUE
 OR coalesce(p_values->>'threshold_base' IN ('VOTES_CAST','PARTICIPANTS','ELIGIBLE'),false) IS NOT TRUE
 OR coalesce(p_values->>'threshold' IN ('MAJORITY','FRACTION'),false) IS NOT TRUE
 OR p_values->>'tie' IS DISTINCT FROM 'NOT_QUALIFIED'
 OR coalesce(p_values->>'reporting' IN ('NAMED','TOTALS_ONLY'),false) IS NOT TRUE
 OR coalesce(p_values->>'reporting_scope' IN ('ENFORCED','DELEGATED'),false) IS NOT TRUE
 OR coalesce(p_values->>'low_turnout' IN ('NOT_QUALIFIED','NEW_CQ'),false) IS NOT TRUE
 OR ((p_values->>'quorum_form' IN ('ABSOLUTE','PERCENT'))<>(p_values->>'quorum_value' IS NOT NULL))
 OR ((p_values->>'threshold'='FRACTION')<>(p_values->>'numerator' IS NOT NULL))
 OR ((p_values->>'threshold'='FRACTION')<>(p_values->>'denominator' IS NOT NULL))
 OR (p_values->>'numerator')::numeric>(p_values->>'denominator')::numeric
 OR (p_values->>'quorum_form'='PERCENT' AND (p_values->>'quorum_value')::numeric>100)
 OR (p_values->>'cdpt_percent')::numeric NOT BETWEEN 51 AND 100
 THEN PERFORM public.cq_refuse('CQ_SETTINGS_INPUT'); END IF;
 IF (p_values->>'quorum_form'='NO_FLOOR' AND NOT p_waive)
 OR (p_values->>'quorum_form'='ABSOLUTE' AND (p_values->>'quorum_value')::integer<3 AND NOT p_waive)
 OR (p_values->>'quorum_form'='PERCENT' AND (p_values->>'quorum_value')::integer<51 AND p_setting IS DISTINCT FROM 'CQPT')
 OR (p_values->>'threshold'='FRACTION' AND 2*(p_values->>'numerator')::numeric<(p_values->>'denominator')::numeric
     AND p_setting IS DISTINCT FROM 'CQT')
 THEN PERFORM public.cq_refuse('CQ_LAW'); END IF;
END $$;

CREATE FUNCTION public.cq_law_current(p_owner uuid,p_law jsonb,p_cut timestamptz) RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT EXISTS(SELECT FROM public.l_laws_at(p_owner,p_cut,p_cut) l WHERE l.binding::text=p_law->>'binding'
     AND l.version::text=p_law->>'version' AND l.class_name IN ('ARTICLE','BYLAW'))
$$;
CREATE FUNCTION public.cq_check_provisions(p_lo bigint,p_owner uuid,p_settings jsonb,p_cut timestamptz)
RETURNS void LANGUAGE plpgsql STABLE AS $$
DECLARE law jsonb; seen jsonb:='[]'; fields text[]; setting_name text; val jsonb; effective_body text;
BEGIN
 IF jsonb_typeof(p_settings->'provisions') IS DISTINCT FROM 'array' THEN PERFORM public.cq_refuse('CQ_LAW'); END IF;
 FOR law IN SELECT value FROM jsonb_array_elements(p_settings->'provisions') LOOP
   IF jsonb_typeof(law) IS DISTINCT FROM 'object' THEN PERFORM public.cq_refuse('CQ_LAW'); END IF;
   fields:=ARRAY['binding','version','provision','body','decision','stage','denominator','setting','value'];
   IF NOT(law ?& fields) OR law-fields<>'{}'::jsonb
      OR EXISTS(SELECT FROM jsonb_each(law-ARRAY['version','value']) e WHERE jsonb_typeof(e.value)<>'string' OR btrim(law->>e.key)='')
      OR coalesce(law->>'version','') !~ '^[1-9][0-9]*$'
      OR (law->>'body' NOT IN ('ALL_BOARDS','LO') AND NOT EXISTS(SELECT FROM public.core_circle c
          WHERE c.parent_organism_id=p_lo AND c.circle_uuid::text=law->>'body'))
      OR (law->>'decision'<>'ALL_DECISIONS' AND NOT EXISTS(SELECT FROM public.discp_threads t JOIN public.core_discboard b USING(board_uuid)
          LEFT JOIN public.core_circle c ON c.id=b.circle_id WHERE b.organism_id=p_lo AND t.thread_uuid::text=law->>'decision'
          AND law->>'body' IN ('ALL_BOARDS',coalesce(c.circle_uuid::text,'LO'))))
      OR NOT public.cq_law_current(p_owner,law,p_cut)
      THEN PERFORM public.cq_refuse('CQ_LAW'); END IF;
   setting_name:=law->>'setting'; val:=law->'value';
   fields:=CASE setting_name WHEN 'CLARITY_STAGE' THEN ARRAY['clarity_stage'] WHEN 'CLARITY_GATE' THEN ARRAY['clarity_gate'] WHEN 'CDPT' THEN ARRAY['cdpt_percent']
     WHEN 'CQPT' THEN ARRAY['quorum_form','quorum_value','waive_minimum']
     WHEN 'CQT' THEN ARRAY['threshold','numerator','denominator'] WHEN 'BASE' THEN ARRAY['threshold_base']
     WHEN 'TIE' THEN ARRAY['tie'] WHEN 'ABSTENTION' THEN ARRAY['abstention'] END;
   IF fields IS NULL OR jsonb_typeof(val) IS DISTINCT FROM 'object' THEN PERFORM public.cq_refuse('CQ_LAW'); END IF;
   IF NOT(val ?& fields) OR val-fields<>'{}'::jsonb
      OR (setting_name='CLARITY_GATE' AND val->'clarity_gate' IS DISTINCT FROM 'false'::jsonb)
      OR (setting_name='CQPT' AND jsonb_typeof(val->'waive_minimum') IS DISTINCT FROM 'boolean')
      OR (setting_name IN ('CLARITY_STAGE','CLARITY_GATE','CDPT'))<>(law->>'stage'='CLARITY')
      OR law->>'stage' NOT IN ('CLARITY','VOTE') THEN PERFORM public.cq_refuse('CQ_LAW'); END IF;
   -- An exact thread belongs to one board, even when the body selector is ALL_BOARDS.
   effective_body:=law->>'body';
   IF law->>'decision'<>'ALL_DECISIONS' THEN
     SELECT coalesce(c.circle_uuid::text,'LO') INTO STRICT effective_body FROM public.discp_threads t
       JOIN public.core_discboard b USING(board_uuid) LEFT JOIN public.core_circle c ON c.id=b.circle_id
       WHERE t.thread_uuid::text=law->>'decision' AND b.organism_id=p_lo;
   END IF;
   IF EXISTS(SELECT FROM jsonb_array_elements(seen) prev WHERE prev->>'setting'=setting_name
       AND (prev->>'body'=effective_body OR 'ALL_BOARDS' IN (prev->>'body',effective_body))
       AND (prev->>'decision'=law->>'decision' OR 'ALL_DECISIONS' IN (prev->>'decision',law->>'decision')))
     THEN PERFORM public.cq_refuse('CQ_LAW'); END IF;
   PERFORM public.cq_check_values(p_settings||val,p_cut,setting_name,coalesce((val->>'waive_minimum')::boolean,false));
   seen:=seen||jsonb_build_array(law||jsonb_build_object('body',effective_body));
 END LOOP;
END $$;

CREATE FUNCTION public.cq_checked_settings(p_lo bigint,p_key uuid,p_args jsonb,p_cut timestamptz)
 RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE v_previous public.core_cqact; v_current jsonb:=public.cq_settings(p_lo); v_next jsonb;
 v_change jsonb:=p_args->'changes'; v_owner uuid;
 v_report text; v_changed boolean; v_rules jsonb; v_prev_rule text; v_from timestamptz:=p_cut; v_prior_from timestamptz;
BEGIN
 SELECT * INTO v_previous FROM public.core_cqact WHERE organism_id=p_lo AND operation='SETTINGS'
 ORDER BY ordinal DESC LIMIT 1;
 v_current:=coalesce(v_previous.facts->'settings',v_current);
 IF p_args->>'predecessor' IS DISTINCT FROM v_previous.act_uuid::text THEN PERFORM public.cq_refuse('CQ_SETTINGS_STALE'); END IF;
 IF jsonb_typeof(v_change) IS DISTINCT FROM 'object' OR v_change='{}'::jsonb THEN PERFORM public.cq_refuse('CQ_SETTINGS_INPUT'); END IF;
 IF EXISTS(SELECT FROM jsonb_object_keys(v_change) k WHERE NOT v_current ? k) THEN PERFORM public.cq_refuse('CQ_SETTINGS_INPUT'); END IF;
 v_next:=v_current||v_change;
 PERFORM public.cq_check_values(v_next,p_cut,NULL,false);
 SELECT organism_id INTO v_owner FROM public.core_livingorganism WHERE id=p_lo;
 IF v_next->>'coordinator_initiation'='true' AND v_current->>'coordinator_initiation'='false'
   AND NOT public.s015_entitlement_effective(v_owner,'CQ_COORDINATOR_INITIATION',p_cut)
 THEN PERFORM public.cq_refuse('CQ_SUBSCRIBER'); END IF;
 IF ((v_next->>'reporting'='TOTALS_ONLY' AND v_current->>'reporting'<>'TOTALS_ONLY')
   OR (v_next->>'reporting_scope'='DELEGATED' AND v_current->>'reporting_scope'<>'DELEGATED'))
   AND NOT public.s015_entitlement_effective(v_owner,'CQ_TOTALS_ONLY',p_cut)
 THEN PERFORM public.cq_refuse('CQ_SUBSCRIBER'); END IF;
 IF v_change ? 'provisions' THEN
   PERFORM public.cq_check_provisions(p_lo,v_owner,v_next,p_cut);
 END IF;
 IF v_next=v_current THEN PERFORM public.cq_refuse('CQ_SETTINGS_UNCHANGED'); END IF;
 v_prev_rule:=coalesce(v_previous.facts->>'reporting_version',(SELECT reporting_version FROM public.cq_platform_defaults));
 v_changed:=v_current->'reporting'<>v_next->'reporting' OR v_current->'reporting_scope'<>v_next->'reporting_scope';
 v_report:=v_prev_rule;
 IF p_args ? 'reporting_effective_from' THEN
   IF NOT v_changed OR coalesce(p_args->>'reporting_effective_from','') !~ '(Z|[+-][0-9][0-9]:[0-9][0-9])$'
     THEN PERFORM public.cq_refuse('CQ_REPORT_DATE'); END IF;
   BEGIN v_from:=(p_args->>'reporting_effective_from')::timestamptz;
   EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN PERFORM public.cq_refuse('CQ_REPORT_DATE'); END;
 END IF;
 IF v_changed THEN
   SELECT (a.facts->'reporting_rule'->>'effective_from')::timestamptz INTO v_prior_from FROM public.core_cqact a
     WHERE a.act_uuid::text=v_prev_rule AND a.organism_id=p_lo;
   IF v_from<p_cut OR v_from<v_prior_from THEN PERFORM public.cq_refuse('CQ_REPORT_DATE'); END IF;
   v_report:=p_key::text;
 END IF;
 v_rules:=jsonb_build_object('effective_from',v_from,'old',v_current->'reporting','new',v_next->'reporting',
   'old_scope',v_current->'reporting_scope','scope',v_next->'reporting_scope',
   'previous_version',v_prev_rule,'old_fingerprint',public.disc_fingerprint(jsonb_build_array(v_current->'reporting',v_current->'reporting_scope')),
   'new_fingerprint',public.disc_fingerprint(jsonb_build_array(v_next->'reporting',v_next->'reporting_scope')));
 RETURN jsonb_build_object('settings',v_next,'previous',v_current,'predecessor',v_previous.act_uuid,
   'reporting_version',v_report,'reporting_change',v_changed,'reporting_rule',v_rules,'decision',p_key,'basis','HG:D11:INTERIM:C1-74');
EXCEPTION WHEN datetime_field_overflow OR interval_field_overflow OR numeric_value_out_of_range THEN
 PERFORM public.cq_refuse('CQ_SETTINGS_INPUT'); RETURN NULL;
END $$;

-- Resolve a declaration only for the body, motion and stage cited by the LO.
-- Ballot opening in P2 must call this again on the then-current settings.
CREATE FUNCTION public.cq_applicable(p_settings jsonb,p_board uuid,p_thread uuid,p_stage text) RETURNS jsonb
LANGUAGE sql STABLE AS $$
 SELECT coalesce(jsonb_agg(x.law ORDER BY x.pos),'[]'::jsonb)
 FROM public.core_discboard b LEFT JOIN public.core_circle c ON c.id=b.circle_id
 CROSS JOIN LATERAL jsonb_array_elements(p_settings->'provisions') WITH ORDINALITY x(law,pos)
 WHERE b.board_uuid=p_board AND x.law->>'stage'=p_stage AND x.law->>'body' IN ('ALL_BOARDS',coalesce(c.circle_uuid::text,'LO'))
   AND x.law->>'decision' IN ('ALL_DECISIONS',p_thread::text)
$$;
CREATE FUNCTION public.cq_scoped_settings(p_settings jsonb,p_owner uuid,p_board uuid,p_thread uuid,p_stage text,p_cut timestamptz)
 RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE law jsonb; v_result jsonb:=p_settings-'provisions';
 applied jsonb:=public.cq_applicable(p_settings,p_board,p_thread,p_stage);
BEGIN
 IF p_stage='CLARITY' AND NOT coalesce(
     (SELECT (item->'value'->>'clarity_stage')::boolean FROM jsonb_array_elements(applied) item
      WHERE item->>'setting'='CLARITY_STAGE'), (p_settings->>'clarity_stage')::boolean) THEN
   SELECT coalesce(jsonb_agg(item ORDER BY pos),'[]'::jsonb) INTO applied
   FROM jsonb_array_elements(applied) WITH ORDINALITY x(item,pos) WHERE item->>'setting'='CLARITY_STAGE';
 END IF;
 FOR law IN SELECT value FROM jsonb_array_elements(applied) LOOP
   IF NOT public.cq_law_current(p_owner,law,p_cut) THEN PERFORM public.cq_refuse('CQ_LAW'); END IF;
   v_result:=v_result||(law->'value');
 END LOOP;
 IF p_stage='CLARITY' THEN
   v_result:=v_result||jsonb_build_object('clarity_gate',(v_result->>'clarity_stage')::boolean AND coalesce((v_result->>'clarity_gate')::boolean,true));
 END IF;
 RETURN v_result||jsonb_build_object('applied_provisions',applied);
END $$;

CREATE FUNCTION public.cq_roster(p_board uuid,p_thread uuid,p_cut timestamptz) RETURNS jsonb LANGUAGE sql STABLE AS $$
 SELECT coalesce(jsonb_agg(jsonb_build_object('person',m.identity_id,'membership',m.membership_uuid,
   'active_period',period.event_uuid,'place',place.id) ORDER BY m.identity_id),'[]'::jsonb)
 FROM public.core_discboard b JOIN public.core_organismmembership m ON m.living_organism_id=b.organism_id
 LEFT JOIN LATERAL (SELECT t.event_uuid FROM public.core_organismmembershiptransition t
   WHERE t.membership_id=m.id AND t.resulting_state='ACTIVE' AND t.prior_state IS DISTINCT FROM 'ACTIVE'
   AND t.received_at<=p_cut AND t.effective_at<=p_cut AND t.recorded_at<=p_cut
   ORDER BY t.effective_at DESC,t.sequence DESC LIMIT 1) period ON true
 LEFT JOIN LATERAL (SELECT p.id FROM public.core_circleparticipation p WHERE p.circle_id=b.circle_id AND p.membership_id=m.id
   AND p.effective_at<=p_cut AND p.recorded_at<=p_cut AND p.received_at<=p_cut
   AND NOT EXISTS(SELECT FROM public.core_circleparticipationend e WHERE e.participation_id=p.id
   AND e.effective_at<=p_cut AND e.received_at<=p_cut AND e.recorded_at<=p_cut) ORDER BY p.id DESC LIMIT 1) place ON true
 WHERE b.board_uuid=p_board AND public.disc_member_state(b.organism_id,m.identity_id,p_cut)='ACTIVE'
 AND public.disc_audience(b.organism_id,b.circle_id,m.identity_id,'MEMBERS',p_cut)
 AND NOT EXISTS(SELECT FROM public.core_cqact a WHERE a.thread_uuid=p_thread AND a.operation='CONFLICT'
    AND a.actor_id=m.identity_id AND a.effective_at<=p_cut AND a.recorded_at<=p_cut)
$$;
"""

FORWARD += r"""
CREATE FUNCTION public.cq_source(p_lo bigint,p_board uuid,p_thread uuid,p_op text,p_person bigint,p_cut timestamptz)
 RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE v_state text; v_office jsonb; v_board public.core_discboard; v_category public.disc_categories;
 v_first public.discp_posts; v_settings jsonb; v_caller bigint:=p_person;
BEGIN
 IF NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
   WHERE i.id=p_person AND i.access_state='active' AND u.is_active) THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 v_state:=public.disc_member_state(p_lo,p_person,p_cut);
 IF v_state IS NULL OR v_state NOT IN ('ACTIVE','PROBATIONARY','RESTRICTED','SUSPENDED') THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 IF p_op='SETTINGS' THEN
   IF p_board IS NOT NULL OR p_thread IS NOT NULL THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
   v_office:=public.disc_office(p_lo,NULL,p_person,p_cut);
 ELSE
   SELECT * INTO v_board FROM public.core_discboard WHERE board_uuid=p_board AND organism_id=p_lo;
   SELECT c.* INTO v_category FROM public.disc_categories c JOIN public.core_discthread t ON t.category_uuid=c.category_uuid
    WHERE t.thread_uuid=p_thread AND c.board_uuid=p_board;
   IF v_board.board_uuid IS NULL OR v_category.category_uuid IS NULL THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
   IF p_op='PREPARATION_DUE' THEN
     IF v_state<>'ACTIVE' THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
     RETURN jsonb_build_object('kind','RULE','reference','HG:DISC2:G1:D4','route','PREPARATION_DUE');
   END IF;
   IF NOT public.disc_audience(p_lo,v_board.circle_id,p_person,v_category.access_level,p_cut) THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
   SELECT * INTO v_first FROM public.discp_posts WHERE thread_uuid=p_thread AND post_number=1;
   v_office:=public.disc_office(p_lo,v_board.circle_id,p_person,p_cut);
   IF (v_first.author_hidden OR v_first.coordinator_hidden) AND v_office IS NULL THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 END IF;
 IF v_state IN ('SUSPENDED','RESTRICTED') THEN PERFORM public.cq_refuse('CQ_RESTRICTED'); END IF;
 IF v_state='PROBATIONARY' THEN PERFORM public.cq_refuse('CQ_PROBATION'); END IF;
 IF p_op IN ('SETTINGS','PREPARED','EXTEND','CLOSE','PROCEED') THEN
   IF v_office IS NULL THEN
     PERFORM public.cq_refuse(CASE p_op WHEN 'SETTINGS' THEN 'CQ_SETTINGS_OFFICE' WHEN 'PREPARED' THEN 'CQ_PREPARE_OFFICE'
       WHEN 'PROCEED' THEN 'CQ_FINISH_OFFICE' ELSE 'CQ_CLARITY_OFFICE' END);
   END IF;
   RETURN v_office;
 END IF;
 IF p_op IN ('CONFIRM','QUESTION') AND EXISTS(SELECT FROM public.core_cqact a WHERE a.thread_uuid=p_thread
   AND a.operation='CONFLICT' AND a.actor_id=p_person AND a.recorded_at<=p_cut)
 THEN PERFORM public.cq_refuse('CQ_CONFIRM_ELIGIBLE'); END IF;
 IF p_op='INITIATE' AND v_first.author_id<>p_person THEN
   v_settings:=public.cq_settings_at(p_lo,p_cut);
   IF v_office IS NULL OR v_settings->>'coordinator_initiation'<>'true' THEN PERFORM public.cq_refuse('CQ_INITIATOR'); END IF;
   RETURN v_office;
 ELSIF p_op='WITHDRAW' AND v_first.author_id<>p_person THEN PERFORM public.cq_refuse('CQ_WITHDRAW_AUTHOR');
 END IF;
 RETURN jsonb_build_object('kind','MEMBER','reference',v_caller,'route',p_op);
END $$;

CREATE FUNCTION public.cq_phase_facts(p_lo bigint,p_board uuid,p_thread uuid,p_op text,p_args jsonb,p_actor bigint,p_cut timestamptz)
 RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE v_head public.cq_phases; v_topic public.discp_threads; v_first public.discp_posts; v_category public.disc_categories;
 v_start public.core_cqact; v_baseline jsonb; v_settings jsonb; v_people jsonb; v_confirmed jsonb;
 v_status jsonb; v_electorate jsonb; v_self jsonb; v_total integer; v_count integer; v_required integer;
 v_facts jsonb; v_phase text; v_deadline timestamptz; v_before timestamptz; v_parent uuid; v_setting uuid;
BEGIN
 SELECT * INTO v_topic FROM public.discp_threads WHERE thread_uuid=p_thread;
 SELECT * INTO v_category FROM public.disc_categories WHERE category_uuid=v_topic.category_uuid;
 SELECT * INTO STRICT v_first FROM public.discp_posts WHERE thread_uuid=p_thread AND post_number=1;
 SELECT * INTO v_start FROM public.core_cqact WHERE thread_uuid=p_thread AND operation='INITIATE';
 SELECT * INTO v_head FROM public.cq_phases WHERE thread_uuid=p_thread;
 v_facts:=jsonb_build_object('version',v_first.version_uuid,'wording_fingerprint',v_first.body_fingerprint);
 IF p_op='INITIATE' THEN
   v_baseline:=public.cq_settings_at(p_lo,p_cut);
   v_settings:=public.cq_scoped_settings(v_baseline,
     (SELECT lo.organism_id FROM public.core_livingorganism lo WHERE lo.id=p_lo),p_board,p_thread,'CLARITY',p_cut);
   IF v_settings->>'enabled'<>'true' THEN PERFORM public.cq_refuse('CQ_DISABLED'); END IF;
   IF v_topic.topic_type<>'SUGGESTION' THEN PERFORM public.cq_refuse('CQ_SUGGESTION'); END IF;
   IF v_start.act_uuid IS NOT NULL THEN PERFORM public.cq_refuse('CQ_EXISTS'); END IF;
   IF v_category.access_level='COORDINATORS' OR v_first.author_hidden OR v_first.coordinator_hidden
      THEN PERFORM public.cq_refuse('CQ_FULL_AUDIENCE'); END IF;
   IF v_category.retired THEN PERFORM public.cq_refuse('DISC_CATEGORY_RETIRED'); END IF;
   IF v_topic.closed THEN PERFORM public.cq_refuse('DISC_CLOSED'); END IF;
   IF v_first.version_uuid::text IS DISTINCT FROM p_args->>'version' THEN PERFORM public.cq_refuse('CQ_INIT_VERSION'); END IF;
   IF p_args->>'previous_thread' IS NOT NULL THEN
     SELECT a.thread_uuid INTO v_parent FROM public.core_cqact a JOIN public.cq_phases h USING(thread_uuid)
       JOIN public.discp_threads t ON t.thread_uuid=h.thread_uuid JOIN public.disc_categories c USING(category_uuid)
       JOIN public.core_discboard b ON b.board_uuid=c.board_uuid JOIN public.discp_posts first ON first.thread_uuid=t.thread_uuid AND first.post_number=1
       WHERE a.thread_uuid::text=p_args->>'previous_thread' AND a.operation='INITIATE' AND a.board_uuid=p_board
       AND h.phase IN ('CLOSED','WITHDRAWN')
       AND public.disc_audience(p_lo,b.circle_id,p_actor,c.access_level,p_cut)
       AND (NOT (first.author_hidden OR first.coordinator_hidden) OR public.disc_office(p_lo,b.circle_id,p_actor,p_cut) IS NOT NULL);
     IF v_parent IS NULL THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
   END IF;
   SELECT act_uuid INTO v_setting FROM public.core_cqact WHERE organism_id=p_lo AND operation='SETTINGS' AND effective_at<=p_cut AND recorded_at<=p_cut ORDER BY ordinal DESC LIMIT 1;
   RETURN v_facts||jsonb_build_object('phase','PREPARATION','record_kind','GOVERNANCE','author',v_first.author_id,
     'previous_thread',v_parent,'settings',v_settings,'baseline',v_baseline,'settings_version',v_setting,
     'deadline',public.cq_deadline(p_cut,(v_settings->>'preparation_days')::int));
 END IF;
 IF v_start.act_uuid IS NULL THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 v_settings:=v_start.facts->'settings';
 IF p_op='PROCEED' THEN
   v_settings:=public.cq_scoped_settings(v_start.facts->'baseline',(SELECT lo.organism_id FROM public.core_livingorganism lo WHERE lo.id=p_lo),
       p_board,p_thread,'CLARITY',p_cut);
 END IF;
 IF p_op IN ('PREPARED','PREPARATION_DUE') THEN
   IF v_head.phase<>'PREPARATION' OR (p_op='PREPARED' AND p_cut>=v_head.deadline)
     OR (p_op='PREPARATION_DUE' AND p_cut<v_head.deadline) THEN PERFORM public.cq_refuse('CQ_PREPARATION'); END IF;
   IF NOT (v_settings->>'clarity_stage')::boolean OR EXISTS(
       SELECT FROM jsonb_array_elements(v_settings->'applied_provisions') law
       JOIN public.core_livingorganism lo ON lo.id=p_lo WHERE NOT public.cq_law_current(lo.organism_id,law,p_cut)) THEN
     RETURN v_facts||jsonb_build_object('rule_open',true,'clarity_gate',v_settings->'clarity_gate','prepared',p_op='PREPARED','due',v_head.deadline,
       'clarity_stage',v_settings->'clarity_stage','applied_provisions',v_settings->'applied_provisions');
   END IF;
   v_deadline:=public.cq_deadline(p_cut,(v_settings->>'clarity_days')::int);
   RETURN v_facts||jsonb_build_object('phase','CLARITY','clarity_gate',v_settings->'clarity_gate','prepared',p_op='PREPARED','due',v_head.deadline,
      'deadline',v_deadline,'response_deadline',public.cq_response_deadline(v_deadline));
 END IF;
 IF p_op='WITHDRAW' THEN
   IF v_head.phase='CLARITY' AND p_cut>=v_head.response_deadline THEN PERFORM public.cq_refuse('CQ_RESPONSE_DUE'); END IF;
   IF v_head.phase NOT IN ('PREPARATION','CLARITY') THEN PERFORM public.cq_refuse('CQ_WITHDRAW_PHASE'); END IF;
   RETURN v_facts||jsonb_build_object('phase','WITHDRAWN');
 END IF;
 IF p_op='CONFLICT' THEN
   IF v_head.phase NOT IN ('PREPARATION','CLARITY') THEN PERFORM public.cq_refuse('CQ_CONFLICT_PERIOD'); END IF;
   IF EXISTS(SELECT FROM public.core_cqact WHERE thread_uuid=p_thread AND actor_id=p_actor AND operation='CONFLICT') THEN PERFORM public.cq_refuse('CQ_CONFLICT_REPEAT'); END IF;
   IF jsonb_typeof(p_args->'basis') IS DISTINCT FROM 'string' OR nullif(btrim(p_args->>'basis'),'') IS NULL THEN PERFORM public.cq_refuse('CQ_CONFLICT_BASIS'); END IF;
   RETURN v_facts||jsonb_build_object('basis',p_args->>'basis');
 END IF;
 IF v_head.phase<>'CLARITY' THEN
   IF p_op IN ('CONFIRM','QUESTION') THEN PERFORM public.cq_refuse('CQ_CONFIRM_PERIOD'); END IF;
   PERFORM public.cq_refuse('CQ_CLARITY_PERIOD');
 END IF;
 IF p_op IN ('CONFIRM','QUESTION') THEN
   IF p_cut>=v_head.deadline THEN PERFORM public.cq_refuse('CQ_CONFIRM_PERIOD'); END IF;
   IF v_first.version_uuid::text IS DISTINCT FROM p_args->>'version' THEN PERFORM public.cq_refuse('CQ_VERSION'); END IF;
   IF v_first.author_hidden OR v_first.coordinator_hidden THEN PERFORM public.cq_refuse('DISC_HIDDEN'); END IF;
   SELECT x INTO v_self FROM jsonb_array_elements(public.cq_roster(p_board,p_thread,p_cut)) x WHERE x->>'person'=p_actor::text;
   IF v_self IS NULL THEN PERFORM public.cq_refuse('CQ_CONFIRM_ELIGIBLE'); END IF;
   IF (SELECT a.operation FROM public.core_cqact a WHERE a.thread_uuid=p_thread AND a.actor_id=p_actor AND a.operation IN ('CONFIRM','QUESTION')
       AND a.facts->>'version'=p_args->>'version' AND a.facts->'member'=v_self ORDER BY a.ordinal DESC LIMIT 1)=p_op
      THEN PERFORM public.cq_refuse(CASE WHEN p_op='QUESTION' THEN 'CQ_QUESTION_REPEAT' ELSE 'CQ_RESPONSE_REPEAT' END); END IF;
   RETURN v_facts||jsonb_build_object('member',v_self);
 END IF;
 IF p_op='RESPONSE_DUE' AND (v_head.response_deadline IS NULL OR p_cut<v_head.response_deadline
    OR p_args->>'window' IS DISTINCT FROM v_head.act_uuid::text) THEN PERFORM public.cq_refuse('CQ_NO_DUE'); END IF;
 IF p_op IN ('EXTEND','CLOSE','PROCEED') AND p_cut>=v_head.response_deadline THEN PERFORM public.cq_refuse('CQ_RESPONSE_DUE'); END IF;
 IF p_op IN ('EXTEND','CLOSE') AND p_cut<v_head.deadline THEN PERFORM public.cq_refuse('CQ_WINDOW'); END IF;
 IF p_op='EXTEND' THEN
    v_deadline:=public.cq_deadline(p_cut,(v_settings->>'clarity_days')::int);
    RETURN v_facts||jsonb_build_object('phase','CLARITY','clarity_gate',v_settings->'clarity_gate','previous_deadline',v_head.deadline,
       'deadline',v_deadline,'response_deadline',public.cq_response_deadline(v_deadline));
 END IF;
 IF p_op NOT IN ('CLOSE','PROCEED','RESPONSE_DUE') THEN PERFORM public.cq_refuse('CQ_INPUT'); END IF;
 v_before:=least(v_head.deadline,p_cut);
 v_people:=public.cq_roster(p_board,p_thread,p_cut);
 SELECT coalesce(jsonb_agg(jsonb_build_object('member',m.value,'response',
    CASE WHEN r.operation='CONFIRM' AND r.facts->>'version'=v_first.version_uuid::text THEN 'CONFIRMED'
         WHEN r.operation='QUESTION' THEN 'QUESTION_UNCONFIRMED' WHEN r.operation='CONFIRM' THEN 'EARLIER_WORDING'
         ELSE 'NO_CONFIRMATION' END) ORDER BY (m.value->>'person')::bigint),'[]'::jsonb)
 INTO v_status FROM jsonb_array_elements(v_people) m LEFT JOIN LATERAL
 (SELECT a.operation,a.facts FROM public.core_cqact a WHERE a.thread_uuid=p_thread AND a.facts->'member'=m.value
   AND a.operation IN ('CONFIRM','QUESTION') AND a.effective_at<=v_before ORDER BY a.ordinal DESC LIMIT 1) r ON true;
 SELECT coalesce(jsonb_agg(x->'member' ORDER BY (x->'member'->>'person')::bigint),'[]'::jsonb)
 INTO v_confirmed FROM jsonb_array_elements(v_status) x WHERE x->>'response'='CONFIRMED';
 v_count:=jsonb_array_length(v_confirmed); v_total:=jsonb_array_length(v_people);
 v_required:=coalesce(ceil(v_total::numeric*(v_settings->>'cdpt_percent')::int/100)::int,v_total/2+1);
 v_electorate:=CASE WHEN (v_settings->>'clarity_gate')::boolean THEN v_confirmed ELSE v_people END;
 IF p_op='PROCEED' THEN
   IF v_first.author_hidden OR v_first.coordinator_hidden OR v_category.access_level='COORDINATORS' THEN PERFORM public.cq_refuse('CQ_FULL_AUDIENCE'); END IF;
   IF v_total<3 THEN PERFORM public.cq_refuse('CQ_THREE'); END IF;
   IF p_cut<v_head.deadline AND v_count<>v_total THEN PERFORM public.cq_refuse('CQ_WINDOW'); END IF;
   IF (v_settings->>'clarity_gate')::boolean AND v_count<v_required THEN PERFORM public.cq_refuse('CQ_CDPT'); END IF;
   IF jsonb_array_length(v_electorate)<3 THEN PERFORM public.cq_refuse('CQ_THREE'); END IF;
   v_phase:='VOTING';
 ELSIF p_op='CLOSE' THEN v_phase:='CLOSED'; END IF;
 IF p_op='RESPONSE_DUE' THEN
   v_facts:=v_facts||jsonb_build_object('rule_open',true,'window',v_head.act_uuid,'response_deadline',v_head.response_deadline);
 ELSE v_facts:=v_facts||jsonb_build_object('phase',v_phase); END IF;
 RETURN v_facts||jsonb_build_object('clarity_gate',v_settings->'clarity_gate','clarity_deadline',v_head.deadline,'eligible',v_people,
   'applied_provisions',v_settings->'applied_provisions','responses',v_status,'confirmed',v_confirmed,'required',v_required,'voting_pool',v_electorate,'clarity_stage',v_settings->'clarity_stage');
END $$;
"""

FORWARD += r"""
CREATE FUNCTION public.cq_input() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_owner uuid; v_stamp timestamptz:=statement_timestamp(); v_source jsonb; v_allowed text[];
BEGIN
 SELECT organism_id INTO v_owner FROM public.core_livingorganism WHERE id=NEW.organism_id;
 PERFORM public.s015_lock_organisms(ARRAY[v_owner]);
 IF NEW.received_at IS NULL OR NEW.received_at>v_stamp OR NEW.execution_xid IS NOT NULL
   OR NEW.facts IS NOT NULL OR NEW.fingerprint IS NOT NULL OR NEW.authority_source IS NOT NULL
   OR NEW.ordinal IS NOT NULL OR NEW.effective_at IS NOT NULL OR NEW.recorded_at IS NOT NULL
   OR NOT EXISTS(SELECT FROM public.core_identity i WHERE i.id=NEW.actor_id AND i.access_epoch=NEW.actor_epoch)
   THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 v_source:=public.cq_source(NEW.organism_id,NEW.board_uuid,NEW.thread_uuid,NEW.operation,NEW.actor_id,v_stamp);
 IF public.cq_source(NEW.organism_id,NEW.board_uuid,NEW.thread_uuid,NEW.operation,NEW.actor_id,NEW.received_at)
   IS DISTINCT FROM v_source THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 v_allowed:=CASE NEW.operation WHEN 'SETTINGS' THEN ARRAY['predecessor','changes','reporting_effective_from']
   WHEN 'INITIATE' THEN ARRAY['version','previous_thread'] WHEN 'CONFIRM' THEN ARRAY['version']
   WHEN 'QUESTION' THEN ARRAY['version'] WHEN 'RESPONSE_DUE' THEN ARRAY['window'] WHEN 'CONFLICT' THEN ARRAY['basis'] ELSE ARRAY[]::text[] END;
 IF jsonb_typeof(NEW.request) IS DISTINCT FROM 'object' OR NEW.request-v_allowed<>'{}'::jsonb
 THEN PERFORM public.cq_refuse('CQ_INPUT'); END IF;
 IF NEW.operation='SETTINGS' THEN
   NEW.facts:=public.cq_checked_settings(NEW.organism_id,NEW.act_uuid,NEW.request,v_stamp);
 ELSIF NEW.operation='PROCEED' THEN PERFORM public.cq_refuse('CQ_NOT_READY');
 ELSE
   NEW.facts:=public.cq_phase_facts(NEW.organism_id,NEW.board_uuid,NEW.thread_uuid,NEW.operation,NEW.request,NEW.actor_id,v_stamp);
 END IF;
 SELECT coalesce(max(a.ordinal),0)+1 INTO NEW.ordinal FROM public.core_cqact a WHERE a.organism_id=NEW.organism_id;
 NEW.fingerprint:=public.disc_fingerprint(jsonb_build_object('domain','DISC2','operation',NEW.operation,'request',NEW.request,
   'facts',NEW.facts,'owner',v_owner,'thread',NEW.thread_uuid,'board',NEW.board_uuid,'act',NEW.act_uuid,'source',v_source));
 NEW.authority_source:=v_source; NEW.execution_xid:=pg_current_xact_id(); NEW.effective_at:=v_stamp; NEW.recorded_at:=v_stamp;
 RETURN NEW;
END $$;
CREATE FUNCTION public.cq_event_matches(p_event public.core_livingorganismevent) RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT EXISTS(SELECT FROM public.core_cqact a JOIN public.core_livingorganism lo ON lo.id=a.organism_id
 WHERE a.act_uuid=p_event.event_uuid AND a.execution_xid=pg_current_xact_id() AND p_event.action='RECORD_CQ'
 AND p_event.living_organism_id=a.organism_id AND p_event.actor_state='LIVING_ORGANISM'
 AND p_event.actor_capacity='STANDING' AND p_event.actor_id IS NULL AND p_event.actor_access_epoch IS NULL
 AND p_event.entry_mode='TRANSPORT' AND p_event.prior_state=p_event.resulting_state AND p_event.sequence>1
 AND p_event.enterer_identity_id=a.actor_id AND p_event.authority_basis_id=lo.founding_authority_basis_id
 AND p_event.authority_decision_reference=public.disc_decision(a.authority_source,a.fingerprint)
 AND p_event.evidence_reference=(CASE WHEN a.operation='SETTINGS' THEN 'cq-settings-decision:' ELSE 'cq-act:' END)||a.act_uuid AND p_event.payload_fingerprint=a.fingerprint
 AND p_event.request_reference='cq:'||a.act_uuid AND p_event.idempotency_key='cq:'||a.act_uuid
 AND p_event.received_at=a.received_at AND p_event.occurred_at=a.received_at
 AND p_event.effective_at=a.effective_at AND p_event.recorded_at=a.recorded_at
 AND p_event.composing_rule_state='NA_TRANSPORT' AND p_event.composer_state='NA_TRANSPORT'
 AND p_event.effective_until_state='NONE')
$$;
-- No request payload, participation identities or ballot data in this projection.
CREATE VIEW public.cq_reporting_rules AS
 SELECT a.organism_id,a.act_uuid::text AS version,a.facts->'reporting_rule'->>'previous_version' AS predecessor,
 a.facts->'reporting_rule'->>'old' AS previous_value,a.facts->'reporting_rule'->>'new' AS value,
 a.facts->'reporting_rule'->>'old_scope' AS previous_scope,a.facts->'reporting_rule'->>'scope' AS scope,
 a.facts->'reporting_rule'->>'old_fingerprint' AS previous_fingerprint,
 a.facts->'reporting_rule'->>'new_fingerprint' AS value_fingerprint,a.actor_id,e.enterer_identity_id,
 a.authority_source,a.received_at,e.occurred_at,a.effective_at AS act_effective_at,
 (a.facts->'reporting_rule'->>'effective_from')::timestamptz AS effective_from,a.recorded_at,e.event_uuid AS decision,
 e.authority_decision_reference,e.evidence_reference
 FROM public.core_cqact a JOIN public.core_livingorganismevent e ON e.event_uuid=a.act_uuid
 WHERE a.operation='SETTINGS' AND a.facts->>'reporting_change'='true';
CREATE FUNCTION public.cq_append() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_lo public.core_livingorganism; v_head public.core_livingorganismevent; v_state text;
BEGIN
 SELECT * INTO STRICT v_lo FROM public.core_livingorganism WHERE id=NEW.organism_id FOR UPDATE;
 SELECT * INTO STRICT v_head FROM public.core_livingorganismevent WHERE id=v_lo.head_event_id;
 v_state:=public.s015_fold_effective_state('core_livingorganism',v_lo.id,NEW.effective_at,NEW.effective_at);
 PERFORM public.s015_append_event('core_livingorganism',v_lo.id,'RECORD_CQ',v_state,NEW.effective_at,
   NEW.received_at,NEW.received_at,NEW.actor_id,v_lo.founding_authority_basis_id,
   public.disc_decision(NEW.authority_source,NEW.fingerprint),
   (CASE WHEN NEW.operation='SETTINGS' THEN 'cq-settings-decision:' ELSE 'cq-act:' END)||NEW.act_uuid,'cq:'||NEW.act_uuid,
   NEW.fingerprint,'{}'::jsonb,NULL,NEW.act_uuid,v_head.id,v_head.sequence::integer,NULL::bigint,NULL::text,'cq:'||NEW.act_uuid);
 RETURN NULL;
END $$;
CREATE FUNCTION public.cq_complete() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_key uuid; v_act public.core_cqact; v_source jsonb;
BEGIN
 v_key:=NEW.act_uuid;
 SELECT * INTO v_act FROM public.core_cqact WHERE act_uuid=v_key;
 IF NOT FOUND OR NOT EXISTS(SELECT FROM public.core_livingorganismevent e WHERE e.event_uuid=v_key AND public.cq_event_matches(e))
 OR NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
   WHERE i.id=v_act.actor_id AND i.access_epoch=v_act.actor_epoch AND i.access_state='active' AND u.is_active)
 THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 -- A later own conflict removes an answer from the roster; it does not invalidate
 -- the historical authority for an earlier, lawful confirmation in this transaction.
 IF v_act.operation IN ('CONFIRM','QUESTION') AND EXISTS(SELECT FROM public.core_cqact c WHERE c.thread_uuid=v_act.thread_uuid
   AND c.actor_id=v_act.actor_id AND c.operation='CONFLICT' AND c.ordinal>v_act.ordinal) THEN
   IF public.disc_member_state(v_act.organism_id,v_act.actor_id,statement_timestamp()) IS DISTINCT FROM 'ACTIVE'
     OR NOT EXISTS(SELECT FROM public.core_discboard b WHERE b.board_uuid=v_act.board_uuid AND
         public.disc_audience(b.organism_id,b.circle_id,v_act.actor_id,'MEMBERS',statement_timestamp()))
   THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
   RETURN NULL;
 END IF;
 v_source:=public.cq_source(v_act.organism_id,v_act.board_uuid,v_act.thread_uuid,v_act.operation,v_act.actor_id,statement_timestamp());
 IF v_source IS DISTINCT FROM v_act.authority_source THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 RETURN NULL;
END $$;
CREATE FUNCTION public.cq_execute(p_owner uuid,p_board uuid,p_thread uuid,p_op text,p_key uuid,p_args jsonb,
 p_actor bigint,p_epoch bigint,p_received timestamptz) RETURNS uuid LANGUAGE plpgsql VOLATILE AS $$
DECLARE v_lo bigint; v_old public.core_cqact; v_existing_board uuid;
 v_constraint text; v_table text; v_schema text;
BEGIN
 SELECT id INTO v_lo FROM public.core_livingorganism WHERE organism_id=p_owner;
 IF v_lo IS NULL THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 PERFORM public.cq_source(v_lo,p_board,p_thread,p_op,p_actor,statement_timestamp());
 SELECT * INTO v_old FROM public.core_cqact WHERE act_uuid=p_key;
 IF FOUND THEN
   IF v_old.organism_id<>v_lo OR v_old.board_uuid IS DISTINCT FROM p_board THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
   IF v_old.thread_uuid IS DISTINCT FROM p_thread OR v_old.operation<>p_op OR v_old.actor_id<>p_actor
      OR v_old.actor_epoch<>p_epoch OR v_old.request IS DISTINCT FROM p_args THEN PERFORM public.cq_refuse('CQ_REQUEST'); END IF;
   RETURN p_key;
 END IF;
 SELECT b.board_uuid INTO v_existing_board FROM (
   SELECT act_uuid,board_uuid FROM public.core_discact UNION ALL SELECT act_uuid,board_uuid FROM public.core_discpostact
 ) b WHERE b.act_uuid=p_key;
 IF v_existing_board IS NOT NULL AND v_existing_board=p_board THEN PERFORM public.cq_refuse('CQ_REQUEST'); END IF;
 IF EXISTS(SELECT FROM public.core_livingorganismevent WHERE event_uuid=p_key) THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 INSERT INTO public.core_cqact(act_uuid,organism_id,board_uuid,thread_uuid,operation,actor_id,actor_epoch,received_at,request)
 VALUES(p_key,v_lo,p_board,p_thread,p_op,p_actor,p_epoch,p_received,p_args);
 RETURN p_key;
EXCEPTION WHEN unique_violation THEN
 GET STACKED DIAGNOSTICS v_constraint=CONSTRAINT_NAME,v_table=TABLE_NAME,v_schema=SCHEMA_NAME;
 IF v_schema='public' AND v_table='core_livingorganismevent' AND EXISTS(SELECT FROM pg_constraint c
   WHERE c.conrelid='public.core_livingorganismevent'::regclass AND c.conname=v_constraint AND c.contype='u'
     AND c.conkey=ARRAY[(SELECT a.attnum FROM pg_attribute a WHERE a.attrelid=c.conrelid AND a.attname='event_uuid')])
 THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 RAISE;
END $$;
CREATE FUNCTION public.cq_edit_lock() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE h public.cq_phases;
BEGIN
 SELECT phase.* INTO h FROM public.core_discpost p JOIN public.cq_phases phase USING(thread_uuid)
 WHERE p.post_uuid::text=NEW.payload->>'post' AND p.post_number=1;
 IF h.phase='CLARITY' AND statement_timestamp()>=h.response_deadline THEN
   PERFORM public.cq_refuse('CQ_EDIT_DUE');
 ELSIF h.phase='CLARITY' AND statement_timestamp()>=h.deadline THEN
   PERFORM public.cq_refuse('CQ_WINDOW_LOCKED');
 ELSIF h.phase='CLOSED' OR EXISTS(SELECT FROM public.core_cqact a WHERE a.thread_uuid=h.thread_uuid AND a.operation='NO_BALLOT') THEN
   PERFORM public.cq_refuse('CQ_CLOSED_LOCKED');
 ELSIF EXISTS(SELECT FROM public.core_cqact a WHERE a.thread_uuid=h.thread_uuid AND a.operation='PROCEED') THEN
   PERFORM public.cq_refuse('CQ_LOCKED');
 ELSIF EXISTS(SELECT FROM public.core_cqact a WHERE a.thread_uuid=h.thread_uuid AND a.operation='OPEN_VOTE') THEN
   PERFORM public.cq_refuse('CQ_VOTE_LOCKED');
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER cq_input BEFORE INSERT ON public.core_cqact FOR EACH ROW EXECUTE FUNCTION public.cq_input();
CREATE TRIGGER cq_append AFTER INSERT ON public.core_cqact FOR EACH ROW EXECUTE FUNCTION public.cq_append();
CREATE TRIGGER cq_immutable BEFORE UPDATE OR DELETE ON public.core_cqact FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE TRIGGER s015_cq_no_truncate BEFORE TRUNCATE ON public.core_cqact FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE CONSTRAINT TRIGGER cq_complete AFTER INSERT ON public.core_cqact DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION public.cq_complete();
-- The act-side deferred check proves the complete pair. This direction only needs to reject an orphan.
CREATE FUNCTION public.cq_event_pair() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT FROM public.core_cqact a WHERE a.act_uuid=NEW.event_uuid AND a.execution_xid=pg_current_xact_id())
 THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER cq_event_complete AFTER INSERT ON public.core_livingorganismevent DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW WHEN (NEW.action='RECORD_CQ') EXECUTE FUNCTION public.cq_event_pair();
-- Runs after DISC1's scope/authority/input trigger, under the same owner lock.
CREATE TRIGGER discq_locked_edit BEFORE INSERT ON public.core_discpostact FOR EACH ROW WHEN (NEW.operation='EDIT')
 EXECUTE FUNCTION public.cq_edit_lock();
"""

# Keep DISC1's original thread view projection and column types, except the effective kind.
old_view = (
    "CREATE OR REPLACE VIEW public.discp_threads AS"
    + posts.FORWARD.split("CREATE VIEW public.discp_threads AS", 1)[1].split(
        "CREATE VIEW public.discp_posts", 1
    )[0]
)
new_view = replace_once(
    old_view,
    "SELECT t.*,",
    """SELECT t.thread_uuid,t.category_uuid,t.creation_act,t.topic_type,t.title,
 CASE WHEN EXISTS(SELECT FROM public.core_cqact cq WHERE cq.thread_uuid=t.thread_uuid AND cq.operation='INITIATE')
 THEN 'GOVERNANCE'::text ELSE t.record_kind END AS record_kind,""",
)
se_body = replace_once(
    boards.SE_BODY,
    "p_capability='DISCUSSION_CUSTOM_CATEGORIES'",
    "p_capability IN ('DISCUSSION_CUSTOM_CATEGORIES','CQ_COORDINATOR_INITIATION','CQ_TOTALS_ONLY')",
)

REVERSE = """
DROP TRIGGER discq_locked_edit ON public.core_discpostact;
DROP TRIGGER cq_event_complete ON public.core_livingorganismevent;
DROP VIEW public.cq_phases,public.cq_reporting_rules;
DROP TABLE public.core_cqact;
DROP VIEW public.cq_platform_defaults;
DROP TABLE public.core_cqreportingdefault;
DROP FUNCTION public.cq_event_pair(),public.cq_check_values(jsonb,timestamptz,text,boolean),
 public.cq_check_provisions(bigint,uuid,jsonb,timestamptz),public.cq_law_current(uuid,jsonb,timestamptz),public.cq_input(),public.cq_append(),public.cq_complete(),public.cq_edit_lock(),
 public.cq_event_matches(public.core_livingorganismevent),public.cq_refuse(text),public.cq_reporting_at(bigint,timestamptz),public.cq_settings(bigint),public.cq_settings_at(bigint,timestamptz),public.cq_response_deadline(timestamptz),public.cq_deadline(timestamptz,integer),
 public.cq_applicable(jsonb,uuid,uuid,text),public.cq_scoped_settings(jsonb,uuid,uuid,uuid,text,timestamptz),
 public.cq_roster(uuid,uuid,timestamptz),public.cq_checked_settings(bigint,uuid,jsonb,timestamptz),
 public.cq_source(bigint,uuid,uuid,text,bigint,timestamptz),
 public.cq_phase_facts(bigint,uuid,uuid,text,jsonb,bigint,timestamptz),
 public.cq_execute(uuid,uuid,uuid,text,uuid,jsonb,bigint,bigint,timestamptz);
"""


def switch(editor, backwards=False):
    writer, gateway = posts.bodies()
    old_writer = replace_once(
        writer, " SELECT string_agg", posts.WRITER_BRANCH + " SELECT string_agg"
    )
    old_gateway = replace_once(gateway, "BEGIN\n", "BEGIN\n" + posts.GATEWAY_BRANCH)
    branch = posts.WRITER_BRANCH.replace("RECORD_DISCUSSION_POST", "RECORD_CQ")
    new_writer = replace_once(
        old_writer, " SELECT string_agg", branch + " SELECT string_agg"
    )
    branch = (
        posts.GATEWAY_BRANCH.replace("RECORD_DISCUSSION_POST", "RECORD_CQ")
        .replace("discp_event_matches", "cq_event_matches")
        .replace("DISC_RECORD", "CQ_COMPLETE")
    )
    new_gateway = replace_once(old_gateway, "BEGIN\n", "BEGIN\n" + branch)
    with editor.connection.cursor() as cursor:
        for signature, expected in (
            (
                "public.s3fu_guard_gateway_effect()",
                new_gateway if backwards else old_gateway,
            ),
            (
                "public.s015_entitlement_effective(uuid,text,timestamptz)",
                se_body if backwards else boards.SE_BODY,
            ),
        ):
            cursor.execute(
                "SELECT prosrc FROM pg_proc WHERE oid=%s::regprocedure", [signature]
            )
            if cursor.fetchone()[0].strip() != expected.strip():
                raise RuntimeError(
                    f"DISC2 stopped: installed definition differs for {signature}."
                )
    boards.gi.law.writer.switch_writer(
        editor,
        new_writer if backwards else old_writer,
        old_writer if backwards else new_writer,
    )
    editor.execute(
        "CREATE OR REPLACE FUNCTION public.s3fu_guard_gateway_effect() RETURNS trigger LANGUAGE plpgsql AS $$"
        + (old_gateway if backwards else new_gateway)
        + "$$;",
        params=None,
    )
    editor.execute(
        boards.SE_HEADER + (boards.SE_BODY if backwards else se_body) + "$$;",
        params=None,
    )


def restore_reporting_default(apps, editor):
    """The database test reset may also run at an earlier migration head."""
    with editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT to_regclass('public.core_cqreportingdefault') IS NOT NULL"
        )
        if cursor.fetchone()[0]:
            cursor.execute(
                "INSERT INTO public.core_cqreportingdefault DEFAULT VALUES ON CONFLICT DO NOTHING"
            )


def install(apps, editor):
    editor.execute(FORWARD + new_view, params=None)
    switch(editor)


def reverse(apps, editor):
    editor.execute(
        "LOCK TABLE public.core_cqact,public.core_livingorganismevent IN ACCESS EXCLUSIVE MODE",
        params=None,
    )
    with editor.connection.cursor() as cursor:
        cursor.execute("SELECT EXISTS(SELECT FROM public.core_cqact)")
        if cursor.fetchone()[0]:
            raise RuntimeError("DISC2 reversal refused while CQ history exists.")
    switch(editor, True)
    editor.execute(old_view + REVERSE, params=None)


class Migration(migrations.Migration):
    atomic = True
    dependencies: ClassVar = [("core", "0073_discussion_posts")]
    operations: ClassVar = [migrations.RunPython(install, reverse)]
