import unittest

from verification.isolation.template import (
    TemplateIdentityUnavailable,
    TemplateReceiptInvalid,
    canonical_listing,
    module_input_lines,
    python_runtime_line,
    schema_setting_lines,
    template_database_name,
    validate_template_receipt,
)


class CanonicalTemplateIdentityTests(unittest.TestCase):
    def test_separate_computations_produce_identical_listing_and_identity(self):
        lines = [
            "head=0040_s2b_role_actor_nullable_state",
            "migration:core/migrations/0001_initial.py=abc123",
            'setting:USE_TZ=true',
            "software:django=5.2.15",
        ]

        first_listing, first_identity = canonical_listing(lines)
        second_listing, second_identity = canonical_listing(list(lines))

        self.assertEqual(first_listing, second_listing)
        self.assertEqual(first_identity, second_identity)
        self.assertTrue(first_listing.endswith("\n"))
        self.assertNotIn("\r", first_listing)

    def test_one_input_change_changes_only_its_line(self):
        lines = [
            "head=0040_s2b_role_actor_nullable_state",
            "migration:core/migrations/0001_initial.py=abc123",
            'setting:USE_TZ=true',
            "software:django=5.2.15",
        ]
        changed_lines = [line.replace("setting:USE_TZ=true", "setting:USE_TZ=false") for line in lines]

        original_listing, original_identity = canonical_listing(lines)
        changed_listing, changed_identity = canonical_listing(changed_lines)
        original_by_key = {line.partition("=")[0]: line for line in original_listing.splitlines()}
        changed_by_key = {line.partition("=")[0]: line for line in changed_listing.splitlines()}
        changed_keys = {key for key in original_by_key if original_by_key[key] != changed_by_key[key]}

        self.assertEqual(changed_keys, {"setting:USE_TZ"})
        self.assertNotEqual(original_identity, changed_identity)

    def test_duplicate_key_makes_identity_unavailable(self):
        with self.assertRaisesRegex(TemplateIdentityUnavailable, "duplicate identity key"):
            canonical_listing(("head=one", "head=two"))

    def test_builtin_import_uses_builtin_marker_and_interpreter_line(self):
        lines = module_input_lines(("sys",))

        self.assertIn("import:sys=builtin", lines)
        self.assertTrue(any(line.startswith("software:python=") for line in lines))

    def test_interpreter_string_change_changes_identity(self):
        first_interpreter = python_runtime_line("CPython", (3, 12, 10, "final", 0), "3.12.10 (build A)")
        second_interpreter = python_runtime_line("CPython", (3, 12, 11, "final", 0), "3.12.11 (build B)")

        _, first_identity = canonical_listing(("import:sys=builtin", first_interpreter))
        _, second_identity = canonical_listing(("import:sys=builtin", second_interpreter))

        self.assertNotEqual(first_identity, second_identity)

    def test_attempt_test_name_does_not_change_template_identity(self):
        common = {
            "installed_apps": ("django.contrib.auth", "core"),
            "auth_user_model": "auth.User",
            "default_auto_field": "django.db.models.BigAutoField",
            "use_tz": True,
            "time_zone": "UTC",
        }
        first_settings = schema_setting_lines(**common, test_options={"NAME": "attempt_1", "CHARSET": None, "COLLATION": None})
        second_settings = schema_setting_lines(**common, test_options={"NAME": "attempt_2", "CHARSET": None, "COLLATION": None})

        _, first_identity = canonical_listing(["head=0040", *first_settings])
        _, second_identity = canonical_listing(["head=0040", *second_settings])

        self.assertEqual(first_settings, second_settings)
        self.assertEqual(first_identity, second_identity)
        self.assertIn("setting:DATABASES.default.TEST.CHARSET=null", first_settings)
        self.assertIn("setting:DATABASES.default.TEST.COLLATION=null", first_settings)
        self.assertFalse(any("TEST.NAME" in line for line in first_settings))

    def test_bounded_template_name_retains_full_identity_in_receipt(self):
        listing, identity = canonical_listing(("head=0040", "import:sys=builtin"))
        receipt = {"identity": identity, "listing": listing, "name": template_database_name(identity), "oid": 123}

        self.assertEqual(len(receipt["name"]), 63)
        self.assertTrue(validate_template_receipt(receipt, identity))

    def test_same_name_prefix_with_different_full_identity_is_refused(self):
        listing, first_identity = canonical_listing(("head=0040",))
        receipt = {
            "identity": first_identity,
            "listing": listing,
            "name": template_database_name(first_identity),
            "oid": 123,
        }
        collision_suffix = "0" if first_identity[-1] != "0" else "1"
        collision_identity = first_identity[:46] + collision_suffix * 18

        self.assertEqual(first_identity[:46], collision_identity[:46])
        self.assertNotEqual(first_identity, collision_identity)
        with self.assertRaisesRegex(TemplateReceiptInvalid, "different full identity"):
            validate_template_receipt(receipt, collision_identity)


if __name__ == "__main__":
    unittest.main()