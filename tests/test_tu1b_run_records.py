"""Focused checks for TU-1b run records and pre-run boundaries."""

import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr
from pathlib import Path
import unittest
from unittest.mock import patch

from django.test import SimpleTestCase

from verification.isolation.run_plan import (
    _run_precheck_suite,
    _standalone_action_environment,
    _standalone_test_database_name,
)
from verification.isolation.runner import IsolationRunner, _runner_print
from verification.parse_results import parse


class _ReporterSampleCase(unittest.TestCase):
    def test_success(self):
        pass


class RunRecordBoundaryTests(SimpleTestCase):
    def test_isolation_messages_follow_test_results_in_parser_output(self):
        def emit(message, test_id=None):
            suffix = " " + test_id if test_id else ""
            _runner_print(
                "ISOLATION %s%s" % (message, suffix),
                file=sys.stderr,
                flush=True,
            )

        def new_runner(emit_controls=False, run_spec="a" * 64):
            runner = object.__new__(IsolationRunner)
            runner.debug_sql = False
            runner.pdb = False
            runner.extra_aliases = ()
            runner.started_ids = []
            runner.outcome_ids = []
            pending_failures = []
            runner._start_timing = lambda test: None
            runner._stop_timing = lambda test: None

            def checkpoint_a(test):
                if emit_controls:
                    for line in (
                        "MODE=clone",
                        "FRESH_AUTHORITY=NONE",
                        "FAILFAST=ON",
                        "RUN_SPEC_SHA256=" + run_spec,
                        "TESTS_EXECUTED=1",
                        "TESTS_NOT_REACHED=0",
                        "EXECUTED_MATCHES_PLAN=FULL",
                        "TEMPLATE_ACTION=REUSED",
                        "TEMPLATE_BUILD_SECONDS=NOT_APPLICABLE",
                        "TEMPLATE_PREPARE_SECONDS=0.000000",
                        "CLONE_SETUP_SECONDS=0.000000",
                    ):
                        _runner_print(line, file=sys.stderr, flush=True)
                emit("CP-A ESTABLISHED", test.id())
                return True

            runner.checkpoint_a = checkpoint_a
            runner._mark_failure = lambda test: pending_failures.append(test)

            def run_pending_cpf():
                while pending_failures:
                    emit("CP-F ESTABLISHED", pending_failures.pop(0).id())

            runner._run_pending_cpf = run_pending_cpf
            runner.checkpoint_z = lambda: emit("CP-Z ESTABLISHED")
            runner._finish_timing_run = lambda: (
                _runner_print(
                    "TIMING: tests=1 check=0.000000s unittest=0.000000s "
                    "post=0.000000s flush=0.000000s gap=0.000000s",
                    file=sys.stderr,
                    flush=True,
                )
                if emit_controls
                else None
            )
            return runner

        class OutputProbe(unittest.TestCase):
            def test_a_pass(self):
                pass

            def test_b_failure(self):
                self.fail("expected parser regression witness")

            def test_c_error(self):
                raise RuntimeError("expected parser regression witness")

            @unittest.skip("expected parser regression witness")
            def test_d_skip(self):
                pass

            def test_e_isolation_message(self):
                pass

            def test_f_nested_probe(self):
                class NestedProbe(unittest.TestCase):
                    def test_nested_failure(self):
                        self.fail("nested probe witness")

                NestedProbe.__module__ = __name__
                NestedProbe.__qualname__ = "NestedProbe"
                nested_runner = new_runner(
                    emit_controls=True,
                    run_spec="b" * 64,
                )
                nested_suite = unittest.defaultTestLoader.loadTestsFromTestCase(
                    NestedProbe
                )
                nested_result = unittest.TextTestRunner(
                    stream=io.StringIO(),
                    verbosity=2,
                    resultclass=nested_runner.get_resultclass(),
                ).run(nested_suite)
                self.assertFalse(nested_result.wasSuccessful())

        OutputProbe.__module__ = __name__
        OutputProbe.__qualname__ = "OutputProbe"
        runner = new_runner(emit_controls=True)
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(OutputProbe)
        collected = [test.id() for test in suite]
        output = io.StringIO()
        with patch.dict(
            os.environ, {"VERIFICATION_RUN_NONCE": "qe-route-repair-regression"}
        ), redirect_stderr(output):
            result = unittest.TextTestRunner(
                stream=output,
                verbosity=2,
                resultclass=runner.get_resultclass(),
            ).run(suite)

        text = output.getvalue() + "\nEXIT_STATUS: %d\n" % (
            0 if result.wasSuccessful() else 1
        )
        parsed = parse(text, collected)
        self.assertTrue(parsed["reconciled"], parsed["reconciliation"])
        self.assertEqual([test["id"] for test in parsed["tests"]], collected)
        self.assertEqual(parsed["summary"]["ran"], len(collected))
        self.assertEqual(
            (
                parsed["summary"]["failures"],
                parsed["summary"]["errors"],
                parsed["summary"]["skipped"],
            ),
            (1, 1, 1),
        )
        self.assertTrue(
            all(len(test["status_events"]) == 1 for test in parsed["tests"])
        )

        lines = text.splitlines()
        status_positions = {}
        for test_id in collected:
            positions = [
                index
                for index, line in enumerate(lines)
                if "(%s) ... " % test_id in line
            ]
            self.assertEqual(len(positions), 1, test_id)
            status_positions[test_id] = positions[0]
        nested_id = "%s.NestedProbe.test_nested_failure" % __name__
        expected_messages = [
            "ISOLATION CP-A ESTABLISHED %s" % test_id
            for test_id in collected
        ]
        expected_messages.extend(
            [
                "ISOLATION CP-A ESTABLISHED %s" % nested_id,
                "ISOLATION CP-F ESTABLISHED %s" % nested_id,
                "ISOLATION CP-Z ESTABLISHED",
            ]
        )
        expected_messages.extend(
            [
                "ISOLATION CP-F ESTABLISHED %s" % collected[1],
                "ISOLATION CP-F ESTABLISHED %s" % collected[2],
                "ISOLATION CP-Z ESTABLISHED",
            ]
        )
        isolation_lines = [
            line for line in lines if line.startswith("ISOLATION ")
        ]
        self.assertEqual(isolation_lines, expected_messages)
        nested_parent_position = status_positions[collected[5]]
        nested_message_positions = [
            lines.index(message)
            for message in expected_messages[
                expected_messages.index(
                    "ISOLATION CP-A ESTABLISHED %s" % nested_id
                ) :
                expected_messages.index(
                    "ISOLATION CP-Z ESTABLISHED"
                )
                + 1
            ]
        ]
        self.assertTrue(
            all(position > nested_parent_position for position in nested_message_positions)
        )
        nested_run_spec = "RUN_SPEC_SHA256=" + ("b" * 64)
        nested_spec_position = lines.index(nested_run_spec)
        nested_controls = [
            "MODE=clone",
            "FRESH_AUTHORITY=NONE",
            "FAILFAST=ON",
            nested_run_spec,
            "TESTS_EXECUTED=1",
            "TESTS_NOT_REACHED=0",
            "EXECUTED_MATCHES_PLAN=FULL",
            "TEMPLATE_ACTION=REUSED",
            "TEMPLATE_BUILD_SECONDS=NOT_APPLICABLE",
            "TEMPLATE_PREPARE_SECONDS=0.000000",
            "CLONE_SETUP_SECONDS=0.000000",
        ]
        nested_control_start = nested_spec_position - 3
        self.assertEqual(
            lines[
                nested_control_start :
                nested_control_start + len(nested_controls)
            ],
            nested_controls,
        )
        nested_cpa_position = lines.index(
            "ISOLATION CP-A ESTABLISHED %s" % nested_id
        )
        nested_cpf_position = lines.index(
            "ISOLATION CP-F ESTABLISHED %s" % nested_id
        )
        nested_cpz_position = lines.index(
            "ISOLATION CP-Z ESTABLISHED", nested_cpf_position
        )
        nested_timing_position = lines.index(
            "TIMING: tests=1 check=0.000000s unittest=0.000000s "
            "post=0.000000s flush=0.000000s gap=0.000000s",
            nested_cpz_position,
        )
        self.assertGreater(nested_control_start, nested_parent_position)
        self.assertLess(
            nested_control_start + len(nested_controls) - 1,
            nested_cpa_position,
        )
        self.assertLess(nested_cpa_position, nested_cpf_position)
        self.assertLess(nested_cpf_position, nested_cpz_position)
        self.assertLess(nested_cpz_position, nested_timing_position)

        class InterruptedProbe(unittest.TestCase):
            def test_interrupted(self):
                raise KeyboardInterrupt

        InterruptedProbe.__module__ = __name__
        InterruptedProbe.__qualname__ = "InterruptedProbe"
        interruption_runner = new_runner(
            emit_controls=True,
            run_spec="c" * 64,
        )
        interruption_suite = unittest.defaultTestLoader.loadTestsFromTestCase(
            InterruptedProbe
        )
        interruption_output = io.StringIO()
        with patch.dict(
            os.environ, {"VERIFICATION_RUN_NONCE": "qe-route-repair-interruption"}
        ), redirect_stderr(interruption_output):
            with self.assertRaises(KeyboardInterrupt):
                unittest.TextTestRunner(
                    stream=interruption_output,
                    verbosity=2,
                    resultclass=interruption_runner.get_resultclass(),
                ).run(interruption_suite)
        self.assertIn("RUN_SPEC_SHA256=" + ("c" * 64), interruption_output.getvalue())
        self.assertIn(
            "ISOLATION CP-A ESTABLISHED %s.InterruptedProbe.test_interrupted"
            % __name__,
            interruption_output.getvalue(),
        )
        self.assertIn("TIMING: tests=1", interruption_output.getvalue())

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
        with patch.dict(os.environ, caller_values), patch(
            "verification.isolation.run_plan.os.getpid", return_value=12345
        ):
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
                        "test_intevia_living_organism_tu1",
                    )
                    self.assertEqual(
                        os.environ["INTEVIA_POSTGRES_TEST_DB"],
                        _standalone_test_database_name("precheck", "p12345"),
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
        expected_identifier = _ReporterSampleCase("test_success").id()
        self.assertIn(
            f"test_success ({expected_identifier}) ... ok",
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

    def test_permitted_skip_policy_is_shared_and_mode_conditioned(self):
        import json

        policy = json.loads(
            Path("verification/permitted_skips.json").read_text(encoding="utf-8")
        )
        entries = policy["entries"]
        self.assertEqual(policy["version"], 1)
        self.assertEqual(len(entries), 4)
        self.assertEqual(
            [entry["id"] for entry in entries if entry["condition"] == "always"],
            [
                "tests.test_s007_authentication_shell.AuthenticationShellTests.test_stale_epoch_and_absolute_age_flush_sessions",
                "tests.test_sbg_governance.SBGGovernanceDepartureCascadeTests.test_w_d3_5_departure_vacates_the_seat_for_equal_time_succession",
                "tests.test_s015_application_authority.ProductionApplicationAuthorityTests.test_ap6_non_superuser_application_role_when_present",
            ],
        )
        self.assertEqual(
            [
                entry["id"]
                for entry in entries
                if entry["condition"] == "fresh-mode"
            ],
            [
                "tests.test_tt1_template_lifecycle.TemplateLifecycleToolingTests.test_template_clone_parity_and_receipt_cleanup"
            ],
        )

        verifier = Path("scripts/verify_integrated.ps1").read_text(
            encoding="utf-8"
        )
        route = Path("verification/route.py").read_text(encoding="utf-8")
        self.assertIn("function Get-PermittedSkipIds", verifier)
        self.assertIn('Get-PermittedSkipIds -Mode "clone"', verifier)
        self.assertIn("verification\\permitted_skips.json", verifier)
        self.assertIn(
            '$script:permittedSkipIds = @(Get-PermittedSkipIds -Mode "clone")',
            verifier,
        )
        self.assertIn("load_permitted_skips()", route)

    def test_qualification_attempt_suffix_uses_state_derived_me_dev_root(self):
        source = Path("scripts/qualify_tu2a.ps1").read_text(encoding="utf-8")
        allocator_start = source.index("    $nextSuffix = 910001")
        allocator_end = source.index("        $caseLog =", allocator_start)
        allocator = source[allocator_start:allocator_end]

        self.assertIn(
            "Join-Path $routes.DevelopmentRoot",
            allocator,
        )
        self.assertNotIn(
            "Join-Path $qualificationRoot",
            allocator,
        )
        state_binding = "$authorizedState = Assert-StateBinding"
        route_binding = "$routes = Get-IntegratedEvidenceRoutes"
        self.assertIn(state_binding, source)
        self.assertIn(route_binding, source)
        self.assertIn("-AuthorizedState $authorizedState", source)
        self.assertLess(source.index(state_binding), source.index(route_binding))

    def test_standalone_database_names_are_bounded_for_long_suffixes(self):
        names = {
            _standalone_test_database_name(action, suffix)
            for action in ("plan", "precheck", "validate-plan")
            for suffix in ("1", "999999", "p12345", "x" * 80)
        }
        self.assertEqual(len(names), 12)
        for name in names:
            self.assertLessEqual(
                len(name.encode("utf-8")),
                47,
                name,
            )

    def test_integrated_launcher_applies_tag_and_name_bounds_before_database_work(self):
        powershell = shutil.which("pwsh")
        if powershell is None:
            self.fail("PowerShell 7 is required for the integrated name-bound witness")

        helper_path = str(Path("scripts/integrated_state.ps1").resolve()).replace(
            "'", "''"
        )
        command = f"""
. '{helper_path}'
$routes = Get-IntegratedEvidenceRoutes `
    -AuthorizedState ([PSCustomObject]@{{ EvidenceRoot = 'PKTB_TEST'; DatabaseTag = '12345678' }}) `
    -HomeDirectory $HOME
if ($routes.DatabaseTagMaxLength -ne 8) {{ throw "wrong maximum tag length" }}
$maximumName = Assert-IntegratedTestDatabaseName `
    -Name ($routes.DevelopmentDatabasePrefix + '999999') `
    -Purpose 'boundary witness'
if ([System.Text.Encoding]::UTF8.GetByteCount($maximumName) -ne 47) {{
    throw "six-digit maximum-tag name did not reach the 47-byte boundary"
}}
$verificationName = Assert-IntegratedTestDatabaseName `
    -Name ($routes.VerificationDatabasePrefix + '999999') `
    -Purpose 'verification boundary witness'
if ([System.Text.Encoding]::UTF8.GetByteCount($verificationName) -ne 47) {{
    throw "verification name did not reach the 47-byte boundary"
}}
$probeName = Get-IntegratedCloneProbeDatabaseName -RunNonce ('x' * 80)
if ([System.Text.Encoding]::UTF8.GetByteCount($probeName) -gt 47) {{
    throw "clone preflight name exceeded the primary-name bound"
}}
$tagRejected = $false
try {{ $null = Assert-IntegratedDatabaseTag -DatabaseTag '123456789' }}
catch {{ $tagRejected = $_.Exception.Message -match 'six-digit attempt' }}
if (-not $tagRejected) {{ throw "overlength database tag was accepted" }}
$nameRejected = $false
try {{ $null = Assert-IntegratedTestDatabaseName -Name ('x' * 48) -Purpose 'probe' }}
catch {{ $nameRejected = $_.Exception.Message -match '47-byte primary-name bound' }}
if (-not $nameRejected) {{ throw "overlength primary database name was accepted" }}
Write-Output "TAG_MAX=8; SIX_DIGIT_NAME_BYTES=47; OVERLIMIT_REFUSALS=PASS"
"""
        result = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("TAG_MAX=8; SIX_DIGIT_NAME_BYTES=47; OVERLIMIT_REFUSALS=PASS", result.stdout)

        state_source = Path("scripts/integrated_state.ps1").read_text(encoding="utf-8")
        launcher_source = Path("scripts/run_integrated_dev.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "$null = Assert-IntegratedDatabaseTag -DatabaseTag $state.database_tag",
            state_source,
        )
        self.assertIn(
            'test_intevia_living_organism_w_',
            state_source,
        )
        self.assertIn(
            "$env:VERIFICATION_CLONE_PROBE_DATABASE_NAME = Get-IntegratedCloneProbeDatabaseName",
            launcher_source,
        )
        witness_pattern = re.search(
            r"\$testCreatedWitnessPattern = '([^']+)'",
            launcher_source,
        )
        self.assertIsNotNone(witness_pattern)
        for witness_name in (
            "test_intevia_living_organism_w_2dd0b81f246a",
            "test_intevia_living_organism_tt1_witness_7c7eff16846d",
        ):
            self.assertRegex(witness_name, witness_pattern.group(1))
        self.assertNotRegex(
            "test_intevia_living_organism_tt1_witness_not-a-token",
            witness_pattern.group(1),
        )
        self.assertIn(
            '$Label -cin @("Clone template preflight", "Explicit-label development run")',
            launcher_source,
        )
        attempt_check = launcher_source.index(
            "$attemptDatabase = Assert-IntegratedTestDatabaseName"
        )
        stall_guard = launcher_source.index("if ($StallGuardSelfTest)", attempt_check)
        self_check_database_call = launcher_source.index(
            '-Action "check-self-check"', stall_guard
        )
        self.assertLess(attempt_check, stall_guard)
        self.assertLess(attempt_check, self_check_database_call)

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
        with tempfile.TemporaryDirectory() as temporary:
            git_root = Path(temporary)
            subprocess.run(
                ["git", "init"],
                cwd=git_root,
                capture_output=True,
                text=True,
                check=True,
            )
            subprocess.run(
                ["git", "config", "--local", "user.name", "TU1B Test"],
                cwd=git_root,
                capture_output=True,
                text=True,
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "config",
                    "--local",
                    "user.email",
                    "tu1b-test@example.invalid",
                ],
                cwd=git_root,
                capture_output=True,
                text=True,
                check=True,
            )
            (git_root / "fixture.txt").write_text("fixture\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", "--", "fixture.txt"],
                cwd=git_root,
                capture_output=True,
                text=True,
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-c",
                    "commit.gpgsign=false",
                    "commit",
                    "-m",
                    "test verifier witness",
                ],
                cwd=git_root,
                capture_output=True,
                text=True,
                check=True,
            )
            result = subprocess.run(
                [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
                cwd=git_root,
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
        runner.observed_aliases = ["default"]
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
        runner.observed_aliases = ["default"]
        emitted = []
        runner._emit = lambda record: emitted.append(record)

        binding = "a" * 64
        with patch.dict(os.environ, {"INTEVIA_CANDIDATE_BINDING_SHA256": binding}):
            runner._summary(None)

        self.assertEqual(emitted[-1]["candidate_binding"], binding)
        self.assertIsNone(emitted[-1]["candidate_binding_reason"])

    def test_attempt_summary_keeps_plan_binding_without_rotation_lineage(self):
        runner = object.__new__(IsolationRunner)
        test_id = "suite.Case.test_one"
        runner.cp0_met = False
        runner.suite_ids = [test_id]
        runner.started_ids = [test_id]
        runner.outcome_ids = [test_id]
        runner.checkpoints = []
        runner.emit_errors = 0
        runner.run_plan = {"tests": [test_id]}
        runner.plan_sha256 = "d" * 64
        runner.checkpoint_stop_id = None
        runner.checkpoint_failure_test = None
        runner.observed_aliases = ["default"]
        emitted = []
        runner._emit = lambda record: emitted.append(record)

        with patch.dict(os.environ, {"INTEVIA_CANDIDATE_BINDING_SHA256": "a" * 64}):
            runner._summary(None)

        summary = emitted[-1]
        self.assertEqual(summary["plan_sha256"], "d" * 64)
        self.assertEqual(summary["executed_matches_plan"], "FULL")
        self.assertFalse(
            {
                "parent_plan_sha256",
                "rotation",
                "rotation_unit",
                "rotation_failure",
            }
            & summary.keys()
        )

        source = Path("scripts/run_integrated_dev.ps1").read_text(encoding="utf-8")
        self.assertNotIn("latest = $true", source)
        self.assertNotIn("parent_plan_sha256", source)
        self.assertNotIn("rotation_side", source)
