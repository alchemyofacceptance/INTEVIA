"""DISC4 P1. A qualified decision can enact one frozen settings patch."""

from importlib import import_module

from django.db import migrations

authority = import_module("core.migrations.0076_cq_authority")
clarity = authority.ballots.clarity
one = authority.one
function = authority.function

HELPERS = r"""
-- This is the one effective-holder resolver. A subsequent recorded transfer
-- can change its answer as of a cut without rewriting any earlier decision.
CREATE FUNCTION public.cq4_holder(p_lo bigint,p_cut timestamptz)
RETURNS TABLE(holder_board uuid,holder_circle bigint,route text,mandate text) LANGUAGE sql STABLE AS $$
 SELECT b.board_uuid,b.circle_id,'CQ_LO_MEMBERSHIP'::text,'HG:DISC4:Q1:DEFAULT'::text
 FROM public.core_livingorganism lo LEFT JOIN public.core_discboard b ON b.organism_id=lo.id AND b.circle_id IS NULL
 WHERE lo.id=p_lo
$$;

CREATE FUNCTION public.cq4_eligible(p_lo bigint,p_cut timestamptz)
RETURNS integer LANGUAGE sql STABLE AS $$
 SELECT count(*)::integer FROM public.core_organismmembership m CROSS JOIN public.cq4_holder(p_lo,p_cut) h
 WHERE m.living_organism_id=p_lo AND public.disc_member_state(p_lo,m.identity_id,p_cut)='ACTIVE'
 AND public.disc_audience(p_lo,h.holder_circle,m.identity_id,'MEMBERS',p_cut)
$$;

-- A matter-specific shortfall is proved by its original closed motion and
-- its continuing eligible membership. Missing answers do not remove members.
CREATE FUNCTION public.cq4_small_result(p_lo bigint,p_result text,p_actor bigint,p_patch jsonb,p_cut timestamptz)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE outcome public.core_cqact; attachment jsonb; amount integer; holder uuid;
BEGIN
 SELECT * INTO outcome FROM public.core_cqact r WHERE r.act_uuid::text=p_result AND r.organism_id=p_lo
   AND r.operation IN ('SETTLE','NO_BALLOT','CLOSE') AND r.recorded_at<=p_cut;
 IF outcome.act_uuid IS NULL OR NOT coalesce(public.cq3_result_visible(outcome.act_uuid,p_lo,p_actor,p_cut),false)
   THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 SELECT h.holder_board INTO holder FROM public.cq4_holder(p_lo,p_cut) h;
 SELECT i.request->'attachment' INTO attachment FROM public.core_cqact i
   WHERE i.thread_uuid=outcome.thread_uuid AND i.operation='INITIATE';
 IF outcome.facts->'count'->>'kind' IS DISTINCT FROM 'NO_DECISION_FEWER_THAN_THREE'
    OR outcome.facts->'referral'->>'destination' IS DISTINCT FROM 'LO_COORDINATOR'
    OR outcome.board_uuid IS DISTINCT FROM holder OR attachment->>'kind' IS DISTINCT FROM 'SETTINGS'
    OR (p_patch IS NOT NULL AND p_patch IS DISTINCT FROM (attachment->'spec')-'organism')
    OR EXISTS(SELECT FROM public.core_cqact c WHERE c.thread_uuid=outcome.thread_uuid AND c.actor_id=p_actor
       AND c.operation='CONFLICT' AND c.effective_at<=p_cut AND c.recorded_at<=p_cut)
    THEN PERFORM public.cq_refuse('CQ_SETTINGS_REFERRAL'); END IF;
 amount:=jsonb_array_length(public.cq_roster(holder,outcome.thread_uuid,p_cut));
 IF amount>=3 THEN PERFORM public.cq_refuse('CQ_SETTINGS_VOTE'); END IF;
 RETURN jsonb_build_object('basis','HG:DISC4:Q1b','eligible_count',amount,'below_three_result',outcome.act_uuid,
    'reason','Fewer than three eligible members can decide the settings by vote.');
END $$;

CREATE FUNCTION public.cq4_personal_basis(p_lo bigint,p_args jsonb,p_actor bigint,p_cut timestamptz)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE v_count integer;
BEGIN
 IF (public.cq_settings_at(p_lo,p_cut)->>'enabled')::boolean THEN
   IF p_args ? 'below_three_result' THEN
     RETURN public.cq4_small_result(p_lo,p_args->>'below_three_result',p_actor,p_args-'below_three_result',p_cut);
   END IF;
   v_count:=public.cq4_eligible(p_lo,p_cut);
   IF v_count>=3 THEN PERFORM public.cq_refuse('CQ_SETTINGS_VOTE'); END IF;
   RETURN jsonb_build_object('basis','HG:DISC4:Q1b','eligible_count',v_count,
     'reason','Fewer than three eligible members can decide the settings by vote.');
 END IF;
 IF p_args ? 'below_three_result' THEN PERFORM public.cq_refuse('CQ_SETTINGS_REFERRAL'); END IF;
 IF (SELECT a.authority_source->>'kind'='CQ_RESULT' FROM public.core_cqact a
     WHERE a.organism_id=p_lo AND a.operation='SETTINGS'
       AND a.request->'changes'->'enabled'='false'::jsonb AND a.facts->'previous'->'enabled'='true'::jsonb
       AND a.effective_at<=p_cut AND a.recorded_at<=p_cut ORDER BY a.ordinal DESC LIMIT 1) THEN
   IF p_args->'changes' IS DISTINCT FROM '{"enabled":true}'::jsonb THEN
     PERFORM public.cq_refuse('CQ_SETTINGS_RESTART'); END IF;
   RETURN '{"basis":"HG:DISC4:Q2:REENABLE"}'::jsonb;
 END IF;
 RETURN '{"basis":"HG:DISC4:Q2:CONFIGURE"}'::jsonb;
END $$;

-- Preflight after cq_source has admitted the caller, before the service
-- diagnoses the patch. The SQL writer repeats the checks under the owner lock.
CREATE FUNCTION public.cq4_direct_admit(p_lo bigint,p_key uuid,p_actor bigint,p_epoch bigint,p_restart boolean,p_origin text,p_cut timestamptz)
RETURNS void LANGUAGE plpgsql STABLE AS $$
DECLARE prior public.core_cqact;
BEGIN
 SELECT * INTO prior FROM public.core_cqact a WHERE a.act_uuid=p_key;
 IF FOUND THEN
   IF prior.organism_id<>p_lo OR prior.board_uuid IS NOT NULL THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
   IF prior.actor_id<>p_actor OR prior.actor_epoch<>p_epoch OR prior.operation<>'SETTINGS' OR prior.request ? 'result'
      THEN PERFORM public.cq_refuse('CQ_REQUEST'); END IF;
   RETURN; -- cq_execute compares the original request on an authorised replay.
 END IF;
 IF EXISTS(SELECT FROM public.core_livingorganismevent e WHERE e.event_uuid=p_key)
    THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 IF p_origin IS NOT NULL THEN
    PERFORM public.cq4_small_result(p_lo,p_origin,p_actor,NULL,p_cut);
    RETURN;
 END IF;
 PERFORM public.cq4_personal_basis(p_lo,jsonb_build_object('changes',
    CASE WHEN p_restart THEN '{"enabled":true}'::jsonb ELSE '{}'::jsonb END),p_actor,p_cut);
END $$;

-- A settings patch may extend the coverage of the same provision. Only
-- the LAW route may retire a live constitutional constraint.
CREATE FUNCTION public.cq4_checked(p_lo bigint,p_key uuid,p_patch jsonb,p_cut timestamptz)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE checked jsonb; owner_uuid uuid;
BEGIN
 checked:=public.cq_checked_settings(p_lo,p_key,p_patch,p_cut);
 IF (p_patch->'changes') ? 'provisions' THEN
   SELECT lo.organism_id INTO STRICT owner_uuid FROM public.core_livingorganism lo WHERE lo.id=p_lo;
   IF EXISTS(SELECT FROM jsonb_array_elements(checked->'previous'->'provisions') old_law
       WHERE public.cq_law_current(owner_uuid,old_law,p_cut)
       AND NOT EXISTS(SELECT FROM jsonb_array_elements(checked->'settings'->'provisions') new_law
         WHERE new_law-ARRAY['decision','body']=old_law-ARRAY['decision','body']
           AND new_law->>'decision' IN (old_law->>'decision','ALL_DECISIONS')
           AND new_law->>'body' IN (old_law->>'body','ALL_BOARDS'))) THEN
     PERFORM public.cq_refuse('CQ_SETTINGS_PROVISION'); END IF;
 END IF;
 RETURN checked;
END $$;

-- An ordinary refusal enters the disposition only for a once-valid target.
-- Internal errors must abort settlement, never turn into a governance result.
CREATE FUNCTION public.cq4_plan(p_lo bigint,p_board uuid,p_att jsonb,p_cut timestamptz)
RETURNS jsonb LANGUAGE plpgsql VOLATILE AS $$
DECLARE v_patch jsonb:=(p_att->'spec')-'organism'; v_holder record; v_reason text; v_code text;
BEGIN
 IF p_att->'spec'->>'organism' IS DISTINCT FROM
    (SELECT lo.organism_id::text FROM public.core_livingorganism lo WHERE lo.id=p_lo)
 THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 SELECT * INTO v_holder FROM public.cq4_holder(p_lo,p_cut);
 IF v_holder.holder_board IS NULL OR p_board IS DISTINCT FROM v_holder.holder_board THEN
   RETURN jsonb_build_object('code','CQ_BODY','reason','This board cannot decide this act. Put it forward in the body that owns it.');
 END IF;
 BEGIN
   IF (p_att->>'text')::jsonb IS DISTINCT FROM p_att->'spec' THEN PERFORM public.cq_refuse('CQ_SETTINGS_WORDING'); END IF;
 EXCEPTION WHEN invalid_text_representation THEN PERFORM public.cq_refuse('CQ_SETTINGS_WORDING'); END;
 IF NOT(v_patch ?& ARRAY['predecessor','changes']) OR v_patch-ARRAY['predecessor','changes','reporting_effective_from']<>'{}'::jsonb
 OR jsonb_typeof(v_patch->'predecessor') NOT IN ('string','null')
 OR nullif(btrim(p_att->>'text'),'') IS NULL THEN PERFORM public.cq_refuse('CQ_INPUT'); END IF;
 IF (public.cq_settings_at(p_lo,p_cut)->>'enabled')::boolean IS NOT TRUE THEN
   RETURN jsonb_build_object('code','CQ_DISABLED','reason','Voting was switched off. Review the current settings before putting this change forward again.');
 END IF;
 BEGIN
   PERFORM public.cq4_checked(p_lo,NULL,v_patch,p_cut);
 EXCEPTION WHEN check_violation THEN
   GET STACKED DIAGNOSTICS v_code=MESSAGE_TEXT;
   v_reason:=CASE v_code
     WHEN 'CQ_SETTINGS_STALE' THEN 'The settings version changed. Put forward a proposal against the current version.'
     WHEN 'CQ_SETTINGS_UNCHANGED' THEN 'These settings are already in force.'
     WHEN 'CQ_SUBSCRIBER' THEN 'This new selection needs current subscriber access.'
     WHEN 'CQ_SETTINGS_PROVISION' THEN 'A cited provision is still in force. Keep it in these settings or change the law first.'
     WHEN 'CQ_LAW' THEN 'The cited provision does not support these settings at this time.'
     WHEN 'CQ_REPORT_DATE' THEN 'The proposed reporting date can no longer take effect as specified.'
     WHEN 'CQ_SETTINGS_INPUT' THEN 'Review the proposed settings values.' END;
   IF v_reason IS NULL THEN RAISE; END IF;
   RETURN jsonb_build_object('reason',v_reason,'code',v_code);
 END;
 RETURN jsonb_build_object('domain','SETTINGS','route',v_holder.route,'holder_board',v_holder.holder_board,
    'mandate',v_holder.mandate,'patch',v_patch);
END $$;

-- Neither the result UUID nor trigger nesting on its own grants authority.
CREATE FUNCTION public.cq4_voted_source(p_act public.core_cqact)
RETURNS jsonb LANGUAGE sql STABLE AS $$
 SELECT i.authority_source FROM public.core_cqact i
 WHERE i.operation='IMPLEMENT' AND i.execution_xid=pg_current_xact_id()
 AND i.organism_id=p_act.organism_id AND i.actor_id=p_act.actor_id AND i.actor_epoch=p_act.actor_epoch
 AND i.effective_at=p_act.received_at AND i.request->'result'=p_act.request->'result'
 AND i.facts->'domain_plan'->>'domain'='SETTINGS'
 AND i.facts->'domain_plan'->'patch'=p_act.request-'result'
 AND public.cq3_implement_source(i)=i.authority_source
$$;
CREATE UNIQUE INDEX cq4_one_settings_version ON public.core_cqact ((request->>'result'))
 WHERE operation='SETTINGS' AND request ? 'result';
CREATE FUNCTION public.cq4_decision(p_version uuid,p_source jsonb)
RETURNS uuid LANGUAGE sql IMMUTABLE AS $$
 SELECT CASE WHEN p_source->>'kind'='CQ_RESULT' THEN (p_source->>'reference')::uuid ELSE p_version END
$$;
"""

VOTED_INPUT = """ IF NEW.operation='SETTINGS' AND NEW.request ? 'result' THEN
   v_source:=public.cq4_voted_source(NEW);
   IF pg_trigger_depth()<2 OR NEW.received_at<>v_stamp OR v_source IS NULL
     THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 ELSIF NEW.operation='IMPLEMENT' THEN
   v_source:="""

VERSION_FACTS = """   IF NEW.request ? 'result' THEN
     NEW.facts:=public.cq4_checked(NEW.organism_id,NEW.act_uuid,NEW.request-'result',v_stamp)
       ||jsonb_build_object('decision',NEW.request->'result','basis','CQ_RESULT');
   ELSE
     NEW.facts:=public.cq4_personal_basis(NEW.organism_id,NEW.request,NEW.actor_id,v_stamp);
     IF public.cq4_personal_basis(NEW.organism_id,NEW.request,NEW.actor_id,NEW.received_at) IS DISTINCT FROM NEW.facts
       THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
     NEW.facts:=public.cq4_checked(NEW.organism_id,NEW.act_uuid,NEW.request,v_stamp)||NEW.facts;
   END IF;"""

APPLY = """ IF plan->>'domain'='SETTINGS' THEN
   INSERT INTO public.core_cqact(act_uuid,organism_id,operation,actor_id,actor_epoch,received_at,request)
   VALUES(gen_random_uuid(),NEW.organism_id,'SETTINGS',NEW.actor_id,NEW.actor_epoch,cut,
     (plan->'patch')||jsonb_build_object('result',NEW.request->'result'));
   RETURN NULL;
 END IF;
 SELECT e.id INTO STRICT event_pk"""

COMPLETE = """ IF v_act.operation='SETTINGS' AND v_act.request ? 'result' THEN
   IF public.cq4_voted_source(v_act) IS DISTINCT FROM v_act.authority_source THEN
     PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
   RETURN NULL;
 END IF;
 IF v_act.operation='IMPLEMENT' THEN"""

CHECK_EFFECT = """ IF plan->>'domain'='SETTINGS' THEN
   IF NOT EXISTS(SELECT FROM public.core_cqact s WHERE s.organism_id=a.organism_id AND s.operation='SETTINGS'
      AND s.execution_xid=a.execution_xid AND s.request=((plan->'patch')||jsonb_build_object('result',a.request->'result'))
      AND s.authority_source=a.authority_source AND s.facts->'decision'=a.request->'result'
      AND public.cq4_voted_source(s)=a.authority_source)
     THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
   RETURN;
 END IF;
 IF plan->>'domain'='LAW' THEN"""


def edits():
    old = function(authority.ATTACHMENT, "cq3_plan")
    new = one(
        old, "'RELEASE_POLICY','GI') THEN", "'RELEASE_POLICY','GI','SETTINGS') THEN"
    )
    yield (
        old,
        one(
            new,
            " IF op='GI' THEN",
            " IF op='SETTINGS' THEN RETURN public.cq4_plan(p_lo,p_board,p_attachment,p_cut); END IF;\n IF op='GI' THEN",
        ),
    )
    old = function(authority.referrals.SQL, "cq3_referral")
    yield (
        old,
        one(
            old,
            " RETURN jsonb_build_object('origin_result'",
            """ IF att->>'kind'='SETTINGS' AND EXISTS(
     SELECT FROM public.cq4_holder(a.organism_id,p_cut) h
     WHERE h.holder_board=a.board_uuid AND jsonb_array_length(public.cq_roster(h.holder_board,a.thread_uuid,p_cut))<3
       AND EXISTS(SELECT FROM public.core_organismmembership m WHERE m.living_organism_id=a.organism_id
         AND public.disc_audience(a.organism_id,h.holder_circle,m.identity_id,'MEMBERS',p_cut)
         AND public.disc_member_state(a.organism_id,m.identity_id,p_cut)='ACTIVE'
         AND NOT excluded @> jsonb_build_array(m.identity_id)
         AND public.disc_office(a.organism_id,h.holder_circle,m.identity_id,p_cut) IS NOT NULL)) THEN
   dest:='LO_COORDINATOR'; dest_key:=origin_key;
 END IF;
 RETURN jsonb_build_object('origin_result'""",
        ),
    )
    writer = function(authority.acts.FORWARD, "l_execute")
    projection = writer[
        writer.index(" IF kind_='RETIRE' OR") : writer.index(" RETURN event_pk;")
    ]
    old = function(one(authority.ATTACHMENT, "__APPLY_LAW__", projection), "cq3_apply")
    yield old, one(old, " SELECT e.id INTO STRICT event_pk", APPLY)
    old = function(authority.COMPLETE, "cq3_check_committed")
    yield old, one(old, " IF plan->>'domain'='LAW' THEN", CHECK_EFFECT)

    for _, old in authority.edits():
        if old.startswith("CREATE OR REPLACE FUNCTION public.cq_source("):
            yield (
                old,
                one(
                    old,
                    "v_office:=public.disc_office(p_lo,NULL,p_person,p_cut);",
                    "SELECT public.disc_office(p_lo,h.holder_circle,p_person,p_cut) INTO v_office "
                    "FROM public.cq4_holder(p_lo,p_cut) h;",
                ),
            )
        if old.startswith("CREATE OR REPLACE FUNCTION public.cq_input()"):
            new = one(
                old, " IF NEW.operation='IMPLEMENT' THEN\n   v_source:=", VOTED_INPUT
            )
            new = one(
                new,
                "ARRAY['predecessor','changes','reporting_effective_from']",
                "ARRAY['predecessor','changes','reporting_effective_from','result','below_three_result']",
            )
            yield (
                old,
                one(
                    new,
                    "   NEW.facts:=public.cq_checked_settings(NEW.organism_id,NEW.act_uuid,NEW.request,v_stamp);",
                    VERSION_FACTS,
                ),
            )
        if old.startswith("CREATE OR REPLACE FUNCTION public.cq_complete()"):
            yield old, one(old, " IF v_act.operation='IMPLEMENT' THEN", COMPLETE)
    # Keep previous event references byte-for-byte for personal and other CQ acts.
    for name, row in (("cq_append", "NEW"), ("cq_event_matches", "a")):
        old = function(clarity.FORWARD, name)
        # The shared chain requires the s015d1 digest. The plain citation belongs
        # in evidence_reference. Every event still has its own event_uuid.
        term = f"(CASE WHEN {row}.operation='SETTINGS' THEN 'cq-settings-decision:' ELSE 'cq-act:' END)||{row}.act_uuid"
        cited = f"(CASE WHEN {row}.operation='SETTINGS' AND {row}.authority_source->>'kind'='CQ_RESULT' THEN 'cq-settings-result:'||({row}.authority_source->>'reference') ELSE {term} END)"
        yield old, one(old, term, cited)


def reporting_view():
    start = clarity.FORWARD.index("CREATE VIEW public.cq_reporting_rules")
    return clarity.FORWARD[start : clarity.FORWARD.index(";", start) + 1].replace(
        "CREATE VIEW", "CREATE OR REPLACE VIEW", 1
    )


def install(apps, editor):
    editor.execute(HELPERS, params=None)
    for _, new in edits():
        editor.execute(new, params=None)
    editor.execute(
        one(
            reporting_view(),
            "e.event_uuid AS decision",
            "public.cq4_decision(a.act_uuid,a.authority_source) AS decision",
        ),
        params=None,
    )


def reverse(apps, editor):
    editor.execute("LOCK TABLE public.core_cqact IN ACCESS EXCLUSIVE MODE")
    with editor.connection.cursor() as cursor:
        cursor.execute("""SELECT EXISTS(SELECT FROM public.core_cqact WHERE request->'attachment'->>'kind'='SETTINGS'
          OR (operation='SETTINGS' AND (request ? 'result' OR facts->>'basis' LIKE 'HG:DISC4:%')))""")
        if cursor.fetchone()[0]:
            raise RuntimeError(
                "DISC4 settings authority has been recorded. Preserve its history."
            )
    editor.execute(reporting_view(), params=None)
    for old, _ in reversed(list(edits())):
        editor.execute(old, params=None)
    editor.execute(
        """DROP INDEX public.cq4_one_settings_version;
      DROP FUNCTION public.cq4_decision(uuid,jsonb),public.cq4_voted_source(public.core_cqact),
        public.cq4_direct_admit(bigint,uuid,bigint,bigint,boolean,text,timestamptz),
        public.cq4_plan(bigint,uuid,jsonb,timestamptz),public.cq4_checked(bigint,uuid,jsonb,timestamptz),public.cq4_personal_basis(bigint,jsonb,bigint,timestamptz),
        public.cq4_small_result(bigint,text,bigint,jsonb,timestamptz),
        public.cq4_eligible(bigint,timestamptz),public.cq4_holder(bigint,timestamptz)""",
        params=None,
    )


class Migration(migrations.Migration):
    dependencies = (("core", "0076_cq_authority"),)
    operations = (migrations.RunPython(install, reverse),)
