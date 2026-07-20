from datetime import datetime, timezone

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import Contribution, Profile, ProfileRole, Role
from src.intevia.services.contribution_authority import ContributionAuthority
from src.intevia.services.contribution_service import (
    ContributionService,
    InvalidContributionTransition,
)


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


class Capability:
    def authorise(self, *, identity, action, target, timestamp):
        return f"authority:{identity.pk}:{action}"


class ContributionServiceTests(TestCase):
    def identity(self, name):
        user = User.objects.create_user(username=name)
        profile = Profile.objects.create(user=user)
        role, _ = Role.objects.get_or_create(name="Service participant")
        ProfileRole.objects.create(profile=profile, role=role)
        return user

    def setUp(self):
        self.contributor = self.identity("service-contributor")
        self.reviewer = self.identity("service-reviewer")
        self.service = ContributionService(
            authority=ContributionAuthority(Capability())
        )

    def test_complete_lifecycle_is_durable_and_attributed(self):
        contribution = self.service.create_contribution(
            identity=self.contributor,
            contribution_id=" contribution:service ",
            content="content",
            occurred_at=NOW,
        )
        self.service.submit_contribution(identity=self.contributor, contribution_id=contribution.contribution_id, occurred_at=NOW)
        self.service.begin_review(identity=self.reviewer, contribution_id=contribution.contribution_id, occurred_at=NOW)
        self.service.record_human_decision(
            identity=self.reviewer,
            contribution_id=contribution.contribution_id,
            decision_type="accepted",
            evidence_reference="evidence:service",
            occurred_at=NOW,
        )
        stored = Contribution.objects.get(contribution_id="contribution:service")
        self.assertEqual(stored.state, Contribution.State.ACCEPTED)
        self.assertEqual(stored.transitions.count(), 4)
        self.assertTrue(
            all(item.authority_reference for item in stored.transitions.all())
        )

    def test_decision_before_review_is_refused_without_partial_write(self):
        contribution = self.service.create_contribution(
            identity=self.contributor,
            contribution_id="contribution:premature",
            content="content",
            occurred_at=NOW,
        )
        self.service.submit_contribution(identity=self.contributor, contribution_id=contribution.contribution_id, occurred_at=NOW)
        with self.assertRaises(InvalidContributionTransition):
            self.service.record_human_decision(
                identity=self.reviewer,
                contribution_id=contribution.contribution_id,
                decision_type="accepted",
                evidence_reference="evidence:premature",
                occurred_at=NOW,
            )
        contribution.refresh_from_db()
        self.assertEqual(contribution.state, Contribution.State.SUBMITTED)
        self.assertEqual(contribution.decisions.count(), 0)

    def test_self_review_is_refused(self):
        contribution = self.service.create_contribution(
            identity=self.contributor,
            contribution_id="contribution:self-review",
            content="content",
            occurred_at=NOW,
        )
        self.service.submit_contribution(identity=self.contributor, contribution_id=contribution.contribution_id, occurred_at=NOW)
        self.service.begin_review(identity=self.contributor, contribution_id=contribution.contribution_id, occurred_at=NOW)
        with self.assertRaises(ValidationError):
            self.service.record_human_decision(
                identity=self.contributor,
                contribution_id=contribution.contribution_id,
                decision_type="accepted",
                evidence_reference="evidence:self-review",
                occurred_at=NOW,
            )
