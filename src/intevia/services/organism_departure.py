"""Explicit self-departure and its consequences under a single LO lock."""

from dataclasses import dataclass

from django.db import connections

from core.models import (
    ContextualRoleAssignment,
    LivingOrganismEvent,
    OrganismMembership,
    OrganismMembershipTransition,
)
from src.intevia.services.organism_application_authority import (
    _fold_state,
)
from src.intevia.services.organism_authority import authority_target_fingerprint
from src.intevia.services.organism_membership_service import S015MembershipEdgeRefused
from src.intevia.services.organism_relationship_event import append_relationship_event
from src.intevia.services.organism_role_service import OrganismRoleService


def refuse(reason):
    raise S015MembershipEdgeRefused(
        f"Leaving was refused. {reason} Membership, offices and Circle places are unchanged."
    )


def pending_elected_successor(alias, event_uuid, membership_id, kind, effective_at, received_at):
    """Consult the same future-mandate predicate as the departure storage guard."""
    with connections[alias].cursor() as cursor:
        cursor.execute("SELECT public.elect_pending_successor(%s,%s,%s,%s,%s)",
                       [event_uuid, membership_id, kind, effective_at, received_at])
        return cursor.fetchone()[0]


def coverage_assessment(alias, member, at, known):
    from src.intevia.services.organism_coverage import roster_at

    return roster_at(alias, member.living_organism, at, known)


def _check_coverage(alias, member, assessment, at, known):
    from src.intevia.services.organism_coverage import assess
    from src.intevia.services.organism_recovery import head_at, projected

    lo = member.living_organism
    current = assess(alias, lo, at, known)
    remaining = assess(alias, lo, at, known, excluding=member.pk)
    if assessment is None or assessment.roster_version != remaining.version or remaining.coordinator_result == "UNKNOWN":
        refuse("The effective essential roster could not be established. Check its governed record.")
    head = head_at(alias, lo, at, known)
    if head and projected(head, at) == "CLOSED":
        refuse("The organisation's dissolution boundary has passed. Check its closing record.")
    if remaining.coordinator_vacancy:
        if head and head.action in {"WITHOUT_COORDINATOR", "K2_DEACTIVATE"}:
            return False
        if current.coordinators == {member.pk} and head and head.resulting_state == "ACTIVE":
            return True
    return False


@dataclass
class Departure:
    roles: OrganismRoleService
    member: OrganismMembership
    actor: object
    decision: object
    command: object
    received_at: object
    fingerprint: str
    offices: list
    reserved_offices: list
    closing_head: object
    start_recovery: bool
    membership_event_uuid: object

    def end_offices(self):
        for assignment, qualified in self.offices:
            head = assignment.head_state_event
            if assignment.circle_id is not None:
                from uuid import uuid5
                from src.intevia.services.circle_commands import write_event

                write_event(self.roles._alias, assignment, self.actor, "END", "ENDED",
                            self.decision.authority_decision_reference, "lo-leave:" + str(self.membership_event_uuid),
                            uuid5(self.membership_event_uuid, str(assignment.assignment_uuid)), self.command.effective_at,
                            composed="CIRCLES:G2:U3", occurred=self.command.occurred_at, received=self.received_at)
                continue
            self.roles._write_event(
                assignment=assignment,
                actor=self.actor,
                sequence=head.sequence + 1,
                predecessor=head,
                event_action="END",
                effective_until=None,
                effective_until_state="NOT_ESTABLISHED",
                decision=qualified,
                basis_id=self.member.living_organism.founding_authority_basis_id,
                request_reference=self.command.request_reference,
                idempotency_key=self.command.idempotency_key,
                payload_fingerprint=self.fingerprint,
                occurred_at=self.command.occurred_at,
                effective_at=self.command.effective_at,
                received_at=self.received_at,
                prior_state="ACTIVE",
                resulting_state="ENDED",
            )
            with connections[self.roles._alias].cursor() as cursor:
                cursor.execute(
                    "SELECT public.s015_refresh_governed_cache(%s,%s,%s,clock_timestamp())",
                    [
                        "core_contextualroleassignment",
                        assignment.pk,
                        self.command.effective_at,
                    ],
                )

    def finish(self, transition):
        if self.reserved_offices:
            with connections[self.roles._alias].cursor() as cursor:
                for office in self.reserved_offices:
                    cursor.execute("SELECT public.elect_cancel_pending_office(%s,%s)",
                                   [office.pk, transition.event_uuid])

        from src.intevia.services.organism_conditions import end_departing_relationships

        end_departing_relationships(self, transition)
        from src.intevia.services.organism_coverage import Cause, record_coverage
        from src.intevia.services.organism_recovery import start

        cause = Cause(self.actor, transition.authority_basis_id, self.decision.authority_decision_reference,
                      f"membership-ending:{transition.event_uuid}", self.command, self.fingerprint, self.received_at)
        if self.closing_head is None:
            result, _ = record_coverage(self.roles._alias, self.member.living_organism, cause)
            if self.start_recovery:
                if not result.coordinator_vacancy:
                    refuse("Coverage changed while leaving. Check the current roster and try again.")
                start(self.roles._alias, self.member.living_organism, cause)
        if self.closing_head is not None:
            append_relationship_event(
                alias=self.roles._alias,
                anchor=self.member.living_organism,
                head=self.closing_head,
                action="DISSOLVE",
                state="CLOSED",
                actor=self.actor,
                basis_id=transition.authority_basis_id,
                decision_reference=self.decision.authority_decision_reference,
                evidence=f"membership-ending:{transition.event_uuid}",
                command=self.command,
                fingerprint=self.fingerprint,
                received_at=self.received_at,
                composing_rule="C0:O-16:v1.2",
            )


def prepare_departure(
    *,
    service,
    membership,
    actor,
    actor_role_refs,
    decision,
    effective_at,
    occurred_at,
    received_at,
    command,
    fingerprint,
    event_uuid,
    roster,
):
    alias = service._alias
    lo = membership.living_organism
    if effective_at > received_at:
        refuse(
            "A future departure cannot yet be executed. Confirm a fresh request when it is due."
        )
    with connections[alias].cursor() as cursor:
        cursor.execute("SELECT clock_timestamp()")
        (known,) = cursor.fetchone()
    if lo.state not in {"ACTIVE", "DORMANT", "RESTRICTED_CONTINUITY"} or lo.platform_root:
        refuse(
            "This organisation cannot use ordinary self-departure in its current state."
        )
    from src.intevia.services.organism_membership_periods import period_at

    original = period_at(membership.pk, effective_at, received_at, alias)
    if original is None or period_at(membership.pk, known, known, alias) != original:
        refuse(
            "The membership period has changed since the request. Check the current period and confirm a fresh departure."
        )
    if membership.head_transition.recorded_at > received_at:
        refuse(
            "Membership changed after receipt. Check its current period and submit a fresh request."
        )
    heads = (
        ContextualRoleAssignment.objects.using(alias)
        .select_for_update(of=("self",))
        .filter(
            membership=membership,
        )
        .select_related("role_definition", "head_state_event")
        .order_by("pk")
    )
    offices = []
    reserved_offices = []
    for assignment in heads:
        head = assignment.head_state_event
        if head is not None and head.recorded_at > received_at:
            refuse(
                "An office changed after this request was received. Check the office history and confirm a fresh departure."
            )
        if (head is not None and head.action == "ASSIGN" and head.effective_at > received_at
                and (head.evidence_reference or "").startswith("elect-result:")
                and pending_elected_successor(alias, head.event_uuid, membership.pk, "OFFICE", effective_at, received_at)):
            reserved_offices.append(assignment)
            continue
        if head is None or head.effective_at > effective_at:
            refuse(
                "An office change starts after the requested date or lacks a head. Check the leaving date."
            )
        if assignment.circle_id is not None and head.resulting_state != "ENDED":
            if (effective_at, occurred_at, received_at) < (head.effective_at, head.occurred_at, head.received_at):
                refuse("A later Circle office change is recorded. Resolve the timing before leaving.")
            offices.append(assignment)
            continue
        if head.resulting_state in {"ENDED", "PROPOSED"}:
            continue
        if (
            head.resulting_state != "ACTIVE"
            or head.received_at > received_at
            or head.recorded_at > received_at
            or assignment.role_definition.scope != "LIVING_ORGANISM"
            or assignment.role_definition.code.startswith("INTEVIA_")
            or (effective_at, occurred_at, received_at)
            < (head.effective_at, head.occurred_at, head.received_at)
        ):
            refuse(
                "An office cannot be ended under this request. Check the office history and submit a fresh request."
            )
        offices.append(assignment)

    other_ids = list(
        OrganismMembership.objects.using(alias)
        .filter(living_organism=lo)
        .exclude(pk=membership.pk)
        .values_list("pk", flat=True)
    )

    def has_continuing_member(at, known_at):
        return any(
            _fold_state(alias, "core_organismmembership", pk, at, known_at)
            in {"ACTIVE", "PROBATIONARY"}
            for pk in other_ids
        )

    dissolution_refusal = "A later organisation or membership event prevents dissolution on this date. Check the leaving date."
    continuing = has_continuing_member(effective_at, received_at)
    if continuing != has_continuing_member(known, known):
        refuse(dissolution_refusal)
    closing_head = None
    opening = False
    if continuing:
        current_roster = coverage_assessment(alias, membership, known, known)
        if roster is None or current_roster is None or roster.pk != current_roster.pk:
            refuse(
                "The essential roster is missing or changed since receipt. Check coverage and submit a fresh request."
            )
        opening = _check_coverage(alias, membership, roster, effective_at, received_at)
        if opening != _check_coverage(alias, membership, current_roster, known, known):
            refuse("Office coverage changed after receipt. Check the current roster and confirm again.")
    else:
        closing_head = (
            LivingOrganismEvent.objects.using(alias)
            .filter(living_organism=lo)
            .order_by("-sequence")
            .first()
        )
        if (
            not closing_head
            or closing_head.effective_at > effective_at
            or OrganismMembershipTransition.objects.using(alias)
            .filter(
                membership__living_organism=lo,
                effective_at__gt=effective_at,
            )
            .exists()
        ):
            refuse(dissolution_refusal)

    roles = OrganismRoleService(
        authority=service._authority, clock=service._clock, database_alias=alias
    )
    qualified = []
    for assignment in offices:
        if assignment.circle_id is not None:
            qualified.append((assignment, decision))
            continue
        target = {
            "action": roles._action.value,
            "assignment_uuid": str(assignment.assignment_uuid),
            "event_action": "END",
            "resulting_state": "ENDED",
            "living_organism_id": str(lo.organism_id),
            "departure_decision": decision.authority_decision_reference,
            "departure_event": str(event_uuid),
        }
        office_decision = roles._evaluate_authority(
            actor=actor,
            actor_membership=membership,
            actor_role_refs=actor_role_refs,
            request_reference=command.request_reference,
            idempotency_key=command.idempotency_key,
            target_fingerprint=authority_target_fingerprint(target),
            target=target,
            received_at=received_at,
            effective_at=effective_at,
            living_organism_id=lo.organism_id,
            assignment_uuid=assignment.assignment_uuid,
        )
        qualified.append((assignment, office_decision))
    return Departure(
        roles,
        membership,
        actor,
        decision,
        command,
        received_at,
        fingerprint,
        qualified,
        reserved_offices,
        closing_head,
        opening,
        event_uuid,
    )
