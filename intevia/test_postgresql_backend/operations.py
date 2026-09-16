import importlib
import re

from django.apps import apps as django_apps
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.db.backends.postgresql.operations import (
    DatabaseOperations as PostgreSQLDatabaseOperations,
)


S015_TEST_ROUTE = "LIVING_ORGANISM_TEST_LIFECYCLE"
S015_DATABASE_PATTERN = re.compile(r"test_intevia_living_organism_[a-z0-9_]+\Z")

# Guardians the reset has always been required to find (0019/0020). Kept as a required
# minimum: every entry must still be discovered, enabled, and restored.
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

# Tables created by migrations in raw SQL, with no Django model, that a test can write.
# Django's flush lists only model tables; these must be reset with them.
# 0021: s015_transaction_register. 0022: part, content, resolution and severance ledger.
S015_UNMODELLED_RESET_TABLES = (
    "core_governedeventcontent",
    "core_governedeventpart",
    "core_identityresolution",
    "core_identityresolutionsevered",
    "s015_transaction_register",
)

# Never reset: Django's own migration ledger.
S015_RESET_EXCLUDED_TABLES = frozenset({"django_migrations"})

# Data seeded by migrations, restored after a reset by calling the migration's own
# function, so the values come from the governing migration and are not copied here.
S015_MIGRATION_SEEDS = (
    ("core.migrations.0003_seed_triad_roles", "create_triad_roles"),
)

_TRUNCATE_TRIGGER_BIT = 32  # pg_trigger.tgtype TRUNCATE event bit


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

    def _reset_tables(self, tables):
        """Model tables Django asked to flush, plus the declared unmodelled tables.

        Refuses any other table in the schema that is neither a model table, a declared
        unmodelled table, nor excluded: an undeclared table could hold rows a reset
        would silently leave behind.
        """
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT relation.relname
                FROM pg_class relation
                JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = current_schema()
                  AND relation.relkind IN ('r', 'p')
                """
            )
            present = {row[0] for row in cursor.fetchall()}
        modelled = set(tables)
        unmodelled = present - modelled - S015_RESET_EXCLUDED_TABLES
        unexpected = sorted(unmodelled - set(S015_UNMODELLED_RESET_TABLES))
        if unexpected:
            raise ImproperlyConfigured(
                "S015 reset refuses undeclared unmodelled tables: "
                + ", ".join(unexpected)
            )
        return sorted(modelled | (unmodelled & set(S015_UNMODELLED_RESET_TABLES)))

    def _qualified_guardians(self, tables):
        """Discover every truncate trigger on the tables to be reset.

        Every discovered trigger must be an S015 guardian and enabled; every required
        guardian whose table is being reset must be among them.
        """
        if not tables:
            return []
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT relation.relname, guardian.tgname, guardian.tgenabled
                FROM pg_trigger guardian
                JOIN pg_class relation ON relation.oid = guardian.tgrelid
                JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = current_schema()
                  AND NOT guardian.tgisinternal
                  AND (guardian.tgtype & %s) <> 0
                  AND relation.relname = ANY(%s)
                ORDER BY relation.relname, guardian.tgname
                """,
                [_TRUNCATE_TRIGGER_BIT, list(tables)],
            )
            found = cursor.fetchall()
        if any(not trigger.startswith("s015_") for _, trigger, _ in found):
            raise ImproperlyConfigured(
                "S015 reset refuses a truncate trigger that is not an S015 guardian"
            )
        if any(enabled != "O" for _, _, enabled in found):
            raise ImproperlyConfigured(
                "S015 truncate guardian inventory is incomplete or disabled"
            )
        found_pairs = {(table, trigger) for table, trigger, _ in found}
        for table, trigger in S015_TRUNCATE_GUARDIANS.items():
            if table in tables and (table, trigger) not in found_pairs:
                raise ImproperlyConfigured(
                    "S015 truncate guardian inventory is incomplete or disabled"
                )
        return sorted(found_pairs)

    def sql_flush(self, style, tables, *, reset_sequences=False, allow_cascade=False):
        if not tables:
            return super().sql_flush(
                style,
                tables,
                reset_sequences=reset_sequences,
                allow_cascade=allow_cascade,
            )

        expected_database = self._qualified_database_name()
        reset_tables = self._reset_tables(tables)
        guardians = self._qualified_guardians(reset_tables)
        truncate = super().sql_flush(
            style,
            reset_tables,
            reset_sequences=reset_sequences,
            allow_cascade=allow_cascade,
        )

        qualification = (
            "DO $s015$ BEGIN "
            f"IF current_database() <> '{expected_database}' THEN "
            "RAISE EXCEPTION 'S015 disposable test database qualification failed'; "
            "END IF; END $s015$;"
        )
        disable = [
            f"ALTER TABLE {self.quote_name(table)} "
            f"DISABLE TRIGGER {self.quote_name(trigger)};"
            for table, trigger in guardians
        ]
        enable = [
            f"ALTER TABLE {self.quote_name(table)} "
            f"ENABLE TRIGGER {self.quote_name(trigger)};"
            for table, trigger in guardians
        ]
        restoration_check = []
        if guardians:
            pairs = ", ".join(f"('{table}', '{trigger}')" for table, trigger in guardians)
            restoration_check = [
                "DO $s015$ BEGIN IF (SELECT count(*) FROM pg_trigger guardian "
                "JOIN pg_class relation ON relation.oid = guardian.tgrelid "
                f"WHERE (relation.relname, guardian.tgname) IN ({pairs}) "
                f"AND guardian.tgenabled = 'O') <> {len(guardians)} THEN "
                "RAISE EXCEPTION 'S015 truncate guardian restoration failed'; "
                "END IF; END $s015$;"
            ]
        # The negative suites re-add s015_platform_root_unset_ck NOT VALID in tearDown.
        # No migration creates an S015 constraint NOT VALID, so the reference state is
        # validated; the reset tables are empty here, so validation cannot fail on data.
        table_list = ", ".join(f"'{table}'" for table in reset_tables)
        revalidate = [
            "DO $s015$ DECLARE c record; BEGIN "
            "FOR c IN SELECT relation.relname AS tbl, con.conname AS name "
            "FROM pg_constraint con "
            "JOIN pg_class relation ON relation.oid = con.conrelid "
            "JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace "
            "WHERE namespace.nspname = current_schema() "
            "AND con.conname LIKE 's015\\_%' AND NOT con.convalidated "
            f"AND relation.relname IN ({table_list}) LOOP "
            "EXECUTE format('ALTER TABLE %I VALIDATE CONSTRAINT %I', c.tbl, c.name); "
            "END LOOP; "
            "IF EXISTS (SELECT 1 FROM pg_constraint con "
            "JOIN pg_namespace namespace ON namespace.oid = con.connamespace "
            "WHERE namespace.nspname = current_schema() "
            "AND con.conname LIKE 's015\\_%' AND NOT con.convalidated) THEN "
            "RAISE EXCEPTION 'S015 constraint validation restoration failed'; "
            "END IF; END $s015$;"
        ]
        return [qualification, *disable, *truncate, *enable, *restoration_check, *revalidate]

    def execute_sql_flush(self, sql_list):
        """Run the reset and restore migration-seeded data in one transaction.

        Any failure rolls back the whole reset, including trigger changes, and propagates.
        """
        with transaction.atomic(
            using=self.connection.alias,
            savepoint=self.connection.features.can_rollback_ddl,
        ):
            with self.connection.cursor() as cursor:
                for sql in sql_list:
                    cursor.execute(sql)
            if sql_list:
                self._qualified_database_name()
                for module_name, function_name in S015_MIGRATION_SEEDS:
                    seed = getattr(importlib.import_module(module_name), function_name)
                    seed(django_apps, None)
