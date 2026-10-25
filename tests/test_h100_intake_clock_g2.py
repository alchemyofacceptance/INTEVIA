"""CLK3-3 governance-seat intake witnesses (D §§7.1, 9.2 and 10.3)."""

import hashlib
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

from django.db import DatabaseError, IntegrityError, OperationalError, connection, connections, transaction
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext

from core.models import (
    AuthorityDecision,
    CircleAdmissionDecision,
    CircleJoinRequest,
    CircleParticipation,
    CommandIntake,
    OrganismCommandReceipt,
)
from core.organism_contract import OrganismAction
from src.intevia.services import organism_governance_service as governance_module
from src.intevia.services.organism_authority import canonical_json_bytes
from src.intevia.services.command_intake import (
    ChildEntry,
    IntakeEntry,
    run_activation_step,
)
from src.intevia.services.organism_command_support import (
    S015CommandAtomicityRequired,
    S015CommandContended,
    S015CommandReplayMismatch,
    canonical_timestamp,
    command_mutex_key,
)
from src.intevia.services.organism_authority import S015AuthorityDenied, S015AuthorityRefusalCode
from src.intevia.services.organism_governance_service import (
    DecideGovernanceSeatCommand,
    RequestGovernanceSeatCommand,
    SBGGovernanceRefused,
    SBGGovernanceUnavailable,
    SeatFoundingGovernanceCircleCommand,
    _RECIPE_SEAT_FOUNDING,
    _compound_stage_identifiers,
)
from tests.s015_concurrency_support import cleanup_workers, start_worker
from tests.sb2_route_support import birth
from tests.sbg_route_support import (
    advance_to_birthing,
    database_now,
    prepare_governance_route,
)


def _public_table_counts():
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


OPEN_REQUEST = "Your own request for seat founder is recorded and remains open."
# R-FIX-01-S2 (the Human Governor's T-LOCK-1 text, decision stage; Q-ST3-2)
R_FIX_01_S2 = (
    "Your request could not be completed because it conflicted with another database operation. "
    "The decision stage of this attempt wrote no governed record; your own request for seat founder "
    "is recorded and remains open, and INTEVIA kept a record that your request was received at {T}. "
    "Please wait a moment before sending the same request again; it is judged as of that time. "
    "If this keeps happening, note the time it occurred; it needs to be checked."
)
# B-09-S2, ACTION = ADMIT_CIRCLE_PARTICIPANT (D §9.2)
B_09_S2 = (
    "ADMIT_CIRCLE_PARTICIPANT could not be completed because of a technical problem; no governed record "
    "was written by the decision stage. Your own request for seat founder is recorded and remains open. "
    "INTEVIA kept a record that this request was received at {T}; the same request can be sent again "
    "and is judged as of that time. If it persists, report it."
)
# R-GOV-13-S2 (class A in the decision stage)
R_GOV_13_S2 = (
    "Nobody was seated. Seat founder is filled by appointment by the LO Coordinators, which this release "
    "can use only while the LO's GOVERNANCE Circle has never been active, and this Circle has been active "
    "before. A Circle that has been active admits new participants through its own GOVERNANCE route, "
    "which arrives with a later release. The LO's Coordinators continue to govern until then. Your own "
    "request for seat founder is recorded and remains open. INTEVIA recorded this decision-stage refusal "
    "and that the request was received at {T}; the decision stage saved nothing else."
)
# R-GOV-07 (standalone, class C)
R_GOV_07 = (
    "Nobody was seated; no governed record was written. A GOVERNANCE seat needs an active member of this "
    "LO at that time, and this person is not an active member at that time. Seat someone who is an active "
    "member of this LO. INTEVIA kept a record that this request was received at {T}; sent again unchanged, "
    "it is answered as of that time."
)


# R-GOV-05 (standalone, class B)
R_GOV_05 = (
    "Nobody was seated; no governed record was written. Seat founder is already held at that time, and "
    "each seat holds one person. Choose a vacant seat, or wait until this seat becomes vacant. A seat "
    "becomes vacant when its holder's seat ends. INTEVIA kept a record that this request was received at "
    "{T}; sent again unchanged, it is answered as of that time."
)
NO_ROLLBACK_CLAIM = "no governed record was written"
COMPOUND_COMPLETED = {
    "core_commandintake": 1,
    "core_authoritydecision": 2,
    "core_circlejoinrequest": 1,
    "core_organismcommandreceipt": 2,
    "core_circleadmissiondecision": 1,
    "core_circleparticipation": 1,
}
REQUEST_SCHEMA = "sbg-governance-seat-request-v1"
DECISION_SCHEMA = "sbg-governance-seat-decision-v1"


def _delta(before, after):
    return {
        table: after[table] - before.get(table, 0)
        for table in after
        if after[table] != before.get(table, 0)
    }


def _later_reading(received_at):
    """D §7.1a's fixture precondition: at most three database readings, one later than T."""
    for _ in range(3):
        observation = database_now()
        if observation > received_at:
            return observation
    raise AssertionError("database clock did not advance; the witness cannot discriminate")


def _request_fingerprint(command, received_at):
    """The request stage's receipt fingerprint, recomputed from its documented projection at T."""
    return hashlib.sha256(canonical_json_bytes({
        "action": OrganismAction.REQUEST_GOVERNANCE_SEAT.value,
        "effective_at": received_at if command.effective_at is None else command.effective_at,
        "idempotency_key": command.idempotency_key,
        "organism_uuid": UUID(str(command.living_organism_uuid)),
        "payload": {
            "circle_uuid": str(UUID(str(command.circle_uuid))),
            "seat_rule_uuid": str(UUID(str(command.seat_rule_uuid))),
            "seat_id": command.seat_id,
        },
        "request_reference": command.request_reference,
        "schema": REQUEST_SCHEMA,
    })).hexdigest()


def _decision_fingerprint(command, received_at):
    """The decision stage's receipt fingerprint, recomputed from its documented projection at T."""
    return hashlib.sha256(canonical_json_bytes({
        "action": OrganismAction.ADMIT_CIRCLE_PARTICIPANT.value,
        "effective_at": received_at if command.effective_at is None else command.effective_at,
        "idempotency_key": command.idempotency_key,
        "organism_uuid": UUID(str(command.living_organism_uuid)),
        "outcome": command.outcome,
        "route": command.route,
        "request_uuid": UUID(str(command.request_uuid)),
        "schema": DECISION_SCHEMA,
    })).hexdigest()


def _receipt(actor, action, key):
    return OrganismCommandReceipt.objects.get(actor=actor, action=action.value, idempotency_key=key)


@contextmanager
def _observed_canonical_bytes(schema):
    """Delegate to the service's canonical serializer and keep the bytes it returns for one schema."""
    captured = []
    original = governance_module.canonical_json_bytes

    def observer(value):
        result = original(value)
        if isinstance(value, dict) and value.get("schema") == schema:
            captured.append(result)
        return result

    with patch.object(governance_module, "canonical_json_bytes", observer):
        yield captured


class _StubRows:
    """A stand-in for one model's rows in the O-01 predicate: filter(...).exists() answers by keyword."""

    def __init__(self, answer):
        self._answer = answer
        self.filters = []

    def filter(self, **kwargs):
        self.filters.append(kwargs)
        return SimpleNamespace(exists=lambda: self._answer(kwargs))


@override_settings(INTEVIA_TEST_CONTEXT=True)
class GovernanceClockG2Tests(TransactionTestCase):
    """Exercise G2 public admission, stage execution, retry and dispatch (D §7.1)."""

    def _route(self, *, governance_enabled=True, birthing=False):
        route = prepare_governance_route(
            tag=f"clk3-g2-{uuid4().hex}",
            governance_enabled=governance_enabled,
            seat_founder=False,
        )
        if birthing:
            advance_to_birthing(route.route, tag=f"{route.route.tag}:phase")
            birth(route.route, suffix="g2-birth")
        return route

    def _request(self, route, tag):
        return RequestGovernanceSeatCommand(
            credential=route.founder.credential,
            living_organism_uuid=route.organism.organism_id,
            circle_uuid=route.circle_uuid,
            seat_rule_uuid=route.rule_uuid,
            seat_id="founder",
            request_reference=f"clk3-g2:{tag}:request",
            idempotency_key=f"clk3-g2:{tag}:request-key",
        )

    def _compound(self, route, tag):
        return DecideGovernanceSeatCommand(
            credential=route.founder.credential,
            living_organism_uuid=route.organism.organism_id,
            circle_uuid=route.circle_uuid,
            seat_rule_uuid=route.rule_uuid,
            seat_id="founder",
            request_reference=f"clk3-g2:{tag}:parent",
            idempotency_key=f"clk3-g2:{tag}:parent-key",
            outcome="ACCEPT",
            route=None,
            request_uuid=None,
        )

    def _decision(self, route, request_uuid, tag, *, outcome="DECLINE"):
        return DecideGovernanceSeatCommand(
            credential=route.founder.credential,
            living_organism_uuid=route.organism.organism_id,
            request_reference=f"clk3-g2:{tag}:decision",
            idempotency_key=f"clk3-g2:{tag}:decision-key",
            outcome=outcome,
            route="CIRCLE_FOUNDING",
            request_uuid=request_uuid,
        )

    def test_iw00_g2_entries_refuse_atomic_and_manual_modes_without_sql(self):
        """G2's four entries fail B-01 before SQL in atomic and manual modes (D §7.1)."""
        route = self._route()
        commands = (
            (route.service.request_governance_seat, self._request(route, "iw00-request")),
            (
                route.service.decide_governance_seat,
                replace(
                    self._decision(route, uuid4(), "iw00-decision"),
                    request_uuid="malformed request UUID",
                ),
            ),
            (route.service.decide_governance_seat, self._compound(route, "iw00-compound")),
            (
                route.service.seat_governance_circle,
                SeatFoundingGovernanceCircleCommand(
                    credential=route.founder.credential,
                    living_organism_uuid=route.organism.organism_id,
                    circle_uuid=route.circle_uuid,
                    seat_rule_uuid=route.rule_uuid,
                    seat_id="founder",
                    request_reference="clk3-g2:iw00-seat",
                    idempotency_key="clk3-g2:iw00-seat-key",
                ),
            ),
        )
        for method, command in commands:
            with self.subTest(method=method.__name__), transaction.atomic():
                with CaptureQueriesContext(connection) as captured:
                    with self.assertRaises(S015CommandAtomicityRequired):
                        method(command)
                self.assertEqual(captured.captured_queries, [])
        original_autocommit = connection.get_autocommit()
        connection.set_autocommit(False)
        try:
            for method, command in commands:
                with self.subTest(mode="manual", method=method.__name__):
                    with self.assertRaises(S015CommandAtomicityRequired):
                        method(command)
        finally:
            connection.set_autocommit(original_autocommit)

    def test_iw00c_g2_entries_admit_then_run_executors_and_the_orchestrator_holds_no_transaction_between_stages(self):
        """A compound public call admits once and opens each stage at top level (D §10.3)."""
        route = self._route(birthing=True)
        command = self._compound(route, "iw00c")
        transaction_states = []
        original_request = route.service._execute_request_governance_seat
        original_decision = route.service._execute_decide_governance_seat

        def observe_request(*args, **kwargs):
            transaction_states.append(connection.in_atomic_block)
            return original_request(*args, **kwargs)

        def observe_decision(*args, **kwargs):
            transaction_states.append(connection.in_atomic_block)
            return original_decision(*args, **kwargs)

        with (
            patch.object(route.service, "_execute_request_governance_seat", observe_request),
            patch.object(route.service, "_execute_decide_governance_seat", observe_decision),
        ):
            result = route.service.decide_governance_seat(command)
        self.assertIsNotNone(result.request_uuid)
        self.assertEqual(transaction_states, [False, False])
        self.assertEqual(
            CommandIntake.objects.filter(
                actor=route.founder,
                entry=IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND.value,
                idempotency_key=command.idempotency_key,
            ).count(),
            1,
        )

    def test_w_clk3_08_standalone_request_and_decision_unchanged_retries_replay_with_distinct_clock_observations(self):
        """Standalone retries replay at T while replay-viewer reads remain fresh (D §§4.8, 7.1a).

        Each receipt's fingerprint is recomputed from the call's own projection and the intake row's T,
        and each retry is preceded by the bounded later-reading precondition.
        """
        route = self._route()
        request_command = self._request(route, "w08")
        request_result = route.service.request_governance_seat(request_command)
        request_intake = CommandIntake.objects.get(
            actor=route.founder,
            entry=IntakeEntry.REQUEST_GOVERNANCE_SEAT.value,
            idempotency_key=request_command.idempotency_key,
        )
        self.assertEqual(
            _receipt(
                route.founder,
                OrganismAction.REQUEST_GOVERNANCE_SEAT,
                request_command.idempotency_key,
            ).payload_fingerprint,
            _request_fingerprint(request_command, request_intake.received_at),
        )
        _later_reading(request_intake.received_at)
        viewer_times = []

        def viewer_clock():
            value = database_now()
            viewer_times.append(value)
            return value

        before_request_replay = _public_table_counts()
        with patch.object(route.service, "_clock", side_effect=viewer_clock):
            replayed_request = route.service.request_governance_seat(request_command)
        self.assertEqual(_public_table_counts(), before_request_replay)
        self.assertEqual(replayed_request, request_result)
        self.assertEqual(len(viewer_times), 1)
        self.assertGreater(viewer_times[0], request_intake.received_at)
        decision_command = self._decision(route, request_result.request_uuid, "w08")
        first_decision = route.service.decide_governance_seat(decision_command)
        decision_intake = CommandIntake.objects.get(
            actor=route.founder,
            entry=IntakeEntry.DECIDE_GOVERNANCE_SEAT.value,
            idempotency_key=decision_command.idempotency_key,
        )
        self.assertEqual(
            _receipt(
                route.founder,
                OrganismAction.ADMIT_CIRCLE_PARTICIPANT,
                decision_command.idempotency_key,
            ).payload_fingerprint,
            _decision_fingerprint(decision_command, decision_intake.received_at),
        )
        _later_reading(decision_intake.received_at)
        decision_viewer_times = []

        def decision_viewer_clock():
            value = database_now()
            decision_viewer_times.append(value)
            return value

        before_decision_replay = _public_table_counts()
        with patch.object(route.service, "_clock", side_effect=decision_viewer_clock):
            self.assertEqual(
                route.service.decide_governance_seat(decision_command),
                first_decision,
            )
        self.assertEqual(_public_table_counts(), before_decision_replay)
        self.assertEqual(len(decision_viewer_times), 1)
        self.assertGreater(decision_viewer_times[0], decision_intake.received_at)
        self.assertEqual(decision_intake.received_at, CommandIntake.objects.get(
            actor=route.founder,
            entry=IntakeEntry.DECIDE_GOVERNANCE_SEAT.value,
            idempotency_key=decision_command.idempotency_key,
        ).received_at)

    def test_w_clk3_02_a_changed_outcome_on_a_standalone_decision_retry_is_refused_at_admission(self):
        """A changed decision outcome under the same key is rejected before execution (D §7.1)."""
        route = self._route()
        request = route.service.request_governance_seat(self._request(route, "w02"))
        command = self._decision(route, request.request_uuid, "w02", outcome="DECLINE")
        route.service.decide_governance_seat(command)
        before = _public_table_counts()
        with self.assertRaises(S015CommandReplayMismatch):
            route.service.decide_governance_seat(replace(command, outcome="ACCEPT"))
        self.assertEqual(_public_table_counts(), before)

    def test_w_clk3_09_request_stage_rollback_then_retry_reproduces_t_on_the_join_request(self):
        """A request-body rollback leaves only intake; retry uses its original time (D §7.1).

        The retry's request row, its AuthorityDecision.evaluated_at and its receipt fingerprint all carry
        T; the fingerprint equals the first attempt's (its canonical bytes observed) and an independent
        recomputation at T.
        """
        route = self._route()
        command = self._request(route, "w09")
        self.assertIsNone(command.effective_at)
        original_save = CircleJoinRequest.save
        hit = []

        def save_then_fail(instance, *args, **kwargs):
            result = original_save(instance, *args, **kwargs)
            if not hit:
                hit.append(True)
                raise RuntimeError("injected request-stage rollback")
            return result

        before = _public_table_counts()
        with (
            patch.object(CircleJoinRequest, "save", save_then_fail),
            _observed_canonical_bytes(REQUEST_SCHEMA) as first_bytes,
        ):
            with self.assertRaisesRegex(RuntimeError, "injected request-stage rollback"):
                route.service.request_governance_seat(command)
        self.assertEqual(hit, [True])
        self.assertEqual(len(first_bytes), 1)
        self.assertEqual(_delta(before, _public_table_counts()), {"core_commandintake": 1})
        binding = CommandIntake.objects.get(
            actor=route.founder,
            entry=IntakeEntry.REQUEST_GOVERNANCE_SEAT.value,
            idempotency_key=command.idempotency_key,
        )
        self.assertFalse(CircleJoinRequest.objects.filter(
            consent_reference=command.request_reference,
        ).exists())
        route.service.request_governance_seat(command)
        request = CircleJoinRequest.objects.get(consent_reference=command.request_reference)
        self.assertEqual(request.received_at, binding.received_at)
        self.assertEqual(request.occurred_at, binding.received_at)
        self.assertEqual(request.effective_at, binding.received_at)
        receipt = _receipt(route.founder, OrganismAction.REQUEST_GOVERNANCE_SEAT, command.idempotency_key)
        self.assertEqual(receipt.payload_fingerprint, hashlib.sha256(first_bytes[0]).hexdigest())
        self.assertEqual(receipt.payload_fingerprint, _request_fingerprint(command, binding.received_at))
        self.assertEqual(
            AuthorityDecision.objects.get(
                decision_reference=receipt.authority_decision_reference,
            ).evaluated_at,
            binding.received_at,
        )

    def test_w_clk3_09b_standalone_decision_rollback_then_retry_reproduces_t_on_the_admission_decision(self):
        """A decision-body rollback leaves stage 1 and retry preserves T (D §7.1).

        The retry's decision row, its AuthorityDecision.evaluated_at and its receipt fingerprint all carry
        T; the fingerprint equals the first attempt's (its canonical bytes observed) and an independent
        recomputation at T with the decision schema.
        """
        route = self._route()
        request = route.service.request_governance_seat(self._request(route, "w09b"))
        command = self._decision(route, request.request_uuid, "w09b")
        self.assertIsNone(command.effective_at)
        original_save = CircleAdmissionDecision.save
        hit = []

        def save_then_fail(instance, *args, **kwargs):
            result = original_save(instance, *args, **kwargs)
            if not hit:
                hit.append(True)
                raise RuntimeError("injected decision-stage rollback")
            return result

        before = _public_table_counts()
        with (
            patch.object(CircleAdmissionDecision, "save", save_then_fail),
            _observed_canonical_bytes(DECISION_SCHEMA) as first_bytes,
        ):
            with self.assertRaisesRegex(RuntimeError, "injected decision-stage rollback"):
                route.service.decide_governance_seat(command)
        self.assertEqual(hit, [True])
        self.assertEqual(len(first_bytes), 1)
        self.assertEqual(_delta(before, _public_table_counts()), {"core_commandintake": 1})
        binding = CommandIntake.objects.get(
            actor=route.founder,
            entry=IntakeEntry.DECIDE_GOVERNANCE_SEAT.value,
            idempotency_key=command.idempotency_key,
        )
        self.assertFalse(CircleAdmissionDecision.objects.filter(
            request__request_uuid=request.request_uuid,
        ).exists())
        route.service.decide_governance_seat(command)
        decision = CircleAdmissionDecision.objects.get(request__request_uuid=request.request_uuid)
        self.assertEqual(decision.received_at, binding.received_at)
        self.assertEqual(decision.occurred_at, binding.received_at)
        self.assertEqual(decision.effective_at, binding.received_at)
        receipt = _receipt(route.founder, OrganismAction.ADMIT_CIRCLE_PARTICIPANT, command.idempotency_key)
        self.assertEqual(receipt.payload_fingerprint, hashlib.sha256(first_bytes[0]).hexdigest())
        self.assertEqual(receipt.payload_fingerprint, _decision_fingerprint(command, binding.received_at))
        self.assertEqual(
            AuthorityDecision.objects.get(
                decision_reference=receipt.authority_decision_reference,
            ).evaluated_at,
            binding.received_at,
        )

    def _stage_two_rows(self, route, command):
        """The exact request's CAD, participation and stage-2 receipt, found by the compound's identifiers."""
        _request_key, request_reference, admission_key, _admission_reference = _compound_stage_identifiers(
            route.organism.organism_id,
            command.idempotency_key,
        )
        request = CircleJoinRequest.objects.get(
            circle__parent_organism=route.organism,
            consent_reference=request_reference,
        )
        decision = CircleAdmissionDecision.objects.filter(request=request).first()
        participation = (
            None if decision is None else CircleParticipation.objects.filter(decision=decision).first()
        )
        receipt = OrganismCommandReceipt.objects.filter(
            actor=route.founder,
            action=OrganismAction.ADMIT_CIRCLE_PARTICIPANT.value,
            idempotency_key=admission_key,
        ).first()
        return request, decision, participation, receipt

    def _assert_replay_returns_the_stored_rows(self, route, call, command, binding):
        """D §9.2's +0 retry: the bounded later reading, then the stored result over unchanged tables."""
        _later_reading(binding.received_at)
        request, decision, participation, receipt = self._stage_two_rows(route, command)
        committed_counts = _public_table_counts()
        result = call(command)
        self.assertEqual(_public_table_counts(), committed_counts)
        self.assertEqual(result.request_uuid, request.request_uuid)
        self.assertEqual(result.decision_uuid, decision.decision_uuid)
        self.assertEqual(result.participation_uuid, participation.participation_uuid)
        self.assertIsNotNone(receipt)

    def test_w_clk3_s2c_a_committed_decision_stage_with_a_lost_response_renders_no_rollback_claim_and_the_retry_replays(self):
        """S2c: the decision stage committed, then its response or result handling failed (D §9.2, BN-1).

        Each subcase states the defect it rejects:
        (a) executor: the response is lost after the stage committed; the old executor catch added a
            false rollback claim and a false open-request sentence.
        (b) post_check and (c) result_from: these ran outside the guarded region, so the injected error
            escaped without stage_context.
        Under the repair all three re-raise the original error, with its cause, stage_context and
        outcome_uncertain = True; the committed CAD, participation and stage-2 receipt are present; the
        identical retry, after the bounded later reading, replays the stored result, +0 over every table.
        """
        for failure_point in ("executor", "post_check", "result_from"):
            route = self._route(birthing=failure_point == "executor")
            prior = RuntimeError(f"prior cause: {failure_point}")
            if failure_point == "executor":
                injected = SBGGovernanceUnavailable(f"injected after commit: {failure_point}")
            else:
                injected = RuntimeError(f"injected after commit: {failure_point}")
            injected.__cause__ = prior
            with self.subTest(failure_point=failure_point):
                if failure_point == "executor":
                    command = self._compound(route, f"s2c-{failure_point}")
                    entry = IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND
                    call = route.service.decide_governance_seat
                    original = route.service._execute_decide_governance_seat

                    def lose_response(*args, **kwargs):
                        original(*args, **kwargs)
                        raise injected

                    context = patch.object(
                        route.service,
                        "_execute_decide_governance_seat",
                        lose_response,
                    )
                else:
                    command = self._founding(route, f"s2c-{failure_point}")
                    entry = IntakeEntry.SEAT_GOVERNANCE_CIRCLE
                    call = route.service.seat_governance_circle
                    recipe = replace(
                        _RECIPE_SEAT_FOUNDING,
                        **{
                            failure_point: lambda _result: (_ for _ in ()).throw(injected)
                        },
                    )
                    context = patch.object(governance_module, "_RECIPE_SEAT_FOUNDING", recipe)
                before = _public_table_counts()
                with context, self.assertRaises(type(injected)) as raised:
                    call(command)
                self.assertIs(raised.exception, injected)
                self.assertEqual(str(raised.exception), f"injected after commit: {failure_point}")
                self.assertIs(raised.exception.__cause__, prior)
                self.assertIs(getattr(raised.exception, "stage_context", None), ChildEntry.ADMIT_STAGE)
                self.assertIs(getattr(raised.exception, "outcome_uncertain", None), True)
                self.assertNotIn(NO_ROLLBACK_CLAIM, str(raised.exception))
                self.assertNotIn(OPEN_REQUEST, str(raised.exception))
                self.assertEqual(_delta(before, _public_table_counts()), COMPOUND_COMPLETED)
                _request, decision, participation, receipt = self._stage_two_rows(route, command)
                self.assertIsNotNone(decision)
                self.assertIsNotNone(participation)
                self.assertIsNotNone(receipt)
                binding = self._binding(route, entry, command)
                self._assert_replay_returns_the_stored_rows(route, call, command, binding)

    def test_w_clk3_s2d_an_unclassified_exit_failure_is_uncertain_and_keeps_the_original_error(self):
        """S2d: a failure at the decision stage's own commit, with no listed SQLSTATE (D §9.2 [A2]).

        A simulation of the classifier's input, not a reproduced network or server failure. A one-shot
        wrapper on the connection's commit is armed only after the stage-2 receipt insert, so the intake's
        and stage 1's commits are untouched; the real authority_atomic has set EXIT before
        transaction.atomic calls commit, and the wrapper records the production cell without setting it.
        (i) before: the wrapper raises before the real commit (rolled back: no CAD, participation or
        stage-2 receipt). (ii) after: it calls the real commit, then raises (committed: all three
        present). (iii) unlisted: as (i), the cause carrying SQLSTATE 57P01, off the closed list.
        Each propagates the original OperationalError with its cause, stage_context and
        outcome_uncertain = True, and no rollback or open-request claim. The wrapper is removed before
        the retry: (i) and (iii) complete stage 2 at T; (ii) replays the stored result, +0.
        """
        for case in ("before", "after", "unlisted"):
            route = self._route(birthing=True)
            command = self._compound(route, f"s2d-{case}")
            injected = OperationalError("simulated connection loss")
            driver_error = RuntimeError("simulated driver error")
            if case == "unlisted":
                driver_error.sqlstate = "57P01"
            injected.__cause__ = driver_error
            wrapper_connection = connections["default"]
            real_commit = wrapper_connection.commit
            original_executor = route.service._execute_decide_governance_seat
            original_receipt_save = OrganismCommandReceipt.save
            cells = []
            armed = []
            hits = []
            phases_at_commit = []

            def executor_spy(stage_command, stage_bound, *, stage_phase=None):
                cells.append(stage_phase)
                return original_executor(stage_command, stage_bound, stage_phase=stage_phase)

            def arm_after_stage_two_receipt(instance, *args, **kwargs):
                result = original_receipt_save(instance, *args, **kwargs)
                if instance.action == OrganismAction.ADMIT_CIRCLE_PARTICIPANT.value and not armed:
                    armed.append(True)
                return result

            def commit_once():
                if not armed or hits:
                    return real_commit()
                hits.append(case)
                phases_at_commit.append(cells[-1]["phase"] if cells and cells[-1] else None)
                if case == "after":
                    real_commit()
                raise injected

            with self.subTest(case=case):
                before = _public_table_counts()
                with (
                    patch.object(route.service, "_execute_decide_governance_seat", executor_spy),
                    patch.object(OrganismCommandReceipt, "save", arm_after_stage_two_receipt),
                    patch.object(wrapper_connection, "commit", commit_once),
                ):
                    with self.assertRaises(OperationalError) as raised:
                        route.service.decide_governance_seat(command)
                self.assertEqual(hits, [case])
                self.assertEqual(phases_at_commit, ["EXIT"])
                self.assertIs(raised.exception, injected)
                self.assertEqual(str(raised.exception), "simulated connection loss")
                self.assertIs(raised.exception.__cause__, driver_error)
                self.assertIs(raised.exception.stage_context, ChildEntry.ADMIT_STAGE)
                self.assertIs(getattr(raised.exception, "outcome_uncertain", None), True)
                self.assertNotIn(NO_ROLLBACK_CLAIM, str(raised.exception))
                self.assertNotIn(OPEN_REQUEST, str(raised.exception))
                self.assertFalse(connection.in_atomic_block)
                failed_counts = _public_table_counts()
                _request, decision, participation, receipt = self._stage_two_rows(route, command)
                committed = case == "after"
                self.assertEqual(decision is not None, committed)
                self.assertEqual(participation is not None, committed)
                self.assertEqual(receipt is not None, committed)
                binding = self._binding(route, IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND, command)
                if committed:
                    self.assertEqual(_delta(before, failed_counts), COMPOUND_COMPLETED)
                    self._assert_replay_returns_the_stored_rows(
                        route, route.service.decide_governance_seat, command, binding,
                    )
                else:
                    self._assert_stage_one_only(before, failed_counts)
                    _later_reading(binding.received_at)
                    self._assert_retry_completes_at_t(
                        route, route.service.decide_governance_seat, command, binding, failed_counts,
                    )

    def _stubbed_open_state(self, route, request_uuid, *, stage_one, receipt, decision):
        """The standalone predicate over separately identified stubs for its three reads (D §5.4)."""
        admission_key = "clk3-g2:o01-stub-admission-key"

        def receipt_answer(filters):
            self.assertEqual(
                filters,
                {
                    "actor_id": route.founder.pk,
                    "action": OrganismAction.ADMIT_CIRCLE_PARTICIPANT.value,
                    "idempotency_key": admission_key,
                },
            )
            return receipt

        requests = _StubRows(lambda filters: stage_one)
        receipts = _StubRows(receipt_answer)
        decisions = _StubRows(lambda filters: decision)
        with (
            patch.object(CircleJoinRequest.objects, "using", return_value=requests),
            patch.object(OrganismCommandReceipt.objects, "using", return_value=receipts),
            patch.object(CircleAdmissionDecision.objects, "using", return_value=decisions),
        ):
            state = route.service._request_open_state(
                compound=False,
                request_uuid=request_uuid,
                actor_pk=route.founder.pk,
                request_key=None,
                admission_key=admission_key,
            )
        self.assertEqual(requests.filters, [{"request_uuid": request_uuid}])
        # The CAD read follows the receipt read (D §5.4): it is made only when no receipt answered.
        self.assertEqual(
            decisions.filters,
            [] if receipt else [{"request__request_uuid": request_uuid}],
        )
        return state

    def test_w_clk3_o01_the_open_request_sentence_appears_only_when_stage_one_is_established_and_both_completion_indicators_are_absent(self):
        """O-01 distinguishes OPEN, COMPLETED, NOT_ESTABLISHED and UNKNOWN (D §5.4)."""
        route = self._route()
        command = self._request(route, "o01")
        request = route.service.request_governance_seat(command)
        state = route.service._request_open_state(
            compound=False,
            request_uuid=request.request_uuid,
            actor_pk=route.founder.pk,
            request_key=None,
            admission_key="unused",
        )
        self.assertEqual(state, "OPEN")
        missing = route.service._request_open_state(
            compound=False,
            request_uuid=uuid4(),
            actor_pk=route.founder.pk,
            request_key=None,
            admission_key="unused",
        )
        self.assertEqual(missing, "NOT_ESTABLISHED")
        # Case (1), real and standalone: no CircleJoinRequest, so no sentence; the original text stands.
        absent = self._decision(route, uuid4(), "o01-absent")
        with self.assertRaises(SBGGovernanceRefused) as not_established:
            route.service.decide_governance_seat(absent)
        absent_binding = CommandIntake.objects.get(
            actor=route.founder,
            entry=IntakeEntry.DECIDE_GOVERNANCE_SEAT.value,
            idempotency_key=absent.idempotency_key,
        )
        self.assertEqual(
            str(not_established.exception),
            R_GOV_07.format(T=canonical_timestamp(absent_binding.received_at)),
        )
        self.assertNotIn(OPEN_REQUEST, str(not_established.exception))
        completed = self._decision(route, request.request_uuid, "o01-completed")
        route.service.decide_governance_seat(completed)
        self.assertEqual(
            route.service._request_open_state(
                compound=False,
                request_uuid=request.request_uuid,
                actor_pk=route.founder.pk,
                request_key=None,
                admission_key=completed.idempotency_key,
            ),
            "COMPLETED",
        )
        with patch("django.db.models.query.QuerySet.exists", side_effect=DatabaseError):
            unknown = route.service._request_open_state(
                compound=False,
                request_uuid=request.request_uuid,
                actor_pk=route.founder.pk,
                request_key=None,
                admission_key="unused",
            )
        self.assertEqual(unknown, "UNKNOWN")
        # Case (4) and BN-2's converse: pure predicate counterexamples with stubbed, model-specific
        # lookups, not reachable database states, and no claim that this request's own retry replays.
        # (stage 1, receipt, no CAD) fails if the receipt conjunct is ignored; (stage 1, CAD, no receipt)
        # fails if the CAD conjunct is ignored.
        for receipt_present, decision_present in ((True, False), (False, True)):
            with self.subTest(receipt=receipt_present, decision=decision_present):
                self.assertEqual(
                    self._stubbed_open_state(
                        route,
                        request.request_uuid,
                        stage_one=True,
                        receipt=receipt_present,
                        decision=decision_present,
                    ),
                    "COMPLETED",
                )
        self.assertEqual(
            self._stubbed_open_state(
                route,
                request.request_uuid,
                stage_one=True,
                receipt=False,
                decision=False,
            ),
            "OPEN",
        )
        # BN-5, standalone COMPLETED through integrity translation: the request is decided (case 3); a new
        # decision key meets a controlled guard error at the executor boundary. The wrapper's own
        # IntegrityError branch translates it to R-GOV-05 at this call's T, with no open-request sentence.
        guard_command = self._decision(route, request.request_uuid, "o01-completed-guard")
        driver_error = Exception("driver error")
        driver_error.diag = SimpleNamespace(
            message_primary=S015AuthorityRefusalCode.SBG_SEAT_OCCUPIED.value,
        )
        guard_error = IntegrityError(
            f"{S015AuthorityRefusalCode.SBG_SEAT_OCCUPIED.value}\n"
            "CONTEXT: PL/pgSQL function sbg_guard_circleparticipation_occupancy()"
        )
        guard_error.__cause__ = driver_error
        with patch.object(
            route.service,
            "_execute_decide_governance_seat",
            side_effect=guard_error,
        ):
            with self.assertRaises(SBGGovernanceRefused) as guard_refusal:
                route.service.decide_governance_seat(guard_command)
        guard_binding = self._binding(route, IntakeEntry.DECIDE_GOVERNANCE_SEAT, guard_command)
        self.assertIs(guard_refusal.exception.refusal_code, S015AuthorityRefusalCode.SBG_SEAT_OCCUPIED)
        self.assertEqual(guard_refusal.exception.request_uuid, request.request_uuid)
        self.assertIs(guard_refusal.exception.__cause__, guard_error)
        self.assertEqual(
            str(guard_refusal.exception),
            R_GOV_05.format(T=canonical_timestamp(guard_binding.received_at)),
        )
        self.assertNotIn(OPEN_REQUEST, str(guard_refusal.exception))
        completed_command = self._decision(
            route,
            request.request_uuid,
            "o01-completed-translation",
        )
        refusal = SBGGovernanceRefused(
            S015AuthorityRefusalCode.SBG_SEAT_ABSENT,
            "original completed refusal",
            request_uuid=request.request_uuid,
        )
        with patch.object(
            route.service,
            "_execute_decide_governance_seat",
            side_effect=refusal,
        ):
            with self.assertRaises(SBGGovernanceRefused) as completed_refusal:
                route.service.decide_governance_seat(
                    replace(
                        completed_command,
                        idempotency_key="clk3-g2:o01-completed-translation-2",
                    )
                )
        self.assertIs(
            completed_refusal.exception.refusal_code,
            refusal.refusal_code,
        )
        self.assertEqual(str(completed_refusal.exception), str(refusal))
        unknown_error = SBGGovernanceRefused(
            S015AuthorityRefusalCode.SBG_SEAT_ABSENT,
            "original error survives an UNKNOWN lookup",
            request_uuid=request.request_uuid,
        )
        prior_cause = RuntimeError("sentinel prior cause")
        unknown_error.__cause__ = prior_cause
        unknown_command = replace(
            completed_command,
            idempotency_key="clk3-g2:o01-unknown",
        )
        with (
            patch.object(
                route.service,
                "_execute_decide_governance_seat",
                side_effect=unknown_error,
            ),
            patch.object(route.service, "_request_open_state", return_value="UNKNOWN"),
        ):
            with self.assertRaises(SBGGovernanceRefused) as unknown_refusal:
                route.service.decide_governance_seat(unknown_command)
        self.assertIs(unknown_refusal.exception, unknown_error)
        self.assertIs(unknown_refusal.exception.__cause__, prior_cause)
        self.assertEqual(
            str(unknown_refusal.exception),
            "original error survives an UNKNOWN lookup",
        )
        # Case (2), real: stage 1 committed and stage 2 rolled back in its body; the sentence appears
        # once, inside B-09-S2's complete rendering, before the intake sentence.
        born = self._route(birthing=True)
        compound = self._compound(born, "o01-open")

        def fail():
            raise RuntimeError("injected for O-01's open case")

        raised = self._run_with_decision_hook(born.service.decide_governance_seat, compound, fail)
        open_binding = self._binding(born, IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND, compound)
        self.assertEqual(str(raised), B_09_S2.format(T=canonical_timestamp(open_binding.received_at)))
        self.assertEqual(str(raised).count(OPEN_REQUEST), 1)

    def _founding(self, route, tag):
        return SeatFoundingGovernanceCircleCommand(
            credential=route.founder.credential,
            living_organism_uuid=route.organism.organism_id,
            circle_uuid=route.circle_uuid,
            seat_rule_uuid=route.rule_uuid,
            seat_id="founder",
            request_reference=f"clk3-g2:{tag}:founding",
            idempotency_key=f"clk3-g2:{tag}:founding-key",
        )

    def _parents(self, tag):
        """EP-08 (born LO, compound decision) and EP-09 (founding before Birthing), each with its call."""
        born = self._route(birthing=True)
        forming = self._route()
        return (
            ("EP-08", born, self._compound(born, f"{tag}-ep08"), IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND,
             born.service.decide_governance_seat),
            ("EP-09", forming, self._founding(forming, f"{tag}-ep09"), IntakeEntry.SEAT_GOVERNANCE_CIRCLE,
             forming.service.seat_governance_circle),
        )

    def _binding(self, route, entry, command):
        return CommandIntake.objects.get(
            actor=route.founder,
            entry=entry.value,
            idempotency_key=command.idempotency_key,
        )

    def _assert_stage_one_only(self, before, after):
        self.assertEqual(
            _delta(before, after),
            {
                "core_commandintake": 1,
                "core_authoritydecision": 1,
                "core_circlejoinrequest": 1,
                "core_organismcommandreceipt": 1,
            },
        )

    def _assert_retry_completes_at_t(self, route, call, command, binding, failed_counts):
        result = call(command)
        self.assertIsNotNone(result.request_uuid)
        self.assertEqual(
            _delta(failed_counts, _public_table_counts()),
            {
                "core_authoritydecision": 1,
                "core_circleadmissiondecision": 1,
                "core_circleparticipation": 1,
                "core_organismcommandreceipt": 1,
            },
        )
        decision = CircleAdmissionDecision.objects.get(request__request_uuid=result.request_uuid)
        self.assertEqual(decision.received_at, binding.received_at)
        self.assertEqual(decision.effective_at, binding.received_at)

    def test_w_clk3_s2a_decision_stage_lock_timeout_renders_r_fix_01_s2_and_the_retry_completes_at_t(self):
        """S2a: contention at the decision stage's mutex renders R-FIX-01-S2; the retry completes at T (D §9.2)."""
        for parent, route, command, entry, call in self._parents("s2a"):
            with self.subTest(parent=parent):
                _request_key, _request_ref, admission_key, _admission_ref = _compound_stage_identifiers(
                    route.organism.organism_id,
                    command.idempotency_key,
                )
                gate_key = command_mutex_key(
                    actor_identity_id=route.founder.identity_id,
                    action=OrganismAction.ADMIT_CIRCLE_PARTICIPANT,
                    idempotency_key=admission_key,
                )
                before = _public_table_counts()

                def call_with_lock_timeout():
                    with connection.cursor() as cursor:
                        cursor.execute("SET lock_timeout = '200ms'")
                    return call(command)

                worker = None
                try:
                    with transaction.atomic():
                        with connection.cursor() as cursor:
                            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [gate_key])
                        worker = start_worker(call_with_lock_timeout)
                        worker.thread.join(timeout=30)
                except BaseException:
                    if worker is not None and worker.thread.is_alive():
                        cleanup_workers([worker])
                    raise
                outcome = worker.result(timeout=30)
                self.assertIsInstance(outcome, S015CommandContended)
                self.assertEqual(outcome.diagnostic_sqlstate, "55P03")
                self.assertIs(outcome.stage_context, ChildEntry.ADMIT_STAGE)
                binding = self._binding(route, entry, command)
                self.assertEqual(
                    str(outcome),
                    R_FIX_01_S2.format(T=canonical_timestamp(binding.received_at)),
                )
                failed_counts = _public_table_counts()
                self._assert_stage_one_only(before, failed_counts)
                self._assert_retry_completes_at_t(route, call, command, binding, failed_counts)

    def _run_with_decision_hook(self, call, command, hook):
        original_save = CircleAdmissionDecision.save
        hits = []

        def save_then_hook(instance, *args, **kwargs):
            result = original_save(instance, *args, **kwargs)
            if not hits:
                hits.append(True)
                hook()
            return result

        with patch.object(CircleAdmissionDecision, "save", save_then_hook):
            with self.assertRaises(Exception) as raised:
                call(command)
        self.assertEqual(hits, [True])
        return raised.exception

    def test_w_clk3_s2b_deferred_constraint_failure_at_the_decision_stage_commit_renders_b_09_s2(self):
        """S2b (P3'): a deferred foreign-key violation at the stage's COMMIT is a known rollback (D §9.2)."""
        route = self._route(birthing=True)
        command = self._compound(route, "s2b")
        entry = IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND
        before = _public_table_counts()

        def defer_a_violation():
            with connection.cursor() as cursor:
                cursor.execute(
                    "CREATE TEMP TABLE clk3_s2b_parent (id integer PRIMARY KEY) ON COMMIT DROP"
                )
                cursor.execute(
                    "CREATE TEMP TABLE clk3_s2b_child (parent_id integer NOT NULL, "
                    "CONSTRAINT clk3_s2b_deferred_fk FOREIGN KEY (parent_id) "
                    "REFERENCES clk3_s2b_parent(id) DEFERRABLE INITIALLY DEFERRED) ON COMMIT DROP"
                )
                cursor.execute("INSERT INTO clk3_s2b_child (parent_id) VALUES (1)")

        raised = self._run_with_decision_hook(
            route.service.decide_governance_seat,
            command,
            defer_a_violation,
        )
        self.assertIsInstance(raised, IntegrityError)
        original = raised.__cause__
        self.assertIsInstance(original, IntegrityError)
        self.assertEqual(getattr(original.__cause__, "sqlstate", None), "23503")
        self.assertIs(raised.stage_context, ChildEntry.ADMIT_STAGE)
        self.assertFalse(getattr(raised, "outcome_uncertain", False))
        binding = self._binding(route, entry, command)
        self.assertEqual(str(raised), B_09_S2.format(T=canonical_timestamp(binding.received_at)))
        failed_counts = _public_table_counts()
        self._assert_stage_one_only(before, failed_counts)
        self._assert_retry_completes_at_t(
            route, route.service.decide_governance_seat, command, binding, failed_counts,
        )

    def test_w_clk3_s2b_prime_body_failure_in_the_decision_stage_renders_b_09_s2(self):
        """S2b' (P2/P3): a body failure after the decision insert is a known rollback (D §9.2)."""
        route = self._route(birthing=True)
        command = self._compound(route, "s2b-prime")
        entry = IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND
        injected = RuntimeError("injected in the decision stage")
        before = _public_table_counts()

        def fail():
            raise injected

        raised = self._run_with_decision_hook(route.service.decide_governance_seat, command, fail)
        self.assertIsInstance(raised, RuntimeError)
        self.assertIsNot(raised, injected)
        self.assertIs(raised.__cause__, injected)
        self.assertIs(raised.stage_context, ChildEntry.ADMIT_STAGE)
        self.assertFalse(getattr(raised, "outcome_uncertain", False))
        binding = self._binding(route, entry, command)
        self.assertEqual(str(raised), B_09_S2.format(T=canonical_timestamp(binding.received_at)))
        failed_counts = _public_table_counts()
        self._assert_stage_one_only(before, failed_counts)
        self._assert_retry_completes_at_t(
            route, route.service.decide_governance_seat, command, binding, failed_counts,
        )

    def test_w_clk3_s2e_a_governed_denial_in_the_decision_stage_keeps_its_rendering_and_the_open_request_clause(self):
        """S2e (BN-3): a real deferred denial before Birthing (EP-08) is a governed outcome, not uncertain (D §9.2)."""
        route = self._route()
        command = self._compound(route, "s2e")
        entry = IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND
        before = _public_table_counts()
        with self.assertRaises(S015AuthorityDenied) as refused:
            route.service.decide_governance_seat(command)
        denial = refused.exception
        self.assertIs(denial.refusal_code, S015AuthorityRefusalCode.SBG_SEAT_ROUTE_UNAVAILABLE)
        self.assertIs(denial.stage_context, ChildEntry.ADMIT_STAGE)
        self.assertFalse(getattr(denial, "outcome_uncertain", False))
        self.assertIsNotNone(denial.request_uuid)
        binding = self._binding(route, entry, command)
        self.assertEqual(str(denial), R_GOV_13_S2.format(T=canonical_timestamp(binding.received_at)))
        self.assertEqual(str(denial).count(OPEN_REQUEST), 1)
        self.assertEqual(
            _delta(before, _public_table_counts()),
            {
                "core_commandintake": 1,
                "core_authoritydecision": 2,
                "core_circlejoinrequest": 1,
                "core_organismcommandreceipt": 1,
            },
        )
        self.assertFalse(
            CircleAdmissionDecision.objects.filter(request__request_uuid=denial.request_uuid).exists()
        )

    def test_step_c_activation_has_no_unaccounted_record_and_reports_an_injected_one(self):
        """Step C accounts for every G2 receipt and surfaces an injected orphan (D §6.3)."""
        route = self._route()
        route.service.seat_governance_circle(
            SeatFoundingGovernanceCircleCommand(
                credential=route.founder.credential,
                living_organism_uuid=route.organism.organism_id,
                circle_uuid=route.circle_uuid,
                seat_rule_uuid=route.rule_uuid,
                seat_id="founder",
                request_reference="clk3-g2:activation",
                idempotency_key="clk3-g2:activation-key",
            )
        )
        entries = (
            IntakeEntry.REQUEST_GOVERNANCE_SEAT,
            IntakeEntry.DECIDE_GOVERNANCE_SEAT,
            IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND,
            IntakeEntry.SEAT_GOVERNANCE_CIRCLE,
        )
        self.assertEqual(run_activation_step("default", entries), [])
        receipt_uuid = uuid4()
        key = f"clk3-g2:unaccounted:{receipt_uuid.hex}"
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
                """,
                [
                    receipt_uuid,
                    route.founder.pk,
                    OrganismAction.REQUEST_GOVERNANCE_SEAT.value,
                    key,
                    f"request:{receipt_uuid.hex}",
                    "b" * 64,
                    "s015d1:" + "c" * 64,
                    "s015l1:" + "d" * 64,
                    '{"fixture": true}',
                    database_now(),
                ],
            )
        records = run_activation_step("default", entries)
        self.assertEqual(
            {(row.kind, row.action, row.idempotency_key, row.actor_id) for row in records},
            {("receipt", OrganismAction.REQUEST_GOVERNANCE_SEAT.value, key, route.founder.pk)},
        )
