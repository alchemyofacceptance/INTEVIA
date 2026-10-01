"""S015 PKT-B LOI (authorised by the Human Governor, 1 Oct 2026, D1): the organism register's stale-row cleanup deletes only stale rows it
can lock without waiting (FOR UPDATE SKIP LOCKED, confined to this housekeeping statement). The eligibility rule, the re-entry check, the
lock-order check, the organism row lock and the register insert are unchanged. This removes the witnessed waits on cleanup rows and the
cycles they caused; it does not claim that every possible deadlock is eliminated. Derived from 0021's FUNCTIONS_SQL by exact-once
substitution. The reverse restores 0021's implementation INCLUDING ITS KNOWN DEFECT, and restores no operational clearance."""
import importlib

from django.db import migrations

CLEANUP = ("DELETE FROM public.s015_transaction_register WHERE kind = 'ORGANISM_LOCK' "
           "AND pg_xact_status(xid) IS DISTINCT FROM 'in progress';")
CLEANUP_NONBLOCKING = ("DELETE FROM public.s015_transaction_register WHERE ctid IN (SELECT ctid FROM public.s015_transaction_register "
                       "WHERE kind = 'ORGANISM_LOCK' AND pg_xact_status(xid) IS DISTINCT FROM 'in progress' FOR UPDATE SKIP LOCKED);")


def replace_once(text, anchor, replacement):
    if text.count(anchor) != 1:
        raise RuntimeError(f"S015 0032 expected exactly one anchor: {anchor!r}")
    return text.replace(anchor, replacement, 1)


def register_sql(nonblocking):
    src = importlib.import_module("core.migrations.0021_s015_recorded_chain_and_bitemporal_fold").FUNCTIONS_SQL
    start = src.index("CREATE FUNCTION public.s015_register_organism_lock(")
    end = src.index("END $$;", start) + len("END $$;")
    body = replace_once(src[start:end], "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION")
    return replace_once(body, CLEANUP, CLEANUP_NONBLOCKING) if nonblocking else body


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        raise RuntimeError("S015 0032 requires PostgreSQL")
    schema_editor.execute(register_sql(True), params=None)


def reverse(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        raise RuntimeError("S015 0032 requires PostgreSQL")
    schema_editor.execute(register_sql(False), params=None)


class Migration(migrations.Migration):
    atomic = True
    dependencies = [("core", "0031_s1fu_admission_actor_requirement")]
    operations = [migrations.RunPython(install, reverse)]
