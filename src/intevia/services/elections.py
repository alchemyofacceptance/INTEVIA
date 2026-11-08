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
    return cq.put_forward(**scope, version_uuid=version_uuid,
                          attachment=attachment(organism_uuid=scope["organism_uuid"], binding_uuid=binding_uuid,
                                                version_id=version_id, provision=provision, place=place, vacancy_uuid=vacancy_uuid))


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
        i.facts->'domain_plan'->'winner'->>'person' AS person,i.facts->'domain_plan'->>'body' AS electorate
        FROM public.core_cqact i JOIN public.core_cqact o ON o.act_uuid=(i.facts->'domain_plan'->>'ballot')::uuid
        WHERE i.organism_id=%s AND i.act_uuid=%s AND i.operation='IMPLEMENT' AND i.facts->'domain_plan'->>'domain'='ELECTION'
        AND public.cq3_result_visible((i.request->>'result')::uuid,i.organism_id,%s,statement_timestamp())""",
        [owner, event_uuid, actor.pk])
    if not rows:
        raise disc.DiscussionUnavailable()
    return rows[0]


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
                    raise cq.CQRefused("ELECT_VACANCY_APPOINTER")
        # SR owns a separate command intake transaction. Bind the exact vacancy in its qualified request.
        roles = OrganismRoleService(authority=S015Authority(provider=ProductionApplicationAuthorityProvider()), clock=database_now)
        return roles.transition_assignment(replace(command, request_reference=f"elect-vacancy:{ending_uuid}"))
    except IntegrityError as exc:
        cq.translate_refusal(exc)
