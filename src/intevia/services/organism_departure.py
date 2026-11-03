"""Explicit self-departure and its consequences under a single LO lock."""

from dataclasses import dataclass

from django.db import connections
from django.db.models import Q

from core.models import (
    ContextualRoleAssignment,
    CoverageAssessment,
    EssentialCoverageRequirement,
    LivingOrganismEvent,
    OrganismMembership,
    OrganismMembershipTransition,
)
from src.intevia.services.organism_application_authority import (
    _assignment_term_in_effect,
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


def coverage_assessment(alias, member, at, known):
    return (
        CoverageAssessment.objects.using(alias)
        .filter(
            case__living_organism_id=member.living_organism_id,
            effective_at__lte=at,
            recorded_at__lte=known,
            received_at__lte=known,
        )
        .order_by(
            "-effective_at", "-occurred_at", "-received_at", "-recorded_at", "-sequence"
        )
        .first()
    )


def _check_coverage(alias, member, assessment, at, known):
    if assessment is None:
        refuse(
            "The effective essential roster could not be established. Coverage recovery awaits C2/C3."
        )
    requirements = (
        EssentialCoverageRequirement.objects.using(alias)
        .filter(
            living_organism_id=member.living_organism_id,
            roster_version=assessment.roster_version,
            effective_from__lte=at,
        )
        .filter(Q(effective_until__isnull=True) | Q(effective_until__gt=at))
    )
    if not requirements:
        refuse("The current roster has no effective coverage requirements. Coverage recovery awaits C2/C3.")
    for rule in requirements:
        occupants = set()
        alternatives = (
            ContextualRoleAssignment.objects.using(alias)
            .filter(
                membership__living_organism_id=member.living_organism_id,
                role_definition__scope=rule.scope,
                role_definition__code=rule.qualifying_role_code,
            )
            .exclude(membership=member)
        )
        for other in alternatives:
            if (
                _fold_state(alias, "core_contextualroleassignment", other.pk, at, known)
                == "ACTIVE"
                and _fold_state(
                    alias, "core_organismmembership", other.membership_id, at, known
                )
                == "ACTIVE"
                and _assignment_term_in_effect(alias, other.pk, at, known)
            ):
                occupants.add(other.membership_id)
        if len(occupants) < rule.minimum_active_occupants:
            refuse(
                f"The continuing organisation needs {rule.qualifying_role_code} coverage under roster "
                f"{rule.roster_version}. Arrange a qualified handover first where available. "
                "Last-Coordinator and recovery routes await C2/C3."
            )


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
    closing_head: object

    def end_offices(self):
        for assignment, qualified in self.offices:
            head = assignment.head_state_event
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
        from src.intevia.services.organism_conditions import end_departing_relationships

        end_departing_relationships(self, transition)
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
    if lo.state not in {"ACTIVE", "DORMANT"} or lo.platform_root:
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
    for assignment in heads:
        head = assignment.head_state_event
        if head is not None and head.recorded_at > received_at:
            refuse(
                "An office changed after this request was received. Check the office history and confirm a fresh departure."
            )
        if head is None or head.effective_at > effective_at:
            refuse(
                "An office change starts after the requested date or lacks a head. Check the leaving date."
            )
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
    if continuing:
        current_roster = coverage_assessment(alias, membership, known, known)
        if roster is None or current_roster is None or roster.pk != current_roster.pk:
            refuse(
                "The essential roster is missing or changed since receipt. Check coverage and submit a fresh request."
            )
        _check_coverage(alias, membership, roster, effective_at, received_at)
        _check_coverage(alias, membership, current_roster, known, known)
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
        closing_head,
    )
