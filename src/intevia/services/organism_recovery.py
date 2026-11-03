"""Governed K-2 steps on the LO chain, with half-open windows."""
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

from django.db import connections, transaction

from core.models import Identity, LivingOrganism, LivingOrganismEvent
from src.intevia.services.clock import database_now
from src.intevia.services.organism_coverage import Cause
from src.intevia.services.organism_authority import authority_target_fingerprint


def duration(lo, phase, at):
    # Neither window has an operative subscriber setting on this base.
    return timedelta(hours=720)


def head_at(alias, lo, at, known):
    """Read the governed LO head and the opening event of its recovery episode."""
    with connections[alias].cursor() as cursor:
        cursor.execute("""
            SELECT h.id AS pk,h.event_uuid,h.action,h.resulting_state,h.effective_at,h.recorded_at,
                   h.effective_until AS deadline,h.enterer_identity_id,h.authority_basis_id,h.authority_decision_reference,
                   origin.event_uuid AS origin_uuid,origin.recorded_at AS origin_recorded,origin.effective_at AS origin_effective
            FROM public.c2_phase_at(%s,%s,%s) h
            LEFT JOIN public.core_livingorganismevent origin ON h.action='K2_DEACTIVATE'
             AND origin.living_organism_id=h.living_organism_id AND origin.action='WITHOUT_COORDINATOR'
             AND h.evidence_reference='k2:'||origin.event_uuid""", [lo.pk, at, known])
        row = cursor.fetchone()
        if row is None:
            return None
        return SimpleNamespace(**dict(zip((column[0] for column in cursor.description), row, strict=True)))


def projected(head, at):
    if not head:
        return None
    if head.action == "WITHOUT_COORDINATOR" and at >= head.deadline:
        return "DORMANT" if at < head.deadline + duration(None, 2, head.deadline) else "CLOSED"
    if head.action == "K2_DEACTIVATE" and at >= head.deadline:
        return "CLOSED"
    return head.resulting_state


def episode_origin(head):
    if head and head.action == "WITHOUT_COORDINATOR":
        return head
    if head and head.action == "K2_DEACTIVATE" and head.origin_uuid:
        return SimpleNamespace(event_uuid=head.origin_uuid, recorded_at=head.origin_recorded,
                               effective_at=head.origin_effective)
    return None


def episode_id(head):
    origin = episode_origin(head)
    return str(origin.event_uuid) if origin else None


def window_at_both_cuts(alias, lo, at, known):
    first = head_at(alias, lo, at, known)
    now = database_now(alias)
    current = head_at(alias, lo, now, now)
    if (first and current and first.event_uuid == current.event_uuid and first.action in {"WITHOUT_COORDINATOR", "K2_DEACTIVATE"}
            and at < first.deadline and now < current.deadline and not lo.platform_root):
        return first, now
    return None, now


def live_episode(alias, lo, at, known):
    from src.intevia.services.organism_coverage import assess

    window, now = window_at_both_cuts(alias, lo, at, known)
    if (window and assess(alias, lo, at, known).coordinator_vacancy
            and assess(alias, lo, now, now).coordinator_vacancy):
        return window
    return None


def proposal_in_episode(alias, episode, proposal, nominee, actor_pk):
    from core.models import AuthorityDecision

    origin = episode_origin(episode)
    return (origin and proposal and proposal.actor_id == actor_pk
            and proposal.recorded_at >= origin.recorded_at and proposal.effective_at >= origin.effective_at
            and AuthorityDecision.objects.using(alias).filter(
                decision_reference=proposal.authority_decision_reference, outcome="QUALIFIED",
                action="TRANSITION_CONTEXTUAL_ROLE", actor_id=actor_pk, target__event_action="PROPOSE",
                target__membership_uuid=str(nominee.membership_uuid), target__k2_episode=str(origin.event_uuid),
            ).exists())


def transition(alias, lo, cause, action, state, until=None):
    head = LivingOrganismEvent.objects.using(alias).filter(living_organism=lo).order_by("-sequence").first()
    return cause.append(alias, anchor=lo, head=head, action=action, state=state, until=until,
                        composing_rule="HG:RC-1:K-2/H-4")


def start(alias, lo, cause):
    if lo.platform_root:
        raise ValueError("Root recovery requires its own qualified route.")
    return transition(alias, lo, cause, "WITHOUT_COORDINATOR", "RESTRICTED_CONTINUITY",
                      cause.command.effective_at + duration(lo, 1, cause.command.effective_at))


def advance(alias, organism_pk):
    """Replay all due boundaries from their original instants under one LO lock."""
    with transaction.atomic(using=alias):
        lo = LivingOrganism.objects.using(alias).select_for_update().get(pk=organism_pk)
        with connections[alias].cursor() as cursor:
            cursor.execute("SELECT public.s015_register_organism_lock(%s)", [lo.organism_id])
            cursor.execute("""
                SELECT DISTINCT i.due, i.source FROM public.c2_timed_input i
                WHERE i.lo_id=%s AND i.due<=clock_timestamp() AND NOT EXISTS (
                    SELECT FROM core_coverageassessment a JOIN core_essentialcoveragecase c ON c.id=a.case_id
                    WHERE c.living_organism_id=i.lo_id AND a.evidence_reference='c2-boundary:'||i.source
                    AND a.effective_at=i.due) ORDER BY i.due,i.source""", [lo.pk])
            for _, source in cursor.fetchall():
                cursor.execute("SELECT public.c2_notice_input(%s,%s)", [lo.pk, source])
        now = database_now(alias)
        count = 0
        while True:
            head = head_at(alias, lo, now, now)
            if (lo.platform_root or not head or head.action not in {"WITHOUT_COORDINATOR", "K2_DEACTIVATE"}
                    or head.deadline is None or head.deadline > now):
                return count
            key = f"k2:{head.event_uuid}"
            # Composed event: identify the human origin of the rule application, never a worker account.
            actor = Identity.objects.using(alias).get(pk=head.enterer_identity_id)
            command = SimpleNamespace(effective_at=head.deadline, occurred_at=head.deadline,
                                      request_reference=key, idempotency_key=key)
            cause = Cause(actor, head.authority_basis_id, head.authority_decision_reference, key, command,
                          authority_target_fingerprint({"origin": str(head.event_uuid), "boundary": head.deadline}), now)
            if head.action == "WITHOUT_COORDINATOR":
                transition(alias, lo, cause, "K2_DEACTIVATE", "DORMANT",
                           head.deadline + duration(lo, 2, head.deadline))
            else:
                transition(alias, lo, cause, "K2_DISSOLVE", "CLOSED")
            count += 1
            now = database_now(alias)


def restore(alias, lo, cause, assessment_uuid):
    transition(alias, lo, replace(cause, evidence=f"coverage-assessment:{assessment_uuid}"),
               "K2_RESTORED", "ACTIVE")


def notice_before_office_intake(alias, member_pk, idempotency_key, *, assignment_uuid=None, membership_uuid=None,
                               action="TRANSITION_CONTEXTUAL_ROLE"):
    """Notice due intent for the member's own office request before fixing its receipt cut."""
    from core.models import CommandIntake, OrganismMembership
    if CommandIntake.objects.using(alias).filter(actor_id=member_pk, action=action,
                                                idempotency_key=idempotency_key).exists():
        return
    rows = OrganismMembership.objects.using(alias).filter(identity_id=member_pk)
    if assignment_uuid is not None:
        from core.models import ContextualRoleAssignment
        rows = rows.filter(pk__in=ContextualRoleAssignment.objects.using(alias).filter(
            assignment_uuid=assignment_uuid).values("membership_id"))
    else:
        rows = rows.filter(membership_uuid=membership_uuid)
    lo_id = rows.values_list("living_organism_id", flat=True).first()
    if lo_id is None:
        return
    with connections[alias].cursor() as cursor:
        cursor.execute("""
            SELECT EXISTS (SELECT FROM public.c2_phase_at(%s,statement_timestamp(),statement_timestamp()) h
                WHERE h.action IN ('WITHOUT_COORDINATOR','K2_DEACTIVATE') AND h.effective_until<=clock_timestamp())
            OR EXISTS (SELECT FROM public.c2_timed_input i WHERE i.lo_id=%s AND i.due<=clock_timestamp()
                AND NOT EXISTS (SELECT FROM core_coverageassessment a JOIN core_essentialcoveragecase c ON c.id=a.case_id
                 WHERE c.living_organism_id=i.lo_id AND a.evidence_reference='c2-boundary:'||i.source
                 AND a.effective_at=i.due))""", [lo_id, lo_id])
        pending, = cursor.fetchone()
    if pending:
        advance(alias, lo_id)
