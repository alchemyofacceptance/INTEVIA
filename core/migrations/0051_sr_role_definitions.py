"""SR chunk C1: the role-definition schema, its guards and the one writer's contract.

Design: SR_COMMISSION_OTHER_OFFICES_v0_7.md (adopted 15 October 2026) sections C, D.1,
D.2, D.3 and G; the Lead Designer's plan LD_RETURN_SR_STAGE0_v1_0.md section 6 row SR-C1
and its amendments v1.1 (section 5.9: clean-install and populated-state behaviour stated
separately; the SR-C1 row at l.209) and v1.2; the Advisor's A-3 (founding must survive C1)
and A-9 (populated reversal refused). Commission 00_COMMISSION_LD_SR_C1_v1_0.md
(US015-B-35, 26 October 2026). Human Governor: Carmian Owen.

Advisor amendment, 27 October 2026: successor scope/family/attribution checked;
every provision insert bound to a required relationship; preflight compares the
complete intake CHECK and the installed 0021 trigger bindings and enabled states.
Prepared by static review; no Advisor database execution.

What this migration installs, in one atomic transaction:

1. The section D.1 columns on ``core_organismroledefinition`` - nullable at install, no
   backfill: the rows founding wrote before SR keep NULL in every new column and stay as
   they are (``participation_required`` carries no column default for the same reason).
2. ``core_organismroledefinitionprovision`` (section D.2), append-only: ``recorded_at`` is
   database-assigned by ``s015_sr_provision_assign_time``; UPDATE, DELETE and TRUNCATE are refused
   through ``s015_refuse_row_mutation`` (0019). The provision table's four triggers are
   named in the ``s015_sr_*`` family (0049's ``s015_h100_*`` precedent): the S015 test
   backend's reset admits a truncate guardian only under the ``s015_`` prefix.
3. The definition guards (section D.3):
   - ``sr_00_definition_insert`` BEFORE INSERT: every LO-scoped row written after this
     migration carries the content and attribution columns, the closed domains, a version
     one above the (lo, code) maximum, the ``CUSTOM`` entitlement at ``received_at``, and
     a QUALIFIED decision that matches the row. Two decision branches and no other:
     (F) the founding branch of the Advisor's A-3 - ``code = 'COORDINATOR'``,
     ``kind = 'COORDINATOR'``, ``definition_version = 1`` and the decision QUALIFIED for
     ``FOUND_LIVING_ORGANISM`` with ``actor_id = NEW.enterer_id``,
     ``evaluated_at = NEW.received_at`` and ``target->>'proposed_organism_uuid'`` naming
     this organism; (D) the definition-act branch - the decision QUALIFIED for
     ``RECORD_ROLE_DEFINITION`` with the same actor and time match and a target naming the
     organism, code, version, kind, nomination policy and appointing authority (the
     installed 0036/0044 pattern), under which an ``INTEVIA_`` code is refused (ruling 4).
     ``recorded_at`` is assigned here from ``statement_timestamp()``; a client value is
     refused (0037's pattern).
   - ``sr_10_definition_update`` BEFORE UPDATE: the only permitted change is
     ``active`` TRUE -> FALSE with every other column unchanged, either (a) the
     retirement - the five retirement columns set from NULL in this statement,
     ``retired_recorded_at`` assigned here, the retiring decision QUALIFIED for
     ``RECORD_ROLE_DEFINITION`` with ``event_action = 'RETIRE'`` naming this
     ``role_uuid``, and no assignment under any version of (lo, code) folding other than
     ENDED at record time - or (b) an AMEND's supersession, retirement columns still NULL.
   - ``sr_20_definition_superseded`` AFTER UPDATE, deferred: a row deactivated under (b)
     must have a later SR-attributed, LO-scoped successor of the same (lo, code)
     whose ``supersedes`` names it at commit.
   - ``sr_30_definition_citations`` AFTER INSERT, deferred: the provision rows the
     version's columns require are present, and no other.
   Scope reading, stated for the Vision Chamber's check: the four guards apply to
   ``scope = 'LIVING_ORGANISM'`` rows; CIRCLE-scoped rows are S4-C's (v0.7 section E
   item 1's principle - "every LO-scoped code is guarded; CIRCLE stays S4-C's"). The
   update-side guards apply to every LO-scoped row; ``sr_20``'s successor requirement and
   ``sr_30``'s citation requirement are stated for SR-attributed rows (``enterer_id`` /
   ``kind`` set), since a pre-SR row has no SR lineage to complete.
4. The intake CHECK ``h100_intake_entry_valid`` replaced with the set widened by
   ``RECORD_ROLE_DEFINITION`` (0049's constraint dropped and re-created by Django from the
   model; the entry is not admitted until chunk C3).

Forward preflight (before any mutation): the definition table has none of the SR columns;
the provision table does not exist; no trigger or function named ``sr_*`` or ``s015_sr_*``
exists; the intake CHECK
exists with exactly 0049's validated entry expression (read from ``pg_constraint``);
0021's five column-immutability triggers, its delete and truncate triggers and its
reserved-code constraint trigger are enabled with their original bindings on the definition table.

Reverse, clean state (no definition row with an SR column set, no provision row, no intake
row with the new entry): the eight triggers and five functions are dropped, Django drops the
table, the columns and the constraints and re-creates 0049's CHECK; the final step verifies
the restoration exactly (no SR column, no provision table, no ``sr_*`` trigger or function,
the CHECK back at 0049's expression). Reverse, populated state: refused with a reason naming
the rows (the Advisor's A-9) - nothing is deleted to permit a rollback.

Primary messages this migration adds: the ``SR definition refused: ...`` family and
``SR provision refused: ...``; ``s015_refuse_row_mutation``'s installed message on the
provision table.
"""

from importlib import import_module
import re

from django.db import migrations, models
import django.db.models.deletion
import uuid


MIGRATION = "0051_sr_role_definitions"
PREVIOUS_MIGRATION = "0050_clk_mono_1_cache_coordinate_detail"

_H100 = import_module("core.migrations.0049_h100_command_intake")
PREVIOUS_ENTRY_VALUES = tuple(_H100.ENTRY_VALUES)
if len(PREVIOUS_ENTRY_VALUES) != 22:
    raise RuntimeError("0049's ENTRY_VALUES is the 22-entry set this migration widens")
NEW_ENTRY = "RECORD_ROLE_DEFINITION"
ENTRY_VALUES = PREVIOUS_ENTRY_VALUES + (NEW_ENTRY,)

ENTRY_CHOICES = list(_H100.ENTRY_CHOICES) + [(NEW_ENTRY, "Record role definition")]

DEFINITION_TABLE = "core_organismroledefinition"
PROVISION_TABLE = "core_organismroledefinitionprovision"
INTAKE_TABLE = "core_commandintake"
INTAKE_CHECK = "h100_intake_entry_valid"

SR_DEFINITION_COLUMNS = (
    "kind",
    "appointing_authority",
    "appointing_body_reference",
    "alternative_route_reference",
    "nomination_basis",
    "nomination_policy",
    "nomination_dependency",
    "participation_required",
    "ex_officio_seat_reference",
    "supersedes_id",
    "enterer_id",
    "enterer_access_epoch",
    "authority_decision_reference",
    "occurred_at",
    "effective_at",
    "received_at",
    "recorded_at",
    "retired_by_decision_reference",
    "retired_effective_at",
    "retired_received_at",
    "retired_recorded_at",
    "retirement_reason",
)

# 0021's triggers on the definition table that must be in place (section D.3: SR adds no
# delete or truncate trigger of its own).
INSTALLED_0021_TRIGGERS = (
    "s015_0021_organismroledefinition_role_uuid_immutable",
    "s015_0021_organismroledefinition_living_organism_id_immutable",
    "s015_0021_organismroledefinition_code_immutable",
    "s015_0021_organismroledefinition_scope_immutable",
    "s015_0021_organismroledefinition_definition_version_immutable",
    "s015_0021_organismroledefinition_delete_immutable",
    "s015_0021_organismroledefinition_truncate_immutable",
    "s015_0021_organismroledefinition_reserved_code",
)

SR_FUNCTIONS = (
    "sr_definition_insert_guard",
    "sr_definition_update_guard",
    "sr_definition_superseded_guard",
    "sr_definition_citations_guard",
    "sr_provision_insert_guard",
)

# (trigger name, table) - the eight triggers this migration creates.
SR_TRIGGERS = (
    ("sr_00_definition_insert", DEFINITION_TABLE),
    ("sr_10_definition_update", DEFINITION_TABLE),
    ("sr_20_definition_superseded", DEFINITION_TABLE),
    ("sr_30_definition_citations", DEFINITION_TABLE),
    ("s015_sr_provision_assign_time", PROVISION_TABLE),
    ("s015_sr_provision_update_immutable", PROVISION_TABLE),
    ("s015_sr_provision_delete_immutable", PROVISION_TABLE),
    ("s015_sr_provision_truncate_immutable", PROVISION_TABLE),
)

# The guard bodies, kept as module constants so a later migration can import and assert them
# (0044's pattern). Each function is created by name; CREATE OR REPLACE is not used forward,
# so a pre-existing function of the same name fails the migration rather than being replaced.
SR_DEFINITION_INSERT_GUARD_BODY = """
DECLARE v_lo_uuid uuid; v_max_version integer; v_decision_row record; v_superseded record;
BEGIN
    IF NEW.scope IS DISTINCT FROM 'LIVING_ORGANISM' THEN
        RETURN NEW;
    END IF;
    IF NEW.recorded_at IS NOT NULL THEN
        RAISE EXCEPTION 'SR definition refused: recorded_at is database-assigned'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    NEW.recorded_at := statement_timestamp();
    IF NEW.kind IS NULL OR NEW.appointing_authority IS NULL OR NEW.nomination_basis IS NULL
       OR NEW.nomination_policy IS NULL OR NEW.participation_required IS NULL
       OR NEW.enterer_id IS NULL OR NEW.enterer_access_epoch IS NULL
       OR NEW.authority_decision_reference IS NULL
       OR NEW.occurred_at IS NULL OR NEW.effective_at IS NULL OR NEW.received_at IS NULL THEN
        RAISE EXCEPTION 'SR definition refused: content or attribution incomplete'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.kind NOT IN ('COORDINATOR', 'BOARD_OFFICE', 'CUSTOM')
       OR NEW.appointing_authority NOT IN ('LO_COORDINATOR_BODY', 'NAMED_BODY')
       OR NEW.nomination_basis NOT IN ('CONSTITUTION', 'SILENT', 'CUSTOM')
       OR NEW.nomination_policy NOT IN ('A', 'B', 'C', 'UNSUPPORTED') THEN
        RAISE EXCEPTION 'SR definition refused: value outside its domain'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (NEW.appointing_authority = 'NAMED_BODY') <> (NEW.appointing_body_reference IS NOT NULL) THEN
        RAISE EXCEPTION 'SR definition refused: appointing body reference required iff NAMED_BODY'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (NEW.nomination_policy = 'UNSUPPORTED') <> (NEW.nomination_dependency IS NOT NULL)
       OR (NEW.nomination_policy = 'UNSUPPORTED' AND NEW.nomination_basis <> 'CONSTITUTION') THEN
        RAISE EXCEPTION 'SR definition refused: UNSUPPORTED only with a constitutional basis and its dependency'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (NEW.kind = 'CUSTOM') <> (NEW.nomination_basis = 'CUSTOM') THEN
        RAISE EXCEPTION 'SR definition refused: nomination basis CUSTOM iff kind CUSTOM'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (NEW.code = 'COORDINATOR') <> (NEW.kind = 'COORDINATOR') THEN
        RAISE EXCEPTION 'SR definition refused: kind COORDINATOR iff code COORDINATOR'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.occurred_at > NEW.received_at OR NEW.received_at > NEW.recorded_at THEN
        RAISE EXCEPTION 'SR definition refused: time order'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NOT NEW.active THEN
        RAISE EXCEPTION 'SR definition refused: a new version is recorded active'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.retired_by_decision_reference IS NOT NULL OR NEW.retired_effective_at IS NOT NULL
       OR NEW.retired_received_at IS NOT NULL OR NEW.retired_recorded_at IS NOT NULL
       OR NEW.retirement_reason IS NOT NULL THEN
        RAISE EXCEPTION 'SR definition refused: retirement columns are set only by retirement'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT lo.organism_id INTO v_lo_uuid FROM public.core_livingorganism lo WHERE lo.id = NEW.living_organism_id;
    IF v_lo_uuid IS NULL THEN
        RAISE EXCEPTION 'SR definition refused: organism missing'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT COALESCE(MAX(d.definition_version), 0) INTO v_max_version
      FROM public.core_organismroledefinition d
     WHERE d.living_organism_id = NEW.living_organism_id AND d.code = NEW.code;
    IF NEW.definition_version <> v_max_version + 1 THEN
        RAISE EXCEPTION 'SR definition refused: version must be one above the latest for this code'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.kind = 'CUSTOM'
       AND NOT COALESCE(public.s015_entitlement_effective(v_lo_uuid, 'CUSTOM_ROLES', NEW.received_at), FALSE) THEN
        RAISE EXCEPTION 'SR definition refused: custom role entitlement absent at receipt'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.supersedes_id IS NOT NULL THEN
        SELECT d.* INTO v_superseded FROM public.core_organismroledefinition d WHERE d.id = NEW.supersedes_id;
        IF v_superseded.id IS NULL
           OR v_superseded.living_organism_id <> NEW.living_organism_id
           OR v_superseded.code <> NEW.code
           OR v_superseded.scope IS DISTINCT FROM 'LIVING_ORGANISM'
           OR v_superseded.active
           OR v_superseded.retired_by_decision_reference IS NOT NULL
           OR v_superseded.retired_effective_at IS NOT NULL
           OR v_superseded.retired_received_at IS NOT NULL
           OR v_superseded.retired_recorded_at IS NOT NULL
           OR v_superseded.retirement_reason IS NOT NULL THEN
            RAISE EXCEPTION 'SR definition refused: supersedes must name the deactivated, unretired prior version of this code'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    SELECT decision.* INTO v_decision_row
      FROM public.core_authoritydecision decision
     WHERE decision.decision_reference = NEW.authority_decision_reference;
    IF v_decision_row.decision_reference IS NULL
       OR v_decision_row.outcome <> 'QUALIFIED'
       OR v_decision_row.actor_id <> NEW.enterer_id
       OR v_decision_row.evaluated_at <> NEW.received_at THEN
        RAISE EXCEPTION 'SR definition refused: governed decision does not match definition'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF v_decision_row.action = 'FOUND_LIVING_ORGANISM' THEN
        -- (F) the founding branch (Advisor A-3): the founding decision attributes version 1 of
        -- the LO's own COORDINATOR definition, and nothing else; no decision is invented at founding.
        IF NEW.code <> 'COORDINATOR' OR NEW.kind <> 'COORDINATOR' OR NEW.definition_version <> 1
           OR NEW.supersedes_id IS NOT NULL
           OR v_decision_row.command_subtype <> 'FOUND_LIVING_ORGANISM'
           OR v_decision_row.target->>'action' IS DISTINCT FROM 'FOUND_LIVING_ORGANISM'
           OR v_decision_row.target->>'proposed_organism_uuid' IS DISTINCT FROM v_lo_uuid::text THEN
            RAISE EXCEPTION 'SR definition refused: founding decision does not match definition'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF v_decision_row.action = 'RECORD_ROLE_DEFINITION' THEN
        -- (D) the definition act: the decision names this very version (the 0036/0044 pattern).
        IF starts_with(NEW.code, 'INTEVIA_') THEN
            RAISE EXCEPTION 'SR definition refused: reserved root office'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF v_decision_row.command_subtype <> 'RECORD_ROLE_DEFINITION'
           OR v_decision_row.target->>'action' IS DISTINCT FROM 'RECORD_ROLE_DEFINITION'
           OR v_decision_row.target->>'living_organism_id' IS DISTINCT FROM v_lo_uuid::text
           OR v_decision_row.target->>'code' IS DISTINCT FROM NEW.code
           OR v_decision_row.target->>'definition_version' IS DISTINCT FROM NEW.definition_version::text
           OR v_decision_row.target->>'kind' IS DISTINCT FROM NEW.kind
           OR v_decision_row.target->>'nomination_policy' IS DISTINCT FROM NEW.nomination_policy
           OR v_decision_row.target->>'appointing_authority' IS DISTINCT FROM NEW.appointing_authority THEN
            RAISE EXCEPTION 'SR definition refused: governed decision does not match definition'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'SR definition refused: governed decision does not match definition'
        USING ERRCODE = 'integrity_constraint_violation';
END
"""

SR_DEFINITION_UPDATE_GUARD_BODY = """
DECLARE v_decision_row record; v_open_assignment bigint; v_now timestamptz;
BEGIN
    IF OLD.scope IS DISTINCT FROM 'LIVING_ORGANISM' THEN
        RETURN NEW;
    END IF;
    IF NEW.role_uuid IS DISTINCT FROM OLD.role_uuid
       OR NEW.living_organism_id IS DISTINCT FROM OLD.living_organism_id
       OR NEW.code IS DISTINCT FROM OLD.code
       OR NEW.scope IS DISTINCT FROM OLD.scope
       OR NEW.definition_version IS DISTINCT FROM OLD.definition_version
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.kind IS DISTINCT FROM OLD.kind
       OR NEW.appointing_authority IS DISTINCT FROM OLD.appointing_authority
       OR NEW.appointing_body_reference IS DISTINCT FROM OLD.appointing_body_reference
       OR NEW.alternative_route_reference IS DISTINCT FROM OLD.alternative_route_reference
       OR NEW.nomination_basis IS DISTINCT FROM OLD.nomination_basis
       OR NEW.nomination_policy IS DISTINCT FROM OLD.nomination_policy
       OR NEW.nomination_dependency IS DISTINCT FROM OLD.nomination_dependency
       OR NEW.participation_required IS DISTINCT FROM OLD.participation_required
       OR NEW.ex_officio_seat_reference IS DISTINCT FROM OLD.ex_officio_seat_reference
       OR NEW.supersedes_id IS DISTINCT FROM OLD.supersedes_id
       OR NEW.enterer_id IS DISTINCT FROM OLD.enterer_id
       OR NEW.enterer_access_epoch IS DISTINCT FROM OLD.enterer_access_epoch
       OR NEW.authority_decision_reference IS DISTINCT FROM OLD.authority_decision_reference
       OR NEW.occurred_at IS DISTINCT FROM OLD.occurred_at
       OR NEW.effective_at IS DISTINCT FROM OLD.effective_at
       OR NEW.received_at IS DISTINCT FROM OLD.received_at
       OR NEW.recorded_at IS DISTINCT FROM OLD.recorded_at THEN
        RAISE EXCEPTION 'SR definition refused: a recorded version is immutable'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF OLD.retired_by_decision_reference IS NOT NULL OR OLD.retired_effective_at IS NOT NULL
       OR OLD.retired_received_at IS NOT NULL OR OLD.retired_recorded_at IS NOT NULL
       OR OLD.retirement_reason IS NOT NULL THEN
        RAISE EXCEPTION 'SR definition refused: a recorded version is immutable'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NOT (OLD.active AND NOT NEW.active) THEN
        RAISE EXCEPTION 'SR definition refused: only the active version may be deactivated, once'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.retired_by_decision_reference IS NULL AND NEW.retired_effective_at IS NULL
       AND NEW.retired_received_at IS NULL AND NEW.retired_recorded_at IS NULL
       AND NEW.retirement_reason IS NULL THEN
        -- (b) an AMEND's supersession: sr_20 requires the successor at commit.
        RETURN NEW;
    END IF;
    -- (a) the retirement: the five columns set from null in this statement.
    IF NEW.retired_by_decision_reference IS NULL OR NEW.retired_effective_at IS NULL
       OR NEW.retired_received_at IS NULL OR NEW.retirement_reason IS NULL THEN
        RAISE EXCEPTION 'SR definition refused: retirement attribution incomplete'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.retired_recorded_at IS NOT NULL THEN
        RAISE EXCEPTION 'SR definition refused: retired_recorded_at is database-assigned'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    NEW.retired_recorded_at := statement_timestamp();
    IF NEW.retired_received_at > NEW.retired_recorded_at THEN
        RAISE EXCEPTION 'SR definition refused: time order'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT decision.* INTO v_decision_row
      FROM public.core_authoritydecision decision
     WHERE decision.decision_reference = NEW.retired_by_decision_reference;
    IF v_decision_row.decision_reference IS NULL
       OR v_decision_row.outcome <> 'QUALIFIED'
       OR v_decision_row.action <> 'RECORD_ROLE_DEFINITION'
       OR v_decision_row.command_subtype <> 'RECORD_ROLE_DEFINITION'
       OR v_decision_row.evaluated_at <> NEW.retired_received_at
       OR v_decision_row.target->>'event_action' IS DISTINCT FROM 'RETIRE'
       OR v_decision_row.target->>'role_uuid' IS DISTINCT FROM OLD.role_uuid::text THEN
        RAISE EXCEPTION 'SR definition refused: retiring decision does not match definition'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    v_now := clock_timestamp();
    SELECT a.id INTO v_open_assignment
      FROM public.core_contextualroleassignment a
      JOIN public.core_organismroledefinition d ON d.id = a.role_definition_id
     WHERE d.living_organism_id = OLD.living_organism_id AND d.code = OLD.code
       AND public.s015_fold_effective_state('core_contextualroleassignment', a.id, v_now, v_now) IS DISTINCT FROM 'ENDED'
     LIMIT 1;
    IF v_open_assignment IS NOT NULL THEN
        RAISE EXCEPTION 'SR definition refused: this office still has a holder or a pending nomination'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
"""

SR_DEFINITION_SUPERSEDED_GUARD_BODY = """
BEGIN
    IF OLD.scope IS DISTINCT FROM 'LIVING_ORGANISM' THEN
        RETURN NULL;
    END IF;
    IF OLD.active AND NOT NEW.active AND NEW.retired_by_decision_reference IS NULL
       AND OLD.enterer_id IS NOT NULL
       AND NOT EXISTS (
           SELECT 1 FROM public.core_organismroledefinition s
            WHERE s.supersedes_id = NEW.id
              AND s.scope = 'LIVING_ORGANISM'
              AND s.living_organism_id = NEW.living_organism_id
              AND s.code = NEW.code
              AND s.definition_version > NEW.definition_version
              AND s.enterer_id IS NOT NULL AND s.kind IS NOT NULL
       ) THEN
        RAISE EXCEPTION 'SR definition refused: a deactivated version must be superseded by a successor at commit'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NULL;
END
"""

SR_DEFINITION_CITATIONS_GUARD_BODY = """
DECLARE v_required text[]; v_present text[]; v_rel text;
BEGIN
    IF NEW.scope IS DISTINCT FROM 'LIVING_ORGANISM' OR NEW.kind IS NULL THEN
        RETURN NULL;
    END IF;
    v_required := ARRAY[]::text[];
    IF NEW.kind = 'BOARD_OFFICE' THEN v_required := array_append(v_required, 'OFFICE_BASIS'); END IF;
    IF NEW.appointing_authority = 'NAMED_BODY' THEN v_required := array_append(v_required, 'APPOINTING_AUTHORITY'); END IF;
    IF NEW.alternative_route_reference IS NOT NULL THEN v_required := array_append(v_required, 'ALTERNATIVE_ROUTE'); END IF;
    IF NEW.nomination_basis = 'CONSTITUTION' THEN v_required := array_append(v_required, 'NOMINATION_ROUTE'); END IF;
    IF NEW.participation_required THEN v_required := array_append(v_required, 'PARTICIPATION_REQUIRED'); END IF;
    IF NEW.ex_officio_seat_reference IS NOT NULL THEN v_required := array_append(v_required, 'EX_OFFICIO_SEAT'); END IF;
    SELECT COALESCE(array_agg(p.relationship ORDER BY p.relationship), ARRAY[]::text[]) INTO v_present
      FROM public.core_organismroledefinitionprovision p WHERE p.definition_id = NEW.id;
    FOREACH v_rel IN ARRAY v_required LOOP
        IF NOT (v_rel = ANY (v_present)) THEN
            IF v_rel = 'OFFICE_BASIS' THEN
                RAISE EXCEPTION 'SR definition refused: office basis citation missing'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RAISE EXCEPTION 'SR definition refused: governance relationship citation missing (%)', v_rel
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END LOOP;
    FOREACH v_rel IN ARRAY v_present LOOP
        IF NOT (v_rel = ANY (v_required)) THEN
            RAISE EXCEPTION 'SR definition refused: citation without its relationship (%)', v_rel
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END LOOP;
    RETURN NULL;
END
"""

SR_PROVISION_INSERT_GUARD_BODY = """
DECLARE v_definition record; v_required boolean;
BEGIN
    IF NEW.recorded_at IS NOT NULL THEN
        RAISE EXCEPTION 'SR provision refused: recorded_at is database-assigned'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.provision_reference IS NULL OR btrim(NEW.provision_reference) = ''
       OR NEW.provision_version IS NULL OR btrim(NEW.provision_version) = '' THEN
        RAISE EXCEPTION 'SR provision refused: the provision and its version are required'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    -- sr_30 checks completeness when the definition is inserted. This check also
    -- covers a later INSERT into the citation table: append-only must not permit
    -- adding an unrelated citation to a version that has already committed.
    SELECT d.* INTO v_definition FROM public.core_organismroledefinition d
     WHERE d.id = NEW.definition_id;
    IF v_definition.id IS NULL OR v_definition.scope IS DISTINCT FROM 'LIVING_ORGANISM'
       OR v_definition.kind IS NULL OR v_definition.enterer_id IS NULL THEN
        RAISE EXCEPTION 'SR provision refused: an SR-attributed LO definition is required'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    v_required := CASE NEW.relationship
        WHEN 'OFFICE_BASIS' THEN v_definition.kind = 'BOARD_OFFICE'
        WHEN 'APPOINTING_AUTHORITY' THEN v_definition.appointing_authority = 'NAMED_BODY'
        WHEN 'ALTERNATIVE_ROUTE' THEN v_definition.alternative_route_reference IS NOT NULL
        WHEN 'NOMINATION_ROUTE' THEN v_definition.nomination_basis = 'CONSTITUTION'
        WHEN 'PARTICIPATION_REQUIRED' THEN v_definition.participation_required
        WHEN 'EX_OFFICIO_SEAT' THEN v_definition.ex_officio_seat_reference IS NOT NULL
        ELSE FALSE
    END;
    IF v_required IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION 'SR definition refused: citation without its relationship (%)', NEW.relationship
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    NEW.recorded_at := statement_timestamp();
    RETURN NEW;
END
"""

_FUNCTION_BODIES = {
    "sr_definition_insert_guard": SR_DEFINITION_INSERT_GUARD_BODY,
    "sr_definition_update_guard": SR_DEFINITION_UPDATE_GUARD_BODY,
    "sr_definition_superseded_guard": SR_DEFINITION_SUPERSEDED_GUARD_BODY,
    "sr_definition_citations_guard": SR_DEFINITION_CITATIONS_GUARD_BODY,
    "sr_provision_insert_guard": SR_PROVISION_INSERT_GUARD_BODY,
}

_TRIGGER_SQL = (
    f"CREATE TRIGGER sr_00_definition_insert BEFORE INSERT ON public.{DEFINITION_TABLE} "
    "FOR EACH ROW EXECUTE FUNCTION public.sr_definition_insert_guard()",
    f"CREATE TRIGGER sr_10_definition_update BEFORE UPDATE ON public.{DEFINITION_TABLE} "
    "FOR EACH ROW EXECUTE FUNCTION public.sr_definition_update_guard()",
    f"CREATE CONSTRAINT TRIGGER sr_20_definition_superseded AFTER UPDATE ON public.{DEFINITION_TABLE} "
    "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.sr_definition_superseded_guard()",
    f"CREATE CONSTRAINT TRIGGER sr_30_definition_citations AFTER INSERT ON public.{DEFINITION_TABLE} "
    "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.sr_definition_citations_guard()",
    f"CREATE TRIGGER s015_sr_provision_assign_time BEFORE INSERT ON public.{PROVISION_TABLE} "
    "FOR EACH ROW EXECUTE FUNCTION public.sr_provision_insert_guard()",
    f"CREATE TRIGGER s015_sr_provision_update_immutable BEFORE UPDATE ON public.{PROVISION_TABLE} "
    "FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation()",
    f"CREATE TRIGGER s015_sr_provision_delete_immutable BEFORE DELETE ON public.{PROVISION_TABLE} "
    "FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation()",
    f"CREATE TRIGGER s015_sr_provision_truncate_immutable BEFORE TRUNCATE ON public.{PROVISION_TABLE} "
    "FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation()",
)


def _columns(cursor, table):
    cursor.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = %s",
        [table],
    )
    return {row[0] for row in cursor.fetchall()}


def _table_exists(cursor, table):
    cursor.execute("SELECT to_regclass(%s) IS NOT NULL", [f"public.{table}"])
    return cursor.fetchone()[0]


def _sr_triggers(cursor):
    cursor.execute(
        """
        SELECT t.tgname, c.relname FROM pg_catalog.pg_trigger t
          JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid
          JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND NOT t.tgisinternal
           AND (t.tgname LIKE 'sr\\_%' OR t.tgname LIKE 's015\\_sr\\_%') ORDER BY 1
        """
    )
    return [tuple(row) for row in cursor.fetchall()]


def _sr_functions(cursor):
    cursor.execute(
        """
        SELECT p.proname FROM pg_catalog.pg_proc p
          JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public' AND (p.proname LIKE 'sr\\_%' OR p.proname LIKE 's015\\_sr\\_%') ORDER BY 1
        """
    )
    return [row[0] for row in cursor.fetchall()]


def _definition_triggers(cursor):
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
        [DEFINITION_TABLE],
    )
    return {row[0]: tuple(row[1:-1]) + (tuple(row[-1]),) for row in cursor.fetchall()}


def _assert_0021_definition_triggers(cursor, phase):
    """Presence alone is insufficient: a disabled or rebound guardian is not installed correctly."""
    observed = _definition_triggers(cursor)
    expected = {}
    prefix = "s015_0021_organismroledefinition_"
    for column in ("role_uuid", "living_organism_id", "code", "scope", "definition_version"):
        # pg_trigger.tgtype: ROW(1) | BEFORE(2) | UPDATE(16).
        expected[prefix + column + "_immutable"] = (
            "O", 19, False, False, "public", "s015_refuse_row_mutation", 0, None, (column,),
        )
    expected[prefix + "delete_immutable"] = (
        "O", 11, False, False, "public", "s015_refuse_row_mutation", 0, None, (),
    )
    expected[prefix + "truncate_immutable"] = (
        "O", 34, False, False, "public", "s015_refuse_row_mutation", 0, None, (),
    )
    expected[prefix + "reserved_code"] = (
        "O", 5, True, True, "public", "s015_guard_reserved_role_code", 0, None, (),
    )
    wrong = [name for name, contract in expected.items() if observed.get(name) != contract]
    if wrong:
        _refuse(f"{phase} failed: 0021 definition trigger bindings or enabled states differ: {wrong}")


def _intake_check(cursor):
    cursor.execute(
        """
        SELECT pg_get_constraintdef(con.oid), con.convalidated
          FROM pg_catalog.pg_constraint con
          JOIN pg_catalog.pg_class c ON c.oid = con.conrelid
          JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relname = %s AND con.conname = %s AND con.contype = 'c'
        """,
        [INTAKE_TABLE, INTAKE_CHECK],
    )
    return cursor.fetchone()


def intake_check_literals(cursor):
    """The literal census, for diagnostics only; it does not establish the CHECK's semantics."""
    row = _intake_check(cursor)
    return None if row is None else frozenset(re.findall(r"'([A-Z_]+)'::character varying", row[0]))


def _assert_previous_intake_check(cursor, phase):
    """Match PostgreSQL's complete 0049 CHECK expression, including its operator and column.

    Only whitespace and parentheses are ignored. With this single comparison and the
    fixed uppercase/underscore literals, neither can alter the expression's meaning.
    A different operator, column, cast, literal order, extra predicate or NOT VALID fails.
    PostgreSQL 17's actual serialization is exercised by the migration witnesses.
    """
    row = _intake_check(cursor)
    literals = ", ".join(f"'{value}'::character varying" for value in PREVIOUS_ENTRY_VALUES)
    expected = f"CHECK (((entry)::text = ANY ((ARRAY[{literals}])::text[])))"
    def compact(value):
        return re.sub(r"[\s()]", "", value)
    if row is None or not row[1] or compact(row[0]) != compact(expected):
        _refuse(f"{phase} failed: {INTAKE_CHECK} is not exactly 0049's validated entry CHECK")


def _refuse(message):
    raise RuntimeError(f"SR 0051 {message}")


def forward_preflight(apps, schema_editor):
    """Refuse to start unless the database is exactly the state 0050 leaves (section 5.9)."""
    if schema_editor.connection.vendor != "postgresql":
        _refuse("forward preflight failed: PostgreSQL is the only substrate")
    with schema_editor.connection.cursor() as cursor:
        present = _columns(cursor, DEFINITION_TABLE) & set(SR_DEFINITION_COLUMNS)
        if present:
            _refuse(f"forward preflight failed: SR columns already present on {DEFINITION_TABLE}: {sorted(present)}")
        if _table_exists(cursor, PROVISION_TABLE):
            _refuse(f"forward preflight failed: {PROVISION_TABLE} already exists")
        triggers = _sr_triggers(cursor)
        if triggers:
            _refuse(f"forward preflight failed: sr_* triggers already exist: {triggers}")
        functions = _sr_functions(cursor)
        if functions:
            _refuse(f"forward preflight failed: sr_* functions already exist: {functions}")
        _assert_previous_intake_check(cursor, "forward preflight")
        _assert_0021_definition_triggers(cursor, "forward preflight")


def reverse_final_check(apps, schema_editor):
    """After Django has dropped the columns, the table and the constraints: verify exact restoration."""
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        present = _columns(cursor, DEFINITION_TABLE) & set(SR_DEFINITION_COLUMNS)
        if present:
            _refuse(f"reverse restoration failed: SR columns remain on {DEFINITION_TABLE}: {sorted(present)}")
        if _table_exists(cursor, PROVISION_TABLE):
            _refuse(f"reverse restoration failed: {PROVISION_TABLE} remains")
        if _sr_triggers(cursor):
            _refuse(f"reverse restoration failed: sr_* triggers remain: {_sr_triggers(cursor)}")
        if _sr_functions(cursor):
            _refuse(f"reverse restoration failed: sr_* functions remain: {_sr_functions(cursor)}")
        _assert_previous_intake_check(cursor, "reverse restoration")
        _assert_0021_definition_triggers(cursor, "reverse restoration")


def install_guards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        _refuse("install failed: PostgreSQL is the only substrate")
    with schema_editor.connection.cursor() as cursor:
        for name, body in _FUNCTION_BODIES.items():
            cursor.execute(
                f"CREATE FUNCTION public.{name}() RETURNS trigger LANGUAGE plpgsql AS $$" + body + "$$"
            )
        for statement in _TRIGGER_SQL:
            cursor.execute(statement)
        installed = _sr_triggers(cursor)
        expected = sorted(SR_TRIGGERS)
        if installed != expected:
            _refuse(f"install failed: triggers installed {installed} differ from {expected}")


def _populated_state(cursor):
    """The rows that make a reverse unsafe, with what they are (A-9)."""
    cursor.execute(
        f"SELECT count(*) FROM public.{INTAKE_TABLE} WHERE entry = %s", [NEW_ENTRY]
    )
    intake_rows = cursor.fetchone()[0]
    attribution_predicate = " OR ".join(f"{column} IS NOT NULL" for column in SR_DEFINITION_COLUMNS)
    cursor.execute(
        f"SELECT count(*), array_agg(role_uuid::text ORDER BY id) "
        f"FROM public.{DEFINITION_TABLE} WHERE {attribution_predicate}"
    )
    definition_count, definition_uuids = cursor.fetchone()
    cursor.execute(f"SELECT count(*) FROM public.{PROVISION_TABLE}")
    provision_rows = cursor.fetchone()[0]
    return intake_rows, definition_count, (definition_uuids or [])[:5], provision_rows


def uninstall_guards(apps, schema_editor):
    """Reverse: refuse a populated state with the reason; otherwise drop the triggers and functions."""
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        intake_rows, definition_count, sample, provision_rows = _populated_state(cursor)
        if intake_rows or definition_count or provision_rows:
            _refuse(
                "reverse refused: the database is populated under SR and nothing is deleted to permit a "
                f"rollback - {intake_rows} intake row(s) with entry {NEW_ENTRY} (immutable; narrowing the "
                f"CHECK would orphan them), {definition_count} definition row(s) carrying SR attribution "
                f"(first role_uuids {sample}), {provision_rows} provision row(s)"
            )
        for name, table in SR_TRIGGERS:
            cursor.execute(f"DROP TRIGGER {name} ON public.{table}")
        for name in _FUNCTION_BODIES:
            cursor.execute(f"DROP FUNCTION public.{name}()")
        if _sr_triggers(cursor) or _sr_functions(cursor):
            _refuse("reverse failed: sr_* objects remain after the drop")


class Migration(migrations.Migration):
    atomic = True

    dependencies = [("core", PREVIOUS_MIGRATION)]

    operations = [
        # 0. forward: the preflight before any mutation; reverse: the final restoration check.
        migrations.RunPython(forward_preflight, reverse_final_check),
        # 1. the section D.1 columns, nullable, no default (no backfill of pre-SR rows)
        migrations.AddField(
            model_name="organismroledefinition",
            name="kind",
            field=models.CharField(
                blank=True,
                choices=[("COORDINATOR", "Coordinator"), ("BOARD_OFFICE", "Board Office"), ("CUSTOM", "Custom")],
                max_length=12,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="organismroledefinition",
            name="appointing_authority",
            field=models.CharField(
                blank=True,
                choices=[("LO_COORDINATOR_BODY", "Lo Coordinator Body"), ("NAMED_BODY", "Named Body")],
                max_length=20,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="organismroledefinition",
            name="appointing_body_reference",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="organismroledefinition",
            name="alternative_route_reference",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="organismroledefinition",
            name="nomination_basis",
            field=models.CharField(
                blank=True,
                choices=[("CONSTITUTION", "Constitution"), ("SILENT", "Silent"), ("CUSTOM", "Custom")],
                max_length=12,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="organismroledefinition",
            name="nomination_policy",
            field=models.CharField(
                blank=True,
                choices=[("A", "A"), ("B", "B"), ("C", "C"), ("UNSUPPORTED", "Unsupported")],
                max_length=11,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="organismroledefinition",
            name="nomination_dependency",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="organismroledefinition",
            name="participation_required",
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="organismroledefinition",
            name="ex_officio_seat_reference",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="organismroledefinition",
            name="supersedes",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="superseded_by",
                to="core.organismroledefinition",
            ),
        ),
        migrations.AddField(
            model_name="organismroledefinition",
            name="enterer",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="entered_role_definitions",
                to="core.identity",
            ),
        ),
        migrations.AddField(
            model_name="organismroledefinition",
            name="enterer_access_epoch",
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="organismroledefinition",
            name="authority_decision_reference",
            field=models.CharField(blank=True, max_length=71, null=True),
        ),
        migrations.AddField(
            model_name="organismroledefinition",
            name="occurred_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="organismroledefinition",
            name="effective_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="organismroledefinition",
            name="received_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="organismroledefinition",
            name="recorded_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="organismroledefinition",
            name="retired_by_decision_reference",
            field=models.CharField(blank=True, max_length=71, null=True),
        ),
        migrations.AddField(
            model_name="organismroledefinition",
            name="retired_effective_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="organismroledefinition",
            name="retired_received_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="organismroledefinition",
            name="retired_recorded_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="organismroledefinition",
            name="retirement_reason",
            field=models.TextField(blank=True, null=True),
        ),
        # the closed domains and reference shapes at the table (null allowed for pre-SR rows)
        migrations.AddConstraint(
            model_name="organismroledefinition",
            constraint=models.CheckConstraint(
                condition=models.Q(("kind__isnull", True), ("kind__in", ["COORDINATOR", "BOARD_OFFICE", "CUSTOM"]), _connector="OR"),
                name="sr_definition_kind_domain_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="organismroledefinition",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("appointing_authority__isnull", True),
                    ("appointing_authority__in", ["LO_COORDINATOR_BODY", "NAMED_BODY"]),
                    _connector="OR",
                ),
                name="sr_definition_appointing_authority_domain_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="organismroledefinition",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("nomination_basis__isnull", True),
                    ("nomination_basis__in", ["CONSTITUTION", "SILENT", "CUSTOM"]),
                    _connector="OR",
                ),
                name="sr_definition_nomination_basis_domain_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="organismroledefinition",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("nomination_policy__isnull", True),
                    ("nomination_policy__in", ["A", "B", "C", "UNSUPPORTED"]),
                    _connector="OR",
                ),
                name="sr_definition_nomination_policy_domain_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="organismroledefinition",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("authority_decision_reference__isnull", True),
                    ("authority_decision_reference__regex", "^s015d1:[0-9a-f]{64}$"),
                    _connector="OR",
                ),
                name="sr_definition_decision_ref_shape_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="organismroledefinition",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("retired_by_decision_reference__isnull", True),
                    ("retired_by_decision_reference__regex", "^s015d1:[0-9a-f]{64}$"),
                    _connector="OR",
                ),
                name="sr_definition_retired_ref_shape_ck",
            ),
        ),
        # 2. the provision table (section D.2)
        migrations.CreateModel(
            name="OrganismRoleDefinitionProvision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("provision_uuid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                (
                    "relationship",
                    models.CharField(
                        choices=[
                            ("OFFICE_BASIS", "Office Basis"),
                            ("APPOINTING_AUTHORITY", "Appointing Authority"),
                            ("ALTERNATIVE_ROUTE", "Alternative Route"),
                            ("NOMINATION_ROUTE", "Nomination Route"),
                            ("PARTICIPATION_REQUIRED", "Participation Required"),
                            ("EX_OFFICIO_SEAT", "Ex Officio Seat"),
                        ],
                        max_length=24,
                    ),
                ),
                ("provision_reference", models.TextField()),
                ("provision_version", models.TextField()),
                ("recorded_at", models.DateTimeField(blank=True, editable=False, null=True)),
                (
                    "definition",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="provisions",
                        to="core.organismroledefinition",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(fields=("definition", "relationship"), name="sr_provision_relationship_uniq"),
                    models.CheckConstraint(
                        condition=models.Q(
                            (
                                "relationship__in",
                                [
                                    "OFFICE_BASIS",
                                    "APPOINTING_AUTHORITY",
                                    "ALTERNATIVE_ROUTE",
                                    "NOMINATION_ROUTE",
                                    "PARTICIPATION_REQUIRED",
                                    "EX_OFFICIO_SEAT",
                                ],
                            )
                        ),
                        name="sr_provision_relationship_domain_ck",
                    ),
                ],
            },
        ),
        # 3. the intake entry: the model's choices and the widened CHECK (0049's dropped, then re-created)
        migrations.AlterField(
            model_name="commandintake",
            name="entry",
            field=models.CharField(choices=ENTRY_CHOICES, max_length=40),
        ),
        migrations.RemoveConstraint(model_name="commandintake", name=INTAKE_CHECK),
        migrations.AddConstraint(
            model_name="commandintake",
            constraint=models.CheckConstraint(
                condition=models.Q(("entry__in", list(ENTRY_VALUES))),
                name=INTAKE_CHECK,
            ),
        ),
        # 4. the guards; reverse: the populated-state refusal, then the drop
        migrations.RunPython(install_guards, uninstall_guards),
    ]
