import inspect
from dataclasses import fields
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from django.contrib.auth.models import User
from django.test import TestCase

from core.models import CourseVersion, Identity
from src.intevia.services.education_course_authority import EducationCourseAuthority
from src.intevia.services.education_course_contract import (
    AppendCourseVersionCommand, CreateCourseCommand, EducationCourseAuthorityResponse
)
from src.intevia.services.education_course_read_service import (
    CourseCurrentDefinitionDTO,
    CourseExactVersionDTO,
    CourseLineageDTO,
    CourseLineageEntryDTO,
    EducationCourseReadService,
    NEUTRAL_MESSAGE,
)
from src.intevia.services.education_course_service import (
    EducationCourseLineageError, EducationCourseNotFound, EducationCourseService
)


class _Allow:
    def evaluate_course_definition(self, request):
        return EducationCourseAuthorityResponse(
            request.database_alias, request.actor_pk, request.actor_identity_id,
            request.actor_access_epoch, request.action, request.target_fingerprint,
            request.request_reference, request.idempotency_key, request.evaluated_at,
            "authority:s014:read",
        )


class EducationCourseReadbackTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="course-reader")
        self.identity = Identity.objects.create(credential=self.user, access_state=Identity.AccessState.ACTIVE)
        self.other_user = User.objects.create_user(username="course-other")
        Identity.objects.create(credential=self.other_user, access_state=Identity.AccessState.ACTIVE)
        self.now = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)
        self.write = EducationCourseService(authority=EducationCourseAuthority(provider=_Allow()), clock=lambda: self.now)
        self.read = EducationCourseReadService(clock=lambda: self.now)
        self.course_id = uuid4()
        self.v1 = self.write.create(CreateCourseCommand(
            self.user, self.course_id, "Course", "Description", "Objectives", "basis",
            "request-1", "idem-1", self.now,
        ))
        self.v2 = self.write.append_version(AppendCourseVersionCommand(
            self.user, self.course_id, self.v1.course_version_pk, self.v1.lineage_reference,
            "Course v2", "Description v2", "Objectives v2", "basis-2",
            "request-2", "idem-2", self.now + timedelta(hours=1),
        ))

    def test_current_exact_and_lineage_projection(self):
        current = self.read.get_current_course(credential=self.user, course_id=self.course_id)
        exact = self.read.get_course_version(credential=self.user, course_id=self.course_id, version_number=1)
        lineage = self.read.get_course_lineage(credential=self.user, course_id=self.course_id)
        self.assertEqual(current.version_number, 2)
        self.assertFalse(exact.is_current)
        self.assertEqual(len(lineage.versions), 2)
        self.assertEqual(current.neutral_message, NEUTRAL_MESSAGE)

    def test_exact_signatures_dto_and_prohibited_field_allowlists(self):
        self.assertEqual(
            str(inspect.signature(EducationCourseReadService.get_current_course)),
            "(self, *, credential: 'User', course_id: 'UUID') -> 'CourseCurrentDefinitionDTO'",
        )
        self.assertEqual(
            str(inspect.signature(EducationCourseReadService.get_course_version)),
            "(self, *, credential: 'User', course_id: 'UUID', version_number: 'int') -> 'CourseExactVersionDTO'",
        )
        self.assertEqual(
            str(inspect.signature(EducationCourseReadService.get_course_lineage)),
            "(self, *, credential: 'User', course_id: 'UUID') -> 'CourseLineageDTO'",
        )
        allowlists = {
            CourseCurrentDefinitionDTO: {
                "course_id", "version_pk", "version_number",
                "predecessor_lineage_reference", "course_name",
                "course_description", "course_learning_objectives",
                "definition_basis_reference", "actor_identity_id", "occurred_at",
                "lineage_reference", "neutral_message",
            },
            CourseExactVersionDTO: {
                "course_id", "version_pk", "version_number",
                "predecessor_lineage_reference", "is_current", "course_name",
                "course_description", "course_learning_objectives",
                "definition_basis_reference", "actor_identity_id", "occurred_at",
                "lineage_reference", "neutral_message",
            },
            CourseLineageEntryDTO: {
                "version_pk", "version_number", "predecessor_lineage_reference",
                "is_current", "course_name", "course_description",
                "course_learning_objectives", "definition_basis_reference",
                "actor_identity_id", "occurred_at", "lineage_reference",
            },
            CourseLineageDTO: {
                "course_id", "current_version_number", "versions", "neutral_message",
            },
        }
        prohibited = {
            "authority_reference", "authority_decision_reference", "request_reference",
            "idempotency_key", "payload_fingerprint", "actor_access_epoch",
        }
        for dto, expected in allowlists.items():
            observed = {field.name for field in fields(dto)}
            self.assertEqual(observed, expected)
            self.assertEqual(observed & prohibited, set())

    def test_absent_and_non_creator_are_identical_not_found(self):
        for credential, course_id in ((self.other_user, self.course_id), (self.user, uuid4())):
            with self.subTest(credential=credential.username), self.assertRaisesRegex(EducationCourseNotFound, "^course not found$"):
                self.read.get_current_course(credential=credential, course_id=course_id)

    def test_bulk_corruption_fails_closed_without_repair(self):
        CourseVersion.objects.filter(pk=self.v2.course_version_pk).update(payload_fingerprint="f" * 64)
        before = CourseVersion.objects.count()
        with self.assertRaises(EducationCourseLineageError):
            self.read.get_course_lineage(credential=self.user, course_id=self.course_id)
        self.assertEqual(CourseVersion.objects.count(), before)
        self.assertEqual(CourseVersion.objects.get(pk=self.v2.course_version_pk).payload_fingerprint, "f" * 64)

    def test_non_nfc_corruption_is_lineage_error_on_every_read_surface(self):
        corrupted = "Cafe\u0301"
        CourseVersion.objects.filter(pk=self.v1.course_version_pk).update(
            course_name=corrupted
        )
        reads = (
            lambda: self.read.get_current_course(
                credential=self.user, course_id=self.course_id
            ),
            lambda: self.read.get_course_version(
                credential=self.user, course_id=self.course_id, version_number=1
            ),
            lambda: self.read.get_course_lineage(
                credential=self.user, course_id=self.course_id
            ),
        )
        for read in reads:
            with self.subTest(read=read), self.assertRaises(
                EducationCourseLineageError
            ):
                read()
        self.assertEqual(
            CourseVersion.objects.get(pk=self.v1.course_version_pk).course_name,
            corrupted,
        )
        self.assertEqual(CourseVersion.objects.count(), 2)