from datetime import datetime, timezone

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import (
    Contribution,
    ContributionDecision,
    ContributionTransition,
    ContributionVersion,
    EvidenceReference,
    Identity,
    ProfileRole,
    Role,
)
from src.intevia.services.contribution_authority import ContributionAuthority
from src.intevia.services.contribution_service import ContributionService


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


class Capability:
    def authorise(self, *, identity, action, target, timestamp):
        return f"authority:{identity.pk}:{action}"


class ContributionModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="model-contributor")
        self.profile = Identity.objects.create(credential=self.user, access_state=Identity.AccessState.ACTIVE)
        role = Role.objects.create(name="Model participant")
        ProfileRole.objects.create(identity=self.profile, role=role)
        self.service = ContributionService(
            authority=ContributionAuthority(Capability())
        )

    def test_five_canonical_models_remain_distinct(self):
        self.assertEqual(
            len(
                {
                    Contribution,
                    ContributionVersion,
                    ContributionTransition,
                    ContributionDecision,
                    EvidenceReference,
                }
            ),
            5,
        )

    def test_transition_is_append_only_and_contains_no_content(self):
        contribution = self.service.create_contribution(
            identity=self.user,
            contribution_id="contribution:model",
            content="protected value",
            occurred_at=NOW,
        )
        transition = contribution.transitions.get()
        transition.command = "changed"
        with self.assertRaises(ValidationError):
            transition.save()
        self.assertFalse(hasattr(transition, "content"))
