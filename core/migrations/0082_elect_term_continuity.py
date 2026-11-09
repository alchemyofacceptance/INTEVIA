"""ELECT P3 T. Half-open Board terms and source-bound adjacent succession."""

from importlib import import_module

from django.db import migrations

from . import _elect_terms as terms

hard = import_module("core.migrations.0081_hard_voting_authority")
elect = hard.elect
p1 = elect.p1
c2 = import_module("core.migrations.0055_c2_coverage")
one = elect.one
function = elect.function
T = "timestamptz"
NEW = {
    "elect_term_includes": f"{T},{T},{T}",
    "elect_place_end": f"bigint,{T}",
    "elect_office_deadline": f"bigint,{T}",
    "elect_office_effective": f"bigint,{T},{T}",
    "elect_term_slots": f"bigint,bigint,text,bigint,{T}",
    "elect_due_office": f"bigint,{T}",
    "elect_record_term_end": "bigint,bigint,bigint",
}

# A term ending is a composed act, verifiable even on direct insertion.
# The caller may bring an existing fact up to date, never select its date.
END_GUARD = """
    IF NEW.composing_rule_reference='ELECT:TERM_END' THEN
      IF NEW.action IS DISTINCT FROM 'END' OR NEW.resulting_state IS DISTINCT FROM 'ENDED'
       OR NEW.prior_state IS NULL OR NEW.prior_state NOT IN ('ACTIVE','SUSPENDED') OR NEW.actor_state IS DISTINCT FROM 'NONE'
       OR NEW.actor_id IS NOT NULL OR NEW.actor_access_epoch IS NOT NULL
       OR NEW.actor_capacity IS DISTINCT FROM 'NA_NO_PARTY' OR NEW.entry_mode IS DISTINCT FROM 'COMPOSED'
       OR NEW.composing_rule_state IS DISTINCT FROM 'REFERENCE' OR NEW.composer_state IS DISTINCT FROM 'REFERENCE'
       OR NEW.composer_identity_id IS DISTINCT FROM NEW.enterer_identity_id
       OR NEW.effective_until IS NOT NULL OR NEW.effective_until_state IS DISTINCT FROM 'NONE'
       OR NEW.received_at IS DISTINCT FROM statement_timestamp()
       OR NEW.occurred_at IS DISTINCT FROM NEW.effective_at OR NOT EXISTS(
         SELECT FROM public.elect_due_office(NEW.assignment_id,NEW.received_at) q
         JOIN public.core_contextualroleassignmentstateevent e ON e.id=q.source_id
         JOIN public.core_contextualroleassignment a ON a.id=e.assignment_id
         JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
         JOIN public.core_identity i ON i.id=NEW.enterer_identity_id JOIN public.auth_user u ON u.id=i.credential_id
         WHERE i.access_state='active' AND u.is_active
         AND public.disc_member_state(d.living_organism_id,i.id,NEW.received_at)='ACTIVE'
         AND NEW.effective_at=q.due AND NEW.authority_basis_id=e.authority_basis_id
         AND NEW.authority_decision_reference=e.authority_decision_reference
         AND NEW.evidence_reference='elect-expiry:'||e.event_uuid
         AND NEW.request_reference=NEW.evidence_reference AND NEW.idempotency_key=NEW.evidence_reference
         AND NEW.payload_fingerprint=public.c2_target(NEW.evidence_reference))
      THEN PERFORM public.cq_refuse('ELECT_RESULT'); END IF;
      RETURN NEW;
    END IF;
"""

FUTURE_PLAN = """
 IF (rule->'term'->>'start')::timestamptz>p_cut THEN
    SELECT count(*),min(s.occupancy::text),min(s.ends) INTO occupants_,incumbent_,old_end_
    FROM public.elect_term_slots(gov.id,role_.id,spec->>'place',NULL,p_cut) s
    WHERE public.elect_term_includes(p_cut,s.starts,s.ends);
    IF occupants_=1 THEN
      IF role_.id IS NULL THEN
        SELECT min(t.effective_at) INTO constitutional_end_
        FROM public.core_circleparticipation q JOIN public.core_circleparticipationend t ON t.participation_id=q.id
        WHERE q.participation_uuid=incumbent_::uuid AND starts_with(t.composing_rule_reference,'elect-term:')
        AND greatest(t.received_at,t.recorded_at)<=p_cut;
      ELSE
        SELECT least(a.effective_until, (
          SELECT min(t.effective_at) FROM public.core_circleparticipation q
          JOIN public.core_circleparticipationend t ON t.participation_id=q.id
          WHERE role_.participation_required AND q.circle_id=gov.id AND q.membership_id=o.membership_id
          AND public.elect_term_includes(a.effective_at,q.effective_at,public.elect_place_end(q.id,p_cut))
          AND greatest(q.received_at,q.recorded_at,t.received_at,t.recorded_at)<=p_cut
          AND starts_with(t.composing_rule_reference,'elect-term:')))
        INTO constitutional_end_ FROM public.core_contextualroleassignment o
        JOIN public.core_contextualroleassignmentstateevent a ON a.assignment_id=o.id AND a.action='ASSIGN'
        WHERE o.assignment_uuid=incumbent_::uuid AND greatest(a.received_at,a.recorded_at)<=p_cut;
      END IF;
    END IF;
    IF occupants_=0 OR (occupants_=1 AND constitutional_end_ IS NULL) THEN
      RETURN jsonb_build_object('code','ELECT_HANDOVER_UNKNOWN','reason','The cited provision gives no confirmed handover date. Supply the provision and evidence of its date.');
    END IF;
    IF occupants_<>1 OR old_end_ IS DISTINCT FROM constitutional_end_
       OR old_end_ IS DISTINCT FROM (rule->'term'->>'start')::timestamptz THEN
      RETURN jsonb_build_object('code','ELECT_HANDOVER','reason','These terms do not meet at the recorded handover. Check the place and both terms before calling the election.');
    END IF;
    extra:=extra||jsonb_build_object('incumbent',incumbent_);
 END IF;
"""

VACANT_BODY = """
 SELECT p_cut IS NOT NULL AND p_plan->>'type' IN ('SEAT','OFFICE')
 AND NOT EXISTS(SELECT FROM public.elect_term_slots((p_plan->>'circle')::bigint,
    (p_plan->>'role_id')::bigint,p_plan->>'place',p_person,p_cut) s
 WHERE (s.ends IS NULL OR s.ends>s.starts)
 AND (s.ends IS NULL OR s.ends>greatest(p_cut,(p_plan->'term'->>'start')::timestamptz))
 AND (p_plan->'term'->>'end' IS NULL OR s.starts<(p_plan->'term'->>'end')::timestamptz))
"""

# After the current term, later recorded seat ends cannot override an earlier
# effective departure. Also check the right boundary of the *new* interval.
SEAT_OVERLAP = """
           AND (public.elect_place_end(p.id,v_known_at) IS NULL
                OR public.elect_place_end(p.id,v_known_at)>NEW.effective_at)
           AND (public.elect_place_end(NEW.id,v_known_at) IS NULL
                OR p.effective_at<public.elect_place_end(NEW.id,v_known_at))
"""

# Ordinary SR appointments keep their own cardinality rule. Only a reserved
# elected succession requires exclusivity over this interval for another route.
RESERVATION_GUARD = """
    IF NEW.action='ASSIGN' THEN
      PERFORM 1 FROM public.core_livingorganism lo JOIN public.core_organismroledefinition d
        ON d.living_organism_id=lo.id JOIN public.core_contextualroleassignment a ON a.role_definition_id=d.id
        WHERE a.id=NEW.assignment_id AND d.kind='BOARD_OFFICE' FOR UPDATE OF lo;
    END IF;
    IF NEW.action='ASSIGN' AND EXISTS(
       SELECT FROM public.core_contextualroleassignment a
       JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
       WHERE a.id=NEW.assignment_id AND d.kind='BOARD_OFFICE'
       AND EXISTS(SELECT FROM public.elect_term_slots(NULL,d.id,d.code,NULL,statement_timestamp()) s
         JOIN public.core_contextualroleassignment other ON other.assignment_uuid=s.occupancy
         JOIN public.core_contextualroleassignmentstateevent assigned ON assigned.assignment_id=other.id AND assigned.action='ASSIGN'
         WHERE EXISTS(SELECT FROM public.core_cqact i WHERE i.act_uuid IN (NEW.event_uuid,assigned.event_uuid)
           AND i.operation='IMPLEMENT' AND i.facts->'domain_plan' ? 'incumbent')
         AND other.id<>a.id
         AND (s.ends IS NULL OR s.ends>NEW.effective_at)
         AND (NEW.effective_until IS NULL OR s.starts<NEW.effective_until)))
    THEN PERFORM public.cq_refuse('ELECT_OCCUPIED'); END IF;
"""


def edits():
    landed = {p1.name(new): new for _, new in elect.edits()}
    earlier = {p1.name(new): new for _, new in p1.other_edits()}
    old = landed["elect_plan"]
    new = one(old, "role_ public.core_organismroledefinition;",
              "occupants_ integer; incumbent_ text; old_end_ timestamptz; constitutional_end_ timestamptz; role_ public.core_organismroledefinition;")
    # Refuse a missing cut before consulting HARD's STRICT voting selector.
    new = one(new, "BEGIN\n IF NOT(spec", """BEGIN
 IF p_cut IS NULL THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 IF NOT(spec""")
    start = new.index(" IF (rule->'term'->>'start')::timestamptz>p_cut")
    stop = new.index(" route_:=", start)
    yield "public.elect_plan(bigint,uuid,jsonb,timestamptz)", old, new[:start] + FUTURE_PLAN + new[stop:]

    old = function(p1.effects.SQL, "elect_vacant")
    yield "public.elect_vacant(jsonb,bigint,timestamptz)", old, one(old, hard.body(old), VACANT_BODY)

    old = function(p1.ballot.SQL, "elect_slate")
    new = one(old, "AND NOT EXISTS(SELECT FROM public.core_cqact w WHERE w.operation='ELECT_WITHDRAW'",
              """AND (NOT p_plan ? 'incumbent' OR (
    public.elect_vacant(p_plan,m.id,p_cut)
    AND public.s015_fold_effective_state('core_organismmembership',m.id,(p_plan->'term'->>'start')::timestamptz,p_cut)='ACTIVE'
    AND (NOT coalesce((p_plan->>'participation_required')::boolean,false) OR
       public.circle_place_at((p_plan->>'circle')::bigint,m.id,(p_plan->'term'->>'start')::timestamptz,p_cut))))
 AND NOT EXISTS(SELECT FROM public.core_cqact w WHERE w.operation='ELECT_WITHDRAW'""")
    yield "public.elect_slate(uuid,jsonb,timestamptz)", old, new

    old = function(elect.vacancies.SQL, "elect_already_replaced")
    new = one(old, "AND p.recorded_at<p_cut)", """AND p.recorded_at<p_cut AND (NOT EXISTS(
        SELECT FROM public.core_cqact i WHERE i.act_uuid=p.participation_uuid AND i.operation='IMPLEMENT'
          AND i.facts->'domain_plan' ? 'incumbent') OR public.circle_place_at(p.circle_id,p.membership_id,p.effective_at,p_cut)))""")
    new = one(new, "AND e.recorded_at<p_cut)", """AND e.recorded_at<p_cut AND (NOT EXISTS(
        SELECT FROM public.core_cqact i WHERE i.act_uuid=e.event_uuid AND i.operation='IMPLEMENT'
          AND i.facts->'domain_plan' ? 'incumbent') OR public.elect_office_effective(a.id,e.effective_at,p_cut)))""")
    yield "public.elect_already_replaced(public.elect_vacancy_sources,timestamptz)", old, new

    old = function(elect.vacancies.SQL, "elect_named_appointment")
    marker = "WHERE m.identity_id=p_actor AND s.standing='QUALIFIED'"
    new = one(old, marker, """WHERE (v.instruction->>'authority'='office:LO_COORDINATOR' OR EXISTS(
     SELECT FROM public.c2_seat_facts(p_lo,p_cut,p_known) f
     WHERE f.membership_id=m.id AND f.role_code=s.role_code AND f.role_scope=s.role_scope AND f.standing='QUALIFIED'
     AND EXISTS(SELECT FROM public.c2_seat_facts(p_lo,p_known,p_known) live
         WHERE live.assignment_id=f.assignment_id AND live.standing='QUALIFIED')))
   AND m.identity_id=p_actor AND s.standing='QUALIFIED'""")
    new = one(new, "WHERE d.id=p_role", "WHERE p_cut IS NOT NULL AND p_known IS NOT NULL AND d.id=p_role")
    new = one(new, "substr(v.instruction->>'authority',7)", "split_part(v.instruction->>'authority',':',2)")
    yield "public.elect_named_appointment(bigint,bigint,bigint,timestamptz,timestamptz)", old, new

    old = function(p1.effects.SQL, "elect_settlement")
    new = one(old, "OR NOT public.elect_vacant", """OR (plan ? 'incumbent' AND NOT EXISTS(
       SELECT FROM public.core_organismmembership m WHERE m.id=(candidate->>'member')::bigint
       AND public.s015_fold_effective_state('core_organismmembership',m.id,(plan->'term'->>'start')::timestamptz,p_cut)='ACTIVE'
       AND (NOT coalesce((plan->>'participation_required')::boolean,false) OR EXISTS(
         SELECT FROM public.core_circleparticipation p WHERE p.circle_id=(plan->>'circle')::bigint
         AND p.membership_id=m.id AND greatest(p.received_at,p.recorded_at)<=p_cut
         AND public.elect_term_includes((plan->'term'->>'start')::timestamptz,p.effective_at,
            public.elect_place_end(p.id,p_cut))))))
    OR NOT public.elect_vacant""")
    yield "public.elect_settlement(public.core_cqact,public.core_cqact,jsonb,timestamptz)", old, new

    signature = "public.sbg_guard_circleparticipation_occupancy()"
    previous = next(body for sig, _, body, _ in p1.circle.BODIES if sig == signature)
    old = p1.trigger_sql(signature, previous)
    start = old.index(
        "           AND (\n                NOT EXISTS (\n                    SELECT 1\n                      FROM public.core_circleparticipationend")
    stop = old.index("    ) INTO v_seat_occupied;", start)
    new = old[:start] + SEAT_OVERLAP + old[stop:]
    start = new.index("           AND NOT EXISTS (", new.index("IF v_seat_occupied THEN"))
    stop = new.index("    ) INTO v_person_seated;", start)
    yield signature, old, new[:start] + SEAT_OVERLAP + new[stop:]

    old = function(c2.SQL, "c2_seat_facts")
    new = one(old, "AND public.s015_fold_effective_state('core_organismmembership',m.id,at_time,known)='ACTIVE'",
              """AND (r.kind IS DISTINCT FROM 'BOARD_OFFICE' OR r.participation_required IS NOT TRUE OR public.elect_office_effective(a.id,at_time,known))
 AND public.s015_fold_effective_state('core_organismmembership',m.id,at_time,known)='ACTIVE'""")
    yield "public.c2_seat_facts(bigint,timestamptz,timestamptz)", old, new

    old = earlier["s015_append_event"]
    marker = " ELSIF token='core_contextualroleassignment' AND (\n     action_name"
    new = one(old, marker, """ ELSIF token='core_contextualroleassignment' AND command_rule='ELECT:TERM_END' THEN
    IF NOT is_command OR action_name IS DISTINCT FROM 'END' OR outcome IS DISTINCT FROM 'ENDED'
       OR NOT EXISTS(SELECT FROM public.elect_due_office(anchor,statement_timestamp()) q WHERE q.due=at_time)
    THEN PERFORM public.cq_refuse('ELECT_RESULT'); END IF;
""" + marker)
    yield p1.root.creation.SIGNATURE, old, new

    old = earlier["s2b_guard_office_attribution"]
    new = one(old, "\nBEGIN\n", "\nBEGIN\n" + END_GUARD + RESERVATION_GUARD)
    new = one(new, "AND held_definition.code = v_role_code", """AND held_definition.code = v_role_code
               AND (held_definition.kind IS DISTINCT FROM 'BOARD_OFFICE' OR NOT EXISTS(
                 SELECT FROM public.core_contextualroleassignmentstateevent started
                 WHERE started.assignment_id=held.id AND started.action='ASSIGN'
                 AND public.elect_office_deadline(started.id,NEW.recorded_at)<=NEW.effective_at))""")
    yield "public.s2b_guard_office_attribution()", old, new


def change(editor, reverse):
    pairs = list(edits())
    with editor.connection.cursor() as cursor:
        for name, args in NEW.items():
            signature = f"public.{name}({args})"
            expected = hard.body(function(terms.SQL, name)) if reverse else None
            if hard.installed(cursor, signature) != expected:
                raise RuntimeError(f"ELECT P3 stopped. Unexpected definition for {signature}.")
        for signature, old, new in pairs:
            if hard.installed(cursor, signature) != hard.body(new if reverse else old):
                raise RuntimeError(f"ELECT P3 stopped. Installed body differs for {signature}.")
        if reverse:
            cursor.execute("""SELECT EXISTS(SELECT FROM public.core_cqact
                WHERE facts->'domain_plan' ? 'incumbent' OR facts->'mandate' ? 'incumbent')
                OR EXISTS(SELECT FROM public.core_contextualroleassignmentstateevent
                WHERE composing_rule_reference='ELECT:TERM_END')""")
            if cursor.fetchone()[0]:
                raise RuntimeError("ELECT P3 retains recorded term continuity.")
    if reverse:
        for _, old, _ in reversed(pairs):
            editor.execute(old, params=None)
        for name, args in reversed(NEW.items()):
            editor.execute(f"DROP FUNCTION public.{name}({args})", params=None)
    else:
        editor.execute(terms.SQL, params=None)
        for _, _, new in pairs:
            editor.execute(new, params=None)


def install(apps, editor):
    change(editor, False)


def uninstall(apps, editor):
    change(editor, True)


class Migration(migrations.Migration):
    dependencies = (("core", "0081_hard_voting_authority"),)
    operations = (migrations.RunPython(install, uninstall),)
