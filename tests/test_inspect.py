from __future__ import annotations

import io
import os
import runpy
import unittest
from contextlib import redirect_stdout

from src.intevia.commands.inspect import inspect_surfaces


EXPECTED_INSPECTION_OUTPUT = (
    "INTEVIA inspection surface\n"
    "\n"
    "Command surfaces:\n"
    "- breathe\n"
    "- heartbeat\n"
    "- inspect\n"
    "- demo_activity_review\n"
    "- observation\n"
    "\n"
    "Governance surfaces:\n"
    "- current_status\n"
    "- format_status\n"
    "\n"
    "Observation surfaces:\n"
    "- ObservationJournal\n"
    "- ContributionService observation emission\n"
    "- Visible Witness snapshot rendering\n"
    "\n"
    "Evidence artefacts:\n"
    "- docs/evidence/sprints/sprint-1/SPRINT_1_EVIDENCE_LOG.md\n"
    "- docs/evidence/sprints/sprint-1/SPRINT_1_CONSTITUTIONAL_CHECKPOINT.md\n"
    "- docs/evidence/sprints/sprint-1/SPRINT_1_OPC_BOUNDED_DOCTRINE_NOTE.md\n"
    "- docs/evidence/sprints/sprint-1/WORK_BLOCK_7_BOUNDARY_CHARTER.md\n"
    "- docs/evidence/sprints/sprint-1/COE_Unit6_VisibleWitness_Implementation_Package_v0.3.md\n"
    "\n"
    "Boundary:\n"
    "Reports visible surfaces only. No routing, mutation, persistence, or hidden state."
)


class InspectSurfacesOutputTests(unittest.TestCase):
    def test_inspect_surfaces_returns_exact_expected_string(self) -> None:
        result = inspect_surfaces()
        self.assertEqual(result, EXPECTED_INSPECTION_OUTPUT)

    def test_inspect_surfaces_has_no_trailing_newline(self) -> None:
        result = inspect_surfaces()
        self.assertFalse(result.endswith("\n"))

    def test_inspect_surfaces_is_deterministic_on_repeated_calls(self) -> None:
        first = inspect_surfaces()
        second = inspect_surfaces()
        self.assertEqual(first, second)

    def test_inspect_surfaces_has_no_duplicate_entries_and_no_extra_text(self) -> None:
        result = inspect_surfaces()
        self.assertEqual(result, EXPECTED_INSPECTION_OUTPUT)
        lines = result.split("\n")
        surface_lines = [
            "- breathe",
            "- heartbeat",
            "- inspect",
            "- demo_activity_review",
            "- observation",
            "- current_status",
            "- format_status",
            "- ObservationJournal",
            "- ContributionService observation emission",
            "- Visible Witness snapshot rendering",
            "- docs/evidence/sprints/sprint-1/SPRINT_1_EVIDENCE_LOG.md",
            "- docs/evidence/sprints/sprint-1/SPRINT_1_CONSTITUTIONAL_CHECKPOINT.md",
            "- docs/evidence/sprints/sprint-1/SPRINT_1_OPC_BOUNDED_DOCTRINE_NOTE.md",
            "- docs/evidence/sprints/sprint-1/WORK_BLOCK_7_BOUNDARY_CHARTER.md",
            "- docs/evidence/sprints/sprint-1/COE_Unit6_VisibleWitness_Implementation_Package_v0.3.md",
        ]
        for surface in surface_lines:
            self.assertEqual(lines.count(surface), 1)


class InspectModuleCLITests(unittest.TestCase):
    def test_module_execution_prints_single_trailing_newline(self) -> None:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        module_path = os.path.join(
            repo_root, "src", "intevia", "commands", "inspect.py"
        )
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            runpy.run_path(module_path, run_name="__main__")
        output = buffer.getvalue()
        self.assertEqual(output, EXPECTED_INSPECTION_OUTPUT + "\n")
        self.assertTrue(output.endswith("\n"))


if __name__ == "__main__":
    raise SystemExit(unittest.main())
