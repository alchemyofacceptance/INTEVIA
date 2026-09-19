"""Recording helpers for the verification route."""
from __future__ import annotations

import json
import os
import traceback
import unittest


HOLDER_ERROR_TYPE = getattr(unittest.suite, "_ErrorHolder")
SUBTEST_TYPE = getattr(unittest.case, "_SubTest")
_ORIGINAL_TESTCASE_RUN = unittest.TestCase.run
_TESTCASE_RUN_GUARD_INSTALLED = False
_ACTIVE_RECORD_SINK = None


class ParallelExecutionRefused(RuntimeError):
    pass


def _require_nonce(explicit=None):
    nonce = explicit or os.environ.get("VERIFICATION_RUN_NONCE")
    if not nonce:
        raise RuntimeError("VERIFICATION_RUN_NONCE is required")
    return nonce


def _suite_tests(suite):
    if isinstance(suite, unittest.TestSuite):
        for item in suite:
            yield from _suite_tests(item)
    else:
        yield suite


def _suite_ids(suite):
    return [test.id() for test in _suite_tests(suite)]


def _target(test):
    identifier = getattr(test, "id", None)
    if callable(identifier):
        try:
            return identifier()
        except Exception:
            pass
    return str(test)


def _phase_from_exc_info(test, err):
    if err is None or err[2] is None:
        return "other"
    method_name = getattr(test, "_testMethodName", None)
    recognized = {
        "_pre_setup": "setup",
        "setUp": "setup",
        "setUpClass": "setup",
        "setUpModule": "setup",
        "tearDown": "teardown",
        "tearDownClass": "teardown",
        "tearDownModule": "teardown",
        "doCleanups": "teardown",
        "_post_teardown": "teardown",
    }
    for frame in reversed(traceback.extract_tb(err[2])):
        if frame.name == method_name or (method_name is None and frame.name.startswith("test")):
            return "body"
        if frame.name in recognized:
            return recognized[frame.name]
    return "other"


def _append_record(sink, kind, test, *, target=None, phase="other", details=None, exc=None, elapsed=None, extra=None):
    sink.seq += 1
    record = {
        "seq": sink.seq,
        "nonce": sink.nonce,
        "step": sink.step,
        "pid": sink.pid,
        "kind": kind,
        "target": _target(test) if target is None else target,
    }
    if phase is not None:
        record["phase"] = phase
    if details is not None:
        record["details"] = details
    if exc is not None:
        record["exc"] = exc
    if elapsed is not None:
        record["elapsed"] = elapsed
    if extra:
        record.update(extra)
    sink.records.append(record)
    if sink.record_stream is not None:
        sink.record_stream.write(json.dumps(record, default=str) + "\n")
        if hasattr(sink.record_stream, "flush"):
            sink.record_stream.flush()
    return record


def _record_fields(*, is_subtest=None, message=None, params=None):
    fields = {}
    if is_subtest is not None:
        fields["is_subtest"] = is_subtest
    if message is not None:
        fields["message"] = message
    if params is not None:
        fields["params"] = params
    return fields


def _append_record_common(sink, kind, test, *, target=None, phase="other", details=None, is_subtest=None, message=None, params=None, exc=None, elapsed=None, extra=None):
    folded = _record_fields(is_subtest=is_subtest, message=message, params=params)
    if extra:
        folded.update(extra)
    return _append_record(sink, kind, test, target=target, phase=phase, details=details, exc=exc, elapsed=elapsed, extra=folded)


def _resultclass_base(runner, getter):
    base = getter() if getter is not None else None
    if base is None:
        base = getattr(runner, "resultclass", None)
    if base is None:
        base = unittest.TextTestResult
    return base


def _record_sink_for_result(result):
    sink = getattr(result, "record_sink", None)
    if sink is not None:
        return sink
    if hasattr(result, "_append_record"):
        return result
    return _ACTIVE_RECORD_SINK


def _record_test_entered(test, result):
    sink = _record_sink_for_result(result)
    if sink is None:
        return None
    return sink._append_record("TEST-ENTERED", test, target=test.id(), phase="other", extra={"result_class": type(result).__name__})


def _activate_record_sink(sink):
    global _ACTIVE_RECORD_SINK
    previous = _ACTIVE_RECORD_SINK
    _ACTIVE_RECORD_SINK = sink
    return previous


def install_testcase_run_guard():
    global _TESTCASE_RUN_GUARD_INSTALLED
    if _TESTCASE_RUN_GUARD_INSTALLED:
        return

    def guarded_run(self, result=None):
        _record_test_entered(self, result)
        return _ORIGINAL_TESTCASE_RUN(self, result)

    unittest.TestCase.run = guarded_run
    _TESTCASE_RUN_GUARD_INSTALLED = True


class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args, record_sink=None, nonce=None, step=None, record_stream=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.record_sink = record_sink or self
        self.nonce = _require_nonce(nonce if record_sink is None else nonce or getattr(self.record_sink, "nonce", None))
        self.step = step if step is not None else getattr(self.record_sink, "step", os.environ.get("VERIFICATION_STEP", "OFFLINE"))
        self.record_stream = record_stream if record_stream is not None else getattr(self.record_sink, "record_stream", None)
        if record_sink is None:
            self.records = []
            self.seq = 0
            self.pid = os.getpid()
        else:
            self.records = self.record_sink.records
            self.pid = self.record_sink.pid
        self.events = self.records

    def _append_record(self, kind, test, *, target=None, phase="other", details=None, is_subtest=None, message=None, params=None, exc=None, elapsed=None, extra=None):
        return _append_record_common(self, kind, test, target=target, phase=phase, details=details, is_subtest=is_subtest, message=message, params=params, exc=exc, elapsed=elapsed, extra=extra)

    def startTest(self, test):
        self.record_sink._append_record("START", test, phase="other")
        super().startTest(test)

    def stopTest(self, test):
        try:
            super().stopTest(test)
        finally:
            self.record_sink._append_record("STOP", test, phase="other")

    def addSuccess(self, test):
        self.record_sink._append_record("OK", test, phase="body")
        super().addSuccess(test)

    def addError(self, test, err):
        kind = "HOLDER-ERROR" if isinstance(test, HOLDER_ERROR_TYPE) else "ERROR"
        self.record_sink._append_record(kind, test, phase=_phase_from_exc_info(test, err), details=self._exc_info_to_string(err, test), exc=err[0].__name__)
        super().addError(test, err)

    def addFailure(self, test, err):
        self.record_sink._append_record("FAIL", test, phase=_phase_from_exc_info(test, err), details=self._exc_info_to_string(err, test), exc=err[0].__name__)
        super().addFailure(test, err)

    def addSkip(self, test, reason):
        if isinstance(test, HOLDER_ERROR_TYPE):
            kind = "HOLDER-SKIP"
        elif isinstance(test, SUBTEST_TYPE):
            kind = "SUB-SKIP"
        else:
            kind = "SKIP"
        phase = "subtest" if isinstance(test, SUBTEST_TYPE) else _phase_from_exc_info(test, None)
        fields = {"phase": phase, "details": reason}
        if isinstance(test, SUBTEST_TYPE):
            message = getattr(test, "_message", None)
            if not isinstance(message, str):
                message = None
            fields.update(_record_fields(is_subtest=True, message=message, params=dict(getattr(getattr(test, "params", {}), "items", lambda: [])())))
        self.record_sink._append_record(kind, test, **fields)
        super().addSkip(test, reason)

    def addExpectedFailure(self, test, err):
        self.record_sink._append_record("XFAIL", test, phase=_phase_from_exc_info(test, err), details=self._exc_info_to_string(err, test), exc=err[0].__name__)
        super().addExpectedFailure(test, err)

    def addUnexpectedSuccess(self, test):
        self.record_sink._append_record("XPASS", test, phase="other")
        super().addUnexpectedSuccess(test)

    def addSubTest(self, test, subtest, err):
        if err is None:
            kind = "SUB-OK"
        elif issubclass(err[0], test.failureException):
            kind = "SUB-FAIL"
        elif issubclass(err[0], unittest.SkipTest):
            kind = "SUB-SKIP"
        else:
            kind = "SUB-ERROR"
        message = getattr(subtest, "_message", None)
        if not isinstance(message, str):
            message = None
        params = dict(getattr(getattr(subtest, "params", {}), "items", lambda: [])())
        fields = {"phase": "subtest", "is_subtest": True, "message": message, "params": params}
        if err is not None:
            fields.update(details=self._exc_info_to_string(err, subtest), exc=err[0].__name__)
        self.record_sink._append_record(kind, subtest, **fields)
        super().addSubTest(test, subtest, err)

    def addDuration(self, test, elapsed):
        self.record_sink._append_record("DURATION", test, phase="other", elapsed=elapsed)
        super().addDuration(test, elapsed)


class RecordingRunnerMixin:
    def __init__(self, *args, nonce=None, step=None, record_stream=None, **kwargs):
        install_testcase_run_guard()
        self.nonce = _require_nonce(nonce)
        self.step = step or os.environ.get("VERIFICATION_STEP", "OFFLINE")
        self.record_stream = record_stream
        self.records = []
        self.seq = 0
        self.pid = os.getpid()
        self.suite_ids = []
        self._run_end_emitted = False
        class_name = self.__class__.__name__
        parallel = kwargs.get("parallel", 1)
        if parallel > 1:
            raise ParallelExecutionRefused("the %s was configured for %s parallel processes; this route requires serial execution" % (class_name, parallel))
        super().__init__(*args, **kwargs)
        parallel = getattr(self, "parallel", parallel)
        if parallel > 1:
            raise ParallelExecutionRefused("the %s was configured for %s parallel processes; this route requires serial execution" % (class_name, parallel))
        self._append_record("RUNNER-START", self, target=self.__class__.__name__, phase="other")

    def _append_record(self, kind, test, *, target=None, phase="other", details=None, is_subtest=None, message=None, params=None, exc=None, elapsed=None, extra=None):
        current = globals().get("_ACTIVE_RECORD_SINK")
        try:
            globals()["_ACTIVE_RECORD_SINK"] = self
            return _append_record_common(self, kind, test, target=target, phase=phase, details=details, is_subtest=is_subtest, message=message, params=params, exc=exc, elapsed=elapsed, extra=extra)
        finally:
            globals()["_ACTIVE_RECORD_SINK"] = current

    def _run_end_fields(self, result):
        if result is None:
            return {
                "testsRun": 0,
                "failures": 0,
                "errors": 0,
                "skipped": 0,
                "expectedFailures": 0,
                "unexpectedSuccesses": 0,
                "wasSuccessful": False,
            }
        return {
            "testsRun": result.testsRun,
            "failures": len(result.failures),
            "errors": len(result.errors),
            "skipped": len(result.skipped),
            "expectedFailures": len(result.expectedFailures),
            "unexpectedSuccesses": len(result.unexpectedSuccesses),
            "wasSuccessful": result.wasSuccessful(),
        }

    def get_resultclass(self):
        base_getter = getattr(super(), "get_resultclass", None)
        base = _resultclass_base(self, base_getter)
        if issubclass(base, RecordingResult):
            return base

        runner = self

        class Result(RecordingResult, base):
            def __init__(self, *args, **kwargs):
                kwargs["record_sink"] = runner
                kwargs.setdefault("nonce", runner.nonce)
                kwargs.setdefault("step", runner.step)
                kwargs.setdefault("record_stream", runner.record_stream)
                super().__init__(*args, **kwargs)

        return Result

    def build_suite(self, *args, **kwargs):
        suite = super().build_suite(*args, **kwargs)
        self.suite_ids = _suite_ids(suite)
        self._append_record("SUITE-BUILT", suite, target="suite", phase="other", extra={"suite_ids": self.suite_ids})
        return suite

    def setup_databases(self, **kwargs):
        self._append_record("DB-SETUP-START", self, target="databases", phase="other")
        try:
            old_config = super().setup_databases(**kwargs)
        except Exception as exc:
            self._append_record("DB-SETUP-REFUSED", self, target="databases", phase="other", details=str(exc), exc=type(exc).__name__)
            raise
        self._append_record("DB-SETUP-DONE", self, target="databases", phase="other")
        return old_config

    def run_suite(self, suite, **kwargs):
        assert getattr(self, "parallel", 1) <= 1
        self._append_record("SUITE-RUN-START", suite, target="suite", phase="other", extra={"suite_ids": self.suite_ids or _suite_ids(suite)})
        result = None
        previous_sink = _activate_record_sink(self)
        try:
            result = super().run_suite(suite, **kwargs)
            return result
        finally:
            if not self._run_end_emitted:
                self._append_record("RUN-END", suite, target="suite", phase="other", extra=self._run_end_fields(result))
                self._run_end_emitted = True
            globals()["_ACTIVE_RECORD_SINK"] = previous_sink


class RecordedTestCaseMixin:
    def run(self, result=None):
        if result is not None and not (hasattr(result, "record_sink") or hasattr(result, "_append_record") or hasattr(result, "records")):
            raise TypeError("RecordingResult required")
        return super().run(result)
