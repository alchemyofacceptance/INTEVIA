from datetime import datetime, timezone

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import (
    LibraryServiceAssociation,
    Identity,
    ProfileRole,
    Role,
    Service,
    ServiceDeliveryEvidenceReference,
    ServiceEventAssociation,
    ServiceEvidenceReference,
    ServiceTransition,
    ServiceVersion,
)
from src.intevia.services.contribution_authority import ContributionAuthority
from src.intevia.services.service_service import GovernedService


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


class Capability:
    def authorise(self, *, identity, action, target, timestamp):
        return f"authority:{identity.pk}:{action}"


class ServiceModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="service-model-owner")
        self.profile = Identity.objects.create(credential=self.user, access_state=Identity.AccessState.ACTIVE)
        role = Role.objects.create(name="Service model participant")
        ProfileRole.objects.create(identity=self.profile, role=role)
        self.foundation = GovernedService(
            authority=ContributionAuthority(Capability())
        )

    def create_service(self):
        return self.foundation.create_service(
            identity=self.user,
            service_id="service:model",
            capability_purpose="Apply governed knowledge",
            domain_intent="Support a domain need",
            evidence_reference="evidence:service-model",
            occurred_at=NOW,
        )

    def test_seven_service_models_remain_distinct(self):
        self.assertEqual(
            len(
                {
                    Service,
                    ServiceVersion,
                    ServiceTransition,
                    ServiceEvidenceReference,
                    LibraryServiceAssociation,
                    ServiceEventAssociation,
                    ServiceDeliveryEvidenceReference,
                }
            ),
            7,
        )

    def test_creation_is_atomic_and_evidence_bearing(self):
        service = self.create_service()
        transition = service.transitions.get()
        evidence = service.evidence_references.get()

        self.assertEqual(service.state, Service.State.DRAFT)
        self.assertEqual(service.current_version.version_number, 1)
        self.assertEqual(transition.version, service.current_version)
        self.assertEqual(evidence.transition, transition)
        self.assertEqual(evidence.version, service.current_version)

    def test_transition_and_service_evidence_are_immutable(self):
        service = self.create_service()
        transition = service.transitions.get()
        evidence = service.evidence_references.get()

        transition.command = "changed"
        with self.assertRaises(ValidationError):
            transition.save()
        evidence.reference = "changed"
        with self.assertRaises(ValidationError):
            evidence.save()
