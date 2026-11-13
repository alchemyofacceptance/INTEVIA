"""The INTEVIA Clock's Python door.

Every Python read of the time in INTEVIA comes through database_now. It asks the
database's INTEVIA Clock (core migration 0096), never the host and never a PostgreSQL
time function of its own.

Two meanings can be asked for:

- INSTANT, the default: the instant of the call (public.intevia_clock_instant()).
- STATEMENT: the start of the statement that asks
  (public.intevia_clock_statement()). Each call sends its own statement, so the value
  is that statement's start. A cut that must be shared with a larger SQL statement is
  read inside that statement, not here.
"""

from datetime import datetime

from django.db import DatabaseError, connections
from django.utils import timezone

INSTANT = "instant"
STATEMENT = "statement"
_FACE_SQL = {
    INSTANT: "SELECT public.intevia_clock_instant()",
    STATEMENT: "SELECT public.intevia_clock_statement()",
}


def database_now(database_alias: str = "default", *, cut: str = INSTANT) -> datetime:
    """Return the INTEVIA Clock's time from the selected database connection."""
    try:
        sql = _FACE_SQL[cut]
    except (KeyError, TypeError):
        raise ValueError("the INTEVIA Clock has no meaning named %r" % (cut,)) from None
    with connections[database_alias].cursor() as cursor:
        cursor.execute(sql)
        row = cursor.fetchone()
    if row is None or len(row) != 1:
        raise DatabaseError("database clock returned no timestamp")
    value = row[0]
    if not isinstance(value, datetime):
        raise DatabaseError("database clock returned an invalid timestamp")
    if not timezone.is_aware(value):
        raise DatabaseError("database clock returned a naive timestamp")
    return value
