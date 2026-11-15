"""REV-03: the INTEVIA Clock's conversion step refuses what it does not expect, and changes nothing.

Core migrations 0097 to 0102 move 110 installed functions onto the INTEVIA Clock through one
shared step, ``core.migrations._clock_conversion``. The step is meant to stop a migration when
an installed function is not the text the migration expects, or when its replacement changed
anything but the text. This witness makes it stop on purpose, each way it can, forward
(``convert``) and backward (``restore``), and checks that the function is then as it was.

It works on two scratch functions that it creates itself and that never outlive the test:
everything happens inside one transaction that is rolled back. It needs no more than the right
to create a function in schema ``public``.
"""

from contextlib import contextmanager
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

from django.db import connection, transaction
from django.test import TransactionTestCase

conversion = import_module("core.migrations._clock_conversion")

PLAIN = "public.intevia_clock_refusal_witness()"
STANDARD_BODY = "public.intevia_clock_refusal_witness_standard_body()"
ABSENT = "public.intevia_clock_refusal_witness_absent()"
LANDED = " SELECT 1 "
CLOCK = " SELECT 2 "
OTHER = " SELECT 3 "
STATE = """
SELECT p.oid::bigint, p.prosrc, pg_catalog.to_jsonb(p)::text, pg_catalog.pg_get_functiondef(p.oid)
  FROM pg_catalog.pg_proc p WHERE p.oid = pg_catalog.to_regprocedure(%s)
"""
EDITOR = SimpleNamespace(connection=connection)
# Forward the step expects the landed text and installs the clock text; backward, the other way.
DIRECTIONS = (
    ("convert", conversion.convert, LANDED, CLOCK),
    ("restore", conversion.restore, CLOCK, LANDED),
)


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


def state(signature):
    """Return everything the catalogue holds about one function, or None."""
    with connection.cursor() as cursor:
        cursor.execute(STATE, [signature])
        return cursor.fetchone()


def install(text):
    """Create or replace the plain scratch function with ``text`` as its body."""
    with connection.cursor() as cursor:
        cursor.execute("CREATE OR REPLACE FUNCTION %s RETURNS integer LANGUAGE sql VOLATILE AS $witness$%s$witness$"
                       % (PLAIN, text))


class ClockConversionRefusalTests(TransactionTestCase):
    def refused(self, call, functions, phrase, case, signature=PLAIN):
        """Run the step where it must refuse before changing anything; check the words and that nothing changed."""
        before = state(signature)
        with self.assertRaises(conversion.ClockConversionRefused,
                               msg="REV-03 %s: the conversion step must refuse." % case) as stopped:
            call(EDITOR, functions)
        self.assertIn(phrase, str(stopped.exception), "REV-03 %s: the refusal must say what was wrong." % case)
        self.assertIn("Nothing was changed.", str(stopped.exception), "REV-03 %s: the refusal must say nothing changed." % case)
        self.assertEqual(state(signature), before, "REV-03 %s: a refusal must leave the function exactly as it was." % case)

    def refused_after_replacing(self, call, functions, phrase, case, reads):
        """Run the step where it must refuse after its replacement; check the words, and that the rollback of the
        step's transaction leaves the function as it was."""
        before = state(PLAIN)
        with transaction.atomic():
            with patch.object(conversion, "_read", side_effect=reads) as read, \
                    self.assertRaises(conversion.ClockConversionRefused,
                                      msg="REV-03 %s: the conversion step must refuse." % case) as stopped:
                call(EDITOR, functions)
            self.assertEqual(read.call_count, 2, "REV-03 %s: the step must have read the function before and after." % case)
            self.assertNotEqual(state(PLAIN), before,
                                "REV-03 %s: this case is only a witness if the replacement was really made first." % case)
            transaction.set_rollback(True)
        self.assertIn(phrase, str(stopped.exception), "REV-03 %s: the refusal must say what was wrong." % case)
        self.assertEqual(state(PLAIN), before,
                         "REV-03 %s: when the step refuses, the rollback of its transaction must leave the function as it was." % case)

    def test_the_conversion_step_refuses_what_it_does_not_expect_and_changes_nothing(self):
        """REV-03. Forward and backward, the shared conversion step stops at a function that is missing, holds
        another text, has no quoted text to replace, or comes out of the replacement changed in anything but its
        text; each time with its message, and each time the function is as it was."""
        real_read = conversion._read
        self.assertIsNone(state(PLAIN), "REV-03: the witness needs the scratch function's name to be free.")
        with undone():
            with connection.cursor() as cursor:
                cursor.execute("CREATE FUNCTION %s RETURNS integer LANGUAGE sql VOLATILE BEGIN ATOMIC SELECT 1; END" % STANDARD_BODY)
            for name, call, expected, replacement in DIRECTIONS:
                functions = ((PLAIN, LANDED, CLOCK),)
                with self.subTest(direction=name):
                    install(expected)
                    call(EDITOR, functions)
                    self.assertEqual(state(PLAIN)[1], replacement,
                                     "REV-03 %s: on the text it expects, the step must make its replacement." % name)

                    with self.subTest(case="the function is not installed"):
                        before = state(PLAIN)
                        self.refused(call, ((ABSENT, LANDED, CLOCK),), "is not installed", name + ", not installed", ABSENT)
                        self.assertEqual(state(PLAIN), before)

                    with self.subTest(case="the installed text is already the replacement"):
                        install(replacement)
                        self.refused(call, functions, "it is the text this step would install", name + ", already replaced")

                    with self.subTest(case="the installed text is neither text"):
                        install(OTHER)
                        self.refused(call, functions, "it is neither of the two texts this migration holds", name + ", another text")

                    with self.subTest(case="the definition does not end with a quoted text"):
                        # A function written with a standard SQL body stores no text; its definition ends in END.
                        self.assertEqual(state(STANDARD_BODY)[1], "", "REV-03: a standard-body function must store no text.")
                        self.refused(call, ((STANDARD_BODY, "", ""),), "does not end with a quoted text",
                                     name + ", no quoted text", STANDARD_BODY)

                    with self.subTest(case="the text is not found once, whole, at the end of the definition"):
                        # Not reachable from PostgreSQL's own output: the step is handed a definition whose text differs.
                        install(expected)

                        def another_definition(cursor, signature):
                            identity, text, definition, facts = real_read(cursor, signature)
                            return identity, text, definition.replace(text, text + "-- "), facts

                        with patch.object(conversion, "_read", side_effect=another_definition):
                            self.refused(call, functions, "was not found once, whole, at the end of its definition",
                                         name + ", text not at the end")

                    with self.subTest(case="after the replacement the function does not hold the intended text"):
                        # Not reachable without a fault: the step's second reading is handed another text.
                        install(expected)
                        answers = iter((lambda row: row, lambda row: (row[0], OTHER, row[2], row[3])))
                        self.refused_after_replacing(
                            call, functions, "is not the same object holding the intended text", name + ", wrong text after",
                            lambda cursor, signature: next(answers)(real_read(cursor, signature)))

                    with self.subTest(case="after the replacement the function is another object"):
                        install(expected)
                        answers = iter((lambda row: row, lambda row: (row[0] + 1, row[1], row[2], row[3])))
                        self.refused_after_replacing(
                            call, functions, "is not the same object holding the intended text", name + ", another object after",
                            lambda cursor, signature: next(answers)(real_read(cursor, signature)))

                    with self.subTest(case="the replacement changed something other than the text"):
                        # The step is handed a definition that also says STABLE, so its replacement really changes the
                        # function's volatility; the step's own second reading of the catalogue must catch it.
                        install(expected)
                        answers = iter((lambda row: (row[0], row[1], row[2].replace(" LANGUAGE sql\n", " LANGUAGE sql\n STABLE\n", 1), row[3]),
                                        lambda row: row))
                        self.assertIn(" LANGUAGE sql\n", state(PLAIN)[3], "REV-03: the witness must be able to add STABLE to the definition.")
                        self.refused_after_replacing(
                            call, functions, "changed something other than the function's text", name + ", row changed",
                            lambda cursor, signature: next(answers)(real_read(cursor, signature)))
        self.assertIsNone(state(PLAIN), "REV-03: the scratch function must not outlive the witness.")
        self.assertIsNone(state(STANDARD_BODY), "REV-03: the scratch function must not outlive the witness.")
