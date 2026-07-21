from datetime import datetime, timezone

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import (
    Identity,
    ProfileRole,
    Role,
    Service,
    ServiceDeliveryEvidenceReference,
)
from src.intevia.services.contribution_authority import (
    ContributionAuthority,
    NotAuthorised,
)
from src.intevia.services.event_service import EventService
from src.intevia.services.service_service import GovernedService


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


class Capability:
    def __init__(self, denied=()):
        self.denied = set(denied)

    def authorise(self, *, identity, action, target, timestamp):
        if action in self.denied:
            return None
        return f"authority:{identity.pk}:{action}"


class ServiceDeliveryEvidenceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="service-delivery-owner")
        profile = Identity.objects.create(credential=self.user, access_state=Identity.AccessState.ACTIVE)
        role = Role.objects.create(name="Service delivery participant")
        ProfileRole.objects.create(identity=profile, role=role)
        authority = ContributionAuthority(Capability())
        self.foundation = GovernedService(authority=authority)
        self.events = EventService(authority=authority)
        self.event = self.events.create_event(
            identity=self.user,
            event_id="event:delivery",
            title="Delivery occurrence",
            evidence_reference="evidence:event-delivery",
            occurred_at=NOW,
        )
        self.service = self.foundation.create_service(
            identity=self.user,
            service_id="service:delivery",
            capability_purpose="Evidence enactment",
            domain_intent="Support temporal capability",
            evidence_reference="evidence:service-delivery",
            occurred_at=NOW,
        )
        self.foundation.transition_service(
            identity=self.user,
            service_id=self.service.service_id,
            command="publish_service",
            evidence_reference="evidence:publish-service-delivery",
            occurred_at=NOW,
        )
        self.service.refresh_from_db()
        self.association = self.foundation.associate_event(
            identity=self.user,
            service_version=self.service.current_version,
            event=self.event,
            evidence_reference="evidence:delivery-association",
            occurred_at=NOW,
        )

    def complete_event(self):
        completed = None
        for command in ("publish_event", "activate_event", "complete_event"):
            completed = self.events.transition_event(
                identity=self.user,
                event_id=self.event.event_id,
                command=command,
                occurred_at=NOW,
            )
        return completed

    def test_completion_alone_is_not_delivery_evidence(self):
        completed = self.complete_event()
        self.assertEqual(ServiceDeliveryEvidenceReference.objects.count(), 0)

        evidence = self.foundation.record_delivery_evidence(
            identity=self.user,
            service_event_association=self.association,
            completed_event_transition=completed,
            evidence_reference="evidence:completed-delivery",
            occurred_at=NOW,
        )

        self.service.refresh_from_db()
        self.assertEqual(evidence.completed_event_transition, completed)
        self.assertEqual(self.service.state, Service.State.PUBLISHED)

    def test_non_completed_transition_and_denied_evidence_leave_no_record(self):
        published = self.events.transition_event(
            identity=self.user,
            event_id=self.event.event_id,
            command="publish_event",
            occurred_at=NOW,
        )
        with self.assertRaises(ValidationError):
            self.foundation.record_delivery_evidence(
                identity=self.user,
                service_event_association=self.association,
                completed_event_transition=published,
                evidence_reference="evidence:not-complete",
                occurred_at=NOW,
            )

        self.events.transition_event(
            identity=self.user,
            event_id=self.event.event_id,
            command="activate_event",
            occurred_at=NOW,
        )
        completed = self.events.transition_event(
            identity=self.user,
            event_id=self.event.event_id,
            command="complete_event",
            occurred_at=NOW,
        )
        denied = GovernedService(
            authority=ContributionAuthority(
                Capability({"record_service_delivery_evidence"})
            )
        )
        with self.assertRaises(NotAuthorised):
            denied.record_delivery_evidence(
                identity=self.user,
                service_event_association=self.association,
                completed_event_transition=completed,
                evidence_reference="evidence:denied-delivery",
                occurred_at=NOW,
            )
        self.assertEqual(ServiceDeliveryEvidenceReference.objects.count(), 0)