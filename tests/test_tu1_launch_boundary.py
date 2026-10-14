"""No-database acceptance checks for the TU-1 launch boundary."""

import hashlib

from django.test import SimpleTestCase

from verification.isolation.run_plan import (
    assert_execution,
    is_rotation_basis_record,
    load_plan_from_data,
    rotation,
    rotation_basis_records,
    validate_run_spec_binding,
    validate_unrotated_selection,
    verify_kind,
)


class LaunchBoundaryTests(SimpleTestCase):
    def test_module_labels_expand_before_execution(self):
        from verification.isolation.run_plan import resolve_labels

        self.assertGreater(len(resolve_labels(["tests.test_tu1_launch_boundary"])), 0)

    def test_real_test_label_builds_one_plan_without_database_connections(self):
        from verification.isolation.run_plan import build_plan

        label = (
            "tests.test_sb2_birthing.BirthingAfterWitnessTests."
            "test_gateway_setup_ignores_python_clock_skew"
        )
        attempts = {"count": 0}
        plan = build_plan([label], connection_attempts=attempts)

        self.assertEqual(plan["tests"], [label])
        self.assertEqual(attempts["count"], 0)

    def test_duplicate_plan_ids_are_refused(self):
        with self.assertRaisesRegex(ValueError, "R4"):
            load_plan_from_data({"tests": ["x", "x"]})

    def test_rotation_failure_in_a_preserves_parent_selection(self):
        parent = {"tests": ["a", "b", "c", "d"], "ordered_groups": [["b", "c"]]}
        a = rotation(parent, "b", "A")
        b = rotation(parent, "b", "B")
        self.assertEqual(set(a + b), set(parent["tests"]))

    def test_rotation_failure_in_b_preserves_parent_selection(self):
        parent = {"tests": ["a", "b", "c", "d"], "ordered_groups": [["b", "c"]]}
        a = rotation(parent, "d", "A")
        b = rotation(parent, "d", "B")
        self.assertEqual(set(a + b), set(parent["tests"]))

    def test_rotation_inside_group_keeps_group_whole(self):
        parent = {"tests": ["a", "b", "c", "d"], "ordered_groups": [["b", "c"]]}
        self.assertEqual(rotation(parent, "c", "A"), ["b", "c", "d"])

    def test_split_or_stale_group_is_refused(self):
        parent = {"tests": ["a", "b", "c"], "ordered_groups": [["b", "c"]]}
        with self.assertRaisesRegex(ValueError, "R5"):
            rotation(parent, "missing", "A")

    def test_pre_run_refusal_does_not_create_rotation_basis(self):
        parent_digest = "parent-plan"
        failed_attempt = {
            "classification": "TEST_ATTEMPT",
            "result": "FAIL",
            "parent_plan_sha256": parent_digest,
        }
        refusal = {
            "classification": "PRE_RUN_REFUSAL",
            "result": "REFUSED",
            "parent_plan_sha256": parent_digest,
        }

        self.assertFalse(is_rotation_basis_record(refusal))
        self.assertEqual(
            rotation_basis_records([failed_attempt, refusal], parent_digest),
            [failed_attempt],
        )

    def test_unrotated_same_failed_selection_is_refused(self):
        with self.assertRaisesRegex(
            ValueError,
            r"R5: the latest attempt on this plan failed; launch rotated \(A, then B\) from its record\.",
        ):
            validate_unrotated_selection(
                ["a", "b"],
                {"classification": "TEST_ATTEMPT", "result": "FAIL"},
                ["a", "b"],
                None,
            )

    def test_unrotated_selection_after_pass_is_accepted(self):
        self.assertIsNone(
            validate_unrotated_selection(
                ["a"],
                {"classification": "TEST_ATTEMPT", "result": "PASS"},
                ["a"],
                None,
            )
        )

    def test_new_parent_missing_failed_plan_test_is_refused(self):
        with self.assertRaisesRegex(ValueError, "R5: a new parent plan must contain every test"):
            validate_unrotated_selection(
                ["a"],
                {"classification": "TEST_ATTEMPT", "result": "ERROR"},
                ["a", "b"],
                "expanded plan",
            )

    def test_reasoned_superset_is_accepted_for_new_parent_plan(self):
        reason = "add c for the expanded verification objective"
        self.assertEqual(
            validate_unrotated_selection(
                ["a", "b", "c"],
                {"classification": "TEST_ATTEMPT", "result": "FAIL"},
                ["a", "b"],
                reason,
            ),
            reason,
        )

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

    def test_closure_requires_same_candidate_and_parent_binding(self):
        from verification.isolation.run_plan import validate_closure

        with self.assertRaisesRegex(ValueError, "R5"):
            validate_closure(
                [
                    {"candidate": "candidate-one", "parent": "plan-a"},
                    {"candidate": "candidate-two", "parent": "plan-a"},
                ]
            )

    def test_full_plan_is_accepted(self):
        self.assertEqual(assert_execution(["a", "b"], ["a", "b"], complete=True), "FULL")

    def test_fail_fast_prefix_is_accepted(self):
        self.assertEqual(
            assert_execution(
                ["a", "b"], ["a"], complete=False, stop_id="a"
            ),
            "PREFIX",
        )

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

    def test_verification_cannot_rotate(self):
        with self.assertRaisesRegex(ValueError, "R10"):
            verify_kind("FULL", ["a"], ["a"], rotation_input=True)

    def test_direct_manage_py_route_refusal_is_present(self):
        from pathlib import Path

        source = Path("intevia/test_settings.py").read_text(encoding="utf-8")
        self.assertIn("INTEVIA_S015_TEST_ROUTE", source)

    def test_self_test_refusals_and_acceptances_are_defined(self):
        from verification.isolation.run_plan import self_test

        self.assertEqual(self_test(), 0)
