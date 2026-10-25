"""Exact-text witnesses for the H100 durable request-intake boundary."""

from __future__ import annotations

import ast
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import User
from django.db import IntegrityError, OperationalError, connection, transaction
from django.db.models import F
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext

from core.models import (
    AuthorityDecision,
    CommandIntake,
    ContextualRoleAssignment,
    ContextualRoleAssignmentStateEvent,
    Identity,
    IntakeEntry,
    OrganismCommandReceipt,
    OrganismMembership,
    OrganismMembershipTransition,
)
from core.organism_contract import OrganismAction
from src.intevia.services import command_intake
from src.intevia.services.command_intake import (
    ACTIVE_ENTRIES,
    ENTRY_ACTIONS,
    ActorRef,
    IntakeUnexpectedHistory,
    S015CommandAtomicityRequired,
    _bound_from_row,
    _request_binding_mismatch,
    _resolve_actor,
    admit_request,
    intake_shape,
)
from src.intevia.services.organism_application_service import (
    OrganismApplicationService,
    SubmitApplicationCommand,
    _governance_enabling_refusal_message,
)
from src.intevia.services.organism_authority import (
    S015Authority,
    S015AuthorityDenied,
    S015AuthorityRefusalCode,
    S015PostFoundingAuthorityRefusal,
    canonical_timestamp,
)
from src.intevia.services.organism_command_support import (
    S015CommandContended,
    S015CommandReceiptMalformed,
    S015CommandReplayMismatch,
    WithheldField,
)
from src.intevia.services.organism_formation_service import (
    OrganismBirthingService,
    OrganismPhaseAdvanceService,
    _sbg_readiness_message,
)
from src.intevia.services.organism_membership_service import (
    MembershipCommandResult,
    OpenMembershipCommand,
    OrganismMembershipService,
    S015MembershipAuthorityUnavailable,
)
from src.intevia.services import organism_membership_service as membership_service_module
from src.intevia.services.organism_role_service import (
    OrganismRoleService,
    ProposeAssignmentCommand,
    TransitionAssignmentCommand,
)
from src.intevia.services.organism_command_support import command_mutex_key
from tests.s015_0021_support import create_test_identity, seed_identity
from tests.s015_concurrency_support import (
    backend_pid,
    cleanup_workers,
    start_worker,
    wait_until_locked,
)
from tests.sb2_route_support import (
    add_active_elector,
    birth,
    prepare_route,
    route_fixture_state,
    route_test_now,
)


UTC = timezone.utc
NOW = datetime(2026, 10, 23, 12, tzinfo=UTC)


class _DeniedOpeningProvider:
    def evaluate_organism_authority(self, request):
        return S015PostFoundingAuthorityRefusal(
            **asdict(request),
            refusal_code=S015AuthorityRefusalCode.DENIED,
        )


class _FounderProtectedProvider:
    def evaluate_organism_authority(self, request):
        return S015PostFoundingAuthorityRefusal(
            **asdict(request),
            refusal_code=S015AuthorityRefusalCode.C_FOUNDER_PROTECTED,
        )


@skipUnless(connection.vendor == "postgresql", "H100 intake witnesses require PostgreSQL")
class H100IntakeTextTests(TransactionTestCase):
    reset_sequences = True

    @staticmethod
    def _public_row_counts():
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT c.relname FROM pg_catalog.pg_class c "
                "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') ORDER BY c.relname"
            )
            tables = [row[0] for row in cursor.fetchall()]
            counts = {}
            for table in tables:
                cursor.execute(
                    f"SELECT count(*) FROM public.{connection.ops.quote_name(table)}"
                )
                counts[table] = cursor.fetchone()[0]
        return counts

    @staticmethod
    def _changed_public_rows(before, after):
        return {
            table: after[table] - count
            for table, count in before.items()
            if after[table] != count
        }

    def _membership_open_case(self, label):
        from tests.test_s015_role_command import _ExactProvider

        tag = f"h100-{label}-{uuid4().hex}"
        route = prepare_route(tag=tag)
        birth(route)
        ids, _gateway = route_fixture_state(route)
        with transaction.atomic(), connection.cursor() as cursor:
            target_pk = seed_identity(cursor, f"{tag}-target")
        target = Identity.objects.get(pk=target_pk)
        now = route_test_now(route)
        key = f"h100:{label}:{tag}"
        command = OpenMembershipCommand(
            credential=route.founder.credential,
            request_reference=f"request:{key}",
            idempotency_key=key,
            occurred_at=now,
            effective_at=now,
            living_organism_id=ids["organism_uuid"],
            member_identity_id=target.identity_id,
            reason_class=f"TEST_ONLY_REASON_{label.replace('-', '_').upper()}",
        )
        service = OrganismMembershipService(
            authority=S015Authority(provider=_ExactProvider(), database_alias="default"),
            clock=lambda: now,
            database_alias="default",
        )
        return route, target, now, key, command, service

    def test_nw1_b01_all_active_entry_reasons_are_pre_sql_and_untouched(self):
        initial_binding_count = CommandIntake.objects.count()
        for entry in sorted(ACTIVE_ENTRIES, key=lambda value: value.value):
            action = ENTRY_ACTIONS[entry] or entry.value
            reasons = (
                (
                    "atomic",
                    "an atomic block is open on this connection",
                    lambda: connection.in_atomic_block,
                    "atomic",
                ),
                (
                    "manual",
                    "a manual transaction is open on this connection",
                    lambda: not connection.get_autocommit(),
                    "manual",
                ),
                (
                    "rollback",
                    "the connection has a pending rollback",
                    lambda: connection.needs_rollback,
                    "rollback",
                ),
            )
            for label, reason, state_check, state in reasons:
                with self.subTest(entry=entry, reason=label):
                    expected = (
                        f"{action} requires a top-level transaction and was not started: "
                        f"{reason}. Nothing was saved; the caller's transaction is untouched."
                    )
                    if state == "atomic":
                        with transaction.atomic():
                            before = (
                                connection.in_atomic_block,
                                connection.get_autocommit(),
                                connection.needs_rollback,
                            )
                            with CaptureQueriesContext(connection) as queries:
                                with self.assertRaises(S015CommandAtomicityRequired) as raised_message:
                                    command_intake._require_admission_state("default", entry)
                                self.assertEqual(
                                    str(raised_message.exception),
                                    expected,
                                )
                            self.assertEqual(queries.captured_queries, [])
                            self.assertTrue(state_check())
                            self.assertEqual(
                                (
                                    connection.in_atomic_block,
                                    connection.get_autocommit(),
                                    connection.needs_rollback,
                                ),
                                before,
                            )
                    elif state == "manual":
                        connection.set_autocommit(False)
                        try:
                            before = (
                                connection.in_atomic_block,
                                connection.get_autocommit(),
                                connection.needs_rollback,
                            )
                            with CaptureQueriesContext(connection) as queries:
                                with self.assertRaises(S015CommandAtomicityRequired) as raised_message:
                                    command_intake._require_admission_state("default", entry)
                                self.assertEqual(
                                    str(raised_message.exception),
                                    expected,
                                )
                            self.assertEqual(queries.captured_queries, [])
                            self.assertTrue(state_check())
                            self.assertEqual(
                                (
                                    connection.in_atomic_block,
                                    connection.get_autocommit(),
                                    connection.needs_rollback,
                                ),
                                before,
                            )
                        finally:
                            connection.rollback()
                            connection.set_autocommit(True)
                    else:
                        before = (
                            connection.in_atomic_block,
                            connection.get_autocommit(),
                            connection.needs_rollback,
                        )
                        with patch.object(connection, "needs_rollback", True):
                            with CaptureQueriesContext(connection) as queries:
                                with self.assertRaises(S015CommandAtomicityRequired) as raised_message:
                                    command_intake._require_admission_state("default", entry)
                                self.assertEqual(
                                    str(raised_message.exception),
                                    expected,
                                )
                            self.assertEqual(queries.captured_queries, [])
                            self.assertTrue(state_check())
                        self.assertEqual(
                            (
                                connection.in_atomic_block,
                                connection.get_autocommit(),
                                connection.needs_rollback,
                            ),
                            before,
                        )
        self.assertEqual(CommandIntake.objects.count(), initial_binding_count)

    def test_nw1_b02_nondefault_writer_alias_refuses_without_sql_or_state_change(self):
        before = (
            connection.in_atomic_block,
            connection.get_autocommit(),
            connection.needs_rollback,
        )
        binding_count = CommandIntake.objects.count()
        with patch.object(command_intake, "connections", {"replica": connection}):
            with CaptureQueriesContext(connection) as queries:
                with self.assertRaises(command_intake.IntakeAdmissionTechnicalFailure) as raised_message:
                    command_intake._require_admission_state(
                        "replica", IntakeEntry.SAVE_GATEWAY_DRAFT
                    )
                self.assertEqual(
                    str(raised_message.exception),
                    "The request could not be admitted: the intake must be admitted on the "
                    "writer database. Nothing was saved.",
                )
        self.assertEqual(queries.captured_queries, [])
        self.assertEqual(
            (
                connection.in_atomic_block,
                connection.get_autocommit(),
                connection.needs_rollback,
            ),
            before,
        )
        self.assertEqual(CommandIntake.objects.count(), binding_count)

    def test_nw1_b03_client_received_at_is_rejected_before_binding(self):
        key = f"h100:nw1:received-at:{uuid4()}"
        command = SubmitApplicationCommand(
            credential=User(username="h100-nw1-unpersisted"),
            living_organism_id=uuid4(),
            request_reference=f"request:{key}",
            idempotency_key=key,
            occurred_at=NOW,
            effective_at=NOW,
            received_at=NOW,
        )
        service = OrganismApplicationService(clock=lambda: NOW)
        before = CommandIntake.objects.filter(idempotency_key=key).count()
        with CaptureQueriesContext(connection) as queries:
            with self.assertRaises(ValueError) as raised_message:
                service.submit_application(command)
            self.assertEqual(
                str(raised_message.exception),
                "received_at is assigned by INTEVIA and must not be supplied.",
            )
        self.assertEqual(queries.captured_queries, [])
        self.assertEqual(CommandIntake.objects.filter(idempotency_key=key).count(), before)

    def test_nw1_b04_b05_replay_mismatch_texts_are_full(self):
        actor = ActorRef(pk=71, identity_id=uuid4(), access_epoch=3)
        target = uuid4()
        shape = command_intake.IntakeShape(
            idempotency_key="h100:nw1:replay",
            request_reference="request:h100:nw1:replay",
            target={"organism": str(target)},
            command={"phase": "advance"},
            omitted=(),
            target_organism_id=target,
        )
        mismatch_row = (
            13,
            IntakeEntry.ADVANCE_PHASE.value,
            "request:some-other-request",
            "stored-fingerprint",
            command_intake.FINGERPRINT_SCHEMA,
            NOW,
            target,
        )
        with self.assertRaises(S015CommandReplayMismatch) as raised_message:
            _bound_from_row(
                mismatch_row,
                entry=IntakeEntry.ADVANCE_PHASE,
                action=OrganismAction.TRANSITION_LIVING_ORGANISM.value,
                actor=actor,
                shape=shape,
                fingerprint="new-fingerprint",
                first=False,
            )
        self.assertEqual(
            str(raised_message.exception),
            "A different request already used this request key; nothing was done. No record "
            "of this request was kept and it inherits nothing from the earlier one. Send it "
            "under a new request key.",
        )

        earlier_format_row = (
            14,
            IntakeEntry.ADVANCE_PHASE.value,
            shape.request_reference,
            "stored-fingerprint",
            "s015-intake-before-v1",
            NOW,
            target,
        )
        with self.assertRaises(S015CommandReplayMismatch) as raised_message:
            _bound_from_row(
                earlier_format_row,
                entry=IntakeEntry.ADVANCE_PHASE,
                action=OrganismAction.TRANSITION_LIVING_ORGANISM.value,
                actor=actor,
                shape=shape,
                fingerprint="new-fingerprint",
                first=False,
            )
        self.assertEqual(
            str(raised_message.exception),
            "This request key was received under an earlier request format. This attempt made "
            "no changes. The earlier request's outcome must be checked before it is sent under "
            "a different key. Report it.",
        )

    def test_nw1_b06_diagnostic_attributes_and_text_are_preserved(self):
        refusal = IntakeUnexpectedHistory(
            unaccounted_kind="decision",
            unaccounted_id=83,
            unaccounted_action=OrganismAction.TRANSITION_MEMBERSHIP.value,
            unaccounted_idempotency_key="h100:nw1:unaccounted",
        )
        self.assertEqual(
            str(refusal),
            "A record for this request key exists without a corresponding intake binding; "
            "this attempt made no changes and kept no new intake record. Report it.",
        )
        self.assertEqual(refusal.unaccounted_kind, "decision")
        self.assertEqual(refusal.unaccounted_id, 83)
        self.assertEqual(
            refusal.unaccounted_action,
            OrganismAction.TRANSITION_MEMBERSHIP.value,
        )
        self.assertEqual(refusal.unaccounted_idempotency_key, "h100:nw1:unaccounted")

    def test_nw1_b06_selected_unaccounted_record_supplies_the_diagnostics(self):
        """Helper-level: _refuse_unexpected_history raises B-06 with the selected record's fields."""
        actor = ActorRef(pk=71, identity_id=uuid4(), access_epoch=3)
        target = uuid4()
        shape = command_intake.IntakeShape(
            idempotency_key="h100:nw1:b06:incoming",
            request_reference="request:h100:nw1:b06",
            target={"organism": str(target)},
            command={"phase": "advance"},
            omitted=(),
            target_organism_id=target,
        )
        record = command_intake.IntakeRecord(
            id=9301,
            actor_id=71,
            action="H100_NW1_B06_RECORDED_ACTION",
            idempotency_key="h100:nw1:b06:recorded",
            kind="H100_NW1_B06_KIND",
            evaluated_at=NOW,
        )
        binding_count = CommandIntake.objects.count()
        with patch.object(command_intake, "_history_records", return_value=[record]):
            with patch.object(command_intake, "_candidate_bindings", return_value=[]):
                with patch.object(command_intake, "_existing_row", return_value=None):
                    with self.assertRaises(command_intake.IntakeUnexpectedHistory) as refused:
                        command_intake._refuse_unexpected_history(
                            "default",
                            actor,
                            IntakeEntry.ADVANCE_PHASE,
                            OrganismAction.TRANSITION_LIVING_ORGANISM.value,
                            shape,
                        )
        self.assertEqual(
            str(refused.exception),
            "A record for this request key exists without a corresponding intake binding; "
            "this attempt made no changes and kept no new intake record. Report it.",
        )
        self.assertEqual(refused.exception.unaccounted_kind, "H100_NW1_B06_KIND")
        self.assertEqual(refused.exception.unaccounted_id, 9301)
        self.assertEqual(refused.exception.unaccounted_action, "H100_NW1_B06_RECORDED_ACTION")
        self.assertEqual(refused.exception.unaccounted_idempotency_key, "h100:nw1:b06:recorded")
        self.assertEqual(CommandIntake.objects.count(), binding_count)

    def test_nw1_b07_binding_mismatch_text_is_full(self):
        self.assertEqual(
            str(command_intake._request_binding_mismatch()),
            "The request being executed is not the request that was admitted; nothing was done.",
        )

    def test_nw1_b08_access_epoch_refusal_uses_the_committed_binding_time(self):
        identity = create_test_identity(
            username=f"h100-nw1-b08-{uuid4().hex}",
            access_state=Identity.AccessState.ACTIVE,
        )
        key = f"h100:nw1:b08:{uuid4()}"
        command = OpenMembershipCommand(
            credential=identity.credential,
            request_reference=f"request:{key}",
            idempotency_key=key,
            occurred_at=NOW,
            effective_at=NOW,
            living_organism_id=uuid4(),
            member_identity_id=uuid4(),
            reason_class="TEST_ONLY_REASON_H100",
        )
        entry = IntakeEntry.OPEN_MEMBERSHIP
        actor_ref = _resolve_actor("default", command.credential)
        self.assertEqual(
            CommandIntake.objects.filter(
                actor_id=identity.pk,
                action=OrganismAction.TRANSITION_MEMBERSHIP.value,
                idempotency_key=key,
            ).count(),
            0,
        )
        bound = admit_request("default", entry, actor_ref, intake_shape(entry, command))
        self.assertIsNotNone(bound.intake_id)
        self.assertEqual(
            CommandIntake.objects.filter(
                actor_id=identity.pk,
                action=OrganismAction.TRANSITION_MEMBERSHIP.value,
                idempotency_key=key,
            ).count(),
            1,
        )
        Identity.objects.filter(pk=identity.pk).update(access_epoch=F("access_epoch") + 1)
        identity.refresh_from_db()
        with CaptureQueriesContext(connection) as queries:
            with self.assertRaises(S015MembershipAuthorityUnavailable) as refused:
                bound.verify(command, identity, entry, "default")
        self.assertEqual(queries.captured_queries, [])
        self.assertEqual(
            str(refused.exception),
            "Your account's security settings changed while this request was being processed; "
            "no governed record was written. INTEVIA kept a record that this request was "
            f"received at {canonical_timestamp(bound.received_at)}. Sign in again and send a "
            "new request.",
        )
        self.assertEqual(
            CommandIntake.objects.filter(
                actor_id=identity.pk,
                action=OrganismAction.TRANSITION_MEMBERSHIP.value,
                idempotency_key=key,
            ).count(),
            1,
        )

    def test_nw5_open4_helper_level_field_and_eligibility_texts_both_forms(self):
        """NW-5 witnesses EP-16 at helper level, not through its guarded public route."""
        organism = object()
        effective_at = NOW
        recorded_at = NOW
        field_condition = "unsupported officer role 'steward'"
        seat_id = "EP16-SEAT"
        conditions = {
            S015AuthorityRefusalCode.SBG_ARRANGEMENT_FIELD_UNSUPPORTED: field_condition,
            S015AuthorityRefusalCode.SBG_ELIGIBILITY_UNSUPPORTED: seat_id,
        }
        forms = (
            (
                "class A",
                ".",
                f"INTEVIA recorded this refusal and that the request was received at "
                f"{canonical_timestamp(NOW)}; nothing else was saved.",
            ),
            (
                "class B",
                ", and nothing from this request was saved except a record that it was received "
                f"at {canonical_timestamp(NOW)}. Something changed for this LO after the request "
                "was received (for example, its Birthing completed), and GOVERNANCE is not ready "
                "to be switched on yet.",
                "Sending this same request again is still judged as of when it was first "
                "received, before the change.",
            ),
        )

        def arrangement_condition(*, code, **_kwargs):
            return conditions[code]

        with patch(
            "src.intevia.services.organism_formation_service._sbg_arrangement_condition",
            side_effect=arrangement_condition,
        ) as condition_reader:
            for code, condition in conditions.items():
                for label, opening_end, tail in forms:
                    with self.subTest(code=code, form=label):
                        if code is S015AuthorityRefusalCode.SBG_ARRANGEMENT_FIELD_UNSUPPORTED:
                            body = (
                                f"The arrangement includes {condition}, which this release "
                                "cannot yet act on. Adopt a version that leaves it out for now, "
                                "then make a new request to switch GOVERNANCE on; it can be "
                                "added by a later version once the release that acts on it is "
                                "delivered."
                            )
                        else:
                            body = (
                                f"Seat {condition} names an eligibility rule this release "
                                "cannot check; it can check only \"an active member of the LO\". "
                                "Adopt a version that uses that rule for now, then make a new "
                                "request to switch GOVERNANCE on; other eligibility rules arrive "
                                "with the slices that can check them."
                            )
                        expected = (
                            f"GOVERNANCE was not switched on{opening_end} {body} {tail}"
                        )
                        actual = _governance_enabling_refusal_message(
                            database_alias="default",
                            organism=organism,
                            effective_at=effective_at,
                            recorded_at=recorded_at,
                            code=code,
                            opening_end=opening_end,
                            tail=tail,
                        )
                        self.assertEqual(actual, expected)
        self.assertEqual(condition_reader.call_count, 4)

    def test_a3_guard_matchers_match_only_their_state_and_diagnostic(self):
        """A3's two matchers translate only their own SQLSTATE and diagnostic, on the same cause."""
        from types import SimpleNamespace

        from src.intevia.services.organism_entitlement_service import _is_constraint_violation
        from src.intevia.services.organism_formation_service import (
            _SBG_BIRTHING_GUARD_CODES,
            _SBG_PHASE_GUARD_CODES,
            _sbg_guard_refusal_code,
        )

        def chained(*causes):
            error = IntegrityError("guard")
            current = error
            for cause in causes:
                current.__cause__ = cause
                current = cause
            return error

        def database_error(*, sqlstate=None, pgcode=None, primary=None, constraint=None):
            error = Exception("database")
            error.sqlstate = sqlstate
            error.pgcode = pgcode
            error.diag = SimpleNamespace(message_primary=primary, constraint_name=constraint)
            return error

        circle = S015AuthorityRefusalCode.SBG_GOVERNANCE_CIRCLE_MISSING
        participation = S015AuthorityRefusalCode.SBG_NO_INITIAL_PARTICIPATION_ROUTE
        snapshot = "core_lobirthingsnapshot_living_organism_id_uniq"
        self.assertIs(
            _sbg_guard_refusal_code(
                chained(database_error(sqlstate="23000", primary=circle.value)),
                _SBG_PHASE_GUARD_CODES,
            ),
            circle,
        )
        self.assertIs(
            _sbg_guard_refusal_code(
                chained(Exception("wrapper"), database_error(pgcode="23000", primary=circle.value)),
                _SBG_BIRTHING_GUARD_CODES,
            ),
            circle,
        )
        for error, codes in (
            (chained(database_error(sqlstate="23505", primary=circle.value)), _SBG_BIRTHING_GUARD_CODES),
            (chained(database_error(sqlstate="23000", primary="SB_BIRTHING_ELECTORATE_EMPTY")), _SBG_BIRTHING_GUARD_CODES),
            (chained(database_error(sqlstate="23000", primary=participation.value)), _SBG_PHASE_GUARD_CODES),
            (chained(database_error(sqlstate="23000"), database_error(primary=circle.value)), _SBG_BIRTHING_GUARD_CODES),
            (IntegrityError(circle.value), _SBG_BIRTHING_GUARD_CODES),
        ):
            with self.subTest(error=repr(error.__cause__)):
                self.assertIsNone(_sbg_guard_refusal_code(error, codes))
        self.assertTrue(
            _is_constraint_violation(
                chained(database_error(sqlstate="23505", constraint=snapshot)),
                sqlstate="23505",
                constraint_name=snapshot,
            )
        )
        for error in (
            chained(database_error(sqlstate="23000", constraint=snapshot)),
            chained(database_error(sqlstate="23505", constraint="core_lobirthingsnapshot_snapshot_uuid_key")),
            chained(database_error(sqlstate="23505"), database_error(constraint=snapshot)),
            IntegrityError(snapshot),
        ):
            with self.subTest(error=repr(error.__cause__)):
                self.assertFalse(
                    _is_constraint_violation(error, sqlstate="23505", constraint_name=snapshot)
                )

    def test_nw7_default_readiness_forms_hold_and_formation_callers_render_the_restated_form(self):
        """The SB-G texts render the adopted per-operation forms through every caller.

        The direct builder, given an operation (Birthing, phase advance or switching GOVERNANCE
        on), renders the default form with that operation's corrective step (adopted 25 Oct
        2026). The formation callers render the restated class A form (the opening ends at "."
        and Stop 3's tail names the receipt time) and, with rolled_back, the class B form of
        amendment A3; the application caller renders the direct form unchanged.
        """
        organism = object()
        effective_at = NOW
        recorded_at = NOW
        conditions = {
            S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE: "no deciding body",
            S015AuthorityRefusalCode.SBG_ARRANGEMENT_FIELD_UNSUPPORTED: "unsupported field",
            S015AuthorityRefusalCode.SBG_ELIGIBILITY_UNSUPPORTED: "EP16-SEAT",
        }
        codes = (
            S015AuthorityRefusalCode.SBG_GOVERNANCE_CIRCLE_MISSING,
            S015AuthorityRefusalCode.SBG_ARRANGEMENT_MISSING,
            S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
            S015AuthorityRefusalCode.SBG_ARRANGEMENT_FIELD_UNSUPPORTED,
            S015AuthorityRefusalCode.SBG_ELIGIBILITY_UNSUPPORTED,
            S015AuthorityRefusalCode.SBG_NO_INITIAL_PARTICIPATION_ROUTE,
        )
        acts = {
            "The LO was not born": "BIRTHING",
            "The phase was not advanced": "PHASE_ADVANCE",
            "GOVERNANCE was not switched on": "ENABLING",
        }
        new_requests = {
            "BIRTHING": "make a new Birthing request",
            "PHASE_ADVANCE": "make a new request to advance the phase",
            "ENABLING": "make a new request to switch GOVERNANCE on",
        }

        def condition_reader(*, code, **_kwargs):
            return conditions[code]

        def expected_default(code, act):
            # The adopted wording of 25 Oct 2026 (A3): each corrective step names a new request.
            new_request = new_requests[acts[act]]
            if code is S015AuthorityRefusalCode.SBG_GOVERNANCE_CIRCLE_MISSING:
                reason = (
                    "Switching GOVERNANCE on needs the LO's GOVERNANCE Circle first."
                    if acts[act] == "ENABLING"
                    else "GOVERNANCE is on for this LO, so it needs its GOVERNANCE Circle first."
                )
                return (
                    f"{act} and nothing changed. {reason} Create the LO's GOVERNANCE Circle, then "
                    f"{new_request}. Creating the Circle early in formation avoids this."
                )
            if code is S015AuthorityRefusalCode.SBG_ARRANGEMENT_MISSING:
                return (
                    f"{act} and nothing changed. GOVERNANCE needs an adopted arrangement saying "
                    "which seats exist, how each is filled and who decides, and this LO has none in "
                    f"effect. Adopt an arrangement, then {new_request}. Adopting it before creating "
                    "the Circle avoids this."
                )
            if code is S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE:
                return (
                    f"{act} and nothing changed. The arrangement is incomplete: "
                    f"{conditions[code]}. Adopt a corrected version, then {new_request}. Every "
                    "seat needs an admission route and a deciding body that matches it."
                )
            if code is S015AuthorityRefusalCode.SBG_ARRANGEMENT_FIELD_UNSUPPORTED:
                return (
                    f"{act} and nothing changed. The arrangement includes "
                    f"{conditions[code]}, which this release cannot yet act on. Adopt a version "
                    f"that leaves it out for now, then {new_request}; it can be added by a later "
                    "version once the release that acts on it is delivered."
                )
            if code is S015AuthorityRefusalCode.SBG_ELIGIBILITY_UNSUPPORTED:
                return (
                    f"{act} and nothing changed. Seat "
                    f"{conditions[code]} names an eligibility rule this release cannot check; it "
                    "can check only \"an active member of the LO\". Adopt a version that uses "
                    f"that rule for now, then {new_request}; other eligibility rules arrive with "
                    "the slices that can check them."
                )
            next_step = (
                "Take a seat before the LO is born, or adopt a version with a seat the "
                "LO Coordinators appoint. Then make a new Birthing request."
                if act == "The LO was not born"
                else f"Adopt a version with a seat the LO Coordinators appoint, then {new_request}."
            )
            return (
                f"{act} and nothing changed. Nobody holds a GOVERNANCE seat yet, and no seat in "
                "the arrangement can be filled in this release: election seats wait for elections, "
                "co-option seats wait for an active GOVERNANCE, and ex officio seats are not yet "
                f"available. {next_step} The arrangement you adopted stays recorded and in effect."
            )

        received_at = NOW

        def expected_restated(code, act):
            default = expected_default(code, act)
            self.assertEqual(default.count(" and nothing changed."), 1)
            return (
                default.replace(" and nothing changed.", ".", 1)
                + " INTEVIA recorded this refusal and that the request was received at "
                + f"{canonical_timestamp(received_at)}; nothing else was saved."
            )

        def expected_rolled_back(code, act):
            # R-FORM-31a-f and R-FORM-32a, -32b, -32c, -32e, -32f (A3, class B), as adopted 25 Oct 2026.
            default = expected_default(code, act)
            self.assertEqual(default.count(" and nothing changed."), 1)
            return (
                default.replace(
                    " and nothing changed.",
                    ", and nothing from this request was saved except a record that it was "
                    f"received at {canonical_timestamp(received_at)}. The LO's governance "
                    "settings changed after the request was received.",
                    1,
                )
                + " Sending this same request again is still judged as of when it was first "
                + "received, before the change."
            )

        phase_service = OrganismPhaseAdvanceService(clock=lambda: NOW)
        birthing_service = OrganismBirthingService(clock=lambda: NOW)
        with patch(
            "src.intevia.services.organism_formation_service._sbg_arrangement_condition",
            side_effect=condition_reader,
        ):
            with patch.object(phase_service, "_phase_at", return_value="CONCEIVING"):
                for code in codes:
                    for act in acts:
                        with self.subTest(code=code, act=act):
                            direct = _sbg_readiness_message(
                                code=code,
                                act=act,
                                operation=acts[act],
                                database_alias="default",
                                organism=organism,
                                effective_at=effective_at,
                                recorded_at=recorded_at,
                            )
                            self.assertEqual(direct, expected_default(code, act))
                            if act == "The phase was not advanced":
                                via_formation = phase_service._authored_refusal_message(
                                    code,
                                    organism,
                                    "BIRTHING",
                                    effective_at,
                                    recorded_at,
                                    received_at=received_at,
                                )
                                self.assertNotEqual(via_formation, direct)
                                self.assertEqual(
                                    via_formation.encode("utf-8"),
                                    expected_restated(code, act).encode("utf-8"),
                                )
                                rolled_back = phase_service._authored_refusal_message(
                                    code,
                                    organism,
                                    "BIRTHING",
                                    effective_at,
                                    recorded_at,
                                    received_at=received_at,
                                    rolled_back=True,
                                )
                                self.assertEqual(
                                    rolled_back.encode("utf-8"),
                                    expected_rolled_back(code, act).encode("utf-8"),
                                )
                            elif act == "The LO was not born":
                                via_formation = birthing_service._authored_refusal_message(
                                    code,
                                    organism,
                                    effective_at,
                                    received_at,
                                )
                                self.assertNotEqual(via_formation, direct)
                                self.assertEqual(
                                    via_formation.encode("utf-8"),
                                    expected_restated(code, act).encode("utf-8"),
                                )
                                rolled_back = birthing_service._authored_refusal_message(
                                    code,
                                    organism,
                                    effective_at,
                                    received_at,
                                    rolled_back=True,
                                )
                                self.assertEqual(
                                    rolled_back.encode("utf-8"),
                                    expected_rolled_back(code, act).encode("utf-8"),
                                )
                            else:
                                via_application = _governance_enabling_refusal_message(
                                    database_alias="default",
                                    organism=organism,
                                    effective_at=effective_at,
                                    recorded_at=recorded_at,
                                    code=code,
                                )
                                self.assertEqual(
                                    via_application.encode("utf-8"),
                                    direct.encode("utf-8"),
                                )

        contended = S015CommandContended("55P03")
        self.assertIsNone(contended.received_at)
        self.assertEqual(
            str(contended),
            "Your request could not be completed because it conflicted with another database "
            "operation. This attempt made no changes. Please wait a moment before trying again. "
            "If this keeps happening, note the time it occurred; it needs to be checked.",
        )
        malformed = S015CommandReceiptMalformed()
        self.assertIsNone(malformed.received_at)
        self.assertEqual(
            str(malformed),
            "The earlier record of this request could not be read. This attempt made no new "
            "changes. This is a record fault, not evidence that you submitted a different "
            "request; it needs to be checked before the request is retried.",
        )

    def test_nw7_static_clock_path_census_has_no_received_at_keyword(self):
        root = Path(__file__).resolve().parents[1]
        census = {
            "src/intevia/services/organism_formation_service.py": {
                "authority_atomic",
                "_finalise",
                "_sbg_readiness_message",
                "_execute_found_living_organism",
                "_execute_advance_phase",
                "_execute_birth_living_organism",
                "_birthing_guard_atomic",
            },
            "src/intevia/services/organism_governance_service.py": {
                "authority_atomic",
                "_execute",
            },
            "src/intevia/services/organism_entitlement_service.py": {
                "authority_atomic",
                "_finalise",
                "_execute_activate_beta",
                "_entitlement_guard_atomic",
            },
        }
        forbidden_keywords = {
            "authority_atomic": {"received_at"},
            "_finalise": {"received_at"},
            "_sbg_readiness_message": {"received_at", "opening_end", "tail"},
        }
        bound_time_executors = {
            (
                "src/intevia/services/organism_formation_service.py",
                "_execute_found_living_organism",
            ),
            (
                "src/intevia/services/organism_formation_service.py",
                "_execute_advance_phase",
            ),
            (
                "src/intevia/services/organism_formation_service.py",
                "_execute_birth_living_organism",
            ),
            (
                "src/intevia/services/organism_entitlement_service.py",
                "_execute_activate_beta",
            ),
            (
                "src/intevia/services/organism_formation_service.py",
                "_birthing_guard_atomic",
            ),
            (
                "src/intevia/services/organism_entitlement_service.py",
                "_entitlement_guard_atomic",
            ),
            ("src/intevia/services/organism_governance_service.py", "_execute"),
        }
        # CENSUS-G1-N2 (PKT-CLK-3 CLK3-2): the builders an activated executor reaches render
        # the restated form, so opening_end= and tail= must be present there, by class.
        restated_builders = {
            (
                "src/intevia/services/organism_formation_service.py",
                "OrganismPhaseAdvanceService",
                "_authored_refusal_message",
            ),
            (
                "src/intevia/services/organism_formation_service.py",
                "OrganismBirthingService",
                "_authored_refusal_message",
            ),
        }
        offenders = []
        positive_calls = set()
        restated_calls = set()
        for relative_path, names in census.items():
            source = (root / Path(relative_path)).read_text(encoding="utf-8")
            tree = ast.parse(source, filename=relative_path)
            parents = {
                child: node
                for node in ast.walk(tree)
                for child in ast.iter_child_nodes(node)
            }

            def enclosing_function(node):
                parent = parents.get(node)
                while parent is not None:
                    if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        return parent.name
                    parent = parents.get(parent)
                return None

            def enclosing_class(node):
                parent = parents.get(node)
                while parent is not None:
                    if isinstance(parent, ast.ClassDef):
                        return parent.name
                    parent = parents.get(parent)
                return None

            def inside_replay_viewer(node):
                parent = parents.get(node)
                while parent is not None:
                    if isinstance(parent, ast.Call):
                        called = (
                            parent.func.id
                            if isinstance(parent.func, ast.Name)
                            else parent.func.attr
                            if isinstance(parent.func, ast.Attribute)
                            else None
                        )
                        if called == "ReplayViewer":
                            return True
                    parent = parents.get(parent)
                return False

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                called_name = (
                    node.func.id
                    if isinstance(node.func, ast.Name)
                    else node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else None
                )
                if called_name not in names:
                    if called_name == "_clock" and (
                        relative_path,
                        enclosing_function(node),
                    ) in bound_time_executors and not inside_replay_viewer(node):
                        offenders.append(
                            f"{relative_path}:{node.lineno}:_clock:outside-ReplayViewer"
                        )
                    continue
                scope = enclosing_function(node)
                if (
                    called_name
                    in {
                        "authority_atomic",
                        "_birthing_guard_atomic",
                        "_entitlement_guard_atomic",
                        "_finalise",
                    }
                    and (relative_path, scope) in bound_time_executors
                ):
                    received_at = next(
                        (keyword.value for keyword in node.keywords if keyword.arg == "received_at"),
                        None,
                    )
                    expected_time = (
                        "received_at"
                        if scope in {"_birthing_guard_atomic", "_entitlement_guard_atomic"}
                        else "bound.received_at"
                    )
                    if received_at is None or ast.unparse(received_at) != expected_time:
                        offenders.append(
                            f"{relative_path}:{node.lineno}:authority_atomic:missing-bound-time"
                        )
                    else:
                        positive_calls.add((relative_path, scope))
                    continue
                if (
                    called_name == "_sbg_readiness_message"
                    and (relative_path, enclosing_class(node), scope) in restated_builders
                ):
                    keywords = {keyword.arg for keyword in node.keywords}
                    if {"opening_end", "tail"} <= keywords and not keywords & {None, "received_at"}:
                        restated_calls.add((relative_path, enclosing_class(node), scope))
                    else:
                        offenders.append(
                            f"{relative_path}:{node.lineno}:{called_name}:restated-form-missing"
                        )
                    continue
                forbidden = forbidden_keywords.get(called_name, {"received_at"})
                for keyword in node.keywords:
                    if keyword.arg is None or keyword.arg in forbidden:
                        offenders.append(
                            f"{relative_path}:{node.lineno}:{called_name}:{keyword.arg or '**'}"
                        )
        self.assertEqual(positive_calls, bound_time_executors)
        self.assertEqual(restated_calls, restated_builders)
        self.assertEqual(offenders, [])

    def test_nw8_ep17_public_opening_refusal_keeps_only_the_intake_binding(self):
        tag = f"h100-nw8-{uuid4().hex}"
        route = prepare_route(tag=tag)
        birth(route)
        ids, _gateway = route_fixture_state(route)
        with transaction.atomic(), connection.cursor() as cursor:
            target_pk = seed_identity(cursor, f"{tag}-target")
        target = Identity.objects.get(pk=target_pk)
        now = route_test_now(route)
        key = f"h100:nw8:{tag}"
        command = OpenMembershipCommand(
            credential=route.founder.credential,
            request_reference=f"request:{key}",
            idempotency_key=key,
            occurred_at=now,
            effective_at=now,
            living_organism_id=ids["organism_uuid"],
            member_identity_id=target.identity_id,
            reason_class="TEST_ONLY_REASON_H100_EP17",
        )
        service = OrganismMembershipService(
            authority=S015Authority(
                provider=_DeniedOpeningProvider(),
                database_alias="default",
            ),
            clock=lambda: now,
            database_alias="default",
        )
        intake_filter = {
            "actor_id": route.founder.pk,
            "action": OrganismAction.TRANSITION_MEMBERSHIP.value,
            "idempotency_key": key,
        }
        self.assertEqual(CommandIntake.objects.filter(**intake_filter).count(), 0)
        before = (
            AuthorityDecision.objects.count(),
            OrganismCommandReceipt.objects.count(),
            OrganismMembership.objects.count(),
            OrganismMembershipTransition.objects.count(),
        )
        with self.assertRaises(S015AuthorityDenied) as refused:
            service.open_membership(command)
        self.assertEqual(refused.exception.refusal_code, S015AuthorityRefusalCode.DENIED)
        self.assertEqual(CommandIntake.objects.filter(**intake_filter).count(), 1)
        self.assertEqual(
            (
                AuthorityDecision.objects.count(),
                OrganismCommandReceipt.objects.count(),
                OrganismMembership.objects.count(),
                OrganismMembershipTransition.objects.count(),
            ),
            before,
        )

    def test_nw3_real_membership_decoder_failure_rolls_back_after_receipt_insert(self):
        from tests.test_s015_role_command import _ExactProvider

        tag = f"h100-nw3-{uuid4().hex}"
        route = prepare_route(tag=tag)
        birth(route)
        ids, _gateway = route_fixture_state(route)
        with transaction.atomic(), connection.cursor() as cursor:
            target_pk = seed_identity(cursor, f"{tag}-target")
        target = Identity.objects.get(pk=target_pk)
        now = route_test_now(route)
        key = f"h100:nw3:{tag}"
        command = OpenMembershipCommand(
            credential=route.founder.credential,
            request_reference=f"request:{key}",
            idempotency_key=key,
            occurred_at=now,
            effective_at=now,
            living_organism_id=ids["organism_uuid"],
            member_identity_id=target.identity_id,
            reason_class="TEST_ONLY_REASON_H100_NW3",
        )
        service = OrganismMembershipService(
            authority=S015Authority(
                provider=_ExactProvider(),
                database_alias="default",
            ),
            clock=lambda: now,
            database_alias="default",
        )
        actor_id = route.founder.pk
        before = (
            OrganismMembership.objects.count(),
            OrganismMembershipTransition.objects.count(),
            AuthorityDecision.objects.count(),
            OrganismCommandReceipt.objects.count(),
        )
        decoded_after_insert = []
        decoder_causes = []
        original_decoder = service._replay_result

        def observe_insert_then_decode(payload):
            decoded_after_insert.append(
                OrganismCommandReceipt.objects.filter(
                    actor_id=actor_id,
                    idempotency_key=key,
                ).count()
            )
            try:
                return original_decoder(payload)
            except S015CommandReceiptMalformed as malformed:
                decoder_causes.append(malformed.__cause__)
                raise

        with patch.object(MembershipCommandResult, "receipt_payload", return_value={"unexpected": "field"}):
            with patch.object(
                service,
                "_replay_result",
                side_effect=observe_insert_then_decode,
            ):
                with self.assertRaises(S015CommandReceiptMalformed) as refused:
                    service.open_membership(command)

        self.assertEqual(decoded_after_insert, [1])
        intake = CommandIntake.objects.get(
            actor_id=actor_id,
            action=OrganismAction.TRANSITION_MEMBERSHIP.value,
            idempotency_key=key,
        )
        self.assertEqual(refused.exception.received_at, intake.received_at)
        self.assertEqual(
            str(refused.exception),
            "The record of this request's result could not be read back. This attempt made no "
            "new governed change; INTEVIA kept a record that your request was received at "
            f"{canonical_timestamp(intake.received_at)}. This is a record fault, not evidence "
            "that you submitted a different request; it needs to be checked before the same "
            "request is sent again.",
        )
        self.assertIsInstance(refused.exception.__cause__, ValueError)
        self.assertEqual(len(decoder_causes), 1)
        self.assertIs(refused.exception.__cause__, decoder_causes[0])
        self.assertEqual(
            str(refused.exception.__cause__),
            "receipt fields do not match the membership result contract",
        )
        self.assertEqual(
            (
                OrganismMembership.objects.count(),
                OrganismMembershipTransition.objects.count(),
                AuthorityDecision.objects.count(),
                OrganismCommandReceipt.objects.count(),
            ),
            before,
        )
        self.assertEqual(
            CommandIntake.objects.filter(
                actor_id=actor_id,
                action=OrganismAction.TRANSITION_MEMBERSHIP.value,
                idempotency_key=key,
            ).count(),
            1,
        )

    def _founder_transition_case(self, label, provider):
        route = prepare_route(tag=f"h100-nw4-{label}-{uuid4().hex}")
        ids, _gateway = route_fixture_state(route)
        actor = add_active_elector(route, tag=f"{label}-actor")
        now = route_test_now(route)
        key = f"h100:nw4:{label}:{uuid4()}"
        command = TransitionAssignmentCommand(
            credential=actor.credential,
            request_reference=f"request:{key}",
            idempotency_key=key,
            occurred_at=now,
            effective_at=now,
            assignment_uuid=ids["founding_assignment_uuid"],
            action="END",
            resulting_state="ENDED",
        )
        service = OrganismRoleService(
            authority=S015Authority(provider=provider, database_alias="default"),
            clock=lambda: now,
            database_alias="default",
        )
        return route, ids, actor, command, service, key

    def test_nw4_r_auth_02_provider_founder_denial_survives_with_binding(self):
        route, ids, actor, command, service, key = self._founder_transition_case(
            "provider",
            _FounderProtectedProvider(),
        )
        assignment = ContextualRoleAssignment.objects.get(
            assignment_uuid=ids["founding_assignment_uuid"],
        )
        before_events = ContextualRoleAssignmentStateEvent.objects.filter(
            assignment=assignment,
        ).count()
        before_receipts = OrganismCommandReceipt.objects.count()
        captured_denials = []
        original_evaluate = service._evaluate_authority

        def capture_denial(*args, **kwargs):
            try:
                return original_evaluate(*args, **kwargs)
            except S015AuthorityDenied as denial:
                captured_denials.append(denial)
                raise

        with patch.object(service, "_evaluate_authority", side_effect=capture_denial):
            with self.assertRaises(S015AuthorityDenied) as refused:
                service.transition_assignment(command)

        binding = CommandIntake.objects.get(
            actor_id=actor.pk,
            action=OrganismAction.TRANSITION_CONTEXTUAL_ROLE.value,
            idempotency_key=key,
        )
        self.assertEqual(refused.exception.refusal_code, S015AuthorityRefusalCode.C_FOUNDER_PROTECTED)
        self.assertEqual(
            refused.exception.disclosed_refusal_code,
            S015AuthorityRefusalCode.C_FOUNDER_PROTECTED,
        )
        self.assertIsNotNone(refused.exception.authority_decision_reference)
        self.assertIsInstance(refused.exception.__cause__, S015AuthorityDenied)
        self.assertEqual(len(captured_denials), 1)
        self.assertIs(refused.exception.__cause__, captured_denials[0])
        self.assertEqual(refused.exception.request_uuid, captured_denials[0].request_uuid)
        self.assertEqual(
            refused.exception.__cause__.refusal_code,
            S015AuthorityRefusalCode.C_FOUNDER_PROTECTED,
        )
        self.assertEqual(
            refused.exception.__cause__.disclosed_refusal_code,
            S015AuthorityRefusalCode.C_FOUNDER_PROTECTED,
        )
        self.assertEqual(
            refused.exception.__cause__.authority_decision_reference,
            refused.exception.authority_decision_reference,
        )
        self.assertEqual(
            str(refused.exception),
            "The office was not ended or reassigned. Until this LO is born, the founder's "
            "original Coordinator appointment can be ended or reassigned only by the founder. "
            "This is a governed protection of the LO's founding, not a technical fault. After "
            "Birthing it no longer applies: the appointment can then be ended or reassigned "
            "under the LO's ordinary governed rules. INTEVIA recorded this refusal and that the "
            f"request was received at {canonical_timestamp(binding.received_at)}; nothing else "
            "was saved.",
        )
        decision = AuthorityDecision.objects.get(
            decision_reference=refused.exception.authority_decision_reference,
        )
        self.assertEqual(decision.outcome, "REFUSED")
        self.assertEqual(
            decision.refusal_code,
            S015AuthorityRefusalCode.C_FOUNDER_PROTECTED.value,
        )
        self.assertEqual(decision.evaluated_at, binding.received_at)
        self.assertEqual(
            CommandIntake.objects.filter(
                actor_id=actor.pk,
                action=OrganismAction.TRANSITION_CONTEXTUAL_ROLE.value,
                idempotency_key=key,
            ).count(),
            1,
        )
        assignment.refresh_from_db()
        self.assertEqual(assignment.state, "ACTIVE")
        self.assertEqual(
            ContextualRoleAssignmentStateEvent.objects.filter(
                assignment=assignment,
            ).count(),
            before_events,
        )
        self.assertEqual(OrganismCommandReceipt.objects.count(), before_receipts)

    def test_nw4_r_auth_03_service_founder_denial_keeps_only_qualified_decision(self):
        from tests.test_s015_role_command import _ExactProvider

        route, ids, actor, command, service, key = self._founder_transition_case(
            "service",
            _ExactProvider(),
        )
        assignment = ContextualRoleAssignment.objects.get(
            assignment_uuid=ids["founding_assignment_uuid"],
        )
        before_events = ContextualRoleAssignmentStateEvent.objects.filter(
            assignment=assignment,
        ).count()
        before_receipts = OrganismCommandReceipt.objects.count()
        with self.assertRaises(S015AuthorityDenied) as refused:
            service.transition_assignment(command)

        binding = CommandIntake.objects.get(
            actor_id=actor.pk,
            action=OrganismAction.TRANSITION_CONTEXTUAL_ROLE.value,
            idempotency_key=key,
        )
        self.assertEqual(refused.exception.refusal_code, S015AuthorityRefusalCode.C_FOUNDER_PROTECTED)
        self.assertEqual(
            refused.exception.disclosed_refusal_code,
            S015AuthorityRefusalCode.C_FOUNDER_PROTECTED,
        )
        self.assertIsNone(refused.exception.authority_decision_reference)
        self.assertIsNone(refused.exception.__cause__)
        self.assertEqual(
            str(refused.exception),
            "The office was not ended or reassigned; no change was made to the office. Until "
            "this LO is born, the founder's original Coordinator appointment can be ended or "
            "reassigned only by the founder. This is a governed protection of the LO's founding, "
            "not a technical fault. After Birthing it no longer applies: the appointment can "
            "then be ended or reassigned under the LO's ordinary governed rules. INTEVIA kept a "
            "record that this request was received at "
            f"{canonical_timestamp(binding.received_at)}; sent again unchanged, it is answered "
            "as of that time.",
        )
        decisions = list(
            AuthorityDecision.objects.filter(
                actor_id=actor.pk,
                idempotency_key=key,
            )
        )
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].outcome, "QUALIFIED")
        self.assertEqual(decisions[0].evaluated_at, binding.received_at)
        self.assertEqual(
            AuthorityDecision.objects.filter(
                actor_id=actor.pk,
                idempotency_key=key,
                outcome="REFUSED",
            ).count(),
            0,
        )
        self.assertEqual(
            CommandIntake.objects.filter(
                actor_id=actor.pk,
                action=OrganismAction.TRANSITION_CONTEXTUAL_ROLE.value,
                idempotency_key=key,
            ).count(),
            1,
        )
        assignment.refresh_from_db()
        self.assertEqual(assignment.state, "ACTIVE")
        self.assertEqual(
            ContextualRoleAssignmentStateEvent.objects.filter(
                assignment=assignment,
            ).count(),
            before_events,
        )
        self.assertEqual(OrganismCommandReceipt.objects.count(), before_receipts)

    def test_nw9_public_membership_and_role_rollbacks_follow_iw04a(self):
        """IW-04a-style public rollback; no public IntegrityError stimulus is forced."""
        from tests.test_s015_role_command import _ExactProvider

        membership_tag = f"h100-nw9-membership-{uuid4().hex}"
        membership_route = prepare_route(tag=membership_tag)
        birth(membership_route)
        membership_ids, _gateway = route_fixture_state(membership_route)
        with transaction.atomic(), connection.cursor() as cursor:
            target_pk = seed_identity(cursor, f"{membership_tag}-target")
        target = Identity.objects.get(pk=target_pk)
        membership_now = route_test_now(membership_route)
        membership_key = f"h100:nw9:membership:{membership_tag}"
        membership_command = OpenMembershipCommand(
            credential=membership_route.founder.credential,
            request_reference=f"request:{membership_key}",
            idempotency_key=membership_key,
            occurred_at=membership_now,
            effective_at=membership_now,
            living_organism_id=membership_ids["organism_uuid"],
            member_identity_id=target.identity_id,
            reason_class="TEST_ONLY_REASON_H100_NW9",
        )
        membership_service = OrganismMembershipService(
            authority=S015Authority(
                provider=_ExactProvider(),
                database_alias="default",
            ),
            clock=lambda: membership_now,
            database_alias="default",
        )

        role_tag = f"h100-nw9-role-{uuid4().hex}"
        role_route = prepare_route(tag=role_tag)
        birth(role_route)
        role_ids, _gateway = route_fixture_state(role_route)
        nominee = add_active_elector(role_route, tag="nominee")
        nominee_membership = OrganismMembership.objects.get(
            living_organism=role_route.organism,
            identity=nominee,
        )
        role_now = route_test_now(role_route)
        role_key = f"h100:nw9:role:{role_tag}"
        role_command = ProposeAssignmentCommand(
            credential=role_route.founder.credential,
            request_reference=f"request:{role_key}",
            idempotency_key=role_key,
            occurred_at=role_now,
            effective_at=role_now,
            membership_uuid=nominee_membership.membership_uuid,
            role_definition_uuid=role_ids["coordinator_role_uuid"],
            effective_until_state="NONE",
            effective_until=None,
        )
        role_service = OrganismRoleService(
            authority=S015Authority(provider=_ExactProvider(), database_alias="default"),
            clock=lambda: role_now,
            database_alias="default",
        )

        cases = (
            (
                "membership",
                membership_service,
                "open_membership",
                membership_command,
                membership_route.founder.pk,
                membership_key,
            ),
            (
                "role",
                role_service,
                "propose_assignment",
                role_command,
                role_route.founder.pk,
                role_key,
            ),
        )

        def row_counts():
            return (
                CommandIntake.objects.count(),
                OrganismMembership.objects.count(),
                OrganismMembershipTransition.objects.count(),
                ContextualRoleAssignment.objects.count(),
                ContextualRoleAssignmentStateEvent.objects.count(),
                AuthorityDecision.objects.count(),
                OrganismCommandReceipt.objects.count(),
            )

        for label, service, method, command, actor_id, key in cases:
            before = row_counts()
            observed_received_at = []

            def fail_before_receipt(*_args, **_kwargs):
                intake = CommandIntake.objects.get(
                    actor_id=actor_id,
                    idempotency_key=key,
                )
                observed_received_at.append(intake.received_at)
                raise RuntimeError("NW-9 finalisation stimulus") from ValueError(
                    "NW-9 injected cause"
                )

            with self.subTest(family=label):
                with patch.object(service, "_finalise", side_effect=fail_before_receipt):
                    with self.assertRaises(RuntimeError) as failed:
                        getattr(service, method)(command)
                self.assertEqual(str(failed.exception), "NW-9 finalisation stimulus")
                self.assertIsInstance(failed.exception.__cause__, ValueError)
                self.assertEqual(str(failed.exception.__cause__), "NW-9 injected cause")
                self.assertEqual(len(observed_received_at), 1)
                after = row_counts()
                self.assertEqual(
                    tuple(end - start for start, end in zip(before, after)),
                    (1, 0, 0, 0, 0, 0, 0),
                )
                self.assertEqual(
                    CommandIntake.objects.filter(
                        actor_id=actor_id,
                        idempotency_key=key,
                    ).count(),
                    1,
                )

    def test_nw2_iw27a_post_admission_mutex_contention_keeps_binding_and_retries_same_time(self):
        route, target, _now, key, command, service = self._membership_open_case("nw2")
        mutex_key = command_mutex_key(
            actor_identity_id=route.founder.identity_id,
            action=OrganismAction.TRANSITION_MEMBERSHIP,
            idempotency_key=key,
        )
        captured_errors = []
        acquire_mutex = membership_service_module.acquire_organism_command_mutex

        def capture_mutex_error(*args, **kwargs):
            try:
                return acquire_mutex(*args, **kwargs)
            except OperationalError as error:
                captured_errors.append(error)
                raise

        mutex_spy = patch.object(
            membership_service_module,
            "acquire_organism_command_mutex",
            side_effect=capture_mutex_error,
        )
        mutex_spy.start()
        self.addCleanup(mutex_spy.stop)
        before = self._public_row_counts()
        holder_pid = backend_pid(connection)
        worker = None
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [mutex_key])

                def open_with_timeout():
                    with connection.cursor() as worker_cursor:
                        worker_cursor.execute("SET lock_timeout = '2s'")
                    return service.open_membership(command)

                worker = start_worker(open_with_timeout)
                wait_until_locked(
                    cursor,
                    worker.pid(),
                    holder_pid,
                    locktype="advisory",
                    timeout=10,
                )
                intake = CommandIntake.objects.get(
                    actor_id=route.founder.pk,
                    action=OrganismAction.TRANSITION_MEMBERSHIP.value,
                    idempotency_key=key,
                )
                worker.thread.join(timeout=15)
                self.assertFalse(worker.thread.is_alive())
                contended = worker.result(timeout=5)
                self.assertIsInstance(contended, S015CommandContended)
                self.assertEqual(contended.diagnostic_sqlstate, "55P03")
                self.assertEqual(contended.received_at, intake.received_at)
                self.assertEqual(
                    str(contended),
                    "Your request could not be completed because it conflicted with another database operation. "
                    "This attempt wrote no governed record; INTEVIA kept a record that your request was received "
                    f"at {canonical_timestamp(intake.received_at)}. Please wait a moment before sending the same "
                    "request again; it is judged as of that time. If this keeps happening, note the time it "
                    "occurred; it needs to be checked.",
                )
                self.assertIsInstance(contended.__cause__, OperationalError)
                self.assertEqual(len(captured_errors), 1)
                self.assertIs(contended.__cause__, captured_errors[0])
                self.assertEqual(
                    getattr(contended.__cause__.__cause__, "sqlstate", None),
                    "55P03",
                )
                after_failure = self._public_row_counts()
                self.assertEqual(
                    self._changed_public_rows(before, after_failure),
                    {"core_commandintake": 1},
                )
        finally:
            if worker is not None:
                worker.thread.join(timeout=30)
                if worker.thread.is_alive():
                    cleanup_workers([worker])

        result = service.open_membership(command)
        member = OrganismMembership.objects.get(
            living_organism=route.organism,
            identity=target,
        )
        self.assertEqual(
            result.membership_uuid,
            WithheldField("membership_uuid", "SUBJECT"),
        )
        transition = OrganismMembershipTransition.objects.get(
            lineage_reference=result.lineage_reference
        )
        self.assertEqual(transition.membership, member)
        self.assertEqual(transition.received_at, intake.received_at)
        self.assertEqual(
            CommandIntake.objects.get(
                actor_id=route.founder.pk,
                action=OrganismAction.TRANSITION_MEMBERSHIP.value,
                idempotency_key=key,
            ).received_at,
            intake.received_at,
        )

    def test_nw10_iw05_deferred_constraint_failure_at_command_commit_retries_same_time(self):
        route, target, _now, key, command, service = self._membership_open_case("nw10-deferred")
        before = self._public_row_counts()
        original_finalise = service._finalise
        deferred_failure_armed = []

        def fail_at_commit(*args, **kwargs):
            result = original_finalise(*args, **kwargs)
            with connection.cursor() as cursor:
                cursor.execute(
                    "CREATE TEMP TABLE h100_deferred_parent "
                    "(id integer PRIMARY KEY) ON COMMIT DROP"
                )
                cursor.execute(
                    "CREATE TEMP TABLE h100_deferred_child ("
                    "parent_id integer NOT NULL, "
                    "CONSTRAINT h100_nw10_deferred_failure_fk "
                    "FOREIGN KEY (parent_id) REFERENCES h100_deferred_parent(id) "
                    "DEFERRABLE INITIALLY DEFERRED) ON COMMIT DROP"
                )
                cursor.execute(
                    "INSERT INTO h100_deferred_child (parent_id) VALUES (1)"
                )
            deferred_failure_armed.append(True)
            return result

        with patch.object(service, "_finalise", side_effect=fail_at_commit):
            with self.assertRaises(IntegrityError) as failed:
                service.open_membership(command)

        self.assertEqual(deferred_failure_armed, [True])
        cause = failed.exception.__cause__
        self.assertIsNotNone(cause)
        self.assertEqual(getattr(cause, "sqlstate", None), "23503")
        self.assertEqual(
            getattr(getattr(cause, "diag", None), "constraint_name", None),
            "h100_nw10_deferred_failure_fk",
        )
        after_failure = self._public_row_counts()
        self.assertEqual(
            self._changed_public_rows(before, after_failure),
            {"core_commandintake": 1},
        )
        intake = CommandIntake.objects.get(
            actor_id=route.founder.pk,
            action=OrganismAction.TRANSITION_MEMBERSHIP.value,
            idempotency_key=key,
        )

        result = service.open_membership(command)
        member = OrganismMembership.objects.get(
            living_organism=route.organism,
            identity=target,
        )
        self.assertEqual(
            result.membership_uuid,
            WithheldField("membership_uuid", "SUBJECT"),
        )
        transition = OrganismMembershipTransition.objects.get(
            lineage_reference=result.lineage_reference
        )
        self.assertEqual(transition.membership, member)
        self.assertEqual(transition.received_at, intake.received_at)
        self.assertEqual(
            CommandIntake.objects.get(
                actor_id=route.founder.pk,
                action=OrganismAction.TRANSITION_MEMBERSHIP.value,
                idempotency_key=key,
            ).received_at,
            intake.received_at,
        )

    def test_nw10_iw27b_executor_statement_timeout_keeps_only_intake_and_retries_same_time(self):
        route, target, _now, key, command, service = self._membership_open_case("nw10-timeout")
        before = self._public_row_counts()
        acquire_mutex = membership_service_module.acquire_organism_command_mutex

        def timeout_after_mutex(*args, **kwargs):
            result = acquire_mutex(*args, **kwargs)
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL statement_timeout = '25ms'")
                cursor.execute("SELECT pg_sleep(0.2)")
            return result

        with patch.object(
            membership_service_module,
            "acquire_organism_command_mutex",
            side_effect=timeout_after_mutex,
        ):
            with self.assertRaises(OperationalError) as failed:
                service.open_membership(command)

        cause = failed.exception.__cause__
        self.assertIsNotNone(cause)
        self.assertEqual(getattr(cause, "sqlstate", None), "57014")
        self.assertIn("statement timeout", str(cause).lower())
        after_failure = self._public_row_counts()
        self.assertEqual(
            self._changed_public_rows(before, after_failure),
            {"core_commandintake": 1},
        )
        intake = CommandIntake.objects.get(
            actor_id=route.founder.pk,
            action=OrganismAction.TRANSITION_MEMBERSHIP.value,
            idempotency_key=key,
        )

        result = service.open_membership(command)
        member = OrganismMembership.objects.get(
            living_organism=route.organism,
            identity=target,
        )
        self.assertEqual(
            result.membership_uuid,
            WithheldField("membership_uuid", "SUBJECT"),
        )
        transition = OrganismMembershipTransition.objects.get(
            lineage_reference=result.lineage_reference
        )
        self.assertEqual(transition.membership, member)
        self.assertEqual(transition.received_at, intake.received_at)
        self.assertEqual(
            CommandIntake.objects.get(
                actor_id=route.founder.pk,
                action=OrganismAction.TRANSITION_MEMBERSHIP.value,
                idempotency_key=key,
            ).received_at,
            intake.received_at,
        )
