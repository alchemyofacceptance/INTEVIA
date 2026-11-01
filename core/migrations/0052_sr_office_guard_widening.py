"""SR chunk C2: the widened office guards.

Design: SR_COMMISSION_OTHER_OFFICES_v0_7.md (adopted 15 October 2026) section E class B
items 1-7, section D.7 and ruling 4; the Lead Designer's plan LD_RETURN_SR_STAGE0_v1_0.md
section 6 row SR-C2 and its amendments v1.1 (section 5.9: 0052's forward preflight, clean
reverse and populated-reverse refusal) and v1.2. Commission 00_COMMISSION_LD_SR_C2_v1_0.md
(US015-B-35, 27 October 2026) as amended by the base addendum v1.2 (US015-B-36, 30 October
2026). Human Governor: Carmian Owen.

What this migration changes, in one atomic transaction, by replacing two function bodies in
place (``CREATE OR REPLACE FUNCTION``): no trigger is created, dropped or rebound, so the
non-internal trigger count must equal its observed value before this migration.
The check does not require a hard-coded total of 483.

1. ``s2b_guard_office_attribution`` (0036's function as 0044 installed it; trigger
   ``s2b_10_attribution`` BEFORE INSERT on ``core_contextualroleassignmentstateevent``):
   - the gate (installed lines 19-22) no longer returns early for non-COORDINATOR codes:
     every LO-scoped code is guarded; CIRCLE-scoped assignments stay S4-C's and return as
     before (section E item 1);
   - at the head of the widened path an ``INTEVIA_`` code is refused: "reserved root office"
     (ruling 4; P2/GENESIS adds its own branch here when it lands);
   - in the PROPOSE branch (installed 23-30), two additions (section E item 5): a ``CUSTOM``
     definition requires ``CUSTOM_ROLES`` effective at ``NEW.received_at``
     (``s015_entitlement_effective``, 0046/0047, the same gate ``sr_00_definition_insert``
     applies at the definition), else "custom role entitlement absent at receipt"; and the
     definition named must be the active version, else "superseded definition";
   - in the ASSIGN path of the general branch (installed 80-91), one addition (section E
     item 5, H48): refuse another assignment of this membership and LO-scoped code whose
     cached state is ACTIVE, or whose history folds to ACTIVE at this event's effective_at
     and database-assigned recorded_at. The fold sees earlier writes in the transaction
     without requiring their caches to have been refreshed. The cached-state alternative
     preserves the LD candidate's refusals. The existing owning-organism lock serialises
     writers; membership, organism, code and LO-scope bindings are retained (lesson 1).
     This Advisor correction requires PostgreSQL 17 qualification, including delayed refresh;
   - the founding self-ASSIGN branch (installed 32-79) and the founder-protection block
     (installed 96-141) are kept verbatim; each carries its own ``v_role_code = 'COORDINATOR'``
     and stays Coordinator-only by its own condition (section E items 2-3; O-10 untouched);
   - the general branch's attribution check and the decision check (installed 142-163) now
     apply to every LO-scoped code, unchanged in text (section E item 4).
2. ``s2b_guard_assign_consent`` (0037:124-215; trigger ``s2b_20_assign_consent``): the gate
   at 0037:139-143 drops its code term - an LO-scoped ASSIGN by the LO needs the nominee's
   current consent whatever the code; CIRCLE scope still returns (section E item 1).
   ``s2b_guard_office_consent`` has no code gate and is untouched.

Forward preflight (before any mutation; lesson 3): ``pg_proc.prosrc`` of each function equals
its installed body byte for byte - 0044's ``NEW_S2B_GUARD_OFFICE_ATTRIBUTION_BODY`` (9,070 B)
and 0037's ``ASSIGN_CONSENT_SQL`` body - and the two triggers' bindings (enabled state, tgtype,
deferrability, function, WHEN clause, UPDATE OF columns) are as 0036 and 0037 created them.

Reverse, clean state (no office-chain row whose definition is LO-scoped and not COORDINATOR):
each body is asserted to be SR's and restored to the installed one exactly. Reverse, populated
state: refused with the count of non-COORDINATOR LO-scoped office rows the installed bodies
would leave unguarded; nothing is deleted to permit a rollback (the Advisor's A-9 principle).

Primary messages this migration adds: ``S2B office attribution refused: reserved root office``,
``S2B office attribution refused: custom role entitlement absent at receipt``,
``S2B office attribution refused: superseded definition``,
``S2B office attribution refused: office already held``. The installed messages are unchanged.

Historical boundary at this chunk's landing: the authority provider still refused
non-Coordinator acts and the role service reported its implementation limit IL-8
(the then-unbuilt office route). Chunks C4 and C5 subsequently opened those routes.
The old test-only suspension helper is not a production permission route and is
not evidence that an act under examination passes these database guards."""

from importlib import import_module

from django.db import migrations


MIGRATION = "0052_sr_office_guard_widening"
PREVIOUS_MIGRATION = "0051_sr_role_definitions"

_SB2 = import_module("core.migrations.0044_sb2_birthing_snapshot")
_S2B_CONSENT = import_module("core.migrations.0037_s2b_office_appointment_consent")

EVENT_TABLE = "core_contextualroleassignmentstateevent"
ATTRIBUTION_FUNCTION = "s2b_guard_office_attribution"
CONSENT_FUNCTION = "s2b_guard_assign_consent"
ATTRIBUTION_TRIGGER = "s2b_10_attribution"
CONSENT_TRIGGER = "s2b_20_assign_consent"

# ---------------------------------------------------------------------------------------------
# The installed bodies: 0044's constant for attribution; 0037's body sliced from the statement
# that created it (the text between ``AS $$`` and ``$$;``), the way 0044 read 0036's.
# ---------------------------------------------------------------------------------------------
PREVIOUS_S2B_GUARD_OFFICE_ATTRIBUTION_BODY = _SB2.NEW_S2B_GUARD_OFFICE_ATTRIBUTION_BODY


def _installed_assign_consent_body():
    """Extract the earlier consent guard body from migration 0037. This supplies the expected baseline; it neither queries nor changes the live database."""
    source = _S2B_CONSENT.ASSIGN_CONSENT_SQL
    function_start = source.index("CREATE FUNCTION public.s2b_guard_assign_consent()")
    body_start = source.index("AS $$", function_start) + len("AS $$")
    body_end = source.index("$$;", body_start)
    return source[body_start:body_end]


PREVIOUS_S2B_GUARD_ASSIGN_CONSENT_BODY = _installed_assign_consent_body()

if len(PREVIOUS_S2B_GUARD_OFFICE_ATTRIBUTION_BODY.encode("utf-8")) != 9070:
    raise RuntimeError("0044's installed office attribution body is not the 9,070-byte body SR C2 widens")


def _replace_once(body, marker, replacement, what):
    """Build the widened guard text only when its expected fragment occurs exactly once. Refuse a changed baseline instead of guessing where to edit; no database write occurs here."""
    if body.count(marker) != 1:
        raise RuntimeError(f"SR 0052: {what} is not unique in the installed body")
    return body.replace(marker, replacement, 1)


# --- s2b_guard_office_attribution ---------------------------------------------------------------

_ATTRIBUTION_DECLARATION_MARKER = (
    "                v_founding_event_uuid uuid; v_founding_authority_decision_reference text;\n"
)
_ATTRIBUTION_DECLARATION_REPLACEMENT = (
    "                v_founding_event_uuid uuid; v_founding_authority_decision_reference text;\n"
    "                v_role_kind text; v_role_active boolean; v_membership_id bigint;\n"
)

_ATTRIBUTION_SELECT_MARKER = (
    "        SELECT lo.id, lo.organism_id, lo.founding_insert_xid, m.membership_uuid,\n"
    "                     m.identity_id, d.role_uuid, d.code, d.scope\n"
    "            INTO v_lo_id, v_lo_uuid, v_founding_xid, v_membership_uuid,\n"
    "                     v_membership_identity_id, v_role_uuid, v_role_code, v_role_scope\n"
)
_ATTRIBUTION_SELECT_REPLACEMENT = (
    "        SELECT lo.id, lo.organism_id, lo.founding_insert_xid, m.membership_uuid,\n"
    "                     m.identity_id, d.role_uuid, d.code, d.scope,\n"
    "                     d.kind, d.active, m.id\n"
    "            INTO v_lo_id, v_lo_uuid, v_founding_xid, v_membership_uuid,\n"
    "                     v_membership_identity_id, v_role_uuid, v_role_code, v_role_scope,\n"
    "                     v_role_kind, v_role_active, v_membership_id\n"
)

# Section E item 1 (the gate) and item 5 (the reserved-root refusal at the head of the widened path).
_ATTRIBUTION_GATE_MARKER = (
    "    IF v_role_code IS DISTINCT FROM 'COORDINATOR'\n"
    "       OR v_role_scope IS DISTINCT FROM 'LIVING_ORGANISM' THEN\n"
    "        RETURN NEW;\n"
    "    END IF;\n"
)
_ATTRIBUTION_GATE_REPLACEMENT = (
    "    IF v_role_scope IS DISTINCT FROM 'LIVING_ORGANISM' THEN\n"
    "        RETURN NEW;\n"
    "    END IF;\n"
    "    IF starts_with(v_role_code, 'INTEVIA_') THEN\n"
    "        RAISE EXCEPTION 'S2B office attribution refused: reserved root office'\n"
    "            USING ERRCODE = 'integrity_constraint_violation';\n"
    "    END IF;\n"
)

# Section E item 5, the PROPOSE branch: the CUSTOM entitlement at receipt; the active version.
_ATTRIBUTION_PROPOSE_MARKER = (
    "            RAISE EXCEPTION 'S2B office attribution refused: PROPOSE must be the proposers act'\n"
    "                USING ERRCODE = 'integrity_constraint_violation';\n"
    "        END IF;\n"
    "    ELSIF NEW.action IN ('ASSIGN', 'END', 'REASSIGN') THEN\n"
)
_ATTRIBUTION_PROPOSE_REPLACEMENT = (
    "            RAISE EXCEPTION 'S2B office attribution refused: PROPOSE must be the proposers act'\n"
    "                USING ERRCODE = 'integrity_constraint_violation';\n"
    "        END IF;\n"
    "        IF v_role_kind = 'CUSTOM'\n"
    "           AND NOT COALESCE(public.s015_entitlement_effective(v_lo_uuid, 'CUSTOM_ROLES', NEW.received_at), FALSE) THEN\n"
    "            RAISE EXCEPTION 'S2B office attribution refused: custom role entitlement absent at receipt'\n"
    "                USING ERRCODE = 'integrity_constraint_violation';\n"
    "        END IF;\n"
    "        IF v_role_active IS DISTINCT FROM TRUE THEN\n"
    "            RAISE EXCEPTION 'S2B office attribution refused: superseded definition'\n"
    "                USING ERRCODE = 'integrity_constraint_violation';\n"
    "        END IF;\n"
    "    ELSIF NEW.action IN ('ASSIGN', 'END', 'REASSIGN') THEN\n"
)

# Section E item 5, the ASSIGN path of the general branch: the office is not already held (H48).
_ATTRIBUTION_GENERAL_MARKER = (
    "            RAISE EXCEPTION 'S2B office attribution refused: LO decision attribution required'\n"
    "                USING ERRCODE = 'integrity_constraint_violation';\n"
    "        END IF;\n"
    "    ELSE\n"
)
_ATTRIBUTION_GENERAL_REPLACEMENT = (
    "            RAISE EXCEPTION 'S2B office attribution refused: LO decision attribution required'\n"
    "                USING ERRCODE = 'integrity_constraint_violation';\n"
    "        END IF;\n"
    "        IF NEW.action = 'ASSIGN' AND EXISTS (\n"
    "            SELECT 1\n"
    "              FROM public.core_contextualroleassignment held\n"
    "              JOIN public.core_organismroledefinition held_definition\n"
    "                ON held_definition.id = held.role_definition_id\n"
    "             WHERE held.membership_id = v_membership_id\n"
    "               AND held.id <> NEW.assignment_id\n"
    "               AND (held.state = 'ACTIVE' OR public.s015_fold_effective_state(\n"
    "                   'core_contextualroleassignment', held.id,\n"
    "                   NEW.effective_at, NEW.recorded_at\n"
    "               ) = 'ACTIVE')\n"
    "               AND held_definition.living_organism_id = v_lo_id\n"
    "               AND held_definition.code = v_role_code\n"
    "               AND held_definition.scope = 'LIVING_ORGANISM'\n"
    "        ) THEN\n"
    "            RAISE EXCEPTION 'S2B office attribution refused: office already held'\n"
    "                USING ERRCODE = 'integrity_constraint_violation';\n"
    "        END IF;\n"
    "    ELSE\n"
)

NEW_S2B_GUARD_OFFICE_ATTRIBUTION_BODY = PREVIOUS_S2B_GUARD_OFFICE_ATTRIBUTION_BODY
for _marker, _replacement, _what in (
    (_ATTRIBUTION_DECLARATION_MARKER, _ATTRIBUTION_DECLARATION_REPLACEMENT, "the declaration"),
    (_ATTRIBUTION_SELECT_MARKER, _ATTRIBUTION_SELECT_REPLACEMENT, "the context SELECT"),
    (_ATTRIBUTION_GATE_MARKER, _ATTRIBUTION_GATE_REPLACEMENT, "the COORDINATOR gate"),
    (_ATTRIBUTION_PROPOSE_MARKER, _ATTRIBUTION_PROPOSE_REPLACEMENT, "the PROPOSE branch"),
    (_ATTRIBUTION_GENERAL_MARKER, _ATTRIBUTION_GENERAL_REPLACEMENT, "the general branch"),
):
    NEW_S2B_GUARD_OFFICE_ATTRIBUTION_BODY = _replace_once(
        NEW_S2B_GUARD_OFFICE_ATTRIBUTION_BODY, _marker, _replacement, _what
    )

# The two Coordinator-only conditions SR keeps verbatim (section E items 2-3; founder protection O-10).
_FOUNDING_SELF_ASSIGN_CONDITION = "           AND v_role_code = 'COORDINATOR'\n"
_FOUNDER_PROTECTION_CONDITION = (
    "    IF NEW.action IN ('END', 'REASSIGN')\n"
    "       AND v_role_code = 'COORDINATOR'\n"
    "       AND v_role_scope = 'LIVING_ORGANISM'\n"
)
for _kept, _what in (
    (_FOUNDING_SELF_ASSIGN_CONDITION, "the founding self-ASSIGN branch's Coordinator condition"),
    (_FOUNDER_PROTECTION_CONDITION, "the founder-protection block's Coordinator condition"),
):
    if NEW_S2B_GUARD_OFFICE_ATTRIBUTION_BODY.count(_kept) != 1:
        raise RuntimeError(f"SR 0052: {_what} is not kept verbatim")

# --- s2b_guard_assign_consent -------------------------------------------------------------------

_CONSENT_GATE_MARKER = (
    "    IF v_assignment_uuid IS NOT NULL\n"
    "       AND (role_code IS DISTINCT FROM 'COORDINATOR'\n"
    "            OR role_scope IS DISTINCT FROM 'LIVING_ORGANISM') THEN\n"
    "        RETURN NEW;\n"
    "    END IF;\n"
)
_CONSENT_GATE_REPLACEMENT = (
    "    IF v_assignment_uuid IS NOT NULL\n"
    "       AND role_scope IS DISTINCT FROM 'LIVING_ORGANISM' THEN\n"
    "        RETURN NEW;\n"
    "    END IF;\n"
)
NEW_S2B_GUARD_ASSIGN_CONSENT_BODY = _replace_once(
    PREVIOUS_S2B_GUARD_ASSIGN_CONSENT_BODY, _CONSENT_GATE_MARKER, _CONSENT_GATE_REPLACEMENT,
    "the consent guard's COORDINATOR gate",
)

BODIES = {
    ATTRIBUTION_FUNCTION: (PREVIOUS_S2B_GUARD_OFFICE_ATTRIBUTION_BODY, NEW_S2B_GUARD_OFFICE_ATTRIBUTION_BODY),
    CONSENT_FUNCTION: (PREVIOUS_S2B_GUARD_ASSIGN_CONSENT_BODY, NEW_S2B_GUARD_ASSIGN_CONSENT_BODY),
}

# pg_trigger contract of the two triggers as 0036 and 0037 created them: enabled 'O'; tgtype 7 =
# ROW(1) | BEFORE(2) | INSERT(4); not deferrable; the function; no WHEN; no UPDATE OF columns.
EXPECTED_TRIGGERS = {
    ATTRIBUTION_TRIGGER: ("O", 7, False, False, "public", ATTRIBUTION_FUNCTION, 0, None, ()),
    CONSENT_TRIGGER: ("O", 7, False, False, "public", CONSENT_FUNCTION, 0, None, ()),
}


def _refuse(message):
    """Stop office guard migration 0052 with a labelled reason. No data is removed to make the migration succeed."""
    raise RuntimeError(f"SR 0052 {message}")


def _installed_body(cursor, function_name):
    """Read the body of exactly one named public database function. Refuse absence or ambiguity; do not substitute a function with a similar name."""
    cursor.execute(
        """
        SELECT p.prosrc
          FROM pg_catalog.pg_proc p
          JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public' AND p.proname = %s
        """,
        [function_name],
    )
    rows = cursor.fetchall()
    if len(rows) != 1:
        _refuse(f"preflight failed: {len(rows)} functions named public.{function_name}, expected one")
    return rows[0][0]


def _assert_body(cursor, function_name, expected_body, phase):
    """Byte for byte (lesson 3): not names, not literal sets."""
    installed = _installed_body(cursor, function_name)
    if installed != expected_body:
        _refuse(
            f"{phase} failed: public.{function_name} body differs from the expected body "
            f"(installed {len(installed.encode('utf-8'))} B, expected {len(expected_body.encode('utf-8'))} B)"
        )


def _event_triggers(cursor):
    """Read office-event trigger bindings, enabled states and conditions. This is read-only evidence for the caller, not a repair operation."""
    cursor.execute(
        """
        SELECT t.tgname, t.tgenabled, t.tgtype, t.tgdeferrable, t.tginitdeferred,
               pn.nspname, p.proname, t.tgnargs, pg_get_expr(t.tgqual, t.tgrelid),
               ARRAY(SELECT a.attname FROM pg_catalog.pg_attribute a
                      WHERE a.attrelid = t.tgrelid AND a.attnum = ANY(t.tgattr::smallint[])
                      ORDER BY a.attnum)
          FROM pg_catalog.pg_trigger t
          JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid
          JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
          JOIN pg_catalog.pg_proc p ON p.oid = t.tgfoid
          JOIN pg_catalog.pg_namespace pn ON pn.oid = p.pronamespace
         WHERE n.nspname = 'public' AND c.relname = %s AND NOT t.tgisinternal
        """,
        [EVENT_TABLE],
    )
    return {row[0]: tuple(row[1:-1]) + (tuple(row[-1]),) for row in cursor.fetchall()}


def _assert_trigger_bindings(cursor, phase):
    """Presence alone is insufficient: a disabled or rebound guard is not the installed contract."""
    observed = _event_triggers(cursor)
    wrong = [name for name, contract in EXPECTED_TRIGGERS.items() if observed.get(name) != contract]
    if wrong:
        _refuse(f"{phase} failed: trigger bindings or enabled states differ from 0036/0037: {wrong}")


def _non_internal_trigger_count(cursor):
    """Count public triggers that are not database-internal. This detects a changed total; it does not prove that any particular guard works."""
    cursor.execute(
        """
        SELECT count(*) FROM pg_catalog.pg_trigger t
          JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid
          JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND NOT t.tgisinternal
        """
    )
    return cursor.fetchone()[0]


def _replace_function(cursor, name, body):
    """Replace one guard function body while retaining its existing trigger bindings. This helper relies on its caller to check the baseline and use an atomic transaction."""
    cursor.execute(
        f"CREATE OR REPLACE FUNCTION public.{name}() RETURNS trigger LANGUAGE plpgsql AS $${body}$$"
    )


def _unguarded_office_rows(cursor):
    """The office rows the installed bodies would leave unguarded: every state event on an
    assignment whose definition is LO-scoped and not COORDINATOR, and the assignments themselves."""
    cursor.execute(
        f"""
        SELECT count(DISTINCT a.id), count(e.id)
          FROM public.core_contextualroleassignment a
          JOIN public.core_organismroledefinition d ON d.id = a.role_definition_id
          LEFT JOIN public.{EVENT_TABLE} e ON e.assignment_id = a.id
         WHERE d.scope = 'LIVING_ORGANISM' AND d.code IS DISTINCT FROM 'COORDINATOR'
        """
    )
    return cursor.fetchone()


def widen_guards(apps, schema_editor):
    """Forward: exact preflight, then both bodies replaced in place; the trigger count is unchanged."""
    if schema_editor.connection.vendor != "postgresql":
        _refuse("forward preflight failed: PostgreSQL is the only substrate")
    with schema_editor.connection.cursor() as cursor:
        for name, (previous, _new) in BODIES.items():
            _assert_body(cursor, name, previous, "forward preflight")
        _assert_trigger_bindings(cursor, "forward preflight")
        before = _non_internal_trigger_count(cursor)
        for name, (_previous, new) in BODIES.items():
            _replace_function(cursor, name, new)
        for name, (_previous, new) in BODIES.items():
            _assert_body(cursor, name, new, "forward install")
        _assert_trigger_bindings(cursor, "forward install")
        after = _non_internal_trigger_count(cursor)
        if after != before:
            _refuse(f"forward install failed: the trigger count moved from {before} to {after}")


def restore_guards(apps, schema_editor):
    """Reverse: refuse a populated state with the count; otherwise restore both installed bodies exactly."""
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        assignments, events = _unguarded_office_rows(cursor)
        if assignments or events:
            _refuse(
                "reverse refused: the database holds office rows the installed bodies would leave "
                f"unguarded and nothing is deleted to permit a rollback - {assignments} non-COORDINATOR "
                f"LO-scoped assignment(s) carrying {events} state event(s)"
            )
        for name, (_previous, new) in BODIES.items():
            _assert_body(cursor, name, new, "reverse preflight")
        _assert_trigger_bindings(cursor, "reverse preflight")
        for name, (previous, _new) in BODIES.items():
            _replace_function(cursor, name, previous)
        for name, (previous, _new) in BODIES.items():
            _assert_body(cursor, name, previous, "reverse restoration")
        _assert_trigger_bindings(cursor, "reverse restoration")


class Migration(migrations.Migration):
    """Widen office decision and consent guards in one atomic database change.
    
    Use the checked forward and reverse functions; no appointment is made here."""
    atomic = True

    dependencies = [("core", PREVIOUS_MIGRATION)]

    operations = [
        migrations.RunPython(widen_guards, restore_guards),
    ]
