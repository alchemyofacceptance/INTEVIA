from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

from src.intevia.commands.demo_activity_review import main, run_demo


class DemoActivityReviewTests(unittest.TestCase):
    def test_run_demo_returns_success_and_is_deterministic(self) -> None:
        buffer_1 = io.StringIO()
        exit_code_1 = run_demo(output=buffer_1)
        rendered_1 = buffer_1.getvalue()

        buffer_2 = io.StringIO()
        exit_code_2 = run_demo(output=buffer_2)
        rendered_2 = buffer_2.getvalue()

        self.assertEqual(exit_code_1, 0)
        self.assertEqual(exit_code_2, 0)
        self.assertEqual(rendered_1, rendered_2)

        self.assertIn("Activity: activity:demo-review", rendered_1)
        self.assertIn("Contribution: contribution:demo-001", rendered_1)
        self.assertIn("Contributor: human:contributor-001", rendered_1)
        self.assertIn("Reviewer: human:reviewer-001", rendered_1)

        self.assertIn("Flow:", rendered_1)
        self.assertIn("draft -> submitted -> accepted", rendered_1)
        self.assertIn("Final state: accepted", rendered_1)

        self.assertIn("Transition history:", rendered_1)
        self.assertIn("1. draft -> submitted", rendered_1)
        self.assertIn("2. submitted -> accepted", rendered_1)
        self.assertIn("Actor: human:contributor-001", rendered_1)
        self.assertIn("Actor: human:reviewer-001", rendered_1)

        self.assertIn(
            "Timestamp: 2026-07-13T08:30:02+00:00",
            rendered_1,
        )
        self.assertIn(
            "Timestamp: 2026-07-13T08:30:03+00:00",
            rendered_1,
        )

        self.assertIn("Human decision: accepted", rendered_1)
        self.assertIn(
            "Authority: explicit Human decision recorded",
            rendered_1,
        )

    def test_main_exits_successfully_and_does_not_leak_output(self) -> None:
        buffer = io.StringIO()

        with redirect_stdout(buffer):
            self.assertEqual(main(), 0)

        rendered = buffer.getvalue()
        self.assertIn(
            "INTEVIA v1.0 — Governed Activity Review Demo",
            rendered,
        )


if __name__ == "__main__":
    raise SystemExit(unittest.main())
