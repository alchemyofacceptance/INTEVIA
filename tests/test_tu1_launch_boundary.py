"""No-database acceptance checks for the TU-1 launch boundary."""

import hashlib

from django.test import SimpleTestCase

from verification.isolation.run_plan import (
    assert_execution,
    load_plan_from_data,
    validate_run_spec_binding,
    validate_run_record_consistency,
    validate_verification_summary,
    verify_kind,
)


class LaunchBoundaryTests(SimpleTestCase):
    def _run_in_fresh_process(self, source):
        import os
        import subprocess
        import sys
        from pathlib import Path

        root = Path.cwd().resolve()
        python_paths = [str(root), str(root / "tests")]
        if os.environ.get("PYTHONPATH"):
            python_paths.append(os.environ["PYTHONPATH"])
        child_env = {
            name: os.environ[name]
            for name in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP")
            if name in os.environ
        }
        child_env.update(
            {
                "DJANGO_SETTINGS_MODULE": "intevia.test_settings",
                "DJANGO_SECRET_KEY": "offline-test-placeholder-not-a-secret",
                "INTEVIA_S015_TEST_ROUTE": "LIVING_ORGANISM_TEST_LIFECYCLE",
                "INTEVIA_DATABASE_ENGINE": "postgresql",
                "INTEVIA_POSTGRES_DB": "postgres",
                "INTEVIA_POSTGRES_USER": "offline_test",
                "INTEVIA_POSTGRES_PASSWORD": "offline-test-placeholder-not-a-secret",
                "INTEVIA_POSTGRES_TEST_DB": "test_intevia_living_organism_tu1_plan_precheck",
                "PYTHONPATH": os.pathsep.join(python_paths),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        return subprocess.run(
            [sys.executable, "-B", "-c", source],
            capture_output=True,
            check=False,
            cwd=root,
            env=child_env,
            text=True,
        )

    def _build_plan_in_fresh_process(self, label):
        import json

        source = (
            "import json\n"
            "from verification.isolation.run_plan import build_plan\n"
            "attempts = {'count': 0}\n"
            f"plan = build_plan([{label!r}], connection_attempts=attempts)\n"
            "print(json.dumps({'plan': plan, 'attempts': attempts}))\n"
        )
        result = self._run_in_fresh_process(source)
        self.assertEqual(result.returncode, 0, result.stderr)
        response = json.loads(result.stdout)
        return response["plan"], response["attempts"]

    def test_module_labels_expand_before_execution(self):
        from verification.isolation.run_plan import resolve_labels

        self.assertGreater(len(resolve_labels(["tests.test_tu1_launch_boundary"])), 0)

    def test_real_test_label_builds_one_plan_without_database_connections(self):
        label = (
            "tests.test_sb2_birthing.BirthingAfterWitnessTests."
            "test_gateway_setup_ignores_python_clock_skew"
        )
        plan, attempts = self._build_plan_in_fresh_process(label)

        self.assertEqual(plan["tests"], [label])
        self.assertEqual(plan["database_route"], "DATABASE")
        self.assertEqual(attempts["count"], 0)

    def test_deep_module_method_label_is_supported(self):
        import re
        from pathlib import Path

        from verification.isolation.run_plan import resolve_labels

        label = (
            "verification.isolation.selfcheck_tests.IsolationSelfChecks."
            "test_v5a_data_check_detects_retained_row"
        )
        self.assertEqual(resolve_labels([label]), [label])

        source = Path("scripts/run_integrated_dev.ps1").read_text(
            encoding="utf-8"
        )
        launcher_pattern = re.search(r'\$label -notmatch "([^"]+)"', source)
        if launcher_pattern is None:
            self.fail("launcher label validation regex is missing")
        self.assertRegex(label, launcher_pattern.group(1))

    def test_pyright_pre_run_is_removed_and_qualification_exit_tracks_verdict(self):
        from pathlib import Path

        launcher = Path("scripts/run_integrated_dev.ps1").read_text(encoding="utf-8")
        qualification = Path("scripts/qualify_tu2a.ps1").read_text(encoding="utf-8")

        self.assertNotIn("Invoke-Tu2aPyrightJson", launcher)
        self.assertNotIn("Get-Tu2aPyrightClass", launcher)
        self.assertNotIn("PYRIGHT_PRE_RUN", launcher)
        self.assertNotIn("PYRIGHT_TRIAL", launcher)
        self.assertNotIn("pyrightconfig.tu2a.json", launcher)
        self.assertNotIn("SkipPyrightTrial", launcher + qualification)
        self.assertIn(
            'pyright_pre_run = "pyright: removed by Human decision, 16 Oct"',
            launcher,
        )
        self.assertIn("$overall -eq \"PASS\"", qualification)
        self.assertIn("$overall -eq \"DRY_CHECK\"", qualification)
        self.assertIn("if ($successfulVerdict) {", qualification)

    def test_development_launcher_process_exit_tracks_final_verdict(self):
        from pathlib import Path

        launcher = Path("scripts/run_integrated_dev.ps1").read_text(encoding="utf-8")
        status_block_start = launcher.rfind('if ($finalDevVerdict -eq "STALL")')
        self.assertGreaterEqual(status_block_start, 0)
        status_block = launcher[status_block_start:]

        self.assertIn(
            'if ($runProblems.Count -eq 0 -and $finalDevVerdict -eq "PASS")',
            status_block,
        )
        self.assertIn("$global:LASTEXITCODE = 0", status_block)
        self.assertIn("$global:LASTEXITCODE = 1", status_block)
        self.assertTrue(
            status_block.rstrip().endswith("exit $global:LASTEXITCODE")
        )

    def test_plan_marks_database_forbidden_selection_as_no_database_route(self):
        label = (
            "tests.test_tu1_launch_boundary.LaunchBoundaryTests."
            "test_duplicate_plan_ids_are_refused"
        )
        plan, attempts = self._build_plan_in_fresh_process(label)

        self.assertEqual(plan["database_route"], "NO_DATABASE")
        self.assertEqual(attempts["count"], 0)

    def test_duplicate_plan_ids_are_refused(self):
        with self.assertRaisesRegex(ValueError, "R4"):
            load_plan_from_data({"tests": ["x", "x"]})

    def test_plan_file_transport_supports_500_ids_without_child_test_arguments(self):
        import tempfile
        from pathlib import Path

        from verification.isolation.run_plan import load_plan, write_plan

        identifiers = [f"synthetic.module.Case.test_{index}" for index in range(500)]
        plan = {
            "schema_version": 1,
            "tests": identifiers,
            "database_route": "NO_DATABASE",
        }
        with tempfile.TemporaryDirectory() as temporary:
            plan_path = Path(temporary) / "large-plan.json"
            digest = write_plan(plan, plan_path)
            loaded, loaded_digest = load_plan(plan_path)

        self.assertEqual(loaded["tests"], identifiers)
        self.assertEqual(loaded_digest, digest)

        source = Path("scripts/run_integrated_dev.ps1").read_text(
            encoding="utf-8"
        )
        argument_start = source.index("$testArguments = @(")
        argument_end = source.index("$testRun = Invoke-WatchedPythonAndLog", argument_start)
        child_arguments = source[argument_start:argument_end]
        self.assertNotIn("$Labels", child_arguments)
        self.assertNotIn("$suite", child_arguments)
        self.assertNotIn("planData.tests", child_arguments)
        self.assertIn("$env:INTEVIA_RUN_PLAN_FILE = $resolvedRunPlanFile", source)
        self.assertIn("$env:INTEVIA_RUN_PLAN_SHA256 = $planHash", source)

    def test_more_than_twenty_labels_are_refused_before_label_resolution(self):
        from verification.isolation.run_plan import resolve_labels

        with self.assertRaisesRegex(ValueError, "R3: at most twenty labels"):
            resolve_labels([f"synthetic.module{index}.Case" for index in range(21)])

    def test_failed_plan_can_be_reused_unrotated_without_a_superset(self):
        import json
        import tempfile
        from pathlib import Path

        full_label = "tests.test_tu1_launch_boundary.LaunchBoundaryTests"
        subset_label = full_label + ".test_duplicate_plan_ids_are_refused"
        with tempfile.TemporaryDirectory() as temporary:
            source = (
                "import json\n"
                "from pathlib import Path\n"
                "from verification.isolation.run_plan import build_plan, write_plan, validate_plan_file\n"
                f"root = Path({temporary!r})\n"
                f"full_label = {full_label!r}\n"
                f"subset_label = {subset_label!r}\n"
                "failed_plan = build_plan([full_label])\n"
                "attempt = root / 'attempt_1'\n"
                "write_plan(failed_plan, attempt / 'run-plan.json')\n"
                "(attempt / 'run-record.json').write_text(json.dumps("
                "{'classification': 'TEST_ATTEMPT', 'result': 'FAIL'}), encoding='utf-8')\n"
                "same_path = root / 'same-plan.json'\n"
                "write_plan(build_plan([full_label]), same_path)\n"
                "same, _ = validate_plan_file(same_path)\n"
                "subset_path = root / 'subset-plan.json'\n"
                "write_plan(build_plan([subset_label]), subset_path)\n"
                "subset, _ = validate_plan_file(subset_path)\n"
                "print(json.dumps({'failed': failed_plan['tests'], 'same': same['tests'], "
                "'subset': subset['tests'], 'same_keys': sorted(same)}))\n"
            )
            result = self._run_in_fresh_process(source)

        self.assertEqual(result.returncode, 0, result.stderr)
        response = json.loads(result.stdout)
        self.assertEqual(response["same"], response["failed"])
        self.assertEqual(
            response["subset"],
            ["tests.test_tu1_launch_boundary.LaunchBoundaryTests.test_duplicate_plan_ids_are_refused"],
        )
        self.assertLess(len(response["subset"]), len(response["failed"]))
        self.assertNotIn("rotation_side", response["same_keys"])
        self.assertNotIn("parent_plan_sha256", response["same_keys"])

    def test_pass_run_record_requires_isolation_and_control_evidence(self):
        record = {
            "classification": "TEST_ATTEMPT",
            "result": "PASS",
            "isolation_verdict": "PASS",
            "dev_run_verdict": "PASS",
            "cp0_requirements_met": True,
            "required_controls_passed": True,
            "ordinary_test_verdict": {
                "was_successful": True,
                "errors": 0,
                "failures": 0,
            },
            "tests_planned": 2,
            "tests_executed": 2,
            "tests_not_reached": 0,
            "executed_matches_plan": "FULL",
        }
        self.assertIsNone(validate_run_record_consistency(record))

        invalid = {**record, "isolation_verdict": "NOT ESTABLISHED"}
        with self.assertRaisesRegex(ValueError, "complete successful control evidence"):
            validate_run_record_consistency(invalid)

        invalid = {**record, "result": "FAIL"}
        with self.assertRaisesRegex(ValueError, "contradicts DEV_RUN_VERDICT"):
            validate_run_record_consistency(invalid)

    def test_state_without_run_spec_binding_fields_is_refused(self):
        with self.assertRaisesRegex(ValueError, "R11: state file lacks"):
            validate_run_spec_binding({}, b"specification")

        from pathlib import Path

        source = Path("scripts/integrated_state.ps1").read_text(encoding="utf-8")
        self.assertNotIn("$runSpecBound", source)
        self.assertIn('["run_spec_version"]', source)
        self.assertIn('["run_spec_sha256"]', source)

    def test_run_spec_binding_does_not_require_instruction_file_authority(self):
        specification = b"v1.38 run specification"
        state = {
            "run_spec_version": "v1.38",
            "run_spec_sha256": hashlib.sha256(specification).hexdigest(),
            "allowed_paths": ["scripts/integrated_state.ps1"],
        }
        self.assertNotIn(".github/copilot-instructions.md", state["allowed_paths"])
        self.assertEqual(
            validate_run_spec_binding(state, specification),
            state["run_spec_sha256"],
        )

        from pathlib import Path

        source = Path("scripts/integrated_state.ps1").read_text(encoding="utf-8")
        self.assertNotIn('".github/copilot-instructions.md" -cnotin $allowedPaths', source)

    def test_full_plan_is_accepted(self):
        self.assertEqual(assert_execution(["a", "b"], ["a", "b"], complete=True), "FULL")

    def test_fail_fast_prefix_is_accepted(self):
        self.assertEqual(
            assert_execution(
                ["a", "b"], ["a"], complete=False, stop_id="a"
            ),
            "PREFIX",
        )

    def test_subtest_identifier_resolves_for_r7_without_database(self):
        from django.db import connections
        from verification.isolation.run_plan import (
            _forbid_database_connections,
            _resolve_parent_test_identifier,
        )

        parent_tests = ["suite.Case.test_before", "suite.Case.test_w1", "suite.Case.test_after"]
        decorated_failure = (
            "suite.Case.test_w1 (skew=datetime.timedelta(days=-1, seconds=79200), "
            "function='limit')"
        )
        attempts = {"count": 0}
        with _forbid_database_connections(connections, attempts, "R7"):
            self.assertEqual(
                assert_execution(
                    parent_tests,
                    parent_tests[:2],
                    complete=False,
                    stop_id=decorated_failure,
                ),
                "PREFIX",
            )
            with self.assertRaisesRegex(ValueError, "missing"):
                _resolve_parent_test_identifier(
                    "missing.test (case=1)", parent_tests, "R7"
                )
            with self.assertRaisesRegex(ValueError, "ambiguous"):
                _resolve_parent_test_identifier(
                    "x (case (nested=1)", ["x", "x (case"], "R7"
                )
        self.assertEqual(attempts["count"], 0)

    def test_gapped_execution_is_refused(self):
        with self.assertRaisesRegex(ValueError, "R7"):
            assert_execution(
                ["a", "b", "c"], ["a", "c"], complete=False, stop_id="c"
            )

    def test_full_verification_requires_complete_order(self):
        with self.assertRaisesRegex(ValueError, "R10"):
            verify_kind("FULL", ["a"], ["a", "b"])

    def test_authorized_delta_requires_matching_impact(self):
        verify_kind("DELTA", ["b"], ["a", "b"], delta_authorized=True, impact=["b"])
        with self.assertRaisesRegex(ValueError, "R10"):
            verify_kind("DELTA", ["a"], ["a", "b"], delta_authorized=True, impact=["b"])

    def test_direct_manage_py_route_refusal_is_present(self):
        from pathlib import Path

        source = Path("intevia/test_settings.py").read_text(encoding="utf-8")
        self.assertIn("INTEVIA_S015_TEST_ROUTE", source)

    def test_self_test_refusals_and_acceptances_are_defined(self):
        source = (
            "from verification.isolation.run_plan import self_test\n"
            "raise SystemExit(self_test())\n"
        )
        result = self._run_in_fresh_process(source)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_verification_summary_cross_check(self):
        summary = {
            "plan_sha256": "plan-digest",
            "tests_planned": 3,
            "executed_matches_plan": "FULL",
            "cpz_established": True,
            "cpz_established": True,
        }
        self.assertIsNone(
            validate_verification_summary(summary, "plan-digest", 3)
        )
        refusal_cases = (
            (
                summary,
                None,
                "R7: the runner's resolved test count is unavailable",
            ),
            (
                {**summary, "plan_sha256": "different-plan"},
                3,
                "R7: isolation summary plan digest differs from the validated plan",
            ),
            (
                {**summary, "tests_planned": 2},
                3,
                "R7: summary tests_planned differs from the runner's resolved test count",
            ),
            (
                {**summary, "executed_matches_plan": "PREFIX"},
                3,
                "R7: summary executed_matches_plan is not FULL",
            ),
            (
                {**summary, "cpz_established": False},
                3,
                "R7: CP-Z was not established after the run completed",
            ),
        )
        for invalid_summary, resolved_tests, expected_message in refusal_cases:
            with self.subTest(expected_message=expected_message):
                with self.assertRaises(ValueError) as raised:
                    validate_verification_summary(
                        invalid_summary, "plan-digest", resolved_tests
                    )
                self.assertEqual(str(raised.exception), expected_message)
