from datetime import datetime, timezone

from django.contrib.auth.models import User
from django.test import TestCase

from core.models import Profile, ProfileRole, Role, Service
from src.intevia.services.contribution_authority import (
    ContributionAuthority,
    NotAuthorised,
)
from src.intevia.services.service_service import (
    GovernedService,
    InvalidServiceTransition,
)


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


class Capability:
    def __init__(self, denied=()):
        self.denied = set(denied)

    def authorise(self, *, identity, action, target, timestamp):
        if action in self.denied:
            return None
        return f"authority:{identity.pk}:{action}"


class ServiceLifecycleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="service-lifecycle")
        profile = Profile.objects.create(user=self.user)
        role = Role.objects.create(name="Service lifecycle participant")
        ProfileRole.objects.create(profile=profile, role=role)
        self.foundation = GovernedService(
            authority=ContributionAuthority(Capability())
        )

    def create_service(self, service_id="service:lifecycle"):
        return self.foundation.create_service(
            identity=self.user,
            service_id=service_id,
            capability_purpose="Governed pathway",
            domain_intent="Serve intent",
            evidence_reference=f"evidence:{service_id}:creation",
            occurred_at=NOW,
        )

    def test_definition_lifecycle_is_linear_and_retired_is_terminal(self):
        service = self.create_service()
        for command, state in (
            ("publish_service", Service.State.PUBLISHED),
            ("retire_service", Service.State.RETIRED),
        ):
            self.foundation.transition_service(
                identity=self.user,
                service_id=service.service_id,
                command=command,
                evidence_reference=f"evidence:{command}",
                occurred_at=NOW,
            )
            service.refresh_from_db()
            self.assertEqual(service.state, state)

        self.assertEqual(service.transitions.count(), 3)
        self.assertEqual(service.evidence_references.count(), 3)
        with self.assertRaises(InvalidServiceTransition):
            self.foundation.transition_service(
                identity=self.user,
                service_id=service.service_id,
                command="publish_service",
                evidence_reference="evidence:invalid",
                occurred_at=NOW,
            )

    def test_denied_publish_leaves_no_transition_evidence_or_state_change(self):
        service = self.create_service("service:denied-publish")
        denied = GovernedService(
            authority=ContributionAuthority(Capability({"publish_service"}))
        )
        transition_count = service.transitions.count()
        evidence_count = service.evidence_references.count()

        with self.assertRaises(NotAuthorised):
            denied.transition_service(
                identity=self.user,
                service_id=service.service_id,
                command="publish_service",
                evidence_reference="evidence:denied",
                occurred_at=NOW,
            )

        service.refresh_from_db()
        self.assertEqual(service.state, Service.State.DRAFT)
        self.assertEqual(service.transitions.count(), transition_count)
        self.assertEqual(service.evidence_references.count(), evidence_count)
