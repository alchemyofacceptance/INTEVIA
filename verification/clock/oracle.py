"""The INTEVIA Clock's verification oracle.

Verification tooling, outside the product (the Human Governor's D6, 12 Nov 2026). It is
the one place where a witness of the INTEVIA Clock reads PostgreSQL's own time functions,
and it reads them only to compare a face with the built-in it stands for. It returns
what it observed; the witness in tests/test_intevia_clock.py does the asserting. Nothing
here supplies a time to the product or to a fixture.
"""

from collections import namedtuple

Cuts = namedtuple("Cuts", "native_transaction face_transaction native_statement face_statement")
Spread = namedtuple("Spread", "instant_min instant_max statement_values face_statement native_statement")

PAIRED = (
    "SELECT pg_catalog.transaction_timestamp(), public.intevia_clock_transaction(), "
    "pg_catalog.statement_timestamp(), public.intevia_clock_statement()"
)
SAVED = "intevia_clock_oracle"


def wall(cursor):
    """One raw reading of PostgreSQL's wall clock, in a statement of its own."""
    cursor.execute("SELECT pg_catalog.clock_timestamp()")
    return cursor.fetchone()[0]


def cuts(cursor):
    """In one statement: the transaction and statement faces, each beside its built-in."""
    cursor.execute(PAIRED)
    return Cuts(*cursor.fetchone())


def spread(cursor, rows):
    """In one statement over many rows: the instant face's range and the statement face's values."""
    cursor.execute(
        "SELECT min(public.intevia_clock_instant()), max(public.intevia_clock_instant()), "
        "count(DISTINCT public.intevia_clock_statement()), min(public.intevia_clock_statement()), "
        "pg_catalog.statement_timestamp() FROM generate_series(1, %s)",
        [rows],
    )
    return Spread(*cursor.fetchone())


def prepare(cursor):
    """Save the paired reading as a prepared statement, so later runs use a saved plan."""
    cursor.execute("PREPARE %s AS %s" % (SAVED, PAIRED))


def saved(cursor):
    """Run the saved statement."""
    cursor.execute("EXECUTE %s" % SAVED)
    return Cuts(*cursor.fetchone())


def forget(cursor):
    """Drop the saved statement."""
    cursor.execute("DEALLOCATE %s" % SAVED)
