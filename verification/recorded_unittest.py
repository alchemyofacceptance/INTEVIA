"""Recorded unittest entry points for the OFFLINE step."""
from __future__ import annotations

import os
import unittest

from verification.recording import RecordedTestCaseMixin, RecordingRunnerMixin, _suite_ids


class RecordedTextTestRunner(RecordingRunnerMixin, unittest.TextTestRunner):
    def __init__(self, *args, record_stream=None, step=None, **kwargs):
        super().__init__(*args, record_stream=record_stream, step=step or os.environ.get("VERIFICATION_STEP", "OFFLINE"), **kwargs)
        self.resultclass = self.get_resultclass()

    def build_suite(self, suite):
        self.suite_ids = _suite_ids(suite)
        self._append_record("SUITE-BUILT", suite, target="suite", phase="other", extra={"suite_ids": self.suite_ids})
        return suite

    def run(self, test):
        suite = self.build_suite(test)
        self._append_record("SUITE-RUN-START", suite, target="suite", phase="other", extra={"suite_ids": self.suite_ids})
        result = None
        from verification import recording as recording_mod
        previous_sink = recording_mod._activate_record_sink(self)
        try:
            result = super().run(suite)
            return result
        finally:
            if not self._run_end_emitted:
                self._append_record("RUN-END", suite, target="suite", phase="other", extra=self._run_end_fields(result))
                self._run_end_emitted = True
            recording_mod._ACTIVE_RECORD_SINK = previous_sink

class RecordedTestCase(RecordedTestCaseMixin, unittest.TestCase):
    pass
