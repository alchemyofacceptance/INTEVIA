from __future__ import annotations

import threading
from collections import deque
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from queue import Queue
from threading import Barrier
from uuid import UUID, uuid4
from unittest import skipUnless

from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import TransactionTestCase

from core.models import Identity, OrganismCommandReceipt, OrganismMembership, OrganismMembershipTransition
from core.organism_contract import OrganismAction
from s015_0021_support import T0, append_event, drop_unset_check, empty_fp, found_root, fresh, refresh, seed_identity
from src.intevia.services.organism_authority import (
    S015Authority,
    S015AuthorityDenied,
    S015AuthorityMalformed,
    S015AuthorityRefusalCode,
    S015AuthorityUnavailable,
    S015PostFoundingAuthorityRefusal,
    S015PostFoundingAuthorityRequest,
    S015PostFoundingAuthorityResponse,
)
from src.intevia.services.organism_membership_service import (
    MembershipCommandResult,
    OpenMembershipCommand,
    OrganismMembershipService,
    S015CommandAtomicityRequired,
    S015CommandReplayMismatch,
    S015MembershipEdgeRefused,
    S015MembershipExists,
    S015MembershipStateUnused,
    S015OperationNotYetImplemented,
    TransitionMembershipCommand,
    acquire_organism_command_mutex,
)

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
POSTGRESQL_ONLY = skipUnless(connection.vendor == "postgresql", "membership command slice is PostgreSQL-only")


class _ExactProvider:
    def evaluate_organism_authority(self, request: S015PostFoundingAuthorityRequest):
        return S015PostFoundingAuthorityResponse(
            **asdict(request),
            authority_reference="authority:fixture",
        )


class _DeniedProvider:
    def evaluate_organism_authority(self, request: S015PostFoundingAuthorityRequest):
        return S015PostFoundingAuthorityRefusal(
            **asdict(request),
            refusal_code=S015AuthorityRefusalCode.DENIED,
        )


class _UnavailableProvider:
    def evaluate_organism_authority(self, request: S015PostFoundingAuthorityRequest):
        raise RuntimeError("offline")


class _MalformedProvider:
    def evaluate_organism_authority(self, request: S015PostFoundingAuthorityRequest):
        payload = asdict(request)
        payload["request_reference"] = "request:wrong"
        return S015PostFoundingAuthorityResponse(
            **payload,
            authority_reference="authority:fixture",
        )


@POSTGRESQL_ONLY
class S015MembershipCommandTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        super().setUp()
        with connection.cursor() as cur:
            drop_unset_check(cur)

    def tearDown(self):
        with connection.cursor() as cur:
            cur.execute(
                "ALTER TABLE public.core_livingorganism ADD CONSTRAINT s015_platform_root_unset_ck CHECK (NOT platform_root) NOT VALID"
            )
        super().tearDown()

    def _service(self, provider):
        return OrganismMembershipService(
            authority=S015Authority(provider=provider, database_alias="default"),
            clock=lambda: NOW,
            database_alias="default",
        )

    def _setup_organism(self, label: str = "membership"):
        with transaction.atomic():
            with connection.cursor() as cur:
                ids = found_root(cur, label)
                target_pk = seed_identity(cur, f"{label}-target")
        actor = Identity.objects.get(pk=ids["identity"])
        target = Identity.objects.get(pk=target_pk)
        with connection.cursor() as cur:
            cur.execute("SELECT organism_id FROM core_livingorganism WHERE id = %s", (ids["lo"],))
            organism_id = cur.fetchone()[0]
        ids["organism_uuid"] = UUID(str(organism_id))
        return ids, actor, target

    def _assign_office(self, *, ids, membership, role_definition_id, tag):
        assignment_uuid = uuid4()
        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO core_contextualroleassignment (
                        assignment_uuid,
                        state,
                        created_at,
                        current_state_effective_at,
                        state_known_at,
                        state_source_event_set_fingerprint,
                        membership_id,
                        role_definition_id
                    ) VALUES (%s, %s, now(), %s, %s, %s, %s, %s) RETURNING id
                    """,
                    (
                        assignment_uuid,
                        "ACTIVE",
                        T0,
                        T0,
                        empty_fp(cur, "core_contextualroleassignment", assignment_uuid),
                        membership.pk,
                        role_definition_id,
                    ),
                )
                assignment_id = cur.fetchone()[0]
                append_event(
                    cur,
                    "core_contextualroleassignmentstateevent",
                    assignment_id,
                    1,
                    "ASSIGN",
                    None,
                    "ACTIVE",
                    ids["identity"],
                    ids["basis"],
                    tag,
                )
                refresh(cur, "core_contextualroleassignment", assignment_id)
        return assignment_id

    def _open_command(self, *, actor, organism_id, target_identity_id, request_reference, idempotency_key, reason_class, occurred_at=NOW, effective_at=NOW, received_at=NOW):
        return OpenMembershipCommand(
            credential=actor.credential,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
            effective_at=effective_at,
            received_at=received_at,
            living_organism_id=organism_id,
            member_identity_id=target_identity_id,
            reason_class=reason_class,
        )

    def _transition_command(self, *, actor, membership_uuid, resulting_state, request_reference, idempotency_key, reason_class, occurred_at=NOW, effective_at=NOW, received_at=NOW):
        return TransitionMembershipCommand(
            credential=actor.credential,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
            effective_at=effective_at,
            received_at=received_at,
            membership_uuid=membership_uuid,
            resulting_state=resulting_state,
            reason_class=reason_class,
        )

    def _open_then_transition(self, *, target_state="ACTIVE", reason_open="TEST_ONLY_REASON_OPEN", reason_transition="TEST_ONLY_REASON_TRANSITION", organism_label="membership"):
        ids, actor, target = self._setup_organism(organism_label)
        service = self._service(_ExactProvider())
        open_result = service.open_membership(
            self._open_command(
                actor=actor,
                organism_id=ids["organism_uuid"],
                target_identity_id=target.identity_id,
                request_reference=f"request:{organism_label}:open",
                idempotency_key=f"idem:{organism_label}:open",
                reason_class=reason_open,
            )
        )
        transition_result = service.transition_membership(
            self._transition_command(
                actor=actor,
                membership_uuid=open_result.membership_uuid,
                resulting_state=target_state,
                request_reference=f"request:{organism_label}:transition",
                idempotency_key=f"idem:{organism_label}:transition",
                reason_class=reason_transition,
            )
        )
        return ids, actor, target, service, open_result, transition_result

    def test_open_then_transition_to_active_commits_and_refreshes_cache(self):
        ids, actor, target, service, open_result, transition_result = self._open_then_transition()
        membership = OrganismMembership.objects.get(membership_uuid=open_result.membership_uuid)
        transitions = list(
            OrganismMembershipTransition.objects.filter(membership=membership).order_by("sequence")
        )
        receipts = list(OrganismCommandReceipt.objects.filter(actor=actor))

        self.assertEqual(open_result.resulting_state, "PROPOSED")
        self.assertEqual(transition_result.resulting_state, "ACTIVE")
        self.assertEqual(membership.state, "ACTIVE")
        self.assertEqual(membership.head_transition_id, transition_result.transition_id)
        self.assertEqual([transition.sequence for transition in transitions], [1, 2])
        self.assertIsNone(transitions[0].predecessor_id)
        self.assertEqual(transitions[1].predecessor_id, transitions[0].id)
        self.assertEqual(len(receipts), 2)

        with connection.cursor() as cur:
            cur.execute(
                "SELECT projection, eligible_count, served_from, fingerprint FROM public.s015_effective_state('core_organismmembership', %s, %s, %s)",
                (membership.pk, membership.current_state_effective_at, membership.state_known_at),
            )
            projection, eligible_count, served_from, fingerprint = cur.fetchone()
        self.assertEqual(projection, "ACTIVE")
        self.assertEqual(eligible_count, 2)
        self.assertEqual(served_from, "CACHE_REQUALIFIED")
        self.assertEqual(fingerprint, membership.state_source_event_set_fingerprint)

    def test_probation_edges_chain(self):
        _, actor, target, service, open_result, probation_result = self._open_then_transition(
            target_state="PROBATIONARY",
            reason_transition="TEST_ONLY_REASON_PROBATION",
            organism_label="probation",
        )
        active_result = service.transition_membership(
            self._transition_command(
                actor=actor,
                membership_uuid=open_result.membership_uuid,
                resulting_state="ACTIVE",
                request_reference="request:probation:active",
                idempotency_key="idem:probation:active",
                reason_class="TEST_ONLY_REASON_ACTIVE",
            )
        )
        membership = OrganismMembership.objects.get(membership_uuid=open_result.membership_uuid)
        self.assertEqual([probation_result.resulting_state, active_result.resulting_state], ["PROBATIONARY", "ACTIVE"])
        self.assertEqual(membership.state, "ACTIVE")

    def test_leaving_keeps_history_intact(self):
        _, actor, target, service, open_result, active_result = self._open_then_transition(
            target_state="ACTIVE",
            reason_transition="TEST_ONLY_REASON_ACTIVE",
            organism_label="leave",
        )
        leave_result = service.transition_membership(
            self._transition_command(
                actor=actor,
                membership_uuid=open_result.membership_uuid,
                resulting_state="ENDED",
                request_reference="request:leave:end",
                idempotency_key="idem:leave:end",
                reason_class="TEST_ONLY_REASON_LEAVE",
            )
        )
        membership = OrganismMembership.objects.get(membership_uuid=open_result.membership_uuid)
        transitions = list(
            OrganismMembershipTransition.objects.filter(membership=membership).order_by("sequence")
        )
        self.assertEqual(leave_result.resulting_state, "ENDED")
        self.assertEqual(membership.state, "ENDED")
        self.assertEqual([transition.resulting_state for transition in transitions], ["PROPOSED", "ACTIVE", "ENDED"])
        self.assertEqual(OrganismMembership.objects.count(), 2)

    def test_rejoin_is_not_yet_implemented(self):
        _, actor, target, service, open_result, active_result = self._open_then_transition(
            target_state="ENDED",
            reason_transition="TEST_ONLY_REASON_LEAVE",
            organism_label="rejoin",
        )
        with self.assertRaisesRegex(S015OperationNotYetImplemented, "IL-1"):
            service.transition_membership(
                self._transition_command(
                    actor=actor,
                    membership_uuid=open_result.membership_uuid,
                    resulting_state="PROPOSED",
                    request_reference="request:rejoin:next-period",
                    idempotency_key="idem:rejoin:next-period",
                    reason_class="TEST_ONLY_REASON_REJOIN",
                )
            )

    def test_unused_states_are_refused(self):
        ids, actor, target = self._setup_organism("unused")
        service = self._service(_ExactProvider())
        open_result = service.open_membership(
            self._open_command(
                actor=actor,
                organism_id=ids["organism_uuid"],
                target_identity_id=target.identity_id,
                request_reference="request:unused:open",
                idempotency_key="idem:unused:open",
                reason_class="TEST_ONLY_REASON_OPEN",
            )
        )
        for state in ("SUSPENDED", "RESTRICTED", "PRIVACY_TRANSFORMED"):
            with self.subTest(state=state):
                with self.assertRaises(S015MembershipStateUnused):
                    service.transition_membership(
                        self._transition_command(
                            actor=actor,
                            membership_uuid=open_result.membership_uuid,
                            resulting_state=state,
                            request_reference=f"request:unused:{state.lower()}",
                            idempotency_key=f"idem:unused:{state.lower()}",
                            reason_class="TEST_ONLY_REASON_UNUSED",
                        )
                    )

    def test_edge_outside_decided_set_is_refused(self):
        ids, actor, target = self._setup_organism("edge")
        service = self._service(_ExactProvider())
        open_result = service.open_membership(
            self._open_command(
                actor=actor,
                organism_id=ids["organism_uuid"],
                target_identity_id=target.identity_id,
                request_reference="request:edge:open",
                idempotency_key="idem:edge:open",
                reason_class="TEST_ONLY_REASON_OPEN",
            )
        )
        service.transition_membership(
            self._transition_command(
                actor=actor,
                membership_uuid=open_result.membership_uuid,
                resulting_state="ACTIVE",
                request_reference="request:edge:active",
                idempotency_key="idem:edge:active",
                reason_class="TEST_ONLY_REASON_ACTIVE",
            )
        )
        with self.assertRaises(S015MembershipEdgeRefused):
            service.transition_membership(
                self._transition_command(
                    actor=actor,
                    membership_uuid=open_result.membership_uuid,
                    resulting_state="PROPOSED",
                    request_reference="request:edge:invalid",
                    idempotency_key="idem:edge:invalid",
                    reason_class="TEST_ONLY_REASON_INVALID",
                )
            )

    def test_leaving_while_holding_office_is_refused(self):
        ids, actor, target = self._setup_organism("office")
        service = self._service(_ExactProvider())
        open_result = service.open_membership(
            self._open_command(
                actor=actor,
                organism_id=ids["organism_uuid"],
                target_identity_id=target.identity_id,
                request_reference="request:office:open",
                idempotency_key="idem:office:open",
                reason_class="TEST_ONLY_REASON_OPEN",
            )
        )
        service.transition_membership(
            self._transition_command(
                actor=actor,
                membership_uuid=open_result.membership_uuid,
                resulting_state="ACTIVE",
                request_reference="request:office:active",
                idempotency_key="idem:office:active",
                reason_class="TEST_ONLY_REASON_ACTIVE",
            )
        )
        membership = OrganismMembership.objects.get(membership_uuid=open_result.membership_uuid)
        role_definition_id = ids["roles"]["COORDINATOR"]
        self._assign_office(ids=ids, membership=membership, role_definition_id=role_definition_id, tag="office:leave")
        with self.assertRaisesRegex(S015OperationNotYetImplemented, "IL-2"):
            service.transition_membership(
                self._transition_command(
                    actor=actor,
                    membership_uuid=open_result.membership_uuid,
                    resulting_state="ENDED",
                    request_reference="request:office:end",
                    idempotency_key="idem:office:end",
                    reason_class="TEST_ONLY_REASON_LEAVE_WITH_OFFICE",
                )
            )

    def test_replay_returns_first_result(self):
        ids, actor, target = self._setup_organism("replay")
        service = self._service(_ExactProvider())
        command = self._open_command(
            actor=actor,
            organism_id=ids["organism_uuid"],
            target_identity_id=target.identity_id,
            request_reference="request:replay:open",
            idempotency_key="idem:replay:open",
            reason_class="TEST_ONLY_REASON_REPLAY",
        )
        first = service.open_membership(command)
        second = service.open_membership(command)
        self.assertEqual(first, second)
        self.assertEqual(OrganismMembership.objects.count(), 2)
        self.assertEqual(OrganismCommandReceipt.objects.count(), 1)

    def test_open_existing_membership_raises_exists_without_writing(self):
        ids, actor, target = self._setup_organism("exists")
        service = self._service(_ExactProvider())
        first = service.open_membership(
            self._open_command(
                actor=actor,
                organism_id=ids["organism_uuid"],
                target_identity_id=target.identity_id,
                request_reference="request:exists:open",
                idempotency_key="idem:exists:open",
                reason_class="TEST_ONLY_REASON_EXISTS",
            )
        )
        membership_count = OrganismMembership.objects.count()
        receipt_count = OrganismCommandReceipt.objects.count()
        transition_count = OrganismMembershipTransition.objects.count()

        with self.assertRaises(S015MembershipExists):
            service.open_membership(
                self._open_command(
                    actor=actor,
                    organism_id=ids["organism_uuid"],
                    target_identity_id=target.identity_id,
                    request_reference="request:exists:open-again",
                    idempotency_key="idem:exists:open-again",
                    reason_class="TEST_ONLY_REASON_EXISTS",
                )
            )

        self.assertIsInstance(first, MembershipCommandResult)
        self.assertEqual(OrganismMembership.objects.count(), membership_count)
        self.assertEqual(OrganismCommandReceipt.objects.count(), receipt_count)
        self.assertEqual(OrganismMembershipTransition.objects.count(), transition_count)

    def test_replay_mismatch_on_reason_change(self):
        ids, actor, target = self._setup_organism("replay-mismatch")
        service = self._service(_ExactProvider())
        first = service.open_membership(
            self._open_command(
                actor=actor,
                organism_id=ids["organism_uuid"],
                target_identity_id=target.identity_id,
                request_reference="request:replay-mismatch:open",
                idempotency_key="idem:replay-mismatch:open",
                reason_class="TEST_ONLY_REASON_ONE",
            )
        )
        with self.assertRaises(S015CommandReplayMismatch):
            service.open_membership(
                self._open_command(
                    actor=actor,
                    organism_id=ids["organism_uuid"],
                    target_identity_id=target.identity_id,
                    request_reference="request:replay-mismatch:open",
                    idempotency_key="idem:replay-mismatch:open",
                    reason_class="TEST_ONLY_REASON_TWO",
                )
            )
        self.assertEqual(OrganismMembership.objects.count(), 2)
        self.assertEqual(OrganismCommandReceipt.objects.count(), 1)
        self.assertIsInstance(first, MembershipCommandResult)

    def test_authority_failures_refuse_without_write(self):
        ids, actor, target = self._setup_organism("authority")
        command = self._open_command(
            actor=actor,
            organism_id=ids["organism_uuid"],
            target_identity_id=target.identity_id,
            request_reference="request:authority:open",
            idempotency_key="idem:authority:open",
            reason_class="TEST_ONLY_REASON_AUTHORITY",
        )
        for provider, exc_type in [
            (_DeniedProvider(), S015AuthorityDenied),
            (_UnavailableProvider(), S015AuthorityUnavailable),
            (_MalformedProvider(), S015AuthorityMalformed),
        ]:
            with self.subTest(exc_type=exc_type.__name__):
                service = self._service(provider)
                with self.assertRaises(exc_type):
                    service.open_membership(command)
        self.assertEqual(OrganismMembership.objects.count(), 1)
        self.assertEqual(OrganismCommandReceipt.objects.count(), 0)

    def test_reason_token_does_not_change_refusal_paths(self):
        reason_token = "TEST_ONLY_REASON_REMOVAL"

        ids, actor, target = self._setup_organism("reason-token")
        service = self._service(_ExactProvider())
        open_result = service.open_membership(
            self._open_command(
                actor=actor,
                organism_id=ids["organism_uuid"],
                target_identity_id=target.identity_id,
                request_reference="request:reason-token:open",
                idempotency_key="idem:reason-token:open",
                reason_class=reason_token,
            )
        )
        service.transition_membership(
            self._transition_command(
                actor=actor,
                membership_uuid=open_result.membership_uuid,
                resulting_state="ACTIVE",
                request_reference="request:reason-token:active",
                idempotency_key="idem:reason-token:active",
                reason_class=reason_token,
            )
        )

        with self.assertRaises(S015MembershipEdgeRefused):
            service.transition_membership(
                self._transition_command(
                    actor=actor,
                    membership_uuid=open_result.membership_uuid,
                    resulting_state="PROPOSED",
                    request_reference="request:reason-token:invalid-edge",
                    idempotency_key="idem:reason-token:invalid-edge",
                    reason_class=reason_token,
                )
            )

        with connection.cursor() as cur:
            denied_target_pk = seed_identity(cur, "reason-token-denied")
        denied_target = Identity.objects.get(pk=denied_target_pk)
        denied_service = self._service(_DeniedProvider())
        with self.assertRaises(S015AuthorityDenied):
            denied_service.open_membership(
                self._open_command(
                    actor=actor,
                    organism_id=ids["organism_uuid"],
                    target_identity_id=denied_target.identity_id,
                    request_reference="request:reason-token:denied",
                    idempotency_key="idem:reason-token:denied",
                    reason_class=reason_token,
                )
            )

        membership = OrganismMembership.objects.get(membership_uuid=open_result.membership_uuid)
        role_definition_id = ids["roles"]["COORDINATOR"]
        self._assign_office(ids=ids, membership=membership, role_definition_id=role_definition_id, tag="reason-token:office")
        with self.assertRaisesRegex(S015OperationNotYetImplemented, "IL-2"):
            service.transition_membership(
                self._transition_command(
                    actor=actor,
                    membership_uuid=open_result.membership_uuid,
                    resulting_state="ENDED",
                    request_reference="request:reason-token:end",
                    idempotency_key="idem:reason-token:end",
                    reason_class=reason_token,
                )
            )

    def test_reason_validation_refuses_bad_inputs(self):
        ids, actor, target = self._setup_organism("reason")
        service = self._service(_ExactProvider())
        with self.assertRaises(TypeError):
            OpenMembershipCommand(
                credential=actor.credential,
                request_reference="request:reason:missing",
                idempotency_key="idem:reason:missing",
                occurred_at=NOW,
                effective_at=NOW,
                received_at=NOW,
                living_organism_id=ids["organism_uuid"],
                member_identity_id=target.identity_id,
            )
        nfc_reason = "reaso" + "\u0301" + "n"
        for reason in ["", " padded ", nfc_reason]:
            with self.subTest(reason=reason):
                with self.assertRaises((TypeError, ValueError)):
                    service.open_membership(
                        self._open_command(
                            actor=actor,
                            organism_id=ids["organism_uuid"],
                            target_identity_id=target.identity_id,
                            request_reference="request:reason:open",
                            idempotency_key=f"idem:reason:{reason!r}",
                            reason_class=reason,
                        )
                    )

    def test_temporal_basis_retrospective_and_cache_consistency(self):
        ids, actor, target = self._setup_organism("temporal")
        service = self._service(_ExactProvider())
        open_result = service.open_membership(
            self._open_command(
                actor=actor,
                organism_id=ids["organism_uuid"],
                target_identity_id=target.identity_id,
                request_reference="request:temporal:open",
                idempotency_key="idem:temporal:open",
                reason_class="TEST_ONLY_REASON_OPEN",
                occurred_at=NOW - timedelta(hours=2),
                effective_at=NOW - timedelta(hours=2),
                received_at=NOW - timedelta(hours=2),
            )
        )
        transition_result = service.transition_membership(
            self._transition_command(
                actor=actor,
                membership_uuid=open_result.membership_uuid,
                resulting_state="ACTIVE",
                request_reference="request:temporal:active",
                idempotency_key="idem:temporal:active",
                reason_class="TEST_ONLY_REASON_ACTIVE",
                occurred_at=NOW - timedelta(hours=2),
                effective_at=NOW - timedelta(hours=2),
                received_at=NOW - timedelta(hours=2),
            )
        )
        retrospective = service.transition_membership(
            self._transition_command(
                actor=actor,
                membership_uuid=open_result.membership_uuid,
                resulting_state="ENDED",
                request_reference="request:temporal:end",
                idempotency_key="idem:temporal:end",
                reason_class="TEST_ONLY_REASON_RETROSPECTIVE",
                occurred_at=NOW - timedelta(hours=1),
                effective_at=NOW - timedelta(hours=1),
                received_at=NOW,
            )
        )
        membership = OrganismMembership.objects.get(membership_uuid=open_result.membership_uuid)
        with connection.cursor() as cur:
            cur.execute(
                "SELECT projection, eligible_count, served_from, fingerprint FROM public.s015_effective_state('core_organismmembership', %s, %s, %s)",
                (membership.pk, membership.current_state_effective_at, membership.state_known_at),
            )
            projection, eligible_count, served_from, fingerprint = cur.fetchone()
        self.assertEqual(projection, membership.state)
        self.assertEqual(eligible_count, 3)
        self.assertEqual(served_from, "CACHE_REQUALIFIED")
        self.assertEqual(fingerprint, membership.state_source_event_set_fingerprint)
        self.assertEqual(transition_result.resulting_state, "ACTIVE")
        self.assertEqual(retrospective.temporal_basis_kind, "RETROSPECTIVE")
        self.assertIsNotNone(retrospective.temporal_basis_reference)

    def test_out_of_order_transition_raises_il3_and_writes_nothing(self):
        ids, actor, target = self._setup_organism("out-of-order")
        service = self._service(_ExactProvider())
        open_result = service.open_membership(
            self._open_command(
                actor=actor,
                organism_id=ids["organism_uuid"],
                target_identity_id=target.identity_id,
                request_reference="request:out-of-order:open",
                idempotency_key="idem:out-of-order:open",
                reason_class="TEST_ONLY_REASON_OPEN",
                occurred_at=NOW - timedelta(hours=2),
                effective_at=NOW - timedelta(hours=2),
                received_at=NOW - timedelta(hours=2),
            )
        )
        service.transition_membership(
            self._transition_command(
                actor=actor,
                membership_uuid=open_result.membership_uuid,
                resulting_state="ACTIVE",
                request_reference="request:out-of-order:active",
                idempotency_key="idem:out-of-order:active",
                reason_class="TEST_ONLY_REASON_ACTIVE",
                occurred_at=NOW - timedelta(hours=2),
                effective_at=NOW - timedelta(hours=2),
                received_at=NOW - timedelta(hours=2),
            )
        )
        transition_count = OrganismMembershipTransition.objects.count()
        receipt_count = OrganismCommandReceipt.objects.count()
        membership = OrganismMembership.objects.get(membership_uuid=open_result.membership_uuid)

        with self.assertRaisesRegex(S015OperationNotYetImplemented, "IL-3"):
            service.transition_membership(
                self._transition_command(
                    actor=actor,
                    membership_uuid=open_result.membership_uuid,
                    resulting_state="ENDED",
                    request_reference="request:out-of-order:end",
                    idempotency_key="idem:out-of-order:end",
                    reason_class="TEST_ONLY_REASON_OUT_OF_ORDER",
                    occurred_at=NOW - timedelta(hours=3),
                    effective_at=NOW - timedelta(hours=3),
                    received_at=NOW,
                )
            )

        membership.refresh_from_db()
        self.assertEqual(membership.state, "ACTIVE")
        self.assertEqual(OrganismMembershipTransition.objects.count(), transition_count)
        self.assertEqual(OrganismCommandReceipt.objects.count(), receipt_count)

    def test_atomicity_required_on_mutex_helper(self):
        with self.assertRaises(S015CommandAtomicityRequired):
            acquire_organism_command_mutex(
                database_alias="default",
                actor_identity_id=uuid4(),
                action=OrganismAction.TRANSITION_MEMBERSHIP,
                idempotency_key="idem:atomicity",
            )

    def test_atomic_failure_leaves_no_partial_rows(self):
        ids, actor, target = self._setup_organism("atomic-failure")
        service = self._service(_ExactProvider())
        future = NOW + timedelta(days=1)
        with self.assertRaises(IntegrityError):
            service.open_membership(
                self._open_command(
                    actor=actor,
                    organism_id=ids["organism_uuid"],
                    target_identity_id=target.identity_id,
                    request_reference="request:atomic-failure:open",
                    idempotency_key="idem:atomic-failure:open",
                    reason_class="TEST_ONLY_REASON_ATOMICITY",
                    effective_at=NOW,
                    received_at=future,
                )
            )
        self.assertEqual(OrganismMembership.objects.count(), 1)
        self.assertEqual(OrganismCommandReceipt.objects.count(), 0)

    def test_concurrent_same_request_replays_once(self):
        ids, actor, target = self._setup_organism("concurrency")
        command_kwargs = dict(
            organism_id=ids["organism_uuid"],
            target_identity_id=target.identity_id,
            request_reference="request:concurrency:open",
            idempotency_key="idem:concurrency:open",
            reason_class="TEST_ONLY_REASON_CONCURRENCY",
        )
        gate = Barrier(2)
        results: Queue[tuple[str, object]] = Queue()

        def worker():
            close_old_connections()
            worker_actor = Identity.objects.get(pk=actor.pk)
            service = self._service(_ExactProvider())
            try:
                gate.wait(timeout=10)
                result = service.open_membership(
                    self._open_command(actor=worker_actor, **command_kwargs)
                )
                results.put(("ok", result))
            except Exception as exc:  # pragma: no cover - thread assertion channel
                results.put(("err", exc))

        thread_one = threading.Thread(target=worker, daemon=True)
        thread_two = threading.Thread(target=worker, daemon=True)
        thread_one.start()
        thread_two.start()
        thread_one.join(timeout=30)
        thread_two.join(timeout=30)
        self.assertFalse(thread_one.is_alive())
        self.assertFalse(thread_two.is_alive())

        collected = [results.get(timeout=5) for _ in range(2)]
        self.assertTrue(all(kind == "ok" for kind, _ in collected))
        self.assertEqual(collected[0][1], collected[1][1])
        self.assertEqual(OrganismMembership.objects.count(), 2)
        self.assertEqual(OrganismCommandReceipt.objects.count(), 1)
        self.assertEqual(OrganismMembershipTransition.objects.count(), 2)