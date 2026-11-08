"""ELECT P1 · one secret, constitution-bound election on the CQ route."""

from importlib import import_module

import django.db.models.deletion
from django.db import migrations, models

from . import _elect_ballot as ballot
from . import _elect_effects as effects

disc4 = import_module("core.migrations.0078_cq_circle_reporting")
disc3 = disc4.previous.authority
votes = disc3.ballots
clarity = votes.clarity
circle = import_module("core.migrations.0059_operating_circles")
root = import_module("core.migrations.0062_root_genesis")
sr = import_module("core.migrations.0052_sr_office_guard_widening")
one = disc3.one
function = disc3.function
PRIOR_OPS = votes.PRIOR_OPS + votes.BALLOT_OPS + ("IMPLEMENT",)
NOMINATIONS = ("ELECT_NOMINATE", "ELECT_CONSENT", "ELECT_WITHDRAW")


def name(sql):
    return sql.split("public.", 1)[1].split("(", 1)[0]


def cq_edits():
    """Each source is the final emitted definition in its named migration."""
    landed = {name(new): new for _, new in disc4.edits()}
    old = landed["cq3_plan"]
    new = one(old, "'CIRCLE_REPORTING') THEN", "'CIRCLE_REPORTING','ELECTION') THEN")
    yield old, one(new, " IF op='CIRCLE_REPORTING' THEN", """
 IF op='ELECTION' THEN RETURN public.elect_plan(p_lo,p_board,p_attachment,p_cut); END IF;
 IF op='CIRCLE_REPORTING' THEN""")

    old = function(disc3.ATTACHMENT, "cq3_attachment_facts")
    yield old, one(old, " IF NOT public.cq3_qualified", """
 IF att->>'kind'='ELECTION' THEN
    RETURN extra||public.elect_settlement(a,ready,att,p_cut);
 END IF;
 IF NOT public.cq3_qualified""")

    old = landed["cq3_apply"]
    yield old, one(old, " IF plan->>'domain'='CIRCLE_REPORTING'", """
 IF plan->>'domain'='ELECTION' THEN PERFORM public.elect_apply(NEW); RETURN NULL; END IF;
 IF plan->>'domain'='CIRCLE_REPORTING'""")
    old = landed["cq3_check_committed"]
    yield old, one(old, " IF plan->>'domain'='CIRCLE_REPORTING'", """
 IF plan->>'domain'='ELECTION' THEN
   IF NOT public.elect_authorises((a.request->>'result')::uuid,a.organism_id,(plan->'winner'->>'person')::uuid,
      plan->>'place',(plan->>'start')::timestamptz,(plan->>'end')::timestamptz)
     OR (plan->>'type'='SEAT' AND NOT EXISTS(SELECT FROM public.core_circleparticipation p
       JOIN public.core_circleadmissiondecision d ON d.id=p.decision_id
       WHERE d.decision_uuid=a.act_uuid AND d.constitutional_route='ELECTION' AND p.participation_uuid=a.act_uuid
       AND p.membership_id=(plan->'winner'->>'member')::bigint AND p.circle_id=(plan->>'circle')::bigint AND p.seat_id=plan->>'place'))
     OR (plan->>'type'='SEAT' AND plan->>'end' IS NOT NULL AND NOT EXISTS(
        SELECT FROM public.core_circleparticipationend x JOIN public.core_circleparticipation p ON p.id=x.participation_id
        WHERE p.participation_uuid=a.act_uuid AND x.composed_from_event_uuid=a.act_uuid AND x.effective_at=(plan->>'end')::timestamptz
        AND x.composing_rule_reference='elect-term:'||(a.request->>'result')))
     OR (plan->>'type'='OFFICE' AND NOT EXISTS(SELECT FROM public.core_contextualroleassignmentstateevent h
       WHERE h.event_uuid=a.act_uuid AND h.action='ASSIGN' AND public.elect_office_source(h.assignment_id,h.event_uuid,
       h.enterer_identity_id,h.authority_decision_reference,h.effective_at,h.effective_until)))
     THEN PERFORM public.cq_refuse('ELECT_RESULT'); END IF;
   RETURN;
 END IF;
 IF plan->>'domain'='CIRCLE_REPORTING'""")

    old = landed["cq_input"]
    new = one(old, "WHEN 'IMPLEMENT' THEN ARRAY['result']", """WHEN 'ELECT_NOMINATE' THEN ARRAY['person','assignment','statement']
   WHEN 'ELECT_CONSENT' THEN ARRAY['nomination'] WHEN 'ELECT_WITHDRAW' THEN ARRAY['nomination']
   WHEN 'IMPLEMENT' THEN ARRAY['result']""")
    new = one(new, " ELSIF NEW.operation='SETTINGS' THEN", """
 ELSIF NEW.operation IN ('ELECT_NOMINATE','ELECT_CONSENT','ELECT_WITHDRAW') THEN
   NEW.facts:=public.elect_nomination(NEW,v_stamp);
 ELSIF NEW.operation='SETTINGS' THEN""")
    new = one(new, " IF NEW.operation='INITIATE' AND NEW.request ? 'referral_result'", """
 IF NEW.operation='INITIATE' AND NEW.request->'attachment'->>'kind'='ELECTION' THEN
   IF public.disc_office(NEW.organism_id,(SELECT b.circle_id FROM public.core_discboard b WHERE b.board_uuid=NEW.board_uuid),
      NEW.actor_id,v_stamp) IS NULL THEN PERFORM public.cq_refuse('ELECT_CALL'); END IF;
 ELSIF public.elect_attachment(NEW.thread_uuid) IS NOT NULL THEN
   IF NEW.operation='ANSWER' THEN
     INSERT INTO public.core_electchoice(receipt,choice) VALUES(NEW.act_uuid,NEW.request->>'choice');
     NEW.request:=NEW.request-'choice';
   ELSIF NEW.operation IN ('NO_BALLOT','CLOSE') THEN
     NEW.facts:=NEW.facts||jsonb_build_object('event_kind','ELECTION','reporting_rule',public.elect_secret());
   ELSIF NEW.operation IN ('PREPARED','PREPARATION_DUE') THEN
     NEW.facts:=NEW.facts||jsonb_build_object('prepared_slate',public.elect_slate(NEW.thread_uuid,
       public.elect_plan(NEW.organism_id,NEW.board_uuid,public.elect_attachment(NEW.thread_uuid),v_stamp),v_stamp));
   END IF;
 END IF;
 IF NEW.operation='INITIATE' AND NEW.request ? 'referral_result'""")
    new = one(new, " v_allowed:=CASE", """ IF NEW.operation IN ('INITIATE','ELECT_NOMINATE') THEN
   v_source:=coalesce(public.elect_actor_basis(NEW,v_stamp),v_source);
   IF public.elect_actor_basis(NEW,NEW.received_at) IS DISTINCT FROM public.elect_actor_basis(NEW,v_stamp)
     THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 END IF;
 v_allowed:=CASE""")
    yield old, one(new, "AND NEW.facts->'count'->>'kind'='NO_DECISION_FEWER_THAN_THREE' THEN",
        "AND NEW.facts->>'event_kind' IS DISTINCT FROM 'ELECTION' AND NEW.facts->'count'->>'kind'='NO_DECISION_FEWER_THAN_THREE' THEN")

    old = next(new for _, new in disc4.previous.edits() if name(new)=="cq_complete")
    yield old, one(old, " IF v_source IS DISTINCT FROM v_act.authority_source", """ IF v_act.operation IN ('INITIATE','ELECT_NOMINATE') THEN
   v_source:=coalesce(public.elect_actor_basis(v_act,v_act.effective_at),v_source);
 END IF;
 IF v_act.operation='ANSWER' AND public.elect_attachment(v_act.thread_uuid) IS NOT NULL AND
      (v_act.request ? 'choice' OR NOT EXISTS(SELECT FROM public.core_electchoice ec WHERE ec.receipt=v_act.act_uuid))
    THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 IF v_source IS DISTINCT FROM v_act.authority_source""")

    old = function(votes.FORWARD, "cq_can_settle")
    yield old, one(old, " SELECT (o.facts", """ SELECT (o.facts->>'event_kind'='ELECTION' AND public.elect_withdrawn(o,least(p_cut,(o.facts->>'deadline')::timestamptz))) OR (o.facts""")
    old = function(votes.FORWARD, "cq_ballot_entries")
    yield old, one(old, "ans.request->>'choice'", """CASE WHEN o.facts->>'event_kind'='ELECTION' THEN
  CASE WHEN ans.act_uuid IS NOT NULL THEN 'SEALED' END ELSE ans.request->>'choice' END""")
    old = next(new for sig, _, new in votes.definitions() if sig.startswith("public.cq_execute("))
    new = one(old, "   IF v_old.thread_uuid IS DISTINCT FROM p_thread", """   IF v_old.operation='ANSWER' AND v_old.actor_id<>p_actor AND EXISTS(
      SELECT FROM public.core_electchoice ec WHERE ec.receipt=v_old.act_uuid) THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
   IF v_old.thread_uuid IS DISTINCT FROM p_thread""")
    yield old, one(new, "v_old.request IS DISTINCT FROM p_args", """(CASE WHEN p_op='ANSWER' AND EXISTS(
       SELECT FROM public.core_electchoice ec WHERE ec.receipt=v_old.act_uuid)
       THEN v_old.request||jsonb_build_object('choice',(SELECT ec.choice FROM public.core_electchoice ec WHERE ec.receipt=v_old.act_uuid))
       ELSE v_old.request END) IS DISTINCT FROM p_args""")

    old = landed["cq_ballot_facts"]
    new = one(old, " v_cut timestamptz;", """ att jsonb:=public.elect_attachment(p_thread); mandate jsonb; slate jsonb; totals jsonb; changes jsonb;
 v_cut timestamptz;""")
    new = one(new, "   v_rules:=v_rules||jsonb_build_object", """   IF att IS NOT NULL THEN
     mandate:=public.elect_plan(p_lo,p_board,att,p_cut);
     IF mandate ? 'reason' THEN PERFORM public.cq_refuse(mandate->>'code'); END IF;
     slate:=public.elect_slate(p_thread,mandate,p_cut);
     IF jsonb_array_length(slate)=0 THEN PERFORM public.cq_refuse('ELECT_EMPTY'); END IF;
     IF NOT public.elect_vacant(mandate,NULL,p_cut) THEN PERFORM public.cq_refuse('ELECT_OCCUPIED'); END IF;
     IF p_op='REOPEN_VOTE' AND (v_open.facts->'slate' IS DISTINCT FROM slate OR v_open.facts->'mandate' IS DISTINCT FROM mandate)
       THEN PERFORM public.cq_refuse('ELECT_CHANGED'); END IF;
     IF (v_ready.facts->>'clarity_stage')::boolean AND EXISTS(SELECT FROM public.core_cqact prep
       WHERE prep.thread_uuid=p_thread AND prep.operation IN ('PREPARED','PREPARATION_DUE') AND prep.facts->'prepared_slate' IS DISTINCT FROM slate)
       THEN PERFORM public.cq_refuse('ELECT_CHANGED'); END IF;
     changes:=(mandate->'counting')-'method';
     IF NOT EXISTS(SELECT FROM jsonb_array_elements(v_rules->'applied_provisions') x WHERE x->>'setting'='BASE') THEN
        v_rules:=v_rules||'{"threshold_base":"VOTES_CAST"}'::jsonb; END IF;
     IF NOT EXISTS(SELECT FROM jsonb_array_elements(v_rules->'applied_provisions') x WHERE x->>'setting'='CQT') THEN
        v_rules:=v_rules||'{"threshold":"MAJORITY","numerator":null,"denominator":null}'::jsonb; END IF;
     v_rules:=v_rules||changes;
     PERFORM public.elect_check_count(v_rules,changes,p_cut);
     v_report:=public.elect_secret();
   END IF;
   v_rules:=v_rules||jsonb_build_object""")
    new = one(new, "   RETURN (CASE WHEN p_op='PROCEED'", """   RETURN (CASE WHEN att IS NOT NULL THEN jsonb_build_object('mandate',mandate,'slate',slate) ELSE '{}'::jsonb END) ||
     (CASE WHEN p_op='PROCEED'""")
    new = one(new, "'phase','VOTING','event_kind','DECISION'", "'phase','VOTING','event_kind',(CASE WHEN att IS NULL THEN 'DECISION' ELSE 'ELECTION' END)")
    new = one(new, "   IF coalesce(p_args->>'choice'", """   IF att IS NOT NULL THEN
     IF public.elect_withdrawn(v_open,p_cut) THEN PERFORM public.cq_refuse('ELECT_CHANGED'); END IF;
     IF NOT coalesce((p_args->>'choice'='ABSTAIN' OR (jsonb_array_length(v_open.facts->'slate')=1
        AND p_args->>'choice' IN ('QUALIFIED','NOT_QUALIFIED')) OR (jsonb_array_length(v_open.facts->'slate')>1 AND
        EXISTS(SELECT FROM jsonb_array_elements(v_open.facts->'slate') s WHERE s->>'person'=p_args->>'choice'))),false)
       THEN PERFORM public.cq_refuse('ELECT_CHOICE'); END IF;
   ELSIF coalesce(p_args->>'choice'""")
    new = one(new, " v_count:=public.cq_count_decision(v_rules,v_eligible,v_yes,v_no,v_abstain);", """
 IF att IS NULL THEN v_count:=public.cq_count_decision(v_rules,v_eligible,v_yes,v_no,v_abstain);
 ELSE
   SELECT coalesce(jsonb_object_agg(v.choice_,v.n),'{}'::jsonb) INTO totals FROM (
     SELECT CASE WHEN secret.choice='QUALIFIED' THEN v_open.facts->'slate'->0->>'person' ELSE secret.choice END AS choice_,count(*) AS n
     FROM public.cq_ballot_entries(v_open.act_uuid,v_cut) e JOIN public.core_cqact a ON a.actor_id=e.person AND a.operation='ANSWER'
       AND a.request->>'ballot'=v_open.act_uuid::text JOIN public.core_electchoice secret ON secret.receipt=a.act_uuid
     WHERE e.choice IS NOT NULL GROUP BY 1) v;
   v_count:=public.elect_tally(v_rules,v_open.facts->'slate',totals,v_eligible);
   IF public.elect_withdrawn(v_open,v_cut) THEN v_count:=v_count||'{"kind":"NO_DECISION_SLATE_CHANGED","winner":null,"cqt_applied":false}'::jsonb; END IF;
   v_participation:=NULL;
 END IF;""")
    new = one(new, " v_pos:=public.cq_next_post", """ IF att IS NOT NULL AND v_retry THEN
   mandate:=public.elect_plan(p_lo,p_board,att,p_cut);
   v_retry:=mandate=v_open.facts->'mandate' AND public.elect_slate(p_thread,mandate,p_cut)=v_open.facts->'slate'
     AND public.elect_vacant(mandate,NULL,p_cut);
 END IF;
 v_pos:=public.cq_next_post""")
    yield old, one(new, "'ballot',v_open.act_uuid,'event_kind','DECISION'", "'ballot',v_open.act_uuid,'event_kind',(CASE WHEN att IS NULL THEN 'DECISION' ELSE 'ELECTION' END)")


def trigger_sql(sig, body):
    return "CREATE OR REPLACE FUNCTION " + sig + " RETURNS trigger LANGUAGE plpgsql AS $$" + body + "$$;"


def other_edits():
    # 0059 final SB-G triggers. Election branch binds every admission column to
    # the same-transaction implementation. Existing personal routes still need an identity.
    for sig, _, body, header in circle.BODIES:
        if sig == "public.sbg_guard_circleadmissiondecision()":
            branch = """
    IF NEW.constitutional_route='ELECTION' THEN
      IF NOT EXISTS(SELECT FROM public.core_cqact i JOIN public.core_circlejoinrequest q ON q.id=NEW.request_id
       JOIN public.core_organismmembership m ON m.id=q.membership_id JOIN public.core_identity p ON p.id=m.identity_id
       CROSS JOIN LATERAL (SELECT i.facts->'domain_plan' AS plan) dom
       WHERE i.operation='IMPLEMENT' AND i.act_uuid=NEW.decision_uuid AND i.organism_id=m.living_organism_id
       AND i.execution_xid=pg_current_xact_id() AND i.authority_source=public.cq3_implement_source(i)
       AND plan->>'type'='SEAT' AND q.circle_id=(plan->>'circle')::bigint AND q.request_uuid::text=plan->'winner'->>'consent'
       AND q.seat_id=NEW.seat_id AND q.seat_rule_uuid=NEW.seat_rule_uuid AND NEW.seat_rule_uuid::text=plan->>'seat_rule'
       AND NEW.settings_basis='rule:'||NEW.seat_rule_uuid||'@'||(plan->>'seat_version')||'#'||NEW.seat_id
       AND NEW.authority_decision_reference=public.disc_decision(i.authority_source,i.fingerprint)
       AND NEW.effective_at=(plan->>'start')::timestamptz AND NEW.received_at=i.received_at AND NEW.occurred_at=i.effective_at
       AND NEW.enterer_identity_id=i.actor_id AND NEW.deciding_identity_id IS NULL AND NOT NEW.self_decision
       AND NEW.outcome='ACCEPT' AND public.elect_authorises((i.request->>'result')::uuid,i.organism_id,
           p.identity_id,NEW.seat_id,NEW.effective_at,(plan->>'end')::timestamptz))
       THEN PERFORM public.cq_refuse('ELECT_RESULT'); END IF;
      RETURN NEW;
    END IF;
    IF NEW.deciding_identity_id IS NULL THEN PERFORM public.cq_refuse('ELECT_RESULT'); END IF;
"""
            yield trigger_sql(sig, body), trigger_sql(sig, one(body, "\nBEGIN\n", "\nBEGIN\n" + branch))
        if sig == "public.sbg_guard_circleparticipationend()":
            branch = """
    IF NEW.composing_rule_reference LIKE 'elect-term:%' THEN
      IF NOT EXISTS(SELECT FROM public.core_cqact i JOIN public.core_circleparticipation p ON p.participation_uuid=i.act_uuid
        JOIN public.core_circleadmissiondecision d ON d.id=p.decision_id AND d.constitutional_route='ELECTION'
        JOIN public.core_governedrule g ON g.rule_uuid=p.seat_rule_uuid AND g.id=NEW.composing_rule_id
        WHERE i.act_uuid=NEW.composed_from_event_uuid AND i.operation='IMPLEMENT' AND i.execution_xid=pg_current_xact_id()
        AND i.authority_source=public.cq3_implement_source(i) AND i.facts->'domain_plan'->>'type'='SEAT'
        AND NEW.participation_id=p.id AND NEW.effective_at=(i.facts->'domain_plan'->>'end')::timestamptz
        AND NEW.effective_at>p.effective_at AND NEW.occurred_at=i.effective_at AND NEW.received_at=i.received_at
        AND NEW.enterer_identity_id=i.actor_id AND NEW.composer_identity_id=i.actor_id AND NEW.actor_state='NONE'
        AND g.version::text=i.facts->'domain_plan'->>'seat_version' AND g.scope_organism_id=i.organism_id
        AND NEW.composing_rule_reference='elect-term:'||(i.request->>'result')) THEN PERFORM public.cq_refuse('ELECT_RESULT'); END IF;
      RETURN NEW;
    END IF;
"""
            yield trigger_sql(sig, body), trigger_sql(sig, one(body, "\nBEGIN\n", "\nBEGIN\n" + branch))
        if sig == "public.sbg_guard_circlejoinrequest()":
            # Permit an existing or recovering Board only for the nominee's
            # exact, just-recorded elected-seat consent. No global state loosening.
            marker = "          ) IS DISTINCT FROM 'ELIGIBLE' THEN"
            branch = """          ) IS DISTINCT FROM 'ELIGIBLE' AND NOT EXISTS(
             SELECT FROM public.core_cqact a JOIN public.core_identity i ON i.id=a.actor_id
             WHERE a.operation IN ('ELECT_CONSENT','ELECT_NOMINATE') AND a.act_uuid=NEW.request_uuid
               AND a.actor_id=NEW.enterer_identity_id AND a.facts->>'person'=i.identity_id::text
               AND NEW.consent_reference='elect-consent:'||a.act_uuid AND a.execution_xid=pg_current_xact_id()
               AND public.elect_plan(a.organism_id,a.board_uuid,public.elect_attachment(a.thread_uuid),a.effective_at)->>'seat_rule'=NEW.seat_rule_uuid::text
               AND public.elect_attachment(a.thread_uuid)->'spec'->>'place'=NEW.seat_id) THEN"""
            yield trigger_sql(sig, body), trigger_sql(sig, one(body, marker, branch))

    # Use 0074's emitted writer, including every earlier command extension.
    original, _ = clarity.posts.bodies()
    before = one(original, " SELECT string_agg", clarity.posts.WRITER_BRANCH + " SELECT string_agg")
    body = one(before, " SELECT string_agg", clarity.posts.WRITER_BRANCH.replace("RECORD_DISCUSSION_POST", "RECORD_CQ") + " SELECT string_agg")
    header = root.creation.HEADER.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION") + " AS $$"
    marker = " IF token='core_contextualroleassignment' AND action_name='ASSIGN' AND EXISTS("
    new = one(body, marker, """
 IF token='core_contextualroleassignment' AND action_name='ASSIGN'
    AND public.elect_office_source(anchor,command_event,actor,decision,at_time,deadline) THEN
    IF command_rule IS NOT NULL OR outcome<>'ACTIVE' OR evidence NOT LIKE 'elect-result:%' THEN PERFORM public.cq_refuse('ELECT_RESULT'); END IF;
 ELSIF token='core_contextualroleassignment' AND action_name='ASSIGN' AND EXISTS(""")
    new = one(new, " SELECT string_agg", """
 IF token='core_contextualroleassignment' AND action_name='ASSIGN' AND public.elect_office_source(anchor,command_event,actor,decision,at_time,deadline) THEN
   values_:=values_||'{"actor_state":"LIVING_ORGANISM","actor_id":null,"actor_access_epoch":null}'::jsonb;
 END IF;
 SELECT string_agg""")
    yield header + body + "$$;", header + new + "$$;"
    for sig, old in [
        ("public.s2b_guard_office_attribution()", next(new for sig, _, new in root.bodies() if sig=="public.s2b_guard_office_attribution()")),
        ("public.s2b_guard_assign_consent()", sr.NEW_S2B_GUARD_ASSIGN_CONSENT_BODY),
    ]:
        new = one(old, "BEGIN\n", """BEGIN
    IF NEW.action='ASSIGN' AND NEW.evidence_reference LIKE 'elect-result:%' THEN
      IF NEW.resulting_state IS DISTINCT FROM 'ACTIVE' OR NEW.prior_state IS DISTINCT FROM 'PROPOSED'
         OR NEW.actor_state IS DISTINCT FROM 'LIVING_ORGANISM' OR NEW.actor_id IS NOT NULL
         OR NEW.actor_access_epoch IS NOT NULL OR NEW.entry_mode IS DISTINCT FROM 'TRANSPORT'
         OR NEW.actor_capacity IS DISTINCT FROM 'STANDING'
         OR NOT public.elect_office_source(NEW.assignment_id,NEW.event_uuid,NEW.enterer_identity_id,
           NEW.authority_decision_reference,NEW.effective_at,NEW.effective_until)
         OR NOT EXISTS(SELECT FROM public.core_cqact i JOIN public.core_livingorganism lo ON lo.id=i.organism_id
           WHERE i.act_uuid=NEW.event_uuid AND i.received_at=NEW.received_at AND i.effective_at=NEW.occurred_at
             AND i.fingerprint=NEW.payload_fingerprint AND NEW.authority_basis_id=lo.founding_authority_basis_id
             AND NEW.evidence_reference='elect-result:'||(i.request->>'result'))
         THEN PERFORM public.cq_refuse('ELECT_RESULT'); END IF;
      RETURN NEW;
    END IF;
""")
        yield trigger_sql(sig, old), trigger_sql(sig, new)


def edits():
    yield from cq_edits()
    yield from other_edits()

SIGNATURES = {
    "cq3_plan": "bigint,uuid,bigint,jsonb,timestamptz",
    "cq3_attachment_facts": "public.core_cqact,timestamptz",
    "cq3_apply": "", "cq3_check_committed": "public.core_cqact", "cq_input": "",
    "cq_can_settle": "uuid,timestamptz", "cq_ballot_entries": "uuid,timestamptz",
    "cq_execute": "uuid,uuid,uuid,text,uuid,jsonb,bigint,bigint,timestamptz",
    "cq_ballot_facts": "bigint,uuid,uuid,text,uuid,jsonb,bigint,timestamptz",
    "cq_complete": "", "sbg_guard_circleparticipationend": "", "sbg_guard_circleadmissiondecision": "", "sbg_guard_circlejoinrequest": "",
    "s2b_guard_office_attribution": "", "s2b_guard_assign_consent": "",
}


def verify(editor, pairs, backwards):
    with editor.connection.cursor() as cur:
        for old, new in pairs:
            short = name(old)
            sig = root.creation.SIGNATURE if short == "s015_append_event" else f"public.{short}({SIGNATURES[short]})"
            expected = (new if backwards else old).split(" AS $$", 1)[1].rsplit("$$;", 1)[0]
            cur.execute("SELECT prosrc FROM pg_proc WHERE oid=to_regprocedure(%s)", [sig])
            if cur.fetchone() != (expected,):
                raise RuntimeError(f"ELECT stopped before replacing functions. Installed body differs for {sig}.")


def install(apps, editor):
    pairs = list(edits())
    verify(editor, pairs, False)
    editor.execute(ballot.SQL, params=None)
    editor.execute(effects.SQL, params=None)
    editor.execute(votes.operations_constraint(PRIOR_OPS + NOMINATIONS), params=None)
    for _, new in pairs:
        editor.execute(new, params=None)


def reverse(apps, editor):
    editor.execute("LOCK TABLE public.core_cqact, public.core_electchoice IN ACCESS EXCLUSIVE MODE", params=None)
    with editor.connection.cursor() as cur:
        cur.execute("""SELECT EXISTS(SELECT FROM public.core_cqact WHERE request->'attachment'->>'kind'='ELECTION'
                       OR operation LIKE 'ELECT_%') OR EXISTS(SELECT FROM public.core_electchoice)""")
        if cur.fetchone()[0]:
            raise RuntimeError("ELECT has election history. Preserve its history.")
    pairs = list(edits())
    verify(editor, pairs, True)
    for old, _ in reversed(pairs):
        editor.execute(old, params=None)
    editor.execute(votes.operations_constraint(PRIOR_OPS), params=None)
    editor.execute("""DROP TRIGGER elect_nomination_followup ON public.core_cqact;
        DROP TRIGGER elect_office_withdrawn ON public.core_officeappointmentconsent;
        DROP TABLE public.core_electchoice;
        DROP FUNCTION public.elect_actor_basis(public.core_cqact,timestamptz),public.elect_office_withdrawn(),public.elect_nomination_followup(), public.elect_apply(public.core_cqact),
        public.elect_office_source(bigint,uuid,bigint,text,timestamptz,timestamptz),
        public.elect_authorises(uuid,bigint,uuid,text,timestamptz,timestamptz),
        public.elect_settlement(public.core_cqact,public.core_cqact,jsonb,timestamptz),
        public.elect_vacant(jsonb,bigint,timestamptz),public.elect_withdrawn(public.core_cqact,timestamptz),
        public.elect_secret(),public.elect_check_count(jsonb,jsonb,timestamptz),
        public.elect_tally(jsonb,jsonb,jsonb,integer),public.elect_slate(uuid,jsonb,timestamptz),
        public.elect_nomination(public.core_cqact,timestamptz),public.elect_plan(bigint,uuid,jsonb,timestamptz),
        public.elect_attachment(uuid),public.elect_choice_guard()""", params=None)


OLD_SHAPE = next(op.constraint.condition for op in circle.Migration.operations
                 if isinstance(op, migrations.AddConstraint) and op.constraint.name == "sbg_circledecision_route_shape_ck")
ELECTED = models.Q(route="GOVERNANCE_CONSTITUTIONAL", constitutional_route="ELECTION",
                   deciding_party="body:LO_MEMBERSHIP", deciding_identity__isnull=True, outcome="ACCEPT")


class Migration(migrations.Migration):
    dependencies = (("core", "0078_cq_circle_reporting"),)
    operations = (
        migrations.RemoveConstraint(model_name="circleadmissiondecision", name="sbg_circledecision_route_shape_ck"),
        migrations.AlterField(model_name="circleadmissiondecision", name="deciding_identity",
                              field=models.ForeignKey(null=True, blank=True, related_name="+", on_delete=django.db.models.deletion.PROTECT, to="core.identity")),
        migrations.AddConstraint(model_name="circleadmissiondecision", constraint=models.CheckConstraint(
            name="sbg_circledecision_route_shape_ck", condition=(OLD_SHAPE & models.Q(deciding_identity__isnull=False)) | ELECTED)),
        migrations.AlterField(model_name="circleparticipationend", name="participation",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="+", to="core.circleparticipation")),
        migrations.AddConstraint(model_name="circleparticipationend", constraint=models.UniqueConstraint(
            fields=("participation",), condition=models.Q(composing_rule_reference__startswith="elect-term:"),
            name="elect_one_scheduled_end")),
        migrations.AddConstraint(model_name="circleparticipationend", constraint=models.UniqueConstraint(
            fields=("participation",), condition=~models.Q(composing_rule_reference__startswith="elect-term:"),
            name="elect_one_other_end")),
        migrations.RunPython(install, reverse),
    )
