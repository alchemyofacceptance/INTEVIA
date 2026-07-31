from dataclasses import replace
from datetime import datetime, timedelta, timezone, tzinfo
from uuid import uuid4

from django.db import transaction
from django.test import TestCase

from src.intevia.services.education_course_authority import (
    EducationCourseAuthority,
    EducationCourseAuthorityDenied,
    EducationCourseAuthorityMalformed,
    EducationCourseAuthorityUnavailable,
)
from src.intevia.services.education_course_contract import (
    EducationCourseAction,
    EducationCourseAuthorityRefusal,
    EducationCourseAuthorityRequest,
    EducationCourseAuthorityResponse,
    EducationCourseRefusalCode,
)


class _Provider:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = 0

    def evaluate_course_definition(self, request):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result(request) if callable(self.result) else self.result


class EducationCourseAuthorityTests(TestCase):
    def setUp(self):
        self.request = EducationCourseAuthorityRequest(
            "default", 1, uuid4(), 0, EducationCourseAction.CREATE,
            "a" * 64, "request", "idempotency", datetime.now(timezone.utc),
        )

    def response(self, request):
        return EducationCourseAuthorityResponse(
            request.database_alias, request.actor_pk, request.actor_identity_id,
            request.actor_access_epoch, request.action, request.target_fingerprint,
            request.request_reference, request.idempotency_key, request.evaluated_at,
            "authority:test",
        )

    def test_exact_response_qualified_once(self):
        provider = _Provider(self.response)
        with transaction.atomic():
            decision = EducationCourseAuthority(provider=provider).qualify(self.request)
        self.assertEqual(provider.calls, 1)
        self.assertTrue(decision.authority_decision_reference.startswith("s014d1:"))

    def test_refusal_denied_after_echo_validation(self):
        provider = _Provider(lambda request: EducationCourseAuthorityRefusal(
            request.database_alias, request.actor_pk, request.actor_identity_id,
            request.actor_access_epoch, request.action, request.target_fingerprint,
            request.request_reference, request.idempotency_key, request.evaluated_at,
            EducationCourseRefusalCode.DENIED,
        ))
        with transaction.atomic(), self.assertRaises(EducationCourseAuthorityDenied):
            EducationCourseAuthority(provider=provider).qualify(self.request)

    def test_subclass_mismatched_echo_and_none_are_malformed(self):
        class Subclass(EducationCourseAuthorityResponse):
            pass
        base = self.response(self.request)
        malformed = [Subclass(*[getattr(base, field) for field in base.__dataclass_fields__]), None]
        mismatched = EducationCourseAuthorityResponse(
            base.database_alias, base.actor_pk + 1, base.actor_identity_id,
            base.actor_access_epoch, base.action, base.target_fingerprint,
            base.request_reference, base.idempotency_key, base.evaluated_at,
            base.authority_reference,
        )
        malformed.append(mismatched)
        for result in malformed:
            with self.subTest(result=result), transaction.atomic(), self.assertRaises(EducationCourseAuthorityMalformed):
                EducationCourseAuthority(provider=_Provider(result)).qualify(self.request)

    def test_provider_exception_is_unavailable(self):
        with transaction.atomic(), self.assertRaises(EducationCourseAuthorityUnavailable):
            EducationCourseAuthority(provider=_Provider(error=RuntimeError("offline"))).qualify(self.request)

    def test_every_echo_is_independently_binding(self):
        base = self.response(self.request)
        mismatches = {
            "database_alias": "other",
            "actor_pk": base.actor_pk + 1,
            "actor_identity_id": uuid4(),
            "actor_access_epoch": base.actor_access_epoch + 1,
            "action": EducationCourseAction.APPEND_VERSION,
            "target_fingerprint": "b" * 64,
            "request_reference": "other-request",
            "idempotency_key": "other-key",
            "evaluated_at": base.evaluated_at + timedelta(seconds=1),
        }
        for field, value in mismatches.items():
            with self.subTest(field=field), transaction.atomic(), self.assertRaises(
                EducationCourseAuthorityMalformed
            ):
                EducationCourseAuthority(
                    provider=_Provider(replace(base, **{field: value}))
                ).qualify(self.request)

    def test_response_and_refusal_echo_types_and_predicates_are_exact(self):
        class StringSubclass(str):
            pass

        class IneffectiveTimezone(tzinfo):
            def utcoffset(self, value):
                return None

        base = self.response(self.request)
        malformed_echoes = {
            "database_alias": StringSubclass(base.database_alias),
            "actor_pk": True,
            "actor_identity_id": str(base.actor_identity_id),
            "actor_access_epoch": 0.0,
            "action": base.action.value,
            "target_fingerprint": StringSubclass(base.target_fingerprint),
            "request_reference": StringSubclass(base.request_reference),
            "idempotency_key": StringSubclass(base.idempotency_key),
            "evaluated_at": datetime(2026, 1, 1, tzinfo=IneffectiveTimezone()),
        }
        refusal = EducationCourseAuthorityRefusal(
            base.database_alias,
            base.actor_pk,
            base.actor_identity_id,
            base.actor_access_epoch,
            base.action,
            base.target_fingerprint,
            base.request_reference,
            base.idempotency_key,
            base.evaluated_at,
            EducationCourseRefusalCode.DENIED,
        )
        for template in (base, refusal):
            for field, value in malformed_echoes.items():
                with (
                    self.subTest(result=type(template).__name__, field=field),
                    transaction.atomic(),
                    self.assertRaises(EducationCourseAuthorityMalformed),
                ):
                    EducationCourseAuthority(
                        provider=_Provider(replace(template, **{field: value}))
                    ).qualify(self.request)

    def test_response_and_refusal_canonical_predicates_are_exact(self):
        base = self.response(self.request)
        refusal = EducationCourseAuthorityRefusal(
            base.database_alias,
            base.actor_pk,
            base.actor_identity_id,
            base.actor_access_epoch,
            base.action,
            base.target_fingerprint,
            base.request_reference,
            base.idempotency_key,
            base.evaluated_at,
            EducationCourseRefusalCode.DENIED,
        )
        malformed = {
            "database_alias": "",
            "actor_pk": 0,
            "actor_identity_id": None,
            "actor_access_epoch": -1,
            "action": "CREATE",
            "target_fingerprint": "A" * 64,
            "request_reference": " request",
            "idempotency_key": "Cafe\u0301",
            "evaluated_at": datetime(2026, 1, 1),
        }
        for template in (base, refusal):
            for field, value in malformed.items():
                with (
                    self.subTest(result=type(template).__name__, field=field),
                    transaction.atomic(),
                    self.assertRaises(EducationCourseAuthorityMalformed),
                ):
                    EducationCourseAuthority(
                        provider=_Provider(replace(template, **{field: value}))
                    ).qualify(self.request)

        response_values = ("", " authority", "a" * 256, "Cafe\u0301")
        for value in response_values:
            with transaction.atomic(), self.assertRaises(
                EducationCourseAuthorityMalformed
            ):
                EducationCourseAuthority(
                    provider=_Provider(replace(base, authority_reference=value))
                ).qualify(self.request)
        with transaction.atomic(), self.assertRaises(
            EducationCourseAuthorityMalformed
        ):
            EducationCourseAuthority(
                provider=_Provider(replace(refusal, refusal_code="DENIED"))
            ).qualify(self.request)

    def test_async_and_noncanonical_authority_reference_are_malformed(self):
        async def async_response(request):
            return self.response(request)

        malformed = replace(
            self.response(self.request), authority_reference=" authority:test "
        )
        for provider in (_Provider(async_response), _Provider(malformed)):
            with transaction.atomic(), self.assertRaises(
                EducationCourseAuthorityMalformed
            ):
                EducationCourseAuthority(provider=provider).qualify(self.request)