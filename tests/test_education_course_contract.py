import inspect
from dataclasses import fields
from datetime import datetime, timedelta, timezone, tzinfo
from uuid import UUID

from django.test import SimpleTestCase

from src.intevia.services.education_course_contract import (
    EducationCourseAction,
    EducationCourseAuthorityResponse,
    AppendCourseVersionCommand,
    AppendCourseVersionReceipt,
    CreateCourseCommand,
    CreateCourseReceipt,
    authority_decision_reference,
    authority_target,
    canonical_json_bytes,
    canonical_timestamp,
    command_payload,
    lineage_payload,
    lineage_reference,
    payload_fingerprint,
    target_fingerprint,
)


class EducationCourseContractTests(SimpleTestCase):
    def test_exact_command_receipt_and_service_signature_allowlists(self):
        self.assertEqual(
            [field.name for field in fields(CreateCourseCommand)],
            [
                "credential", "course_id", "course_name", "course_description",
                "course_learning_objectives", "definition_basis_reference",
                "request_reference", "idempotency_key", "occurred_at",
            ],
        )
        self.assertEqual(
            [field.name for field in fields(AppendCourseVersionCommand)],
            [
                "credential", "course_id", "expected_current_version_pk",
                "expected_current_lineage_reference", "course_name",
                "course_description", "course_learning_objectives",
                "definition_basis_reference", "request_reference",
                "idempotency_key", "occurred_at",
            ],
        )
        self.assertEqual(
            [field.name for field in fields(CreateCourseReceipt)],
            [
                "database_alias", "course_id", "course_version_pk",
                "version_number", "lineage_reference", "request_reference",
                "idempotency_key", "payload_fingerprint", "occurred_at", "replayed",
            ],
        )
        self.assertEqual(
            [field.name for field in fields(AppendCourseVersionReceipt)],
            [
                "database_alias", "course_id", "course_version_pk",
                "version_number", "predecessor_version_pk",
                "predecessor_lineage_reference", "lineage_reference",
                "request_reference", "idempotency_key", "payload_fingerprint",
                "occurred_at", "replayed",
            ],
        )
        from src.intevia.services.education_course_service import EducationCourseService
        self.assertEqual(
            str(inspect.signature(EducationCourseService.create)),
            "(self, command: 'CreateCourseCommand') -> 'CreateCourseReceipt'",
        )
        self.assertEqual(
            str(inspect.signature(EducationCourseService.append_version)),
            "(self, command: 'AppendCourseVersionCommand') -> 'AppendCourseVersionReceipt'",
        )

    def test_timestamp_profile_is_fixed_width_effectively_aware_and_offset_safe(self):
        for year in (1, 99, 999, 1000):
            with self.subTest(year=year):
                self.assertEqual(
                    canonical_timestamp(
                        datetime(year, 1, 2, 3, 4, 5, 6, tzinfo=timezone.utc)
                    ),
                    f"{year:04d}-01-02T03:04:05.000006Z",
                )

        self.assertEqual(
            canonical_timestamp(
                datetime(
                    2026,
                    1,
                    1,
                    0,
                    30,
                    tzinfo=timezone(timedelta(hours=1)),
                )
            ),
            "2025-12-31T23:30:00.000000Z",
        )

        class IneffectiveTimezone(tzinfo):
            def utcoffset(self, value):
                return None

        with self.assertRaises(ValueError):
            canonical_timestamp(datetime(2026, 1, 1, tzinfo=IneffectiveTimezone()))

    def test_all_eight_frozen_vectors(self):
        actor = UUID("11111111-1111-4111-8111-111111111111")
        course = UUID("22222222-2222-4222-8222-222222222222")
        create_target = authority_target(
            database_alias="default",
            action=EducationCourseAction.CREATE,
            course_id=course,
        )
        self.assertEqual(
            canonical_json_bytes(create_target).decode(),
            '{"action":"CREATE","contract_version":1,"course_id":"22222222-2222-4222-8222-222222222222","database_alias":"default","schema":"intevia.s014.education-course.authority-target.v1"}',
        )
        self.assertEqual(
            target_fingerprint(create_target),
            "cf4a81b43c6a5cf520d3e536706de8b797bc0fc97dfbbdc2c152582073261885",
        )
        create_payload = command_payload(
            database_alias="default",
            action=EducationCourseAction.CREATE,
            actor_identity_id=actor,
            actor_access_epoch=7,
            target=create_target,
            course_name="HAT Practitioner Training Level 1",
            course_description="Foundational Human–AI teaming practice.",
            course_learning_objectives="Establish safe, reflective, governed practice.",
            definition_basis_reference="source:s014-vector:create:1",
            request_reference="req-s014-create-001",
            idempotency_key="idem-s014-create-001",
            occurred_at=datetime(2026, 7, 30, 12, 0, 0, 123456, tzinfo=timezone.utc),
        )
        create_fingerprint = payload_fingerprint(create_payload)
        self.assertEqual(
            create_fingerprint,
            "303eb2026cf5d1bdf1439fb5231cec8a982b7087fee42f445bf8aa7243764323",
        )
        create_response = EducationCourseAuthorityResponse(
            "default", 17, actor, 7, EducationCourseAction.CREATE,
            target_fingerprint(create_target), "req-s014-create-001",
            "idem-s014-create-001", datetime(2026, 7, 30, 12, 0, 1, tzinfo=timezone.utc),
            "authority:s014:vector:allow:001",
        )
        create_decision = authority_decision_reference(create_response)
        self.assertEqual(
            create_decision,
            "s014d1:c3408ab74f101bc31f91a570c290e1c0e8f907b06d60b4ea864784b3f3b6253b",
        )
        create_lineage = lineage_reference(lineage_payload(
            database_alias="default", course_id=course, version_number=1,
            predecessor_lineage_reference=None, action=EducationCourseAction.CREATE,
            actor_identity_id=actor, actor_access_epoch=7,
            authority_decision_reference=create_decision,
            request_reference="req-s014-create-001", idempotency_key="idem-s014-create-001",
            payload_fingerprint=create_fingerprint,
            occurred_at=datetime(2026, 7, 30, 12, 0, 0, 123456, tzinfo=timezone.utc),
        ))
        self.assertEqual(create_lineage, "s014l1:41ea805842a27b2f62a8d95c367ed9c6660b6fba4ae06371c561c33119377bf8")
        append_target = authority_target(
            database_alias="default", action=EducationCourseAction.APPEND_VERSION,
            course_id=course, expected_current_version_pk=101,
            expected_current_lineage_reference=create_lineage,
        )
        self.assertEqual(target_fingerprint(append_target), "094f26ff2876bdfd4334733c12b04ea7ea13e88d44d4e8406ff979751d267a64")
        append_payload = command_payload(
            database_alias="default", action=EducationCourseAction.APPEND_VERSION,
            actor_identity_id=actor, actor_access_epoch=7, target=append_target,
            course_name="HAT Practitioner Training Level 1",
            course_description="A governed foundation for Human–AI teaming practice.",
            course_learning_objectives="Establish safe, reflective, governed practice and evidence discipline.",
            definition_basis_reference="source:s014-vector:append:2",
            request_reference="req-s014-append-002", idempotency_key="idem-s014-append-002",
            occurred_at=datetime(2026, 7, 30, 13, 15, 0, 654321, tzinfo=timezone.utc),
        )
        append_fingerprint = payload_fingerprint(append_payload)
        self.assertEqual(append_fingerprint, "de2ee2f38663d8c83986e56e29c31e62a1fb991e1062955f2c7c4b6189f1a69d")
        append_response = EducationCourseAuthorityResponse(
            "default", 17, actor, 7, EducationCourseAction.APPEND_VERSION,
            target_fingerprint(append_target), "req-s014-append-002", "idem-s014-append-002",
            datetime(2026, 7, 30, 13, 15, 1, tzinfo=timezone.utc),
            "authority:s014:vector:allow:002",
        )
        append_decision = authority_decision_reference(append_response)
        self.assertEqual(append_decision, "s014d1:0974f7c78e5b269cf6fc12516134836cc86af48dcec7bf5b74c7340003c568c5")
        self.assertEqual(lineage_reference(lineage_payload(
            database_alias="default", course_id=course, version_number=2,
            predecessor_lineage_reference=create_lineage,
            action=EducationCourseAction.APPEND_VERSION, actor_identity_id=actor,
            actor_access_epoch=7, authority_decision_reference=append_decision,
            request_reference="req-s014-append-002", idempotency_key="idem-s014-append-002",
            payload_fingerprint=append_fingerprint,
            occurred_at=datetime(2026, 7, 30, 13, 15, 0, 654321, tzinfo=timezone.utc),
        )), "s014l1:7b5e54ff2d4de976dd31fa2906a08d71a49648504e64fd559b57978b8b51797e")

    def test_letter_bearing_uuid_is_lowercase_hyphenated(self):
        value = {"id": UUID("ABCDEFAB-CDEF-4ABC-8DEF-ABCDEFABCDEF")}
        self.assertEqual(canonical_json_bytes(value), b'{"id":"abcdefab-cdef-4abc-8def-abcdefabcdef"}')

    def test_float_and_non_nfc_key_are_rejected(self):
        with self.assertRaises(TypeError):
            canonical_json_bytes({"value": 1.5})
        with self.assertRaises(ValueError):
            canonical_json_bytes({"e\u0301": "value"})

    def test_exact_canonical_primitive_shape_and_timestamp_digest_round_trip(self):
        class IntegerSubclass(int):
            pass

        self.assertEqual(
            canonical_json_bytes({"false": False, "integer": 1, "null": None}),
            b'{"false":false,"integer":1,"null":null}',
        )
        with self.assertRaises(TypeError):
            canonical_json_bytes({"value": IntegerSubclass(1)})
        with self.assertRaises(ValueError):
            canonical_json_bytes({"value": "Cafe\u0301"})

        actor = UUID("abcdefab-cdef-4abc-8def-abcdefabcdef")
        common = dict(
            database_alias="default",
            actor_pk=1,
            actor_identity_id=actor,
            actor_access_epoch=0,
            action=EducationCourseAction.CREATE,
            target_fingerprint="a" * 64,
            request_reference="request",
            idempotency_key="idempotency",
            authority_reference="authority",
        )
        offset = EducationCourseAuthorityResponse(
            **common,
            evaluated_at=datetime(
                2026, 7, 30, 13, tzinfo=timezone(timedelta(hours=1))
            ),
        )
        reconstructed = EducationCourseAuthorityResponse(
            **common,
            evaluated_at=datetime(2026, 7, 30, 12, tzinfo=timezone.utc),
        )
        self.assertEqual(
            authority_decision_reference(offset),
            authority_decision_reference(reconstructed),
        )