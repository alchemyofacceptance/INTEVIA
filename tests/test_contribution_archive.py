from datetime import datetime, timezone

from django.contrib.auth.models import User
from django.test import TestCase

from core.models import Identity, ProfileRole, Role
from src.intevia.services.contribution_authority import ContributionAuthority, NotAuthorised
from src.intevia.services.contribution_service import ContributionService, InvalidContributionTransition


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


class Capability:
    def __init__(self, deny_archive=False):
        self.deny_archive = deny_archive

    def authorise(self, *, identity, action, target, timestamp):
        if self.deny_archive and action == "archive_contribution":
            return None
        return f"authority:{identity.pk}:{action}"


class ContributionArchiveTests(TestCase):
    def identity(self, name):
        user = User.objects.create_user(username=name)
        profile = Identity.objects.create(credential=user, access_state=Identity.AccessState.ACTIVE)
        role, _ = Role.objects.get_or_create(name="Archive participant")
        ProfileRole.objects.create(identity=profile, role=role)
        return user

    def test_archive_requires_authority_and_preserves_lineage(self):
        contributor = self.identity("archive-contributor")
        service = ContributionService(authority=ContributionAuthority(Capability()))
        contribution = service.create_contribution(
            identity=contributor,
            contribution_id="contribution:archive",
            content="protected",
            occurred_at=NOW,
        )
        service.withdraw_contribution(identity=contributor, contribution_id=contribution.contribution_id, occurred_at=NOW)
        denied = ContributionService(authority=ContributionAuthority(Capability(True)))
        with self.assertRaises(NotAuthorised):
            denied.archive_contribution(
                identity=contributor,
                contribution_id=contribution.contribution_id,
                evidence_reference="evidence:archive",
                occurred_at=NOW,
            )
        service.archive_contribution(
            identity=contributor,
            contribution_id=contribution.contribution_id,
            evidence_reference="evidence:archive",
            occurred_at=NOW,
        )
        contribution.refresh_from_db()
        self.assertEqual(contribution.state, "archived")
        self.assertEqual(contribution.transitions.count(), 3)
        self.assertEqual(contribution.evidence_references.count(), 1)
        with self.assertRaises(InvalidContributionTransition):
            service.withdraw_contribution(identity=contributor, contribution_id=contribution.contribution_id, occurred_at=NOW)
