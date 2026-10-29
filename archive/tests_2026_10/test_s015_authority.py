import json
from dataclasses import FrozenInstanceError, asdict, replace
from datetime import datetime, timedelta, timezone
from uuid import UUID

from django.db import transaction
from django.test import TestCase

from core.models import AuthorityDecision, Identity
from core.organism_contract import (
    GENESIS_BOOTSTRAP_REASON,
    OrganismAction,
    QUALIFICATION_PLANE_MATRIX,
    QualificationPlane,
    QualificationPlaneState,
)
from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider
from src.intevia.services.organism_authority import (
    QualifiedS015AuthorityDecision,
    S015Authority,
    S015AuthorityDenied,
    S015AuthorityMalformed,
    S015AuthorityRefusalCode,
    S015OwnApplicantAuthorityRefusal,
    S015OwnApplicantAuthorityRequest,
    S015OwnApplicantAuthorityResponse,
    S015PostFoundingAuthorityRefusal,
    S015PostFoundingAuthorityRequest,
    S015PostFoundingAuthorityResponse,
    S015PreFoundingAuthorityRefusal,
    S015PreFoundingAuthorityRequest,
    S015PreFoundingAuthorityResponse,
    _facts_read,
    authority_target_fingerprint,
)
from tests.s015_0021_support import create_test_identity


class _ExactProvider:
    def evaluate_organism_authority(self, request):
        return S015PreFoundingAuthorityResponse(
            **asdict(request),
            authority_reference="authority:fixture",
        )


class _EchoMismatchProvider:
    def evaluate_organism_authority(self, request):
        values = asdict(request)
        values["request_reference"] = "request:wrong"
        return S015PreFoundingAuthorityResponse(
            **values,
            authority_reference="authority:fixture",
        )


class _Str(str):
    pass


class _CountingExactProvider:
    def __init__(self, mutate=None):
        self.calls = 0
        self.mutate = mutate

    def evaluate_organism_authority(self, request):
        self.calls += 1
        values = asdict(request)
        if self.mutate is not None:
            self.mutate(values)
        response_types = {
            S015PreFoundingAuthorityRequest: S015PreFoundingAuthorityResponse,
            S015PostFoundingAuthorityRequest: S015PostFoundingAuthorityResponse,
            S015OwnApplicantAuthorityRequest: S015OwnApplicantAuthorityResponse,
        }
        return response_types[type(request)](
            **values,
            authority_reference="authority:fixture",
        )


class _CountingRefusalProvider:
    def __init__(self, refusal_code, disclosed_refusal_code=None, authority_reference="authority:fixture"):
        self.calls = 0
        self.refusal_code = refusal_code
        self.disclosed_refusal_code = disclosed_refusal_code
        self.authority_reference = authority_reference

    def evaluate_organism_authority(self, request):
        self.calls += 1
        refusal_types = {
            S015PreFoundingAuthorityRequest: S015PreFoundingAuthorityRefusal,
            S015PostFoundingAuthorityRequest: S015PostFoundingAuthorityRefusal,
            S015OwnApplicantAuthorityRequest: S015OwnApplicantAuthorityRefusal,
        }
        return refusal_types[type(request)](
            **asdict(request),
            refusal_code=self.refusal_code,
            disclosed_refusal_code=self.disclosed_refusal_code,
            authority_reference=self.authority_reference,
        )


class _ProductionEncodedSeatRefusalProvider:
    def __init__(self):
        self.calls = 0
        self._production = ProductionApplicationAuthorityProvider()

    def evaluate_organism_authority(self, request):
        self.calls += 1
        return self._production._refuse(
            request,
            S015AuthorityRefusalCode.SBG_LO_NOT_ACTIVE,
            S015AuthorityRefusalCode.DENIED,
        )


class S015AuthorityContractTests(TestCase):
    def _request(self):
        return S015PreFoundingAuthorityRequest(
            database_alias="default",
            actor_pk=1,
            actor_identity_id=UUID(int=1),
            actor_access_epoch=0,
            action=OrganismAction.REGISTER_AUTHORITY_PRINCIPAL,
            command_subtype="REGISTER_AUTHORITY_PRINCIPAL/GENESIS_BOOTSTRAP",
            target_fingerprint="0" * 64,
            request_reference="request:fixture",
            idempotency_key="fixture",
            evaluated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

    def test_empty_electorate_refusal_code_is_catalogued(self):
        self.assertEqual(
            S015AuthorityRefusalCode.SB_BIRTHING_ELECTORATE_EMPTY.value,
            "SB_BIRTHING_ELECTORATE_EMPTY",
        )

    def test_matrix_rows_are_complete_and_noninheriting(self):
        # Brittle-test repair (27 Oct 2026): no frozen row total; every row covers the four planes, and the
        # SB-G and SE rows named below exist with their declared states.
        self.assertTrue(QUALIFICATION_PLANE_MATRIX)
        self.assertTrue(all(len(row) == len(QualificationPlane) for row in QUALIFICATION_PLANE_MATRIX.values()))
        adoption = QUALIFICATION_PLANE_MATRIX[
            "ADOPT_GOVERNING_INSTRUMENT/GOVERNANCE_CONSTITUTION"
        ]
        self.assertEqual(adoption[QualificationPlane.LEGAL].reason, "NO_LEGAL_CONCLUSION")
        self.assertIs(adoption[QualificationPlane.CONSTITUTIONAL].state,
                      QualificationPlaneState.PREREQUISITE_REQUIRED)
        admission = QUALIFICATION_PLANE_MATRIX["ADMIT_CIRCLE_PARTICIPANT"]
        self.assertEqual(admission, QUALIFICATION_PLANE_MATRIX[OrganismAction.SUBMIT_APPLICATION.value])
        for action in (
            OrganismAction.EFFECT_ENTITLEMENT_ELECTION,
            OrganismAction.RECORD_ENTITLEMENT_GRANT,
        ):
            with self.subTest(action=action):
                self.assertEqual(
                    QUALIFICATION_PLANE_MATRIX[action.value],
                    QUALIFICATION_PLANE_MATRIX[
                        "ADOPT_GOVERNING_INSTRUMENT/GOVERNANCE_CONSTITUTION"
                    ],
                )
        self.assertEqual(
            QUALIFICATION_PLANE_MATRIX["REQUEST_GOVERNANCE_SEAT"],
            QUALIFICATION_PLANE_MATRIX[OrganismAction.SUBMIT_APPLICATION.value],
        )
        ordinary = QUALIFICATION_PLANE_MATRIX[OrganismAction.SUBMIT_APPLICATION.value]
        for action in (
            OrganismAction.EFFECT_APPLICATION_SETTINGS,
            OrganismAction.EFFECT_APPLICATION_QUESTION,
            OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
        ):
            with self.subTest(action=action):
                self.assertEqual(QUALIFICATION_PLANE_MATRIX[action.value], ordinary)
        self.assertEqual(
            QUALIFICATION_PLANE_MATRIX[OrganismAction.RECORD_OFFICE_CONSENT.value],
            ordinary,
        )
        genesis = QUALIFICATION_PLANE_MATRIX[
            "REGISTER_AUTHORITY_PRINCIPAL/GENESIS_BOOTSTRAP"
        ]
        self.assertEqual(genesis[QualificationPlane.EXECUTION].reason, GENESIS_BOOTSTRAP_REASON)
        self.assertIs(
            genesis[QualificationPlane.PROPAGATION].state,
            QualificationPlaneState.FORBIDDEN,
        )

    def test_request_is_frozen_and_exact_echo_qualifies(self):
        request = self._request()
        with self.assertRaises(FrozenInstanceError):
            request.idempotency_key = "changed"
        with transaction.atomic():
            result = S015Authority(provider=_ExactProvider()).qualify(request)
        self.assertRegex(result.authority_decision_reference, r"^s015d1:[0-9a-f]{64}$")

    def test_echo_mismatch_is_refused(self):
        with transaction.atomic():
            with self.assertRaises(S015AuthorityMalformed):
                S015Authority(provider=_EchoMismatchProvider()).qualify(self._request())

    def test_entitlement_election_reference_reads_exact_birthing_facts(self):
        organism_uuid = UUID(int=2)
        request = S015PostFoundingAuthorityRequest(
            database_alias="default",
            actor_pk=1,
            actor_identity_id=UUID(int=1),
            actor_access_epoch=0,
            action=OrganismAction.EFFECT_ENTITLEMENT_ELECTION,
            command_subtype=OrganismAction.EFFECT_ENTITLEMENT_ELECTION.value,
            target_fingerprint="0" * 64,
            request_reference="request:fixture",
            idempotency_key="fixture",
            evaluated_at=datetime(2026, 10, 12, tzinfo=timezone.utc),
            active_membership_head_reference="membership-head:fixture",
            contextual_role_head_references=(),
            effective_at=datetime(2026, 10, 12, tzinfo=timezone.utc),
            target={
                "action": OrganismAction.EFFECT_ENTITLEMENT_ELECTION.value,
                "organism_uuid": organism_uuid,
                "snapshot_uuid": str(UUID(int=6)),
            },
            living_organism_id=organism_uuid,
            version_uuid=UUID(int=7),
            version_kind="EFFECT",
        )
        content = {
            "kind": "BIRTHING",
            "c": "governance-configuration-version:fixture",
            "s": "application-settings-version:fixture",
            "q": "application-question-version:fixture",
            "o": str(UUID(int=5)),
            "n": str(UUID(int=6)),
        }
        reference = "s015a1:" + json.dumps(content, separators=(",", ":"))

        facts = _facts_read(request, reference)
        self.assertEqual(
            {
                fact["relation"]: fact["reference"]
                for fact in facts
                if fact["relation"] != "core_identity"
                and fact["relation"] != "core_livingorganism"
            },
            {
                "core_logovernanceconfigurationversion":
                    "governance-configuration-version:fixture",
                "core_loapplicationsettingsversion":
                    "application-settings-version:fixture",
                "core_loapplicationquestionversion":
                    "application-question-version:fixture",
                "core_contextualroleassignment": str(UUID(int=5)),
                "core_lobirthingsnapshot": str(UUID(int=6)),
            },
        )

        gateway_version_uuid = UUID(int=8)
        gateway_request = replace(
            request,
            target={
                "action": OrganismAction.EFFECT_ENTITLEMENT_ELECTION.value,
                "organism_uuid": organism_uuid,
                "version_uuid": gateway_version_uuid,
            },
            version_uuid=gateway_version_uuid,
        )
        gateway_content = {
            "c": "governance-configuration-version:fixture",
            "m": str(UUID(int=9)),
            "o": str(UUID(int=10)),
            "s": "application-settings-version:current",
            "t": "CHANGE_FLAG_NO",
            "v": str(gateway_version_uuid),
        }
        gateway_facts = _facts_read(
            gateway_request,
            "s015a1:" + json.dumps(gateway_content, separators=(",", ":")),
        )
        self.assertEqual(
            len(_facts_read(gateway_request, 's015a1:{"x":"DENIED"}')),
            2,
        )
        self.assertEqual(
            {
                (fact["relation"], fact["reference"])
                for fact in gateway_facts
                if fact["relation"] not in {"core_identity", "core_livingorganism"}
            },
            {
                (
                    "core_logovernanceconfigurationversion",
                    "governance-configuration-version:fixture",
                ),
                ("core_organismmembershiptransition", str(UUID(int=9))),
                ("core_contextualroleassignmentstateevent", str(UUID(int=10))),
                ("core_loapplicationsettingsversion", "application-settings-version:current"),
                (
                    "core_loapplicationsettingsversion",
                    f"application-settings-version:{gateway_version_uuid}",
                ),
            },
        )
        with self.assertRaises(S015AuthorityMalformed):
            _facts_read(gateway_request, reference)
        with self.assertRaises(S015AuthorityMalformed):
            _facts_read(
                request,
                "s015a1:" + json.dumps(gateway_content, separators=(",", ":")),
            )
        with self.assertRaises(S015AuthorityMalformed):
            _facts_read(
                gateway_request,
                "s015a1:" + json.dumps({**gateway_content, "unrelated": "fixture"}),
            )
        with self.assertRaises(S015AuthorityMalformed):
            _facts_read(
                gateway_request,
                's015a1:{"x":"DENIED","unrelated":"fixture"}',
            )

        with self.assertRaises(S015AuthorityMalformed):
            _facts_read(
                request,
                "s015a1:" + json.dumps({**content, "unrelated": "fixture"}),
            )
        with self.assertRaises(S015AuthorityMalformed):
            missing_snapshot = {
                key: value for key, value in content.items() if key != "n"
            }
            _facts_read(request, "s015a1:" + json.dumps(missing_snapshot))
        with self.assertRaises(S015AuthorityMalformed):
            _facts_read(request, None)

        self.assertEqual(
            len(_facts_read(request, 's015a1:{"x":"DENIED"}')),
            2,
        )

    def _recorded_request(self, **overrides):
        action = OrganismAction.RECORD_ENTITLEMENT_GRANT
        organism_uuid = UUID(int=2)
        target = {"action": action.value, "organism_uuid": str(organism_uuid)}
        request = S015PostFoundingAuthorityRequest(
            database_alias="default",
            actor_pk=1,
            actor_identity_id=UUID(int=1),
            actor_access_epoch=0,
            action=action,
            command_subtype=action.value,
            target_fingerprint=authority_target_fingerprint(target),
            request_reference="request:fixture",
            idempotency_key="fixture",
            evaluated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            active_membership_head_reference="membership-head:fixture",
            contextual_role_head_references=(),
            target=target,
            living_organism_id=organism_uuid,
        )
        return replace(request, **overrides)

    def _identity_fixture(self):
        return create_test_identity(
            username="s015_authority_witness",
            id=1,
            access_state=Identity.AccessState.ACTIVE,
        )

    def test_bool_for_int_echo_is_refused_as_mismatch(self):
        for field, value in (("actor_pk", True), ("actor_access_epoch", False)):
            with self.subTest(field=field):
                provider = _CountingExactProvider(
                    mutate=lambda values, field=field, value=value: values.__setitem__(field, value)
                )
                before = AuthorityDecision.objects.count()
                with transaction.atomic():
                    with self.assertRaises(S015AuthorityMalformed) as caught:
                        S015Authority(provider=provider).qualify(self._request())
                self.assertEqual(str(caught.exception), "authority response echo mismatch")
                self.assertIsNone(caught.exception.__cause__)
                self.assertEqual(provider.calls, 1)
                self.assertEqual(AuthorityDecision.objects.count(), before)

    def test_non_canonical_echo_is_refused_as_malformed(self):
        for field, value in (("actor_pk", 1.0), ("request_reference", _Str("request:fixture"))):
            with self.subTest(field=field):
                provider = _CountingExactProvider(
                    mutate=lambda values, field=field, value=value: values.__setitem__(field, value)
                )
                before = AuthorityDecision.objects.count()
                with transaction.atomic():
                    with self.assertRaises(S015AuthorityMalformed) as caught:
                        S015Authority(provider=provider).qualify(self._request())
                self.assertEqual(str(caught.exception), "authority response echo is not canonical")
                self.assertIsInstance(caught.exception.__cause__, TypeError)
                self.assertEqual(provider.calls, 1)
                self.assertEqual(AuthorityDecision.objects.count(), before)

    def test_plain_string_action_echo_qualifies(self):
        provider = _CountingExactProvider(
            mutate=lambda values: values.__setitem__("action", "REGISTER_AUTHORITY_PRINCIPAL")
        )
        with transaction.atomic():
            result = S015Authority(provider=provider).qualify(self._request())
        self.assertIsInstance(result, QualifiedS015AuthorityDecision)
        self.assertRegex(result.authority_decision_reference, r"^s015d1:[0-9a-f]{64}$")
        self.assertEqual(result.action, OrganismAction.REGISTER_AUTHORITY_PRINCIPAL)
        self.assertEqual(provider.calls, 1)

    def test_other_zone_evaluated_at_echo_qualifies(self):
        request = self._request()
        with transaction.atomic():
            expected = S015Authority(provider=_ExactProvider()).qualify(request)
        provider = _CountingExactProvider(
            mutate=lambda values: values.__setitem__(
                "evaluated_at", request.evaluated_at.astimezone(timezone(timedelta(hours=1)))
            )
        )
        with transaction.atomic():
            result = S015Authority(provider=provider).qualify(request)
        self.assertEqual(
            result.authority_decision_reference,
            expected.authority_decision_reference,
        )
        self.assertEqual(provider.calls, 1)

    def test_non_canonical_result_field_is_refused_before_recording(self):
        request = self._recorded_request()
        provider = _CountingRefusalProvider(
            S015AuthorityRefusalCode.DENIED,
            disclosed_refusal_code=1.5,
        )
        before = AuthorityDecision.objects.count()
        with transaction.atomic():
            with self.assertRaises(S015AuthorityMalformed) as caught:
                S015Authority(provider=provider).qualify(request)
        self.assertEqual(str(caught.exception), "authority result is not canonical")
        self.assertIsInstance(caught.exception.__cause__, TypeError)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(AuthorityDecision.objects.count(), before)

    def test_refusal_authority_reference_must_be_canonical(self):
        for reference, message in (
            (123, "authority_reference must be a string"),
            ("a" * 256, "authority_reference is not canonical"),
        ):
            with self.subTest(reference=reference):
                provider = _CountingRefusalProvider(
                    S015AuthorityRefusalCode.DENIED,
                    authority_reference=reference,
                )
                before = AuthorityDecision.objects.count()
                with transaction.atomic():
                    with self.assertRaises(S015AuthorityMalformed) as caught:
                        S015Authority(provider=provider).qualify(self._recorded_request())
                self.assertEqual(str(caught.exception), message)
                self.assertEqual(provider.calls, 1)
                self.assertEqual(AuthorityDecision.objects.count(), before)

    def test_refusal_without_authority_reference_is_still_denied(self):
        provider = _CountingRefusalProvider(
            S015AuthorityRefusalCode.DENIED,
            authority_reference=None,
        )
        with transaction.atomic():
            with self.assertRaises(S015AuthorityDenied) as caught:
                S015Authority(provider=provider).qualify(self._request())
        self.assertEqual(str(caught.exception), "organism authority denied")
        self.assertIs(caught.exception.refusal_code, S015AuthorityRefusalCode.DENIED)
        self.assertRegex(
            caught.exception.authority_decision_reference,
            r"^s015d1:[0-9a-f]{64}$",
        )
        self.assertEqual(provider.calls, 1)

    def test_recorded_request_without_target_is_refused_before_provider(self):
        for label, overrides in (
            ("none", {"target": None, "target_fingerprint": "0" * 64}),
            ("empty", {"target": {}, "target_fingerprint": authority_target_fingerprint({})}),
        ):
            with self.subTest(target=label):
                provider = _CountingExactProvider()
                request = self._recorded_request(**overrides)
                before = AuthorityDecision.objects.count()
                with transaction.atomic():
                    with self.assertRaises(S015AuthorityMalformed) as caught:
                        S015Authority(provider=provider).qualify(request)
                self.assertEqual(
                    str(caught.exception),
                    "recorded request requires a non-empty plain target",
                )
                self.assertEqual(provider.calls, 0)
                self.assertEqual(AuthorityDecision.objects.count(), before)

    def test_recorded_request_with_target_qualifies_and_writes_that_target(self):
        identity = self._identity_fixture()
        request = replace(
            self._recorded_request(),
            actor_identity_id=identity.identity_id,
            actor_access_epoch=identity.access_epoch,
        )
        self.assertEqual(request.target_fingerprint, authority_target_fingerprint(request.target))
        provider = _CountingExactProvider()
        before = AuthorityDecision.objects.count()
        with transaction.atomic():
            result = S015Authority(provider=provider).qualify(request)
        self.assertIsInstance(result, QualifiedS015AuthorityDecision)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(AuthorityDecision.objects.count(), before + 1)
        row = AuthorityDecision.objects.get(decision_reference=result.authority_decision_reference)
        self.assertEqual(row.outcome, "QUALIFIED")
        self.assertEqual(row.target, request.target)
        self.assertEqual(row.target_fingerprint, request.target_fingerprint)
        self.assertEqual(row.action, OrganismAction.RECORD_ENTITLEMENT_GRANT.value)

    def test_request_string_fields_must_be_exact_str(self):
        base = self._request()
        withdrawal_target = {
            "action": OrganismAction.WITHDRAW_APPLICATION.value,
            "application_uuid": str(UUID(int=3)),
        }
        withdrawal = S015OwnApplicantAuthorityRequest(
            database_alias=base.database_alias,
            actor_pk=base.actor_pk,
            actor_identity_id=base.actor_identity_id,
            actor_credential_pk=1,
            actor_access_epoch=base.actor_access_epoch,
            action=OrganismAction.WITHDRAW_APPLICATION,
            command_subtype=OrganismAction.WITHDRAW_APPLICATION.value,
            target_fingerprint=authority_target_fingerprint(withdrawal_target),
            request_reference=base.request_reference,
            idempotency_key=base.idempotency_key,
            evaluated_at=base.evaluated_at,
            living_organism_pk=1,
            application_pk=1,
            target=withdrawal_target,
            predecessor_reference=1.5,
        )
        cases = (
            (
                replace(base, command_subtype=_Str(base.command_subtype)),
                "command_subtype is invalid",
                None,
            ),
            (
                replace(base, target_fingerprint=_Str("0" * 64)),
                "target_fingerprint is invalid",
                None,
            ),
            (
                self._recorded_request(outcome=_Str("ACCEPT")),
                "outcome is invalid",
                None,
            ),
            (withdrawal, "authority request is malformed", TypeError),
        )
        for request, message, cause_type in cases:
            with self.subTest(message=message, request_type=type(request).__name__):
                provider = _CountingExactProvider()
                before = AuthorityDecision.objects.count()
                with transaction.atomic():
                    with self.assertRaises(S015AuthorityMalformed) as caught:
                        S015Authority(provider=provider).qualify(request)
                self.assertEqual(str(caught.exception), message)
                if cause_type is not None:
                    self.assertIsInstance(caught.exception.__cause__, cause_type)
                self.assertEqual(provider.calls, 0)
                self.assertEqual(AuthorityDecision.objects.count(), before)

    def test_seat_request_refusal_is_denied_with_refused_row(self):
        identity = self._identity_fixture()
        action = OrganismAction.REQUEST_GOVERNANCE_SEAT
        organism_uuid = UUID(int=2)
        target = {
            "action": action.value,
            "organism_uuid": str(organism_uuid),
            "circle_uuid": str(UUID(int=3)),
            "kind": "GOVERNANCE",
            "seat_rule_uuid": str(UUID(int=4)),
            "seat_id": "seat:fixture",
            "applicant_identity_uuid": str(identity.identity_id),
        }
        request = replace(
            self._recorded_request(),
            actor_identity_id=identity.identity_id,
            actor_access_epoch=identity.access_epoch,
            action=action,
            command_subtype=action.value,
            target=target,
            target_fingerprint=authority_target_fingerprint(target),
            living_organism_id=organism_uuid,
        )
        provider = _ProductionEncodedSeatRefusalProvider()
        before = AuthorityDecision.objects.count()
        with transaction.atomic():
            with self.assertRaises(S015AuthorityDenied) as caught:
                S015Authority(provider=provider).qualify(request)
        self.assertIs(caught.exception.refusal_code, S015AuthorityRefusalCode.SBG_LO_NOT_ACTIVE)
        self.assertIs(caught.exception.disclosed_refusal_code, S015AuthorityRefusalCode.DENIED)
        self.assertRegex(
            caught.exception.authority_decision_reference,
            r"^s015d1:[0-9a-f]{64}$",
        )
        self.assertEqual(provider.calls, 1)
        self.assertEqual(AuthorityDecision.objects.count(), before + 1)
        row = AuthorityDecision.objects.get(decision_reference=caught.exception.authority_decision_reference)
        self.assertEqual(row.outcome, "REFUSED")
        self.assertEqual(row.refusal_code, "SBG_LO_NOT_ACTIVE")
        self.assertEqual(row.authority_reference, 's015a1:{"x":"SBG_LO_NOT_ACTIVE"}')
        self.assertEqual(
            {fact["relation"] for fact in row.facts_read},
            {"core_identity", "core_livingorganism"},
        )

    def test_validation_overflow_is_translated_to_malformed(self):
        request = replace(
            self._request(),
            evaluated_at=datetime(
                9999,
                12,
                31,
                23,
                30,
                tzinfo=timezone(timedelta(hours=-1)),
            ),
        )
        provider = _CountingExactProvider()
        with transaction.atomic():
            with self.assertRaises(S015AuthorityMalformed) as caught:
                S015Authority(provider=provider).qualify(request)
        self.assertEqual(str(caught.exception), "authority request is malformed")
        self.assertIsInstance(caught.exception.__cause__, OverflowError)
        self.assertEqual(provider.calls, 0)
