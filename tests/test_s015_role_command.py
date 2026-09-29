"""PKT-B slice S2 — office appointments and authorised endings. PostgreSQL, disposable test database.

Commission: COPILOT_COMMISSION_PKT_B_S2_v1_1.md with the Human Governor's rulings of 29 September
2026 (IL-7 provenance, IL-8, no reason input, COORDINATOR-only). Test ids R1–R19 map to the
commission's §5 table plus the ruling's additions; R16 is S1's own suite, run unchanged by the
Human Governor — this slice edits no S1 behaviour, only the shared-helper move of §2.

Every fixture label and token is visibly test-only (u21g: tags, TEST_ONLY_* markers). The
setUp/tearDown pair mirrors S1's harness: the landed s015_platform_root_unset_ck is dropped for
fixture latitude and restored with the landed NOT VALID pattern, though this suite founds no root.
"""
from __future__ import annotations

import threading
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from queue import Queue
from threading import Barrier
from uuid import UUID
from unittest import skipUnless

from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import TransactionTestCase

from core.models import (
    ContextualRoleAssignment,
    ContextualRoleAssignmentStateEvent,
    Identity,
    OrganismCommandReceipt,
    OrganismMembership,
)
from core.organism_contract import OrganismAction
from s015_0021_support import T0, append_event, drop_unset_check, empty_fp, found_nonroot, refresh, seed_identity, u
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
from src.intevia.services.organism_command_support import (
    S015CommandReplayMismatch,
)
from src.intevia.services.organism_membership_service import (
    S015OperationNotYetImplemented,
)
from src.intevia.services.organism_role_service import (
    AssignmentCommandResult,
    OrganismRoleService,
    ProposeAssignmentCommand,
    S015AssignmentEdgeRefused,
    S015AssignmentMembershipRefused,
    S015AssignmentStateUnused,
    S015AssignmentTermRefused,
    TransitionAssignmentCommand,
)

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
FAR_FUTURE = datetime(2999, 1, 1, tzinfo=timezone.utc)  # later than any real recorded_at, so the four-time check refuses it on every run
POSTGRESQL_ONLY = skipUnless(connection.vendor == "postgresql", "role command slice is PostgreSQL-only")


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


def _add_member(cur, ids, tag, state="ACTIVE"):
    """A second member of the LO with its own identity and a sequence-1 membership chain.
    Test-only fixture; the landed admission routes are S3's."""
    member_pk = seed_identity(cur, tag)
    m_uuid = u(f"membership:{tag}")
    cur.execute(
        "INSERT INTO core_organismmembership (membership_uuid, state, created_at, current_state_effective_at, state_known_at, state_source_event_set_fingerprint, identity_id, living_organism_id) "
        "VALUES (%s, %s, now(), %s, %s, %s, %s, %s) RETURNING id",
        (m_uuid, state, T0, T0, empty_fp(cur, "core_organismmembership", m_uuid), member_pk, ids["lo"]),
    )
    membership_id = cur.fetchone()[0]
    append_event(
        cur, "core_organismmembershiptransition", membership_id, 1, "JOIN", None, state,
        member_pk, ids["basis"], tag, extra={"reason_class": "TEST_ONLY_ADMISSION"},
    )
    refresh(cur, "core_organismmembership", membership_id)
    return membership_id, member_pk, UUID(m_uuid)


def _add_role_definition(cur, ids, tag, code, scope="LIVING_ORGANISM"):
    """An active role definition on the LO. Test-only fixture; definition governance is not S2's."""
    r_uuid = u(f"role:{tag}:{code}")
    cur.execute(
        "INSERT INTO core_organismroledefinition (role_uuid, code, scope, definition_version, active, created_at, living_organism_id) "
        "VALUES (%s, %s, %s, 1, true, now(), %s) RETURNING id",
        (r_uuid, code, scope, ids["lo"]),
    )
    return cur.fetchone()[0], UUID(r_uuid)


def _assignment_at_state(cur, ids, membership_id, role_definition_id, tag, state, action="ASSIGN"):
    """An office anchor directly at `state` with a single sequence-1 event. Test-only fixture."""
    a_uuid = u(f"assignment:{tag}")
    cur.execute(
        "INSERT INTO core_contextualroleassignment (assignment_uuid, state, created_at, current_state_effective_at, state_known_at, state_source_event_set_fingerprint, membership_id, role_definition_id) "
        "VALUES (%s, %s, now(), %s, %s, %s, %s, %s) RETURNING id",
        (a_uuid, state, T0, T0, empty_fp(cur, "core_contextualroleassignment", a_uuid), membership_id, role_definition_id),
    )
    assignment_id = cur.fetchone()[0]
    append_event(
        cur, "core_contextualroleassignmentstateevent", assignment_id, 1, action, None, state,
        ids["identity"], ids["basis"], tag,
    )
    refresh(cur, "core_contextualroleassignment", assignment_id)
    return assignment_id, UUID(a_uuid)


@POSTGRESQL_ONLY
class S015RoleCommandTests(TransactionTestCase):
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
        return OrganismRoleService(
            authority=S015Authority(provider=provider, database_alias="default"),
            clock=lambda: NOW,
            database_alias="default",
        )

    def _setup_organism(self, label: str = "role"):
        """A non-root LO: founder member holding the founding COORDINATOR office (IL-7's protected
        shape: the office's first event was written by the LO's founding transaction)."""
        with transaction.atomic():
            with connection.cursor() as cur:
                ids = found_nonroot(cur, label)
        founder = Identity.objects.get(pk=ids["identity"])
        with connection.cursor() as cur:
            cur.execute("SELECT organism_id FROM core_livingorganism WHERE id = %s", (ids["lo"],))
            organism_id = cur.fetchone()[0]
            cur.execute("SELECT membership_uuid FROM core_organismmembership WHERE id = %s", (ids["membership"],))
            founder_membership_uuid = UUID(str(cur.fetchone()[0]))
            cur.execute(
                "SELECT assignment_uuid FROM core_contextualroleassignment WHERE id = %s",
                (ids["assignments"]["COORDINATOR"],),
            )
            founding_assignment_uuid = UUID(str(cur.fetchone()[0]))
            cur.execute("SELECT role_uuid FROM core_organismroledefinition WHERE id = %s", (ids["roles"]["COORDINATOR"],))
            coordinator_role_uuid = UUID(str(cur.fetchone()[0]))
        ids["organism_uuid"] = UUID(str(organism_id))
        ids["founder_membership_uuid"] = founder_membership_uuid
        ids["founding_assignment_uuid"] = founding_assignment_uuid
        ids["coordinator_role_uuid"] = coordinator_role_uuid
        return ids, founder

    def _add_active_member(self, ids, label):
        with transaction.atomic():
            with connection.cursor() as cur:
                membership_id, member_pk, member_membership_uuid = _add_member(cur, ids, f"{label}-member")
        member = Identity.objects.get(pk=member_pk)
        return membership_id, member, member_membership_uuid

    def _counts(self):
        return (
            ContextualRoleAssignment.objects.count(),
            ContextualRoleAssignmentStateEvent.objects.count(),
            OrganismCommandReceipt.objects.count(),
        )

    def _propose_command(self, *, actor, membership_uuid, role_definition_uuid, request_reference, idempotency_key, effective_until_state="NONE", effective_until=None, occurred_at=NOW, effective_at=NOW, received_at=NOW):
        return ProposeAssignmentCommand(
            credential=actor.credential,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
            effective_at=effective_at,
            received_at=received_at,
            membership_uuid=membership_uuid,
            role_definition_uuid=role_definition_uuid,
            effective_until_state=effective_until_state,
            effective_until=effective_until,
        )

    def _transition_command(self, *, actor, assignment_uuid, action, resulting_state, request_reference, idempotency_key, effective_until_state=None, effective_until=None, occurred_at=NOW, effective_at=NOW, received_at=NOW):
        return TransitionAssignmentCommand(
            credential=actor.credential,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
            effective_at=effective_at,
            received_at=received_at,
            assignment_uuid=assignment_uuid,
            action=action,
            resulting_state=resulting_state,
            effective_until_state=effective_until_state,
            effective_until=effective_until,
        )

    def _propose_then_assign(self, *, label="role", effective_until_state="NONE", effective_until=None, at=NOW):
        ids, founder = self._setup_organism(label)
        _, member, member_membership_uuid = self._add_active_member(ids, label)
        service = self._service(_ExactProvider())
        propose_result = service.propose_assignment(
            self._propose_command(
                actor=founder,
                membership_uuid=member_membership_uuid,
                role_definition_uuid=ids["coordinator_role_uuid"],
                request_reference=f"request:{label}:propose",
                idempotency_key=f"idem:{label}:propose",
                effective_until_state=effective_until_state,
                effective_until=effective_until,
                occurred_at=at,
                effective_at=at,
                received_at=at,
            )
        )
        assign_result = service.transition_assignment(
            self._transition_command(
                actor=founder,
                assignment_uuid=propose_result.assignment_uuid,
                action="ASSIGN",
                resulting_state="ACTIVE",
                request_reference=f"request:{label}:assign",
                idempotency_key=f"idem:{label}:assign",
                # the appointing decision supplies the term (§3.3)
                effective_until_state=effective_until_state,
                effective_until=effective_until,
                occurred_at=at,
                effective_at=at,
                received_at=at,
            )
        )
        return ids, founder, member, service, propose_result, assign_result

    # R1 — propose, then appoint (ASSIGN): sequences 1, 2; state ACTIVE; two receipts; the term on ASSIGN.
    def test_propose_then_appoint_commits_and_refreshes_cache(self):
        until = NOW + timedelta(days=180)
        ids, founder, member, service, propose_result, assign_result = self._propose_then_assign(
            effective_until_state="FIXED", effective_until=until,
        )
        assignment = ContextualRoleAssignment.objects.get(pk=assign_result.assignment_id)
        events = list(
            ContextualRoleAssignmentStateEvent.objects.filter(assignment=assignment).order_by("sequence")
        )
        receipts = list(
            OrganismCommandReceipt.objects.filter(actor=founder, action=OrganismAction.TRANSITION_CONTEXTUAL_ROLE.value)
        )

        self.assertEqual(propose_result.resulting_state, "PROPOSED")
        self.assertEqual(assign_result.resulting_state, "ACTIVE")
        self.assertEqual(assignment.state, "ACTIVE")
        self.assertEqual(assignment.head_state_event_id, assign_result.event_id)
        self.assertEqual([event.sequence for event in events], [1, 2])
        self.assertEqual([event.action for event in events], ["PROPOSE", "ASSIGN"])
        self.assertIsNone(events[0].predecessor_id)
        self.assertEqual(events[1].predecessor_id, events[0].id)
        self.assertEqual(len(receipts), 2)
        with connection.cursor() as cur:
            cur.execute(
                "SELECT effective_until_state, effective_until FROM public.core_contextualroleassignmentstateevent WHERE id = ANY(%s) ORDER BY sequence",
                ([event.id for event in events],),
            )
            propose_term, assign_term = cur.fetchall()
        # the PROPOSE row establishes no term (a chain row's effective_until states when that
        # event's effect ends); the proposed term is proposal content in the fingerprints
        self.assertEqual(propose_term, ("NOT_ESTABLISHED", None))
        # the appointing decision supplies the term, written on the ASSIGN row
        self.assertEqual(assign_term, ("FIXED", until))
        self.assertEqual(assign_result.effective_until_state, "FIXED")
        self.assertEqual(assign_result.effective_until, until)
        # the propose result and receipt describe the written row (the Reviewer's S2 F4)
        self.assertEqual(propose_result.effective_until_state, "NOT_ESTABLISHED")
        self.assertIsNone(propose_result.effective_until)
        propose_receipts = [r for r in receipts if r.result_payload["event_action"] == "PROPOSE"]
        self.assertEqual(len(propose_receipts), 1)
        self.assertEqual(propose_receipts[0].result_payload["effective_until_state"], "NOT_ESTABLISHED")
        self.assertIsNone(propose_receipts[0].result_payload["effective_until"])

    # R2 — the term: FIXED with a value; NONE without; FIXED with effective_until <= effective_at refused before any write.
    def test_term_validation_refused_before_any_write(self):
        ids, founder = self._setup_organism("term")
        _, member, member_membership_uuid = self._add_active_member(ids, "term")
        service = self._service(_ExactProvider())
        base = dict(
            actor=founder,
            membership_uuid=member_membership_uuid,
            role_definition_uuid=ids["coordinator_role_uuid"],
        )
        before = self._counts()
        with self.assertRaises(S015AssignmentTermRefused):
            service.propose_assignment(
                self._propose_command(
                    **base,
                    request_reference="request:term:fixed-missing",
                    idempotency_key="idem:term:fixed-missing",
                    effective_until_state="FIXED",
                    effective_until=None,
                )
            )
        with self.assertRaises(S015AssignmentTermRefused):
            service.propose_assignment(
                self._propose_command(
                    **base,
                    request_reference="request:term:not-later",
                    idempotency_key="idem:term:not-later",
                    effective_until_state="FIXED",
                    effective_until=NOW,
                )
            )
        with self.assertRaises(S015AssignmentTermRefused):
            service.propose_assignment(
                self._propose_command(
                    **base,
                    request_reference="request:term:none-with-value",
                    idempotency_key="idem:term:none-with-value",
                    effective_until_state="NONE",
                    effective_until=NOW + timedelta(days=1),
                )
            )
        self.assertEqual(self._counts(), before)

        fixed = service.propose_assignment(
            self._propose_command(
                **base,
                request_reference="request:term:fixed",
                idempotency_key="idem:term:fixed",
                effective_until_state="FIXED",
                effective_until=NOW + timedelta(days=90),
            )
        )
        open_ended = service.propose_assignment(
            self._propose_command(
                **base,
                request_reference="request:term:none",
                idempotency_key="idem:term:none",
                effective_until_state="NONE",
            )
        )
        self.assertEqual(fixed.resulting_state, "PROPOSED")
        self.assertEqual(open_ended.resulting_state, "PROPOSED")
        self.assertIsNone(open_ended.effective_until)
        after_proposals = self._counts()

        # the appointing decision supplies the term: required for ASSIGN, refused for END and
        # REASSIGN — before any write
        assign_base = dict(
            actor=founder,
            assignment_uuid=fixed.assignment_uuid,
            request_reference="request:term:assign",
            idempotency_key="idem:term:assign",
        )
        with self.assertRaises(S015AssignmentTermRefused):
            service.transition_assignment(
                self._transition_command(**assign_base, action="ASSIGN", resulting_state="ACTIVE")
            )
        with self.assertRaises(S015AssignmentTermRefused):
            service.transition_assignment(
                self._transition_command(
                    **assign_base, action="ASSIGN", resulting_state="ACTIVE",
                    effective_until_state="FIXED", effective_until=NOW,
                )
            )
        with self.assertRaises(S015AssignmentTermRefused):
            service.transition_assignment(
                self._transition_command(
                    **assign_base, action="END", resulting_state="ENDED",
                    effective_until_state="NONE",
                )
            )
        self.assertEqual(self._counts(), after_proposals)
        self.assertEqual(ContextualRoleAssignment.objects.get(pk=fixed.assignment_id).state, "PROPOSED")
        self.assertEqual(
            ContextualRoleAssignmentStateEvent.objects.filter(assignment_id=fixed.assignment_id).count(), 1,
        )

    # R3 — the no-tolling value in the ASSIGN payload and the receipt; a changed term under the
    # same ASSIGN key is a replay mismatch. The tolling value is not a command input (it is the
    # design default), so the reachable mismatch is the changed term; the value itself is asserted
    # on the receipt.
    def test_tolling_snapshot_recorded_and_term_change_mismatches(self):
        until = NOW + timedelta(days=30)
        ids, founder = self._setup_organism("tolling")
        _, member, member_membership_uuid = self._add_active_member(ids, "tolling")
        service = self._service(_ExactProvider())
        propose_result = service.propose_assignment(
            self._propose_command(
                actor=founder,
                membership_uuid=member_membership_uuid,
                role_definition_uuid=ids["coordinator_role_uuid"],
                request_reference="request:tolling:propose",
                idempotency_key="idem:tolling:propose",
            )
        )
        assign_result = service.transition_assignment(
            self._transition_command(
                actor=founder,
                assignment_uuid=propose_result.assignment_uuid,
                action="ASSIGN",
                resulting_state="ACTIVE",
                request_reference="request:tolling:assign",
                idempotency_key="idem:tolling:assign",
                effective_until_state="FIXED",
                effective_until=until,
            )
        )
        self.assertEqual(assign_result.suspension_tolls_term, "NO")
        self.assertEqual(assign_result.suspension_tolls_term_source, "DEFAULT (no settings store)")
        self.assertEqual(assign_result.effective_until_state, "FIXED")
        self.assertEqual(assign_result.effective_until, until)
        receipt = OrganismCommandReceipt.objects.get(
            actor=founder,
            action=OrganismAction.TRANSITION_CONTEXTUAL_ROLE.value,
            idempotency_key="idem:tolling:assign",
        )
        self.assertEqual(receipt.result_payload["suspension_tolls_term"], "NO")
        self.assertEqual(receipt.result_payload["suspension_tolls_term_source"], "DEFAULT (no settings store)")
        self.assertEqual(receipt.result_payload["effective_until_state"], "FIXED")
        event = ContextualRoleAssignmentStateEvent.objects.get(pk=assign_result.event_id)
        self.assertEqual(event.payload_fingerprint, receipt.payload_fingerprint)
        with connection.cursor() as cur:
            cur.execute(
                "SELECT effective_until_state, effective_until FROM public.core_contextualroleassignmentstateevent WHERE id = %s",
                (assign_result.event_id,),
            )
            self.assertEqual(cur.fetchone(), ("FIXED", until))

        # the identical appointing decision under the same key replays the first result
        same = service.transition_assignment(
            self._transition_command(
                actor=founder,
                assignment_uuid=propose_result.assignment_uuid,
                action="ASSIGN",
                resulting_state="ACTIVE",
                request_reference="request:tolling:assign",
                idempotency_key="idem:tolling:assign",
                effective_until_state="FIXED",
                effective_until=until,
            )
        )
        self.assertEqual(same, assign_result)
        # a changed term under the same key mismatches
        with self.assertRaises(S015CommandReplayMismatch):
            service.transition_assignment(
                self._transition_command(
                    actor=founder,
                    assignment_uuid=propose_result.assignment_uuid,
                    action="ASSIGN",
                    resulting_state="ACTIVE",
                    request_reference="request:tolling:assign",
                    idempotency_key="idem:tolling:assign",
                    effective_until_state="FIXED",
                    effective_until=until + timedelta(days=1),
                )
            )

    # R4 — END by an authorised decision; history intact.
    def test_end_by_authorised_decision_keeps_history(self):
        ids, founder, member, service, propose_result, assign_result = self._propose_then_assign(label="end")
        end_result = service.transition_assignment(
            self._transition_command(
                actor=founder,
                assignment_uuid=assign_result.assignment_uuid,
                action="END",
                resulting_state="ENDED",
                request_reference="request:end:end",
                idempotency_key="idem:end:end",
            )
        )
        self.assertEqual(end_result.resulting_state, "ENDED")
        self.assertEqual(end_result.event_sequence, 3)
        assignment = ContextualRoleAssignment.objects.get(pk=assign_result.assignment_id)
        events = list(
            ContextualRoleAssignmentStateEvent.objects.filter(assignment=assignment).order_by("sequence")
        )
        self.assertEqual(assignment.state, "ENDED")
        self.assertEqual([event.action for event in events], ["PROPOSE", "ASSIGN", "END"])
        self.assertEqual([event.prior_state for event in events], [None, "PROPOSED", "ACTIVE"])
        self.assertEqual([event.resulting_state for event in events], ["PROPOSED", "ACTIVE", "ENDED"])

    # R5 — REASSIGN ends the office; a fresh appointment is a new anchor.
    def test_reassign_ends_and_fresh_appointment_is_new_anchor(self):
        ids, founder, member, service, propose_result, assign_result = self._propose_then_assign(label="reassign")
        reassign_result = service.transition_assignment(
            self._transition_command(
                actor=founder,
                assignment_uuid=assign_result.assignment_uuid,
                action="REASSIGN",
                resulting_state="ENDED",
                request_reference="request:reassign:end",
                idempotency_key="idem:reassign:end",
            )
        )
        self.assertEqual(reassign_result.resulting_state, "ENDED")
        self.assertEqual(reassign_result.event_action, "REASSIGN")
        assignment = ContextualRoleAssignment.objects.get(pk=assign_result.assignment_id)
        self.assertEqual(assignment.state, "ENDED")
        # a fresh appointment is a new anchor (U21g-6): no uniqueness constraint blocks it
        membership = OrganismMembership.objects.get(identity=member, living_organism_id=ids["lo"])
        fresh_result = service.propose_assignment(
            self._propose_command(
                actor=founder,
                membership_uuid=membership.membership_uuid,
                role_definition_uuid=ids["coordinator_role_uuid"],
                request_reference="request:reassign:fresh",
                idempotency_key="idem:reassign:fresh",
            )
        )
        self.assertEqual(fresh_result.resulting_state, "PROPOSED")
        self.assertNotEqual(fresh_result.assignment_id, assign_result.assignment_id)
        self.assertNotEqual(fresh_result.assignment_uuid, assign_result.assignment_uuid)

    # R6 — edges outside the decided set, including PROPOSED -> ENDED and every PROBATIONARY edge,
    # are refused; no row.
    def test_undecided_edges_and_tokens_refused_without_write(self):
        ids, founder = self._setup_organism("edge")
        _, member, member_membership_uuid = self._add_active_member(ids, "edge")
        service = self._service(_ExactProvider())
        propose_result = service.propose_assignment(
            self._propose_command(
                actor=founder,
                membership_uuid=member_membership_uuid,
                role_definition_uuid=ids["coordinator_role_uuid"],
                request_reference="request:edge:propose",
                idempotency_key="idem:edge:propose",
            )
        )
        assignment_uuid = propose_result.assignment_uuid
        before = self._counts()
        # PROPOSED -> ENDED: a proposal is not declined by any decided edge
        with self.assertRaises(S015AssignmentEdgeRefused):
            service.transition_assignment(
                self._transition_command(
                    actor=founder, assignment_uuid=assignment_uuid, action="END", resulting_state="ENDED",
                    request_reference="request:edge:decline", idempotency_key="idem:edge:decline",
                )
            )
        # no decided edge enters PROBATIONARY
        with self.assertRaises(S015AssignmentEdgeRefused):
            service.transition_assignment(
                self._transition_command(
                    actor=founder, assignment_uuid=assignment_uuid, action="ASSIGN", resulting_state="PROBATIONARY",
                    effective_until_state="NONE",
                    request_reference="request:edge:probationary", idempotency_key="idem:edge:probationary",
                )
            )
        # PROPOSE is propose_assignment's act, refused as a transition input before authority
        with self.assertRaises(S015AssignmentEdgeRefused):
            service.transition_assignment(
                self._transition_command(
                    actor=founder, assignment_uuid=assignment_uuid, action="PROPOSE", resulting_state="PROPOSED",
                    request_reference="request:edge:repropose", idempotency_key="idem:edge:repropose",
                )
            )
        # an undecided action token, refused before authority
        with self.assertRaises(S015AssignmentEdgeRefused):
            service.transition_assignment(
                self._transition_command(
                    actor=founder, assignment_uuid=assignment_uuid, action="PROMOTE", resulting_state="ACTIVE",
                    request_reference="request:edge:promote", idempotency_key="idem:edge:promote",
                )
            )
        # an unused state token, refused before authority
        with self.assertRaises(S015AssignmentStateUnused):
            service.transition_assignment(
                self._transition_command(
                    actor=founder, assignment_uuid=assignment_uuid, action="END", resulting_state="PRIVACY_TRANSFORMED",
                    request_reference="request:edge:privacy", idempotency_key="idem:edge:privacy",
                )
            )
        self.assertEqual(self._counts(), before)

    # R6b — an office standing at PROBATIONARY (a test-only fixture; no decided edge enters it)
    # has no decided out-edge: END, REASSIGN and ASSIGN out of it are refused; no row (the
    # Reviewer's S2 F1).
    def test_probationary_office_out_edges_refused_without_write(self):
        ids, founder = self._setup_organism("probedge")
        membership_id, member, _ = self._add_active_member(ids, "probedge")
        with transaction.atomic():
            with connection.cursor() as cur:
                _, probationary_uuid = _assignment_at_state(
                    cur, ids, membership_id, ids["roles"]["COORDINATOR"], "probedge:fixture", "PROBATIONARY",
                )
        service = self._service(_ExactProvider())
        before = self._counts()
        for action, resulting_state, term_state in (
            ("END", "ENDED", None),
            ("REASSIGN", "ENDED", None),
            ("ASSIGN", "ACTIVE", "NONE"),
        ):
            with self.assertRaises(S015AssignmentEdgeRefused):
                service.transition_assignment(
                    self._transition_command(
                        actor=founder, assignment_uuid=probationary_uuid, action=action,
                        resulting_state=resulting_state, effective_until_state=term_state,
                        request_reference=f"request:probedge:{action.lower()}",
                        idempotency_key=f"idem:probedge:{action.lower()}",
                    )
                )
        self.assertEqual(self._counts(), before)

    # R7 — IL-4: suspension, both directions, refused; no row.
    def test_role_suspension_refused_both_directions_il4(self):
        ids, founder, member, service, propose_result, assign_result = self._propose_then_assign(label="suspend")
        before = self._counts()
        with self.assertRaisesRegex(S015OperationNotYetImplemented, r"IL-4, S2b"):
            service.transition_assignment(
                self._transition_command(
                    actor=founder, assignment_uuid=assign_result.assignment_uuid, action="SUSPEND", resulting_state="SUSPENDED",
                    request_reference="request:suspend:on", idempotency_key="idem:suspend:on",
                )
            )
        # the other direction, on a test-only fixture anchor standing at SUSPENDED
        membership_id = OrganismMembership.objects.get(identity=member, living_organism_id=ids["lo"]).pk
        with transaction.atomic():
            with connection.cursor() as cur:
                _, suspended_uuid = _assignment_at_state(
                    cur, ids, membership_id, ids["roles"]["COORDINATOR"], "suspend:fixture", "SUSPENDED", action="SUSPEND",
                )
        with self.assertRaisesRegex(S015OperationNotYetImplemented, r"IL-4, S2b"):
            service.transition_assignment(
                self._transition_command(
                    actor=founder, assignment_uuid=suspended_uuid, action="SUSPEND", resulting_state="ACTIVE",
                    request_reference="request:suspend:off", idempotency_key="idem:suspend:off",
                )
            )
        self.assertEqual(ContextualRoleAssignment.objects.count(), before[0] + 1)
        self.assertEqual(ContextualRoleAssignmentStateEvent.objects.count(), before[1] + 1)
        self.assertEqual(OrganismCommandReceipt.objects.count(), before[2])

    # R8 — IL-6: a Circle-scoped proposal refused; no row.
    def test_circle_scoped_office_refused_il6(self):
        ids, founder = self._setup_organism("circle")
        _, member, member_membership_uuid = self._add_active_member(ids, "circle")
        with transaction.atomic():
            with connection.cursor() as cur:
                _, circle_role_uuid = _add_role_definition(cur, ids, "circle", "TEST_ONLY_CIRCLE_SCRIBE", scope="CIRCLE")
        service = self._service(_ExactProvider())
        before = self._counts()
        with self.assertRaisesRegex(S015OperationNotYetImplemented, r"IL-6, S4"):
            service.propose_assignment(
                self._propose_command(
                    actor=founder,
                    membership_uuid=member_membership_uuid,
                    role_definition_uuid=circle_role_uuid,
                    request_reference="request:circle:propose",
                    idempotency_key="idem:circle:propose",
                )
            )
        self.assertEqual(self._counts(), before)

    # R9 — IL-3: an out-of-order office event refused; no row.
    def test_out_of_order_event_raises_il3_and_writes_nothing(self):
        past = NOW - timedelta(hours=2)
        ids, founder, member, service, propose_result, assign_result = self._propose_then_assign(
            label="out-of-order", at=past,
        )
        before = self._counts()
        with self.assertRaisesRegex(S015OperationNotYetImplemented, "IL-3"):
            service.transition_assignment(
                self._transition_command(
                    actor=founder,
                    assignment_uuid=assign_result.assignment_uuid,
                    action="END",
                    resulting_state="ENDED",
                    request_reference="request:out-of-order:end",
                    idempotency_key="idem:out-of-order:end",
                    occurred_at=NOW - timedelta(hours=3),
                    effective_at=NOW - timedelta(hours=3),
                    received_at=NOW,
                )
            )
        assignment = ContextualRoleAssignment.objects.get(pk=assign_result.assignment_id)
        self.assertEqual(assignment.state, "ACTIVE")
        self.assertEqual(self._counts(), before)

    # R10a — the founding assignment is identified by its provenance; another authorised actor's
    # END and REASSIGN are refused with IL-7. No row, no receipt.
    def test_founder_original_office_protected_from_other_actors_il7(self):
        ids, founder = self._setup_organism("founder-guard")
        _, other, _ = self._add_active_member(ids, "founder-guard")
        service = self._service(_ExactProvider())
        founding_uuid = ids["founding_assignment_uuid"]
        before = self._counts()
        for action in ("END", "REASSIGN"):
            with self.subTest(action=action):
                with self.assertRaisesRegex(S015OperationNotYetImplemented, r"IL-7, SB") as caught:
                    service.transition_assignment(
                        self._transition_command(
                            actor=other,
                            assignment_uuid=founding_uuid,
                            action=action,
                            resulting_state="ENDED",
                            request_reference=f"request:founder-guard:{action.lower()}",
                            idempotency_key=f"idem:founder-guard:{action.lower()}",
                        )
                    )
                message = str(caught.exception)
                self.assertIn("not yet built", message)
                self.assertNotIn("not birthed", message)
        self.assertEqual(self._counts(), before)
        assignment = ContextualRoleAssignment.objects.get(assignment_uuid=founding_uuid)
        self.assertEqual(assignment.state, "ACTIVE")

    # R10b — the founder's own END of that assignment is admitted, subject to authority.
    def test_founder_own_ending_of_original_office_admitted(self):
        ids, founder = self._setup_organism("founder-end")
        service = self._service(_ExactProvider())
        result = service.transition_assignment(
            self._transition_command(
                actor=founder,
                assignment_uuid=ids["founding_assignment_uuid"],
                action="END",
                resulting_state="ENDED",
                request_reference="request:founder-end:end",
                idempotency_key="idem:founder-end:end",
            )
        )
        self.assertEqual(result.resulting_state, "ENDED")
        self.assertEqual(result.event_sequence, 2)
        assignment = ContextualRoleAssignment.objects.get(assignment_uuid=ids["founding_assignment_uuid"])
        events = list(
            ContextualRoleAssignmentStateEvent.objects.filter(assignment=assignment).order_by("sequence")
        )
        self.assertEqual(assignment.state, "ENDED")
        self.assertEqual([event.action for event in events], ["ASSIGN", "END"])

    # R10c — a denied authority on the same attempt gets the authority error, not IL-7.
    def test_denied_authority_preempts_founder_protection(self):
        ids, founder = self._setup_organism("founder-denied")
        _, other, _ = self._add_active_member(ids, "founder-denied")
        denied_service = self._service(_DeniedProvider())
        before = self._counts()
        with self.assertRaises(S015AuthorityDenied):
            denied_service.transition_assignment(
                self._transition_command(
                    actor=other,
                    assignment_uuid=ids["founding_assignment_uuid"],
                    action="END",
                    resulting_state="ENDED",
                    request_reference="request:founder-denied:end",
                    idempotency_key="idem:founder-denied:end",
                )
            )
        self.assertEqual(self._counts(), before)

    # R10e — protection is read from the first event, so an anchor UPDATE cannot lift it: refresh
    # the founding office's cache (which updates the anchor row), then another authorised actor's
    # END is still refused with IL-7 and nothing is written.
    def test_founder_protection_survives_anchor_updates(self):
        ids, founder = self._setup_organism("founder-refresh")
        _, other, _ = self._add_active_member(ids, "founder-refresh")
        service = self._service(_ExactProvider())
        founding_assignment_id = ids["assignments"]["COORDINATOR"]
        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute(
                    "SELECT public.s015_refresh_governed_cache(%s, %s, %s, clock_timestamp())",
                    ["core_contextualroleassignment", founding_assignment_id, T0],
                )
        assignment = ContextualRoleAssignment.objects.get(pk=founding_assignment_id)
        self.assertEqual(assignment.state, "ACTIVE")
        before = self._counts()
        with self.assertRaisesRegex(S015OperationNotYetImplemented, r"IL-7, SB"):
            service.transition_assignment(
                self._transition_command(
                    actor=other,
                    assignment_uuid=ids["founding_assignment_uuid"],
                    action="END",
                    resulting_state="ENDED",
                    request_reference="request:founder-refresh:end",
                    idempotency_key="idem:founder-refresh:end",
                )
            )
        self.assertEqual(self._counts(), before)

    # R10d — a later fresh COORDINATOR appointment held by the founder is not protected.
    def test_later_appointment_held_by_founder_is_not_protected(self):
        ids, founder = self._setup_organism("founder-later")
        _, other, _ = self._add_active_member(ids, "founder-later")
        service = self._service(_ExactProvider())
        propose_result = service.propose_assignment(
            self._propose_command(
                actor=founder,
                membership_uuid=ids["founder_membership_uuid"],
                role_definition_uuid=ids["coordinator_role_uuid"],
                request_reference="request:founder-later:propose",
                idempotency_key="idem:founder-later:propose",
            )
        )
        assign_result = service.transition_assignment(
            self._transition_command(
                actor=founder,
                assignment_uuid=propose_result.assignment_uuid,
                action="ASSIGN",
                resulting_state="ACTIVE",
                effective_until_state="NONE",
                request_reference="request:founder-later:assign",
                idempotency_key="idem:founder-later:assign",
            )
        )
        end_result = service.transition_assignment(
            self._transition_command(
                actor=other,
                assignment_uuid=assign_result.assignment_uuid,
                action="END",
                resulting_state="ENDED",
                request_reference="request:founder-later:end",
                idempotency_key="idem:founder-later:end",
            )
        )
        self.assertEqual(end_result.resulting_state, "ENDED")
        assignment = ContextualRoleAssignment.objects.get(pk=assign_result.assignment_id)
        self.assertEqual(assignment.state, "ENDED")
        # the founding assignment itself is untouched
        founding = ContextualRoleAssignment.objects.get(assignment_uuid=ids["founding_assignment_uuid"])
        self.assertEqual(founding.state, "ACTIVE")

    # R11 — replay returns the first result; mismatch refused.
    def test_replay_returns_first_result(self):
        ids, founder = self._setup_organism("replay")
        _, member, member_membership_uuid = self._add_active_member(ids, "replay")
        service = self._service(_ExactProvider())
        propose_command = self._propose_command(
            actor=founder,
            membership_uuid=member_membership_uuid,
            role_definition_uuid=ids["coordinator_role_uuid"],
            request_reference="request:replay:propose",
            idempotency_key="idem:replay:propose",
        )
        first = service.propose_assignment(propose_command)
        second = service.propose_assignment(propose_command)
        self.assertIsInstance(first, AssignmentCommandResult)
        self.assertEqual(first, second)
        self.assertEqual(ContextualRoleAssignment.objects.count(), 2)
        self.assertEqual(OrganismCommandReceipt.objects.count(), 1)
        assign_command = self._transition_command(
            actor=founder,
            assignment_uuid=first.assignment_uuid,
            action="ASSIGN",
            resulting_state="ACTIVE",
            effective_until_state="NONE",
            request_reference="request:replay:assign",
            idempotency_key="idem:replay:assign",
        )
        first_assign = service.transition_assignment(assign_command)
        second_assign = service.transition_assignment(assign_command)
        self.assertEqual(first_assign, second_assign)
        self.assertEqual(OrganismCommandReceipt.objects.count(), 2)
        self.assertEqual(
            ContextualRoleAssignmentStateEvent.objects.filter(assignment_id=first.assignment_id).count(),
            2,
        )

    # R12 — authority denied, unavailable or malformed: no row, no receipt.
    def test_authority_failures_refuse_without_write(self):
        ids, founder = self._setup_organism("authority")
        _, member, member_membership_uuid = self._add_active_member(ids, "authority")
        propose_command = self._propose_command(
            actor=founder,
            membership_uuid=member_membership_uuid,
            role_definition_uuid=ids["coordinator_role_uuid"],
            request_reference="request:authority:propose",
            idempotency_key="idem:authority:propose",
        )
        failures = [
            (_DeniedProvider(), S015AuthorityDenied),
            (_UnavailableProvider(), S015AuthorityUnavailable),
            (_MalformedProvider(), S015AuthorityMalformed),
        ]
        before = self._counts()
        for provider, exc_type in failures:
            with self.subTest(exc_type=exc_type.__name__):
                with self.assertRaises(exc_type):
                    self._service(provider).propose_assignment(propose_command)
        self.assertEqual(self._counts(), before)

        service = self._service(_ExactProvider())
        propose_result = service.propose_assignment(propose_command)
        transition_command = self._transition_command(
            actor=founder,
            assignment_uuid=propose_result.assignment_uuid,
            action="ASSIGN",
            resulting_state="ACTIVE",
            effective_until_state="NONE",
            request_reference="request:authority:assign",
            idempotency_key="idem:authority:assign",
        )
        mid = self._counts()
        for provider, exc_type in failures:
            with self.subTest(exc_type=f"transition:{exc_type.__name__}"):
                with self.assertRaises(exc_type):
                    self._service(provider).transition_assignment(transition_command)
        self.assertEqual(self._counts(), mid)

    # R13 — atomic failure leaves nothing; two sessions, one row.
    def test_atomic_failure_leaves_nothing(self):
        ids, founder = self._setup_organism("atomic-failure")
        _, member, member_membership_uuid = self._add_active_member(ids, "atomic-failure")
        service = self._service(_ExactProvider())
        before = self._counts()
        with self.assertRaises(IntegrityError):
            service.propose_assignment(
                self._propose_command(
                    actor=founder,
                    membership_uuid=member_membership_uuid,
                    role_definition_uuid=ids["coordinator_role_uuid"],
                    request_reference="request:atomic-failure:propose",
                    idempotency_key="idem:atomic-failure:propose",
                    effective_at=NOW,
                    received_at=FAR_FUTURE,
                )
            )
        self.assertEqual(self._counts(), before)

    def test_concurrent_same_request_replays_once(self):
        ids, founder = self._setup_organism("concurrency")
        _, member, member_membership_uuid = self._add_active_member(ids, "concurrency")
        command_kwargs = dict(
            membership_uuid=member_membership_uuid,
            role_definition_uuid=ids["coordinator_role_uuid"],
            request_reference="request:concurrency:propose",
            idempotency_key="idem:concurrency:propose",
        )
        gate = Barrier(2)
        results: Queue[tuple[str, object]] = Queue()

        def worker():
            close_old_connections()
            worker_actor = Identity.objects.get(pk=founder.pk)
            service = self._service(_ExactProvider())
            try:
                gate.wait(timeout=10)
                result = service.propose_assignment(
                    self._propose_command(actor=worker_actor, **command_kwargs)
                )
                results.put(("ok", result))
            except Exception as exc:  # pragma: no cover - thread assertion channel
                results.put(("err", exc))
            finally:
                connection.close()

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
        self.assertEqual(ContextualRoleAssignment.objects.count(), 2)
        self.assertEqual(ContextualRoleAssignmentStateEvent.objects.count(), 2)
        self.assertEqual(OrganismCommandReceipt.objects.count(), 1)

    # R14 — cache equals the fold after each command.
    def test_cache_equals_fold_after_each_command(self):
        ids, founder = self._setup_organism("cache")
        _, member, member_membership_uuid = self._add_active_member(ids, "cache")
        service = self._service(_ExactProvider())
        propose_result = service.propose_assignment(
            self._propose_command(
                actor=founder,
                membership_uuid=member_membership_uuid,
                role_definition_uuid=ids["coordinator_role_uuid"],
                request_reference="request:cache:propose",
                idempotency_key="idem:cache:propose",
            )
        )

        def fold_check(assignment_pk, expected_state, expected_count):
            assignment = ContextualRoleAssignment.objects.get(pk=assignment_pk)
            with connection.cursor() as cur:
                cur.execute(
                    "SELECT projection, eligible_count, served_from, fingerprint FROM public.s015_effective_state('core_contextualroleassignment', %s, %s, %s)",
                    (assignment.pk, assignment.current_state_effective_at, assignment.state_known_at),
                )
                projection, eligible_count, served_from, fingerprint = cur.fetchone()
            self.assertEqual(projection, expected_state)
            self.assertEqual(projection, assignment.state)
            self.assertEqual(eligible_count, expected_count)
            self.assertEqual(served_from, "CACHE_REQUALIFIED")
            self.assertEqual(fingerprint, assignment.state_source_event_set_fingerprint)

        fold_check(propose_result.assignment_id, "PROPOSED", 1)
        assign_result = service.transition_assignment(
            self._transition_command(
                actor=founder,
                assignment_uuid=propose_result.assignment_uuid,
                action="ASSIGN",
                resulting_state="ACTIVE",
                effective_until_state="NONE",
                request_reference="request:cache:assign",
                idempotency_key="idem:cache:assign",
            )
        )
        fold_check(assign_result.assignment_id, "ACTIVE", 2)

    # R15 — assignment_in_effect: in effect before effective_until, not at or after it; the recorded
    # state stays ACTIVE; a NONE term stays in effect.
    def test_assignment_in_effect_reads_the_term(self):
        until = NOW + timedelta(hours=2)
        ids, founder, member, service, propose_result, assign_result = self._propose_then_assign(
            label="in-effect", effective_until_state="FIXED", effective_until=until,
        )
        assignment = ContextualRoleAssignment.objects.get(pk=assign_result.assignment_id)
        self.assertTrue(service.assignment_in_effect(assignment, NOW))
        self.assertFalse(service.assignment_in_effect(assignment, until))
        self.assertFalse(service.assignment_in_effect(assignment, until + timedelta(hours=1)))
        assignment.refresh_from_db()
        self.assertEqual(assignment.state, "ACTIVE")

        ids2, founder2, member2, service2, propose2, assign2 = self._propose_then_assign(label="in-effect-open")
        open_assignment = ContextualRoleAssignment.objects.get(pk=assign2.assignment_id)
        self.assertTrue(service2.assignment_in_effect(open_assignment, NOW + timedelta(days=365)))

    # R17 — a denied authority asking for an undecided edge or a limit gets the authority error,
    # not the edge or limit error, and no write (the founder half is R10c).
    def test_denied_authority_preempts_edge_and_limit_refusals(self):
        ids, founder, member, service, propose_result, assign_result = self._propose_then_assign(label="order")
        denied = self._service(_DeniedProvider())
        with transaction.atomic():
            with connection.cursor() as cur:
                _, archivist_uuid = _add_role_definition(cur, ids, "order", "TEST_ONLY_ARCHIVIST")
        member_membership_uuid = OrganismMembership.objects.get(identity=member, living_organism_id=ids["lo"]).membership_uuid
        before = self._counts()
        # an undecided edge
        with self.assertRaises(S015AuthorityDenied):
            denied.transition_assignment(
                self._transition_command(
                    actor=founder, assignment_uuid=assign_result.assignment_uuid, action="END", resulting_state="PROPOSED",
                    request_reference="request:order:edge", idempotency_key="idem:order:edge",
                )
            )
        # a limit (IL-4 suspension)
        with self.assertRaises(S015AuthorityDenied):
            denied.transition_assignment(
                self._transition_command(
                    actor=founder, assignment_uuid=assign_result.assignment_uuid, action="SUSPEND", resulting_state="SUSPENDED",
                    request_reference="request:order:suspend", idempotency_key="idem:order:suspend",
                )
            )
        # a limit on propose (IL-8, an organism-defined office other than COORDINATOR)
        with self.assertRaises(S015AuthorityDenied):
            denied.propose_assignment(
                self._propose_command(
                    actor=founder,
                    membership_uuid=member_membership_uuid,
                    role_definition_uuid=archivist_uuid,
                    request_reference="request:order:il8",
                    idempotency_key="idem:order:il8",
                )
            )
        self.assertEqual(self._counts(), before)

    # R18 — a role definition other than COORDINATOR is refused with IL-8 (reserved and
    # organism-defined offices alike). No row.
    def test_non_coordinator_role_refused_il8(self):
        ids, founder = self._setup_organism("reserved")
        _, member, member_membership_uuid = self._add_active_member(ids, "reserved")
        with transaction.atomic():
            with connection.cursor() as cur:
                _, archivist_uuid = _add_role_definition(cur, ids, "reserved", "TEST_ONLY_ARCHIVIST")
        service = self._service(_ExactProvider())
        before = self._counts()
        with self.assertRaisesRegex(S015OperationNotYetImplemented, r"IL-8, SR"):
            service.propose_assignment(
                self._propose_command(
                    actor=founder,
                    membership_uuid=member_membership_uuid,
                    role_definition_uuid=archivist_uuid,
                    request_reference="request:reserved:propose",
                    idempotency_key="idem:reserved:propose",
                )
            )
        self.assertEqual(self._counts(), before)

    # R18b — IL-8 binds transitions too: an ACTIVE office on an organism-defined, non-COORDINATOR
    # role definition (a test-only fixture) cannot be ended through S2; no row (the Reviewer's S2 F2).
    def test_non_coordinator_office_transition_refused_il8(self):
        ids, founder = self._setup_organism("otheroffice")
        membership_id, member, _ = self._add_active_member(ids, "otheroffice")
        with transaction.atomic():
            with connection.cursor() as cur:
                archivist_id, _ = _add_role_definition(cur, ids, "otheroffice", "TEST_ONLY_ARCHIVIST")
                _, office_uuid = _assignment_at_state(
                    cur, ids, membership_id, archivist_id, "otheroffice:fixture", "ACTIVE",
                )
        service = self._service(_ExactProvider())
        before = self._counts()
        with self.assertRaisesRegex(S015OperationNotYetImplemented, r"IL-8, SR"):
            service.transition_assignment(
                self._transition_command(
                    actor=founder, assignment_uuid=office_uuid, action="END", resulting_state="ENDED",
                    request_reference="request:otheroffice:end", idempotency_key="idem:otheroffice:end",
                )
            )
        self.assertEqual(self._counts(), before)

    # R19 — a COORDINATOR proposal on a non-current membership is refused as design policy
    # (⟨E2.114⟩: PROBATIONARY holds no COORDINATOR office; ⟨C.037⟩: only ACTIVE is current). No row.
    def test_non_current_membership_holds_no_coordinator_office(self):
        ids, founder = self._setup_organism("probation")
        service = self._service(_ExactProvider())
        for state in ("PROBATIONARY", "PROPOSED", "ENDED"):
            with self.subTest(state=state):
                with transaction.atomic():
                    with connection.cursor() as cur:
                        _, _, member_membership_uuid = _add_member(cur, ids, f"probation-{state.lower()}", state=state)
                before = self._counts()
                with self.assertRaises(S015AssignmentMembershipRefused):
                    service.propose_assignment(
                        self._propose_command(
                            actor=founder,
                            membership_uuid=member_membership_uuid,
                            role_definition_uuid=ids["coordinator_role_uuid"],
                            request_reference=f"request:probation:{state.lower()}",
                            idempotency_key=f"idem:probation:{state.lower()}",
                        )
                    )
                self.assertEqual(self._counts(), before)
