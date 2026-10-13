import uuid
import unittest
from unittest import mock, skipUnless

from django.contrib.auth.models import Group
from django.core.management.color import no_style
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test import SimpleTestCase, TransactionTestCase

from core.models import GovernedRule, Role
from intevia.test_postgresql_backend.operations import (
    DatabaseOperations,
    S015SequenceDisciplineError,
    S015_MIGRATION_SEEDS,
    S015_TEST_ROUTE,
    S015_TRUNCATE_GUARDIANS,
)
import intevia.test_postgresql_backend.operations as sequence_operations
from tests.sbg_route_support import prepare_governance_route
from verification.isolation import state


def _read_sequence(sequence_name):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT last_value, is_called FROM %s"
            % connection.ops.quote_name(sequence_name)
        )
        return cursor.fetchone()


def _read_sequence_start(sequence_name):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT seqstart FROM pg_sequence WHERE seqrelid = %s::regclass",
            [sequence_name],
        )
        return cursor.fetchone()[0]


def _next_sequence_value(sequence_state):
    last_value, is_called = sequence_state
    return last_value + 1 if is_called else last_value


def _wind_sequence(table_name, next_value):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT setval(pg_get_serial_sequence(%s, %s), %s, false)",
            [table_name, "id", next_value],
        )


def _reset_sequences_for_models(*models):
    statements = connection.ops.sequence_reset_sql(no_style(), models)
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)


def _insert_h64_explicit_role(apps, schema_editor):
    role_model = apps.get_model("core", "Role")
    role_id = _next_sequence_value(_read_sequence("core_role_id_seq"))
    role_model.objects.create(
        id=role_id,
        name="H64 explicit-id restoration witness",
        description="Temporary sequence discipline witness",
    )


class _CapturingTestResult(unittest.TestResult):
    def __init__(self):
        super().__init__()
        self.captured_errors = []

    def addError(self, test, err):
        self.captured_errors.append((test, err[1]))
        super().addError(test, err)


class S015LiveBackendAssertionTests(SimpleTestCase):
    def test_live_connection_uses_the_custom_postgresql_backend(self):
        self.assertEqual(connection.vendor, "postgresql")
        self.assertEqual(
            type(connection.ops).__module__,
            "intevia.test_postgresql_backend.operations",
        )


class S015TestBackendQualificationTests(SimpleTestCase):
    def test_backend_refuses_connected_database_name_mismatch(self):
        fake_connection = mock.MagicMock()
        fake_connection.settings_dict = {
            "S015_TEST_ROUTE": S015_TEST_ROUTE,
            "NAME": "test_intevia_living_organism_expected",
        }
        fake_cursor = fake_connection.cursor.return_value.__enter__.return_value
        fake_cursor.fetchone.return_value = (
            "test_intevia_living_organism_unqualified",
        )

        operations = DatabaseOperations(fake_connection)

        with self.assertRaisesMessage(
            ImproperlyConfigured,
            "S015 test lifecycle backend refuses the connected database",
        ):
            operations._qualified_database_name()


@skipUnless(connection.vendor == "postgresql", "PostgreSQL lifecycle control")
class S015PostgreSQLFlushLifecycleTests(TransactionTestCase):
    reset_sequences = True

    def guardian_states(self):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT relation.relname, guardian.tgname, guardian.tgenabled
                FROM pg_trigger guardian
                JOIN pg_class relation ON relation.oid = guardian.tgrelid
                WHERE guardian.tgname = ANY(%s)
                ORDER BY relation.relname
                """,
                [list(S015_TRUNCATE_GUARDIANS.values())],
            )
            return cursor.fetchall()

    def test_flush_restores_every_s015_truncate_guardian(self):
        call_command("flush", interactive=False, verbosity=0)

        states = self.guardian_states()

        self.assertEqual(len(states), len(S015_TRUNCATE_GUARDIANS))
        self.assertTrue(all(enabled == "O" for _, _, enabled in states))

    def test_direct_mutations_remain_refused_outside_flush(self):
        principal_uuid = str(uuid.uuid4())
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO core_authorityprincipal (
                    principal_uuid,
                    canonical_governed_source_id,
                    governed_source_namespace,
                    display_label,
                    bootstrap_invocation_fingerprint,
                    recorded_at
                ) VALUES (%s, %s, %s, %s, NULL, CURRENT_TIMESTAMP)
                RETURNING id
                """,
                [
                    principal_uuid,
                    f"urn:intevia:test:{principal_uuid}",
                    "S015_TEST_LIFECYCLE",
                    "Lifecycle guardian control",
                ],
            )
            principal_id = cursor.fetchone()[0]

        guarded_statements = (
            (
                "UPDATE core_authorityprincipal "
                "SET canonical_governed_source_id = %s WHERE id = %s",
                [f"urn:intevia:test:changed:{principal_uuid}", principal_id],
            ),
            ("DELETE FROM core_authorityprincipal WHERE id = %s", [principal_id]),
            ("TRUNCATE TABLE core_authorityprincipal CASCADE", []),
        )
        for sql, parameters in guarded_statements:
            with self.assertRaises(IntegrityError), transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(sql, parameters)


@skipUnless(connection.vendor == "postgresql", "PostgreSQL sequence discipline")
class S015SequenceDisciplineTests(TransactionTestCase):
    reset_sequences = True

    def _run_inner_test(self, test_case):
        result = _CapturingTestResult()
        unittest.TestSuite([test_case]).run(result)
        return result

    def _assert_clean_rule_counter(self):
        self.assertEqual(GovernedRule.objects.count(), 3)
        highest_rule_id = GovernedRule.objects.order_by("-id").values_list(
            "id", flat=True
        ).first()
        self.assertEqual(
            _read_sequence("core_governedrule_id_seq"),
            (highest_rule_id, True),
        )

    def _assert_sequence_discipline_error_in_chain(self, error, kind):
        pending = [error.__cause__, error.__context__]
        seen = set()
        while pending:
            chained_error = pending.pop()
            if chained_error is None or id(chained_error) in seen:
                continue
            seen.add(id(chained_error))
            if (
                isinstance(chained_error, S015SequenceDisciplineError)
                and chained_error.kind == kind
            ):
                return chained_error
            pending.extend((chained_error.__cause__, chained_error.__context__))
        self.fail(
            "inner flush error chain omitted S015SequenceDisciplineError "
            "with kind %r" % kind
        )

    def test_w_h64_1_by_name_reset_positions_populated_and_empty_tables(self):
        group_sequence_start = _read_sequence_start("auth_group_id_seq")
        TransactionTestCase._reset_sequences("default")
        self.assertEqual(GovernedRule.objects.count(), 3)
        highest_rule_id = GovernedRule.objects.order_by("-id").values_list(
            "id", flat=True
        ).first()
        self.assertEqual(
            _read_sequence("core_governedrule_id_seq"),
            (highest_rule_id, True),
        )
        self.assertEqual(Group.objects.count(), 0)
        empty_group_sequence = _read_sequence("auth_group_id_seq")
        self.assertEqual(empty_group_sequence, (group_sequence_start, False))

        route = prepare_governance_route(seat_founder=False)
        created_rule = GovernedRule.objects.get(
            rule_uuid=route.rule_uuid,
            version=1,
        )
        self.assertEqual(created_rule.id, highest_rule_id + 1)

        generated_group = Group.objects.create(name="H64 empty sequence witness")
        self.assertEqual(
            generated_group.id,
            _next_sequence_value(empty_group_sequence),
        )

    def test_w_h64_2a_reports_preexisting_damage_and_recovers_after_inner_run(self):
        class InnerDamageTest(TransactionTestCase):
            reset_sequences = True

            def test_winds_the_governed_rule_sequence(self):
                self.highest_rule_id = GovernedRule.objects.order_by(
                    "-id"
                ).values_list("id", flat=True).first()
                self.damage_next_value = self.highest_rule_id
                _wind_sequence("core_governedrule", self.damage_next_value)
                self.damage_next_value = _next_sequence_value(
                    _read_sequence("core_governedrule_id_seq")
                )
                self.assertEqual(self.damage_next_value, self.highest_rule_id)

        try:
            inner_test = InnerDamageTest("test_winds_the_governed_rule_sequence")
            result = self._run_inner_test(inner_test)
        finally:
            _reset_sequences_for_models(GovernedRule)

        self.assertEqual(len(result.errors), 1)
        self.assertEqual(len(result.captured_errors), 1)
        inner_test_id = InnerDamageTest(
            "test_winds_the_governed_rule_sequence"
        ).id()
        self.assertEqual(result.errors[0][0].id(), inner_test_id)
        inner_test, error = result.captured_errors[0]
        self.assertEqual(inner_test.id(), inner_test_id)
        discipline_error = self._assert_sequence_discipline_error_in_chain(
            error,
            "before_teardown",
        )
        message = str(discipline_error)
        expected_finding = "core_governedrule.id next %s, highest %s" % (
            inner_test.damage_next_value,
            inner_test.highest_rule_id,
        )
        self.assertIn(
            "S015 sequence discipline: observed before the teardown reset "
            "of this test: " + expected_finding,
            message,
        )
        self.assertIn(message, result.errors[0][1])
        self._assert_clean_rule_counter()

    def test_w_h64_2b_prioritizes_failed_restoration_without_claiming_clean_state(self):
        class InnerRestoreDamageTest(TransactionTestCase):
            reset_sequences = True

            def test_winds_the_governed_rule_sequence(self):
                self.highest_rule_id = GovernedRule.objects.order_by(
                    "-id"
                ).values_list("id", flat=True).first()
                self.governed_rule_next_value = self.highest_rule_id
                _wind_sequence(
                    "core_governedrule",
                    self.governed_rule_next_value,
                )
                self.governed_rule_next_value = _next_sequence_value(
                    _read_sequence("core_governedrule_id_seq")
                )
                self.assertEqual(
                    self.governed_rule_next_value,
                    self.highest_rule_id,
                )

        restoration_observations = []

        def record_explicit_role_restorer(apps, schema_editor):
            role_model = apps.get_model("core", "Role")
            next_role_value = _next_sequence_value(
                _read_sequence("core_role_id_seq")
            )
            role_id = next_role_value
            role_model.objects.create(
                id=role_id,
                name="H64 explicit-id restoration witness",
                description="Temporary sequence discipline witness",
            )
            restoration_observations.append(
                {"id": role_id, "next_value": next_role_value}
            )

        augmented_seeds = S015_MIGRATION_SEEDS + (
            (__name__, "_insert_h64_explicit_role"),
        )
        try:
            with mock.patch.object(
                sequence_operations,
                "S015_MIGRATION_SEEDS",
                augmented_seeds,
            ), mock.patch(
                "%s._insert_h64_explicit_role" % __name__,
                new=record_explicit_role_restorer,
            ):
                result = self._run_inner_test(
                    InnerRestoreDamageTest("test_winds_the_governed_rule_sequence")
                )
        finally:
            Role.objects.filter(
                name="H64 explicit-id restoration witness"
            ).delete()
            _reset_sequences_for_models(GovernedRule, Role)

        self.assertEqual(len(result.errors), 1)
        self.assertEqual(len(result.captured_errors), 1)
        inner_test_id = InnerRestoreDamageTest(
            "test_winds_the_governed_rule_sequence"
        ).id()
        self.assertEqual(result.errors[0][0].id(), inner_test_id)
        inner_test, error = result.captured_errors[0]
        self.assertEqual(inner_test.id(), inner_test_id)
        discipline_error = self._assert_sequence_discipline_error_in_chain(
            error,
            "after_restore",
        )
        self.assertEqual(
            {finding["table"] for finding in discipline_error.earlier},
            {"core_governedrule"},
        )
        self.assertEqual(
            {finding["table"] for finding in discipline_error.findings},
            {"core_role"},
        )
        expected_governed_rule_finding = (
            "core_governedrule.id next %s, highest %s"
            % (
                inner_test.governed_rule_next_value,
                inner_test.highest_rule_id,
            )
        )
        message = str(discipline_error)
        self.assertIn(
            "S015 sequence discipline: observed after the seed restore of this reset:",
            message,
        )
        self.assertEqual(len(restoration_observations), 1)
        restored_role = restoration_observations[0]
        self.assertIn(expected_governed_rule_finding, message)
        expected_role_finding = "core_role.id next %s, highest %s" % (
            restored_role["next_value"],
            restored_role["id"],
        )
        self.assertIn(expected_role_finding, message)
        self.assertIn("database is NOT confirmed clean", message)
        self.assertNotIn("post-check passed", message)
        self.assertIn(message, result.errors[0][1])
        self._assert_clean_rule_counter()
        self.assertEqual(Role.objects.count(), 3)
        highest_role_id = Role.objects.order_by("-id").values_list(
            "id", flat=True
        ).first()
        self.assertEqual(
            _read_sequence("core_role_id_seq"),
            (highest_role_id, True),
        )

    def test_w_h64_2c_rejects_flush_inside_outer_transaction_before_sql(self):
        sentinel = Group.objects.create(name="H64 flush precondition sentinel")

        with transaction.atomic():
            with self.assertRaisesMessage(
                ImproperlyConfigured,
                "S015 reset must not run inside an open transaction",
            ):
                connection.ops.execute_sql_flush(["DELETE FROM auth_group"])
            self.assertTrue(Group.objects.filter(pk=sentinel.pk).exists())

    def test_w_h64_3_sequence_observation_does_not_consume_values(self):
        reference = state.capture()
        try:
            highest_rule_id = GovernedRule.objects.order_by("-id").values_list(
                "id", flat=True
            ).first()
            _wind_sequence("core_governedrule", highest_rule_id)
            damaged_before = _read_sequence("core_governedrule_id_seq")
            backend_findings = connection.ops._sequence_discipline_findings()
            captured = state.capture()
            damaged_after = _read_sequence("core_governedrule_id_seq")

            self.assertEqual(damaged_before, damaged_after)
            self.assertTrue(
                any(
                    finding["table"] == "core_governedrule"
                    and finding["status"] == "BEHIND"
                    for finding in backend_findings
                )
            )
            requirements = state.check_requirements(
                captured, S015_TRUNCATE_GUARDIANS
            )
            self.assertTrue(
                any(
                    violation.startswith("REQ-S1 core_governedrule.id")
                    for violation in requirements
                )
            )
            data_violations, _protection_violations = state.compare(
                reference, captured
            )
            self.assertTrue(
                any(
                    violation.startswith("REQ-S1 core_governedrule.id")
                    for violation in data_violations
                )
            )
            without_observation = {
                key: value
                for key, value in captured.items()
                if key != "sequence_discipline"
            }
            self.assertEqual(
                state.digest(captured),
                state.digest(without_observation),
            )
        finally:
            _reset_sequences_for_models(GovernedRule)