import copy
import json
import os
import tempfile
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


class _FakeSqlCursor:
    def __init__(self, statements):
        self.statements = statements

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def execute(self, statement):
        self.statements.append(statement)


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
    def setUp(self):
        super().setUp()
        tag_environment = patch.dict(os.environ, {"INTEVIA_DATABASE_TAG": "tu1b"})
        tag_environment.start()
        self.addCleanup(tag_environment.stop)

    template_name = "test_intevia_tpl_me_" + "0" * 43

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

    def test_drop_refusal_restores_connection_access_without_dropping(self):
        name = "test_intevia_owned_template"
        statements = []
        cursor = _FakeSqlCursor(statements)
        connection = type("FakeConnection", (), {
            "creation": type("FakeCreation", (), {"_nodb_cursor": lambda self: cursor})(),
            "ops": type("FakeOperations", (), {"quote_name": staticmethod(lambda value: value)})(),
        })()
        before_close = {
            "oid": 901, "allow_connections": True, "is_template": True, "active_connections": 0,
        }
        active_after_close = {
            "oid": 901, "allow_connections": False, "is_template": True, "active_connections": 1,
        }

        with patch.object(
            template, "_database_record", side_effect=[before_close, active_after_close]
        ), self.assertRaisesRegex(template.TemplateReceiptInvalid, "active sessions"):
            template._drop_database_by_oid(connection, name, 901, clear_is_template=True)

        self.assertIn("ALTER DATABASE %s WITH ALLOW_CONNECTIONS false" % name, statements)
        self.assertIn("ALTER DATABASE %s WITH ALLOW_CONNECTIONS true" % name, statements)
        self.assertFalse(any(statement.startswith("DROP DATABASE") for statement in statements))
        self.assertFalse(any("IS_TEMPLATE false" in statement for statement in statements))


class DatabaseTagRegisterTests(unittest.TestCase):
    def test_binding_is_idempotent_and_conflicts_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "database-tag-register.jsonl"
            first = template.register_database_tag(path, "tu1b", "PKTB_TU1B")
            second = template.register_database_tag(path, "tu1b", "PKTB_TU1B")

            self.assertEqual(first["status"], "BOUND")
            self.assertEqual(second["status"], "EXISTING")
            self.assertEqual(first["sha256"], second["sha256"])
            self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 1)
            with self.assertRaisesRegex(RuntimeError, "already bound"):
                template.register_database_tag(path, "tu1b", "PKTB_OTHER")
            with self.assertRaisesRegex(RuntimeError, "already bound"):
                template.register_database_tag(path, "tu2b", "PKTB_TU1B")

    def test_register_tampering_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "database-tag-register.jsonl"
            template.register_database_tag(path, "tu1b", "PKTB_TU1B")
            path.write_text(
                path.read_text(encoding="utf-8").replace("PKTB_TU1B", "PKTB_TU2B"),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(template.TemplateReceiptInvalid, "hash chain"):
                template.register_database_tag(path, "tu1b", "PKTB_TU1B")


class TemplateBuildRecoveryTests(unittest.TestCase):
    def test_intent_without_database_recovers_before_attempt_receipt_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "PKTB_TU1B"
            intent_path = root / "ME_DEV" / "attempt_1" / "template-store" / "template-build-intents" / (
                "a" * 32 + ".json"
            )
            intent_path.parent.mkdir(parents=True)
            intent = {
                "version": 1,
                "namespace": "me",
                "database_tag": "tu1b",
                "evidence_root": "PKTB_TU1B",
                "run_nonce": "a" * 32,
                "attempt_receipt_path": str(root / "ME_DEV" / "attempt_1" / "database-receipts.jsonl"),
                "bootstrap_name": "test_intevia_boot_tu1b_" + "a" * 32,
                "database_oid": None,
                "target_name": None,
                "identity": None,
                "receipt_path": None,
                "candidate_binding": "b" * 64,
                "stage": "INTENT_WRITTEN",
            }
            intent_path.write_text(json.dumps(intent), encoding="utf-8")

            with patch.dict(os.environ, {
                "INTEVIA_DATABASE_TAG": "tu1b",
                "INTEVIA_EVIDENCE_ROOT": "PKTB_TU1B",
                "VERIFICATION_TEMPLATE_NAMESPACE": "me",
            }), patch.object(template, "_database_record", return_value=None):
                recovered = template.recover_template_builds(object(), root)

            self.assertEqual(len(recovered), 1)
            self.assertEqual(recovered[0]["stage"], "RECOVERED_ABSENT")
            self.assertEqual(json.loads(intent_path.read_text(encoding="utf-8"))["stage"], "RECOVERED_ABSENT")

    def test_owned_incomplete_build_is_removed_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "PKTB_TU1B"
            attempt_directory = root / "ME_DEV" / "attempt_1"
            intent_path = attempt_directory / "template-store" / "template-build-intents" / (
                "a" * 32 + ".json"
            )
            intent_path.parent.mkdir(parents=True)
            attempt_directory.mkdir(parents=True, exist_ok=True)
            bootstrap_name = "test_intevia_boot_tu1b_" + "a" * 32
            attempt_path = attempt_directory / "database-receipts.jsonl"
            attempt_path.write_text("\n".join(json.dumps(record) for record in (
                {
                    "event": "template_create_attempt",
                    "name": bootstrap_name,
                    "unit_tag": "tu1b",
                    "evidence_root": "PKTB_TU1B",
                    "run_nonce": "a" * 32,
                },
                {
                    "event": "template_database_created",
                    "name": bootstrap_name,
                    "oid": 902,
                    "run_nonce": "a" * 32,
                },
            )) + "\n", encoding="utf-8")
            intent_path.write_text(json.dumps({
                "version": 1,
                "namespace": "me",
                "database_tag": "tu1b",
                "evidence_root": "PKTB_TU1B",
                "run_nonce": "a" * 32,
                "attempt_receipt_path": str(attempt_path),
                "bootstrap_name": bootstrap_name,
                "database_oid": 902,
                "target_name": None,
                "identity": None,
                "receipt_path": None,
                "candidate_binding": "b" * 64,
                "stage": "BOOTSTRAP_CREATED",
            }), encoding="utf-8")
            database = {"oid": 902}

            with patch.dict(os.environ, {
                "INTEVIA_DATABASE_TAG": "tu1b",
                "INTEVIA_EVIDENCE_ROOT": "PKTB_TU1B",
                "VERIFICATION_TEMPLATE_NAMESPACE": "me",
            }), patch.object(template, "_database_record", return_value=database) as lookup, \
                    patch.object(template, "_drop_database_by_oid", return_value=True) as drop, \
                    patch.object(template, "_attempt_receipt"):
                connection = object()
                first = template.recover_template_builds(connection, root)
                completed_bytes = intent_path.read_bytes()
                second = template.recover_template_builds(connection, root)

            drop.assert_called_once_with(connection, bootstrap_name, 902, clear_is_template=True)
            self.assertEqual(first[0]["stage"], "RECOVERED_ABSENT")
            self.assertEqual(second[0]["stage"], "RECOVERED_ABSENT")
            self.assertEqual(intent_path.read_bytes(), completed_bytes)
            self.assertEqual(lookup.call_count, 1)

    def test_interrupted_transition_boundaries_recover_once(self):
        for transition in (
            "BOOTSTRAP_CREATED", "MIGRATED", "RENAME_PENDING", "RENAMED",
            "SEALED", "TEMPLATE_FLAGGED", "TEMPLATE_FLAG_UNAVAILABLE",
        ):
            with self.subTest(transition=transition), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "PKTB_TU1B"
                attempt_directory = root / "ME_DEV" / "attempt_1"
                store = attempt_directory / "template-store"
                nonce = "a" * 31 + str(len(transition) % 10)
                bootstrap_name = "test_intevia_boot_tu1b_" + nonce
                identity = "b" * 64
                target_name = template._template_database_name(identity, "me", "tu1b")
                attempt_path = attempt_directory / "database-receipts.jsonl"
                intent_path = store / "template-build-intents" / (nonce + ".json")
                intent_path.parent.mkdir(parents=True)
                attempt_path.parent.mkdir(parents=True, exist_ok=True)
                record_names = [bootstrap_name]
                if transition in {"RENAMED", "SEALED", "TEMPLATE_FLAGGED", "TEMPLATE_FLAG_UNAVAILABLE"}:
                    record_names.append(target_name)
                attempt_records = [
                    {
                        "event": "template_create_attempt", "name": bootstrap_name,
                        "unit_tag": "tu1b", "evidence_root": "PKTB_TU1B", "run_nonce": nonce,
                    },
                    {
                        "event": "template_database_created", "name": bootstrap_name,
                        "oid": 902, "run_nonce": nonce,
                    },
                ]
                if target_name in record_names:
                    attempt_records.append({
                        "event": "template_database_renamed", "name": target_name,
                        "oid": 902, "run_nonce": nonce,
                    })
                attempt_path.write_text(
                    "\n".join(json.dumps(record) for record in attempt_records) + "\n",
                    encoding="utf-8",
                )
                intent = {
                    "version": 1,
                    "namespace": "me",
                    "database_tag": "tu1b",
                    "evidence_root": "PKTB_TU1B",
                    "run_nonce": nonce,
                    "attempt_receipt_path": str(attempt_path),
                    "bootstrap_name": bootstrap_name,
                    "database_oid": 902,
                    "target_name": target_name if transition != "BOOTSTRAP_CREATED" else None,
                    "identity": identity if transition != "BOOTSTRAP_CREATED" else None,
                    "receipt_path": str(store / (target_name + ".json"))
                    if transition != "BOOTSTRAP_CREATED" else None,
                    "candidate_binding": "c" * 64,
                    "stage": "INTENT_WRITTEN",
                }
                template._write_build_intent(intent_path, intent)

                def interrupt_after_persisted_transition(*_):
                    raise RuntimeError("simulated interruption")

                with self.assertRaisesRegex(RuntimeError, "simulated interruption"):
                    template._set_build_stage(
                        intent_path, intent, transition, interrupt_after_persisted_transition
                    )

                database_name = (
                    target_name if target_name in record_names else bootstrap_name
                )
                database = {
                    "oid": 902,
                    "allow_connections": transition not in {
                        "SEALED", "TEMPLATE_FLAGGED", "TEMPLATE_FLAG_UNAVAILABLE",
                    },
                    "is_template": transition == "TEMPLATE_FLAGGED",
                    "active_connections": 0,
                }
                with patch.dict(os.environ, {
                    "INTEVIA_DATABASE_TAG": "tu1b",
                    "INTEVIA_EVIDENCE_ROOT": "PKTB_TU1B",
                    "VERIFICATION_TEMPLATE_NAMESPACE": "me",
                }), patch.object(
                    template, "_database_record",
                    side_effect=lambda _connection, name: database if name == database_name else None,
                ), patch.object(template, "_drop_database_by_oid", return_value=True) as drop, \
                        patch.object(template, "_attempt_receipt"):
                    recovered = template.recover_template_builds(object(), root)
                    completed_bytes = intent_path.read_bytes()
                    recovered_again = template.recover_template_builds(object(), root)

                drop.assert_called_once()
                self.assertEqual(recovered[0]["stage"], "RECOVERED_ABSENT")
                self.assertEqual(recovered_again[0]["stage"], "RECOVERED_ABSENT")
                self.assertEqual(intent_path.read_bytes(), completed_bytes)

    def test_final_receipt_completes_its_intent_without_recovery_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "PKTB_TU1B"
            store = root / "ME_DEV" / "integrated-template-store"
            receipt_path, receipt = TemplateCleanupLifecycleTests()._receipt("me", store)
            intent_path = Path(receipt["build_intent"]["path"])
            intent = json.loads(intent_path.read_text(encoding="utf-8"))
            intent["stage"] = "TEMPLATE_FLAGGED"
            intent_path.write_text(json.dumps(intent), encoding="utf-8")
            intent_bytes = intent_path.read_bytes()
            attempt_path = Path(intent["attempt_receipt_path"])
            attempt_records = [
                json.loads(line)
                for line in attempt_path.read_text(encoding="utf-8").splitlines()
            ]
            create_attempt = next(
                record for record in attempt_records
                if record.get("event") == "template_create_attempt"
            )
            create_attempt["at"] = "2026-10-15T00:00:00Z"
            create_attempt["pid"] = 101
            attempt_records.append({
                **create_attempt,
                "at": "2026-10-15T00:00:01Z",
                "pid": 102,
            })
            attempt_path.write_text(
                "\n".join(json.dumps(record) for record in attempt_records) + "\n",
                encoding="utf-8",
            )
            attempt_bytes = attempt_path.read_bytes()
            database = {"oid": receipt["oid"], "allow_connections": False, "active_connections": 0}

            with patch.dict(os.environ, {
                "INTEVIA_DATABASE_TAG": "tu1b",
                "INTEVIA_EVIDENCE_ROOT": "PKTB_TU1B",
                "INTEVIA_EVIDENCE_ROOT_PATH": str(root),
                "VERIFICATION_TEMPLATE_NAMESPACE": "me",
            }), patch.object(
                template, "_database_record",
                side_effect=lambda _connection, name: database if name == receipt["name"] else None,
            ), patch.object(template, "_drop_database_by_oid") as drop, \
                    patch.object(template, "verify_registered_template") as verify:
                first = template.recover_template_builds(object(), root)
                second = template.recover_template_builds(object(), root)

            drop.assert_not_called()
            self.assertEqual(verify.call_count, 2)
            self.assertEqual(first[0]["stage"], "TEMPLATE_FLAGGED")
            self.assertEqual(second[0]["stage"], "TEMPLATE_FLAGGED")
            self.assertEqual(intent_path.read_bytes(), intent_bytes)
            self.assertEqual(attempt_path.read_bytes(), attempt_bytes)
            self.assertTrue(receipt_path.is_file())
            attempt_records.append({**create_attempt, "template": "unexpected-source"})
            attempt_path.write_text(
                "\n".join(json.dumps(record) for record in attempt_records) + "\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {
                "INTEVIA_DATABASE_TAG": "tu1b",
                "INTEVIA_EVIDENCE_ROOT": "PKTB_TU1B",
                "INTEVIA_EVIDENCE_ROOT_PATH": str(root),
                "VERIFICATION_TEMPLATE_NAMESPACE": "me",
            }), patch.object(
                template, "_database_record",
                side_effect=lambda _connection, name: database if name == receipt["name"] else None,
            ), patch.object(template, "verify_registered_template") as verify_conflict:
                with self.assertRaises(template.TemplateReceiptInvalid):
                    template.recover_template_builds(object(), root)

            verify_conflict.assert_not_called()


class TemplateCleanupLifecycleTests(unittest.TestCase):
    def _receipt(self, namespace, store):
        listing, identity = template.canonical_listing(("head=stage1",))
        nonce = "a" * 32
        attempt_directory = store.parent / "attempt_1"
        attempt_directory.mkdir(parents=True, exist_ok=True)
        attempt_path = attempt_directory / "database-receipts.jsonl"
        receipt = {
            "identity": identity,
            "listing": listing,
            "name": template._template_database_name(identity, namespace, "tu1b"),
            "oid": 901,
            "run_nonce": nonce,
            "database_tag": "tu1b",
            "evidence_root": "PKTB_TU1B",
            "candidate_binding": "b" * 64,
            "cp0_digest": "c" * 64,
            "fresh_build_cp0_digest": "c" * 64,
            "allow_connections": False,
            "is_template": True,
            "connection_refused": True,
        }
        path = store / (receipt["name"] + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        intent_path = store / "template-build-intents" / (nonce + ".json")
        intent_path.parent.mkdir(parents=True, exist_ok=True)
        bootstrap_name = "test_intevia_boot_tu1b_" + nonce
        intent = {
            "version": 1,
            "namespace": namespace,
            "database_tag": "tu1b",
            "evidence_root": "PKTB_TU1B",
            "run_nonce": nonce,
            "attempt_receipt_path": str(attempt_path),
            "bootstrap_name": bootstrap_name,
            "database_oid": receipt["oid"],
            "target_name": receipt["name"],
            "identity": identity,
            "receipt_path": str(path),
            "candidate_binding": receipt["candidate_binding"],
            "stage": "COMPLETE",
        }
        intent_path.write_text(json.dumps(intent), encoding="utf-8")
        receipt["build_intent"] = {
            "path": str(intent_path),
            "identity": identity,
            "run_nonce": nonce,
            "database_oid": receipt["oid"],
        }
        attempt_path.write_text("\n".join(json.dumps(record) for record in (
            {
                "event": "template_create_attempt", "name": bootstrap_name,
                "unit_tag": "tu1b", "evidence_root": "PKTB_TU1B", "run_nonce": nonce,
            },
            {
                "event": "template_database_created", "name": bootstrap_name,
                "oid": receipt["oid"], "run_nonce": nonce,
            },
            {
                "event": "template_database_renamed", "name": receipt["name"],
                "oid": receipt["oid"], "run_nonce": nonce,
            },
            {
                "event": "template_created", "name": receipt["name"],
                "oid": receipt["oid"], "run_nonce": nonce,
            },
        )) + "\n", encoding="utf-8")
        path.write_text(json.dumps(receipt), encoding="utf-8")
        return path, receipt

    def test_development_cleanup_without_parity_consumes_receipt_idempotently(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "PKTB_TU1B"
            store = root / "ME_DEV" / "integrated-template-store"
            receipt_path, receipt = self._receipt("me", store)
            database = {
                "oid": receipt["oid"], "allow_connections": False,
                "is_template": True, "active_connections": 0,
            }
            with patch.dict(os.environ, {
                "VERIFICATION_TEMPLATE_NAMESPACE": "me",
                "VERIFICATION_TEMPLATE_STORE": str(store),
                "INTEVIA_DATABASE_TAG": "tu1b",
                "INTEVIA_EVIDENCE_ROOT": "PKTB_TU1B",
                "INTEVIA_EVIDENCE_ROOT_PATH": str(root),
                "VERIFICATION_RUN_NONCE": "d" * 32,
                "INTEVIA_CANDIDATE_BINDING_SHA256": "e" * 64,
            }), patch.object(template, "_database_record", return_value=database), \
            patch.object(template, "_drop_database_by_oid", return_value=True) as drop, \
            patch.object(template, "_attempt_receipt"):
                self.assertTrue(template.cleanup_template(
                    object(), receipt_path, receipt["identity"], allow_incomplete_parity=True
                ))
                consumed_bytes = receipt_path.read_bytes()
                consumed = json.loads(consumed_bytes)
                record_path = Path(consumed["destroyed"]["cleanup_record"])
                record_bytes = record_path.read_bytes()

                with patch.object(template, "_database_record", return_value=None):
                    self.assertTrue(template.cleanup_template(
                        object(), receipt_path, receipt["identity"], allow_incomplete_parity=True
                    ))

            self.assertEqual(receipt_path.read_bytes(), consumed_bytes)
            self.assertEqual(record_path.read_bytes(), record_bytes)
            drop.assert_called_once()

    def test_verification_cleanup_requires_final_parity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "PKTB_TU1B"
            store = root / "VERIFICATION_OF_RECORD" / "tooling-template-store"
            receipt_path, receipt = self._receipt("vr", store)
            with patch.dict(os.environ, {
                "VERIFICATION_TEMPLATE_NAMESPACE": "vr",
                "VERIFICATION_TEMPLATE_STORE": str(store),
                "INTEVIA_DATABASE_TAG": "tu1b",
                "INTEVIA_EVIDENCE_ROOT": "PKTB_TU1B",
                "INTEVIA_EVIDENCE_ROOT_PATH": str(root),
            }), patch.object(template, "_database_record") as database_record, \
                    patch.object(template, "_attempt_receipt"):
                with self.assertRaisesRegex(template.TemplateReceiptInvalid, "final clone CP-0"):
                    template.cleanup_template(
                        object(), receipt_path, receipt["identity"], allow_incomplete_parity=True
                    )

            database_record.assert_not_called()


class TemplateLifecycleToolingTests(unittest.TestCase):
    databases = {"default"}

    def test_template_clone_parity_and_receipt_cleanup(self):
        store = os.environ.get("VERIFICATION_TEMPLATE_STORE")
        if not store:
            self.skipTest("requires the TT1 attempt template store")
        from django.apps import apps
        if not apps.ready:
            self.skipTest("requires an initialized Django test runner")

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

        template_store = Path(store)
        clone_name = "test_intevia_living_organism_tt1_witness_" + uuid.uuid4().hex[:12]
        clone_alias = "tt1_witness_clone_" + uuid.uuid4().hex[:8]
        receipt = None
        clone_created = False
        clone_digest = None
        parity_recorded = False
        clone_connection = None
        template_path = None
        old_state_connection = state.connection
        try:
            receipt, built = prepare_template(connection, "default", template_store)
            template_path = template_receipt_path(template_store, receipt["identity"])
            self.assertFalse(built)
            other_candidate_binding = "0" * 64
            if other_candidate_binding == receipt["candidate_binding"]:
                other_candidate_binding = "1" * 64
            self.assertNotEqual(other_candidate_binding, receipt["candidate_binding"])
            with patch.dict(os.environ, {"INTEVIA_CANDIDATE_BINDING_SHA256": other_candidate_binding}):
                reused_receipt, rebuilt = prepare_template(connection, "default", template_store)
                self.assertFalse(rebuilt)
                self.assertEqual(reused_receipt["identity"], receipt["identity"])
                self.assertEqual(reused_receipt["candidate_binding"], receipt["candidate_binding"])
                receipt = reused_receipt
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
                if receipt is not None and template_path is not None:
                    cleanup_template(connection, template_path, receipt["identity"],
                                     allow_incomplete_parity=not parity_recorded)
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