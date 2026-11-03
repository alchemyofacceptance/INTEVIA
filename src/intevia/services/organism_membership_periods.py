"""C1a's internal rejoin qualification; no authority grant or public reader.

The caller holds the registered LO lock and applicant lock, has qualified a
fresh application/acceptance, and owns the transaction. Identity and the unique
identity/LO anchor stay unchanged. An application period is identified by the event UUID
of its fresh PROPOSED transition. A founding or legacy period without a
proposal is identified by the UUID of its first qualifying membership event.
"""

from datetime import datetime

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

    C1a conservatively refuses every old condition link (subject OR mentor)
    and any office whose recorded head is not ENDED, including nominations
    and future/suspended assignments. C1b must replace these explicit limits
    with period-bound condition/office rules; deleting history is not a remedy.
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
    if MembershipCondition.objects.using(database_alias).filter(
        Q(subject_membership=membership) | Q(mentor_membership=membership),
    ).exists():
        raise MembershipPeriodUnavailable(
            "Rejoining was not recorded: this membership has condition or mentor history "
            "whose period handling is not yet implemented in C1a. Membership and condition "
            "history are unchanged. There is no rejoin route for this case yet; keep the "
            "history for the remaining C1 work. The received request remains recorded."
        )
    if ContextualRoleAssignment.objects.using(database_alias).filter(
        membership=membership,
    ).exclude(head_state_event__resulting_state="ENDED").exists():
        raise MembershipPeriodUnavailable(
            "Rejoining was not recorded: this membership has an office assignment or "
            "nomination without a recorded ending. Membership and office history are "
            "unchanged. Complete its authorised ending where available; otherwise this "
            "case must wait for the remaining C1 work. The received request remains recorded."
        )
    return head
