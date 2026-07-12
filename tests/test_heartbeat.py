import unittest

from src.intevia.commands.heartbeat import heartbeat


class HeartbeatCommandTests(unittest.TestCase):
    def test_heartbeat_output_contains_breath_and_governance_context(self):
        output = heartbeat()

        self.assertIn("INTEVIA organism initialised", output)
        self.assertIn("Workstream: INTEVIA v1.0 Implementation", output)
        self.assertIn("Status: implementation active", output)
        self.assertIn("Authority: Human Governor retains final authority", output)
        self.assertIn("Operating frame: Run steadily. Observe consciously. Redesign only from recorded evidence.", output)


if __name__ == "__main__":
    unittest.main()
