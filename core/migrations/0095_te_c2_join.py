"""TIMED EVENTS: the C2 join. LO coverage recovery, one step per attempt.

The landed command (organism_recovery.advance) notices every due timed input and then
takes every due phase step, under one LO lock. This migration lets the engine take the
same steps one at a time, in the same order. It adds no table. It adds one trigger, the
origin recorder on the LO's own events (0091), and:

- the two selectors. The notice selector offers one input only, the oldest that the
  landed c2_notice_input would act on, and repeats that function's conditions: the LO is
  not the root; the input is due; it is not before the founding; no assessment cites it
  at its due time; a roster exists. The phase selector offers the head only when it is
  WITHOUT_COORDINATOR or K2_DEACTIVATE, its deadline has passed, and no notice is offered.
  So a notice always goes before a phase step, and the engine's cursor has one row at
  most to order;
- the proof of both purposes, which supplies the effect's own enterer: the founding
  enterer for a notice and the head's enterer for a phase step, as the landed writers
  record them. The runner takes an opened C2 receipt's enterer from this proof;
- the claim, which tries without waiting the coverage case the notice locks and the
  identity and authority basis the new records refer to;
- the two purposes' rows in the registry (te_consumers, replaced under G2-4);
- one changed line in one landed function (c2_guard_recovery, replaced under G2-4). The
  landed guard accepts a WITHOUT_COORDINATOR event only if its coverage assessment was
  made "in this transaction", and tests that by comparing the row's xmin with the
  top-level transaction id. The engine writes every effect and its receipt inside one
  savepoint, where a new row's xmin is the savepoint's own id, so the landed test refuses
  every notice the engine makes. The test becomes te_made_here (migration 0091): the
  assessment's origin record holds this transaction's own full top-level id. That is so
  at the top level and in a savepoint of it that has not been rolled back, and for no
  other transaction, committed or not, old or new. The other five same-transaction tests
  in the guard are for acts the engine never makes; they are not changed.

The rules are the landed writers' own: C0:G2:C2 is what c2_append_event records on a
notice's assessment; HG:RC-1:K-2/H-4 is what organism_recovery.transition records on a
phase step.
"""

from importlib import import_module

from django.db import migrations

storage = import_module("core.migrations.0091_te_engine_storage")
elect = import_module("core.migrations.0092_te_elect_joins")
cq = import_module("core.migrations.0093_te_cq_joins")
committee = import_module("core.migrations.0094_te_committee")
landed = import_module("core.migrations.0055_c2_coverage")

GUARD_SIGNATURE = "public.c2_guard_recovery()"
_GUARD_OPEN = "CREATE FUNCTION public.c2_guard_recovery() RETURNS trigger LANGUAGE plpgsql AS $$"
_TOP_LEVEL_ONLY = "WHERE c.living_organism_id=lo.id AND x.xmin=pg_current_xact_id()::xid AND public.c2_result("
_THIS_TRANSACTION = ("WHERE c.living_organism_id=lo.id AND public.te_made_here('core_coverageassessment',x.event_uuid::text)"
                     " AND public.c2_result(")


def landed_guard():
    """The body of c2_guard_recovery as its only definer (0055) installs it."""
    if landed.SQL.count(_GUARD_OPEN) != 1:
        raise RuntimeError("0095 cannot find the landed c2_guard_recovery")
    return landed.SQL.split(_GUARD_OPEN, 1)[1].split("$$;", 1)[0]


OLD_GUARD = landed_guard()
if OLD_GUARD.count(_TOP_LEVEL_ONLY) != 1:
    raise RuntimeError("0095 predecessor text no longer matches: c2_guard_recovery")
NEW_GUARD = OLD_GUARD.replace(_TOP_LEVEL_ONLY, _THIS_TRANSACTION, 1)


def guard_sql(body):
    return "CREATE OR REPLACE FUNCTION public.c2_guard_recovery() RETURNS trigger LANGUAGE plpgsql AS $$" + body + "$$;"


# consumer, purpose, ordinal, rule, selector, validator, claim. Witness: C2-06.
C2_ROWS = (
    ("C2", "NOTICE", 30, "C0:G2:C2", "te_c2_notice_due", "te_c2_proof", "te_c2_claim"),
    ("C2", "PHASE", 31, "HG:RC-1:K-2/H-4", "te_c2_phase_due", "te_c2_proof", "te_c2_claim"),
)
OLD_REGISTRY = committee.NEW_REGISTRY
NEW_REGISTRY = storage.registry_sql((*elect.ROWS, *cq.CQ_ROWS, *committee.CMT_ROWS, *C2_ROWS))

SQL = r"""
-- A notice's occurrence: its due time, written one way.
CREATE FUNCTION public.te_c2_occurrence(p_due timestamptz) RETURNS text LANGUAGE sql STABLE AS $$
 SELECT to_char(p_due AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
$$;

-- The one notice the landed command would make next: its order (due time, then source),
-- and c2_notice_input's own conditions, so an input that function refuses for good is
-- never offered and cannot hold a phase step back.
CREATE FUNCTION public.te_c2_notice_due(p_lo bigint,p_known timestamptz)
RETURNS TABLE(source_ref text,occurrence text,due_at timestamptz) LANGUAGE sql STABLE AS $$
 SELECT d.source,public.te_c2_occurrence(d.due),d.due FROM (
   SELECT DISTINCT i.due,i.source FROM public.c2_timed_input i
   JOIN public.core_livingorganism lo ON lo.id=i.lo_id AND NOT lo.platform_root
   JOIN public.core_livingorganismevent f ON f.living_organism_id=i.lo_id AND f.sequence=1
   WHERE i.lo_id=p_lo AND i.due<=p_known AND i.due>=f.effective_at
   AND NOT EXISTS(SELECT FROM public.core_coverageassessment a
       JOIN public.core_essentialcoveragecase c ON c.id=a.case_id
       WHERE c.living_organism_id=i.lo_id AND a.evidence_reference='c2-boundary:'||i.source
       AND a.effective_at=i.due)
   AND public.c2_roster_version(i.lo_id,i.due,p_known) IS NOT NULL
   ORDER BY i.due,i.source LIMIT 1) d
$$;

-- The phase step, as advance reads it (c2_phase_at at one cut), and only when no notice
-- is offered: advance makes every notice before any phase step.
CREATE FUNCTION public.te_c2_phase_due(p_lo bigint,p_known timestamptz)
RETURNS TABLE(source_ref text,occurrence text,due_at timestamptz) LANGUAGE sql STABLE AS $$
 SELECT h.event_uuid::text,'PHASE'::text,h.effective_until
 FROM public.core_livingorganism lo CROSS JOIN LATERAL public.c2_phase_at(lo.id,p_known,p_known) h
 WHERE lo.id=p_lo AND NOT lo.platform_root AND h.action IN ('WITHOUT_COORDINATOR','K2_DEACTIVATE')
 AND h.effective_until IS NOT NULL AND h.effective_until<=p_known
 AND NOT EXISTS(SELECT FROM public.te_c2_notice_due(p_lo,p_known))
$$;

-- The proof. A notice's effect is the assessment that cites the boundary at its due time;
-- a phase step's is the LO event that cites the head it stepped from. Each gives the
-- effect's own enterer, which is never the person whose request carried the attempt.
CREATE FUNCTION public.te_c2_proof(p_lo bigint,p_purpose text,p_source text,p_occurrence text,p_effect text)
RETURNS TABLE(due_at timestamptz,effect_at timestamptz,enterer_id bigint,made_here boolean)
LANGUAGE sql STABLE AS $$
 SELECT a.effective_at,a.recorded_at,a.enterer_identity_id,public.te_made_here('core_coverageassessment',a.event_uuid::text)
 FROM public.core_coverageassessment a JOIN public.core_essentialcoveragecase c ON c.id=a.case_id
 WHERE p_purpose='NOTICE' AND c.living_organism_id=p_lo AND a.evidence_reference='c2-boundary:'||p_source
 AND public.te_c2_occurrence(a.effective_at)=p_occurrence AND a.event_uuid::text=p_effect
 AND EXISTS(SELECT FROM public.c2_timed_input i WHERE i.lo_id=p_lo AND i.source=p_source AND i.due=a.effective_at)
 UNION ALL
 SELECT e.effective_at,e.recorded_at,e.enterer_identity_id,public.te_made_here('core_livingorganismevent',e.event_uuid::text)
 FROM public.core_livingorganismevent e JOIN public.core_livingorganismevent h
   ON h.living_organism_id=e.living_organism_id AND e.evidence_reference='k2:'||h.event_uuid
 WHERE p_purpose='PHASE' AND p_occurrence='PHASE' AND e.living_organism_id=p_lo
 AND h.event_uuid::text=p_source AND h.action IN ('WITHOUT_COORDINATOR','K2_DEACTIVATE')
 AND e.action IN ('K2_DEACTIVATE','K2_DISSOLVE') AND e.event_uuid::text=p_effect
$$;

-- The record whose enterer and authority the new records will carry: the founding event
-- for a notice, the head for a phase step.
CREATE FUNCTION public.te_c2_origin(p_lo bigint,p_purpose text,p_source text)
RETURNS TABLE(enterer bigint,basis bigint) LANGUAGE sql STABLE AS $$
 SELECT o.enterer_identity_id,o.authority_basis_id FROM public.core_livingorganismevent o
 WHERE o.living_organism_id=p_lo
 AND ((p_purpose='NOTICE' AND o.sequence=1) OR (p_purpose='PHASE' AND o.event_uuid::text=p_source))
$$;

-- Tried without waiting, under the LO: the coverage claim of 0092 (the LO's essential-coverage
-- case, which c2_publish locks, and its restricted-continuity case if one exists, to which
-- c2_publish appends), and the identity and authority basis the new records refer to (their
-- foreign keys take a key-share lock, which waits for a writer that holds the row). The
-- key-share try on another person's Identity under the LO is the scoped exception the Human
-- Governor ruled on 12 Nov 2026 to the lock-order ruling of 15 Oct; it never waits.
-- A notice and a phase step also append to the LO's own chain, so the rows the LO row itself
-- refers to (its founder and its founding basis: te_lo_references, 0092) are tried as well.
-- For a notice they are the origin's own; for a phase step they need not be.
CREATE FUNCTION public.te_c2_claim(p_lo bigint,p_purpose text,p_source text,p_occurrence text,p_phase text)
RETURNS boolean LANGUAGE sql VOLATILE AS $$
 SELECT p_phase<>'UNDER_LO' OR (public.te_coverage_claim(p_lo)
  AND (SELECT count(*) FROM (
     SELECT FROM public.core_identity i WHERE i.id IN (SELECT o.enterer FROM public.te_c2_origin(p_lo,p_purpose,p_source) o)
     FOR KEY SHARE OF i SKIP LOCKED) held)=(SELECT count(DISTINCT o.enterer) FROM public.te_c2_origin(p_lo,p_purpose,p_source) o)
  AND (SELECT count(*) FROM (
     SELECT FROM public.core_authoritybasis b WHERE b.id IN (SELECT o.basis FROM public.te_c2_origin(p_lo,p_purpose,p_source) o)
     FOR KEY SHARE OF b SKIP LOCKED) held)=(SELECT count(DISTINCT o.basis) FROM public.te_c2_origin(p_lo,p_purpose,p_source) o)
  AND public.te_reference_claim(ARRAY(SELECT r.identity FROM public.te_lo_references(p_lo) r),
        ARRAY(SELECT r.basis FROM public.te_lo_references(p_lo) r)))
$$;

-- Origin evidence for a phase step (0091). Only the two K-2 steps are recorded from the LO's
-- own chain. A notice's assessment is recorded by the trigger 0092 puts on that table.
-- Of the LO's own events, the two phase steps are recorded (te_origin_applies, 0091).
CREATE TRIGGER zz_te_origin BEFORE INSERT ON public.core_livingorganismevent
 FOR EACH ROW EXECUTE FUNCTION public.te_record_origin();
"""

REMOVE = """
DROP TRIGGER zz_te_origin ON public.core_livingorganismevent;
DROP FUNCTION public.te_c2_claim(bigint,text,text,text,text);
DROP FUNCTION public.te_c2_origin(bigint,text,text);
DROP FUNCTION public.te_c2_proof(bigint,text,text,text,text);
DROP FUNCTION public.te_c2_phase_due(bigint,timestamptz);
DROP FUNCTION public.te_c2_notice_due(bigint,timestamptz);
DROP FUNCTION public.te_c2_occurrence(timestamptz);
"""


def install(apps, schema_editor):
    """Create the C2 functions; widen the guard's one test; admit the two purposes in the registry."""
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        elect.expect(cursor, OLD_REGISTRY)
        storage.expect(cursor, GUARD_SIGNATURE, OLD_GUARD)
        cursor.execute(guard_sql(NEW_GUARD))
        cursor.execute(SQL)
        cursor.execute(elect.replace(NEW_REGISTRY))


def uninstall(apps, schema_editor):
    """Refuse while C2 receipts exist; put the registry and the landed guard back; drop the C2 functions."""
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        elect.expect(cursor, NEW_REGISTRY)
        storage.expect(cursor, GUARD_SIGNATURE, NEW_GUARD)
        # Fence receipt writes before looking, to the end of the migration's transaction.
        cursor.execute("LOCK TABLE public.core_tereceipt IN SHARE ROW EXCLUSIVE MODE")
        cursor.execute("SELECT EXISTS (SELECT 1 FROM public.core_tereceipt WHERE consumer = 'C2')")
        if cursor.fetchone()[0]:
            raise RuntimeError("0095 reverse refused while C2 receipts exist")
        # Pace rows are counts, not evidence. They cannot outlive their consumer.
        cursor.execute("DELETE FROM public.core_tepace WHERE consumer = 'C2'")
        cursor.execute(elect.replace(OLD_REGISTRY))
        cursor.execute(REMOVE)
        cursor.execute(guard_sql(OLD_GUARD))


class Migration(migrations.Migration):
    dependencies = (("core", "0094_te_committee"),)
    operations = (migrations.RunPython(install, uninstall),)
