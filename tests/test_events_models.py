from datetime import datetime, timezone

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import (
    Event,
    EventEvidenceReference,
    EventParticipation,
    EventTransition,
    Profile,
    ProfileRole,
    Role,
)
from src.intevia.services.contribution_authority import ContributionAuthority
from src.intevia.services.event_service import EventService


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


class Capability:
    def authorise(self, *, identity, action, target, timestamp):
        return f"authority:{identity.pk}:{action}"


class EventModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="event-model-owner")
        self.profile = Profile.objects.create(user=self.user)
        role = Role.objects.create(name="Event model participant")
        ProfileRole.objects.create(profile=self.profile, role=role)
        self.service = EventService(
            authority=ContributionAuthority(Capability())
        )

    def test_four_event_models_remain_distinct(self):
        self.assertEqual(
            len({Event, EventTransition, EventParticipation, EventEvidenceReference}),
            4,
        )

    def test_transition_is_append_only(self):
        event = self.service.create_event(
            identity=self.user,
            event_id="event:model",
            title="Model event",
            evidence_reference="evidence:event-model",
            occurred_at=NOW,
        )
        transition = event.transitions.get()
        transition.command = "changed"
        with self.assertRaises(ValidationError):
            transition.save()
