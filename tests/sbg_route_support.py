from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from django.db import connection, transaction

from core.models import (
    AuthorityDecision,
    CircleAdmissionDecision,
    CircleJoinRequest,
    CircleParticipation,
    CircleParticipationEnd,
    Circle,
    CircleStateEvent,
    GovernedRule,
    Identity,
    LOBirthingSnapshot,
    LivingOrganism,
    LivingOrganismEvent,
    OrganismCommandReceipt,
    OrganismMembership,
    OrganismMembershipTransition,
)
from src.intevia.services.organism_governance_service import (
    AdoptGoverningInstrumentCommand,
    CreateGovernanceCircleCommand,
    GovernanceActResult,
    GovernanceSeatingResult,
    OrganismGovernanceService,
    SBGGovernanceRefused,
    SeatFoundingGovernanceCircleCommand,
)
from src.intevia.services.organism_authority import S015AuthorityDenied
from src.intevia.services.organism_formation_service import (
    PhaseAdvanceCommand,
    PhaseAdvanceResult,
)
from tests.sb2_route_support import (
    BirthingRoute,
    database_now,
    prepare_route,
    route_test_now,
)
from tests.s015_0021_support import append_event, refresh


@dataclass(slots=True)
class SBGGovernanceRoute:
    route: BirthingRoute
    service: OrganismGovernanceService
    rule_uuid: UUID
    circle_uuid: UUID
    adoption: GovernanceActResult
    circle_creation: GovernanceActResult
    seating: GovernanceSeatingResult | None

    @property
    def founder(self) -> Identity:
        return self.route.founder

    @property
    def organism(self) -> LivingOrganism:
        return self.route.organism


def governance_arrangement_body(
    *,
    seat_id: str = "founder",
    admission_route: str = "APPOINTMENT",
    deciding_body: str = "office:LO_COORDINATOR",
    effective_from: datetime,
) -> dict[str, object]:
    return {
        "seats": [
            {
                "seat_id": seat_id,
                "eligibility": "LO_MEMBER",
                "admission_route": admission_route,
                "deciding_body": deciding_body,
                "term": None,
                "vacancy_rule": "BY_SEAT_ROUTE",
                "effective_from": effective_from.isoformat(),
            }
        ],
        "officer_roles": [],
        "election_schedule": None,
    }


def adopt_governance_arrangement(
    route: BirthingRoute,
    service: OrganismGovernanceService,
    *,
    tag: str,
    body: dict[str, object],
    rule_uuid: UUID | None = None,
    version: int = 1,
    effective_from: datetime | None = None,
) -> GovernanceActResult:
    effective_at = route_test_now(route)
    effective_from = effective_at if effective_from is None else effective_from
    return service.adopt_governing_instrument(AdoptGoverningInstrumentCommand(
        credential=route.founder.credential,
        living_organism_uuid=route.organism.organism_id,
        rule_uuid=rule_uuid or uuid4(),
        version=version,
        body=body,
        effective_from=effective_from,
        request_reference=f"sbg:adopt:{tag}",
        idempotency_key=f"sbg:adopt:key:{tag}",
        effective_at=effective_at,
    ))


def create_governance_circle(
    route: BirthingRoute,
    service: OrganismGovernanceService,
    *,
    tag: str,
) -> GovernanceActResult:
    effective_at = route_test_now(route)
    return service.create_governance_circle(CreateGovernanceCircleCommand(
        credential=route.founder.credential,
        living_organism_uuid=route.organism.organism_id,
        request_reference=f"sbg:create-circle:{tag}",
        idempotency_key=f"sbg:create-circle:key:{tag}",
        effective_at=effective_at,
    ))


def seat_governance_founder(
    route: BirthingRoute,
    service: OrganismGovernanceService,
    *,
    tag: str,
    circle_uuid: UUID,
    rule_uuid: UUID,
    seat_id: str = "founder",
) -> GovernanceSeatingResult:
    effective_at = route_test_now(route)
    return service.seat_governance_circle(SeatFoundingGovernanceCircleCommand(
        credential=route.founder.credential,
        living_organism_uuid=route.organism.organism_id,
        circle_uuid=circle_uuid,
        seat_rule_uuid=rule_uuid,
        seat_id=seat_id,
        request_reference=f"sbg:seat:{tag}",
        idempotency_key=f"sbg:seat:key:{tag}",
        effective_at=effective_at,
    ))


def advance_to_birthing(route: BirthingRoute, *, tag: str) -> PhaseAdvanceResult:
    effective_at = route_test_now(route)
    return route.phase_service.advance_phase(PhaseAdvanceCommand(
        credential=route.founder.credential,
        living_organism_uuid=route.organism.organism_id,
        from_phase="GERMINATING",
        to_phase="BIRTHING",
        request_reference=f"sbg:phase:{tag}",
        idempotency_key=f"sbg:phase:key:{tag}",
        effective_at=effective_at,
    ))


def prepare_governance_route(
    tag: str | None = None,
    *,
    governance_enabled: bool = True,
    seat_founder: bool = True,
    body: dict[str, object] | None = None,
) -> SBGGovernanceRoute:
    route = prepare_route(
        tag=tag,
        governance_enabled=governance_enabled,
        to_birthing_phase=False,
    )
    service = OrganismGovernanceService(clock=database_now)
    rule_uuid = uuid4()
    arrangement = body or governance_arrangement_body(
        effective_from=route_test_now(route),
    )
    try:
        adoption = adopt_governance_arrangement(
            route,
            service,
            tag=route.tag,
            body=arrangement,
            rule_uuid=rule_uuid,
        )
        circle_creation = create_governance_circle(route, service, tag=route.tag)
        circle_uuid = circle_creation.record_uuid
        seating = (
            seat_governance_founder(
                route,
                service,
                tag=route.tag,
                circle_uuid=circle_uuid,
                rule_uuid=rule_uuid,
            )
            if seat_founder
            else None
        )
    except (S015AuthorityDenied, SBGGovernanceRefused) as exc:
        raise AssertionError(
            f"Unexpected governance route setup refusal: "
            f"refusal_code={exc.refusal_code.value}; message={exc}"
        ) from exc
    return SBGGovernanceRoute(
        route=route,
        service=service,
        rule_uuid=rule_uuid,
        circle_uuid=circle_uuid,
        adoption=adoption,
        circle_creation=circle_creation,
        seating=seating,
    )


def governance_route_state(route: SBGGovernanceRoute) -> dict[str, int]:
    organism = route.organism
    return {
        "authority_decisions": AuthorityDecision.objects.filter(actor=route.founder).count(),
        "receipts": OrganismCommandReceipt.objects.filter(actor=route.founder).count(),
        "rules": GovernedRule.objects.filter(
            scope_kind="LIVING_ORGANISM",
            scope_organism=organism,
        ).count(),
        "living_organism_events": LivingOrganismEvent.objects.filter(
            living_organism=organism,
        ).count(),
        "governance_circles": Circle.objects.filter(
            parent_organism=organism,
            kind="GOVERNANCE",
        ).count(),
        "circle_events": CircleStateEvent.objects.filter(
            circle__parent_organism=organism,
        ).count(),
        "join_requests": CircleJoinRequest.objects.filter(
            circle__parent_organism=organism,
        ).count(),
        "admission_decisions": CircleAdmissionDecision.objects.filter(
            request__circle__parent_organism=organism,
        ).count(),
        "participations": CircleParticipation.objects.filter(
            circle__parent_organism=organism,
        ).count(),
        "participation_endings": CircleParticipationEnd.objects.filter(
            participation__circle__parent_organism=organism,
        ).count(),
        "birthing_snapshots": LOBirthingSnapshot.objects.filter(
            living_organism=organism,
        ).count(),
    }


def end_membership_with_participations(
    membership: OrganismMembership,
    effective_at: datetime,
    tag: str,
) -> tuple[OrganismMembershipTransition, list[CircleParticipationEnd]]:
    membership.refresh_from_db()
    endings = []
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, sequence, authority_basis_id
              FROM public.core_organismmembershiptransition
             WHERE membership_id = %s
             ORDER BY sequence DESC
             LIMIT 1
            """,
            [membership.pk],
        )
        predecessor_id, sequence, basis_id = cursor.fetchone()
        received_at = database_now()
        transition_id, _ = append_event(
            cursor,
            "core_organismmembershiptransition",
            membership.pk,
            sequence + 1,
            "LEAVE",
            membership.state,
            "ENDED",
            membership.identity_id,
            basis_id,
            f"test-sbg-end:{tag}",
            pred_id=predecessor_id,
            eff=effective_at,
            occ=effective_at,
            rec=received_at,
            extra={"reason_class": "TEST_ONLY_REASON_LEAVE"},
        )
        transition = OrganismMembershipTransition.objects.get(pk=transition_id)
        refresh(cursor, "core_organismmembership", membership.pk, effective_at)

        rule = GovernedRule.objects.get(
            scope_kind="PLATFORM",
            code="U3_MEMBERSHIP_END_ENDS_CIRCLE_PARTICIPATION",
            version=1,
        )
        participations = CircleParticipation.objects.select_for_update().filter(
            membership=membership,
            effective_at__lte=effective_at,
        ).order_by("pk")
        for participation in participations:
            if CircleParticipationEnd.objects.filter(participation=participation).exists():
                continue
            endings.append(CircleParticipationEnd.objects.create(
                participation=participation,
                actor_state="NONE",
                actor=None,
                actor_access_epoch=None,
                actor_capacity="NA_NO_PARTY",
                entry_mode="COMPOSED",
                enterer_identity=membership.identity,
                composing_rule_state="REFERENCE",
                composing_rule=rule,
                composing_rule_reference=f"rule:{rule.rule_uuid}@{rule.version}",
                composer_state="REFERENCE",
                composer_identity=membership.identity,
                composed_from_event_uuid=transition.event_uuid,
                occurred_at=effective_at,
                effective_at=effective_at,
                received_at=received_at,
            ))
    return transition, endings
