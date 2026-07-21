from datetime import datetime, timezone

from django.contrib.auth.models import User
from django.test import TestCase

from core.models import Identity, ProfileRole, Role, ServiceEventAssociation
from src.intevia.services.contribution_authority import (
    ContributionAuthority,
    NotAuthorised,
)
from src.intevia.services.event_service import EventService
from src.intevia.services.service_service import (
    GovernedService,
    InvalidServiceTransition,
)


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


class Capability:
    def __init__(self, denied=()):
        self.denied = set(denied)

    def authorise(self, *, identity, action, target, timestamp):
        if action in self.denied:
            return None
        return f"authority:{identity.pk}:{action}"


class ServiceEventAssociationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="service-event-owner")
        profile = Identity.objects.create(credential=self.user, access_state=Identity.AccessState.ACTIVE)
        role = Role.objects.create(name="Service Event participant")
        ProfileRole.objects.create(identity=profile, role=role)
        authority = ContributionAuthority(Capability())
        self.foundation = GovernedService(authority=authority)
        self.event = EventService(authority=authority).create_event(
            identity=self.user,
            event_id="event:service-enactment",
            title="Service enactment",
            evidence_reference="evidence:event-enactment",
            occurred_at=NOW,
        )
        self.service = self.foundation.create_service(
            identity=self.user,
            service_id="service:event",
            capability_purpose="Temporal expression",
            domain_intent="Support bounded enactment",
            evidence_reference="evidence:service-event",
            occurred_at=NOW,
        )
        self.foundation.transition_service(
            identity=self.user,
            service_id=self.service.service_id,
            command="publish_service",
            evidence_reference="evidence:publish-service-event",
            occurred_at=NOW,
        )
        self.service.refresh_from_db()

    def test_association_pins_event_without_transferring_lifecycle(self):
        event_state = self.event.state
        service_state = self.service.state
        association = self.foundation.associate_event(
            identity=self.user,
            service_version=self.service.current_version,
            event=self.event,
            evidence_reference="evidence:event-association",
            occurred_at=NOW,
        )

        self.event.refresh_from_db()
        self.service.refresh_from_db()
        self.assertEqual(association.event, self.event)
        self.assertEqual(self.event.state, event_state)
        self.assertEqual(self.service.state, service_state)
        self.assertEqual(association.delivery_evidence_references.count(), 0)

    def test_draft_and_denied_associations_persist_nothing(self):
        successor = self.foundation.create_successor_version(
            identity=self.user,
            service_id=self.service.service_id,
            capability_purpose="Draft successor",
            domain_intent="Draft intent",
            evidence_reference="evidence:draft-successor",
            occurred_at=NOW,
        )
        with self.assertRaises(InvalidServiceTransition):
            self.foundation.associate_event(
                identity=self.user,
                service_version=successor,
                event=self.event,
                evidence_reference="evidence:draft-association",
                occurred_at=NOW,
            )

        self.foundation.transition_service(
            identity=self.user,
            service_id=self.service.service_id,
            command="publish_service",
            evidence_reference="evidence:publish-successor",
            occurred_at=NOW,
        )
        denied = GovernedService(
            authority=ContributionAuthority(Capability({"associate_service_event"}))
        )
        with self.assertRaises(NotAuthorised):
            denied.associate_event(
                identity=self.user,
                service_version=successor,
                event=self.event,
                evidence_reference="evidence:denied-event-association",
                occurred_at=NOW,
            )
        self.assertEqual(ServiceEventAssociation.objects.count(), 0)
