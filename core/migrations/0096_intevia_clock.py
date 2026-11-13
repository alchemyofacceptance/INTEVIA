"""The INTEVIA Clock: its three faces, as production runs them.

INTEVIA reads the time in three meanings and no others: the start of the current
statement, the instant of the call, and the start of the current transaction. Until
this migration every site called PostgreSQL's own function for its meaning. This
migration gives INTEVIA its own three, one for each meaning, so that the product has
one named place where the time is read (Human Governor, 12 Nov 2026: "After the
building of the INTEVIA Clock, I'd like to have only one clock.").

In production each face is its built-in and nothing more:

    public.intevia_clock_statement()    pg_catalog.statement_timestamp()    STABLE
    public.intevia_clock_instant()      pg_catalog.clock_timestamp()        VOLATILE
    public.intevia_clock_transaction()  pg_catalog.transaction_timestamp()  STABLE

Each is a one-expression SQL function, run with the caller's own rights, with no
setting of its own, returning timestamp with time zone, with the volatility and the
parallel safety PostgreSQL gives the built-in it stands for. Precision, time zone
handling and casts stay at the call sites. No offset, counter or test time is read
here, and nothing in this migration can be set.

Each face carries a comment that names it as a production face. The startup refusal
(core/clock_startup.py) requires the three faces exactly as written here, comment
included. The owned test route marks a test database's faces differently. If the test
route's migration is reversed, the production faces are put back exactly as written here
and the test route's counter stays in the database; the startup refusal refuses on it.

This migration adds no table and no trigger, and replaces no landed function. No site
reads the faces yet: converting the sites is the work of the parts that follow.
"""

from django.db import migrations

RESULT = "timestamp with time zone"
# name, volatility as written, volatility as stored, built-in, meaning
FACES = (
    ("intevia_clock_statement", "STABLE", "s", "statement_timestamp", "the start of the current statement"),
    ("intevia_clock_instant", "VOLATILE", "v", "clock_timestamp", "the instant of the call"),
    ("intevia_clock_transaction", "STABLE", "s", "transaction_timestamp", "the start of the current transaction"),
)


def body(builtin):
    """The whole stored body of a production face."""
    return " SELECT pg_catalog.%s() " % builtin


def comment(meaning):
    """The comment that names a production face."""
    return "INTEVIA Clock, production face: %s. Installed by core migration 0096." % meaning


def expected(volatility, builtin, meaning):
    """What the catalogue must hold for a production face."""
    return (body(builtin), volatility, "s", False, "sql", RESULT, None, comment(meaning))


def installed(cursor, name):
    """What the catalogue holds for a face, or None when it is absent."""
    cursor.execute(
        """SELECT p.prosrc, p.provolatile::text, p.proparallel::text, p.prosecdef, l.lanname::text,
                  pg_catalog.format_type(p.prorettype, NULL), p.proconfig,
                  pg_catalog.obj_description(p.oid, 'pg_proc')
             FROM pg_catalog.pg_proc p
             JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
             JOIN pg_catalog.pg_language l ON l.oid = p.prolang
            WHERE n.nspname = 'public' AND p.proname = %s AND p.pronargs = 0""",
        [name],
    )
    rows = cursor.fetchall()
    if len(rows) > 1:
        raise RuntimeError("0096 found more than one public.%s()" % name)
    return tuple(rows[0]) if rows else None


def install(apps, schema_editor):
    """Create the three faces, then read each back and refuse anything but what was written."""
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for name, written, stored, builtin, meaning in FACES:
            if installed(cursor, name) is not None:
                raise RuntimeError("0096 refused: public.%s() already exists" % name)
        for name, written, stored, builtin, meaning in FACES:
            cursor.execute(
                "CREATE FUNCTION public.%s() RETURNS %s LANGUAGE sql %s PARALLEL SAFE SECURITY INVOKER "
                "AS $intevia_clock$%s$intevia_clock$" % (name, RESULT, written, body(builtin))
            )
            cursor.execute("COMMENT ON FUNCTION public.%s() IS %%s" % name, [comment(meaning)])
        for name, written, stored, builtin, meaning in FACES:
            if installed(cursor, name) != expected(stored, builtin, meaning):
                raise RuntimeError("0096 refused: public.%s() was not installed as written" % name)


def uninstall(apps, schema_editor):
    """Drop the three faces, and only while each is still the production face."""
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for name, written, stored, builtin, meaning in FACES:
            if installed(cursor, name) != expected(stored, builtin, meaning):
                raise RuntimeError("0096 reverse refused: public.%s() is not the production face" % name)
        for name, written, stored, builtin, meaning in FACES:
            # No CASCADE: anything that has come to depend on a face stops this.
            cursor.execute("DROP FUNCTION public.%s()" % name)


class Migration(migrations.Migration):
    dependencies = (("core", "0095_te_c2_join"),)
    operations = (migrations.RunPython(install, uninstall),)
