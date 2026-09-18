"""Recorded unittest entry points for the OFFLINE step."""
from __future__ import annotations

import unittest

from verification.recording import RecordedTestCaseMixin, RecordingRunnerMixin


class RecordedTextTestRunner(RecordingRunnerMixin, unittest.TextTestRunner):
    pass


class RecordedTestCase(RecordedTestCaseMixin, unittest.TestCase):
    pass
