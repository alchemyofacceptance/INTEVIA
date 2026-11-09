"""Constitution-bound elections on the owning body's CQ journey."""

import json
from dataclasses import replace

from django.db import IntegrityError, transaction

from src.intevia.services import cq, cq_reads
from src.intevia.services import discussion as disc


def attachment(*, organism_uuid, binding_uuid, version_id, provision, place, vacancy_uuid=None):
    """Cite an exact Article or Bylaw. The caller supplies no counting instructions."""
    return cq._settings_proposal("ELECTION", {
        "organism": organism_uuid, "binding": binding_uuid, "version": version_id,
        "provision": provision, "place": place,
        **({"vacancy": vacancy_uuid} if vacancy_uuid is not None else {}),
    })


def call(*, version_uuid, binding_uuid, version_id, provision, place, vacancy_uuid=None, **scope):
    proposal = attachment(organism_uuid=scope["organism_uuid"], binding_uuid=binding_uuid,
                          version_id=version_id, provision=provision, place=place, vacancy_uuid=vacancy_uuid)
    if vacancy_uuid is None:
        return cq.put_forward(**scope, version_uuid=version_uuid, attachment=proposal)
    try:
        with transaction.atomic():
            return _call_vacancy(scope, version_uuid, proposal, vacancy_uuid)
    except IntegrityError as error:
        cq.translate_refusal(error)


def _call_vacancy(scope, version_uuid, proposal, vacancy_uuid):
    spec = proposal["spec"]
    actor = disc._actor(scope["credential"])
    cq._lock(cq._id(scope["request_uuid"]), cq._id(scope["organism_uuid"]))
    admission = cq_reads._scope(actor, scope["organism_uuid"], scope.get("circle_uuid"), scope["thread_uuid"])
    if not admission["thread_audience"]:
        raise disc.DiscussionUnavailable()
    if disc._rows("""SELECT 1 FROM public.core_electvacancy v JOIN public.core_discboard b ON b.organism_id=v.organism_id
        WHERE b.board_uuid=%s AND v.ending=%s AND v.place=%s AND v.instruction->>'binding'=%s
        AND v.instruction->>'version'=%s AND v.instruction->>'election'=%s
        AND v.instruction->>'route' IN ('BY_ELECTION','BOARD_CQ','COOPTION')
        AND NOT EXISTS(SELECT FROM public.core_cqact replay WHERE replay.act_uuid=%s AND replay.organism_id=v.organism_id
          AND replay.thread_uuid=%s AND replay.actor_id=%s AND replay.operation='INITIATE')
        AND public.disc_member_state(v.organism_id,%s,statement_timestamp())='ACTIVE'
        AND public.elect_admin_office(v.organism_id,b.board_uuid,%s::jsonb,%s,statement_timestamp()) IS NOT NULL
        AND (public.cq_settings_values_at(v.organism_id,statement_timestamp(),statement_timestamp())->>'enabled')::boolean IS FALSE""",
        [admission["board_uuid"], vacancy_uuid, spec["place"], str(spec["binding"]), str(spec["version"]), spec["provision"], scope["request_uuid"],
         scope["thread_uuid"], actor.pk, actor.pk, json.dumps(proposal), actor.pk]):
        raise cq.CQRefused("ELECT_VACANCY_VOTING")
    return cq.put_forward(**scope, version_uuid=version_uuid, attachment=proposal)


def nominate(*, person_uuid, assignment_uuid=None, statement="", **scope):
    return cq._command(operation="ELECT_NOMINATE", person=person_uuid, assignment=assignment_uuid,
                       statement=statement, **scope)


def consent(*, nomination_uuid, **scope):
    return cq._command(operation="ELECT_CONSENT", nomination=nomination_uuid, **scope)


def withdraw(*, nomination_uuid, **scope):
    """The nominee declines or withdraws their own nomination."""
    return cq._command(operation="ELECT_WITHDRAW", nomination=nomination_uuid, **scope)


@transaction.atomic
def slate(*, credential, organism_uuid, thread_uuid, circle_uuid=None):
    actor = disc._actor(credential)
    admission = cq_reads._scope(actor, organism_uuid, circle_uuid, thread_uuid)
    if not admission["thread_audience"]:
        raise disc.DiscussionUnavailable()
    rows = disc._rows("""SELECT coalesce(o.facts->'slate',prep.facts->'prepared_slate',
          public.elect_slate(i.thread_uuid,public.elect_plan(i.organism_id,i.board_uuid,i.request->'attachment',statement_timestamp()),
             statement_timestamp())) AS candidates
       FROM public.core_cqact i LEFT JOIN LATERAL (SELECT a.facts FROM public.core_cqact a WHERE a.thread_uuid=i.thread_uuid
         AND a.operation IN ('PROCEED','OPEN_VOTE','REOPEN_VOTE') ORDER BY a.ordinal DESC LIMIT 1) o ON true
       LEFT JOIN LATERAL (SELECT a.facts FROM public.core_cqact a WHERE a.thread_uuid=i.thread_uuid
         AND a.operation IN ('PREPARED','PREPARATION_DUE') ORDER BY a.ordinal DESC LIMIT 1) prep ON true
       WHERE i.operation='INITIATE' AND i.thread_uuid=%s AND i.board_uuid=%s AND i.request->'attachment'->>'kind'='ELECTION'""",
       [thread_uuid, admission["board_uuid"]])
    if not rows:
        raise disc.DiscussionUnavailable()
    return [{key: entry[key] for key in ("person", "statement", "nomination", "consent")} for entry in rows[0]["candidates"]]


def transfer_policy(*, scope, citation, from_body, to_body, predecessor, purpose):
    """Draft one Policy instruction. Only its subsequently qualified adoption can transfer a power."""
    return json.dumps({"purpose": purpose, "authority_transfer": {
        "scope": scope, "citation": citation, "from": from_body, "to": to_body, "predecessor": predecessor,
    }}, default=cq._json_reference, ensure_ascii=False, sort_keys=True)


@transaction.atomic
def power_history(*, credential, organism_uuid, scope):
    actor = disc._actor(credential)
    owner = cq._read_owner(actor, organism_uuid)
    if not isinstance(scope, dict):
        raise TypeError("Identify the exact power to read.")
    return disc._rows("""SELECT t.act_uuid AS transfer,t.decision::uuid AS result,t.transfer->'predecessor' AS predecessor,
        t.transfer->'scope' AS scope,t.transfer->'citation' AS provision,t.transfer->'constitutional_decision' AS membership_decision,
        t.transfer->>'from' AS previous_holder,t.transfer->>'to' AS holder,t.transfer->>'policy_binding' AS policy_binding,
        t.transfer->'policy_version' AS policy_version,t.recorded_at,t.effective_at,e.event_uuid AS ilc_event
        FROM public.elect_transfers t JOIN public.core_livingorganismevent e ON e.event_uuid=t.act_uuid
        WHERE t.organism_id=%s AND t.transfer->'scope'=%s::jsonb
        AND public.cq3_result_visible(t.decision::uuid,t.organism_id,%s,statement_timestamp()) ORDER BY t.ordinal""",
        [owner, json.dumps(scope, default=cq._json_reference), actor.pk])


@transaction.atomic
def authorising_result(*, credential, organism_uuid, event_uuid):
    """Follow an elected admission or appointment to its visible result and original ballot."""
    actor = disc._actor(credential)
    owner = cq._read_owner(actor, organism_uuid)
    disc._read_uuid(event_uuid)
    rows = disc._rows("""SELECT i.act_uuid AS implementation,(i.request->>'result')::uuid AS result,o.act_uuid AS ballot,
        o.facts->'administrator_mandate' AS administration, i.facts->'domain_plan'->>'place' AS place,
        i.facts->'domain_plan'->'winner'->>'person' AS person,i.facts->'domain_plan'->>'body' AS electorate,
        (i.facts->'domain_plan'->>'start')::timestamptz AS term_start,
        (i.facts->'domain_plan'->>'end')::timestamptz AS term_end,
        (i.facts->'domain_plan'->>'incumbent')::uuid AS preceding_occupancy,
        support.composed_from_event_uuid AS supporting_place_end_act,
        support.composing_rule_reference AS supporting_place_end_rule,
        support.effective_at AS supporting_place_ends_at,
        CASE WHEN i.facts->'domain_plan' ? 'incumbent' AND
          (support.effective_at<=award.effective_at OR NOT EXISTS(
          SELECT FROM public.core_organismmembership m WHERE m.id=(i.facts->'domain_plan'->'winner'->>'member')::bigint
          AND NOT EXISTS(SELECT FROM public.core_organismmembershiptransition t WHERE t.membership_id=m.id
              AND t.resulting_state='ENDED' AND t.recorded_at<=statement_timestamp()
              AND public.elect_pending_successor(i.act_uuid,m.id,i.facts->'domain_plan'->>'type',t.effective_at,t.received_at))
          AND public.s015_fold_effective_state('core_organismmembership',m.id,
              (i.facts->'domain_plan'->>'start')::timestamptz,statement_timestamp())='ACTIVE'
          AND (coalesce((i.facts->'domain_plan'->>'participation_required')::boolean,false)=false
            OR public.circle_place_at((i.facts->'domain_plan'->>'circle')::bigint,m.id,
                (i.facts->'domain_plan'->>'start')::timestamptz,statement_timestamp()))))
        THEN 'ELECT_SUCCESSOR_LOST' END AS eligibility_reason
        FROM public.core_cqact i JOIN public.core_cqact o ON o.act_uuid=(i.facts->'domain_plan'->>'ballot')::uuid
        LEFT JOIN public.core_contextualroleassignmentstateevent award ON award.event_uuid=i.act_uuid AND award.action='ASSIGN'
        LEFT JOIN LATERAL public.elect_office_support_ending(award.id,statement_timestamp()) support
          ON award.effective_until IS NULL OR support.effective_at<=award.effective_until
        WHERE i.organism_id=%s AND i.act_uuid=%s AND i.operation='IMPLEMENT' AND i.facts->'domain_plan'->>'domain'='ELECTION'
        AND public.cq3_result_visible((i.request->>'result')::uuid,i.organism_id,%s,statement_timestamp())""",
        [owner, event_uuid, actor.pk])
    if not rows:
        raise disc.DiscussionUnavailable()
    row = rows[0]
    row["eligibility_message"] = cq.MESSAGES.get(row.pop("eligibility_reason"))
    return row


def catch_up_vacancies(*, credential):
    """Start past or now-effective routes once. No member supplies a filling instruction."""
    with transaction.atomic():
        actor = disc._actor(credential)
        pending = disc._rows("""SELECT s.ending,s.organism_id FROM public.elect_vacancy_sources s
            JOIN public.core_organismmembership m ON m.living_organism_id=s.organism_id AND m.identity_id=%s
            WHERE greatest(s.recorded_at,s.effective_at)<=statement_timestamp()
            AND NOT public.elect_already_replaced(s,statement_timestamp()) AND NOT EXISTS(
                SELECT FROM public.core_electvacancy v WHERE v.ending=s.ending)
            AND (public.cq_settings_at(s.organism_id,greatest(s.recorded_at,s.effective_at))->>'enabled')::boolean
            AND public.disc_member_state(s.organism_id,%s,statement_timestamp())='ACTIVE' ORDER BY s.organism_id,s.ending""",
            [actor.pk, actor.pk])
    started = False
    for source in pending:
        with transaction.atomic():
            actor = disc._actor(credential)
            disc._rows("SELECT id FROM public.core_livingorganism WHERE id=%s FOR UPDATE", [source["organism_id"]])
            rows = disc._rows("""SELECT public.elect_start_vacancy(%s,%s,%s) AS started
                WHERE public.disc_member_state(%s,%s,statement_timestamp())='ACTIVE'""",
                [source["organism_id"], source["ending"], actor.pk, source["organism_id"], actor.pk])
            started = started or bool(rows and rows[0]["started"])
    return started


@transaction.atomic
def vacancies(*, credential, organism_uuid, ending_uuid=None):
    """Read the actual ending, the frozen filling instruction and its call, fill or hold."""
    actor = disc._actor(credential)
    owner = cq._read_owner(actor, organism_uuid)
    if ending_uuid is not None:
        disc._read_uuid(ending_uuid)
    return disc._rows("""SELECT v.ending,v.route_act,v.kind,v.place,v.effective_at,v.recorded_at,v.instruction,
      CASE WHEN public.cq3_result_visible(f.result,v.organism_id,%s,statement_timestamp()) THEN f.result END AS result,
      CASE WHEN f.result IS NULL OR public.cq3_result_visible(f.result,v.organism_id,%s,statement_timestamp()) THEN f.event_uuid END AS filled_by,
      CASE WHEN f.result IS NULL OR public.cq3_result_visible(f.result,v.organism_id,%s,statement_timestamp()) THEN f.decision_reference END AS fill_authority,
      CASE WHEN allowed.visible THEN i.act_uuid END AS call,CASE WHEN allowed.visible THEN i.thread_uuid END AS thread
      FROM public.core_electvacancy v LEFT JOIN public.core_electvacancyfill f ON f.ending=v.ending
      LEFT JOIN public.core_cqact i ON i.organism_id=v.organism_id AND i.operation='INITIATE'
       AND i.request->'attachment'->'spec'->>'vacancy'=v.ending::text
      LEFT JOIN LATERAL (SELECT (public.disc_audience(v.organism_id,b.circle_id,%s,c.access_level,statement_timestamp())
          AND ((NOT first.coordinator_hidden AND (NOT first.author_hidden OR EXISTS(SELECT FROM public.core_cqact o WHERE
              o.thread_uuid=i.thread_uuid AND o.operation IN ('PROCEED','OPEN_VOTE','CLOSE','NO_BALLOT'))))
          OR public.disc_office(v.organism_id,b.circle_id,%s,statement_timestamp()) IS NOT NULL)) AS visible
        FROM public.core_discboard b JOIN public.core_discthread t ON t.thread_uuid=i.thread_uuid
        JOIN public.disc_categories c ON c.category_uuid=t.category_uuid
        JOIN public.discp_posts first ON first.thread_uuid=i.thread_uuid AND first.post_number=1
        WHERE b.board_uuid=i.board_uuid) allowed ON true
      WHERE v.organism_id=%s AND (%s::uuid IS NULL OR v.ending=%s) ORDER BY v.effective_at,v.ending""",
      [actor.pk, actor.pk, actor.pk, actor.pk, actor.pk, owner, ending_uuid, ending_uuid])


def _holder_term_refusal(owner, actor, role, instruction, effective):
    """Explain a named Board officer's own time boundary after ordinary audience checks."""
    authority = instruction.get("authority") or ""
    if (instruction.get("route") != "APPOINTMENT" or role.appointing_body_reference != authority
            or not authority.startswith("office:") or authority == "office:LO_COORDINATOR"):
        return "ELECT_VACANCY_APPOINTER"
    rows = disc._rows("""
        SELECT e.effective_at AS starts,public.elect_office_deadline(e.id,statement_timestamp()) AS ends,
               statement_timestamp() AS known
        FROM public.core_organismroledefinition d JOIN public.core_contextualroleassignment a ON a.role_definition_id=d.id
        JOIN public.core_organismmembership m ON m.id=a.membership_id
        JOIN public.core_contextualroleassignmentstateevent e ON e.assignment_id=a.id AND e.action='ASSIGN'
        WHERE d.living_organism_id=%s AND d.scope='LIVING_ORGANISM' AND d.kind='BOARD_OFFICE'
        AND d.code=%s AND m.identity_id=%s AND greatest(e.recorded_at,e.received_at)<=statement_timestamp()
        ORDER BY e.effective_at DESC,e.id DESC""", [owner, authority.removeprefix("office:"), actor.pk])
    if rows and effective is not None:
        for row in rows:
            if row["starts"] <= effective and row["starts"] <= row["known"]:
                if row["ends"] is not None and row["ends"] <= max(effective, row["known"]):
                    return "ELECT_HOLDER_EXPIRED"
                return "ELECT_VACANCY_APPOINTER"
        return "ELECT_HOLDER_EARLY"
    return "ELECT_VACANCY_APPOINTER"


def appoint_to_vacancy(*, credential, organism_uuid, ending_uuid, command):
    """Exercise a named office's route through SR's normal consent, authority and receipt writer."""
    from core.models import ContextualRoleAssignment
    from src.intevia.services.clock import database_now
    from src.intevia.services.organism_application_authority import (
        ProductionApplicationAuthorityProvider,
    )
    from src.intevia.services.organism_authority import S015Authority
    from src.intevia.services.organism_role_service import OrganismRoleService

    try:
        with transaction.atomic():
            actor = disc._actor(credential)
            owner = cq._read_owner(actor, organism_uuid)
            records = vacancies(credential=credential, organism_uuid=organism_uuid, ending_uuid=ending_uuid)
            assignment = ContextualRoleAssignment.objects.filter(assignment_uuid=command.assignment_uuid,
                    membership__living_organism_id=owner).select_related("role_definition").first()
            if not records or assignment is None or command.credential != credential:
                raise disc.DiscussionUnavailable()
            record = records[0]
            if record["instruction"]["route"] == "HOLD_HG":
                raise cq.CQRefused("ELECT_VACANCY_HOLD")
            if command.action != "ASSIGN" or record["kind"] != "OFFICE" or record["place"] != assignment.role_definition.code:
                raise cq.CQRefused("ELECT_VACANCY_ROUTE")
            if record["filled_by"] is None:
                admitted = disc._rows("""SELECT public.elect_named_appointment(%s,%s,%s,%s,statement_timestamp()) AS ending""",
                                       [owner, assignment.role_definition_id, actor.pk, command.effective_at])
                if admitted[0]["ending"] != ending_uuid:
                    raise cq.CQRefused(_holder_term_refusal(owner, actor, assignment.role_definition,
                                                            record["instruction"], command.effective_at))
        # SR owns a separate command intake transaction. Bind the exact vacancy in its qualified request.
        roles = OrganismRoleService(authority=S015Authority(provider=ProductionApplicationAuthorityProvider()), clock=database_now)
        return roles.transition_assignment(replace(command, request_reference=f"elect-vacancy:{ending_uuid}"))
    except IntegrityError as exc:
        cq.translate_refusal(exc)


def catch_up_terms(*, credential, organism_uuid):
    """Record outstanding Board term consequences in one authorised LO. Safe to repeat."""
    try:
        with transaction.atomic():
            actor = disc._actor(credential)
            owner = cq._read_owner(actor, organism_uuid)
            disc._rows("SELECT id FROM public.core_livingorganism WHERE id=%s FOR UPDATE", [owner])
            state = disc._rows("SELECT public.disc_member_state(%s,%s,statement_timestamp()) AS state",
                               [owner, actor.pk])
            if state[0]["state"] != "ACTIVE":
                raise disc.DiscussionUnavailable()
            offices = disc._rows("""
                SELECT a.id FROM public.core_contextualroleassignment a
                JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
                CROSS JOIN LATERAL public.elect_due_office(a.id,statement_timestamp()) q
                WHERE d.living_organism_id=%s ORDER BY q.due,a.id""", [owner])
            recorded = 0
            for office in offices:
                rows = disc._rows("SELECT public.elect_record_term_end(%s,%s,%s) AS changed",
                                  [owner, office["id"], actor.pk])
                recorded += int(rows[0]["changed"])
            notices = disc._rows("""SELECT a.act_uuid FROM public.core_cqact a
                WHERE a.organism_id=%s AND a.operation='BOARD_RESIGN' AND (a.facts->>'end')::timestamptz<=statement_timestamp()
                AND NOT EXISTS(SELECT FROM public.core_coverageassessment x JOIN public.core_essentialcoveragecase c ON c.id=x.case_id
                  WHERE c.living_organism_id=a.organism_id AND x.evidence_reference='elect-resignation:'||a.act_uuid)
                ORDER BY a.ordinal""", [owner])
            for notice in notices:
                rows = disc._rows("SELECT public.elect_resignation_coverage(%s,%s,%s) AS changed",
                                  [owner, notice["act_uuid"], actor.pk])
                recorded += int(rows[0]["changed"])
            # Fixed seat endings already exist. Pass due ones to the unchanged
            # ELECT route. Office END inserts use its existing deferred trigger.
            seats = disc._rows("""
                SELECT s.ending FROM public.elect_vacancy_sources s
                WHERE s.organism_id=%s AND s.kind='SEAT'
                AND greatest(s.effective_at,s.recorded_at)<=statement_timestamp()
                AND NOT EXISTS(SELECT FROM public.core_electvacancy v WHERE v.ending=s.ending)
                AND NOT public.elect_already_replaced(s,statement_timestamp())
                ORDER BY s.effective_at,s.ending""", [owner])
            for seat in seats:
                rows = disc._rows("SELECT public.elect_start_vacancy(%s,%s,%s) AS changed",
                                  [owner, seat["ending"], actor.pk])
                recorded += int(rows[0]["changed"])
        return recorded
    except IntegrityError as error:
        cq.translate_refusal(error)


def confirm_recognition(*, claim, **scope):
    """The incumbent confirms the exact external appointment and its remaining term."""
    return _continuity_command("RECOGNITION_CONSENT", {"claim": claim}, **scope)


def recognise_incumbent(*, claim, consent_uuid=None, result_uuid=None, **scope):
    """Record the constitutional recognition separately from the original appointment."""
    return _continuity_command("RECOGNISE", {"claim": claim, "consent": consent_uuid, "result": result_uuid}, **scope)


def resign_board_place(*, participation_uuid, binding_uuid=None, version_id=None, provision=None,
                       notice_uuid=None, **scope):
    """Give or complete the holder's notice. Return the act, effect and any referral message."""
    return _continuity_command("BOARD_RESIGN", {
        "participation": participation_uuid, "binding": binding_uuid, "version": version_id,
        "provision": provision, "notice": notice_uuid,
    }, **scope)


def _continuity_command(operation, fields, *, credential, organism_uuid, request_uuid):
    try:
        with transaction.atomic():
            actor = disc._actor(credential)
            key, owner = cq._id(request_uuid), cq._id(organism_uuid)
            received = cq._lock(key, owner)
            disc._rows("""SELECT public.cq_source(lo.id,NULL,NULL,%s,%s,statement_timestamp())
                         FROM public.core_livingorganism lo WHERE lo.organism_id=%s""", [operation, actor.pk, owner])
            act = cq._execute(actor, owner, None, None, operation, key, fields, received)
            row = disc._rows("SELECT facts->>'effect' AS effect FROM public.core_cqact WHERE act_uuid=%s", [act])[0]
            return {"act": act, "effect": row["effect"], "message": (
                cq.MESSAGES["ELECT_NOTICE_DATE"] if row["effect"] == "AWAITING_BASIS" else None)}
    except IntegrityError as error:
        cq.translate_refusal(error)


@transaction.atomic
def board_continuity(*, credential, organism_uuid, act_uuid=None):
    """Follow recognised incumbencies and Board notices to their own institutional receipts."""
    actor = disc._actor(credential)
    owner = cq._read_owner(actor, organism_uuid)
    if act_uuid is not None:
        disc._read_uuid(act_uuid)
    return disc._rows("""
        SELECT a.act_uuid,a.operation,a.actor_id AS enterer,a.authority_source,a.request,
               a.facts,a.received_at,a.effective_at,a.recorded_at,e.event_uuid AS ilc_event,
               h.act_uuid AS holder_consent,h.actor_id AS consent_enterer,h.effective_at AS consented_at
        FROM public.core_cqact a JOIN public.core_livingorganismevent e ON e.event_uuid=a.act_uuid
        LEFT JOIN public.core_cqact h ON h.act_uuid::text=a.facts->>'consent' AND h.organism_id=a.organism_id
          AND h.operation='RECOGNITION_CONSENT'
        WHERE a.organism_id=%s AND a.operation IN ('RECOGNISE','BOARD_RESIGN')
        AND (%s::uuid IS NULL OR a.act_uuid=%s) AND (a.authority_source->>'kind'<>'CQ_RESULT'
          OR public.cq3_result_visible((a.request->>'result')::uuid,a.organism_id,%s,statement_timestamp()))
        ORDER BY a.ordinal""", [owner, act_uuid, act_uuid, actor.pk])
