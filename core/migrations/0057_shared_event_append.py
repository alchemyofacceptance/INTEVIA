"""Shared S015 chain append for the C2 trigger route and the Python command route.

Under 00_COMMISSION_C1_16_SHARED_EVENT_APPEND_v1_0.md and 00_COMMISSION_C1_16_v1_1.md,
s015_append_event replaces the row assembly in c2_append_event and append_relationship_event.
"""
from importlib import import_module

from django.db import migrations


SQL = r"""
CREATE FUNCTION public.s015_append_event(
 token text, anchor bigint, action_name text, outcome text,
 at_time timestamptz, occurred timestamptz, received timestamptz, actor bigint, basis bigint,
 decision text, evidence text, request_key text, payload text, metadata jsonb, deadline timestamptz DEFAULT NULL,
 -- command_event IS NOT NULL alone selects the command route. All six trailing
 -- parameters are command-only and ignored on the trigger route. Command callers
 -- must always pass command_event, including when starting a chain without a head.
 command_event uuid DEFAULT NULL, supplied_head bigint DEFAULT NULL, supplied_sequence integer DEFAULT NULL,
 access_epoch bigint DEFAULT NULL, command_rule text DEFAULT NULL, command_key text DEFAULT NULL)
RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE
 is_command boolean := command_event IS NOT NULL;
 event uuid := coalesce(command_event,gen_random_uuid());
 table_name text; fk text; col text; previous_id bigint; previous_seq integer; previous_l1 text; prior text;
 rule_ref text; lineage text; values_ jsonb; cols text; id_ bigint;
BEGIN
 SELECT m.event_table,m.fk_col,m.state_col INTO STRICT table_name,fk,col
 FROM public.s015_anchor_map() m WHERE m.anchor_token=token AND (
    (is_command AND token IN ('core_livingorganism','core_membershipcondition','core_authoritybasis','core_determinationcontestcase'))
 OR (NOT is_command AND token IN ('core_livingorganism','core_essentialcoveragecase','core_restrictedcontinuitycase')));

 IF is_command THEN
    -- Keep the caller's checked predecessor, even for a stale/empty head.
    -- The installed chain guards still decide whether that continuation is lawful.
    previous_id:=supplied_head; previous_seq:=supplied_sequence;
    IF supplied_head IS NOT NULL THEN
       EXECUTE format('SELECT l1_commitment FROM public.%I WHERE id=$1',table_name)
       INTO STRICT previous_l1 USING supplied_head;
    END IF;
    rule_ref:=command_rule;
    lineage:=public.c2_target(format('{"event":"%s","request":%s}',event,to_json(payload)));
    IF EXISTS (SELECT FROM jsonb_object_keys(metadata) k
               WHERE token<>'core_authoritybasis' OR k<>'kind') THEN
       RAISE EXCEPTION 'Unexpected command chain metadata'; END IF;
 ELSE
    EXECUTE format('SELECT id,sequence,l1_commitment FROM public.%I WHERE %I=$1 ORDER BY sequence DESC LIMIT 1',table_name,fk)
    INTO previous_id,previous_seq,previous_l1 USING anchor;
    rule_ref:='C0:G2:C2';
    lineage:=encode(sha256(convert_to(event::text||':'||payload,'UTF8')),'hex');
    IF EXISTS(SELECT FROM jsonb_object_keys(metadata) k WHERE k NOT IN
       ('roster_version','evaluated_state_at','evaluated_known_at','determiner_capacity_reference',
        'permitted_measures_reference','prohibited_effects_reference','triggering_assessment_id','accountable_actor_id')) THEN
       RAISE EXCEPTION 'C2 chain metadata is invalid'; END IF;
 END IF;
 IF NOT is_command OR supplied_head IS NOT NULL THEN
    EXECUTE format('SELECT %I FROM public.%I WHERE %I=$1 AND (effective_at,occurred_at,received_at)<=($2,$3,$4)
        ORDER BY effective_at DESC,occurred_at DESC,received_at DESC,recorded_at DESC,sequence DESC,event_uuid DESC LIMIT 1',
        col,table_name,fk) INTO prior USING anchor,at_time,occurred,received;
 END IF;

 values_:=jsonb_build_object(fk,anchor,'event_uuid',event,'sequence',coalesce(previous_seq,0)+1,
    'predecessor_id',previous_id,'predecessor_sequence',previous_seq,'action',action_name,'prior_state',prior,col,outcome,
    'actor_state',CASE WHEN rule_ref IS NULL THEN 'PARTY' ELSE 'NONE' END,
    'actor_id',CASE WHEN rule_ref IS NULL THEN actor END,
    'actor_access_epoch',CASE WHEN rule_ref IS NULL THEN access_epoch END,
    'actor_capacity',CASE WHEN rule_ref IS NULL THEN 'STANDING' ELSE 'NA_NO_PARTY' END,
    'enterer_identity_id',actor,'entry_mode',CASE WHEN rule_ref IS NULL THEN 'TRANSPORT' ELSE 'COMPOSED' END,
    'composing_rule_state',CASE WHEN rule_ref IS NULL THEN 'NA_TRANSPORT' ELSE 'REFERENCE' END,
    'composing_rule_reference',rule_ref,'composer_state',CASE WHEN rule_ref IS NULL THEN 'NA_TRANSPORT' ELSE 'REFERENCE' END,
    'composer_identity_id',CASE WHEN rule_ref IS NOT NULL THEN actor END,
    'effective_until_state',CASE WHEN deadline IS NULL THEN 'NONE' ELSE 'FIXED' END,'effective_until',deadline,
    'authority_basis_id',basis,'authority_decision_reference',decision,'evidence_reference',evidence,
    'request_reference',request_key,'idempotency_key',CASE WHEN is_command THEN command_key ELSE request_key END,
    'payload_fingerprint',payload,'lineage_reference','s015l1:'||lineage,
    'temporal_basis_kind',CASE WHEN at_time<received THEN 'RETROSPECTIVE' WHEN at_time>received THEN 'PROSPECTIVE' END,
    'temporal_basis_reference',CASE WHEN at_time<>received THEN 's015tb1:'||payload END,
    'effective_at',at_time,'occurred_at',occurred,'received_at',received,'predecessor_l1_commitment',previous_l1,
    'parts_commitment',public.s015_0022_digest('s015p1',public.s015_0022_parts_preimage(table_name,event)))||metadata;
 SELECT string_agg(quote_ident(k),',' ORDER BY k) INTO cols FROM jsonb_object_keys(values_) AS k;
 EXECUTE format('INSERT INTO public.%I (%s) SELECT %s FROM jsonb_populate_record(NULL::public.%I,$1) RETURNING id',
                table_name,cols,cols,table_name) INTO id_ USING values_;
 IF token='core_livingorganism' THEN
    PERFORM public.s015_refresh_governed_cache(token,anchor,greatest(
        (SELECT current_state_effective_at FROM public.core_livingorganism WHERE id=anchor),
        least(at_time,clock_timestamp())),clock_timestamp());
 END IF;
 RETURN id_;
END $$;
"""

SIGNATURE = ("text,bigint,text,text,timestamptz,timestamptz,timestamptz,bigint,bigint,"
             "text,text,text,text,jsonb,timestamptz")
WRAPPER = """
BEGIN
 RETURN public.s015_append_event(token,anchor,action_name,outcome,at_time,occurred,received,
     actor,basis,decision,evidence,request_key,payload,metadata,deadline);
END """
# Only these bodies call the 0055 writer directly. Keep every existing SQL
# signature, default, trigger binding and owner intact in both directions.
CALLERS = (
    ("c2_append_event", SIGNATURE, 0),
    ("c2_publish", "bigint,timestamptz,timestamptz,timestamptz,bigint,bigint,text,text,text,text", 2),
    ("c2_source_change", "", 1),
    ("c2_notice_input", "bigint,text", 1),
)


def bodies():
    source = import_module("core.migrations.0055_c2_coverage").SQL
    for name, arguments, calls in CALLERS:
        start = f"CREATE FUNCTION public.{name}("
        if source.count(start) != 1:
            raise RuntimeError(f"C1-16 could not identify the qualified definition of {name}")
        declaration, body = source.split(start, 1)[1].split(" AS $$", 1)
        original = body.split("$$;", 1)[0]
        old_call = "public.c2_append_event("
        if original.count(old_call) != calls:
            raise RuntimeError(f"C1-16 expected {calls} direct calls in {name}")
        replacement = original.replace(old_call, "public.s015_append_event(") if calls else WRAPPER
        yield (f"public.{name}({arguments})", start.replace("CREATE ", "CREATE OR REPLACE ") + declaration,
               original, replacement)


def switch(editor, forward):
    with editor.connection.cursor() as cursor:
        changes = list(bodies())
        for signature, _, old, new in changes:
            cursor.execute("SELECT prosrc FROM pg_catalog.pg_proc WHERE oid=%s::regprocedure", [signature])
            if cursor.fetchone() != (old if forward else new,):
                raise RuntimeError(f"C1-16 body mismatch for {signature}")
        if forward:
            editor.execute(SQL, params=None)
        else:
            cursor.execute("SELECT prosrc FROM pg_catalog.pg_proc WHERE oid=%s::regprocedure",
                           [f"public.s015_append_event({SIGNATURE},uuid,bigint,integer,bigint,text,text)"])
            if cursor.fetchone() != (SQL.split(" AS $$", 1)[1].split("$$;", 1)[0],):
                raise RuntimeError("C1-16 shared writer differs on reverse")
        for _, header, old, new in changes:
            editor.execute(header + " AS $$" + (new if forward else old) + "$$;", params=None)
        if not forward:
            editor.execute(f"DROP FUNCTION public.s015_append_event({SIGNATURE},uuid,bigint,integer,bigint,text,text)")


def install(apps, editor):
    switch(editor, True)


def reverse(apps, editor):
    switch(editor, False)


class Migration(migrations.Migration):
    dependencies = [("core", "0056_c3_authority_lifecycle")]
    operations = [migrations.RunPython(install, reverse)]
