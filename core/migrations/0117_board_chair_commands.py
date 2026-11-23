"""Give the governing Board's four exceptional vote commands to its effective Chair (CF)."""

from importlib import import_module

from django.db import migrations

from core.migrations._clock_conversion import swap


PREPARER = import_module("core.migrations.0115_suggestion_preparer")
# Insert after hidden-opening visibility and specialist election checks, before
# the ordinary Suggestion author's independent membership route.
ANCHOR = " IF v_state IN ('SUSPENDED','RESTRICTED') THEN"
CHAIR = """ IF p_op IN ('PREPARED','PROCEED','EXTEND','CLOSE') AND EXISTS (
   SELECT FROM public.core_circle c WHERE c.id=v_board.circle_id
     AND c.parent_organism_id=p_lo AND c.kind='GOVERNANCE'
 ) THEN
   SELECT jsonb_build_object('kind','OFFICE','reference',a.assignment_uuid,
          'organism',lo.organism_id,'circle',c.circle_uuid,'route','BOARD_CHAIR')
          || CASE WHEN grant_.act_uuid IS NOT NULL THEN
             jsonb_build_object('transfer',grant_.act_uuid,'policy_decision',grant_.decision)
             ELSE '{}'::jsonb END
     INTO v_office
     FROM public.core_contextualroleassignment a
     JOIN public.core_organismmembership m ON m.id=a.membership_id
     JOIN public.core_livingorganism lo ON lo.id=m.living_organism_id
     JOIN public.core_circle c ON c.parent_organism_id=lo.id AND c.id=v_board.circle_id
     LEFT JOIN LATERAL (SELECT public.elect_attachment(p_thread) AS att) election ON true
     LEFT JOIN LATERAL public.elect_last_transfer(p_lo,public.elect_run_scope(election.att->'spec'),p_cut) grant_
       ON election.att IS NOT NULL AND NOT public.elect_board_confirmation(election.att)
     WHERE a.id=public.cmt_board_chair(p_lo,p_cut,p_cut) AND m.identity_id=p_person
       AND lo.id=p_lo AND public.cq3_board(p_lo,p_cut,p_cut)=c.id
       AND public.s015_fold_effective_state('core_livingorganism',lo.id,p_cut,p_cut)='ACTIVE'
       AND public.c2_basis_in_force(lo.founding_authority_basis_id,p_cut,p_cut)
       AND (NOT lo.platform_root OR public.r_circle_root_qualified(p_lo,p_cut,p_cut))
       AND (grant_.act_uuid IS NULL OR (grant_.transfer->>'holder_circle')::bigint=c.id);
 END IF;
"""


def qualified_bodies():
    _, previous = PREPARER.qualified_bodies()
    if previous.count(ANCHOR) != 1:
        raise RuntimeError("Cannot locate the single received Board command boundary.")
    return previous, previous.replace(ANCHOR, CHAIR + ANCHOR)


def install(apps, schema_editor):
    previous, revised = qualified_bodies()
    with schema_editor.connection.cursor() as cursor:
        swap(cursor, PREPARER.SIGNATURE, previous, revised)


def uninstall(apps, schema_editor):
    previous, revised = qualified_bodies()
    with schema_editor.connection.cursor() as cursor:
        swap(cursor, PREPARER.SIGNATURE, revised, previous)


class Migration(migrations.Migration):
    atomic = True
    dependencies = (("core", "0116_clarity_full_participation"),)
    operations = (migrations.RunPython(install, uninstall),)
