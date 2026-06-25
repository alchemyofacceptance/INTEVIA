import unittest

from src.intevia.commands.heartbeat import heartbeat


class HeartbeatCommandTests(unittest.TestCase):
    def test_heartbeat_output_contains_breath_and_governance_context(self):
        output = heartbeat()

        self.assertIn("INTEVIA organism initialised", output)
        self.assertIn("Sprint: INTEVIA Sprint 1", output)
        self.assertIn("Status: governed build-study active", output)
        self.assertIn("Authority: Human Governor retains final authority", output)
        self.assertIn("Operating frame: Build INTEVIA. Instrument INTEVIA.", output)


if __name__ == "__main__":
    unittest.main()
