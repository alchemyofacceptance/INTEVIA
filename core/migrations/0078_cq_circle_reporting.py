"""DISC4. A Circle's own qualified decision governs its reporting preference."""

from importlib import import_module

from django.db import migrations

previous = import_module("core.migrations.0077_cq_settings_authority")
one = previous.one
function = previous.function

HELPERS = r"""
-- The immutable implementation receipt is the local version. Its ILC event
-- and its verified, unique SETTLE source already commit together.
CREATE INDEX cq4_circle_versions ON public.core_cqact (organism_id,board_uuid,ordinal DESC)
 WHERE operation='IMPLEMENT' AND facts->'domain_plan'->>'domain'='CIRCLE_REPORTING';

CREATE FUNCTION public.cq4_local_version(p_lo bigint,p_circle bigint,p_cut timestamptz)
RETURNS public.core_cqact LANGUAGE sql STABLE AS $$
 SELECT i.* FROM public.core_cqact i JOIN public.core_discboard b USING(board_uuid)
 WHERE i.organism_id=p_lo AND b.circle_id=p_circle AND i.operation='IMPLEMENT'
   AND i.facts->'domain_plan'->>'domain'='CIRCLE_REPORTING'
   AND i.effective_at<=p_cut AND i.recorded_at<=p_cut ORDER BY i.ordinal DESC LIMIT 1
$$;

CREATE FUNCTION public.cq4_reporting_law(p_lo bigint,p_circle bigint,p_thread uuid,p_cut timestamptz)
RETURNS jsonb LANGUAGE sql STABLE AS $$
 SELECT jsonb_build_object('origin','CONSTITUTION','version',declaration.act_uuid,
    'value',law->'value'->'reporting','scope',law->'body','provision',law)
 FROM public.core_livingorganism lo LEFT JOIN public.core_circle c ON c.id=p_circle AND c.parent_organism_id=lo.id
 CROSS JOIN LATERAL jsonb_array_elements(public.cq_settings_at(lo.id,p_cut)->'provisions') law
 CROSS JOIN LATERAL (SELECT a.act_uuid FROM public.core_cqact a WHERE a.organism_id=lo.id
    AND a.operation='SETTINGS' AND a.effective_at<=p_cut AND a.recorded_at<=p_cut
    AND a.facts->'settings'->'provisions' @> jsonb_build_array(law)
    AND NOT a.facts->'previous'->'provisions' @> jsonb_build_array(law)
    ORDER BY a.ordinal DESC LIMIT 1) declaration
 WHERE lo.id=p_lo AND law->>'setting'='REPORTING' AND law->>'stage'='VOTE'
   AND law->>'body' IN ('ALL_BOARDS',coalesce(c.circle_uuid::text,'LO'))
   AND law->>'decision' IN ('ALL_DECISIONS',p_thread::text)
   AND public.cq_law_current(lo.organism_id,law,p_cut)
$$;

CREATE FUNCTION public.cq4_reporting(p_lo bigint,p_circle bigint,p_thread uuid,p_cut timestamptz)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE local_act public.core_cqact; rule jsonb; choice text;
BEGIN
 rule:=public.cq4_reporting_law(p_lo,p_circle,p_thread,p_cut);
 IF rule IS NOT NULL THEN RETURN rule; END IF;
 IF p_circle IS NOT NULL THEN
   local_act:=public.cq4_local_version(p_lo,p_circle,p_cut);
   choice:=local_act.facts->'domain_plan'->>'choice';
   IF choice IN ('NAMED','TOTALS_ONLY') THEN RETURN jsonb_build_object('origin','CIRCLE',
     'version',local_act.act_uuid,'value',choice,'scope','CIRCLE'); END IF;
 END IF;
 rule:=public.cq_reporting_at(p_lo,p_cut);
 IF choice='INHERIT' THEN rule:=rule||jsonb_build_object('inheritance_version',local_act.act_uuid); END IF;
 RETURN rule;
END $$;

CREATE FUNCTION public.cq4_board_reporting(p_lo bigint,p_board uuid,p_thread uuid,p_cut timestamptz)
RETURNS jsonb LANGUAGE sql STABLE AS $$
 SELECT public.cq4_reporting(p_lo,b.circle_id,p_thread,p_cut) FROM public.core_discboard b
 WHERE b.board_uuid=p_board AND b.organism_id=p_lo
$$;

CREATE FUNCTION public.cq4_circle_plan(p_lo bigint,p_board uuid,p_actor bigint,p_att jsonb,p_cut timestamptz)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE spec jsonb:=p_att->'spec'; choice text:=spec->>'reporting'; local_act public.core_cqact;
 target public.core_circle; owner_uuid uuid; old_rule jsonb; law jsonb;
BEGIN
 SELECT * INTO target FROM public.core_circle c WHERE c.parent_organism_id=p_lo AND c.circle_uuid::text=spec->>'circle';
 IF target.id IS NULL THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 IF NOT EXISTS(SELECT FROM public.core_discboard b WHERE b.board_uuid=p_board
     AND b.organism_id=p_lo AND b.circle_id=target.id) THEN
   IF NOT coalesce(public.disc_audience(p_lo,target.id,p_actor,'MEMBERS',p_cut),false) THEN
     PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
   RETURN jsonb_build_object('code','CQ_BODY','reason','This board cannot decide this act. Put it forward in the body that owns it.');
 END IF;
 BEGIN
   IF (p_att->>'text')::jsonb IS DISTINCT FROM spec THEN PERFORM public.cq_refuse('CQ_SETTINGS_WORDING'); END IF;
 EXCEPTION WHEN invalid_text_representation THEN PERFORM public.cq_refuse('CQ_SETTINGS_WORDING'); END;
 IF NOT(spec ?& ARRAY['organism','circle','predecessor','reporting']) OR spec-ARRAY['organism','circle','predecessor','reporting']<>'{}'::jsonb
    OR jsonb_typeof(spec->'predecessor') NOT IN ('string','null') OR choice IS NULL
    OR choice NOT IN ('NAMED','TOTALS_ONLY','INHERIT') THEN PERFORM public.cq_refuse('CQ_INPUT'); END IF;
 IF NOT (public.cq_settings_at(p_lo,p_cut)->>'enabled')::boolean THEN
   RETURN jsonb_build_object('code','CQ_DISABLED','reason','Voting was switched off. Review the current settings before putting this change forward again.');
 END IF;
 local_act:=public.cq4_local_version(p_lo,target.id,p_cut);
 IF spec->>'predecessor' IS DISTINCT FROM local_act.act_uuid::text THEN
   RETURN jsonb_build_object('code','CQ_SETTINGS_STALE','reason','The Circle reporting version changed. Put forward a proposal against the current version.');
 END IF;
 SELECT lo.organism_id INTO STRICT owner_uuid FROM public.core_livingorganism lo WHERE lo.id=p_lo;
 IF choice<>'INHERIT' AND NOT public.s015_entitlement_effective(owner_uuid,'CQ_TOTALS_ONLY',p_cut) THEN
   RETURN jsonb_build_object('code','CQ_SUBSCRIBER','reason','Subscriber access is required to select this CQ option. An option already chosen stays in force.');
 END IF;
 law:=public.cq4_reporting_law(p_lo,target.id,NULL,p_cut);
 IF choice<>'INHERIT' AND law IS NOT NULL AND law->>'value'<>choice THEN
   RETURN jsonb_build_object('code','CQ_REPORTING_LAW','reason','The cited constitutional provision fixes reporting for this Circle. Follow the provision or change the law first.');
 END IF;
 IF choice=coalesce(local_act.facts->'domain_plan'->>'choice','INHERIT') THEN
   RETURN jsonb_build_object('code','CQ_SETTINGS_UNCHANGED','reason','These CQ settings are already in force.');
 END IF;
 old_rule:=public.cq4_reporting(p_lo,target.id,NULL,p_cut);
 RETURN jsonb_build_object('domain','CIRCLE_REPORTING','route','CQ_CIRCLE','circle',target.circle_uuid,'choice',choice,
    'predecessor',local_act.act_uuid,'previous_choice',coalesce(local_act.facts->'domain_plan'->>'choice','INHERIT'),
    'previous_rule',old_rule,'value',coalesce(law->>'value',nullif(choice,'INHERIT'),public.cq_reporting_at(p_lo,p_cut)->>'value'));
END $$;
"""


def edits():
    current = {
        new.split("public.", 1)[1].split("(", 1)[0]: new for _, new in previous.edits()
    }
    # Retain historical LO scope evidence. A new LO act cannot delegate or
    # enforce the local reporting choice after the Governor's simpler Q3 ruling.
    old = function(previous.HELPERS, "cq4_checked")
    yield (
        old,
        one(
            old,
            " checked:=public.cq_checked_settings",
            """ IF p_patch->'changes' ? 'reporting_scope'
    AND p_patch->'changes'->'reporting_scope' IS DISTINCT FROM coalesce(
      (SELECT a.facts->'settings'->'reporting_scope' FROM public.core_cqact a
       WHERE a.organism_id=p_lo AND a.operation='SETTINGS' ORDER BY a.ordinal DESC LIMIT 1),
      public.cq_settings_at(p_lo,p_cut)->'reporting_scope') THEN
   PERFORM public.cq_refuse('CQ_REPORT_SCOPE'); END IF;
 checked:=public.cq_checked_settings""",
        ),
    )
    old = function(previous.HELPERS, "cq4_plan")
    yield (
        old,
        one(
            old,
            "     WHEN 'CQ_REPORT_DATE' THEN",
            """     WHEN 'CQ_REPORT_SCOPE' THEN 'Each subscribing Circle decides its own reporting. Put a local change to that Circle.'
     WHEN 'CQ_REPORT_DATE' THEN""",
        ),
    )
    old = current["cq3_plan"]
    new = one(old, "'GI','SETTINGS') THEN", "'GI','SETTINGS','CIRCLE_REPORTING') THEN")
    yield (
        old,
        one(
            new,
            " IF op='SETTINGS' THEN",
            " IF op='CIRCLE_REPORTING' THEN RETURN public.cq4_circle_plan(p_lo,p_board,p_actor,p_attachment,p_cut); END IF;\n IF op='SETTINGS' THEN",
        ),
    )

    old = current["cq3_apply"]
    yield (
        old,
        one(
            old,
            " IF plan->>'domain'='SETTINGS' THEN",
            " IF plan->>'domain'='CIRCLE_REPORTING' THEN RETURN NULL; END IF;\n IF plan->>'domain'='SETTINGS' THEN",
        ),
    )
    old = current["cq3_check_committed"]
    yield (
        old,
        one(
            old,
            " IF plan->>'domain'='SETTINGS' THEN",
            """ IF plan->>'domain'='CIRCLE_REPORTING' THEN
   IF NOT EXISTS(SELECT FROM public.core_cqact r JOIN public.core_discboard b USING(board_uuid)
     JOIN public.core_circle c ON c.id=b.circle_id AND c.parent_organism_id=b.organism_id
     WHERE r.act_uuid::text=a.request->>'result' AND r.facts->'domain_plan'=plan
       AND plan->>'circle'=c.circle_uuid::text AND public.cq3_implement_source(a)=a.authority_source)
     THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
   RETURN;
 END IF;
 IF plan->>'domain'='SETTINGS' THEN""",
        ),
    )
    old = function(previous.clarity.FORWARD, "cq_check_provisions")
    yield (
        old,
        one(
            old,
            "WHEN 'ABSTENTION' THEN ARRAY['abstention'] END;",
            "WHEN 'ABSTENTION' THEN ARRAY['abstention'] WHEN 'REPORTING' THEN ARRAY['reporting'] END;",
        ),
    )
    old = function(previous.authority.ballots.FORWARD, "cq_ballot_facts")
    term = "public.cq_reporting_at(p_lo,p_cut)"
    assert old.count(term) == 2
    yield (
        old,
        old.replace(term, "public.cq4_board_reporting(p_lo,p_board,p_thread,p_cut)"),
    )
    old = current["cq_input"]
    yield (
        old,
        one(
            old,
            "public.cq_reporting_at(NEW.organism_id,v_stamp)",
            "public.cq4_board_reporting(NEW.organism_id,NEW.board_uuid,NEW.thread_uuid,v_stamp)",
        ),
    )
    for name, row in (("cq_append", "NEW"), ("cq_event_matches", "a")):
        old = current[name]
        term = f"{row}.operation='SETTINGS' AND {row}.authority_source->>'kind'='CQ_RESULT'"
        yield (
            old,
            one(
                old,
                term,
                f"({row}.operation='SETTINGS' OR ({row}.operation='IMPLEMENT' AND "
                f"{row}.facts->'domain_plan'->>'domain'='CIRCLE_REPORTING')) AND {row}.authority_source->>'kind'='CQ_RESULT'",
            ),
        )


def install(apps, editor):
    editor.execute(HELPERS, params=None)
    for _, new in edits():
        editor.execute(new, params=None)


def reverse(apps, editor):
    editor.execute("LOCK TABLE public.core_cqact IN ACCESS EXCLUSIVE MODE")
    with editor.connection.cursor() as cursor:
        cursor.execute("""SELECT EXISTS(SELECT FROM public.core_cqact a
            WHERE a.request->'attachment'->>'kind'='CIRCLE_REPORTING' OR
            a.request->'attachment'->'spec'->'changes'->'provisions' @> '[{"setting":"REPORTING"}]'::jsonb OR
            a.facts->'settings'->'provisions' @> '[{"setting":"REPORTING"}]'::jsonb)""")
        if cursor.fetchone()[0]:
            raise RuntimeError(
                "Circle reporting authority has been recorded. Preserve its history."
            )
    for old, _ in reversed(list(edits())):
        editor.execute(old, params=None)
    editor.execute(
        """DROP INDEX public.cq4_circle_versions;
        DROP FUNCTION public.cq4_circle_plan(bigint,uuid,bigint,jsonb,timestamptz),
        public.cq4_board_reporting(bigint,uuid,uuid,timestamptz),
        public.cq4_reporting(bigint,bigint,uuid,timestamptz),
        public.cq4_reporting_law(bigint,bigint,uuid,timestamptz),
        public.cq4_local_version(bigint,bigint,timestamptz)""",
        params=None,
    )


class Migration(migrations.Migration):
    dependencies = (("core", "0077_cq_settings_authority"),)
    operations = (migrations.RunPython(install, reverse),)
