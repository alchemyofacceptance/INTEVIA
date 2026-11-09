"""Real seat instruments, requests, decisions and office consent for DISC3."""

from uuid import uuid4

from django.db import connection, transaction

from core import models
from core.organism_contract import OrganismAction
from src.intevia.services import organism_application_service as gateway
from src.intevia.services import organism_governance_service as governance
from src.intevia.services.organism_application_authority import (
    ProductionApplicationAuthorityProvider,
)
from src.intevia.services.organism_authority import S015Authority
from src.intevia.services.organism_office_consent_service import (
    OfficeAppointmentConsentService,
    RecordOfficeConsentCommand,
)
from src.intevia.services.organism_role_service import OrganismRoleService
from tests import s015_0021_support as support
from tests import test_c2_determinations as findings
from tests import test_circle_operations as operations
from tests import test_law_acts as law


def authority():
    return S015Authority(provider=ProductionApplicationAuthorityProvider())


def command_args(lo, actor):
    key = uuid4().hex
    return {
        "credential": actor.credential,
        "living_organism_uuid": lo.organism_id,
        "request_reference": "disc3:" + key,
        "idempotency_key": key,
        "effective_at": support.database_now(),
    }


def form_board(lo, founder, size, elected=(), external=(), *, seat_founder=True):
    service = governance.OrganismGovernanceService(
        authority=authority(), clock=support.database_now
    )
    adoption_args = command_args(lo, founder)
    at = adoption_args["effective_at"]
    rule = uuid4()
    seats = [
        {
            "seat_id": f"trustee-{number}",
            "eligibility": "LO_MEMBER",
            "admission_route": "ELECTION" if number in elected else "APPOINTMENT",
            "deciding_body": "body:LO_MEMBERSHIP" if number in elected else "office:LO_COORDINATOR",
            "term": None,
            "vacancy_rule": "BY_SEAT_ROUTE",
            "effective_from": at.isoformat(),
        }
        for number in range(size)
    ]
    for number in external:
        seats[number]["deciding_body"] = "external:Trustee Appointments Panel"
    service.adopt_governing_instrument(
        governance.AdoptGoverningInstrumentCommand(
            **adoption_args,
            rule_uuid=rule,
            version=1,
            effective_from=at,
            body={"seats": seats, "officer_roles": [], "election_schedule": None},
        )
    )
    created = service.create_governance_circle(
        governance.CreateGovernanceCircleCommand(**command_args(lo, founder))
    )
    if not seat_founder:
        return
    service.seat_governance_circle(
        governance.SeatFoundingGovernanceCircleCommand(
            **command_args(lo, founder),
            circle_uuid=created.record_uuid,
            seat_rule_uuid=rule,
            seat_id=seats[0]["seat_id"],
        )
    )


def seat_trustee(lo, founder, circle, seat_number):
    service = governance.OrganismGovernanceService(
        authority=authority(), clock=support.database_now
    )
    rule = models.CircleParticipation.objects.get(
        circle=circle, membership__identity=founder
    ).seat_rule_uuid
    member = law.add_member(lo)
    request = service.request_governance_seat(
        governance.RequestGovernanceSeatCommand(
            **command_args(lo, member),
            circle_uuid=circle.circle_uuid,
            seat_rule_uuid=rule,
            seat_id=f"trustee-{seat_number}",
        )
    )
    service.decide_governance_seat(
        governance.DecideGovernanceSeatCommand(
            **command_args(lo, founder),
            request_uuid=request.request_uuid,
            outcome="ACCEPT",
        )
    )
    return member


def appoint_board_coordinator(lo, circle, founder, trustee):
    """Use the same independently scoped mandate/standing evidence as the non-generic office route."""
    fixture = operations.CircleOperationsTests()
    fixture.lo, fixture.actor, fixture.subject = (
        lo,
        founder,
        lo.founding_authority_basis,
    )
    fixture.instrument = findings.ScopedFindingTests()
    with transaction.atomic(), connection.cursor() as cur:
        fixture.instrument.verifier = support.seed_identity(cur, uuid4().hex)
        fixture.instrument.issuer = support.seed_identity(cur, uuid4().hex)
    place = models.CircleParticipation.objects.get(
        circle=circle, membership__identity=trustee
    )
    assignment, nomination = uuid4(), uuid4()
    fixture.grant(
        circle.circle_uuid,
        f"NOMINATE:{assignment}:place:{place.participation_uuid}",
        founder,
        trustee,
    )
    fixture.grant(circle.circle_uuid, f"APPOINT:{assignment}:NONE", founder, trustee)
    fixture.grant(assignment, "COUNT_OFFICE", trustee, trustee)
    roles = OrganismRoleService(authority=authority(), clock=support.database_now)
    scope = {
        "credential": founder.credential,
        "organism_uuid": lo.organism_id,
        "circle_uuid": circle.circle_uuid,
    }
    roles.nominate_circle_coordinator(
        **scope,
        participation_uuid=place.participation_uuid,
        assignment_uuid=assignment,
        request_uuid=nomination,
    )
    at = support.database_now()
    OfficeAppointmentConsentService(
        authority=authority(), clock=support.database_now
    ).record_office_consent(
        RecordOfficeConsentCommand(
            credential=trustee.credential,
            request_reference="disc3-consent:" + uuid4().hex,
            idempotency_key=uuid4().hex,
            occurred_at=at,
            effective_at=at,
            assignment_uuid=assignment,
            proposal_event_uuid=nomination,
            action="CONSENT",
        )
    )
    roles.appoint_circle_coordinator(
        **scope, assignment_uuid=assignment, request_uuid=uuid4()
    )


def turn_off_birth_choice(lo, founder):
    service = gateway.OrganismApplicationService(
        authority=authority(), clock=support.database_now
    )
    fields = {
        "cq_active": False,
        "governance_enabled": False,
        "inherit_portal_cq_settings": False,
        "require_cq_to_change_lo_settings": False,
        "require_cq_to_change_application_question": False,
    }

    def arguments():
        at = support.database_now()
        key = uuid4().hex
        return {
            "credential": founder.credential,
            "living_organism_id": lo.organism_id,
            "action": OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
            "occurred_at": at,
            "effective_at": at,
            "request_reference": "disc3-configuration:" + key,
            "idempotency_key": key,
        }

    draft = service.save_gateway_draft(
        gateway.GatewayDraftCommand(**arguments(), fields=fields)
    )
    service.effect_gateway_version(
        gateway.EffectGatewayVersionCommand(
            **arguments(), version_uuid=draft.version_uuid
        )
    )


def ordinary(size, *, board=False):
    lo, founder = law.ordinary_birth(
        offering_choice="FREE", board_seats=size if board else 0
    )
    scope = {"credential": founder.credential, "organism_uuid": lo.organism_id}
    circle = (
        models.Circle.objects.get(parent_organism=lo, kind="GOVERNANCE")
        if board
        else None
    )
    people = [founder]
    for number in range(1, size):
        people.append(
            seat_trustee(lo, founder, circle, number) if board else law.add_member(lo)
        )
    return lo, scope, people, circle
