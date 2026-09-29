import importlib

from django.db import migrations


GUARD_NAMES = ("s3_claim_guard", "s3_end_guard", "s3_engagement_guard", "s3_terminal_lock")
LOCK_CALL = "    PERFORM public.s3_lock_application(NEW.application_id);"
END_LOCK_CALL = "    PERFORM public.s3_lock_application(application_id);"


def guard_bodies(*, post_lock_time):
    previous = importlib.import_module("core.migrations.0025_s015_application_routes")
    for name in GUARD_NAMES:
        original, = (sql for sql in previous.INSTALL_SQL if sql.lstrip().startswith(f"CREATE FUNCTION public.{name}()"))
        if post_lock_time:
            lock_call = END_LOCK_CALL if name == "s3_end_guard" else LOCK_CALL
            if original.count(lock_call) != 1:
                raise RuntimeError(f"unexpected 0025 guard body for {name}")
            original = original.replace(
                lock_call, lock_call + "\n    NEW.recorded_at := clock_timestamp();", 1,
            )
        yield original.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        raise RuntimeError("S015 application guards require PostgreSQL")
    with schema_editor.connection.cursor() as cursor:
        for sql in guard_bodies(post_lock_time=True):
            cursor.execute(sql)


def reverse(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        raise RuntimeError("S015 application guards require PostgreSQL")
    with schema_editor.connection.cursor() as cursor:
        for sql in guard_bodies(post_lock_time=False):
            cursor.execute(sql)


class Migration(migrations.Migration):
    atomic = True

    dependencies = [("core", "0025_s015_application_routes")]

    operations = [migrations.RunPython(install, reverse)]