"""Once-per-route SR migration rehearsal on its own receipt-bound clone."""
from importlib import import_module

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase

from tests.s015_0021_support import current_core_leaf, migrate_to_target_with_restore
from tests.test_sr_office_guards import HEAD_0051, HEAD_0052, SROfficeFixture


class SRRoundTrip(SROfficeFixture, TransactionTestCase):
    """Both reversals run on historical schemas and restore all received application leaves."""

    ISOLATION_FULL_CAPTURE = True

    def test_clean_and_occupied_reversal(self):
        """Reapply clean SR migrations and preserve the occupied office on a refused reversal."""
        migration = import_module("core.migrations.0052_sr_office_guard_widening")
        safeguard = import_module("core.migrations.0053_sr_office_safeguards")
        received_ledger = set(MigrationRecorder(connection).applied_migrations())
        at_head = self._migration_facts()
        self.assertEqual(at_head["head"], current_core_leaf(connection)[0][1])
        migrate_to_target_with_restore(self, ("core", "0053_sr_office_safeguards"))
        restored_sr = self._migration_facts()
        self.assertEqual(restored_sr["definition_insert"], safeguard.NEW_SR_DEFINITION_INSERT_GUARD_BODY)
        self.assertEqual(restored_sr["attribution"], safeguard.NEW_S2B_GUARD_OFFICE_ATTRIBUTION_BODY)
        sr_triggers = restored_sr["triggers"]
        self.assertGreater(at_head["triggers"], sr_triggers,
                           msg="Reversing the C2 migration must remove its extra coverage and qualification guards.")
        MigrationExecutor(connection).migrate([("core", HEAD_0052)])
        at_0052 = self._migration_facts()
        self.assertEqual(at_0052["attribution"], migration.NEW_S2B_GUARD_OFFICE_ATTRIBUTION_BODY, msg="After reversing to 0052, the attribution safeguard should be the exact 0052 body; it differs.")
        self.assertEqual(at_0052["definition_insert"], safeguard.BODIES["sr_definition_insert_guard"][0], msg="After reversing to 0052, the definition-insert safeguard should be its exact earlier body; it differs.")
        self.assertEqual(at_0052["definition_update"], safeguard.BODIES["sr_definition_update_guard"][0], msg="After reversing to 0052, the definition-update safeguard should be its exact earlier body; it differs.")
        self.assertEqual(at_0052["triggers"], sr_triggers, msg="Reversing to 0052 should preserve the observed trigger count; the count changed.")
        MigrationExecutor(connection).migrate([("core", HEAD_0051)])
        at_0051 = self._migration_facts()
        self.assertEqual(at_0051["head"], HEAD_0051, msg="After reversing to 0051, the recorded migration head should be 0051; it differs.")
        self.assertEqual(at_0051["attribution"], migration.PREVIOUS_S2B_GUARD_OFFICE_ATTRIBUTION_BODY, msg="After reversing to 0051, the attribution safeguard should be its exact pre-0052 body; it differs.")
        self.assertEqual(at_0051["consent"], migration.PREVIOUS_S2B_GUARD_ASSIGN_CONSENT_BODY, msg="After reversing to 0051, the consent safeguard should be its exact pre-0052 body; it differs.")
        self.assertEqual(at_0051["triggers"], sr_triggers, msg="Reversing to 0051 should preserve the observed trigger count; the count changed.")
        self.restore_received_migrations()
        again = self._migration_facts()
        self.assertEqual(again, at_head, msg="Restoring the received migrations should restore every compared body, head and trigger count; the recorded facts differ.")

        self._occupy_office()
        migrate_to_target_with_restore(self, ("core", "0053_sr_office_safeguards"))
        at_0053 = self._migration_facts()
        self.assertEqual(at_0053["head"], "0053_sr_office_safeguards", msg="The populated reversal checks must start at 0053.")
        before = self._state()
        with self.assertRaises(RuntimeError, msg="The new migration should refuse rollback while an office remains; it allowed rollback.") as refused:
            MigrationExecutor(connection).migrate([("core", HEAD_0051)])
        message = str(refused.exception)
        self.assertIn("SR 0053 reverse refused", message, msg="Rollback with an occupied office should be refused by 0053 itself; its refusal was not reported.")
        self.assertIn("1 non-Coordinator office assignment(s)", message, msg="The 0053 rollback refusal should identify the remaining non-Coordinator assignment; it did not.")
        with connection.schema_editor(atomic=True) as editor:
            with self.assertRaisesRegex(RuntimeError, "1 non-Coordinator office assignment") as direct:
                safeguard.restore_guards(None, editor)
        self.assertIn("SR 0053 reverse refused", str(direct.exception))
        self._assert_0052_populated_refusal(migration)
        self.assertEqual(self._migration_facts(), at_0053, msg="Refused rollback attempts should leave all compared migration facts unchanged; they changed.")
        self.assertEqual(self._state(), before, msg="Refused rollback attempts should leave the compared office records unchanged; they changed.")
        self.restore_received_migrations()
        self.assertEqual(self._migration_facts(), at_head)
        self.assertEqual(set(MigrationRecorder(connection).applied_migrations()), received_ledger)
