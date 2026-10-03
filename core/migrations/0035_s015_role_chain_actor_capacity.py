from django.db import migrations


TABLE = "public.core_contextualroleassignmentstateevent"
ACTOR_STATE_CHECK = "s015_0022_contextualroleassignmentstateevent_actor_state_vocab_ck"
ACTOR_CAPACITY_CHECK = "s015_0022_contextualroleassignmentstateevent_actor_capacity_state_ck"


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        raise RuntimeError("S015 0035 requires PostgreSQL")
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"ALTER TABLE {TABLE} DROP CONSTRAINT {ACTOR_STATE_CHECK}")
        cursor.execute(
            f"ALTER TABLE {TABLE} ADD CONSTRAINT {ACTOR_STATE_CHECK} "
            "CHECK (actor_state IN ('PARTY','NONE','NOT_ESTABLISHED','LIVING_ORGANISM'))"
        )
        cursor.execute(f"ALTER TABLE {TABLE} DROP CONSTRAINT {ACTOR_CAPACITY_CHECK}")
        cursor.execute(
            f"ALTER TABLE {TABLE} ADD CONSTRAINT {ACTOR_CAPACITY_CHECK} "
            "CHECK ((actor_state IN ('PARTY','LIVING_ORGANISM')) = "
            "(actor_capacity = 'STANDING'))"
        )


def reverse(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        raise RuntimeError("S015 0035 requires PostgreSQL")
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            f"SELECT count(*) FROM {TABLE} WHERE actor_state = 'LIVING_ORGANISM'"
        )
        if cursor.fetchone()[0]:
            raise RuntimeError(
                "S015 0035 reversal refused: LO-actor role events exist"
            )
        cursor.execute(f"ALTER TABLE {TABLE} DROP CONSTRAINT {ACTOR_CAPACITY_CHECK}")
        cursor.execute(
            f"ALTER TABLE {TABLE} ADD CONSTRAINT {ACTOR_CAPACITY_CHECK} "
            "CHECK ((actor_state = 'PARTY') = (actor_capacity = 'STANDING'))"
        )
        cursor.execute(f"ALTER TABLE {TABLE} DROP CONSTRAINT {ACTOR_STATE_CHECK}")
        cursor.execute(
            f"ALTER TABLE {TABLE} ADD CONSTRAINT {ACTOR_STATE_CHECK} "
            "CHECK (actor_state IN ('PARTY','NONE','NOT_ESTABLISHED'))"
        )


class Migration(migrations.Migration):
    atomic = True

    dependencies = [("core", "0034_s015_authority_decisions")]

    operations = [migrations.RunPython(install, reverse)]