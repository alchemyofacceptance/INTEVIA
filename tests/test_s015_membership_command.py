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
from tests.s015_concurrency_support import cleanup_workers, exact_wait, start_worker, wait_until_locked
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
FAR_FUTURE = datetime(2999, 1, 1, tzinfo=timezone.utc)  # later than any real recorded_at, so the four-time check refuses it on every run
POSTGRESQL_ONLY = skipUnless(connection.vendor == "postgresql", "membership command slice is PostgreSQL-only")


class _ExactProvider:
    def evaluate_organism_authority(self, request: S015PostFoundingAuthorityRequest):
        authority_reference = "authority:fixture"
        if (
            request.action is OrganismAction.TRANSITION_CONTEXTUAL_ROLE
            and request.target.get("event_action") == "ASSIGN"
        ):
            from core.models import ContextualRoleAssignment, ContextualRoleAssignmentStateEvent
            from src.intevia.services.organism_authority import canonical_json_bytes

            assignment = ContextualRoleAssignment.objects.select_related(
                "membership", "role_definition",
            ).get(assignment_uuid=request.target["assignment_uuid"])
            proposal = ContextualRoleAssignmentStateEvent.objects.get(
                assignment=assignment,
                sequence=1,
                action="PROPOSE",
            )
            authority_reference = "s015a1:" + canonical_json_bytes({
                "a": str(assignment.assignment_uuid),
                "g": str(assignment.role_definition.role_uuid),
                "k": "ASSIGN",
                "p": str(proposal.event_uuid),
                "u": "e:" + str(proposal.event_uuid),
            }).decode("utf-8")
        return S015PostFoundingAuthorityResponse(
            **asdict(request),
            authority_reference=authority_reference,
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

    def _fixture_membership(self, ids, target, state, label, at=T0):
        # TEST-ONLY founding-style setup (NULL -> ACTIVE), or probation prior-state construction;
        # neither is an operational founding or probation route.
        membership_uuid = uuid4()
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute(
                "INSERT INTO core_organismmembership (membership_uuid, identity_id, living_organism_id, "
                "state, created_at, current_state_effective_at, state_known_at, "
                "state_source_event_set_fingerprint) VALUES (%s, %s, %s, %s, now(), %s, %s, %s) RETURNING id",
                [membership_uuid, target.pk, ids["lo"], state, at, at,
                 empty_fp(cur, "core_organismmembership", membership_uuid)],
            )
            membership_id = cur.fetchone()[0]
            append_event(cur, "core_organismmembershiptransition", membership_id, 1, "JOIN",
                         None, state, ids["identity"], ids["basis"], label,
                         eff=at, occ=at, rec=at, extra={"reason_class": "FOUNDING"})
            refresh(cur, "core_organismmembership", membership_id, at)
        return OrganismMembership.objects.get(pk=membership_id)

    def _assign_office(self, *, ids, membership, role_definition_id, tag):
        from core.models import OrganismRoleDefinition
        from src.intevia.services.organism_role_service import (
            OrganismRoleService,
            ProposeAssignmentCommand,
            TransitionAssignmentCommand,
        )

        proposer = Identity.objects.select_related("credential").get(pk=membership.identity_id)
        keying_actor = Identity.objects.select_related("credential").get(pk=ids["identity"])
        role = OrganismRoleDefinition.objects.get(pk=role_definition_id)

        def database_now():
            with connection.cursor() as cursor:
                cursor.execute("SELECT clock_timestamp()")
                return cursor.fetchone()[0]

        service = OrganismRoleService(
            authority=S015Authority(provider=_ExactProvider(), database_alias="default"),
            clock=lambda: NOW,
            database_alias="default",
        )
        # TEST-ONLY authority fixture supplies the office state needed by membership witnesses; it is not production permission.
        proposed_at = NOW
        proposal_received_at = database_now()
        proposal = service.propose_assignment(ProposeAssignmentCommand(
            credential=proposer.credential,
            request_reference=f"{tag}:propose:{fresh()}",
            idempotency_key=f"{tag}:propose-key:{fresh()}",
            occurred_at=proposed_at,
            effective_at=proposed_at,
            received_at=proposal_received_at,
            membership_uuid=membership.membership_uuid,
            role_definition_uuid=role.role_uuid,
            effective_until_state="NONE",
            effective_until=None,
        ))
        assigned_at = NOW
        assignment_received_at = database_now()
        service.transition_assignment(TransitionAssignmentCommand(
            credential=keying_actor.credential,
            request_reference=f"{tag}:assign:{fresh()}",
            idempotency_key=f"{tag}:assign-key:{fresh()}",
            occurred_at=assigned_at,
            effective_at=assigned_at,
            received_at=assignment_received_at,
            assignment_uuid=proposal.assignment_uuid,
            action="ASSIGN",
            resulting_state="ACTIVE",
            effective_until_state="NONE",
            effective_until=None,
        ))
        return proposal.assignment_id

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

    def _open_then_transition(self, *, target_state="ENDED", reason_open="TEST_ONLY_REASON_OPEN", reason_transition="TEST_ONLY_REASON_TRANSITION", organism_label="membership"):
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
        membership = OrganismMembership.objects.get(membership_uuid=open_result.membership_uuid)
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute(
                "SELECT id, sequence FROM core_organismmembershiptransition "
                "WHERE membership_id = %s ORDER BY sequence DESC LIMIT 1",
                [membership.pk],
            )
            predecessor_id, sequence = cur.fetchone()
            transition_id, _ = append_event(
                cur,
                "core_organismmembershiptransition",
                membership.pk,
                sequence + 1,
                "LEAVE",
                "PROPOSED",
                target_state,
                target.pk,
                ids["basis"],
                f"{organism_label}:member-leave",
                pred_id=predecessor_id,
                eff=NOW,
                occ=NOW,
                rec=NOW,
                extra={"reason_class": reason_transition},
            )
            refresh(cur, "core_organismmembership", membership.pk, NOW)
        transition_result = OrganismMembershipTransition.objects.get(pk=transition_id)
        return ids, actor, target, service, open_result, transition_result

    def test_open_then_transition_to_active_is_refused_without_write(self):
        ids, actor, target = self._setup_organism("admission-refused")
        service = self._service(_ExactProvider())
        open_result = service.open_membership(self._open_command(
            actor=actor, organism_id=ids["organism_uuid"], target_identity_id=target.identity_id,
            request_reference="request:admission:open", idempotency_key="idem:admission:open",
            reason_class="TEST_ONLY_REASON_OPEN",
        ))
        membership = OrganismMembership.objects.get(membership_uuid=open_result.membership_uuid)
        self.assertEqual(open_result.resulting_state, "PROPOSED")
        before = (OrganismMembershipTransition.objects.count(), OrganismCommandReceipt.objects.count())
        with self.assertRaises(S015OperationNotYetImplemented) as refused:
            service.transition_membership(self._transition_command(
                actor=actor, membership_uuid=open_result.membership_uuid, resulting_state="ACTIVE",
                request_reference="request:admission:active", idempotency_key="idem:admission:active",
                reason_class="TEST_ONLY_REASON_ACTIVE",
            ))
        self.assertEqual(refused.exception.limit_id, "S1-IL-ADMISSION")
        membership.refresh_from_db()
        self.assertEqual(membership.state, "PROPOSED")
        self.assertEqual((OrganismMembershipTransition.objects.count(), OrganismCommandReceipt.objects.count()), before)

    def test_probation_edges_chain(self):
        ids, actor, target = self._setup_organism("probation")
        service = self._service(_ExactProvider())
        opened = service.open_membership(self._open_command(
            actor=actor, organism_id=ids["organism_uuid"], target_identity_id=target.identity_id,
            request_reference="request:probation:open", idempotency_key="idem:probation:open",
            reason_class="TEST_ONLY_REASON_OPEN",
        ))
        before = (OrganismMembershipTransition.objects.count(), OrganismCommandReceipt.objects.count())
        with self.assertRaises(S015OperationNotYetImplemented) as refused:
            service.transition_membership(self._transition_command(
                actor=actor, membership_uuid=opened.membership_uuid, resulting_state="PROBATIONARY",
                request_reference="request:probation:proposed", idempotency_key="idem:probation:proposed",
                reason_class="TEST_ONLY_REASON_PROBATION",
            ))
        self.assertEqual(refused.exception.limit_id, "S3-IL-PROBATION")
        self.assertEqual((OrganismMembershipTransition.objects.count(), OrganismCommandReceipt.objects.count()), before)
        with connection.cursor() as cur:
            second_pk = seed_identity(cur, "probation-prior-target")
        probationary = self._fixture_membership(ids, Identity.objects.get(pk=second_pk), "PROBATIONARY",
                                                 "probation-prior-only")
        before = (OrganismMembershipTransition.objects.count(), OrganismCommandReceipt.objects.count())
        with self.assertRaises(S015OperationNotYetImplemented) as refused:
            service.transition_membership(self._transition_command(
                actor=actor, membership_uuid=probationary.membership_uuid, resulting_state="ACTIVE",
                request_reference="request:probation:active", idempotency_key="idem:probation:active",
                reason_class="TEST_ONLY_REASON_ACTIVE",
            ))
        self.assertEqual(refused.exception.limit_id, "S3-IL-PROBATION")
        probationary.refresh_from_db()
        self.assertEqual(probationary.state, "PROBATIONARY")
        self.assertEqual((OrganismMembershipTransition.objects.count(), OrganismCommandReceipt.objects.count()), before)

    def test_leaving_from_proposed_edge(self):
        ids, actor, target = self._setup_organism("leave-proposed")
        service = self._service(_ExactProvider())
        open_result = service.open_membership(
            self._open_command(
                actor=actor,
                organism_id=ids["organism_uuid"],
                target_identity_id=target.identity_id,
                request_reference="request:leave-proposed:open",
                idempotency_key="idem:leave-proposed:open",
                reason_class="TEST_ONLY_REASON_OPEN",
            )
        )
        membership = OrganismMembership.objects.get(membership_uuid=open_result.membership_uuid)
        with connection.cursor() as cur:
            cur.execute(
                "SELECT id, sequence FROM core_organismmembershiptransition "
                "WHERE membership_id = %s ORDER BY sequence DESC LIMIT 1",
                [membership.pk],
            )
            predecessor_id, sequence = cur.fetchone()
        before_refusal = (
            OrganismMembershipTransition.objects.count(),
            OrganismCommandReceipt.objects.count(),
        )
        with self.assertRaisesRegex(IntegrityError, "PARTY actor must be the proposed member"):
            with transaction.atomic(), connection.cursor() as cur:
                append_event(
                    cur,
                    "core_organismmembershiptransition",
                    membership.pk,
                    sequence + 1,
                    "LEAVE",
                    "PROPOSED",
                    "ENDED",
                    actor.pk,
                    ids["basis"],
                    "leave-proposed:proposer-end",
                    pred_id=predecessor_id,
                    eff=NOW,
                    occ=NOW,
                    rec=NOW,
                    extra={"reason_class": "TEST_ONLY_REASON_LEAVE"},
                )
        membership.refresh_from_db()
        self.assertEqual(membership.state, "PROPOSED")
        self.assertEqual(
            (OrganismMembershipTransition.objects.count(), OrganismCommandReceipt.objects.count()),
            before_refusal,
        )
        with transaction.atomic(), connection.cursor() as cur:
            transition_id, _ = append_event(
                cur,
                "core_organismmembershiptransition",
                membership.pk,
                sequence + 1,
                "LEAVE",
                "PROPOSED",
                "ENDED",
                target.pk,
                ids["basis"],
                "leave-proposed:member-end",
                pred_id=predecessor_id,
                eff=NOW,
                occ=NOW,
                rec=NOW,
                extra={"reason_class": "TEST_ONLY_REASON_LEAVE"},
            )
            refresh(cur, "core_organismmembership", membership.pk, NOW)
        leave_result = OrganismMembershipTransition.objects.get(pk=transition_id)
        membership.refresh_from_db()
        transitions = list(
            OrganismMembershipTransition.objects.filter(membership=membership).order_by("sequence")
        )
        self.assertEqual(leave_result.resulting_state, "ENDED")
        self.assertEqual(membership.state, "ENDED")
        self.assertEqual([transition.resulting_state for transition in transitions], ["PROPOSED", "ENDED"])

    def test_leaving_from_probationary_edge(self):
        ids, actor, target = self._setup_organism("leave-probationary")
        service = self._service(_ExactProvider())
        membership = self._fixture_membership(ids, target, "PROBATIONARY", "leave-probationary-prior")
        leave_result = service.transition_membership(
            self._transition_command(
                actor=actor,
                membership_uuid=membership.membership_uuid,
                resulting_state="ENDED",
                request_reference="request:leave-probationary:end",
                idempotency_key="idem:leave-probationary:end",
                reason_class="TEST_ONLY_REASON_LEAVE",
            )
        )
        membership.refresh_from_db()
        transitions = list(
            OrganismMembershipTransition.objects.filter(membership=membership).order_by("sequence")
        )
        self.assertEqual(leave_result.resulting_state, "ENDED")
        self.assertEqual(membership.state, "ENDED")
        self.assertEqual([transition.resulting_state for transition in transitions], ["PROBATIONARY", "ENDED"])

    def test_leaving_keeps_history_intact(self):
        ids, actor, target = self._setup_organism("leave")
        service = self._service(_ExactProvider())
        membership = self._fixture_membership(ids, target, "ACTIVE", "leave-active-prior")
        leave_result = service.transition_membership(
            self._transition_command(
                actor=actor,
                membership_uuid=membership.membership_uuid,
                resulting_state="ENDED",
                request_reference="request:leave:end",
                idempotency_key="idem:leave:end",
                reason_class="TEST_ONLY_REASON_LEAVE",
            )
        )
        membership.refresh_from_db()
        transitions = list(
            OrganismMembershipTransition.objects.filter(membership=membership).order_by("sequence")
        )
        self.assertEqual(leave_result.resulting_state, "ENDED")
        self.assertEqual(membership.state, "ENDED")
        self.assertEqual([transition.resulting_state for transition in transitions], ["ACTIVE", "ENDED"])
        self.assertEqual(OrganismMembership.objects.count(), 2)

    def test_sql_open_and_active_departure_are_outside_admission_guard(self):
        ids, actor, target = self._setup_organism("sql-boundaries")
        proposed = self._fixture_membership(ids, target, "PROPOSED", "sql-opening")
        self.assertEqual(proposed.state, "PROPOSED")
        with connection.cursor() as cur:
            active_target_pk = seed_identity(cur, "sql-active-departure-target")
        active = self._fixture_membership(ids, Identity.objects.get(pk=active_target_pk),
                                          "ACTIVE", "sql-active-opening")
        with transaction.atomic(), connection.cursor() as cur:
            predecessor = OrganismMembershipTransition.objects.get(pk=active.head_transition_id)
            departed_id, _ = append_event(
                cur, "core_organismmembershiptransition", active.pk, predecessor.sequence + 1,
                "TRANSITION_MEMBERSHIP", "ACTIVE", "ENDED", actor.pk, ids["basis"],
                "sql-active-departure", pred_id=predecessor.pk,
                eff=NOW, occ=NOW, rec=NOW, extra={"reason_class": "TEST"},
            )
            refresh(cur, "core_organismmembership", active.pk, NOW)
        active.refresh_from_db()
        self.assertEqual(OrganismMembershipTransition.objects.get(pk=departed_id).resulting_state, "ENDED")
        self.assertEqual(active.state, "ENDED")

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

    def test_rejoin_refused_with_design_token_reason(self):
        _, actor, target, service, open_result, active_result = self._open_then_transition(
            target_state="ENDED",
            reason_transition="TEST_ONLY_REASON_LEAVE",
            organism_label="rejoin-token",
        )
        transition_count = OrganismMembershipTransition.objects.count()
        receipt_count = OrganismCommandReceipt.objects.count()
        with self.assertRaisesRegex(S015OperationNotYetImplemented, r"IL-1, S4"):
            service.transition_membership(
                self._transition_command(
                    actor=actor,
                    membership_uuid=open_result.membership_uuid,
                    resulting_state="PROPOSED",
                    request_reference="request:rejoin-token:next-period",
                    idempotency_key="idem:rejoin-token:next-period",
                    reason_class="TEST_ONLY_REASON_REMOVAL",
                )
            )
        self.assertEqual(OrganismMembershipTransition.objects.count(), transition_count)
        self.assertEqual(OrganismCommandReceipt.objects.count(), receipt_count)

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
        membership = self._fixture_membership(ids, target, "ACTIVE", "edge-active-prior")
        with self.assertRaises(S015MembershipEdgeRefused):
            service.transition_membership(
                self._transition_command(
                    actor=actor,
                    membership_uuid=membership.membership_uuid,
                    resulting_state="PROPOSED",
                    request_reference="request:edge:invalid",
                    idempotency_key="idem:edge:invalid",
                    reason_class="TEST_ONLY_REASON_INVALID",
                )
            )

    def test_leaving_while_holding_office_is_refused(self):
        ids, actor, target = self._setup_organism("office")
        service = self._service(_ExactProvider())
        membership = self._fixture_membership(ids, target, "ACTIVE", "office-active-prior")
        role_definition_id = ids["roles"]["COORDINATOR"]
        self._assign_office(ids=ids, membership=membership, role_definition_id=role_definition_id, tag="office:leave")
        with self.assertRaisesRegex(S015OperationNotYetImplemented, r"IL-2, S5"):
            service.transition_membership(
                self._transition_command(
                    actor=actor,
                    membership_uuid=membership.membership_uuid,
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
        with self.assertRaises(S015CommandReplayMismatch):
            service.open_membership(
                self._open_command(
                    actor=actor,
                    organism_id=ids["organism_uuid"],
                    target_identity_id=target.identity_id,
                    request_reference="request:replay-mismatch:open",
                    idempotency_key="idem:replay-mismatch:open",
                    reason_class="TEST_ONLY_REASON_ONE",
                    effective_at=NOW + timedelta(minutes=1),
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

    def test_denied_authority_preempts_edge_and_exists_refusals(self):
        ids, actor, target = self._setup_organism("authority-order")
        service = self._service(_ExactProvider())
        open_result = service.open_membership(
            self._open_command(
                actor=actor,
                organism_id=ids["organism_uuid"],
                target_identity_id=target.identity_id,
                request_reference="request:authority-order:open",
                idempotency_key="idem:authority-order:open",
                reason_class="TEST_ONLY_REASON_AUTHORITY",
            )
        )
        membership_count = OrganismMembership.objects.count()
        receipt_count = OrganismCommandReceipt.objects.count()
        transition_count = OrganismMembershipTransition.objects.count()

        denied_service = self._service(_DeniedProvider())
        with self.assertRaises(S015AuthorityDenied):
            denied_service.transition_membership(
                self._transition_command(
                    actor=actor,
                    membership_uuid=open_result.membership_uuid,
                    resulting_state="PROPOSED",
                    request_reference="request:authority-order:invalid-edge",
                    idempotency_key="idem:authority-order:invalid-edge",
                    reason_class="TEST_ONLY_REASON_AUTHORITY",
                )
            )
        self.assertEqual(OrganismMembership.objects.count(), membership_count)
        self.assertEqual(OrganismCommandReceipt.objects.count(), receipt_count)
        self.assertEqual(OrganismMembershipTransition.objects.count(), transition_count)

        with self.assertRaises(S015AuthorityDenied):
            denied_service.open_membership(
                self._open_command(
                    actor=actor,
                    organism_id=ids["organism_uuid"],
                    target_identity_id=target.identity_id,
                    request_reference="request:authority-order:existing-membership",
                    idempotency_key="idem:authority-order:existing-membership",
                    reason_class="TEST_ONLY_REASON_AUTHORITY",
                )
            )
        self.assertEqual(OrganismMembership.objects.count(), membership_count)
        self.assertEqual(OrganismCommandReceipt.objects.count(), receipt_count)
        self.assertEqual(OrganismMembershipTransition.objects.count(), transition_count)

    def test_reason_token_does_not_change_refusal_paths(self):
        reason_token = "TEST_ONLY_REASON_REMOVAL"

        ids, actor, target = self._setup_organism("reason-token")
        service = self._service(_ExactProvider())
        membership = self._fixture_membership(ids, target, "ACTIVE", "reason-token-active-prior")

        with self.assertRaises(S015MembershipEdgeRefused):
            service.transition_membership(
                self._transition_command(
                    actor=actor,
                    membership_uuid=membership.membership_uuid,
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

        role_definition_id = ids["roles"]["COORDINATOR"]
        self._assign_office(ids=ids, membership=membership, role_definition_id=role_definition_id, tag="reason-token:office")
        with self.assertRaisesRegex(S015OperationNotYetImplemented, r"IL-2, S5"):
            service.transition_membership(
                self._transition_command(
                    actor=actor,
                    membership_uuid=membership.membership_uuid,
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
        membership = self._fixture_membership(ids, target, "ACTIVE", "temporal-active-prior",
                                               at=NOW - timedelta(hours=2))
        retrospective = service.transition_membership(
            self._transition_command(
                actor=actor,
                membership_uuid=membership.membership_uuid,
                resulting_state="ENDED",
                request_reference="request:temporal:end",
                idempotency_key="idem:temporal:end",
                reason_class="TEST_ONLY_REASON_RETROSPECTIVE",
                occurred_at=NOW - timedelta(hours=1),
                effective_at=NOW - timedelta(hours=1),
                received_at=NOW,
            )
        )
        membership.refresh_from_db()
        with connection.cursor() as cur:
            cur.execute(
                "SELECT projection, eligible_count, served_from, fingerprint FROM public.s015_effective_state('core_organismmembership', %s, %s, %s)",
                (membership.pk, membership.current_state_effective_at, membership.state_known_at),
            )
            projection, eligible_count, served_from, fingerprint = cur.fetchone()
        self.assertEqual(projection, membership.state)
        self.assertEqual(eligible_count, 2)
        self.assertEqual(served_from, "CACHE_REQUALIFIED")
        self.assertEqual(fingerprint, membership.state_source_event_set_fingerprint)
        self.assertEqual(membership.state, "ENDED")
        self.assertEqual(retrospective.temporal_basis_kind, "RETROSPECTIVE")
        self.assertIsNotNone(retrospective.temporal_basis_reference)

    def test_out_of_order_transition_raises_il3_and_writes_nothing(self):
        ids, actor, target = self._setup_organism("out-of-order")
        service = self._service(_ExactProvider())
        membership = self._fixture_membership(ids, target, "ACTIVE", "out-of-order-active-prior",
                                               at=NOW - timedelta(hours=2))
        transition_count = OrganismMembershipTransition.objects.count()
        receipt_count = OrganismCommandReceipt.objects.count()

        with self.assertRaisesRegex(S015OperationNotYetImplemented, "IL-3"):
            service.transition_membership(
                self._transition_command(
                    actor=actor,
                    membership_uuid=membership.membership_uuid,
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

    def test_insert_time_refusal_rolls_back(self):
        ids, actor, target = self._setup_organism("atomic-failure")
        service = self._service(_ExactProvider())
        future = FAR_FUTURE
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

    def test_loi_x1_s1_departure_and_registered_session_complete_without_cycle(self):
        """On the pre-0032 implementation this interleaving ended in deadlock detected (Vision Chamber X1)."""
        ids, actor, target = self._setup_organism(f"loi-x1-{fresh()}")
        membership = self._fixture_membership(ids, target, "ACTIVE", "loi-x1-active")
        organism_uuid = ids["organism_uuid"]
        with connection.cursor() as cursor:  # TEST SETUP: remove fixture residue before seeding one stale row.
            cursor.execute(
                "DELETE FROM public.s015_transaction_register WHERE kind = 'ORGANISM_LOCK' "
                "AND pg_xact_status(xid) IS DISTINCT FROM 'in progress'",
                params=None,
            )
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "SELECT public.s015_lock_organisms(ARRAY[%s]::uuid[])",
                [organism_uuid],
            )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM public.s015_transaction_register "
                "WHERE kind = 'ORGANISM_LOCK' AND organism_uuid = %s::uuid "
                "AND pg_xact_status(xid) IS DISTINCT FROM 'in progress'",
                [organism_uuid],
            )
            self.assertEqual(cursor.fetchone()[0], 1)
            cursor.execute(
                "SELECT count(*) FROM public.s015_transaction_register "
                "WHERE kind = 'ORGANISM_LOCK' AND pg_xact_status(xid) IS DISTINCT FROM 'in progress'",
                params=None,
            )
            self.assertEqual(cursor.fetchone()[0], 1)

        entered = threading.Event()
        release = threading.Event()

        class _PausingProvider:
            def evaluate_organism_authority(self, request):
                # TEST DEVICE: freeze only this S1 act inside authority after its LO locks.
                entered.set()
                if not release.wait(timeout=30):
                    raise TimeoutError("S1 authority pause was not released")
                return S015PostFoundingAuthorityResponse(
                    **asdict(request),
                    authority_reference="authority:fixture",
                )

        workers = []
        self.addCleanup(cleanup_workers, workers)
        self.addCleanup(release.set)

        def depart():
            worker_actor = Identity.objects.get(pk=actor.pk)
            service = self._service(_PausingProvider())
            service.transition_membership(
                self._transition_command(
                    actor=worker_actor,
                    membership_uuid=membership.membership_uuid,
                    resulting_state="ENDED",
                    request_reference="request:loi-x1:departure",
                    idempotency_key="idem:loi-x1:departure",
                    reason_class="TEST_ONLY_REASON_LOI_X1",
                )
            )

        s1 = start_worker(depart)
        workers.append(s1)
        self.assertTrue(entered.wait(timeout=10))

        def register_organism():
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(
                    "SELECT public.s015_lock_organisms(ARRAY[%s]::uuid[])",
                    [organism_uuid],
                )

        registered = start_worker(register_organism)
        workers.append(registered)
        with connection.cursor() as cursor:
            wait_until_locked(
                cursor,
                registered.pid(),
                s1.pid(),
                relations={"core_livingorganism"},
                locktype="transactionid",
            )
            observation = exact_wait(cursor, registered.pid())
        self.assertEqual(observation["waiting"]["locktype"], "transactionid")
        self.assertIn(s1.pid(), observation["holders"])
        self.assertEqual(observation["tuple_relation"], "core_livingorganism")

        release.set()
        s1_result = s1.result(timeout=30)
        registered_result = registered.result(timeout=30)
        self.assertEqual(s1_result, "ok")
        self.assertEqual(registered_result, "ok")
        self.assertNotIn("deadlock detected", str(s1_result).lower())
        self.assertNotIn("deadlock detected", str(registered_result).lower())
        membership.refresh_from_db()
        self.assertEqual(membership.state, "ENDED")