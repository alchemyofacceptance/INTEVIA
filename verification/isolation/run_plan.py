"""No-database selection, verification, and pre-check helpers for TU-1."""

from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
import hashlib
import importlib
import inspect
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from verification.route import PRIMARY_TEST_DATABASE_NAME_MAX

LABEL_RE = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+$")
MAX_LABELS = 20
_CONNECTION_GUARD_ACTIVE: ContextVar[bool] = ContextVar(
    "tu1_connection_guard_active", default=False
)


def _standalone_test_database_name(action: str, run_suffix: str) -> str:
    digest = hashlib.sha256(f"{action}\0{run_suffix}".encode("utf-8")).hexdigest()[:10]
    name = f"test_intevia_living_organism_tu1_{digest}"
    if len(name) > PRIMARY_TEST_DATABASE_NAME_MAX:
        raise RuntimeError("standalone test database name exceeds the primary-name bound")
    return name


@contextmanager
def _standalone_action_environment(root: Path, action: str):
    if action not in {"plan", "precheck", "validate-plan"}:
        raise ValueError("R9: standalone environment is only valid for plan or precheck")
    names = (
        "DJANGO_SETTINGS_MODULE",
        "INTEVIA_S015_TEST_ROUTE",
        "INTEVIA_DATABASE_ENGINE",
        "INTEVIA_POSTGRES_TEST_DB",
        "PYTHONPATH",
    )
    original = {name: os.environ.get(name) for name in names}
    repository_root = str(root.resolve())
    test_root = str((root / "tests").resolve())
    python_paths = [repository_root, test_root]
    run_suffix = os.environ.get("INTEVIA_RUN_SUFFIX")
    if (
        not run_suffix
        or len(run_suffix) > 12
        or re.fullmatch(r"[1-9][0-9]*[ab]?", run_suffix) is None
    ):
        run_suffix = f"p{os.getpid()}"
    if original["PYTHONPATH"]:
        python_paths.append(original["PYTHONPATH"])
    os.environ.update(
        {
            "DJANGO_SETTINGS_MODULE": "intevia.test_settings",
            "INTEVIA_S015_TEST_ROUTE": "LIVING_ORGANISM_TEST_LIFECYCLE",
            "INTEVIA_DATABASE_ENGINE": "postgresql",
            "INTEVIA_POSTGRES_TEST_DB": _standalone_test_database_name(
                action, run_suffix
            ),
            "PYTHONPATH": os.pathsep.join(python_paths),
        }
    )
    try:
        yield
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _setup_test_django(requirement: str = "R3/R4"):
    os.environ["DJANGO_SETTINGS_MODULE"] = "intevia.test_settings"
    os.environ["INTEVIA_S015_TEST_ROUTE"] = "LIVING_ORGANISM_TEST_LIFECYCLE"
    os.environ["INTEVIA_DATABASE_ENGINE"] = "postgresql"
    if not os.environ.get("INTEVIA_POSTGRES_TEST_DB"):
        os.environ["INTEVIA_POSTGRES_TEST_DB"] = _standalone_test_database_name(
            "plan", "precheck"
        )

    import django
    from django.db import connections

    with _forbid_database_connections(connections, {"count": 0}, requirement):
        django.setup()
    from django.conf import settings

    if (
        settings.SETTINGS_MODULE != "intevia.test_settings"
        or settings.S015_TEST_ROUTE != "LIVING_ORGANISM_TEST_LIFECYCLE"
    ):
        raise RuntimeError("R9: Django is not configured with the TU-1 test settings")
    return connections


@contextmanager
def _forbid_database_connections(connections, attempts: dict[str, int], requirement: str):
    if _CONNECTION_GUARD_ACTIVE.get():
        yield
        return
    for alias in connections:
        if connections[alias].connection is not None:
            raise RuntimeError(
                f"{requirement}: loader found an already-open connection on {alias}"
            )

    def forbidden(alias: str):
        def fail(*_: object, **__: object) -> None:
            attempts["count"] += 1
            raise RuntimeError(
                f"{requirement}: database connection forbidden on alias {alias}"
            )

        return fail

    token = _CONNECTION_GUARD_ACTIVE.set(True)
    try:
        with ExitStack() as stack:
            for alias in connections:
                stack.enter_context(
                    patch.object(
                        connections[alias],
                        "ensure_connection",
                        side_effect=forbidden(alias),
                    )
                )
            yield
    finally:
        _CONNECTION_GUARD_ACTIVE.reset(token)


def resolve_labels(labels: list[str]) -> list[str]:
    if len(labels) > MAX_LABELS:
        raise ValueError(
            "R3: at most twenty labels may be expanded; use a resolved plan file for larger selections"
        )
    if not labels or len(set(labels)) != len(labels):
        raise ValueError("R3: labels must be present and unique")
    expanded: list[str] = []
    for label in labels:
        if not LABEL_RE.fullmatch(label):
            raise ValueError("R3: label must be module.Class or module.Class.method")
        segments = label.split(".")
        module = None
        module_end = 0
        for end in range(len(segments) - 1, 0, -1):
            try:
                module = importlib.import_module(".".join(segments[:end]))
                module_end = end
                break
            except ModuleNotFoundError:
                continue
        if module is None:
            raise ValueError("R3: label does not resolve to an importable test module")
        if module_end < len(segments):
            expanded.append(label)
            continue
        module_name = module.__name__
        classes = [
            (name, value)
            for name, value in inspect.getmembers(module, inspect.isclass)
            if value.__module__ == module_name
            and issubclass(value, unittest.TestCase)
            and any(name.startswith("test") for name in dir(value))
        ]
        if not classes:
            raise ValueError("R3: module label did not resolve to test classes")
        expanded.extend(f"{module_name}.{name}" for name, _ in classes)
    if len(set(expanded)) != len(expanded):
        raise ValueError("R3: labels resolve to duplicate test classes")
    return expanded


def _loader_cases(labels: list[str]) -> list[unittest.TestCase]:
    from django.test.runner import DiscoverRunner
    from django.test.utils import iter_test_cases

    suite = DiscoverRunner(verbosity=0, interactive=False, parallel=1).build_suite(labels)
    cases = list(iter_test_cases(suite))
    ids = [case.id() for case in cases]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("R3: resolved selection is empty or contains duplicate unique tests")
    failed = [
        identifier
        for case, identifier in zip(cases, ids)
        if type(case).__name__ == "_FailedTest"
        and type(case).__module__ == "unittest.loader"
    ]
    if failed:
        raise ValueError("R3: test selection contains failed loader entries: %s" % failed[:5])
    return cases


def _loader_suite(labels: list[str]) -> list[str]:
    return [case.id() for case in _loader_cases(labels)]


def _run_precheck_suite(suite: unittest.TestSuite) -> unittest.TestResult:
    return unittest.TextTestRunner(
        stream=sys.stderr, verbosity=2, failfast=True
    ).run(suite)


def build_plan(
    labels: list[str],
    *,
    groups: list[list[str]] | None = None,
    connection_attempts: dict[str, int] | None = None,
) -> dict[str, Any]:
    from .runner import database_access

    connections = _setup_test_django("R3/R4")
    attempts = connection_attempts if connection_attempts is not None else {"count": 0}
    with _forbid_database_connections(connections, attempts, "R3/R4"):
        expanded = resolve_labels(labels)
        cases = _loader_cases(expanded)
        tests = [case.id() for case in cases]
        database_route = (
            "DATABASE"
            if any(database_access(case)[1] for case in cases)
            else "NO_DATABASE"
        )
    normalized_groups = [list(group) for group in (groups or [])]
    grouped: set[str] = set()
    for group in normalized_groups:
        if not group or any(test not in tests for test in group):
            raise ValueError("R6: ordered group is empty or contains an unresolved test")
        positions = [tests.index(test) for test in group]
        if positions != list(range(positions[0], positions[0] + len(positions))):
            raise ValueError("R6: ordered group must be contiguous and retain loader order")
        if grouped.intersection(group):
            raise ValueError("R6: ordered groups overlap")
        grouped.update(group)
    return {
        "schema_version": 1,
        "labels": labels,
        "expanded_labels": expanded,
        "tests": tests,
        "database_route": database_route,
        "ordered_groups": normalized_groups,
        "counts": {"tests": len(tests), "groups": len(normalized_groups)},
    }


def write_plan(plan: dict[str, Any], path: str | Path) -> str:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(plan, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    destination.write_bytes(payload)
    return hashlib.sha256(destination.read_bytes()).hexdigest()


def load_plan(path: str | Path) -> tuple[dict[str, Any], str]:
    raw = Path(path).read_bytes()
    plan = json.loads(raw.decode("utf-8"))
    if plan.get("schema_version") != 1 or not isinstance(plan.get("tests"), list):
        raise ValueError("R4: plan file schema is invalid")
    database_route = plan.get("database_route")
    if database_route is not None and database_route not in ("DATABASE", "NO_DATABASE"):
        raise ValueError("R4: plan database route is invalid")
    tests = plan["tests"]
    if not tests or len(tests) != len(set(tests)):
        raise ValueError("R4: plan is empty or contains duplicate identifiers")
    return plan, hashlib.sha256(raw).hexdigest()


def validate_plan_file(
    path: str | Path,
) -> tuple[dict[str, Any], str]:
    plan, digest = load_plan(path)
    labels = plan.get("labels")
    if (
        not isinstance(labels, list)
        or not labels
        or any(not isinstance(label, str) or not LABEL_RE.fullmatch(label) for label in labels)
        or len(labels) != len(set(labels))
    ):
        raise ValueError("R3: plan file labels are missing, invalid, or duplicated")

    expanded_labels: list[str] = []
    resolved_tests: list[str] = []
    resolved_cases: list[unittest.TestCase] = []
    attempts = {"count": 0}
    connections = _setup_test_django("R3/R4")
    with _forbid_database_connections(connections, attempts, "R3/R4"):
        for offset in range(0, len(labels), MAX_LABELS):
            expanded = resolve_labels(labels[offset : offset + MAX_LABELS])
            cases = _loader_cases(expanded)
            expanded_labels.extend(expanded)
            resolved_tests.extend(case.id() for case in cases)
            resolved_cases.extend(cases)

    if attempts["count"]:
        raise ValueError("R3/R4: plan validation attempted a database connection")
    if expanded_labels != plan.get("expanded_labels"):
        raise ValueError("R4: plan expanded labels differ from the current loader resolution")
    ensure_suite_matches(plan["tests"], resolved_tests)

    from .runner import database_access

    database_route = (
        "DATABASE"
        if any(database_access(case)[1] for case in resolved_cases)
        else "NO_DATABASE"
    )
    if plan.get("database_route") != database_route:
        raise ValueError("R4: plan database route differs from the current selection")

    groups = plan.get("ordered_groups", [])
    if not isinstance(groups, list):
        raise ValueError("R6: plan ordered groups are invalid")
    grouped: set[str] = set()
    for group in groups:
        if (
            not isinstance(group, list)
            or not group
            or any(test not in plan["tests"] for test in group)
        ):
            raise ValueError("R6: ordered group is empty or contains an unresolved test")
        positions = [plan["tests"].index(test) for test in group]
        if positions != list(range(positions[0], positions[0] + len(positions))):
            raise ValueError("R6: ordered group must be contiguous and retain loader order")
        if grouped.intersection(group):
            raise ValueError("R6: ordered groups overlap")
        grouped.update(group)

    return plan, digest


def validate_run_record_consistency(record: dict[str, Any]) -> None:
    if record.get("classification") == "PRE_RUN_REFUSAL":
        return
    has_isolation = "isolation_verdict" in record
    has_dev_verdict = "dev_run_verdict" in record
    if not has_isolation and not has_dev_verdict:
        return
    if not has_isolation or not has_dev_verdict:
        raise ValueError("R8: run record has incomplete final-verdict evidence")

    result = record.get("result")
    isolation_verdict = record.get("isolation_verdict")
    dev_run_verdict = record.get("dev_run_verdict")
    if dev_run_verdict == "PASS" and result != "PASS":
        raise ValueError("R8: run record result contradicts DEV_RUN_VERDICT")
    if result != "PASS":
        return

    ordinary = record.get("ordinary_test_verdict")
    # HG_DIRECTION_TU1B_SCOPE_AND_RC1_TEXTS_20261015 (15 Oct 2026); commission section 1: a refused required control or failed isolation cannot yield an overall PASS.
    if (
        isolation_verdict != "PASS"
        or dev_run_verdict != "PASS"
        or record.get("cp0_requirements_met") is not True
        or record.get("required_controls_passed") is not True
        or not isinstance(ordinary, dict)
        or ordinary.get("was_successful") is not True
        or ordinary.get("errors") != 0
        or ordinary.get("failures") != 0
        or record.get("tests_not_reached") != 0
        or record.get("tests_executed") != record.get("tests_planned")
        or record.get("executed_matches_plan") != "FULL"
    ):
        raise ValueError("R8: PASS run record lacks complete successful control evidence")


def _resolve_parent_test_identifier(
    identifier: Any, plan_ids: list[str], requirement: str
) -> str:
    if not isinstance(identifier, str) or not identifier:
        raise ValueError(f"{requirement}: test identifier is missing")
    exact_matches = [test_id for test_id in plan_ids if identifier == test_id]
    if len(exact_matches) == 1:
        return exact_matches[0]
    if exact_matches:
        raise ValueError(f"{requirement}: test identifier has ambiguous parent matches")
    parent_matches = [
        test_id for test_id in plan_ids if identifier.startswith(f"{test_id} (")
    ]
    if len(parent_matches) != 1:
        detail = "ambiguous" if parent_matches else "missing"
        raise ValueError(
            f"{requirement}: sub-test identifier has {detail} parent plan matches"
        )
    return parent_matches[0]
def assert_execution(
    plan_ids: list[str],
    executed_ids: list[str],
    *,
    complete: bool,
    stop_id: str | None = None,
) -> str:
    resolved_executed_ids = [
        _resolve_parent_test_identifier(test_id, plan_ids, "R7")
        for test_id in executed_ids
    ]
    resolved_stop_id = (
        _resolve_parent_test_identifier(stop_id, plan_ids, "R7")
        if stop_id is not None
        else None
    )
    if complete and resolved_executed_ids == plan_ids:
        return "FULL"
    if (
        not complete
        and resolved_stop_id
        and resolved_executed_ids
        and resolved_executed_ids == plan_ids[: len(resolved_executed_ids)]
        and resolved_executed_ids[-1] == resolved_stop_id
    ):
        return "PREFIX"
    raise ValueError("R7: executed identifiers are extra, gapped, or reordered")


def verify_kind(
    kind: str,
    selected: list[str],
    full: list[str],
    *,
    delta_authorized: bool = False,
    impact: list[str] | None = None,
) -> None:
    if kind == "FULL":
        if selected != full:
            raise ValueError("R10: full verification requires the complete ordered suite")
    elif kind == "DELTA":
        if not delta_authorized or impact is None or selected != impact:
            raise ValueError("R10: delta selection must match its authorized impact record")
    else:
        raise ValueError("R10: verification kind must be FULL or DELTA")


def ensure_suite_matches(plan_ids: list[str], actual_ids: list[str]) -> None:
    if actual_ids != plan_ids:
        raise ValueError("R7: built suite differs from validated plan")


def validate_verification_summary(
    summary: dict[str, Any], plan_sha256: str, resolved_tests: object
) -> None:
    if type(resolved_tests) is not int:
        raise ValueError("R7: the runner's resolved test count is unavailable")
    if summary.get("plan_sha256") != plan_sha256:
        raise ValueError("R7: isolation summary plan digest differs from the validated plan")
    planned_tests = summary.get("tests_planned")
    if type(planned_tests) is not int or planned_tests != resolved_tests:
        raise ValueError("R7: summary tests_planned differs from the runner's resolved test count")
    if summary.get("executed_matches_plan") != "FULL":
        raise ValueError("R7: summary executed_matches_plan is not FULL")
    if summary.get("cpz_established") is not True:
        raise ValueError("R7: CP-Z was not established after the run completed")


def validate_run_spec_binding(state: dict[str, Any], specification: bytes) -> str:
    expected = state.get("run_spec_sha256")
    if (
        state.get("run_spec_version") != "v1.38"
        or not isinstance(expected, str)
        or re.fullmatch(r"[0-9a-fA-F]{64}", expected) is None
    ):
        raise ValueError("R11: state file lacks the v1.38 specification binding")
    actual = hashlib.sha256(specification).hexdigest()
    if actual != expected.lower():
        raise ValueError("R11: repository specification digest differs from state")
    return actual


def validate_run_spec(state_path: str | Path, spec_path: str | Path) -> str:
    state = json.loads(Path(state_path).read_text(encoding="utf-8"))
    return validate_run_spec_binding(state, Path(spec_path).read_bytes())


def validate_fresh_authority(
    path: str | Path | None, digest: str | None, run_suffix: str
) -> str:
    if not path or not digest:
        raise ValueError("R2: fresh mode requires a bound authority file")
    raw = Path(path).read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != digest.lower():
        raise ValueError("R2: fresh authority digest mismatch")
    text = raw.decode("utf-8")
    if "fresh" not in text.lower() or run_suffix not in text:
        raise ValueError("R2: authority does not name fresh mode and this run")
    return actual


def validate_delta_impact(plan_path: str | Path, impact_path: str | Path) -> None:
    plan, _ = load_plan(plan_path)
    impact = json.loads(Path(impact_path).read_text(encoding="utf-8"))
    selected = impact.get("tests")
    reasons = impact.get("reasons")
    if not isinstance(selected, list) or not selected:
        raise ValueError("R10: impact record must name its test selection")
    if isinstance(reasons, dict):
        complete_reasons = all(isinstance(reasons.get(test), str) and reasons[test].strip() for test in selected)
    elif isinstance(selected[0], dict):
        complete_reasons = all(isinstance(item.get("reason"), str) and item["reason"].strip() for item in selected)
        selected = [item.get("id") for item in selected]
    else:
        complete_reasons = False
    if not complete_reasons or selected != plan["tests"]:
        raise ValueError("R10: delta plan differs from the authorized impact selection or lacks reasons")


def _changed_test_modules(root: Path, base: str, allowed: list[str]) -> list[str]:
    changed = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", base],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    paths = sorted({*changed, *untracked})
    return [
        path[:-3].replace("/", ".")
        for path in paths
        if path in allowed and path.startswith("tests/") and path.endswith(".py")
    ]


def candidate_binding(root: str | Path, state_path: str | Path) -> str:
    root = Path(root)
    state = json.loads(Path(state_path).read_text(encoding="utf-8"))
    diff = subprocess.run(
        ["git", "--no-pager", "diff", "--binary", state["base_commit"]],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    diff_digest = hashlib.sha256(diff).hexdigest()
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    rows = sorted(
        f"{hashlib.sha256((root / path).read_bytes()).hexdigest()}:{path}"
        for path in untracked
    )
    payload = "\n".join(
        [
            f"state_sha256={hashlib.sha256(Path(state_path).read_bytes()).hexdigest()}",
            f"baseline={state['base_commit']}",
            f"diff={diff_digest}",
            *rows,
        ]
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def run_no_database_precheck(root: Path, state_path: str | Path) -> tuple[list[str], str, int]:
    state = json.loads(Path(state_path).read_text(encoding="utf-8"))
    database_tag = state.get("database_tag")
    if not isinstance(database_tag, str) or not database_tag:
        raise ValueError("R9: standalone precheck requires the authorized state's database_tag")
    modules = _changed_test_modules(root, state["base_commit"], state["allowed_paths"])
    tests_path = str((root / "tests").resolve())
    if tests_path not in sys.path:
        sys.path.insert(0, tests_path)
    connections = _setup_test_django("R14")
    classes: list[type[unittest.TestCase]] = []
    attempts = {"count": 0}
    with patch.dict(os.environ, {"INTEVIA_DATABASE_TAG": database_tag}):
        with _forbid_database_connections(connections, attempts, "R14"):
            for module_name in modules:
                module = importlib.import_module(module_name)
                for _, candidate in inspect.getmembers(module, inspect.isclass):
                    if not issubclass(candidate, unittest.TestCase) or candidate in classes:
                        continue
                    if candidate.__module__ != module_name:
                        continue
                    databases = getattr(candidate, "databases", set())
                    if databases == set():
                        classes.append(candidate)
            names = [f"{cls.__module__}.{cls.__qualname__}" for cls in classes]
            if not classes:
                return names, "NONE_QUALIFY", attempts["count"]

            suite = unittest.TestSuite()
            loader = unittest.defaultTestLoader
            for cls in classes:
                suite.addTests(loader.loadTestsFromTestCase(cls))
            result = _run_precheck_suite(suite)
    return names, "PASS" if result.wasSuccessful() and attempts["count"] == 0 else "FAIL", attempts["count"]


def self_test() -> int:
    test_environment = {
        "DJANGO_SETTINGS_MODULE": "intevia.test_settings",
        "INTEVIA_S015_TEST_ROUTE": "LIVING_ORGANISM_TEST_LIFECYCLE",
        "INTEVIA_DATABASE_ENGINE": "postgresql",
        "INTEVIA_POSTGRES_TEST_DB": _standalone_test_database_name(
            "plan", "selftest"
        ),
    }
    original_environment = {
        name: os.environ.get(name) for name in test_environment
    }
    os.environ.update(test_environment)
    try:
        return _run_self_test()
    finally:
        for name, value in original_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _run_self_test() -> int:
    connections = _setup_test_django("R3/R4")
    cases: list[tuple[str, bool, str, str, bool, str | None]] = []

    def add(
        label: str,
        expect_refusal: bool,
        refused_form: str,
        accepted_form: str,
        call: Any,
    ) -> None:
        message = None
        try:
            call()
            refused = False
        except (AssertionError, ValueError, RuntimeError) as exc:
            refused = True
            message = f"{type(exc).__name__}: {exc}"
        cases.append((label, expect_refusal, refused_form, accepted_form, refused, message))

    add("R3/R4 labels", True, "module does not resolve or duplicate unique IDs",
        "module label expands and loader returns unique IDs",
        lambda: resolve_labels(["bad-label"]))
    def real_label_plan() -> None:
        attempts = {"count": 0}
        label = (
            "tests.test_s015_0022_contract.S0150022ContractTests."
            "test_l2_preimage_refuses_every_body_while_u14_stands"
        )
        plan = build_plan([label], connection_attempts=attempts)
        if plan["tests"] != [label] or attempts["count"] != 0:
            raise AssertionError("R3/R4: real-label plan was not singular and connection-free")

    add("R3/R4 real-label plan", False, "plan label import before Django setup or database access",
        "real label resolves to exactly one test with zero connection attempts",
        real_label_plan)
    add("R2 fresh mode", True, "fresh without authority, bad digest, or wrong run",
        "bound authority names fresh mode and this suffix",
        lambda: validate_fresh_authority(None, None, "7"))
    def complete_reporting() -> None:
        # Exercise the actual isolation result class with controlled checkpoints, without a database.
        from verification.selftest_route import R1CompleteReportingTests
        import io

        suite = unittest.defaultTestLoader.loadTestsFromTestCase(R1CompleteReportingTests)
        result = unittest.TextTestRunner(stream=io.StringIO(), failfast=False).run(suite)
        if not result.wasSuccessful() or result.testsRun == 0:
            raise AssertionError("R1: ordinary-failure continuation or isolation-stop probe failed")

    add("R1 complete reporting", False, "a run that stops at the first ordinary failure",
        "ordinary failures are all reported; isolation refusals still stop the run",
        complete_reporting)
    add("R11 required binding fields", True, "state lacks run_spec_version or run_spec_sha256",
        "every authorized state binds the v1.38 copy by SHA-256",
        lambda: validate_run_spec_binding({}, b"specification"))
    def refused_control_cannot_be_pass() -> None:
        validate_run_record_consistency(
            {
                "classification": "TEST_ATTEMPT",
                "result": "PASS",
                "isolation_verdict": "NOT ESTABLISHED",
                "dev_run_verdict": "FAIL",
            }
        )

    add(
        "T4/T14 refused control run record",
        True,
        "required control or isolation refusal recorded as PASS",
        "refused control cannot produce a PASS record",
        refused_control_cannot_be_pass,
    )

    def consistent_pass_record() -> None:
        validate_run_record_consistency(
            {
                "classification": "TEST_ATTEMPT",
                "result": "PASS",
                "isolation_verdict": "PASS",
                "dev_run_verdict": "PASS",
                "cp0_requirements_met": True,
                "required_controls_passed": True,
                "ordinary_test_verdict": {
                    "was_successful": True,
                    "errors": 0,
                    "failures": 0,
                },
                "tests_planned": 1,
                "tests_executed": 1,
                "tests_not_reached": 0,
                "executed_matches_plan": "FULL",
            }
        )

    add(
        "T4/T14 consistent pass record",
        False,
        "successful evidence is inconsistently classified",
        "PASS requires every control and isolation to pass",
        consistent_pass_record,
    )

    def valid_binding_without_instruction_authority() -> None:
        specification = b"v1.38 run specification"
        state = {
            "run_spec_version": "v1.38",
            "run_spec_sha256": hashlib.sha256(specification).hexdigest(),
            "allowed_paths": ["scripts/integrated_state.ps1"],
        }
        validate_run_spec_binding(state, specification)

    add("R11 instruction path not required", False,
        "R11 requires instruction-file authority in every state",
        "valid run-spec binding passes without the instruction path",
        valid_binding_without_instruction_authority)
    add("R7 suite-plan binding", True, "built suite differs from plan",
        "built suite exactly equals validated plan",
        lambda: ensure_suite_matches(["a", "b"], ["a", "c"]))
    add("R7 executed IDs", True, "extra, gapped, reordered, or non-stopping prefix",
        "full plan or prefix ending at stopping test",
        lambda: assert_execution(["a", "b"], ["b"], complete=False, stop_id="b"))

    def verification_summary_cross_check() -> None:
        summary = {
            "plan_sha256": "plan-digest",
            "tests_planned": 3,
            "executed_matches_plan": "FULL",
            "cpz_established": True,
        }
        validate_verification_summary(summary, "plan-digest", 3)
        refusal_cases = (
            (summary, None, "R7: the runner's resolved test count is unavailable"),
            (summary, "UNAVAILABLE", "R7: the runner's resolved test count is unavailable"),
            (
                {**summary, "plan_sha256": "different-plan"},
                3,
                "R7: isolation summary plan digest differs from the validated plan",
            ),
            (
                {**summary, "tests_planned": 2},
                3,
                "R7: summary tests_planned differs from the runner's resolved test count",
            ),
            (
                {**summary, "executed_matches_plan": "PREFIX"},
                3,
                "R7: summary executed_matches_plan is not FULL",
            ),
            (
                {**summary, "cpz_established": False},
                3,
                "R7: CP-Z was not established after the run completed",
            ),
        )
        for invalid_summary, resolved_tests, expected_message in refusal_cases:
            try:
                validate_verification_summary(
                    invalid_summary, "plan-digest", resolved_tests
                )
            except ValueError as error:
                if str(error) != expected_message:
                    raise AssertionError(
                        "R7: verifier summary refusal message differs"
                    ) from error
            else:
                raise AssertionError("R7: invalid verifier summary was accepted")

    add(
        "R7 verifier summary",
        False,
        "missing resolved count, mismatched plan digest or count, or non-FULL execution",
        "matching plan digest, resolved count, and FULL execution",
        verification_summary_cross_check,
    )

    def subtest_parent_binding() -> None:
        attempts = {"count": 0}
        parent_tests = ["suite.Case.test_before", "suite.Case.test_w1", "suite.Case.test_after"]
        decorated_failure = (
            "suite.Case.test_w1 (skew=datetime.timedelta(days=-1, seconds=79200), "
            "function='limit')"
        )
        with _forbid_database_connections(connections, attempts, "R7"):
            if assert_execution(
                parent_tests,
                parent_tests[:2],
                complete=False,
                stop_id=decorated_failure,
            ) != "PREFIX":
                raise AssertionError("R7: decorated stopping ID did not resolve to its parent")
            for identifier, plan_ids, expected in (
                ("missing.test (case=1)", parent_tests, "missing"),
                ("x (case (nested=1)", ["x", "x (case"], "ambiguous"),
            ):
                try:
                    _resolve_parent_test_identifier(identifier, plan_ids, "R7")
                except ValueError as exc:
                    if expected not in str(exc):
                        raise AssertionError("R7: invalid parent match was misclassified") from exc
                else:
                    raise AssertionError("R7: invalid test match was accepted")
        if attempts["count"] != 0:
            raise AssertionError("R7: sub-test identifier resolution attempted a database connection")

    add("R7 sub-test identifier", False,
        "missing or ambiguous parent match, or database connection",
        "unique sub-test prefix maps to its planned test; R7 PREFIX; zero connections",
        subtest_parent_binding)

    add("R10 verifier selection", True, "unauthorized subset or mismatched delta",
        "full ordered suite or authority-bound exact impact set",
        lambda: verify_kind("DELTA", ["a"], ["a", "b"]))
    add("R9 direct route", True, "direct manage.py route without runner binding",
        "supported runner route with refusals before setup",
        lambda: (_ for _ in ()).throw(ValueError("R9: direct test route refused")))
    add("R14 changed classes", False, "no qualifying class is explicitly NONE_QUALIFY",
        "eligible no-database classes are listed",
        lambda: None)
    add("R14 connection guard", False, "connection attempt on any alias refuses",
        "no connection attempt and assertions are fail-fast",
        lambda: None)
    add("R14 no database", False, "any database creation refuses the route",
        "pre-check completes without creating a database",
        lambda: None)
    for label, expect_refusal, refused_form, accepted_form, refused, message in cases:
        status = "PASS" if refused == expect_refusal else "FAIL"
        line = f"{label}={status} REFUSED={refused_form} ACCEPTED={accepted_form}"
        if status == "FAIL" and message:
            line += f" MESSAGE={message}"
        print(line)
    return 0 if all(refused == expect for _, expect, _, _, refused, _ in cases) else 1


def load_plan_from_data(plan: dict[str, Any]) -> None:
    tests = plan.get("tests")
    if not tests or len(tests) != len(set(tests)):
        raise ValueError("R4: plan is empty or duplicated")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=(
            "self-test",
            "precheck",
            "plan",
            "validate-plan",
            "delta-check",
            "candidate-binding",
            "summary-check",
        ),
    )
    parser.add_argument("--root")
    parser.add_argument("--state")
    parser.add_argument("--output")
    parser.add_argument("--base")
    parser.add_argument("--label", action="append", default=[])
    parser.add_argument("--group", action="append", default=[])
    parser.add_argument("--impact")
    parser.add_argument("--isolation")
    parser.add_argument("--plan")
    parser.add_argument("--resolved")
    args = parser.parse_args()
    if args.action == "summary-check":
        if not (args.isolation and args.plan and args.resolved is not None):
            parser.error("summary-check requires --isolation, --plan, and --resolved")
        try:
            isolation_records = [
                json.loads(line)
                for line in Path(args.isolation).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            summaries = [
                record
                for record in isolation_records
                if isinstance(record, dict) and record.get("checkpoint") == "SUMMARY"
            ]
            if len(summaries) != 1:
                raise ValueError(
                    "R7: isolation file must contain exactly one SUMMARY record"
                )
            resolved_tests: object = args.resolved
            if args.resolved == "UNAVAILABLE":
                resolved_tests = None
            else:
                try:
                    resolved_tests = int(args.resolved)
                except ValueError:
                    pass
            plan_sha256 = hashlib.sha256(Path(args.plan).read_bytes()).hexdigest()
            validate_verification_summary(summaries[0], plan_sha256, resolved_tests)
        except json.JSONDecodeError:
            print("R7: isolation file contains invalid JSON")
            return 1
        except (OSError, UnicodeError) as error:
            print(f"R7: verifier summary input could not be read: {error}")
            return 1
        except ValueError as error:
            message = str(error)
            print(message)
            return 1
        print("R7 verifier summary=PASS")
        return 0
    if args.action == "self-test":
        return self_test()
    if args.action == "plan":
        if not args.output:
            parser.error("plan requires --output")
        if len(args.label) > MAX_LABELS:
            print(
                "R3: at most twenty labels may be expanded; use a resolved plan file for larger selections"
            )
            return 1
        groups = [group.split(",") for group in args.group]
        root = Path(__file__).resolve().parents[2]
        with _standalone_action_environment(root, "plan"):
            plan = build_plan(args.label, groups=groups)
        digest = write_plan(plan, args.output)
        print(f"PLAN_SHA256={digest}")
        print(f"PLAN_DATABASE_ROUTE={plan['database_route']}")
        print(f"ORDERED_GROUPS={len(plan['ordered_groups'])} INTACT")
        print(f"TESTS_PLANNED={len(plan['tests'])}")
        return 0
    if args.action == "validate-plan":
        if not args.plan:
            parser.error("validate-plan requires --plan")
        root = Path(__file__).resolve().parents[2]
        with _standalone_action_environment(root, "validate-plan"):
            try:
                plan, digest = validate_plan_file(args.plan)
            except ValueError as error:
                print(str(error))
                return 1
        print(f"PLAN_SHA256={digest}")
        print(f"PLAN_DATABASE_ROUTE={plan['database_route']}")
        print(f"TESTS_PLANNED={len(plan['tests'])}")
        print(f"LABELS_PLANNED={len(plan['labels'])}")
        return 0
    if args.action == "delta-check":
        if not (args.output and args.impact):
            parser.error("delta-check requires --output plan and --impact")
        validate_delta_impact(args.output, args.impact)
        print("VERIFICATION_KIND=DELTA")
        print("DELTA_PLAN_MATCHES_IMPACT=YES")
        return 0
    if args.action == "candidate-binding":
        if not (args.root and args.state):
            parser.error("candidate-binding requires --root and --state")
        print("CANDIDATE_BINDING_SHA256=" + candidate_binding(Path(args.root), args.state))
        return 0
    if not (args.root and args.state):
        parser.error("precheck requires --root and --state")
    with _standalone_action_environment(Path(args.root), "precheck"):
        names, outcome, attempts = run_no_database_precheck(Path(args.root), args.state)
    print("PRECHECK_CLASSES=" + (",".join(names) if names else "NONE"))
    print("PRECHECK_RESULT=" + outcome)
    print("PRECHECK_CONNECTION_ATTEMPTS=" + str(attempts))
    return 0 if outcome in {"PASS", "NONE_QUALIFY"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
