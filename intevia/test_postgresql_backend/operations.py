import re

from django.core.exceptions import ImproperlyConfigured
from django.db.backends.postgresql.operations import (
    DatabaseOperations as PostgreSQLDatabaseOperations,
)


S015_TEST_ROUTE = "LIVING_ORGANISM_TEST_LIFECYCLE"
S015_DATABASE_PATTERN = re.compile(r"test_intevia_living_organism_[a-z0-9_]+\Z")
S015_TRUNCATE_GUARDIANS = {
    "core_authorityprincipal": "s015_authorityprincipal_truncate_immutable",
    "core_authoritybasis": "s015_authoritybasis_truncate_immutable",
    "core_emergencyauthorityenvelope": (
        "s015_emergencyauthorityenvelope_truncate_immutable"
    ),
    "core_governedvisibilitygrant": (
        "s015_governedvisibilitygrant_truncate_immutable"
    ),
    "core_livingorganism": "s015_livingorganism_truncate_immutable",
    "core_circle": "s015_circle_truncate_immutable",
    "core_organismmembership": "s015_organismmembership_truncate_immutable",
    "core_contextualroleassignment": (
        "s015_contextualroleassignment_truncate_immutable"
    ),
    "core_authorityprincipalaliasreview": (
        "s015_authorityprincipalaliasreview_truncate_append_only"
    ),
    "core_authorityderivationedge": (
        "s015_authorityderivationedge_truncate_append_only"
    ),
    "core_governeddetermination": (
        "s015_governeddetermination_truncate_append_only"
    ),
    "core_organismcommandreceipt": (
        "s015_organismcommandreceipt_truncate_append_only"
    ),
}


class DatabaseOperations(PostgreSQLDatabaseOperations):
    def _qualified_database_name(self):
        settings = self.connection.settings_dict
        expected_database = settings.get("NAME", "")
        if (
            settings.get("S015_TEST_ROUTE") != S015_TEST_ROUTE
            or not S015_DATABASE_PATTERN.fullmatch(expected_database)
        ):
            raise ImproperlyConfigured(
                "S015 test lifecycle backend is not qualified for this route"
            )
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            current_database = cursor.fetchone()[0]
        if current_database != expected_database:
            raise ImproperlyConfigured(
                "S015 test lifecycle backend refuses the connected database"
            )
        return expected_database

    def _qualified_guardians(self, tables):
        expected = {
            table: S015_TRUNCATE_GUARDIANS[table]
            for table in tables
            if table in S015_TRUNCATE_GUARDIANS
        }
        if not expected:
            return expected
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT relation.relname, guardian.tgname, guardian.tgenabled
                FROM pg_trigger guardian
                JOIN pg_class relation ON relation.oid = guardian.tgrelid
                JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = current_schema()
                  AND guardian.tgname = ANY(%s)
                  AND NOT guardian.tgisinternal
                """,
                [list(expected.values())],
            )
            actual = {
                table: (trigger, enabled)
                for table, trigger, enabled in cursor.fetchall()
            }
        if set(actual) != set(expected) or any(
            actual[table] != (trigger, "O")
            for table, trigger in expected.items()
        ):
            raise ImproperlyConfigured(
                "S015 truncate guardian inventory is incomplete or disabled"
            )
        return expected

    def sql_flush(self, style, tables, *, reset_sequences=False, allow_cascade=False):
        sql = super().sql_flush(
            style,
            tables,
            reset_sequences=reset_sequences,
            allow_cascade=allow_cascade,
        )
        if not sql:
            return sql

        expected_database = self._qualified_database_name()
        guardians = self._qualified_guardians(tables)
        if not guardians:
            return sql

        qualification = (
            "DO $s015$ BEGIN "
            f"IF current_database() <> '{expected_database}' THEN "
            "RAISE EXCEPTION 'S015 disposable test database qualification failed'; "
            "END IF; END $s015$;"
        )
        disable = [
            f"ALTER TABLE {self.quote_name(table)} "
            f"DISABLE TRIGGER {self.quote_name(trigger)};"
            for table, trigger in guardians.items()
        ]
        enable = [
            f"ALTER TABLE {self.quote_name(table)} "
            f"ENABLE TRIGGER {self.quote_name(trigger)};"
            for table, trigger in guardians.items()
        ]
        trigger_names = ", ".join(
            f"'{trigger}'" for trigger in guardians.values()
        )
        restoration_check = (
            "DO $s015$ BEGIN IF ("
            "SELECT count(*) FROM pg_trigger "
            f"WHERE tgname IN ({trigger_names}) AND tgenabled = 'O'"
            f") <> {len(guardians)} THEN "
            "RAISE EXCEPTION 'S015 truncate guardian restoration failed'; "
            "END IF; END $s015$;"
        )
        return [qualification, *disable, *sql, *enable, restoration_check]