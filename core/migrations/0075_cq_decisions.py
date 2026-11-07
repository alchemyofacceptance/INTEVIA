"""DISC2. Decision ballots, sealed results and numbered Group Gaze posts."""

from importlib import import_module
from typing import ClassVar

from django.db import migrations

clarity = import_module("core.migrations.0074_cq_clarity")
posts = clarity.posts

BALLOT_OPS = ("OPEN_VOTE", "ANSWER", "SETTLE", "REOPEN_VOTE", "RESPONSE_DUE", "NO_BALLOT")
PRIOR_OPS = (
    "SETTINGS",
    "INITIATE",
    "PREPARED",
    "PREPARATION_DUE",
    "CONFIRM",
    "QUESTION",
    "CONFLICT",
    "EXTEND",
    "CLOSE",
    "PROCEED",
    "WITHDRAW",
)


def operations_constraint(operations):
    choices = ",".join("'" + op + "'" for op in operations)
    return (
        "ALTER TABLE public.core_cqact DROP CONSTRAINT core_cqact_operation_check, "
        "ADD CONSTRAINT core_cqact_operation_check CHECK (operation IN ("
        + choices
        + "));"
    )


FORWARD = r"""
CREATE UNIQUE INDEX cq_one_first_vote ON public.core_cqact(thread_uuid) WHERE operation IN ('PROCEED','OPEN_VOTE');
CREATE UNIQUE INDEX cq_one_retry ON public.core_cqact(thread_uuid) WHERE operation='REOPEN_VOTE';
CREATE UNIQUE INDEX cq_one_answer ON public.core_cqact((request->>'ballot'),actor_id) WHERE operation='ANSWER';
CREATE UNIQUE INDEX cq_no_ballot_once ON public.core_cqact(thread_uuid) WHERE operation='NO_BALLOT';
CREATE UNIQUE INDEX cq_one_result ON public.core_cqact((request->>'ballot')) WHERE operation='SETTLE';

-- Use only membership/place facts effective AND known at the cut. Authentication
-- of today's viewer is a different operation. A rejoin has a different period.
CREATE FUNCTION public.cq_continuing(p_board uuid,p_thread uuid,p_pool jsonb,p_cut timestamptz)
RETURNS jsonb LANGUAGE sql STABLE AS $$
 SELECT coalesce(jsonb_agg(x.member ORDER BY m.identity_id),'[]'::jsonb)
 FROM jsonb_array_elements(p_pool) x(member)
 JOIN public.core_discboard b ON b.board_uuid=p_board
 JOIN public.core_organismmembership m ON m.living_organism_id=b.organism_id
     AND m.membership_uuid=(x.member->>'membership')::uuid AND m.identity_id=(x.member->>'person')::bigint
 WHERE public.disc_member_state(b.organism_id,m.identity_id,p_cut)='ACTIVE'
 AND (x.member->>'active_period')::uuid IS NOT DISTINCT FROM (
    SELECT t.event_uuid FROM public.core_organismmembershiptransition t
    WHERE t.membership_id=m.id AND t.resulting_state='ACTIVE' AND t.prior_state IS DISTINCT FROM 'ACTIVE'
    AND greatest(t.received_at,t.recorded_at,t.effective_at)<=p_cut ORDER BY t.effective_at DESC,t.sequence DESC LIMIT 1)
 AND (b.circle_id IS NULL OR EXISTS(
    SELECT FROM public.core_circleparticipation cp WHERE cp.circle_id=b.circle_id AND cp.membership_id=m.id
    AND cp.id=(x.member->>'place')::bigint AND greatest(cp.received_at,cp.recorded_at,cp.effective_at)<=p_cut
    AND NOT EXISTS(SELECT FROM public.core_circleparticipationend e WHERE e.participation_id=cp.id
      AND greatest(e.received_at,e.recorded_at,e.effective_at)<=p_cut)))
 AND NOT EXISTS(SELECT FROM public.core_cqact c WHERE c.thread_uuid=p_thread AND c.operation='CONFLICT'
    AND c.actor_id=m.identity_id AND greatest(c.received_at,c.recorded_at,c.effective_at)<=p_cut)
$$;

CREATE FUNCTION public.cq_count_decision(p_rules jsonb,p_size integer,p_yes integer,p_no integer,p_abstain integer)
RETURNS jsonb LANGUAGE plpgsql IMMUTABLE STRICT AS $$
DECLARE v_required integer; v_base bigint; v_present integer:=p_yes+p_no+p_abstain; v_kind text; v_cqt boolean:=false;
BEGIN
 v_required:=CASE p_rules->>'quorum_form' WHEN 'NO_FLOOR' THEN 0
   WHEN 'ABSOLUTE' THEN (p_rules->>'quorum_value')::int
   WHEN 'PERCENT' THEN ceil(p_size::numeric*(p_rules->>'quorum_value')::int/100)::int
   ELSE p_size/2+1 END;
 IF NOT coalesce((p_rules->>'waive_minimum')::boolean,false) THEN v_required:=greatest(3,v_required); END IF;
 v_base:=CASE p_rules->>'threshold_base' WHEN 'ELIGIBLE' THEN p_size
   WHEN 'PARTICIPANTS' THEN v_present ELSE p_yes+p_no END;
 IF p_size<3 THEN v_kind:='NO_DECISION_FEWER_THAN_THREE';
 ELSIF v_present<v_required THEN v_kind:='NOT_QUALIFIED_INSUFFICIENT_PARTICIPATION';
 ELSE
   v_cqt:=true;
   IF p_yes=p_no THEN v_kind:='NOT_QUALIFIED_TIE'; -- CQ_DEFAULT_TIE
   ELSIF p_rules->>'threshold'='FRACTION' THEN
     IF p_yes::numeric*(p_rules->>'denominator')::integer>=v_base::numeric*(p_rules->>'numerator')::integer
        AND v_base>0 THEN v_kind:='QUALIFIED'; ELSE v_kind:='NOT_QUALIFIED_VOTE'; END IF;
   ELSIF p_yes::bigint*2>v_base THEN v_kind:='QUALIFIED'; ELSE v_kind:='NOT_QUALIFIED_VOTE'; END IF;
 END IF;
 RETURN jsonb_build_object('kind',v_kind,'eligible',p_size,'participated',v_present,
  'required',v_required,'qualified',p_yes,'not_qualified',p_no,'abstained',p_abstain,
  'cqt_applied',v_cqt,'qualification_denominator',CASE WHEN v_cqt THEN v_base END);
END $$;

CREATE FUNCTION public.cq_ballot_entries(p_ballot uuid,p_cut timestamptz)
RETURNS TABLE(person bigint,identity_uuid uuid,display_name text,choice text) LANGUAGE sql STABLE AS $$
 SELECT i.id,i.identity_id,coalesce(nullif(i.display_name,''),'Member'),ans.request->>'choice'
 FROM public.core_cqact o
 CROSS JOIN LATERAL jsonb_array_elements(public.cq_continuing(o.board_uuid,o.thread_uuid,o.facts->'roster',p_cut)) x(member)
 JOIN public.core_identity i ON i.id=(x.member->>'person')::bigint
 LEFT JOIN public.core_cqact ans ON ans.operation='ANSWER' AND ans.request->>'ballot'=o.act_uuid::text
   AND ans.actor_id=i.id AND greatest(ans.received_at,ans.effective_at,ans.recorded_at)<=p_cut
 WHERE o.act_uuid=p_ballot AND o.operation IN ('PROCEED','OPEN_VOTE','REOPEN_VOTE')
 ORDER BY i.id
$$;

-- The command checks personal eligibility before looking up the request UUID.
-- The input guard repeats the check when deriving a new answer. Commit-time
-- lineage checks continue to examine the recorded act and its authority.
CREATE FUNCTION public.cq_answer_eligible(p_board uuid,p_thread uuid,p_ballot text,p_person bigint,p_cut timestamptz)
RETURNS void LANGUAGE plpgsql STABLE AS $$
DECLARE v_open public.core_cqact; v_ready public.core_cqact; v_cut timestamptz;
BEGIN
 SELECT * INTO v_open FROM public.core_cqact WHERE thread_uuid=p_thread AND board_uuid=p_board
   AND operation IN ('PROCEED','OPEN_VOTE','REOPEN_VOTE') AND act_uuid::text=p_ballot;
 IF v_open.act_uuid IS NULL THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 v_cut:=least(p_cut,(v_open.facts->>'deadline')::timestamptz,(
     SELECT (r.facts->>'closed_at')::timestamptz FROM public.core_cqact r WHERE r.operation='SETTLE'
      AND r.request->>'ballot'=v_open.act_uuid::text));
 IF EXISTS(SELECT FROM public.core_cqact c WHERE c.operation='CONFLICT' AND c.thread_uuid=p_thread
      AND c.actor_id=p_person AND c.effective_at<=v_cut AND c.recorded_at<=v_cut)
   THEN PERFORM public.cq_refuse('CQ_CONFLICTED'); END IF;
 IF EXISTS(SELECT FROM public.cq_ballot_entries(v_open.act_uuid,v_cut) e WHERE e.person=p_person) THEN RETURN; END IF;
 SELECT * INTO v_ready FROM public.core_cqact WHERE act_uuid=(v_open.facts->>'pre_vote_act')::uuid;
 IF (v_ready.facts->>'clarity_gate')::boolean
    AND EXISTS(SELECT FROM jsonb_array_elements(v_ready.facts->'eligible') x WHERE x->>'person'=p_person::text)
    AND NOT EXISTS(SELECT FROM jsonb_array_elements(v_ready.facts->'confirmed') x WHERE x->>'person'=p_person::text)
    THEN PERFORM public.cq_refuse('CQ_SAT_OUT'); END IF;
 PERFORM public.cq_refuse('CQ_ELECTORATE');
END $$;

CREATE FUNCTION public.cq_can_settle(p_ballot uuid,p_cut timestamptz) RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT (o.facts->>'deadline')::timestamptz<=p_cut OR
    (SELECT count(*)<3 OR count(*)=count(v.choice) FROM public.cq_ballot_entries(o.act_uuid,p_cut) v)
 FROM public.core_cqact o WHERE o.act_uuid=p_ballot AND o.operation IN ('PROCEED','OPEN_VOTE','REOPEN_VOTE')
$$;

CREATE FUNCTION public.cq_open_roster(p_ready jsonb,p_board uuid,p_thread uuid,p_cut timestamptz) RETURNS jsonb
LANGUAGE sql STABLE AS $$
 SELECT CASE WHEN (p_ready->>'clarity_gate')::boolean THEN
     public.cq_continuing(p_board,p_thread,p_ready->'confirmed',p_cut)
   ELSE public.cq_roster(p_board,p_thread,p_cut) END
$$;

-- A rule opening is a direct consequence of this same-transaction preparation
-- or elapsed response. The prompting identity remains the human enterer.
CREATE FUNCTION public.cq_open_source(p_lo bigint,p_board uuid,p_thread uuid,p_actor bigint,p_epoch bigint,p_request jsonb)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE origin public.core_cqact;
BEGIN
 SELECT * INTO origin FROM public.core_cqact WHERE act_uuid::text=p_request->>'trigger_act'
   AND organism_id=p_lo AND board_uuid=p_board AND thread_uuid=p_thread AND actor_id=p_actor AND actor_epoch=p_epoch
   AND execution_xid=pg_current_xact_id() AND operation IN ('PREPARED','PREPARATION_DUE','RESPONSE_DUE') AND facts->>'rule_open'='true';
 IF origin.act_uuid IS NULL THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 RETURN jsonb_build_object('kind','RULE','reference','HG:DISC2:A2/D7','route',origin.operation,'cause',origin.act_uuid);
END $$;
CREATE FUNCTION public.cq_next_post(p_thread uuid) RETURNS integer LANGUAGE sql STABLE AS $$
 SELECT (1+(SELECT count(*) FROM public.core_discpost WHERE thread_uuid=p_thread)
          +(SELECT count(*) FROM public.core_cqact WHERE thread_uuid=p_thread AND operation IN ('SETTLE','CLOSE','NO_BALLOT')))::int
$$;
CREATE FUNCTION public.cq_ballot_facts(p_lo bigint,p_board uuid,p_thread uuid,p_op text,p_key uuid,
 p_args jsonb,p_actor bigint,p_cut timestamptz) RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE v_head public.cq_phases; v_open public.core_cqact; v_ready public.core_cqact; v_prior public.core_cqact;
 v_wording public.discp_posts; v_stale jsonb; v_block text; v_available boolean; v_owner uuid; v_setting uuid; v_rules jsonb; v_report jsonb; v_roster jsonb; v_deadline timestamptz;
 v_cut timestamptz; v_count jsonb; v_gaze jsonb; v_participation jsonb; v_pos integer; v_eligible integer; v_yes integer; v_no integer;
 v_abstain integer; v_attempt integer:=1; v_retry boolean; v_previous uuid; v_mode text;
BEGIN
 IF p_op='ANSWER' THEN PERFORM public.cq_answer_eligible(p_board,p_thread,p_args->>'ballot',p_actor,p_cut); END IF;
 SELECT * INTO v_head FROM public.cq_phases WHERE thread_uuid=p_thread;
 SELECT * INTO v_ready FROM public.core_cqact WHERE thread_uuid=p_thread
   AND (operation='PROCEED' OR facts->>'rule_open'='true') ORDER BY ordinal DESC LIMIT 1;
 SELECT organism_id INTO v_owner FROM public.core_livingorganism WHERE id=p_lo;
 IF p_op='OPEN_VOTE' THEN PERFORM public.cq_refuse('CQ_NOT_READY'); END IF;
 IF p_op IN ('PROCEED','RULE_OPEN','REOPEN_VOTE') THEN
   IF p_op<>'REOPEN_VOTE' THEN
     IF p_op='PROCEED' THEN
       v_ready.act_uuid:=p_key;
       v_ready.facts:=public.cq_phase_facts(p_lo,p_board,p_thread,p_op,p_args,p_actor,p_cut);
     END IF;
     IF p_op='RULE_OPEN' AND (v_ready.act_uuid::text IS DISTINCT FROM p_args->>'trigger_act'
       OR v_head.phase NOT IN ('PREPARATION','CLARITY')) THEN PERFORM public.cq_refuse('CQ_NO_DUE'); END IF;
     SELECT * INTO STRICT v_wording FROM public.discp_posts WHERE thread_uuid=p_thread AND post_number=1;
     SELECT NOT (v_wording.author_hidden OR v_wording.coordinator_hidden OR c.access_level='COORDINATORS') INTO STRICT v_available
       FROM public.discp_threads t JOIN public.disc_categories c USING(category_uuid) WHERE t.thread_uuid=p_thread;
     v_rules:=public.cq_settings_at(p_lo,p_cut);
     SELECT act_uuid INTO v_setting FROM public.core_cqact WHERE organism_id=p_lo AND operation='SETTINGS'
       AND recorded_at<=p_cut ORDER BY ordinal DESC LIMIT 1;
     SELECT coalesce(jsonb_agg(x.value ORDER BY x.n),'[]'::jsonb) INTO v_stale
       FROM jsonb_array_elements((v_ready.facts->'applied_provisions')||public.cq_applicable(v_rules,p_board,p_thread,'VOTE'))
         WITH ORDINALITY AS x(value,n) WHERE NOT public.cq_law_current(v_owner,x.value,p_cut);
     v_roster:=public.cq_open_roster(v_ready.facts,p_board,p_thread,p_cut);
     IF v_stale<>'[]'::jsonb THEN v_block:='CQ_LAW';
     ELSIF NOT v_available THEN v_block:='CQ_FULL_AUDIENCE';
     ELSIF v_ready.operation='RESPONSE_DUE' AND (v_ready.facts->>'clarity_gate')::boolean
        AND jsonb_array_length(v_ready.facts->'confirmed')<(v_ready.facts->>'required')::integer
        AND jsonb_array_length(v_ready.facts->'eligible')>=3 THEN v_block:='CQ_CDPT';
     ELSIF jsonb_array_length(v_roster)<3 THEN v_block:='CQ_THREE'; END IF;
     IF v_block IS NOT NULL THEN
       IF p_op<>'RULE_OPEN' THEN PERFORM public.cq_refuse(v_block); END IF;
       RETURN jsonb_build_object('phase','RESULT','event_kind','DECISION','ballot',NULL,'version',v_ready.facts->'version',
         'wording_fingerprint',v_ready.facts->'wording_fingerprint','post_number',public.cq_next_post(p_thread),
         'pre_vote_act',v_ready.act_uuid,'invalid_provisions',v_stale,'settings_version',v_setting,'reason',v_block,
         'clarity_gate',v_ready.facts->'clarity_gate','reporting_rule',public.cq_reporting_at(p_lo,p_cut),
         'closed_at',p_cut,'settled_at',p_cut,'restart',false,'retry_requested',false,
         'count',jsonb_build_object('eligible',jsonb_array_length(v_roster),'cqt_applied',false,'kind',
           CASE v_block WHEN 'CQ_LAW' THEN 'NO_DECISION_PROVISION_ENDED' WHEN 'CQ_THREE' THEN 'NO_DECISION_FEWER_THAN_THREE'
             WHEN 'CQ_CDPT' THEN 'NO_DECISION_CLARITY_THRESHOLD' ELSE 'NO_DECISION_MOTION_UNAVAILABLE' END)
           || CASE WHEN v_block='CQ_CDPT' THEN jsonb_build_object('clarity_eligible',jsonb_array_length(v_ready.facts->'eligible'),
             'clarity_confirmed',jsonb_array_length(v_ready.facts->'confirmed'),'clarity_required',v_ready.facts->'required') ELSE '{}'::jsonb END);
     END IF;
     v_rules:=public.cq_scoped_settings(v_rules,v_owner,p_board,p_thread,'VOTE',p_cut);
   ELSE
     SELECT * INTO v_prior FROM public.core_cqact WHERE act_uuid=v_head.act_uuid AND operation='SETTLE';
     IF v_prior.act_uuid IS NULL OR (v_prior.facts->>'restart')::boolean IS NOT TRUE
        OR v_prior.act_uuid::text IS DISTINCT FROM p_args->>'result'
        OR v_prior.execution_xid<>pg_current_xact_id()
        THEN PERFORM public.cq_refuse('CQ_NO_DUE'); END IF;
     SELECT * INTO STRICT v_open FROM public.core_cqact WHERE act_uuid=(v_prior.request->>'ballot')::uuid;
     v_rules:=v_open.facts->'rules';
     v_ready.facts:=v_ready.facts||jsonb_build_object('version',v_open.facts->'version','wording_fingerprint',v_open.facts->'wording_fingerprint');
     v_setting:=(v_open.facts->>'settings_version')::uuid; v_attempt:=2; v_previous:=v_prior.act_uuid;
   END IF;
   -- The retry preserves the decision procedure. Reporting is a display rule
   -- chosen for each ballot at its own opening, including a fresh retry.
   v_report:=public.cq_reporting_at(p_lo,p_cut);
   v_rules:=v_rules||jsonb_build_object('clarity_gate',v_ready.facts->'clarity_gate','clarity_stage',v_ready.facts->'clarity_stage','reporting',v_report->'value','reporting_scope',v_report->'scope');
   IF p_op='REOPEN_VOTE' THEN v_roster:=public.cq_open_roster(v_ready.facts,p_board,p_thread,p_cut); END IF;
   IF jsonb_array_length(v_roster)<3 THEN PERFORM public.cq_refuse('CQ_THREE'); END IF;
   RETURN (CASE WHEN p_op='PROCEED' THEN v_ready.facts ELSE '{}'::jsonb END) || jsonb_build_object('phase','VOTING','event_kind','DECISION','ballot',p_key,'attempt',v_attempt,
     'previous_result',v_previous,'settings_version',v_setting,'rules',v_rules,'reporting_rule',v_report,
     'pre_vote_act',v_ready.act_uuid,'clarity_gate',v_ready.facts->'clarity_gate','clarity_provisions',v_ready.facts->'applied_provisions','clarity',CASE WHEN (v_ready.facts->>'clarity_stage')::boolean THEN v_ready.act_uuid END,'version',v_ready.facts->'version','wording_fingerprint',v_ready.facts->'wording_fingerprint',
     'roster',v_roster,'deadline',public.cq_deadline(p_cut,(v_rules->>'vote_days')::integer));
 END IF;
 IF v_head.phase IS DISTINCT FROM 'VOTING' THEN
   IF p_op='CONFLICT' THEN PERFORM public.cq_refuse('CQ_CONFLICT_PERIOD'); END IF;
   IF p_op='SETTLE' THEN PERFORM public.cq_refuse('CQ_NO_DUE'); END IF;
   IF v_head.phase='RESULT' THEN PERFORM public.cq_refuse('CQ_VOTE_CLOSED'); END IF;
   PERFORM public.cq_refuse('CQ_NOT_OPEN');
 ELSE
   SELECT * INTO STRICT v_open FROM public.core_cqact WHERE act_uuid=v_head.act_uuid;
   v_deadline:=(v_open.facts->>'deadline')::timestamptz;
   IF p_op<>'CONFLICT' AND p_args->>'ballot' IS DISTINCT FROM v_open.act_uuid::text THEN
     IF EXISTS(SELECT FROM public.core_cqact WHERE thread_uuid=p_thread AND act_uuid::text=p_args->>'ballot'
         AND operation IN ('PROCEED','OPEN_VOTE','REOPEN_VOTE')) THEN PERFORM public.cq_refuse('CQ_VOTE_CLOSED'); END IF;
     PERFORM public.cq_refuse('DISC_UNAVAILABLE');
   END IF;
   IF p_op<>'SETTLE' AND p_cut>=v_deadline THEN PERFORM public.cq_refuse('CQ_VOTE_CLOSED'); END IF;
   IF p_op='CONFLICT' THEN
     IF EXISTS(SELECT FROM public.core_cqact WHERE thread_uuid=p_thread AND operation='CONFLICT' AND actor_id=p_actor) THEN
       PERFORM public.cq_refuse('CQ_CONFLICT_REPEAT');
     END IF;
     IF NOT EXISTS(SELECT FROM public.cq_ballot_entries(v_open.act_uuid,p_cut) e WHERE e.person=p_actor) THEN
       PERFORM public.cq_refuse('CQ_ELECTORATE');
     END IF;
   END IF;
 END IF;
 IF p_op='CONFLICT' THEN
   IF jsonb_typeof(p_args->'basis') IS DISTINCT FROM 'string' OR nullif(btrim(p_args->>'basis'),'') IS NULL
     THEN PERFORM public.cq_refuse('CQ_CONFLICT_BASIS'); END IF;
   SELECT * INTO STRICT v_wording FROM public.discp_posts WHERE thread_uuid=p_thread AND post_number=1;
   RETURN jsonb_build_object('version',v_wording.version_uuid,'basis',p_args->'basis','ballot',v_open.act_uuid);
 END IF;
 IF p_op='ANSWER' THEN
   IF EXISTS(SELECT FROM public.core_cqact WHERE operation='ANSWER' AND request->>'ballot'=v_open.act_uuid::text AND actor_id=p_actor)
     THEN PERFORM public.cq_refuse('CQ_DUPLICATE'); END IF;
   IF coalesce(p_args->>'choice' IN ('QUALIFIED','NOT_QUALIFIED','ABSTAIN'),false) IS NOT TRUE
     THEN PERFORM public.cq_refuse('CQ_CHOICE'); END IF;
   IF p_args->>'choice'='ABSTAIN' AND (v_open.facts->'rules'->>'abstention')::boolean IS NOT TRUE
     THEN PERFORM public.cq_refuse('CQ_ABSTAIN_OFF'); END IF;
   RETURN jsonb_build_object('version',v_open.facts->'version','ballot',v_open.act_uuid);
 END IF;
 IF p_op<>'SETTLE' OR NOT public.cq_can_settle(v_open.act_uuid,p_cut) THEN PERFORM public.cq_refuse('CQ_NO_DUE'); END IF;
 v_cut:=least(p_cut,v_deadline); v_rules:=v_open.facts->'rules'; v_mode:=v_open.facts->'reporting_rule'->>'value';
 SELECT count(*),count(*) FILTER(WHERE e.choice='QUALIFIED'),count(*) FILTER(WHERE e.choice='NOT_QUALIFIED'),
    count(*) FILTER(WHERE e.choice='ABSTAIN'),
    jsonb_agg(jsonb_build_object('identity',e.identity_uuid,'name',e.display_name,'answer',e.choice) ORDER BY e.person)
       FILTER(WHERE e.choice IS NOT NULL),
    coalesce(jsonb_agg(jsonb_build_object('identity',e.identity_uuid,'status',
     CASE WHEN e.choice IS NULL THEN 'NO_ANSWER_BY_CLOSE' ELSE 'ANSWERED' END) ORDER BY e.person),'[]'::jsonb)
 INTO v_eligible,v_yes,v_no,v_abstain,v_gaze,v_participation FROM public.cq_ballot_entries(v_open.act_uuid,v_cut) e;
 v_count:=public.cq_count_decision(v_rules,v_eligible,v_yes,v_no,v_abstain);
 v_retry:=v_count->>'kind'='NOT_QUALIFIED_INSUFFICIENT_PARTICIPATION'
     AND v_rules->>'low_turnout'='NEW_CQ' AND (v_open.facts->>'attempt')::int=1;
 v_pos:=public.cq_next_post(p_thread);
 RETURN jsonb_build_object('phase','RESULT','ballot',v_open.act_uuid,'event_kind','DECISION','version',v_open.facts->'version',
     'wording_fingerprint',v_open.facts->'wording_fingerprint','attempt',v_open.facts->'attempt',
     'reporting_rule',v_open.facts->'reporting_rule','opened_at',v_open.effective_at,'deadline',v_deadline,
     'closed_at',v_cut,'settled_at',p_cut,'count',v_count,'post_number',v_pos,'group_gaze',
     CASE WHEN v_mode='NAMED' THEN coalesce(v_gaze,'[]'::jsonb) END,
     'restart',v_retry AND jsonb_array_length(public.cq_open_roster(v_ready.facts,p_board,p_thread,p_cut))>=3,
     'retry_requested',v_retry,'participation_record',v_participation);
END $$;
"""

FORWARD += r"""
CREATE FUNCTION public.cq_vote_followup() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_open uuid; v_op text; v_args jsonb;
BEGIN
 IF NEW.operation IN ('PREPARED','PREPARATION_DUE','RESPONSE_DUE') THEN
   IF NEW.facts->>'rule_open' IS DISTINCT FROM 'true' THEN RETURN NULL; END IF;
   v_op:='RULE_OPEN'; v_args:=jsonb_build_object('trigger_act',NEW.act_uuid);
 ELSIF NEW.operation IN ('ANSWER','CONFLICT') THEN
   SELECT h.act_uuid INTO v_open FROM public.cq_phases h
    WHERE h.thread_uuid=NEW.thread_uuid AND h.phase='VOTING';
   IF v_open IS NULL OR NOT public.cq_can_settle(v_open,statement_timestamp()) THEN RETURN NULL; END IF;
   v_op:='SETTLE'; v_args:=jsonb_build_object('ballot',v_open);
 ELSIF NEW.operation='SETTLE' AND (NEW.facts->>'restart')::boolean THEN
   v_op:='REOPEN_VOTE'; v_args:=jsonb_build_object('result',NEW.act_uuid);
   IF NEW.request ? 'cause' THEN v_args:=v_args||jsonb_build_object('cause',NEW.request->'cause'); END IF;
 ELSE RETURN NULL; END IF;
 INSERT INTO public.core_cqact(act_uuid,organism_id,board_uuid,thread_uuid,operation,actor_id,actor_epoch,received_at,request)
 VALUES(gen_random_uuid(),NEW.organism_id,NEW.board_uuid,NEW.thread_uuid,v_op,NEW.actor_id,NEW.actor_epoch,statement_timestamp(),v_args);
 RETURN NULL;
END $$;
CREATE TRIGGER cq_vote_followup AFTER INSERT ON public.core_cqact FOR EACH ROW
 WHEN (NEW.operation IN ('ANSWER','CONFLICT','SETTLE','PREPARED','PREPARATION_DUE','RESPONSE_DUE')) EXECUTE FUNCTION public.cq_vote_followup();
"""

# A recorded departure can complete turnout without a further ballot command.
# Its actual enterer may just have left the LO. Validate the immutable cause
# rather than borrowing current office or choosing an unrelated member.
FORWARD += r"""
CREATE FUNCTION public.cq_loss_source(p_lo bigint,p_board uuid,p_thread uuid,p_actor bigint,p_args jsonb)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE v_cause jsonb:=p_args->'cause'; v_open public.core_cqact; v_member bigint;
BEGIN
 SELECT * INTO v_open FROM public.core_cqact WHERE act_uuid::text=v_cause->>'ballot'
    AND organism_id=p_lo AND board_uuid=p_board AND thread_uuid=p_thread AND operation IN ('PROCEED','OPEN_VOTE','REOPEN_VOTE');
 IF v_open.act_uuid IS NULL THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 IF p_args ? 'ballot' AND p_args->>'ballot' IS DISTINCT FROM v_open.act_uuid::text
 OR (p_args ? 'result' AND NOT EXISTS(SELECT FROM public.core_cqact r WHERE r.act_uuid::text=p_args->>'result'
   AND r.operation='SETTLE' AND r.request->'cause'=v_cause AND r.execution_xid=pg_current_xact_id()))
 THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 IF v_cause->>'kind'='MEMBERSHIP' THEN
   SELECT m.identity_id INTO v_member FROM public.core_organismmembershiptransition t
    JOIN public.core_organismmembership m ON m.id=t.membership_id WHERE t.id::text=v_cause->>'id'
    AND m.living_organism_id=p_lo AND t.enterer_identity_id=p_actor AND t.prior_state='ACTIVE' AND t.resulting_state<>'ACTIVE'
    AND t.effective_at<=statement_timestamp() AND t.recorded_at>=v_open.recorded_at;
 ELSIF v_cause->>'kind'='CIRCLE_PLACE' THEN
   SELECT m.identity_id INTO v_member FROM public.core_circleparticipationend e
    JOIN public.core_circleparticipation cp ON cp.id=e.participation_id JOIN public.core_organismmembership m ON m.id=cp.membership_id
    JOIN public.core_discboard b ON b.circle_id=cp.circle_id AND b.organism_id=m.living_organism_id
    WHERE b.board_uuid=p_board AND e.id::text=v_cause->>'id' AND e.enterer_identity_id=p_actor
    AND e.effective_at<=statement_timestamp() AND e.recorded_at>=v_open.recorded_at;
 END IF;
 IF v_member IS NULL OR NOT EXISTS(SELECT FROM jsonb_array_elements(v_open.facts->'roster') x
   WHERE x->>'person'=v_member::text) THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 RETURN jsonb_build_object('kind','RULE','reference','HG:DISC2:D3/D5','cause',v_cause);
END $$;

CREATE FUNCTION public.cq_after_departure() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_lo bigint; v_circle bigint; v_member bigint; v_epoch bigint; v_kind text; v_vote record;
BEGIN
 IF NEW.effective_at>statement_timestamp() THEN RETURN NULL; END IF;
 IF TG_TABLE_NAME='core_organismmembershiptransition' THEN
   SELECT living_organism_id,identity_id INTO v_lo,v_member FROM public.core_organismmembership WHERE id=NEW.membership_id;
   v_kind:='MEMBERSHIP';
 ELSE
   SELECT m.living_organism_id,m.identity_id,cp.circle_id INTO v_lo,v_member,v_circle
    FROM public.core_circleparticipation cp JOIN public.core_organismmembership m ON m.id=cp.membership_id
    WHERE cp.id=NEW.participation_id;
   v_kind:='CIRCLE_PLACE';
 END IF;
 FOR v_vote IN SELECT o.act_uuid,o.thread_uuid,o.board_uuid FROM public.cq_phases h
   JOIN public.core_cqact o ON o.act_uuid=h.act_uuid JOIN public.core_discboard b ON b.board_uuid=o.board_uuid
   WHERE h.organism_id=v_lo AND h.phase='VOTING' AND h.deadline>statement_timestamp()
     AND o.recorded_at<=NEW.recorded_at AND (v_circle IS NULL OR v_circle=b.circle_id)
     AND EXISTS(SELECT FROM jsonb_array_elements(o.facts->'roster') x WHERE x->>'person'=v_member::text)
     AND public.cq_can_settle(o.act_uuid,statement_timestamp()) ORDER BY o.ordinal
 LOOP
   SELECT i.access_epoch INTO v_epoch FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
    WHERE i.id=NEW.enterer_identity_id AND i.access_state='active' AND u.is_active;
   IF v_epoch IS NULL THEN CONTINUE; END IF;
   INSERT INTO public.core_cqact(act_uuid,organism_id,board_uuid,thread_uuid,operation,actor_id,actor_epoch,received_at,request)
   VALUES(gen_random_uuid(),v_lo,v_vote.board_uuid,v_vote.thread_uuid,'SETTLE',NEW.enterer_identity_id,v_epoch,
     statement_timestamp(),jsonb_build_object('ballot',v_vote.act_uuid,'cause',
       jsonb_build_object('kind',v_kind,'id',NEW.id,'ballot',v_vote.act_uuid)));
 END LOOP;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER cq_membership_exit AFTER INSERT ON public.core_organismmembershiptransition
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.prior_state='ACTIVE' AND NEW.resulting_state<>'ACTIVE')
 EXECUTE FUNCTION public.cq_after_departure();
CREATE CONSTRAINT TRIGGER cq_place_exit AFTER INSERT ON public.core_circleparticipationend
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.cq_after_departure();
"""

REVERSE = r"""
DROP TRIGGER cq_membership_exit ON public.core_organismmembershiptransition;
DROP TRIGGER cq_place_exit ON public.core_circleparticipationend;
DROP FUNCTION public.cq_after_departure(),public.cq_loss_source(bigint,uuid,uuid,bigint,jsonb);
DROP TRIGGER cq_vote_followup ON public.core_cqact;
DROP FUNCTION public.cq_next_post(uuid),public.cq_open_source(bigint,uuid,uuid,bigint,bigint,jsonb),public.cq_vote_followup(),public.cq_ballot_facts(bigint,uuid,uuid,text,uuid,jsonb,bigint,timestamptz),
 public.cq_open_roster(jsonb,uuid,uuid,timestamptz),public.cq_can_settle(uuid,timestamptz),
 public.cq_ballot_entries(uuid,timestamptz),public.cq_count_decision(jsonb,integer,integer,integer,integer),
 public.cq_continuing(uuid,uuid,jsonb,timestamptz),public.cq_answer_eligible(uuid,uuid,text,bigint,timestamptz);
DROP INDEX public.cq_no_ballot_once,public.cq_one_first_vote,public.cq_one_retry,public.cq_one_answer,public.cq_one_result;
"""


def definitions():
    """Construct both definitions from the installed migration's exact source."""
    dispatch = """ ELSIF NEW.operation IN ('PROCEED','RULE_OPEN','OPEN_VOTE','ANSWER','SETTLE','REOPEN_VOTE')
   OR (NEW.operation='CONFLICT' AND EXISTS(SELECT FROM public.cq_phases h
       WHERE h.thread_uuid=NEW.thread_uuid AND h.phase IN ('VOTING','RESULT'))) THEN
   NEW.facts:=public.cq_ballot_facts(NEW.organism_id,NEW.board_uuid,NEW.thread_uuid,NEW.operation,
       NEW.act_uuid,NEW.request,NEW.actor_id,v_stamp);
   IF NEW.operation='RULE_OPEN' THEN
     NEW.operation:=CASE WHEN NEW.facts->>'phase'='RESULT' THEN 'NO_BALLOT' ELSE 'OPEN_VOTE' END;
   END IF;
   IF NEW.operation IN ('ANSWER','SETTLE') THEN
     NEW.facts:=NEW.facts||jsonb_build_object('commitment_nonce',gen_random_uuid());
   END IF;
 ELSIF NEW.operation='CLOSE' THEN
   NEW.facts:=public.cq_phase_facts(NEW.organism_id,NEW.board_uuid,NEW.thread_uuid,'CLOSE',NEW.request,NEW.actor_id,v_stamp)
     || jsonb_build_object('event_kind','DECISION','ballot',NULL,'post_number',public.cq_next_post(NEW.thread_uuid),
       'closed_at',v_stamp,'settled_at',v_stamp,'reporting_rule',public.cq_reporting_at(NEW.organism_id,v_stamp),
       'restart',false,'retry_requested',false,'count',jsonb_build_object('kind','NO_DECISION_COORDINATOR_CLOSED','cqt_applied',false));
 ELSE
   NEW.facts:=public.cq_phase_facts"""
    edits = (
        (
            clarity.FORWARD,
            "cq_input",
            "",
            (
                (
                    " v_source:=public.cq_source",
                    (
                        " IF NEW.operation='RULE_OPEN' THEN\n"
                        "   IF pg_trigger_depth()<2 OR NEW.received_at<>v_stamp THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;\n"
                        "   v_source:=public.cq_open_source(NEW.organism_id,NEW.board_uuid,NEW.thread_uuid,NEW.actor_id,NEW.actor_epoch,NEW.request);\n"
                        " ELSIF NEW.operation='NO_BALLOT' THEN PERFORM public.cq_refuse('CQ_COMPLETE');\n"
                        " ELSIF NEW.operation IN ('SETTLE','REOPEN_VOTE') AND NEW.request ? 'cause' THEN\n"
                        "   IF pg_trigger_depth()<2 THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;\n"
                        "   v_source:=public.cq_loss_source(NEW.organism_id,NEW.board_uuid,NEW.thread_uuid,NEW.actor_id,NEW.request);\n"
                        " ELSE\n v_source:=public.cq_source"
                    ),
                ),
                (" v_allowed:=CASE", " END IF;\n v_allowed:=CASE"),
                (
                    "WHEN 'QUESTION' THEN ARRAY['version']",
                    (
                        "WHEN 'RULE_OPEN' THEN ARRAY['trigger_act'] WHEN 'ANSWER' THEN ARRAY['choice','ballot'] "
                        "WHEN 'SETTLE' THEN ARRAY['ballot','cause'] WHEN 'REOPEN_VOTE' THEN ARRAY['result','cause'] WHEN 'QUESTION' THEN ARRAY['version']"
                    ),
                ),
                (" ELSIF NEW.operation='PROCEED' THEN PERFORM public.cq_refuse('CQ_NOT_READY');\n ELSE\n   NEW.facts:=public.cq_phase_facts", dispatch),
            ),
        ),
        (
            clarity.FORWARD,
            "cq_execute",
            "uuid,uuid,uuid,text,uuid,jsonb,bigint,bigint,timestamptz",
            (
                (
                    " SELECT * INTO v_old FROM",
                    """ IF p_op='ANSWER' THEN PERFORM public.cq_answer_eligible(p_board,p_thread,p_args->>'ballot',p_actor,statement_timestamp()); END IF;
 SELECT * INTO v_old FROM""",
                ),
            ),
        ),
        (
            clarity.FORWARD,
            "cq_complete",
            "",
            (
                (
                    " v_source:=public.cq_source",
                    (
                        " IF v_act.operation IN ('NO_BALLOT','OPEN_VOTE') AND v_act.request ? 'trigger_act' THEN\n"
                        "   v_source:=public.cq_open_source(v_act.organism_id,v_act.board_uuid,v_act.thread_uuid,v_act.actor_id,v_act.actor_epoch,v_act.request);\n"
                        " ELSIF v_act.operation IN ('SETTLE','REOPEN_VOTE') AND v_act.request ? 'cause' THEN\n"
                        "   v_source:=public.cq_loss_source(v_act.organism_id,v_act.board_uuid,v_act.thread_uuid,v_act.actor_id,v_act.request);\n"
                        " ELSE\n v_source:=public.cq_source"
                    ),
                ),
                (
                    " IF v_source IS DISTINCT FROM v_act.authority_source",
                    " END IF;\n IF v_source IS DISTINCT FROM v_act.authority_source",
                ),
            ),
        ),
        (
            clarity.FORWARD,
            "cq_source",
            "bigint,uuid,uuid,text,bigint,timestamptz",
            (
                (
                    "IF p_op='PREPARATION_DUE' THEN",
                    (
                        "IF p_op='RESPONSE_DUE' THEN\n"
                        "     IF v_state<>'ACTIVE' THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;\n"
                        "     RETURN jsonb_build_object('kind','RULE','reference','HG:DISC2:D7','route',p_op);\n"
                        "   END IF;\n   IF p_op IN ('SETTLE','REOPEN_VOTE') THEN\n"
                        "     IF v_state<>'ACTIVE' THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;\n"
                        "     RETURN jsonb_build_object('kind','RULE','reference','HG:DISC2:D4/D5/D12c','route',p_op);\n"
                        "   END IF;\n   IF p_op='PREPARATION_DUE' THEN"
                    ),
                ),
                (
                    "IF v_state='PROBATIONARY' THEN",
                    (
                        "IF v_state='PROBATIONARY' AND p_op='ANSWER' THEN "
                        "PERFORM public.cq_refuse('CQ_PROBATION_VOTE'); END IF;\n IF v_state='PROBATIONARY' THEN"
                    ),
                ),
                (
                    "'EXTEND','CLOSE','PROCEED')",
                    "'EXTEND','CLOSE','PROCEED','OPEN_VOTE')",
                ),
                (
                    "WHEN 'PROCEED' THEN 'CQ_FINISH_OFFICE'",
                    "WHEN 'PROCEED' THEN 'CQ_FINISH_OFFICE' WHEN 'OPEN_VOTE' THEN 'CQ_OPEN_OFFICE'",
                ),
            ),
        ),
        (
            clarity.FORWARD,
            "cq_phase_facts",
            "bigint,uuid,uuid,text,jsonb,bigint,timestamptz",
            (
                (
                    "h.phase IN ('CLOSED','WITHDRAWN')",
                    "h.phase IN ('VOTING','RESULT','CLOSED','WITHDRAWN')",
                ),
            ),
        ),
        (
            posts.FORWARD,
            "discp_input",
            "",
            (
                (
                    "SELECT count(*) INTO v_count FROM public.core_discpost p WHERE p.thread_uuid=NEW.thread_uuid;",
                    (
                        "SELECT count(*)+(SELECT count(*) FROM public.core_cqact a WHERE a.thread_uuid=NEW.thread_uuid "
                        "AND a.operation IN ('SETTLE','CLOSE','NO_BALLOT')) INTO v_count FROM public.core_discpost p WHERE p.thread_uuid=NEW.thread_uuid;"
                    ),
                ),
            ),
        ),
        (
            posts.FORWARD,
            "discp_execute",
            "jsonb,text,bigint,bigint,timestamptz",
            (
                (
                    "SELECT count(*) INTO v_count FROM public.core_discpost p WHERE p.thread_uuid=v_thread;",
                    (
                        "SELECT count(*)+(SELECT count(*) FROM public.core_cqact a WHERE a.thread_uuid=v_thread AND "
                        "a.operation IN ('SETTLE','CLOSE','NO_BALLOT')) INTO v_count FROM public.core_discpost p WHERE p.thread_uuid=v_thread;"
                    ),
                ),
            ),
        ),
    )
    for source, name, signature, changes in edits:
        start = source.index("CREATE FUNCTION public." + name + "(")
        end = source.index("END $$;", start) + len("END $$;")
        old = source[start:end].replace(
            "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1
        )
        new = old
        for target, replacement in changes:
            new = clarity.replace_once(new, target, replacement)
        yield f"public.{name}({signature})", old, new


def switch(editor, backwards):
    versions = list(definitions())
    with editor.connection.cursor() as cursor:
        for signature, old, new in versions:
            expected = new if backwards else old
            cursor.execute(
                "SELECT prosrc FROM pg_proc WHERE oid=%s::regprocedure", [signature]
            )
            if (
                cursor.fetchone()[0]
                != expected.split("AS $$", 1)[1].rsplit("$$;", 1)[0]
            ):
                raise RuntimeError(
                    f"DISC2 vote stopped: installed definition differs for {signature}."
                )
    for _, old, new in versions:
        editor.execute(old if backwards else new, params=None)


def install(apps, editor):
    editor.execute(operations_constraint(PRIOR_OPS + BALLOT_OPS) + FORWARD, params=None)
    switch(editor, False)


def reverse(apps, editor):
    editor.execute("LOCK TABLE public.core_cqact IN ACCESS EXCLUSIVE MODE", params=None)
    with editor.connection.cursor() as cursor:
        cursor.execute("""SELECT EXISTS(SELECT FROM public.core_cqact WHERE operation IN
            ('PROCEED','CLOSE','OPEN_VOTE','ANSWER','SETTLE','REOPEN_VOTE','RESPONSE_DUE','NO_BALLOT') OR (operation='CONFLICT' AND facts ? 'ballot'))""")
        if cursor.fetchone()[0]:
            raise RuntimeError(
                "DISC2 vote reversal refused while ballot history exists."
            )
    switch(editor, True)
    editor.execute(REVERSE + operations_constraint(PRIOR_OPS), params=None)


class Migration(migrations.Migration):
    atomic = True
    dependencies: ClassVar = [("core", "0074_cq_clarity")]
    operations: ClassVar = [migrations.RunPython(install, reverse)]
