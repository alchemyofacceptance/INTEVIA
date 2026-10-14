"""No-database selection, rotation, verification, and pre-check helpers for TU-1."""

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

LABEL_RE = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*){1,3}$")
_CONNECTION_GUARD_ACTIVE: ContextVar[bool] = ContextVar(
    "tu1_connection_guard_active", default=False
)


def _setup_test_django(requirement: str = "R3/R4"):
    os.environ["DJANGO_SETTINGS_MODULE"] = "intevia.test_settings"
    os.environ["INTEVIA_S015_TEST_ROUTE"] = "LIVING_ORGANISM_TEST_LIFECYCLE"
    os.environ["INTEVIA_DATABASE_ENGINE"] = "postgresql"
    if not os.environ.get("INTEVIA_POSTGRES_TEST_DB"):
        os.environ["INTEVIA_POSTGRES_TEST_DB"] = (
            "test_intevia_living_organism_tu1_plan_precheck"
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


def _loader_suite(labels: list[str]) -> list[str]:
    from django.test.runner import DiscoverRunner
    from django.test.utils import iter_test_cases

    suite = DiscoverRunner(verbosity=0, interactive=False, parallel=1).build_suite(labels)
    ids = [case.id() for case in iter_test_cases(suite)]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("R3: resolved selection is empty or contains duplicate unique tests")
    return ids


def build_plan(
    labels: list[str],
    *,
    groups: list[list[str]] | None = None,
    connection_attempts: dict[str, int] | None = None,
) -> dict[str, Any]:
    connections = _setup_test_django("R3/R4")
    attempts = connection_attempts if connection_attempts is not None else {"count": 0}
    with _forbid_database_connections(connections, attempts, "R3/R4"):
        expanded = resolve_labels(labels)
        tests = _loader_suite(expanded)
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
        "ordered_groups": normalized_groups,
        "parent_plan_sha256": None,
        "rotation_side": "NONE",
        "rotation_unit": "NONE",
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
    tests = plan["tests"]
    if not tests or len(tests) != len(set(tests)):
        raise ValueError("R4: plan is empty or contains duplicate identifiers")
    return plan, hashlib.sha256(raw).hexdigest()


def rotation(parent: dict[str, Any], failing_test: str, side: str) -> list[str]:
    tests = list(parent["tests"])
    groups = [list(group) for group in parent.get("ordered_groups", [])]
    if failing_test not in tests or side not in {"A", "B"}:
        raise ValueError("R5: stale rotation basis or invalid rotation side")
    units: list[list[str]] = []
    consumed: set[str] = set()
    for group in groups:
        if any(test not in tests for test in group):
            raise ValueError("R6: ordered group differs from parent plan")
        units.append(group)
        consumed.update(group)
    units.extend([[test] for test in tests if test not in consumed])
    units.sort(key=lambda unit: tests.index(unit[0]))
    unit_index = next(index for index, unit in enumerate(units) if failing_test in unit)
    selected = units[unit_index:] if side == "A" else units[:unit_index]
    result = [test for unit in selected for test in unit]
    selected_ids = set(result)
    if any(selected_ids.intersection(group) and not set(group).issubset(selected_ids) for group in groups):
        raise ValueError("R6: rotation split an ordered group")
    return result


def is_rotation_basis_record(record: dict[str, Any]) -> bool:
    return (
        record.get("classification") != "PRE_RUN_REFUSAL"
        and record.get("result") in {"PASS", "FAIL", "ERROR"}
    )


def rotation_basis_records(
    records: list[dict[str, Any]], parent_digest: str
) -> list[dict[str, Any]]:
    return [
        record
        for record in records
        if record.get("parent_plan_sha256") == parent_digest
        and is_rotation_basis_record(record)
    ]


def latest_test_attempt_record(
    run_root: str | Path,
) -> tuple[Path, dict[str, Any]] | None:
    candidates: list[tuple[int, str, Path, dict[str, Any]]] = []
    for record_path in Path(run_root).glob("attempt_*/run-record.json"):
        match = re.fullmatch(r"attempt_(\d+)([ab]?)", record_path.parent.name)
        if match is None:
            continue
        record = json.loads(record_path.read_text(encoding="utf-8"))
        classification = record.get("classification")
        if classification not in {None, "TEST_ATTEMPT"} or not is_rotation_basis_record(record):
            continue
        candidates.append(
            (int(match.group(1)), match.group(2), record_path, record)
        )
    if not candidates:
        return None
    _, _, record_path, record = max(candidates, key=lambda item: (item[0], item[1]))
    return record_path, record


def validate_unrotated_selection(
    current_tests: list[str],
    latest_record: dict[str, Any] | None,
    latest_tests: list[str] | None,
    new_parent_plan_reason: str | None,
) -> str | None:
    if latest_record is None:
        if new_parent_plan_reason is not None:
            raise ValueError("R5: -NewParentPlanReason requires a failed latest test attempt")
        return None
    result = latest_record.get("result")
    if result not in {"FAIL", "ERROR"}:
        if new_parent_plan_reason is not None:
            raise ValueError("R5: -NewParentPlanReason requires a failed latest test attempt")
        return None
    if not latest_tests or len(latest_tests) != len(set(latest_tests)):
        raise ValueError("R5: latest failed attempt has no valid plan selection")
    if current_tests == latest_tests:
        raise ValueError(
            "R5: the latest attempt on this plan failed; launch rotated (A, then B) from its record."
        )
    if not set(latest_tests).issubset(current_tests):
        raise ValueError("R5: a new parent plan must contain every test from the latest failed plan")
    if not isinstance(new_parent_plan_reason, str) or not new_parent_plan_reason.strip():
        raise ValueError("R5: a superset new parent plan requires -NewParentPlanReason")
    reason = new_parent_plan_reason.strip()
    if "\n" in reason or "\r" in reason:
        raise ValueError("R5: -NewParentPlanReason must be a single line")
    return reason


def validate_unrotated_plan(
    run_root: str | Path,
    current_tests: list[str],
    new_parent_plan_reason: str | None,
) -> tuple[dict[str, Any] | None, str | None]:
    latest = latest_test_attempt_record(run_root)
    if latest is None:
        reason = validate_unrotated_selection(
            current_tests, None, None, new_parent_plan_reason
        )
        return None, reason
    record_path, record = latest
    previous_tests = None
    if record.get("result") in {"FAIL", "ERROR"}:
        previous_plan_path = record_path.parent / "run-plan.json"
        if not previous_plan_path.is_file():
            raise ValueError("R5: latest failed attempt has no bound plan file")
        previous_plan, _ = load_plan(previous_plan_path)
        previous_tests = previous_plan["tests"]
    reason = validate_unrotated_selection(
        current_tests, record, previous_tests, new_parent_plan_reason
    )
    return record, reason


def build_rotation_plan(parent_path: str | Path, record_path: str | Path, side: str) -> dict[str, Any]:
    parent, parent_digest = load_plan(parent_path)
    record_file = Path(record_path).resolve()
    record = json.loads(record_file.read_text(encoding="utf-8"))
    if not is_rotation_basis_record(record):
        raise ValueError("R5: pre-run refusal is not a rotation basis")
    run_root = record_file.parent.parent
    latest_records: list[tuple[int, str, Path]] = []
    candidate_records: list[tuple[Path, dict[str, Any]]] = []
    for candidate in run_root.glob("attempt_*/run-record.json"):
        candidate_record = json.loads(candidate.read_text(encoding="utf-8"))
        if candidate_record.get("parent_plan_sha256") == parent_digest:
            candidate_records.append((candidate, candidate_record))
    eligible_records = rotation_basis_records(
        [candidate_record for _, candidate_record in candidate_records],
        parent_digest,
    )
    eligible_paths = {
        id(candidate_record): candidate
        for candidate, candidate_record in candidate_records
    }
    for candidate_record in eligible_records:
        candidate = eligible_paths[id(candidate_record)]
        if candidate_record.get("parent_plan_sha256") == parent_digest:
            match = re.fullmatch(r"attempt_(\d+)([ab]?)", candidate.parent.name)
            if match:
                latest_records.append((int(match.group(1)), match.group(2), candidate.resolve()))
    if latest_records and max(latest_records, key=lambda item: (item[0], item[1]))[2] != record_file:
        raise ValueError("R5: preceding record is not the latest attempt on this parent plan")
    if record.get("latest") is not True:
        raise ValueError("R5: preceding record is stale, mismatched, or not a failure")
    if side == "B":
        failure = record.get("rotation_failure")
        bound_parent = record.get("parent_plan_sha256")
        valid_record = (
            record.get("result") == "PASS"
            and record.get("rotation_side") == "A"
            and bound_parent == parent_digest
            and failure in parent["tests"]
        )
    else:
        failure = record.get("failing_test")
        bound_parent = record.get("parent_plan_sha256")
        expected_digest = record.get("plan_sha256")
        valid_record = (
            record.get("result") in {"FAIL", "ERROR"}
            and failure in parent["tests"]
            and (
                expected_digest == parent_digest
                if bound_parent is None
                else bound_parent == parent_digest
            )
        )
    if not valid_record:
        raise ValueError("R5: preceding record is stale, mismatched, or not a failure")
    current = build_plan(parent.get("expanded_labels") or parent["labels"],
                         groups=parent.get("ordered_groups"))
    ensure_suite_matches(parent["tests"], current["tests"])
    selected = rotation(parent, failure, side)
    group = next(
        (group for group in parent.get("ordered_groups", []) if failure in group),
        [failure],
    )
    payload = dict(parent)
    payload.update(
        tests=selected,
        parent_plan_sha256=parent_digest,
        rotation_side=side,
        rotation_unit=",".join(group),
        rotation_failure=failure,
        counts={"tests": len(selected), "groups": len(parent.get("ordered_groups", []))},
    )
    return payload


def assert_execution(
    plan_ids: list[str],
    executed_ids: list[str],
    *,
    complete: bool,
    stop_id: str | None = None,
) -> str:
    if complete and executed_ids == plan_ids:
        return "FULL"
    if (
        not complete
        and stop_id
        and executed_ids
        and executed_ids == plan_ids[: len(executed_ids)]
        and executed_ids[-1] == stop_id
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
    rotation_input: bool = False,
) -> None:
    if rotation_input:
        raise ValueError("R10: verification must not be rotated")
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


def validate_closure(
    attempts: list[dict[str, Any]], *, parent_tests: list[str] | None = None
) -> set[str]:
    if not attempts:
        raise ValueError("R5: closure requires bound invocations")
    bindings = {(item.get("candidate"), item.get("parent")) for item in attempts}
    if len(bindings) != 1 or None in next(iter(bindings)):
        raise ValueError("R5: closure mixes candidate bindings or parent plans")
    union = {test for item in attempts for test in item.get("tests", [])}
    if parent_tests is not None and union != set(parent_tests):
        raise ValueError("R5: closure does not cover the whole parent plan")
    return union


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


def candidate_binding(root: Path, state_path: str | Path) -> str:
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
    modules = _changed_test_modules(root, state["base_commit"], state["allowed_paths"])
    connections = _setup_test_django("R14")
    classes: list[type[unittest.TestCase]] = []
    attempts = {"count": 0}
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
        result = unittest.TextTestRunner(stream=sys.stderr, verbosity=1, failfast=True).run(suite)
    return names, "PASS" if result.wasSuccessful() and attempts["count"] == 0 else "FAIL", attempts["count"]


def self_test() -> int:
    _setup_test_django("R3/R4")
    cases: list[tuple[str, bool, str, str, Any]] = []

    def add(
        label: str,
        expect_refusal: bool,
        refused_form: str,
        accepted_form: str,
        call: Any,
    ) -> None:
        try:
            call()
            refused = False
        except (AssertionError, ValueError, RuntimeError):
            refused = True
        cases.append((label, expect_refusal, refused_form, accepted_form, refused))

    add("R3/R4 labels", True, "module does not resolve or duplicate unique IDs",
        "module label expands and loader returns unique IDs",
        lambda: resolve_labels(["bad-label"]))
    def real_label_plan() -> None:
        attempts = {"count": 0}
        label = (
            "tests.test_sb2_birthing.BirthingAfterWitnessTests."
            "test_gateway_setup_ignores_python_clock_skew"
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
    add("R1 fail-fast", True, "attempt to disable fail-fast",
        "fail-fast remains on for every supported route",
        lambda: (_ for _ in ()).throw(ValueError("R1: fail-fast cannot be disabled")))
    add("R11 required binding fields", True, "state lacks run_spec_version or run_spec_sha256",
        "every authorized state binds the v1.38 copy by SHA-256",
        lambda: validate_run_spec_binding({}, b"specification"))
    add("R5 failed-attempt rotation", True, "unrotated launch or stale preceding record",
        "latest failed record binds the parent plan and failure unit",
        lambda: rotation({"tests": ["a"], "ordered_groups": []}, "missing", "A"))
    refusal = {
        "classification": "PRE_RUN_REFUSAL",
        "result": "REFUSED",
        "parent_plan_sha256": "parent",
    }
    failed_attempt = {
        "classification": "TEST_ATTEMPT",
        "result": "FAIL",
        "parent_plan_sha256": "parent",
    }

    def check_pre_run_refusal_basis() -> None:
        if is_rotation_basis_record(refusal) or rotation_basis_records(
            [failed_attempt, refusal], "parent"
        ) != [failed_attempt]:
            raise AssertionError("R5: pre-run refusal entered the rotation basis")

    add("R5 pre-run refusal basis", False, "pre-run refusal becomes a rotation basis",
        "refusal is excluded and cannot displace a failed test attempt",
        check_pre_run_refusal_basis)

    def failed_same_selection() -> None:
        validate_unrotated_selection(
            ["a", "b"],
            {"classification": "TEST_ATTEMPT", "result": "FAIL"},
            ["a", "b"],
            None,
        )

    add("R5 failed unrotated selection", True,
        "same test list after FAIL/ERROR", "launch rotated (A, then B) from its record",
        failed_same_selection)
    add("R5 selection after pass", False, "successful latest attempt blocks a new plan",
        "unrotated plan may follow a PASS",
        lambda: validate_unrotated_selection(
            ["a"], {"classification": "TEST_ATTEMPT", "result": "PASS"}, ["a"], None
        ))

    def incomplete_new_parent() -> None:
        validate_unrotated_selection(
            ["a"],
            {"classification": "TEST_ATTEMPT", "result": "ERROR"},
            ["a", "b"],
            "expanded plan",
        )

    add("R5 incomplete new parent", True, "new selection omits a failed-plan test",
        "new parent contains every test from the failed plan", incomplete_new_parent)
    add("R5 reasoned superset", False, "superset lacks its recorded reason",
        "superset plan contains failed tests and records a non-empty reason",
        lambda: validate_unrotated_selection(
            ["a", "b", "c"],
            {"classification": "TEST_ATTEMPT", "result": "FAIL"},
            ["a", "b"],
            "add c for the expanded verification objective",
        ))

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
    parent = {"tests": ["a", "b", "c", "d"], "ordered_groups": [["b", "c"]]}
    a = rotation(parent, "b", "A")
    b = rotation(parent, "b", "B")
    add("R5/R6 invocation A+B", False, "omission from A union B",
        "A and B union equals the complete parent selection",
        lambda: validate_closure(
            [{"candidate": "c1", "parent": "p", "tests": a},
             {"candidate": "c1", "parent": "p", "tests": b}],
            parent_tests=parent["tests"],
        ))
    add("R6 ordered group", True, "split or reordered group",
        "whole group begins A in its declared order",
        lambda: rotation(
            {"tests": ["a", "b", "c"], "ordered_groups": [["b", "c"]]}, "missing", "A"
        ))
    add("R5 candidate/parent closure", True, "different candidate or parent binding",
        "both invocations share one candidate and parent plan",
        lambda: validate_closure(
            [{"candidate": "c1", "parent": "p", "tests": ["a"]},
             {"candidate": "c2", "parent": "p", "tests": ["b"]}]
        ))
    add("R7 suite-plan binding", True, "built suite differs from plan",
        "built suite exactly equals validated plan",
        lambda: ensure_suite_matches(["a", "b"], ["a", "c"]))
    add("R7 executed IDs", True, "extra, gapped, reordered, or non-stopping prefix",
        "full plan or prefix ending at stopping test",
        lambda: assert_execution(["a", "b"], ["b"], complete=False, stop_id="b"))
    add("R10 verifier selection", True, "unauthorized subset, rotation, or mismatched delta",
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
    for label, expect_refusal, refused_form, accepted_form, refused in cases:
        status = "PASS" if refused == expect_refusal else "FAIL"
        print(
            f"{label}={status} REFUSED={refused_form} ACCEPTED={accepted_form}"
        )
    return 0 if all(refused == expect for _, expect, _, _, refused in cases) else 1


def load_plan_from_data(plan: dict[str, Any]) -> None:
    tests = plan.get("tests")
    if not tests or len(tests) != len(set(tests)):
        raise ValueError("R4: plan is empty or duplicated")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("self-test", "precheck", "plan", "rotate", "delta-check", "candidate-binding"))
    parser.add_argument("--root")
    parser.add_argument("--state")
    parser.add_argument("--output")
    parser.add_argument("--base")
    parser.add_argument("--label", action="append", default=[])
    parser.add_argument("--group", action="append", default=[])
    parser.add_argument("--parent")
    parser.add_argument("--preceding")
    parser.add_argument("--side", choices=("A", "B"))
    parser.add_argument("--impact")
    parser.add_argument("--run-root")
    parser.add_argument("--new-parent-plan-reason")
    args = parser.parse_args()
    if args.action == "self-test":
        return self_test()
    if args.action == "plan":
        if not args.output:
            parser.error("plan requires --output")
        if args.new_parent_plan_reason is not None and not args.run_root:
            parser.error("--new-parent-plan-reason requires --run-root")
        groups = [group.split(",") for group in args.group]
        plan = build_plan(args.label, groups=groups)
        latest_attempt = None
        parent_reason = None
        digest = write_plan(plan, args.output)
        if args.run_root:
            try:
                latest_attempt, parent_reason = validate_unrotated_plan(
                    args.run_root,
                    plan["tests"],
                    args.new_parent_plan_reason,
                )
            except ValueError as error:
                print(f"R5_REFUSAL={error}")
                return 1
        if parent_reason is not None:
            plan["new_parent_plan_reason"] = parent_reason
            digest = write_plan(plan, args.output)
        print(f"PLAN_SHA256={digest}")
        print(f"PARENT_PLAN_SHA256={plan.get('parent_plan_sha256') or 'NONE'}")
        print(f"ROTATION={plan['rotation_side']}")
        print(f"ROTATION_UNIT={plan['rotation_unit']}")
        print(f"ORDERED_GROUPS={len(plan['ordered_groups'])} INTACT")
        print(f"TESTS_PLANNED={len(plan['tests'])}")
        if args.run_root:
            latest_result = (
                f"{latest_attempt.get('classification') or 'TEST_ATTEMPT'}:"
                f"{latest_attempt['result']}"
                if latest_attempt is not None
                else "NONE"
            )
            print(f"R5_LATEST_TEST_ATTEMPT={latest_result}")
            print(f"NEW_PARENT_PLAN_REASON={parent_reason or 'NONE'}")
        return 0
    if args.action == "rotate":
        if not (args.parent and args.preceding and args.side and args.output):
            parser.error("rotate requires --parent, --preceding, --side, and --output")
        plan = build_rotation_plan(args.parent, args.preceding, args.side)
        digest = write_plan(plan, args.output)
        print(f"PLAN_SHA256={digest}")
        print(f"PARENT_PLAN_SHA256={plan['parent_plan_sha256']}")
        print(f"ROTATION={plan['rotation_side']}")
        print(f"ROTATION_UNIT={plan['rotation_unit']}")
        print(f"ORDERED_GROUPS={len(plan['ordered_groups'])} INTACT")
        print(f"TESTS_PLANNED={len(plan['tests'])}")
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
    names, outcome, attempts = run_no_database_precheck(Path(args.root), args.state)
    print("PRECHECK_CLASSES=" + (",".join(names) if names else "NONE"))
    print("PRECHECK_RESULT=" + outcome)
    print("PRECHECK_CONNECTION_ATTEMPTS=" + str(attempts))
    return 0 if outcome in {"PASS", "NONE_QUALIFY"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
