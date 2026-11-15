"""The INTEVIA Clock: an installed function's text replaced, and nothing else about it (G2-4).

The storage migrations after 0096 move every installed function that reads the time onto
the INTEVIA Clock. Each replaces a function's text with a successor that differs only
where a PostgreSQL time call becomes the face with the same meaning:

    statement_timestamp()    public.intevia_clock_statement()
    clock_timestamp()        public.intevia_clock_instant()
    transaction_timestamp()  public.intevia_clock_transaction()

This module is the one step they share. For one function it:

1. refuses unless the installed text is exactly the text the migration expects;
2. takes the function's own installed definition, keeps everything before its text
   (name, arguments, result, language, volatility, strictness, security mode, settings,
   parallel label, cost) and puts the other text in its place;
3. refuses unless the installed text is then exactly the replacement, the function is
   the same object, and every other fact the catalogue holds about it is unchanged:
   every column of its ``pg_proc`` row, its stored permissions included.

The header is taken from the catalogue because ``CREATE OR REPLACE FUNCTION`` resets every
attribute its statement does not restate. A migration runs in one transaction, so a refusal
leaves the database as it was found.
"""

import re

_CLOSING_DELIMITER = re.compile(r"(\$[A-Za-z0-9_]*\$)\n?\Z")

# The row, read before and after. The text is read apart from the rest. Two columns hold
# parsed expressions whose stored form records where each token stood in the statement that
# created them; they are compared as PostgreSQL prints them back.
_ROW = """
SELECT p.oid::bigint,
       p.prosrc,
       pg_catalog.pg_get_functiondef(p.oid),
       ((pg_catalog.to_jsonb(p) - 'prosrc' - 'proargdefaults' - 'prosqlbody')
        || pg_catalog.jsonb_build_object(
               'proargdefaults', pg_catalog.pg_get_expr(p.proargdefaults, 0),
               'prosqlbody', pg_catalog.pg_get_function_sqlbody(p.oid)))::text
  FROM pg_catalog.pg_proc p
 WHERE p.oid = pg_catalog.to_regprocedure(%s)
"""


class ClockConversionRefused(RuntimeError):
    """An installed function was not what this conversion expected. Nothing was changed."""


def _read(cursor, signature):
    """Return the function's identity, text, definition and every other catalogue fact, or None."""
    cursor.execute(_ROW, [signature])
    return cursor.fetchone()


def _statement(signature, definition, text, replacement):
    """Return the installed definition with ``replacement`` in place of ``text``."""
    closing = _CLOSING_DELIMITER.search(definition)
    if closing is None:
        raise ClockConversionRefused(
            "The INTEVIA Clock conversion stopped at %s: its installed definition does not end with a "
            "quoted text, so its text cannot be replaced alone. Nothing was changed." % signature
        )
    delimiter = closing.group(1)
    quoted = "AS " + delimiter + text + delimiter
    head = definition[: closing.start()]
    if not (head + delimiter).endswith(quoted) or definition.count(quoted) != 1:
        raise ClockConversionRefused(
            "The INTEVIA Clock conversion stopped at %s: its installed text was not found once, whole, "
            "at the end of its definition. Nothing was changed." % signature
        )
    number = 0
    while delimiter in replacement:
        number += 1
        delimiter = "$intevia_clock_%d$" % number
    return (head + closing.group(1))[: -len(quoted)] + "AS " + delimiter + replacement + delimiter


def swap(cursor, signature, expected, replacement):
    """Replace one installed function's text with ``replacement``, having found exactly ``expected``."""
    before = _read(cursor, signature)
    if before is None:
        raise ClockConversionRefused(
            "The INTEVIA Clock conversion stopped: the function %s is not installed. Nothing was changed." % signature
        )
    identity, text, definition, facts = before
    if text != expected:
        found = "the text this step would install" if text == replacement else "neither of the two texts this migration holds"
        raise ClockConversionRefused(
            "The INTEVIA Clock conversion stopped at %s: its installed text is not the text this migration "
            "expects to replace (it is %s). Nothing was changed." % (signature, found)
        )
    cursor.execute(_statement(signature, definition, text, replacement))
    after = _read(cursor, signature)
    if after is None or after[0] != identity or after[1] != replacement:
        raise ClockConversionRefused(
            "The INTEVIA Clock conversion stopped at %s: after the replacement the installed function is not "
            "the same object holding the intended text. Nothing was changed." % signature
        )
    if after[3] != facts:
        raise ClockConversionRefused(
            "The INTEVIA Clock conversion stopped at %s: the replacement changed something other than the "
            "function's text (its catalogue row differs). Nothing was changed." % signature
        )


def convert(schema_editor, functions):
    """Move each ``(signature, landed text, clock text)`` onto the INTEVIA Clock."""
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for signature, landed, clock in functions:
            swap(cursor, signature, landed, clock)


def restore(schema_editor, functions):
    """Put each function's landed text back, last converted first."""
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for signature, landed, clock in reversed(functions):
            swap(cursor, signature, clock, landed)
