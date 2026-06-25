import unittest

from src.intevia.app import breathe
from src.intevia.governance.status import current_status, format_status


class FirstBreathTests(unittest.TestCase):
    def test_breathe_returns_initialised_message(self):
        self.assertEqual(breathe(), "INTEVIA organism initialised")

    def test_current_status_declares_human_authority(self):
        status = current_status()

        self.assertEqual(status.sprint, "INTEVIA Sprint 1")
        self.assertEqual(status.status, "governed build-study active")
        self.assertEqual(status.human_authority, "Human Governor retains final authority")
        self.assertEqual(status.operating_frame, "Build INTEVIA. Instrument INTEVIA.")

    def test_format_status_is_human_readable(self):
        formatted = format_status(current_status())

        self.assertIn("Sprint: INTEVIA Sprint 1", formatted)
        self.assertIn("Authority: Human Governor retains final authority", formatted)


if __name__ == "__main__":
    unittest.main()
