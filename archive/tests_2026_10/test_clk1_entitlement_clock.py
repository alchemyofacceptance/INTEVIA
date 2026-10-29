"""PKT-CLK-1 witnesses for database time and SE knowledge time."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone as datetime_timezone
import inspect
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.db import DatabaseError, IntegrityError, connection
from django.db.models.query import QuerySet
from django.test import SimpleTestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import (
    GovernedRule,
    OrganismEntitlement,
    OrganismEntitlementEvent,
)
from src.intevia.services.clock import database_now
from src.intevia.services.organism_entitlement_service import (
    capability_status,
    entitlement_effective,
    entitlement_limit,
    entitlement_summary,
)
from tests.sb2_route_support import database_now as test_database_now
from tests.sb2_route_support import prepare_route
from tests.se_route_support import declared_event_less_entitlement_rows


_LIMITS = {
    "circles": {"kind": "LIMIT", "value": 9},
    "organism_learners": {"kind": "NOT_SET"},
    "document_footprint": {"kind": "NOT_SET"},
}


class _FakeCursor:
    def __init__(self, row):
        self.row = row
        self.statement = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, statement):
        self.statement = statement

    def fetchone(self):
        return self.row


class _FakeConnection:
    def __init__(self, row):
        self.fake_cursor = _FakeCursor(row)
        self.cursor_calls = 0

    def cursor(self):
        self.cursor_calls += 1
        return self.fake_cursor


class DatabaseNowNoDatabaseTests(SimpleTestCase):
    def test_uses_the_requested_alias_and_returns_an_aware_database_value(self):
        value = datetime(2026, 10, 14, 12, tzinfo=datetime_timezone.utc)
        fake = _FakeConnection((value,))
        with patch("src.intevia.services.clock.connections", {"reporting": fake}):
            self.assertEqual(database_now("reporting"), value)
        self.assertEqual(fake.cursor_calls, 1)
        self.assertEqual(fake.fake_cursor.statement, "SELECT clock_timestamp()")

    def test_refuses_missing_null_or_naive_clock_values(self):
        naive = datetime(2026, 10, 14, 12)
        for row in (None, (None,), (naive,)):
            with self.subTest(row=row):
                fake = _FakeConnection(row)
                with patch("src.intevia.services.clock.connections", {"default": fake}):
                    with self.assertRaises(DatabaseError):
                        database_now()


def _make_entitlement(route, *, effective_at, effective_until=None, limits=None):
    with declared_event_less_entitlement_rows(
        "family 3: TRIAL entitlement with controlled effective window"
    ):
        entitlement = OrganismEntitlement.objects.create(
            living_organism=route.organism,
            source="COMPLIMENTARY",
            basis="TRANSITION_TRIAL",
            policy_reference="fixture:trial",
            policy_version=1,
            capabilities=["APPLICATION_CIRCLE"],
            limits=_LIMITS if limits is None else limits,
            effective_at=effective_at,
            effective_until=effective_until,
        )
    entitlement.refresh_from_db()
    return entitlement


def _append_free_tier_rule(effective_from):
    latest = (
        GovernedRule.objects.filter(
            scope_kind="PLATFORM",
            scope_organism__isnull=True,
            scope_circle__isnull=True,
            code="FREE_TIER",
        )
        .order_by("-version")
        .first()
    )
    if latest is None:
        raise AssertionError("the platform FREE_TIER seed is unavailable")
    rule = GovernedRule.objects.create(
        rule_uuid=latest.rule_uuid,
        version=latest.version + 1,
        scope_kind="PLATFORM",
        scope_organism=None,
        scope_circle=None,
        code="FREE_TIER",
        body=latest.body,
        authority_reference=f"fixture:clk1:{uuid4()}",
        source="PLATFORM_RULING",
        effective_from=effective_from,
        decided_at=effective_from,
    )
    rule.refresh_from_db()
    return rule


def _free_rules_known_at(at, known_at, *, future=False):
    rules = GovernedRule.objects.filter(
        scope_kind="PLATFORM",
        scope_organism__isnull=True,
        scope_circle__isnull=True,
        code="FREE_TIER",
        recorded_at__lte=known_at,
    )
    if future:
        rules = rules.filter(effective_from__gt=at)
    else:
        rules = rules.filter(effective_from__lte=at)
    return rules.order_by(
        "-effective_from",
        "-recorded_at",
        "-version",
        "-pk",
    )


class _ExtensionFixtureQuerySet:
    def __init__(self, rows):
        self.rows = rows

    def order_by(self, *_):
        return self

    def values(self, *fields):
        return [
            {field: getattr(row, field) for field in fields}
            for row in self.rows
        ]


class EntitlementDatabaseClockTests(TransactionTestCase):
    ISOLATION_FULL_CAPTURE = True

    reset_sequences = True

    def test_w1_omitted_at_uses_database_time_under_both_host_skews(self):
        route = prepare_route()
        database_at = test_database_now()
        _make_entitlement(
            route,
            effective_at=database_at - timedelta(hours=1),
            effective_until=database_at + timedelta(hours=1),
            limits={
                **_LIMITS,
                "circles": {"kind": "LIMIT", "value": 4},
            },
        )

        expected = {
            "effective": entitlement_effective(
                route.organism.organism_id,
                "APPLICATION_CIRCLE",
                at=database_at,
            ),
            "limit": entitlement_limit(
                route.organism.organism_id,
                "circles",
                at=database_at,
            ),
            "summary": entitlement_summary(
                route.organism.organism_id,
                at=database_at,
            ),
            "status": capability_status(
                route.organism.organism_id,
                at=database_at,
            ),
        }
        self.assertTrue(expected["effective"])
        self.assertEqual(expected["limit"], ("LIMIT", 4))

        default_calls = {
            "effective": lambda: entitlement_effective(
                route.organism.organism_id,
                "APPLICATION_CIRCLE",
            ),
            "limit": lambda: entitlement_limit(
                route.organism.organism_id,
                "circles",
            ),
            "summary": lambda: entitlement_summary(route.organism.organism_id),
            "status": lambda: capability_status(route.organism.organism_id),
        }
        host_skew_answers = []
        for skew in (timedelta(hours=-2), timedelta(hours=2)):
            host_value = database_at + skew
            skewed = {
                "effective": entitlement_effective(
                    route.organism.organism_id,
                    "APPLICATION_CIRCLE",
                    at=host_value,
                ),
                "limit": entitlement_limit(
                    route.organism.organism_id,
                    "circles",
                    at=host_value,
                ),
                "summary": entitlement_summary(
                    route.organism.organism_id,
                    at=host_value,
                ),
                "status": capability_status(
                    route.organism.organism_id,
                    at=host_value,
                ),
            }
            for name in expected:
                with self.subTest(skew=skew, function=name):
                    self.assertNotEqual(skewed[name], expected[name])

            with patch("django.utils.timezone.now", return_value=host_value):
                self.assertEqual(timezone.now(), host_value)
                default_answers = {}
                for name, call in default_calls.items():
                    with self.subTest(skew=skew, function=name):
                        answer = call()
                        self.assertEqual(answer, expected[name])
                        default_answers[name] = answer
            host_skew_answers.append(
                {
                    "skew_hours": int(skew.total_seconds() / 3600),
                    "explicit_at": skewed,
                    "omitted_at": default_answers,
                }
            )
        print(
            f"W1_ANSWERS=expected:{expected!r}; host_skews:{host_skew_answers!r}",
            flush=True,
        )

    def test_w2_summary_uses_current_knowledge_for_its_free_tier_rule(self):
        route = prepare_route()
        state_at = test_database_now() - timedelta(days=3)
        effective_from = state_at - timedelta(days=1)
        recorded_rule = _append_free_tier_rule(effective_from)
        known_now = test_database_now()
        self.assertGreater(recorded_rule.recorded_at, state_at)

        correct = _free_rules_known_at(state_at, known_now).first()
        conflated = _free_rules_known_at(state_at, state_at).first()
        self.assertEqual(correct, recorded_rule)
        conflated_effective = (
            None if conflated is None else conflated.effective_from.isoformat()
        )
        self.assertNotEqual(correct.effective_from.isoformat(), conflated_effective)

        summary = entitlement_summary(route.organism.organism_id, at=state_at)
        self.assertEqual(
            summary["effective_at"],
            correct.effective_from.isoformat(),
        )
        self.assertNotEqual(summary["effective_at"], conflated_effective)
        print(
            "W2_ANSWERS="
            f"current_knowledge:{correct.effective_from.isoformat()}; "
            f"historical_knowledge:{conflated_effective}; "
            f"summary:{summary['effective_at']}",
            flush=True,
        )

    def test_w3_summary_uses_current_knowledge_for_future_free_tier_rules(self):
        route = prepare_route()
        state_at = test_database_now() - timedelta(days=3)
        future_effective = state_at + timedelta(days=1)
        recorded_rule = _append_free_tier_rule(future_effective)
        known_now = test_database_now()
        self.assertGreater(recorded_rule.recorded_at, state_at)

        correct = list(_free_rules_known_at(state_at, known_now, future=True))
        conflated = list(_free_rules_known_at(state_at, state_at, future=True))
        self.assertEqual(correct, [recorded_rule])
        self.assertEqual(conflated, [])

        summary = entitlement_summary(route.organism.organism_id, at=state_at)
        self.assertEqual(summary["next_transition"], future_effective.isoformat())
        print(
            "W3_ANSWERS="
            f"current_knowledge:{[rule.effective_from.isoformat() for rule in correct]!r}; "
            f"historical_knowledge:{[rule.effective_from.isoformat() for rule in conflated]!r}; "
            f"summary_next_transition:{summary['next_transition']}",
            flush=True,
        )

    def test_w4_summary_reads_clock_timestamp_exactly_once_with_or_without_at(self):
        route = prepare_route()
        answer_counts = []
        for at in (None, test_database_now()):
            with self.subTest(at_is_omitted=at is None):
                with CaptureQueriesContext(connection) as queries:
                    if at is None:
                        entitlement_summary(route.organism.organism_id)
                    else:
                        entitlement_summary(route.organism.organism_id, at=at)
                reads = [
                    query
                    for query in queries.captured_queries
                    if "clock_timestamp()" in query["sql"].lower()
                ]
                self.assertEqual(len(reads), 1)
                answer_counts.append(("omitted" if at is None else "explicit", len(reads)))
        print(f"W4_ANSWERS={answer_counts!r}", flush=True)

    def test_w5_explicit_at_keeps_historical_event_cutoffs_and_pins_omitted_form(self):
        route = prepare_route()
        base = _make_entitlement(
            route,
            effective_at=test_database_now() - timedelta(days=30),
        )
        state_at = test_database_now()
        occurred_at = test_database_now()
        while occurred_at <= state_at:
            occurred_at = test_database_now()
        future_effective = state_at + (occurred_at - state_at) / 2
        self.assertGreater(future_effective, state_at)
        self.assertLessEqual(future_effective, occurred_at)

        future_event = OrganismEntitlementEvent.objects.create(
            entitlement=base,
            sequence=1,
            predecessor=None,
            event_type="GRACE_BEGUN",
            actor_state="NOT_ESTABLISHED",
            actor_capacity="NA_NO_PARTY",
            entry_mode="TRANSPORT",
            enterer_identity=route.founder,
            composing_rule_state="NA_TRANSPORT",
            composer_state="NA_TRANSPORT",
            occurred_at=occurred_at,
            effective_at=future_effective,
            received_at=occurred_at,
        )
        future_event.refresh_from_db()
        self.assertGreater(future_event.recorded_at, state_at)

        recorded_now = test_database_now()
        extension_refusal = None
        try:
            extension = OrganismEntitlementEvent.objects.create(
                entitlement=base,
                sequence=2,
                predecessor=future_event,
                event_type="EXTENDED",
                actor_state="NOT_ESTABLISHED",
                actor_capacity="NA_NO_PARTY",
                entry_mode="TRANSPORT",
                enterer_identity=route.founder,
                composing_rule_state="NA_TRANSPORT",
                composer_state="NA_TRANSPORT",
                occurred_at=recorded_now,
                effective_at=state_at - timedelta(days=1),
                received_at=recorded_now,
                reason="retrospective fixture extension",
            )
            extension.refresh_from_db()
        except IntegrityError as exc:
            extension_refusal = str(exc)
            extension = SimpleNamespace(
                sequence=2,
                effective_at=state_at - timedelta(days=1),
                received_at=recorded_now,
                reason="retrospective fixture extension",
                recorded_at=recorded_now,
            )
        if extension_refusal is None:
            print("W5_EXTENSION_SOURCE=real EXTENDED event row", flush=True)
        else:
            print(
                "W5_EXTENSION_SOURCE=stand-in for EXTENDED-event query rows; "
                f"database refusal: {extension_refusal}",
                flush=True,
            )
        self.assertLess(extension.effective_at, state_at)
        self.assertGreater(extension.recorded_at, state_at)
        extension_at = [
            event for event in (extension,) if event.recorded_at <= state_at
        ]
        extension_now = [
            event for event in (extension,) if event.recorded_at <= recorded_now
        ]
        transition_at = [
            event.effective_at
            for event in (future_event,)
            if event.effective_at > state_at and event.recorded_at <= state_at
        ]
        transition_now = [
            event.effective_at
            for event in (future_event,)
            if event.effective_at > state_at and event.recorded_at <= recorded_now
        ]
        self.assertNotEqual(extension_at, extension_now)
        self.assertNotEqual(transition_at, transition_now)

        original_filter = QuerySet.filter

        def filter_with_extension_fixture(queryset, *args, **kwargs):
            if (
                queryset.model is OrganismEntitlementEvent
                and kwargs.get("event_type") == "EXTENDED"
            ):
                cutoff = kwargs["recorded_at__lte"]
                rows = [
                    event
                    for event in (extension,)
                    if event.recorded_at <= cutoff
                ]
                return _ExtensionFixtureQuerySet(rows)
            return original_filter(queryset, *args, **kwargs)

        if extension_refusal is None:
            historical = entitlement_summary(
                route.organism.organism_id,
                at=state_at,
            )
            omitted = entitlement_summary(route.organism.organism_id)
        else:
            with patch.object(QuerySet, "filter", new=filter_with_extension_fixture):
                historical = entitlement_summary(
                    route.organism.organism_id,
                    at=state_at,
                )
                omitted = entitlement_summary(route.organism.organism_id)

        self.assertEqual(historical["extensions"], [])
        self.assertEqual(
            historical["next_transition"],
            None if not transition_at else transition_at[0].isoformat(),
        )
        self.assertEqual(
            omitted["extensions"],
            [
                {
                    "sequence": extension.sequence,
                    "effective_at": extension.effective_at.isoformat(),
                    "received_at": extension.received_at.isoformat(),
                    "reason": extension.reason,
                }
            ],
        )
        self.assertIsNone(omitted["next_transition"])
        self.assertEqual(
            transition_now,
            [future_event.effective_at],
        )
        print(
            "W5_ANSWERS="
            f"explicit_at_extensions:{historical['extensions']!r}; "
            f"omitted_at_extensions:{omitted['extensions']!r}; "
            f"explicit_at_next_transition:{historical['next_transition']!r}; "
            f"omitted_at_next_transition:{omitted['next_transition']!r}; "
            f"extension_visibility:{extension_at!r}/{extension_now!r}; "
            f"transition_visibility:{transition_at!r}/{transition_now!r}",
            flush=True,
        )

    def test_w7_stated_past_and_future_times_reach_sql_unchanged(self):
        route = prepare_route()
        current = test_database_now()
        stated_times = (
            current - timedelta(days=4),
            current + timedelta(days=4),
        )

        class CaptureSql:
            def __init__(self):
                self.calls = []
                self.clock_reads = 0

            def __call__(self, execute, sql, params, many, context):
                lowered = sql.lower()
                if "clock_timestamp()" in lowered:
                    self.clock_reads += 1
                if (
                    "s015_entitlement_effective" in lowered
                    or "s015_entitlement_limit" in lowered
                ):
                    self.calls.append((lowered, tuple(params)))
                return execute(sql, params, many, context)

        answers = []
        for at in stated_times:
            observed_parameters = []
            for function in (
                lambda value: entitlement_effective(
                    route.organism.organism_id,
                    "APPLICATION_CIRCLE",
                    at=value,
                ),
                lambda value: entitlement_limit(
                    route.organism.organism_id,
                    "circles",
                    at=value,
                ),
            ):
                capture = CaptureSql()
                with connection.execute_wrapper(capture):
                    function(at)
                self.assertEqual(capture.clock_reads, 0)
                self.assertEqual(len(capture.calls), 1)
                self.assertEqual(capture.calls[0][1][-1], at)
                observed_parameters.append(capture.calls[0][1][-1])
            answers.append(tuple(observed_parameters))
        print(f"W7_ANSWERS={answers!r}", flush=True)

    def test_w8_target_functions_make_no_host_clock_calls_and_control_is_seen(self):
        route = prepare_route()
        now = test_database_now()
        _make_entitlement(
            route,
            effective_at=now - timedelta(hours=1),
            effective_until=now + timedelta(hours=1),
        )
        root = Path(__file__).resolve().parents[1]

        def monitored_clock():
            reads = []
            original_now = timezone.now

            def record_now():
                frame = inspect.currentframe().f_back
                while frame is not None:
                    try:
                        rel = Path(frame.f_code.co_filename).resolve().relative_to(root)
                    except ValueError:
                        frame = frame.f_back
                        continue
                    if rel.parts and rel.parts[0] in {"src", "core", "intevia"}:
                        if not (len(rel.parts) > 1 and rel.parts[:2] == ("core", "migrations")):
                            reads.append(rel.as_posix())
                        break
                    frame = frame.f_back
                return original_now()

            return reads, patch.object(timezone, "now", side_effect=record_now)

        host_clock_callables = {
            id(timezone.now): "django.utils.timezone.now",
            id(timezone.localtime): "django.utils.timezone.localtime",
            id(timezone.localdate): "django.utils.timezone.localdate",
            id(time.time): "time.time",
            id(time.time_ns): "time.time_ns",
            id(time.gmtime): "time.gmtime",
            id(time.localtime): "time.localtime",
            id(time.ctime): "time.ctime",
            id(time.strftime): "time.strftime",
        }
        expected_host_clock_names = set(host_clock_callables.values()) | {
            "datetime.datetime.now",
            "datetime.datetime.utcnow",
            "datetime.datetime.today",
            "datetime.date.today",
        }
        allowed_host_clock_sites = {
            ("core/session.py", "bind_identity_session", "time.time"),
            (
                "core/session.py",
                "IdentityAccessMiddleware._valid_identity",
                "time.time",
            ),
        }

        def host_clock_name(called):
            module_function_name = host_clock_callables.get(id(called))
            if module_function_name is not None:
                return module_function_name
            owner = getattr(called, "__self__", None)
            method_name = getattr(called, "__name__", None)
            if owner is datetime and method_name in {"now", "utcnow", "today"}:
                return f"datetime.datetime.{method_name}"
            if owner is date and method_name == "today":
                return "datetime.date.today"
            return None

        def run_with_clock_monitor(call):
            monitoring = getattr(sys, "monitoring", None)
            if monitoring is None:
                reads, clock_patch = monitored_clock()
                with clock_patch:
                    call()
                return [
                    ("django.utils.timezone.now", path)
                    for path in reads
                ]

            tool_id = next(
                (
                    candidate
                    for candidate in range(6)
                    if monitoring.get_tool(candidate) is None
                ),
                None,
            )
            if tool_id is None:
                self.fail("sys.monitoring has no available tool id")
            monitoring.use_tool_id(tool_id, f"clk1-w8-{uuid4().hex}")
            reads = []

            def record_call(_code, _instruction_offset, called, _arg0):
                callable_name = host_clock_name(called)
                if callable_name is None:
                    return
                frame = sys._getframe(1)
                while frame is not None:
                    try:
                        relative = Path(frame.f_code.co_filename).resolve().relative_to(root)
                    except ValueError:
                        frame = frame.f_back
                        continue
                    if relative.parts and relative.parts[0] in {
                        "src",
                        "core",
                        "intevia",
                    }:
                        if relative.parts[:2] == ("core", "migrations"):
                            return
                        call_site = (
                            relative.as_posix(),
                            frame.f_code.co_qualname,
                            callable_name,
                        )
                        if call_site not in allowed_host_clock_sites:
                            reads.append((callable_name, relative.as_posix()))
                        return
                    frame = frame.f_back

            try:
                monitoring.register_callback(
                    tool_id,
                    monitoring.events.CALL,
                    record_call,
                )
                monitoring.set_events(tool_id, monitoring.events.CALL)
                call()
            finally:
                try:
                    monitoring.set_events(tool_id, 0)
                finally:
                    monitoring.free_tool_id(tool_id)
            return reads

        calls = {
            "entitlement_effective": lambda: entitlement_effective(
                route.organism.organism_id,
                "APPLICATION_CIRCLE",
            ),
            "entitlement_limit": lambda: entitlement_limit(
                route.organism.organism_id,
                "circles",
            ),
            "entitlement_summary": lambda: entitlement_summary(
                route.organism.organism_id,
            ),
            "capability_status": lambda: capability_status(
                route.organism.organism_id,
            ),
        }
        call_counts = []
        for name, call in calls.items():
            with self.subTest(function=name):
                reads = run_with_clock_monitor(call)
                self.assertEqual(reads, [])
                call_counts.append((name, len(reads)))

        control_path = root / "src" / "__clk1_clock_guard_control__.py"
        namespace = {
            "timezone": timezone,
            "datetime": datetime,
            "date": date,
            "time": time,
        }
        exec(
            compile(
                "def host_clock_control():\n"
                "    timezone.now()\n"
                "    timezone.localtime()\n"
                "    timezone.localdate()\n"
                "    datetime.now()\n"
                "    datetime.utcnow()\n"
                "    datetime.today()\n"
                "    date.today()\n"
                "    time.time()\n"
                "    time.time_ns()\n"
                "    time.gmtime()\n"
                "    time.localtime()\n"
                "    time.ctime()\n"
                "    time.strftime('%Y')\n",
                str(control_path),
                "exec",
            ),
            namespace,
        )
        reads = run_with_clock_monitor(namespace["host_clock_control"])
        control_counts = Counter(name for name, _ in reads)
        if getattr(sys, "monitoring", None) is None:
            self.assertEqual(set(control_counts), {"django.utils.timezone.now"})
            self.assertGreater(control_counts["django.utils.timezone.now"], 0)
        else:
            self.assertEqual(set(control_counts), expected_host_clock_names)
            self.assertTrue(
                all(
                    control_counts[name] > 0
                    for name in expected_host_clock_names
                ),
                control_counts,
            )
        self.assertTrue(
            all(path == "src/__clk1_clock_guard_control__.py" for _, path in reads),
            reads,
        )
        print(
            f"W8_ANSWERS=production:{call_counts!r}; "
            f"positive_control:{dict(sorted(control_counts.items()))!r}",
            flush=True,
        )
