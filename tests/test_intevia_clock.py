"""The INTEVIA Clock's own witnesses (TIMED EVENTS T1b).

This file takes no time from PostgreSQL's own functions and none from the host. Where a
witness must compare a face with the built-in it stands for, verification/clock/oracle.py
makes the comparison and returns what it observed.
"""

import os
import sys
from contextlib import contextmanager
from datetime import timedelta, tzinfo
from importlib import import_module
from unittest.mock import patch

from django.contrib.sessions.models import Session
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import DatabaseError, OperationalError, connection, connections, transaction
from django.db.backends.postgresql.operations import DatabaseOperations as PlainOperations
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext

from core import clock_startup as startup
from email_foundation import service as mail
from intevia.test_postgresql_backend import clock as owned_route
from intevia.test_postgresql_backend import clock_route, creation, operations
from src.intevia.services import clock
from verification.clock import oracle
from verification.isolation import state as instrument

# What production's settings hold where the owned test route's settings differ.
PRODUCTION_SETTINGS = {"S015_TEST_ROUTE": None, "INTEVIA_TEST_CONTEXT": False}
# The real serving entries: importing either module is how a server starts INTEVIA.
SERVING_ENTRIES = ("intevia.wsgi", "intevia.asgi")
REFUSAL_PARTS = (
    "INTEVIA did not start on the database",
    "Its clock is not the production clock",
    "No business data was read or changed",
    "Point INTEVIA at its production database, or build a new database",
    "the platform's operators decide what happens to this database",
)


class _Undo(Exception):
    """Raised to end a block whose work must not stay."""


@contextmanager
def undone():
    """Run a block inside a transaction, then roll all of it back."""
    try:
        with transaction.atomic():
            yield
            raise _Undo()
    except _Undo:
        pass


TEST_CLOCK = import_module("intevia_test_clock.migrations.0001_moving_part")
PRODUCTION = import_module("core.migrations.0096_intevia_clock")
MICROSECOND = timedelta(microseconds=1)
COUNTER_FILE = "SELECT pg_catalog.pg_relation_filenode('public.intevia_test_clock_offset_us'::regclass)"


def production_faces(cursor):
    """Put the production faces in place of the owned route's. Only ever inside a block that is rolled back."""
    for name, written, _stored, builtin, meaning in PRODUCTION.FACES:
        cursor.execute(
            "CREATE OR REPLACE FUNCTION public.%s() RETURNS %s LANGUAGE sql %s PARALLEL SAFE SECURITY INVOKER "
            "AS $intevia_clock$%s$intevia_clock$" % (name, PRODUCTION.RESULT, written, PRODUCTION.body(builtin)))
        cursor.execute("COMMENT ON FUNCTION public.%s() IS %%s" % name, [PRODUCTION.comment(meaning)])


def as_production_built_it(cursor):
    """Make this database, inside a block that is rolled back, what production's migrations alone would build."""
    production_faces(cursor)
    cursor.execute("DROP PROCEDURE public.intevia_test_clock_advance(bigint, timestamp with time zone, bigint)")
    cursor.execute("DROP FUNCTION public.intevia_test_clock_observed_obstacle()")
    cursor.execute("DROP FUNCTION public.intevia_test_clock_obstacle(oid, oid, boolean)")
    cursor.execute("DROP FUNCTION public.intevia_test_clock_reset()")
    cursor.execute("DROP FUNCTION public.intevia_test_clock_shift()")
    cursor.execute("DROP FUNCTION public.intevia_test_clock_offset()")
    cursor.execute("DROP FUNCTION public.intevia_test_clock_require_clone()")
    cursor.execute("DROP FUNCTION public.intevia_test_clock_refuse(text)")
    cursor.execute("DROP FUNCTION public.intevia_test_clock_is_clone(text)")
    cursor.execute("DROP SEQUENCE public.intevia_test_clock_offset_us")
    cursor.execute("DELETE FROM public.django_migrations WHERE app = %s", [startup.TEST_CLOCK_APP])


class InteviaClockTests(TransactionTestCase):
    def peer(self):
        """A second, independent session on the same database, closed when the test ends."""
        other = connection.copy()
        self.addCleanup(other.close)
        return other

    def test_production_refuses_a_database_that_ever_held_the_moving_part(self):
        """CLK-01. The owned test clock's lifecycle is kept apart from production. Production, at each real serving
        entry and on every new physical connection, refuses a database that bears any mark of the moving part; and
        installing, moving and reversing the moving part are permitted only on the owned route's own databases."""
        written = {name: PRODUCTION.expected(stored, builtin, meaning) for name, _w, stored, builtin, meaning in PRODUCTION.FACES}
        marked = {name: TEST_CLOCK.expected(stored, builtin, meaning) for name, _w, stored, builtin, meaning in PRODUCTION.FACES}
        self.assertEqual(written, startup.PRODUCTION_FACES,
                         "CLK-01: the startup refusal must expect the three faces exactly as migration 0096 writes them.")
        with connection.cursor() as cursor:
            held = {name: PRODUCTION.installed(cursor, name) for name in written}
        self.assertEqual(held, marked, "CLK-01: an owned test database must hold the three faces exactly as the moving part writes them.")
        self.assertEqual(startup.refuse_unless_production_clock("default"), startup.OWNED_TEST_ROUTE_STANDS_DOWN,
                         "CLK-01: under the owned test route's own settings the startup check stands down.")
        clone = connection.settings_dict["NAME"]

        def start(entry):
            with patch.dict(os.environ):
                sys.modules.pop(entry, None)
                try:
                    import_module(entry)
                finally:
                    sys.modules.pop(entry, None)

        def outcome():
            try:
                return startup.refuse_unless_production_clock("default")
            except DatabaseError as error:
                return "the evidence could not be read: %s" % error

        # Every physical connection this test opens is recorded, so that "closed" is read from the connection itself.
        opened = []
        open_physical = connection.get_new_connection

        def recorded(parameters):
            opened.append(open_physical(parameters))
            return opened[-1]

        def physical():
            return (len(opened), opened[-1].closed if opened else None, connection.connection is None)

        counter_found = "the test route's relation public.intevia_test_clock_offset_us is present"
        self.addCleanup(connection.close)
        with patch.object(connection, "get_new_connection", recorded):
            # This clone is a real test database: it holds the moving part. Production must not start on it.
            with self.subTest(connection="the first connection to this test database"), override_settings(**PRODUCTION_SETTINGS):
                connection.close()
                with self.assertRaises(startup.ClockStartupRefused,
                                       msg="CLK-01 first connection: a process must be refused at its first connection.") as refused:
                    connection.ensure_connection()
                self.assertEqual(physical(), (1, True, True),
                                 "CLK-01 first connection: a refused database must be left with no open connection.")
                with self.assertRaises(startup.ClockStartupRefused,
                                       msg="CLK-01 first connection: every later use must meet the refusal again."):
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT 1")
                self.assertEqual(physical(), (2, True, True),
                                 "CLK-01 first connection: the later use must have opened a new connection, and that one must be closed too.")
                text = str(refused.exception)
                for finding in (counter_found,
                                "the test route's procedure public.intevia_test_clock_advance is present",
                                "the migration ledger records the test route's app intevia_test_clock",
                                "the face public.intevia_clock_instant() is not the production face"):
                    self.assertIn(finding, text, "CLK-01: the refusal must name each mark it found.")
                for part in REFUSAL_PARTS:
                    self.assertIn(part, text, "CLK-01: the refusal must keep its five parts.")

            with self.subTest(connection="accepted once, then a new physical connection under the same alias and name"), \
                    override_settings(**PRODUCTION_SETTINGS):
                # The first physical connection is told that it reads a clean production database, and is accepted.
                # Nothing may be remembered from that: the next physical connection reads this real, marked database.
                del opened[:]
                connection.close()
                with patch.object(startup, "clock_evidence", return_value=(sorted(startup.PRODUCTION_FACES), [])):
                    connection.ensure_connection()
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT 1")
                    accepted = (connection.alias, connection.settings_dict["NAME"])
                    self.assertEqual(physical(), (1, False, False),
                                     "CLK-01 reconnect: the accepted connection must be open and in use.")
                connection.close()
                with self.assertRaises(startup.ClockStartupRefused,
                                       msg="CLK-01 reconnect: a new physical connection must be checked afresh, and this database refused."):
                    connection.ensure_connection()
                self.assertEqual(((connection.alias, connection.settings_dict["NAME"]), physical()), (accepted, (2, True, True)),
                                 "CLK-01 reconnect: under the same alias and name, the refused connection must be closed.")

            with self.subTest(connection="the evidence read fails, then a retry"), override_settings(**PRODUCTION_SETTINGS):
                del opened[:]
                connection.close()
                with patch.object(startup, "EVIDENCE_SQL", "SELECT 1 / 0"), \
                        self.assertRaises(DatabaseError,
                                          msg="CLK-01 failed read: a database error while the evidence is read must be raised.") as failed:
                    connection.ensure_connection()
                self.assertIn("division by zero", str(failed.exception), "CLK-01 failed read: the error raised must be the database's own.")
                self.assertEqual(physical(), (1, True, True),
                                 "CLK-01 failed read: a connection whose check failed must be closed, never left open unchecked.")
                with self.assertRaises(startup.ClockStartupRefused,
                                       msg="CLK-01 failed read: the retry must be checked afresh, and this database refused."):
                    connection.ensure_connection()
                self.assertEqual(physical(), (2, True, True), "CLK-01 failed read: the refused retry must be closed.")

            for entry in SERVING_ENTRIES:
                # Back under the owned route's settings the same database is this route's own again. The connection is
                # opened before the entry starts, so the check of new connections does not run: only this entry's own
                # check can refuse.
                connection.ensure_connection()
                del opened[:]
                held = connection.connection
                with self.subTest(entry=entry, database="this test database, already connected"), \
                        override_settings(**PRODUCTION_SETTINGS):
                    with self.assertRaises(startup.ClockStartupRefused,
                                           msg="CLK-01 %s: this serving entry's own check must refuse to start." % entry) as refused:
                        start(entry)
                    self.assertIn(counter_found, str(refused.exception), "CLK-01 %s: the refusal must name what it found." % entry)
                    self.assertEqual(physical()[:2], (0, None),
                                     "CLK-01 %s: no new connection may have been opened, so that this entry's own check is what refused." % entry)
                    self.assertEqual((held.closed, connection.connection is None), (True, True),
                                     "CLK-01 %s: a serving entry's check that refuses must leave this alias with no open connection." % entry)
            # Under the owned route's settings again, for what follows.
            connection.ensure_connection()

        marks = (
            ("the test route's counter", "CREATE SEQUENCE public.intevia_test_clock_offset_us",
             "the test route's relation public.intevia_test_clock_offset_us is present"),
            ("the test route's move function",
             "CREATE FUNCTION public.intevia_test_clock_advance() RETURNS integer LANGUAGE sql AS ' SELECT 1 '",
             "the test route's function public.intevia_test_clock_advance is present"),
            ("a function whose whole name is the test route's prefix",
             "CREATE FUNCTION public.intevia_test_clock() RETURNS integer LANGUAGE sql AS ' SELECT 1 '",
             "the test route's function public.intevia_test_clock is present"),
            ("the test route's migration in the ledger",
             "INSERT INTO public.django_migrations (app, name, applied) "
             "VALUES ('intevia_test_clock', '0001_moving_part', public.intevia_clock_statement())",
             "the migration ledger records the test route's app intevia_test_clock"),
            ("a face with another body",
             "CREATE OR REPLACE FUNCTION public.intevia_clock_instant() RETURNS timestamp with time zone "
             "LANGUAGE sql VOLATILE PARALLEL SAFE AS ' SELECT public.intevia_clock_statement() '",
             "the face public.intevia_clock_instant() is not the production face"),
            ("a face that kept the test route's mark after its body was put back",
             "COMMENT ON FUNCTION public.intevia_clock_statement() IS %s" % "'" + TEST_CLOCK.MARK % "the start of the current statement" + "'",
             "the face public.intevia_clock_statement() is not the production face"),
            ("a face removed", "DROP FUNCTION public.intevia_clock_transaction()",
             "the face public.intevia_clock_transaction() is missing"),
            ("a face's name in a second form, which takes an argument",
             "CREATE FUNCTION public.intevia_clock_instant(integer) RETURNS timestamp with time zone "
             "LANGUAGE sql AS ' SELECT NULL::timestamp with time zone '",
             "public.intevia_clock_instant exists in a form that takes arguments"),
        )
        with self.subTest(database="as production's migrations alone build it"), override_settings(**PRODUCTION_SETTINGS), undone():
            with connection.cursor() as cursor:
                as_production_built_it(cursor)
                self.assertEqual({name: PRODUCTION.installed(cursor, name) for name in written}, written,
                                 "CLK-01 clean: the faces put back must be the production faces.")
            with CaptureQueriesContext(connection) as asked:
                accepted_as = startup.refuse_unless_production_clock("default")
            self.assertEqual((accepted_as, len(asked.captured_queries)), (startup.PRODUCTION_CLOCK, 1),
                             "CLK-01 clean: a database with the production clock and no mark must be accepted, on one statement's evidence.")
            for label, sql, finding in marks:
                with self.subTest(mark=label), undone():
                    with connection.cursor() as cursor:
                        cursor.execute(sql)
                    with self.assertRaises(startup.ClockStartupRefused,
                                           msg="CLK-01 mark (%s): startup must refuse it." % label) as refused:
                        startup.refuse_unless_production_clock("default")
                    self.assertIn(finding, str(refused.exception), "CLK-01 mark (%s): the refusal must name what it found." % label)
                    if "takes an argument" in label:
                        self.assertEqual([wrong for wrong in ("is not the production face", "more than one form", "is missing")
                                          if wrong in str(refused.exception)], [],
                                         "CLK-01 mark (%s): a form that takes arguments is named as that, and the face beside it, "
                                         "which is production's, is not blamed." % label)
            self.assertEqual(startup.refuse_unless_production_clock("default"), startup.PRODUCTION_CLOCK,
                             "CLK-01 clean: once each mark is rolled back the database must be accepted again.")
            with self.subTest(names="that only resemble the test route's prefix"), undone():
                with connection.cursor() as cursor:
                    cursor.execute("CREATE SEQUENCE public.intevia_test_cloc")
                    cursor.execute("CREATE SEQUENCE public.intevia_test_clocl")
                    cursor.execute("CREATE FUNCTION public.intevia_test_clocl_advance() RETURNS integer LANGUAGE sql AS ' SELECT 1 '")
                self.assertEqual(outcome(), startup.PRODUCTION_CLOCK,
                                 "CLK-01 look-alikes: a name just short of the prefix, and one just past it, are not the test route's.")
            with self.subTest(database="not yet migrated: no ledger table"), undone():
                with connection.cursor() as cursor:
                    cursor.execute("ALTER TABLE public.django_migrations RENAME TO clk01_ledger_set_aside")
                self.assertEqual(outcome(), startup.PRODUCTION_CLOCK,
                                 "CLK-01 no ledger: the one statement must read a database that has no ledger table.")
                with connection.cursor() as cursor:
                    for name in written:
                        cursor.execute("DROP FUNCTION public.%s()" % name)
                self.assertEqual(outcome(), startup.NOT_YET_MIGRATED,
                                 "CLK-01 not yet migrated: a database with no face, no ledger and no mark is not refused, "
                                 "so that migrate can install the clock.")

        with connection.cursor() as cursor:
            parts = "SELECT count(*) FROM pg_catalog.pg_proc p WHERE pg_catalog.starts_with(p.proname::text, 'intevia_test_clock')"
            cursor.execute(parts)
            moving_part = cursor.fetchone()[0]
        for entry in SERVING_ENTRIES:
            # Each entry in a block of its own. A check that passes closes the connection; that ends the block's
            # transaction at the server, and so undoes the block.
            connection.ensure_connection()
            with self.subTest(entry=entry, database="as production's migrations alone build it"), \
                    override_settings(**PRODUCTION_SETTINGS), undone():
                with connection.cursor() as cursor:
                    as_production_built_it(cursor)
                held = connection.connection
                try:
                    start(entry)
                except startup.ClockStartupRefused as stopped:
                    self.fail("CLK-01 %s clean: this serving entry must start on a production database, and was refused: %s"
                              % (entry, stopped))
                self.assertEqual((held.closed, connection.closed_in_transaction or connection.connection is None), (True, True),
                                 "CLK-01 %s clean: after a check that passes, this serving entry must leave this alias with no open "
                                 "connection: a pre-fork server hands every open socket to its workers." % entry)
            with connection.cursor() as cursor:
                cursor.execute(parts)
                self.assertEqual((cursor.fetchone()[0], owned_route.offset()), (moving_part, 0),
                                 "CLK-01 %s clean: the block must have been undone whole; this clone still holds its moving part." % entry)

        with self.subTest(names="the owned route's stages"):
            tag = "t1b"
            bootstrap, template = "test_intevia_boot_%s_%s" % (tag, "0" * 32), "test_intevia_tpl_me_%s_%s" % (tag, "a" * 32)
            control = "test_intevia_ctl_%s_%s" % (tag, "b" * 20)
            owned = {bootstrap: clock_route.BOOTSTRAP, template: clock_route.TEMPLATE,
                     "test_intevia_tpl_vr_%s_%s" % (tag, "f" * 32): clock_route.TEMPLATE, control: clock_route.CONTROL,
                     clone: clock_route.CLONE}
            self.assertEqual({name: clock_route.owned_stage(name) for name in owned}, owned,
                             "CLK-01 names: each owned stage's own name must be told apart.")
            strangers = ("intevia", "intevia_production", "postgres", "template1", "test_intevia", "test_intevia_living_organism_",
                         "Test_intevia_living_organism_a", clone + "\n", " " + clone, clone.upper(),
                         "xtest_intevia_boot_%s_%s" % (tag, "0" * 32), "test_intevia_boot_%s_%s" % (tag, "0" * 31),
                         "test_intevia_boot_%s_%s" % (tag, "g" * 32), "test_intevia_tpl_xx_%s_%s" % (tag, "a" * 32),
                         "test_intevia_ctl_%s_%s" % (tag, "b" * 19), "", None, 7, clone.encode("ascii"))
            self.assertEqual([name for name in strangers if clock_route.owned_stage(name) is not None], [],
                             "CLK-01 names: a production-shaped name, and a name that only resembles a stage's, belong to no stage.")

            def reason(purpose, stages, name, **changed):
                # A rehearsal, bound by the test runner's own record: no receipts, no verification variable.
                facts = {"owned_settings": True, "configured": name, "connected": name, "owner_is_session_role": True,
                         "receipts": None, "receipt_configuration": owned_route.NONE, "verification_run": False,
                         "recorded": (name, 7), "connected_oid": 7}
                facts.update(changed)
                return owned_route.refusal(purpose, stages, **facts)

            def decide(purpose, stages, name, **changed):
                return reason(purpose, stages, name, **changed) is None

            with_receipts = {"receipt_configuration": owned_route.COMPLETE, "verification_run": True, "recorded": None}

            install = {name: decide("Installing", owned_route.INSTALL_STAGES, name) for name in (bootstrap, clone, template, control)}
            self.assertEqual(install, {bootstrap: True, clone: True, template: False, control: False},
                             "CLK-01 install: the moving part is installed only where the owned route runs migrations.")
            move = {name: decide("Moving", owned_route.ADVANCE_STAGES, name) for name in (bootstrap, clone, template, control)}
            self.assertEqual(move, {bootstrap: False, clone: True, template: False, control: False},
                             "CLK-01 move: only a clone may ever be an advance target.")
            reverse = {name: decide("Reversing", owned_route.REVERSE_STAGES, name) for name in (bootstrap, clone, template, control)}
            self.assertEqual(reverse, {bootstrap: False, clone: True, template: False, control: False},
                             "CLK-01 reverse: the moving part's migration is reversed only on a clone.")
            for purpose, stages in (("Installing", owned_route.INSTALL_STAGES), ("Moving", owned_route.ADVANCE_STAGES),
                                    ("Reversing", owned_route.REVERSE_STAGES)):
                refused_for = {
                    "a production-shaped name": decide(purpose, stages, "intevia_production"),
                    "a name that only resembles a clone's": decide(purpose, stages, clone.upper()),
                    "settings that are not the owned route's": decide(purpose, stages, clone, owned_settings=False),
                    "a connected database that is not the configured one": decide(purpose, stages, clone, configured=bootstrap),
                    "a role that does not own the database": decide(purpose, stages, clone, owner_is_session_role=False),
                    "receipts that do not record creating it": decide(purpose, stages, clone, receipts=[("created", clone, 1, 2)], **with_receipts),
                    "receipts for another database": decide(purpose, stages, clone, receipts=[("created", bootstrap, 7, 7)], **with_receipts),
                    "receipts that could not be trusted": decide(purpose, stages, clone, receipts=[], **with_receipts),
                    "a receipt whose oids were not read": decide(purpose, stages, clone, receipts=[("created", clone, None, None)], **with_receipts),
                    "a receipt whose oids are text": decide(purpose, stages, clone, receipts=[("created", clone, "7", "7")], **with_receipts),
                    "a receipt whose oids are truth values": decide(purpose, stages, clone, receipts=[("created", clone, True, True)], **with_receipts),
                    "receipts configured and not read": decide(purpose, stages, clone, receipts=None, **with_receipts),
                    "receipts configured and missing, with the test runner's record": decide(
                        purpose, stages, clone, receipts=[], **{**with_receipts, "recorded": (clone, 7)}),
                    "a record of a clone with this name and another oid": decide(purpose, stages, clone, recorded=(clone, 8)),
                    "a record of another database": decide(purpose, stages, clone, recorded=(bootstrap, 7)),
                    "no receipt and no record": decide(purpose, stages, clone, recorded=None),
                    "an oid that was not read": decide(purpose, stages, clone, recorded=(clone, None), connected_oid=None),
                    "a receipt configuration the route does not know": decide(purpose, stages, clone, receipt_configuration="other"),
                }
                self.assertEqual([case for case, allowed in refused_for.items() if allowed], [],
                                 "CLK-01 %s: each of these must be refused." % purpose.lower())
                self.assertTrue(decide(purpose, stages, clone, receipts=[("destroyed", clone, 7, 7), ("created", clone, 7, 7)], **with_receipts),
                                "CLK-01 %s: the clone this run recorded creating, by its oid, is accepted." % purpose.lower())
                self.assertTrue(decide(purpose, stages, clone),
                                "CLK-01 %s: on a rehearsal, the clone the test runner recorded, by its name and oid, is accepted." % purpose.lower())
                # No fail-open configuration: each of these names its own condition, with or without a record that matches.
                named = {
                    "a receipt file without a nonce, or a nonce without a file":
                        ({"receipt_configuration": owned_route.PARTIAL, "verification_run": True}, "half-configured for receipts"),
                    "the same, stated without any other verification variable":
                        ({"receipt_configuration": owned_route.PARTIAL, "verification_run": False}, "half-configured for receipts"),
                    "a verification variable set, and no receipts":
                        ({"receipt_configuration": owned_route.NONE, "verification_run": True}, "this is a verification run"),
                    "whether a verification variable is set, not read":
                        ({"receipt_configuration": owned_route.NONE, "verification_run": None}, "this is a verification run"),
                }
                said = {case: reason(purpose, stages, clone, **facts) or "" for case, (facts, _words) in named.items()}
                self.assertEqual([case for case, (_facts, words) in named.items() if words not in said[case]], [],
                                 "CLK-01 %s: a run that is neither a verification run with receipts nor a clean rehearsal "
                                 "must be refused, and the refusal must name the condition: %r" % (purpose.lower(), said))
            with connection.cursor() as cursor:
                cursor.execute("SELECT name, public.intevia_test_clock_is_clone(name) FROM unnest(%s::text[]) AS given(name)",
                               [[bootstrap, template, control, clone, "intevia_production", clone.upper(), clone + "\n", None]])
                storage = dict(cursor.fetchall())
            self.assertEqual([name for name, allowed in storage.items() if allowed], [clone],
                             "CLK-01 move: storage too accepts a clone's name and no other.")
            self.assertIs(owned_route.authorise_advance("default"), connections["default"],
                          "CLK-01 move: this qualified active clone, outside every transaction, is accepted as an advance target.")

        wrapper = connections["default"]
        permissions = (("installing", lambda: owned_route.authorise_install(wrapper)),
                       ("moving", lambda: owned_route.authorise_advance("default")),
                       ("reversing", lambda: owned_route.authorise_reverse(wrapper)))

        with self.subTest(binding="this clone, as this run is really configured"):
            facts = owned_route._facts(wrapper)
            really = {purpose: owned_route.refusal(purpose, stages, **facts)
                      for purpose, stages in (("installing", owned_route.INSTALL_STAGES), ("moving", owned_route.ADVANCE_STAGES),
                                              ("reversing", owned_route.REVERSE_STAGES))}
            self.assertEqual(really, {"installing": None, "moving": None, "reversing": None},
                             "CLK-01 binding: as this run is really configured, this clone is bound to it for all three permissions.")
            if facts["receipt_configuration"] == owned_route.NONE:
                self.assertEqual((facts["verification_run"], facts["recorded"]), (False, (clone, facts["connected_oid"])),
                                 "CLK-01 binding: a run without receipts must hold its test runner's own record of this clone, by name and oid.")
            else:
                self.assertEqual(facts["receipt_configuration"], owned_route.COMPLETE,
                                 "CLK-01 binding: a run that is not a rehearsal must carry its receipts whole.")

        with self.subTest(binding="this clone, under each stated configuration"):
            oid = owned_route._facts(wrapper)["connected_oid"]
            rehearsal = {name: value for name, value in os.environ.items() if not name.startswith(owned_route.VERIFICATION_PREFIX)}
            absent = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clk01-no-such-receipt-file.jsonl")

            def answers(environment, record):
                """What each permission answers on this real clone, under a stated environment and a stated record."""
                given = {}
                with patch.dict(os.environ, environment, clear=True), patch.object(wrapper, creation.RECORD, record, create=True):
                    for purpose, ask in permissions:
                        try:
                            ask()
                            given[purpose] = "accepted"
                        except owned_route.ClockRouteRefused as stopped:
                            given[purpose] = str(stopped)
                return given

            self.assertEqual(answers(rehearsal, (clone, oid)), {"installing": "accepted", "moving": "accepted", "reversing": "accepted"},
                             "CLK-01 binding: a rehearsal whose test runner recorded this clone, by name and oid, is accepted.")
            stated = (
                ("a receipt file without a nonce", {**rehearsal, owned_route.RECEIPT_ENV: absent}, (clone, oid), "half-configured for receipts"),
                ("a nonce without a receipt file", {**rehearsal, owned_route.NONCE_ENV: "0" * 32}, (clone, oid), "half-configured for receipts"),
                ("a verification variable set, and no receipts", {**rehearsal, "VERIFICATION_STEP": "CLK01"}, (clone, oid),
                 "this is a verification run"),
                ("receipts configured, and none that records this clone",
                 {**rehearsal, owned_route.RECEIPT_ENV: absent, owned_route.NONCE_ENV: "0" * 32}, (clone, oid),
                 "this run's receipts do not record creating the database"),
                ("a clone with this name and this owner that is not the one the test runner recorded", rehearsal, (clone, oid + 1),
                 "is not the database this run's test runner created or selected"),
                ("no receipt and no record", rehearsal, None, "its test runner recorded no database of its own"),
            )
            for label, environment, record, words in stated:
                given = answers(environment, record)
                self.assertEqual([purpose for purpose, answer in given.items() if words not in answer], [],
                                 "CLK-01 binding (%s): installing, moving and reversing must each be refused, naming the condition: %r"
                                 % (label, given))

        with self.subTest(binding="moving and reversing need the lifecycle backend's qualified active clone"):
            for purpose, ask in permissions[1:]:
                with patch.dict(wrapper.settings_dict, {"S015_TEST_ROUTE": None}), \
                        self.assertRaises(ImproperlyConfigured,
                                          msg="CLK-01 %s: a connection the lifecycle backend does not qualify must be refused." % purpose):
                    ask()
                with patch.object(wrapper, "ops", PlainOperations(wrapper)), \
                        self.assertRaises(owned_route.ClockRouteRefused,
                                          msg="CLK-01 %s: a connection that is not the lifecycle backend must be refused." % purpose) as stopped:
                    ask()
                self.assertIn("this connection is not the owned test lifecycle backend", str(stopped.exception),
                              "CLK-01 %s: the refusal must name the condition." % purpose)

        with self.subTest(counter="kept through a reversal, and compared whole"):
            # Decisions over stated facts: no test may move the clock, so a moved clock is stated, never made.
            value = "value read by intevia_test_clock_offset()"
            migrated = dict(owned_route.COUNTER_AS_MIGRATED)
            self.assertEqual(sorted(migrated), sorted(("type", "start", "increment", "minimum", "maximum", "cache", "cycle",
                                                       "ownership", value)),
                             "CLK-01 counter: the kept counter's check must name every attribute it compares.")
            with connection.cursor() as cursor:
                held = owned_route.counter_facts(cursor)
            self.assertEqual(held, migrated, "CLK-01 counter: this clone's counter must be exactly as the moving part's migration writes it.")
            others = {"type": ("integer",), "start": (1,), "increment": (2,), "minimum": (-1,), "maximum": (9007199254740992,),
                      "cache": (2,), "cycle": (True,), value: (1, 9007199254740991),
                      "ownership": ("a role that does not own the database, and no table column",
                                    "the database's owner, and a table column")}
            self.assertEqual(sorted(others), sorted(migrated), "CLK-01 counter: every attribute compared must be tried with another value.")
            for purpose in ("Reversing", "Installing"):
                self.assertIsNone(owned_route.counter_refusal(purpose, held),
                                  "CLK-01 counter (%s): the counter exactly as migrated, at zero, is kept." % purpose.lower())
                kept_anyway = [(attribute, other) for attribute, tried in others.items() for other in tried
                               if owned_route.counter_refusal(purpose, {**held, attribute: other}) is None]
                self.assertEqual(kept_anyway, [], "CLK-01 counter (%s): a counter that differs in any one attribute must be refused."
                                 % purpose.lower())
                unread = [attribute for attribute in migrated
                          if owned_route.counter_refusal(purpose, {k: v for k, v in held.items() if k != attribute}) is None]
                self.assertEqual(unread, [], "CLK-01 counter (%s): an attribute that was not read must be refused." % purpose.lower())
            moved = owned_route.counter_refusal("Reversing", {**held, value: 1})
            self.assertIsNotNone(moved, "CLK-01 reverse: a reversal never carries a moved clock.")
            self.assertIn("the clock is at 1 microseconds, not zero", moved, "CLK-01 reverse: the refusal must say the clock is not at zero.")
            self.assertIn("Nothing changed.", moved, "CLK-01 reverse: the refusal must say that nothing changed.")

            class Editor:
                """What a migration's callable is given: the connection it works on."""
                connection = connections["default"]

            # The migration's own reverse and forward, met by a counter whose configuration alone was changed.
            # The change is transactional and is rolled back with its block; the clock stays at zero throughout.
            not_as_migrated = "ALTER SEQUENCE public.intevia_test_clock_offset_us CACHE 2"
            with undone():
                with connection.cursor() as cursor:
                    cursor.execute(not_as_migrated)
                with self.assertRaises(owned_route.ClockRouteRefused,
                                       msg="CLK-01 reverse: the reverse must refuse a counter that is not as migrated.") as refused:
                    TEST_CLOCK.uninstall(None, Editor)
                self.assertIn("Reversing the INTEVIA Clock's moving part refused: the counter's cache is 2", str(refused.exception),
                              "CLK-01 reverse: the refusal must name the attribute that differs.")
            with undone():
                TEST_CLOCK.uninstall(None, Editor)
                with connection.cursor() as cursor:
                    self.assertEqual({name: PRODUCTION.installed(cursor, name) for name in written}, written,
                                     "CLK-01 reverse: the reverse must put the production faces back exactly.")
                    cursor.execute(not_as_migrated)
                with self.assertRaises(owned_route.ClockRouteRefused,
                                       msg="CLK-01 install: the forward must refuse a kept counter that is not as migrated.") as refused:
                    TEST_CLOCK.install(None, Editor)
                self.assertIn("Installing the INTEVIA Clock's moving part refused: the counter's cache is 2", str(refused.exception),
                              "CLK-01 install: the refusal must name the attribute that differs.")
            with connection.cursor() as cursor:
                self.assertEqual((owned_route.counter_facts(cursor), {name: PRODUCTION.installed(cursor, name) for name in written}),
                                 (migrated, marked), "CLK-01 counter: once each block is rolled back the clone is as it was.")
            self.assertEqual(owned_route.offset(), 0, "CLK-01: nothing in this witness may move the clock.")

    def test_each_face_keeps_its_built_in_meaning(self):
        """CLK-04, offset zero. With no offset, each face of the INTEVIA Clock, in storage and through the Python
        door, returns what the built-in it stands for returns at the same cut: as production writes the faces,
        and as the owned route writes them."""
        offset = timedelta(0)
        self.assertEqual(owned_route.offset(), 0, "CLK-04: this witness reads the clock at zero offset.")

        def observe(cursor):
            before = oracle.wall(cursor)
            with transaction.atomic():
                first = oracle.cuts(cursor)
                second = oracle.cuts(cursor)
                spread = oracle.spread(cursor, 5000)
                oracle.prepare(cursor)
                try:
                    saved_first = oracle.saved(cursor)
                    saved_second = oracle.saved(cursor)
                finally:
                    oracle.forget(cursor)
            return before, first, second, spread, saved_first, saved_second, oracle.wall(cursor)

        observed = {}
        with undone(), connection.cursor() as cursor:
            production_faces(cursor)
            observed["as production writes them"] = observe(cursor)
        with connection.cursor() as cursor:
            observed["as the owned route writes them"] = observe(cursor)
        for faces, (before, first, second, spread, saved_first, saved_second, after) in observed.items():
            readings = (("first statement", first), ("second statement", second),
                        ("saved plan, first run", saved_first), ("saved plan, second run", saved_second))
            for label, cut in readings:
                with self.subTest(faces=faces, cut=label):
                    self.assertEqual(cut.face_transaction, cut.native_transaction + offset,
                                     "CLK-04 %s, %s: the transaction face must be the transaction's start." % (faces, label))
                    self.assertEqual(cut.face_statement, cut.native_statement + offset,
                                     "CLK-04 %s, %s: the statement face must be this statement's start." % (faces, label))
                    self.assertEqual(cut.native_transaction, first.native_transaction,
                                     "CLK-04 %s, %s: every statement of one transaction shares its start." % (faces, label))
            with self.subTest(faces=faces):
                starts = [cut.native_statement for _label, cut in readings]
                self.assertEqual(starts, sorted(set(starts)),
                                 "CLK-04 %s: four separately sent statements must have four starts, in order, "
                                 "or this witness cannot tell the faces apart." % faces)
                self.assertLess(first.native_transaction, second.native_statement,
                                "CLK-04 %s: a later statement must start after its transaction, "
                                "or this witness cannot tell the faces apart." % faces)
                self.assertEqual((spread.statement_values, spread.face_statement), (1, spread.native_statement + offset),
                                 "CLK-04 %s, one statement, many rows: the statement face must give every row the statement's start." % faces)
                self.assertLess(spread.instant_min, spread.instant_max,
                                "CLK-04 %s, one statement, many rows: the instant face must advance inside the statement." % faces)
                self.assertLessEqual(spread.native_statement + offset, spread.instant_min,
                                     "CLK-04 %s, one statement, many rows: no instant may precede its statement's start." % faces)
                self.assertLessEqual(before + offset, spread.instant_min,
                                     "CLK-04 %s: the instant face must not read earlier than a wall reading taken before it." % faces)
                self.assertLessEqual(spread.instant_max, after + offset,
                                     "CLK-04 %s: the instant face must not read later than a wall reading taken after it." % faces)

        with self.subTest(door="python"):
            with connection.cursor() as cursor:
                door_before = oracle.wall(cursor)
            with CaptureQueriesContext(connection) as asked:
                default = clock.database_now()
                instant = clock.database_now("default", cut=clock.INSTANT)
                statement = clock.database_now("default", cut=clock.STATEMENT)
            with connection.cursor() as cursor:
                door_after = oracle.wall(cursor)
            self.assertEqual([query["sql"] for query in asked.captured_queries],
                             ["SELECT public.intevia_clock_instant()", "SELECT public.intevia_clock_instant()",
                              "SELECT public.intevia_clock_statement()"],
                             "CLK-04 door: by default the door reads the instant face, and the statement face when asked.")
            self.assertTrue(door_before + offset <= default <= instant <= door_after + offset,
                            "CLK-04 door: two instants must come in order between the wall readings around them.")
            self.assertTrue(door_before + offset <= statement <= door_after + offset,
                            "CLK-04 door: the statement cut must fall between the wall readings around it.")
            for unknown in ("transaction", "", None, [], {"statement": 1}):
                # The last two cannot even be looked up. The door must refuse them in its own words too.
                try:
                    clock.database_now("default", cut=unknown)
                    answered = "accepted"
                except ValueError as refused:
                    answered = "ValueError: %s" % refused
                except Exception as error:
                    answered = "%s: %s" % (type(error).__name__, error)
                self.assertEqual(answered, "ValueError: the INTEVIA Clock has no meaning named %r" % (unknown,),
                                 "CLK-04 door: a meaning the clock does not have must be refused, in the door's own words.")

        with self.subTest(door="mail"):
            with CaptureQueriesContext(connection) as asked:
                mail_now = mail.database_now()
            with connection.cursor() as cursor:
                mail_after = oracle.wall(cursor)
            self.assertEqual([query["sql"] for query in asked.captured_queries], ["SELECT public.intevia_clock_instant()"],
                             "CLK-04 mail: the mail helper must read the time through the one door.")
            self.assertTrue(door_after + offset <= mail_now <= mail_after + offset,
                            "CLK-04 mail: the mail helper's instant must fall between the wall readings around it.")
            with patch.object(connection, "vendor", "not-postgresql"), \
                    self.assertRaises(mail.MailRefused, msg="CLK-04 mail: the mail helper must still refuse a database that is not PostgreSQL.") as refused:
                mail.database_now()
            self.assertIn("PostgreSQL is required", str(refused.exception),
                          "CLK-04 mail: the refusal must keep its landed wording.")

    def test_the_clock_refuses_every_move_that_is_not_forward(self):
        """CLK-02, refusals. A move of nothing, a move backwards, a move to a time already past, a move with no
        amount or two, a move past the clock's range, a duration the clock could not hold and a time that is not
        aware are each refused, and the offset stays where it was."""
        self.assertEqual(owned_route.offset(), 0, "CLK-02: the clock must start this witness at zero.")
        shown = clock.database_now()
        refused = (
            ("a move of nothing", lambda: owned_route.advance(timedelta(0)), "the clock moves only forward"),
            ("a move backwards", lambda: owned_route.advance(-MICROSECOND), "the clock moves only forward"),
            ("a move to the time the clock already showed", lambda: owned_route.advance_to(shown), "the clock moves only forward"),
            ("a move to an hour ago", lambda: owned_route.advance_to(shown - timedelta(hours=1)), "the clock moves only forward"),
            ("a move past the clock's range", lambda: owned_route._move("default", TEST_CLOCK.CEILING + 1, None),
             "the largest offset the clock can hold"),
            ("a move with no amount", lambda: owned_route._move("default", None, None), "not both and not neither"),
            ("a move with two amounts", lambda: owned_route._move("default", 1, shown + timedelta(days=30)), "not both and not neither"),
        )
        for label, attempt, reason in refused:
            with self.subTest(move=label):
                with self.assertRaises(owned_route.ClockRouteRefused, msg="CLK-02 %s: must be refused." % label) as stopped:
                    attempt()
                self.assertIn(reason, str(stopped.exception), "CLK-02 %s: the refusal must say why." % label)
                self.assertIn("Nothing changed.", str(stopped.exception), "CLK-02 %s: the refusal must say nothing changed." % label)
                self.assertEqual(owned_route.offset(), 0, "CLK-02 %s: the offset must be unchanged." % label)
        for label, by, reason in (
                ("a duration one microsecond longer than the clock can hold", timedelta(microseconds=TEST_CLOCK.CEILING + 1),
                 "the largest offset the clock can hold"),
                ("the longest duration there is", timedelta.max, "the largest offset the clock can hold"),
                ("the longest duration backwards", timedelta.min, "the clock moves only forward")):
            with self.subTest(move=label):
                with CaptureQueriesContext(connection) as asked:
                    try:
                        owned_route.advance(by)
                        kind, words = "not refused", ""
                    except owned_route.ClockRouteRefused as stopped:
                        kind, words = "the owned route refused", str(stopped)
                    except Exception as error:
                        kind, words = "%s raised" % type(error).__name__, str(error).split("\n")[0]
                called = [query["sql"] for query in asked.captured_queries if "intevia_test_clock_advance" in query["sql"]]
                self.assertEqual((kind, reason in words, "Nothing changed." in words, called, owned_route.offset()),
                                 ("the owned route refused", True, True, [], 0),
                                 "CLK-02 %s: the helper must refuse it in INTEVIA's words, and storage must not be called: %r" % (label, words))

        class NoOffset(tzinfo):
            """A zone that gives no offset: Django judges a time that carries it not aware."""

            def utcoffset(self, moment):
                return None

            def dst(self, moment):
                return None

            def tzname(self, moment):
                return "no offset"

        # The last is two days back, so that no reading of it, in any zone, could lie ahead of the clock.
        for label, attempt in (("a number, not a duration", lambda: owned_route.advance(1)),
                               ("a time with no zone", lambda: owned_route.advance_to(shown.replace(tzinfo=None))),
                               ("a time whose zone gives no offset",
                                lambda: owned_route.advance_to((shown - timedelta(days=2)).replace(tzinfo=NoOffset())))):
            with self.subTest(move=label):
                try:
                    attempt()
                    answered = "not refused"
                except TypeError:
                    answered = "TypeError"
                except Exception as error:
                    answered = "%s: %s" % (type(error).__name__, str(error).split("\n")[0])
                self.assertEqual((answered, owned_route.offset()), ("TypeError", 0),
                                 "CLK-02 %s: must be refused as the wrong kind of argument, with the offset unchanged." % label)
        self.assertEqual(owned_route.offset(), 0, "CLK-02: after every refusal the offset must still be zero.")

    def test_no_transaction_can_straddle_a_move(self):
        """CLK-06, refusals. A move is refused while any other transaction is open, as the move can see it, while it
        cannot tell whether another connection has one open, and while any transaction that has read the clock is
        open; a move can only stand alone; and no transaction is admitted to the clock while a move holds it. The
        offset stays where it was."""
        self.assertEqual(owned_route.offset(), 0, "CLK-06: the clock must start this witness at zero.")
        # Every move asked for here is a move of nothing. Each case asserts the refusal's own reason, which storage
        # reaches before it looks at the amount; and were that refusal ever missing, storage would still refuse the
        # amount. So no break of this witness can move the clock.
        nothing = timedelta(0)
        call = "CALL public.intevia_test_clock_advance(0, NULL, NULL)"
        alone = "inside an open transaction; the clock moves only between transactions"
        open_elsewhere = "another connection has a transaction open; the clock moves only between transactions"
        not_read = ("another connection's transaction status could not be read: its activity reporting is off, or its state "
                    "shows no transaction start. The clock moves only while every other connection is seen to be idle.")
        not_forward = "the clock moves only forward"

        def refused(attempt):
            """What an attempt to move was answered: the refusal's kind and words, or that it was not refused."""
            try:
                attempt()
            except owned_route.ClockRouteRefused as stopped:
                return ("the owned route refused", str(stopped))
            except Exception as error:
                state = getattr(error, "sqlstate", None) or getattr(error.__cause__, "sqlstate", None)
                return ("the database refused, SQLSTATE %s" % state,
                        "%s %s" % (error, getattr(getattr(error, "diag", None), "context", "") or ""))
            return ("not refused", "")

        with self.subTest(open="an outer transaction on this connection"), transaction.atomic():
            with self.assertRaises(owned_route.ClockRouteRefused, msg="CLK-06 outer transaction: the helper must refuse.") as stopped, \
                    transaction.atomic():
                owned_route.advance(nothing)
            self.assertIn("has a transaction open", str(stopped.exception),
                          "CLK-06 outer transaction: the helper's own refusal must come first, naming the open transaction.")
            with self.assertRaises(DatabaseError, msg="CLK-06 outer transaction: storage must refuse too.") as stored, \
                    transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(call)
            self.assertEqual((getattr(stored.exception.__cause__, "sqlstate", None), "intevia_test_clock_advance" in str(stored.exception)),
                             (owned_route.INSIDE_A_TRANSACTION, True),
                             "CLK-06 outer transaction: storage's own refusal must be PostgreSQL's, of the move's COMMIT: %s" % stored.exception)
        with self.subTest(open="a savepoint on this connection"), transaction.atomic(), transaction.atomic():
            with self.assertRaises(owned_route.ClockRouteRefused, msg="CLK-06 savepoint: the helper must refuse."), transaction.atomic():
                owned_route.advance(nothing)
        with self.subTest(open="a manual transaction on this connection"):
            connection.set_autocommit(False)
            try:
                with self.assertRaises(owned_route.ClockRouteRefused, msg="CLK-06 manual transaction: the helper must refuse."):
                    owned_route.advance(nothing)
            finally:
                connection.rollback()
                connection.set_autocommit(True)
        self.assertEqual(owned_route.offset(), 0, "CLK-06: this connection's refusals must leave the offset unchanged.")

        # Transactions that Django does not know of, opened on the connection's own raw session.
        connection.ensure_connection()
        raw = connection.connection

        def end_raw():
            raw.execute("ROLLBACK")

        with self.subTest(open="a raw transaction on this connection that has already read the clock"):
            raw.execute("BEGIN")
            try:
                raw.execute("SELECT public.intevia_clock_statement()")
                kind, words = refused(lambda: raw.execute(call))
                self.assertEqual((kind, "intevia_test_clock_advance" in words and "at COMMIT" in words),
                                 ("the database refused, SQLSTATE 2D000", True),
                                 "CLK-06 raw transaction: PostgreSQL itself must refuse the COMMIT of a move asked for by a transaction "
                                 "that has read the clock: %r" % words)
                raw.execute("ROLLBACK")
                raw.execute("BEGIN")
                raw.execute("SELECT public.intevia_clock_statement()")
                kind, words = refused(lambda: owned_route.advance(nothing))
            finally:
                end_raw()
            self.assertEqual((kind, words, alone in words), ("the owned route refused", owned_route.INSIDE_A_TRANSACTION_TEXT, True),
                             "CLK-06 raw transaction: through the helper the refusal must name the open transaction, in INTEVIA's words.")
        with self.subTest(open="one message that opens a transaction and asks for the move, so that their starts coincide"):
            try:
                kind, words = refused(lambda: raw.execute("BEGIN; " + call))
            finally:
                end_raw()
            self.assertEqual(kind, "the database refused, SQLSTATE 2D000",
                             "CLK-06 one message: PostgreSQL itself must refuse the move's COMMIT inside a transaction block: %r" % words)
            self.assertIn("intevia_test_clock_advance", words, "CLK-06 one message: PostgreSQL's refusal must name the move.")
            self.assertIn("at COMMIT", words, "CLK-06 one message: PostgreSQL's refusal must be of the move's own COMMIT.")
            try:
                with patch.object(owned_route, "MOVE_SQL", "BEGIN; " + owned_route.MOVE_SQL):
                    kind, words = refused(lambda: owned_route.advance(nothing))
            finally:
                end_raw()
            self.assertEqual((kind, words), ("the owned route refused", owned_route.INSIDE_A_TRANSACTION_TEXT),
                             "CLK-06 one message: through the helper, PostgreSQL's refusal must be told in INTEVIA's words.")
        with self.subTest(open="one message whose first statement is not the move"):
            try:
                kind, words = refused(lambda: raw.execute("SELECT 1; " + call))
            finally:
                end_raw()
            self.assertEqual(kind, "the database refused, SQLSTATE 2D000",
                             "CLK-06 implicit block: a move that is not alone in its message must be refused by PostgreSQL itself: %r" % words)
        self.assertEqual(owned_route.offset(), 0, "CLK-06: the raw transactions' refusals must leave the offset unchanged.")

        peer = self.peer()
        with self.subTest(open="another connection, idle in a transaction that has not read the clock"):
            peer.set_autocommit(False)
            try:
                with peer.cursor() as cursor:
                    cursor.execute("SELECT 1")
                with self.assertRaises(owned_route.ClockRouteRefused,
                                       msg="CLK-06 idle peer: a move must be refused while another connection's transaction is open.") as stopped:
                    owned_route.advance(nothing)
                self.assertIn(open_elsewhere, str(stopped.exception), "CLK-06 idle peer: the open transaction must be the reason.")
            finally:
                peer.rollback()
                peer.set_autocommit(True)
        with self.subTest(open="another connection, in a transaction that has read the clock"):
            peer.set_autocommit(False)
            try:
                with peer.cursor() as cursor:
                    cursor.execute("SELECT public.intevia_clock_statement()")
                with self.assertRaises(owned_route.ClockRouteRefused,
                                       msg="CLK-06 reader: a move must be refused while a transaction that read the clock is open.") as stopped:
                    owned_route.advance(nothing)
                self.assertIn("a transaction that has read the clock is open", str(stopped.exception),
                              "CLK-06 reader: the clock's own admission must be the reason, before any other check.")
            finally:
                peer.rollback()
                peer.set_autocommit(True)
        with self.subTest(open="another connection, whose read of the clock was rolled back to a savepoint"):
            first, second = (int(half) for half in TEST_CLOCK.ADMISSION.split(","))
            admitted = ("SELECT count(*) FROM pg_catalog.pg_locks WHERE locktype = 'advisory' AND classid = %s AND objid = %s "
                        "AND objsubid = 2 AND granted AND pid = pg_catalog.pg_backend_pid()")
            peer.set_autocommit(False)
            try:
                with peer.cursor() as cursor:
                    cursor.execute("SAVEPOINT clk06")
                    cursor.execute("SELECT public.intevia_clock_statement()")
                    cursor.execute(admitted, [first, second])
                    held_under = cursor.fetchone()[0]
                    cursor.execute("ROLLBACK TO SAVEPOINT clk06")
                    cursor.execute(admitted, [first, second])
                    held_after = cursor.fetchone()[0]
                self.assertEqual((held_under, held_after), (1, 0),
                                 "CLK-06 savepoint peer: the read must take admission, and the rollback to the savepoint must release it, "
                                 "or this case does not show what it claims.")
                kind, words = refused(lambda: owned_route.advance(nothing))
                self.assertEqual((kind, open_elsewhere in words), ("the owned route refused", True),
                                 "CLK-06 savepoint peer: with its admission gone the transaction is still open, and the move must be "
                                 "refused for that reason: %r" % words)
            finally:
                peer.rollback()
                peer.set_autocommit(True)

        # What the move really observes of a peer: the activity view itself, not stated facts. A peer can turn its own
        # activity reporting off; the view then shows no transaction start whether or not it has a transaction open.
        with peer.cursor() as cursor:
            cursor.execute("SELECT pg_catalog.pg_backend_pid()")
            peer_pid = cursor.fetchone()[0]

        def shown():
            """What the activity view shows this role of the peer: its state, and whether a transaction start is shown."""
            with connection.cursor() as cursor:
                cursor.execute("SELECT a.state, a.xact_start IS NOT NULL FROM pg_catalog.pg_stat_activity a WHERE a.pid = %s", [peer_pid])
                return cursor.fetchone()

        def answered():
            """How a move of nothing was answered: stopped at the peer, and for which reason, or let past it to the amount."""
            kind, words = refused(lambda: owned_route.advance(nothing))
            return (kind, "status not read" if not_read in words else "a transaction open" if open_elsewhere in words
                    else "let past the peer, to the amount" if not_forward in words else words)

        def reporting(setting):
            with peer.cursor() as cursor:
                cursor.execute("SET track_activities = %s" % setting)

        with self.subTest(observed="another connection, seen to be idle"):
            self.assertEqual((shown(), answered()), (("idle", False), ("the owned route refused", "let past the peer, to the amount")),
                             "CLK-06 idle peer observed: a peer the view shows idle must not stop a move; the move of nothing must be "
                             "refused only for its amount.")
        with self.subTest(observed="another connection with its activity reporting off"):
            try:
                reporting("off")
            except DatabaseError as error:
                self.fail("CLK-06 unreadable peer: this witness needs a test role that may turn activity reporting off (a superuser, "
                          "or a role granted SET on the parameter track_activities). It does not skip: %s" % str(error).split("\n")[0])
            try:
                peer.set_autocommit(False)
                with peer.cursor() as cursor:
                    cursor.execute("SAVEPOINT clk06")
                    cursor.execute("SELECT public.intevia_clock_statement()")
                    cursor.execute("ROLLBACK TO SAVEPOINT clk06")
                    cursor.execute(admitted, [first, second])
                    held_after = cursor.fetchone()[0]
                in_a_transaction = (held_after, shown(), answered())
                with peer.cursor() as cursor:
                    cursor.execute("SET track_activities = on")
                reporting_back_on_inside = (shown(), answered())
                peer.rollback()
                peer.set_autocommit(True)
                no_transaction = (shown(), answered())
            finally:
                peer.rollback()
                peer.set_autocommit(True)
                reporting("on")
            stopped = ("the owned route refused", "status not read")
            self.assertEqual(in_a_transaction, (0, ("disabled", False), stopped),
                             "CLK-06 unreadable peer: a peer with reporting off, inside a transaction that read the clock under a "
                             "savepoint it rolled back, holds no admission and shows no transaction start; the move must be refused "
                             "because its status could not be read.")
            self.assertEqual(reporting_back_on_inside, (("idle in transaction", False), stopped),
                             "CLK-06 unreadable peer: reporting turned back on inside that transaction shows a state and still no "
                             "start; the move must still be refused.")
            self.assertEqual(no_transaction, (("disabled", False), stopped),
                             "CLK-06 unreadable peer: with reporting off and no transaction the peer still cannot be read, and the "
                             "move must be refused.")
            self.assertEqual((shown(), answered()), (("idle", False), ("the owned route refused", "let past the peer, to the amount")),
                             "CLK-06 unreadable peer: once the peer reports again and is seen idle, it must no longer stop a move.")
        self.assertEqual(owned_route.offset(), 0, "CLK-06: the peers' refusals must leave the offset unchanged.")

        with self.subTest(open="another role's session, as storage decides it over stated facts"):
            # The suite's clone has one role. What the move decides for each other session is one function of stated facts.
            other_role = ("another role is connected to this database. The move sees only its own role's open transactions, "
                          "so the clock moves only while the database's owning role is the only role connected.")
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT stated.label, public.intevia_test_clock_obstacle(stated.mover, stated.session_role, stated.transaction_open)
                         FROM (VALUES ('this role, no transaction open', 10::oid, 10::oid, false),
                                      ('this role, a transaction open', 10, 10, true),
                                      ('this role, and whether a transaction is open was not read', 10, 10, NULL),
                                      ('another role, no transaction seen', 10, 11, false),
                                      ('another role, a transaction seen', 10, 11, true),
                                      ('a background process, which has no role', 10, NULL, false),
                                      ('the mover''s own role not read', NULL, 10, false))
                              AS stated(label, mover, session_role, transaction_open)""")
                decided = dict(cursor.fetchall())
            self.assertEqual(decided, {
                "this role, no transaction open": None,
                "this role, a transaction open": open_elsewhere + ".",
                "this role, and whether a transaction is open was not read": not_read,
                "another role, no transaction seen": other_role,
                "another role, a transaction seen": other_role,
                "a background process, which has no role": None,
                "the mover's own role not read": "the mover's own role could not be read.",
            }, "CLK-06 other role: another role's session must stop a move whether or not a transaction is seen on it.")

        with self.subTest(race="a transaction that starts while a move holds the clock"):
            # This connection holds the clock exactly as a move does, for as long as the witness needs: no luck involved.
            with transaction.atomic(), connection.cursor() as holder:
                holder.execute("SELECT pg_catalog.pg_advisory_xact_lock(%s)" % TEST_CLOCK.ADMISSION)
                peer.set_autocommit(False)
                try:
                    with peer.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '50ms'")
                        with self.assertRaises(OperationalError,
                                               msg="CLK-06 race: a transaction must not be admitted to the clock while a move holds it."):
                            cursor.execute("SELECT public.intevia_clock_statement()")
                finally:
                    peer.rollback()
                    peer.set_autocommit(True)
            with peer.cursor() as cursor:
                cursor.execute("SELECT public.intevia_test_clock_offset(), public.intevia_clock_statement() IS NOT NULL")
                self.assertEqual(cursor.fetchone(), (0, True), "CLK-06 race: once the clock is released the peer reads it, at the same offset.")
        self.assertEqual(owned_route.offset(), 0, "CLK-06: nothing in this witness may move the clock.")

    def test_the_reset_returns_the_clock_inside_the_database_reset(self):
        """CLK-07, order and failure. The database reset returns the clock to zero inside its own transaction,
        after the tables are empty and before any seed is restored; a reset that fails puts the clock back with the
        rows; storage alone decides what the reset's emptiness check leaves out; and the reset is refused for
        whatever would refuse a move, and to a transaction that has itself read the clock, so that no transaction
        reads two offsets across it."""
        self.assertEqual(owned_route.offset(), 0, "CLK-07: the clock must start this witness at zero.")

        def counter_file():
            with connection.cursor() as cursor:
                cursor.execute(COUNTER_FILE)
                return cursor.fetchone()[0]

        def flush():
            call_command("flush", verbosity=0, interactive=False, database="default", reset_sequences=False,
                         allow_cascade=False, inhibit_post_migrate=False)

        Session.objects.create(session_key="clk07", session_data="held", expire_date=clock.database_now())
        original = counter_file()
        with self.subTest(order="the clock is not reset while a row remains"):
            with self.assertRaises(Exception, msg="CLK-07 scope: the reset must refuse while its scope holds a row.") as stopped, \
                    transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("SELECT public.intevia_test_clock_reset()")
            self.assertIn("the reset's scope is not empty", str(stopped.exception), "CLK-07 scope: the rows must be the reason.")
            self.assertEqual((counter_file(), Session.objects.filter(session_key="clk07").count()), (original, 1),
                             "CLK-07 scope: a refused reset must leave the counter and the row as they were.")

        def unchanged():
            return (counter_file(), Session.objects.filter(session_key="clk07").count(), owned_route.offset())

        with self.subTest(scope="the caller cannot choose what the reset leaves out"):
            self.assertEqual(TEST_CLOCK.RESET_KEPT, tuple(sorted(operations.S015_RESET_EXCLUDED_TABLES)),
                             "CLK-07 exempt set: what storage leaves out must be exactly what the lifecycle backend's reset leaves out.")
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT pg_catalog.pg_get_function_identity_arguments(p.oid), p.prosrc FROM pg_catalog.pg_proc p
                         JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
                        WHERE n.nspname = 'public' AND p.proname = 'intevia_test_clock_reset' ORDER BY 1""")
                forms = cursor.fetchall()
            self.assertEqual([arguments for arguments, _body in forms], [""],
                             "CLK-07 exempt set: the reset must exist in one form, and that form takes no argument.")
            self.assertIn("c.relname::text <> ALL (ARRAY['django_migrations']::text[])", forms[0][1],
                          "CLK-07 exempt set: the reset's body must hold the fixed exempt set, with no NULL in it.")
            lists = (("a list that leaves out the session table and a business table", ["django_migrations", "django_session", "core_role"]),
                     ("an empty list", []), ("a list holding NULL", [None]), ("a list holding the ledger and NULL", ["django_migrations", None]),
                     ("no list", None))
            answers = {}
            for label, given in lists:
                try:
                    with transaction.atomic(), connection.cursor() as cursor:
                        cursor.execute("SELECT public.intevia_test_clock_reset(%s::text[])", [given])
                    answers[label] = "the reset ran"
                except DatabaseError as error:
                    answers[label] = getattr(error.__cause__, "sqlstate", None)
                self.assertEqual(unchanged(), (original, 1, 0),
                                 "CLK-07 exempt set (%s): the row, the counter and the offset must be as they were." % label)
            self.assertEqual(answers, {label: "42883" for label, _given in lists},
                             "CLK-07 exempt set: no form of the reset takes a list, so each of these calls must find no such function.")

        with self.subTest(scope="a business table holds a row when the reset reaches the clock"):
            clock_step = operations.DatabaseOperations._clock_to_real_time
            reached = []
            given = clock.database_now()

            def occupied(backend):
                # Inside the reset's own transaction, after its truncation: one row in a business table, then the real step.
                with backend.connection.cursor() as cursor:
                    # The row's time is given, not read here: a face read inside the reset's transaction, before its
                    # clock step, is itself refused by storage (the own-transaction cases below).
                    cursor.execute("INSERT INTO public.core_role (name, description, created_at) VALUES ('clk07', 'CLK-07', %s)", [given])
                    cursor.execute("SELECT count(*) FROM public.django_session")
                    reached.append((cursor.fetchone()[0], backend.connection.in_atomic_block))
                return clock_step(backend)

            with patch.object(operations.DatabaseOperations, "_clock_to_real_time", occupied), \
                    self.assertRaises(Exception, msg="CLK-07 business table: the reset must fail while a business table holds a row.") as stopped:
                flush()
            self.assertEqual(reached, [(0, True)],
                             "CLK-07 business table: the row must have been added inside the reset's transaction, after its truncation.")
            self.assertIn("the reset's scope is not empty (core_role holds rows)", "%s %s" % (stopped.exception, stopped.exception.__cause__),
                          "CLK-07 business table: the occupied business table must be the reason, by name.")
            with connection.cursor() as cursor:
                cursor.execute("SELECT count(*) FROM public.core_role WHERE name = 'clk07'")
                added = cursor.fetchone()[0]
            self.assertEqual((unchanged(), added), ((original, 1, 0), 0),
                             "CLK-07 business table: the refused reset must be undone whole: the session row back, the added row gone, "
                             "the counter's file unchanged and the offset zero.")

        seen = []
        module_name, function_name = operations.S015_MIGRATION_SEEDS[0]
        first_seed = getattr(import_module(module_name), function_name)
        last_module, last_function = operations.S015_MIGRATION_SEEDS[-1]

        def watched_seed(apps, schema_editor):
            seen.append((owned_route.offset(), counter_file(), Session.objects.count(), connection.in_atomic_block))
            return first_seed(apps, schema_editor)

        def failing_seed(apps, schema_editor):
            raise RuntimeError("CLK-07: a seed restorer failed after the clock was reset")

        with self.subTest(failure="a seed restorer fails after the clock is reset"):
            with patch.object(import_module(module_name), function_name, watched_seed), \
                    patch.object(import_module(last_module), last_function, failing_seed), \
                    self.assertRaises(Exception, msg="CLK-07 failure: the failed restoration must fail the reset.") as failed:
                flush()
            self.assertIn("a seed restorer failed after the clock was reset", "%s %r" % (failed.exception, failed.exception.__cause__),
                          "CLK-07 failure: the reset must fail for the forced reason and no other.")
            self.assertEqual(len(seen), 1, "CLK-07 failure: the first seed restorer must have run once.")
            at_seed_offset, at_seed_file, at_seed_rows, in_transaction = seen[0]
            self.assertEqual((at_seed_offset, at_seed_rows, in_transaction), (0, 0, True),
                             "CLK-07 order: when the first seed is restored the tables are empty, the offset is zero, and the reset's transaction is open.")
            self.assertNotEqual(at_seed_file, original,
                                "CLK-07 order: the counter must already have been reset, inside the transaction, before the first seed.")
            self.assertEqual((counter_file(), Session.objects.filter(session_key="clk07").count(), owned_route.offset()), (original, 1, 0),
                             "CLK-07 failure: the failed reset must put the counter back as it was, with the row.")
            self.assertIs(owned_route.authorise_advance("default"), connections["default"],
                          "CLK-07 failure: the failed reset must leave no transaction open.")

        # D8 covers the reset. What follows goes through the real database reset and the real activity view, at offset
        # zero: nothing here moves the clock. A peer of this role stops the reset exactly as it would stop a move.
        not_reset = "the clock is not reset, for the reason that would stop a move: "
        open_elsewhere = "another connection has a transaction open; the clock moves only between transactions."
        not_read = ("another connection's transaction status could not be read: its activity reporting is off, or its state "
                    "shows no transaction start. The clock moves only while every other connection is seen to be idle.")
        view = "SELECT a.state, a.xact_start IS NOT NULL FROM pg_catalog.pg_stat_activity a WHERE a.pid = %s"
        first, second = (int(half) for half in TEST_CLOCK.ADMISSION.split(","))
        admitted = ("SELECT count(*) FROM pg_catalog.pg_locks WHERE locktype = 'advisory' AND classid = %s AND objid = %s "
                    "AND objsubid = 2 AND granted AND pid = pg_catalog.pg_backend_pid()")
        peer = self.peer()
        with peer.cursor() as cursor:
            cursor.execute("SELECT pg_catalog.pg_backend_pid()")
            peer_pid = cursor.fetchone()[0]

        def shown():
            """What the activity view shows this role of the peer: its state, and whether a transaction start is shown."""
            with connection.cursor() as cursor:
                cursor.execute(view, [peer_pid])
                return cursor.fetchone()

        def flushed():
            """How the real database reset was answered: done, or refused and for which reason."""
            try:
                flush()
            except Exception as error:
                words = "%s %s" % (error, error.__cause__)
                if not_reset in words:
                    return "refused, the clock not reset: " + words[words.index(not_reset) + len(not_reset):].split(" Nothing changed.")[0]
                return "refused otherwise: " + words
            return "done"

        with self.subTest(peer="in a transaction whose read of the clock was rolled back to a savepoint"):
            peer.set_autocommit(False)
            try:
                with peer.cursor() as cursor:
                    cursor.execute("SAVEPOINT clk07")
                    cursor.execute("SELECT public.intevia_clock_statement()")
                    cursor.execute("ROLLBACK TO SAVEPOINT clk07")
                    cursor.execute(admitted, [first, second])
                    held_after = cursor.fetchone()[0]
                answered = (held_after, shown(), flushed(), unchanged())
            finally:
                peer.rollback()
                peer.set_autocommit(True)
            self.assertEqual(answered, (0, ("idle in transaction", True), "refused, the clock not reset: " + open_elsewhere, (original, 1, 0)),
                             "CLK-07 open peer: a peer that holds no admission and is still inside its transaction must stop the "
                             "database reset, in words that say the clock is not reset, with the row, the counter and the offset as "
                             "they were.")
        with self.subTest(peer="with its activity reporting off"):
            try:
                with peer.cursor() as cursor:
                    cursor.execute("SET track_activities = off")
            except DatabaseError as error:
                self.fail("CLK-07 unreadable peer: this witness needs a test role that may turn activity reporting off (a superuser, "
                          "or a role granted SET on the parameter track_activities). It does not skip: %s" % str(error).split("\n")[0])
            try:
                no_transaction = (shown(), flushed(), unchanged())
                peer.set_autocommit(False)
                with peer.cursor() as cursor:
                    cursor.execute("SELECT 1")
                in_a_transaction = (shown(), flushed(), unchanged())
            finally:
                peer.rollback()
                peer.set_autocommit(True)
                with peer.cursor() as cursor:
                    cursor.execute("SET track_activities = on")
            stopped = (("disabled", False), "refused, the clock not reset: " + not_read, (original, 1, 0))
            self.assertEqual((no_transaction, in_a_transaction), (stopped, stopped),
                             "CLK-07 unreadable peer: a peer whose transaction status cannot be read must stop the database reset, "
                             "inside a transaction or not, with the database left as it was.")
        with self.subTest(peer="that opens its transaction after the reset's own transaction first read the activity view"):
            read_before = []

            def after_an_earlier_read(backend):
                # Inside the reset's transaction: the view is read while the peer is idle, and the peer then opens a
                # transaction. PostgreSQL shows a transaction the view as it stood at that transaction's first read.
                with backend.connection.cursor() as cursor:
                    cursor.execute(view, [peer_pid])
                    earlier = cursor.fetchone()
                peer.set_autocommit(False)
                with peer.cursor() as cursor:
                    cursor.execute("SELECT 1")
                with backend.connection.cursor() as cursor:
                    cursor.execute(view, [peer_pid])
                    read_before.append((earlier, cursor.fetchone(), backend.connection.in_atomic_block))
                return clock_step(backend)

            try:
                with patch.object(operations.DatabaseOperations, "_clock_to_real_time", after_an_earlier_read):
                    answered = (flushed(), unchanged())
            finally:
                peer.rollback()
                peer.set_autocommit(True)
            self.assertEqual((read_before, answered),
                             ([(("idle", False), ("idle", False), True)], ("refused, the clock not reset: " + open_elsewhere, (original, 1, 0))),
                             "CLK-07 stale view: the reset's transaction still sees the peer idle, as it first read it; the reset "
                             "must read the view afresh, and be refused for the transaction the peer has opened since.")

        # The transaction that resets must not itself have read the clock (the Advisor's finding on R4). At offset zero.
        by_hand = "S015 reset must not run while its connection has autocommit off"
        own_read = "this transaction has itself read the clock; the clock is not reset inside a transaction that has read it."
        with self.subTest(resetter="read the clock in a transaction it manages by hand, then asks for the database reset"):
            connection.set_autocommit(False)
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT public.intevia_clock_statement()")
                    cursor.execute(admitted, [first, second])
                    held_before = cursor.fetchone()[0]
                with CaptureQueriesContext(connection) as asked:
                    try:
                        flush()
                        answer = "done"
                    except Exception as error:
                        words = "%s %s" % (error, error.__cause__)
                        answer = "refused before any change, for autocommit off" if by_hand in words else "refused otherwise: " + words
                reached = sorted({word for query in asked.captured_queries for word in ("TRUNCATE", "intevia_test_clock_reset")
                                  if word in query["sql"]})
                as_django_saw_it = (connection.in_atomic_block, connection.get_autocommit())
            finally:
                connection.rollback()
                connection.set_autocommit(True)
            self.assertEqual((held_before, as_django_saw_it, answer, reached, unchanged()),
                             (1, (False, False), "refused before any change, for autocommit off", [], (original, 1, 0)),
                             "CLK-07 own transaction: a connection that read the clock in a transaction no atomic block knows of must be "
                             "refused the database reset before any table changes, with the row, the counter's file and the offset as "
                             "they were.")
        with self.subTest(resetter="read the clock in its own transaction, then calls storage's reset itself"):
            try:
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute("SELECT public.intevia_clock_statement()")
                    cursor.execute("SELECT public.intevia_test_clock_reset()")
                answer = "the reset ran"
            except DatabaseError as error:
                answer = str(error).split("\n")[0]
            self.assertEqual((own_read in answer, "the reset's scope" in answer, unchanged()), (True, False, (original, 1, 0)),
                             "CLK-07 own transaction, storage: the reset must refuse a transaction that has itself read the clock, "
                             "for that reason and before any other: %r" % answer)
        with self.subTest(resetter="the database reset itself, were a face read ever placed in its transaction before its clock step"):
            def after_a_face_read(backend):
                with backend.connection.cursor() as cursor:
                    cursor.execute("SELECT public.intevia_clock_statement()")
                return clock_step(backend)

            with patch.object(operations.DatabaseOperations, "_clock_to_real_time", after_a_face_read):
                answer = flushed()
            self.assertEqual((own_read in answer, unchanged()), (True, (original, 1, 0)),
                             "CLK-07 own transaction, lifecycle: the database reset's own transaction must read no face before the "
                             "clock is reset; storage must refuse it if it has, and the reset is undone whole: %r" % answer)

        with self.subTest(order="a reset that succeeds"):
            self.assertEqual(shown(), ("idle", False),
                             "CLK-07: the peer must be connected and seen idle here, so that this reset shows an idle peer is no obstacle.")
            del seen[:]
            with patch.object(import_module(module_name), function_name, watched_seed):
                flush()
            self.assertEqual([(offset, rows, inside) for offset, _file, rows, inside in seen], [(0, 0, True)],
                             "CLK-07 order: the clock is at zero, in the reset's own transaction, when the first seed is restored.")
            self.assertNotEqual(seen[0][1], original, "CLK-07 order: the counter was reset before the first seed.")
            self.assertEqual((counter_file(), Session.objects.count(), owned_route.offset()), (seen[0][1], 0, 0),
                             "CLK-07: a reset that succeeds keeps the counter it reset, at zero, with the database empty.")

    def test_a_database_with_the_clock_app_must_hold_the_counter(self):
        """CLK-09. At the isolation instrument's first checkpoint, a database whose migration ledger records the
        clock's test-only app must hold the clock's counter; a database whose ledger does not may hold none."""
        missing = "the INTEVIA Clock's counter is missing from a database that must hold it"
        reference = instrument.capture()
        light = instrument.capture_light(instrument.build_light_plan(reference))
        applied = [row for row in reference["migrations"] if row[0] == startup.TEST_CLOCK_APP]
        self.assertEqual((applied, reference["intevia_clock_counter"] is not None, light["clock_counter"]),
                         ([(startup.TEST_CLOCK_APP, "0001_moving_part")], True, reference["intevia_clock_counter"]),
                         "CLK-09: this clone's own capture must record the clock's app and hold its counter, in the full capture and the light one.")
        self.assertEqual(instrument.CLOCK_APP, startup.TEST_CLOCK_APP, "CLK-09: the instrument must name the same app as the startup refusal.")

        def first_checkpoint(app_applied, counter_present):
            """What the two first-checkpoint checks say of the clock, for this real capture with the two facts as stated."""
            full, seen = dict(reference), dict(light)
            if not app_applied:
                full["migrations"] = [row for row in reference["migrations"] if row[0] != startup.TEST_CLOCK_APP]
            if not counter_present:
                full["intevia_clock_counter"] = None
                seen["clock_counter"] = None
            return ([text for text in instrument.check_requirements(full, operations.S015_TRUNCATE_GUARDIANS) if text.startswith("REQ-CLK ")],
                    [text for text in instrument.light_baseline_violations(full, seen) if text.startswith("CP-0 Q5 ")])

        self.assertEqual(first_checkpoint(True, True), ([], []),
                         "CLK-09 as captured: the app applied and the counter present, at zero, is no violation.")
        requirements, light_check = first_checkpoint(True, False)
        self.assertEqual((len(requirements), len(light_check)), (1, 1),
                         "CLK-09 app applied, counter missing: each of the two first-checkpoint checks must report it, once.")
        for text in requirements + light_check:
            self.assertIn(missing, text, "CLK-09 app applied, counter missing: the violation must say the counter is missing.")
            self.assertIn(startup.TEST_CLOCK_APP, text, "CLK-09 app applied, counter missing: the violation must name the app.")
        self.assertEqual(first_checkpoint(False, False), ([], []),
                         "CLK-09 production shape: a database whose ledger does not record the app may hold no counter.")
        self.assertEqual(first_checkpoint(False, True), ([], []),
                         "CLK-09 below the clock: a counter kept at zero, as migrated, where the app is not applied is no violation.")

        with self.subTest(against="a baseline that held the counter"):
            lost, lost_light = dict(reference, intevia_clock_counter=None), dict(light, clock_counter=None)
            data, _protection = instrument.compare(reference, lost)
            self.assertEqual([text for text in data if "INTEVIA Clock" in text and missing not in text and "differs from reference" not in text], [],
                             "CLK-09 baseline: a lost counter is reported as lost, and as nothing else.")
            self.assertEqual((sum(missing in text for text in data), sum("differs from reference" in text for text in data)), (1, 1),
                             "CLK-09 baseline: the full comparison must report a counter lost since the baseline, as missing and as different.")
            self.assertEqual(sorted(text for text in instrument.compare_light(light, lost_light) if text.startswith("Q5 ")),
                             sorted(["Q5 " + instrument.clock_counter_violations(None, True)[0], "Q5 the INTEVIA Clock's counter differs"]),
                             "CLK-09 baseline: the light comparison must report it too.")
            self.assertEqual(instrument.compare_light(dict(light, clock_counter=None), lost_light), [],
                             "CLK-09 baseline: where the baseline held no counter, none is required by the light comparison.")
        self.assertEqual(owned_route.offset(), 0, "CLK-09: nothing in this witness may move the clock.")

    def test_a_reversal_below_the_clock_returns_whole_and_keeps_the_mark(self):
        """CLK-08. An owned clone reversed below the clock's migration and restored returns to exactly the state it
        had, at offset zero, and at every point in between production's startup refuses it."""
        counter_found = "the test route's relation public.intevia_test_clock_offset_us is present"
        other_marks = ("the test route's function", "the test route's procedure", "the migration ledger records", "the face public.")

        def faces():
            with connection.cursor() as cursor:
                return {name: PRODUCTION.installed(cursor, name) for name, *_rest in PRODUCTION.FACES}

        def ledger():
            with connection.cursor() as cursor:
                cursor.execute("SELECT app, name FROM public.django_migrations ORDER BY app, name")
                return cursor.fetchall()

        def moving_part():
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT p.proname::text FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
                        WHERE n.nspname = 'public' AND pg_catalog.starts_with(p.proname::text, %s) ORDER BY 1""",
                    [startup.TEST_CLOCK_PREFIX])
                return [row[0] for row in cursor.fetchall()]

        def production_startup(label):
            with override_settings(**PRODUCTION_SETTINGS):
                with self.assertRaises(startup.ClockStartupRefused,
                                       msg="CLK-08 %s: production's startup must refuse this database." % label) as refused:
                    startup.refuse_unless_production_clock("default")
            return str(refused.exception)

        received = tuple(MigrationExecutor(connection).loader.graph.leaf_nodes())
        restored = []

        def restore():
            # Marked done before it migrates, as REV-01's is: a restore that fails is not run a second time at clean-up.
            if not restored:
                restored.append(True)
                MigrationExecutor(connection).migrate(list(received))

        self.assertIn((startup.TEST_CLOCK_APP, "0001_moving_part"), ledger(), "CLK-08: this clone must begin with the moving part applied.")
        self.assertIn(("core", "0096_intevia_clock"), ledger(), "CLK-08: this clone must begin with the clock's migration applied.")
        before, faces_before, ledger_before, parts_before = instrument.capture(), faces(), ledger(), moving_part()
        self.assertEqual(instrument.clock_counter_violations(before["intevia_clock_counter"], True), [],
                         "CLK-08: the clone must begin with its counter present, at offset zero.")
        counter_before = before["intevia_clock_counter"]
        self.assertEqual(len(parts_before), 9, "CLK-08: the clone must begin with the nine routines of the moving part.")
        self.assertIn(counter_found, production_startup("at head"))

        self.addCleanup(restore)
        # Down one step: the moving part reversed, the clock's own migration still applied.
        MigrationExecutor(connection).migrate([(startup.TEST_CLOCK_APP, None)])
        written = {name: PRODUCTION.expected(stored, builtin, meaning) for name, _w, stored, builtin, meaning in PRODUCTION.FACES}
        self.assertEqual(faces(), written, "CLK-08 reversed: the faces must be the production faces, exactly as 0096 writes them.")
        self.assertEqual(moving_part(), [], "CLK-08 reversed: no function of the moving part may remain.")
        self.assertNotIn(startup.TEST_CLOCK_APP, [app for app, _name in ledger()], "CLK-08 reversed: the ledger must hold no row for the test route's app.")
        self.assertEqual(instrument.clock_counter_observation(), counter_before,
                         "CLK-08 reversed: the counter must be kept, at zero and exactly as it was.")
        text = production_startup("reversed")
        self.assertIn(counter_found, text, "CLK-08 reversed: the refusal must name the counter.")
        self.assertEqual([mark for mark in other_marks if mark in text], [],
                         "CLK-08 reversed: with the production faces back, the counter must be the only mark found.")

        # Down below the clock: no face at all.
        MigrationExecutor(connection).migrate([("core", "0095_te_c2_join")])
        self.assertEqual(faces(), {name: None for name in written}, "CLK-08 below the clock: no face may remain.")
        self.assertEqual(moving_part(), [], "CLK-08 below the clock: no function of the moving part may remain.")
        self.assertEqual([row for row in ledger() if row in ((startup.TEST_CLOCK_APP, "0001_moving_part"), ("core", "0096_intevia_clock"))],
                         [], "CLK-08 below the clock: the ledger must hold neither migration.")
        self.assertEqual(instrument.clock_counter_observation(), counter_before,
                         "CLK-08 below the clock: the counter must be kept, at zero and exactly as it was.")
        text = production_startup("below the clock")
        self.assertIn(counter_found, text, "CLK-08 below the clock: the refusal must name the counter.")
        for part in REFUSAL_PARTS:
            self.assertIn(part, text, "CLK-08 below the clock: the refusal must keep its five parts.")

        # Up again: every received leaf, through the executor.
        restore()
        after = instrument.capture()
        self.assertEqual(instrument.compare(before, after), ([], []), "CLK-08 restored: the full capture must equal the one taken at head.")
        self.assertEqual(after["intevia_clock_counter"], counter_before, "CLK-08 restored: the counter must be the one kept, at zero.")
        self.assertEqual(faces(), faces_before, "CLK-08 restored: the faces and their comments must be as they were.")
        self.assertEqual(moving_part(), parts_before, "CLK-08 restored: the nine routines must be back.")
        self.assertEqual(ledger(), ledger_before, "CLK-08 restored: the ledger must be as it was received.")
        self.assertEqual(owned_route.offset(), 0, "CLK-08 restored: the clock must be at zero.")
        self.assertIn(counter_found, production_startup("restored"), "CLK-08 restored: production's startup must still refuse.")
