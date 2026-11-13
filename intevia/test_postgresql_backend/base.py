from django.db.backends.postgresql.base import DatabaseWrapper as PostgreSQLDatabaseWrapper

from .creation import DatabaseCreation
from .operations import DatabaseOperations


class DatabaseWrapper(PostgreSQLDatabaseWrapper):
    creation_class = DatabaseCreation
    ops_class = DatabaseOperations
