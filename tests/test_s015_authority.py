import json
from dataclasses import FrozenInstanceError, asdict, replace
from datetime import datetime, timezone
from uuid import UUID

from django.db import transaction
from django.test import TestCase

from core.organism_contract import (
    GENESIS_BOOTSTRAP_REASON,
    OrganismAction,
    QUALIFICATION_PLANE_MATRIX,
    QualificationPlane,
    QualificationPlaneState,
)
from src.intevia.services.organism_authority import (
    S015Authority,
    S015AuthorityMalformed,
    S015AuthorityRefusalCode,
    S015PostFoundingAuthorityRequest,
    S015PreFoundingAuthorityRequest,
    S015PreFoundingAuthorityResponse,
    _facts_read,
)


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

    def test_matrix_has_42_complete_noninheriting_rows(self):
        # SB-G adds three rows; SE adds the adopted election and grant rows.
        self.assertEqual(len(QUALIFICATION_PLANE_MATRIX), 42)
        self.assertTrue(all(len(row) == 4 for row in QUALIFICATION_PLANE_MATRIX.values()))
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
