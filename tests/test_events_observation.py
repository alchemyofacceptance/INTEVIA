from datetime import datetime, timezone

from django.contrib.auth.models import User
from django.test import TestCase

from core.models import Event, Identity, ProfileRole, Role
from src.intevia.observation.journal import (
    ObservationEntry,
    ObservationEventKind,
    ObservationJournal,
)
from src.intevia.services.contribution_authority import ContributionAuthority
from src.intevia.services.event_service import EventService


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


class Capability:
    def authorise(self, *, identity, action, target, timestamp):
        return f"authority:{identity.pk}:{action}"


class EventObservationTests(TestCase):
    def test_observation_is_derived_from_durable_transition(self):
        user = User.objects.create_user(username="event-observation")
        profile = Identity.objects.create(credential=user, access_state=Identity.AccessState.ACTIVE)
        role = Role.objects.create(name="Event observation participant")
        ProfileRole.objects.create(identity=profile, role=role)
        service = EventService(authority=ContributionAuthority(Capability()))
        event = service.create_event(
            identity=user,
            event_id="event:observation",
            title="Observation",
            evidence_reference="evidence:observation",
            occurred_at=NOW,
        )
        transition = service.transition_event(
            identity=user,
            event_id=event.event_id,
            command="publish_event",
            occurred_at=NOW,
        )
        journal = ObservationJournal()
        journal.append(
            ObservationEntry(
                event_kind=ObservationEventKind.EVENT_TRANSITION_RECORDED,
                occurred_at=transition.occurred_at,
                event_id=event.event_id,
                actor_ref=str(transition.actor_id),
                prior_state=transition.from_state,
                new_state=transition.to_state,
            )
        )
        stored = Event.objects.get(pk=event.pk)
        self.assertEqual(stored.state, Event.State.PUBLISHED)
        self.assertEqual(stored.transitions.count(), 2)
        self.assertEqual(len(journal.list_entries()), 1)
