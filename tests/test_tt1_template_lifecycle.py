import copy
import json
import os
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from verification.isolation import template


class _FakeOperationalError(Exception):
    pass


class _FakeCursor:
    def __init__(self, catalog_value):
        self.catalog_value = catalog_value

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def execute(self, query, parameters):
        self.query = query
        self.parameters = parameters

    def fetchone(self):
        return None if self.catalog_value is None else (self.catalog_value,)


class _FakeControlConnection:
    def __init__(self, catalog_value):
        self.catalog_value = catalog_value
        self.cursors = []
        self.closed = False

    def cursor(self):
        cursor = _FakeCursor(self.catalog_value)
        self.cursors.append(cursor)
        return cursor

    def close(self):
        self.closed = True


class _FakeDatabase:
    OperationalError = _FakeOperationalError

    def __init__(self, template_name, catalog_value=False, fail_control_call=None):
        self.template_name = template_name
        self.catalog_value = catalog_value
        self.fail_control_call = fail_control_call
        self.calls = []
        self.control_connections = []

    def connect(self, **parameters):
        self.calls.append(parameters)
        if parameters["dbname"] == self.template_name:
            raise _FakeOperationalError("database is not currently accepting connections")
        control_count = sum(call["dbname"] != self.template_name for call in self.calls)
        if control_count == self.fail_control_call:
            raise _FakeOperationalError("control database connection failed")
        control_connection = _FakeControlConnection(self.catalog_value)
        self.control_connections.append(control_connection)
        return control_connection


class TemplateConnectionWitnessTests(unittest.TestCase):
    template_name = "test_intevia_tpl_unit"

    def _run_witness(self, catalog_value=False, fail_control_call=None):
        database = _FakeDatabase(self.template_name, catalog_value, fail_control_call)
        connection = type("FakeConnection", (), {
            "Database": database,
            "get_connection_params": lambda self: {
                "dbname": "base", "password": "unit-test-password",
            },
        })()
        control_record = {"oid": 902, "allow_connections": True, "is_template": False}
        with patch.object(template, "_create_database", return_value=control_record), \
                patch.object(template, "_drop_database_by_oid", return_value=True) as drop_database, \
                patch.object(template, "_attempt_receipt") as receipt:
            result = template._refuse_template_connections(connection, self.template_name, 901)
        return result, database, drop_database, receipt

    def test_refusal_requires_catalog_control_and_operational_error(self):
        witness, database, drop_database, receipt = self._run_witness()

        self.assertTrue(witness["connection_refused"])
        self.assertFalse(witness["connection_refusal_catalog_datallowconn"])
        self.assertTrue(witness["connection_refusal_catalog_connected"])
        self.assertTrue(witness["connection_refusal_control_connected"])
        self.assertIsNone(witness["connection_refusal_sqlstate"])
        self.assertEqual(witness["connection_refusal_message"],
                         "database is not currently accepting connections")
        control_name = database.calls[0]["dbname"]
        self.assertTrue(control_name.startswith("test_intevia_ctl_"))
        self.assertEqual([call["dbname"] for call in database.calls],
                         [control_name, control_name, self.template_name])
        self.assertTrue(all(call["password"] == "unit-test-password" for call in database.calls))
        self.assertEqual(database.control_connections[0].cursors[0].query,
                         "SELECT datallowconn FROM pg_database WHERE oid = %s")
        self.assertEqual(database.control_connections[0].cursors[0].parameters, [901])
        self.assertTrue(database.control_connections[0].closed)
        self.assertTrue(database.control_connections[1].closed)
        drop_database.assert_called_once()
        self.assertEqual(drop_database.call_args.args[1:], (control_name, 902))
        events = [call.args[0] for call in receipt.call_args_list]
        self.assertIn("template_control_database_destroyed", events)
        self.assertIn("template_connection_refusal_result", events)

    def test_template_probe_is_not_attempted_when_catalog_allows_connections(self):
        database = _FakeDatabase(self.template_name, catalog_value=True)
        connection = type("FakeConnection", (), {
            "Database": database,
            "get_connection_params": lambda self: {"dbname": "base", "password": "unit-test-password"},
        })()
        with patch.object(template, "_create_database", return_value={
            "oid": 902, "allow_connections": True, "is_template": False,
        }), patch.object(template, "_drop_database_by_oid", return_value=True), \
                patch.object(template, "_attempt_receipt"):
            with self.assertRaises(template.TemplateSealingRefused):
                template._refuse_template_connections(connection, self.template_name, 901)

        self.assertEqual(len(database.calls), 1)
        self.assertNotEqual(database.calls[0]["dbname"], self.template_name)

    def test_template_probe_is_not_attempted_when_control_connection_fails(self):
        database = _FakeDatabase(self.template_name, fail_control_call=2)
        connection = type("FakeConnection", (), {
            "Database": database,
            "get_connection_params": lambda self: {"dbname": "base", "password": "unit-test-password"},
        })()
        with patch.object(template, "_create_database", return_value={
            "oid": 902, "allow_connections": True, "is_template": False,
        }), patch.object(template, "_drop_database_by_oid", return_value=True), \
                patch.object(template, "_attempt_receipt"):
            with self.assertRaises(_FakeOperationalError):
                template._refuse_template_connections(connection, self.template_name, 901)

        self.assertEqual(len(database.calls), 2)
        self.assertNotEqual(database.calls[-1]["dbname"], self.template_name)


class TemplateLifecycleToolingTests(unittest.TestCase):
    databases = {"default"}

    def test_template_clone_parity_and_receipt_cleanup(self):
        store = os.environ.get("VERIFICATION_TEMPLATE_STORE")
        if not store:
            self.skipTest("requires the TT1 attempt template store")
        from django.apps import apps
        if not apps.ready:
            self.skipTest("requires an initialized Django test runner")

        from django.conf import settings
        from django.db import connection, connections
        from verification.isolation import state
        from verification.isolation.template import (
            cleanup_template,
            create_clone_database,
            prepare_template,
            read_template_receipt,
            record_final_clone_cp0,
            template_receipt_path,
        )
        from verification.ownership import read_receipts

        test_options = settings.DATABASES["default"].setdefault("TEST", {})
        had_charset = "CHARSET" in test_options
        previous_charset = test_options.get("CHARSET")
        test_options["CHARSET"] = "UTF8"
        witness_store = Path(store) / "tooling" / uuid.uuid4().hex
        clone_name = "test_intevia_living_organism_tt1_witness_" + uuid.uuid4().hex[:12]
        clone_alias = "tt1_witness_clone_" + uuid.uuid4().hex[:8]
        receipt = None
        clone_created = False
        clone_digest = None
        parity_recorded = False
        clone_connection = None
        old_state_connection = state.connection
        try:
            receipt, _ = prepare_template(connection, "default", witness_store)
            template_path = template_receipt_path(witness_store, receipt["identity"])
            receipt = read_template_receipt(template_path, receipt["identity"])
            create_clone_database(connection, receipt, clone_name)
            clone_created = True

            clone_settings = copy.deepcopy(connection.settings_dict)
            clone_settings["NAME"] = clone_name
            connections.databases[clone_alias] = clone_settings
            clone_connection = connections[clone_alias]
            state.connection = clone_connection
            clone_snapshot = state.capture()
            violations = state.check_requirements(
                clone_snapshot,
                __import__("intevia.test_postgresql_backend.operations", fromlist=["S015_TRUNCATE_GUARDIANS"]).S015_TRUNCATE_GUARDIANS,
            )
            clone_digest = state.digest(clone_snapshot)
            self.assertFalse(violations)
            self.assertEqual(clone_digest, receipt["fresh_build_cp0_digest"])
            self.assertTrue(receipt["connection_refused"])
            record_final_clone_cp0(template_path, receipt["identity"], clone_digest)
            parity_recorded = True
        finally:
            state.connection = old_state_connection
            close_error = None
            try:
                if clone_connection is not None:
                    clone_connection.close()
            except Exception as exc:
                close_error = exc
            finally:
                if clone_alias in connections:
                    del connections[clone_alias]
                connections.databases.pop(clone_alias, None)
            try:
                if clone_created:
                    connection.creation._destroy_test_db(clone_name, verbosity=0)
            finally:
                try:
                    if receipt is not None:
                        cleanup_template(connection, template_path, receipt["identity"],
                                         allow_incomplete_parity=not parity_recorded)
                finally:
                    if had_charset:
                        test_options["CHARSET"] = previous_charset
                    else:
                        test_options.pop("CHARSET", None)
            if close_error is not None:
                raise close_error

        records, problem = read_receipts(os.environ["VERIFICATION_DB_RECEIPT"], os.environ["VERIFICATION_RUN_NONCE"])
        self.assertIsNone(problem)
        clone_created = [record for record in records if record.get("event") == "clone_created" and record.get("name") == clone_name]
        destroyed = [record for record in records if record.get("event") == "destroyed" and record.get("name") == clone_name]
        self.assertEqual(len(clone_created), 1)
        self.assertEqual(clone_created[0]["clone_oid"], destroyed[0]["oid"])
        self.assertEqual(clone_created[0]["template_oid"], receipt["oid"])
        self.assertTrue(destroyed[0]["confirmed_absent"])
        with open(template_path, encoding="utf-8") as receipt_file:
            destroyed_template = json.load(receipt_file)
        self.assertTrue(destroyed_template["destroyed"]["confirmed_absent"])


if __name__ == "__main__":
    unittest.main()