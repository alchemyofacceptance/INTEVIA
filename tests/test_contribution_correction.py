from datetime import datetime, timezone

from django.contrib.auth.models import User
from django.test import TestCase

from core.models import ContributionDecision, Identity, ProfileRole, Role
from src.intevia.services.contribution_authority import ContributionAuthority
from src.intevia.services.contribution_service import ContributionService


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


class Capability:
    def authorise(self, *, identity, action, target, timestamp):
        return f"authority:{identity.pk}:{action}"


class ContributionCorrectionTests(TestCase):
    def identity(self, name):
        user = User.objects.create_user(username=name)
        profile = Identity.objects.create(credential=user, access_state=Identity.AccessState.ACTIVE)
        role, _ = Role.objects.get_or_create(name="Correction participant")
        ProfileRole.objects.create(identity=profile, role=role)
        return user

    def test_correction_preserves_predecessor_and_requires_new_decision(self):
        contributor = self.identity("correction-contributor")
        reviewer = self.identity("correction-reviewer")
        service = ContributionService(
            authority=ContributionAuthority(Capability())
        )
        contribution = service.create_contribution(
            identity=contributor,
            contribution_id="contribution:correction",
            content="version one",
            occurred_at=NOW,
        )
        first_version = contribution.current_version
        service.submit_contribution(identity=contributor, contribution_id=contribution.contribution_id, occurred_at=NOW)
        service.begin_review(identity=reviewer, contribution_id=contribution.contribution_id, occurred_at=NOW)
        first_decision = service.record_human_decision(
            identity=reviewer,
            contribution_id=contribution.contribution_id,
            decision_type="accepted",
            evidence_reference="evidence:first",
            occurred_at=NOW,
        )
        service.request_correction(
            identity=reviewer,
            contribution_id=contribution.contribution_id,
            evidence_reference="evidence:correction",
            occurred_at=NOW,
        )
        successor = service.create_correction(
            identity=contributor,
            contribution_id=contribution.contribution_id,
            content="version two",
            occurred_at=NOW,
        )

        first_version.refresh_from_db()
        first_decision.refresh_from_db()
        self.assertEqual(first_version.content, "version one")
        self.assertEqual(first_version.state, "superseded")
        self.assertEqual(successor.supersedes, first_version)
        self.assertEqual(successor.decisions.count(), 0)
        self.assertEqual(ContributionDecision.objects.count(), 1)
