"""The INTEVIA Clock's startup refusal.

INTEVIA serves only a database whose clock is the production clock. A database that the
owned test route built holds a clock that can be moved; such a database must never
serve people, even after the moving part has been taken out again.

What is read is the database's own catalogue and its migration ledger, nothing else.
The database is refused on any of three kinds of evidence:

1. A face of the INTEVIA Clock is missing while another is present, or a face is not
   exactly the production face that core migration 0096 writes: its body, volatility,
   parallel safety, rights, language, result type, settings and comment. The owned test
   route marks a test database's faces in their comments. A face is the form of its name
   that takes no argument; a form of the same name that takes arguments is a finding too,
   and is named as that.
2. Anything named for the test route's moving part is present in schema public.
3. The migration ledger records a migration of the test route's own app.

The owned test route's migration can be reversed only on an owned test clone whose clock
is at zero. That reverse puts the production faces back and removes the moving part's
routines (the move, which is a procedure, and its functions), and it keeps the counter:
so the second kind of evidence stays in a database
from the first time the moving part is installed until the database is destroyed, at
every schema state, including one with no face at all. A database that has held the
moving part is destroyed, never repaired into service.

A database that has no face at all and none of this evidence has not yet been migrated
to the INTEVIA Clock. It is not refused here, so that `migrate` can install the clock.

The refusal is made at two places: where a serving process starts (intevia/wsgi.py and
intevia/asgi.py), and on every new physical connection a process opens, before that
connection's first use (every request, management command and worker). Nothing is
remembered from one connection to the next: a process that was once shown a production
database is checked again on the next connection it opens. A check that does not pass,
whether it refuses or fails while the evidence is read, closes the connection before the
error is raised, so the next use opens a new connection and meets the check again.

The check made where a serving process starts leaves no connection open behind it, whether
it passed, refused or failed. That process can be a pre-fork server's master, and a
database connection must not be handed across a fork: each worker opens its own, which is
checked when it opens.

The evidence is read in one statement, once for each new physical connection and never per
query. A serving entry's own check reads it once more, on the connection that the check
opens and then closes: one more statement for each process that starts, and on purpose,
so that the entry's check does not rest on the wiring of the check of new connections.
Under the owned test route's own settings the check stands down: that route owns its
databases and proves them itself.
"""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connections
from django.db.backends.signals import connection_created

OWNED_TEST_ROUTE = "LIVING_ORGANISM_TEST_LIFECYCLE"
TEST_CLOCK_PREFIX = "intevia_test_clock"
TEST_CLOCK_APP = "intevia_test_clock"
RESULT = "timestamp with time zone"

PRODUCTION_CLOCK = "PRODUCTION_CLOCK"
NOT_YET_MIGRATED = "NOT_YET_MIGRATED"
# The first name that sorts after every name beginning with the test route's prefix.
_AFTER_PREFIX = TEST_CLOCK_PREFIX[:-1] + chr(ord(TEST_CLOCK_PREFIX[-1]) + 1)
OWNED_TEST_ROUTE_STANDS_DOWN = "OWNED_TEST_ROUTE"


def _face(builtin, volatility, meaning):
    return (
        " SELECT pg_catalog.%s() " % builtin,
        volatility,
        "s",
        False,
        "sql",
        RESULT,
        None,
        "INTEVIA Clock, production face: %s. Installed by core migration 0096." % meaning,
    )


# The three faces exactly as core migration 0096 writes them: body, volatility, parallel
# safety, security definer, language, result type, settings, comment.
PRODUCTION_FACES = {
    "intevia_clock_statement": _face("statement_timestamp", "s", "the start of the current statement"),
    "intevia_clock_instant": _face("clock_timestamp", "v", "the instant of the call"),
    "intevia_clock_transaction": _face("transaction_timestamp", "s", "the start of the current transaction"),
}


class ClockStartupRefused(ImproperlyConfigured):
    """INTEVIA did not start: this database's clock is not the production clock."""


def _owned_test_route():
    return (
        getattr(settings, "S015_TEST_ROUTE", None) == OWNED_TEST_ROUTE
        and getattr(settings, "INTEVIA_TEST_CONTEXT", False) is True
    )


# One statement reads all three kinds of evidence. Each row is (kind, name, then a face's eight catalogue values).
# The ledger is a table, and a database that has never been migrated does not have it yet: a statement that named
# the table would fail there. So the ledger row is read through query_to_xml, which runs its query only when it is
# reached, and it is reached only when the table exists. No row found gives an empty result.
# A name begins with the test route's prefix exactly when it sorts from the prefix up to, and not including, the prefix
# with its last character raised by one (names sort by their bytes); written so, the catalogue's own indexes answer it.
EVIDENCE_SQL = """
SELECT 'face' AS kind, p.proname::text AS name, p.pronargs::integer AS arguments, p.prosrc::text, p.provolatile::text,
       p.proparallel::text, p.prosecdef, l.lanname::text, pg_catalog.format_type(p.prorettype, NULL), p.proconfig,
       pg_catalog.obj_description(p.oid, 'pg_proc')
  FROM pg_catalog.pg_proc p
  JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
  JOIN pg_catalog.pg_language l ON l.oid = p.prolang
 WHERE n.nspname = 'public' AND p.proname = ANY(%(faces)s::name[])
UNION ALL
SELECT 'relation', c.relname::text, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
  FROM pg_catalog.pg_class c
  JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public' AND c.relname >= %(prefix)s::name AND c.relname < %(after)s::name
UNION ALL
SELECT CASE p.prokind WHEN 'p' THEN 'procedure' ELSE 'function' END, p.proname::text, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
  FROM pg_catalog.pg_proc p
  JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname = 'public' AND p.proname >= %(prefix)s::name AND p.proname < %(after)s::name
UNION ALL
SELECT 'ledger', %(app)s::text, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
 WHERE CASE WHEN pg_catalog.to_regclass('public.django_migrations') IS NULL THEN false
            ELSE pg_catalog.query_to_xml(
                     pg_catalog.format('SELECT 1 AS found FROM public.django_migrations WHERE app = %%L LIMIT 1', %(app)s::text),
                     false, true, '')::text <> ''
       END
 ORDER BY 1, 2, 3
"""


def clock_evidence(connection):
    """Read the catalogue and the ledger, in one statement. Returns (faces found, findings)."""
    with connection.cursor() as cursor:
        cursor.execute(EVIDENCE_SQL, {
            "faces": sorted(PRODUCTION_FACES), "prefix": TEST_CLOCK_PREFIX, "after": _AFTER_PREFIX, "app": TEST_CLOCK_APP,
        })
        rows = cursor.fetchall()
    findings = []
    found = {}
    for kind, name, arguments, *held in rows:
        if kind == "face":
            if arguments != 0:
                # Not a face: a face takes no argument. Two forms that take none cannot share a name.
                findings.append("public.%s exists in a form that takes arguments" % name)
            else:
                found[name] = tuple(held)
    for kind, name, *_rest in rows:
        if kind in ("function", "procedure", "relation"):
            findings.append("the test route's %s public.%s is present" % (kind, name))
    for kind, name, *_rest in rows:
        if kind == "ledger":
            findings.append("the migration ledger records the test route's app %s" % name)
    if found:
        for name, production in sorted(PRODUCTION_FACES.items()):
            if name not in found:
                findings.append("the face public.%s() is missing" % name)
            elif found[name] != production:
                findings.append("the face public.%s() is not the production face" % name)
    return sorted(found), findings


def refusal_text(database, findings):
    """The refusal, in five parts: what happened, why, what is unchanged, what to do, who handles the rest."""
    return (
        "INTEVIA did not start on the database %s. "
        "Its clock is not the production clock: %s. That is the mark of a database built by INTEVIA's test route, "
        "or changed outside INTEVIA's migrations, and such a database can hold a clock that was moved. "
        "No business data was read or changed; only the database's own catalogue and migration ledger were read. "
        "Point INTEVIA at its production database, or build a new database with INTEVIA's migrations; "
        "a database that was ever a test database is destroyed, never repaired into service. "
        "If that does not resolve it, the platform's operators decide what happens to this database; "
        "start no INTEVIA service on it meanwhile."
    ) % (database, "; ".join(findings))


def refuse_unless_production_clock(alias="default", *, connection=None):
    """Refuse unless this database's INTEVIA Clock is production's. Returns what was found otherwise."""
    if _owned_test_route():
        return OWNED_TEST_ROUTE_STANDS_DOWN
    connection = connections[alias] if connection is None else connection
    if connection.vendor != "postgresql":
        return NOT_YET_MIGRATED
    found, findings = clock_evidence(connection)
    if findings:
        raise ClockStartupRefused(refusal_text(connection.settings_dict.get("NAME"), findings))
    return PRODUCTION_CLOCK if found else NOT_YET_MIGRATED


def refuse_at_serving_entry(alias="default"):
    """A serving entry's own check (intevia/wsgi.py and intevia/asgi.py): refuse unless this database's INTEVIA
    Clock is production's, and leave no connection open behind the check, whether it passed, refused or failed.

    The process that imports a serving entry can be a pre-fork server's master. Every worker it forks would
    inherit an open database socket, and a database connection must not be shared across a fork. Each worker
    opens its own connection, which is checked when it opens. Under the owned test route's own settings the
    check stands down and the connection is not touched."""
    if _owned_test_route():
        return OWNED_TEST_ROUTE_STANDS_DOWN
    try:
        return refuse_unless_production_clock(alias)
    finally:
        connections[alias].close()


def on_connection_created(sender, connection, **kwargs):
    """Check every new physical connection before its first use. Nothing is remembered between connections."""
    if connection.vendor != "postgresql":
        return
    try:
        refuse_unless_production_clock(connection=connection)
    except BaseException:
        # Leave no open connection behind a check that did not pass, whether it refused or failed while the
        # evidence was read: the next use must open a new connection and meet the check again.
        connection.close()
        raise


def connect():
    """Wire the check of every new connection. Called once, from core's app configuration."""
    connection_created.connect(on_connection_created, dispatch_uid="intevia_clock_startup")
