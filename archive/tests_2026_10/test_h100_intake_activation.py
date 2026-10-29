"""Witness the H100 activation census over abstract intake, receipt, and decision rows."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from django.db import connection
from django.test import TransactionTestCase

from core.models import (
    AuthorityDecision,
    Identity,
    IntakeEntry,
    OrganismCommandReceipt,
)
from core.organism_contract import OrganismAction
from tests.s015_0021_support import create_test_identity
from src.intevia.services import command_intake, organism_authority
from src.intevia.services.command_intake import (
    ActorRef,
    ENTRY_ACTIONS,
    FINGERPRINT_SCHEMA,
    INSERT_COMMAND_INTAKE_SQL,
    IntakeShape,
    UnaccountedRecord,
    run_activation_step,
    slots,
)
from src.intevia.services.organism_authority import (
    S015AuthorityRefusalCode,
    S015PostFoundingAuthorityRefusal,
    S015PostFoundingAuthorityRequest,
    S015PostFoundingAuthorityResponse,
    authority_target_fingerprint,
)


BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


class _EchoProvider:
    provider_identity = "H100 test provider"
    provider_version = "1"

    def __init__(self, *, refuse=False):
        self.refuse = refuse

    def evaluate_organism_authority(self, request):
        fields = asdict(request)
        refusal_code = (
            S015AuthorityRefusalCode.E_IDENTITY_INACTIVE if self.refuse else None
        )
        fields["authority_reference"] = _authority_reference(
            request,
            refusal_code=refusal_code,
        )
        if self.refuse:
            fields["refusal_code"] = refusal_code
            fields["disclosed_refusal_code"] = refusal_code
            return S015PostFoundingAuthorityRefusal(**fields)
        return S015PostFoundingAuthorityResponse(**fields)


def _authority_reference(
    request: S015PostFoundingAuthorityRequest,
    *,
    refusal_code: S015AuthorityRefusalCode | None = None,
) -> str:
    action = request.action
    if action is OrganismAction.REQUEST_GOVERNANCE_SEAT:
        return 's015a1:{"kind":"GOVERNANCE_SEAT_REQUEST"}'
    if action is OrganismAction.ADMIT_CIRCLE_PARTICIPANT:
        return 's015a1:{"kind":"GOVERNANCE","route":"CIRCLE_FOUNDING"}'
    if action is OrganismAction.EFFECT_ENTITLEMENT_ELECTION:
        if refusal_code is not None:
            content = {"x": refusal_code.value}
        else:
            if request.version_uuid is None:
                raise ValueError("election fixture requires an effect version")
            content = {
                "c": "governance-configuration-version:fixture",
                "m": str(uuid4()),
                "o": str(uuid4()),
                "s": "application-settings-version:fixture",
                "t": "CHANGE_FLAG_NO",
                "v": str(request.version_uuid),
            }
        return "s015a1:" + organism_authority.canonical_json_bytes(content).decode(
            "utf-8"
        )
    if action is OrganismAction.TRANSITION_LIVING_ORGANISM:
        if request.command_subtype == "TRANSITION_LIVING_ORGANISM/BIRTHING":
            return 's015a1:{"kind":"BIRTHING"}'
        return 's015a1:{"kind":"PHASE_ADVANCE"}'
    return "s015a1:{}"


def _actor_ref(username: str) -> ActorRef:
    identity = create_test_identity(
        username=username,
        access_state=Identity.AccessState.ACTIVE,
    )
    return ActorRef(
        pk=identity.pk,
        identity_id=identity.identity_id,
        access_epoch=identity.access_epoch,
    )


def _shape(key: str, *, target_organism_id=None) -> IntakeShape:
    return IntakeShape(
        idempotency_key=key,
        request_reference=f"request:{key}",
        target={"fixture": key},
        command={"fixture": True},
        omitted=(),
        target_organism_id=target_organism_id,
    )


class H100IntakeActivationTests(TransactionTestCase):
    ISOLATION_FULL_CAPTURE = True

    def setUp(self):
        self.actor = _actor_ref(f"h100-activation-a-{uuid4().hex}")
        self.other_actor = _actor_ref(f"h100-activation-b-{uuid4().hex}")
        self.target_organism_id = uuid4()

    def _insert_binding(
        self,
        entry: IntakeEntry,
        key: str,
        *,
        actor: ActorRef | None = None,
        target=None,
        action: str | None = None,
    ):
        actor = self.actor if actor is None else actor
        action = ENTRY_ACTIONS[entry] if action is None else action
        shape = _shape(key, target_organism_id=target)
        values = [
            actor.pk,
            actor.identity_id,
            actor.access_epoch,
            entry.value,
            action,
            key,
            shape.request_reference,
            "a" * 64,
            FINGERPRINT_SCHEMA,
            target,
        ]
        with connection.cursor() as cursor:
            cursor.execute(INSERT_COMMAND_INTAKE_SQL, values)
            return cursor.fetchone()

    def _compound_stage_records(
        self,
        actor: ActorRef,
        key: str,
        target,
        received_at,
        *,
        refuse_admission=False,
    ):
        request_slot, admission_slot = slots(
            IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND,
            key,
            target,
        )
        self._receipt(actor, request_slot.action, request_slot.idempotency_key)
        self._decision(
            actor,
            OrganismAction.REQUEST_GOVERNANCE_SEAT,
            request_slot.idempotency_key,
            received_at,
        )
        self._receipt(actor, admission_slot.action, admission_slot.idempotency_key)
        self._decision(
            actor,
            OrganismAction.ADMIT_CIRCLE_PARTICIPANT,
            admission_slot.idempotency_key,
            received_at,
            refuse=refuse_admission,
        )
        return request_slot, admission_slot

    def _receipt(self, actor: ActorRef, action: str, key: str):
        receipt_uuid = uuid4()
        receipt = OrganismCommandReceipt(
            receipt_uuid=receipt_uuid,
            actor_id=actor.pk,
            action=action,
            idempotency_key=key,
            request_reference=f"request:{receipt_uuid.hex}",
            payload_fingerprint="b" * 64,
            authority_decision_reference="s015d1:" + "c" * 64,
            lineage_reference="s015l1:" + hashlib.sha256(
                f"h100-fixture-lineage:{receipt_uuid}".encode("ascii")
            ).hexdigest(),
            result_payload={"fixture": True},
            completed_at=BASE_TIME,
        )
        receipt.full_clean()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO public.core_organismcommandreceipt (
                    receipt_uuid, actor_id, action, idempotency_key,
                    request_reference, payload_fingerprint,
                    authority_decision_reference, lineage_reference,
                    result_payload, completed_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                RETURNING id
                """,
                [
                    receipt.receipt_uuid,
                    receipt.actor_id,
                    receipt.action,
                    receipt.idempotency_key,
                    receipt.request_reference,
                    receipt.payload_fingerprint,
                    receipt.authority_decision_reference,
                    receipt.lineage_reference,
                    json.dumps(receipt.result_payload),
                    receipt.completed_at,
                ],
            )
            return cursor.fetchone()[0]

    def _decision(
        self,
        actor: ActorRef,
        action: OrganismAction,
        key: str,
        evaluated_at,
        *,
        refuse=False,
        command_subtype: str | None = None,
    ):
        target_organism_id = self.target_organism_id
        if action is OrganismAction.EFFECT_ENTITLEMENT_ELECTION:
            version_uuid = uuid4()
            target = {
                "action": action.value,
                "organism_uuid": target_organism_id,
                "version_uuid": version_uuid,
            }
            version_kind = "EFFECT"
        else:
            target = {"fixture": key}
            version_uuid = None
            version_kind = None
        request = S015PostFoundingAuthorityRequest(
            database_alias="default",
            actor_pk=actor.pk,
            actor_identity_id=actor.identity_id,
            actor_access_epoch=actor.access_epoch,
            action=action,
            command_subtype=(
                action.value if command_subtype is None else command_subtype
            ),
            target_fingerprint=authority_target_fingerprint(target),
            request_reference=f"request:{uuid4().hex}",
            idempotency_key=key,
            evaluated_at=evaluated_at,
            active_membership_head_reference="fixture-membership-head",
            contextual_role_head_references=(),
            effective_at=evaluated_at,
            target=target,
            living_organism_id=target_organism_id,
            version_uuid=version_uuid,
            version_kind=version_kind,
        )
        provider = _EchoProvider(refuse=refuse)
        response = provider.evaluate_organism_authority(request)
        decision_payload_bytes = organism_authority.canonical_json_bytes(
            asdict(response)
        )
        target_bytes = organism_authority.canonical_json_bytes(target)
        facts_read = organism_authority._facts_read(
            request,
            response.authority_reference,
        )
        decision = AuthorityDecision(
            decision_reference="s015d1:"
            + hashlib.sha256(
                organism_authority._DECISION_DOMAIN + decision_payload_bytes
            ).hexdigest(),
            decision_payload=json.loads(decision_payload_bytes.decode("utf-8")),
            decision_payload_bytes=decision_payload_bytes,
            request_kind=type(request).__name__,
            action=action.value,
            command_subtype=request.command_subtype,
            actor_id=actor.pk,
            actor_identity_id=actor.identity_id,
            actor_access_epoch=actor.access_epoch,
            actor_credential_pk=None,
            target=json.loads(target_bytes.decode("utf-8")),
            target_bytes=target_bytes,
            target_fingerprint=authority_target_fingerprint(target),
            effective_at=evaluated_at,
            evaluated_at=evaluated_at,
            authority_reference=response.authority_reference,
            facts_read=facts_read,
            outcome="REFUSED" if refuse else "QUALIFIED",
            refusal_code=(
                response.refusal_code.value if refuse else None
            ),
            provider_identity=provider.provider_identity,
            provider_version=provider.provider_version,
            request_reference=request.request_reference,
            idempotency_key=key,
            recorded_at=None,
        )
        decision.full_clean()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO public.core_authoritydecision (
                    decision_reference, decision_payload,
                    decision_payload_bytes, request_kind, action,
                    command_subtype, actor_id, actor_identity_id,
                    actor_access_epoch, actor_credential_pk, target,
                    target_bytes, target_fingerprint, effective_at,
                    evaluated_at, authority_reference, facts_read, outcome,
                    refusal_code, provider_identity, provider_version,
                    request_reference, idempotency_key, recorded_at
                )
                VALUES (
                    %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s::jsonb, %s, %s, %s, %s, %s, %s::jsonb, %s, %s,
                    %s, %s, %s, %s, %s
                )
                RETURNING id
                """,
                [
                    decision.decision_reference,
                    decision_payload_bytes.decode("utf-8"),
                    decision.decision_payload_bytes,
                    decision.request_kind,
                    decision.action,
                    decision.command_subtype,
                    decision.actor_id,
                    decision.actor_identity_id,
                    decision.actor_access_epoch,
                    decision.actor_credential_pk,
                    target_bytes.decode("utf-8"),
                    decision.target_bytes,
                    decision.target_fingerprint,
                    decision.effective_at,
                    decision.evaluated_at,
                    decision.authority_reference,
                    organism_authority.canonical_json_bytes(facts_read).decode(
                        "utf-8"
                    ),
                    decision.outcome,
                    decision.refusal_code,
                    decision.provider_identity,
                    decision.provider_version,
                    decision.request_reference,
                    decision.idempotency_key,
                    decision.recorded_at,
                ],
            )
            return cursor.fetchone()[0]

    def _assert_records(self, actual: list[UnaccountedRecord], expected):
        self.assertEqual(
            {(row.kind, row.action, row.idempotency_key, row.actor_id) for row in actual},
            expected,
        )

    def test_iw_25_unbound_receipts_decisions_and_derived_stage_records_are_reported(self):
        direct_key = f"h100-unbound:{uuid4().hex}"
        stage_key = f"h100-compound:{uuid4().hex}"
        request_slot, admission_slot = slots(
            IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND,
            stage_key,
            self.target_organism_id,
        )
        self._receipt(self.actor, OrganismAction.TRANSITION_LIVING_ORGANISM.value, direct_key)
        self._decision(
            self.actor,
            OrganismAction.TRANSITION_LIVING_ORGANISM,
            direct_key,
            BASE_TIME,
            command_subtype="TRANSITION_LIVING_ORGANISM/PHASE_ADVANCE",
        )
        self._receipt(self.actor, request_slot.action, request_slot.idempotency_key)
        self._decision(
            self.actor,
            OrganismAction.REQUEST_GOVERNANCE_SEAT,
            request_slot.idempotency_key,
            BASE_TIME,
        )
        self._decision(self.actor, OrganismAction.ADMIT_CIRCLE_PARTICIPANT, admission_slot.idempotency_key, BASE_TIME)

        actual = run_activation_step(
            "default",
            (IntakeEntry.ADVANCE_PHASE, IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND),
        )
        self._assert_records(
            actual,
            {
                ("receipt", OrganismAction.TRANSITION_LIVING_ORGANISM.value, direct_key, self.actor.pk),
                ("decision", OrganismAction.TRANSITION_LIVING_ORGANISM.value, direct_key, self.actor.pk),
                ("receipt", request_slot.action, request_slot.idempotency_key, self.actor.pk),
                ("decision", request_slot.action, request_slot.idempotency_key, self.actor.pk),
                ("decision", OrganismAction.ADMIT_CIRCLE_PARTICIPANT.value, admission_slot.idempotency_key, self.actor.pk),
            },
        )

    def test_iw_25_direct_binding_accounts_matching_receipt_and_decision(self):
        key = f"h100-accounted:{uuid4().hex}"
        _, received_at = self._insert_binding(IntakeEntry.ADVANCE_PHASE, key)
        self._receipt(self.actor, OrganismAction.TRANSITION_LIVING_ORGANISM.value, key)
        self._decision(
            self.actor,
            OrganismAction.TRANSITION_LIVING_ORGANISM,
            key,
            received_at,
            command_subtype="TRANSITION_LIVING_ORGANISM/PHASE_ADVANCE",
        )
        self.assertEqual(
            run_activation_step("default", (IntakeEntry.ADVANCE_PHASE,)),
            [],
        )

    def test_iw_25_birthing_c1_accounts_decision_not_receipt(self):
        key = f"h100-birthing:{uuid4().hex}"
        _, received_at = self._insert_binding(IntakeEntry.BIRTHING, key)
        self._receipt(self.actor, OrganismAction.TRANSITION_LIVING_ORGANISM.value, key)
        self._decision(
            self.actor,
            OrganismAction.TRANSITION_LIVING_ORGANISM,
            key,
            received_at,
            command_subtype="TRANSITION_LIVING_ORGANISM/BIRTHING",
        )
        self._decision(
            self.actor,
            OrganismAction.EFFECT_ENTITLEMENT_ELECTION,
            key,
            received_at,
        )
        unclaimed = self._receipt(
            self.actor,
            OrganismAction.EFFECT_ENTITLEMENT_ELECTION.value,
            key,
        )

        actual = run_activation_step("default", (IntakeEntry.BIRTHING,))
        self._assert_records(
            actual,
            {
                (
                    "receipt",
                    OrganismAction.EFFECT_ENTITLEMENT_ELECTION.value,
                    key,
                    self.actor.pk,
                ),
            },
        )
        self.assertEqual(actual[0].record_id, unclaimed)

    def test_iw_25_compound_binding_accounts_both_derived_slots(self):
        key = f"h100-compound-bound:{uuid4().hex}"
        target = self.target_organism_id
        _, received_at = self._insert_binding(
            IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND,
            key,
            target=target,
        )
        self._compound_stage_records(
            self.actor,
            key,
            target,
            received_at,
            refuse_admission=True,
        )
        self.assertEqual(
            run_activation_step(
                "default",
                (IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND,),
            ),
            [],
        )

    def test_iw_25_wrong_actor_key_and_decision_time_are_unaccounted(self):
        key = f"h100-mismatch:{uuid4().hex}"
        _, received_at = self._insert_binding(IntakeEntry.ADVANCE_PHASE, key)
        wrong_actor = self._receipt(
            self.other_actor,
            OrganismAction.TRANSITION_LIVING_ORGANISM.value,
            key,
        )
        wrong_key = self._receipt(
            self.actor,
            OrganismAction.TRANSITION_LIVING_ORGANISM.value,
            key + ":other",
        )
        wrong_time = self._decision(
            self.actor,
            OrganismAction.TRANSITION_LIVING_ORGANISM,
            key,
            received_at + timedelta(seconds=1),
            command_subtype="TRANSITION_LIVING_ORGANISM/PHASE_ADVANCE",
        )

        actual = run_activation_step("default", (IntakeEntry.ADVANCE_PHASE,))
        self.assertEqual(
            {record.record_id for record in actual},
            {wrong_actor, wrong_key, wrong_time},
        )
        self._assert_records(
            actual,
            {
                (
                    "receipt",
                    OrganismAction.TRANSITION_LIVING_ORGANISM.value,
                    key,
                    self.other_actor.pk,
                ),
                (
                    "receipt",
                    OrganismAction.TRANSITION_LIVING_ORGANISM.value,
                    key + ":other",
                    self.actor.pk,
                ),
                (
                    "decision",
                    OrganismAction.TRANSITION_LIVING_ORGANISM.value,
                    key,
                    self.actor.pk,
                ),
            },
        )
        self.assertIn(wrong_time, {record.record_id for record in actual})

    def test_iw_25_refused_decision_is_still_claimed_by_matching_binding(self):
        key = f"h100-refused:{uuid4().hex}"
        _, received_at = self._insert_binding(IntakeEntry.ADVANCE_PHASE, key)
        self._decision(
            self.actor,
            OrganismAction.TRANSITION_LIVING_ORGANISM,
            key,
            received_at,
            refuse=True,
            command_subtype="TRANSITION_LIVING_ORGANISM/PHASE_ADVANCE",
        )
        self.assertEqual(
            run_activation_step("default", (IntakeEntry.ADVANCE_PHASE,)),
            [],
        )

    def test_activation_group_expands_before_the_namespace_scan(self):
        self.assertEqual(
            run_activation_step("default", (IntakeEntry.ADVANCE_PHASE,)),
            [],
        )
        self.assertEqual(
            frozenset((IntakeEntry.ADVANCE_PHASE, IntakeEntry.BIRTHING, IntakeEntry.ACTIVATE_BETA)),
            command_intake._activation_entries((IntakeEntry.ADVANCE_PHASE,)),
        )

    def test_iw_25_unbound_draft_decision_is_reported_in_g3(self):
        key = f"h100-draft:{uuid4().hex}"
        self._decision(
            self.actor,
            OrganismAction.EFFECT_APPLICATION_SETTINGS,
            key,
            BASE_TIME,
        )
        actual = run_activation_step(
            "default",
            (IntakeEntry.SAVE_GATEWAY_DRAFT,),
        )
        self._assert_records(
            actual,
            {
                (
                    "decision",
                    OrganismAction.EFFECT_APPLICATION_SETTINGS.value,
                    key,
                    self.actor.pk,
                ),
            },
        )

    def test_iw_25_two_birthing_beta_bindings_account_shared_election_history_in_both_orders(self):
        for reverse in (False, True):
            key = f"h100-shared-time:{reverse}:{uuid4().hex}"
            entries = (
                (IntakeEntry.ACTIVATE_BETA, IntakeEntry.BIRTHING)
                if reverse
                else (IntakeEntry.BIRTHING, IntakeEntry.ACTIVATE_BETA)
            )
            first_entry, second_entry = entries
            first_row = self._insert_binding(first_entry, key)
            second_row = self._insert_binding(second_entry, key)
            first_time, second_time = first_row[1], second_row[1]
            self.assertNotEqual(first_time, second_time)
            for event_time in (first_time, second_time):
                self._decision(
                    self.actor,
                    OrganismAction.EFFECT_ENTITLEMENT_ELECTION,
                    key,
                    event_time,
                )
            self._receipt(
                self.actor,
                OrganismAction.EFFECT_ENTITLEMENT_ELECTION.value,
                key,
            )
        self.assertEqual(
            run_activation_step("default", (IntakeEntry.BIRTHING,)),
            [],
        )

    def test_iw_25_unbound_refused_election_decision_is_reported(self):
        key = f"h100-unbound-refused:{uuid4().hex}"
        record = self._decision(
            self.actor,
            OrganismAction.EFFECT_ENTITLEMENT_ELECTION,
            key,
            BASE_TIME,
            refuse=True,
        )
        actual = run_activation_step("default", (IntakeEntry.BIRTHING,))
        self._assert_records(
            actual,
            {
                (
                    "decision",
                    OrganismAction.EFFECT_ENTITLEMENT_ELECTION.value,
                    key,
                    self.actor.pk,
                ),
            },
        )
        self.assertEqual(actual[0].record_id, record)

    def test_iw_25_wrong_actor_cannot_borrow_a_compound_bindings_derived_slot(self):
        key = f"h100-wrong-actor:{uuid4().hex}"
        target = self.target_organism_id
        _, received_at = self._insert_binding(
            IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND,
            key,
            target=target,
        )
        request_slot, _ = self._compound_stage_records(
            self.actor,
            key,
            target,
            received_at,
        )
        wrong_receipt = self._receipt(
            self.other_actor,
            request_slot.action,
            request_slot.idempotency_key,
        )
        wrong_decision = self._decision(
            self.other_actor,
            OrganismAction.REQUEST_GOVERNANCE_SEAT,
            request_slot.idempotency_key,
            received_at,
        )
        actual = run_activation_step(
            "default",
            (IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND,),
        )
        self._assert_records(
            actual,
            {
                (
                    "receipt",
                    request_slot.action,
                    request_slot.idempotency_key,
                    self.other_actor.pk,
                ),
                (
                    "decision",
                    request_slot.action,
                    request_slot.idempotency_key,
                    self.other_actor.pk,
                ),
            },
        )
        self.assertEqual(
            {record.record_id for record in actual},
            {wrong_receipt, wrong_decision},
        )

    def test_iw_25_standalone_binding_does_not_claim_compound_stage_slots(self):
        compound_key = f"h100-valid-compound:{uuid4().hex}"
        compound_row = self._insert_binding(
            IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND,
            compound_key,
            target=self.target_organism_id,
        )
        self._compound_stage_records(
            self.actor,
            compound_key,
            self.target_organism_id,
            compound_row[1],
        )

        standalone_key = f"h100-standalone:{uuid4().hex}"
        standalone_row = self._insert_binding(
            IntakeEntry.DECIDE_GOVERNANCE_SEAT,
            standalone_key,
        )
        self._receipt(
            self.actor,
            OrganismAction.ADMIT_CIRCLE_PARTICIPANT.value,
            standalone_key,
        )
        self._decision(
            self.actor,
            OrganismAction.ADMIT_CIRCLE_PARTICIPANT,
            standalone_key,
            standalone_row[1],
        )
        stage_slot = slots(
            IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND,
            standalone_key,
            self.target_organism_id,
        )[0]
        stray_receipt = self._receipt(self.actor, stage_slot.action, stage_slot.idempotency_key)
        stray_decision = self._decision(
            self.actor,
            OrganismAction.REQUEST_GOVERNANCE_SEAT,
            stage_slot.idempotency_key,
            standalone_row[1],
        )
        actual = run_activation_step(
            "default",
            (IntakeEntry.DECIDE_GOVERNANCE_SEAT,),
        )
        self._assert_records(
            actual,
            {
                (
                    "receipt",
                    stage_slot.action,
                    stage_slot.idempotency_key,
                    self.actor.pk,
                ),
                (
                    "decision",
                    stage_slot.action,
                    stage_slot.idempotency_key,
                    self.actor.pk,
                ),
            },
        )
        self.assertEqual(
            {record.record_id for record in actual},
            {stray_receipt, stray_decision},
        )

    def test_iw_25_legitimate_two_actor_compound_bindings_are_both_accounted(self):
        key = f"h100-two-actors:{uuid4().hex}"
        target = self.target_organism_id
        first = self._insert_binding(
            IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND,
            key,
            actor=self.actor,
            target=target,
        )
        second = self._insert_binding(
            IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND,
            key,
            actor=self.other_actor,
            target=target,
        )
        self._compound_stage_records(self.actor, key, target, first[1])
        self._compound_stage_records(self.other_actor, key, target, second[1])
        self.assertEqual(
            run_activation_step(
                "default",
                (IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND,),
            ),
            [],
        )
