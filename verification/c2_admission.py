"""Once-only C2 faults on the run-owned, already-qualified clone."""
import hashlib
from importlib import import_module
from pathlib import Path

from django.db import connection

from verification.c1_admission import C1AdmissionRunner


class C2AdmissionRunner(C1AdmissionRunner):
    registry = Path(__file__).with_suffix(".json")
    unit = "C2"
    restore_sql = None

    def _install_fault(self):
        if self.entry["kind"] == "python":
            return super()._install_fault()
        module = import_module("core.migrations.0055_c2_coverage")
        data = Path(module.__file__).read_bytes().replace(b"\r\n", b"\n")
        if hashlib.sha256(data).hexdigest() != self.entry["source_sha256"]:
            raise RuntimeError("The C2 SQL fault does not match the candidate migration.")
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_get_functiondef(oid),prosrc FROM pg_proc WHERE oid=%s::regprocedure",
                           [self.entry["signature"]])
            definition, body = cursor.fetchone()
            old = self.entry["old"]
            if body.count(old) != 1 or body not in module.SQL or definition.count(old) != 1:
                raise RuntimeError("The installed C2 function differs from the candidate fault carrier.")
            cursor.execute("SELECT current_database()")
            self.fault_database, = cursor.fetchone()
            self.restore_sql = definition
            cursor.execute(definition.replace(old, self.entry["new"], 1))

    def _restore(self):
        if self.restore_sql is not None:
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_database()")
                if cursor.fetchone()[0] != self.fault_database:
                    self.restore_sql = None
                    raise RuntimeError("C2 fault restoration refused because the owned clone changed.")
                cursor.execute(self.restore_sql)
            self.restore_sql = None
        super()._restore()
