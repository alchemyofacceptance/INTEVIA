"""Use the effective CQ setting and enforce the constitutional seat route."""

from importlib import import_module

from django.db import migrations

elect = import_module("core.migrations.0080_elect_authority_and_vacancy")
clarity = elect.p1.clarity
circle = elect.p1.circle
SELECTOR = "public.cq_settings_values_at(bigint,timestamptz,timestamptz)"
SELECTOR_SQL = """
CREATE FUNCTION public.cq_settings_values_at(p_lo bigint,p_effective timestamptz,p_known timestamptz)
RETURNS jsonb LANGUAGE sql STABLE STRICT AS $$
 SELECT coalesce((
   SELECT a.facts->'settings' FROM public.core_cqact a
   WHERE a.organism_id=p_lo AND a.operation='SETTINGS'
     AND a.effective_at<=p_effective AND a.received_at<=p_known AND a.recorded_at<=p_known
   ORDER BY a.ordinal DESC LIMIT 1
 ), (SELECT settings FROM public.cq_platform_defaults))
$$;
"""


def edits():
    settings = elect.function(clarity.FORWARD, "cq_settings_at")
    start = settings.index(" SELECT coalesce(")
    end = settings.index(" || jsonb_build_object", start)
    yield ("public.cq_settings_at(bigint,timestamptz)", settings,
           settings[:start] + " SELECT public.cq_settings_values_at(p_lo,p_cut,p_cut)" + settings[end:])

    volunteer = elect.function(circle.SQL, "circle_direct_volunteer")
    new = elect.one(volunteer, " SELECT NOT EXISTS(SELECT FROM current_choice)",
                    " SELECT (public.cq_settings_values_at(lo,at_time,known)->>'enabled')::boolean IS FALSE\n"
                    "    AND (NOT EXISTS(SELECT FROM current_choice)")
    new = elect.one(new, "AND v.cq_active IS FALSE AND v.inherit_portal_cq_settings IS FALSE)",
                    "AND v.inherit_portal_cq_settings IS FALSE))")
    yield "public.circle_direct_volunteer(bigint,timestamptz,timestamptz)", volunteer, new

    guard, = (after for _, after in elect.edits()
              if elect.p1.name(after) == "sbg_guard_circleadmissiondecision")
    boundary = """
    IF (NEW.route='CIRCLE_FOUNDING') = EXISTS (
      SELECT FROM public.core_livingorganismevent b
      WHERE b.living_organism_id=v_circle.parent_organism_id AND b.action='BIRTHING'
        AND b.effective_at<=NEW.effective_at AND b.received_at<=NEW.received_at
        AND b.recorded_at<=NEW.received_at
    ) THEN
      RAISE EXCEPTION 'SBG_SEAT_ROUTE_UNAVAILABLE' USING ERRCODE='integrity_constraint_violation';
    END IF;
    IF NEW.route='GOVERNANCE_CONSTITUTIONAL' AND NOT EXISTS (
      SELECT FROM jsonb_array_elements(v_rule.body->'seats') AS seat(value)
      WHERE seat.value->>'seat_id'=NEW.seat_id
        AND (seat.value->>'effective_from')::timestamptz<=NEW.effective_at
        AND seat.value->>'admission_route'='APPOINTMENT'
        AND seat.value->>'deciding_body'='office:LO_COORDINATOR'
    ) THEN
      RAISE EXCEPTION 'SBG_SEAT_ROUTE_UNAVAILABLE' USING ERRCODE='integrity_constraint_violation';
    END IF;
"""
    insertion = "    v_expected_settings_basis := 'rule:'"
    yield "public.sbg_guard_circleadmissiondecision()", guard, elect.one(guard, insertion, boundary + insertion)


def installed(cursor, signature):
    cursor.execute("SELECT prosrc FROM pg_proc WHERE oid=to_regprocedure(%s)", [signature])
    row = cursor.fetchone()
    return row[0] if row else None


def body(sql):
    return sql.split(" AS $$", 1)[1].rsplit("$$;", 1)[0]


def change(editor, *, reverse):
    pairs = list(edits())
    with editor.connection.cursor() as cursor:
        if installed(cursor, SELECTOR) != (body(SELECTOR_SQL) if reverse else None):
            raise RuntimeError(f"HARD stopped before replacement. Unexpected definition for {SELECTOR}.")
        for signature, old, new in pairs:
            if installed(cursor, signature) != body(new if reverse else old):
                raise RuntimeError(f"HARD stopped before replacement. Installed body differs for {signature}.")
    if reverse:
        # No new persisted shape, code or reference can depend on this selector.
        # All accepted acts and refusals are readable under their original schemas.
        for _, old, _ in reversed(pairs):
            editor.execute(old, params=None)
        editor.execute(f"DROP FUNCTION {SELECTOR}", params=None)
    else:
        editor.execute(SELECTOR_SQL, params=None)
        for _, _, new in pairs:
            editor.execute(new, params=None)


def install(apps, editor):
    change(editor, reverse=False)


def uninstall(apps, editor):
    change(editor, reverse=True)


class Migration(migrations.Migration):
    dependencies = (("core", "0080_elect_authority_and_vacancy"),)
    operations = (migrations.RunPython(install, uninstall),)
