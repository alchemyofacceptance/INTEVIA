"""Open the first ballot by rule on the final current-wording confirmation (BR2)."""

from hashlib import sha256
from importlib import import_module

from django.db import migrations

from core.migrations._clock_conversion import swap


# Exact pg_proc.prosrc bytes of the reviewed predecessor and successor.
# The last member confirms as a member. A second, rule-owned act opens the ballot.
PHASE = "public.cq_phase_facts(bigint,uuid,uuid,text,jsonb,bigint,timestamptz)"
SOURCE = "public.cq_open_source(bigint,uuid,uuid,bigint,bigint,jsonb)"
FOLLOWUP = "public.cq_vote_followup()"
COMPLETE = "public.cq_complete()"

EDITS = {
    PHASE: (
        (
            "   RETURN v_facts||jsonb_build_object('member',v_self);",
            """   v_facts:=v_facts||jsonb_build_object('member',v_self);
   IF p_op='QUESTION' THEN RETURN v_facts; END IF;""",
        ),
        (
            " IF p_op NOT IN ('CLOSE','PROCEED','RESPONSE_DUE') THEN",
            " IF p_op NOT IN ('CLOSE','PROCEED','RESPONSE_DUE','CONFIRM') THEN",
        ),
        (
            "    CASE WHEN r.operation='CONFIRM' AND r.facts->>'version'=v_first.version_uuid::text THEN 'CONFIRMED'",
            """    CASE WHEN p_op='CONFIRM' AND m.value=v_self THEN 'CONFIRMED'
         WHEN r.operation='CONFIRM' AND r.facts->>'version'=v_first.version_uuid::text THEN 'CONFIRMED'""",
        ),
        (
            " v_count:=jsonb_array_length(v_confirmed); v_total:=jsonb_array_length(v_people);",
            """ v_count:=jsonb_array_length(v_confirmed); v_total:=jsonb_array_length(v_people);
 IF p_op='CONFIRM' THEN
   IF v_total<3 OR v_count<>v_total THEN RETURN v_facts; END IF;
   v_settings:=public.cq_scoped_settings(v_start.facts->'baseline',
     (SELECT lo.organism_id FROM public.core_livingorganism lo WHERE lo.id=p_lo),
     p_board,p_thread,'CLARITY',p_cut);
 END IF;""",
        ),
        (
            """ IF p_op='RESPONSE_DUE' THEN
   v_facts:=v_facts||jsonb_build_object('rule_open',true""",
            """ IF p_op='CONFIRM' THEN
   v_facts:=v_facts||jsonb_build_object('rule_open',true,'window',v_head.act_uuid);
 ELSIF p_op='RESPONSE_DUE' THEN
   v_facts:=v_facts||jsonb_build_object('rule_open',true""",
        ),
    ),
    SOURCE: (
        (
            "operation IN ('PREPARED','PREPARATION_DUE','RESPONSE_DUE')",
            "operation IN ('PREPARED','PREPARATION_DUE','RESPONSE_DUE','CONFIRM')",
        ),
        (
            " IF origin.act_uuid IS NULL THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;",
            """ IF origin.act_uuid IS NULL THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 IF origin.operation='CONFIRM' THEN
   RETURN jsonb_build_object('kind','RULE','reference','HG:BR2','route','CLARITY_FULL_PARTICIPATION','cause',origin.act_uuid);
 END IF;""",
        ),
    ),
    FOLLOWUP: (
        (
            " IF NEW.operation IN ('PREPARED','PREPARATION_DUE','RESPONSE_DUE') THEN",
            " IF NEW.operation IN ('PREPARED','PREPARATION_DUE','RESPONSE_DUE','CONFIRM') THEN",
        ),
    ),
    COMPLETE: (
        (
            " IF v_act.operation='CLOSE' AND v_act.request ? 'activation' THEN",
            """ IF v_act.operation='CONFIRM' AND v_act.facts->>'rule_open'='true' AND NOT EXISTS (
   SELECT FROM public.core_cqact child WHERE child.thread_uuid=v_act.thread_uuid
     AND child.board_uuid=v_act.board_uuid AND child.organism_id=v_act.organism_id
     AND child.execution_xid=v_act.execution_xid AND child.ordinal>v_act.ordinal
     AND child.operation IN ('OPEN_VOTE','NO_BALLOT')
     AND child.request->>'trigger_act'=v_act.act_uuid::text
     AND child.authority_source=jsonb_build_object('kind','RULE','reference','HG:BR2',
       'route','CLARITY_FULL_PARTICIPATION','cause',v_act.act_uuid)
 ) THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 IF v_act.operation='CLOSE' AND v_act.request ? 'activation' THEN""",
        ),
    ),
}

PINS = {
    PHASE: (
        (10269, "bcaa90b76ebc0b5e50da385331de48e40a0342130b45897da73c2bafc59e054d"),
        (10796, "4f2afac2d23f6f5ffc82fb1292325c392d384e60734c5f5ece0cc499524bdddc"),
    ),
    SOURCE: (
        (594, "a3ad0352ca13431fadc104f6b17f817b271b8bf4082558615859b7f7eca7a235"),
        (776, "0bba7e8d526aa151f3fe2a7c5fc0fc4bd4731b2b9a2a75fe6f43a337d6aa2e9a"),
    ),
    FOLLOWUP: (
        (1208, "1162aa9b2763a2281d58ae7fc3556fad6ff8918352da86f53388441a72d7a801"),
        (1218, "48bf4c6444e05daaff92a1a370dc88f4a6193d58ccdde5522ec2103d1d69d43b"),
    ),
    COMPLETE: (
        (5851, "409c1d8f4160b959095488647fdb0408305aa75f545d691e8f2a5a45857fe529"),
        (6506, "2ef23724983d44ef5af47f8897b4051ee0d3f76c0c6b8ee82da48edba8f2bfe6"),
    ),
}

CREATE_TRIGGER = """CREATE TRIGGER cq_br2_confirm
AFTER INSERT ON public.core_cqact
FOR EACH ROW
WHEN (NEW.operation = 'CONFIRM' AND NEW.facts->>'rule_open' = 'true')
EXECUTE FUNCTION public.cq_vote_followup()"""
DROP_TRIGGER = "DROP TRIGGER cq_br2_confirm ON public.core_cqact"


def qualified_bodies():
    """Reconstruct the four installed definitions from named historical migrations."""
    ballots = import_module("core.migrations.0075_cq_decisions")
    authority = import_module("core.migrations.0076_cq_authority")
    hard = import_module("core.migrations.0081_hard_voting_authority")
    followup = import_module("core.migrations.0100_clock_storage_0073_0080")
    completion = import_module("core.migrations.0101_clock_storage_0081_0087")
    phase, = (ddl for signature, _, ddl in ballots.definitions() if signature == PHASE)
    predecessors = (
        (PHASE, hard.body(phase)),
        (SOURCE, hard.body(authority.function(ballots.FORWARD, "cq_open_source"))),
        (FOLLOWUP, followup.CQ_VOTE_FOLLOWUP_CLOCK),
        (COMPLETE, completion.CQ_COMPLETE_CLOCK),
    )
    bodies = []
    for signature, before in predecessors:
        after = before
        for old, new in EDITS[signature]:
            if after.count(old) != 1:
                raise RuntimeError(f"0116 expected one occurrence of its reviewed SQL in {signature}.")
            after = after.replace(old, new, 1)
        for body, pin in zip((before, after), PINS[signature], strict=True):
            raw = body.encode("utf-8")
            if (len(raw), sha256(raw).hexdigest()) != pin:
                raise RuntimeError(f"0116 found a different migration body for {signature}.")
        bodies.append((signature, before, after))
    return bodies


def install(apps, schema_editor):
    bodies = qualified_bodies()
    with schema_editor.connection.cursor() as cursor:
        for signature, before, after in bodies:
            swap(cursor, signature, before, after)
        cursor.execute(CREATE_TRIGGER)


def uninstall(apps, schema_editor):
    bodies = qualified_bodies()
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(DROP_TRIGGER)
        for signature, before, after in reversed(bodies):
            swap(cursor, signature, after, before)


class Migration(migrations.Migration):
    atomic = True
    dependencies = (("core", "0115_suggestion_preparer"),)
    operations = (migrations.RunPython(install, uninstall),)
