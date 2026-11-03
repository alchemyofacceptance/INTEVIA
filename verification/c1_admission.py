"""Once-only C1a and C1b v1.3 fault admission using the existing owned TT-1 clone machinery.

No persistent source edit. The fault is installed after clone qualification and
restored on teardown. Not imported by the recurring CI mutation registry.
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import os
import textwrap
from pathlib import Path

from django.test.runner import DiscoverRunner

from verification.isolation.template_mode import TemplateModeRunnerMixin
from verification.mutations import MutationRunner
from verification.ownership import OwnedDatabaseRunnerMixin
from verification.recording import RecordingRunnerMixin


class C1AdmissionRunner(
    RecordingRunnerMixin,
    TemplateModeRunnerMixin,
    OwnedDatabaseRunnerMixin,
    DiscoverRunner,
):
    registry = Path(__file__).with_suffix(".json")
    unit = "C1"

    def run_tests(self, test_labels, **kwargs):
        if (
            os.environ.get("VERIFICATION_TEMPLATE_MODE", "clone") != "clone"
            or os.environ.get("VERIFICATION_TEMPLATE_POLICY") != "reuse-only"
        ):
            raise RuntimeError(
                f"{self.unit} admission needs the run's existing TT-1 template in clone/reuse-only mode. No fresh fallback is allowed."
            )
        entries = json.loads(self.registry.read_text(encoding="utf-8"))
        selected = os.environ.get(f"{self.unit}_ADMISSION_CASE")
        self.entry = next((e for e in entries if e["id"] == selected), None)
        self.phase = os.environ.get(f"{self.unit}_ADMISSION_PHASE")
        if self.entry is None or self.phase not in {"normal", "fault", "restored"}:
            raise RuntimeError(
                f"Name {self.unit}_ADMISSION_CASE and a normal/fault/restored phase; no test ran."
            )
        if list(test_labels) != [self.entry["test"]]:
            raise RuntimeError(
                f"{self.unit} admission requires exactly the declared target test; no test ran."
            )
        self.restore_code = None
        try:
            return super().run_tests(test_labels, **kwargs)
        finally:
            self._restore()

    def setup_databases(self, **kwargs):
        old_config = super().setup_databases(**kwargs)
        MutationRunner.verify_clone_matches_template(self)
        if self.phase == "fault":
            self._install_fault()
        print(
            f"{self.unit}_ADMISSION={self.entry['id']} PHASE={self.phase}; no message payload logged.",
            flush=True,
        )
        return old_config

    def _install_fault(self):
        if self.entry["kind"] != "python":
            raise RuntimeError(f"{self.unit} requires a candidate-bound Python admission fault.")
        module = importlib.import_module(self.entry["module"])
        owner = module
        parts = self.entry["qualname"].split(".")
        for part in parts[:-1]:
            owner = getattr(owner, part)
        name = parts[-1]
        path = Path(module.__file__)
        if (
            hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            != self.entry["source_sha256"]
        ):
            raise RuntimeError(
                f"{self.unit} source fault does not match its LF-normalised candidate source."
            )
        function = inspect.unwrap(getattr(owner, name))
        source = textwrap.dedent(inspect.getsource(function))
        if source.count(self.entry["old"]) != 1:
            raise RuntimeError(f"{self.unit} fault anchor is missing or ambiguous.")
        namespace = dict(module.__dict__)
        exec(
            compile(
                source.replace(self.entry["old"], self.entry["new"], 1),
                str(path),
                "exec",
            ),
            namespace,
        )
        changed = inspect.unwrap(namespace[name])
        self.restore_code = (function, function.__code__)
        function.__code__ = changed.__code__

    def _restore(self):
        if self.restore_code:
            function, original = self.restore_code
            function.__code__ = original
            self.restore_code = None

    def teardown_databases(self, old_config, **kwargs):
        self._restore()
        return super().teardown_databases(old_config, **kwargs)
