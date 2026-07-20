from datetime import datetime, timezone

from django.contrib.auth.models import User
from django.test import TestCase

from core.models import Profile, ProfileRole, Role
from src.intevia.observation.journal import (
    ObservationEntry,
    ObservationEventKind,
    ObservationJournal,
)
from src.intevia.services.contribution_authority import ContributionAuthority
from src.intevia.services.contribution_service import ContributionService


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


class Capability:
    def authorise(self, *, identity, action, target, timestamp):
        return f"authority:{identity.pk}:{action}"


class ObservationIntegrationTests(TestCase):
    def test_optional_observation_is_derived_from_durable_transition(self):
        user = User.objects.create_user(username="observation-contributor")
        profile = Profile.objects.create(user=user)
        role = Role.objects.create(name="Observation participant")
        ProfileRole.objects.create(profile=profile, role=role)
        service = ContributionService(authority=ContributionAuthority(Capability()))
        contribution = service.create_contribution(
            identity=user,
            contribution_id="contribution:observation",
            content="content",
            occurred_at=NOW,
        )
        transition = service.submit_contribution(
            identity=user,
            contribution_id=contribution.contribution_id,
            occurred_at=NOW,
        )
        journal = ObservationJournal()
        journal.append(
            ObservationEntry(
                event_kind=ObservationEventKind.CONTRIBUTION_TRANSITION_RECORDED,
                occurred_at=transition.occurred_at,
                contribution_id=contribution.contribution_id,
                actor_ref=str(transition.actor_id),
                prior_state=transition.from_state,
                new_state=transition.to_state,
            )
        )
        self.assertEqual(len(journal.list_entries()), 1)
        self.assertEqual(contribution.transitions.count(), 2)
