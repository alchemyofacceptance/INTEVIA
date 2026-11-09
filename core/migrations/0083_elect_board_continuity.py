"""Recognise an external appointment or record the holder's Board resignation."""

from importlib import import_module

from django.db import migrations, models

from . import _elect_continuity as continuity

terms = import_module("core.migrations.0082_elect_term_continuity")
elect, p1, hard = terms.elect, terms.p1, terms.hard
one = terms.one
OPS = ("RECOGNITION_CONSENT", "RECOGNISE", "BOARD_RESIGN")
OP_LIST = "('RECOGNITION_CONSENT','RECOGNISE','BOARD_RESIGN')"
departure = import_module("core.migrations.0054_c1b_relationships")
NEW = {
    "elect_office_support_ending": "bigint,timestamptz",
    "elect_pending_successor": "uuid,bigint,text,timestamptz,timestamptz",
    "elect_cancel_pending_office": "bigint,uuid",
    "elect_continuity_clause": "bigint,jsonb,text,timestamptz",
    "elect_recogniser": "public.core_cqact,jsonb,jsonb,timestamptz",
    "elect_recognition_facts": "public.core_cqact,timestamptz",
    "elect_resignation_facts": "public.core_cqact,timestamptz",
    "elect_recognition_office": "bigint,uuid,bigint,text,timestamptz,timestamptz",
    "elect_notice_enterer": "bigint,bigint,bigint,timestamptz",
    "elect_resignation_coverage": "bigint,uuid,bigint",
    "elect_continuity_effect": "",
}


def edits():
    prior = {p1.name(new): new for _, new in p1.edits()}
    prior.update({p1.name(new): new for _, new in elect.edits()})
    prior.update({p1.name(new): new for _, _, new in hard.edits()})
    prior.update({p1.name(new): new for _, _, new in terms.edits()})

    # 0082 defined the original deadline; using the later knowledge cut to
    # select the supporting place lost its identity when its term shortened.
    old = terms.function(terms.terms.SQL, "elect_office_deadline")
    yield old, one(old, hard.body(old), """
 SELECT least(e.effective_until, (
     SELECT greatest(e.effective_at,x.effective_at)
     FROM public.elect_office_support_ending(e.id,p_known) x))
 FROM public.core_contextualroleassignmentstateevent e
 WHERE e.id=p_assign AND e.action='ASSIGN' AND greatest(e.received_at,e.recorded_at)<=p_known
""")

    # Every insertion, including CQ advance, the deferred trigger and a
    # same-transaction fill, crosses this 0080 guard before the immutable row.
    old = terms.function(elect.vacancies.SQL, "elect_vacancy_guard")
    new = one(old, "DECLARE s public.elect_vacancy_sources;", "DECLARE notice_ uuid; assessment_ uuid; s public.elect_vacancy_sources;")
    marker = " NEW.instruction:=public.elect_vacancy_instruction(s); RETURN NEW;"
    yield old, one(new, marker, """
 IF s.kind='SEAT' THEN
   SELECT first_end.composed_from_event_uuid INTO notice_
   FROM public.core_circleparticipation p CROSS JOIN LATERAL (
     SELECT e.composed_from_event_uuid,e.composing_rule_reference
     FROM public.core_circleparticipationend e WHERE e.participation_id=p.id
     ORDER BY e.effective_at,e.recorded_at,e.id LIMIT 1
   ) first_end
   WHERE p.participation_uuid=s.occupancy
     AND first_end.composing_rule_reference='elect-resign:'||first_end.composed_from_event_uuid;
 END IF;
 IF notice_ IS NOT NULL THEN
   PERFORM public.elect_resignation_coverage(s.organism_id,notice_,NEW.enterer);
   SELECT x.event_uuid INTO STRICT assessment_ FROM public.core_coverageassessment x
   JOIN public.core_essentialcoveragecase c ON c.id=x.case_id
   WHERE c.living_organism_id=s.organism_id AND x.effective_at=s.effective_at
     AND x.evidence_reference='elect-resignation:'||notice_;
 END IF;
 NEW.instruction:=public.elect_vacancy_instruction(s);
 IF notice_ IS NOT NULL THEN
   NEW.instruction:=jsonb_set(NEW.instruction,'{assessment}',to_jsonb(assessment_));
 END IF;
 RETURN NEW;""")

    old = p1.trigger_sql("public.sbg_guard_membership_end_completeness()", departure.NEW)
    # Current places still need the usual U3 effect. A reserved successor has
    # a zero-length term, with the same departure as the causal event.
    future = "public.elect_pending_successor(p.participation_uuid,NEW.membership_id,'SEAT',NEW.effective_at,NEW.received_at)"
    new = one(old, "p.effective_at > NEW.effective_at\n                OR", f"(p.effective_at > NEW.effective_at AND NOT {future})\n                OR")
    marker = "AND e.effective_at <= NEW.effective_at"
    if new.count(marker) != 2:
        raise RuntimeError("Expected the two seat-ending checks from 0054.")
    new = new.replace(marker, f"""AND (e.effective_at <= NEW.effective_at OR ({future}
                       AND e.effective_at=p.effective_at AND e.composed_from_event_uuid=NEW.event_uuid))""")
    yield old, one(new, "h.effective_at>NEW.effective_at))", """h.effective_at>NEW.effective_at)
                     AND NOT (h.resulting_state='ENDED' AND h.composing_rule_reference IS NOT DISTINCT FROM 'ELECT:SUCCESSOR_LEFT'
                       AND h.evidence_reference LIKE 'elect-departure:'||NEW.event_uuid||':%'))""")

    # An elected seat cancelled at its start has an empty interval. Neither
    # the seat conflict nor the one-place-per-person check may count it.
    old = prior["sbg_guard_circleparticipation_occupancy"]
    interval = terms.SEAT_OVERLAP
    if old.count(interval) != 2:
        raise RuntimeError("Expected both occupancy checks from 0082.")
    yield old, old.replace(interval, interval +
        "           AND public.elect_place_end(p.id,v_known_at) IS DISTINCT FROM p.effective_at\n")

    old = prior["elect_already_replaced"]
    marker = " SELECT CASE WHEN s.kind='SEAT' THEN EXISTS("
    new = one(old, marker, """ SELECT CASE
 WHEN s.kind='OFFICE' AND EXISTS(SELECT FROM public.core_contextualroleassignmentstateevent e WHERE e.event_uuid=s.ending
   AND e.composing_rule_reference='ELECT:SUCCESSOR_LEFT' AND e.recorded_at<=p_cut) THEN true
 WHEN s.kind='SEAT' AND EXISTS(SELECT FROM public.core_circleparticipation p
   JOIN public.core_circleparticipationend e ON e.participation_id=p.id
   JOIN public.core_organismmembershiptransition t ON t.event_uuid=e.composed_from_event_uuid
   WHERE p.participation_uuid=s.ending AND t.resulting_state='ENDED' AND e.effective_at=p.effective_at
   AND greatest(e.received_at,e.recorded_at)<=p_cut
   AND public.elect_pending_successor(p.participation_uuid,t.membership_id,'SEAT',t.effective_at,t.received_at)) THEN true
 WHEN s.kind='SEAT' THEN EXISTS(""")
    yield old, one(new, "OR public.circle_place_at(p.circle_id,p.membership_id,p.effective_at,p_cut)",
                   "OR (public.elect_term_includes(p.effective_at,p.effective_at,public.elect_place_end(p.id,p_cut)) "
                   "AND public.circle_place_at(p.circle_id,p.membership_id,p.effective_at,p_cut))")

    old = prior["cq_source"]
    marker = " IF p_op='SETTINGS' THEN"
    yield old, one(old, marker, f"""
 IF p_op IN {OP_LIST} THEN
   IF p_board IS NOT NULL OR p_thread IS NOT NULL OR (v_state<>'ACTIVE'
     AND (p_op<>'BOARD_RESIGN' OR v_state NOT IN ('SUSPENDED','RESTRICTED')))
     THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
   RETURN jsonb_build_object('kind','MEMBER','reference',p_person,'route',p_op);
 END IF;
""" + marker)

    old = prior["cq_input"]
    marker = "WHEN 'ELECT_VACANCY' THEN ARRAY['ending']"
    new = one(old, marker, """WHEN 'RECOGNITION_CONSENT' THEN ARRAY['claim']
    WHEN 'RECOGNISE' THEN ARRAY['claim','consent','result']
    WHEN 'BOARD_RESIGN' THEN ARRAY['participation','binding','version','provision','notice'] """ + marker)
    marker = " IF NEW.operation='ELECT_VACANCY' THEN\n   SELECT jsonb_build_object('instruction'"
    yield old, one(new, marker, f""" IF NEW.operation IN {OP_LIST} THEN
   IF NEW.operation='BOARD_RESIGN' THEN NEW.facts:=public.elect_resignation_facts(NEW,v_stamp);
   ELSE
     NEW.facts:=public.elect_recognition_facts(NEW,v_stamp);
     IF (public.elect_recognition_facts(NEW,NEW.received_at)-'start') IS DISTINCT FROM (NEW.facts-'start')
       THEN PERFORM public.cq_refuse('ELECT_RECOGNISER'); END IF;
   END IF;
   v_source:=NEW.facts->'source';
 ELSIF NEW.operation='ELECT_VACANCY' THEN
   SELECT jsonb_build_object('instruction'""")

    old = prior["cq_execute"]
    marker = "   IF v_old.operation='ANSWER'"
    yield old, one(old, marker, f"""   IF p_op IN {OP_LIST} THEN
     IF v_old.actor_id IS DISTINCT FROM p_actor THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
     IF v_old.operation<>p_op OR v_old.request IS DISTINCT FROM p_args OR v_old.actor_epoch<>p_epoch
       THEN PERFORM public.cq_refuse('ELECT_CONT_REQUEST'); END IF;
   END IF;
""" + marker)

    old = prior["cq_complete"]
    marker = " IF v_act.operation='ELECT_VACANCY' THEN"
    yield old, one(old, marker, f""" IF v_act.operation IN {OP_LIST} THEN
   IF v_act.authority_source IS DISTINCT FROM v_act.facts->'source'
      OR (v_act.operation='RECOGNISE' AND v_act.facts->>'type'='SEAT' AND NOT EXISTS(
        SELECT FROM public.core_circleparticipation p JOIN public.core_circleadmissiondecision d ON d.id=p.decision_id
        WHERE p.participation_uuid=v_act.act_uuid AND d.decision_uuid=v_act.act_uuid AND d.constitutional_route='RECOGNITION'
        AND (v_act.facts->>'end' IS NULL OR EXISTS(SELECT FROM public.core_circleparticipationend x WHERE x.participation_id=p.id
          AND x.composed_from_event_uuid=v_act.act_uuid AND x.effective_at=(v_act.facts->>'end')::timestamptz))))
      OR (v_act.operation='RECOGNISE' AND v_act.facts->>'type'='OFFICE' AND NOT EXISTS(
        SELECT FROM public.core_contextualroleassignmentstateevent e WHERE e.event_uuid=v_act.act_uuid
        AND e.assignment_id=(v_act.facts->>'assignment_id')::bigint AND e.effective_at=v_act.effective_at
        AND e.evidence_reference='elect-recognised:'||v_act.act_uuid))
      OR (v_act.operation='BOARD_RESIGN' AND v_act.facts->>'end' IS NOT NULL AND NOT EXISTS(
        SELECT FROM public.core_circleparticipationend x WHERE x.participation_id=(v_act.facts->>'participation_id')::bigint
        AND x.composed_from_event_uuid=v_act.act_uuid AND x.effective_at=(v_act.facts->>'end')::timestamptz))
   THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
   RETURN NULL;
 END IF;
""" + marker)

    old = terms.function(terms.terms.SQL, "elect_record_term_end")
    marker = "IF public.disc_member_state(p_lo,p_enterer,cut_) IS DISTINCT FROM 'ACTIVE' OR NOT EXISTS("
    yield old, one(old, marker, """IF (public.disc_member_state(p_lo,p_enterer,cut_) IS DISTINCT FROM 'ACTIVE'
    AND NOT public.elect_notice_enterer(p_lo,p_id,p_enterer,cut_)) OR NOT EXISTS(""")

    for name, guard in (
        ("sbg_guard_circlejoinrequest", continuity.REQUEST_GUARD),
        ("sbg_guard_circleadmissiondecision", continuity.DECISION_GUARD),
        ("sbg_guard_circleparticipationend", continuity.END_GUARD),
        ("s2b_guard_office_attribution", continuity.OFFICE_GUARD),
        ("s2b_guard_assign_consent", continuity.OFFICE_GUARD),
    ):
        old = prior[name]
        if name == "sbg_guard_circleparticipationend":
            marker = "v_transition.effective_at IS DISTINCT FROM NEW.effective_at"
            new = one(old, marker, """(v_transition.effective_at IS DISTINCT FROM NEW.effective_at
          AND NOT (NEW.effective_at=v_participation.effective_at AND public.elect_pending_successor(
             v_participation.participation_uuid,v_transition.membership_id,'SEAT',v_transition.effective_at,v_transition.received_at)))""")
            yield old, one(new, "\nBEGIN\n", "\nBEGIN\n" + guard)
        elif name == "s2b_guard_office_attribution":
            # Keep the original lock and overlap check ahead of the new attribution branch.
            new = one(old, "AND i.operation='IMPLEMENT' AND i.facts->'domain_plan' ? 'incumbent')",
                      "AND (i.operation='RECOGNISE' OR (i.operation='IMPLEMENT' AND i.facts->'domain_plan' ? 'incumbent')))")
            # An empty reservation cannot overlap even an appointment whose
            # proposed start falls before the cancelled handover.
            new = one(new, "AND other.id<>a.id", "AND other.id<>a.id AND (s.ends IS NULL OR s.ends>s.starts)")
            marker = "public.disc_member_state(d.living_organism_id,i.id,NEW.received_at)='ACTIVE'"
            new = one(new, marker, "(" + marker + """ OR
             public.elect_notice_enterer(d.living_organism_id,NEW.assignment_id,i.id,NEW.received_at))""")
            marker = "    IF NEW.action='ASSIGN' AND NEW.evidence_reference LIKE 'elect-result:%' THEN"
            yield old, one(new, marker, continuity.CANCEL_OFFICE_GUARD + guard + marker)
        else:
            yield old, one(old, "\nBEGIN\n", "\nBEGIN\n" + guard)

    old = terms.function(elect.vacancies.SQL, "elect_vacancy_instruction")
    marker = "x.evidence_reference='office:'||s.ending OR EXISTS("
    yield old, one(old, marker, """x.evidence_reference='office:'||s.ending OR EXISTS(
      SELECT FROM public.core_cqact n JOIN public.core_circleparticipation p ON p.id=(n.facts->>'participation_id')::bigint
      WHERE n.organism_id=s.organism_id AND n.operation='BOARD_RESIGN' AND x.evidence_reference='elect-resignation:'||n.act_uuid
      AND p.membership_id=s.membership_id AND (n.facts->>'end')::timestamptz=s.effective_at
      AND ((s.kind='SEAT' AND p.participation_uuid=s.occupancy) OR (s.kind='OFFICE' AND EXISTS(
        SELECT FROM public.core_contextualroleassignment a JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
        JOIN public.core_contextualroleassignmentstateevent e ON e.assignment_id=a.id AND e.action='ASSIGN'
        WHERE a.assignment_uuid=s.occupancy AND d.participation_required AND e.effective_at>=p.effective_at
        AND e.effective_at<s.effective_at)))) OR EXISTS(""")

    old = prior["s015_append_event"]
    marker = " IF token='core_contextualroleassignment' AND action_name='ASSIGN'\n    AND public.elect_office_source"
    new = one(old, marker, """ IF token='core_contextualroleassignment' AND action_name='ASSIGN'
    AND public.elect_recognition_office(anchor,command_event,actor,decision,at_time,deadline) THEN
    IF command_rule IS NOT NULL OR outcome IS DISTINCT FROM 'ACTIVE' OR evidence IS DISTINCT FROM 'elect-recognised:'||event
      THEN PERFORM public.cq_refuse('ELECT_RECOGNISER'); END IF;
 ELSIF token='core_contextualroleassignment' AND action_name='ASSIGN'
    AND public.elect_office_source""")
    marker = " ELSIF token='core_contextualroleassignment' AND command_rule='ELECT:TERM_END' THEN"
    new = one(new, marker, """ ELSIF token='core_contextualroleassignment' AND command_rule='ELECT:SUCCESSOR_LEFT' THEN
    -- The assignment input guard independently binds the complete row to the
    -- exact departure and predecessor. Other uses of this envelope refuse.
    IF NOT is_command OR action_name IS DISTINCT FROM 'END' OR outcome IS DISTINCT FROM 'ENDED'
      THEN PERFORM public.cq_refuse('ELECT_RESULT'); END IF;
""" + marker)
    marker = "AND public.elect_office_source(anchor,command_event,actor,decision,at_time,deadline) THEN\n   values_"
    yield old, one(new, marker, """AND (public.elect_office_source(anchor,command_event,actor,decision,at_time,deadline)
   OR public.elect_recognition_office(anchor,command_event,actor,decision,at_time,deadline)) THEN
   values_""")


def signature(sql):
    name = p1.name(sql)
    if name in ("sbg_guard_membership_end_completeness", "sbg_guard_circleparticipation_occupancy"):
        return f"public.{name}()"
    if name == "elect_vacancy_guard":
        return "public.elect_vacancy_guard()"
    if name == "elect_office_deadline":
        return "public.elect_office_deadline(bigint,timestamptz)"
    if name == "elect_already_replaced":
        return "public.elect_already_replaced(public.elect_vacancy_sources,timestamptz)"
    if name == "elect_record_term_end":
        return "public.elect_record_term_end(bigint,bigint,bigint)"
    if name == "elect_vacancy_instruction":
        return "public.elect_vacancy_instruction(public.elect_vacancy_sources)"
    if name == "s015_append_event":
        return p1.root.creation.SIGNATURE
    return f"public.{name}({elect.SIGNATURES[name]})"


def check(editor, backwards):
    with editor.connection.cursor() as cur:
        for name, args in NEW.items():
            sig = f"public.{name}({args})"
            expected = hard.body(terms.function(continuity.SQL, name)) if backwards else None
            if hard.installed(cur, sig) != expected:
                raise RuntimeError(f"ELECT P3 continuity stopped before DDL for {sig}.")
        for old, new in edits():
            sig = signature(old)
            if hard.installed(cur, sig) != hard.body(new if backwards else old):
                raise RuntimeError(f"ELECT P3 continuity stopped before DDL for {sig}.")
        if backwards:
            cur.execute(f"SELECT EXISTS(SELECT FROM public.core_cqact WHERE operation IN {OP_LIST})")
            if cur.fetchone()[0]:
                raise RuntimeError("ELECT P3 retains recorded recognitions and Board notices.")
            cur.execute("""SELECT EXISTS(SELECT FROM public.core_contextualroleassignmentstateevent
                WHERE composing_rule_reference='ELECT:SUCCESSOR_LEFT')
                OR EXISTS(SELECT FROM public.core_circleparticipationend e
                JOIN public.core_circleparticipation p ON p.id=e.participation_id
                JOIN public.core_organismmembershiptransition t ON t.event_uuid=e.composed_from_event_uuid
                WHERE t.resulting_state='ENDED' AND e.effective_at=p.effective_at
                AND public.elect_pending_successor(p.participation_uuid,t.membership_id,'SEAT',t.effective_at,t.received_at))""")
            if cur.fetchone()[0]:
                raise RuntimeError("ELECT P3 retains a recorded departure before handover.")


def scope(editor, backwards):
    # Select the auto-named shape check by its constrained columns, as in 0080.
    with editor.connection.cursor() as cur:
        cur.execute("""SELECT c.conname FROM pg_constraint c WHERE c.conrelid='public.core_cqact'::regclass AND c.contype='c'
          AND (SELECT array_agg(a.attname::text ORDER BY a.attname) FROM unnest(c.conkey) k
              JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k)=ARRAY['board_uuid','operation','thread_uuid']""")
        rows = cur.fetchall()
    if len(rows) != 1:
        raise RuntimeError("Cannot identify the CQ owner-scope constraint.")
    name = editor.quote_name(rows[0][0])
    ops = ("SETTINGS", "ELECT_VACANCY") + (() if backwards else OPS)
    allowed = ",".join("'" + op + "'" for op in ops)
    editor.execute(f"ALTER TABLE public.core_cqact DROP CONSTRAINT {name}, ADD CONSTRAINT {name} "
                   f"CHECK ((thread_uuid IS NULL AND board_uuid IS NULL)=(operation IN ({allowed})))", params=None)
    editor.execute(p1.votes.operations_constraint(p1.PRIOR_OPS + p1.NOMINATIONS + ops[1:]), params=None)


def preflight(apps, editor):
    check(editor, False)


def install(apps, editor):
    check(editor, False)
    scope(editor, False)
    editor.execute(continuity.SQL, params=None)
    for _, new in edits():
        editor.execute(new, params=None)
    editor.execute(f"""CREATE TRIGGER zz_elect_continuity_effect AFTER INSERT ON public.core_cqact FOR EACH ROW
        WHEN (NEW.operation IN {OP_LIST}) EXECUTE FUNCTION public.elect_continuity_effect()""", params=None)


def uninstall(apps, editor):
    check(editor, True)
    editor.execute("DROP TRIGGER zz_elect_continuity_effect ON public.core_cqact", params=None)
    for old, _ in reversed(list(edits())):
        editor.execute(old, params=None)
    for name, args in reversed(NEW.items()):
        editor.execute(f"DROP FUNCTION public.{name}({args})", params=None)
    scope(editor, True)


RECOGNISED = models.Q(route="GOVERNANCE_CONSTITUTIONAL", constitutional_route="RECOGNITION",
                     deciding_identity__isnull=True, outcome="ACCEPT")


class Migration(migrations.Migration):
    dependencies = (("core", "0082_elect_term_continuity"),)
    operations = (
        migrations.RunPython(preflight, migrations.RunPython.noop),
        migrations.RemoveConstraint("circleadmissiondecision", "sbg_circledecision_route_shape_ck"),
        migrations.AddConstraint("circleadmissiondecision", models.CheckConstraint(
            name="sbg_circledecision_route_shape_ck",
            condition=(p1.OLD_SHAPE & models.Q(deciding_identity__isnull=False)) | p1.ELECTED | elect.COOPTED | RECOGNISED)),
        migrations.RemoveConstraint("circleparticipationend", "elect_one_other_end"),
        migrations.AddConstraint("circleparticipationend", models.UniqueConstraint(fields=("participation",),
            condition=~models.Q(composing_rule_reference__startswith="elect-term:") &
                      ~models.Q(composing_rule_reference__startswith="elect-resign:"), name="elect_one_other_end")),
        migrations.AddConstraint("circleparticipationend", models.UniqueConstraint(fields=("participation",),
            condition=models.Q(composing_rule_reference__startswith="elect-resign:"), name="elect_one_resignation")),
        migrations.RunPython(install, uninstall),
    )
