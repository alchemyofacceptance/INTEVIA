"""The owned test lifecycle backend's database creation: it records which database a run's test runner made.

The INTEVIA Clock's moving part may be installed, moved and reversed only on a database the owned test route
owns (clock.py). Where a run carries the verification route's receipts, the receipts say which database that
is: the verification runners replace this class's creation step with their own (verification/ownership.py,
verification/isolation/template.py), so nothing is recorded here on such a run.

Where a run carries no receipts, as in a rehearsal started with plain `manage.py test`, names and ownership
cannot tell two databases of one owner apart. So this class records, on the connection, the name and the oid
of the database the test runner has just created or, with keepdb, selected, before Django points the
connection at it and migrates. The clock's three permissions compare the connected database with that record.

With keepdb the database selected is trusted by its name and its owner at selection. The record stops a
different database being used later in the run; it does not vouch for what was already under that name.
"""

from django.db.backends.postgresql.creation import DatabaseCreation as PostgreSQLDatabaseCreation

RECORD = "intevia_owned_test_database"


def recorded(connection):
    """The (name, oid) this connection's test runner recorded creating or selecting, or None."""
    return getattr(connection, RECORD, None)


class DatabaseCreation(PostgreSQLDatabaseCreation):
    """PostgreSQL's test database creation, with a record of the database it created or selected."""

    def _create_test_db(self, verbosity, autoclobber, keepdb=False):
        setattr(self.connection, RECORD, None)
        name = super()._create_test_db(verbosity, autoclobber, keepdb)
        with self._nodb_cursor() as cursor:
            cursor.execute("SELECT d.oid::bigint FROM pg_catalog.pg_database d WHERE d.datname = %s", [name])
            row = cursor.fetchone()
        setattr(self.connection, RECORD, (name, row[0]) if row else None)
        return name
