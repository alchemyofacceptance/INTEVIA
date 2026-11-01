"""SR-H: the database refuses office changes already forbidden by the services.

Replace three guard bodies, keeping every existing branch and trigger binding.
An appointment must use an active definition. The Coordinator can be defined
only by founding, reserved platform definitions cannot be changed here, board
offices cannot be retired, and a successor cannot change the office's kind.
Every later version must supersede the latest version of that office.

Forward checks the exact installed bodies and trigger contracts before writing.
Reverse checks this migration's bodies, then refuses populated non-Coordinator
LO office definitions/assignments, and Coordinator successors. A founding-only
LO or historical pre-SR definition scaffold can reverse; no record is deleted.
This is a bounded rollback restriction, not an audit of all pre-existing data.
Both directions are atomic and preserve the observed public trigger count.
"""
from importlib import import_module

from django.db import migrations

_C1 = import_module("core.migrations.0051_sr_role_definitions")
_C2 = import_module("core.migrations.0052_sr_office_guard_widening")
MIGRATION = "0053_sr_office_safeguards"
PREVIOUS_MIGRATION = "0052_sr_office_guard_widening"


def _replace_once(body, old, new):
    """Refuse an unexpected baseline instead of guessing where to insert a rule."""
    if body.count(old) != 1:
        raise RuntimeError("SR 0053 cannot prepare its guard: the expected source fragment is missing or repeated")
    return body.replace(old, new, 1)


ASSIGN_MARKER = "        IF NEW.action = 'ASSIGN' AND EXISTS (\n"
ASSIGN_CLAUSE = """        IF NEW.action = 'ASSIGN' THEN
            -- Hold the named version against a concurrent direct deactivation.
            SELECT d.active INTO v_role_active
              FROM public.core_organismroledefinition d
              JOIN public.core_contextualroleassignment a ON a.role_definition_id=d.id
             WHERE a.id=NEW.assignment_id FOR SHARE OF d;
        END IF;
        IF NEW.action = 'ASSIGN' AND v_role_active IS DISTINCT FROM TRUE THEN
            RAISE EXCEPTION 'S2B office attribution refused: superseded definition'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
"""
NEW_S2B_GUARD_OFFICE_ATTRIBUTION_BODY = _replace_once(
    _C2.NEW_S2B_GUARD_OFFICE_ATTRIBUTION_BODY, ASSIGN_MARKER, ASSIGN_CLAUSE + ASSIGN_MARKER,
)

COORDINATOR_INSERT_MARKER = "        -- (D) the definition act: the decision names this very version (the 0036/0044 pattern).\n"
COORDINATOR_INSERT_CLAUSE = """        IF NEW.code = 'COORDINATOR' THEN
            RAISE EXCEPTION 'SR definition refused: the Coordinator office is established at founding and cannot be changed here'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
"""
KIND_MARKER = "        IF v_superseded.id IS NULL\n"
KIND_CLAUSE = """        IF v_superseded.id IS NOT NULL AND NEW.kind IS DISTINCT FROM v_superseded.kind THEN
            RAISE EXCEPTION 'SR definition refused: an office must keep its original kind'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
"""
NEW_SR_DEFINITION_INSERT_GUARD_BODY = _replace_once(
    _replace_once(_C1.SR_DEFINITION_INSERT_GUARD_BODY, COORDINATOR_INSERT_MARKER,
                  COORDINATOR_INSERT_MARKER + COORDINATOR_INSERT_CLAUSE),
    KIND_MARKER, KIND_CLAUSE + KIND_MARKER,
)

SUCCESSOR_MARKER = '    SELECT decision.* INTO v_decision_row\n      FROM public.core_authoritydecision decision\n     WHERE decision.decision_reference = NEW.authority_decision_reference;\n'
SUCCESSOR_CLAUSE = """    IF v_max_version > 0 AND NEW.code <> 'COORDINATOR' THEN
        IF NEW.supersedes_id IS NULL THEN
            RAISE EXCEPTION 'SR definition refused: a later version must supersede the latest version of this office'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF v_superseded.definition_version <> v_max_version THEN
            RAISE EXCEPTION 'SR definition refused: a later version must supersede the latest version of this office'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
"""
NEW_SR_DEFINITION_INSERT_GUARD_BODY = _replace_once(
    NEW_SR_DEFINITION_INSERT_GUARD_BODY, SUCCESSOR_MARKER, SUCCESSOR_CLAUSE + SUCCESSOR_MARKER,
)

UPDATE_MARKER = "    IF NEW.role_uuid IS DISTINCT FROM OLD.role_uuid\n"
COORDINATOR_UPDATE_CLAUSE = """    IF OLD.code = 'COORDINATOR' THEN
        RAISE EXCEPTION 'SR definition refused: the Coordinator office is established at founding and cannot be changed here'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
"""
RESERVED_UPDATE_CLAUSE = """    IF starts_with(OLD.code, 'INTEVIA_') THEN
        RAISE EXCEPTION 'SR definition refused: reserved root office'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
"""
RETIRE_MARKER = "    -- (a) the retirement: the five columns set from null in this statement.\n"
BOARD_RETIRE_CLAUSE = """    IF OLD.kind = 'BOARD_OFFICE' THEN
        RAISE EXCEPTION 'SR definition refused: board offices are permanent; amend the definition instead of retiring the office'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
"""
NEW_SR_DEFINITION_UPDATE_GUARD_BODY = _replace_once(
    _replace_once(_C1.SR_DEFINITION_UPDATE_GUARD_BODY, UPDATE_MARKER,
                  COORDINATOR_UPDATE_CLAUSE + RESERVED_UPDATE_CLAUSE + UPDATE_MARKER),
    RETIRE_MARKER, RETIRE_MARKER + BOARD_RETIRE_CLAUSE,
)

BODIES = {
    "s2b_guard_office_attribution": (_C2.NEW_S2B_GUARD_OFFICE_ATTRIBUTION_BODY, NEW_S2B_GUARD_OFFICE_ATTRIBUTION_BODY),
    "sr_definition_insert_guard": (_C1.SR_DEFINITION_INSERT_GUARD_BODY, NEW_SR_DEFINITION_INSERT_GUARD_BODY),
    "sr_definition_update_guard": (_C1.SR_DEFINITION_UPDATE_GUARD_BODY, NEW_SR_DEFINITION_UPDATE_GUARD_BODY),
}
# Enabled, type, deferrable, initially deferred, function schema/name, args,
# WHEN expression and UPDATE OF columns. No trigger is created or rebound.
EXPECTED_TRIGGERS = {
    ("core_contextualroleassignmentstateevent", "s2b_10_attribution"):
        ("O", 7, False, False, "public", "s2b_guard_office_attribution", 0, None, ()),
    ("core_contextualroleassignmentstateevent", "s2b_20_assign_consent"):
        ("O", 7, False, False, "public", "s2b_guard_assign_consent", 0, None, ()),
    ("core_organismroledefinition", "sr_00_definition_insert"):
        ("O", 7, False, False, "public", "sr_definition_insert_guard", 0, None, ()),
    ("core_organismroledefinition", "sr_10_definition_update"):
        ("O", 19, False, False, "public", "sr_definition_update_guard", 0, None, ()),
    ("core_organismroledefinition", "sr_20_definition_superseded"):
        ("O", 17, True, True, "public", "sr_definition_superseded_guard", 0, None, ()),
    ("core_organismroledefinition", "sr_30_definition_citations"):
        ("O", 5, True, True, "public", "sr_definition_citations_guard", 0, None, ()),
}


def _refuse(message):
    """Stop without deleting or repairing records to make the migration pass."""
    raise RuntimeError("SR 0053 " + message)


def _check_bodies(cursor, index, phase):
    """Compare each complete installed body, including unchanged cooperating guards."""
    expected = {name: pair[index] for name, pair in BODIES.items()}
    expected.update({
        "s2b_guard_assign_consent": _C2.NEW_S2B_GUARD_ASSIGN_CONSENT_BODY,
        "sr_definition_superseded_guard": _C1.SR_DEFINITION_SUPERSEDED_GUARD_BODY,
        "sr_definition_citations_guard": _C1.SR_DEFINITION_CITATIONS_GUARD_BODY,
    })
    for name, body in expected.items():
        if _C2._installed_body(cursor, name) != body:
            _refuse(f"{phase} refused: the installed body of {name} differs; reconcile the database before retrying")


def _check_bindings(cursor, phase):
    """Check enabled trigger bindings, not merely names or a total."""
    cursor.execute("""
        SELECT c.relname, t.tgname, t.tgenabled, t.tgtype, t.tgdeferrable, t.tginitdeferred,
               pn.nspname, p.proname, t.tgnargs, pg_get_expr(t.tgqual, t.tgrelid),
               ARRAY(SELECT a.attname FROM pg_catalog.pg_attribute a
                     WHERE a.attrelid=t.tgrelid AND a.attnum=ANY(t.tgattr::smallint[]) ORDER BY a.attnum)
          FROM pg_catalog.pg_trigger t
          JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
          JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
          JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
          JOIN pg_catalog.pg_namespace pn ON pn.oid=p.pronamespace
         WHERE n.nspname='public' AND NOT t.tgisinternal
           AND c.relname IN ('core_contextualroleassignmentstateevent','core_organismroledefinition')
    """)
    observed = {(row[0], row[1]): tuple(row[2:-1]) + (tuple(row[-1]),) for row in cursor.fetchall()}
    wrong = [key for key, value in EXPECTED_TRIGGERS.items() if observed.get(key) != value]
    if wrong:
        _refuse(f"{phase} refused: office trigger bindings differ at {wrong}; reconcile them before retrying")


def _protected_rows(cursor):
    """Count rows that make rollback unsafe; founding-only Coordinator v1 is retained."""
    cursor.execute("""
        SELECT
          (SELECT count(*) FROM public.core_organismroledefinition d
            WHERE d.scope='LIVING_ORGANISM'
              AND ((d.code <> 'COORDINATOR' AND d.kind IS NOT NULL)
                   OR (d.code='COORDINATOR' AND d.definition_version > 1))),
          (SELECT count(*) FROM public.core_contextualroleassignment a
            JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
            WHERE d.scope='LIVING_ORGANISM' AND d.code <> 'COORDINATOR')
    """)
    return cursor.fetchone()


def _change(schema_editor, reverse):
    """Preflight all guards, replace three bodies atomically, then check restoration."""
    if schema_editor.connection.vendor != "postgresql":
        _refuse("refused: these office safeguards require PostgreSQL")
    before, after = (1, 0) if reverse else (0, 1)
    phase = "reverse" if reverse else "forward"
    with schema_editor.connection.cursor() as cursor:
        _check_bodies(cursor, before, phase + " preflight")
        _check_bindings(cursor, phase + " preflight")
        if reverse:
            definitions, assignments = _protected_rows(cursor)
            if definitions or assignments:
                _refuse(f"reverse refused: {definitions} protected definition(s) and {assignments} non-Coordinator office assignment(s) remain; no records were removed; keep these safeguards installed")
        count = _C2._non_internal_trigger_count(cursor)
        for name, pair in BODIES.items():
            _C2._replace_function(cursor, name, pair[after])
        _check_bodies(cursor, after, phase + " result")
        _check_bindings(cursor, phase + " result")
        if _C2._non_internal_trigger_count(cursor) != count:
            _refuse(f"{phase} refused: the public trigger total changed unexpectedly; the migration must roll back")


def install_guards(apps, schema_editor):
    """Add the database's own office protections without changing stored records."""
    _change(schema_editor, False)


def restore_guards(apps, schema_editor):
    """Restore the exact earlier bodies only when the bounded reverse check permits it."""
    _change(schema_editor, True)


class Migration(migrations.Migration):
    """Keep preflight, body replacements and postflight in one transaction."""
    atomic = True
    dependencies = [("core", PREVIOUS_MIGRATION)]
    operations = [migrations.RunPython(install_guards, restore_guards)]
