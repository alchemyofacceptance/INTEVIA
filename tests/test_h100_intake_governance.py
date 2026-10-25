"""Chunk-3 governance stage and compound-intake witnesses."""

import hashlib
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import User
from django.db import IntegrityError, connection, transaction
from django.test import SimpleTestCase, TransactionTestCase, override_settings

from core.models import (
    AuthorityDecision,
    CircleAdmissionDecision,
    CircleJoinRequest,
    Identity,
    IntakeEntry,
    OrganismCommandReceipt,
)
from core.organism_contract import OrganismAction
from src.intevia.services.organism_command_support import canonical_timestamp
from src.intevia.services.command_intake import (
    _CONSTRUCTOR_SENTINEL,
    ActorRef,
    BoundRequest,
    ChildEntry,
    IntakeAdmissionTechnicalFailure,
    admit_request,
    intake_fingerprint,
    intake_shape,
)
from src.intevia.services.organism_authority import (
    S015AuthorityDenied,
    S015AuthorityRefusalCode,
    canonical_json_bytes,
)
from src.intevia.services.organism_governance_service import (
    DecideGovernanceSeatCommand,
    GovernanceSeatDecisionResult,
    GovernanceSeatingResult,
    RequestGovernanceSeatCommand,
    SBGGovernanceRefused,
    SBGGovernanceUnavailable,
    SeatFoundingGovernanceCircleCommand,
    _RECIPE_DECIDE_COMPOUND,
    _RECIPE_SEAT_FOUNDING,
    _compound_stage_identifiers,
    _founding_seating_result,
    _identity_decision_result,
)
from tests.sbg_route_support import (
    advance_to_birthing,
    database_now,
    prepare_governance_route,
)
from tests.sb2_route_support import add_active_elector, birth, effect_gateway


def _parent_command(route, entry, *, tag, effective_at):
    common = {
        "credential": route.founder.credential,
        "living_organism_uuid": route.organism.organism_id,
        "circle_uuid": route.circle_uuid,
        "seat_rule_uuid": route.rule_uuid,
        "seat_id": "founder",
        "request_reference": f"c3:parent:ref:{tag}",
        "idempotency_key": f"c3:parent:key:{tag}",
        "effective_at": effective_at,
    }
    if entry is IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND:
        return DecideGovernanceSeatCommand(
            **{
                **common,
                "outcome": "ACCEPT",
                "route": None,
                "request_uuid": None,
            }
        )
    if entry is IntakeEntry.SEAT_GOVERNANCE_CIRCLE:
        return SeatFoundingGovernanceCircleCommand(**common)
    raise ValueError("unsupported compound parent entry")


def _stage_commands(command):
    organism_uuid = command.living_organism_uuid
    request_key, request_reference, admission_key, admission_reference = (
        _compound_stage_identifiers(organism_uuid, command.idempotency_key)
    )
    request = RequestGovernanceSeatCommand(
        credential=command.credential,
        living_organism_uuid=organism_uuid,
        circle_uuid=command.circle_uuid,
        seat_rule_uuid=command.seat_rule_uuid,
        seat_id=command.seat_id,
        request_reference=request_reference,
        idempotency_key=request_key,
        effective_at=command.effective_at,
    )
    outcome = "ACCEPT" if type(command) is SeatFoundingGovernanceCircleCommand else command.outcome
    route = (
        "CIRCLE_FOUNDING"
        if type(command) is SeatFoundingGovernanceCircleCommand
        else "GOVERNANCE_CONSTITUTIONAL"
    )
    decision = DecideGovernanceSeatCommand(
        credential=command.credential,
        living_organism_uuid=organism_uuid,
        request_reference=admission_reference,
        idempotency_key=admission_key,
        outcome=outcome,
        route=route,
        request_uuid=uuid4(),
        effective_at=command.effective_at,
    )
    return request, decision


def _actor_ref(identity):
    return ActorRef(
        pk=identity.pk,
        identity_id=identity.identity_id,
        access_epoch=identity.access_epoch,
    )


def _shape_fingerprint(entry, actor_ref, shape):
    return intake_fingerprint(
        entry=entry,
        action=shape.action
        or (
            "REQUEST_GOVERNANCE_SEAT"
            if entry is IntakeEntry.REQUEST_GOVERNANCE_SEAT
            else "ADMIT_CIRCLE_PARTICIPANT"
        ),
        actor_identity_id=actor_ref.identity_id,
        actor_access_epoch=actor_ref.access_epoch,
        request_reference=shape.request_reference,
        idempotency_key=shape.idempotency_key,
        target=shape.target,
        command=shape.command,
        omitted=shape.omitted,
    )


def _replace_bound_fixture(bound, **changes):
    return replace(
        bound,
        _sentinel=_CONSTRUCTOR_SENTINEL,
        **changes,
    )


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


def _row_delta(before, after):
    return {
        table: after[table] - count
        for table, count in before.items()
        if after[table] != count
    }


@override_settings(INTEVIA_TEST_CONTEXT=True)
class GovernanceProjectionAndChildTests(SimpleTestCase):
    """Exercise pure G2 projections, child derivation and served-set verification."""

    def setUp(self):
        self.credential = User(username="h100-c3-projection")
        self.actor_ref = ActorRef(pk=31, identity_id=uuid4(), access_epoch=6)
        self.identity = Identity(
            pk=self.actor_ref.pk,
            identity_id=self.actor_ref.identity_id,
            access_epoch=self.actor_ref.access_epoch,
        )
        self.organism_uuid = uuid4()
        self.circle_uuid = uuid4()
        self.rule_uuid = uuid4()
        self.now = datetime.now(timezone.utc)

    def _parents(self):
        decide = DecideGovernanceSeatCommand(
            credential=self.credential,
            living_organism_uuid=self.organism_uuid,
            circle_uuid=self.circle_uuid,
            seat_rule_uuid=self.rule_uuid,
            seat_id="founder",
            request_reference="c3:decide:parent-ref",
            idempotency_key="c3:decide:parent-key",
            outcome="ACCEPT",
            route=None,
            request_uuid=None,
            effective_at=None,
        )
        founding = SeatFoundingGovernanceCircleCommand(
            credential=self.credential,
            living_organism_uuid=self.organism_uuid,
            circle_uuid=self.circle_uuid,
            seat_rule_uuid=self.rule_uuid,
            seat_id="founder",
            request_reference="c3:founding:parent-ref",
            idempotency_key="c3:founding:parent-key",
            effective_at=self.now,
        )
        return (
            (IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND, decide),
            (IntakeEntry.SEAT_GOVERNANCE_CIRCLE, founding),
        )

    def test_four_governance_shapes_and_effective_time_projection(self):
        request = RequestGovernanceSeatCommand(
            credential=self.credential,
            living_organism_uuid=self.organism_uuid,
            circle_uuid=self.circle_uuid,
            seat_rule_uuid=self.rule_uuid,
            seat_id="founder",
            request_reference="c3:request:ref",
            idempotency_key="c3:request:key",
        )
        standalone = DecideGovernanceSeatCommand(
            credential=self.credential,
            living_organism_uuid=self.organism_uuid,
            request_reference="c3:decision:ref",
            idempotency_key="c3:decision:key",
            outcome="DECLINE",
            route="CIRCLE_FOUNDING",
            request_uuid=uuid4(),
        )
        compound_entry, compound = self._parents()[0]
        founding_entry, founding = self._parents()[1]

        request_shape = intake_shape(IntakeEntry.REQUEST_GOVERNANCE_SEAT, request)
        standalone_shape = intake_shape(IntakeEntry.DECIDE_GOVERNANCE_SEAT, standalone)
        compound_shape = intake_shape(compound_entry, compound)
        founding_shape = intake_shape(founding_entry, founding)

        self.assertEqual(
            set(request_shape.target),
            {"living_organism_uuid", "circle_uuid", "seat_rule_uuid", "seat_id"},
        )
        self.assertEqual(set(standalone_shape.target), {"living_organism_uuid", "request_uuid"})
        self.assertEqual(
            set(compound_shape.target),
            {"living_organism_uuid", "circle_uuid", "seat_rule_uuid", "seat_id"},
        )
        self.assertEqual(compound_shape.target_organism_id, self.organism_uuid)
        self.assertEqual(founding_shape.target_organism_id, self.organism_uuid)
        self.assertEqual(standalone_shape.target_organism_id, None)
        self.assertEqual(
            set(standalone_shape.command),
            {"outcome", "route", "effective_at"},
        )
        self.assertEqual(request_shape.omitted, ())
        self.assertEqual(standalone_shape.omitted, ())
        self.assertEqual(compound_shape.omitted, ())
        self.assertEqual(founding_shape.omitted, ())
        for entry, command in (
            (IntakeEntry.REQUEST_GOVERNANCE_SEAT, request),
            (IntakeEntry.DECIDE_GOVERNANCE_SEAT, standalone),
            (compound_entry, compound),
            (founding_entry, founding),
        ):
            shape = intake_shape(entry, command)
            original_fingerprint = _shape_fingerprint(entry, self.actor_ref, shape)
            self.assertEqual(
                original_fingerprint,
                _shape_fingerprint(entry, self.actor_ref, intake_shape(entry, command)),
            )
            changes = {}
            if entry is IntakeEntry.REQUEST_GOVERNANCE_SEAT:
                changes.update(
                    living_organism_uuid=uuid4(),
                    circle_uuid=uuid4(),
                    seat_rule_uuid=uuid4(),
                    seat_id="changed-seat",
                    effective_at=self.now - timedelta(seconds=1),
                )
            elif entry is IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND:
                changes.update(
                    living_organism_uuid=uuid4(),
                    circle_uuid=uuid4(),
                    seat_rule_uuid=uuid4(),
                    seat_id="changed-seat",
                    outcome="DECLINE" if command.outcome == "ACCEPT" else "ACCEPT",
                    route="GOVERNANCE_CONSTITUTIONAL",
                    effective_at=self.now - timedelta(seconds=1),
                )
            elif entry is IntakeEntry.SEAT_GOVERNANCE_CIRCLE:
                changes.update(
                    living_organism_uuid=uuid4(),
                    circle_uuid=uuid4(),
                    seat_rule_uuid=uuid4(),
                    seat_id="changed-seat",
                    effective_at=self.now - timedelta(seconds=1),
                )
            else:
                changes.update(
                    living_organism_uuid=uuid4(),
                    request_uuid=uuid4(),
                    outcome="ACCEPT" if command.outcome == "DECLINE" else "DECLINE",
                    route="GOVERNANCE_CONSTITUTIONAL"
                    if command.route != "GOVERNANCE_CONSTITUTIONAL"
                    else "CIRCLE_FOUNDING",
                    effective_at=self.now - timedelta(seconds=1),
                )
            for field, value in changes.items():
                with self.subTest(entry=entry, field=field):
                    changed = replace(command, **{field: value})
                    changed_shape = intake_shape(entry, changed)
                    self.assertNotEqual(
                        original_fingerprint,
                        _shape_fingerprint(entry, self.actor_ref, changed_shape),
                    )
        invalid_standalone = replace(
            standalone,
            circle_uuid=self.circle_uuid,
            seat_rule_uuid=self.rule_uuid,
            seat_id="founder",
        )
        with self.assertRaisesRegex(
            ValueError,
            "an existing request supplies its own circle and seat",
        ):
            intake_shape(IntakeEntry.DECIDE_GOVERNANCE_SEAT, invalid_standalone)

        explicit = replace(standalone, effective_at=self.now)
        explicit_shape = intake_shape(IntakeEntry.DECIDE_GOVERNANCE_SEAT, explicit)
        self.assertNotEqual(
            intake_fingerprint(
                entry=IntakeEntry.DECIDE_GOVERNANCE_SEAT,
                action="ADMIT_CIRCLE_PARTICIPANT",
                actor_identity_id=self.actor_ref.identity_id,
                actor_access_epoch=self.actor_ref.access_epoch,
                request_reference=standalone_shape.request_reference,
                idempotency_key=standalone_shape.idempotency_key,
                target=standalone_shape.target,
                command=standalone_shape.command,
                omitted=standalone_shape.omitted,
            ),
            intake_fingerprint(
                entry=IntakeEntry.DECIDE_GOVERNANCE_SEAT,
                action="ADMIT_CIRCLE_PARTICIPANT",
                actor_identity_id=self.actor_ref.identity_id,
                actor_access_epoch=self.actor_ref.access_epoch,
                request_reference=explicit_shape.request_reference,
                idempotency_key=explicit_shape.idempotency_key,
                target=explicit_shape.target,
                command=explicit_shape.command,
                omitted=explicit_shape.omitted,
            ),
        )

    def test_both_parent_recipes_derive_both_children_and_served_sets(self):
        for parent_entry, command in self._parents():
            parent = BoundRequest.for_test(
                parent_entry,
                command,
                received_at=self.now,
                actor=self.actor_ref,
            ).verify_content(command, parent_entry)
            request, decision = _stage_commands(command)
            for child_entry, stage_command, served, other_served in (
                (
                    ChildEntry.REQUEST_STAGE,
                    request,
                    frozenset(
                        {
                            IntakeEntry.REQUEST_GOVERNANCE_SEAT,
                            ChildEntry.REQUEST_STAGE,
                        }
                    ),
                    frozenset(
                        {
                            IntakeEntry.DECIDE_GOVERNANCE_SEAT,
                            ChildEntry.ADMIT_STAGE,
                        }
                    ),
                ),
                (
                    ChildEntry.ADMIT_STAGE,
                    decision,
                    frozenset(
                        {
                            IntakeEntry.DECIDE_GOVERNANCE_SEAT,
                            ChildEntry.ADMIT_STAGE,
                        }
                    ),
                    frozenset(
                        {
                            IntakeEntry.REQUEST_GOVERNANCE_SEAT,
                            ChildEntry.REQUEST_STAGE,
                        }
                    ),
                ),
            ):
                with self.subTest(parent=parent_entry, child=child_entry):
                    child = parent.child(child_entry, command=stage_command)
                    public_entry = (
                        IntakeEntry.REQUEST_GOVERNANCE_SEAT
                        if child_entry is ChildEntry.REQUEST_STAGE
                        else IntakeEntry.DECIDE_GOVERNANCE_SEAT
                    )
                    public_bound = BoundRequest.for_test(
                        public_entry,
                        stage_command,
                        received_at=self.now,
                        actor=self.actor_ref,
                    )
                    verified = child.verify_content(stage_command, served)
                    self.assertTrue(verified.content_verified)
                    self.assertIs(verified.parent, parent)
                    expected_keys = _compound_stage_identifiers(
                        command.living_organism_uuid,
                        command.idempotency_key,
                    )
                    expected_key, expected_reference = (
                        expected_keys[0:2]
                        if child_entry is ChildEntry.REQUEST_STAGE
                        else expected_keys[2:4]
                    )
                    self.assertEqual(verified.action, child.action)
                    self.assertEqual(
                        verified.action,
                        "REQUEST_GOVERNANCE_SEAT"
                        if child_entry is ChildEntry.REQUEST_STAGE
                        else "ADMIT_CIRCLE_PARTICIPANT",
                    )
                    self.assertEqual(verified.idempotency_key, expected_key)
                    self.assertEqual(verified.request_reference, expected_reference)
                    self.assertEqual(verified.intake_id, parent.intake_id)
                    self.assertEqual(verified.received_at, parent.received_at)
                    self.assertEqual(verified.actor_identity_id, parent.actor_identity_id)
                    self.assertEqual(verified.actor_pk, parent.actor_pk)
                    self.assertEqual(verified.actor_access_epoch, parent.actor_access_epoch)
                    self.assertEqual(verified.target_organism_id, parent.target_organism_id)
                    self.assertNotEqual(
                        verified.request_fingerprint,
                        public_bound.request_fingerprint,
                    )
                    self.assertTrue(
                        public_bound.verify_content(stage_command, served).content_verified
                    )
                    verified.verify(stage_command, self.identity, served, "default")
                    with self.assertRaises(IntakeAdmissionTechnicalFailure):
                        child.verify_content(stage_command, other_served)
                    changed_stage = (
                        replace(stage_command, seat_id="changed-seat")
                        if child_entry is ChildEntry.REQUEST_STAGE
                        else replace(
                            stage_command,
                            outcome="DECLINE"
                            if stage_command.outcome == "ACCEPT"
                            else "ACCEPT",
                        )
                    )
                    with self.assertRaises(IntakeAdmissionTechnicalFailure):
                        child.verify_content(changed_stage, served)

    def test_compound_projection_fingerprint_is_bound_to_its_entry(self):
        compound_entry, compound = self._parents()[0]
        founding_entry, founding = self._parents()[1]
        compound_shape = intake_shape(compound_entry, compound)
        founding_shape = intake_shape(founding_entry, founding)
        self.assertNotEqual(
            _shape_fingerprint(compound_entry, self.actor_ref, compound_shape),
            _shape_fingerprint(founding_entry, self.actor_ref, compound_shape),
        )
        self.assertNotEqual(
            _shape_fingerprint(founding_entry, self.actor_ref, founding_shape),
            _shape_fingerprint(compound_entry, self.actor_ref, founding_shape),
        )

    def test_recipe_result_conversions_keep_the_established_result_types(self):
        result = GovernanceSeatDecisionResult(
            living_organism_uuid=uuid4(),
            request_uuid=uuid4(),
            decision_uuid=uuid4(),
            participation_uuid=uuid4(),
            lineage_reference="lineage",
            authority_reference="authority",
            authority_decision_reference="authority-decision",
        )
        self.assertIs(_identity_decision_result(result), result)
        self.assertEqual(
            _founding_seating_result(result),
            GovernanceSeatingResult(
                living_organism_uuid=result.living_organism_uuid,
                request_uuid=result.request_uuid,
                decision_uuid=result.decision_uuid,
                participation_uuid=result.participation_uuid,
                lineage_reference=result.lineage_reference,
                authority_reference=result.authority_reference,
                authority_decision_reference=result.authority_decision_reference,
            ),
        )
        with self.assertRaises(RuntimeError):
            _founding_seating_result(replace(result, participation_uuid=None))

    def test_child_requires_verified_allowed_parent_and_unchanged_child_content(self):
        entry, command = self._parents()[0]
        parent = BoundRequest.for_test(
            entry,
            command,
            received_at=self.now,
            actor=self.actor_ref,
        )
        request, _ = _stage_commands(command)
        with self.assertRaises(IntakeAdmissionTechnicalFailure):
            parent.child(ChildEntry.REQUEST_STAGE, command=request)

        verified_parent = parent.verify_content(command, entry)
        with self.assertRaises(IntakeAdmissionTechnicalFailure):
            verified_parent.verify_content(
                command,
                IntakeEntry.SEAT_GOVERNANCE_CIRCLE,
            )
        request_child = verified_parent.child(ChildEntry.REQUEST_STAGE, command=request)
        changed_request = replace(request, seat_id="another-seat")
        with self.assertRaises(IntakeAdmissionTechnicalFailure):
            request_child.verify_content(
                changed_request,
                frozenset(
                    {
                        IntakeEntry.REQUEST_GOVERNANCE_SEAT,
                        ChildEntry.REQUEST_STAGE,
                    }
                ),
            )
        unallowed = _replace_bound_fixture(
            verified_parent,
            entry=IntakeEntry.REQUEST_GOVERNANCE_SEAT,
        )
        with self.assertRaises(IntakeAdmissionTechnicalFailure):
            unallowed.child(ChildEntry.REQUEST_STAGE, command=request)

    def test_v5_epoch_refusal_covers_four_parent_entries_and_two_children(self):
        request = RequestGovernanceSeatCommand(
            credential=self.credential,
            living_organism_uuid=self.organism_uuid,
            circle_uuid=self.circle_uuid,
            seat_rule_uuid=self.rule_uuid,
            seat_id="founder",
            request_reference="c3:v5:request-ref",
            idempotency_key="c3:v5:request-key",
        )
        standalone = DecideGovernanceSeatCommand(
            credential=self.credential,
            living_organism_uuid=self.organism_uuid,
            request_reference="c3:v5:decision-ref",
            idempotency_key="c3:v5:decision-key",
            outcome="DECLINE",
            route="CIRCLE_FOUNDING",
            request_uuid=uuid4(),
        )
        cases = [
            (IntakeEntry.REQUEST_GOVERNANCE_SEAT, request),
            (IntakeEntry.DECIDE_GOVERNANCE_SEAT, standalone),
            *self._parents(),
        ]
        changed_epoch_actor = Identity(
            pk=self.identity.pk,
            identity_id=self.identity.identity_id,
            access_epoch=self.actor_ref.access_epoch + 1,
        )
        for entry, command in cases:
            bound = BoundRequest.for_test(
                entry,
                command,
                received_at=self.now,
                actor=self.actor_ref,
            )
            with self.subTest(entry=entry):
                with self.assertRaises(SBGGovernanceUnavailable):
                    bound.verify(
                        command,
                        changed_epoch_actor,
                        entry,
                        "default",
                    )
        parent_entry, parent_command = self._parents()[0]
        parent = BoundRequest.for_test(
            parent_entry,
            parent_command,
            received_at=self.now,
            actor=self.actor_ref,
        ).verify_content(parent_command, parent_entry)
        stage_request, stage_decision = _stage_commands(parent_command)
        for child_entry, child_command in (
            (ChildEntry.REQUEST_STAGE, stage_request),
            (ChildEntry.ADMIT_STAGE, stage_decision),
        ):
            child = parent.child(child_entry, command=child_command)
            with self.subTest(entry=child_entry):
                with self.assertRaises(SBGGovernanceUnavailable):
                    child.verify(
                        child_command,
                        changed_epoch_actor,
                        frozenset({child_entry}),
                        "default",
                    )


class GovernanceStageDatabaseTests(TransactionTestCase):
    """Exercise real PostgreSQL admission, stage execution and surviving writes."""

    ISOLATION_FULL_CAPTURE = True

    def _route(self, *, governance_enabled=True, birthing=False):
        route = prepare_governance_route(
            governance_enabled=governance_enabled,
            seat_founder=False,
        )
        if birthing:
            advance_to_birthing(route.route, tag=f"{route.route.tag}:c3-phase")
            birth(route.route, suffix="c3-birth")
        return route

    def _parent(self, route, entry=IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND, *, suffix):
        command = _parent_command(
            route,
            entry,
            tag=f"{route.route.tag}:{suffix}",
            effective_at=None,
        )
        bound = admit_request(
            "default",
            entry,
            _actor_ref(route.founder),
            intake_shape(entry, command),
        )
        return command, bound

    def test_directly_admitted_parents_refuse_the_other_recipe_before_stage_one(self):
        for parent_entry, correct, wrong in (
            (
                IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND,
                _RECIPE_DECIDE_COMPOUND,
                _RECIPE_SEAT_FOUNDING,
            ),
            (
                IntakeEntry.SEAT_GOVERNANCE_CIRCLE,
                _RECIPE_SEAT_FOUNDING,
                _RECIPE_DECIDE_COMPOUND,
            ),
        ):
            route = self._route()
            command, bound = self._parent(route, parent_entry, suffix="wrong-recipe")
            before = _public_row_counts()
            with self.subTest(parent=parent_entry):
                with self.assertRaises(IntakeAdmissionTechnicalFailure):
                    route.service._orchestrate_compound_seating(command, bound, wrong)
                self.assertEqual(_public_row_counts(), before)
                self.assertIsNotNone(correct)

    def test_changed_compound_seat_id_refuses_before_stage_one(self):
        route = self._route()
        command, bound = self._parent(
            route,
            IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND,
            suffix="changed-seat",
        )
        before = _public_row_counts()
        with self.assertRaises(IntakeAdmissionTechnicalFailure):
            route.service._orchestrate_compound_seating(
                replace(command, seat_id="changed-seat"),
                bound,
                _RECIPE_DECIDE_COMPOUND,
            )
        self.assertEqual(_public_row_counts(), before)

    def test_v6_parent_row_passes_and_tampered_expected_parent_refuses(self):
        for parent_entry in (
            IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND,
            IntakeEntry.SEAT_GOVERNANCE_CIRCLE,
        ):
            route = self._route(
                birthing=parent_entry
                is IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND
            )
            command, bound = self._parent(
                route,
                parent_entry,
                suffix=f"v6-positive-{parent_entry.value.lower()}",
            )
            verified_parent = bound.verify_content(command, parent_entry)
            request_command, decision_command = _stage_commands(command)
            request_child = verified_parent.child(
                ChildEntry.REQUEST_STAGE,
                command=request_command,
            )
            before_stage_one = _public_row_counts()
            result = route.service._execute_request_governance_seat(
                request_command,
                request_child,
            )
            self.assertEqual(
                CircleJoinRequest.objects.get(request_uuid=result.request_uuid).received_at,
                bound.received_at,
            )
            self.assertEqual(
                _row_delta(before_stage_one, _public_row_counts()),
                {
                    "core_authoritydecision": 1,
                    "core_circlejoinrequest": 1,
                    "core_organismcommandreceipt": 1,
                },
            )
            decision_command = replace(
                decision_command,
                request_uuid=result.request_uuid,
            )
            decision_child = verified_parent.child(
                ChildEntry.ADMIT_STAGE,
                command=decision_command,
            )
            before_stage_two = _public_row_counts()
            stop = SBGGovernanceRefused(
                S015AuthorityRefusalCode.SBG_SEAT_ABSENT,
                "stop after parent-row verification",
            )
            if parent_entry is IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND:
                validator = "_validate_constitutional_seating"
            else:
                validator = "_validate_seating"
            with patch.object(route.service, validator, side_effect=stop):
                with self.assertRaises(SBGGovernanceRefused):
                    route.service._execute_decide_governance_seat(
                        decision_command,
                        decision_child,
                    )
            self.assertEqual(_row_delta(before_stage_two, _public_row_counts()), {})

        second_command, second_bound = self._parent(
            route,
            IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND,
            suffix="v6-negative",
        )
        verified_second = second_bound.verify_content(
            second_command,
            IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND,
        )
        second_request, _ = _stage_commands(second_command)
        altered_parent = _replace_bound_fixture(
            verified_second,
            request_fingerprint="0" * 64,
        )
        altered_child = altered_parent.child(
            ChildEntry.REQUEST_STAGE,
            command=second_request,
        )
        before = _public_row_counts()
        with self.assertRaises(IntakeAdmissionTechnicalFailure):
            route.service._execute_request_governance_seat(
                second_request,
                altered_child,
            )
        self.assertEqual(_public_row_counts(), before)

    def test_v7_unallowed_child_fixture_refuses_without_writes(self):
        route = self._route()
        command, bound = self._parent(
            route,
            IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND,
            suffix="v7-child",
        )
        parent = bound.verify_content(
            command,
            IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND,
        )
        request_command, _ = _stage_commands(command)
        child = parent.child(ChildEntry.REQUEST_STAGE, command=request_command)
        unallowed_parent = _replace_bound_fixture(
            child.parent,
            entry=IntakeEntry.REQUEST_GOVERNANCE_SEAT,
        )
        unallowed_child = _replace_bound_fixture(child, parent=unallowed_parent)
        before = _public_row_counts()
        with self.assertRaises(IntakeAdmissionTechnicalFailure):
            route.service._execute_request_governance_seat(
                request_command,
                unallowed_child,
            )
        self.assertEqual(_public_row_counts(), before)

    def test_stage_one_uses_bound_time_and_replay_keeps_the_original_coordinates(self):
        route = self._route(governance_enabled=False, birthing=True)
        command = _parent_command(
            route,
            IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND,
            tag=f"{route.route.tag}:iw16-replay",
            effective_at=None,
        )
        before = _public_row_counts()
        verify_calls = []
        boundary_calls = []
        original_verify = BoundRequest.verify
        original_request_executor = route.service._execute_request_governance_seat
        original_decision_executor = route.service._execute_decide_governance_seat

        def recording_verify(bound_request, stage_command, actor, served, alias):
            verify_calls.append((bound_request.entry, served, bound_request.received_at))
            return original_verify(bound_request, stage_command, actor, served, alias)

        def recording_request_executor(*args, **kwargs):
            boundary_calls.append(connection.in_atomic_block)
            return original_request_executor(*args, **kwargs)

        def recording_decision_executor(*args, **kwargs):
            boundary_calls.append(connection.in_atomic_block)
            return original_decision_executor(*args, **kwargs)

        with (
            patch.object(BoundRequest, "verify", recording_verify),
            patch.object(
                route.service,
                "_execute_request_governance_seat",
                recording_request_executor,
            ),
            patch.object(
                route.service,
                "_execute_decide_governance_seat",
                recording_decision_executor,
            ),
            patch.object(route.service, "_clock", side_effect=AssertionError("clock read")),
        ):
            with self.assertRaises(SBGGovernanceRefused) as first:
                route.service.decide_governance_seat(command)
        bound = self._parent(
            route,
            IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND,
            suffix="iw16-replay",
        )[1]
        self.assertEqual(first.exception.refusal_code, S015AuthorityRefusalCode.SBG_SEATING_WHILE_DISABLED)
        self.assertEqual(first.exception.request_uuid, CircleJoinRequest.objects.latest("pk").request_uuid)
        self.assertIs(first.exception.stage_context, ChildEntry.ADMIT_STAGE)
        request = CircleJoinRequest.objects.get(request_uuid=first.exception.request_uuid)
        self.assertEqual(request.received_at, bound.received_at)
        self.assertEqual(request.effective_at, bound.received_at)
        self.assertEqual(
            AuthorityDecision.objects.filter(evaluated_at=bound.received_at).count(),
            1,
        )
        self.assertEqual(
            _row_delta(before, _public_row_counts()),
            {
                "core_commandintake": 1,
                "core_authoritydecision": 1,
                "core_circlejoinrequest": 1,
                "core_organismcommandreceipt": 1,
            },
        )
        after_first = _public_row_counts()
        # D §7.1a (i): a later database reading is a bounded fixture precondition, never a sleep.
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

        with patch.object(route.service, "_clock", side_effect=viewer_clock):
            with self.assertRaises(SBGGovernanceRefused) as retry:
                route.service.decide_governance_seat(command)
        self.assertEqual(retry.exception.refusal_code, S015AuthorityRefusalCode.SBG_SEATING_WHILE_DISABLED)
        self.assertIs(retry.exception.stage_context, ChildEntry.ADMIT_STAGE)
        self.assertEqual(len(viewer_times), 1)
        self.assertGreater(viewer_times[0], bound.received_at)
        self.assertEqual(_row_delta(after_first, _public_row_counts()), {})
        self.assertEqual(
            CircleJoinRequest.objects.filter(request_uuid=first.exception.request_uuid).count(),
            1,
        )
        self.assertEqual(
            CircleAdmissionDecision.objects.filter(request_id=request.pk).count(),
            0,
        )
        self.assertEqual(
            [served for _, served, _ in verify_calls],
            [
                frozenset(
                    {
                        IntakeEntry.REQUEST_GOVERNANCE_SEAT,
                        ChildEntry.REQUEST_STAGE,
                    }
                ),
                frozenset(
                    {
                        IntakeEntry.DECIDE_GOVERNANCE_SEAT,
                        ChildEntry.ADMIT_STAGE,
                    }
                ),
            ],
        )
        self.assertEqual(boundary_calls, [False, False])

        request_key, request_reference, admission_key, admission_reference = (
            _compound_stage_identifiers(
                command.living_organism_uuid,
                command.idempotency_key,
            )
        )
        self.assertEqual(request.consent_reference, request_reference)
        self.assertEqual(
            OrganismCommandReceipt.objects.filter(
                action=OrganismAction.REQUEST_GOVERNANCE_SEAT.value,
                idempotency_key=request_key,
                request_reference=request_reference,
            ).count(),
            1,
        )
        # D §7.1a (ii): the stage-1 receipt's fingerprint, recomputed from the request projection with
        # the intake row's T (effective_at unstated), never with a reading of the test's own.
        self.assertIsNone(command.effective_at)
        self.assertEqual(
            OrganismCommandReceipt.objects.get(
                actor=route.founder,
                action=OrganismAction.REQUEST_GOVERNANCE_SEAT.value,
                idempotency_key=request_key,
            ).payload_fingerprint,
            hashlib.sha256(canonical_json_bytes({
                "action": OrganismAction.REQUEST_GOVERNANCE_SEAT.value,
                "effective_at": bound.received_at,
                "idempotency_key": request_key,
                "organism_uuid": route.organism.organism_id,
                "payload": {
                    "circle_uuid": str(route.circle_uuid),
                    "seat_rule_uuid": str(route.rule_uuid),
                    "seat_id": "founder",
                },
                "request_reference": request_reference,
                "schema": "sbg-governance-seat-request-v1",
            })).hexdigest(),
        )
        decision_command = DecideGovernanceSeatCommand(
            credential=command.credential,
            living_organism_uuid=command.living_organism_uuid,
            request_reference=admission_reference,
            idempotency_key=admission_key,
            outcome=command.outcome,
            route="GOVERNANCE_CONSTITUTIONAL",
            request_uuid=request.request_uuid,
            effective_at=command.effective_at,
        )
        verified_parent = bound.verify_content(
            command,
            IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND,
        )
        decision_child = verified_parent.child(
            ChildEntry.ADMIT_STAGE,
            command=decision_command,
        )
        before_changed_child = _public_row_counts()
        with self.assertRaises(IntakeAdmissionTechnicalFailure):
            route.service._execute_decide_governance_seat(
                replace(decision_command, outcome="DECLINE"),
                decision_child,
            )
        self.assertEqual(_public_row_counts(), before_changed_child)

    def test_iw16_ep09_before_birthing_and_ep08_route_unavailable_control(self):
        for entry, suffix, governance_enabled in (
            (IntakeEntry.SEAT_GOVERNANCE_CIRCLE, "ep09", False),
            (IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND, "ep08-before", True),
        ):
            route = self._route(governance_enabled=governance_enabled)
            command = _parent_command(
                route,
                entry,
                tag=f"{route.route.tag}:{suffix}",
                effective_at=None,
            )
            before = _public_row_counts()
            before_decision_pks = set(AuthorityDecision.objects.values_list("pk", flat=True))
            with self.subTest(entry=entry):
                with self.assertRaises((S015AuthorityDenied, SBGGovernanceRefused)) as refused:
                    if entry is IntakeEntry.SEAT_GOVERNANCE_CIRCLE:
                        route.service.seat_governance_circle(command)
                    else:
                        route.service.decide_governance_seat(command)
                self.assertIs(refused.exception.stage_context, ChildEntry.ADMIT_STAGE)
                self.assertIsNotNone(refused.exception.request_uuid)
                self.assertIn(
                    "Your own request for seat founder is recorded and remains open.",
                    str(refused.exception),
                )
                if entry is IntakeEntry.SEAT_GOVERNANCE_CIRCLE:
                    self.assertIsInstance(refused.exception, SBGGovernanceRefused)
                    self.assertIs(
                        refused.exception.refusal_code,
                        S015AuthorityRefusalCode.SBG_SEATING_WHILE_DISABLED,
                    )
                else:
                    self.assertIsInstance(refused.exception, S015AuthorityDenied)
                    self.assertIs(
                        refused.exception.refusal_code,
                        S015AuthorityRefusalCode.SBG_SEAT_ROUTE_UNAVAILABLE,
                    )
                delta = _row_delta(before, _public_row_counts())
                expected_authority_decision_delta = (
                    1
                    if entry is IntakeEntry.SEAT_GOVERNANCE_CIRCLE
                    else 2
                )
                self.assertEqual(
                    delta,
                    {
                        "core_commandintake": 1,
                        "core_authoritydecision": expected_authority_decision_delta,
                        "core_circlejoinrequest": 1,
                        "core_organismcommandreceipt": 1,
                    },
                )
                created_decisions = list(
                    AuthorityDecision.objects.exclude(pk__in=before_decision_pks).values(
                        "decision_reference",
                        "action",
                        "outcome",
                        "refusal_code",
                    )
                )
                self.assertEqual(
                    len(created_decisions),
                    expected_authority_decision_delta,
                )
                qualified_decisions = [
                    decision
                    for decision in created_decisions
                    if decision["outcome"] == "QUALIFIED"
                ]
                self.assertEqual(len(qualified_decisions), 1)
                self.assertEqual(
                    qualified_decisions[0]["action"],
                    OrganismAction.REQUEST_GOVERNANCE_SEAT.value,
                )
                self.assertIsNone(qualified_decisions[0]["refusal_code"])
                refused_decisions = [
                    decision
                    for decision in created_decisions
                    if decision["outcome"] == "REFUSED"
                ]
                if entry is IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND:
                    self.assertEqual(len(refused_decisions), 1)
                    self.assertEqual(
                        refused_decisions[0]["action"],
                        OrganismAction.ADMIT_CIRCLE_PARTICIPANT.value,
                    )
                    self.assertEqual(
                        refused_decisions[0]["refusal_code"],
                        S015AuthorityRefusalCode.SBG_SEAT_ROUTE_UNAVAILABLE.value,
                    )
                    self.assertEqual(
                        refused_decisions[0]["decision_reference"],
                        refused.exception.authority_decision_reference,
                    )
                else:
                    self.assertEqual(refused_decisions, [])

    def test_iw16_c_reenabled_retry_refuses_and_standalone_ep07_replay_succeeds(self):
        route = self._route(governance_enabled=False, birthing=True)
        applicant = add_active_elector(route.route, "iw16-c-applicant")
        command = replace(
            _parent_command(
                route,
                IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND,
                tag=f"{route.route.tag}:iw16-c",
                effective_at=None,
            ),
            credential=applicant.credential,
        )
        before_first = _public_row_counts()
        decision_coordinates = []
        original_decision_executor = route.service._execute_decide_governance_seat

        def record_decision_coordinates(stage_command, stage_bound, *, stage_phase=None):
            decision_coordinates.append(
                (stage_bound.received_at, stage_command.effective_at)
            )
            return original_decision_executor(
                stage_command,
                stage_bound,
                stage_phase=stage_phase,
            )

        with patch.object(
            route.service,
            "_execute_decide_governance_seat",
            record_decision_coordinates,
        ):
            with self.assertRaises(SBGGovernanceRefused) as first:
                route.service.decide_governance_seat(command)
        bound = admit_request(
            "default",
            IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND,
            _actor_ref(applicant),
            intake_shape(IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND, command),
        )
        self.assertIs(
            first.exception.refusal_code,
            S015AuthorityRefusalCode.SBG_SEATING_WHILE_DISABLED,
        )
        request = CircleJoinRequest.objects.get(request_uuid=first.exception.request_uuid)
        self.assertEqual(request.received_at, bound.received_at)
        self.assertEqual(request.effective_at, bound.received_at)
        self.assertEqual(
            _row_delta(before_first, _public_row_counts()),
            {
                "core_commandintake": 1,
                "core_authoritydecision": 1,
                "core_circlejoinrequest": 1,
                "core_organismcommandreceipt": 1,
            },
        )

        effect_gateway(
            route.route.application_service,
            route.founder,
            route.organism,
            "iw16-c-enable",
            OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
            {
                "cq_active": False,
                "governance_enabled": True,
                "inherit_portal_cq_settings": False,
                "require_cq_to_change_lo_settings": False,
                "require_cq_to_change_application_question": False,
            },
        )
        before_retry = _public_row_counts()
        with patch.object(
            route.service,
            "_execute_decide_governance_seat",
            record_decision_coordinates,
        ):
            with self.assertRaises((S015AuthorityDenied, SBGGovernanceRefused)) as retry:
                route.service.decide_governance_seat(command)
        self.assertIs(retry.exception.stage_context, ChildEntry.ADMIT_STAGE)
        self.assertEqual(
            decision_coordinates,
            [(bound.received_at, None), (bound.received_at, None)],
        )
        self.assertEqual(
            _row_delta(before_retry, _public_row_counts()),
            {},
        )
        self.assertEqual(CircleJoinRequest.objects.filter(pk=request.pk).count(), 1)
        self.assertEqual(
            CircleJoinRequest.objects.filter(
                circle__parent_organism=route.organism,
            ).count(),
            1,
        )
        self.assertEqual(
            CircleJoinRequest.objects.get(pk=request.pk).effective_at,
            bound.received_at,
        )

        decision_time = database_now()
        decision_command = DecideGovernanceSeatCommand(
            credential=route.founder.credential,
            living_organism_uuid=route.organism.organism_id,
            request_reference=f"c3:iw16-c:ep07:ref:{route.route.tag}",
            idempotency_key=f"c3:iw16-c:ep07:key:{route.route.tag}",
            outcome="ACCEPT",
            route="GOVERNANCE_CONSTITUTIONAL",
            request_uuid=request.request_uuid,
            effective_at=decision_time,
        )
        before_ep07 = _public_row_counts()
        result = route.service.decide_governance_seat(decision_command)
        decision_bound = admit_request(
            "default",
            IntakeEntry.DECIDE_GOVERNANCE_SEAT,
            _actor_ref(route.founder),
            intake_shape(IntakeEntry.DECIDE_GOVERNANCE_SEAT, decision_command),
        )
        self.assertFalse(decision_bound.first)
        self.assertIsInstance(result, GovernanceSeatDecisionResult)
        self.assertIsNotNone(result.participation_uuid)
        self.assertEqual(
            CircleAdmissionDecision.objects.filter(request_id=request.pk).count(),
            1,
        )
        self.assertEqual(CircleJoinRequest.objects.filter(pk=request.pk).count(), 1)
        self.assertEqual(
            CircleJoinRequest.objects.filter(
                circle__parent_organism=route.organism,
            ).count(),
            1,
        )
        self.assertGreaterEqual(decision_time, request.effective_at)
        decision_delta = _row_delta(before_ep07, _public_row_counts())
        self.assertEqual(
            decision_delta,
            {
                "core_commandintake": 1,
                "core_authoritydecision": 1,
                "core_circleadmissiondecision": 1,
                "core_circleparticipation": 1,
                "core_organismcommandreceipt": 1,
            },
        )

        viewer_times = []

        def viewer_clock():
            value = database_now()
            viewer_times.append(value)
            return value

        after_ep07 = _public_row_counts()
        with patch.object(route.service, "_clock", side_effect=viewer_clock):
            replay =             route.service.decide_governance_seat(decision_command)
        self.assertEqual(replay, result)
        self.assertEqual(len(viewer_times), 1)
        self.assertGreaterEqual(viewer_times[0], decision_bound.received_at)
        self.assertEqual(_row_delta(after_ep07, _public_row_counts()), {})
        self.assertEqual(CircleJoinRequest.objects.filter(pk=request.pk).count(), 1)
        self.assertEqual(
            CircleJoinRequest.objects.filter(
                circle__parent_organism=route.organism,
            ).count(),
            1,
        )

    def test_epoch_change_between_stages_is_rethrown_with_open_request_context(self):
        route = self._route()
        command, bound = self._parent(
            route,
            IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND,
            suffix="v5-between-stages",
        )
        before = _public_row_counts()
        original = route.service._execute_request_governance_seat
        original_verify = BoundRequest.verify
        v5_errors = []

        def capture_v5_error(stage_bound, stage_command, actor, served, alias):
            try:
                return original_verify(stage_bound, stage_command, actor, served, alias)
            except SBGGovernanceUnavailable as exc:
                v5_errors.append(exc)
                raise

        def request_then_revoke_epoch(stage_command, stage_bound):
            result = original(stage_command, stage_bound)
            Identity.objects.filter(pk=route.founder.pk).update(
                access_epoch=route.founder.access_epoch + 1
            )
            return result

        with (
            patch.object(BoundRequest, "verify", capture_v5_error),
            patch.object(
                route.service,
                "_execute_request_governance_seat",
                side_effect=request_then_revoke_epoch,
            ),
        ):
            with self.assertRaises(SBGGovernanceUnavailable) as refused:
                route.service._orchestrate_compound_seating(
                    command,
                    bound,
                    _RECIPE_DECIDE_COMPOUND,
                )
        self.assertEqual(
            refused.exception.request_uuid,
            CircleJoinRequest.objects.latest("pk").request_uuid,
        )
        self.assertIs(refused.exception.stage_context, ChildEntry.ADMIT_STAGE)
        self.assertEqual(len(v5_errors), 1)
        self.assertIs(refused.exception.__cause__, v5_errors[0])
        self.assertIn(
            "Your own request for seat founder is recorded and remains open.",
            str(refused.exception),
        )
        self.assertEqual(
            _row_delta(before, _public_row_counts()),
            {
                "core_authoritydecision": 1,
                "core_circlejoinrequest": 1,
                "core_organismcommandreceipt": 1,
            },
        )

    def test_stage_two_refusal_and_technical_failures_keep_stage_context(self):
        cases = (
            (
                SBGGovernanceRefused(
                    S015AuthorityRefusalCode.SBG_MEMBER_NOT_ELIGIBLE,
                    "stage-two refusal",
                ),
                SBGGovernanceRefused,
            ),
            (
                S015AuthorityDenied(
                    "authority refusal",
                    refusal_code=S015AuthorityRefusalCode.SBG_MEMBER_NOT_ELIGIBLE,
                ),
                S015AuthorityDenied,
            ),
            (IntegrityError("unmapped integrity failure"), IntegrityError),
            (RuntimeError("same technical failure"), RuntimeError),
        )
        for injected, expected_type in cases:
            route = self._route(birthing=True)
            command, bound = self._parent(
                route,
                IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND,
                suffix=f"stage-context-{type(injected).__name__}",
            )
            original_validator = route.service._validate_constitutional_seating

            def inject_after_stage_two_validation(*args, **kwargs):
                if kwargs.get("require_enabled") is True:
                    raise injected
                return original_validator(*args, **kwargs)

            original_cause = RuntimeError("original cause")
            if isinstance(injected, RuntimeError):
                injected.__cause__ = original_cause
            prior_cause = injected.__cause__
            before = _public_row_counts()
            with patch.object(
                route.service,
                "_validate_constitutional_seating",
                side_effect=inject_after_stage_two_validation,
            ):
                with self.subTest(error=type(injected).__name__):
                    with self.assertRaises(expected_type) as raised:
                        route.service._orchestrate_compound_seating(
                            command,
                            bound,
                            _RECIPE_DECIDE_COMPOUND,
                        )
            self.assertIs(raised.exception.stage_context, ChildEntry.ADMIT_STAGE)
            # PKT-CLK-3 CLK3-3 part 2: every case is re-raised from the injected error, which keeps
            # its prior cause; a technical failure in the body (a known rollback) takes B-09-S2.
            self.assertIsNot(raised.exception, injected)
            self.assertIs(raised.exception.__cause__, injected)
            self.assertIs(injected.__cause__, prior_cause)
            if isinstance(injected, (RuntimeError, IntegrityError)):
                self.assertFalse(getattr(raised.exception, "outcome_uncertain", False))
                self.assertEqual(
                    str(raised.exception),
                    "ADMIT_CIRCLE_PARTICIPANT could not be completed because of a technical problem; "
                    "no governed record was written by the decision stage. Your own request for seat "
                    "founder is recorded and remains open. INTEVIA kept a record that this request was "
                    f"received at {canonical_timestamp(bound.received_at)}; the same request can be sent "
                    "again and is judged as of that time. If it persists, report it.",
                )
            self.assertEqual(
                _row_delta(before, _public_row_counts()),
                {
                    "core_authoritydecision": 1,
                    "core_circlejoinrequest": 1,
                    "core_organismcommandreceipt": 1,
                },
            )
            if isinstance(raised.exception, (SBGGovernanceRefused, S015AuthorityDenied)):
                self.assertEqual(
                    raised.exception.request_uuid,
                    CircleJoinRequest.objects.latest("pk").request_uuid,
                )
                self.assertIn(
                    "Your own request for seat founder is recorded and remains open.",
                    str(raised.exception),
                )

    def test_stage_one_integrity_mapping_is_applied_outside_its_atomic_block(self):
        route = self._route()
        request_command = RequestGovernanceSeatCommand(
            credential=route.founder.credential,
            living_organism_uuid=route.organism.organism_id,
            circle_uuid=route.circle_uuid,
            seat_rule_uuid=route.rule_uuid,
            seat_id="founder",
            request_reference=f"c3:guard:ref:{route.route.tag}",
            idempotency_key=f"c3:guard:key:{route.route.tag}",
        )
        bound = BoundRequest.for_test(
            IntakeEntry.REQUEST_GOVERNANCE_SEAT,
            request_command,
            received_at=database_now(),
            actor=_actor_ref(route.founder),
        )
        injected = IntegrityError("test guard")
        mapped = SBGGovernanceRefused(
            S015AuthorityRefusalCode.SBG_SEAT_ABSENT,
            "mapped guard refusal",
        )

        @contextmanager
        def fail_on_exit(alias, *, received_at=None):
            with transaction.atomic(using=alias):
                yield
                raise injected

        before = _public_row_counts()
        with (
            patch(
                "src.intevia.services.organism_governance_service.authority_atomic",
                fail_on_exit,
            ),
            patch(
                "src.intevia.services.organism_governance_service._controlled_integrity_refusal",
                return_value=mapped,
            ),
        ):
            with self.assertRaises(SBGGovernanceRefused) as refused:
                route.service._execute_request_governance_seat(request_command, bound)
        self.assertEqual(refused.exception.refusal_code, mapped.refusal_code)
        self.assertIs(refused.exception.__cause__, injected)
        self.assertEqual(_public_row_counts(), before)

    def test_test_only_public_and_child_routes_verify_without_compound_success_claim(self):
        public_route = self._route()
        public_parent = _parent_command(
            public_route,
            IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND,
            tag=f"{public_route.route.tag}:iw00c-public",
            effective_at=None,
        )
        public_request, _ = _stage_commands(public_parent)
        public_bound = BoundRequest.for_test(
            IntakeEntry.REQUEST_GOVERNANCE_SEAT,
            public_request,
            received_at=database_now(),
            actor=_actor_ref(public_route.founder),
        )
        before_public = _public_row_counts()
        public_result = public_route.service._execute_request_governance_seat(
            public_request,
            public_bound,
        )
        self.assertIsNotNone(public_result.request_uuid)
        self.assertEqual(
            _row_delta(before_public, _public_row_counts()),
            {
                "core_authoritydecision": 1,
                "core_circlejoinrequest": 1,
                "core_organismcommandreceipt": 1,
            },
        )

        test_parent_route = self._route(governance_enabled=False, birthing=True)
        command = _parent_command(
            test_parent_route,
            IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND,
            tag=f"{test_parent_route.route.tag}:iw00c-parent",
            effective_at=None,
        )
        test_parent_bound = BoundRequest.for_test(
            IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND,
            command,
            received_at=database_now(),
            actor=_actor_ref(test_parent_route.founder),
        )
        before_compound = _public_row_counts()
        with self.assertRaises(SBGGovernanceRefused) as stopped:
            test_parent_route.service._orchestrate_compound_seating(
                command,
                test_parent_bound,
                _RECIPE_DECIDE_COMPOUND,
            )
        self.assertIs(stopped.exception.stage_context, ChildEntry.ADMIT_STAGE)
        self.assertEqual(
            _row_delta(before_compound, _public_row_counts()),
            {
                "core_authoritydecision": 1,
                "core_circlejoinrequest": 1,
                "core_organismcommandreceipt": 1,
            },
        )

        route = self._route(governance_enabled=False, birthing=True)
        command, parent_bound = self._parent(
            route,
            IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND,
            suffix="iw00c-child",
        )
        verified_parent = parent_bound.verify_content(
            command,
            IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND,
        )
        request_command, decision_command = _stage_commands(command)
        child = verified_parent.child(ChildEntry.REQUEST_STAGE, command=request_command)
        before_stage_one = _public_row_counts()
        request_result = route.service._execute_request_governance_seat(request_command, child)
        self.assertIsNotNone(request_result.request_uuid)
        self.assertEqual(
            _row_delta(before_stage_one, _public_row_counts()),
            {
                "core_authoritydecision": 1,
                "core_circlejoinrequest": 1,
                "core_organismcommandreceipt": 1,
            },
        )
        after_stage_one = _public_row_counts()
        with self.assertRaises(IntakeAdmissionTechnicalFailure):
            route.service._execute_request_governance_seat(
                replace(request_command, seat_id="changed-seat"),
                child,
            )
        self.assertEqual(_public_row_counts(), after_stage_one)

        decision_command = replace(
            decision_command,
            request_uuid=request_result.request_uuid,
        )
        decision_child = verified_parent.child(
            ChildEntry.ADMIT_STAGE,
            command=decision_command,
        )
        verified = decision_child.verify_content(
            decision_command,
            frozenset(
                {
                    IntakeEntry.DECIDE_GOVERNANCE_SEAT,
                    ChildEntry.ADMIT_STAGE,
                }
            ),
        )
        self.assertTrue(verified.content_verified)
        before_child_stage_two = _public_row_counts()
        with self.assertRaises(SBGGovernanceRefused):
            route.service._execute_decide_governance_seat(
                decision_command,
                verified,
            )
        self.assertEqual(_public_row_counts(), before_child_stage_two)
