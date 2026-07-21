from datetime import datetime, timezone

from django.contrib.auth.models import User
from django.test import TestCase

from core.models import Event, Identity, ProfileRole, Role
from src.intevia.services.contribution_authority import ContributionAuthority
from src.intevia.services.event_service import EventService, InvalidEventTransition


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


class Capability:
    def authorise(self, *, identity, action, target, timestamp):
        return f"authority:{identity.pk}:{action}"


class EventLifecycleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="event-lifecycle")
        profile = Identity.objects.create(credential=self.user, access_state=Identity.AccessState.ACTIVE)
        role = Role.objects.create(name="Event lifecycle participant")
        ProfileRole.objects.create(identity=profile, role=role)
        self.service = EventService(
            authority=ContributionAuthority(Capability())
        )
        self.event = self.service.create_event(
            identity=self.user,
            event_id="event:lifecycle",
            title="Lifecycle",
            evidence_reference="evidence:lifecycle",
            occurred_at=NOW,
        )

    def test_complete_lifecycle_is_explicit_and_durable(self):
        for command, state in (
            ("publish_event", Event.State.PUBLISHED),
            ("activate_event", Event.State.ACTIVE),
            ("complete_event", Event.State.COMPLETED),
            ("archive_event", Event.State.ARCHIVED),
        ):
            self.service.transition_event(
                identity=self.user,
                event_id=self.event.event_id,
                command=command,
                occurred_at=NOW,
            )
            self.event.refresh_from_db()
            self.assertEqual(self.event.state, state)
        self.assertEqual(self.event.transitions.count(), 5)

    def test_invalid_transition_and_terminal_state_are_rejected(self):
        with self.assertRaises(InvalidEventTransition):
            self.service.transition_event(
                identity=self.user,
                event_id=self.event.event_id,
                command="complete_event",
                occurred_at=NOW,
            )
        self.event.refresh_from_db()
        self.assertEqual(self.event.state, Event.State.DRAFT)
        self.assertEqual(self.event.transitions.count(), 1)
