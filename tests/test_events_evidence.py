from datetime import datetime, timezone

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import Event, EventEvidenceReference, Identity, ProfileRole, Role
from src.intevia.services.contribution_authority import ContributionAuthority
from src.intevia.services.event_service import EventService


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


class Capability:
    def authorise(self, *, identity, action, target, timestamp):
        return f"authority:{identity.pk}:{action}"


class EventEvidenceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="event-evidence")
        self.profile = Identity.objects.create(credential=self.user, access_state=Identity.AccessState.ACTIVE)
        role = Role.objects.create(name="Event evidence participant")
        ProfileRole.objects.create(identity=self.profile, role=role)
        self.service = EventService(
            authority=ContributionAuthority(Capability())
        )

    def test_creation_persists_event_transition_and_evidence_together(self):
        event = self.service.create_event(
            identity=self.user,
            event_id="event:evidence",
            title="Evidence",
            evidence_reference="evidence:event-creation",
            occurred_at=NOW,
        )
        transition = event.transitions.get()
        evidence = event.evidence_references.get()
        self.assertEqual(evidence.transition, transition)
        self.assertEqual(evidence.supplied_by, self.profile)
        self.assertEqual(evidence.authority_reference, transition.authority_reference)
        self.assertEqual(evidence.occurred_at, transition.occurred_at)

    def test_missing_creation_evidence_rolls_back_everything(self):
        with self.assertRaises(ValidationError):
            self.service.create_event(
                identity=self.user,
                event_id="event:no-evidence",
                title="No evidence",
                evidence_reference=" ",
                occurred_at=NOW,
            )
        self.assertFalse(Event.objects.filter(event_id="event:no-evidence").exists())
        self.assertEqual(EventEvidenceReference.objects.count(), 0)
