"""Self-test of verification.parse_results (C-P1). Standard library only: no Django, no database.

The logs are produced by unittest's own verbose runner - the runner Django uses for 'manage.py test -v 2' - over a
synthetic test case whose tests pass, fail, error, use sub-tests and are skipped, with and without docstrings. Each
check states the property it demonstrates.

Run: python -m unittest verification.selftest_parser -v
"""
import io
import unittest

from verification.parse_results import parse

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
