"""REV-04: the column-defaults step of the INTEVIA Clock refuses what it does not expect, and changes nothing.

Core migration 0103 moves ten column defaults onto the INTEVIA Clock through a step of its own
(``change`` and ``_each`` in ``core/migrations/0103_clock_storage_column_defaults.py``). The step
is meant to stop the migration when a column is not there, when its default is not the one the
migration expects, or when the change altered anything but that default. This witness makes it
stop on purpose, each way it can, forward (the landed default to the face) and backward (the
face to the landed default), and checks that the column, its default and its table's other
defaults are then as they were.

It works on one scratch table that it creates itself and that never outlives the test:
everything on that table happens inside one transaction that is rolled back. It needs no more
than the right to create a table in schema ``public``. The migration's own two entry points
choose only which list of columns the step is given; the witness gives the step its scratch
columns instead, through the same ``_each``.
"""

from contextlib import contextmanager
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

from django.db import connection, transaction
from django.test import TransactionTestCase

conversion = import_module("core.migrations._clock_conversion")
defaults = import_module("core.migrations.0103_clock_storage_column_defaults")

TABLE = "intevia_clock_default_witness"
LANDED = "statement_timestamp()"
FACE = "public.intevia_clock_statement()"
OTHER = "clock_timestamp()"
EDITOR = SimpleNamespace(connection=connection)
# Forward the step expects the landed default and sets the face; backward, the other way.
DIRECTIONS = (("forward", LANDED, FACE), ("backward", FACE, LANDED))
# Everything the catalogue holds about the scratch table's columns and defaults, and its storage file.
STATE = """
SELECT c.relfilenode::bigint,
       (SELECT pg_catalog.jsonb_agg(pg_catalog.to_jsonb(a) ORDER BY a.attnum)
          FROM pg_catalog.pg_attribute a WHERE a.attrelid = c.oid AND a.attnum > 0)::text,
       (SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(d.adnum, pg_catalog.pg_get_expr(d.adbin, d.adrelid)) ORDER BY d.adnum)
          FROM pg_catalog.pg_attrdef d WHERE d.adrelid = c.oid)::text
  FROM pg_catalog.pg_class c
 WHERE c.oid = pg_catalog.to_regclass(%s)
"""
DEFAULT = """
SELECT pg_catalog.pg_get_expr(d.adbin, d.adrelid)
  FROM pg_catalog.pg_attrdef d JOIN pg_catalog.pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
 WHERE d.adrelid = pg_catalog.to_regclass(%s) AND a.attname = %s
"""


class _Undo(Exception):
    pass


@contextmanager
def undone():
    """Run a block inside a transaction that is always rolled back."""
    try:
        with transaction.atomic():
            yield
            raise _Undo
    except _Undo:
        pass


@contextmanager
def catalogue_names():
    """Read with only ``pg_catalog`` on the search path, as the step does, so a face is printed with its schema."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_catalog.current_setting('search_path')")
        path = cursor.fetchone()[0]
        cursor.execute("SELECT pg_catalog.set_config('search_path', 'pg_catalog', true)")
        try:
            yield cursor
        finally:
            cursor.execute("SELECT pg_catalog.set_config('search_path', %s, true)", [path])


def state(table=TABLE):
    """Return the table's storage file, every catalogue fact of its columns, and all its defaults; or None."""
    with catalogue_names() as cursor:
        cursor.execute(STATE, ["public." + table])
        return cursor.fetchone()


def default_of(column, table=TABLE):
    """Return one column's default as PostgreSQL prints it, or None."""
    with catalogue_names() as cursor:
        cursor.execute(DEFAULT, ["public." + table, column])
        row = cursor.fetchone()
        return None if row is None else row[0]


def make_table(default):
    """Create the scratch table: one column with ``default``, one with another time default, one with none."""
    with connection.cursor() as cursor:
        cursor.execute(
            "CREATE TABLE public.%s (id integer, at timestamptz DEFAULT %s, beside timestamptz DEFAULT %s, bare timestamptz)"
            % (TABLE, default, OTHER))
        cursor.execute("INSERT INTO public.%s (id, at) VALUES (1, '2026-10-12T00:00:00Z')" % TABLE)


def stored_row():
    with connection.cursor() as cursor:
        cursor.execute("SELECT id, at, beside IS NOT NULL, bare FROM public.%s" % TABLE)
        return cursor.fetchall()


class ClockColumnDefaultRefusalTests(TransactionTestCase):
    def refused(self, pairs, phrase, case):
        """Run the step where it must refuse before changing anything; check the words and that nothing changed."""
        before = state()
        with self.assertRaises(conversion.ClockConversionRefused,
                               msg="REV-04 %s: the column-defaults step must refuse." % case) as stopped:
            defaults._each(EDITOR, pairs)
        self.assertIn(phrase, str(stopped.exception), "REV-04 %s: the refusal must say what was wrong." % case)
        self.assertIn("Nothing was changed.", str(stopped.exception), "REV-04 %s: the refusal must say nothing changed." % case)
        self.assertEqual(state(), before, "REV-04 %s: a refusal must leave the column, its default and its siblings as they were." % case)

    def refused_after_changing(self, pairs, replacement, phrase, case, reads):
        """Run the step where it must refuse after its change; check the words, that the change was really made,
        and that the rollback of the step's transaction leaves everything as it was."""
        before = state()
        with transaction.atomic():
            with patch.object(defaults, "_read", side_effect=reads) as read, \
                    self.assertRaises(conversion.ClockConversionRefused,
                                      msg="REV-04 %s: the column-defaults step must refuse." % case) as stopped:
                defaults._each(EDITOR, pairs)
            self.assertEqual(read.call_count, 2, "REV-04 %s: the step must have read the column before and after." % case)
            self.assertEqual(default_of("at"), replacement,
                             "REV-04 %s: this case is only a witness if the default was really changed first." % case)
            self.assertNotEqual(state(), before, "REV-04 %s: the catalogue must show the change before the rollback." % case)
            transaction.set_rollback(True)
        self.assertIn(phrase, str(stopped.exception), "REV-04 %s: the refusal must say what was wrong." % case)
        self.assertEqual(state(), before,
                         "REV-04 %s: when the step refuses, the rollback of its transaction must leave everything as it was." % case)

    def test_the_column_defaults_step_refuses_what_it_does_not_expect_and_changes_nothing(self):
        """REV-04. Forward and backward, migration 0103's own step stops at a column that is missing, has no
        default, already has the default it would set, or has another default; and, after its change, at a
        default that is not the intended one or a change that touched anything else. It does not run outside a
        transaction. Each time it says so, and the column, its default and its table's other defaults are as
        they were."""
        real_read = defaults._read
        self.assertFalse(connection.in_atomic_block, "REV-04: this witness starts outside any transaction.")
        # One of the ten installed columns, read before and after: the witness must not touch it.
        table, column = defaults.DEFAULTS[0][:2]
        installed, whole = default_of(column, table), state(table)
        with self.assertRaises(conversion.ClockConversionRefused,
                               msg="REV-04 outside a transaction: the step must refuse.") as stopped:
            # The step is given a column of the scratch table, which does not exist yet: were the step to go on
            # outside a transaction, it would find nothing to change.
            defaults._each(EDITOR, ((TABLE, "at", LANDED, FACE),))
        self.assertIn("the column defaults are changed only inside a transaction", str(stopped.exception),
                      "REV-04 outside a transaction: the refusal must say what was wrong.")
        self.assertIsNone(state(), "REV-04: the scratch table must not exist before the witness makes it.")

        for direction, expected, replacement in DIRECTIONS:
            case = lambda what: "%s, %s" % (direction, what)  # noqa: E731
            pair = ((TABLE, "at", expected, replacement),)
            with undone():
                make_table(expected)
                rows = stored_row()
                beside = default_of("beside")
                # The step does its work when the default is the expected one: only that default changes.
                before = state()
                defaults._each(EDITOR, pair)
                after = state()
                self.assertEqual(default_of("at"), replacement, "REV-04 %s." % case("the step must set the default it was asked to set"))
                self.assertEqual((after[0], default_of("beside"), default_of("bare"), stored_row()), (before[0], beside, None, rows),
                                 "REV-04 %s." % case("the change must leave the table's storage, its other defaults and its rows alone"))
                # Now the column holds what the step would set.
                self.refused(pair, "it is the default this step would set", case("already changed"))
            with undone():
                make_table(expected)
                self.refused(((TABLE, "absent", expected, replacement),), "is not there", case("a column that is not there"))
                self.refused(((TABLE, "bare", expected, replacement),), "it is no default", case("a column with no default"))
                self.refused(((TABLE, "beside", expected, replacement),),
                             "it is neither of the two defaults this migration holds", case("a column with another default"))
                # The two refusals after the change cannot be reached through PostgreSQL itself: SET DEFAULT sets
                # what it is given and touches nothing else. Each is reached by changing one field of the step's
                # own second reading of the catalogue (its _read), the narrowest point; the first reading and the
                # ALTER TABLE are real.

                def second_read_says(change):
                    seen = []

                    def reads(cursor, table, column):
                        row = real_read(cursor, table, column)
                        seen.append(row)
                        return row if len(seen) == 1 else change(row)
                    return reads

                self.refused_after_changing(
                    pair, replacement, "after the change its default is not the intended one", case("a default that did not take"),
                    second_read_says(lambda row: (expected,) + tuple(row[1:])))
                self.refused_after_changing(
                    pair, replacement, "the change altered something other than the column's default",
                    case("another default changed with it"),
                    second_read_says(lambda row: tuple(row[:3]) + ('[[3, "now()"]]',)))
                self.refused_after_changing(
                    pair, replacement, "the change altered something other than the column's default",
                    case("the table's storage changed with it"),
                    second_read_says(lambda row: tuple(row[:2]) + (row[2] + 1, row[3])))
                self.refused_after_changing(
                    pair, replacement, "the change altered something other than the column's default",
                    case("the column itself changed with it"),
                    second_read_says(lambda row: (row[0], row[1] + " ", row[2], row[3])))
            self.assertIsNone(state(), "REV-04 %s: the scratch table must not outlive the witness." % direction)
        self.assertEqual((default_of(column, table), state(table)), (installed, whole),
                         "REV-04: the installed columns must be as the witness found them.")
