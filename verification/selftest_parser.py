"""Self-test of verification.parse_results (C-P1, and C-A1-B1 in Change C v0.2). Standard library only: no Django, no database.

The logs are produced by unittest's own verbose runner - the runner Django uses for 'manage.py test -v 2' - over a
synthetic test case whose tests pass, fail, error, use sub-tests and are skipped, with and without docstrings. Each
check states the property it demonstrates.

Run: python -m unittest verification.selftest_parser -v
"""
import io
import unittest

from verification.parse_results import join_description_lines, parse

MODULE = "verification.selftest_parser"


def _cases(with_docstrings):
    class Synthetic(unittest.TestCase):
        def test_a_pass(self):
            pass

        def test_b_fail(self):
            self.assertEqual(1, 2)

        def test_c_error(self):
            raise RuntimeError("boom")

        def test_d_subtests(self):
            for i in range(3):
                with self.subTest(i=i):
                    self.assertNotEqual(i, 1)

        def test_e_subtests_all_pass(self):
            for i in range(2):
                with self.subTest(i=i):
                    self.assertTrue(True)

        @unittest.skip("not applicable here")
        def test_f_skip(self):
            pass

    if with_docstrings:
        docs = {"test_a_pass": "Passing test.", "test_b_fail": "Failing test ... with dots inside its description.",
                "test_c_error": "Erroring test.", "test_d_subtests": "Sub-tests, one failing.",
                "test_e_subtests_all_pass": "Sub-tests, all passing.", "test_f_skip": "Skipped test."}
        for name, doc in docs.items():
            getattr(Synthetic, name).__doc__ = doc
    Synthetic.__module__ = MODULE
    Synthetic.__qualname__ = "Synthetic"
    return Synthetic


def run_log(with_docstrings, interleave=True):
    """A log in the shape the route writes: runner output, optional instrument lines, then EXIT_STATUS."""
    case = _cases(with_docstrings)
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(case)
    ids = sorted(t.id() for t in suite)  # before running: a suite releases its tests as it runs
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    text = stream.getvalue()
    if interleave:
        text = "\n".join(("ISOLATION CP-A ESTABLISHED %s\n" % line.split("(")[1].split(")")[0] + line)
                         if line.startswith("test_") and "(" in line else line for line in text.split("\n"))
    return text + "\nEXIT_STATUS: %d\n" % (0 if result.wasSuccessful() else 1), ids


EXPECTED_BODY = {"test_a_pass": "ok", "test_b_fail": "FAIL", "test_c_error": "ERROR", "test_d_subtests": "SUBTEST FAILURES",
                 "test_e_subtests_all_pass": "ok", "test_f_skip": "skipped"}


def outcomes(out):
    return {t["id"].rsplit(".", 1)[1]: t["body"] for t in out["tests"]}


class DescriptionLineParsing(unittest.TestCase):
    def test_p1_docstring_log_records_each_outcome_once_and_reconciles(self):
        text, ids = run_log(with_docstrings=True)
        self.assertIn("\nFailing test ... with dots inside its description. ... FAIL", text)  # the shape under test
        out = parse(text, ids)
        self.assertTrue(out["reconciled"], out["reconciliation"])
        self.assertEqual(outcomes(out), EXPECTED_BODY)
        self.assertEqual(len(out["tests"]), 6)
        self.assertEqual([t["id"] for t in out["tests"]], sorted(set(t["id"] for t in out["tests"]), key=[t["id"] for t in out["tests"]].index))
        self.assertEqual((out["summary"]["failures"], out["summary"]["errors"], out["summary"]["skipped"]), (2, 1, 1))
        sub = [t for t in out["tests"] if t["id"].endswith("test_d_subtests")][0]
        self.assertEqual((sub["subtest_failures"], sub["subtest_errors"]), (1, 0))

    def test_p2_same_outcomes_without_docstrings(self):
        text, ids = run_log(with_docstrings=False)
        out = parse(text, ids)
        self.assertTrue(out["reconciled"], out["reconciliation"])
        self.assertEqual(outcomes(out), EXPECTED_BODY)

    def test_p3_a_missing_result_stays_incomplete(self):
        text, ids = run_log(with_docstrings=True)
        self.assertIn("Passing test. ... ok\n", text)
        out = parse(text.replace("Passing test. ... ok\n", "", 1), ids)
        self.assertFalse(out["reconciled"])
        self.assertNotEqual(outcomes(out).get("test_a_pass"), "ok")

    def test_p4_a_failure_is_not_hidden_by_an_ok_status_line(self):
        text, ids = run_log(with_docstrings=True)
        tampered = text.replace("Failing test ... with dots inside its description. ... FAIL", "Failing test ... with dots inside its description. ... ok", 1)
        out = parse(tampered, ids)
        self.assertEqual(outcomes(out)["test_b_fail"], "FAIL")
        self.assertEqual(out["summary"]["failures"], 2)

    def test_p5_no_verdict_is_incomplete(self):
        text, ids = run_log(with_docstrings=True)
        out = parse(text.split("\n----------------------------------------------------------------------\nRan ")[0], ids)
        self.assertFalse(out["verdict_present"])
        self.assertFalse(out["reconciled"])

    def test_p6_collected_identity_without_a_result_is_incomplete(self):
        text, ids = run_log(with_docstrings=True)
        out = parse(text, ids + [MODULE + ".Synthetic.test_z_collected_not_run"])
        self.assertFalse(out["reconciled"])

    def test_p7_header_without_description_line_is_not_given_a_status(self):
        text, ids = run_log(with_docstrings=True)
        cut = text.replace("Erroring test. ... ERROR\n", "", 1)
        out = parse(cut, ids)
        self.assertFalse(out["reconciled"])


# ---------------------------------------------------------------------------------------------------------------
# C-A1-B1 (Change C v0.2): complete, unambiguous outcome accounting. Counterexamples from A1's review, with positive
# controls. TEARDOWN_LOG was captured from Django 5.2.15 (a SimpleTestCase whose _post_teardown raises for *_td tests,
# and whose tearDown raises for test_teardown_method), with only the module path renamed.
# ---------------------------------------------------------------------------------------------------------------
TAIL = "\n" + "-" * 70 + "\nRan 1 test in 0.001s\n\nOK\nEXIT_STATUS: 0\n"
TID = "probe.C.test_a"
TEARDOWN_LOG = 'test_fail_td (probe.A.test_fail_td) ... FAIL\ntest_fail_td (probe.A.test_fail_td) ... ERROR\ntest_ok_td (probe.A.test_ok_td) ... ok\ntest_ok_td (probe.A.test_ok_td) ... ERROR\ntest_sub_td (probe.A.test_sub_td) ... \n  test_sub_td (probe.A.test_sub_td) (i=1) ... FAIL\ntest_sub_td (probe.A.test_sub_td) ... ERROR\ntest_sub_then_error (probe.A.test_sub_then_error) ... \n  test_sub_then_error (probe.A.test_sub_then_error) (i=1) ... FAIL\ntest_sub_then_error (probe.A.test_sub_then_error) ... ERROR\ntest_teardown_method (probe.A.test_teardown_method) ... ERROR\n\n======================================================================\nERROR: test_fail_td (probe.A.test_fail_td)\n----------------------------------------------------------------------\nTraceback (most recent call last):\n  File "probe.py", line 9, in _post_teardown\n    raise RuntimeError("flush failed")\nRuntimeError: flush failed\n\n======================================================================\nERROR: test_ok_td (probe.A.test_ok_td)\n----------------------------------------------------------------------\nTraceback (most recent call last):\n  File "probe.py", line 9, in _post_teardown\n    raise RuntimeError("flush failed")\nRuntimeError: flush failed\n\n======================================================================\nERROR: test_sub_td (probe.A.test_sub_td)\n----------------------------------------------------------------------\nTraceback (most recent call last):\n  File "probe.py", line 9, in _post_teardown\n    raise RuntimeError("flush failed")\nRuntimeError: flush failed\n\n======================================================================\nERROR: test_sub_then_error (probe.A.test_sub_then_error)\n----------------------------------------------------------------------\nTraceback (most recent call last):\n  File "probe.py", line 17, in test_sub_then_error\n    raise ValueError("after subtests")\nValueError: after subtests\n\n======================================================================\nERROR: test_teardown_method (probe.A.test_teardown_method)\n----------------------------------------------------------------------\nTraceback (most recent call last):\n  File "probe.py", line 20, in tearDown\n    if self._testMethodName == \'test_teardown_method\': raise RuntimeError(\'tearDown boom\')\n                                                       ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\nRuntimeError: tearDown boom\n\n======================================================================\nFAIL: test_fail_td (probe.A.test_fail_td)\n----------------------------------------------------------------------\nTraceback (most recent call last):\n  File "probe.py", line 11, in test_fail_td\n    def test_fail_td(self): self.assertEqual(1, 2)\n                            ^^^^^^^^^^^^^^^^^^^^^^\nAssertionError: 1 != 2\n\n======================================================================\nFAIL: test_sub_td (probe.A.test_sub_td) (i=1)\n----------------------------------------------------------------------\nTraceback (most recent call last):\n  File "probe.py", line 14, in test_sub_td\n    with self.subTest(i=i): self.assertEqual(i, 0)\n                            ^^^^^^^^^^^^^^^^^^^^^^\nAssertionError: 1 != 0\n\n======================================================================\nFAIL: test_sub_then_error (probe.A.test_sub_then_error) (i=1)\n----------------------------------------------------------------------\nTraceback (most recent call last):\n  File "probe.py", line 16, in test_sub_then_error\n    with self.subTest(i=1): self.assertEqual(1, 0)\n                            ^^^^^^^^^^^^^^^^^^^^^^\nAssertionError: 1 != 0\n\n----------------------------------------------------------------------\nRan 5 tests in 0.003s\n\nFAILED (failures=3, errors=5)\n\n'
TEARDOWN_IDS = ["probe.A." + n for n in ("test_fail_td", "test_ok_td", "test_sub_td", "test_sub_then_error", "test_teardown_method")]


class OutcomeAccounting(unittest.TestCase):
    def test_b1_1_missing_status_single_line_is_not_reconciled(self):
        out = parse("test_a (probe.C.test_a) ...\n" + TAIL, [TID])
        self.assertFalse(out["reconciled"])
        self.assertEqual(out["accounting_violations"][0]["id"], TID)

    def test_b1_2_missing_status_description_form_is_not_reconciled(self):
        out = parse("test_a (probe.C.test_a)\nA description. ...\n" + TAIL, [TID])
        self.assertFalse(out["reconciled"])

    def test_b1_3_duplicate_ok_is_not_reconciled(self):
        out = parse("test_a (probe.C.test_a) ... ok\ntest_a (probe.C.test_a) ... ok\n" + TAIL, [TID])
        self.assertFalse(out["reconciled"])

    def test_b1_4_duplicate_ok_description_form_is_not_reconciled(self):
        out = parse("test_a (probe.C.test_a)\nA description. ... ok\ntest_a (probe.C.test_a)\nA description. ... ok\n" + TAIL, [TID])
        self.assertFalse(out["reconciled"])

    def test_b1_5_join_never_takes_another_tests_status_line(self):
        lines = list(join_description_lines(["test_a (probe.C.test_a)", "test_b (probe.C.test_b) ... ok"]))
        self.assertEqual(lines, ["test_a (probe.C.test_a)", "test_b (probe.C.test_b) ... ok"])
        text = "test_a (probe.C.test_a)\ntest_b (probe.C.test_b) ... ok\n" + TAIL.replace("Ran 1 test", "Ran 2 tests")
        self.assertFalse(parse(text, [TID, "probe.C.test_b"])["reconciled"])

    def test_b1_6_extra_error_without_a_teardown_block_is_not_reconciled(self):
        text = "test_a (probe.C.test_a) ... ok\ntest_a (probe.C.test_a) ... ERROR\n" + TAIL
        self.assertFalse(parse(text, [TID])["reconciled"])

    def test_b1_7_positive_django_teardown_and_subtest_shapes_reconcile(self):
        out = parse(TEARDOWN_LOG + "\nEXIT_STATUS: 1\n", TEARDOWN_IDS)
        self.assertTrue(out["reconciled"], out["reconciliation"])
        self.assertEqual(out["accounting_violations"], [])
        by = {t["id"].rsplit(".", 1)[1]: t for t in out["tests"]}
        self.assertEqual(by["test_ok_td"]["teardown_errors"], 1)
        self.assertEqual(by["test_fail_td"]["body"], "FAIL")
        self.assertEqual(by["test_sub_td"]["subtest_failures"], 1)

    def test_b1_8_positive_all_real_shapes_from_the_synthetic_runner_still_reconcile(self):
        for docs in (True, False):
            text, ids = run_log(with_docstrings=docs)
            out = parse(text, ids)
            self.assertTrue(out["reconciled"], (docs, out["reconciliation"]))
            self.assertEqual(out["accounting_violations"], [])


# ------------------------------------------------------------------ r7: A1 residual RC-B1 and VC-O1 (UFUND-3, Change C v0.6)
import json as _json
import os as _os

with open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "runner_captures.json"), encoding="utf-8") as _f:
    CAPTURES = _json.load(_f)["captures"]


def _body(tid, status_lines, blocks="", summary="OK", ran=1, exit_status=0):
    return status_lines + blocks + "\n" + "-" * 70 + "\nRan %d test%s in 0.001s\n\n%s\nEXIT_STATUS: %d\n" % (ran, "" if ran == 1 else "s", summary, exit_status)


def _block(kind, head, frame):
    return "\n" + "=" * 70 + "\n%s: %s\n" % (kind, head) + "-" * 70 + "\nTraceback (most recent call last):\n  File \"probe.py\", line 9, in %s\n    raise RuntimeError(\"x\")\nRuntimeError: x\n" % frame


class EventReconciliation(unittest.TestCase):
    """RC-B1: a status event contradicted by another status event is refused even where no failure block exists; genuine
    runner shapes - several teardown errors, skipped sub-tests, expected failures - reconcile. Counterexamples are A1's
    exact logs; the lawful shapes are real runner captures (runner_captures.json), not hand-written."""

    def test_r7_1_a1_contradictory_subtest_fail_then_ok_is_refused(self):
        out = parse("test_a (probe.C.test_a) ...\n  test_a (probe.C.test_a) (i=1) ... FAIL\ntest_a (probe.C.test_a) ... ok\n" + TAIL, [TID])
        self.assertFalse(out["reconciled"])
        self.assertIn("sub-test events", out["accounting_violations"][0]["reason"])

    def test_r7_2_a1_contradictory_subtest_error_then_ok_is_refused(self):
        out = parse("test_a (probe.C.test_a) ...\n  test_a (probe.C.test_a) (i=1) ... ERROR\ntest_a (probe.C.test_a) ... ok\n" + TAIL, [TID])
        self.assertFalse(out["reconciled"])

    def test_r7_3_vc_named_parameter_subtest_error_in_a_second_test_is_refused(self):
        text = _body(None, "test_one (tests.test_x.X.test_one) ... ok\ntest_two (tests.test_x.X.test_two) ...\n"
                           "  test_two (tests.test_x.X.test_two) (part='decision') ... ERROR\ntest_two (tests.test_x.X.test_two) ... ok\n", ran=2)
        out = parse(text, ["tests.test_x.X.test_one", "tests.test_x.X.test_two"])
        self.assertFalse(out["reconciled"])
        self.assertEqual([v["id"] for v in out["accounting_violations"]], ["tests.test_x.X.test_two"])

    def test_r7_4_subtest_failure_with_its_block_then_ok_is_still_refused(self):
        text = _body(None, "test_a (probe.C.test_a) ...\n  test_a (probe.C.test_a) (i=1) ... FAIL\ntest_a (probe.C.test_a) ... ok\n",
                     _block("FAIL", "test_a (probe.C.test_a) (i=1)", "test_a"), summary="FAILED (failures=1)", exit_status=1)
        self.assertFalse(parse(text, [TID])["reconciled"])

    def test_r7_5_subtest_failure_block_without_its_event_is_refused(self):
        text = _body(None, "test_a (probe.C.test_a) ...\ntest_a (probe.C.test_a) ... ERROR\n",
                     _block("FAIL", "test_a (probe.C.test_a) (i=1)", "test_a") + _block("ERROR", "test_a (probe.C.test_a)", "_post_teardown"),
                     summary="FAILED (failures=1, errors=1)", exit_status=1)
        self.assertFalse(parse(text, [TID])["reconciled"])

    def test_r7_6_ok_after_an_error_is_refused(self):
        text = _body(None, "test_a (probe.C.test_a) ... ERROR\ntest_a (probe.C.test_a) ... ok\n",
                     _block("ERROR", "test_a (probe.C.test_a)", "_post_teardown"), summary="FAILED (errors=1)", exit_status=1)
        self.assertFalse(parse(text, [TID])["reconciled"])

    def test_r7_7_two_failure_blocks_outside_teardown_are_refused(self):
        text = _body(None, "test_a (probe.C.test_a) ... FAIL\ntest_a (probe.C.test_a) ... ERROR\n",
                     _block("ERROR", "test_a (probe.C.test_a)", "test_a") + _block("FAIL", "test_a (probe.C.test_a)", "test_a"),
                     summary="FAILED (failures=1, errors=1)", exit_status=1)
        self.assertFalse(parse(text, [TID])["reconciled"])

    def test_r7_8_real_three_error_shape_reconciles_and_is_an_error(self):
        cap = CAPTURES["ThreeErrors"]
        out = parse(cap["log"], cap["collected"])
        self.assertTrue(out["reconciled"], out["reconciliation"])
        t = out["tests"][0]
        self.assertEqual((t["body"], t["teardown_errors"]), ("ERROR", 2))

    def test_r7_9_real_skipped_subtests_reconcile_counted_per_event(self):
        cap = CAPTURES["SkippedSubtests"]
        out = parse(cap["log"], cap["collected"])
        self.assertTrue(out["reconciled"], out["reconciliation"])
        self.assertEqual(out["summary"]["skipped"], 4)
        self.assertEqual({t["body"] for t in out["tests"]}, {"skipped"})

    def test_r7_10_real_teardown_assertion_and_expected_outcomes_reconcile(self):
        for name in ("TeardownFail", "Expected"):
            cap = CAPTURES[name]
            out = parse(cap["log"], cap["collected"])
            self.assertTrue(out["reconciled"], (name, out["reconciliation"]))
        self.assertEqual(parse(CAPTURES["Expected"]["log"], CAPTURES["Expected"]["collected"])["summary"]["failures"], 0)

    def test_r7_11_positive_controls_remain_reconciled(self):
        self.assertTrue(parse("test_a (probe.C.test_a) ... ok\n" + TAIL, [TID])["reconciled"])
        self.assertTrue(parse(TEARDOWN_LOG + "\nEXIT_STATUS: 1\n", TEARDOWN_IDS)["reconciled"])
        for docs in (True, False):
            text, ids = run_log(with_docstrings=docs)
            self.assertTrue(parse(text, ids)["reconciled"])
