from __future__ import annotations

import io
import runpy
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from src.intevia.commands.run_observation import run_observation


EXPECTED_RUN_OBSERVATION_OUTPUT = (
    "First Breath:\n"
    "INTEVIA organism initialised\n"
    "\n"
    "Governance Status:\n"
    "Workstream: INTEVIA v1.0 Implementation\n"
    "Status: implementation active\n"
    "Authority: Human Governor retains final authority\n"
    "Operating frame: Run steadily. Observe consciously. Redesign only from recorded evidence."
)


class RunObservationTests(unittest.TestCase):
    def test_run_observation_returns_exact_output_without_trailing_newline(self) -> None:
        result = run_observation()
        self.assertEqual(result, EXPECTED_RUN_OBSERVATION_OUTPUT)
        self.assertFalse(result.endswith("\n"))

    def test_run_observation_is_deterministic(self) -> None:
        self.assertEqual(run_observation(), run_observation())

    def test_module_execution_emits_exactly_one_trailing_newline(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        module_path = repo_root / "src" / "intevia" / "commands" / "run_observation.py"
        buffer = io.StringIO()

        with redirect_stdout(buffer):
            runpy.run_path(str(module_path), run_name="__main__")

        self.assertEqual(buffer.getvalue(), EXPECTED_RUN_OBSERVATION_OUTPUT + "\n")

    def test_root_entrypoint_emits_exact_governed_report(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        run_path = repo_root / "run.py"
        buffer = io.StringIO()

        with redirect_stdout(buffer):
            runpy.run_path(str(run_path), run_name="__main__")

        self.assertEqual(buffer.getvalue(), EXPECTED_RUN_OBSERVATION_OUTPUT + "\n")


if __name__ == "__main__":
    unittest.main()
