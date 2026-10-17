"""PKT-SE-K2 allowance and two-coordinate measurement witnesses."""

from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch
from uuid import uuid4

from django.db import (
    DatabaseError,
    IntegrityError,
    close_old_connections,
    connection,
    connections,
    transaction,
)
from django.test import SimpleTestCase, TransactionTestCase

from core.models import (
    AuthorityDecision,
    Circle,
    CircleStateEvent,
    GovernedRule,
    LivingOrganismEvent,
    LOApplicationSettingsVersion,
    OrganismCommandReceipt,
    OrganismEntitlement,
    OrganismEntitlementEvent,
    OrganismEntitlementPolicyText,
    OrganismMembership,
    OrganismMembershipTransition,
    SE_ENTITLEMENT_CAPABILITY_CODES,
)
from src.intevia.services.organism_entitlement_service import (
    S015EntitlementTechnicalFailure,
    MeasurementResult,
    _decide_measurement,
    _measure_usage,
    check_allowance,
    entitlement_summary,
)
from src.intevia.services.organism_governance_service import (
    OrganismGovernanceService,
)
from tests.s015_0021_support import (
    append_event,
    database_now,
    empty_fp,
    refresh,
)
from tests.sbg_route_support import (
    adopt_governance_arrangement,
    create_governance_circle,
    governance_arrangement_body,
    route_test_now,
)
from tests.sb2_route_support import birth, prepare_route
from tests.se_route_support import (
    TEST_POLICY_VERSION,
    add_test_coordinator,
    ensure_test_policy_text,
)


class _RollbackFixture(Exception):
    pass


def _route_with_adopted_arrangement():
    """W-K2.R1: prepare an organism whose first governance Circle is still to be created."""
    route = prepare_route(
        tag=f"sek2-{uuid4().hex}",
        governance_enabled=True,
        to_birthing_phase=False,
    )
    service = OrganismGovernanceService(clock=database_now)
    rule_uuid = uuid4()
    adopt_governance_arrangement(
        route,
        service,
        tag=route.tag,
        body=governance_arrangement_body(
            effective_from=route_test_now(route),
        ),
        rule_uuid=rule_uuid,
    )
    return route, service


def _insert_circle(
    route,
    *,
    tag,
    effective_at,
    received_at,
    with_event=True,
    prior_state=None,
    refresh_cache=True,
):
    """W-K2.A4/R3/R4: create a declared anchor/event history at controlled times."""
    circle_uuid = uuid4()
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO core_circle (
                circle_uuid, kind, parent_organism_id, state, created_at,
                current_state_effective_at, state_known_at,
                state_source_event_set_fingerprint, founding_reference
            ) VALUES (%s, 'GENERIC', %s, 'DORMANT', %s, %s, %s, %s, %s)
            RETURNING id
            """,
            [
                circle_uuid,
                route.organism.pk,
                received_at,
                effective_at,
                received_at,
                empty_fp(cursor, "core_circle", circle_uuid),
                f"sek2-fixture:{tag}",
            ],
        )
        circle_id = cursor.fetchone()[0]
        recorded_at = None
        if with_event:
            _, recorded_at = append_event(
                cursor,
                "core_circlestateevent",
                circle_id,
                1,
                "CREATE_DORMANT",
                prior_state,
                "DORMANT",
                None,
                route.organism.founding_authority_basis_id,
                f"sek2:{tag}",
                eff=effective_at,
                occ=received_at,
                rec=received_at,
                extra={
                    "actor_state": "LIVING_ORGANISM",
                    "actor_id": None,
                    "actor_access_epoch": None,
                    "actor_capacity": "STANDING",
                    "enterer_identity_id": route.founder.pk,
                    "evidence_reference": f"sek2:fixture:{tag}",
                },
            )
            if refresh_cache:
                refresh(
                    cursor,
                    "core_circle",
                    circle_id,
                    effective_at,
                )
    return Circle.objects.get(pk=circle_id), recorded_at


def _table_state(model):
    """Return all concrete rows for a no-write comparison."""
    field_names = tuple(field.attname for field in model._meta.concrete_fields)
    return tuple(model.objects.order_by("pk").values_list(*field_names))


def _read_state():
    """Capture every authoritative table read or written by the allowance surfaces."""
    return tuple(
        _table_state(model)
        for model in (
            Circle,
            CircleStateEvent,
            OrganismMembership,
            OrganismMembershipTransition,
            OrganismEntitlement,
            OrganismEntitlementEvent,
            OrganismEntitlementPolicyText,
            LOApplicationSettingsVersion,
            LivingOrganismEvent,
            OrganismCommandReceipt,
            AuthorityDecision,
            GovernedRule,
        )
    )


def _seed_learner_limit(route, *, effective_at):
    """W-K2.R1m: give the learner-history witness a measurable entitlement limit."""
    ensure_test_policy_text()
    rule = GovernedRule.objects.get(
        scope_kind="PLATFORM",
        scope_organism__isnull=True,
        scope_circle__isnull=True,
        code="BETA_COMPLIMENTARY_ACCESS",
    )
    return OrganismEntitlement.objects.create(
        living_organism=route.organism,
        source="COMPLIMENTARY",
        basis="BETA_OPT_IN",
        policy_reference=f"policy-text:v{TEST_POLICY_VERSION}",
        policy_version=TEST_POLICY_VERSION,
        capabilities=list(SE_ENTITLEMENT_CAPABILITY_CODES),
        limits={
            "circles": {"kind": "UNLIMITED"},
            "organism_learners": {"kind": "LIMIT", "value": 9},
            "document_footprint": {"kind": "NOT_SET"},
        },
        effective_at=effective_at,
        ending_condition_reference=(
            f"paid-availability-transition:{route.organism.organism_id}"
        ),
    )


class AllowanceMeasurementTests(TransactionTestCase):
    reset_sequences = True

    def _read_only(self, operation):
        """W-K2.R6: prove each measurement read leaves the captured state unchanged."""
        before = _read_state()
        result = operation()
        self.assertEqual(_read_state(), before)
        return result

    def test_w_k2_r1_increment_uses_current_usage_and_performs_no_writes(self):
        """W-K2.R1: calculate current usage plus the requested increment read-only."""
        route, service = _route_with_adopted_arrangement()
        at = database_now()
        allowance = self._read_only(
            lambda: check_allowance(
                route.organism.organism_id,
                "circles",
                1,
                at,
            )
        )
        summary = self._read_only(
            lambda: entitlement_summary(route.organism.organism_id, at=at)
        )
        self.assertEqual(allowance.status, "within")
        self.assertEqual(allowance.measured_usage, 0)
        self.assertEqual(allowance.increment, 1)
        self.assertEqual(allowance.proposed_total, 1)
        self.assertEqual(summary["dimensions"]["circles"]["measured_usage"], 0)

        created = create_governance_circle(route, service, tag=route.tag)
        circle = Circle.objects.get(circle_uuid=created.record_uuid)
        event = CircleStateEvent.objects.get(circle=circle)
        self.assertGreater(event.effective_at, at)
        self.assertGreater(event.recorded_at, at)

        later = self._read_only(
            lambda: check_allowance(
                route.organism.organism_id,
                "circles",
                1,
                database_now(),
            )
        )
        historical_check = self._read_only(
            lambda: check_allowance(
                route.organism.organism_id,
                "circles",
                1,
                at,
            )
        )
        after_creation_historical = self._read_only(
            lambda: entitlement_summary(
                route.organism.organism_id,
                at=at,
            )
        )
        later_summary = self._read_only(
            lambda: entitlement_summary(
                route.organism.organism_id,
                at=database_now(),
            )
        )
        self.assertEqual(later.measured_usage, 1)
        self.assertEqual(later.proposed_total, 2)
        self.assertEqual(historical_check.measured_usage, 0)
        self.assertEqual(historical_check.status, "within")
        self.assertEqual(
            after_creation_historical["dimensions"]["circles"]["measured_usage"],
            0,
        )
        self.assertEqual(
            after_creation_historical["dimensions"]["circles"]["not_yet_known"],
            0,
        )
        self.assertIsNone(
            after_creation_historical["dimensions"]["circles"][
                "measurement_reason_code"
            ]
        )
        self.assertEqual(later_summary["dimensions"]["circles"]["measured_usage"], 1)

    def test_w_k2_a4_retroactive_history_is_separate_from_state_absence(self):
        """W-K2.A4: distinguish retroactive unknown history from future-effective state."""
        route, service = _route_with_adopted_arrangement()
        at = database_now()
        retro_circle, retro_recorded_at = _insert_circle(
            route,
            tag=f"retro-{uuid4().hex}",
            effective_at=at - timedelta(seconds=1),
            received_at=database_now(),
        )
        self.assertGreater(retro_recorded_at, at)

        future_circle = create_governance_circle(route, service, tag=route.tag)
        future = Circle.objects.get(circle_uuid=future_circle.record_uuid)
        future_event = CircleStateEvent.objects.get(circle=future)
        self.assertGreater(future_event.effective_at, at)
        self.assertGreater(future_event.recorded_at, at)

        historical = self._read_only(
            lambda: entitlement_summary(route.organism.organism_id, at=at)
        )
        circles = historical["dimensions"]["circles"]
        self.assertEqual(circles["measured_usage"], 0)
        self.assertEqual(circles["not_yet_known"], 1)
        self.assertIsNone(circles["measurement_reason_code"])
        self.assertNotIn("not_yet_known", circles["display_line"])

        measurement = self._read_only(
            lambda: _measure_usage(
                route.organism.organism_id,
                "circles",
                at,
                "default",
                known_at=at,
            )
        )
        self.assertEqual(measurement.counted, 0)
        self.assertEqual(measurement.absent_state, 1)
        self.assertEqual(measurement.not_yet_known, 1)

        current_knowledge = self._read_only(
            lambda: check_allowance(
                route.organism.organism_id,
                "circles",
                0,
                at,
            )
        )
        later = self._read_only(
            lambda: entitlement_summary(
                route.organism.organism_id,
                at=database_now(),
            )["dimensions"]["circles"]
        )
        self.assertEqual(current_knowledge.status, "within")
        self.assertEqual(current_knowledge.measured_usage, 1)
        self.assertEqual(later["measured_usage"], 2)
        self.assertEqual(later["not_yet_known"], 0)
        self.assertEqual(Circle.objects.get(pk=retro_circle.pk).state, "DORMANT")

    def test_w_k2_r1m_membership_usage_counts_distinct_active_identities(self):
        """W-K2.R1m: measure active identities from membership event histories."""
        route = prepare_route(tag=f"sek2-{uuid4().hex}")
        birth(route, suffix=f"sek2-membership-{route.tag}")
        _seed_learner_limit(route, effective_at=database_now())
        at = database_now()
        initial = self._read_only(
            lambda: _measure_usage(
                route.organism.organism_id,
                "organism_learners",
                at,
                "default",
                known_at=at,
            )
        )

        add_test_coordinator(route, tag=f"member-{uuid4().hex}")
        historical = self._read_only(
            lambda: _measure_usage(
                route.organism.organism_id,
                "organism_learners",
                at,
                "default",
                known_at=at,
            )
        )
        historical_check = self._read_only(
            lambda: check_allowance(
                route.organism.organism_id,
                "organism_learners",
                0,
                at,
            )
        )
        historical_summary = self._read_only(
            lambda: entitlement_summary(
                route.organism.organism_id,
                at=at,
            )["dimensions"]["organism_learners"]
        )
        self.assertEqual(historical.counted, initial.counted)
        self.assertEqual(historical.absent_state, 1)
        self.assertEqual(historical.not_yet_known, 0)
        self.assertEqual(historical_check.measured_usage, initial.counted)
        self.assertEqual(historical_summary["measured_usage"], initial.counted)
        self.assertEqual(historical_summary["not_yet_known"], 0)

        later_at = database_now()
        later = self._read_only(
            lambda: _measure_usage(
                route.organism.organism_id,
                "organism_learners",
                later_at,
                "default",
                known_at=later_at,
            )
        )
        later_summary = self._read_only(
            lambda: entitlement_summary(
                route.organism.organism_id,
                at=later_at,
            )["dimensions"]["organism_learners"]
        )
        self.assertEqual(later.counted, initial.counted + 1)
        self.assertEqual(later_summary["measured_usage"], initial.counted + 1)
        self.assertEqual(later_summary["not_yet_known"], 0)

    def test_w_k2_a5_measurement_does_not_block_a_concurrent_circle_writer(self):
        """W-K2.A5: a paused read holds no lock that prevents a real Circle write."""
        route, service = _route_with_adopted_arrangement()
        at = database_now()
        measurement_started = Event()
        continue_measurement = Event()
        original_measure = _measure_usage

        def pause_before_measurement(*args, **kwargs):
            measurement_started.set()
            if not continue_measurement.wait(timeout=10):
                raise TimeoutError("the concurrent measurement was not released")
            return original_measure(*args, **kwargs)

        def read_allowance():
            close_old_connections()
            try:
                return check_allowance(
                    route.organism.organism_id,
                    "circles",
                    1,
                    at,
                )
            finally:
                connections.close_all()

        with patch(
            "src.intevia.services.organism_entitlement_service._measure_usage",
            side_effect=pause_before_measurement,
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                read_future = executor.submit(read_allowance)
                self.assertTrue(measurement_started.wait(timeout=10))
                write_future = executor.submit(
                    create_governance_circle,
                    route,
                    service,
                    tag=route.tag,
                )
                try:
                    created = write_future.result(timeout=10)
                finally:
                    state_after_writer = _read_state()
                    continue_measurement.set()
                allowance = read_future.result(timeout=20)

        self.assertEqual(allowance.status, "within")
        self.assertEqual(allowance.measured_usage, 0)
        self.assertEqual(
            Circle.objects.filter(
                circle_uuid=created.record_uuid,
                parent_organism=route.organism,
            ).count(),
            1,
        )
        self.assertEqual(_read_state(), state_after_writer)

    def test_w_k2_r3_history_without_any_event_is_unmeasurable_and_rolled_back(self):
        """W-K2.R3: refuse a Circle anchor whose event history is empty."""
        route = prepare_route(tag=f"sek2-{uuid4().hex}")
        at = database_now()
        before = _read_state()

        with self.assertRaises(_RollbackFixture):
            with transaction.atomic():
                _insert_circle(
                    route,
                    tag=f"missing-{uuid4().hex}",
                    effective_at=at,
                    received_at=at,
                    with_event=False,
                )
                allowance = self._read_only(
                    lambda: check_allowance(
                        route.organism.organism_id,
                        "circles",
                        0,
                        at,
                    )
                )
                self.assertEqual(allowance.status, "cannot_check")
                summary = self._read_only(
                    lambda: entitlement_summary(
                        route.organism.organism_id,
                        at=at,
                    )["dimensions"]["circles"]
                )
                self.assertIsNone(summary["measured_usage"])
                self.assertEqual(summary["measurement_reason_code"], "UNMEASURABLE")
                raise _RollbackFixture()

        self.assertEqual(_read_state(), before)

    def test_w_k2_r4_readable_fold_survives_damage_but_commit_guard_rejects(self):
        """W-K2.R4: retain readable state while the deferred integrity guard rejects commit."""
        route = prepare_route(tag=f"sek2-{uuid4().hex}")
        at = database_now()
        before = _read_state()
        readable_fold_assertions_passed = False

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                _insert_circle(
                    route,
                    tag=f"damaged-{uuid4().hex}",
                    effective_at=at,
                    received_at=at,
                    refresh_cache=False,
                )
                measurement = self._read_only(
                    lambda: _measure_usage(
                        route.organism.organism_id,
                        "circles",
                        at,
                        "default",
                        known_at=None,
                    )
                )
                self.assertEqual(measurement.counted, 1)
                self.assertEqual(measurement.anomalous, 0)
                readable_fold_assertions_passed = True

        self.assertTrue(readable_fold_assertions_passed)
        self.assertEqual(_read_state(), before)

    def test_w_k2_r4_database_errors_are_not_silently_treated_as_zero(self):
        """W-K2.R4: propagate measurement faults into the authored cannot-check outcomes."""
        route = prepare_route(tag=f"sek2-{uuid4().hex}")
        at = database_now()

        class _FailingCursor:
            def __enter__(self):
                return self

            def __exit__(self, *_exc_info):
                return False

            def execute(self, *_args, **_kwargs):
                raise DatabaseError("fixture measurement query failure")

        class _FailingConnection:
            def cursor(self):
                return _FailingCursor()

        before = _read_state()
        with patch(
            "src.intevia.services.organism_entitlement_service.connections",
            {"default": _FailingConnection()},
        ):
            with self.assertRaises(DatabaseError):
                _measure_usage(
                    route.organism.organism_id,
                    "circles",
                    at,
                    "default",
                    known_at=None,
                )
        self.assertEqual(_read_state(), before)

        with patch(
            "src.intevia.services.organism_entitlement_service._measure_usage",
            side_effect=DatabaseError("fixture measurement query failure"),
        ):
            allowance = self._read_only(
                lambda: check_allowance(
                    route.organism.organism_id,
                    "circles",
                    0,
                    at,
                )
            )
            summary = self._read_only(
                lambda: entitlement_summary(
                    route.organism.organism_id,
                    at=at,
                )["dimensions"]["circles"]
            )
        self.assertEqual(allowance.status, "cannot_check")
        self.assertIsNone(summary["measured_usage"])
        self.assertEqual(summary["measurement_reason_code"], "UNMEASURABLE")

class MeasurementDecisionTests(SimpleTestCase):
    """Pure decision witnesses; no database access is required."""

    def test_w_k2_r5_summary_and_check_have_distinct_not_yet_known_rules(self):
        """W-K2.R5: permit historical summaries but refuse current checks with N3."""
        retrospective = MeasurementResult(
            counted=2,
            absent_state=3,
            not_yet_known=1,
            unreadable=0,
            anomalous=0,
        )
        self.assertEqual(_decide_measurement(retrospective, "summary"), 2)
        with self.assertRaises(S015EntitlementTechnicalFailure):
            _decide_measurement(retrospective, "check")

        anomalous = MeasurementResult(
            counted=2,
            absent_state=0,
            not_yet_known=0,
            unreadable=0,
            anomalous=1,
        )
        with self.assertRaises(S015EntitlementTechnicalFailure):
            _decide_measurement(anomalous, "summary")
