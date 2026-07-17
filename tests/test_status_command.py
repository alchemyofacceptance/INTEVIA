from __future__ import annotations

import io
import runpy
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from src.intevia.commands.status_command import status_command


EXPECTED_STATUS_OUTPUT = (
    "Workstream: INTEVIA v1.0 Implementation\n"
    "Status: implementation active\n"
    "Authority: Human Governor retains final authority\n"
    "Operating frame: Run steadily. Observe consciously. Redesign only from recorded evidence."
)


class StatusCommandTests(unittest.TestCase):
    def test_status_command_returns_exact_output_without_trailing_newline(self) -> None:
        result = status_command()
        self.assertEqual(result, EXPECTED_STATUS_OUTPUT)
        self.assertFalse(result.endswith("\n"))

    def test_status_command_is_deterministic(self) -> None:
        self.assertEqual(status_command(), status_command())

    def test_module_execution_emits_exactly_one_trailing_newline(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        module_path = repo_root / "src" / "intevia" / "commands" / "status_command.py"
        buffer = io.StringIO()

        with redirect_stdout(buffer):
            runpy.run_path(str(module_path), run_name="__main__")

        self.assertEqual(buffer.getvalue(), EXPECTED_STATUS_OUTPUT + "\n")


if __name__ == "__main__":
    unittest.main()
