"""Database-free F0 pre-run diagnostics; never qualifying CI evidence.

The ordinary verification route discovers the same eight test methods. This
helper is only their focused R14-style pre-run: fail-fast, no database, timings.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import sys
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SECRET_KEY", secrets.token_urlsafe(50))
os.environ.setdefault("INTEVIA_DATABASE_ENGINE", "postgresql")
os.environ.setdefault("INTEVIA_POSTGRES_DB", "intevia_f0_unconnected")
os.environ.setdefault("INTEVIA_POSTGRES_USER", "f0_unconnected")
os.environ.setdefault("INTEVIA_POSTGRES_PASSWORD", secrets.token_urlsafe(50))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "intevia.settings")
import django

django.setup()
from django.db import connections
from django.test.runner import DiscoverRunner

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("labels", nargs="*")
parser.add_argument("--report", type=Path)
args = parser.parse_args()
connection_attempts = []

class TimedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.timings = {}

    def startTest(self, test):
        self.started = time.perf_counter()
        super().startTest(test)

    def stopTest(self, test):
        self.timings[test.id()] = round(time.perf_counter() - self.started, 6)
        super().stopTest(test)

class ReportingRunner(unittest.TextTestRunner):
    resultclass = TimedResult

    def run(self, test):
        result = super().run(test)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps({
                "run_kind": "pre-run", "fail_fast": True, "database_mode": "none",
                "tests": result.testsRun, "failures": len(result.failures),
                "errors": len(result.errors), "skips": len(result.skipped),
                "connection_attempts": len(connection_attempts),
                "test_seconds": result.timings,
            }, indent=2) + "\n")
        return result

class F0Runner(DiscoverRunner):
    test_runner = ReportingRunner


def refuse_connection(*args, **kwargs):
    connection_attempts.append(True)
    raise AssertionError("F0's presentation pre-run must not connect to any database.")

started = time.perf_counter()
patchers = [patch.object(connections[alias], "ensure_connection", refuse_connection) for alias in connections]
for patcher in patchers:
    patcher.start()
try:
    failures = F0Runner(verbosity=2, failfast=True).run_tests(args.labels or ["tests.test_f0_ui"])
finally:
    for patcher in reversed(patchers):
        patcher.stop()
print(f"F0 presentation witnesses: {time.perf_counter()-started:.3f}s; connection attempts {len(connection_attempts)}; not qualifying evidence")
raise SystemExit(bool(failures or connection_attempts))
