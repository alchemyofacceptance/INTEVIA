"""TIMED EVENTS: the committee join. A mandate's stated end date, marked once.

A dated mandate stops being live at its end date because of that date alone, and on
main nothing is written then: the composition gap and the custody moves are recorded
at whatever later moment something else touches the committee. This migration adds:

- the marker, one immutable row per mandate: "this mandate reached its stated end
  date", dated at that date. It is the engine's effect for this consumer;
- the marker as a source of the landed committee refresh (cmt_changed, replaced under
  G2-4 with one new case), so the landed mechanism writes every gap and custody move,
  dated at the end date, through its own guards. The engine writes no custody row;
- the selector, proof and claim of the purpose, and its row in the registry;
- OD-2, one landed function (cmt_board_return, replaced under G2-4): work that came
  back to the Chair by an automatic return is held when that Chair's term ends, and is
  not handed on a second time to whoever holds the office next;
- OD-1, one landed function (cmt_custody_due, replaced under G2-4): the date at which a
  committee custody spell falls due counts the mandate's stated end only if the mandate
  was live when that spell began. Work the Board recovers after a mandate has ended
  early is held by the recovered officer until that officer's own ending; the ended
  mandate's nominal date, still ahead, is no longer offered as the spell's end. The
  office's own deadline is untouched.

A date is marked only if it is genuine: the mandate was still live at the moment just
before it. A mandate that ended earlier for another cause (its task completed, it was
replaced, its Circle closed, a parent ended, the Board stood down) has only a nominal
date left, and gets no marker.
"""

from importlib import import_module

from django.db import migrations

storage = import_module("core.migrations.0091_te_engine_storage")
elect = import_module("core.migrations.0092_te_elect_joins")
cq = import_module("core.migrations.0093_te_cq_joins")
landed = import_module("core.migrations._committee_mandates")
stand_down = import_module("core.migrations.0087_members_stand_down")

CHANGED_SIGNATURE = "public.cmt_changed()"
_OPEN = "CREATE FUNCTION public.cmt_changed() RETURNS trigger LANGUAGE plpgsql AS $$"
_CASE_AFTER = " ELSIF TG_TABLE_NAME='core_electvacancy' THEN\n"
_NEW_CASE = """ ELSIF TG_TABLE_NAME='core_cmtmandateend' THEN
   -- TIMED EVENTS: the mandate reached its stated end date. Refresh it and its children at that date.
   lo:=NEW.organism_id; cut:=NEW.effective_at; forming:=NEW.mandate; cascade_:=true;
"""


def one(text, old, new):
    """Replace exactly one occurrence, or stop: the predecessor is not the one expected."""
    if text.count(old) != 1:
        raise RuntimeError("0094 predecessor text no longer matches: " + old[:60])
    return text.replace(old, new, 1)


def landed_changed():
    """The body of cmt_changed as its last definer (0084, _committee_mandates.py) installs it."""
    if landed.SQL.count(_OPEN) != 1:
        raise RuntimeError("0094 cannot find the landed cmt_changed")
    return landed.SQL.split(_OPEN, 1)[1].split("$$;", 1)[0]


def landed_board_return():
    """cmt_board_return as its last definer (0087, the stand-down exclusion) installs it: the whole statement."""
    hits = [new for _, old, new in stand_down.mandate_edits() if "FUNCTION public.cmt_board_return(" in old]
    if len(hits) != 1:
        raise RuntimeError("0094 cannot find the landed cmt_board_return")
    return hits[0]


OLD_CHANGED = landed_changed()
NEW_CHANGED = one(OLD_CHANGED, _CASE_AFTER, _NEW_CASE + _CASE_AFTER)

# OD-2. A custody row written by an automatic return carries the ending in its cause
# (ending_place, ending_office); a Board's own act carries the implementing act. When
# the row now ending is itself an automatic return, the work is held: no holder and no
# office, with the actual ending kept for the guarded writer. Without this, a Chair whose
# term ends as a successor's begins hands returned work on a second time, unasked. A
# later Board reassignment starts a new, authorised spell. 0087's stand-down exclusion is kept.
RETURN_SIGNATURE = "public.cmt_board_return(uuid,timestamptz,timestamptz)"
_CHAIR_JOIN = (" LEFT JOIN public.core_contextualroleassignment a"
               " ON a.id=public.cmt_board_chair(b.parent_organism_id,due.moment,p_known)\n")
OLD_RETURN = landed_board_return()
NEW_RETURN = one(OLD_RETURN, _CHAIR_JOIN, """ LEFT JOIN public.core_contextualroleassignment a ON NOT (w.cause ? 'ending_office')
   AND a.id=public.cmt_board_chair(b.parent_organism_id,due.moment,p_known)
""")

# OD-1. cmt_custody_due is the earlier of the office's own deadline and the mandate's observed
# end, and main counts that end whenever it is not before the spell began. One condition is
# added to the mandate-date candidate, and to nothing else: the mandate was live at the
# spell's start. Its two callers, the refresh and the custody guard, follow without an edit.
DUE_SIGNATURE = "public.cmt_custody_due(bigint,uuid,timestamptz,timestamptz,timestamptz)"
_DUE_OPEN = ("CREATE FUNCTION public.cmt_custody_due(p_office bigint,p_binding uuid,p_since timestamptz,"
             "p_cut timestamptz,p_known timestamptz)\nRETURNS timestamptz LANGUAGE sql STABLE AS $$")
_NOMINAL = "CASE WHEN ending>=p_since THEN ending END"
_LIVE_AT_START = "CASE WHEN ending>=p_since AND public.cmt_live(p_binding,p_since,p_known) THEN ending END"


def landed_due():
    """The body of cmt_custody_due as its only definer (0084, _committee_mandates.py) installs it."""
    if landed.SQL.count(_DUE_OPEN) != 1:
        raise RuntimeError("0094 cannot find the landed cmt_custody_due")
    return landed.SQL.split(_DUE_OPEN, 1)[1].split("$$;", 1)[0]


OLD_DUE = landed_due()
NEW_DUE = one(OLD_DUE, _NOMINAL, _LIVE_AT_START)


def due_sql(body):
    return _DUE_OPEN.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1) + body + "$$;"


# consumer, purpose, ordinal, rule, selector, validator, claim. Witness: C-04.
# The rule label is this build's own name for the rule: no landed writer records a
# mandate's dated end, so there is no landed name to take.
CMT_ROWS = (
    ("CMT", "MANDATE_END", 20, "CMT:MANDATE_END", "te_cmt_end_due", "te_cmt_end_proof", "te_cmt_end_claim"),
)
OLD_REGISTRY = cq.NEW_REGISTRY
NEW_REGISTRY = storage.registry_sql((*elect.ROWS, *cq.CQ_ROWS, *CMT_ROWS))

SQL = r"""
CREATE TABLE public.core_cmtmandateend(
 mandate uuid PRIMARY KEY REFERENCES public.core_organismlawbinding(binding_uuid),
 organism_id bigint NOT NULL REFERENCES public.core_livingorganism(id),
 effective_at timestamptz NOT NULL,
 recorded_at timestamptz NOT NULL,
 enterer bigint NOT NULL REFERENCES public.core_identity(id)
);

-- A marker is accepted only for a mandate of that LO, dated exactly at the mandate's
-- stated end, once that date has come, and only if the mandate was live just before it.
CREATE FUNCTION public.cmt_end_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE m public.cmt_mandates;
BEGIN
 NEW.recorded_at:=statement_timestamp();
 SELECT * INTO m FROM public.cmt_mandates x WHERE x.binding=NEW.mandate AND x.organism_id=NEW.organism_id;
 IF m.binding IS NULL OR m.mandate->>'ends_at' IS NULL
    OR NEW.effective_at IS DISTINCT FROM (m.mandate->>'ends_at')::timestamptz
    OR NEW.effective_at>NEW.recorded_at
    OR NOT public.cmt_live(NEW.mandate,NEW.effective_at-interval '1 microsecond',NEW.recorded_at)
 THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 RETURN NEW;
END $$;

CREATE TRIGGER cmt_input BEFORE INSERT ON public.core_cmtmandateend
 FOR EACH ROW EXECUTE FUNCTION public.cmt_end_guard();
CREATE TRIGGER cmt_immutable BEFORE UPDATE OR DELETE ON public.core_cmtmandateend
 FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE TRIGGER s015_cmt_no_truncate BEFORE TRUNCATE ON public.core_cmtmandateend
 FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE CONSTRAINT TRIGGER zz_cmt_change AFTER INSERT ON public.core_cmtmandateend
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.cmt_changed();
-- Origin evidence for the marker (0091).
CREATE TRIGGER zz_te_origin BEFORE INSERT ON public.core_cmtmandateend
 FOR EACH ROW EXECUTE FUNCTION public.te_record_origin();

-- Due: the stated end date has come, the mandate was live just before it, no marker yet.
CREATE FUNCTION public.te_cmt_end_due(p_lo bigint,p_known timestamptz)
RETURNS TABLE(source_ref text,occurrence text,due_at timestamptz) LANGUAGE sql STABLE AS $$
 SELECT m.binding::text,'DATED_END'::text,e.ends
 FROM public.cmt_mandates m CROSS JOIN LATERAL (SELECT (m.mandate->>'ends_at')::timestamptz AS ends) e
 WHERE m.organism_id=p_lo AND e.ends<=p_known AND m.recorded_at<=p_known
 AND NOT EXISTS(SELECT FROM public.core_cmtmandateend x WHERE x.mandate=m.binding)
 AND public.cmt_live(m.binding,e.ends-interval '1 microsecond',p_known)
$$;

CREATE FUNCTION public.te_cmt_end_proof(p_lo bigint,p_purpose text,p_source text,p_occurrence text,p_effect text)
RETURNS TABLE(due_at timestamptz,effect_at timestamptz,enterer_id bigint,made_here boolean)
LANGUAGE sql STABLE AS $$
 SELECT x.effective_at,x.recorded_at,x.enterer,public.te_made_here('core_cmtmandateend',x.mandate::text) FROM public.core_cmtmandateend x
 WHERE p_purpose='MANDATE_END' AND p_occurrence='DATED_END' AND x.organism_id=p_lo
 AND x.mandate::text=p_source AND p_effect=p_source
$$;

-- The marker's commit runs the committee refresh, so it makes the refresh's claim.
CREATE FUNCTION public.te_cmt_end_claim(p_lo bigint,p_purpose text,p_source text,p_occurrence text,p_phase text)
RETURNS boolean LANGUAGE sql VOLATILE AS $$
 SELECT p_phase<>'UNDER_LO' OR public.te_refresh_claim(p_lo)
$$;

-- The writer. The same enterer conditions as the landed term-end writer. It marks only a
-- date the selector offers (te_cmt_end_due), so a date that is not genuine writes nothing;
-- the marker's guard asks again and is the backstop. False means nothing was written.
CREATE FUNCTION public.cmt_record_dated_end(p_lo bigint,p_binding uuid,p_enterer bigint)
RETURNS boolean LANGUAGE plpgsql VOLATILE AS $$
DECLARE cut_ timestamptz:=statement_timestamp(); writes integer;
BEGIN
 PERFORM 1 FROM public.core_livingorganism WHERE id=p_lo FOR UPDATE;
 IF public.disc_member_state(p_lo,p_enterer,cut_) IS DISTINCT FROM 'ACTIVE' OR NOT EXISTS(
    SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
    WHERE i.id=p_enterer AND i.access_state='active' AND u.is_active)
 THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 INSERT INTO public.core_cmtmandateend(mandate,organism_id,effective_at,recorded_at,enterer)
 SELECT d.source_ref::uuid,p_lo,d.due_at,cut_,p_enterer FROM public.te_cmt_end_due(p_lo,cut_) d
 WHERE d.source_ref=p_binding::text
 ON CONFLICT(mandate) DO NOTHING;
 GET DIAGNOSTICS writes=ROW_COUNT; RETURN writes=1;
END $$;
"""

REMOVE = """
DROP FUNCTION public.cmt_record_dated_end(bigint,uuid,bigint);
DROP FUNCTION public.te_cmt_end_claim(bigint,text,text,text,text);
DROP FUNCTION public.te_cmt_end_proof(bigint,text,text,text,text);
DROP FUNCTION public.te_cmt_end_due(bigint,timestamptz);
DROP TRIGGER zz_te_origin ON public.core_cmtmandateend;
DROP TRIGGER zz_cmt_change ON public.core_cmtmandateend;
DROP TRIGGER s015_cmt_no_truncate ON public.core_cmtmandateend;
DROP TRIGGER cmt_immutable ON public.core_cmtmandateend;
DROP TRIGGER cmt_input ON public.core_cmtmandateend;
DROP FUNCTION public.cmt_end_guard();
DROP TABLE public.core_cmtmandateend;
"""


def changed_sql(body):
    return "CREATE OR REPLACE FUNCTION public.cmt_changed() RETURNS trigger LANGUAGE plpgsql AS $$" + body + "$$;"


def expect(cursor, registry, changed, board_return, due):
    """G2-4: refuse unless the installed registry, cmt_changed, cmt_board_return and cmt_custody_due are exactly the expected ones."""
    if storage.installed(cursor, DUE_SIGNATURE) != (due, False, "s", "sql"):
        raise RuntimeError("Unexpected installed body: " + DUE_SIGNATURE)
    if storage.installed(cursor, RETURN_SIGNATURE) != (elect.body(board_return), False, "s", "sql"):
        raise RuntimeError("Unexpected installed body: " + RETURN_SIGNATURE)
    if storage.installed(cursor, elect.REGISTRY_SIGNATURE) != (elect.body(registry), False, "i", "sql"):
        raise RuntimeError("Unexpected installed body: " + elect.REGISTRY_SIGNATURE)
    if storage.installed(cursor, CHANGED_SIGNATURE) != (changed, False, "v", "plpgsql"):
        raise RuntimeError("Unexpected installed body: " + CHANGED_SIGNATURE)


def install(apps, schema_editor):
    """Create the marker and its functions; add the marker case to cmt_changed; admit the purpose."""
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        expect(cursor, OLD_REGISTRY, OLD_CHANGED, OLD_RETURN, OLD_DUE)
        cursor.execute(changed_sql(NEW_CHANGED))
        cursor.execute(NEW_RETURN)
        cursor.execute(due_sql(NEW_DUE))
        cursor.execute(SQL)
        cursor.execute(elect.replace(NEW_REGISTRY))


def uninstall(apps, schema_editor):
    """Refuse while a marker or a CMT receipt exists; put cmt_changed and the registry back; drop the rest."""
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        expect(cursor, NEW_REGISTRY, NEW_CHANGED, NEW_RETURN, NEW_DUE)
        # Fence receipt and marker writes before looking, to the end of the migration's transaction.
        cursor.execute("LOCK TABLE public.core_tereceipt, public.core_cmtmandateend IN SHARE ROW EXCLUSIVE MODE")
        cursor.execute("""SELECT EXISTS (SELECT 1 FROM public.core_tereceipt WHERE consumer = 'CMT')
                          OR EXISTS (SELECT 1 FROM public.core_cmtmandateend)""")
        if cursor.fetchone()[0]:
            raise RuntimeError("0094 reverse refused while dated-end markers or CMT receipts exist")
        cursor.execute("DELETE FROM public.core_tepace WHERE consumer = 'CMT'")
        cursor.execute(elect.replace(OLD_REGISTRY))
        cursor.execute(REMOVE)
        cursor.execute(changed_sql(OLD_CHANGED))
        cursor.execute(OLD_RETURN)
        cursor.execute(due_sql(OLD_DUE))


class Migration(migrations.Migration):
    dependencies = (("core", "0093_te_cq_joins"),)
    operations = (migrations.RunPython(install, uninstall),)
