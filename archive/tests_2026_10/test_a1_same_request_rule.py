"""A-1 witnesses: the same-request rule for a compound governance seating's stage 2.

SEPARATE PART, SEQUENCED AFTER CHUNK 3 (LD return A-1 v1.0, R4). This module is
written against chunk 3's built files, which are not in the A-1 carrier. The
names below marked C3 are taken from the chunk 3 commission v0.4 and the
adopted design (D), not from a carried source file; the Vision Chamber confirms
each against chunk 3's result before this module is added:

  C3 names used here                                    where specified
  ----------------------------------------------------  -------------------------
  OrganismGovernanceService._orchestrate_compound_seating(command, bound, recipe)
                                                        C3:126-130; D:548-567
  OrganismGovernanceService._execute_decide_governance_seat(command, bound)
                                                        C3:47
  organism_governance_service._RECIPE_DECIDE_COMPOUND   C3:121-123; D:537-541
  organism_governance_service._RECIPE_SEAT_FOUNDING     C3:121-123; D:542-546
  command_intake.admit_request(alias, entry, actor_ref, shape)
                                                        CI:1395-1494; CH-22
  command_intake.intake_shape(entry, command) with the G2 branch chunk 3 adds
                                                        CI:1285-1309; C3 (G2)
  BoundRequest.for_test(entry, command, *, received_at, actor)
                                                        CI:135-175

The Vision Chamber confirmed these names against chunk 3 as committed
(5eee83e); CI and GOV line numbers in this module are at 5eee83e.

Everything else is carried at 8006f8c or in the U-FACTS-1 part-2 result:
_compound_stage_identifiers (GOV:588-619), the result classes (GOV:465-505,
532-568), the sbg fixtures (tests/sb2_route_support, tests/sbg_route_support,
as tests/test_sbg_governance.py imports them at sbg:77-106), and the exit
injection helper reproduced from sbg:109-128 (sbg is not edited, C3 E6).

Rules observed (C3:59, CH-30): every witness uses the production provider; T is
the intake row's database-assigned received_at for an admitted parent, or a
database_now() reading taken after setup for a test-only bound; no future-dated
T, no backdated row, no NOW rebinding; an identical retry keeps T, the key, the
reference and every material input; a separate EP-07 decision takes a new key,
reference and time. No witness wraps the orchestrator in an atomic block.

Expected outcomes are stated per witness for the BUILT provider (part-2 result
plus the A-1 blocks) and for the BASE provider (part-2 result alone), practice
item 92. The base outcomes are what the Advisor's A-1 finding predicts; they
are not claims that this module was run.

There is no secret to discover in INTEVIA. Test values that mimic credentials
are not secrets.
"""
import hashlib
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4, uuid5

from django.db import connection, transaction
from django.test import TransactionTestCase
import unittest

from core.models import (
    AuthorityDecision,
    CircleAdmissionDecision,
    CircleJoinRequest,
    CircleParticipation,
    CommandIntake,
    IntakeEntry,
    LivingOrganism,
    OrganismCommandReceipt,
    OrganismMembership,
)
from core.organism_contract import OrganismAction
from src.intevia.services.command_intake import (
    COMPOUND_ENTRIES,
    ENTRY_ACTIONS,
    ActorRef,
    BoundRequest,
    ChildEntry,
    admit_request,
    intake_shape,
)
from src.intevia.services.organism_application_authority import (
    ProductionApplicationAuthorityProvider,
    _COMPOUND_ADMISSION_KEY_PREFIX,
)
from src.intevia.services.organism_authority import (
    S015AuthorityDenied,
    S015AuthorityRefusalCode,
    canonical_json_bytes,
)
from src.intevia.services.organism_command_support import _authority_request, canonical_timestamp
from src.intevia.services.organism_governance_service import (
    DecideGovernanceSeatCommand,
    GovernanceSeatDecisionResult,
    GovernanceSeatingResult,
    OrganismGovernanceService,
    RequestGovernanceSeatCommand,
    SeatFoundingGovernanceCircleCommand,
    _RECIPE_DECIDE_COMPOUND,
    _RECIPE_SEAT_FOUNDING,
    _authority_target,
    _compound_stage_identifiers,
)
from tests.sb2_route_support import (
    birth,
    add_active_elector,
    database_now,
    effect_gateway,
    prepare_route,
    route_test_now,
)
from tests.sbg_route_support import (
    adopt_governance_arrangement,
    create_governance_circle,
    governance_arrangement_body,
    governance_route_state,
    prepare_governance_route,
)

ALIAS = "default"


def _public_table_counts():
    """Row counts of every public table (D §7.1a: +0 is asserted over every table)."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT c.relname FROM pg_catalog.pg_class c "
            "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') ORDER BY c.relname"
        )
        tables = [row[0] for row in cursor.fetchall()]
        counts = {}
        for table in tables:
            cursor.execute(f"SELECT count(*) FROM public.{connection.ops.quote_name(table)}")
            counts[table] = cursor.fetchone()[0]
    return counts
ADMIT = OrganismAction.ADMIT_CIRCLE_PARTICIPANT.value
REQUEST = OrganismAction.REQUEST_GOVERNANCE_SEAT.value


class _StageExitTechnicalFailure(RuntimeError):
    """A test-only technical failure raised at a stage's atomic exit.

    Not an IntegrityError, so _controlled_integrity_refusal never maps it
    (CH-24): the class propagates as a technical failure.
    """


def _atomic_failing_on_exit(error, *, fail_on=1, injection_reached=None):
    """sbg:109-128 reproduced: the fail_on-th authority_atomic raises `error`
    after its body completes, so the block's writes are rolled back."""
    exit_count = 0

    @contextmanager
    def fail_at_exit(database_alias, *, received_at=None, stage_phase=None):
        nonlocal exit_count
        exit_count += 1
        with transaction.atomic(using=database_alias):
            yield
            if exit_count == fail_on:
                if injection_reached is not None:
                    injection_reached.append(True)
                raise error

    return fail_at_exit


def _post_birthing_route(tag, *, enable=True):
    """sbg:2866-2917 reproduced as a function: a born LO with an adopted
    governance arrangement, a dormant GOVERNANCE circle and (enable) the
    governance gateway on; the founder is coordinator and decider."""
    route = prepare_route(tag=f"a1-{uuid4().hex}", governance_enabled=False)
    birth(route, suffix=f"a1:{tag}")
    service = OrganismGovernanceService(clock=database_now)
    effective_at = route_test_now(route)
    rule_uuid = uuid4()
    body = governance_arrangement_body(effective_from=effective_at)
    adoption = adopt_governance_arrangement(
        route, service, tag=f"a1:{tag}:adopt", body=body, rule_uuid=rule_uuid,
    )
    creation = create_governance_circle(route, service, tag=f"a1:{tag}:circle")
    if enable:
        effect_gateway(
            route.application_service,
            route.founder,
            route.organism,
            f"a1:{tag}:enable",
            OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
            {
                "cq_active": False,
                "governance_enabled": True,
                "inherit_portal_cq_settings": False,
                "require_cq_to_change_lo_settings": False,
                "require_cq_to_change_application_question": False,
            },
        )
    return SimpleNamespace(
        route=route,
        service=service,
        founder=route.founder,
        organism=route.organism,
        rule_uuid=rule_uuid,
        circle_uuid=creation.record_uuid,
        adoption=adoption,
    )


def _actor_ref(identity):
    return ActorRef(pk=identity.pk, identity_id=identity.identity_id, access_epoch=identity.access_epoch)


def _admit(entry, identity, command):
    """CH-22's test-only route: a real intake row through the module-level
    admit_request (autocommit; TransactionTestCase), returning the bound
    request whose received_at is the row's database-assigned time T."""
    return admit_request(ALIAS, entry, _actor_ref(identity), intake_shape(entry, command))


def _decide_compound_command(route, tag, *, seat_id="founder", outcome="ACCEPT", effective_at=None):
    """EP-08's shape: request_uuid None, circle and seat given (sbg:2919-2948)."""
    return DecideGovernanceSeatCommand(
        credential=route.founder.credential,
        living_organism_uuid=route.organism.organism_id,
        request_reference=f"a1:decision:{tag}",
        idempotency_key=f"a1:decision:key:{tag}",
        outcome=outcome,
        effective_at=route_test_now(route.route) if effective_at is None else effective_at,
        circle_uuid=route.circle_uuid,
        seat_rule_uuid=route.rule_uuid,
        seat_id=seat_id,
    )


def _stage_rows(route, interaction_key):
    """The stage identifiers and the rows a compound seating under
    (route.organism, interaction_key) writes or would write."""
    request_key, request_reference, admission_key, admission_reference = (
        _compound_stage_identifiers(route.organism.organism_id, interaction_key)
    )
    request_uuid = uuid5(route.organism.organism_id, f"{REQUEST}:{request_key}")
    return SimpleNamespace(
        request_key=request_key,
        request_reference=request_reference,
        admission_key=admission_key,
        admission_reference=admission_reference,
        request_uuid=request_uuid,
        request=lambda: CircleJoinRequest.objects.filter(request_uuid=request_uuid).first(),
        request_receipt=lambda: OrganismCommandReceipt.objects.filter(
            actor=route.founder, action=REQUEST, idempotency_key=request_key,
        ).first(),
        admission_receipt=lambda: OrganismCommandReceipt.objects.filter(
            actor=route.founder, action=ADMIT, idempotency_key=admission_key,
        ).first(),
        request_decisions=lambda: AuthorityDecision.objects.filter(
            actor=route.founder, request_reference=request_reference,
        ),
        admission_decisions=lambda: AuthorityDecision.objects.filter(
            actor=route.founder, request_reference=admission_reference,
        ),
    )


class A1SameRequestRuleNoDatabaseTests(unittest.TestCase):
    """W-A1-01: the rule's constants agree with the derivation it recognises.
    No database. BUILT: passes. BASE: fails at import
    (_COMPOUND_ADMISSION_KEY_PREFIX is undefined)."""

    def test_w_a1_01_prefix_and_entry_actions_match_the_derivation(self):
        organism_uuid = uuid4()
        request_key, request_reference, admission_key, admission_reference = (
            _compound_stage_identifiers(organism_uuid, "a1:no-db:key")
        )
        self.assertTrue(admission_key.startswith(_COMPOUND_ADMISSION_KEY_PREFIX))
        self.assertFalse(request_key.startswith(_COMPOUND_ADMISSION_KEY_PREFIX))
        self.assertFalse(request_reference.startswith(_COMPOUND_ADMISSION_KEY_PREFIX))
        self.assertFalse(admission_reference.startswith(_COMPOUND_ADMISSION_KEY_PREFIX))
        self.assertEqual(_COMPOUND_ADMISSION_KEY_PREFIX, "sbg:admit:")
        # the parent rows the rule looks for carry the admission action (ENTRY_ACTIONS, CI:381 on)
        for entry in COMPOUND_ENTRIES:
            self.assertEqual(ENTRY_ACTIONS[entry], ADMIT)
        # the stage-1 identity the rule recomputes is stage 1's own (GOV:1020-1021)
        self.assertEqual(
            uuid5(organism_uuid, f"{REQUEST}:{request_key}"),
            uuid5(organism_uuid, "REQUEST_GOVERNANCE_SEAT:" + request_key),
        )


class A1SameRequestRuleCompoundTests(TransactionTestCase):
    """Compound seatings through chunk 3's orchestrator with admitted parents
    (CH-22), stage 2 at the parent's T, the production provider."""

    def _assert_success_rows(self, route, stage, bound, result):
        request = stage.request()
        self.assertIsNotNone(request)
        self.assertEqual(request.received_at, bound.received_at)
        # the defect's premise, proven on the row the rule admitted
        self.assertGreater(request.recorded_at, bound.received_at)
        self.assertEqual(request.consent_reference, stage.request_reference)
        self.assertEqual(result.request_uuid, stage.request_uuid)
        admission = CircleAdmissionDecision.objects.get(request=request)
        self.assertEqual(admission.decision_uuid, result.decision_uuid)
        self.assertEqual(admission.received_at, bound.received_at)
        participation = CircleParticipation.objects.get(decision=admission)
        self.assertEqual(participation.participation_uuid, result.participation_uuid)
        self.assertIsNotNone(stage.request_receipt())
        self.assertIsNotNone(stage.admission_receipt())
        self.assertEqual(stage.request_decisions().filter(outcome="QUALIFIED").count(), 1)
        self.assertEqual(stage.admission_decisions().filter(outcome="QUALIFIED").count(), 1)
        self.assertEqual(
            stage.request_decisions().get(outcome="QUALIFIED").evaluated_at, bound.received_at,
        )
        self.assertEqual(
            stage.admission_decisions().get(outcome="QUALIFIED").evaluated_at, bound.received_at,
        )
        self.assertEqual(stage.admission_decisions().count(), 1)
        self.assertEqual(
            stage.admission_receipt().authority_decision_reference,
            stage.admission_decisions().get().decision_reference,
        )
        self.assertEqual(
            CommandIntake.objects.filter(actor_id=route.founder.pk, action=ADMIT).count(), 1,
        )

    def test_w_a1_02_ep08_after_birthing_stage_2_succeeds_at_t(self):
        """IW-01 for DECIDE_GOVERNANCE_SEAT_COMPOUND, and IW-00 (c)'s stage-2
        outcome. BUILT: GovernanceSeatDecisionResult; the request row's
        recorded_at > T; +1 intake, +2 receipts, +2 QUALIFIED decisions,
        +1 request, +1 admission decision, +1 participation. BASE: stage 2
        raises S015AuthorityDenied UNRECOGNISED_ACTION with the open-request
        sentence; stage 1's intake, receipt, decision and request survive;
        no admission decision or participation."""
        route = _post_birthing_route("w02")
        tag = f"w02-{uuid4().hex}"
        command = _decide_compound_command(route, tag)
        stage = _stage_rows(route, command.idempotency_key)
        before = governance_route_state(route)
        result = route.service.decide_governance_seat(command)
        bound = _admit(IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND, route.founder, command)
        self.assertFalse(bound.first)
        self.assertIsInstance(result, GovernanceSeatDecisionResult)
        self._assert_success_rows(route, stage, bound, result)
        after = governance_route_state(route)
        for key, delta in (("authority_decisions", 2), ("receipts", 2), ("join_requests", 1)):
            self.assertEqual(after[key], before[key] + delta, key)
        # only these three deltas of governance_route_state are asserted here;
        # its other keys are not compared. The rows the stages write are
        # checked separately by _assert_success_rows.

    def test_w_a1_03_ep09_before_birthing_founding_stage_2_succeeds_at_t(self):
        """IW-01 for SEAT_GOVERNANCE_CIRCLE (founding route, pre-Birthing).
        BUILT: GovernanceSeatingResult with a participation; the request row's
        recorded_at > T. BASE: stage 2 raises S015AuthorityDenied
        SBG_MEMBER_NOT_ELIGIBLE with the open-request sentence; stage 1
        survives; nobody is seated."""
        route = prepare_governance_route(seat_founder=False)
        tag = f"w03-{uuid4().hex}"
        command = SeatFoundingGovernanceCircleCommand(
            credential=route.founder.credential,
            living_organism_uuid=route.organism.organism_id,
            circle_uuid=route.circle_uuid,
            seat_rule_uuid=route.rule_uuid,
            seat_id="founder",
            request_reference=f"a1:seat:{tag}",
            idempotency_key=f"a1:seat:key:{tag}",
        )
        stage = _stage_rows(route, command.idempotency_key)
        before = governance_route_state(route)
        result = route.service.seat_governance_circle(command)
        bound = _admit(IntakeEntry.SEAT_GOVERNANCE_CIRCLE, route.founder, command)
        self.assertFalse(bound.first)
        self.assertIsInstance(result, GovernanceSeatingResult)
        self._assert_success_rows(route, stage, bound, result)
        after = governance_route_state(route)
        for key, delta in (("authority_decisions", 2), ("receipts", 2), ("join_requests", 1)):
            self.assertEqual(after[key], before[key] + delta, key)
        admission = CircleAdmissionDecision.objects.get(decision_uuid=result.decision_uuid)
        self.assertEqual(admission.route, "CIRCLE_FOUNDING")
        self.assertEqual(admission.deciding_party, "FOUNDING_BODY")

    def test_w_a1_07_replay_of_a_completed_compound_writes_nothing(self):
        """Replay (D §4.6 case 3; IW-25P (n)). BUILT and BASE alike once the
        first call has completed on BUILT: the identical request admits
        first=False at the same T, stage 1 and stage 2 replay by receipt, the
        same result is returned, no counted row is written, and the provider
        is not consulted: a guard on its qualify raises if it is called.

        D §7.1a: effective_at is unstated so that T enters both stage fingerprints; before the replay a
        later database reading is a bounded precondition; the replay is +0 over every table, both receipts'
        fingerprints equal recomputations with the intake row's T, and the replay viewers read fresh."""
        route = _post_birthing_route("w07")
        tag = f"w07-{uuid4().hex}"
        command = replace(_decide_compound_command(route, tag), effective_at=None)
        first = route.service.decide_governance_seat(command)
        bound = _admit(IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND, route.founder, command)
        self.assertFalse(bound.first)
        after_first = governance_route_state(route)
        intake_count = CommandIntake.objects.count()
        again = _admit(IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND, route.founder, command)
        self.assertFalse(again.first)
        self.assertEqual(again.received_at, bound.received_at)
        self.assertEqual(again.intake_id, bound.intake_id)
        request_key, request_reference, admission_key, admission_reference = (
            _compound_stage_identifiers(route.organism.organism_id, command.idempotency_key)
        )
        self.assertEqual(
            OrganismCommandReceipt.objects.get(
                actor=route.founder, action=REQUEST, idempotency_key=request_key,
            ).payload_fingerprint,
            hashlib.sha256(canonical_json_bytes({
                "action": REQUEST,
                "effective_at": bound.received_at,
                "idempotency_key": request_key,
                "organism_uuid": route.organism.organism_id,
                "payload": {
                    "circle_uuid": str(route.circle_uuid),
                    "seat_rule_uuid": str(route.rule_uuid),
                    "seat_id": command.seat_id,
                },
                "request_reference": request_reference,
                "schema": "sbg-governance-seat-request-v1",
            })).hexdigest(),
        )
        self.assertEqual(
            OrganismCommandReceipt.objects.get(
                actor=route.founder, action=ADMIT, idempotency_key=admission_key,
            ).payload_fingerprint,
            hashlib.sha256(canonical_json_bytes({
                "action": ADMIT,
                "effective_at": bound.received_at,
                "idempotency_key": admission_key,
                "organism_uuid": route.organism.organism_id,
                "outcome": command.outcome,
                "route": "GOVERNANCE_CONSTITUTIONAL",
                "request_uuid": first.request_uuid,
                "schema": "sbg-governance-seat-decision-v1",
            })).hexdigest(),
        )
        later = None
        for _ in range(3):
            observation = database_now()
            if observation > bound.received_at:
                later = observation
                break
        self.assertIsNotNone(later, "database clock did not advance; the witness cannot discriminate")
        viewer_times = []

        def viewer_clock():
            value = database_now()
            viewer_times.append(value)
            return value

        before_replay = _public_table_counts()
        with (
            patch.object(
                route.service._authority,
                "qualify",
                side_effect=AssertionError("completed replay must not requalify"),
            ) as qualify_guard,
            patch.object(route.service, "_clock", side_effect=viewer_clock),
        ):
            replay = route.service.decide_governance_seat(command)
        qualify_guard.assert_not_called()
        self.assertEqual(replay.request_uuid, first.request_uuid)
        self.assertEqual(replay.decision_uuid, first.decision_uuid)
        self.assertEqual(replay.participation_uuid, first.participation_uuid)
        self.assertEqual(governance_route_state(route), after_first)
        self.assertEqual(CommandIntake.objects.count(), intake_count)
        self.assertEqual(_public_table_counts(), before_replay)
        self.assertEqual(len(viewer_times), 2)
        for reading in viewer_times:
            self.assertGreater(reading, bound.received_at)

    def test_w_a1_08_iw16d_technical_failure_at_stage_2_exit_then_retry_at_t(self):
        """IW-16 (d) (D:1647; deferred by CH-32). A technical failure injected
        at stage 2's atomic exit, after a successful stage-2 body. BUILT: the
        failure propagates as its own class (not mapped); stage 1 persists
        (intake, request, receipt, QUALIFIED decision); stage 2's rows are
        rolled back; the identical retry admits first=False at the same T,
        replays stage 1 and completes stage 2 at T with the same request row
        (recorded_at > T). BASE: the injection point is never reached: stage 2
        refuses before its exit (UNRECOGNISED_ACTION), and the retry refuses
        again at T."""
        route = _post_birthing_route("w08")
        tag = f"w08-{uuid4().hex}"
        command = _decide_compound_command(route, tag)
        stage = _stage_rows(route, command.idempotency_key)
        before = governance_route_state(route)
        before_intake = CommandIntake.objects.count()
        reached = []
        with patch(
            "src.intevia.services.organism_governance_service.authority_atomic",
            new=_atomic_failing_on_exit(
                _StageExitTechnicalFailure("injected at stage 2's exit"),
                fail_on=2,
                injection_reached=reached,
            ),
        ):
            with self.assertRaises(_StageExitTechnicalFailure) as raised:
                route.service.decide_governance_seat(command)
        self.assertEqual(reached, [True])
        bound = _admit(IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND, route.founder, command)
        self.assertFalse(bound.first)
        # PKT-CLK-3 CLK3-3: the substitute replaces authority_atomic, so the cell is BODY (a known
        # rollback, D §9.2); the failure keeps its class and is re-raised with B-09-S2's text.
        self.assertEqual(raised.exception.__cause__.args, ("injected at stage 2's exit",))
        self.assertEqual(
            str(raised.exception),
            "ADMIT_CIRCLE_PARTICIPANT could not be completed because of a technical problem; no "
            "governed record was written by the decision stage. Your own request for seat founder is "
            "recorded and remains open. INTEVIA kept a record that this request was received at "
            f"{canonical_timestamp(bound.received_at)}; the same request can be sent again and is "
            "judged as of that time. If it persists, report it.",
        )
        self.assertEqual(CommandIntake.objects.count(), before_intake + 1)
        self.assertEqual(getattr(raised.exception, "stage_context", None), ChildEntry.ADMIT_STAGE)
        expected = before.copy()
        expected["authority_decisions"] += 1
        expected["receipts"] += 1
        expected["join_requests"] += 1
        self.assertEqual(governance_route_state(route), expected)
        self.assertIsNotNone(stage.request())
        self.assertIsNone(stage.admission_receipt())
        self.assertFalse(stage.admission_decisions().exists())
        self.assertFalse(CircleAdmissionDecision.objects.filter(request=stage.request()).exists())
        retry = _admit(IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND, route.founder, command)
        self.assertFalse(retry.first)
        self.assertEqual(retry.received_at, bound.received_at)
        result = route.service.decide_governance_seat(command)
        self.assertIsInstance(result, GovernanceSeatDecisionResult)
        self._assert_success_rows(route, stage, retry, result)
        self.assertEqual(stage.request_decisions().count(), 1)


class A1SameRequestRuleStandaloneTests(TransactionTestCase):
    """The standalone EP-07 decision and an unrelated later request: the
    recorded_at cut is unchanged for every request that is not a compound
    seating's own stage 1."""

    def _standalone_command(self, route, tag, request_uuid, effective_at):
        return DecideGovernanceSeatCommand(
            credential=route.founder.credential,
            living_organism_uuid=route.organism.organism_id,
            request_reference=f"a1:standalone:{tag}",
            idempotency_key=f"a1:standalone:key:{tag}",
            outcome="ACCEPT",
            effective_at=effective_at,
            request_uuid=request_uuid,
        )

    def test_w_a1_04_06_unrelated_later_request_refused_at_t_and_decided_at_a_new_time(self):
        """W-A1-04 then W-A1-06. An elector's seat request is received and
        recorded after T (its effective_at is T, so the GOV effective check
        passes and the provider's time cut decides). A standalone EP-07
        decision bound to T: BUILT and BASE refuse UNRECOGNISED_ACTION (the
        constitutional route's code; the request is not a fact at T). A
        separate EP-07 decision with a new key, reference and time after the
        request's recording (CH-30): BUILT and BASE succeed; one request row
        throughout."""
        route = _post_birthing_route("w04")
        applicant = add_active_elector(route.route, "a1-w04-applicant")
        t = database_now()
        requested = route.service.request_governance_seat(RequestGovernanceSeatCommand(
            credential=applicant.credential,
            living_organism_uuid=route.organism.organism_id,
            circle_uuid=route.circle_uuid,
            seat_rule_uuid=route.rule_uuid,
            seat_id="founder",
            request_reference=f"a1:request:w04:{uuid4().hex}",
            idempotency_key=f"a1:request:key:w04:{uuid4().hex}",
            effective_at=t,
        ))
        request = CircleJoinRequest.objects.get(request_uuid=requested.request_uuid)
        self.assertGreater(request.received_at, t)
        self.assertGreater(request.recorded_at, t)
        before = governance_route_state(route)
        at_t = self._standalone_command(route, f"w04-{uuid4().hex}", requested.request_uuid, t)
        bound_t = BoundRequest.for_test(
            IntakeEntry.DECIDE_GOVERNANCE_SEAT, at_t, received_at=t, actor=_actor_ref(route.founder),
        )
        with self.assertRaises(S015AuthorityDenied) as refused:
            route.service._execute_decide_governance_seat(at_t, bound_t)
        self.assertIs(refused.exception.refusal_code, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
        expected = before.copy()
        expected["authority_decisions"] += 1  # the REFUSED decision at T
        self.assertEqual(governance_route_state(route), expected)
        self.assertFalse(CircleAdmissionDecision.objects.filter(request=request).exists())
        # W-A1-06: a new key, reference and time, after the request's recording
        later = database_now()
        self.assertGreater(later, request.recorded_at)
        at_later = self._standalone_command(route, f"w06-{uuid4().hex}", requested.request_uuid, later)
        bound_later = BoundRequest.for_test(
            IntakeEntry.DECIDE_GOVERNANCE_SEAT, at_later, received_at=later, actor=_actor_ref(route.founder),
        )
        result = route.service._execute_decide_governance_seat(at_later, bound_later)
        self.assertIsInstance(result, GovernanceSeatDecisionResult)
        self.assertEqual(result.request_uuid, requested.request_uuid)
        self.assertTrue(CircleAdmissionDecision.objects.filter(request=request).exists())
        self.assertEqual(CircleJoinRequest.objects.filter(circle__circle_uuid=route.circle_uuid).count(), 1)


class A1SameRequestRuleDirectPredicateTests(TransactionTestCase):
    """W-A1-05: the predicate itself, called directly on the production
    provider with hand-built requests. A genuine compound seating is run
    first (W-A1-02's path) to obtain a real parent row and a real stage-1
    row. The genuine request is accepted and each variant is refused. The
    variants are adversarial negatives, not proof that each condition is
    necessary on its own: some fail more than one condition (an earlier T
    also fails the ordinary received_at cut; a later request can be refused
    at the candidate time cut before its identity is compared), and no
    second parent or other-organism fixture is created.

    Runtime assertions: (a) the genuine request; (b) the ordinary read;
    (c) to (g) the variants. Checked by source reasoning only, not
    witnessed: the pre-filter (proof 0) on its own; proof 3's
    consent_reference, enterer and received_at comparisons (isolating them
    would need a forged row, which CH-30 forbids); and two parents of one
    actor sharing T. Database assignment gives T its provenance, not
    uniqueness; the predicate considers every matching intake row and
    requires exactly one whose derived admission key and reference both
    equal the request's, so parents sharing T do not take each other's
    stage identity.

    BUILT: as asserted. BASE (a semantic prediction from the old predicate,
    not a run): this module does not import there
    (_COMPOUND_ADMISSION_KEY_PREFIX is undefined), and the setup's compound
    seating would refuse at stage 2 before any direct call; the genuine
    case (a) is the defect."""

    def _provider_request(
        self, route, *, idempotency_key, request_reference, evaluated_at, target, actor=None,
    ):
        actor = route.founder if actor is None else actor
        membership = OrganismMembership.objects.get(
            identity=actor, living_organism=route.organism,
        )
        return _authority_request(
            database_alias=ALIAS,
            actor=actor,
            actor_membership=membership,
            actor_role_head_refs=(),
            action=OrganismAction.ADMIT_CIRCLE_PARTICIPANT,
            command_subtype=ADMIT,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            received_at=evaluated_at,
            target_fingerprint="a1-direct-predicate",
            effective_at=evaluated_at,
            target=target,
            living_organism_id=route.organism.organism_id,
        )

    def _target(self, route, request_uuid):
        return _authority_target(
            OrganismAction.ADMIT_CIRCLE_PARTICIPANT,
            route.organism.organism_id,
            {
                "circle_uuid": str(route.circle_uuid),
                "seat_rule_uuid": str(route.rule_uuid),
                "seat_id": "founder",
                "kind": "GOVERNANCE",
                "route": "GOVERNANCE_CONSTITUTIONAL",
                "request_uuid": str(request_uuid),
                "applicant_identity_uuid": str(route.founder.identity_id),
                "constitutional_route": "APPOINTMENT",
                "deciding_party": "office:LO_COORDINATOR",
                "outcome": "ACCEPT",
            },
        )

    def test_w_a1_05_genuine_parent_and_adversarial_variants(self):
        route = _post_birthing_route("w05")
        # the second applicant's membership must be effective before T (F2)
        applicant = add_active_elector(route.route, "a1-w05-applicant")
        tag = f"w05-{uuid4().hex}"
        command = _decide_compound_command(route, tag)
        stage = _stage_rows(route, command.idempotency_key)
        bound = _admit(IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND, route.founder, command)
        route.service._orchestrate_compound_seating(command, bound, _RECIPE_DECIDE_COMPOUND)
        t = bound.received_at
        organism = LivingOrganism.objects.get(organism_id=route.organism.organism_id)
        provider = ProductionApplicationAuthorityProvider(database_alias=ALIAS)
        genuine = dict(
            idempotency_key=stage.admission_key,
            request_reference=stage.admission_reference,
            evaluated_at=t,
            target=self._target(route, stage.request_uuid),
        )

        def matches(**changes):
            values = {**genuine, **changes}
            request = self._provider_request(route, **values)
            return provider._governance_request_matches_target(request, organism, values["target"])

        # (a) the genuine stage-2 request: BUILT True (the same-request rule); BASE False
        self.assertTrue(matches())
        self.assertGreater(stage.request().recorded_at, t)
        # (b) the ordinary read still answers for a row recorded before evaluated_at (BUILT and BASE)
        self.assertTrue(matches(evaluated_at=stage.request().recorded_at + timedelta(microseconds=1)))
        # (c) proof 0/1: a key without the admission-stage prefix (fails the pre-filter, and proof 1)
        self.assertFalse(matches(idempotency_key=f"a1:standalone:key:{tag}"))
        # (d) proof 1: a reference that is not this parent's derived admission reference
        other = _compound_stage_identifiers(route.organism.organism_id, f"{command.idempotency_key}:other")
        self.assertFalse(matches(request_reference=other[3]))
        # (d') proof 1: a key derived from another interaction key (no parent row binds it)
        self.assertFalse(matches(idempotency_key=other[2], request_reference=other[3]))
        # (e) another time than the parent's T: no parent row at that time; this
        # also fails the ordinary received_at cut, so it does not isolate proof 1
        self.assertFalse(matches(evaluated_at=t - timedelta(microseconds=1)))
        # (e') proof 1: another actor (a member with no parent row; the actor is both the
        # intake row's actor and the stage-1 row's enterer)
        self.assertFalse(matches(actor=applicant))
        # (f) the target names another request than this parent's stage 1: proof 2
        # (the derived stage-1 identity) refuses it. It does not isolate proof 2:
        # without it, the final candidate query would still find nothing (its
        # request_uuid filter conflicts, and that request was received after T)
        other_request = route.service.request_governance_seat(RequestGovernanceSeatCommand(
            credential=applicant.credential,
            living_organism_uuid=route.organism.organism_id,
            circle_uuid=route.circle_uuid,
            seat_rule_uuid=route.rule_uuid,
            seat_id="founder",
            request_reference=f"a1:request:w05:{uuid4().hex}",
            idempotency_key=f"a1:request:key:w05:{uuid4().hex}",
            effective_at=t,
        ))
        forged_target = self._target(route, other_request.request_uuid)
        forged_target["applicant_identity_uuid"] = str(applicant.identity_id)
        self.assertFalse(matches(target=forged_target))
        # (g) proof 3: the genuine identity with a wrong applicant in the target finds no candidate
        wrong_applicant = self._target(route, stage.request_uuid)
        wrong_applicant["applicant_identity_uuid"] = str(applicant.identity_id)
        self.assertFalse(matches(target=wrong_applicant))
