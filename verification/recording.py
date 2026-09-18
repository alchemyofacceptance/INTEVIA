"""Recording helpers for the verification route."""
from __future__ import annotations

import unittest


class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.events = []

    def _record(self, kind, test, details=None):
        self.events.append({"kind": kind, "test": test.id(), "details": details})

    def addSuccess(self, test):
        self._record("success", test)
        super().addSuccess(test)

    def addError(self, test, err):
        self._record("error", test, self._exc_info_to_string(err, test))
        super().addError(test, err)

    def addFailure(self, test, err):
        self._record("failure", test, self._exc_info_to_string(err, test))
        super().addFailure(test, err)

    def addSkip(self, test, reason):
        self._record("skip", test, reason)
        super().addSkip(test, reason)

    def addExpectedFailure(self, test, err):
        self._record("expected_failure", test, self._exc_info_to_string(err, test))
        super().addExpectedFailure(test, err)

    def addUnexpectedSuccess(self, test):
        self._record("unexpected_success", test)
        super().addUnexpectedSuccess(test)


class RecordingRunnerMixin:
    def get_resultclass(self):
        base = super().get_resultclass() or unittest.TextTestResult
        if issubclass(base, RecordingResult):
            return base

        class Result(RecordingResult, base):
            pass

        return Result


class RecordedTestCaseMixin:
    def run(self, result=None):
        if result is not None and not hasattr(result, "events"):
            raise TypeError("RecordingResult required")
        return super().run(result)
