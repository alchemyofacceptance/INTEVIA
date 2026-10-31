"""Self-test of the verification route's safeguards (Change C v0.2): A1 findings C-A1-B1 to C-A1-B5.

No database server and no network: route methods run against controlled substitutes (fake cursors and connections,
temporary git repositories). Each class pairs A1's counterexamples with positive controls, so the route is shown both to
accept valid evidence and to reject defective evidence. The live database behaviour of the same safeguards is checked by
the route's OWNERSHIP step.

Run: python -m unittest -v verification.selftest_route   (needs Django installed; no settings or database are used)
"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from verification import ownership
from verification import route as route_mod
from verification.isolation import state as isolation_state
from verification.isolation.runner import (
    CheckpointRefused,
    IsolationRunner,
    _runner_print,
    due_aliases,
    isolation_verdict,
    sequence_check_summary,
)
from verification.parse_results import parse

TID = "probe.C.test_a"
TAIL = "\n" + "-" * 70 + "\nRan 1 test in 0.001s\n\nOK\nEXIT_STATUS: 0\n"


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="selftest_route_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def route(self, name="ev", run_id="probe"):
        r = route_mod.Route(os.path.join(self.tmp, name), run_id)
        self.addCleanup(lambda: r.transcript.close())
        return r


class B0PycachePrefixEnv(_Tmp):
    def test_ci_binding_matches_candidate_binding_for_a_clean_checkout(self):
        root = os.path.join(self.tmp, "clean-checkout")
        os.makedirs(os.path.join(root, "docs", "operations"))
        with open(
            os.path.join(root, "docs", "operations", "RUN_REQUIREMENTS.md"),
            "wb",
        ) as handle:
            handle.write(b"v1.38 test requirements\n")

        def git(*args):
            subprocess.run(
                [
                    "git",
                    "-C",
                    root,
                    "-c",
                    "user.name=route-selftest",
                    "-c",
                    "user.email=route-selftest@example.invalid",
                    *args,
                ],
                check=True,
                capture_output=True,
            )

        subprocess.run(["git", "init", "-q", root], check=True, capture_output=True)
        git("add", "-A")
        git("commit", "-qm", "clean candidate")
        _, head, error = route_mod.run_git(
            ["rev-parse", "--verify", "HEAD"], cwd=root
        )
        self.assertFalse(error)
        r = self.route()
        r.candidate_root = root
        r.candidate_base_commit = head.decode().strip()

        state_path, binding, specification_sha256 = r._ensure_candidate_binding()
        from verification.isolation.run_plan import candidate_binding

        self.assertEqual(binding, candidate_binding(root, state_path))
        with open(state_path, encoding="utf-8") as handle:
            state = json.load(handle)
        self.assertEqual(state["base_commit"], r.candidate_base_commit)
        self.assertEqual(state["allowed_paths"], [])
        self.assertEqual(
            specification_sha256,
            route_mod.sha256_file(
                os.path.join(root, "docs", "operations", "RUN_REQUIREMENTS.md")
            ),
        )

    def test_child_envs_follow_sys_pycache_prefix_and_omit_none(self):
        r = self.route()

        with mock.patch.object(route_mod.sys, "pycache_prefix", os.path.join(self.tmp, "pycache")), \
                mock.patch.object(r, "_ensure_candidate_binding", return_value=(None, "a" * 64, "b" * 64)):
            django_env = r.django_env("test_db", "SELF")
            offline_env = route_mod._child_python_env(PYTHONPATH=route_mod.ROOT, PYTHONIOENCODING="utf-8")
            self.assertEqual(django_env["PYTHONPYCACHEPREFIX"], route_mod.sys.pycache_prefix)
            self.assertEqual(offline_env["PYTHONPYCACHEPREFIX"], route_mod.sys.pycache_prefix)

        with mock.patch.object(route_mod.sys, "pycache_prefix", None), \
                mock.patch.object(r, "_ensure_candidate_binding", return_value=(None, "a" * 64, "b" * 64)):
            django_env = r.django_env("test_db", "SELF")
            offline_env = route_mod._child_python_env(PYTHONPATH=route_mod.ROOT, PYTHONIOENCODING="utf-8")
            self.assertNotIn("PYTHONPYCACHEPREFIX", django_env)
            self.assertNotIn("PYTHONPYCACHEPREFIX", offline_env)

    def test_offline_step_forwards_sys_pycache_prefix_to_run_logged(self):
        r = self.route()
        captured = {}
        expected_prefix = os.path.join(self.tmp, "pycache")

        def fake_run_logged(cmd, log_path, env):
            captured["env"] = dict(env)
            captured["cmd"] = list(cmd)
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("offline log\n")
            return 0

        fake_out = {"summary": {"ran": 0, "final": "OK", "errors": 0}, "reconciled": True, "tests": [], "reconciliation": {},
                    "test_totals_by_result": {}, "accounting_violations": []}

        with mock.patch.object(route_mod.sys, "pycache_prefix", expected_prefix), \
                mock.patch.object(r, "run_logged", side_effect=fake_run_logged), \
                mock.patch.object(route_mod.parse_results, "parse", return_value=fake_out):
            step = r.step_offline()

        self.assertEqual(captured["env"]["PYTHONPYCACHEPREFIX"], expected_prefix)
        self.assertNotIn("-f", captured["cmd"])
        self.assertNotIn("--failfast", captured["cmd"])
        self.assertEqual(step["outcome"], "PASS")

        captured.clear()
        os.remove(r.path("OFFLINE_results.json"))
        with mock.patch.object(route_mod.sys, "pycache_prefix", None), \
                mock.patch.object(r, "run_logged", side_effect=fake_run_logged), \
                mock.patch.object(route_mod.parse_results, "parse", return_value=fake_out):
            step = r.step_offline()

        self.assertNotIn("PYTHONPYCACHEPREFIX", captured["env"])
        self.assertEqual(step["outcome"], "PASS")


    def test_django_env_sets_and_guards_route_lock_timeout(self):
        """Claim: every child the route starts carries the route's lock timeout in PGOPTIONS (ROUTE_TEST_LOCK_TIMEOUT_MS),
        appended to whatever PGOPTIONS the parent inherited, and the route refuses to start a child whose inherited
        PGOPTIONS already sets lock_timeout rather than letting two settings compete."""
        r = self.route()
        expected = "-c lock_timeout=%d" % route_mod.ROUTE_TEST_LOCK_TIMEOUT_MS
        with mock.patch.object(
            r, "_ensure_candidate_binding", return_value=(None, "a" * 64, "b" * 64)
        ):
            with mock.patch.dict(os.environ):
                os.environ.pop("PGOPTIONS", None)
                child_env = r.django_env("test_db", "SELF")
            self.assertEqual(child_env["PGOPTIONS"], expected,
                             "with no inherited PGOPTIONS the child must receive exactly the route's lock timeout; it received %r, so a stuck lock would not time out as the route promises" % child_env["PGOPTIONS"])

            inherited = "-c application_name=route-selftest"
            with mock.patch.dict(os.environ, {"PGOPTIONS": inherited}):
                child_env = r.django_env("test_db", "SELF")
            self.assertEqual(
                child_env["PGOPTIONS"],
                inherited + " " + expected,
                "an inherited PGOPTIONS must be kept and the route's lock timeout appended; the child received %r" % child_env["PGOPTIONS"],
            )

            with mock.patch.dict(
                os.environ, {"PGOPTIONS": "-c lock_timeout=5s"}
            ):
                with self.assertRaisesRegex(
                    ValueError, "inherited PGOPTIONS already sets lock_timeout",
                    msg="an inherited lock_timeout must be refused before a child starts, not silently overridden or doubled",
                ):
                    r.django_env("test_db", "SELF")


class CollectionFactTests(_Tmp):
    def test_g2_uses_collected_ids_and_reports_nonpattern_files(self):
        root = Path(self.tmp)
        for name in (
            "__init__.py",
            "test_collected.py",
            "test_empty.py",
            "fixture_support.py",
            "fixture.py",
            "orphan.py",
        ):
            (root / name).touch()

        facts = route_mod.collection_facts(
            str(root),
            ["tests.test_collected.Case.test_one"],
            {"default": "d" * 63, "secondary": "é" * 32},
        )

        self.assertEqual(facts["modules_without_tests"], ["test_empty"])
        self.assertEqual(
            facts["outside_discovery_pattern"],
            ["fixture.py", "fixture_support.py", "orphan.py"],
        )
        self.assertEqual(
            facts["alias_name_overflow"],
            [{"alias": "secondary", "name_bytes": 64}],
        )
        facts_without_missing_tests = {
            **facts,
            "modules_without_tests": [],
            "alias_name_overflow": [],
        }
        self.assertEqual(
            route_mod.blocking_collection_facts(facts_without_missing_tests),
            {"G2 modules_without_tests": [], "G4 alias_name_overflow": []},
        )
        self.assertEqual(
            route_mod.blocking_collection_facts(facts)["G2 modules_without_tests"],
            ["test_empty"],
        )

        evidence_dir = root / "evidence"
        evidence_dir.mkdir()
        reporter = object.__new__(route_mod.Route)
        reporter.dir = str(evidence_dir)
        reporter.transcript = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()):
            reporter.write({
                "result": "PASS",
                "exit_status": 0,
                "cleanup": {"outcome": "CLEAN", "names": []},
                "identity": {},
                "environment": {
                    "python": "test",
                    "django": "test",
                    "psycopg": "test",
                    "platform": "test",
                    "ci": None,
                },
                "steps": [{
                    "id": "S015",
                    "outcome": "PASS",
                    "collection": {"facts": facts},
                }],
                "run_id": "test",
                "run_token": "token",
                "run_nonce": "nonce",
                "started": "start",
                "finished": "finish",
            })
        summary = (evidence_dir / "SUMMARY.md").read_text(encoding="utf-8")
        self.assertIn(
            "**outside the discovery pattern; not collected:** "
            "`fixture.py`, `fixture_support.py`, `orphan.py`",
            summary,
        )


class R1CompleteReportingTests(unittest.TestCase):
    def test_ordinary_failure_continues_but_checkpoint_refusal_stops(self):
        visited = []

        class Probe(unittest.TestCase):
            def test_a_failure(self):
                visited.append("failure")
                self.fail("deliberate R1 probe")

            def test_b_later(self):
                visited.append("later")

        for refuse_later in (False, True):
            with self.subTest(refuse_later=refuse_later):
                visited.clear()
                suite = unittest.defaultTestLoader.loadTestsFromTestCase(Probe)
                ids = [case.id() for case in suite]
                runner = BufferedOutputGuard()._runner(ids[1] if refuse_later else None)
                output = io.StringIO()
                with mock.patch.dict(os.environ, {"VERIFICATION_RUN_NONCE": "r1-probe"}), \
                        contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                    executor = unittest.TextTestRunner(
                        stream=output, resultclass=runner.get_resultclass(), failfast=False,
                    )
                    if refuse_later:
                        with self.assertRaises(CheckpointRefused):
                            executor.run(suite)
                    else:
                        result = executor.run(suite)
                self.assertEqual(visited, ["failure"] if refuse_later else ["failure", "later"])
                if not refuse_later:
                    self.assertEqual(result.testsRun, len(ids))
                    self.assertEqual(len(result.failures), 1)
                    self.assertFalse(result.wasSuccessful())
                    self.assertEqual(runner.started_ids, ids)


class BufferedOutputGuard(unittest.TestCase):
    def _runner(self, refuse_test=None):
        runner = object.__new__(IsolationRunner)
        runner.debug_sql = False
        runner.pdb = False
        runner.extra_aliases = ()
        runner.started_ids = []
        runner.outcome_ids = []
        runner.pending_failures = []
        runner._start_timing = lambda test: None
        runner._stop_timing = lambda test: None

        def checkpoint_a(test):
            if test.id() == refuse_test:
                runner.checkpoint_failure_test = (
                    runner.started_ids[-1] if runner.started_ids else None
                )
                _runner_print(
                    "ISOLATION CP-A NOT ESTABLISHED %s" % test.id(),
                    file=sys.stderr,
                    flush=True,
                )
                return False
            _runner_print(
                "ISOLATION CP-A ESTABLISHED %s" % test.id(),
                file=sys.stderr,
                flush=True,
            )
            return True

        runner.checkpoint_a = checkpoint_a
        runner._mark_failure = runner.pending_failures.append

        def run_pending_cpf():
            while runner.pending_failures:
                test = runner.pending_failures.pop(0)
                _runner_print(
                    "ISOLATION CP-F ESTABLISHED %s" % test.id(),
                    file=sys.stderr,
                    flush=True,
                )

        runner._run_pending_cpf = run_pending_cpf
        runner.checkpoint_z = lambda: _runner_print(
            "ISOLATION CP-Z ESTABLISHED", file=sys.stderr, flush=True
        )
        runner._finish_timing_run = lambda: _runner_print(
            "TIMING: tests=%d check=0.000000s unittest=0.000000s "
            "post=0.000000s flush=0.000000s gap=0.000000s"
            % len(runner.started_ids),
            file=sys.stderr,
            flush=True,
        )
        return runner

    def _suite(self):
        class Probe(unittest.TestCase):
            def test_a_pass_witness(self):
                print("PASS_STDOUT_WITNESS")
                print("PASS_STDERR_WITNESS", file=sys.stderr)

            def test_b_warning(self):
                with warnings.catch_warnings():
                    warnings.simplefilter("always")
                    warnings.warn("BUFFERED_WARNING_WITNESS", RuntimeWarning)

            def test_c_fail_with_output(self):
                print("FAIL_STDOUT_WITNESS")
                print("FAIL_STDERR_WITNESS", file=sys.stderr)
                self.fail("expected buffered failure")

            def test_d_error(self):
                raise RuntimeError("expected buffered error")

            @unittest.skip("expected buffered skip")
            def test_e_skip(self):
                pass

            def test_f_subtests(self):
                for value in ("pass", "fail"):
                    with self.subTest(value=value):
                        self.assertEqual(value, "pass")

            def test_g_nested_probe(self):
                class NestedProbe(unittest.TestCase):
                    def test_nested_failure(self):
                        self.fail("nested failure witness")

                NestedProbe.__module__ = __name__
                NestedProbe.__qualname__ = "NestedProbe"
                nested_runner = BufferedOutputGuard()._runner()
                nested_suite = unittest.defaultTestLoader.loadTestsFromTestCase(
                    NestedProbe
                )
                nested_result = unittest.TextTestRunner(
                    stream=io.StringIO(),
                    verbosity=2,
                    resultclass=nested_runner.get_resultclass(),
                ).run(nested_suite)
                self.assertFalse(nested_result.wasSuccessful())

        Probe.__module__ = __name__
        Probe.__qualname__ = "Probe"
        return unittest.defaultTestLoader.loadTestsFromTestCase(Probe)

    def _run(self, *, buffer, refuse_test=None):
        log = io.StringIO()
        runner = self._runner(refuse_test)
        with mock.patch.dict(
            os.environ, {"VERIFICATION_RUN_NONCE": "buffered-output-probe"}
        ), contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            result = unittest.TextTestRunner(
                stream=log,
                verbosity=2,
                resultclass=runner.get_resultclass(),
                buffer=buffer,
            ).run(self._suite())
        return log.getvalue(), result

    def test_buffered_output_preserves_parser_accounting_and_refusal_evidence(self):
        collected = [test.id() for test in self._suite()]
        unbuffered_text, unbuffered_result = self._run(buffer=False)
        unbuffered = parse(
            unbuffered_text
            + "\nEXIT_STATUS: %d\n"
            % (0 if unbuffered_result.wasSuccessful() else 1),
            collected,
        )
        self.assertFalse(unbuffered["reconciled"])

        buffered_text, buffered_result = self._run(buffer=True)
        buffered = parse(
            buffered_text
            + "\nEXIT_STATUS: %d\n"
            % (0 if buffered_result.wasSuccessful() else 1),
            collected,
        )
        self.assertTrue(buffered["reconciled"], buffered["reconciliation"])
        self.assertEqual(buffered["summary"]["ran"], 7)
        self.assertEqual(
            (
                buffered["summary"]["failures"],
                buffered["summary"]["errors"],
                buffered["summary"]["skipped"],
            ),
            (2, 1, 1),
        )
        self.assertNotIn("PASS_STDOUT_WITNESS", buffered_text)
        self.assertNotIn("PASS_STDERR_WITNESS", buffered_text)
        self.assertNotIn("BUFFERED_WARNING_WITNESS", buffered_text)
        self.assertNotIn("nested_failure", buffered_text)
        lines = buffered_text.splitlines()
        failure_line = next(
            index
            for index, line in enumerate(lines)
            if "Probe.test_c_fail_with_output) ... FAIL" in line
        )
        mirrored_output = next(
            index
            for index, line in enumerate(lines)
            if "FAIL_STDOUT_WITNESS" in line
        )
        self.assertGreater(mirrored_output, failure_line)

        refusal_suite = self._suite()
        refused_test = next(
            test.id()
            for test in refusal_suite
            if test._testMethodName == "test_d_error"
        )
        refused_log = io.StringIO()
        runner = self._runner(refused_test)
        with mock.patch.dict(
            os.environ, {"VERIFICATION_RUN_NONCE": "buffered-refusal-probe"}
        ), contextlib.redirect_stdout(refused_log), contextlib.redirect_stderr(
            refused_log
        ):
            with self.assertRaises(CheckpointRefused):
                unittest.TextTestRunner(
                    stream=refused_log,
                    verbosity=2,
                    resultclass=runner.get_resultclass(),
                    buffer=True,
                ).run(refusal_suite)
        refusal_text = refused_log.getvalue()
        self.assertIn(
            "ISOLATION CP-A REFUSED before %s:" % refused_test,
            refusal_text,
        )
        refused_status_line = next(
            line for line in refusal_text.splitlines() if refused_test in line
        )
        self.assertFalse(
            any(
                refused_status_line.endswith("... " + status)
                for status in ("ok", "FAIL", "ERROR", "skipped")
            )
        )


# ------------------------------------------------------------------ B1
class B1StepGating(_Tmp):
    def test_every_route_test_command_is_buffered_and_full_steps_report_every_failure(self):
        """Claim: the command the route runs for a database step is Django's test runner with output buffering on and
        fail-fast off (R1: ordinary failures do not stop a step, so one run reports every failure), and a step whose
        child reports every test ok is classified PASS from that command's log. This checks the command the route
        actually issues; the child's execution of it is the VC's route rehearsal, not this test."""
        r = self.route()
        r.permitted_skips = []
        commands = []
        collection = {
            "ids": [TID],
            "count": 1,
            "digest": "d" * 64,
            "failed_loads": [],
            "run_nonce": r.nonce,
            "facts": {},
        }

        def fake_collection(command, **kwargs):
            with open(command[4], "x", encoding="utf-8") as handle:
                json.dump(collection, handle)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        def fake_run_logged(command, log_path, env):
            commands.append(command)
            with open(log_path, "x", encoding="utf-8") as handle:
                handle.write("test_a (probe.C.test_a) ... ok\n" + TAIL)
            return 0

        env = {
            ownership.RECEIPT_ENV: os.path.join(self.tmp, "receipts.jsonl"),
            # a Human-authorised fresh run: this test's claim is buffering and full reporting, not template binding
            # (CICloneTemplateBinding proves that), so no clone evidence is staged here
            "VERIFICATION_TEMPLATE_MODE": "fresh",
        }
        with mock.patch.object(route_mod.subprocess, "run", side_effect=fake_collection), \
                mock.patch.object(r, "run_logged", side_effect=fake_run_logged), \
                mock.patch.object(r, "django_env", return_value=env), \
                mock.patch.object(r, "oid_of", return_value=None):
            step = r.step_suite("SELF", "label", [TID], isolation=False)

        self.assertEqual(step["outcome"], "PASS", "a child whose log reports every test ok must be classified PASS; the step was %s (%s)" % (step["outcome"], step.get("reason")))
        command = commands[0]
        self.assertEqual(command[1:3], ["manage.py", "test"], "the route must run Django's test command; it ran %s" % command[1:3])
        self.assertIn("--buffer", command, "the route's test command must buffer test output (so a test's own prints cannot split the result lines the parser pairs); the command was %s" % command[1:])
        self.assertNotIn("--failfast", command, "the route's test command must not stop at the first failure (R1: every failure is reported in one run); the command was %s" % command[1:])
        self.assertEqual(command[command.index("--parallel") + 1], "1", "the route must run tests serially (--parallel 1) for the isolation instrument; the command was %s" % command[1:])

    def test_fail_fast_accepts_only_a_collected_failure_prefix(self):
        collected = ["probe.C.test_a", "probe.C.test_b"]
        prefix = parse(
            "test_a (probe.C.test_a) ... FAIL\n"
            + "=" * 70
            + "\nFAIL: test_a (probe.C.test_a)\n"
            + "-" * 70
            + "\nTraceback (most recent call last):\n  File 'x.py', line 1\n"
            + "AssertionError: expected\n\n"
            + "-" * 70
            + "\nRan 1 test in 0.001s\n\nFAILED (failures=1)\nEXIT_STATUS: 1\n",
            collected,
        )
        outcome, reason = route_mod.Route.classify(
            prefix, 1, fail_fast=True, collected_ids=collected
        )
        self.assertEqual(outcome, "FAIL", reason)
        self.assertIn("1 test(s) not reached", reason)

        reordered = parse(
            "test_b (probe.C.test_b) ... FAIL\n"
            + "=" * 70
            + "\nFAIL: test_b (probe.C.test_b)\n"
            + "-" * 70
            + "\nTraceback (most recent call last):\n  File 'x.py', line 1\n"
            + "AssertionError: expected\n\n"
            + "-" * 70
            + "\nRan 1 test in 0.001s\n\nFAILED (failures=1)\nEXIT_STATUS: 1\n",
            collected,
        )
        outcome, _ = route_mod.Route.classify(
            reordered, 1, fail_fast=True, collected_ids=collected
        )
        self.assertEqual(outcome, "INCOMPLETE")

    def test_b1_missing_status_step_is_incomplete_not_pass(self):
        r = self.route()
        r.candidate_state_path = "candidate-state.json"
        r.candidate_binding = "a" * 64
        r.run_spec_sha256 = "b" * 64

        def fake_collection(cmd, **kw):
            with open(cmd[4], "x") as f:
                json.dump({"ids": [TID], "count": 1, "digest": "d", "failed_loads": [], "run_nonce": r.nonce}, f)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        def fake_log(cmd, path, env):
            with open(path, "x") as f:
                f.write("test_a (probe.C.test_a) ...\n" + TAIL)
            return 0

        with mock.patch.object(route_mod.subprocess, "run", side_effect=fake_collection), mock.patch.object(r, "run_logged", side_effect=fake_log), \
                mock.patch.object(r, "oid_of", return_value=None), mock.patch.object(r, "read_isolation", return_value={"verdict": "PASS"}):
            step = r.step_suite("S015", "label", [TID])
        self.assertEqual(step["outcome"], "INCOMPLETE")

    def test_b1_positive_ok_is_pass_and_teardown_error_is_fail(self):
        ok = parse("test_a (probe.C.test_a) ... ok\n" + TAIL, [TID])
        self.assertEqual(route_mod.Route.classify(ok, 0)[0], "PASS")
        td = ("test_a (probe.C.test_a) ... ok\ntest_a (probe.C.test_a) ... ERROR\n\n" + "=" * 70 + "\nERROR: test_a (probe.C.test_a)\n" + "-" * 70 +
              '\nTraceback (most recent call last):\n  File "x.py", line 9, in _post_teardown\n    raise RuntimeError("flush")\nRuntimeError: flush\n\n' +
              "-" * 70 + "\nRan 1 test in 0.001s\n\nFAILED (errors=1)\nEXIT_STATUS: 1\n")
        parsed = parse(td, [TID])
        self.assertTrue(parsed["reconciled"])
        self.assertEqual(route_mod.Route.classify(parsed, 1)[0], "FAIL")


# ------------------------------------------------------------------ B2
class B2CleanupRecords(_Tmp):
    def _run_with_cleanup_failure(self):
        r = self.route()
        fake_lock = mock.MagicMock()
        fake_lock.execute.return_value.fetchall.return_value = []

        def lock():
            r.lock_conn = fake_lock

        def suite(sid, *a, **k):
            r.attempted.append((sid, r.db_name(sid), r.path(sid + "_db_receipts.jsonl")))
            return {"id": sid, "outcome": "PASS"}

        env = {"INTEVIA_POSTGRES_USER": "probe", "INTEVIA_POSTGRES_PASSWORD": "not-a-credential"}
        with mock.patch.dict(os.environ, env), mock.patch.object(r, "identity", return_value={"valid": True, "problems": [], "tested": "x"}), \
                mock.patch.object(r, "acquire_lock", side_effect=lock), mock.patch.object(r, "server", return_value={"version": "simulated"}), \
                mock.patch.object(r, "step_offline", return_value={"id": "OFFLINE", "outcome": "PASS"}), mock.patch.object(r, "step_suite", side_effect=suite), \
                mock.patch.object(r, "step_ownership", return_value={"id": "OWNERSHIP", "outcome": "PASS"}), \
                mock.patch.object(r, "step_lock", return_value={"id": "LOCK", "outcome": "PASS"}), \
                mock.patch.object(r, "oid_of", side_effect=RuntimeError("simulated server unavailable during cleanup")), \
                contextlib.redirect_stdout(io.StringIO()):
            code = r.run(skip_mutations=True)
        return r, code, json.loads(route_mod.read_text(r.path("summary.json")))

    def test_b2_cleanup_exception_keeps_every_name_and_exits_3(self):
        r, code, summary = self._run_with_cleanup_failure()
        self.assertEqual(code, 3)
        self.assertEqual(summary["cleanup"]["outcome"], "NOT CLEAN")
        names = {n["name"]: n for n in summary["cleanup"]["names"]}
        self.assertEqual(set(names), {r.db_name("SELF"), r.db_name("S015")})
        self.assertTrue(all(n["state"].startswith("UNRESOLVED") and "simulated server unavailable" in n["state"] for n in names.values()))
        self.assertNotIn("SETUP", [s["id"] for s in summary["steps"]])

    def test_b2_positive_clean_cleanup_exits_0(self):
        r = self.route()
        r.attempted.append(("S015", r.db_name("S015"), r.path("S015_db_receipts.jsonl")))
        with mock.patch.object(r, "oid_of", return_value=None):
            result = r.finalise_cleanup()
        self.assertEqual(result["outcome"], "CLEAN")
        self.assertEqual(result["names"][0]["state"], "ABSENT - never created by this run")


# ------------------------------------------------------------------ B3
class _Conn:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.log.append(sql)
        return mock.MagicMock()


class B3RouteCleanupOwnership(_Tmp):
    def _receipts(self, r, name, *records):
        p = r.path("S015_db_receipts.jsonl")
        with open(p, "w") as f:
            for rec in records:
                f.write(json.dumps(dict(rec, run_nonce=r.nonce, name=name)) + "\n")
        return p

    def _cleanup(self, r, name, path, oids):
        sql = []
        seq = iter(oids)
        with mock.patch.object(r, "oid_of", side_effect=lambda *a, **k: next(seq)), mock.patch.object(r, "connect", return_value=_Conn(sql)):
            return r.cleanup_one("S015", name, path), sql

    def test_b3_no_receipt_present_database_is_left_untouched(self):
        r = self.route(); name = r.db_name("S015")
        rec, sql = self._cleanup(r, name, r.path("none.jsonl"), [4242])
        self.assertFalse(rec["resolved"]); self.assertEqual(sql, [])

    def test_b3_replaced_database_is_left_untouched(self):
        r = self.route(); name = r.db_name("S015")
        rec, sql = self._cleanup(r, name, self._receipts(r, name, {"event": "created", "oid": 100}), [200])
        self.assertTrue(rec["state"].startswith("REPLACED")); self.assertFalse(rec["resolved"]); self.assertEqual(sql, [])

    def test_b3_collision_refused_database_is_not_owned_and_untouched(self):
        r = self.route(); name = r.db_name("S015")
        rec, sql = self._cleanup(r, name, self._receipts(r, name, {"event": "collision_refused", "existing_oid": 7}), [7])
        self.assertTrue(rec["resolved"]); self.assertTrue(rec["state"].startswith("NOT OWNED")); self.assertEqual(sql, [])

    def test_b3_other_runs_receipts_are_not_trusted(self):
        r = self.route(); name = r.db_name("S015")
        p = r.path("S015_db_receipts.jsonl")
        with open(p, "w") as f:
            f.write(json.dumps({"event": "created", "oid": 100, "name": name, "run_nonce": "another-run"}) + "\n")
        rec, sql = self._cleanup(r, name, p, [100])
        self.assertFalse(rec["resolved"]); self.assertEqual(sql, [])

    def test_b3_interrupted_alias_receipt_is_swept_but_replacement_is_untouched(self):
        r = self.route()
        primary = r.db_name("S015")
        alias_name = primary + "_s014_secondary"
        receipt_path = r.path("S015_db_receipts.jsonl")
        with open(receipt_path, "w", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "event": "created",
                        "run_nonce": r.nonce,
                        "name": alias_name,
                        "oid": 100,
                    }
                )
                + "\n"
            )
        current_oids = {primary: None, alias_name: 200}

        with mock.patch.object(
            r, "oid_of", side_effect=lambda name, conn=None: current_oids[name]
        ), mock.patch.object(r, "connect") as connect:
            records = r.cleanup_receipt_file(
                "S015", primary, receipt_path
            )

        self.assertEqual([record["name"] for record in records], [primary, alias_name])
        self.assertTrue(records[0]["resolved"])
        self.assertTrue(records[1]["state"].startswith("REPLACED"))
        self.assertFalse(records[1]["resolved"])
        connect.assert_not_called()

    def test_b3_positive_owned_database_is_dropped_by_oid(self):
        r = self.route(); name = r.db_name("S015")
        rec, sql = self._cleanup(r, name, self._receipts(r, name, {"event": "created", "oid": 100}), [100, 100, None])
        self.assertTrue(rec["resolved"]); self.assertTrue(rec["state"].startswith("DROPPED")); self.assertEqual(len([s for s in sql if s.startswith("DROP DATABASE")]), 1)


class _Cursor:
    def __init__(self, sql, existing):
        self.sql, self.existing, self._row = sql, existing, None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, query, params=None):
        self.sql.append(query)
        if query.startswith("SELECT oid FROM pg_database"):
            self._row = (self.existing[params[0]],) if params[0] in self.existing else None
        elif query.startswith("DROP DATABASE"):
            self.existing.pop(query.split()[-1].strip('"'), None)

    def fetchone(self):
        return self._row


class B3RunnerCreationAndDestruction(_Tmp):
    NAME = "test_intevia_living_organism_vprobe_s015"

    def _creation(self, sql, existing, duplicate_on_create=False):
        from django.db.backends.base.creation import BaseDatabaseCreation

        class Creation(BaseDatabaseCreation):
            def _get_test_db_name(inner):
                return self.NAME

            def _nodb_cursor(inner):
                return _Cursor(sql, existing)

            def sql_table_creation_suffix(inner):
                return ""

            def log(inner, msg):
                pass

            def _execute_create_test_db(inner, cursor, parameters, keepdb=False):
                cursor.execute("CREATE DATABASE %s" % parameters["dbname"])
                if duplicate_on_create:
                    raise RuntimeError('database "%s" already exists' % self.NAME)
                existing[self.NAME] = 5555

        return Creation(SimpleNamespace(ops=SimpleNamespace(quote_name=lambda x: '"%s"' % x), settings_dict={"NAME": "postgres", "TEST": {}}))

    def _env(self):
        return mock.patch.dict(os.environ, {ownership.RECEIPT_ENV: os.path.join(self.tmp, "receipts.jsonl"), ownership.NONCE_ENV: "nonce-1"})

    def receipts(self):
        return ownership.read_receipts(os.path.join(self.tmp, "receipts.jsonl"), "nonce-1")[0]

    def test_b3_control_unmodified_django_clobbers_on_collision(self):
        sql, existing = [], {self.NAME: 1}
        c = self._creation(sql, existing, duplicate_on_create=True)
        attempts = {"n": 0}
        original = c._execute_create_test_db

        def once(cursor, parameters, keepdb=False):
            attempts["n"] += 1
            cursor.execute("CREATE DATABASE %s" % parameters["dbname"])
            if attempts["n"] == 1:
                raise RuntimeError("already exists")
        c._execute_create_test_db = once
        c._create_test_db(verbosity=0, autoclobber=True, keepdb=False)
        self.assertTrue(any(s.startswith("DROP DATABASE") for s in sql), sql)  # the hazard A1 identified

    def test_b3_existing_database_is_refused_without_create_or_drop(self):
        sql, existing = [], {self.NAME: 1}
        c = self._creation(sql, existing)
        with self._env():
            ownership.install(c)
            with self.assertRaises(ownership.CollisionRefused):
                c._create_test_db(verbosity=0, autoclobber=True, keepdb=False)
            self.assertEqual([r["event"] for r in self.receipts()], ["collision_refused"])
        self.assertFalse(any(s.startswith(("DROP", "CREATE")) for s in sql)); self.assertEqual(existing, {self.NAME: 1})

    def test_b3_concurrent_duplicate_raises_and_nothing_is_dropped(self):
        sql, existing = [], {}
        c = self._creation(sql, existing, duplicate_on_create=True)
        with self._env():
            ownership.install(c)
            with self.assertRaises(RuntimeError):
                c._create_test_db(verbosity=0, autoclobber=True, keepdb=False)
            self.assertEqual([r["event"] for r in self.receipts()], ["create_attempt"])
        self.assertFalse(any(s.startswith("DROP") for s in sql))

    def test_b3_replaced_database_is_not_destroyed_by_the_runner(self):
        sql, existing = [], {}
        c = self._creation(sql, existing)
        with self._env():
            ownership.install(c)
            c._create_test_db(verbosity=0, autoclobber=True, keepdb=False)
            existing[self.NAME] = 9999  # replaced by another actor
            c._destroy_test_db(self.NAME, verbosity=0)
            events = [r["event"] for r in self.receipts()]
        self.assertEqual(events, ["create_attempt", "created", "destroy_refused"]); self.assertFalse(any(s.startswith("DROP") for s in sql))

    def test_b3_positive_owned_database_is_created_then_destroyed(self):
        sql, existing = [], {}
        c = self._creation(sql, existing)
        with self._env():
            ownership.install(c)
            self.assertEqual(c._create_test_db(verbosity=0, autoclobber=True, keepdb=False), self.NAME)
            c._destroy_test_db(self.NAME, verbosity=0)
            recs = self.receipts()
        self.assertEqual([r["event"] for r in recs], ["create_attempt", "created", "destroyed"]); self.assertTrue(recs[2]["confirmed_absent"])


# ------------------------------------------------------------------ B4
@contextlib.contextmanager
def _alias_checkpoint_stubs(test_id, *, mismatch=False):
    alias = "s014_secondary"
    default_conn = SimpleNamespace(alias="default")
    alias_conn = SimpleNamespace(alias=alias)
    runner = object.__new__(IsolationRunner)
    runner.debug_sql = runner.pdb = False
    runner.extra_aliases = [alias]
    runner.reference = {"alias": "default"}
    runner.light_plan = runner.light_reference = {"alias": "default"}
    runner.alias_refs = {alias: ({"alias": alias}, {"alias": alias}, {"alias": alias})}
    runner.started_ids = []
    runner.outcome_ids = []
    runner._start_timing = runner._stop_timing = lambda test: None
    runner.timing_by_id = {test_id: {"t_check": 0.01, "_stop_at": 1.0}}
    runner.checkpoints = []
    runner.ignore_tables = ()
    runner.observed_aliases = ["default", alias]
    runner.checkpoint_stop_id = runner.checkpoint_failure_test = None
    emitted, connections_seen = [], []
    runner._emit = emitted.append
    runner._full_capture_trigger = lambda test: None

    def capture_light(plan, conn=None):
        connections_seen.append(conn)
        return {"sequence": {"sequences_observed": 1, "findings": []}}

    def compare_light(reference, light):
        return ["core_role rows changed"] if mismatch and reference["alias"] == alias else []

    def capture(conn=None):
        connections_seen.append(conn)
        return {"connection": conn}

    def compare(reference, captured, ignore_tables):
        changed = mismatch and captured["connection"] is alias_conn
        return (["core_role rows changed"] if changed else [], [])

    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch(
            "verification.isolation.runner.database_access",
            return_value=("permitted", ["default", alias]),
        ))
        stack.enter_context(mock.patch("django.db.connections", {
            "default": default_conn, alias: alias_conn,
        }))
        stack.enter_context(mock.patch(
            "verification.isolation.runner.state.capture_light",
            side_effect=capture_light,
        ))
        stack.enter_context(mock.patch(
            "verification.isolation.runner.state.compare_light",
            side_effect=compare_light,
        ))
        stack.enter_context(mock.patch(
            "verification.isolation.runner.state.capture", side_effect=capture,
        ))
        stack.enter_context(mock.patch(
            "verification.isolation.runner.state.compare", side_effect=compare,
        ))
        stack.enter_context(mock.patch(
            "verification.isolation.runner.state.digest", return_value="d" * 64,
        ))
        stack.enter_context(mock.patch(
            "verification.isolation.runner.sequence_check_summary",
            return_value={"not_established": []},
        ))
        yield runner, SimpleNamespace(id=lambda: test_id), emitted, connections_seen, default_conn, alias_conn


class SeedRoutingTests(unittest.TestCase):
    def test_each_migration_seed_write_uses_the_editor_alias(self):
        from importlib import import_module

        alias, writes = "s014_secondary", []

        class Manager:
            def using(self, database_alias):
                self.alias = database_alias
                return self

            def get_or_create(self, **kwargs):
                writes.append(("orm", self.alias))

            def filter(self, **kwargs):
                return self

            def delete(self):
                writes.append(("orm", self.alias))

        class Apps:
            def get_model(self, app_label, model_name):
                return SimpleNamespace(objects=Manager())

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, sql):
                writes.append(("sql", alias))

        connection = SimpleNamespace(
            alias=alias, vendor="postgresql", cursor=lambda: Cursor()
        )
        editor = SimpleNamespace(connection=connection)
        triad = import_module("core.migrations.0003_seed_triad_roles")
        platform = import_module("core.migrations.0045_sbg_governance_formation")
        entitlement = import_module("core.migrations.0046_se_entitlement_foundation")
        triad.create_triad_roles(Apps(), editor)
        triad.remove_triad_roles(Apps(), editor)
        platform.restore_platform_rules(None, editor)
        entitlement.restore_entitlement_rules(None, editor)
        self.assertEqual(writes, [("orm", alias)] * 4 + [("sql", alias)] * 2)

    def test_flush_passes_an_editor_for_the_flushed_connection(self):
        from intevia.test_postgresql_backend import operations

        alias, passed = "s014_secondary", []
        connection = SimpleNamespace(
            alias=alias,
            in_atomic_block=False,
            features=SimpleNamespace(can_rollback_ddl=True),
        )
        editor = SimpleNamespace(connection=connection)
        connection.schema_editor = lambda: editor
        cursor = mock.MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.__exit__.return_value = False
        connection.cursor = lambda: cursor
        backend = object.__new__(operations.DatabaseOperations)
        backend.connection = connection
        backend._qualified_database_name = lambda: None
        backend._sequence_discipline_findings = lambda: []
        module = SimpleNamespace(seed=lambda apps, schema_editor: passed.append(schema_editor))
        with mock.patch.object(operations, "S015_MIGRATION_SEEDS", (("stub", "seed"),)), \
                mock.patch.object(operations.importlib, "import_module", return_value=module), \
                mock.patch.object(operations.transaction, "atomic", return_value=contextlib.nullcontext()):
            backend.execute_sql_flush(["TRUNCATE core_role"])
        self.assertEqual(passed, [editor])
        self.assertIs(passed[0].connection, connection)


class AliasCheckpointTests(unittest.TestCase):
    def test_all_aliases_share_the_seed_baseline(self):
        self.assertEqual(len(isolation_state.expected_seed_rows()), 3)

    def test_due_alias_is_captured_and_recorded_with_alias_specific_connection(self):
        self.assertEqual(due_aliases(["s014_secondary", "archive"], ["default", "s014_secondary"]), ["s014_secondary"])
        self.assertEqual(due_aliases(["s014_secondary", "archive"], ["default"]), [])
        with _alias_checkpoint_stubs("tests.alias_probe.AliasCase.test_read") as (
            runner, test, emitted, seen, default_conn, alias_conn
        ):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertTrue(runner.checkpoint_a(test))
        self.assertEqual(seen, [default_conn, alias_conn])
        record = emitted[0]
        alias_record = record["alias_checks"]["s014_secondary"]
        self.assertEqual((record["checkpoint"], record["established"]), ("CP-A", True))
        self.assertEqual((alias_record["established"], alias_record["capture"]), (True, "light"))
        self.assertEqual(alias_record["sequence_check_summary"]["performed"], ["Q1", "Q2", "Q3", "Q4"])

    def test_post_test_alias_check_is_attributed_to_declaring_test(self):
        test_id = "tests.alias_probe.AliasCase.test_leak"
        with _alias_checkpoint_stubs(test_id, mismatch=True) as (
            runner, test, emitted, seen, _default_conn, alias_conn
        ):
            record = runner.checkpoint_a_after(test)
        self.assertEqual(seen, [alias_conn, alias_conn])
        self.assertEqual(record["checkpoint"], "CP-A'")
        self.assertEqual(record["after"], "post-teardown")
        self.assertEqual(record["test"], test_id)
        self.assertFalse(record["established"])
        self.assertEqual(record["attributed_to"], test_id)
        self.assertEqual(record["alias_checks"]["s014_secondary"]["attributed_to"], test_id)
        self.assertEqual(runner.checkpoint_stop_id, test_id)
        self.assertEqual(runner.checkpoint_failure_test, test_id)
        self.assertGreater(runner.timing_by_id[test_id]["t_check"], 0.01)
        self.assertEqual(emitted, [record])

    def test_isolation_verdict_requires_post_test_alias_check(self):
        test_id = "tests.alias_probe.AliasCase.test_read"
        suite = [test_id]
        checkpoints = [
            {
                "checkpoint": "CP-A",
                "test": test_id,
                "established": True,
                "post_teardown_aliases_expected": ["s014_secondary"],
                "alias_checks": {"s014_secondary": {"established": True}},
            },
            {"checkpoint": "CP-Z", "established": True},
        ]
        verdict, reasons = isolation_verdict(
            True, suite, suite, suite, checkpoints, 0
        )
        self.assertEqual(verdict, "NOT ESTABLISHED")
        self.assertTrue(
            any("missing post-test alias checkpoints" in reason for reason in reasons)
        )

        checkpoints.insert(
            1,
            {
                "checkpoint": "CP-A'",
                "test": test_id,
                "after": "post-teardown",
                "established": True,
                "alias_checks": {"s014_secondary": {"established": True}},
            },
        )
        self.assertEqual(
            isolation_verdict(True, suite, suite, suite, checkpoints, 0)[0],
            "PASS",
        )

    @contextlib.contextmanager
    def _post_teardown_result(self, teardown, post_check):
        test_id = "tests.alias_probe.AliasCase.test_leak"
        with _alias_checkpoint_stubs(test_id) as (runner, _, _, _, _, _):
            runner.checkpoint_a = lambda test: True
            runner.checkpoint_a_after = post_check
            result = runner.get_resultclass()(
                stream=io.StringIO(), descriptions=False, verbosity=0,
                nonce="post-teardown-alias-check",
            )
            test = SimpleNamespace(id=lambda: test_id, _post_teardown=teardown)
            output = io.StringIO()
            with mock.patch("sys.stderr", output), mock.patch(
                "verification.isolation.runner.clear_test_timing_context"
            ):
                result.startTest(test)
                result.stopTest(test)
                yield runner, result, test, output

    def test_post_teardown_refusal_is_visible_and_stops_buffered_run(self):
        lifecycle = []

        def teardown():
            lifecycle.append("teardown")

        def post_check(test, *, teardown_error=None):
            lifecycle.append("post-check")
            return {
                "established": False,
                "reason": "post-test alias mismatch",
            }

        with self._post_teardown_result(teardown, post_check) as (
            _, result, test, buffered_output
        ):
            self.assertFalse(result.shouldStop)
            test._post_teardown()

        self.assertTrue(result.shouldStop)
        self.assertEqual(lifecycle, ["teardown", "post-check"])
        self.assertIn(
            "ISOLATION CP-A REFUSED after tests.alias_probe.AliasCase.test_leak",
            buffered_output.getvalue(),
        )
        self.assertEqual(result.records[-1]["kind"], "STOP")

    def test_post_teardown_error_is_reported_and_still_checked(self):
        lifecycle = []

        def teardown():
            lifecycle.append("teardown")
            raise RuntimeError("teardown failure")

        def post_check(test, *, teardown_error=None):
            lifecycle.append(("post-check", type(teardown_error).__name__))
            return {
                "established": True,
                "post_teardown_error_type": type(teardown_error).__name__,
            }

        with self._post_teardown_result(teardown, post_check) as (
            _, _result, test, buffered_output
        ):
            with self.assertRaisesRegex(RuntimeError, "teardown failure"):
                test._post_teardown()

        self.assertEqual(lifecycle, ["teardown", ("post-check", "RuntimeError")])
        self.assertIn(
            "ISOLATION POST-TEARDOWN ERROR after "
            "tests.alias_probe.AliasCase.test_leak: RuntimeError",
            buffered_output.getvalue(),
        )
        self.assertIn(
            "ISOLATION CP-A' ESTABLISHED after "
            "tests.alias_probe.AliasCase.test_leak",
            buffered_output.getvalue(),
        )

    def test_cpz_record_includes_every_observed_alias(self):
        with _alias_checkpoint_stubs("tests.alias_probe.AliasCase.test_read") as (
            runner, _test, emitted, _seen, _default_conn, _alias_conn
        ):
            with contextlib.redirect_stderr(io.StringIO()):
                runner.checkpoint_z()
        record = emitted[0]
        self.assertEqual(record["observed_aliases"], ["default", "s014_secondary"])
        self.assertTrue(record["established"] and record["alias_checks"]["s014_secondary"]["established"])

    def test_sequence_summary_marks_only_unchecked_aliases_unobserved(self):
        captured = {"sequence_discipline": []}
        checked = sequence_check_summary(captured, ["default", "s014_secondary"], checked_aliases=("default", "s014_secondary"))
        unchecked = sequence_check_summary(captured, ["default", "s014_secondary"], checked_aliases=("default",))
        self.assertFalse(
            any("database alias" in reason for reason in checked["not_established"])
        )
        self.assertIn(
            "database alias s014_secondary is not observed by the isolation instrument",
            unchecked["not_established"],
        )


class B4FreshEvidence(_Tmp):
    def test_b4_existing_evidence_directory_is_refused_and_preserved(self):
        d = os.path.join(self.tmp, "reused"); os.mkdir(d)
        with open(os.path.join(d, "transcript.txt"), "w") as f:
            f.write("EARLIER RUN EVIDENCE\n")
        with self.assertRaises(route_mod.RouteRefused):
            route_mod.Route(d, "probe")
        self.assertEqual(route_mod.read_text(os.path.join(d, "transcript.txt")), "EARLIER RUN EVIDENCE\n"); self.assertEqual(os.listdir(d), ["transcript.txt"])

    def _iso(self, r, records):
        p = r.path("S015_isolation.jsonl")
        with open(p, "w") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")
        return p

    def _good(self, r, ids):
        n = r.nonce
        return ([{"checkpoint": "CP-0", "requirements_met": True, "run_nonce": n}] +
                [{"checkpoint": "CP-A", "test": t, "established": True, "applicable": True, "database_access": "permitted", "run_nonce": n} for t in ids] +
                [{"checkpoint": "CP-F", "test": ids[0], "established": False, "run_nonce": n},
                 {"checkpoint": "TIMING", "test": ids[0], "run_nonce": n},
                 {"checkpoint": "CP-Z", "established": True, "run_nonce": n},
                 {"checkpoint": "SUMMARY", "isolation_verdict": "PASS",
                  "cpz_established": True, "run_nonce": n}])

    def test_b4_summary_only_isolation_is_not_pass(self):
        r = self.route()
        res = r.read_isolation(self._iso(r, [{"checkpoint": "SUMMARY", "isolation_verdict": "PASS", "run_nonce": r.nonce}]), [TID])
        self.assertNotEqual(res["verdict"], "PASS")

    def test_b4_records_from_another_run_are_not_evidence(self):
        r = self.route()
        recs = self._good(r, [TID])
        for rec in recs:
            rec["run_nonce"] = "earlier-run"
        self.assertIsNone(r.read_isolation(self._iso(r, recs), [TID])["verdict"])

    def test_b4_duplicate_or_missing_checkpoints_are_not_established(self):
        r = self.route()
        dup = self._good(r, [TID]); dup.insert(1, dict(dup[1]))
        self.assertEqual(r.read_isolation(self._iso(r, dup), [TID])["verdict"], "NOT ESTABLISHED")
        os.remove(r.path("S015_isolation.jsonl"))
        self.assertEqual(r.read_isolation(self._iso(r, self._good(r, [TID])), [TID, "probe.C.test_b"])["verdict"], "NOT ESTABLISHED")

    def test_b4_summary_disagreeing_with_records_is_not_established(self):
        r = self.route()
        recs = self._good(r, [TID]); recs[1]["established"] = False
        self.assertEqual(r.read_isolation(self._iso(r, recs), [TID])["verdict"], "NOT ESTABLISHED")

    def test_b4_positive_current_complete_records_pass(self):
        r = self.route()
        na = {"checkpoint": "CP-A", "test": "probe.C.test_b", "established": None, "applicable": False, "database_access": "forbidden", "run_nonce": r.nonce}
        recs = self._good(r, [TID]); recs.insert(2, na)
        res = r.read_isolation(self._iso(r, recs), [TID, "probe.C.test_b"])
        self.assertEqual((res["verdict"], res["established"], res["not_applicable"]), ("PASS", 1, 1))


# ------------------------------------------------------------------ B5
class B5Identity(_Tmp):
    def _repo(self):
        g = os.path.join(self.tmp, "repo"); os.makedirs(os.path.join(g, "core", "migrations"))

        def git(*a):
            subprocess.run(["git", "-C", g, "-c", "user.name=probe", "-c", "user.email=probe@example.invalid", *a], check=True, capture_output=True)
        subprocess.run(["git", "init", "-q", g], check=True, capture_output=True)
        git("config", "core.autocrlf", "false")
        # files are written as bytes with LF endings, so the committed content is the same on every platform
        # (text mode would write CRLF on Windows - found in the qualifying run CHC_20260917T103926Z)
        for rel, data in (("core/migrations/0001_initial.py", b"# initial\n"), ("old.py", b"# content\n" * 30), ("gone.py", b"# to delete\n")):
            with open(os.path.join(g, *rel.split("/")), "wb") as f:
                f.write(data)
        git("add", "-A"); git("commit", "-qm", "base")
        return g, git

    def identity(self, g):
        r = self.route(name="ev-identity")
        with mock.patch.object(route_mod, "ROOT", g):
            return r.identity()

    def test_b5_failed_git_is_invalid_and_never_clean(self):
        r = self.route()
        with mock.patch.object(route_mod, "run_git", return_value=(128, b"", "fatal: not a git repository")):
            ident = r.identity()
        self.assertFalse(ident["valid"]); self.assertNotIn("working_tree_clean", ident); self.assertIn("not a git repository", ident["problems"][0])

    def test_b5_failed_identity_makes_the_run_incomplete(self):
        r = self.route()
        with mock.patch.object(r, "identity", return_value={"valid": False, "problems": ["git failed"]}), \
                mock.patch.dict(os.environ, {"INTEVIA_POSTGRES_PASSWORD": ""}, clear=False), mock.patch.object(route_mod.sys.stdin, "isatty", return_value=False), \
                contextlib.redirect_stdout(io.StringIO()):
            code = r.run(skip_mutations=True)
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(route_mod.read_text(r.path("summary.json")))["steps"][0], {"id": "IDENTITY", "outcome": "INCOMPLETE", "reason": "git failed"})

    def test_b5_rename_quoted_path_and_deletion_are_identified_by_their_bytes(self):
        g, git = self._repo()
        git("mv", "old.py", "new.py")
        with open(os.path.join(g, "new.py"), "ab") as f:
            f.write(b"# changed\n")
        # a path git quotes in its ordinary porcelain output (non-ASCII, spaces, an apostrophe and a hash), using only
        # characters valid on NTFS as well as POSIX file systems (a double quote is not - found in CHC_20260917T103926Z)
        odd = "dir with space/caf\u00e9 'quoted' #1.py"
        os.makedirs(os.path.join(g, "dir with space"))
        with open(os.path.join(g, *odd.split("/")), "wb") as f:
            f.write(b"# odd path\n")
        os.remove(os.path.join(g, "gone.py"))
        ident = self.identity(g)
        self.assertTrue(ident["valid"], ident["problems"])
        by = {c["path"]: c for c in ident["working_tree_changes"]}
        self.assertEqual(by["new.py"]["orig_path"], "old.py")
        self.assertEqual(by["new.py"]["sha256"], route_mod.sha256_file(os.path.join(g, "new.py")))
        self.assertEqual(by[odd]["sha256"], route_mod.sha256_file(os.path.join(g, *odd.split("/"))))
        self.assertEqual((by["gone.py"]["kind"], by["gone.py"]["sha256"]), ("deleted", None))

    def test_b5_non_file_entry_is_unsupported_and_invalid(self):
        # an untracked nested repository is reported by git as a directory entry, not a regular file; it needs no special
        # privilege on any platform (a symlink needs one on Windows - found in CHC_20260917T103926Z)
        g, git = self._repo()
        nested = os.path.join(g, "nested")
        subprocess.run(["git", "init", "-q", nested], check=True, capture_output=True)
        with open(os.path.join(nested, "x.py"), "wb") as f:
            f.write(b"# nested\n")
        ident = self.identity(g)
        self.assertFalse(ident["valid"]); self.assertTrue(any("unsupported" in p for p in ident["problems"]), ident["problems"])

    def test_b5_positive_clean_tree_and_working_tree_git_tree(self):
        g, git = self._repo()
        ident = self.identity(g)
        self.assertTrue(ident["valid"]); self.assertTrue(ident["working_tree_clean"])
        self.assertEqual(ident["working_tree_git_tree"], ident["commit_tree"])

    def test_b5_line_endings_differ_in_bytes_not_in_git_tree(self):
        g, git = self._repo()
        git("config", "core.autocrlf", "true")
        with open(os.path.join(g, "old.py"), "rb") as f:
            data = f.read()
        with open(os.path.join(g, "old.py"), "wb") as f:
            f.write(data.replace(b"\n", b"\r\n"))
        ident = self.identity(g)
        self.assertTrue(ident["valid"], ident["problems"])
        self.assertEqual(ident["working_tree_git_tree"], ident["commit_tree"])  # same git content (the O-1 situation)


class B5EvidenceInsideRepository(_Tmp):
    """CI writes its evidence into the repository (verification-evidence/, which .gitignore ignores). Found in CI run
    35215316126: identity failed there and the route correctly reported INCOMPLETE.

    Every git call in these self-tests captures its output: Git for Windows prints line-ending warnings, which would
    otherwise land inside the verbose runner's status lines (found in the v0.4 qualifying run)."""

    def _repo(self, gitignore):
        g = os.path.join(self.tmp, "repo"); os.makedirs(os.path.join(g, "core", "migrations"))
        subprocess.run(["git", "init", "-q", g], check=True, capture_output=True)
        files = {"core/migrations/0001_initial.py": b"# initial\n", "a.py": b"# a\n"}
        if gitignore is not None:
            files[".gitignore"] = gitignore
        for rel, data in files.items():
            with open(os.path.join(g, *rel.split("/")), "wb") as f:
                f.write(data)
        subprocess.run(["git", "-C", g, "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", g, "-c", "user.name=probe", "-c", "user.email=probe@example.invalid", "commit", "-qm", "base"], check=True, capture_output=True)
        return g

    def _identity_with_evidence_inside(self, g):
        r = route_mod.Route(os.path.join(g, "verification-evidence"), "probe")
        self.addCleanup(lambda: r.transcript.close())
        with mock.patch.object(route_mod, "ROOT", g):
            return r.identity()

    def test_b5_ignored_evidence_directory_inside_the_repository(self):
        g = self._repo(b"verification-evidence/\n")
        ident = self._identity_with_evidence_inside(g)
        self.assertTrue(ident["valid"], ident["problems"])
        self.assertTrue(ident["working_tree_clean"]); self.assertEqual(ident["working_tree_git_tree"], ident["commit_tree"])

    def test_b5_unignored_evidence_directory_inside_the_repository_is_excluded(self):
        g = self._repo(None)
        ident = self._identity_with_evidence_inside(g)
        self.assertTrue(ident["valid"], ident["problems"])
        self.assertTrue(ident["working_tree_clean"]); self.assertEqual(ident["working_tree_git_tree"], ident["commit_tree"])


# ------------------------------------------------------------------ UFUND-3 Change C v0.6: A1 residuals RC-B2, RC-B3, RC-B4, RC-O1, RC-O2
class _Server:
    """An in-memory PostgreSQL stand-in for route-level control flow: databases by name and oid, CREATE/DROP by name, and
    injectable failures. It is the route's orchestration that is under test, not PostgreSQL."""

    def __init__(self, route):
        self.r, self.dbs, self.next_oid, self.sql, self.drops = route, {}, 100, [], []
        self.fail_oid_once_for = None       # name: the next oid lookup on a connection raises (after a successful CREATE)
        self.replace_after_lookup = None    # name: after the next oid lookup on a connection, another client replaces it
        self.create_raises_after = None     # name: CREATE succeeds on the server but the client sees an error
        self.connect_dbnames = []

    def connect(self, dbname=None):
        self.connect_dbnames.append(dbname)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        pass

    def execute(self, sql, params=None):
        self.sql.append(sql)
        if sql.startswith("CREATE DATABASE"):
            name = sql.split('"')[1]
            if name in self.dbs:
                exc = RuntimeError('database "%s" already exists' % name); exc.sqlstate = "42P04"
                raise exc
            self.next_oid += 1; self.dbs[name] = self.next_oid
            if self.create_raises_after == name:
                self.create_raises_after = None
                raise RuntimeError("connection lost after CREATE was sent")
        elif sql.startswith("DROP DATABASE"):
            name = sql.split('"')[1]
            self.drops.append((name, self.dbs.get(name))); self.dbs.pop(name, None)
        return SimpleNamespace(fetchone=lambda: (None,), fetchall=lambda: [])

    def oid_of(self, name, conn=None):
        if conn is not None and self.fail_oid_once_for == name and name in self.dbs:
            self.fail_oid_once_for = None
            raise RuntimeError("injected oid lookup failure")
        oid = self.dbs.get(name)
        if conn is not None and self.replace_after_lookup == name and oid is not None:
            self.replace_after_lookup = None
            self.dbs[name] = 9001  # another client dropped and recreated the name
        return oid


class RCB2B3FixtureLifecycle(_Tmp):
    """RC-B2 (every fixture creation attempt is accounted for, exceptions included) and RC-B3 (every drop, including the
    deliberate replacement transition, checks ownership first). A1's three failure injections and its replacement
    counterexample, plus an uncertain CREATE and a duplicate at CREATE, against the positive control."""

    def _run(self, inject=None, collision_log="CollisionRefused: test database exists\n"):
        r = self.route()
        srv = _Server(r)
        if inject:
            inject(r, srv)

        def collide(cmd, path, env):
            name = r.db_name("OWN-COLLIDE")
            with open(path, "x") as f:
                f.write(collision_log + "EXIT_STATUS: 1\n")
            ownership_env = {ownership.RECEIPT_ENV: env[ownership.RECEIPT_ENV], ownership.NONCE_ENV: r.nonce}
            with mock.patch.dict(os.environ, ownership_env):
                ownership.write_receipt("collision_refused", name=name, existing_oid=srv.dbs.get(name))
            return 1

        env = {"INTEVIA_POSTGRES_USER": "probe", "INTEVIA_POSTGRES_PASSWORD": "not-a-credential"}
        fake_lock = mock.MagicMock(); fake_lock.execute.return_value.fetchall.return_value = []
        with mock.patch.dict(os.environ, env), mock.patch.object(r, "identity", return_value={"valid": True, "problems": [], "tested": "x"}), \
                mock.patch.object(r, "acquire_lock", side_effect=lambda: setattr(r, "lock_conn", fake_lock)), \
                mock.patch.object(r, "lock_record", return_value={"held": True}), mock.patch.object(r, "server", return_value={"version": "simulated"}), \
                mock.patch.object(r, "step_offline", return_value={"id": "OFFLINE", "outcome": "PASS"}), \
                mock.patch.object(r, "step_suite", side_effect=lambda sid, *a, **k: {"id": sid, "outcome": "PASS"}), \
                mock.patch.object(r, "step_lock", return_value={"id": "LOCK", "outcome": "PASS"}), \
                mock.patch.object(r, "_ensure_candidate_binding", return_value=(None, "a" * 64, "b" * 64)), \
                mock.patch.object(r, "connect", side_effect=srv.connect), mock.patch.object(r, "oid_of", side_effect=srv.oid_of), \
                mock.patch.object(r, "run_logged", side_effect=collide), contextlib.redirect_stdout(io.StringIO()):
            code = r.run(skip_mutations=True)
        summary = json.loads(route_mod.read_text(r.path("summary.json")))
        return r, srv, code, summary

    def names(self, summary):
        return {n["name"]: n for n in summary["cleanup"]["names"]}

    def test_v06_1_positive_control_exit_0_clean_and_every_drop_by_its_own_oid(self):
        r, srv, code, summary = self._run()
        self.assertEqual((code, summary["cleanup"]["outcome"]), (0, "CLEAN"), summary)
        self.assertEqual(srv.dbs, {})
        self.assertTrue(all(oid is not None and oid < 9000 for _, oid in srv.drops))
        own = next(s for s in summary["steps"] if s["id"] == "OWNERSHIP")
        self.assertEqual(own["outcome"], "PASS", own)

    def test_v06_2_oid_failure_after_collision_fixture_create_is_unresolved_exit_3(self):
        def inject(r, srv):
            srv.fail_oid_once_for = r.db_name("OWN-COLLIDE")
        r, srv, code, summary = self._run(inject)
        name = r.db_name("OWN-COLLIDE")
        self.assertEqual((code, summary["cleanup"]["outcome"]), (3, "NOT CLEAN"))
        self.assertIn(name, srv.dbs)  # left untouched: its identity was never confirmed
        self.assertTrue(self.names(summary)[name]["state"].startswith("UNRESOLVED"))

    def test_v06_3_oid_failure_after_first_replacement_fixture_create_is_unresolved_exit_3(self):
        def inject(r, srv):
            srv.fail_oid_once_for = r.db_name("OWN-REPLACE")
        r, srv, code, summary = self._run(inject)
        name = r.db_name("OWN-REPLACE")
        self.assertEqual(code, 3); self.assertIn(name, srv.dbs)
        self.assertTrue(self.names(summary)[name]["state"].startswith("UNRESOLVED"))

    def test_v06_4_receipt_write_failure_after_owned_create_is_cleaned_by_its_oid(self):
        real_open = open

        def failing_open(path, *a, **k):
            if str(path).endswith("OWN-OWNED_db_receipts.jsonl"):
                raise OSError("injected receipt write failure")
            return real_open(path, *a, **k)

        with mock.patch("builtins.open", side_effect=failing_open):
            r, srv, code, summary = self._run()
        name = r.db_name("OWN-OWNED")
        self.assertEqual((code, summary["cleanup"]["outcome"]), (2, "CLEAN"))  # step INCOMPLETE; the fixture is accounted for
        self.assertNotIn(name, srv.dbs)
        self.assertTrue(self.names(summary)[name]["state"].startswith("DROPPED BY THE ROUTE"))

    def test_v06_5_a1_replacement_before_the_transition_drop_is_left_untouched(self):
        def inject(r, srv):
            srv.replace_after_lookup = r.db_name("OWN-REPLACE")
        r, srv, code, summary = self._run(inject)
        name = r.db_name("OWN-REPLACE")
        self.assertNotIn((name, 9001), srv.drops)            # the replacement was not dropped
        self.assertEqual(srv.dbs.get(name), 9001)
        own = next(s for s in summary["steps"] if s["id"] == "OWNERSHIP")
        self.assertEqual(own["outcome"], "FAIL")
        self.assertEqual((code, summary["cleanup"]["outcome"]), (3, "NOT CLEAN"))
        self.assertTrue(self.names(summary)[name]["state"].startswith("REPLACED"))

    def test_v06_6_create_outcome_unknown_is_left_untouched_and_unresolved(self):
        def inject(r, srv):
            srv.create_raises_after = r.db_name("OWN-OWNED")
        r, srv, code, summary = self._run(inject)
        name = r.db_name("OWN-OWNED")
        self.assertEqual(code, 3); self.assertIn(name, srv.dbs)
        self.assertIn("never confirmed", self.names(summary)[name]["state"])

    def test_v06_7_name_taken_by_another_client_is_not_touched_and_is_not_claimed(self):
        def inject(r, srv):
            srv.dbs[r.db_name("OWN-OWNED")] = 7777
        r, srv, code, summary = self._run(inject)
        name = r.db_name("OWN-OWNED")
        self.assertEqual(srv.dbs.get(name), 7777); self.assertNotIn((name, 7777), srv.drops)
        self.assertEqual((code, summary["cleanup"]["outcome"]), (2, "CLEAN"))
        self.assertTrue(self.names(summary)[name]["state"].startswith("NOT CREATED"))

    def test_v06_8_rc_o2_a_collision_run_that_reports_a_test_outcome_fails_the_check(self):
        log = "test_x (probe.C.test_x) ... ERROR\n\n" + "-" * 70 + "\nRan 1 test in 0.001s\n\nFAILED (errors=1)\n"
        r, srv, code, summary = self._run(collision_log=log)
        own = next(s for s in summary["steps"] if s["id"] == "OWNERSHIP")
        self.assertFalse(own["checks"]["collision"]["no_test_outcome_reported"]); self.assertEqual(own["outcome"], "FAIL")

    def test_v06_9_runner_database_whose_creation_receipt_failed_is_left_untouched(self):
        r = self.route(); name = r.db_name("S015")
        p = r.path("S015_db_receipts.jsonl")
        with open(p, "w") as f:
            f.write(json.dumps({"event": "create_attempt", "name": name, "run_nonce": r.nonce}) + "\n")
        sql = []
        with mock.patch.object(r, "oid_of", return_value=4321), mock.patch.object(r, "connect", return_value=_Conn(sql)):
            rec = r.cleanup_one("S015", name, p)
        self.assertFalse(rec["resolved"]); self.assertIn("CREATE was issued", rec["state"]); self.assertEqual(sql, [])


class RCO1LockDatabase(_Tmp):
    """RC-O1: the lock is always taken in the common lock database, whatever INTEVIA_POSTGRES_DB names; an unreachable
    lock database refuses the run instead of running it unserialised. Live contention is the route's LOCK step."""

    def test_v06_10_lock_uses_the_common_database_whatever_the_connection_database(self):
        r = self.route()
        srv = _Server(r)
        with mock.patch.dict(os.environ, {"INTEVIA_POSTGRES_DB": "some_other_database"}), mock.patch.object(r, "connect", side_effect=srv.connect), \
                mock.patch.object(srv, "execute", return_value=SimpleNamespace(fetchone=lambda: (True,))):
            r.acquire_lock()
        self.assertEqual(srv.connect_dbnames, [route_mod.LOCK_DATABASE])

    def test_v06_11_unreachable_lock_database_refuses_the_run(self):
        r = self.route()
        env = {"INTEVIA_POSTGRES_USER": "probe", "INTEVIA_POSTGRES_PASSWORD": "not-a-credential"}
        with mock.patch.dict(os.environ, env), mock.patch.object(r, "identity", return_value={"valid": True, "problems": [], "tested": "x"}), \
                mock.patch.object(r, "connect", side_effect=RuntimeError("permission denied for database postgres")), \
                contextlib.redirect_stdout(io.StringIO()):
            code = r.run(skip_mutations=True)
        summary = json.loads(route_mod.read_text(r.path("summary.json")))
        self.assertEqual(code, 2)
        self.assertIn("lock database", summary["steps"][1]["reason"])
        self.assertEqual(summary["cleanup"]["outcome"], "NOTHING TO CLEAN")


class RCB4IndexFlags(B5Identity):
    """RC-B4 (Human Governor D-3, 17 Sep 2026): flagged index states are refused with the affected paths; the route never
    clears flags or touches the checkout; the computed working tree is still compared with the commit."""

    def _flag(self, g, git, flag, change=True):
        git("update-index", flag, "old.py")
        if change:
            with open(os.path.join(g, "old.py"), "ab") as f:
                f.write(b"# hidden change\n")
        with open(os.path.join(g, "old.py"), "rb") as f:
            return f.read()

    def _assert_refused_and_untouched(self, g, ident, before, expect_letter):
        self.assertFalse(ident["valid"])
        self.assertNotIn("exactly", ident["tested"]); self.assertFalse(ident["working_tree_clean"])
        self.assertTrue(any("old.py" in p and "index flags" in p for p in ident["problems"]), ident["problems"])
        with open(os.path.join(g, "old.py"), "rb") as f:
            self.assertEqual(f.read(), before)  # the working file is untouched
        tags = subprocess.run(["git", "-C", g, "ls-files", "-v", "old.py"], check=True, capture_output=True, text=True).stdout
        self.assertEqual(tags[0], expect_letter)  # the flag is still set: the route did not clear it

    def test_v06_12_skip_worktree_with_a_hidden_change_is_refused(self):
        g, git = self._repo(); before = self._flag(g, git, "--skip-worktree")
        ident = self.identity(g)
        self._assert_refused_and_untouched(g, ident, before, "S")
        self.assertNotEqual(ident["working_tree_git_tree"], ident["commit_tree"])  # the independent comparison is retained

    def test_v06_13_assume_unchanged_with_a_hidden_change_is_refused(self):
        g, git = self._repo(); before = self._flag(g, git, "--assume-unchanged")
        self._assert_refused_and_untouched(g, self.identity(g), before, "h")

    def test_v06_14_a_flag_without_a_change_is_still_refused(self):
        g, git = self._repo(); before = self._flag(g, git, "--skip-worktree", change=False)
        self._assert_refused_and_untouched(g, self.identity(g), before, "S")

    def test_v06_15_tree_that_the_inventory_does_not_explain_is_invalid_without_flags(self):
        g, git = self._repo()
        with open(os.path.join(g, "old.py"), "ab") as f:
            f.write(b"# change\n")
        real = route_mod.run_git

        def status_sees_nothing(args, env=None, cwd=None):
            if args[:1] == ["status"]:
                return 0, b"", ""
            return real(args, env=env, cwd=cwd)

        with mock.patch.object(route_mod, "run_git", side_effect=status_sees_nothing):
            ident = self.identity(g)
        self.assertFalse(ident["valid"]); self.assertTrue(any("does not list" in p and "old.py" in p for p in ident["problems"]))
        self.assertNotIn("exactly", ident["tested"])

    def test_v06_16_positive_listed_change_and_clean_tree_remain_valid(self):
        g, git = self._repo()
        self.assertEqual(self.identity(g)["tested"].split()[-1], "exactly")
        with open(os.path.join(g, "old.py"), "ab") as f:
            f.write(b"# listed change\n")
        r = self.route(name="ev-identity-2")
        with mock.patch.object(route_mod, "ROOT", g):
            ident = r.identity()
        self.assertTrue(ident["valid"], ident["problems"]); self.assertEqual(ident["tree_delta_paths"], ["old.py"])


class DatabaseNameBounds(_Tmp):
    def test_route_tokens_bound_every_step_name_and_preserve_run_identity(self):
        run_ids = ("gh389745356161", "12345678901234567890")
        for index, run_id in enumerate(run_ids):
            route = self.route(name="db-name-%d" % index, run_id=run_id)
            expected_token = "g" + hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:10]
            self.assertEqual(route.run_token, expected_token)
            self.assertEqual(
                route.database_identity(),
                {"run_id": run_id, "run_token": expected_token},
            )
            self.assertEqual(
                route.database_name_pattern(),
                route_mod.DB_PREFIX + expected_token + "\\_%",
            )
            for step in route_mod.STEP_CODES:
                name = route.db_name(step)
                self.assertLessEqual(
                    len(name.encode("utf-8")),
                    route_mod.PRIMARY_TEST_DATABASE_NAME_MAX,
                    (run_id, step, name),
                )
                self.assertLessEqual(
                    len((name + "_s014_secondary").encode("utf-8")),
                    route_mod.POSTGRES_IDENTIFIER_MAX,
                    (run_id, step, name),
                )


# ------------------------------------------------------------------ CI-CLONE-1 (clone mode in the CI route)
# Controlled example values for the staged evidence below. They are synthetic OIDs and digests chosen for the test, not
# values read from a live database; every name, nonce and path is derived from the route under test.
TEMPLATE_CP0 = "e" * 64
TEMPLATE_IDENTITY = "1" * 64
TEMPLATE_NAME = "test_intevia_tpl_vr_%s_%s" % ("0" * 8, TEMPLATE_IDENTITY[:32])
TEMPLATE_OID = 4100


class _CloneFixture(_Tmp):
    """Stages, for one step, the evidence a clone-mode child leaves behind: receipts, TT1 log lines and (for an
    isolation step) the CP-0 record. Names, nonces and paths come from the route under test; OIDs and digests are the
    controlled example values above."""

    def receipts(self, r, sid, *records):
        path = r.receipts_path(sid)
        with mock.patch.dict(os.environ, {ownership.RECEIPT_ENV: path, ownership.NONCE_ENV: r.nonce}):
            for event, fields in records:
                ownership.write_receipt(event, **fields)
        return path

    def template_record(self, r, name=TEMPLATE_NAME, oid=TEMPLATE_OID, cp0=TEMPLATE_CP0):
        return ("template_created", {"name": name, "oid": oid, "identity": TEMPLATE_IDENTITY, "cp0_digest": cp0,
                                     "allow_connections": False, "connection_refused": True})

    def clone_record(self, db, oid=TEMPLATE_OID, name=TEMPLATE_NAME, cp0=TEMPLATE_CP0):
        return ("clone_created", {"name": db, "clone_oid": 5000, "template_name": name, "template_oid": oid,
                                  "identity": TEMPLATE_IDENTITY, "template_cp0_digest": cp0})

    def cp0_record(self, r, sid, clone=TEMPLATE_CP0, template=TEMPLATE_CP0):
        path = r.path(sid + "_isolation.jsonl")
        with open(path, "x", encoding="utf-8") as handle:
            handle.write(json.dumps({"checkpoint": "CP-0", "run_nonce": r.nonce, "requirements_met": True,
                                     "template_cp0_digest": template, "clone_cp0_digest": clone}) + "\n")
        return path

    GOOD_LINES = ["TT1_TEMPLATE_BUILD_SECONDS=NOT_APPLICABLE", "TT1_CLONE_SETUP_SECONDS=1.5", "TT1_SETUP_OUTCOME=succeeded"]

    def step(self, r, sid, log_lines, receipts, isolation_path=None):
        """r.step_suite with the child mocked: the child's log and receipts are what the fixture staged."""
        collection = {"ids": [TID], "count": 1, "digest": "d" * 64, "failed_loads": [], "run_nonce": r.nonce, "facts": {}}

        def fake_collection(command, **kwargs):
            with open(command[4], "x", encoding="utf-8") as handle:
                json.dump(collection, handle)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        def fake_run_logged(command, log_path, env):
            with open(log_path, "x", encoding="utf-8") as handle:
                handle.write("\n".join(log_lines) + "\ntest_a (probe.C.test_a) ... ok\n" + TAIL)
            return 0

        env = {ownership.RECEIPT_ENV: receipts, "VERIFICATION_TEMPLATE_MODE": "clone",
               "VERIFICATION_ISOLATION_JSON": isolation_path or r.path(sid + "_isolation.jsonl")}
        with mock.patch.object(route_mod.subprocess, "run", side_effect=fake_collection), \
                mock.patch.object(r, "run_logged", side_effect=fake_run_logged), \
                mock.patch.object(r, "django_env", return_value=env), \
                mock.patch.object(r, "oid_of", return_value=None), \
                mock.patch.object(r, "read_isolation", return_value={"verdict": "PASS", "applicable": 1, "established": 1, "not_applicable": 0}):
            return r.step_suite(sid, "label", [TID], isolation=isolation_path is not None)

    def run_route(self, r, suite, child, oid_of):
        """r.run with the lock, identity and the other steps mocked; `suite` plays the database steps, `child` the
        TPL-FINAL child process. Returns (exit code, summary.json)."""
        fake_lock = mock.MagicMock()
        fake_lock.execute.return_value.fetchall.return_value = []
        env = {"INTEVIA_POSTGRES_USER": "probe", "INTEVIA_POSTGRES_PASSWORD": "not-a-credential"}
        with mock.patch.dict(os.environ, env), mock.patch.object(r, "identity", return_value={"valid": True, "problems": [], "tested": "x"}), \
                mock.patch.object(r, "acquire_lock", side_effect=lambda: setattr(r, "lock_conn", fake_lock)), \
                mock.patch.object(r, "lock_record", return_value={"held": True}), mock.patch.object(r, "server", return_value={"version": "simulated"}), \
                mock.patch.object(r, "step_offline", return_value={"id": "OFFLINE", "outcome": "PASS"}), mock.patch.object(r, "step_suite", side_effect=suite), \
                mock.patch.object(r, "step_ownership", return_value={"id": "OWNERSHIP", "outcome": "PASS"}), \
                mock.patch.object(r, "step_lock", return_value={"id": "LOCK", "outcome": "PASS"}), \
                mock.patch.object(r, "_ensure_candidate_binding", return_value=(None, "a" * 64, "b" * 64)), \
                mock.patch.object(r, "run_logged", side_effect=child), \
                mock.patch.object(r, "oid_of", side_effect=oid_of), \
                contextlib.redirect_stdout(io.StringIO()):
            code = r.run(skip_mutations=True)
        return code, json.loads(route_mod.read_text(r.path("summary.json")))


class CICloneTemplateBinding(_CloneFixture):
    def test_every_database_step_is_bound_to_the_one_template_the_run_built(self):
        """Claim: in clone mode every database step's database is a clone of the one template SELF built (CP-0 matching
        the fresh-build reference, one build per run, the route never setting fresh mode) - proven two ways: (a) the
        route's environment and read-back refuse any step not so bound (no build, second build, other template, differing
        CP-0, fresh build, no clone, incomplete setup); (b) the shared set-up itself enforces the policy before any
        database work: SELF alone may build (one attempt, no retry), every later child reuses the recorded template and
        refuses a missing database, a missing receipt or a changed identity with zero build or migration calls; (c) in the
        route, a SELF whose build failed stops every later database step; (d) under a CI policy a second alias with its own
        physical database is refused before any template preparation or database creation; (b) reaches TT-1's real
        verify_registered_template for the missing database, with only the catalogue read stubbed."""
        from verification.isolation import template as template_mod
        from verification.isolation import template_mode

        r = self.route()
        r.permitted_skips = []
        with mock.patch.object(r, "_ensure_candidate_binding", return_value=(None, "a" * 64, "b" * 64)):
            env_self = r.django_env("test_db", "SELF")
            r.template = {"name": TEMPLATE_NAME, "oid": TEMPLATE_OID, "identity": TEMPLATE_IDENTITY, "cp0_digest": TEMPLATE_CP0, "clones": []}
            env_s015 = r.django_env("test_db", "S015")
            r.template = None
        self.assertEqual(env_self["VERIFICATION_TEMPLATE_MODE"], "clone",
                         "the CI route must create every database in clone mode (R2 as varied 31 Oct 2026); it set %r" % env_self["VERIFICATION_TEMPLATE_MODE"])
        self.assertEqual((env_self[template_mode.POLICY_ENV], env_s015[template_mode.POLICY_ENV]), ("build-once", "reuse-only"),
                         "SELF must run under the build-once policy and every later step under reuse-only; the route set %r / %r"
                         % (env_self[template_mode.POLICY_ENV], env_s015[template_mode.POLICY_ENV]))
        self.assertEqual(env_s015[template_mode.EXPECTED_ENV], "%s:%s:%s" % (TEMPLATE_NAME, TEMPLATE_OID, TEMPLATE_IDENTITY),
                         "a later step must be told the template SELF recorded (name:oid:identity); the route passed %r" % env_s015[template_mode.EXPECTED_ENV])
        self.assertEqual(env_self["INTEVIA_EVIDENCE_ROOT_PATH"], r.template_root, "the TT-1 evidence root must be the route's template root")
        self.assertEqual(os.path.basename(r.template_root), env_self["INTEVIA_EVIDENCE_ROOT"], "the evidence root name must equal the directory name")
        self.assertTrue(env_self["VERIFICATION_TEMPLATE_STORE"].startswith(r.template_root), "the template store must sit inside the template root")
        self.assertEqual(env_self[ownership.RECEIPT_ENV], r.receipts_path("SELF"), "a step's receipts must sit inside the template root")
        self.assertNotIn("INTEVIA_FRESH_AUTHORITY_FILE", env_self, "the route must never hand a child a fresh authority")

        # (a) positive control: SELF builds, S015 clones (isolation step), a mutation step clones and verifies before its SQL
        mut_sid = "MUT-" + sorted(route_mod.MUTATIONS)[0]
        self_db, s015_db, mut_db = r.db_name("SELF"), r.db_name("S015"), r.db_name(mut_sid)
        step = self.step(r, "SELF", ["TT1_TEMPLATE_BUILD_SECONDS=27.5", "TT1_CLONE_SETUP_SECONDS=1.25", "TT1_SETUP_OUTCOME=succeeded"],
                         self.receipts(r, "SELF", self.template_record(r), self.clone_record(self_db)), self.cp0_record(r, "SELF"))
        self.assertEqual((step["outcome"], step["template"]["built"], step["template"]["build_seconds"]), ("PASS", True, 27.5),
                         "SELF with a real build line, a template_created receipt and a matching clone must PASS as the building step; got %s" % step.get("reason", step["outcome"]))
        self.assertEqual(r.template["built_by_step"], "SELF", "the route must record SELF as the building step")
        step = self.step(r, "S015", self.GOOD_LINES, self.receipts(r, "S015", self.clone_record(s015_db)), self.cp0_record(r, "S015"))
        self.assertEqual((step["outcome"], step["template"]["built"], step["template"]["clone_cp0_matches_template"]), ("PASS", False, True),
                         "S015 reusing the template with a matching CP-0 must PASS without building; got %s" % step.get("reason", step["outcome"]))
        mut_receipts = self.receipts(r, mut_sid, self.clone_record(mut_db),
                                     ("clone_cp0_verified", {"name": mut_db, "template_cp0_digest": TEMPLATE_CP0, "clone_cp0_digest": TEMPLATE_CP0, "matches": True}))
        step = self.step(r, mut_sid, self.GOOD_LINES + ["TT1_CLONE_CP0_MATCHES_TEMPLATE=True"], mut_receipts)
        self.assertEqual((step["outcome"], step["template"]["clone_cp0_matches_template"]), ("PASS", True),
                         "a mutation step whose clone_cp0_verified receipt matches must PASS; got %s" % step.get("reason", step["outcome"]))
        self.assertEqual([c["step"] for c in r.template["clones"]], ["SELF", "S015", mut_sid], "every clone step must be inventoried on the template record")
        # (a) counterexamples, each on a fresh route so the positive control above is not disturbed
        cases = {
            "no template built before a clone step": lambda q, db: (self.GOOD_LINES, self.receipts(q, "S015", self.clone_record(db)), self.cp0_record(q, "S015")),
            "a second fresh build": lambda q, db: (["TT1_TEMPLATE_BUILD_SECONDS=26.0", "TT1_CLONE_SETUP_SECONDS=1.0", "TT1_SETUP_OUTCOME=succeeded"], self.receipts(q, "S015", self.template_record(q, oid=4101), self.clone_record(db, oid=4101)), self.cp0_record(q, "S015")),
            "a clone of another template": lambda q, db: (self.GOOD_LINES, self.receipts(q, "S015", self.clone_record(db, oid=9999)), self.cp0_record(q, "S015")),
            "a clone whose CP-0 differs": lambda q, db: (self.GOOD_LINES, self.receipts(q, "S015", self.clone_record(db)), self.cp0_record(q, "S015", clone="f" * 64)),
            "a fresh build instead of a clone": lambda q, db: (["TT1_FRESH_SETUP_SECONDS=28.0", "TT1_SETUP_OUTCOME=succeeded"], self.receipts(q, "S015", self.clone_record(db)), self.cp0_record(q, "S015")),
            "no clone at all": lambda q, db: (self.GOOD_LINES, self.receipts(q, "S015"), self.cp0_record(q, "S015")),
            "a setup that did not complete": lambda q, db: (["TT1_TEMPLATE_BUILD_SECONDS=NOT_APPLICABLE", "TT1_CLONE_SETUP_SECONDS=0.4", "TT1_SETUP_OUTCOME=failed"], self.receipts(q, "S015", self.clone_record(db)), self.cp0_record(q, "S015")),
        }
        for index, (label, stage) in enumerate(cases.items()):
            with self.subTest(counterexample=label):
                q = self.route(name="clone-%d" % index)
                q.permitted_skips = []
                if label != "no template built before a clone step":
                    q.template = dict(r.template, clones=[])
                lines, receipts, cp0 = stage(q, q.db_name("S015"))
                step = self.step(q, "S015", lines, receipts, cp0)
                self.assertEqual(step["outcome"], "FAIL", "%s must make the step FAIL (the step's database is not bound to the run's template), got %s: %s" % (label, step["outcome"], step.get("reason")))
                self.assertIn("template binding not established", step["reason"], "%s: the step's reason must name the template binding" % label)

        # (b) the shared set-up's control flow, with the database boundary stubbed
        store = os.path.join(self.tmp, "store")
        built_receipt = {"name": TEMPLATE_NAME, "oid": TEMPLATE_OID, "identity": TEMPLATE_IDENTITY, "cp0_digest": TEMPLATE_CP0,
                         "listing": "L", "run_nonce": "n" * 32, "candidate_binding": "a" * 64}
        calls = []

        def exercise(policy, expected=None, present=True, receipt=built_receipt, identity=TEMPLATE_IDENTITY, build_fails=False):
            calls.clear()

            def verify(connection, receipt_, ident, listing):
                calls.append("verify")
                if not present:
                    # the real TT-1 verification, with only the catalogue read stubbed to "absent": its own
                    # database-presence check (template.py verify_registered_template) must refuse
                    with mock.patch.object(template_mod, "validate_template_receipt", return_value=True), \
                            mock.patch.object(template_mod, "_verify_template_build_intent", return_value=None), \
                            mock.patch.object(template_mod, "_database_record", return_value=None):
                        return template_mod.verify_registered_template(connection, receipt_, ident, listing)
            env = {template_mode.POLICY_ENV: policy, "INTEVIA_EVIDENCE_ROOT_PATH": self.tmp, "VERIFICATION_RUN_NONCE": "n" * 32,
                   "INTEVIA_CANDIDATE_BINDING_SHA256": "a" * 64}
            if expected is not None:
                env[template_mode.EXPECTED_ENV] = expected

            def build(connection, alias, store_, lines):
                calls.append("build")
                if build_fails:
                    raise template_mod.TemplateSealingRefused("simulated: the role could not seal the template")
                return built_receipt, True

            def migrate(*a, **k):
                calls.append("migrate")

            with mock.patch.dict(os.environ, env), \
                    mock.patch.object(template_mode, "_build_template", side_effect=build), \
                    mock.patch.object(template_mod, "_migrate_build_database", side_effect=migrate), \
                    mock.patch.object(template_mode, "recover_template_builds", side_effect=lambda *a: calls.append("recover")), \
                    mock.patch.object(template_mode, "source_input_lines", return_value=["head=x"]), \
                    mock.patch.object(template_mode, "runtime_input_lines", return_value=[]), \
                    mock.patch.object(template_mode, "complete_identity", return_value=("L", identity)), \
                    mock.patch.object(template_mode, "collect_template_identity", return_value=("L", identity)), \
                    mock.patch.object(template_mode, "template_database_name", return_value=TEMPLATE_NAME), \
                    mock.patch.object(template_mode, "template_receipt_path", side_effect=lambda s_, i: os.path.join(s_, i + ".json")), \
                    mock.patch.object(template_mode, "read_template_receipt", side_effect=lambda path, ident: receipt), \
                    mock.patch.object(template_mode, "_database_record", side_effect=lambda c, n: {"oid": TEMPLATE_OID} if present else None), \
                    mock.patch.object(template_mode, "verify_registered_template", side_effect=verify):
                try:
                    if policy == "build-once":
                        return template_mode.prepare_template_build_once(mock.MagicMock(), "default", store), None
                    return template_mode.prepare_template_reuse_only(mock.MagicMock(), "default", store, template_mode.parse_expected_template(expected)), None
                except Exception as exc:
                    return None, exc

        shutil.rmtree(store, ignore_errors=True)
        result, error = exercise("build-once", present=False)
        self.assertEqual((result, error, calls), ((built_receipt, True), None, ["build"]),
                         "SELF (build-once) on an empty store must build exactly once; calls %s, error %s" % (calls, error))
        with open(os.path.join(store, TEMPLATE_IDENTITY + ".json"), "x", encoding="utf-8") as handle:
            json.dump(built_receipt, handle)
        result, error = exercise("build-once", present=True)
        self.assertTrue(result is None and "build" not in calls, "build-once with pre-existing store evidence must refuse before building; calls %s, result %s" % (calls, result))
        shutil.rmtree(store, ignore_errors=True)
        result, error = exercise("build-once", present=False, build_fails=True)
        self.assertTrue(isinstance(error, template_mod.TemplateSealingRefused) and calls == ["build", "recover"],
                        "a failed initial build must propagate after one attempt and recovery, with no retry; calls %s, error %r" % (calls, error))
        expected = "%s:%s:%s" % (TEMPLATE_NAME, TEMPLATE_OID, TEMPLATE_IDENTITY)
        result, error = exercise("reuse-only", expected=expected)
        self.assertEqual((result, error, calls), ((built_receipt, False), None, ["verify"]),
                         "a later step (reuse-only) with the recorded template present and verified must reuse it; calls %s, error %s" % (calls, error))
        for label, kwargs in (("missing receipt", {"receipt": None}), ("changed identity", {"identity": "2" * 64}), ("missing database", {"present": False})):
            with self.subTest(refusal=label):
                result, error = exercise("reuse-only", expected=expected, **kwargs)
                self.assertTrue(result is None and error is not None and "build" not in calls and "migrate" not in calls and "recover" not in calls,
                                "reuse-only with %s must refuse with zero build, migration or recovery calls; calls %s, result %s, error %r" % (label, calls, result, error))
                if label == "missing database":
                    self.assertEqual(str(error), "template database is absent or its OID differs from the receipt",
                                     "the missing-database refusal must come from TT-1's real verify_registered_template, not from a test stub; it was %r" % error)

        # (c) a SELF whose build failed stops every later database step
        q = self.route(name="self-failed")
        order = []

        def failing_suite(sid, *a, **k):
            order.append(sid)
            if sid == "SELF":
                raise RuntimeError("R9: clone pre-flight refused (R2 build-once: simulated sealing refusal); fresh-mode fallback is prohibited")
            return {"id": sid, "outcome": "PASS"}

        code, summary = self.run_route(q, failing_suite, lambda *a: 0, lambda name, conn=None: None)
        self.assertEqual(order, ["SELF"], "after a failed SELF build the route must start no later database step; it started %s" % order)
        by_id = {st["id"]: st for st in summary["steps"]}
        self.assertEqual((by_id["SELF"]["outcome"], by_id["S015"]["outcome"], by_id["S015"].get("reason"), code), ("INCOMPLETE", "INCOMPLETE", "not run", 2),
                         "SELF must be INCOMPLETE, S015 'not run' and the route exit 2; got SELF %s, S015 %s (%s), exit %s" % (by_id["SELF"]["outcome"], by_id["S015"]["outcome"], by_id["S015"].get("reason"), code))
        self.assertEqual(summary["template"].get("built_by_step"), None, "no template may be recorded as built after a failed SELF build")

        # (d) the alias boundary: a second alias with its own physical database is refused before any preparation
        from django.test.runner import DiscoverRunner

        from verification.isolation.runner import IsolationRunner

        prepared = []
        fake_default = mock.MagicMock(alias="default", settings_dict={"NAME": "postgres", "TEST": {}})
        fake_secondary = mock.MagicMock(alias="secondary", settings_dict={"NAME": "other", "TEST": {}})
        env = {"VERIFICATION_TEMPLATE_MODE": "clone", "INTEVIA_CANDIDATE_BINDING_SHA256": "a" * 64, "VERIFICATION_TEMPLATE_STORE": store,
               template_mode.POLICY_ENV: "reuse-only", template_mode.EXPECTED_ENV: expected}
        with mock.patch.dict(os.environ, env), mock.patch("django.db.connections", {"default": fake_default, "secondary": fake_secondary}), \
                mock.patch.object(template_mode, "install", side_effect=lambda creation: None), \
                mock.patch.object(template_mode, "prepare_template_reuse_only", side_effect=lambda *a: prepared.append("prepare")), \
                mock.patch.object(DiscoverRunner, "setup_databases", autospec=True, side_effect=lambda *a, **k: prepared.append("django")), \
                mock.patch("verification.isolation.runner._runner_print", side_effect=lambda *a, **k: prepared.append(a[0])):
            runner = IsolationRunner(parallel=0, verbosity=0, nonce="test-nonce", step="SELF")
            with self.assertRaises(RuntimeError, msg="a second alias with its own physical test database must be refused under a CI policy") as refused:
                runner.setup_databases()
        self.assertIn("alias 'secondary' has its own physical test database", str(refused.exception), "the refusal must name the alias; it said %s" % refused.exception)
        self.assertEqual([p_ for p_ in prepared if p_ in ("prepare", "django")], [], "the refusal must come before any template preparation or database creation; observed %s" % prepared)
        self.assertIn("TT1_SETUP_OUTCOME=not started", prepared, "a pre-flight refusal must still print its setup outcome ('not started'); printed %s" % prepared)
        fake_secondary.settings_dict["TEST"] = {"MIRROR": "default"}
        with mock.patch.dict(os.environ, env), mock.patch("django.db.connections", {"default": fake_default, "secondary": fake_secondary}), \
                mock.patch.object(template_mode, "install", side_effect=lambda creation: None), \
                mock.patch.object(template_mode, "prepare_template_reuse_only", return_value=(built_receipt, False)), \
                mock.patch.object(template_mode, "install_clone_creation", side_effect=lambda *a: None), \
                mock.patch.object(DiscoverRunner, "setup_databases", autospec=True, return_value=[(fake_default, "x", True)]), \
                mock.patch("verification.isolation.runner._runner_print", side_effect=lambda *a, **k: prepared.append(a[0])):
            runner = IsolationRunner(parallel=0, verbosity=0, nonce="test-nonce", step="SELF")
            runner.setup_databases()
        self.assertIn("TT1_SETUP_OUTCOME=succeeded", prepared, "a mirror alias needs no create and must not be refused; printed %s" % prepared)

    def test_every_step_records_its_timing_and_the_summary_shows_build_and_clone_seconds(self):
        """Claim: summary.json carries, for every planned step, its start, end and monotonic duration (status 'not run' for a
        step the run never reached), the template build and clone-setup seconds the steps' logs reported (never a zero
        for absent evidence), and the final inspection's own timing and clone setup; SUMMARY.md renders the timing table,
        the final-inspection row and the template section with the two clone categories counted from records."""
        r = self.route()
        template = {"mode": "clone", "built_by_step": "SELF", "name": "tpl", "oid": 1, "identity": "1" * 64, "cp0_digest": TEMPLATE_CP0,
                    "sealed": True, "build_seconds": 27.5, "clones": [{"step": "SELF", "database": "x", "clone_setup_seconds": 1.25, "clone_cp0_matches_template": True, "problems": []}]}
        # the simulated story: SELF builds and takes the first clone; S015 fails to start, so it takes no clone
        import itertools

        ticks = itertools.count(100.0, 0.5)  # a controlled monotonic clock: every start/end pair is 0.5 s apart

        def suite(sid, *a, **k):
            r.template = template
            if sid == "S015":
                raise RuntimeError("simulated: the S015 child could not be started")
            return {"id": sid, "outcome": "PASS", "template": {"mode": "clone", "built": sid == "SELF", "build_seconds": 27.5 if sid == "SELF" else None,
                                                             "clone_setup_seconds": 1.5, "clone_cp0_matches_template": True, "template": "tpl", "template_oid": 1, "problems": []}}

        def child(command, log_path, env):
            with open(log_path, "x", encoding="utf-8") as handle:
                handle.write("final\n")
            with open(command[3], "x", encoding="utf-8") as handle:
                json.dump({"matches": True, "template_dropped": True, "parity_recorded": True, "template_oid": 1, "clone_setup_seconds": 1.1,
                           "final_clone_cp0_digest": TEMPLATE_CP0, "template_cp0_digest": TEMPLATE_CP0}, handle)
            return 0

        with mock.patch.object(route_mod.time, "perf_counter", side_effect=lambda: next(ticks)):
            code, summary = self.run_route(r, suite, child, lambda name, conn=None: None)
        by_id = {step["id"]: step for step in summary["steps"]}
        self.assertEqual(code, 2, "a step that raised is INCOMPLETE, so the route must exit 2; it exited %s" % code)
        for step_id in ("OFFLINE", "SELF", "S015", "OWNERSHIP", "LOCK"):
            timing = by_id[step_id].get("timing")
            self.assertIsNotNone(timing, "step %s recorded no timing at all; every executed step must carry started, finished and seconds" % step_id)
            self.assertEqual(set(timing), {"started", "finished", "seconds"}, "step %s timing has keys %s, not started/finished/seconds" % (step_id, sorted(timing)))
            self.assertTrue(isinstance(timing["seconds"], float) and timing["seconds"] >= 0, "step %s seconds %r must be a non-negative number" % (step_id, timing["seconds"]))
        self.assertEqual(summary["identity"].get("timing", {}).keys() and set(summary["identity"]["timing"]), {"started", "finished", "seconds"},
                         "IDENTITY's timing must be recorded under summary.identity.timing")
        for step_id in ("OFFLINE", "SELF", "S015", "OWNERSHIP", "LOCK"):
            self.assertEqual(by_id[step_id]["timing"]["seconds"], 0.5, "step %s's duration must come from the controlled monotonic clock (0.5 s per start/end pair), not the wall clock; got %r" % (step_id, by_id[step_id]["timing"]["seconds"]))
        self.assertEqual(by_id["S015"]["outcome"], "INCOMPLETE", "a step whose child raised must be INCOMPLETE, with its timing still recorded")
        self.assertEqual((by_id["SELF"]["template"]["build_seconds"], by_id["OWNERSHIP"].get("template")), (27.5, None),
                         "the building step must keep its build seconds and a non-database step must carry no template record")
        self.assertEqual(summary["template"]["built_by_step"], "SELF", "summary.template must name the building step")
        final = summary["template"]["final"]
        self.assertEqual((final["timing"]["status"], final["clone_setup_seconds"], final["matches"]), ("completed", 1.1, True),
                         "the final inspection must record its own timing status, clone setup seconds and parity; got %s" % final)
        self.assertEqual(final["timing"]["seconds"], 0.5, "the final inspection's duration must come from the controlled monotonic clock; got %r" % final["timing"]["seconds"])
        planned_not_run = [s for s in summary["steps"] if s.get("reason") == "not run"]
        self.assertTrue(all(s.get("timing") == {"status": "not run"} for s in planned_not_run), "a planned step that never ran must carry timing status 'not run', never a zero duration")
        summary_md = route_mod.read_text(r.path("SUMMARY.md"))
        self.assertIn("## Step timings", summary_md, "SUMMARY.md must render the step-timing table")
        self.assertIn("| SELF | %s | %s | %s | 27.5 | 1.5 | True |" % (by_id["SELF"]["timing"]["started"], by_id["SELF"]["timing"]["finished"], by_id["SELF"]["timing"]["seconds"]), summary_md,
                      "the SELF row must show its build seconds (27.5), clone setup (1.5) and CP-0 match")
        self.assertIn("| TPL-FINAL (final inspection; cleanup, not a control) | %s | %s | %s | reused | 1.1 | True |" % (final["timing"]["started"], final["timing"]["finished"], final["timing"]["seconds"]), summary_md,
                      "the final inspection must be rendered as its own timing row with its clone setup seconds, outside the fixed controls")
        self.assertIn("- **Test-runner clones (SELF, S015, mutation steps):** 1;", summary_md, "the test-runner clone count must be derived from the template's clone records (one: SELF's; S015 never started)")
        self.assertIn("- **Final-inspection clone:** `%s` (1 more clone; setup 1.1 s)" % final["database"], summary_md, "the final-inspection clone must be counted separately")
        self.assertIn("Built by step:** SELF in 27.5 s", summary_md, "the template section must show the building step and its build seconds")

    def test_the_template_is_one_more_owned_database_inspected_then_dropped(self):
        """Claim: at the end of the run the route's final inspection (run_final_inspection -> finalise_route_template)
        clones the template once, captures CP-0 on that final clone with the connection name restored on every exit path,
        records parity only when the compared properties match the fresh-build reference and only then invokes TT-1's
        owned cleanup; a mismatch never reaches cleanup and the record the child writes still carries matches False,
        both digests and the clone's setup time; the route records a matching, dropped template as CLEAN (exit 0,
        timing status "completed") and a failed or incomplete final check as UNRESOLVED and NOT CLEAN (exit 3, timing
        status "failed")."""
        from verification.isolation import template as template_mod
        from verification.isolation import template_mode

        # the child's logic, with the database and TT-1 boundaries stubbed
        def exercise(clone_digest, capture_raises=False):
            events = []
            connection = mock.MagicMock()
            connection.settings_dict = {"NAME": "postgres"}
            connection.close.side_effect = lambda: events.append(("close", connection.settings_dict["NAME"]))
            settings = SimpleNamespace(DATABASES={"default": {"NAME": "postgres"}})
            receipt = {"name": TEMPLATE_NAME, "oid": TEMPLATE_OID, "cp0_digest": TEMPLATE_CP0}

            def capture():
                events.append(("capture", connection.settings_dict["NAME"], settings.DATABASES["default"]["NAME"]))
                if capture_raises:
                    raise RuntimeError("simulated: the capture query failed")
                return clone_digest

            out_path = os.path.join(self.tmp, "final-%s-%s.json" % (clone_digest[:4], capture_raises))
            with mock.patch.object(template_mode, "read_template_receipt", return_value=receipt), \
                    mock.patch.object(template_mode, "template_receipt_path", side_effect=lambda s_, i: os.path.join(s_, i + ".json")), \
                    mock.patch.object(template_mod, "create_clone_database", side_effect=lambda c, rcpt, name: events.append(("clone", name)) or name), \
                    mock.patch.object(template_mod, "record_final_clone_cp0", side_effect=lambda path, ident, digest: events.append(("record", digest))), \
                    mock.patch.object(template_mod, "cleanup_template", side_effect=lambda c, path, ident: events.append(("cleanup", ident))):
                status = template_mode.run_final_inspection(connection, settings, self.tmp, TEMPLATE_IDENTITY, "final_db", out_path, capture=capture)
            written = json.loads(route_mod.read_text(out_path))
            result = written if status == 0 else None
            error = written.get("error") if status != 0 else None
            return result, error, events, connection.settings_dict["NAME"], settings.DATABASES["default"]["NAME"], written, status

        result, error, events, name_after, setting_after, written, status = exercise(TEMPLATE_CP0)
        self.assertEqual((status, error), (0, None), "a final clone whose CP-0 matches must complete with exit 0 and no error; got exit %s, error %r" % (status, error))
        self.assertEqual([e[0] for e in events], ["clone", "close", "capture", "close", "record", "cleanup"],
                         "the final inspection must clone, capture on the clone, record parity and only then clean up; observed order %s" % [e[0] for e in events])
        self.assertEqual(events[2], ("capture", "final_db", "final_db"), "the CP-0 capture must target the final clone on both the connection and the settings; it targeted %s" % (events[2],))
        self.assertEqual((name_after, setting_after), ("postgres", "postgres"), "the connection and settings names must be restored after the capture")
        self.assertEqual((result["matches"], result["parity_recorded"], result["template_dropped"]), (True, True, True), "the equal case must record parity before owned cleanup and report the drop; got %s" % result)
        result, error, events, name_after, setting_after, written, status = exercise("f" * 64)
        self.assertEqual(status, 1, "a differing final CP-0 must make the child exit 1; it exited %s" % status)
        self.assertTrue(error and error.startswith("TemplateReceiptInvalid:"), "a differing final CP-0 must refuse with TemplateReceiptInvalid; the record says %r" % error)
        self.assertEqual((written.get("matches"), written.get("final_clone_cp0_digest"), written.get("template_cp0_digest")), (False, "f" * 64, TEMPLATE_CP0),
                         "on a mismatch the record the child writes must keep matches False and both digests (Argon G1); it holds %s" % written)
        self.assertIsInstance(written.get("clone_setup_seconds"), float, "on a mismatch the record must keep the final clone's setup time; it holds %r" % written.get("clone_setup_seconds"))
        self.assertNotIn("cleanup", [e[0] for e in events], "on a mismatch the owned cleanup must never be invoked (the template stays untouched); events %s" % events)
        self.assertNotIn("record", [e[0] for e in events], "on a mismatch parity must not be recorded; events %s" % events)
        self.assertIn("template integrity check failed", str(error), "the refusal must say the integrity check failed and that the template was left untouched; it said %s" % error)
        result, error, events, name_after, setting_after, written, status = exercise(TEMPLATE_CP0, capture_raises=True)
        self.assertEqual((status, written.get("matches")), (1, None), "a failed capture must exit 1 with no parity claim; got exit %s, matches %r" % (status, written.get("matches")))
        self.assertEqual((name_after, setting_after), ("postgres", "postgres"), "the names must be restored even when the capture fails; left %s / %s" % (name_after, setting_after))
        self.assertNotIn("cleanup", [e[0] for e in events], "a failed capture must not reach cleanup; events %s" % events)

        # the route's classification of the child's result
        def run_with(final_result, child_exit=0, present_after=False):
            r = self.route(name="final-%s" % hashlib.sha256(json.dumps(final_result, sort_keys=True).encode()).hexdigest()[:8])
            template = {"mode": "clone", "built_by_step": "SELF", "name": TEMPLATE_NAME, "oid": TEMPLATE_OID, "identity": TEMPLATE_IDENTITY,
                        "cp0_digest": TEMPLATE_CP0, "sealed": True, "build_seconds": 27.5, "clones": []}
            commands = []

            def suite(sid, *a, **k):
                r.template = template
                return {"id": sid, "outcome": "PASS"}

            def child(command, log_path, env):
                commands.append((command, env))
                with open(log_path, "x", encoding="utf-8") as handle:
                    handle.write("final\n")
                if final_result is not None:
                    with open(command[3], "x", encoding="utf-8") as handle:
                        json.dump(dict(final_result, template_oid=TEMPLATE_OID), handle)
                return child_exit

            code, summary = self.run_route(r, suite, child, lambda name, conn=None: TEMPLATE_OID if (name == TEMPLATE_NAME and present_after) else None)
            return r, code, summary, commands

        r, code, summary, commands = run_with({"matches": True, "template_dropped": True, "parity_recorded": True, "final_clone_cp0_digest": TEMPLATE_CP0, "template_cp0_digest": TEMPLATE_CP0})
        self.assertEqual((code, summary["cleanup"]["outcome"]), (0, "CLEAN"), "a matching, dropped template must leave cleanup CLEAN and exit 0; got %s %s" % (code, summary["cleanup"]))
        self.assertEqual(summary["template"]["final"]["timing"]["status"], "completed", "a final check that exited 0 and was classified dropped must read 'completed'; it reads %r" % summary["template"]["final"]["timing"]["status"])
        record = next(n for n in summary["cleanup"]["names"] if n["step"] == "template")
        self.assertTrue(record["resolved"] and record["state"].startswith("DROPPED BY THE ROUTE (template;"), "the template's cleanup record must be resolved and dropped; got %s" % record)
        self.assertEqual(record["owned_oid"], TEMPLATE_OID, "the cleanup record must carry the template's owned oid")
        command, env = commands[-1]
        self.assertEqual((command[1], command[4], command[5]), ("-c", r.db_name("TPL-FINAL"), TEMPLATE_IDENTITY), "the final check must clone under the route's own TPL-FINAL name with the template identity; command %s" % command[1:6])
        self.assertEqual((env["VERIFICATION_TEMPLATE_MODE"], env[template_mode.POLICY_ENV]), ("clone", "reuse-only"), "the final child must run in clone mode under the reuse-only policy")
        self.assertIn(("TPL-FINAL", r.db_name("TPL-FINAL"), r.receipts_path("TPL-FINAL")), r.attempted, "the final clone must be an attempted database the route cleans")
        r, code, summary, _ = run_with({"matches": False, "template_dropped": False, "final_clone_cp0_digest": "f" * 64, "template_cp0_digest": TEMPLATE_CP0, "error": "TemplateReceiptInvalid: the template integrity check failed"}, child_exit=1, present_after=True)
        self.assertEqual((code, summary["cleanup"]["outcome"]), (3, "NOT CLEAN"), "a failed integrity check must leave cleanup NOT CLEAN and exit 3; got %s %s" % (code, summary["cleanup"]["outcome"]))
        record = next(n for n in summary["cleanup"]["names"] if n["step"] == "template")
        self.assertTrue(record["state"].startswith("UNRESOLVED - the template integrity check failed") and not record["resolved"] and "left the template untouched" in record["state"],
                        "the template record must be unresolved, name the failed integrity check and say the template was left untouched; got %s" % record)
        self.assertIn("f" * 64, record["state"], "the operator's message must carry the final clone's digest; it reads %s" % record["state"])
        self.assertEqual(summary["template"]["final"]["timing"]["status"], "failed", "a final check whose child exited 1 must read 'failed', never 'completed'; it reads %r" % summary["template"]["final"]["timing"]["status"])
        r, code, summary, _ = run_with(None, child_exit=1, present_after=True)
        self.assertEqual(summary["template"]["final"]["timing"]["status"], "failed", "a final check with no result record must read 'failed'; it reads %r" % summary["template"]["final"]["timing"]["status"])
        self.assertEqual((code, summary["cleanup"]["outcome"]), (3, "NOT CLEAN"), "a final check with no result record must leave cleanup NOT CLEAN and exit 3; got %s" % code)
        record = next(n for n in summary["cleanup"]["names"] if n["step"] == "template")
        self.assertTrue(record["state"].startswith("UNRESOLVED - the final template check did not complete") and not record["resolved"], "an incomplete final check must be unresolved; got %s" % record)


class CICloneMutationRunner(unittest.TestCase):
    def test_a_mutation_is_applied_to_the_clone_only_after_its_cp0_equals_the_templates(self):
        """Claim: through the shared set-up, MutationRunner's mutation runs on the database the set-up created as a clone
        of the template (never the template itself), and only after the clone's CP-0 has been recomputed and recorded as
        matching the fresh-build reference; a differing CP-0 refuses with no mutation statement; a clone-creation failure
        produces no mutation SQL and no fresh creation; a Human-authorised fresh run (not a CI mode) applies the mutation
        without a clone check. Runner output is captured so the OFFLINE step's accounting is undisturbed (VC V1)."""
        from django.test.runner import DiscoverRunner

        from verification import mutations as mutations_mod
        from verification.isolation import runner as runner_mod
        from verification.isolation import state as state_mod
        from verification.isolation import template as template_mod
        from verification.isolation import template_mode

        name = sorted(mutations_mod.MUTATIONS)[0]
        template_digest = "a" * 64
        receipt = {"name": TEMPLATE_NAME, "oid": TEMPLATE_OID, "cp0_digest": template_digest, "identity": TEMPLATE_IDENTITY,
                   "build_intent": {"path": "/x"}}

        def exercise(mode, clone_digest, clone_fails=False):
            executed, receipts, printed, created = [], [], [], []
            fake_connection = mock.MagicMock()
            fake_connection.alias = "default"
            fake_connection.settings_dict = {"NAME": "postgres", "TEST": {}}
            cursor = mock.MagicMock()
            cursor.__enter__.return_value.execute.side_effect = lambda statement: executed.append((fake_connection.settings_dict["NAME"], statement))
            fake_connection.cursor.return_value = cursor
            connections = {"default": fake_connection}

            def fresh_install(creation):
                creation._create_test_db = lambda *a, **k: created.append(("fresh", "test_db")) or "test_db"

            def create_clone(connection, rcpt, clone_name):
                if clone_fails:
                    raise template_mod.TemplateNameCollision("simulated: clone database %s already exists" % clone_name)
                created.append(("clone", clone_name))
                return clone_name

            def django_setup(runner_self, **kwargs):
                clone_name = "test_intevia_living_organism_probe_mut1"
                fake_connection.settings_dict["NAME"] = fake_connection.creation._create_test_db(0, False)
                return [(fake_connection, clone_name, True)]

            env = {mutations_mod.MUTATION_ENV: name, "VERIFICATION_TEMPLATE_MODE": mode, "INTEVIA_CANDIDATE_BINDING_SHA256": "a" * 64,
                   "VERIFICATION_TEMPLATE_STORE": "/store", template_mode.POLICY_ENV: "reuse-only",
                   template_mode.EXPECTED_ENV: "%s:%s:%s" % (TEMPLATE_NAME, TEMPLATE_OID, TEMPLATE_IDENTITY),
                   "INTEVIA_FRESH_AUTHORITY_FILE": "", "INTEVIA_FRESH_AUTHORITY_SHA256": ""}
            fake_connection.creation._get_test_db_name = lambda: "test_intevia_living_organism_probe_mut1"
            with mock.patch.dict(os.environ, env), \
                    mock.patch("django.db.connections", connections), \
                    mock.patch.object(template_mode, "install", side_effect=fresh_install), \
                    mock.patch.object(template_mode, "prepare_template_reuse_only", return_value=(receipt, False)), \
                    mock.patch.object(template_mode, "_database_record", return_value=None), \
                    mock.patch.object(template_mod, "create_clone_database", side_effect=create_clone), \
                    mock.patch("verification.isolation.run_plan.validate_fresh_authority", return_value="AUTH"), \
                    mock.patch.object(DiscoverRunner, "setup_databases", autospec=True, side_effect=django_setup), \
                    mock.patch.object(mutations_mod, "connection", fake_connection), \
                    mock.patch.object(mutations_mod, "write_receipt", side_effect=lambda event, **fields: receipts.append((event, dict(fields), list(executed)))), \
                    mock.patch.object(state_mod, "capture", return_value={"probe": True}), \
                    mock.patch.object(state_mod, "digest", return_value=clone_digest), \
                    mock.patch.object(runner_mod, "_runner_print", side_effect=lambda *a, **k: printed.append(a[0])), \
                    contextlib.redirect_stdout(io.StringIO()) as captured:
                runner = mutations_mod.MutationRunner(parallel=0, verbosity=0, nonce="test-nonce", step="MUT-" + name)
                try:
                    runner.setup_databases()
                    error = None
                except Exception as exc:
                    error = exc
            return executed, receipts, printed, created, captured.getvalue(), error

        executed, receipts, printed, created, output, error = exercise("clone", template_digest)
        self.assertIsNone(error, "with a matching clone the set-up must complete; it raised %r" % error)
        self.assertEqual(created, [("clone", "test_intevia_living_organism_probe_mut1")], "the set-up must create exactly one clone and no fresh database; it created %s" % created)
        self.assertEqual([target for target, _ in executed], ["test_intevia_living_organism_probe_mut1"] * len(executed), "every mutation statement must target the created clone, never the template %s; targets %s" % (TEMPLATE_NAME, [t for t, _ in executed]))
        self.assertEqual([statement for _, statement in executed], mutations_mod.MUTATIONS[name]["sql"], "the mutation must run exactly as registered")
        self.assertEqual([(event, fields["name"], fields["matches"], before) for event, fields, before in receipts], [("clone_cp0_verified", "test_intevia_living_organism_probe_mut1", True, [])],
                         "the CP-0 check must be recorded for the clone before any mutation statement; receipts %s" % receipts)
        self.assertIn("TT1_CLONE_CP0_MATCHES_TEMPLATE=True", printed, "the runner must print the match marker; printed %s" % printed)
        self.assertIn("VERIFICATION MUTATION APPLIED: %s" % name, output, "the applied marker must be printed (and captured here, so OFFLINE's accounting is undisturbed)")
        executed, receipts, printed, created, output, error = exercise("clone", "b" * 64)
        self.assertEqual(executed, [], "a clone whose CP-0 differs from the fresh-build reference must receive no mutation statement; executed %s" % executed)
        self.assertIsNotNone(error, "a differing CP-0 must refuse the step")
        self.assertIn("could not start because the clone's recorded starting state differs from the fresh-build reference", str(error), "the refusal must explain what happened in plain English; it said %s" % error)
        self.assertIn("A fresh database will not be substituted", str(error), "the refusal must say no fresh fallback happens")
        self.assertEqual([(event, fields["matches"]) for event, fields, _ in receipts], [("clone_cp0_verified", False)], "the mismatch must be recorded; receipts %s" % receipts)
        self.assertNotIn("VERIFICATION MUTATION APPLIED", output, "no applied marker may be printed on a mismatch")
        executed, receipts, printed, created, output, error = exercise("clone", template_digest, clone_fails=True)
        self.assertEqual((executed, created, receipts), ([], [], []), "a clone-creation failure must produce no mutation SQL, no fresh creation and no CP-0 receipt; got %s %s %s" % (executed, created, receipts))
        self.assertIsNotNone(error, "a clone-creation failure must raise")
        executed, receipts, printed, created, output, error = exercise("fresh", "irrelevant")
        self.assertIsNone(error, "an authorised fresh run must still complete; it raised %r" % error)
        self.assertEqual(([statement for _, statement in executed], receipts, created), (mutations_mod.MUTATIONS[name]["sql"], [], [("fresh", "test_db")]),
                         "an authorised fresh run (not a CI mode) applies the mutation to its fresh database without a clone check; got %s %s %s" % (executed, receipts, created))
