"""The two migrations that make the automatic time fields the Clock's change nothing in the database.

``core/migrations/0104_clock_automatic_time_fields.py`` and
``email_foundation/migrations/0002_clock_automatic_time_fields.py`` tell Django that thirty-three
fields are now ``ClockDateTimeField``. The columns themselves do not change, so neither migration
may run a database operation, forwards or backwards. This test asks Django for the SQL each would
run, in each direction, exactly as ``sqlmigrate`` does, and expects none. It also shows that the
same question finds a database operation where there is one: a copy of each migration, made in
memory with one reversible operation added, gives SQL in both directions.

Neither migration file is read as text or changed. Nothing is written to the database.
"""

from django.db import connection, migrations
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase

STATE_ONLY = (
    ("core", "0104_clock_automatic_time_fields"),
    ("email_foundation", "0002_clock_automatic_time_fields"),
)


def migrations_under_test(loader):
    """The landed migrations this test is about, as Django loads them."""
    return [loader.get_migration(app, name) for app, name in STATE_ONLY]


def with_a_database_operation(migration):
    """A copy of a migration, in memory only, with one reversible database operation added."""
    copy = type(migration)(migration.name, migration.app_label)
    copy.operations = [*migration.operations, migrations.RunSQL("SELECT 1", "SELECT 2")]
    return copy


def database_statements(loader, migration, backwards):
    """The SQL Django would run for one migration in one direction (what ``sqlmigrate`` prints, without its comments)."""
    collected = loader.collect_sql([(migration, backwards)])
    return [statement for statement in collected if statement.strip() and not statement.lstrip().startswith("--")]


class ClockTimeFieldMigrationTests(TestCase):
    def test_the_time_field_migrations_run_no_database_operation_in_either_direction(self):
        """G-4. Neither migration runs any SQL, forwards or backwards; a copy with one reversible database operation does."""
        loader = MigrationLoader(connection)
        subjects = migrations_under_test(loader)
        self.assertEqual([(m.app_label, m.name) for m in subjects], list(STATE_ONLY))
        for migration in subjects:
            self.assertTrue(migration.operations, "%s must hold the state change it was written for." % migration)
            for backwards, direction in ((False, "forwards"), (True, "backwards")):
                with self.subTest(migration=str(migration), direction=direction):
                    self.assertEqual(
                        database_statements(loader, migration, backwards), [],
                        "%s would run SQL when applied %s. It only tells Django that the automatic time fields are the "
                        "Clock's; the columns do not change, so it must run no database operation in either direction."
                        % (migration, direction))
                    self.assertTrue(
                        database_statements(loader, with_a_database_operation(migration), backwards),
                        "The question asked of %s (%s) must find a database operation where there is one, or its "
                        "empty answer shows nothing." % (migration, direction))
