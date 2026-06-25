import unittest

from src.intevia.commands.inspect import inspect_surfaces


class InspectCommandTests(unittest.TestCase):
    def test_inspect_output_contains_expected_sections_and_surfaces(self):
        output = inspect_surfaces()

        self.assertIn("INTEVIA inspection surface", output)
        self.assertIn("Command surfaces:", output)
        self.assertIn("- breathe", output)
        self.assertIn("- heartbeat", output)

        self.assertIn("Governance surfaces:", output)
        self.assertIn("- current_status", output)
        self.assertIn("- format_status", output)

        self.assertIn("Evidence artefacts:", output)
        self.assertIn("SPRINT_1_EVIDENCE_LOG.md", output)
        self.assertIn("SPRINT_1_CONSTITUTIONAL_CHECKPOINT.md", output)
        self.assertIn("SPRINT_1_OPC_BOUNDED_DOCTRINE_NOTE.md", output)
        self.assertIn("WORK_BLOCK_7_BOUNDARY_CHARTER.md", output)

        self.assertIn(
            "Reports visible surfaces only. No routing, mutation, persistence, or hidden state.",
            output,
        )


if __name__ == "__main__":
    unittest.main()
