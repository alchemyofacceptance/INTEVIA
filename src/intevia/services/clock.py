"""Database-backed clock reads for services."""

from datetime import datetime

from django.db import DatabaseError, connections
from django.utils import timezone


def database_now(database_alias: str = "default") -> datetime:
    """Return the current instant from the selected database connection."""
    with connections[database_alias].cursor() as cursor:
        cursor.execute("SELECT clock_timestamp()")
        row = cursor.fetchone()
    if row is None or len(row) != 1:
        raise DatabaseError("database clock returned no timestamp")
    value = row[0]
    if not isinstance(value, datetime):
        raise DatabaseError("database clock returned an invalid timestamp")
    if not timezone.is_aware(value):
        raise DatabaseError("database clock returned a naive timestamp")
    return value
