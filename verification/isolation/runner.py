"""Test runner for the isolation instrument (repository route; lineage: ufund1_isolation candidate v0.3, UFUND-2). Use with:
   manage.py test <labels> --testrunner verification.isolation.runner.IsolationRunner
with VERIFICATION_ISOLATION_JSON set to an output path. verification/run.py sets both.

Two verdicts are kept separate:
  ordinary test verdict  - what the tests themselves reported (from the runner's result)
  isolation verdict      - PASS only if CP-0 requirements were met, every executed test has exactly one
                           established CP-A checkpoint, and CP-Z was established after the completed run.
                           CP-F captures after failed tests are diagnostic and do not by themselves block it.
Anything else, including a missing SUMMARY record, is NOT ESTABLISHED.

v0.3: a CP-A checkpoint is NOT APPLICABLE only where the test framework itself forbids database access for that test -
determined from the test's effective database permissions (Django's _validate_databases() excludes 'default'), not
from its class name. The checkpoint records the reason and the effective databases, and no query is attempted. Every
other checkpoint is applicable and must be established. A checkpoint marked not applicable while its recorded access is
anything but 'forbidden' counts as not established. Applicable and not-applicable counts are reported separately."""
import json
import os
import re
import sys
import time
import unittest
from unittest.mock import patch

from django.test import TransactionTestCase
from django.test.runner import DiscoverRunner
from django.test.utils import iter_test_cases

from intevia.test_postgresql_backend.operations import (
    S015_TRUNCATE_GUARDIANS,
    clear_test_timing_context,
    set_test_timing_context,
)

from verification.ownership import OwnedDatabaseRunnerMixin, install, read_receipts
from verification.recording import RecordingResult, RecordingRunnerMixin

from . import state
from .template import (
    TemplateIdentityUnavailable,
    TemplateNameCollision,
    TemplateReceiptInvalid,
    TemplateSealingRefused,
    cleanup_template,
    collect_template_identity,
    install_clone_creation,
    prepare_template,
    read_template_receipt,
    record_final_clone_cp0,
    template_receipt_path,
)
from .run_plan import assert_execution, ensure_suite_matches, load_plan

CURRENT = {"runner": None}
DIGEST_KEYS = ("triggers", "constraints", "functions", "ownership_grants", "core_role", "content_types", "permissions", "migrations")


def _observe_lock_timeout(connection, alias):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT setting, unit FROM pg_settings WHERE name = 'lock_timeout'"
        )
        row = cursor.fetchone()
    if row is None:
        raise RuntimeError("lock_timeout was absent from pg_settings")
    try:
        value_ms = int(row[0])
    except (TypeError, ValueError) as exc:
        raise RuntimeError("lock_timeout setting was not an integer") from exc
    unit = str(row[1])
    print(
        "LOCK_TIMEOUT_EFFECTIVE alias=%s value_ms=%d unit=%s"
        % (alias, value_ms, unit),
        file=sys.stderr,
        flush=True,
    )


def isolation_verdict(cp0_met, suite_ids, started_ids, outcome_ids, checkpoints, emit_errors):
    """Pure function: returns (verdict, reasons). Separated so it can be tested without a database."""
    reasons = []
    if not cp0_met:
        reasons.append("CP-0 requirements not met")
    cpa = [c for c in checkpoints if c.get("checkpoint") == "CP-A"]
    cpz = [c for c in checkpoints if c.get("checkpoint") == "CP-Z"]
    cp_ids = [c["test"] for c in cpa]
    dup = sorted({t for t in cp_ids if cp_ids.count(t) > 1})
    if dup:
        reasons.append("duplicate checkpoints: %s" % dup[:5])
    executed = set(started_ids) | set(outcome_ids)
    missing = sorted(executed - set(cp_ids))
    if missing:
        reasons.append("executed without a checkpoint: %s" % missing[:5])
    extra = sorted(set(cp_ids) - executed)
    if extra:
        reasons.append("checkpoints without executed tests: %s" % extra[:5])
    if len(cp_ids) != len(set(cp_ids)) or len(cp_ids) != len(executed):
        reasons.append(
            "coverage mismatch: executed %d, checkpoints %d"
            % (len(executed), len(cp_ids))
        )
    post_alias = [c for c in checkpoints if c.get("checkpoint") == "CP-A'"]
    post_ids = [c.get("test") for c in post_alias]
    required_post = {c["test"] for c in cpa if (c.get("post_teardown_aliases_expected") or c.get("alias_checks")) and c["test"] in executed}
    missing_post = sorted(required_post - set(post_ids))
    duplicates = sorted({test_id for test_id in post_ids if post_ids.count(test_id) > 1})
    unexpected = sorted(set(post_ids) - required_post)
    failed_post = [c.get("test") for c in post_alias if c.get("test") in required_post and c.get("established") is not True]
    if missing_post: reasons.append("missing post-test alias checkpoints: %s" % missing_post[:5])
    if duplicates: reasons.append("duplicate post-test alias checkpoints: %s" % duplicates[:5])
    if unexpected: reasons.append("unexpected post-test alias checkpoints: %s" % unexpected[:5])
    if failed_post: reasons.append("post-test alias checks not established at %d checkpoint(s), first %s" % (len(failed_post), failed_post[0]))
    lawful_na = [
        c["test"]
        for c in cpa
        if c.get("applicable") is False
        and c.get("database_access") == "forbidden"
    ]
    not_est = [
        c["test"]
        for c in cpa
        if c.get("established") is not True and c["test"] not in lawful_na
    ]
    if not_est:
        reasons.append("not established at %d checkpoint(s), first %s" % (len(not_est), not_est[0]))
    errors = [
        c["test"]
        for c in cpa
        if str(c.get("reason", "")).startswith("check error")
    ]
    if errors:
        reasons.append("check errors at %d checkpoint(s)" % len(errors))
    if len(cpz) != 1 or cpz[0].get("established") is not True:
        reasons.append("CP-Z was not established after the run completed")
    if emit_errors:
        reasons.append("output write errors: %d" % emit_errors)
    if not suite_ids or not executed:
        reasons.append("no tests in suite")
    return ("PASS" if not reasons else "NOT ESTABLISHED"), reasons


def due_aliases(extra_aliases, current_databases):
    current = set(current_databases or ())
    return sorted(alias for alias in extra_aliases if alias in current)


class CheckpointRefused(RuntimeError):
    pass


class _RunnerConsoleStream:
    def __init__(self, stream):
        self._stream = stream
        self._runner_output = []

    def write(self, text):
        self._stream.write(text)
        return len(text)

    def write_runner(self, text):
        self._runner_output.append(text)
        return len(text)

    def flush(self):
        self._stream.flush()

    def flush_runner_output(self):
        while self._runner_output:
            text = self._runner_output[0]
            self._stream.write(text)
            del self._runner_output[0]
        self._stream.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)


def _runner_print(*args, sep=" ", end="\n", file=None, flush=False):
    stream = sys.stderr if file is None else file
    text = sep.join(str(arg) for arg in args) + end
    if isinstance(stream, _RunnerConsoleStream):
        stream.write_runner(text)
        if flush:
            stream.flush()
    else:
        print(*args, sep=sep, end=end, file=stream, flush=flush)


def database_access(test):
    """'forbidden' when the framework refuses every query to the default connection during this test: the test's
    effective databases, as Django itself resolves them, exclude 'default'. 'permitted' when they include it.
    'unknown' when the test is not a Django test case (no framework restriction), which is treated as applicable."""
    validate = getattr(type(test), "_validate_databases", None)
    if validate is None:
        return "unknown", None
    databases = sorted(validate())
    return ("permitted" if "default" in databases else "forbidden"), databases


def sequence_check_summary(
    captured, databases, ignore_tables=(), checked_aliases=("default",)
):
    """Summarize sequence checks performed and sequence state not established."""
    ignored = set(ignore_tables)
    observations = [
        observation
        for observation in captured.get("sequence_discipline", ())
        if observation["table"] not in ignored
    ]
    not_established = []
    for observation in observations:
        if observation["status"] == "UNSUPPORTED":
            not_established.append(
                "REQ-S0 unsupported configuration: %s" % observation["sequence"]
            )
        elif observation["status"] in ("UNOBSERVED", "INDETERMINATE"):
            not_established.append(
                "REQ-S1 not established: %s.%s (%s)"
                % (
                    observation["table"],
                    observation["column"],
                    observation["status"],
                )
            )
    checked_aliases = set(checked_aliases)
    unobserved_aliases = [
        alias for alias in (databases or ()) if alias not in checked_aliases
    ]
    not_established.extend(
        "database alias %s is not observed by the isolation instrument" % alias
        for alias in unobserved_aliases
    )
    return {
        "performed": ["REQ-S0", "REQ-S1"],
        "sequences_observed": len(observations),
        "not_established": not_established,
    }


def capture_baseline(conn=None, *, tolerate_light_errors=False):
    reference = state.capture(conn=conn)
    violations = state.check_requirements(reference, S015_TRUNCATE_GUARDIANS)
    plan = light = None
    try:
        plan = state.build_light_plan(reference)
        light = state.capture_light(plan, conn=conn)
        violations.extend(state.light_baseline_violations(reference, light))
    except Exception as exc:
        if not tolerate_light_errors:
            raise
        violations.append("CP-0 light-check baseline error: %s: %s" % (type(exc).__name__, exc))
    return reference, plan, light, violations


def owned_database_problem(database_name, receipt_path, run_nonce, protected_names=()):
    """Return why a database is not proven to be created by this attempt, without connecting to it."""
    if not database_name:
        return "default database name is not configured"
    if database_name in set(protected_names):
        return "refused protected database %s" % database_name
    if not receipt_path or not run_nonce:
        return "ownership receipt path and run nonce are required"
    try:
        records, problem = read_receipts(receipt_path, run_nonce)
    except (OSError, UnicodeError) as exc:
        return "ownership receipt could not be read: %s" % type(exc).__name__
    if problem:
        return "ownership receipt is not trustworthy: %s" % problem
    created = [record for record in records if record.get("event") == "created"
               and record.get("name") == database_name and isinstance(record.get("oid"), int)
               and record["oid"] > 0]
    if not created:
        return "no current-attempt creation receipt for database %s" % database_name
    return None


class IsolationRunner(RecordingRunnerMixin, OwnedDatabaseRunnerMixin, DiscoverRunner):
    extra_aliases = ()
    observed_aliases = ("default",)

    def build_suite(self, test_labels=None, **kwargs):
        self.run_plan = None
        self.plan_sha256 = None
        plan_path = os.environ.get("INTEVIA_RUN_PLAN_FILE")
        if not plan_path:
            return super().build_suite(test_labels, **kwargs)
        self.run_plan, self.plan_sha256 = load_plan(plan_path)
        if os.environ.get("INTEVIA_RUN_PLAN_SHA256") != self.plan_sha256:
            raise RuntimeError("R7: execution plan digest differs from validated plan")
        suite = super().build_suite(self.run_plan["tests"], **kwargs)
        actual_ids = [case.id() for case in iter_test_cases(suite)]
        ensure_suite_matches(self.run_plan["tests"], actual_ids)
        _runner_print("PLAN_SHA256=" + self.plan_sha256, file=sys.stderr, flush=True)
        _runner_print("TESTS_PLANNED=" + str(len(self.run_plan["tests"])), file=sys.stderr, flush=True)
        _runner_print("ORDERED_GROUPS=%d INTACT" % len(self.run_plan.get("ordered_groups", [])),
              file=sys.stderr, flush=True)
        return suite

    def setup_databases(self, **kwargs):
        from django.db import connections

        candidate_binding = os.environ.get("INTEVIA_CANDIDATE_BINDING_SHA256")
        if not isinstance(candidate_binding, str) or re.fullmatch(r"[0-9a-f]{64}", candidate_binding) is None:
            # Commission §1: unavailable candidate identity is an evidence gap, never a successful fallback.
            raise RuntimeError("R9: INTEVIA_CANDIDATE_BINDING_SHA256 is missing or invalid")
        mode = os.environ.get("VERIFICATION_TEMPLATE_MODE", "clone")
        if mode not in ("fresh", "clone"):
            raise RuntimeError("R2: template mode must be clone or authorized fresh")
        if mode == "fresh":
            from .run_plan import validate_fresh_authority

            self.fresh_authority = validate_fresh_authority(
                os.environ.get("INTEVIA_FRESH_AUTHORITY_FILE"),
                os.environ.get("INTEVIA_FRESH_AUTHORITY_SHA256"),
                os.environ.get("INTEVIA_RUN_SUFFIX", ""),
            )
        else:
            self.fresh_authority = "NONE"
        self.template_receipt = None
        self.template_prepare_seconds = 0.0
        self.template_build_seconds = 0.0
        self.template_rebuilt = False
        for alias in connections:
            install(connections[alias].creation)

        if mode == "clone":
            store = os.environ.get("VERIFICATION_TEMPLATE_STORE")
            if not store:
                raise RuntimeError("VERIFICATION_TEMPLATE_STORE is required for clone mode")
            connection = connections["default"]
            template_started = time.perf_counter()
            try:
                self.template_receipt, built = prepare_template(connection, "default", store)
            except TemplateSealingRefused:
                raise
            except (TemplateIdentityUnavailable, TemplateNameCollision, TemplateReceiptInvalid) as exc:
                raise RuntimeError("R9: clone pre-flight refused; fresh-mode fallback is prohibited") from exc
            else:
                self.template_prepare_seconds = time.perf_counter() - template_started
                self.template_rebuilt = bool(built)
                if built:
                    self.template_build_seconds = self.template_prepare_seconds
                install_clone_creation(connection.creation, self.template_receipt)

        setup_started = time.perf_counter()
        old_config = DiscoverRunner.setup_databases(self, **kwargs)
        setup_seconds = time.perf_counter() - setup_started
        self.clone_setup_seconds = setup_seconds
        self.observed_aliases = sorted(
            {connection.alias for connection, _, _ in old_config}
        )
        self.extra_aliases = [
            alias for alias in self.observed_aliases if alias != "default"
        ]
        self.alias_refs = {}

        if mode == "clone" and self.template_receipt is not None:
            _runner_print(
                "TT1_TEMPLATE_BUILD_SECONDS=%.6f" % self.template_build_seconds
                if self.template_rebuilt else "TT1_TEMPLATE_BUILD_SECONDS=NOT_APPLICABLE",
                file=sys.stderr,
                flush=True,
            )
            _runner_print("TT1_CLONE_SETUP_SECONDS=%.6f" % setup_seconds, file=sys.stderr, flush=True)
        else:
            _runner_print("TT1_FRESH_SETUP_SECONDS=%.6f" % setup_seconds, file=sys.stderr, flush=True)
        return old_config

    def run_suite(self, suite, **kwargs):
        assert getattr(self, "parallel", 1) == 1
        self.out_path = os.environ["VERIFICATION_ISOLATION_JSON"]
        self.emit_errors = 0
        self.checkpoints = []
        self.started_ids = []
        self.outcome_ids = []
        self.ignore_tables = ()
        self.suite_ids = [t.id() for t in iter_test_cases(suite)]
        self.observed_aliases = getattr(self, "observed_aliases", ["default"])
        self.extra_aliases = [
            alias for alias in self.observed_aliases if alias != "default"
        ]
        self.alias_refs = {}
        self.timing_records = []
        self.timing_by_id = {}
        self.current_timing_id = None
        self.pending_cpf = {}
        self.checkpoint_stop_id = None
        self.checkpoint_failure_test = None
        if self.run_plan is not None:
            ensure_suite_matches(self.run_plan["tests"], self.suite_ids)
        self.reference = None
        self.light_plan = None
        self.light_reference = None
        self.cp0_met = False
        from django.db import connections

        database_name = state.connection.settings_dict.get("NAME")
        protected_names = ("intevia", "intevia_dev", os.environ.get("INTEVIA_POSTGRES_DB"))
        ownership_problems = {}
        ownership_problem = owned_database_problem(
            database_name,
            os.environ.get("VERIFICATION_DB_RECEIPT"),
            os.environ.get("VERIFICATION_RUN_NONCE"),
            protected_names,
        )
        if ownership_problem:
            ownership_problems["default"] = ownership_problem
        for alias in self.extra_aliases:
            alias_problem = owned_database_problem(
                connections[alias].settings_dict.get("NAME"),
                os.environ.get("VERIFICATION_DB_RECEIPT"),
                os.environ.get("VERIFICATION_RUN_NONCE"),
                protected_names,
            )
            if alias_problem:
                ownership_problems[alias] = alias_problem
        if ownership_problems:
            violations = [
                "%s: %s" % (alias, problem)
                for alias, problem in ownership_problems.items()
            ]
            self._emit({"checkpoint": "CP-0", "requirements_met": False, "violations": violations,
                        "suite_ids": self.suite_ids,
                        "observed_aliases": self.observed_aliases,
                        "alias_requirements": {alias: {"requirements_met": False, "violations": [problem]}
                                               for alias, problem in ownership_problems.items()}})
            _runner_print("ISOLATION CP-0 requirements met: False", file=sys.stderr, flush=True)
            for violation in violations:
                _runner_print("ISOLATION CP-0 VIOLATION: %s" % violation, file=sys.stderr, flush=True)
            self._summary(None)
            raise RuntimeError("isolation runner refused database before CP-0: %s" % "; ".join(violations))
        clone_record = None
        template_receipt = None
        template_problem = None
        receipt_path = os.environ.get("VERIFICATION_DB_RECEIPT")
        run_nonce = os.environ.get("VERIFICATION_RUN_NONCE")
        records, receipt_problem = read_receipts(receipt_path, run_nonce)
        if receipt_problem:
            template_problem = "attempt receipt is not trustworthy: %s" % receipt_problem
        else:
            clones = [record for record in records if record.get("event") == "clone_created"
                      and record.get("name") == database_name]
            if clones:
                clone_record = clones[-1]
                try:
                    template_receipt = read_template_receipt(
                        clone_record["template_receipt"], clone_record["identity"])
                    if template_receipt is None:
                        raise TemplateReceiptInvalid("template receipt is absent")
                    listing, identity = collect_template_identity(connection=state.connection)
                    if identity != clone_record["identity"] or listing != template_receipt["listing"]:
                        raise TemplateReceiptInvalid("clone inputs differ from the full template identity")
                except (KeyError, TemplateIdentityUnavailable, TemplateReceiptInvalid) as exc:
                    template_problem = str(exc)
        reference_digests = {}
        alias_requirements = {}
        alias_references = {}
        try:
            ref, self.light_plan, light_reference, violations = capture_baseline(
                tolerate_light_errors=True
            )
            _observe_lock_timeout(state.connection, "default")
            cp0_digest = state.digest(ref)
            if clone_record and template_receipt:
                if cp0_digest != clone_record.get("template_cp0_digest") or cp0_digest != template_receipt.get("cp0_digest"):
                    template_problem = "clone CP-0 differs from the template's recorded fresh-build CP-0"
                if not template_receipt.get("connection_refused"):
                    template_problem = "template direct-connection refusal was not established"
                if template_problem:
                    violations.append(template_problem)
            reference_digests["default"] = {
                key: state.digest(ref[key]) for key in DIGEST_KEYS
            }
        except Exception as exc:
            violations = ["check error: %s: %s" % (type(exc).__name__, exc)]
            ref = None
            light_reference = None
            self.light_plan = None
            cp0_digest = None
        for alias in self.extra_aliases:
            try:
                alias_ref, alias_plan, alias_light, alias_violations = capture_baseline(
                    connections[alias]
                )
                _observe_lock_timeout(connections[alias], alias)
                alias_references[alias] = (alias_ref, alias_plan, alias_light)
                reference_digests[alias] = {
                    key: state.digest(alias_ref[key]) for key in DIGEST_KEYS
                }
                alias_requirements[alias] = {
                    "requirements_met": not alias_violations,
                    "violations": alias_violations,
                }
            except Exception as exc:
                alias_requirements[alias] = {
                    "requirements_met": False,
                    "violations": [
                        "check error: %s: %s" % (type(exc).__name__, exc)
                    ],
                }
        alias_violations = [
            "%s: %s" % (alias, violation)
            for alias, result in alias_requirements.items()
            for violation in result["violations"]
        ]
        violations.extend(alias_violations)
        default_met = ref is not None and not (
            violations[: len(violations) - len(alias_violations)]
        )
        self.cp0_met = default_met and not alias_violations
        self.reference = ref if self.cp0_met else None
        self.light_reference = light_reference if self.cp0_met else None
        self.alias_refs = alias_references if self.cp0_met else {}
        self._emit({
            "checkpoint": "CP-0",
            "requirements_met": self.cp0_met,
            "violations": violations,
            "reference_digests": reference_digests,
            "observed_aliases": self.observed_aliases,
            "alias_requirements": alias_requirements,
            "trigger_count": len(ref["triggers"]) if ref is not None else 0,
            "suite_ids": self.suite_ids,
            "light_check": {
                "statements": 4,
                "tables": len(self.light_plan["table_names"])
                if self.light_plan is not None else 0,
            },
            "template_identity": clone_record.get("identity") if clone_record else None,
            "template_cp0_digest": clone_record.get("template_cp0_digest") if clone_record else None,
            "clone_cp0_digest": cp0_digest if clone_record else None,
        })
        _runner_print("ISOLATION CP-0 requirements met: %s" % self.cp0_met, file=sys.stderr, flush=True)
        for v in violations:
            _runner_print("ISOLATION CP-0 VIOLATION: %s" % v, file=sys.stderr, flush=True)
        if clone_record and not self.cp0_met and not template_problem:
            template_problem = "clone CP-0 did not satisfy the isolation requirements"
        if template_problem and clone_record:
            self._summary(None)
            raise RuntimeError("template clone refused before tests: %s" % template_problem)
        if clone_record and template_receipt and os.environ.get("VERIFICATION_TEMPLATE_CLEANUP") == "1":
            record_final_clone_cp0(
                clone_record["template_receipt"], clone_record["identity"], state.digest(ref)
            )
        CURRENT["runner"] = self
        result = None
        try:
            with patch.object(
                TransactionTestCase,
                "_post_teardown",
                self._timed_post_teardown(TransactionTestCase._post_teardown),
            ):
                result = super().run_suite(suite, **kwargs)
            return result
        finally:
            CURRENT["runner"] = None
            self._run_pending_cpf()
            self._finish_timing_run()
            self._summary(result)

    def teardown_databases(self, old_config, **kwargs):
        from django.db import connections

        result = DiscoverRunner.teardown_databases(self, old_config, **kwargs)
        if os.environ.get("VERIFICATION_TEMPLATE_CLEANUP") == "1" and self.template_receipt:
            store = os.environ["VERIFICATION_TEMPLATE_STORE"]
            path = template_receipt_path(store, self.template_receipt["identity"])
            cleanup_template(
                connections["default"],
                path,
                self.template_receipt["identity"],
                allow_incomplete_parity=os.environ.get("VERIFICATION_TEMPLATE_NAMESPACE") == "me",
            )
        return result

    def get_resultclass(self):
        base_getter = super(RecordingRunnerMixin, self).get_resultclass
        base = base_getter()
        if base is None:
            base = getattr(self, "resultclass", None) or unittest.TextTestResult
        runner = self

        class IsolationResult(base):
            def startTest(self, test):
                runner._start_timing(test)
                unbuffered_stderr = sys.stderr
                super().startTest(test)
                self._runner_console_stream = None
                if not isinstance(sys.stderr, _RunnerConsoleStream):
                    self._runner_console_stream = _RunnerConsoleStream(sys.stderr)
                    sys.stderr = self._runner_console_stream
                try:
                    if not runner.checkpoint_a(test):
                        raise CheckpointRefused(
                            "CP-A' was not established before %s; previous test: %s"
                            % (
                                test.id(),
                                runner.checkpoint_failure_test or "none",
                            )
                        )
                except BaseException as exc:
                    _runner_print(
                        "ISOLATION CP-A REFUSED before %s: %s"
                        % (test.id(), exc),
                        file=unbuffered_stderr,
                        flush=True,
                    )
                    self._release_runner_output()
                    raise
                self._install_post_teardown_alias_check(test)
                runner.started_ids.append(test.id())

            def _release_runner_output(self):
                stream = getattr(self, "_runner_console_stream", None)
                if stream is None:
                    return
                stream.flush_runner_output()
                if sys.stderr is stream:
                    sys.stderr = stream._stream
                self._runner_console_stream = None

            def _post_test_alias_check(self, test, teardown_error):
                if teardown_error is not None:
                    _runner_print(
                        "ISOLATION POST-TEARDOWN ERROR after %s: %s"
                        % (test.id(), type(teardown_error).__name__),
                        file=sys.stderr,
                        flush=True,
                    )
                post_alias = runner.checkpoint_a_after(
                    test,
                    teardown_error=teardown_error,
                )
                if post_alias is None:
                    return
                if post_alias.get("established") is True:
                    _runner_print(
                        "ISOLATION CP-A' ESTABLISHED after %s" % test.id(),
                        file=sys.stderr,
                        flush=True,
                    )
                else:
                    self._release_runner_output()
                    _runner_print(
                        "ISOLATION CP-A REFUSED after %s: %s"
                        % (
                            test.id(),
                            post_alias.get(
                                "reason",
                                "post-test alias check was not established",
                            ),
                        ),
                        file=sys.stderr,
                        flush=True,
                    )
                    self.stop()

            def _install_post_teardown_alias_check(self, test):
                access, databases = database_access(test)
                if (
                    access == "forbidden"
                    or not due_aliases(runner.extra_aliases, databases)
                ):
                    return
                original_post_teardown = getattr(test, "_post_teardown", None)
                if not callable(original_post_teardown):
                    return

                instance_state = getattr(test, "__dict__", {})
                had_instance_override = "_post_teardown" in instance_state
                previous_override = instance_state.get("_post_teardown")

                def post_teardown_with_alias_check():
                    try:
                        try:
                            return original_post_teardown()
                        finally:
                            self._post_test_alias_check(test, sys.exc_info()[1])
                    finally:
                        if had_instance_override:
                            test._post_teardown = previous_override
                        else:
                            delattr(test, "_post_teardown")

                test._post_teardown = post_teardown_with_alias_check

            def _outcome(self, test):
                runner.outcome_ids.append(test.id())

            def addSuccess(self, test):
                self._outcome(test); super().addSuccess(test)

            def addError(self, test, err):
                self._outcome(test); super().addError(test, err)
                runner._mark_failure(test)

            def addFailure(self, test, err):
                self._outcome(test); super().addFailure(test, err)
                runner._mark_failure(test)

            def addSkip(self, test, reason):
                self._outcome(test); super().addSkip(test, reason)

            def addExpectedFailure(self, test, err):
                self._outcome(test); super().addExpectedFailure(test, err)

            def addUnexpectedSuccess(self, test):
                self._outcome(test); super().addUnexpectedSuccess(test)

            def addSubTest(self, test, subtest, err):
                self._outcome(test); super().addSubTest(test, subtest, err)
                if err is not None:
                    runner._mark_failure(test)

            def stopTest(self, test):
                try:
                    runner._stop_timing(test)
                    try:
                        super().stopTest(test)
                    finally:
                        clear_test_timing_context(test.id())
                finally:
                    self._release_runner_output()

            def stopTestRun(self):
                try:
                    super().stopTestRun()
                finally:
                    self._release_runner_output()
                    runner._run_pending_cpf()
                    runner.checkpoint_z()
                    runner._finish_timing_run()

        class Result(RecordingResult, IsolationResult):
            pass

        return Result

    def _checkpoint_body(self, conn, reference, light_plan, light_reference, record_key):
        rec, alias, previous_test = (record_key[key] for key in ("record", "alias", "previous_test"))
        databases, checked_aliases, trigger = (record_key[key] for key in ("databases", "checked_aliases", "trigger"))
        if reference is None:
            rec["reason"] = "CP-0 requirements not met" if alias == "default" else "alias CP-0 reference is not established"
            return
        if trigger:
            self._record_full_comparison(
                rec, state.capture(conn=conn), databases, trigger=trigger,
                reference=reference, checked_aliases=checked_aliases,
                attributed_test=(
                    rec.get("test")
                    if record_key.get("annotate_test", True)
                    else None
                ),
            )
            return
        light = state.capture_light(light_plan, conn=conn)
        light_violations = state.compare_light(light_reference, light)
        sequence_summary = {
            "performed": ["Q1", "Q2", "Q3", "Q4"],
            "sequences_observed": light["sequence"]["sequences_observed"],
            "not_established": ["Q3 %s: %s" % (f["status"], f["sequence"]) for f in light["sequence"]["findings"]],
        }
        sequence_summary["not_established"].extend(
            "database alias %s is not observed by the isolation instrument" % observed_alias
            for observed_alias in (databases or ())
            if observed_alias not in checked_aliases
        )
        rec.update(capture="light", trigger="light", light_capture=light, sequence_check_summary=sequence_summary)
        if alias == "default":
            rec["light_check"] = sequence_summary
        if not light_violations:
            rec["established"] = True
            return
        label = "" if alias == "default" else "alias "
        rec.update(capture="full", trigger="mismatch", light_mismatches=light_violations, attributed_to=previous_test,
                   reason="%sCP-A' mismatch after previous test %s" % (label, previous_test or "none"))
        self._record_full_comparison(
            rec, state.capture(conn=conn), databases, trigger="mismatch",
            reference=reference, checked_aliases=checked_aliases,
            attributed_test=rec.get("test") if record_key.get("annotate_test", True) else None,
        )
        rec["established"] = False

    def checkpoint_a(self, test):
        from django.db import connections

        rec = {
            "checkpoint": "CP-A",
            "test": test.id(),
            "established": False,
            "applicable": True,
            "capture": "none",
            "trigger": "light",
            "sequence_check_summary": {
                "performed": [],
                "sequences_observed": 0,
                "not_established": [],
            },
            "post_teardown_aliases_expected": [],
        }
        started = time.perf_counter()
        previous_test = self.started_ids[-1] if self.started_ids else None
        try:
            access, databases = database_access(test)
            due = due_aliases(self.extra_aliases, databases)
            checked_aliases = ("default", *due)
            rec.update(database_access=access, effective_databases=databases)
            rec["post_teardown_aliases_expected"] = due if access != "forbidden" else []
            if access == "forbidden":
                rec.update(
                    applicable=False, established=None, capture="none",
                    trigger="not-applicable",
                    reason="not applicable: database access forbidden by the framework for this test (effective databases %s)" % databases,
                )
                rec["sequence_check_summary"]["not_established"].append("default database access is forbidden for this test")
            elif self.reference is None:
                rec["reason"] = "CP-0 requirements not met"
                rec["sequence_check_summary"]["not_established"].append("CP-0 requirements were not met; CP-A sequence checks did not run")
            else:
                trigger = self._full_capture_trigger(test)
                rec.update(capture="full" if trigger else "light", trigger=trigger or "light")
                self._checkpoint_body(
                    connections["default"], self.reference, self.light_plan,
                    self.light_reference,
                    {
                        "record": rec, "alias": "default",
                        "previous_test": previous_test, "databases": databases,
                        "trigger": trigger, "checked_aliases": checked_aliases,
                    },
                )
            if due:
                alias_trigger = trigger if access != "forbidden" and self.reference is not None else None
                if rec.get("trigger") == "mismatch":
                    alias_trigger = "mismatch"
                rec["alias_checks"] = {
                    alias: self._alias_checkpoint(
                        alias, test, previous_test, databases, alias_trigger,
                        checked_aliases,
                    ) for alias in due
                }
                default_established = access == "forbidden" or rec.get("established") is True
                aliases_established = all(check.get("established") is True for check in rec["alias_checks"].values())
                rec["established"] = default_established and aliases_established
                rec["applicable"] = True
                if not aliases_established:
                    rec.setdefault("reason", "one or more due database aliases were not established")
        except Exception as exc:
            rec.update(established=False, reason="check error: %s: %s" % (type(exc).__name__, exc))
            rec["sequence_check_summary"]["performed"] = ["capture attempted"]
            rec["sequence_check_summary"]["not_established"].append("sequence check error: %s: %s" % (type(exc).__name__, exc))
            rec["attributed_to"] = previous_test
        finally:
            rec["t_check"] = time.perf_counter() - started
            timing = self.timing_by_id.get(test.id())
            if timing is not None:
                timing["t_check"] = rec["t_check"]
        if rec.get("established") is not True and rec.get("applicable") is not False:
            rec.setdefault("attributed_to", previous_test)
            rec.setdefault("reason", "CP-A' not established after previous test %s" % (previous_test or "none"))
            self.checkpoint_stop_id = self.started_ids[-1] if self.started_ids else None
            self.checkpoint_failure_test = previous_test
        self.checkpoints.append(rec)
        self._emit(rec)
        label = "NOT APPLICABLE" if rec.get("applicable") is False else ("ESTABLISHED" if rec["established"] else "NOT ESTABLISHED")
        _runner_print("ISOLATION CP-A %s %s" % (label, test.id()), file=sys.stderr, flush=True)
        return rec.get("applicable") is False or rec.get("established") is True

    def checkpoint_a_after(self, test, *, teardown_error=None):
        access, databases = database_access(test)
        due = due_aliases(self.extra_aliases, databases)
        if access == "forbidden" or not due:
            return None

        started = time.perf_counter()
        test_id = test.id()
        checked_aliases = ("default", *due)
        rec = {
            "checkpoint": "CP-A'",
            "test": test_id,
            "after": "post-teardown",
            "trigger": "post-teardown",
            "effective_databases": databases,
            "observed_aliases": self.observed_aliases,
            "established": False,
            "teardown_completed": teardown_error is None,
        }
        if teardown_error is not None:
            rec["post_teardown_error_type"] = type(teardown_error).__name__
        try:
            rec["alias_checks"] = {
                alias: self._alias_checkpoint(
                    alias, test, test_id, databases, None, checked_aliases
                )
                for alias in due
            }
            rec["established"] = all(check.get("established") is True for check in rec["alias_checks"].values())
            if not rec["established"]:
                rec["attributed_to"] = test_id
                rec["reason"] = (
                    "post-test alias state differs from CP-0; attributed to the declaring test"
                )
                self.checkpoint_stop_id = test_id
                self.checkpoint_failure_test = test_id
        except Exception as exc:
            rec["reason"] = "post-test alias check error: %s: %s" % (
                type(exc).__name__,
                exc,
            )
            rec["attributed_to"] = test_id
            self.checkpoint_stop_id = test_id
            self.checkpoint_failure_test = test_id
        finally:
            elapsed = time.perf_counter() - started
            rec["t_check"] = elapsed
            timing = self.timing_by_id.get(test_id)
            if timing is not None:
                timing["t_check"] = (timing.get("t_check") or 0.0) + elapsed
                timing["_stop_at"] = time.perf_counter()
        self.checkpoints.append(rec)
        self._emit(rec)
        return rec

    def _alias_checkpoint(
        self, alias, test, previous_test, databases, trigger, checked_aliases
    ):
        from django.db import connections

        rec = {
            "test": test.id(),
            "established": False,
            "capture": "none",
            "trigger": trigger or "light",
            "sequence_check_summary": {
                "performed": [],
                "sequences_observed": 0,
                "not_established": [],
            },
        }
        reference_data = self.alias_refs.get(alias)
        if reference_data is None:
            rec["reason"] = "alias CP-0 reference is not established"
            return rec

        reference, light_plan, light_reference = reference_data
        conn = connections[alias]
        try:
            self._checkpoint_body(
                conn,
                reference,
                light_plan,
                light_reference,
                {
                    "record": rec,
                    "alias": alias,
                    "previous_test": previous_test,
                    "databases": databases,
                    "trigger": trigger,
                    "checked_aliases": checked_aliases,
                },
            )
        except Exception as exc:
            rec.update(
                established=False,
                reason="alias check error: %s: %s" % (type(exc).__name__, exc),
            )
            rec["sequence_check_summary"]["performed"] = ["capture attempted"]
            rec["sequence_check_summary"]["not_established"].append(
                "alias sequence check error: %s: %s"
                % (type(exc).__name__, exc)
            )
        return rec

    def _full_capture_trigger(self, test):
        if os.environ.get("VERIFICATION_ISOLATION_PER_TEST") == "1":
            return "switch"
        method = getattr(test, getattr(test, "_testMethodName", ""), None)
        method_function = getattr(method, "__func__", method)
        module = sys.modules.get(type(test).__module__)
        if (
            getattr(method_function, "ISOLATION_FULL_CAPTURE", False)
            or getattr(type(test), "ISOLATION_FULL_CAPTURE", False)
            or getattr(module, "ISOLATION_FULL_CAPTURE", False)
        ):
            return "declared"
        return None

    def _record_full_comparison(
        self,
        rec,
        captured,
        databases,
        *,
        trigger,
        reference=None,
        checked_aliases=("default",),
        attributed_test=None,
    ):
        rec.update(capture="full", trigger=trigger)
        rec["full_capture"] = captured
        rec["full_capture_digest"] = state.digest(captured)
        data, protection = state.compare(
            self.reference if reference is None else reference,
            captured,
            self.ignore_tables,
        )
        data = [
            "%s — observed at CP-A for %s" % (violation, attributed_test)
            if attributed_test and violation.startswith("REQ-S1 ")
            else violation
            for violation in data
        ]
        sequence_summary = sequence_check_summary(
            captured,
            databases,
            self.ignore_tables,
            checked_aliases=checked_aliases,
        )
        rec.update(
            data_violations=data,
            protection_violations=protection,
            sequence_check_summary=sequence_summary,
            established=(
                not data
                and not protection
                and not sequence_summary["not_established"]
            ),
        )

    def _full_alias_check(
        self, alias, reference, databases, checked_aliases, *,
        include_reference_digest=False
    ):
        from django.db import connections

        result = {}
        self._checkpoint_body(
            connections[alias],
            reference,
            None,
            None,
            {
                "record": result,
                "alias": alias,
                "previous_test": None,
                "databases": databases,
                "trigger": "full",
                "checked_aliases": checked_aliases,
                "annotate_test": False,
            },
        )
        result.pop("trigger", None)
        if include_reference_digest:
            result["reference_digest"] = state.digest(reference)
        return result

    def _start_timing(self, test):
        now = time.perf_counter()
        previous_id = self.current_timing_id
        if previous_id is not None:
            previous = self.timing_by_id[previous_id]
            stop_at = previous.get("_stop_at")
            if stop_at is not None:
                previous["t_gap"] = now - stop_at
            self._run_pending_cpf()
            self._finalize_timing(previous)
            self.current_timing_id = None

        test_id = test.id()
        timing = {
            "test": test_id,
            "t_check": None,
            "t_unittest": None,
            "t_post": None,
            "t_flush": None,
            "t_gap": None,
            "_unittest_start": now,
            "_stop_at": None,
            "_post_complete": False,
        }
        self.timing_records.append(timing)
        self.timing_by_id[test_id] = timing
        self.current_timing_id = test_id
        set_test_timing_context(
            test_id,
            lambda callback_test_id, elapsed: self._record_flush_time(
                callback_test_id, elapsed
            ),
        )

    def _stop_timing(self, test):
        timing = self.timing_by_id.get(test.id())
        if timing is not None:
            now = time.perf_counter()
            timing["t_unittest"] = now - timing["_unittest_start"]
            timing["_stop_at"] = now

    def _record_post_teardown(self, test, elapsed, completed):
        test_id = test.id()
        timing = self.timing_by_id.get(test_id)
        if timing is not None:
            timing["t_post"] = elapsed
            timing["_post_complete"] = completed
        clear_test_timing_context(test_id)

    def _timed_post_teardown(self, original):
        def timed_post_teardown(test, *args, **kwargs):
            started = time.perf_counter()
            completed = False
            try:
                result = original(test, *args, **kwargs)
                completed = True
                return result
            finally:
                self._record_post_teardown(
                    test, time.perf_counter() - started, completed
                )

        return timed_post_teardown

    def _record_flush_time(self, test_id, elapsed):
        timing = self.timing_by_id.get(test_id)
        if timing is not None:
            timing["t_flush"] = (timing["t_flush"] or 0.0) + elapsed

    def _finalize_timing(self, timing, *, final=False):
        if timing.get("_post_complete") and timing["t_flush"] is None:
            timing["t_flush"] = 0.0
        phases = {
            name: value if value is not None else "incomplete"
            for name, value in (
                ("t_check", timing["t_check"]),
                ("t_unittest", timing["t_unittest"]),
                ("t_post", timing["t_post"]),
                ("t_flush", timing["t_flush"]),
                ("t_gap", timing["t_gap"]),
            )
        }
        if (
            final
            and timing["_stop_at"] is not None
            and phases["t_gap"] == "incomplete"
        ):
            phases["t_gap"] = "not_applicable"
        self._emit(
            {
                "checkpoint": "TIMING",
                "test": timing["test"],
                "label": "between-test interval",
                "phases": phases,
            }
        )
        timing["phases"] = phases
        for name in (
            "_unittest_start",
            "_stop_at",
            "_post_complete",
            "t_check",
            "t_unittest",
            "t_post",
            "t_flush",
            "t_gap",
        ):
            timing.pop(name, None)

    def _finish_timing_run(self):
        if self.current_timing_id is None:
            return
        timing = self.timing_by_id[self.current_timing_id]
        self._finalize_timing(timing, final=True)
        clear_test_timing_context(self.current_timing_id)
        self.current_timing_id = None

    def _mark_failure(self, test):
        self.pending_cpf[test.id()] = test

    def _run_pending_cpf(self):
        for test_id, test in list(self.pending_cpf.items()):
            timing = self.timing_by_id.get(test_id)
            rec = {
                "checkpoint": "CP-F",
                "test": test_id,
                "capture": "full",
                "trigger": "failure",
                "after": "post-teardown",
                "established": False,
            }
            try:
                if timing is None or not timing.get("_post_complete"):
                    rec["reason"] = "post-teardown did not complete"
                elif self.reference is None:
                    rec["reason"] = "CP-0 requirements not met"
                else:
                    self._checkpoint_body(
                        None,
                        self.reference,
                        None,
                        None,
                        {
                            "record": rec,
                            "alias": "default",
                            "previous_test": test_id,
                            "databases": None,
                            "trigger": "failure",
                            "checked_aliases": ("default",),
                            "annotate_test": False,
                        },
                    )
                    access, databases = database_access(test)
                    aliases = [
                        alias
                        for alias in self.extra_aliases
                        if alias in (databases or ())
                    ]
                    if aliases:
                        rec["alias_checks"] = {}
                    for alias in aliases:
                        alias_reference = self.alias_refs.get(alias)
                        alias_rec = {
                            "capture": "full",
                            "established": False,
                        }
                        if alias_reference is None:
                            alias_rec["reason"] = (
                                "alias CP-0 reference is not established"
                            )
                        else:
                            alias_ref = alias_reference[0]
                            alias_rec.update(
                                self._full_alias_check(
                                    alias,
                                    alias_ref,
                                    databases,
                                    self.observed_aliases,
                                )
                            )
                        rec["alias_checks"][alias] = alias_rec
            except Exception as exc:
                rec["reason"] = "check error: %s: %s" % (
                    type(exc).__name__,
                    exc,
                )
            self.checkpoints.append(rec)
            self._emit(rec)
            self.pending_cpf.pop(test_id, None)

    def checkpoint_z(self):
        rec = {
            "checkpoint": "CP-Z",
            "capture": "full",
            "trigger": "run-complete",
            "established": False,
            "observed_aliases": self.observed_aliases,
            "reference_digests": {},
            "alias_checks": {},
        }
        default_established = False
        try:
            if self.reference is None:
                rec["reason"] = "CP-0 requirements not met"
            else:
                self._checkpoint_body(
                    None,
                    self.reference,
                    None,
                    None,
                    {
                        "record": rec,
                        "alias": "default",
                        "previous_test": None,
                        "databases": None,
                        "trigger": "run-complete",
                        "checked_aliases": ("default",),
                        "annotate_test": False,
                    },
                )
                rec["reference_digests"]["default"] = state.digest(
                    self.reference
                )
                default_established = rec["established"]
        except Exception as exc:
            rec["reason"] = "check error: %s: %s" % (type(exc).__name__, exc)
        alias_established = []
        for alias in self.extra_aliases:
            reference_data = self.alias_refs.get(alias)
            alias_rec = {
                "capture": "full",
                "established": False,
            }
            if reference_data is None:
                alias_rec["reason"] = "alias CP-0 reference is not established"
            else:
                reference = reference_data[0]
                try:
                    alias_rec.update(
                        self._full_alias_check(
                            alias,
                            reference,
                            None,
                            self.observed_aliases,
                            include_reference_digest=True,
                        )
                    )
                    rec["reference_digests"][alias] = alias_rec["reference_digest"]
                except Exception as exc:
                    alias_rec["reason"] = "check error: %s: %s" % (
                        type(exc).__name__,
                        exc,
                    )
            alias_established.append(alias_rec["established"])
            rec["alias_checks"][alias] = alias_rec
        rec["established"] = default_established and all(alias_established)
        if not rec["established"]:
            rec["attributed_to"] = "CP-Z"
        self.checkpoints.append(rec)
        self._emit(rec)
        _runner_print(
            "ISOLATION CP-Z %s"
            % ("ESTABLISHED" if rec["established"] else "NOT ESTABLISHED"),
            file=sys.stderr,
            flush=True,
        )

    def _emit(self, rec):
        rec = dict(rec, run_nonce=os.environ.get("VERIFICATION_RUN_NONCE"))  # binds every record to the current run (C-A1-B4)
        try:
            with open(self.out_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        except Exception:
            self.emit_errors += 1

    def _timing_summary(self):
        phases = ("t_check", "t_unittest", "t_post", "t_flush", "t_gap")
        sums = {}
        incomplete = {}
        records = getattr(self, "timing_records", [])
        for phase in phases:
            values = [
                timing["phases"][phase]
                for timing in records
                if isinstance(timing.get("phases", {}).get(phase), (int, float))
            ]
            sums[phase] = sum(values)
            incomplete[phase] = sum(
                1
                for timing in records
                if timing.get("phases", {}).get(phase) == "incomplete"
            )
        _runner_print(
            "TIMING: tests=%d check=%.6fs unittest=%.6fs post=%.6fs flush=%.6fs gap=%.6fs"
            % (
                len(records),
                sums["t_check"],
                sums["t_unittest"],
                sums["t_post"],
                sums["t_flush"],
                sums["t_gap"],
            ),
            file=sys.stderr,
            flush=True,
        )
        return {
            "tests": len(records),
            "check_seconds": sums["t_check"],
            "unittest_seconds": sums["t_unittest"],
            "post_seconds": sums["t_post"],
            "flush_seconds": sums["t_flush"],
            "gap_seconds": sums["t_gap"],
            "incomplete_phases": incomplete,
        }

    def _summary(self, result):
        verdict, reasons = isolation_verdict(self.cp0_met, self.suite_ids, self.started_ids,
                                             self.outcome_ids, self.checkpoints, self.emit_errors)
        checkpoints = {}
        for record in self.checkpoints:
            checkpoints.setdefault(record.get("checkpoint"), []).append(record)
        cpa, post_alias = checkpoints.get("CP-A", []), checkpoints.get("CP-A'", [])
        cpz, cpf = checkpoints.get("CP-Z", []), checkpoints.get("CP-F", [])
        ordinary = None if result is None else {
            "tests_run": result.testsRun, "errors": len(result.errors),
            "failures": len(result.failures), "skipped": len(result.skipped),
            "was_successful": result.wasSuccessful(),
        }
        timing_summary = self._timing_summary()
        summary = {
            "checkpoint": "SUMMARY", "isolation_verdict": verdict, "reasons": reasons,
            "cp0_requirements_met": self.cp0_met, "suite_tests": len(self.suite_ids),
            "checkpoints": len(cpa),
            "established": sum(c.get("established") is True for c in cpa),
            "applicable": sum(c.get("applicable") is not False for c in cpa),
            "not_applicable": sum(c.get("applicable") is False for c in cpa),
            "cpz_established": len(cpz) == 1 and cpz[0].get("established") is True,
            "cpf_checkpoints": len(cpf), "observed_aliases": self.observed_aliases,
            "alias_checkpoints": sum(bool(c.get("alias_checks")) for c in cpa),
            "alias_post_checkpoints": len(post_alias),
            "alias_post_checkpoints_established": sum(c.get("established") is True for c in post_alias),
            "timing": timing_summary, "ordinary_test_verdict": ordinary,
            "evidence_claim": "equality at compared properties only; not complete database-state equivalence; "
                              "sequence and identity positions and primary keys of framework rows remain excluded from equality; "
                              "table-owned sequence configuration (REQ-S0) and next-id invariants (REQ-S1) "
                              "are checked at reset boundaries, CP-0, CP-A', CP-F and CP-Z. "
                              "The backend checks before teardown flushing and after seed restoration, and checks "
                              "the result of the by-name setup reset. The isolation instrument checks at CP-0, CP-A', "
                              "CP-F and CP-Z.",
        }
        candidate_binding = os.environ.get("INTEVIA_CANDIDATE_BINDING_SHA256")
        candidate_binding_valid = isinstance(candidate_binding, str) and re.fullmatch(
            r"[0-9a-f]{64}", candidate_binding
        ) is not None
        summary["candidate_binding"] = candidate_binding if candidate_binding_valid else "UNAVAILABLE"
        summary["candidate_binding_reason"] = (
            None if candidate_binding_valid else
            "INTEVIA_CANDIDATE_BINDING_SHA256 is missing or invalid"
        )
        if not candidate_binding_valid:
            verdict = "NOT ESTABLISHED"
            reasons.append("candidate binding unavailable")
            summary["isolation_verdict"] = verdict
            summary["reasons"] = reasons
        template_receipt = getattr(self, "template_receipt", None)
        template_rebuilt = getattr(self, "template_rebuilt", False)
        if template_receipt is not None:
            summary["template_action"] = "BUILT" if template_rebuilt else "REUSED"
            summary["template_build_seconds"] = (
                "%.6f" % getattr(self, "template_build_seconds", 0.0)
                if template_rebuilt else "NOT_APPLICABLE"
            )
            summary["template_prepare_seconds"] = "%.6f" % getattr(
                self, "template_prepare_seconds", 0.0
            )
            summary["template_name"] = template_receipt["name"]
        else:
            summary["template_action"] = "NOT_APPLICABLE"
            summary["template_build_seconds"] = "NOT_APPLICABLE"
            summary["template_prepare_seconds"] = "NOT_APPLICABLE"
        if self.run_plan is not None:
            executed_ids = list(self.started_ids)
            complete = len(executed_ids) == len(self.run_plan["tests"])
            stopping_test = (
                (result.errors + result.failures)[-1][0].id()
                if result is not None and (result.errors or result.failures)
                else self.checkpoint_stop_id
            )
            try:
                match = assert_execution(
                    self.run_plan["tests"],
                    executed_ids,
                    complete=complete,
                    stop_id=stopping_test,
                )
            except ValueError:
                match = "MISMATCH"
                verdict = "NOT ESTABLISHED"
                reasons.append("R7: executed identifiers do not match the validated plan")
                summary["isolation_verdict"] = verdict
                summary["reasons"] = reasons
            not_reached = self.run_plan["tests"][len(executed_ids):]
            summary.update(
                plan_sha256=self.plan_sha256,
                tests_planned=len(self.run_plan["tests"]),
                tests_executed=len(executed_ids),
                tests_not_reached=len(not_reached),
                executed_matches_plan=match,
                failing_test=(
                    self.checkpoint_failure_test or stopping_test
                ),
            )
            mode = os.environ.get("VERIFICATION_TEMPLATE_MODE", "UNKNOWN")
            _runner_print("MODE=" + mode, file=sys.stderr, flush=True)
            _runner_print("FRESH_AUTHORITY=" + getattr(self, "fresh_authority", "NONE"),
                  file=sys.stderr, flush=True)
            _runner_print("FAILFAST=" + ("ON" if getattr(self, "failfast", False) else "OFF"), file=sys.stderr, flush=True)
            _runner_print("RUN_SPEC_SHA256=" + os.environ.get("INTEVIA_RUN_SPEC_SHA256", "UNAVAILABLE"),
                  file=sys.stderr, flush=True)
            _runner_print("TESTS_EXECUTED=%d" % len(executed_ids), file=sys.stderr, flush=True)
            _runner_print("TESTS_NOT_REACHED=%d" % len(not_reached), file=sys.stderr, flush=True)
            _runner_print("EXECUTED_MATCHES_PLAN=" + match, file=sys.stderr, flush=True)
            if mode == "clone":
                _runner_print("TEMPLATE_ACTION=" + summary["template_action"], file=sys.stderr, flush=True)
                _runner_print("TEMPLATE_BUILD_SECONDS=" + summary["template_build_seconds"],
                      file=sys.stderr, flush=True)
                _runner_print("TEMPLATE_PREPARE_SECONDS=" + summary["template_prepare_seconds"],
                      file=sys.stderr, flush=True)
                _runner_print("CLONE_SETUP_SECONDS=%.6f" % getattr(self, "clone_setup_seconds", 0.0),
                      file=sys.stderr, flush=True)
        self._emit(summary)
        _runner_print("ISOLATION VERDICT: %s" % verdict, file=sys.stderr, flush=True)
        for r in reasons:
            _runner_print("ISOLATION REASON: %s" % r, file=sys.stderr, flush=True)
        _runner_print("ISOLATION ORDINARY TEST VERDICT (separate): %s" % ordinary, file=sys.stderr, flush=True)
