from django.db import migrations


ADMISSION_ROUTE_SQL = """
CREATE FUNCTION public.s1fu_guard_admission_route() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    -- Mapped PROPOSED -> ACTIVE routes: Coordinator ACCEPT (TRANSPORT) and
    -- LO_AUTOMATIC ACCEPT (COMPOSED). s3_guard_lo_actor validates both facts.
    IF (NEW.prior_state IS DISTINCT FROM 'PROPOSED' OR
        NEW.resulting_state NOT IN ('ACTIVE', 'PROBATIONARY')) AND
       (NEW.prior_state IS DISTINCT FROM 'PROBATIONARY' OR
        NEW.resulting_state IS DISTINCT FROM 'ACTIVE') THEN
        RETURN NEW;
    END IF;
    IF NEW.resulting_state = 'PROBATIONARY' OR NEW.prior_state = 'PROBATIONARY' THEN
        RAISE EXCEPTION 'S1FU admission refused: no mapped probation route'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.actor_state IS DISTINCT FROM 'LIVING_ORGANISM' THEN
        RAISE EXCEPTION 'S1FU admission refused: an LO-decided admission requires the LO as actor'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;
"""

ADMISSION_TRIGGER_SQL = (
    "CREATE TRIGGER s1fu_05_admission_route BEFORE INSERT ON public.core_organismmembershiptransition "
    "FOR EACH ROW EXECUTE FUNCTION public.s1fu_guard_admission_route()"
)


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        raise RuntimeError("S1-FU admission route requires PostgreSQL")
    ex = lambda sql: schema_editor.execute(sql, params=None)
    ex(ADMISSION_ROUTE_SQL)
    ex(ADMISSION_TRIGGER_SQL)


def reverse(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        raise RuntimeError("S1-FU admission route requires PostgreSQL")
    ex = lambda sql: schema_editor.execute(sql, params=None)
    ex("DROP TRIGGER s1fu_05_admission_route ON public.core_organismmembershiptransition")
    ex("DROP FUNCTION public.s1fu_guard_admission_route()")


class Migration(migrations.Migration):
    atomic = True
    dependencies = [("core", "0030_s015_automatic_application")]
    operations = [migrations.RunPython(install, reverse)]