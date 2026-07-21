from datetime import datetime, timezone

from django.contrib.auth.models import User
from django.test import TestCase

from core.models import Identity, ProfileRole, Role
from src.intevia.services.contribution_authority import ContributionAuthority
from src.intevia.services.contribution_service import ContributionService, LegalHoldPreventsErasure


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


class Capability:
    def authorise(self, *, identity, action, target, timestamp):
        return f"authority:{identity.pk}:{action}"


class ContributionPrivacyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="privacy-actor")
        profile = Identity.objects.create(credential=self.user, access_state=Identity.AccessState.ACTIVE)
        role = Role.objects.create(name="Privacy participant")
        ProfileRole.objects.create(identity=profile, role=role)
        self.service = ContributionService(authority=ContributionAuthority(Capability()))

    def contribution(self, identifier):
        return self.service.create_contribution(
            identity=self.user,
            contribution_id=identifier,
            content="personal protected value",
            occurred_at=NOW,
        )

    def test_restriction_and_erasure_preserve_content_free_lineage(self):
        contribution = self.contribution("contribution:privacy")
        version = self.service.restrict_content(identity=self.user, version_id=contribution.current_version_id, occurred_at=NOW)
        self.assertEqual(version.state, "restricted")
        version = self.service.erase_content(identity=self.user, version_id=version.pk, occurred_at=NOW)
        self.assertIsNone(version.content)
        self.assertEqual(version.state, "erased_content")
        for transition in contribution.transitions.all():
            self.assertFalse(hasattr(transition, "content"))

    def test_legal_hold_blocks_erasure(self):
        contribution = self.contribution("contribution:hold")
        version = contribution.current_version
        version.legal_hold = True
        version.save(update_fields=("legal_hold",))
        with self.assertRaises(LegalHoldPreventsErasure):
            self.service.erase_content(identity=self.user, version_id=version.pk, occurred_at=NOW)
        version.refresh_from_db()
        self.assertEqual(version.content, "personal protected value")
