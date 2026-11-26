"""Separate reading Board discussions from the authority to contribute (CR, DI, DJ)."""

from importlib import import_module

from django.db import migrations

from core.migrations._clock_conversion import swap


READ_AUDIENCE = """
CREATE FUNCTION public.disc_read_audience(
    p_lo bigint, p_circle bigint, p_person bigint, p_level text, p_cut timestamptz
) RETURNS boolean LANGUAGE sql STABLE SECURITY INVOKER AS $$
SELECT CASE
    WHEN p_level IS NULL OR p_level NOT IN ('MEMBERS','PROBATIONARY','COORDINATORS')
        THEN false
    WHEN p_level IN ('MEMBERS','PROBATIONARY')
        AND EXISTS (SELECT FROM public.core_circle c
                    WHERE c.id = p_circle AND c.parent_organism_id = p_lo
                      AND c.kind = 'GOVERNANCE')
        AND public.disc_audience(p_lo, NULL, p_person, 'MEMBERS', p_cut)
        THEN true
    ELSE coalesce(public.disc_audience(p_lo, p_circle, p_person, p_level, p_cut), false)
END
$$;
"""

BOARD_PLACE = """
CREATE FUNCTION public.disc_board_place_ok(
    p_lo bigint, p_circle bigint, p_person bigint, p_cut timestamptz
) RETURNS boolean LANGUAGE sql STABLE SECURITY INVOKER AS $$
SELECT CASE
    WHEN EXISTS (SELECT FROM public.core_circle c
                 WHERE c.id = p_circle AND c.parent_organism_id = p_lo
                   AND c.kind = 'GOVERNANCE')
    THEN EXISTS (
        SELECT FROM public.core_circleparticipation p
        JOIN public.core_organismmembership m ON m.id = p.membership_id
        WHERE p.circle_id = p_circle AND m.identity_id = p_person
          AND m.living_organism_id = p_lo
          AND p.effective_at <= p_cut AND p.received_at <= p_cut AND p.recorded_at <= p_cut
          AND NOT EXISTS (
              SELECT FROM public.core_circleparticipationend x WHERE x.participation_id = p.id
                AND x.effective_at <= p_cut AND x.received_at <= p_cut AND x.recorded_at <= p_cut))
    ELSE true
END
$$;
"""

SOURCE = "public.discp_source(jsonb,bigint,timestamptz)"
OLD_CHECK = "   OR NOT public.disc_audience(v_board.organism_id,v_board.circle_id,p_person,v_category.access_level,p_cut) THEN"
NEW_CHECK = """   OR NOT public.disc_audience(v_board.organism_id,v_board.circle_id,p_person,v_category.access_level,p_cut)
   OR NOT public.disc_board_place_ok(v_board.organism_id, v_board.circle_id, p_person, p_cut) THEN"""


def source_bodies():
    """Take the received source from 0073, retaining the body's original whitespace."""
    posts = import_module("core.migrations.0073_discussion_posts")
    clarity = import_module("core.migrations.0076_cq_authority")
    hard = import_module("core.migrations.0081_hard_voting_authority")
    previous = hard.body(clarity.function(posts.FORWARD, "discp_source"))
    if previous.count(OLD_CHECK) != 1:
        raise RuntimeError("The posting authority's category check must occur exactly once.")
    return previous, previous.replace(OLD_CHECK, NEW_CHECK)


def install(apps, schema_editor):
    previous, revised = source_bodies()
    schema_editor.execute(READ_AUDIENCE, params=None)
    schema_editor.execute(BOARD_PLACE, params=None)
    with schema_editor.connection.cursor() as cursor:
        swap(cursor, SOURCE, previous, revised)


def uninstall(apps, schema_editor):
    previous, revised = source_bodies()
    with schema_editor.connection.cursor() as cursor:
        swap(cursor, SOURCE, revised, previous)
    schema_editor.execute("DROP FUNCTION public.disc_board_place_ok(bigint,bigint,bigint,timestamptz)")
    schema_editor.execute("DROP FUNCTION public.disc_read_audience(bigint,bigint,bigint,text,timestamptz)")


class Migration(migrations.Migration):
    atomic = True
    dependencies = (("core", "0118_author_clarity_and_conflict"),)
    operations = (migrations.RunPython(install, uninstall),)
