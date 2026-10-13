"""Test runner for the isolation instrument (repository route; lineage: ufund1_isolation candidate v0.3, UFUND-2). Use with:
   manage.py test <labels> --testrunner verification.isolation.runner.IsolationRunner
with VERIFICATION_ISOLATION_JSON set to an output path. verification/run.py sets both.

Two verdicts are kept separate:
  ordinary test verdict  - what the tests themselves reported (from the runner's result)
  isolation verdict      - PASS only if CP-0 requirements were met AND every executed test has exactly one
                           CP-A checkpoint, established, with no check error, AND the checkpoint set, the
                           executed set and the suite's collected set are identical.
Anything else, including a missing SUMMARY record, is NOT ESTABLISHED.

v0.3: a CP-A checkpoint is NOT APPLICABLE only where the test framework itself forbids database access for that test -
determined from the test's effective database permissions (Django's _validate_databases() excludes 'default'), not
from its class name. The checkpoint records the reason and the effective databases, and no query is attempted. Every
other checkpoint is applicable and must be established. A checkpoint marked not applicable while its recorded access is
anything but 'forbidden' counts as not established. Applicable and not-applicable counts are reported separately."""
import json
import os
import sys
import time
import unittest

from django.test.runner import DiscoverRunner
from django.test.utils import iter_test_cases

from intevia.test_postgresql_backend.operations import S015_TRUNCATE_GUARDIANS

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

CURRENT = {"runner": None}
DIGEST_KEYS = ("triggers", "constraints", "functions", "ownership_grants", "core_role", "content_types", "permissions", "migrations")


def isolation_verdict(cp0_met, suite_ids, started_ids, outcome_ids, checkpoints, emit_errors):
    """Pure function: returns (verdict, reasons). Separated so it can be tested without a database."""
    reasons = []
    if not cp0_met:
        reasons.append("CP-0 requirements not met")
    cp_ids = [c["test"] for c in checkpoints]
    dup = sorted({t for t in cp_ids if cp_ids.count(t) > 1})
    if dup:
        reasons.append("duplicate checkpoints: %s" % dup[:5])
    executed = set(started_ids) | set(outcome_ids)
    missing = sorted(executed - set(cp_ids))
    if missing:
        reasons.append("executed without a checkpoint: %s" % missing[:5])
    if set(cp_ids) != set(suite_ids) or executed != set(suite_ids):
        reasons.append("coverage mismatch: suite %d, checkpoints %d, executed %d" % (len(set(suite_ids)), len(set(cp_ids)), len(executed)))
    lawful_na = [c["test"] for c in checkpoints if c.get("applicable") is False and c.get("database_access") == "forbidden"]
    not_est = [c["test"] for c in checkpoints if c.get("established") is not True and c["test"] not in lawful_na]
    if not_est:
        reasons.append("not established at %d checkpoint(s), first %s" % (len(not_est), not_est[0]))
    errors = [c["test"] for c in checkpoints if str(c.get("reason", "")).startswith("check error")]
    if errors:
        reasons.append("check errors at %d checkpoint(s)" % len(errors))
    if emit_errors:
        reasons.append("output write errors: %d" % emit_errors)
    if not suite_ids:
        reasons.append("no tests in suite")
    return ("PASS" if not reasons else "NOT ESTABLISHED"), reasons


def database_access(test):
    """'forbidden' when the framework refuses every query to the default connection during this test: the test's
    effective databases, as Django itself resolves them, exclude 'default'. 'permitted' when they include it.
    'unknown' when the test is not a Django test case (no framework restriction), which is treated as applicable."""
    validate = getattr(type(test), "_validate_databases", None)
    if validate is None:
        return "unknown", None
    databases = sorted(validate())
    return ("permitted" if "default" in databases else "forbidden"), databases


def sequence_check_summary(captured, databases, ignore_tables=()):
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
    unobserved_aliases = [
        alias for alias in (databases or ()) if alias != "default"
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
    def setup_databases(self, **kwargs):
        from django.db import connections

        mode = os.environ.get("VERIFICATION_TEMPLATE_MODE", "fresh")
        if mode not in ("fresh", "clone"):
            raise RuntimeError("VERIFICATION_TEMPLATE_MODE must be fresh or clone")
        self.template_receipt = None
        self.template_build_seconds = 0.0
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
                from verification.ownership import write_receipt

                write_receipt("template_fallback", route="fresh", reason=str(exc))
            else:
                if built:
                    self.template_build_seconds = time.perf_counter() - template_started
                install_clone_creation(connection.creation, self.template_receipt)

        setup_started = time.perf_counter()
        old_config = DiscoverRunner.setup_databases(self, **kwargs)
        setup_seconds = time.perf_counter() - setup_started
        if mode == "clone" and self.template_receipt is not None:
            print("TT1_TEMPLATE_BUILD_SECONDS=%.6f" % self.template_build_seconds, file=sys.stderr, flush=True)
            print("TT1_CLONE_SETUP_SECONDS=%.6f" % setup_seconds, file=sys.stderr, flush=True)
        else:
            print("TT1_FRESH_SETUP_SECONDS=%.6f" % setup_seconds, file=sys.stderr, flush=True)
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
        self.reference = None
        self.cp0_met = False
        database_name = state.connection.settings_dict.get("NAME")
        protected_names = ("intevia", "intevia_dev", os.environ.get("INTEVIA_POSTGRES_DB"))
        ownership_problem = owned_database_problem(database_name, os.environ.get("VERIFICATION_DB_RECEIPT"),
                                                   os.environ.get("VERIFICATION_RUN_NONCE"), protected_names)
        if ownership_problem:
            violations = [ownership_problem]
            self._emit({"checkpoint": "CP-0", "requirements_met": False, "violations": violations,
                        "suite_ids": self.suite_ids})
            print("ISOLATION CP-0 requirements met: False", file=sys.stderr, flush=True)
            print("ISOLATION CP-0 VIOLATION: %s" % ownership_problem, file=sys.stderr, flush=True)
            self._summary(None)
            raise RuntimeError("isolation runner refused database before CP-0: %s" % ownership_problem)
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
        try:
            ref = state.capture()
            violations = state.check_requirements(ref, S015_TRUNCATE_GUARDIANS)
            self.cp0_met = not violations
            cp0_digest = state.digest(ref)
            if clone_record and template_receipt:
                if cp0_digest != clone_record.get("template_cp0_digest") or cp0_digest != template_receipt.get("cp0_digest"):
                    template_problem = "clone CP-0 differs from the template's recorded fresh-build CP-0"
                if not template_receipt.get("connection_refused"):
                    template_problem = "template direct-connection refusal was not established"
                if template_problem:
                    violations.append(template_problem)
                    self.cp0_met = False
            self.reference = ref if self.cp0_met else None
            self._emit({"checkpoint": "CP-0", "requirements_met": self.cp0_met, "violations": violations,
                        "reference_digests": {k: state.digest(ref[k]) for k in DIGEST_KEYS},
                        "trigger_count": len(ref["triggers"]), "suite_ids": self.suite_ids,
                        "template_identity": clone_record.get("identity") if clone_record else None,
                        "template_cp0_digest": clone_record.get("template_cp0_digest") if clone_record else None,
                        "clone_cp0_digest": cp0_digest if clone_record else None})
        except Exception as exc:
            violations = ["check error: %s: %s" % (type(exc).__name__, exc)]
            self._emit({"checkpoint": "CP-0", "requirements_met": False, "violations": violations})
        print("ISOLATION CP-0 requirements met: %s" % self.cp0_met, file=sys.stderr, flush=True)
        for v in violations:
            print("ISOLATION CP-0 VIOLATION: %s" % v, file=sys.stderr, flush=True)
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
            result = super().run_suite(suite, **kwargs)
            return result
        finally:
            CURRENT["runner"] = None
            self._summary(result)

    def teardown_databases(self, old_config, **kwargs):
        from django.db import connections

        result = DiscoverRunner.teardown_databases(self, old_config, **kwargs)
        if os.environ.get("VERIFICATION_TEMPLATE_CLEANUP") == "1" and self.template_receipt:
            store = os.environ["VERIFICATION_TEMPLATE_STORE"]
            path = template_receipt_path(store, self.template_receipt["identity"])
            cleanup_template(connections["default"], path, self.template_receipt["identity"])
        return result

    def get_resultclass(self):
        base_getter = super(RecordingRunnerMixin, self).get_resultclass
        base = base_getter()
        if base is None:
            base = getattr(self, "resultclass", None) or unittest.TextTestResult
        runner = self

        class IsolationResult(base):
            def startTest(self, test):
                runner.started_ids.append(test.id())
                runner.checkpoint_a(test)
                super().startTest(test)

            def _outcome(self, test):
                runner.outcome_ids.append(test.id())

            def addSuccess(self, test):
                self._outcome(test); super().addSuccess(test)

            def addError(self, test, err):
                self._outcome(test); super().addError(test, err)

            def addFailure(self, test, err):
                self._outcome(test); super().addFailure(test, err)

            def addSkip(self, test, reason):
                self._outcome(test); super().addSkip(test, reason)

            def addExpectedFailure(self, test, err):
                self._outcome(test); super().addExpectedFailure(test, err)

            def addUnexpectedSuccess(self, test):
                self._outcome(test); super().addUnexpectedSuccess(test)

            def addSubTest(self, test, subtest, err):
                self._outcome(test); super().addSubTest(test, subtest, err)

        class Result(RecordingResult, IsolationResult):
            pass

        return Result

    def checkpoint_a(self, test):
        rec = {
            "checkpoint": "CP-A",
            "test": test.id(),
            "established": False,
            "applicable": True,
            "sequence_check_summary": {
                "performed": [],
                "sequences_observed": 0,
                "not_established": [],
            },
        }
        try:
            access, databases = database_access(test)
            rec.update(database_access=access, effective_databases=databases)
            if access == "forbidden":
                rec.update(applicable=False, established=None,
                           reason="not applicable: database access forbidden by the framework for this test (effective databases %s)" % databases)
                rec["sequence_check_summary"]["not_established"].append(
                    "default database access is forbidden for this test"
                )
            elif self.reference is None:
                rec["reason"] = "CP-0 requirements not met"
                rec["sequence_check_summary"]["not_established"].append(
                    "CP-0 requirements were not met; CP-A sequence checks did not run"
                )
            else:
                captured = state.capture()
                sequence_summary = sequence_check_summary(
                    captured, databases, self.ignore_tables
                )
                data, prot = state.compare(
                    self.reference, captured, self.ignore_tables
                )
                data = [
                    "%s — observed at CP-A for %s" % (violation, test.id())
                    if violation.startswith("REQ-S1 ")
                    else violation
                    for violation in data
                ]
                rec.update(
                    established=not data and not prot,
                    data_violations=data,
                    protection_violations=prot,
                    sequence_check_summary=sequence_summary,
                )
        except Exception as exc:
            rec.update(
                established=False,
                reason="check error: %s: %s" % (type(exc).__name__, exc),
            )
            rec["sequence_check_summary"]["performed"] = ["capture attempted"]
            rec["sequence_check_summary"]["not_established"].append(
                "sequence check error: %s: %s" % (type(exc).__name__, exc)
            )
        self.checkpoints.append(rec)
        self._emit(rec)
        label = "NOT APPLICABLE" if rec.get("applicable") is False else ("ESTABLISHED" if rec["established"] else "NOT ESTABLISHED")
        print("ISOLATION CP-A %s %s" % (label, test.id()), file=sys.stderr, flush=True)

    def _emit(self, rec):
        rec = dict(rec, run_nonce=os.environ.get("VERIFICATION_RUN_NONCE"))  # binds every record to the current run (C-A1-B4)
        try:
            with open(self.out_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        except Exception:
            self.emit_errors += 1

    def _summary(self, result):
        verdict, reasons = isolation_verdict(self.cp0_met, self.suite_ids, self.started_ids,
                                             self.outcome_ids, self.checkpoints, self.emit_errors)
        ordinary = None
        if result is not None:
            ordinary = {"tests_run": result.testsRun, "errors": len(result.errors), "failures": len(result.failures),
                        "skipped": len(result.skipped), "was_successful": result.wasSuccessful()}
        summary = {"checkpoint": "SUMMARY", "isolation_verdict": verdict, "reasons": reasons,
                   "cp0_requirements_met": self.cp0_met, "suite_tests": len(self.suite_ids),
                   "checkpoints": len(self.checkpoints),
                   "established": sum(1 for c in self.checkpoints if c.get("established") is True),
                   "applicable": sum(1 for c in self.checkpoints if c.get("applicable") is not False),
                   "not_applicable": sum(1 for c in self.checkpoints if c.get("applicable") is False),
                   "ordinary_test_verdict": ordinary,
                   "evidence_claim": "equality at compared properties only; not complete database-state equivalence; "
                                     "sequence and identity positions and primary keys of framework rows remain excluded from equality; "
                                     "table-owned sequence configuration (REQ-S0) and next-id invariants (REQ-S1) "
                                     "are checked at reset boundaries, CP-0 and CP-A. "
                                     "The backend checks before teardown flushing and after seed restoration, and checks "
                                     "the result of the by-name setup reset. The isolation instrument checks at CP-0 and CP-A."}
        self._emit(summary)
        print("ISOLATION VERDICT: %s" % verdict, file=sys.stderr, flush=True)
        for r in reasons:
            print("ISOLATION REASON: %s" % r, file=sys.stderr, flush=True)
        print("ISOLATION ORDINARY TEST VERDICT (separate): %s" % ordinary, file=sys.stderr, flush=True)
