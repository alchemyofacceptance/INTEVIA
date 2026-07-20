from datetime import datetime, timezone

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import Profile, ProfileRole, Role, Service
from src.intevia.services.contribution_authority import ContributionAuthority
from src.intevia.services.service_service import (
    GovernedService,
    InvalidServiceTransition,
)


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


class Capability:
    def authorise(self, *, identity, action, target, timestamp):
        return f"authority:{identity.pk}:{action}"


class ServiceVersionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="service-version-owner")
        profile = Profile.objects.create(user=self.user)
        role = Role.objects.create(name="Service version participant")
        ProfileRole.objects.create(profile=profile, role=role)
        self.foundation = GovernedService(
            authority=ContributionAuthority(Capability())
        )
        self.service = self.foundation.create_service(
            identity=self.user,
            service_id="service:versions",
            capability_purpose="Version one",
            domain_intent="Initial intent",
            evidence_reference="evidence:version-one",
            occurred_at=NOW,
        )

    def test_successor_is_immutable_monotonic_and_resets_published(self):
        first = self.service.current_version
        self.foundation.transition_service(
            identity=self.user,
            service_id=self.service.service_id,
            command="publish_service",
            evidence_reference="evidence:publish-one",
            occurred_at=NOW,
        )
        second = self.foundation.create_successor_version(
            identity=self.user,
            service_id=self.service.service_id,
            capability_purpose="Version two",
            domain_intent="Refined intent",
            evidence_reference="evidence:version-two",
            occurred_at=NOW,
        )

        self.service.refresh_from_db()
        self.assertEqual(second.predecessor, first)
        self.assertEqual(second.version_number, 2)
        self.assertEqual(self.service.current_version, second)
        self.assertEqual(self.service.state, Service.State.DRAFT)
        self.assertEqual(
            list(
                self.foundation.get_lineage(self.service.service_id).values_list(
                    "version_number",
                    flat=True,
                )
            ),
            [1, 2],
        )

        second.capability_purpose = "changed"
        with self.assertRaises(ValidationError):
            second.save()
        with self.assertRaises(ValidationError):
            second.delete()

    def test_retired_service_rejects_successor(self):
        self.foundation.transition_service(
            identity=self.user,
            service_id=self.service.service_id,
            command="publish_service",
            evidence_reference="evidence:publish",
            occurred_at=NOW,
        )
        self.foundation.transition_service(
            identity=self.user,
            service_id=self.service.service_id,
            command="retire_service",
            evidence_reference="evidence:retire",
            occurred_at=NOW,
        )

        with self.assertRaises(InvalidServiceTransition):
            self.foundation.create_successor_version(
                identity=self.user,
                service_id=self.service.service_id,
                capability_purpose="Forbidden successor",
                domain_intent="Forbidden intent",
                evidence_reference="evidence:forbidden",
                occurred_at=NOW,
            )
