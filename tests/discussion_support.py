"""DISC1 fixtures use existing registration, office, admission and consent routes."""
from uuid import uuid4

from core import models
from src.intevia.services import circle_commands
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
from tests import test_law_acts as fixtures


def independent_circle(lo, actor):
    member = fixtures.add_member(lo)
    circle = circle_commands.form(credential=actor.credential, organism_uuid=lo.organism_id,
        circle_uuid=uuid4(), kind="GENERIC", purpose="DISC1 independent Circle office", request_uuid=uuid4())
    membership = models.OrganismMembership.objects.get(identity=member, living_organism=lo)
    request = circle_commands.request_place(credential=member.credential, organism_uuid=lo.organism_id,
        circle_uuid=circle.circle_uuid, membership_uuid=membership.membership_uuid,
        request_uuid=uuid4(), consent="DISC1 office witness")
    place = circle_commands.admit(credential=actor.credential, organism_uuid=lo.organism_id,
        circle_uuid=circle.circle_uuid, request_uuid=request.request_uuid)
    authority = S015Authority(provider=ProductionApplicationAuthorityProvider())
    roles = OrganismRoleService(authority=authority, clock=support.database_now)
    assignment, nomination = uuid4(), uuid4()
    args = {"credential": actor.credential, "organism_uuid": lo.organism_id, "circle_uuid": circle.circle_uuid}
    roles.nominate_circle_coordinator(**args, participation_uuid=place.participation_uuid,
                                     assignment_uuid=assignment, request_uuid=nomination)
    at = support.database_now()
    OfficeAppointmentConsentService(authority=authority, clock=support.database_now).record_office_consent(
        RecordOfficeConsentCommand(credential=member.credential, request_reference="disc-consent:"+uuid4().hex,
            idempotency_key=uuid4().hex, occurred_at=at, effective_at=at, assignment_uuid=assignment,
            proposal_event_uuid=nomination, action="CONSENT"))
    roles.appoint_circle_coordinator(**args, assignment_uuid=assignment, request_uuid=uuid4())
    founder_place = models.CircleParticipation.objects.get(circle=circle, membership__identity=actor)
    circle_commands.leave(**args, participation_uuid=founder_place.participation_uuid,
                          request_uuid=uuid4(), confirmed=True)
    return circle, member
