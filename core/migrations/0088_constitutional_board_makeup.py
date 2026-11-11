"""Read Board makeup from the operative local law at the requested cuts."""
from importlib import import_module

from django.db import migrations

stand_down = import_module("core.migrations.0087_members_stand_down")
voting = stand_down.p1
one = voting.one

MAKEUP = """
CREATE FUNCTION public.c108_board_makeup(p_lo bigint,p_cut timestamptz,p_known timestamptz)
RETURNS jsonb LANGUAGE sql STABLE STRICT AS $$
 WITH provisions AS MATERIALIZED (
   SELECT law.binding,law.version,law.version_number,law.class_name,
          public.l_law_rank(law.class_name) AS authority_rank,
          public.elect_document(v.content)->'board_makeup' AS provision
   FROM public.core_livingorganism lo
   CROSS JOIN LATERAL public.l_laws_at(lo.organism_id,p_cut,p_known) law
   JOIN public.core_libraryresourceversion v ON v.id=law.version
   WHERE lo.id=p_lo AND NOT law.inherited AND law.scope='LO:'||lo.organism_id
     AND law.class_name IN ('ARTICLE','BYLAW','POLICY')
 ), declarations AS (
   SELECT p.*,field,
          CASE WHEN jsonb_typeof(provision->field)='number'
               THEN (provision->>field)::numeric END AS n,
          dense_rank() OVER (PARTITION BY field ORDER BY authority_rank) AS priority
   FROM provisions p CROSS JOIN (VALUES ('minimum'),('maximum')) AS fields(field)
   WHERE provision IS NOT NULL AND (jsonb_typeof(provision)<>'object' OR provision ? field)
 ), bounds AS (
   SELECT field,min(n) AS n,
          bool_and(n IS NOT NULL AND n=trunc(n) AND n>=1) AND count(DISTINCT n)=1 AS usable,
          jsonb_agg(jsonb_build_object('binding',binding,'version',version,
              'version_number',version_number,'class',class_name) ORDER BY binding) AS sources
   FROM declarations WHERE priority=1 GROUP BY field
 )
 SELECT jsonb_build_object(
     'minimum',low.n,'maximum',high.n,'minimum_sources',coalesce(low.sources,'[]'),
     'maximum_sources',coalesce(high.sources,'[]'),
     'problem',CASE WHEN low.field IS NULL THEN 'CIRCLE_G4_MAKEUP_MISSING'
          WHEN NOT low.usable OR NOT high.usable OR high.n<low.n THEN 'CIRCLE_G4_MAKEUP_INVALID' END)
 FROM (SELECT 1) seed LEFT JOIN bounds low ON low.field='minimum'
 LEFT JOIN bounds high ON high.field='maximum'
$$;
"""


def edits():
    law = stand_down.law
    old = stand_down.function(law.HELPERS, "cq3_birth_proof")
    first = old.index(" SELECT 'birth:'")
    last = old.index(" FROM public.core_circle c", first)
    # The readable citation and the committee suffix must fit the existing
    # 255-character evidence field. Commit the old formation inputs together.
    select = """
 WITH makeup AS MATERIALIZED (
   SELECT public.c108_board_makeup(c.parent_organism_id,p_cut,p_known) AS rule
   FROM public.core_circle c WHERE c.id=p_circle
 )
 SELECT 'birth:'||snap.snapshot_uuid||
   '|min:'||(mk.rule#>>'{minimum_sources,0,binding}')||'@'||(mk.rule#>>'{minimum_sources,0,version}')||
   coalesce('|max:'||(mk.rule#>>'{maximum_sources,0,binding}')||'@'||(mk.rule#>>'{maximum_sources,0,version}'),'')||
   '|proof:'||public.c2_target(jsonb_build_array(opening.event_uuid,office.id,current_rule.rule_uuid,mk.rule,
     array_agg(p.participation_uuid ORDER BY p.participation_uuid))::text)
"""
    new = old[:first] + select + old[last:]
    new = one(new, " WHERE c.id=p_circle AND NOT lo.platform_root",
              " CROSS JOIN makeup mk\n WHERE mk.rule->>'problem' IS NULL AND c.id=p_circle AND NOT lo.platform_root")
    new = one(new, "GROUP BY snap.snapshot_uuid", "GROUP BY mk.rule,snap.snapshot_uuid")
    new = one(new, "HAVING count(DISTINCT m.identity_id)>=3 AND count(DISTINCT p.seat_id)>=3", """
 HAVING least(count(DISTINCT m.identity_id),count(DISTINCT p.seat_id)) >= (mk.rule->>'minimum')::numeric
    AND (mk.rule->>'maximum' IS NULL OR
         greatest(count(DISTINCT m.identity_id),count(DISTINCT p.seat_id)) <= (mk.rule->>'maximum')::numeric)""")
    yield "0076", old, new

    old = voting.previous(stand_down.cmt, "circle_activation_problem")
    guard = "IF public.cq3_birth_proof(c.id,enterer,cut,as_known) IS NULL THEN RETURN 'CIRCLE_G4_GOVERNANCE_SEATS'; END IF;"
    read = "proof:=public.cq3_birth_proof(c.id,enterer,cut,as_known);"
    new = one(old, guard + "\n         " + read, read + """
         IF proof IS NULL THEN
           RETURN coalesce(public.c108_board_makeup(c.parent_organism_id,cut,as_known)->>'problem',
                           'CIRCLE_G4_GOVERNANCE_SEATS');
         END IF;""")
    yield "0084", old, new


def change(editor, reverse):
    pairs = tuple(edits())
    with editor.connection.cursor() as cursor:
        # Exclude concurrent activations while checking the populated reverse.
        cursor.execute("LOCK TABLE public.core_circlestateevent IN SHARE ROW EXCLUSIVE MODE")
        checks = [(old, new if reverse else old) for _, old, new in pairs]
        checks.append((MAKEUP, MAKEUP if reverse else None))
        for definition, expected in checks:
            sig = stand_down.sig(definition)
            cursor.execute("""SELECT p.prosrc,p.proisstrict,p.provolatile::text,l.lanname::text
                FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang
                WHERE p.oid=to_regprocedure(%s)""", [sig])
            actual = cursor.fetchone()
            wanted = (voting.hard.body(expected), *voting.attributes(expected)) if expected else None
            if actual != wanted:
                raise RuntimeError("Unexpected installed Board makeup function: " + sig)
        if reverse:
            cursor.execute("""SELECT EXISTS(SELECT FROM public.core_circlestateevent
                WHERE action='ACTIVATE' AND evidence_reference LIKE 'birth:%|min:%')""")
            if cursor.fetchone()[0]:
                raise RuntimeError("C1-108 retains activations recorded against a Board makeup provision.")
    if not reverse:
        editor.execute(MAKEUP, params=None)
    for _, old, new in (reversed(pairs) if reverse else pairs):
        editor.execute(voting.replace_sql(old if reverse else new), params=None)
    if reverse:
        editor.execute("DROP FUNCTION " + stand_down.sig(MAKEUP))


def install(apps, editor):
    change(editor, False)


def uninstall(apps, editor):
    change(editor, True)


class Migration(migrations.Migration):
    dependencies = (("core", "0087_members_stand_down"),)
    operations = (migrations.RunPython(install, uninstall),)
