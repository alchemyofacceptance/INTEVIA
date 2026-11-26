"""C1a's internal rejoin qualification; no authority grant or public reader.

The caller holds the registered LO lock and applicant lock, has qualified a
fresh application/acceptance, and owns the transaction. Identity and the unique
identity/LO anchor stay unchanged. An application period is identified by the event UUID
of its fresh PROPOSED transition. A founding or legacy period without a
proposal is identified by the UUID of its first qualifying membership event.
"""

from datetime import datetime

from django.db import connections
from django.db.models import Q

from core.models import (
    ContextualRoleAssignment,
    MembershipCondition,
    OrganismMembership,
    OrganismMembershipTransition,
)


class MembershipPeriodUnavailable(ValueError):
    """The bounded C1a route cannot safely open the requested new period."""


def rejoin_predecessor(
    membership: OrganismMembership,
    *,
    application_effective_at: datetime,
    application_received_at: datetime,
    database_alias: str,
) -> OrganismMembershipTransition:
    """Require a known ended head before a genuinely later application.

    Called both before storing a rejoin application and again at acceptance.
    The acceptance call uses the application's original receipt time, so an
    application predating the ending cannot be repurposed as a new period.
    Current caches alone are not proof of a qualifying ended history.

    Ambiguous legacy condition origins and unfinished offices refuse. An old
    proposal remains historical when it was recorded before the period ending.
    """
    head = OrganismMembershipTransition.objects.using(database_alias).filter(
        pk=membership.head_transition_id, membership=membership,
    ).first()
    if head is None or head.resulting_state != "ENDED":
        raise MembershipPeriodUnavailable(
            "Rejoining was not recorded: this organisation's membership has no ended "
            "head period. Membership is unchanged; use the current membership or "
            "complete its authorised departure first. The received request remains recorded."
        )
    if (
        application_effective_at <= head.effective_at
        or head.effective_at > application_received_at
        or application_received_at < head.received_at
        or application_received_at < head.recorded_at
    ):
        raise MembershipPeriodUnavailable(
            "Rejoining was not recorded: a new application must take effect after the "
            "previous membership ends and be received after that ending is recorded. "
            "Membership is unchanged. After the ending takes effect, submit a new "
            "application with a new request key; this received request remains recorded."
        )
    conditions = MembershipCondition.objects.using(database_alias).filter(
        Q(subject_membership=membership) | Q(mentor_membership=membership),
    )
    if any(not condition_bound(row, database_alias) for row in conditions):
        raise MembershipPeriodUnavailable(
            "Rejoining was not recorded: this membership has condition or mentor history "
            "with no provable original period. Membership and condition "
            "history are unchanged. There is no rejoin route for this case yet; keep the "
            "original history for governed reconciliation. The received request remains recorded."
        )
    if ContextualRoleAssignment.objects.using(database_alias).filter(
        membership=membership,
    ).exclude(
        Q(head_state_event__resulting_state="ENDED") |
        Q(head_state_event__sequence=1, head_state_event__action="PROPOSE",
          head_state_event__resulting_state="PROPOSED", head_state_event__recorded_at__lt=head.recorded_at,
          head_state_event__effective_at__lte=head.effective_at)
    ).exists():
        raise MembershipPeriodUnavailable(
            "Rejoining was not recorded: this membership has an office assignment or "
            "nomination without a recorded ending. Membership and office history are "
            "unchanged. Complete its authorised ending where available; otherwise this "
            "case needs governed reconciliation. The received request remains recorded."
        )
    return head


def period_at(membership_id, at, known, alias):
    """Return the opening event UUID of a membership period.

    Calls public.c1b_period_at on alias with the membership's
    primary key, effective cut at and knowledge cut known. A
    NULL SQL input returns None.
    Count transitions effective by at and received and recorded
    by known. Pick the head by latest effective, occurred,
    received, recorded, sequence and event UUID, in that order.

    The head must be ACTIVE or PROBATIONARY. Pick the highest
    eligible opening sequence at or below the head: sequence
    one in PROPOSED, ACTIVE or PROBATIONARY, or a transition
    from ENDED to PROPOSED. No eligible ENDED may lie from the
    opening through the head in sequence order. Return None if
    no period qualifies. Takes no explicit row lock.
    """
    with connections[alias].cursor() as cursor:
        cursor.execute("SELECT public.c1b_period_at(%s,%s,%s)", [membership_id, at, known])
        return cursor.fetchone()[0]


def bound_periods(condition, alias):
    """Return the period starts a condition was recorded against.

    Reads public.c1b_condition_period for condition.pk on alias:
    (subject_start, mentor_start), each as period_at gives it at
    the condition's first state event's effective_at, with that
    event's recorded_at as the knowledge cut. mentor_start is
    None when there is no mentor; both are None when there is no
    first state event; either is None when no period is found.
    Returns None when no row matches (an unsaved condition).
    Takes no lock.
    """
    with connections[alias].cursor() as cursor:
        cursor.execute("SELECT subject_start, mentor_start FROM public.c1b_condition_period WHERE condition_id=%s",
                       [condition.pk])
        return cursor.fetchone()


def condition_bound(condition, alias):
    """Report whether a condition's periods can be proven.

    True when bound_periods gives a subject period start and,
    if the condition names a mentor, a mentor period start too.
    When no row matches, unpacking None raises TypeError.
    """
    subject, mentor = bound_periods(condition, alias)
    return subject is not None and (condition.mentor_membership_id is None or mentor is not None)
