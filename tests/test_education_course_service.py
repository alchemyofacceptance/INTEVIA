from dataclasses import replace
from datetime import datetime, timedelta, timezone, tzinfo
from types import SimpleNamespace
from uuid import UUID, uuid4

from django.contrib.auth.models import User
from django.db import IntegrityError
from django.test import TestCase

from core.models import Course, CourseVersion, Identity
from src.intevia.services.education_course_authority import EducationCourseAuthority
from src.intevia.services.education_course_authority import EducationCourseAuthorityMalformed
from src.intevia.services.education_course_contract import (
    AppendCourseVersionCommand,
    CreateCourseCommand,
    EducationCourseAuthorityRefusal,
    EducationCourseAuthorityResponse,
    EducationCourseRefusalCode,
)
from src.intevia.services.education_course_service import (
    EducationCourseActorError,
    EducationCourseAuthorityDenied,
    EducationCourseCrossEpochConflict,
    EducationCourseIdempotencyCourseConflict,
    EducationCourseIdentityConflict,
    EducationCourseLineageError,
    EducationCourseNotFound,
    EducationCoursePayloadConflict,
    EducationCourseService,
    EducationCourseStaleHeadConflict,
    EducationCourseValidationError,
)


class _Allow:
    def evaluate_course_definition(self, request):
        return EducationCourseAuthorityResponse(
            request.database_alias, request.actor_pk, request.actor_identity_id,
            request.actor_access_epoch, request.action, request.target_fingerprint,
            request.request_reference, request.idempotency_key, request.evaluated_at,
            "authority:s014:test",
        )


class _Deny:
    def evaluate_course_definition(self, request):
        return EducationCourseAuthorityRefusal(
            request.database_alias, request.actor_pk, request.actor_identity_id,
            request.actor_access_epoch, request.action, request.target_fingerprint,
            request.request_reference, request.idempotency_key, request.evaluated_at,
            EducationCourseRefusalCode.DENIED,
        )


class _MalformedEqualEcho:
    def evaluate_course_definition(self, request):
        return EducationCourseAuthorityResponse(
            request.database_alias,
            True,
            request.actor_identity_id,
            request.actor_access_epoch,
            request.action,
            request.target_fingerprint,
            request.request_reference,
            request.idempotency_key,
            request.evaluated_at,
            "authority:s014:malformed",
        )


class EducationCourseServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="course-service")
        self.identity = Identity.objects.create(
            credential=self.user, access_state=Identity.AccessState.ACTIVE
        )
        self.now = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)
        self.service = EducationCourseService(
            authority=EducationCourseAuthority(provider=_Allow()),
            clock=lambda: self.now,
        )
        self.course_id = uuid4()

    def create_command(self, **values):
        defaults = dict(
            credential=self.user, course_id=self.course_id, course_name="Course",
            course_description="Description", course_learning_objectives="Objectives",
            definition_basis_reference="basis", request_reference="create-request",
            idempotency_key="create-idem", occurred_at=self.now,
        )
        defaults.update(values)
        return CreateCourseCommand(**defaults)

    def append_command(self, receipt, **values):
        defaults = dict(
            credential=self.user, course_id=self.course_id,
            expected_current_version_pk=receipt.course_version_pk,
            expected_current_lineage_reference=receipt.lineage_reference,
            course_name="Course v2", course_description="Description v2",
            course_learning_objectives="Objectives v2",
            definition_basis_reference="basis-v2", request_reference="append-request",
            idempotency_key="append-idem", occurred_at=self.now + timedelta(hours=1),
        )
        defaults.update(values)
        return AppendCourseVersionCommand(**defaults)

    def test_create_and_exact_replay(self):
        command = self.create_command()
        first = self.service.create(command)
        replay = self.service.create(command)
        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(replace(first, replayed=True), replay)
        self.assertEqual(Course.objects.count(), 1)
        self.assertEqual(CourseVersion.objects.count(), 1)

    def test_exact_command_scalar_normalization_bounds_and_grammar(self):
        class IneffectiveTimezone(tzinfo):
            def utcoffset(self, value):
                return None

        command = self.create_command(
            definition_basis_reference="basis:with punctuation/[]",
            request_reference="request:with punctuation/[]",
            idempotency_key="key:with punctuation/[]",
        )
        self.assertEqual(self.service.create(command).version_number, 1)
        normalized = self.service.create(self.create_command(
            course_id=uuid4(), course_name=" Cafe\u0301 ",
            request_reference="normalized-request",
            idempotency_key="normalized-idem",
        ))
        self.assertEqual(
            CourseVersion.objects.get(pk=normalized.course_version_pk).course_name,
            "Caf\u00e9",
        )

        cases = {
            "credential": None,
            "course_id": str(uuid4()),
            "course_name_type": 1,
            "course_name_empty": " ",
            "course_name_overlong": "x" * 46,
            "course_description_overlong": "x" * 4097,
            "course_learning_objectives_overlong": "x" * 4097,
            "definition_basis_reference_overlong": "x" * 256,
            "request_reference_overlong": "x" * 129,
            "idempotency_key_overlong": "x" * 121,
            "occurred_at_naive": datetime(2026, 1, 1),
            "occurred_at_effective_naive": datetime(
                2026, 1, 1, tzinfo=IneffectiveTimezone()
            ),
        }
        fields = {
            "course_name_type": "course_name",
            "course_name_empty": "course_name",
            "course_name_overlong": "course_name",
            "course_description_overlong": "course_description",
            "course_learning_objectives_overlong": "course_learning_objectives",
            "definition_basis_reference_overlong": "definition_basis_reference",
            "request_reference_overlong": "request_reference",
            "idempotency_key_overlong": "idempotency_key",
            "occurred_at_naive": "occurred_at",
            "occurred_at_effective_naive": "occurred_at",
        }
        for name, value in cases.items():
            field = fields.get(name, name)
            with self.subTest(name=name), self.assertRaises(
                EducationCourseValidationError
            ):
                self.service.create(replace(self.create_command(), **{field: value}))

        first = self.service.create(self.create_command(
            course_id=uuid4(), request_reference="selector-create",
            idempotency_key="selector-create-idem",
        ))
        append = self.append_command(first)
        selector_cases = {
            "expected_current_version_pk": (True, 1.0, 0, None),
            "expected_current_lineage_reference": (
                "s014l1:" + "A" * 64,
                "s014l1:" + "a" * 63,
                None,
            ),
        }
        for field, values in selector_cases.items():
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(
                    EducationCourseValidationError
                ):
                    self.service.append_version(replace(append, **{field: value}))

    def test_malformed_authority_echo_creates_no_durable_row(self):
        service = EducationCourseService(
            authority=EducationCourseAuthority(provider=_MalformedEqualEcho()),
            clock=lambda: self.now,
        )
        with self.assertRaises(EducationCourseAuthorityMalformed):
            service.create(self.create_command())
        self.assertEqual(Course.objects.count(), 0)
        self.assertEqual(CourseVersion.objects.count(), 0)

    def test_append_and_advanced_head_replay(self):
        first = self.service.create(self.create_command())
        v2_command = self.append_command(first)
        v2 = self.service.append_version(v2_command)
        v3 = self.service.append_version(self.append_command(
            v2, idempotency_key="append-idem-3", request_reference="append-request-3",
            course_name="Course v3", occurred_at=self.now + timedelta(hours=2),
        ))
        replay = self.service.append_version(v2_command)
        self.assertEqual(replay.course_version_pk, v2.course_version_pk)
        self.assertTrue(replay.replayed)
        self.assertEqual(Course.objects.get().current_version_id, v3.course_version_pk)
        self.assertEqual(CourseVersion.objects.count(), 3)

    def test_stale_head_and_post_authority_time_precondition_write_nothing(self):
        first = self.service.create(self.create_command())
        with self.assertRaises(EducationCourseStaleHeadConflict):
            self.service.append_version(self.append_command(
                first, expected_current_version_pk=first.course_version_pk + 99
            ))
        with self.assertRaises(EducationCourseValidationError):
            self.service.append_version(self.append_command(first, occurred_at=self.now))
        self.assertEqual(CourseVersion.objects.count(), 1)

    def test_occupied_course_identity_is_conflict(self):
        self.service.create(self.create_command())
        with self.assertRaises(EducationCourseIdentityConflict):
            self.service.create(self.create_command(
                idempotency_key="another-key", request_reference="another-request"
            ))

    def test_cross_epoch_and_payload_conflicts_precede_replay(self):
        command = self.create_command()
        self.service.create(command)
        Identity.objects.filter(pk=self.identity.pk).update(access_epoch=1)
        with self.assertRaises(EducationCourseCrossEpochConflict):
            self.service.create(command)
        Identity.objects.filter(pk=self.identity.pk).update(access_epoch=0)
        with self.assertRaises(EducationCoursePayloadConflict):
            self.service.create(replace(command, course_name="Different Course"))
        self.assertEqual(Course.objects.count(), 1)
        self.assertEqual(CourseVersion.objects.count(), 1)

    def test_cross_course_global_key_conflict_in_both_root_orderings(self):
        orderings = (
            (
                UUID("10000000-0000-4000-8000-000000000000"),
                UUID("f0000000-0000-4000-8000-000000000000"),
            ),
            (
                UUID("f0000000-0000-4000-8000-000000000001"),
                UUID("10000000-0000-4000-8000-000000000001"),
            ),
        )
        for number, (existing_id, requested_id) in enumerate(orderings):
            with self.subTest(existing_id=existing_id, requested_id=requested_id):
                user = User.objects.create_user(username=f"course-order-{number}")
                Identity.objects.create(
                    credential=user, access_state=Identity.AccessState.ACTIVE
                )
                service = EducationCourseService(
                    authority=EducationCourseAuthority(provider=_Allow()),
                    clock=lambda: self.now,
                )
                existing = self.create_command(
                    credential=user,
                    course_id=existing_id,
                    idempotency_key=f"global-{number}",
                    request_reference=f"existing-{number}",
                )
                service.create(existing)
                with self.assertRaises(EducationCourseIdempotencyCourseConflict):
                    service.create(replace(
                        existing,
                        course_id=requested_id,
                        request_reference=f"requested-{number}",
                    ))

    def test_append_not_found_is_not_disclosed_before_current_authority(self):
        denied = EducationCourseService(
            authority=EducationCourseAuthority(provider=_Deny()),
            clock=lambda: self.now,
        )
        missing = SimpleNamespace(
            course_version_pk=1,
            lineage_reference="s014l1:" + "a" * 64,
        )
        with self.assertRaises(EducationCourseAuthorityDenied):
            denied.append_version(self.append_command(missing))
        self.assertEqual(Course.objects.count(), 0)
        self.assertEqual(CourseVersion.objects.count(), 0)

    def test_current_authority_is_required_before_replay(self):
        command = self.create_command()
        self.service.create(command)
        denied = EducationCourseService(
            authority=EducationCourseAuthority(provider=_Deny()),
            clock=lambda: self.now,
        )
        with self.assertRaises(EducationCourseAuthorityDenied):
            denied.create(command)
        self.assertEqual(Course.objects.count(), 1)
        self.assertEqual(CourseVersion.objects.count(), 1)

    def test_inactive_or_nonactive_actor_is_refused(self):
        for access_state in (
            Identity.AccessState.PENDING,
            Identity.AccessState.RESTRICTED,
            Identity.AccessState.DEACTIVATED,
        ):
            with self.subTest(access_state=access_state):
                Identity.objects.filter(pk=self.identity.pk).update(
                    access_state=access_state
                )
                with self.assertRaises(EducationCourseActorError):
                    self.service.create(self.create_command())
        Identity.objects.filter(pk=self.identity.pk).update(
            access_state=Identity.AccessState.ACTIVE
        )
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        with self.assertRaises(EducationCourseActorError):
            self.service.create(self.create_command())
        self.assertEqual(Course.objects.count(), 0)

    def test_provider_authorised_noncreator_append_preserves_creator_readback(self):
        from src.intevia.services.education_course_read_service import (
            EducationCourseReadService,
        )
        from src.intevia.services.education_course_service import EducationCourseNotFound

        first = self.service.create(self.create_command())
        other_user = User.objects.create_user(username="course-other-author")
        Identity.objects.create(
            credential=other_user, access_state=Identity.AccessState.ACTIVE
        )
        command = self.append_command(first, credential=other_user)
        appended = self.service.append_version(command)
        self.assertEqual(appended.version_number, 2)
        read_service = EducationCourseReadService(clock=lambda: self.now)
        self.assertEqual(
            read_service.get_current_course(
                credential=self.user, course_id=self.course_id
            ).version_pk,
            appended.course_version_pk,
        )
        with self.assertRaisesRegex(EducationCourseNotFound, "^course not found$"):
            read_service.get_current_course(
                credential=other_user, course_id=self.course_id
            )

    def test_corrupt_authority_evidence_blocks_replay_without_repair(self):
        command = self.create_command()
        receipt = self.service.create(command)
        CourseVersion.objects.filter(pk=receipt.course_version_pk).update(
            authority_decision_reference="s014d1:" + "f" * 64
        )
        with self.assertRaises(EducationCourseLineageError):
            self.service.create(command)
        self.assertEqual(Course.objects.count(), 1)
        self.assertEqual(CourseVersion.objects.count(), 1)
        self.assertEqual(
            CourseVersion.objects.get().authority_decision_reference,
            "s014d1:" + "f" * 64,
        )

    def test_non_nfc_corruption_is_lineage_error_on_both_replays(self):
        create_command = self.create_command()
        first = self.service.create(create_command)
        append_command = self.append_command(first)
        self.service.append_version(append_command)
        corrupted = "Cafe\u0301"
        CourseVersion.objects.filter(pk=first.course_version_pk).update(
            course_name=corrupted
        )
        for replay in (
            lambda: self.service.create(create_command),
            lambda: self.service.append_version(append_command),
        ):
            with self.subTest(replay=replay), self.assertRaises(
                EducationCourseLineageError
            ):
                replay()
        self.assertEqual(
            CourseVersion.objects.get(pk=first.course_version_pk).course_name,
            corrupted,
        )
        self.assertEqual(CourseVersion.objects.count(), 2)

    def test_sqlite_unnamed_race_loss_propagates_unchanged(self):
        error = IntegrityError("unnamed sqlite uniqueness loss")
        recoveries = (
            self.service._recover_create_race,
            self.service._recover_append_race,
        )
        for recovery in recoveries:
            with self.subTest(recovery=recovery), self.assertRaises(
                IntegrityError
            ) as raised:
                recovery(
                    exc=error,
                    actor=self.identity,
                    values={},
                    fingerprint="a" * 64,
                )
            self.assertIs(raised.exception, error)