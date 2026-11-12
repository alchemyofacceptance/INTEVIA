"""TIMED EVENTS: the ELECT joins. Term end, resignation coverage and vacancy.

The engine changes when the landed ELECT writers run, never what they decide. Each
purpose registers three functions:

- a selector, which lists what the landed predicate says is due now;
- a proof (the validator), which confirms that a cited effect is the effect of that
  source and occurrence in that LO, and returns the due time, the effect's own
  recorded time, the effect's own enterer, and whether this transaction made it;
- a claim, which tries, without waiting, the rows the landed writer will lock, and the
  rows its effect makes the committee refresh lock at commit.

The selectors repeat the landed catch-up's own conditions (services/elections.py,
catch_up_terms) and the writers' own conditions (elect_record_term_end,
elect_resignation_coverage, elect_start_vacancy). A false return from a writer is
never read as done: only the effect itself proves completion.
"""

from importlib import import_module

from django.db import migrations

storage = import_module("core.migrations.0091_te_engine_storage")

REGISTRY_SIGNATURE = "public.te_consumers()"
# consumer, purpose, ordinal, rule, selector, validator, claim
# Each purpose is registered together with the witness that proves it:
# TERM_END by TE-02 and ELECT-09; RESIGNATION by ELECT-13; VACANCY by ELECT-13 and ELECT-11.
# The rule labels. 'ELECT:TERM_END' is the landed writer's own: the rule text
# elect_record_term_end writes on its END event. The other two are this build's labels,
# since their writers record no rule text: 'ELECT:RESIGNATION_COVERAGE' is named for
# elect_resignation_coverage, the landed function that records the assessment, and
# 'ELECT:VACANCY' for ELECT_VACANCY, the operation of the landed act a vacancy records as
# its route.
ROWS = (
    ("ELECT", "TERM_END", 1, "ELECT:TERM_END", "te_elect_term_due", "te_elect_term_proof", "te_elect_claim"),
    ("ELECT", "RESIGNATION", 2, "ELECT:RESIGNATION_COVERAGE", "te_elect_resignation_due",
     "te_elect_resignation_proof", "te_elect_claim"),
    ("ELECT", "VACANCY", 3, "ELECT:VACANCY", "te_elect_vacancy_due", "te_elect_vacancy_proof", "te_elect_claim"),
)
OLD_REGISTRY = storage.REGISTRY
NEW_REGISTRY = storage.registry_sql(ROWS)

SQL = r"""
-- Term end. Due exactly when elect_due_office says so. The occurrence is the due
-- ASSIGN event; the effect is the END event that cites it.
CREATE FUNCTION public.te_elect_term_due(p_lo bigint,p_known timestamptz)
RETURNS TABLE(source_ref text,occurrence text,due_at timestamptz) LANGUAGE sql STABLE AS $$
 SELECT a.assignment_uuid::text,e.event_uuid::text,q.due
 FROM public.core_contextualroleassignment a
 JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
 CROSS JOIN LATERAL public.elect_due_office(a.id,p_known) q
 JOIN public.core_contextualroleassignmentstateevent e ON e.id=q.source_id
 WHERE d.living_organism_id=p_lo
$$;

CREATE FUNCTION public.te_elect_term_proof(p_lo bigint,p_purpose text,p_source text,p_occurrence text,p_effect text)
RETURNS TABLE(due_at timestamptz,effect_at timestamptz,enterer_id bigint,made_here boolean)
LANGUAGE sql STABLE AS $$
 SELECT h.effective_at,h.recorded_at,h.enterer_identity_id,public.te_made_here('core_contextualroleassignmentstateevent',h.event_uuid::text)
 FROM public.core_contextualroleassignment a
 JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
 JOIN public.core_contextualroleassignmentstateevent e ON e.assignment_id=a.id AND e.action='ASSIGN'
 JOIN public.core_contextualroleassignmentstateevent h ON h.assignment_id=a.id AND h.action='END'
 WHERE p_purpose='TERM_END' AND d.living_organism_id=p_lo AND a.assignment_uuid::text=p_source
 AND e.event_uuid::text=p_occurrence AND h.event_uuid::text=p_effect
 AND h.evidence_reference='elect-expiry:'||e.event_uuid AND h.composing_rule_reference='ELECT:TERM_END'
$$;

-- Resignation. Due when the notice's stated end has passed and its seat ending is
-- effective, and no assessment cites the notice yet.
CREATE FUNCTION public.te_elect_resignation_due(p_lo bigint,p_known timestamptz)
RETURNS TABLE(source_ref text,occurrence text,due_at timestamptz) LANGUAGE sql STABLE AS $$
 SELECT n.act_uuid::text,'COVERAGE'::text,(n.facts->>'end')::timestamptz
 FROM public.core_cqact n JOIN public.core_livingorganism lo ON lo.id=n.organism_id
 WHERE n.organism_id=p_lo AND NOT lo.platform_root AND n.operation='BOARD_RESIGN'
 AND (n.facts->>'end')::timestamptz<=p_known
 AND EXISTS(SELECT FROM public.core_circleparticipationend x WHERE x.composed_from_event_uuid=n.act_uuid
   AND x.composing_rule_reference='elect-resign:'||n.act_uuid AND x.effective_at<=p_known)
 AND NOT EXISTS(SELECT FROM public.core_coverageassessment x JOIN public.core_essentialcoveragecase c ON c.id=x.case_id
   WHERE c.living_organism_id=n.organism_id AND x.evidence_reference='elect-resignation:'||n.act_uuid)
$$;

CREATE FUNCTION public.te_elect_resignation_proof(p_lo bigint,p_purpose text,p_source text,p_occurrence text,p_effect text)
RETURNS TABLE(due_at timestamptz,effect_at timestamptz,enterer_id bigint,made_here boolean)
LANGUAGE sql STABLE AS $$
 SELECT (n.facts->>'end')::timestamptz,x.recorded_at,x.enterer_identity_id,public.te_made_here('core_coverageassessment',x.event_uuid::text)
 FROM public.core_cqact n
 JOIN public.core_coverageassessment x ON x.evidence_reference='elect-resignation:'||n.act_uuid
 JOIN public.core_essentialcoveragecase c ON c.id=x.case_id AND c.living_organism_id=n.organism_id
 WHERE p_purpose='RESIGNATION' AND p_occurrence='COVERAGE' AND n.organism_id=p_lo AND n.operation='BOARD_RESIGN'
 AND n.act_uuid::text=p_source AND x.event_uuid::text=p_effect
$$;

-- Vacancy. Due when every condition of elect_start_vacancy holds. The effect is the
-- vacancy row for that ending, whose key is the ending itself.
CREATE FUNCTION public.te_elect_vacancy_due(p_lo bigint,p_known timestamptz)
RETURNS TABLE(source_ref text,occurrence text,due_at timestamptz) LANGUAGE sql STABLE AS $$
 SELECT s.ending::text,'VACANCY'::text,s.effective_at
 FROM public.elect_vacancy_sources s
 WHERE s.organism_id=p_lo AND greatest(s.effective_at,s.recorded_at)<=p_known
 AND NOT public.elect_already_replaced(s,p_known)
 AND EXISTS(SELECT FROM public.core_circle c WHERE c.parent_organism_id=p_lo AND c.kind='GOVERNANCE')
 AND (public.cq_settings_at(p_lo,greatest(s.recorded_at,s.effective_at))->>'enabled')::boolean
 AND NOT EXISTS(SELECT FROM public.core_electvacancy v WHERE v.ending=s.ending)
$$;

CREATE FUNCTION public.te_elect_vacancy_proof(p_lo bigint,p_purpose text,p_source text,p_occurrence text,p_effect text)
RETURNS TABLE(due_at timestamptz,effect_at timestamptz,enterer_id bigint,made_here boolean)
LANGUAGE sql STABLE AS $$
 SELECT v.effective_at,v.recorded_at,v.enterer,public.te_made_here('core_electvacancy',v.ending::text) FROM public.core_electvacancy v
 WHERE p_purpose='VACANCY' AND p_occurrence='VACANCY' AND v.organism_id=p_lo
 AND v.ending::text=p_source AND p_effect=p_source
$$;

-- Work this LO holds in custody: every open activity whose current custody row belongs
-- to the LO's Board (Board work) or to one of the LO's committee mandates.
CREATE FUNCTION public.te_elect_work_in_custody(p_lo bigint) RETURNS SETOF bigint LANGUAGE sql STABLE AS $$
 SELECT a.id FROM public.core_serviceactivity a
 WHERE a.state IN ('assigned','in_progress') AND EXISTS(SELECT FROM public.core_cmtcustody w
   WHERE w.activity=a.id AND NOT EXISTS(SELECT FROM public.core_cmtcustody n WHERE n.predecessor=w.receipt)
   AND (EXISTS(SELECT FROM public.core_circle c WHERE c.id=w.board_circle AND c.parent_organism_id=p_lo)
     OR (w.board_circle IS NULL AND EXISTS(SELECT FROM public.cmt_mandates m
           WHERE m.binding=w.mandate AND m.organism_id=p_lo))))
$$;

-- The law bindings a refresh in this LO may refer to: each of the LO's mandates, and
-- the binding cited by the current custody row of each activity the LO holds.
CREATE FUNCTION public.te_refresh_bindings(p_lo bigint) RETURNS SETOF uuid LANGUAGE sql STABLE AS $$
 SELECT m.binding FROM public.cmt_mandates m WHERE m.organism_id=p_lo
 UNION SELECT w.mandate FROM public.core_cmtcustody w WHERE w.activity IN (SELECT public.te_elect_work_in_custody(p_lo))
   AND NOT EXISTS(SELECT FROM public.core_cmtcustody n WHERE n.predecessor=w.receipt)
$$;

-- The identities a refresh in this LO may name as holder: every office holder of the LO.
CREATE FUNCTION public.te_refresh_holders(p_lo bigint) RETURNS SETOF bigint LANGUAGE sql STABLE AS $$
 SELECT DISTINCT om.identity_id FROM public.core_contextualroleassignment a
 JOIN public.core_organismmembership om ON om.id=a.membership_id WHERE om.living_organism_id=p_lo
$$;

-- The assignments a custody row may name as its receiving office: every assignment of a role
-- definition of the LO, or held through a membership of the LO. The custody writer chooses the
-- office, at its own dated cut; this set does not repeat that choice.
CREATE FUNCTION public.te_refresh_offices(p_lo bigint) RETURNS SETOF bigint LANGUAGE sql STABLE AS $$
 SELECT a.id FROM public.core_contextualroleassignment a
 JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id WHERE d.living_organism_id=p_lo
 UNION SELECT a.id FROM public.core_contextualroleassignment a
 JOIN public.core_organismmembership om ON om.id=a.membership_id WHERE om.living_organism_id=p_lo
$$;

-- What the committee refresh (cmt_drain) locks at commit, other than the LO row, tried
-- here without waiting. An effect whose row is a source of cmt_changed calls this under
-- the LO lock and holds what it takes to the end of the transaction.
--  1. The activity of every custody row the refresh may write (cmt_custody_guard and
--     cmt_refresh_board_activity lock it; SERVICE locks an activity without the LO).
--  2. The law bindings a new custody or composition row refers to. Their foreign keys
--     take a key-share lock, which waits for a law ending that holds the binding.
--  3. The identities that may receive work: every office holder of the LO. The holder's
--     foreign key takes a key-share lock, which waits for a SERVICE command that holds
--     that identity.
--  4. The assignments that may receive work. A custody row names its receiving office by an
--     immediate foreign key to the assignment row (core_cmtcustody.office), which takes a
--     key-share lock at the insert. Every landed locker of an assignment takes the LO first,
--     except the platform-law check (l_lock_dependencies, 0067), which holds FOR UPDATE, with
--     no LO, assignments that were nominations at its own earlier statement time. One of those
--     can have become the receiving Chair by the time the work is handed over.
-- Each set is wider than the refresh will touch, on purpose: it does not repeat the
-- refresh's own selection, so it cannot fall out of step with it.
CREATE FUNCTION public.te_refresh_claim(p_lo bigint) RETURNS boolean LANGUAGE sql VOLATILE AS $$
 SELECT (SELECT count(*) FROM (
     SELECT FROM public.core_serviceactivity a WHERE a.id IN (SELECT public.te_elect_work_in_custody(p_lo))
     FOR UPDATE OF a SKIP LOCKED) held)=(SELECT count(*) FROM public.te_elect_work_in_custody(p_lo))
  AND (SELECT count(*) FROM (
     SELECT FROM public.core_organismlawbinding b WHERE b.binding_uuid IN (SELECT public.te_refresh_bindings(p_lo))
     FOR KEY SHARE OF b SKIP LOCKED) held)=(SELECT count(*) FROM public.te_refresh_bindings(p_lo))
  AND (SELECT count(*) FROM (
     SELECT FROM public.core_identity i WHERE i.id IN (SELECT public.te_refresh_holders(p_lo))
     FOR KEY SHARE OF i SKIP LOCKED) held)=(SELECT count(*) FROM public.te_refresh_holders(p_lo))
  AND (SELECT count(*) FROM (
     SELECT FROM public.core_contextualroleassignment a WHERE a.id IN (SELECT public.te_refresh_offices(p_lo))
     FOR KEY SHARE OF a SKIP LOCKED) held)=(SELECT count(*) FROM public.te_refresh_offices(p_lo))
$$;

-- The two rows a coverage publication writes under the LO, tried here without waiting.
-- c2_publish locks the LO's essential-coverage case, and appends REASSESS to the LO's
-- restricted-continuity case if one exists, which updates that case's head. Every route
-- by which an engine purpose reaches c2_publish makes this claim (found with
-- grep -rn c2_publish core/migrations):
--   0055 c2_source_change, the coverage trigger on office and membership events: a term end;
--   _elect_continuity.py elect_resignation_coverage: a resignation, and a seat vacancy that
--     records the resignation's assessment itself;
--   0055 c2_notice_input: a C2 notice (0095);
--   0055 c2_future_cuts: the forecast, which the same triggers call in the same transaction.
-- A case the publication would create does not exist yet and cannot be held by anyone.
CREATE FUNCTION public.te_coverage_claim(p_lo bigint) RETURNS boolean LANGUAGE sql VOLATILE AS $$
 SELECT (SELECT count(*) FROM (
     SELECT FROM public.core_essentialcoveragecase c WHERE c.living_organism_id=p_lo
     FOR UPDATE OF c SKIP LOCKED) held)=(SELECT count(*) FROM public.core_essentialcoveragecase c
     WHERE c.living_organism_id=p_lo)
  AND (SELECT count(*) FROM (
     SELECT FROM public.core_restrictedcontinuitycase r WHERE r.living_organism_id=p_lo
     FOR UPDATE OF r SKIP LOCKED) held)=(SELECT count(*) FROM public.core_restrictedcontinuitycase r
     WHERE r.living_organism_id=p_lo)
$$;

-- The rows the LO row's own foreign keys reach. A writer that appends to the LO's chain
-- updates the LO row more than once in its transaction (the head, then the cached state), and
-- PostgreSQL checks the foreign keys of a row again when the version being updated is the
-- transaction's own. So those writers read-lock these two rows at commit.
CREATE FUNCTION public.te_lo_references(p_lo bigint) RETURNS TABLE(identity bigint,basis bigint) LANGUAGE sql STABLE AS $$
 SELECT lo.founder_identity_id,lo.founding_authority_basis_id FROM public.core_livingorganism lo WHERE lo.id=p_lo
$$;

-- Identities and authority bases that the records a writer inserts will refer to, tried here
-- without waiting. Their foreign keys take a key-share lock, most of them at commit (the
-- constraints are deferred), and a key-share lock waits for a holder of the row FOR UPDATE:
-- main holds an authority basis so in 0056 (c3 lifecycle), and an Identity in several services.
-- A row of the transaction's own (the carrier, which the runner holds before the LO) is not
-- skipped. The key-share try on another person's Identity under the LO is the scoped exception
-- the Human Governor ruled on 12 Nov 2026 to the lock-order ruling of 15 Oct; it never waits.
CREATE FUNCTION public.te_reference_claim(p_identities bigint[],p_bases bigint[]) RETURNS boolean LANGUAGE sql VOLATILE AS $$
 SELECT (SELECT count(*) FROM (
     SELECT FROM public.core_identity i WHERE i.id=ANY(p_identities)
     FOR KEY SHARE OF i SKIP LOCKED) held)=(SELECT count(*) FROM public.core_identity i WHERE i.id=ANY(p_identities))
  AND (SELECT count(*) FROM (
     SELECT FROM public.core_authoritybasis b WHERE b.id=ANY(p_bases)
     FOR KEY SHARE OF b SKIP LOCKED) held)=(SELECT count(*) FROM public.core_authoritybasis b WHERE b.id=ANY(p_bases))
$$;

-- The authority bases an ELECT writer's records cite: the LO's founding basis (the
-- resignation's assessment and the vacancy's route act cite it; the LO row refers to it); and,
-- for a term end, the basis of every state event of the assignment, since the END event and
-- the coverage records made from it carry the due ASSIGN event's own basis.
CREATE FUNCTION public.te_elect_bases(p_lo bigint,p_purpose text,p_source text) RETURNS SETOF bigint LANGUAGE sql STABLE AS $$
 SELECT r.basis FROM public.te_lo_references(p_lo) r
 UNION SELECT e.authority_basis_id FROM public.core_contextualroleassignmentstateevent e
   JOIN public.core_contextualroleassignment a ON a.id=e.assignment_id
   WHERE p_purpose='TERM_END' AND a.assignment_uuid::text=p_source
$$;

-- The rows the assignment row's own foreign keys reach: its membership, its role definition
-- and its Circle. A term end updates the assignment more than once (the head, then the cached
-- state), so they are read-locked at commit, as te_lo_references says of the LO row.
CREATE FUNCTION public.te_assignment_claim(p_source text) RETURNS boolean LANGUAGE sql VOLATILE AS $$
 SELECT (SELECT count(*) FROM (
     SELECT FROM public.core_organismmembership m WHERE m.id IN (SELECT a.membership_id
       FROM public.core_contextualroleassignment a WHERE a.assignment_uuid::text=p_source)
     FOR KEY SHARE OF m SKIP LOCKED) held)=(SELECT count(*) FROM public.core_organismmembership m
     WHERE m.id IN (SELECT a.membership_id FROM public.core_contextualroleassignment a WHERE a.assignment_uuid::text=p_source))
  AND (SELECT count(*) FROM (
     SELECT FROM public.core_organismroledefinition d WHERE d.id IN (SELECT a.role_definition_id
       FROM public.core_contextualroleassignment a WHERE a.assignment_uuid::text=p_source)
     FOR KEY SHARE OF d SKIP LOCKED) held)=(SELECT count(*) FROM public.core_organismroledefinition d
     WHERE d.id IN (SELECT a.role_definition_id FROM public.core_contextualroleassignment a WHERE a.assignment_uuid::text=p_source))
  AND (SELECT count(*) FROM (
     SELECT FROM public.core_circle c WHERE c.id IN (SELECT a.circle_id
       FROM public.core_contextualroleassignment a WHERE a.assignment_uuid::text=p_source)
     FOR KEY SHARE OF c SKIP LOCKED) held)=(SELECT count(*) FROM public.core_circle c
     WHERE c.id IN (SELECT a.circle_id FROM public.core_contextualroleassignment a WHERE a.assignment_uuid::text=p_source))
$$;

-- Every row an ELECT writer locks, or reaches by a foreign key of a record it writes, through
-- commit, other than the LO (held), the carrier (held by the runner before the LO) and rows of
-- the LO's own append-only chains (a new record's predecessor), is tried here without waiting:
--  the assignment, for a term end (elect_record_term_end locks it), with the rows its own
--    foreign keys reach (te_assignment_claim);
--  the coverage claim, for every purpose. A seat vacancy that follows a resignation records
--    that resignation's assessment itself, so it makes the coverage claim too;
--  the identities and authority bases the new records cite (te_reference_claim), for every
--    purpose: the enterer is the carrier; the bases are te_elect_bases; the LO row's own
--    references are te_lo_references;
--  the refresh's claim, for a term end and a vacancy, which are sources of the committee
--    refresh. A resignation is not: it writes coverage records only, and no coverage table is a
--    source of cmt_changed, so it does not make that claim.
-- Each set is wider than the writers touch, on purpose (te_refresh_claim says why).
-- One lock is not a row's. A term end is the one registered purpose that writes the office
-- state event table, and main locks that whole table against writers while the root's founder
-- changes a platform law that has a predecessor (l_lock_dependencies, 0067: SHARE MODE, the
-- only table lock main takes at run time). So a term end tries the table lock its insert needs
-- (te_office_events_claim, below) before the LO, and again under it, where it is already held.
CREATE FUNCTION public.te_office_events_claim() RETURNS boolean LANGUAGE plpgsql VOLATILE AS $$
BEGIN
 -- The lock an INSERT takes, tried without waiting and kept to the end of the transaction.
 -- NOWAIT also refuses to queue in front of a law change that is itself waiting for the table.
 LOCK TABLE public.core_contextualroleassignmentstateevent IN ROW EXCLUSIVE MODE NOWAIT;
 RETURN true;
EXCEPTION WHEN lock_not_available THEN
 RETURN false;
END $$;

CREATE FUNCTION public.te_elect_claim(p_lo bigint,p_purpose text,p_source text,p_occurrence text,p_phase text)
RETURNS boolean LANGUAGE sql VOLATILE AS $$
 SELECT CASE
  WHEN p_purpose='TERM_END' AND NOT public.te_office_events_claim() THEN false
  WHEN p_phase<>'UNDER_LO' THEN true
  ELSE ((p_purpose<>'TERM_END' OR ((SELECT count(*)=1 FROM (
     SELECT FROM public.core_contextualroleassignment a WHERE a.assignment_uuid::text=p_source
     FOR UPDATE OF a SKIP LOCKED) held) AND public.te_assignment_claim(p_source)))
  AND public.te_coverage_claim(p_lo)
  AND public.te_reference_claim(ARRAY(SELECT r.identity FROM public.te_lo_references(p_lo) r),
        ARRAY(SELECT public.te_elect_bases(p_lo,p_purpose,p_source)))
  AND (p_purpose NOT IN ('TERM_END','VACANCY') OR public.te_refresh_claim(p_lo))) END
$$;

-- Origin evidence for the three effects (0091). A term end is the END event that cites the
-- due ASSIGN event; only those are recorded from that busy table. Every coverage assessment
-- is recorded: the resignation's here, and C2's own in 0095, where the landed recovery guard
-- reads it too.
-- Each is the last BEFORE INSERT trigger of its table by name, so it reads the row as the
-- other triggers leave it, and writes the origin just before the row itself. Which rows are
-- recorded is te_origin_applies's to say (0091), in one place for the recorder and the check
-- at commit: of the office state events, the ends this engine's expiry writes.
CREATE TRIGGER zz_te_origin BEFORE INSERT ON public.core_contextualroleassignmentstateevent
 FOR EACH ROW EXECUTE FUNCTION public.te_record_origin();
CREATE TRIGGER zz_te_origin BEFORE INSERT ON public.core_coverageassessment
 FOR EACH ROW EXECUTE FUNCTION public.te_record_origin();
CREATE TRIGGER zz_te_origin BEFORE INSERT ON public.core_electvacancy
 FOR EACH ROW EXECUTE FUNCTION public.te_record_origin();
"""

REMOVE = """
DROP TRIGGER zz_te_origin ON public.core_electvacancy;
DROP TRIGGER zz_te_origin ON public.core_coverageassessment;
DROP TRIGGER zz_te_origin ON public.core_contextualroleassignmentstateevent;
DROP FUNCTION public.te_elect_claim(bigint,text,text,text,text);
DROP FUNCTION public.te_office_events_claim();
DROP FUNCTION public.te_assignment_claim(text);
DROP FUNCTION public.te_elect_bases(bigint,text,text);
DROP FUNCTION public.te_reference_claim(bigint[],bigint[]);
DROP FUNCTION public.te_lo_references(bigint);
DROP FUNCTION public.te_coverage_claim(bigint);
DROP FUNCTION public.te_refresh_claim(bigint);
DROP FUNCTION public.te_refresh_offices(bigint);
DROP FUNCTION public.te_refresh_holders(bigint);
DROP FUNCTION public.te_refresh_bindings(bigint);
DROP FUNCTION public.te_elect_work_in_custody(bigint);
DROP FUNCTION public.te_elect_vacancy_proof(bigint,text,text,text,text);
DROP FUNCTION public.te_elect_vacancy_due(bigint,timestamptz);
DROP FUNCTION public.te_elect_resignation_proof(bigint,text,text,text,text);
DROP FUNCTION public.te_elect_resignation_due(bigint,timestamptz);
DROP FUNCTION public.te_elect_term_proof(bigint,text,text,text,text);
DROP FUNCTION public.te_elect_term_due(bigint,timestamptz);
"""


def body(sql):
    """The function body as PostgreSQL stores it."""
    return sql.split(" AS $$", 1)[1].rsplit("$$;", 1)[0]


def expect(cursor, sql):
    """G2-4: refuse unless the installed registry is exactly the expected one."""
    if storage.installed(cursor, REGISTRY_SIGNATURE) != (body(sql), False, "i", "sql"):
        raise RuntimeError("Unexpected installed body: " + REGISTRY_SIGNATURE)


def replace(sql):
    return sql.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


def install(apps, schema_editor):
    """Create the ELECT functions, then admit the three witnessed purposes in the registry."""
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        expect(cursor, OLD_REGISTRY)
        cursor.execute(SQL)
        cursor.execute(replace(NEW_REGISTRY))


def uninstall(apps, schema_editor):
    """Refuse while ELECT receipts exist; put the empty registry back; drop the ELECT functions."""
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        expect(cursor, NEW_REGISTRY)
        # Fence receipt writes before looking, to the end of the migration's transaction.
        cursor.execute("LOCK TABLE public.core_tereceipt IN SHARE ROW EXCLUSIVE MODE")
        cursor.execute("SELECT EXISTS (SELECT 1 FROM public.core_tereceipt WHERE consumer = 'ELECT')")
        if cursor.fetchone()[0]:
            raise RuntimeError("0092 reverse refused while ELECT receipts exist")
        # Pace rows are counts, not evidence. They cannot outlive their consumer.
        cursor.execute("DELETE FROM public.core_tepace WHERE consumer = 'ELECT'")
        cursor.execute(replace(OLD_REGISTRY))
        cursor.execute(REMOVE)


class Migration(migrations.Migration):
    dependencies = (("core", "0091_te_engine_storage"),)
    operations = (migrations.RunPython(install, uninstall),)
