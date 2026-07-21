from datetime import datetime, timezone

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import (
    LibraryResource,
    LibraryResourceEvidenceReference,
    LibraryResourceTransition,
    LibraryResourceVersion,
    Identity,
    ProfileRole,
    Role,
)
from src.intevia.services.contribution_authority import (
    ContributionAuthority,
    NotAuthorised,
)
from src.intevia.services.library_service import (
    InvalidLibraryTransition,
    LibraryService,
)


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


class Capability:
    def __init__(self, denied=()):
        self.denied = set(denied)

    def authorise(self, *, identity, action, target, timestamp):
        if action in self.denied:
            return None
        return f"authority:{identity.pk}:{action}"


class LibraryFoundationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="library-owner")
        self.profile = Identity.objects.create(credential=self.user, access_state=Identity.AccessState.ACTIVE)
        role = Role.objects.create(name="Library participant")
        ProfileRole.objects.create(identity=self.profile, role=role)
        self.service = LibraryService(
            authority=ContributionAuthority(Capability())
        )

    def create_resource(self, resource_id="library:resource"):
        return self.service.create_resource(
            identity=self.user,
            resource_id=resource_id,
            content="version one",
            evidence_reference=f"evidence:{resource_id}:creation",
            occurred_at=NOW,
        )

    def test_creation_persists_atomic_governed_aggregate(self):
        resource = self.create_resource()

        self.assertEqual(resource.state, LibraryResource.State.DRAFT)
        self.assertEqual(resource.current_version.version_number, 1)
        self.assertEqual(resource.current_version.content, "version one")
        transition = resource.transitions.get()
        evidence = resource.evidence_references.get()
        self.assertEqual(transition.from_state, "new")
        self.assertEqual(transition.to_state, "draft")
        self.assertEqual(transition.version, resource.current_version)
        self.assertEqual(evidence.transition, transition)
        self.assertEqual(evidence.version, resource.current_version)
        self.assertEqual(
            transition.authority_reference,
            f"authority:{self.profile.pk}:create_library_resource",
        )

    def test_successor_preserves_backward_lineage_and_resets_published(self):
        resource = self.create_resource()
        first = resource.current_version
        self.service.transition_resource(
            identity=self.user,
            resource_id=resource.resource_id,
            command="publish_library_resource",
            evidence_reference="evidence:publish",
            occurred_at=NOW,
        )

        second = self.service.create_successor_version(
            identity=self.user,
            resource_id=resource.resource_id,
            content="version two",
            evidence_reference="evidence:version-two",
            occurred_at=NOW,
        )

        resource.refresh_from_db()
        first.refresh_from_db()
        self.assertEqual(second.predecessor, first)
        self.assertEqual(second.version_number, 2)
        self.assertEqual(first.content, "version one")
        self.assertEqual(resource.current_version, second)
        self.assertEqual(resource.state, LibraryResource.State.DRAFT)

    def test_versions_transitions_and_evidence_are_immutable(self):
        resource = self.create_resource()
        version = resource.current_version
        transition = resource.transitions.get()
        evidence = resource.evidence_references.get()

        version.content = "changed"
        with self.assertRaises(ValidationError):
            version.save()
        transition.command = "changed"
        with self.assertRaises(ValidationError):
            transition.save()
        evidence.reference = "changed"
        with self.assertRaises(ValidationError):
            evidence.save()

    def test_lifecycle_is_linear_and_archived_is_terminal(self):
        resource = self.create_resource()
        commands = (
            ("publish_library_resource", "evidence:publish"),
            ("deprecate_library_resource", "evidence:deprecate"),
            ("archive_library_resource", "evidence:archive"),
        )
        for command, evidence in commands:
            self.service.transition_resource(
                identity=self.user,
                resource_id=resource.resource_id,
                command=command,
                evidence_reference=evidence,
                occurred_at=NOW,
            )

        resource.refresh_from_db()
        self.assertEqual(resource.state, LibraryResource.State.ARCHIVED)
        self.assertEqual(resource.archived_at, NOW)
        with self.assertRaises(InvalidLibraryTransition):
            self.service.transition_resource(
                identity=self.user,
                resource_id=resource.resource_id,
                command="publish_library_resource",
                evidence_reference="evidence:invalid",
                occurred_at=NOW,
            )
        with self.assertRaises(InvalidLibraryTransition):
            self.service.create_successor_version(
                identity=self.user,
                resource_id=resource.resource_id,
                content="invalid successor",
                evidence_reference="evidence:invalid-version",
                occurred_at=NOW,
            )

    def test_evidence_is_required_before_any_mutation(self):
        with self.assertRaises(ValidationError):
            self.service.create_resource(
                identity=self.user,
                resource_id="library:no-evidence",
                content="content",
                evidence_reference=" ",
                occurred_at=NOW,
            )
        self.assertFalse(
            LibraryResource.objects.filter(
                resource_id="library:no-evidence"
            ).exists()
        )

        resource = self.create_resource()
        with self.assertRaises(ValidationError):
            self.service.create_successor_version(
                identity=self.user,
                resource_id=resource.resource_id,
                content="version two",
                evidence_reference="",
                occurred_at=NOW,
            )
        with self.assertRaises(ValidationError):
            self.service.transition_resource(
                identity=self.user,
                resource_id=resource.resource_id,
                command="publish_library_resource",
                evidence_reference="",
                occurred_at=NOW,
            )
        resource.refresh_from_db()
        self.assertEqual(resource.versions.count(), 1)
        self.assertEqual(resource.transitions.count(), 1)
        self.assertEqual(resource.state, LibraryResource.State.DRAFT)

    def test_denied_authority_persists_nothing(self):
        service = LibraryService(
            authority=ContributionAuthority(
                Capability(denied={"create_library_resource"})
            )
        )
        with self.assertRaises(NotAuthorised):
            service.create_resource(
                identity=self.user,
                resource_id="library:denied",
                content="content",
                evidence_reference="evidence:denied",
                occurred_at=NOW,
            )
        self.assertFalse(
            LibraryResource.objects.filter(resource_id="library:denied").exists()
        )

    def test_exact_retrieval_returns_resource_versions_and_evidence(self):
        resource = self.create_resource()
        second = self.service.create_successor_version(
            identity=self.user,
            resource_id=resource.resource_id,
            content="version two",
            evidence_reference="evidence:version-two",
            occurred_at=NOW,
        )

        self.assertEqual(
            self.service.get_resource(resource.resource_id).pk,
            resource.pk,
        )
        self.assertEqual(
            self.service.get_version(resource.resource_id, 2),
            second,
        )
        self.assertEqual(
            list(
                self.service.get_lineage(resource.resource_id).values_list(
                    "version_number",
                    flat=True,
                )
            ),
            [1, 2],
        )
        self.assertEqual(
            self.service.get_evidence_references(resource.resource_id).count(),
            2,
        )

    def test_four_library_models_remain_distinct(self):
        self.assertEqual(
            len(
                {
                    LibraryResource,
                    LibraryResourceVersion,
                    LibraryResourceTransition,
                    LibraryResourceEvidenceReference,
                }
            ),
            4,
        )