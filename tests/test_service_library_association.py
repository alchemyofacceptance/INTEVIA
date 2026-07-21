from datetime import datetime, timezone

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import LibraryServiceAssociation, Identity, ProfileRole, Role
from src.intevia.services.contribution_authority import (
    ContributionAuthority,
    NotAuthorised,
)
from src.intevia.services.library_service import LibraryService
from src.intevia.services.service_service import GovernedService


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


class Capability:
    def __init__(self, denied=()):
        self.denied = set(denied)

    def authorise(self, *, identity, action, target, timestamp):
        if action in self.denied:
            return None
        return f"authority:{identity.pk}:{action}"


class ServiceLibraryAssociationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="service-library-owner")
        profile = Identity.objects.create(credential=self.user, access_state=Identity.AccessState.ACTIVE)
        role = Role.objects.create(name="Service Library participant")
        ProfileRole.objects.create(identity=profile, role=role)
        authority = ContributionAuthority(Capability())
        self.foundation = GovernedService(authority=authority)
        library = LibraryService(authority=authority)
        resource = library.create_resource(
            identity=self.user,
            resource_id="library:service-support",
            content="supporting knowledge",
            evidence_reference="evidence:library-support",
            occurred_at=NOW,
        )
        self.library_version = resource.current_version
        self.service = self.foundation.create_service(
            identity=self.user,
            service_id="service:library",
            capability_purpose="Apply knowledge",
            domain_intent="Knowledge-supported capability",
            evidence_reference="evidence:service-library",
            occurred_at=NOW,
        )
        self.foundation.transition_service(
            identity=self.user,
            service_id=self.service.service_id,
            command="publish_service",
            evidence_reference="evidence:publish-service-library",
            occurred_at=NOW,
        )
        self.service.refresh_from_db()

    def test_association_pins_exact_versions_and_preserves_library_ownership(self):
        association = self.foundation.associate_library_resource(
            identity=self.user,
            service_version=self.service.current_version,
            library_resource_version=self.library_version,
            evidence_reference="evidence:library-association",
            occurred_at=NOW,
        )

        self.assertEqual(association.service_version, self.service.current_version)
        self.assertEqual(
            association.library_resource_version,
            self.library_version,
        )
        self.assertEqual(association.library_resource_version.content, "supporting knowledge")
        association.evidence_reference = "changed"
        with self.assertRaises(ValidationError):
            association.save()

    def test_denied_association_persists_nothing(self):
        denied = GovernedService(
            authority=ContributionAuthority(
                Capability({"associate_library_service"})
            )
        )
        with self.assertRaises(NotAuthorised):
            denied.associate_library_resource(
                identity=self.user,
                service_version=self.service.current_version,
                library_resource_version=self.library_version,
                evidence_reference="evidence:denied-association",
                occurred_at=NOW,
            )
        self.assertEqual(LibraryServiceAssociation.objects.count(), 0)
