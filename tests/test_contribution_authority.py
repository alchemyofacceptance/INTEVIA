from __future__ import annotations

from datetime import datetime, timezone

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import Identity, ProfileRole, Role
from src.intevia.services.contribution_authority import (
    ContributionAuthority,
    NotAuthorised,
)
from src.intevia.services.contribution_service import ContributionService


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


class Capability:
    def __init__(self, denied: set[str] | None = None) -> None:
        self.denied = denied or set()
        self.actions: list[str] = []

    def authorise(self, *, identity, action, target, timestamp):
        self.actions.append(action)
        if action in self.denied:
            return None
        return f"authority:{identity.pk}:{action}"


class ContributionAuthorityTests(TestCase):
    def make_identity(self, name: str, *, active: bool = True):
        user = User.objects.create_user(username=name, is_active=active)
        profile = Identity.objects.create(credential=user, display_name=name, access_state=Identity.AccessState.ACTIVE)
        return user, profile

    def assign_role(self, profile: Identity) -> None:
        role, _ = Role.objects.get_or_create(name="Slice 001 participant")
        ProfileRole.objects.create(identity=profile, role=role)

    def test_unknown_or_inactive_identity_is_rejected(self):
        authority = ContributionAuthority(Capability())
        with self.assertRaises(NotAuthorised):
            authority.evaluate(
                identity="human:actor",
                action="create_contribution",
                target="contribution:1",
                timestamp=NOW,
            )

        user, profile = self.make_identity("inactive", active=False)
        self.assign_role(profile)
        with self.assertRaises(NotAuthorised):
            authority.evaluate(
                identity=user,
                action="create_contribution",
                target="contribution:1",
                timestamp=NOW,
            )

    def test_role_assignment_and_capability_are_both_required(self):
        user, profile = self.make_identity("actor")
        authority = ContributionAuthority(Capability())
        with self.assertRaises(NotAuthorised):
            authority.evaluate(
                identity=user,
                action="create_contribution",
                target="contribution:1",
                timestamp=NOW,
            )

        self.assign_role(profile)
        denied = ContributionAuthority(Capability({"create_contribution"}))
        with self.assertRaises(NotAuthorised):
            denied.evaluate(
                identity=user,
                action="create_contribution",
                target="contribution:1",
                timestamp=NOW,
            )

    def test_authority_reference_is_recorded(self):
        user, profile = self.make_identity("contributor")
        self.assign_role(profile)
        service = ContributionService(
            authority=ContributionAuthority(Capability())
        )

        contribution = service.create_contribution(
            identity=user,
            contribution_id="contribution:authority",
            content="Protected content",
            occurred_at=NOW,
        )

        transition = contribution.transitions.get()
        self.assertEqual(
            transition.authority_reference,
            f"authority:{profile.pk}:create_contribution",
        )

    def test_contributor_cannot_decide_own_version(self):
        user, profile = self.make_identity("contributor")
        self.assign_role(profile)
        service = ContributionService(
            authority=ContributionAuthority(Capability())
        )
        contribution = service.create_contribution(
            identity=user,
            contribution_id="contribution:self-review",
            content="Protected content",
            occurred_at=NOW,
        )
        service.submit_contribution(
            identity=user,
            contribution_id=contribution.contribution_id,
            occurred_at=NOW,
        )
        service.begin_review(
            identity=user,
            contribution_id=contribution.contribution_id,
            occurred_at=NOW,
        )

        with self.assertRaises(ValidationError):
            service.record_human_decision(
                identity=user,
                contribution_id=contribution.contribution_id,
                decision_type="accepted",
                evidence_reference="evidence:self-review",
                occurred_at=NOW,
            )

    def test_denied_decision_persists_no_decision_or_transition(self):
        contributor, contributor_profile = self.make_identity("contributor")
        reviewer, reviewer_profile = self.make_identity("reviewer")
        self.assign_role(contributor_profile)
        self.assign_role(reviewer_profile)
        capability = Capability({"accepted_contribution"})
        service = ContributionService(
            authority=ContributionAuthority(capability)
        )
        contribution = service.create_contribution(
            identity=contributor,
            contribution_id="contribution:denied-decision",
            content="Protected content",
            occurred_at=NOW,
        )
        service.submit_contribution(
            identity=contributor,
            contribution_id=contribution.contribution_id,
            occurred_at=NOW,
        )
        service.begin_review(
            identity=reviewer,
            contribution_id=contribution.contribution_id,
            occurred_at=NOW,
        )
        decision_count = contribution.decisions.count()
        transition_count = contribution.transitions.count()

        with self.assertRaises(NotAuthorised):
            service.record_human_decision(
                identity=reviewer,
                contribution_id=contribution.contribution_id,
                decision_type="accepted",
                evidence_reference="evidence:denied-decision",
                occurred_at=NOW,
            )

        contribution.refresh_from_db()
        self.assertEqual(capability.actions[-1], "accepted_contribution")
        self.assertEqual(contribution.decisions.count(), decision_count)
        self.assertEqual(contribution.transitions.count(), transition_count)
        self.assertEqual(contribution.state, contribution.State.UNDER_REVIEW)
