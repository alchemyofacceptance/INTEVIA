"""Install scoped election mandates and the constitutional vacancy work queue."""

from importlib import import_module

from django.db import migrations, models

from . import _elect_transfer as transfers
from . import _elect_vacancy as vacancies

p1 = import_module("core.migrations.0079_elect_and_seat")
disc4 = p1.disc4
one, function = p1.one, p1.function
SIGNATURES = p1.SIGNATURES | {
    "cq4_holder": "bigint,timestamptz",
    "cq_source": "bigint,uuid,uuid,text,bigint,timestamptz",
    "cq_append": "",
    "cq_event_matches": "public.core_livingorganismevent",
    "elect_nomination": "public.core_cqact,timestamptz",
    "elect_plan": "bigint,uuid,jsonb,timestamptz",
    "elect_authorises": "uuid,bigint,uuid,text,timestamptz,timestamptz",
    "elect_actor_basis": "public.core_cqact,timestamptz",
    "elect_apply": "public.core_cqact",
}


def settings_at_open():
    plan = next(new for _, new in disc4.edits() if p1.name(new) == "cq4_plan")
    new = one(plan, "public.cq4_plan(", "public.elect_settings_plan(")
    new = one(new, "p_cut timestamptz)", "p_cut timestamptz,p_holder_cut timestamptz)")
    new = one(new, "p_holder_cut timestamptz)", "p_open jsonb)")
    return one(
        new,
        "SELECT * INTO v_holder FROM public.cq4_holder(p_lo,p_cut);",
        """SELECT
       (p_open->>'holder_board')::uuid AS holder_board,p_open->>'route' AS route,p_open->>'mandate' AS mandate
       INTO v_holder;""",
    )


def edits():
    older = {p1.name(new): new for _, new in disc4.previous.edits()}
    prior = {p1.name(new): new for _, new in disc4.edits()}
    latest = {p1.name(new): new for _, new in p1.edits()}
    old = function(disc4.previous.HELPERS, "cq4_holder")
    header, _ = old.split(" AS $$", 1)
    yield (
        old,
        header
        + """ AS $$
 SELECT b.board_uuid,(t.transfer->>'holder_circle')::bigint,
   CASE WHEN t.transfer->>'holder_circle' IS NULL THEN 'CQ_LO_MEMBERSHIP' ELSE 'CQ_DELEGATED_CIRCLE' END,
   coalesce(t.act_uuid::text,'HG:DISC4:Q1:DEFAULT')
 FROM public.core_livingorganism lo
 LEFT JOIN LATERAL public.elect_last_transfer(lo.id,'{"power":"VOTING_SETTINGS"}',p_cut) t ON true
 LEFT JOIN public.core_discboard b ON b.organism_id=lo.id
   AND b.circle_id IS NOT DISTINCT FROM (t.transfer->>'holder_circle')::bigint
 WHERE lo.id=p_lo
$$;""",
    )
    old = latest["cq3_plan"]
    yield (
        old,
        one(
            old,
            "   RETURN plan||jsonb_build_object('domain',op);",
            """
   IF spec->>'law_class'='POLICY' AND spec->>'operation'='ADOPT' AND NOT plan ? 'reason' THEN
     plan:=plan||public.elect_policy_transfer(p_lo,p_board,p_attachment,p_cut);
   END IF;
   RETURN plan||jsonb_build_object('domain',op);""",
        ),
    )
    old = latest["cq3_check_committed"]
    new = one(
        old,
        "check_plan IS DISTINCT FROM plan-'domain'",
        "check_plan IS DISTINCT FROM plan-ARRAY['domain','transfer']",
    )
    yield (
        old,
        one(
            new,
            "d.constitutional_route='ELECTION'",
            "d.constitutional_route=coalesce(plan->>'admission_route','ELECTION')",
        ),
    )
    old = latest["cq3_attachment_facts"]
    new = one(
        old,
        " IF a.operation<>'SETTLE'",
        """
 IF att->>'kind'='SETTINGS' AND a.operation IN ('OPEN_VOTE','REOPEN_VOTE','PROCEED') THEN
   plan:=public.cq4_plan(a.organism_id,a.board_uuid,att,p_cut);
   IF plan ? 'reason' THEN PERFORM public.cq_refuse(plan->>'code'); END IF;
   extra:=extra||jsonb_build_object('settings_mandate',plan-'patch');
 END IF;
 IF a.operation<>'SETTLE'""",
    )
    yield (
        old,
        one(
            new,
            " plan:=public.cq3_plan(a.organism_id,a.board_uuid,a.actor_id,att,p_cut);\n IF plan ? 'reason'",
            """
 IF att->>'kind'='SETTINGS' THEN
   plan:=public.elect_settings_plan(a.organism_id,a.board_uuid,att,p_cut,coalesce(ready.facts->'settings_mandate',
      jsonb_build_object('holder_board',ready.board_uuid,'route','CQ_LO_MEMBERSHIP','mandate','HG:DISC4:Q1:DEFAULT')));
   IF ready.facts ? 'settings_mandate' AND NOT plan ? 'reason'
      AND plan-'patch' IS DISTINCT FROM ready.facts->'settings_mandate' THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 ELSE plan:=public.cq3_plan(a.organism_id,a.board_uuid,a.actor_id,att,p_cut); END IF;
 IF plan ? 'reason'""",
        ),
    )
    for key, row in [("cq_append", "NEW"), ("cq_event_matches", "a")]:
        old = prior[key]
        marker = f"(CASE WHEN ({row}.operation='SETTINGS'"
        receipt = f"""(CASE WHEN {row}.operation='IMPLEMENT' AND {row}.facts->'domain_plan' ? 'transfer'
 THEN 'elect-transfer:'||({row}.request->>'result')||':'||coalesce({row}.facts->'domain_plan'->'transfer'->>'predecessor','DEFAULT')
 WHEN ({row}.operation='SETTINGS'"""
        yield old, one(old, marker, receipt)
    old = older["cq_source"]
    yield (
        old,
        one(
            old,
            " END IF;\n IF v_state IN ('SUSPENDED'",
            """
   IF p_op IN ('PREPARED','EXTEND','CLOSE','PROCEED','OPEN_VOTE') AND public.elect_attachment(p_thread) IS NOT NULL THEN
     v_office:=public.elect_admin_office(p_lo,p_board,public.elect_attachment(p_thread),p_person,p_cut);
   END IF;
 END IF;
 IF v_state IN ('SUSPENDED'""",
        ),
    )
    old = latest["cq_input"]
    marker = "public.disc_office(NEW.organism_id,(SELECT b.circle_id FROM public.core_discboard b WHERE b.board_uuid=NEW.board_uuid),\n      NEW.actor_id,v_stamp)"
    new = one(
        old,
        marker,
        "public.elect_admin_office(NEW.organism_id,NEW.board_uuid,NEW.request->'attachment',NEW.actor_id,v_stamp)",
    )
    new = one(
        new,
        " IF NEW.operation='SETTINGS' AND NEW.request ? 'result' THEN",
        """
 IF NEW.operation='ELECT_VACANCY' THEN
   SELECT jsonb_build_object('kind','RULE','route','ELECT_VACANCY','reference',v.ending) INTO v_source
   FROM public.core_electvacancy v WHERE v.ending::text=NEW.request->>'ending' AND v.route_act=NEW.act_uuid
    AND v.organism_id=NEW.organism_id AND v.enterer=NEW.actor_id AND NEW.received_at=v.recorded_at;
   IF pg_trigger_depth()<2 OR v_source IS NULL THEN PERFORM public.cq_refuse('ELECT_VACANCY_SOURCE'); END IF;
 ELSIF NEW.operation='SETTINGS' AND NEW.request ? 'result' THEN""",
    )
    new = one(
        new,
        "WHEN 'IMPLEMENT' THEN ARRAY['result']",
        "WHEN 'ELECT_VACANCY' THEN ARRAY['ending'] WHEN 'IMPLEMENT' THEN ARRAY['result']",
    )
    new = one(
        new,
        " IF NEW.operation='IMPLEMENT' THEN\n   SELECT jsonb_build_object",
        """
 IF NEW.operation='ELECT_VACANCY' THEN
   SELECT jsonb_build_object('instruction',v.instruction,'kind',v.kind,'place',v.place,'vacant_from',v.effective_at) INTO STRICT NEW.facts
   FROM public.core_electvacancy v WHERE v.route_act=NEW.act_uuid;
 ELSIF NEW.operation='IMPLEMENT' THEN
   SELECT jsonb_build_object""",
    )
    new = one(
        new,
        "IF NEW.operation='ANSWER' THEN\n     INSERT INTO public.core_electchoice",
        """IF NEW.operation='ANSWER' AND NOT public.elect_board_confirmation(public.elect_attachment(NEW.thread_uuid)) THEN
     INSERT INTO public.core_electchoice""",
    )
    new = one(
        new,
        "ELSIF NEW.operation IN ('NO_BALLOT','CLOSE') THEN",
        """ELSIF NEW.operation IN ('NO_BALLOT','CLOSE')
      AND NOT public.elect_board_confirmation(public.elect_attachment(NEW.thread_uuid)) THEN""",
    )
    yield (
        old,
        one(
            new,
            " ELSIF public.elect_attachment(NEW.thread_uuid) IS NOT NULL THEN",
            """
   IF NEW.request->'attachment'->'spec' ? 'vacancy' AND EXISTS(SELECT FROM public.core_cqact i
       WHERE i.organism_id=NEW.organism_id AND i.operation='INITIATE'
       AND i.request->'attachment'->'spec'->>'vacancy'=NEW.request->'attachment'->'spec'->>'vacancy') THEN
      PERFORM public.cq_refuse('ELECT_VACANCY_USED'); END IF;
 ELSIF public.elect_attachment(NEW.thread_uuid) IS NOT NULL THEN""",
        ),
    )
    old = latest["cq_complete"]
    new = one(
        old,
        "IF v_act.operation='ANSWER' AND public.elect_attachment(v_act.thread_uuid) IS NOT NULL AND",
        "IF v_act.operation='ANSWER' AND public.elect_attachment(v_act.thread_uuid) IS NOT NULL AND NOT public.elect_board_confirmation(public.elect_attachment(v_act.thread_uuid)) AND",
    )
    yield (
        old,
        one(
            new,
            " IF v_act.operation='SETTINGS' AND v_act.request ? 'result' THEN",
            """
 IF v_act.operation='ELECT_VACANCY' THEN
   IF NOT EXISTS(SELECT FROM public.core_electvacancy v WHERE v.route_act=v_act.act_uuid AND v.enterer=v_act.actor_id
      AND v.organism_id=v_act.organism_id AND v.ending::text=v_act.request->>'ending'
      AND v_act.facts->'instruction'=v.instruction AND v_act.authority_source=jsonb_build_object('kind','RULE','route','ELECT_VACANCY','reference',v.ending))
      THEN PERFORM public.cq_refuse('ELECT_VACANCY_SOURCE'); END IF;
   RETURN NULL;
 END IF;
 IF v_act.operation='SETTINGS' AND v_act.request ? 'result' THEN""",
        ),
    )
    old = function(p1.effects.SQL, "elect_actor_basis")
    new = one(
        old,
        " IF p_act.operation='INITIATE' THEN",
        " IF p_act.operation='INITIATE' THEN\n    att:=p_act.request->'attachment';",
    )
    yield (
        old,
        one(
            new,
            """ SELECT public.disc_office(p_act.organism_id,b.circle_id,p_act.actor_id,p_cut) INTO office_
   FROM public.core_discboard b WHERE b.board_uuid=p_act.board_uuid AND b.organism_id=p_act.organism_id;""",
            " office_:=public.elect_admin_office(p_act.organism_id,p_act.board_uuid,att,p_act.actor_id,p_cut);",
        ),
    )
    old = function(p1.ballot.SQL, "elect_nomination")
    yield (
        old,
        one(
            old,
            """public.disc_office(p_act.organism_id,(SELECT circle_id FROM public.core_discboard WHERE board_uuid=p_act.board_uuid),
        p_act.actor_id,p_cut)""",
            "public.elect_admin_office(p_act.organism_id,p_act.board_uuid,att,p_act.actor_id,p_cut)",
        ),
    )
    old = latest["cq_ballot_facts"]
    new = one(
        old,
        "     changes:=(mandate->'counting')-'method';",
        """     IF public.elect_board_confirmation(att) THEN
       IF jsonb_array_length(slate)<>1 THEN PERFORM public.cq_refuse('ELECT_VACANCY_NOMINEE'); END IF;
     ELSE
     changes:=(mandate->'counting')-'method';""",
    )
    new = one(new, "     v_report:=public.elect_secret();", "     v_report:=public.elect_secret(); END IF;")
    # A collective appointment uses its Board's ordinary CQ participation, reporting and count.
    new = new.replace(
        "CASE WHEN att IS NULL THEN 'DECISION' ELSE 'ELECTION' END",
        "CASE WHEN att IS NULL OR public.elect_board_confirmation(att) THEN 'DECISION' ELSE 'ELECTION' END",
    )
    new = one(
        new,
        " IF att IS NULL THEN v_count:=public.cq_count_decision",
        """ IF public.elect_board_confirmation(att) THEN
   v_count:=public.cq_count_decision(v_rules,v_eligible,v_yes,v_no,v_abstain);
   v_count:=v_count||jsonb_build_object('winner',CASE WHEN v_count->>'kind'='QUALIFIED' THEN v_open.facts->'slate'->0->>'person' END);
   IF public.elect_withdrawn(v_open,v_cut) THEN v_count:=v_count||'{"kind":"NO_DECISION_SLATE_CHANGED","winner":null,"cqt_applied":false}'::jsonb; END IF;
 ELSIF att IS NULL THEN v_count:=public.cq_count_decision""",
    )
    yield (
        old,
        one(
            new,
            "jsonb_build_object('mandate',mandate,'slate',slate)",
            """jsonb_build_object('mandate',mandate,'slate',slate,
       'administrator_mandate',public.elect_admin_mandate(p_lo,att,p_cut))""",
        ),
    )
    old = function(p1.ballot.SQL, "elect_plan")
    new = one(
        old,
        "role_ public.core_organismroledefinition;",
        "vacancy_ jsonb; role_ public.core_organismroledefinition;",
    )
    new = one(
        new,
        "OR spec-ARRAY['organism','binding','version','provision','place']",
        "OR spec-ARRAY['organism','binding','version','provision','place','vacancy']",
    )
    new = one(
        new,
        " IF gov.id IS NULL",
        """
 IF NOT spec ? 'vacancy' AND EXISTS(SELECT FROM public.elect_pending_vacancy(p_lo,rule->>'type',spec->>'place',p_cut) v
    WHERE v.ending IS NOT NULL) THEN
    RETURN jsonb_build_object('code','ELECT_VACANCY_LINK','reason','This place has a recorded vacancy. Start from its filling instruction.'); END IF;
 IF spec ? 'vacancy' THEN
   vacancy_:=public.elect_call_route(p_lo,spec,rule,p_cut);
   IF vacancy_ ? 'reason' THEN RETURN vacancy_; END IF;
 END IF;
 IF gov.id IS NULL""",
    )
    new = one(
        new,
        "AND board_circle IS NOT NULL)",
        "AND board_circle IS NOT NULL AND vacancy_->>'route' IS DISTINCT FROM 'COOPTION')",
    )
    new = one(
        new,
        "s->>'admission_route'='ELECTION' AND s->>'deciding_body'='body:LO_MEMBERSHIP'",
        """
 s->>'admission_route'=CASE WHEN vacancy_->>'route'='COOPTION' THEN 'COOPTION' ELSE 'ELECTION' END
 AND s->>'deciding_body'=('body:'||(rule->>'body'))""",
    )
    yield (
        old,
        one(
            new,
            " RETURN spec-'version'||extra",
            """ IF vacancy_->>'route'='COOPTION' AND rule->>'type'='SEAT' THEN
    extra:=extra||'{"admission_route":"COOPTION"}'::jsonb; END IF;
 RETURN spec-'version'||extra""",
        ),
    )
    old = function(p1.effects.SQL, "elect_authorises")
    new = one(
        old,
        "r.facts->>'event_kind'='ELECTION'",
        """(r.facts->>'event_kind'='ELECTION' OR
     (r.facts->>'event_kind'='DECISION' AND public.elect_board_confirmation(public.elect_attachment(r.thread_uuid))
      AND jsonb_array_length(o.facts->'slate')=1))""",
    )
    yield (
        old,
        one(
            new, "AND o.facts->>'event_kind'='ELECTION'", "AND o.facts->>'event_kind'=r.facts->>'event_kind'"
        ),
    )
    old = latest["cq_can_settle"]
    yield (
        old,
        one(
            old,
            "o.facts->>'event_kind'='ELECTION' AND public.elect_withdrawn",
            """(o.facts->>'event_kind'='ELECTION' OR
      public.elect_board_confirmation(public.elect_attachment(o.thread_uuid))) AND public.elect_withdrawn""",
        ),
    )
    old = function(p1.effects.SQL, "elect_apply")
    yield (
        old,
        one(
            old,
            "'GOVERNANCE_CONSTITUTIONAL','ELECTION','body:LO_MEMBERSHIP'",
            """'GOVERNANCE_CONSTITUTIONAL',
    coalesce(plan->>'admission_route','ELECTION'),'body:'||(plan->>'body')""",
        ),
    )
    old = latest["sbg_guard_circleadmissiondecision"]
    new = one(
        old,
        "IF NEW.constitutional_route='ELECTION' THEN",
        """IF NEW.constitutional_route='ELECTION' OR
      (NEW.constitutional_route='COOPTION' AND NEW.deciding_identity_id IS NULL) THEN""",
    )
    yield (
        old,
        one(
            new,
            "AND plan->>'type'='SEAT' AND q.circle_id",
            """AND plan->>'type'='SEAT' AND NEW.constitutional_route=coalesce(plan->>'admission_route','ELECTION')
       AND NEW.deciding_party='body:'||(plan->>'body') AND q.circle_id""",
        ),
    )
    old = latest["sbg_guard_circleparticipationend"]
    yield (
        old,
        one(
            old,
            "d.constitutional_route='ELECTION'",
            "d.constitutional_route=coalesce(i.facts->'domain_plan'->>'admission_route','ELECTION')",
        ),
    )


def check(editor, pairs, backwards):
    with editor.connection.cursor() as cur:
        for old, new in pairs:
            key = p1.name(old)
            signature = f"public.{key}({SIGNATURES[key]})"
            expected = (new if backwards else old).split(" AS $$", 1)[1].rsplit("$$;", 1)[0]
            cur.execute("SELECT prosrc FROM pg_proc WHERE oid=to_regprocedure(%s)", [signature])
            if cur.fetchone() != (expected,):
                raise RuntimeError(
                    f"ELECT stopped before replacement. Installed body differs for {signature}."
                )


def owner_shape(editor, extended):
    # The original owner-scope constraint has an automatically assigned name.
    with editor.connection.cursor() as cur:
        cur.execute("""SELECT c.conname FROM pg_constraint c WHERE c.conrelid='public.core_cqact'::regclass AND c.contype='c'
          AND (SELECT array_agg(a.attname::text ORDER BY a.attname) FROM unnest(c.conkey) k
              JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k)=ARRAY['board_uuid','operation','thread_uuid']""")
        rows = cur.fetchall()
        if len(rows) != 1:
            raise RuntimeError("Cannot identify the CQ owner-scope constraint.")
        name = editor.quote_name(rows[0][0])
        allowed = "operation IN ('SETTINGS','ELECT_VACANCY')" if extended else "operation='SETTINGS'"
        editor.execute(
            f"ALTER TABLE public.core_cqact DROP CONSTRAINT {name}, ADD CONSTRAINT {name} "
            f"CHECK ((thread_uuid IS NULL AND board_uuid IS NULL)=({allowed}))",
            params=None,
        )
    previous = p1.PRIOR_OPS + p1.NOMINATIONS
    editor.execute(
        p1.votes.operations_constraint(previous + (("ELECT_VACANCY",) if extended else ())), params=None
    )


def install(apps, editor):
    pairs = list(edits())
    check(editor, pairs, False)
    owner_shape(editor, True)
    for sql in [transfers.SQL, vacancies.SQL, settings_at_open(), *(new for _, new in pairs)]:
        editor.execute(sql, params=None)


def reverse(apps, editor):
    editor.execute(
        "LOCK TABLE public.core_cqact, public.core_electvacancy IN ACCESS EXCLUSIVE MODE", params=None
    )
    with editor.connection.cursor() as cur:
        cur.execute("""SELECT EXISTS(SELECT FROM public.elect_transfers) OR EXISTS(SELECT FROM public.core_electvacancy)
           OR EXISTS(SELECT FROM public.core_cqact WHERE facts ?| ARRAY['administrator_mandate','settings_mandate']
             OR request->'attachment'->'spec' ? 'vacancy')""")
        if cur.fetchone()[0]:
            raise RuntimeError("ELECT has mandate or vacancy history. Preserve its history.")
    pairs = list(edits())
    check(editor, pairs, True)
    for old, _ in reversed(pairs):
        editor.execute(old, params=None)
    owner_shape(editor, False)
    editor.execute(vacancies.REVERSE, params=None)
    editor.execute(
        """DROP FUNCTION public.elect_settings_plan(bigint,uuid,jsonb,timestamptz,jsonb),
        public.elect_admin_office(bigint,uuid,jsonb,bigint,timestamptz),public.elect_admin_mandate(bigint,jsonb,timestamptz),
        public.elect_policy_transfer(bigint,uuid,jsonb,timestamptz),public.elect_run_scope(jsonb),
        public.elect_last_transfer(bigint,jsonb,timestamptz);
        DROP INDEX public.elect_transfer_history; DROP VIEW public.elect_transfers; DROP FUNCTION public.elect_document(text);""",
        params=None,
    )


COOPTED = models.Q(
    route="GOVERNANCE_CONSTITUTIONAL",
    constitutional_route="COOPTION",
    deciding_party="body:GOVERNANCE",
    deciding_identity__isnull=True,
    outcome="ACCEPT",
)


class Migration(migrations.Migration):
    dependencies = (("core", "0079_elect_and_seat"),)
    operations = (
        migrations.RemoveConstraint(
            model_name="circleadmissiondecision", name="sbg_circledecision_route_shape_ck"
        ),
        migrations.AddConstraint(
            model_name="circleadmissiondecision",
            constraint=models.CheckConstraint(
                name="sbg_circledecision_route_shape_ck",
                condition=(p1.OLD_SHAPE & models.Q(deciding_identity__isnull=False)) | p1.ELECTED | COOPTED,
            ),
        ),
        migrations.RunPython(install, reverse),
    )
