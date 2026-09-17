"""Disposable test-database ownership for the S015 verification route (Change C v0.2, A1 finding C-A1-B3).

Django's non-interactive test runner creates the test database with autoclobber: if a database of that name already
exists, it drops it and creates it again. On this route that could destroy a database the run did not create. This
module replaces the creation and destruction paths of the runner's database connection:

  create   refuses if a database of the name exists (CollisionRefused, before any SQL that changes state), creates it
           without clobbering (a concurrent duplicate raises and nothing is dropped), and writes a creation receipt with
           the new database's oid only after the create succeeded.
  destroy  drops the database only if its current oid equals the receipt's oid. A database that was replaced (same
           name, different oid), or one without a receipt, is left untouched and the refusal is recorded.

Receipts are JSON lines in the file named by VERIFICATION_DB_RECEIPT, each carrying the run nonce from
VERIFICATION_RUN_NONCE. The route's cleanup reads them to decide ownership. keepdb is refused.
"""
import datetime
import json
import os

RECEIPT_ENV = "VERIFICATION_DB_RECEIPT"
NONCE_ENV = "VERIFICATION_RUN_NONCE"


class CollisionRefused(RuntimeError):
    pass


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def write_receipt(event, **fields):
    path, nonce = os.environ.get(RECEIPT_ENV), os.environ.get(NONCE_ENV)
    if not path or not nonce:
        raise RuntimeError("%s and %s must be set by the verification route" % (RECEIPT_ENV, NONCE_ENV))
    record = {"event": event, "run_nonce": nonce, "at": _now(), "pid": os.getpid(), **fields}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")
        f.flush()
        os.fsync(f.fileno())


def read_receipts(path, nonce):
    """Returns (records, problem). A record with another nonce or an unreadable line makes the file untrustworthy."""
    if not os.path.exists(path):
        return [], None
    records = []
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            return records, "line %d is not JSON" % n
        if rec.get("run_nonce") != nonce:
            return records, "line %d belongs to another run" % n
        records.append(rec)
    return records, None


def database_oid(cursor, name):
    cursor.execute("SELECT oid FROM pg_database WHERE datname = %s", [name])
    row = cursor.fetchone()
    return int(row[0]) if row else None


def install(creation):
    """Replace a connection's test database creation and destruction with owned, non-clobbering versions."""
    if not os.environ.get(RECEIPT_ENV) or not os.environ.get(NONCE_ENV):
        raise RuntimeError("the verification runners require %s and %s" % (RECEIPT_ENV, NONCE_ENV))
    quote = creation.connection.ops.quote_name

    def _create_test_db(verbosity, autoclobber, keepdb=False):
        if keepdb:
            raise CollisionRefused("keepdb is not permitted on the verification route")
        name = creation._get_test_db_name()
        with creation._nodb_cursor() as cursor:
            existing = database_oid(cursor, name)
            if existing is not None:
                write_receipt("collision_refused", name=name, existing_oid=existing)
                raise CollisionRefused("test database %s already exists (oid %s); refused without dropping it" % (name, existing))
            # keepdb=False and no autoclobber path: a database created meanwhile by another actor raises here, and
            # nothing is dropped
            creation._execute_create_test_db(cursor, {"dbname": quote(name), "suffix": creation.sql_table_creation_suffix()}, keepdb=False)
            oid = database_oid(cursor, name)
        if oid is None:
            raise RuntimeError("test database %s not found after CREATE DATABASE" % name)
        write_receipt("created", name=name, oid=oid)
        return name

    def _destroy_test_db(test_database_name, verbosity):
        records, problem = read_receipts(os.environ[RECEIPT_ENV], os.environ[NONCE_ENV])
        created = [r for r in records if r.get("event") == "created" and r.get("name") == test_database_name]
        with creation._nodb_cursor() as cursor:
            current = database_oid(cursor, test_database_name)
            if problem or not created or current != created[-1]["oid"]:
                write_receipt("destroy_refused", name=test_database_name, current_oid=current,
                              receipt_oid=created[-1]["oid"] if created else None, receipt_problem=problem)
                return
            cursor.execute("DROP DATABASE %s" % quote(test_database_name))
            absent = database_oid(cursor, test_database_name) is None
        write_receipt("destroyed", name=test_database_name, oid=created[-1]["oid"], confirmed_absent=absent)

    creation._create_test_db = _create_test_db
    creation._destroy_test_db = _destroy_test_db


class OwnedDatabaseRunnerMixin:
    """Mixed into every route test runner, ahead of DiscoverRunner."""

    def setup_databases(self, **kwargs):
        from django.db import connections
        for alias in connections:
            install(connections[alias].creation)
        return super().setup_databases(**kwargs)
