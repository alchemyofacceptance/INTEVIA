"""U21g Phase 2 test backend: PostgreSQL with a flush that steps around the S015 TRUNCATE guardians (0019 and 0021)."""
from django.db.backends.postgresql.base import DatabaseWrapper as PostgreSQLDatabaseWrapper
from django.db.backends.postgresql.operations import DatabaseOperations as PostgreSQLDatabaseOperations


class DatabaseOperations(PostgreSQLDatabaseOperations):
    def _truncate_guardians(self, tables):
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT c.relname, t.tgname
                FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = current_schema() AND NOT t.tgisinternal
                  AND c.relname = ANY(%s) AND t.tgname LIKE 's015\\_%%' ESCAPE '\\' AND t.tgname LIKE '%%truncate%%'
                ORDER BY 1, 2
                """,
                [list(tables)],
            )
            return cursor.fetchall()

    def sql_flush(self, style, tables, *, reset_sequences=False, allow_cascade=False):
        sql = super().sql_flush(style, tables, reset_sequences=reset_sequences, allow_cascade=allow_cascade)
        if not sql:
            return sql
        guardians = self._truncate_guardians(tables)
        disable = [f"ALTER TABLE {self.quote_name(t)} DISABLE TRIGGER {self.quote_name(g)};" for t, g in guardians]
        enable = [f"ALTER TABLE {self.quote_name(t)} ENABLE TRIGGER {self.quote_name(g)};" for t, g in guardians]
        return [*disable, *sql, *enable]


class DatabaseWrapper(PostgreSQLDatabaseWrapper):
    ops_class = DatabaseOperations
