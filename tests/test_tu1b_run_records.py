"""Focused checks for TU-1b run records and pre-run boundaries."""

import io
import os
import shutil
import subprocess
from contextlib import redirect_stderr
from pathlib import Path
import unittest
from unittest.mock import patch

from django.test import SimpleTestCase

from verification.isolation.run_plan import (
    _run_precheck_suite,
    _standalone_action_environment,
)
from verification.isolation.runner import IsolationRunner


class _ReporterSampleCase(unittest.TestCase):
    def test_success(self):
        pass


class RunRecordBoundaryTests(SimpleTestCase):
    def test_standalone_environment_isolated_and_restored_on_exception(self):
        names = (
            "DJANGO_SETTINGS_MODULE",
            "INTEVIA_S015_TEST_ROUTE",
            "INTEVIA_DATABASE_ENGINE",
            "INTEVIA_POSTGRES_TEST_DB",
            "PYTHONPATH",
        )
        caller_values = {
            "DJANGO_SETTINGS_MODULE": "caller.settings",
            "INTEVIA_S015_TEST_ROUTE": "CALLER_ROUTE",
            "INTEVIA_DATABASE_ENGINE": "sqlite",
            "INTEVIA_POSTGRES_TEST_DB": "caller_database",
            "INTEVIA_RUN_SUFFIX": "invalid",
            "PYTHONPATH": "caller-python-path",
        }
        with patch.dict(os.environ, caller_values):
            before = {name: os.environ.get(name) for name in names}
            with self.assertRaisesRegex(RuntimeError, "exercise restoration"):
                with _standalone_action_environment(Path.cwd(), "precheck"):
                    self.assertEqual(
                        os.environ["DJANGO_SETTINGS_MODULE"],
                        "intevia.test_settings",
                    )
                    self.assertEqual(
                        os.environ["INTEVIA_S015_TEST_ROUTE"],
                        "LIVING_ORGANISM_TEST_LIFECYCLE",
                    )
                    self.assertEqual(
                        os.environ["INTEVIA_POSTGRES_TEST_DB"].rsplit("_", 1)[0],
                        "test_intevia_living_organism_tu1_precheck",
                    )
                    self.assertIn(str(Path.cwd()), os.environ["PYTHONPATH"])
                    self.assertNotIn("caller_database", os.environ["INTEVIA_POSTGRES_TEST_DB"])
                    raise RuntimeError("exercise restoration")
            self.assertEqual(
                {name: os.environ.get(name) for name in names},
                before,
            )

    def test_precheck_reports_each_test_identifier_and_outcome(self):
        output = io.StringIO()
        suite = unittest.TestSuite(
            [_ReporterSampleCase("test_success")]
        )
        with redirect_stderr(output):
            result = _run_precheck_suite(suite)

        self.assertTrue(result.wasSuccessful())
        self.assertIn(
            "test_success (tests.test_tu1b_run_records._ReporterSampleCase.test_success) ... ok",
            output.getvalue(),
        )

    def test_pre_run_refusals_are_kept_outside_attempt_directories(self):
        source = Path("scripts/run_integrated_dev.ps1").read_text(encoding="utf-8")
        precheck = source.index('"R14 changed-test no-database pre-check"')
        route_check = source.index('"T6_NO_DATABASE_ROUTE"')
        attempt_promotion = source.index(
            "Move-Item -LiteralPath $preflightDirectory -Destination $runDirectory"
        )

        self.assertIn('$preRunRoot = Join-Path $stageRoot "pre-run-refusals"', source)
        self.assertLess(precheck, route_check)
        self.assertLess(route_check, attempt_promotion)
        self.assertIn("-RecordDirectory $preflightDirectory", source)
        self.assertNotIn("New-Item -ItemType Directory -Path $runDirectory", source)

    def test_run_record_is_written_after_database_reconciliation(self):
        source = Path("scripts/run_integrated_dev.ps1").read_text(encoding="utf-8")
        reconciliation = source.rindex("Get-RunDatabaseReconciliation")
        final_record = source.index('RUN_RECORD_CLASSIFICATION=TEST_ATTEMPT')

        self.assertLess(reconciliation, final_record)
        self.assertIn('$temporaryRecordPath = "$runRecordPath.tmp"', source)
        self.assertIn('"required_controls_passed"] = $requiredControlsPassed', source)

    def test_verifier_passes_run_spec_binding_or_unavailable(self):
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if powershell is None:
            self.fail("PowerShell is required to execute the verifier binding witness")

        verifier_path = Path("scripts/verify_integrated.ps1").resolve()
        quoted_path = "'" + str(verifier_path).replace("'", "''") + "'"
        command = f"""
$verifierPath = {quoted_path}
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $verifierPath, [ref]$tokens, [ref]$parseErrors
)
if ($parseErrors.Count -gt 0) {{ throw $parseErrors[0].Message }}
foreach ($name in @("Get-Sha256Bytes", "Get-SourceBinding", "Set-RunSpecBinding")) {{
    $definition = $ast.FindAll(
        {{ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] }},
        $true
    ) | Where-Object {{ $_.Name -eq $name }} | Select-Object -First 1
    if ($null -eq $definition) {{ throw "Verifier function not found: $name" }}
    Invoke-Expression $definition.Extent.Text
}}
function Get-IntegratedState {{
    param([string]$StateFile, [string]$StateSha256)
    return $script:authorizedState
}}
$head = (& git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) {{ throw "Could not resolve test HEAD" }}
$script:authorizedState = [PSCustomObject]@{{
    SourceKind = "COMMITTED"
    Head = $head
    MergeCommit = "test-merge"
    BaseCommit = "test-base"
    StateSha256 = ("1" * 64)
    StateId = "test-state"
    StateFile = "test-state.json"
    EvidenceRoot = "PKTB_TEST"
    DatabaseTag = "tu1b"
    State = [PSCustomObject]@{{ run_spec_sha256 = ("a" * 64) }}
}}
$binding = Get-SourceBinding -AuthorizedStateFile "test-state.json" `
    -AuthorizedStateSha256 ("1" * 64)
if ($binding.State.run_spec_sha256 -cne ("a" * 64)) {{
    throw "Source binding did not retain the state's run-spec digest"
}}
$line = Set-RunSpecBinding -SourceBinding $binding
if ($line -cne ("RUN_SPEC_SHA256=" + ("a" * 64)) -or
    $env:INTEVIA_RUN_SPEC_SHA256 -cne ("a" * 64)) {{
    throw "Verifier did not print and pass the state's run-spec digest"
}}
Write-Output "PRESENT:$line"
$script:authorizedState.State = [PSCustomObject]@{{}}
$binding = Get-SourceBinding -AuthorizedStateFile "test-state.json" `
    -AuthorizedStateSha256 ("1" * 64)
$line = Set-RunSpecBinding -SourceBinding $binding
if ($line -cne "RUN_SPEC_SHA256=UNAVAILABLE" -or
    $env:INTEVIA_RUN_SPEC_SHA256 -cne "UNAVAILABLE") {{
    throw "Missing run-spec digest was not marked UNAVAILABLE"
}}
Write-Output "MISSING:$line"
"""
        result = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PRESENT:RUN_SPEC_SHA256=" + ("a" * 64), result.stdout.splitlines())
        self.assertIn("MISSING:RUN_SPEC_SHA256=UNAVAILABLE", result.stdout.splitlines())

    def test_isolation_summary_reports_unavailable_candidate_binding_explicitly(self):
        runner = object.__new__(IsolationRunner)
        runner.cp0_met = False
        runner.suite_ids = []
        runner.started_ids = []
        runner.outcome_ids = []
        runner.checkpoints = []
        runner.emit_errors = 0
        runner.run_plan = None
        emitted = []
        runner._emit = lambda record: emitted.append(record)

        with patch.dict(os.environ, {"INTEVIA_CANDIDATE_BINDING_SHA256": "invalid"}):
            runner._summary(None)

        summary = emitted[-1]
        self.assertEqual(summary["candidate_binding"], "UNAVAILABLE")
        self.assertEqual(
            summary["candidate_binding_reason"],
            "INTEVIA_CANDIDATE_BINDING_SHA256 is missing or invalid",
        )
        self.assertEqual(summary["isolation_verdict"], "NOT ESTABLISHED")
        self.assertIn("candidate binding unavailable", summary["reasons"])

    def test_isolation_summary_accepts_well_formed_candidate_binding(self):
        runner = object.__new__(IsolationRunner)
        runner.cp0_met = False
        runner.suite_ids = []
        runner.started_ids = []
        runner.outcome_ids = []
        runner.checkpoints = []
        runner.emit_errors = 0
        runner.run_plan = None
        emitted = []
        runner._emit = lambda record: emitted.append(record)

        binding = "a" * 64
        with patch.dict(os.environ, {"INTEVIA_CANDIDATE_BINDING_SHA256": binding}):
            runner._summary(None)

        self.assertEqual(emitted[-1]["candidate_binding"], binding)
        self.assertIsNone(emitted[-1]["candidate_binding_reason"])
