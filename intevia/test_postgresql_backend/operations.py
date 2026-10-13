"""H64 sequence discipline for the PostgreSQL test lifecycle backend.

The Human Governor's 13 October 2026 direction is to keep generated identifiers
ahead of every existing row, including rows restored by migrations. The backend
checks before teardown flushing and after seed restoration, and checks the result
of the by-name setup reset. The isolation instrument checks at CP-0 and CP-A.
"""

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
    "core_governedrule": "s015_governedrule_truncate_immutable",
    "core_circlejoinrequest": "s015_circlejoinrequest_truncate_immutable",
    "core_circleadmissiondecision": "s015_circleadmissiondecision_truncate_immutable",
    "core_circleparticipation": "s015_circleparticipation_truncate_immutable",
    "core_circleparticipationend": "s015_circleparticipationend_truncate_immutable",
    "core_organismentitlement": "s015_organismentitlement_truncate_immutable",
    "core_organismentitlementevent": "s015_organismentitlementevent_truncate_immutable",
    "core_organismentitlementpolicytext": "s015_organismentitlementpolicytext_truncate_immutable",
    "core_organismmembership": "s015_organismmembership_truncate_immutable",
    "core_contextualroleassignment": (
        "s015_contextualroleassignment_truncate_immutable"
    ),
    "core_lofoundingcontentversion": "s015_lofoundingcontentversion_truncate_immutable",
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
    "core_platformtermsversion": "s015_platformtermsversion_truncate_immutable",
    "core_platformtermsacceptance": "s015_platformtermsacceptance_truncate_immutable",
    "core_learnerregistration": "s015_learnerregistration_truncate_immutable",
    "core_registrationreceipt": "s015_registrationreceipt_truncate_immutable",
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
    ("core.migrations.0045_sbg_governance_formation", "restore_platform_rules"),
    ("core.migrations.0046_se_entitlement_foundation", "restore_entitlement_rules"),
)

_TRUNCATE_TRIGGER_BIT = 32  # pg_trigger.tgtype TRUNCATE event bit


def _sequence_finding_text(finding):
    status = finding["status"]
    if status == "UNSUPPORTED":
        config = finding["configuration"]
        return (
            "sequence %s has unsupported configuration "
            "(increment %s, min %s, max %s, start %s, cycle %s, cache %s)"
            % (
                finding["sequence"],
                config["increment"],
                config["minimum"],
                config["maximum"],
                config["start"],
                config["cycle"],
                config["cache"],
            )
        )
    suffix = (
        "(sequence %s; last_value %s, is_called %s)"
        % (
            finding["sequence"],
            finding["last_value"],
            str(finding["is_called"]).lower(),
        )
    )
    if status == "EXHAUSTED":
        return (
            "%s.%s next %s exceeds sequence maximum %s, highest %s %s"
            % (
                finding["table"],
                finding["column"],
                finding["next"],
                finding["maximum"],
                finding["highest"],
                suffix,
            )
        )
    return (
        "%s.%s next %s, highest %s %s"
        % (
            finding["table"],
            finding["column"],
            finding["next"],
            finding["highest"],
            suffix,
        )
    )


class S015SequenceDisciplineError(RuntimeError):
    """Report a sequence invariant failure after the reset transaction commits."""

    def __init__(self, *, kind, findings, earlier=None):
        self.kind = kind
        self.findings = tuple(findings)
        self.earlier = tuple(earlier or ())
        current_text = "; ".join(_sequence_finding_text(item) for item in self.findings)
        if kind == "before_teardown":
            message = (
                "S015 sequence discipline: observed before the teardown reset of this test: "
                "%s. The reset then completed and its post-check passed: every counter is "
                "past its rows and the seeds are restored. If this test's CP-A was "
                "established, the producer lies within the test — its setUp, body, "
                "tearDown or cleanups; find the write that set an id directly or moved "
                "a sequence, and take ids from the rows it creates (practice v1.38 "
                "§5zzn)." % current_text
            )
        elif kind == "after_restore":
            earlier_text = (
                "; ".join(_sequence_finding_text(item) for item in self.earlier)
                if self.earlier
                else "nothing"
            )
            message = (
                "S015 sequence discipline: observed after the seed restore of this reset: "
                "%s. Earlier, before the reset: %s. The database is NOT confirmed clean; "
                "results after this point in the run are not to be relied on. The "
                "restorers ran in the order 0003, 0045, 0046."
                % (current_text, earlier_text)
            )
        else:
            raise ValueError("unknown S015 sequence discipline error kind")
        super().__init__(message)


class DatabaseOperations(PostgreSQLDatabaseOperations):
    """Database operations with qualified, counter-aware test lifecycle resets."""

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

    def _table_owned_sequence_rows(self):
        """Read table-owned sequence configuration from the current schema."""
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_relation.relname, column_info.attname, sequence_relation.relname,
                       sequence.seqincrement, sequence.seqmin, sequence.seqmax,
                       sequence.seqstart, sequence.seqcycle, sequence.seqcache
                FROM pg_class sequence_relation
                JOIN pg_sequence sequence ON sequence.seqrelid = sequence_relation.oid
                JOIN pg_depend dependency
                  ON dependency.objid = sequence_relation.oid
                 AND dependency.classid = 'pg_class'::regclass
                 AND dependency.refclassid = 'pg_class'::regclass
                 AND dependency.deptype IN ('a', 'i')
                JOIN pg_class table_relation ON table_relation.oid = dependency.refobjid
                JOIN pg_attribute column_info
                  ON column_info.attrelid = table_relation.oid
                 AND column_info.attnum = dependency.refobjsubid
                JOIN pg_namespace namespace ON namespace.oid = table_relation.relnamespace
                WHERE namespace.nspname = current_schema()
                  AND sequence_relation.relkind = 'S'
                  AND table_relation.relkind IN ('r', 'p')
                ORDER BY 1, 2
                """
            )
            rows = cursor.fetchall()
        return [
            {
                "table": row[0],
                "column": row[1],
                "sequence": row[2],
                "increment": row[3],
                "minimum": row[4],
                "maximum": row[5],
                "start": row[6],
                "cycle": row[7],
                "cache": row[8],
            }
            for row in rows
        ]

    @staticmethod
    def _supported_sequence_configuration(sequence):
        return (
            sequence["increment"] == 1
            and sequence["minimum"] == 1
            and sequence["start"] == 1
            and sequence["cycle"] is False
            and sequence["cache"] == 1
        )

    def _sequence_discipline_findings(self):
        """Find unsupported or behind table-owned sequences without consuming them."""
        findings = []
        for sequence in self._table_owned_sequence_rows():
            finding = {
                **sequence,
                "configuration": {
                    "increment": sequence["increment"],
                    "minimum": sequence["minimum"],
                    "maximum": sequence["maximum"],
                    "start": sequence["start"],
                    "cycle": sequence["cycle"],
                    "cache": sequence["cache"],
                },
            }
            if not self._supported_sequence_configuration(sequence):
                finding["status"] = "UNSUPPORTED"
                findings.append(finding)
                continue

            table = self.quote_name(sequence["table"])
            column = self.quote_name(sequence["column"])
            sequence_name = self.quote_name(sequence["sequence"])
            with self.connection.cursor() as cursor:
                cursor.execute("SELECT max(%s) FROM %s" % (column, table))
                highest = cursor.fetchone()[0]
            if highest is None:
                continue

            with self.connection.cursor() as cursor:
                cursor.execute("SELECT last_value, is_called FROM %s" % sequence_name)
                last_value, is_called = cursor.fetchone()
            next_value = last_value + 1 if is_called else last_value
            if next_value > sequence["maximum"]:
                status = "EXHAUSTED"
            elif next_value <= highest:
                status = "BEHIND"
            else:
                continue
            findings.append(
                {
                    **finding,
                    "status": status,
                    "highest": highest,
                    "last_value": last_value,
                    "is_called": is_called,
                    "next": next_value,
                }
            )
        return findings

    def _sequence_reset_verification_sql(self):
        """Return one SQL block that verifies every table-owned sequence after reset."""
        return """
DO $s015$
DECLARE
    sequence_record record;
    highest_id bigint;
    sequence_last_value bigint;
    sequence_is_called boolean;
    next_id bigint;
BEGIN
    FOR sequence_record IN
        SELECT table_relation.relname AS table_name,
               column_info.attname AS column_name,
               sequence_relation.relname AS sequence_name,
               sequence.seqincrement, sequence.seqmin, sequence.seqmax,
               sequence.seqstart, sequence.seqcycle, sequence.seqcache
        FROM pg_class sequence_relation
        JOIN pg_sequence sequence ON sequence.seqrelid = sequence_relation.oid
        JOIN pg_depend dependency
          ON dependency.objid = sequence_relation.oid
         AND dependency.classid = 'pg_class'::regclass
         AND dependency.refclassid = 'pg_class'::regclass
         AND dependency.deptype IN ('a', 'i')
        JOIN pg_class table_relation ON table_relation.oid = dependency.refobjid
        JOIN pg_attribute column_info
          ON column_info.attrelid = table_relation.oid
         AND column_info.attnum = dependency.refobjsubid
        JOIN pg_namespace namespace ON namespace.oid = table_relation.relnamespace
        WHERE namespace.nspname = current_schema()
          AND sequence_relation.relkind = 'S'
          AND table_relation.relkind IN ('r', 'p')
        ORDER BY 1, 2
    LOOP
        IF sequence_record.seqincrement <> 1
           OR sequence_record.seqmin <> 1
           OR sequence_record.seqstart <> 1
           OR sequence_record.seqcycle
           OR sequence_record.seqcache <> 1 THEN
            RAISE EXCEPTION
                'S015 sequence reset found unsupported configuration for sequence %',
                sequence_record.sequence_name;
        END IF;
        EXECUTE format(
            'SELECT max(%I) FROM %I',
            sequence_record.column_name,
            sequence_record.table_name
        ) INTO highest_id;
        IF highest_id IS NOT NULL THEN
            EXECUTE format(
                'SELECT last_value, is_called FROM %I',
                sequence_record.sequence_name
            ) INTO sequence_last_value, sequence_is_called;
            next_id := CASE
                WHEN sequence_is_called THEN sequence_last_value + 1
                ELSE sequence_last_value
            END;
            IF next_id > sequence_record.seqmax THEN
                RAISE EXCEPTION
                    'S015 sequence reset left %.% exhausted (next %, highest %)',
                    sequence_record.table_name, sequence_record.column_name,
                    next_id, highest_id;
            ELSIF next_id <= highest_id THEN
                RAISE EXCEPTION
                    'S015 sequence reset left %.% behind its rows (next %, highest %)',
                    sequence_record.table_name, sequence_record.column_name,
                    next_id, highest_id;
            END IF;
        END IF;
    END LOOP;
END
$s015$;
"""

    def sequence_reset_by_name_sql(self, style, sequences):
        """Reset counters after restored rows and verify the by-name setup reset.

        The backend checks before teardown flushing and after seed restoration, and
        checks the result of the by-name setup reset. The isolation instrument checks
        at CP-0 and CP-A.
        """
        if not sequences:
            return []

        expected_database = self._qualified_database_name()
        known_sequences = {
            (sequence["table"], sequence["column"]): sequence
            for sequence in self._table_owned_sequence_rows()
        }
        for sequence_info in sequences:
            table_name = sequence_info["table"]
            column_name = sequence_info["column"] or "id"
            sequence = known_sequences.get((table_name, column_name))
            if sequence is None or not self._supported_sequence_configuration(sequence):
                raise ImproperlyConfigured(
                    "S015 by-name reset refuses an unsupported sequence configuration"
                )

        qualification = (
            "DO $s015$ BEGIN "
            f"IF current_database() <> '{expected_database}' THEN "
            "RAISE EXCEPTION 'S015 disposable test database qualification failed'; "
            "END IF; END $s015$;"
        )
        reset_statements = []
        for sequence_info in sequences:
            table_name = sequence_info["table"]
            column_name = sequence_info["column"] or "id"
            quoted_column = self.quote_name(column_name)
            reset_statements.append(
                "%s setval(pg_get_serial_sequence('%s','%s'), "
                "coalesce(max(%s), 1), max(%s) IS NOT NULL) FROM %s;"
                % (
                    style.SQL_KEYWORD("SELECT"),
                    style.SQL_TABLE(self.quote_name(table_name)),
                    style.SQL_FIELD(column_name),
                    style.SQL_FIELD(quoted_column),
                    style.SQL_FIELD(quoted_column),
                    style.SQL_TABLE(self.quote_name(table_name)),
                )
            )
        return [
            qualification,
            *reset_statements,
            self._sequence_reset_verification_sql(),
        ]

    def sequence_reset_sql(self, style, model_list):
        """Keep model-based resets database-qualified through this backend."""
        reset_statements = super().sequence_reset_sql(style, model_list)
        if not reset_statements:
            return []
        expected_database = self._qualified_database_name()
        qualification = (
            "DO $s015$ BEGIN "
            f"IF current_database() <> '{expected_database}' THEN "
            "RAISE EXCEPTION 'S015 disposable test database qualification failed'; "
            "END IF; END $s015$;"
        )
        return [qualification, *reset_statements]

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
        The backend checks before teardown flushing and after seed restoration, and checks
        the result of the by-name setup reset. The isolation instrument checks at CP-0
        and CP-A. Django's flush command raises CommandError from this error; its own text
        is Django's generic flush header and hints. The discipline message is in the
        chained cause, which the test report's traceback prints. When the discipline error
        is the cause, the flush and restore ran to completion, and the message says whether
        the result was clean.
        """
        if self.connection.in_atomic_block:
            raise ImproperlyConfigured(
                "S015 reset must not run inside an open transaction"
            )

        before = after = None
        try:
            with transaction.atomic(
                using=self.connection.alias,
                savepoint=self.connection.features.can_rollback_ddl,
            ):
                if sql_list:
                    self._qualified_database_name()
                    before = self._sequence_discipline_findings()
                with self.connection.cursor() as cursor:
                    for sql in sql_list:
                        cursor.execute(sql)
                if sql_list:
                    self._qualified_database_name()
                    for module_name, function_name in S015_MIGRATION_SEEDS:
                        seed = getattr(importlib.import_module(module_name), function_name)
                        seed(django_apps, None)
                    after = self._sequence_discipline_findings()
        except Exception as exc:
            if before:
                findings = "; ".join(_sequence_finding_text(item) for item in before)
                exc.add_note(
                    "S015 sequence discipline: observed before this reset ran; the reset "
                    "raised an exception and restoration was not confirmed: %s." % findings
                )
            raise

        if after:
            raise S015SequenceDisciplineError(
                kind="after_restore",
                findings=after,
                earlier=before,
            )
        if before:
            raise S015SequenceDisciplineError(
                kind="before_teardown",
                findings=before,
            )
