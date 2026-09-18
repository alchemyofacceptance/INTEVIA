"""Self-test of verification.recording and verification.recorded_unittest.

This module keeps the recorder controls permanent and route-bound. The tests use the probes already exercised in the
branch, but assert the real contract directly so new record kinds or callback regressions are refused.

Run: python -m unittest verification.selftest_recording -v
"""
from __future__ import annotations

import inspect
import io
import json
import os
import sys
import unittest
from unittest import mock

from verification import recording as recording_mod
from verification.recorded_unittest import RecordedTestCase, RecordedTextTestRunner
from verification.recording import RecordingRunnerMixin


ALLOWED_KINDS = frozenset({
    "RUNNER-START",
    "SUITE-BUILT",
    "DB-SETUP-START",
    "DB-SETUP-DONE",
    "DB-SETUP-REFUSED",
    "SUITE-RUN-START",
    "RUN-END",
    "TEST-ENTERED",
    "START",
    "STOP",
    "OK",
    "FAIL",
    "ERROR",
    "SKIP",
    "XFAIL",
    "XPASS",
    "SUB-OK",
    "SUB-FAIL",
    "SUB-ERROR",
    "SUB-SKIP",
    "HOLDER-ERROR",
    "HOLDER-SKIP",
    "DURATION",
})

MODULE = "verification.selftest_recording"


def _recording_env(step="PROBE"):
    return mock.patch.dict(os.environ, {"VERIFICATION_RUN_NONCE": "probe-selftest-recording", "VERIFICATION_STEP": step}, clear=False)


def _django_env():
    return mock.patch.dict(
        os.environ,
        {
            "DJANGO_SETTINGS_MODULE": "intevia.settings",
            "DJANGO_SECRET_KEY": "probe-secret",
            "INTEVIA_DATABASE_ENGINE": "postgresql",
            "INTEVIA_POSTGRES_DB": "probe_db",
            "INTEVIA_POSTGRES_USER": "probe_user",
            "INTEVIA_POSTGRES_PASSWORD": "probe_password",
            "INTEVIA_POSTGRES_HOST": "127.0.0.1",
            "INTEVIA_POSTGRES_PORT": "5432",
        },
        clear=False,
    )


def _allowed(records):
    kinds = [record["kind"] for record in records]
    offenders = [kind for kind in kinds if kind not in ALLOWED_KINDS]
    return not offenders, offenders


def _json(record):
    return json.dumps(record, sort_keys=True, default=str)


class VocabularyControl(unittest.TestCase):
    def _recorded_run(self, case):
        with _recording_env():
            runner = RecordedTextTestRunner(stream=io.StringIO(), verbosity=0, record_stream=io.StringIO(), step="PROBE")
            return runner.run(unittest.defaultTestLoader.loadTestsFromTestCase(case))

    def test_vocabulary_rejects_invented_kinds(self):
        class CleanCase(unittest.TestCase):
            def test_one(self):
                pass

        result = self._recorded_run(CleanCase)
        allowed, offenders = _allowed(result.records)
        self.assertTrue(allowed, offenders)
        self.assertNotIn("DB-TEARDOWN-START", ALLOWED_KINDS)

        forged = [dict(result.records[0], kind="SUBTEST")]
        allowed, offenders = _allowed(forged)
        self.assertFalse(allowed)
        self.assertEqual(offenders, ["SUBTEST"])

        forged = [dict(result.records[0], kind="DB-TEARDOWN-DONE")]
        allowed, offenders = _allowed(forged)
        self.assertFalse(allowed)
        self.assertEqual(offenders, ["DB-TEARDOWN-DONE"])


class TestEnteredControl(unittest.TestCase):
    def test_plain_runner_emits_test_entered_with_plain_result_class(self):
        class PlainCase(unittest.TestCase):
            def test_one(self):
                pass

        class SinkRunner(RecordingRunnerMixin, unittest.TextTestRunner):
            pass

        with _recording_env():
            sink_runner = SinkRunner(stream=io.StringIO(), verbosity=0, step="PROBE")
            plain_runner = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0)
            previous_sink = recording_mod._activate_record_sink(sink_runner)
            try:
                result = plain_runner.run(unittest.defaultTestLoader.loadTestsFromTestCase(PlainCase))
            finally:
                recording_mod._ACTIVE_RECORD_SINK = previous_sink

        allowed, offenders = _allowed(sink_runner.records)
        self.assertTrue(allowed, offenders)
        entered = [record for record in sink_runner.records if record["kind"] == "TEST-ENTERED"]
        self.assertEqual(len(entered), 1, sink_runner.records)
        self.assertEqual(entered[0]["result_class"], "TextTestResult")
        self.assertIsNone(recording_mod._ACTIVE_RECORD_SINK)
        self.assertEqual(type(result).__name__, "TextTestResult")

    def test_recorded_testcase_emits_one_test_entered_per_test(self):
        class SampleCase(RecordedTestCase):
            def test_one(self):
                pass

            def test_two(self):
                self.skipTest("skip body")

        with _recording_env():
            runner = RecordedTextTestRunner(stream=io.StringIO(), verbosity=0, record_stream=io.StringIO(), step="PROBE")
            result = runner.run(unittest.defaultTestLoader.loadTestsFromTestCase(SampleCase))

        allowed, offenders = _allowed(result.records)
        self.assertTrue(allowed, offenders)
        entered = [record for record in result.records if record["kind"] == "TEST-ENTERED"]
        self.assertEqual(len(entered), 2, result.records)
        self.assertTrue(all(record["result_class"] == "Result" for record in entered))
        self.assertIsNone(recording_mod._ACTIVE_RECORD_SINK)


class SinkLifetimeControl(unittest.TestCase):
    def test_sink_is_cleared_after_run_returns(self):
        class CleanCase(unittest.TestCase):
            def test_one(self):
                pass

        with _recording_env():
            runner = RecordedTextTestRunner(stream=io.StringIO(), verbosity=0, record_stream=io.StringIO(), step="PROBE")
            result = runner.run(unittest.defaultTestLoader.loadTestsFromTestCase(CleanCase))

        allowed, offenders = _allowed(result.records)
        self.assertTrue(allowed, offenders)
        self.assertIsNone(recording_mod._ACTIVE_RECORD_SINK)
        self.assertTrue(result.wasSuccessful())


class HolderClassificationControl(unittest.TestCase):
    def _run_case(self, case):
        with _recording_env():
            runner = RecordedTextTestRunner(stream=io.StringIO(), verbosity=0, record_stream=io.StringIO(), step="PROBE")
            return runner.run(unittest.defaultTestLoader.loadTestsFromTestCase(case))

    def test_holder_error_and_skip_are_recognised_by_instance(self):
        class SetupErrorCase(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                raise RuntimeError("setup class boom")

            def test_one(self):
                pass

        class SetupSkipCase(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                raise unittest.SkipTest("setup class skip")

            def test_one(self):
                pass

        error_result = self._run_case(SetupErrorCase)
        skip_result = self._run_case(SetupSkipCase)

        for result, expected_kind in ((error_result, "HOLDER-ERROR"), (skip_result, "HOLDER-SKIP")):
            allowed, offenders = _allowed(result.records)
            self.assertTrue(allowed, offenders)
            holder_records = [record for record in result.records if record["kind"] == expected_kind]
            self.assertEqual(len(holder_records), 1, result.records)
            self.assertIn(expected_kind, [record["kind"] for record in result.records])
            holder = result.errors[0][0] if expected_kind == "HOLDER-ERROR" else result.skipped[0][0]
            self.assertIsInstance(holder, unittest.suite._ErrorHolder)
            self.assertTrue(holder_records[0]["target"].startswith(("setUpClass", "tearDownClass")))


class SubtestControl(unittest.TestCase):
    def test_subtest_kinds_and_metadata_are_distinct(self):
        class SubtestCase(unittest.TestCase):
            def test_subtests(self):
                for index in range(4):
                    with self.subTest(msg="a message", label=index):
                        if index == 0:
                            pass
                        elif index == 1:
                            self.fail("sub fail")
                        elif index == 2:
                            raise RuntimeError("sub error")
                        else:
                            self.skipTest("sub skip")

        with _recording_env():
            runner = RecordedTextTestRunner(stream=io.StringIO(), verbosity=0, record_stream=io.StringIO(), step="PROBE")
            result = runner.run(unittest.defaultTestLoader.loadTestsFromTestCase(SubtestCase))

        allowed, offenders = _allowed(result.records)
        self.assertTrue(allowed, offenders)
        sub_records = [record for record in result.records if record["kind"].startswith("SUB-")]
        self.assertEqual([record["kind"] for record in sub_records], ["SUB-OK", "SUB-FAIL", "SUB-ERROR", "SUB-SKIP"])
        for record in sub_records:
            self.assertTrue(record["is_subtest"])
            self.assertEqual(record["message"], "a message")
            self.assertEqual(record["params"], {"label": sub_records.index(record)})
            self.assertEqual(record["phase"], "subtest")
        self.assertIsNone(recording_mod._ACTIVE_RECORD_SINK)

    def test_duplicate_guard_defect_is_detected_by_counting(self):
        class OneCase(RecordedTestCase):
            def test_one(self):
                pass

        original_run = recording_mod.RecordedTestCaseMixin.run

        def defective_run(self, result=None):
            if result is not None and not (hasattr(result, "record_sink") or hasattr(result, "_append_record") or hasattr(result, "records")):
                raise TypeError("RecordingResult required")
            if result is not None:
                recording_mod._record_test_entered(self, result)
            return original_run(self, result)

        with _recording_env(), mock.patch.object(recording_mod.RecordedTestCaseMixin, "run", defective_run):
            runner = RecordedTextTestRunner(stream=io.StringIO(), verbosity=0, record_stream=io.StringIO(), step="PROBE")
            result = runner.run(unittest.defaultTestLoader.loadTestsFromTestCase(OneCase))

        entered = [record for record in result.records if record["kind"] == "TEST-ENTERED"]
        self.assertEqual(len(entered), 2, result.records)
        self.assertTrue(all(record["result_class"] == "Result" for record in entered))


class EnvelopeAndDbControl(unittest.TestCase):
    def test_envelope_sequence_and_positive_run_are_well_formed(self):
        class CleanCase(unittest.TestCase):
            def test_alpha(self):
                pass

            def test_beta(self):
                pass

        with _recording_env():
            runner = RecordedTextTestRunner(stream=io.StringIO(), verbosity=0, record_stream=io.StringIO(), step="PROBE")
            result = runner.run(unittest.defaultTestLoader.loadTestsFromTestCase(CleanCase))

        allowed, offenders = _allowed(result.records)
        self.assertTrue(allowed, offenders)
        self.assertTrue(result.wasSuccessful())
        kinds = [record["kind"] for record in result.records]
        self.assertEqual(kinds[0:3], ["RUNNER-START", "SUITE-BUILT", "SUITE-RUN-START"])
        self.assertEqual(kinds[-1], "RUN-END")
        self.assertEqual(sum(1 for kind in kinds if kind == "RUN-END"), 1)
        self.assertEqual([record["seq"] for record in result.records], list(range(1, len(result.records) + 1)))
        expected_suite_ids = [test.id() for test in unittest.defaultTestLoader.loadTestsFromTestCase(CleanCase)]
        self.assertEqual(result.records[1]["suite_ids"], expected_suite_ids)
        run_end = result.records[-1]
        for key in ("testsRun", "failures", "errors", "skipped", "expectedFailures", "unexpectedSuccesses", "wasSuccessful"):
            self.assertIn(key, run_end)
        self.assertTrue(run_end["wasSuccessful"])
        self.assertIsNone(recording_mod._ACTIVE_RECORD_SINK)

    def test_db_setup_start_done_and_refused_are_recorded(self):
        with _recording_env(), _django_env():
            import django

            if not django.apps.apps.ready:
                django.setup()

            from django.test.runner import DiscoverRunner
            import verification.ownership as ownership

            log = []

            def fake_install(creation):
                log.append(("install", type(creation).__name__))

            class DatabaseRunner(RecordingRunnerMixin, ownership.OwnedDatabaseRunnerMixin, DiscoverRunner):
                pass

            with mock.patch.object(ownership, "install", side_effect=fake_install), \
                    mock.patch.object(DiscoverRunner, "setup_databases", autospec=True, side_effect=lambda self, **kw: (log.append(("django_setup", kw)), {"token": "old-config"})[1]):
                runner = DatabaseRunner(verbosity=0, step="PROBE")
                setup_result = runner.setup_databases()

            self.assertEqual(setup_result, {"token": "old-config"})
            allowed, offenders = _allowed(runner.records)
            self.assertTrue(allowed, offenders)
            self.assertEqual([record["kind"] for record in runner.records], ["RUNNER-START", "DB-SETUP-START", "DB-SETUP-DONE"])
            self.assertEqual(log, [("install", "DatabaseCreation"), ("django_setup", {})])

            refusal_log = []

            def refusing_install(creation):
                refusal_log.append(("install", type(creation).__name__))
                raise RuntimeError("install refused")

            with mock.patch.object(ownership, "install", side_effect=refusing_install), \
                    mock.patch.object(DiscoverRunner, "setup_databases", autospec=True, side_effect=lambda self, **kw: (refusal_log.append(("django_setup", kw)), {"token": "old-config"})[1]):
                runner_refusal = DatabaseRunner(verbosity=0, step="PROBE")
                with self.assertRaises(RuntimeError):
                    runner_refusal.setup_databases()

            allowed, offenders = _allowed(runner_refusal.records)
            self.assertTrue(allowed, offenders)
            self.assertEqual([record["kind"] for record in runner_refusal.records], ["RUNNER-START", "DB-SETUP-START", "DB-SETUP-REFUSED"])
            self.assertEqual(runner_refusal.records[-1]["exc"], "RuntimeError")
            self.assertEqual(refusal_log, [("install", "DatabaseCreation")])


class ControlIntegration(unittest.TestCase):
    def test_import_boundary_and_parser_delegation_remain_intact(self):
        import subprocess
        import verification.parse_results as parse_results
        proc = subprocess.run(
            [sys.executable, "-c", "import sys, verification.run; print(sorted(m for m in sys.modules if m.startswith('verification') or m in ('django','psycopg','psycopg2')))"] ,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            capture_output=True,
            text=True,
            check=True,
            env=dict(os.environ, PYTHONPATH=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        )
        self.assertEqual(proc.stdout.strip(), "['verification', 'verification.run']")
        source = inspect.getsource(parse_results)
        self.assertTrue("outcome.derive(" in source)
        self.assertFalse("NOT REACHED (setup error)" in source)
