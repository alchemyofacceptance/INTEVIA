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

        self.assertIn(
            "Identity: authenticated Django User and Profile",
            rendered_1,
        )
        self.assertIn(
            "Authority: role assignment plus entitlement capability",
            rendered_1,
        )
        self.assertIn(
            "Attribution: actor strings do not establish authority",
            rendered_1,
        )
        self.assertIn(
            "draft -> submitted -> under_review -> accepted|rejected",
            rendered_1,
        )
        self.assertIn(
            "correction_pending_review -> submitted",
            rendered_1,
        )
        self.assertIn(
            "Every successor version requires a new Human decision.",
            rendered_1,
        )

    def test_main_exits_successfully_and_does_not_leak_output(self) -> None:
        buffer = io.StringIO()

        with redirect_stdout(buffer):
            self.assertEqual(main(), 0)

        rendered = buffer.getvalue()
        self.assertIn(
            "INTEVIA v1.0 — Governed Contribution Lifecycle",
            rendered,
        )


if __name__ == "__main__":
    raise SystemExit(unittest.main())
