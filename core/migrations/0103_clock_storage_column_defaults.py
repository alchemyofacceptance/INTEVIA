"""The INTEVIA Clock, storage 7 of 7: the ten column defaults that read the time (CK-2f).

Ten columns take their value, when a writer gives none, from one of PostgreSQL's own time
calls. This migration changes each default in place to the INTEVIA Clock's face with the same
meaning (nine statement start, one instant):

    statement_timestamp()    public.intevia_clock_statement()
    clock_timestamp()        public.intevia_clock_instant()

In production each face is exactly its built-in (migration 0096), so nothing changes there.
In an owned test database the faces are the ones a test may move.

Forward, for each column: the stored default must be exactly the landed expression; it is set
to the face; the stored default must then be exactly the face, and nothing else about the
column, its table's storage or the table's other defaults may have changed. Reverse: the
same, the other way. A default is replaced by ``ALTER TABLE ... ALTER COLUMN ... SET DEFAULT``:
no row is rewritten and no stored value changes. A migration runs in one transaction, so a
refusal leaves the database as it was found.

After this migration no installed function and no column default reads the time except
through a face.
"""

from importlib import import_module

from django.db import migrations

clock = import_module("core.migrations._clock_conversion")

STATEMENT = ("statement_timestamp()", "public.intevia_clock_statement()")
INSTANT = ("clock_timestamp()", "public.intevia_clock_instant()")

# (table, column, landed default, clock default), in the order of the catalogue's export.
DEFAULTS = (
    ("core_essentialcoveragerequirement", "c2_recorded_at") + STATEMENT,
    ("core_identityresolutionsevered", "severed_at") + INSTANT,
    ("core_libauthorclaim", "effective_at") + STATEMENT,
    ("core_libauthorclaim", "recorded_at") + STATEMENT,
    ("core_libpolicyversion", "effective_at") + STATEMENT,
    ("core_libpolicyversion", "recorded_at") + STATEMENT,
    ("core_libpublicationfact", "effective_at") + STATEMENT,
    ("core_libpublicationfact", "recorded_at") + STATEMENT,
    ("core_libreleasepermission", "effective_at") + STATEMENT,
    ("core_libreleasepermission", "recorded_at") + STATEMENT,
)

# One column, read before and after: its stored default as PostgreSQL prints it, every fact
# the catalogue holds about the column, the table's storage file, and the table's other
# defaults. The default is printed with only ``pg_catalog`` on the search path, so a face is
# always printed with its schema and a built-in never is, whatever the session's own path.
_COLUMN = """
SELECT pg_catalog.pg_get_expr(d.adbin, d.adrelid),
       pg_catalog.to_jsonb(a)::text,
       c.relfilenode::bigint,
       (SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(
                   o.adnum, pg_catalog.pg_get_expr(o.adbin, o.adrelid)) ORDER BY o.adnum), '[]'::jsonb)
          FROM pg_catalog.pg_attrdef o
         WHERE o.adrelid = c.oid AND o.adnum <> a.attnum)::text
  FROM pg_catalog.pg_class c
  JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid AND NOT a.attisdropped
  LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
 WHERE n.nspname = 'public' AND c.relname = %s AND a.attname = %s
"""


def _read(cursor, table, column):
    """Return the column's default, its other catalogue facts, its table's file and the table's other defaults."""
    cursor.execute(_COLUMN, [table, column])
    return cursor.fetchone()


def change(cursor, quote, table, column, expected, replacement):
    """Set one column's default to ``replacement``, having found exactly ``expected``."""
    name = "public.%s.%s" % (table, column)
    before = _read(cursor, table, column)
    if before is None:
        raise clock.ClockConversionRefused(
            "The INTEVIA Clock conversion stopped: the column %s is not there. Nothing was changed." % name
        )
    if before[0] != expected:
        found = (
            "no default" if before[0] is None
            else "the default this step would set" if before[0] == replacement
            else "neither of the two defaults this migration holds"
        )
        raise clock.ClockConversionRefused(
            "The INTEVIA Clock conversion stopped at %s: its default is not the one this migration expects to "
            "replace (it is %s). Nothing was changed." % (name, found)
        )
    cursor.execute("ALTER TABLE public.%s ALTER COLUMN %s SET DEFAULT %s" % (quote(table), quote(column), replacement))
    after = _read(cursor, table, column)
    if after is None or after[0] != replacement:
        raise clock.ClockConversionRefused(
            "The INTEVIA Clock conversion stopped at %s: after the change its default is not the intended one. "
            "Nothing was changed." % name
        )
    if after[1:] != before[1:]:
        raise clock.ClockConversionRefused(
            "The INTEVIA Clock conversion stopped at %s: the change altered something other than the column's "
            "default (the column, the table's storage or another default differs). Nothing was changed." % name
        )


def _each(schema_editor, pairs):
    """Change each default, reading every one with only ``pg_catalog`` on the search path."""
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return
    if not connection.in_atomic_block:
        raise clock.ClockConversionRefused(
            "The INTEVIA Clock conversion stopped: the column defaults are changed only inside a transaction. "
            "Nothing was changed."
        )
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_catalog.current_setting('search_path')")
        path = cursor.fetchone()[0]
        cursor.execute("SELECT pg_catalog.set_config('search_path', 'pg_catalog', true)")
        try:
            for table, column, expected, replacement in pairs:
                change(cursor, connection.ops.quote_name, table, column, expected, replacement)
        except clock.ClockConversionRefused:
            # The transaction is still whole: give the session its own path back before refusing.
            cursor.execute("SELECT pg_catalog.set_config('search_path', %s, true)", [path])
            raise
        cursor.execute("SELECT pg_catalog.set_config('search_path', %s, true)", [path])


def install(apps, schema_editor):
    """Move these column defaults onto the INTEVIA Clock."""
    _each(schema_editor, DEFAULTS)


def uninstall(apps, schema_editor):
    """Put the landed defaults back, last converted first."""
    _each(schema_editor, tuple((table, column, face, landed) for table, column, landed, face in reversed(DEFAULTS)))


class Migration(migrations.Migration):
    dependencies = (("core", "0102_clock_storage_0088_0095"),)
    operations = (migrations.RunPython(install, uninstall),)
