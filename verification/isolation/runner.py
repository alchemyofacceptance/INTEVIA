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
import unittest

from django.test.runner import DiscoverRunner
from django.test.utils import iter_test_cases

from intevia.test_postgresql_backend.operations import S015_TRUNCATE_GUARDIANS

from verification.ownership import OwnedDatabaseRunnerMixin

from . import state

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


class IsolationRunner(OwnedDatabaseRunnerMixin, DiscoverRunner):
    def run_suite(self, suite, **kwargs):
        self.out_path = os.environ["VERIFICATION_ISOLATION_JSON"]
        self.emit_errors = 0
        self.checkpoints = []
        self.started_ids = []
        self.outcome_ids = []
        self.ignore_tables = ()
        self.suite_ids = [t.id() for t in iter_test_cases(suite)]
        self.reference = None
        self.cp0_met = False
        try:
            ref = state.capture()
            violations = state.check_requirements(ref, S015_TRUNCATE_GUARDIANS)
            self.cp0_met = not violations
            self.reference = ref if self.cp0_met else None
            self._emit({"checkpoint": "CP-0", "requirements_met": self.cp0_met, "violations": violations,
                        "reference_digests": {k: state.digest(ref[k]) for k in DIGEST_KEYS},
                        "trigger_count": len(ref["triggers"]), "suite_ids": self.suite_ids})
        except Exception as exc:
            violations = ["check error: %s: %s" % (type(exc).__name__, exc)]
            self._emit({"checkpoint": "CP-0", "requirements_met": False, "violations": violations})
        print("ISOLATION CP-0 requirements met: %s" % self.cp0_met, file=sys.stderr, flush=True)
        for v in violations:
            print("ISOLATION CP-0 VIOLATION: %s" % v, file=sys.stderr, flush=True)
        CURRENT["runner"] = self
        result = None
        try:
            result = super().run_suite(suite, **kwargs)
            return result
        finally:
            CURRENT["runner"] = None
            self._summary(result)

    def get_resultclass(self):
        base = super().get_resultclass() or unittest.TextTestResult
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

        return IsolationResult

    def checkpoint_a(self, test):
        rec = {"checkpoint": "CP-A", "test": test.id(), "established": False, "applicable": True}
        try:
            access, databases = database_access(test)
            rec.update(database_access=access, effective_databases=databases)
            if access == "forbidden":
                rec.update(applicable=False, established=None,
                           reason="not applicable: database access forbidden by the framework for this test (effective databases %s)" % databases)
            elif self.reference is None:
                rec["reason"] = "CP-0 requirements not met"
            else:
                data, prot = state.compare(self.reference, state.capture(), self.ignore_tables)
                rec.update(established=not data and not prot, data_violations=data, protection_violations=prot)
        except Exception as exc:
            rec.update(established=False, reason="check error: %s: %s" % (type(exc).__name__, exc))
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
                                     "excluded: sequence and identity positions, primary keys of framework rows"}
        self._emit(summary)
        print("ISOLATION VERDICT: %s" % verdict, file=sys.stderr, flush=True)
        for r in reasons:
            print("ISOLATION REASON: %s" % r, file=sys.stderr, flush=True)
        print("ISOLATION ORDINARY TEST VERDICT (separate): %s" % ordinary, file=sys.stderr, flush=True)
