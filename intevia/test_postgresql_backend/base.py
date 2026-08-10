from django.db.backends.postgresql.base import DatabaseWrapper as PostgreSQLDatabaseWrapper

from .operations import DatabaseOperations


class DatabaseWrapper(PostgreSQLDatabaseWrapper):
    ops_class = DatabaseOperations