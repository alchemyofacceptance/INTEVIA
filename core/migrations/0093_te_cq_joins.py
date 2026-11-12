"""TIMED EVENTS: the CQ joins. Two deadlines are registered; the settlement is not.

The engine changes when the landed CQ rule acts are made, never what they decide. A
CQ thread's head says which act is due: the preparation deadline, the Clarity
response deadline, or the ballot's deadline. The engine calls the same landed act,
with the same request key, that the landed catch-up calls (services/cq.py, catch_up).

Registered here: PREPARATION_DUE and RESPONSE_DUE. Each writer, and the acts that
follow from it in the same transaction (the rule's opening of the ballot, or its
record that no ballot opens), lock only the LO's own row, which the engine already
holds, after the request key the engine has already tried. That was traced at source
through to commit.

Written and installed but NOT registered, so the engine cannot select it: SETTLE. A
settlement that applies makes its implementation in the same transaction, and that
implementation reaches the election, stand-down, guiding-intention, law and library
writers. Among their locks are a table lock and other LOs' rows, which no row try
can take in advance. The settlement stays on its landed route (cq.catch_up).
"""

from importlib import import_module

from django.db import migrations

storage = import_module("core.migrations.0091_te_engine_storage")
elect = import_module("core.migrations.0092_te_elect_joins")

REGISTRY_SIGNATURE = elect.REGISTRY_SIGNATURE
# The rule of the purpose is the rule reference the landed act itself records as its
# authority (cq_source, operation PREPARATION_DUE: kind RULE, reference HG:DISC2:G1:D4).
CQ_ROWS = (
    ("CQ", "PREPARATION_DUE", 10, "HG:DISC2:G1:D4", "te_cq_preparation_due", "te_cq_proof", "te_cq_claim"),
    # RESPONSE_DUE: the same landed function gives its authority (kind RULE, reference HG:DISC2:D7). Witness: TE-03.
    ("CQ", "RESPONSE_DUE", 11, "HG:DISC2:D7", "te_cq_response_due", "te_cq_proof", "te_cq_claim"),
)
OLD_REGISTRY = elect.NEW_REGISTRY
NEW_REGISTRY = storage.registry_sql((*elect.ROWS, *CQ_ROWS))

SQL = r"""
-- What is due for one phase: every thread of the LO whose head is in that phase and
-- whose deadline has passed. The occurrence is the head act itself.
CREATE FUNCTION public.te_cq_due(p_lo bigint,p_known timestamptz,p_head text)
RETURNS TABLE(source_ref text,occurrence text,due_at timestamptz) LANGUAGE sql STABLE AS $$
 SELECT h.thread_uuid::text,h.act_uuid::text,d.at
 FROM public.cq_phases h
 CROSS JOIN LATERAL (SELECT CASE WHEN p_head='CLARITY' THEN h.response_deadline ELSE h.deadline END AS at) d
 WHERE h.organism_id=p_lo AND h.phase=p_head AND d.at<=p_known
$$;

CREATE FUNCTION public.te_cq_preparation_due(p_lo bigint,p_known timestamptz)
RETURNS TABLE(source_ref text,occurrence text,due_at timestamptz) LANGUAGE sql STABLE AS $$
 SELECT d.source_ref,d.occurrence,d.due_at FROM public.te_cq_due(p_lo,p_known,'PREPARATION') d
$$;

CREATE FUNCTION public.te_cq_response_due(p_lo bigint,p_known timestamptz)
RETURNS TABLE(source_ref text,occurrence text,due_at timestamptz) LANGUAGE sql STABLE AS $$
 SELECT d.source_ref,d.occurrence,d.due_at FROM public.te_cq_due(p_lo,p_known,'CLARITY') d
$$;

-- The engine settles only for the clock: the ballot's own deadline has passed.
CREATE FUNCTION public.te_cq_settle_due(p_lo bigint,p_known timestamptz)
RETURNS TABLE(source_ref text,occurrence text,due_at timestamptz) LANGUAGE sql STABLE AS $$
 SELECT d.source_ref,d.occurrence,d.due_at FROM public.te_cq_due(p_lo,p_known,'VOTING') d
$$;

-- The effect is the rule act made for that head, under the registered rule of the purpose.
-- For the preparation deadline: the cited head set the PREPARATION phase, its deadline had
-- come when the act was recorded, and no other phase-setting act lies between the two. Acts
-- that set no phase may lie between them: a nomination or a consent made during an
-- election's preparation is lawful, and the head is still the head. A later phase-setting
-- act makes the cited head a superseded one, and it proves nothing. For the other two
-- purposes the act names the head as its window or its ballot.
CREATE FUNCTION public.te_cq_proof(p_lo bigint,p_purpose text,p_source text,p_occurrence text,p_effect text)
RETURNS TABLE(due_at timestamptz,effect_at timestamptz,enterer_id bigint,made_here boolean)
LANGUAGE sql STABLE AS $$
 SELECT CASE WHEN p_purpose='RESPONSE_DUE' THEN (h.facts->>'response_deadline')::timestamptz
             ELSE (h.facts->>'deadline')::timestamptz END,
        a.recorded_at,a.actor_id,public.te_made_here('core_cqact',a.act_uuid::text)
 FROM public.core_cqact h JOIN public.core_cqact a ON a.thread_uuid=h.thread_uuid AND a.organism_id=h.organism_id
 WHERE h.organism_id=p_lo AND h.thread_uuid::text=p_source AND h.act_uuid::text=p_occurrence
 AND a.act_uuid::text=p_effect AND a.operation=p_purpose AND a.ordinal>h.ordinal
 AND a.authority_source->>'kind'='RULE'
 AND a.authority_source->>'reference'=(SELECT r.rule FROM public.te_consumers() r
        WHERE r.consumer='CQ' AND r.purpose=p_purpose)
 AND ((p_purpose='PREPARATION_DUE' AND h.facts->>'phase'='PREPARATION'
       AND (h.facts->>'deadline')::timestamptz<=a.recorded_at
       AND NOT EXISTS(SELECT FROM public.core_cqact b
        WHERE b.thread_uuid=h.thread_uuid AND b.organism_id=h.organism_id AND b.facts ? 'phase'
        AND b.ordinal>h.ordinal AND b.ordinal<a.ordinal))
   OR (p_purpose='RESPONSE_DUE' AND a.request->>'window'=p_occurrence)
   OR (p_purpose='SETTLE' AND a.request->>'ballot'=p_occurrence))
$$;

-- The request key is tried before the LO by the consumer's own code, which derives
-- it as the landed catch-up does. Under the LO a rule act reaches these rows, through commit:
--  the LO's founding basis: the act's event on the LO's chain cites it (cq_append, 0074), and
--    the LO row refers to it. Its foreign key takes a key-share lock at commit, which waits
--    for a holder of the row FOR UPDATE (0056 holds one so). Tried here, without waiting;
--  the LO's founder: the act updates the LO row more than once, so the row's own foreign keys
--    are checked again at commit (te_lo_references, 0092). Tried here, without waiting;
--  the carrier, who is the act's actor and the event's enterer: held by the runner before the LO;
--  the thread and the board, by the act's own foreign keys: not tried. Their rows are never
--    updated or deleted (disc_immutable, 0072; discp_immutable, 0073) and no statement on main
--    locks one, so a key-share lock on them cannot wait;
--  the LO chain's previous event: reached only under the LO, which is held.
-- Neither registered act gives the committee refresh anything to do: cmt_changed reads a
-- committee plan from an act's facts, and a plan is written only when a decision is applied
-- (0076), which is the settlement, not registered here. So the refresh's claim is not made. A
-- purpose that applies a decision would have to make it.
CREATE FUNCTION public.te_cq_claim(p_lo bigint,p_purpose text,p_source text,p_occurrence text,p_phase text)
RETURNS boolean LANGUAGE sql VOLATILE AS $$
 SELECT p_phase<>'UNDER_LO' OR public.te_reference_claim(
   ARRAY(SELECT r.identity FROM public.te_lo_references(p_lo) r),
   ARRAY(SELECT r.basis FROM public.te_lo_references(p_lo) r))
$$;

-- Origin evidence for a rule act (0091). Only acts made under a rule are recorded.
-- Of the CQ acts, the rule acts are recorded (te_origin_applies, 0091).
CREATE TRIGGER zz_te_origin BEFORE INSERT ON public.core_cqact
 FOR EACH ROW EXECUTE FUNCTION public.te_record_origin();
"""

REMOVE = """
DROP TRIGGER zz_te_origin ON public.core_cqact;
DROP FUNCTION public.te_cq_claim(bigint,text,text,text,text);
DROP FUNCTION public.te_cq_proof(bigint,text,text,text,text);
DROP FUNCTION public.te_cq_settle_due(bigint,timestamptz);
DROP FUNCTION public.te_cq_response_due(bigint,timestamptz);
DROP FUNCTION public.te_cq_preparation_due(bigint,timestamptz);
DROP FUNCTION public.te_cq_due(bigint,timestamptz,text);
"""


def expect(cursor, sql):
    """G2-4: refuse unless the installed registry is exactly the expected one."""
    if storage.installed(cursor, REGISTRY_SIGNATURE) != (elect.body(sql), False, "i", "sql"):
        raise RuntimeError("Unexpected installed body: " + REGISTRY_SIGNATURE)


def install(apps, schema_editor):
    """Create the CQ functions, then admit the two witnessed purposes in the registry."""
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        expect(cursor, OLD_REGISTRY)
        cursor.execute(SQL)
        cursor.execute(elect.replace(NEW_REGISTRY))


def uninstall(apps, schema_editor):
    """Refuse while CQ receipts exist; put the ELECT registry back; drop the CQ functions."""
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        expect(cursor, NEW_REGISTRY)
        # Fence receipt writes before looking, to the end of the migration's transaction.
        cursor.execute("LOCK TABLE public.core_tereceipt IN SHARE ROW EXCLUSIVE MODE")
        cursor.execute("SELECT EXISTS (SELECT 1 FROM public.core_tereceipt WHERE consumer = 'CQ')")
        if cursor.fetchone()[0]:
            raise RuntimeError("0093 reverse refused while CQ receipts exist")
        # Pace rows are counts, not evidence. They cannot outlive their consumer.
        cursor.execute("DELETE FROM public.core_tepace WHERE consumer = 'CQ'")
        cursor.execute(elect.replace(OLD_REGISTRY))
        cursor.execute(REMOVE)


class Migration(migrations.Migration):
    dependencies = (("core", "0092_te_elect_joins"),)
    operations = (migrations.RunPython(install, uninstall),)
