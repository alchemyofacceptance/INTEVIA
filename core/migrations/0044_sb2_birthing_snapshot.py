import importlib
from pathlib import Path
import re

from django.db import migrations, models
import django.db.models.deletion
import uuid


def _installed_office_attribution_body():
    source_path = Path(__file__).with_name("0036_s2b_role_attribution_guards.py")
    source = source_path.read_text(encoding="utf-8")
    function_start = source.index("CREATE FUNCTION public.s2b_guard_office_attribution()")
    body_start = source.index("AS $$", function_start) + len("AS $$")
    body_end = source.index("$$;", body_start)
    return source[body_start:body_end]


_SB1 = importlib.import_module("core.migrations.0042_sb1_founding_terms_phases")


def _installed_phase_body():
    source_path = Path(__file__).with_name("0042_sb1_founding_terms_phases.py")
    source = source_path.read_text(encoding="utf-8")
    function_start = source.index("CREATE FUNCTION public.sb_guard_phase_event()")
    body_start = source.index("AS $$", function_start) + len("AS $$")
    body_end = source.index("$$;", body_start)
    return source[body_start:body_end]


PREVIOUS_S3FU_GUARD_GATEWAY_EFFECT_BODY = _SB1.NEW_S3FU_GUARD_GATEWAY_EFFECT_BODY
PREVIOUS_SB_GUARD_PHASE_EVENT_BODY = _installed_phase_body()
PREVIOUS_S2B_GUARD_OFFICE_ATTRIBUTION_BODY = _installed_office_attribution_body()

_GATEWAY_MARKER = (
    "BEGIN\n"
    "    IF NEW.action NOT IN "
    "('EFFECT_APPLICATION_SETTINGS','EFFECT_APPLICATION_QUESTION',"
    "'EFFECT_GOVERNANCE_CONFIGURATION','PHASE_ADVANCE') THEN"
)
_GATEWAY_REPLACEMENT = (
    "BEGIN\n"
    "    IF NEW.action = 'BIRTHING' THEN RETURN NEW; END IF;\n"
    "    IF NEW.action NOT IN "
    "('EFFECT_APPLICATION_SETTINGS','EFFECT_APPLICATION_QUESTION',"
    "'EFFECT_GOVERNANCE_CONFIGURATION','PHASE_ADVANCE') THEN"
)
if PREVIOUS_S3FU_GUARD_GATEWAY_EFFECT_BODY.count(_GATEWAY_MARKER) != 1:
    raise RuntimeError("0042 gateway body does not match the accepted B-1 preflight")
NEW_S3FU_GUARD_GATEWAY_EFFECT_BODY = PREVIOUS_S3FU_GUARD_GATEWAY_EFFECT_BODY.replace(
    _GATEWAY_MARKER,
    _GATEWAY_REPLACEMENT,
    1,
)

_PHASE_DECLARATION_MARKER = "DECLARE organism_row public.core_livingorganism%ROWTYPE;"
_PHASE_DECLARATION_REPLACEMENT = """DECLARE organism_row public.core_livingorganism%ROWTYPE;
                    snapshot_row record;
                    v_snapshot_uuid uuid;
                    snapshot_preimage text;
                    expected_snapshot_commitment text;
                    effective_configuration_uuid uuid;
                    effective_settings_uuid uuid;
                    effective_applications_open boolean;
                    effective_automatic_acceptance boolean;
                    effective_question_uuid uuid;
                    effective_question_text text;
                    effective_question_format text;
                    expected_content_reference text;
                    expected_acceptance_reference text;"""
_PHASE_ACTION_MARKER = "IF NEW.action <> 'PHASE_ADVANCE' THEN RETURN NEW; END IF;"
_BIRTHING_LIMB = """
                IF NEW.action = 'BIRTHING' THEN
                    SELECT lo.* INTO organism_row
                      FROM public.core_livingorganism lo
                     WHERE lo.id = NEW.living_organism_id;
                    IF organism_row.id IS NULL
                       OR NEW.prior_state IS DISTINCT FROM 'ACTIVE'
                       OR NEW.resulting_state IS DISTINCT FROM 'ACTIVE'
                       OR NEW.actor_state IS DISTINCT FROM 'LIVING_ORGANISM'
                       OR NEW.actor_id IS NOT NULL OR NEW.actor_access_epoch IS NOT NULL
                       OR NEW.actor_capacity IS DISTINCT FROM 'STANDING'
                       OR NEW.enterer_identity_id IS NULL
                       OR NEW.entry_mode IS DISTINCT FROM 'TRANSPORT'
                       OR NEW.authority_basis_id IS DISTINCT FROM organism_row.founding_authority_basis_id
                       OR (NEW.effective_at = NEW.received_at
                           AND (NEW.temporal_basis_kind IS NOT NULL
                                OR NEW.temporal_basis_reference IS NOT NULL))
                       OR (NEW.effective_at < NEW.received_at
                           AND (NEW.temporal_basis_kind IS DISTINCT FROM 'RETROSPECTIVE'
                                OR NEW.temporal_basis_reference IS DISTINCT FROM
                                   ('s015tb1:' || NEW.payload_fingerprint)))
                       OR (NEW.effective_at > NEW.received_at
                           AND (NEW.temporal_basis_kind IS DISTINCT FROM 'PROSPECTIVE'
                                OR NEW.temporal_basis_reference IS DISTINCT FROM
                                   ('s015tb1:' || NEW.payload_fingerprint)))
                       OR NEW.evidence_reference !~ '^birthing-snapshot:[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$' THEN
                        RAISE EXCEPTION 'SB phase guard: malformed birthing event'
                            USING ERRCODE = 'integrity_constraint_violation';
                    END IF;
                    IF EXISTS (
                        SELECT 1 FROM public.core_livingorganismevent birthing
                         WHERE birthing.living_organism_id = NEW.living_organism_id
                           AND birthing.action = 'BIRTHING'
                           AND birthing.effective_at <= NEW.effective_at
                           AND birthing.received_at <= NEW.recorded_at
                           AND birthing.recorded_at <= NEW.recorded_at
                    ) THEN
                        RAISE EXCEPTION 'BIRTHING_ALREADY_RECORDED'
                            USING ERRCODE = 'integrity_constraint_violation';
                    END IF;
                    SELECT split_part(e.evidence_reference, '->', 2)
                      INTO current_phase
                      FROM public.core_livingorganismevent e
                     WHERE e.living_organism_id = NEW.living_organism_id
                       AND e.action = 'PHASE_ADVANCE'
                       AND e.effective_at <= NEW.effective_at
                       AND e.received_at <= NEW.recorded_at
                       AND e.recorded_at <= NEW.recorded_at
                     ORDER BY e.effective_at DESC, e.occurred_at DESC, e.received_at DESC,
                              e.recorded_at DESC, e.sequence DESC, e.event_uuid DESC
                     LIMIT 1;
                    current_phase := COALESCE(current_phase, 'CONCEIVING');
                    IF current_phase IS DISTINCT FROM 'BIRTHING' THEN
                        RAISE EXCEPTION 'BIRTHING_PREREQUISITE_MISSING: the phase to read Birthing'
                            USING ERRCODE = 'integrity_constraint_violation';
                    END IF;
                    SELECT count(*) INTO electorate_count
                      FROM public.core_organismmembership m
                     WHERE m.living_organism_id = NEW.living_organism_id
                       AND public.s015_fold_effective_state(
                           'core_organismmembership', m.id, NEW.effective_at, NEW.recorded_at
                       ) = 'ACTIVE';
                    IF electorate_count = 0 THEN
                        RAISE EXCEPTION 'SB_BIRTHING_ELECTORATE_EMPTY'
                            USING ERRCODE = 'integrity_constraint_violation';
                    END IF;
                    IF electorate_count > 1 THEN
                        RAISE EXCEPTION 'INV_CQ_ROUTE_REQUIRED'
                            USING ERRCODE = 'integrity_constraint_violation';
                    END IF;
                    SELECT v.version_uuid, v.governance_enabled
                      INTO effective_configuration_uuid, effective_governance_enabled
                      FROM public.core_livingorganismevent e
                      JOIN public.core_logovernanceconfigurationversion v
                        ON e.evidence_reference = 'governance-configuration-version:' || v.version_uuid::text
                     WHERE e.living_organism_id = NEW.living_organism_id
                       AND e.action = 'EFFECT_GOVERNANCE_CONFIGURATION'
                       AND e.effective_at <= NEW.effective_at
                       AND e.received_at <= NEW.recorded_at
                       AND e.recorded_at <= NEW.recorded_at
                     ORDER BY e.effective_at DESC, e.occurred_at DESC, e.received_at DESC,
                              e.recorded_at DESC, e.sequence DESC, e.event_uuid DESC
                     LIMIT 1;
                    IF NOT FOUND OR effective_governance_enabled IS NULL THEN
                        RAISE EXCEPTION 'BIRTHING_PREREQUISITE_MISSING: an effective governance configuration'
                            USING ERRCODE = 'integrity_constraint_violation';
                    END IF;
                    IF effective_governance_enabled THEN
                        RAISE EXCEPTION 'SB-IL-GOVCONST: The LO was not born and nothing changed. Its GOVERNANCE choice in effect is Yes, so Birthing needs its GOVERNANCE Circle and that Circle''s constitutional admission route to be established, and those records cannot be made in this release. This is a known limit of the current build, not a refusal of the LO''s choice; it lifts when GOVERNANCE formation is delivered.'
                            USING ERRCODE = 'integrity_constraint_violation';
                    END IF;
                    SELECT v.version_uuid, v.applications_open, v.automatic_acceptance
                      INTO effective_settings_uuid, effective_applications_open,
                           effective_automatic_acceptance
                      FROM public.core_livingorganismevent e
                      JOIN public.core_loapplicationsettingsversion v
                        ON e.evidence_reference = 'application-settings-version:' || v.version_uuid::text
                     WHERE e.living_organism_id = NEW.living_organism_id
                       AND e.action = 'EFFECT_APPLICATION_SETTINGS'
                       AND e.effective_at <= NEW.effective_at
                       AND e.received_at <= NEW.recorded_at
                       AND e.recorded_at <= NEW.recorded_at
                     ORDER BY e.effective_at DESC, e.occurred_at DESC, e.received_at DESC,
                              e.recorded_at DESC, e.sequence DESC, e.event_uuid DESC
                     LIMIT 1;
                    IF NOT FOUND OR effective_applications_open IS NULL
                       OR effective_automatic_acceptance IS NULL THEN
                        RAISE EXCEPTION 'BIRTHING_PREREQUISITE_MISSING: both application settings chosen'
                            USING ERRCODE = 'integrity_constraint_violation';
                    END IF;
                    SELECT v.version_uuid, v.question_text, v.answer_format
                      INTO effective_question_uuid, effective_question_text, effective_question_format
                      FROM public.core_livingorganismevent e
                      JOIN public.core_loapplicationquestionversion v
                        ON e.evidence_reference = 'application-question-version:' || v.version_uuid::text
                     WHERE e.living_organism_id = NEW.living_organism_id
                       AND e.action = 'EFFECT_APPLICATION_QUESTION'
                       AND e.effective_at <= NEW.effective_at
                       AND e.received_at <= NEW.recorded_at
                       AND e.recorded_at <= NEW.recorded_at
                     ORDER BY e.effective_at DESC, e.occurred_at DESC, e.received_at DESC,
                              e.recorded_at DESC, e.sequence DESC, e.event_uuid DESC
                     LIMIT 1;
                    IF NOT FOUND OR nullif(btrim(effective_question_text), '') IS NULL
                       OR effective_question_format IS NULL THEN
                        RAISE EXCEPTION 'BIRTHING_PREREQUISITE_MISSING: a reviewed application question'
                            USING ERRCODE = 'integrity_constraint_violation';
                    END IF;
                    v_snapshot_uuid := substring(NEW.evidence_reference from 19)::uuid;
                    SELECT s.* INTO snapshot_row
                      FROM public.core_lobirthingsnapshot s
                     WHERE s.snapshot_uuid = v_snapshot_uuid;
                    IF NOT FOUND
                       OR snapshot_row.living_organism_id IS DISTINCT FROM NEW.living_organism_id
                       OR snapshot_row.effective_at IS DISTINCT FROM NEW.effective_at
                       OR snapshot_row.recorded_at > NEW.recorded_at
                       OR jsonb_typeof(snapshot_row.body->'constitutional_documents') IS DISTINCT FROM 'array'
                       OR snapshot_row.body->'constitutional_documents' IS DISTINCT FROM '[]'::jsonb
                       OR jsonb_typeof(snapshot_row.body->'laws') IS DISTINCT FROM 'array'
                       OR snapshot_row.body->'laws' IS DISTINCT FROM '[]'::jsonb
                       OR snapshot_row.body->'governance_constitution' IS DISTINCT FROM 'null'::jsonb
                       OR snapshot_row.body->'settings'->>'governance_configuration'
                            IS DISTINCT FROM 'governance-configuration-version:' || effective_configuration_uuid::text
                       OR snapshot_row.body->'settings'->>'application_settings'
                            IS DISTINCT FROM 'application-settings-version:' || effective_settings_uuid::text
                       OR snapshot_row.body->'settings'->>'application_question'
                            IS DISTINCT FROM 'application-question-version:' || effective_question_uuid::text
                       OR snapshot_row.body->'formation_history_reference'->'lo_chain_sequence_range'
                            IS DISTINCT FROM jsonb_build_array(1, NEW.sequence - 1) THEN
                        RAISE EXCEPTION 'BIRTHING_SNAPSHOT_INVALID'
                            USING ERRCODE = 'integrity_constraint_violation';
                    END IF;
                    SELECT 'founding-content:' || content.content_uuid::text || '@' || content.version::text
                      INTO expected_content_reference
                      FROM public.core_lofoundingcontentversion content
                     WHERE content.living_organism_id = NEW.living_organism_id
                       AND content.version = 1
                       AND content.effective_at <= NEW.effective_at
                       AND content.recorded_at <= NEW.recorded_at
                     ORDER BY content.effective_at DESC, content.recorded_at DESC
                     LIMIT 1;
                    IF expected_content_reference IS NULL
                       OR snapshot_row.body->>'founding_content' IS DISTINCT FROM expected_content_reference THEN
                        RAISE EXCEPTION 'BIRTHING_SNAPSHOT_INVALID'
                            USING ERRCODE = 'integrity_constraint_violation';
                    END IF;
                    SELECT 'platform-terms-acceptance:' || acceptance.acceptance_uuid::text
                      INTO expected_acceptance_reference
                      FROM public.core_platformtermsacceptance acceptance
                      JOIN public.core_platformtermsversion terms
                        ON terms.id = acceptance.terms_version_id
                      JOIN public.core_livingorganismevent founding
                        ON founding.living_organism_id = NEW.living_organism_id
                       AND founding.sequence = 1
                     WHERE acceptance.acceptance_kind = 'FOUNDING'
                       AND acceptance.subject_reference = 'living-organism:' || organism_row.organism_id::text
                       AND acceptance.accepted_by_identity_id = organism_row.founder_identity_id
                       AND acceptance.accepted_at = founding.effective_at
                       AND acceptance.recorded_at <= NEW.recorded_at
                       AND terms.effective_from <= founding.effective_at
                       AND terms.recorded_at <= founding.recorded_at
                     ORDER BY acceptance.recorded_at DESC, acceptance.id DESC
                     LIMIT 1;
                    IF expected_acceptance_reference IS NULL
                       OR snapshot_row.body->>'platform_terms_acceptance'
                            IS DISTINCT FROM expected_acceptance_reference THEN
                        RAISE EXCEPTION 'BIRTHING_SNAPSHOT_INVALID'
                            USING ERRCODE = 'integrity_constraint_violation';
                    END IF;
                    snapshot_preimage := '{"body":' || COALESCE(to_json(snapshot_row.body)::text, 'null')
                        || ',"effective_at":' || COALESCE(to_json(snapshot_row.effective_at)::text, 'null')
                        || ',"living_organism_uuid":' || COALESCE(to_json(organism_row.organism_id::text)::text, 'null')
                        || ',"received_at":' || COALESCE(to_json(snapshot_row.received_at)::text, 'null')
                        || ',"snapshot_uuid":' || COALESCE(to_json(snapshot_row.snapshot_uuid::text)::text, 'null')
                        || '}';
                    expected_snapshot_commitment := public.s015_0022_digest('s015r1', snapshot_preimage);
                    IF snapshot_row.snapshot_commitment IS DISTINCT FROM expected_snapshot_commitment THEN
                        RAISE EXCEPTION 'BIRTHING_SNAPSHOT_INVALID'
                            USING ERRCODE = 'integrity_constraint_violation';
                    END IF;
                    RETURN NEW;
                END IF;
"""
if PREVIOUS_SB_GUARD_PHASE_EVENT_BODY.count(_PHASE_DECLARATION_MARKER) != 1:
    raise RuntimeError("0042 phase body does not match the accepted B-2 preflight")
if PREVIOUS_SB_GUARD_PHASE_EVENT_BODY.count(_PHASE_ACTION_MARKER) != 1:
    raise RuntimeError("0042 phase action marker is not unique")
NEW_SB_GUARD_PHASE_EVENT_BODY = PREVIOUS_SB_GUARD_PHASE_EVENT_BODY.replace(
    _PHASE_DECLARATION_MARKER,
    _PHASE_DECLARATION_REPLACEMENT,
    1,
).replace(
    _PHASE_ACTION_MARKER,
    _BIRTHING_LIMB + "                " + _PHASE_ACTION_MARKER,
    1,
)

_S2B_DECLARATION_MARKER = (
    "DECLARE lo_id bigint; lo_uuid uuid; founding_xid xid8; membership_uuid uuid;\n"
    "                membership_identity_id bigint; role_uuid uuid; role_code text; role_scope text; decision_row record;"
)
_S2B_DECLARATION_REPLACEMENT = (
    "DECLARE v_lo_id bigint; v_lo_uuid uuid; v_founding_xid xid8; v_membership_uuid uuid;\n"
    "                v_membership_identity_id bigint; v_role_uuid uuid; v_role_code text; "
    "v_role_scope text; v_decision_row record;\n"
    "                v_founding_event_uuid uuid; v_founding_authority_decision_reference text;"
)
if PREVIOUS_S2B_GUARD_OFFICE_ATTRIBUTION_BODY.count(_S2B_DECLARATION_MARKER) != 1:
    raise RuntimeError("0036 office attribution body does not match the accepted B-3 preflight")
NEW_S2B_GUARD_OFFICE_ATTRIBUTION_BODY = PREVIOUS_S2B_GUARD_OFFICE_ATTRIBUTION_BODY.replace(
    _S2B_DECLARATION_MARKER,
    _S2B_DECLARATION_REPLACEMENT,
    1,
)
for _old_name, _new_name in (
    ("lo_id", "v_lo_id"),
    ("lo_uuid", "v_lo_uuid"),
    ("founding_xid", "v_founding_xid"),
    ("membership_identity_id", "v_membership_identity_id"),
    ("role_code", "v_role_code"),
    ("role_scope", "v_role_scope"),
    ("decision_row", "v_decision_row"),
):
    NEW_S2B_GUARD_OFFICE_ATTRIBUTION_BODY = re.sub(
        rf"(?<![\w.]){_old_name}\b",
        _new_name,
        NEW_S2B_GUARD_OFFICE_ATTRIBUTION_BODY,
    )
_S2B_INTO_MARKER = (
    "INTO v_lo_id, v_lo_uuid, v_founding_xid, membership_uuid,\n"
    "                     v_membership_identity_id, role_uuid, v_role_code, v_role_scope"
)
if NEW_S2B_GUARD_OFFICE_ATTRIBUTION_BODY.count(_S2B_INTO_MARKER) != 1:
    raise RuntimeError("0036 office attribution SELECT targets are not unique")
NEW_S2B_GUARD_OFFICE_ATTRIBUTION_BODY = NEW_S2B_GUARD_OFFICE_ATTRIBUTION_BODY.replace(
    _S2B_INTO_MARKER,
    "INTO v_lo_id, v_lo_uuid, v_founding_xid, v_membership_uuid,\n"
    "                     v_membership_identity_id, v_role_uuid, v_role_code, v_role_scope",
    1,
)
NEW_S2B_GUARD_OFFICE_ATTRIBUTION_BODY = NEW_S2B_GUARD_OFFICE_ATTRIBUTION_BODY.replace(
    "membership_uuid::text",
    "v_membership_uuid::text",
).replace(
    "role_uuid::text",
    "v_role_uuid::text",
)
_S2B_ASSIGNMENT_LOOKUP_MARKER = (
    "SELECT assignment_uuid::text FROM public.core_contextualroleassignment WHERE id = NEW.assignment_id"
)
if NEW_S2B_GUARD_OFFICE_ATTRIBUTION_BODY.count(_S2B_ASSIGNMENT_LOOKUP_MARKER) != 1:
    raise RuntimeError("0036 assignment lookup is not unique")
NEW_S2B_GUARD_OFFICE_ATTRIBUTION_BODY = NEW_S2B_GUARD_OFFICE_ATTRIBUTION_BODY.replace(
    _S2B_ASSIGNMENT_LOOKUP_MARKER,
    "SELECT target_assignment.assignment_uuid::text "
    "FROM public.core_contextualroleassignment target_assignment "
    "WHERE target_assignment.id = NEW.assignment_id",
    1,
)
_S2B_FOUNDING_BRANCH_MARKER = "           ) THEN\n            RETURN NEW;\n        END IF;"
_S2B_FOUNDING_BRANCH_REPLACEMENT = """           ) THEN
            SELECT founding.event_uuid, founding.authority_decision_reference
              INTO v_founding_event_uuid, v_founding_authority_decision_reference
              FROM public.core_livingorganismevent founding
             WHERE founding.living_organism_id = v_lo_id
               AND founding.sequence = 1
               AND founding.action = 'FOUND'
               AND founding.xmin = v_founding_xid::xid
               AND founding.actor_id = NEW.actor_id
               AND founding.actor_access_epoch = NEW.actor_access_epoch
               AND founding.authority_basis_id = NEW.authority_basis_id
               AND founding.actor_state = 'PARTY'
             LIMIT 1;
            IF NEW.evidence_reference IS DISTINCT FROM
                ('founding-batch:' || v_founding_event_uuid::text) THEN
                RAISE EXCEPTION 'S2B office attribution refused: the founding appointment must name this LO''s founding act'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF NEW.authority_decision_reference IS DISTINCT FROM
                v_founding_authority_decision_reference THEN
                RAISE EXCEPTION 'S2B office attribution refused: the founding appointment must carry that founding act''s authority decision'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END IF;"""
if PREVIOUS_S2B_GUARD_OFFICE_ATTRIBUTION_BODY.count(_S2B_FOUNDING_BRANCH_MARKER) != 1:
    raise RuntimeError("0036 founding ASSIGN branch is not unique")
NEW_S2B_GUARD_OFFICE_ATTRIBUTION_BODY = NEW_S2B_GUARD_OFFICE_ATTRIBUTION_BODY.replace(
    _S2B_FOUNDING_BRANCH_MARKER,
    _S2B_FOUNDING_BRANCH_REPLACEMENT,
    1,
)
_S2B_DECISION_LOOKUP_MARKER = (
    "    SELECT * INTO v_decision_row FROM public.core_authoritydecision\n"
    "     WHERE decision_reference = NEW.authority_decision_reference;"
)
_S2B_DECISION_LOOKUP_REPLACEMENT = """    IF NEW.action IN ('END', 'REASSIGN')
       AND v_role_code = 'COORDINATOR'
       AND v_role_scope = 'LIVING_ORGANISM'
       AND EXISTS (
            SELECT 1
              FROM public.core_livingorganism organism
             WHERE organism.id = v_lo_id
               AND organism.founder_identity_id IS NOT NULL
               AND organism.founder_identity_id = v_membership_identity_id
               AND NEW.enterer_identity_id IS DISTINCT FROM organism.founder_identity_id
       )
       AND NOT EXISTS (
            SELECT 1
              FROM public.core_livingorganismevent birthing
             WHERE birthing.living_organism_id = v_lo_id
               AND birthing.action = 'BIRTHING'
               AND birthing.effective_at <= NEW.effective_at
               AND birthing.recorded_at <= NEW.recorded_at
       )
       AND EXISTS (
            SELECT 1
              FROM public.core_contextualroleassignmentstateevent original
              JOIN public.core_livingorganismevent founding
                ON founding.living_organism_id = v_lo_id
               AND founding.sequence = 1
               AND founding.action = 'FOUND'
             WHERE original.assignment_id = NEW.assignment_id
               AND original.sequence = 1
               AND original.action = 'ASSIGN'
               AND original.prior_state IS NULL
               AND original.resulting_state = 'ACTIVE'
               AND original.actor_state = 'PARTY'
               AND original.actor_id = v_membership_identity_id
               AND original.enterer_identity_id = v_membership_identity_id
               AND (
                    original.xmin = founding.xmin
                    OR (
                        original.authority_basis_id = founding.authority_basis_id
                        AND original.evidence_reference = 'founding-batch:' || founding.event_uuid::text
                        AND original.authority_decision_reference = founding.authority_decision_reference
                    )
               )
       ) THEN
        RAISE EXCEPTION 'C_FOUNDER_PROTECTED'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT decision.* INTO v_decision_row
      FROM public.core_authoritydecision decision
     WHERE decision.decision_reference = NEW.authority_decision_reference;"""
if PREVIOUS_S2B_GUARD_OFFICE_ATTRIBUTION_BODY.count(
    "    SELECT * INTO decision_row FROM public.core_authoritydecision\n"
    "     WHERE decision_reference = NEW.authority_decision_reference;"
) != 1:
    raise RuntimeError("0036 authority-decision lookup is not unique")
NEW_S2B_GUARD_OFFICE_ATTRIBUTION_BODY = NEW_S2B_GUARD_OFFICE_ATTRIBUTION_BODY.replace(
    _S2B_DECISION_LOOKUP_MARKER,
    _S2B_DECISION_LOOKUP_REPLACEMENT,
    1,
)


def _assert_body(cursor, function_name, expected_body, direction):
    cursor.execute(
        """
        SELECT p.prosrc
          FROM pg_catalog.pg_proc p
          JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public' AND p.proname = %s
        """,
        [function_name],
    )
    row = cursor.fetchone()
    if row is None or row[0] != expected_body:
        raise RuntimeError(
            f"SB-2 {direction} body preflight failed for {function_name}: "
            f"expected_match={row is not None and row[0] == expected_body}"
        )


def check_previous_bodies(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        _assert_body(
            cursor,
            "s3fu_guard_gateway_effect",
            PREVIOUS_S3FU_GUARD_GATEWAY_EFFECT_BODY,
            "forward",
        )
        _assert_body(cursor, "sb_guard_phase_event", PREVIOUS_SB_GUARD_PHASE_EVENT_BODY, "forward")
        _assert_body(
            cursor,
            "s2b_guard_office_attribution",
            PREVIOUS_S2B_GUARD_OFFICE_ATTRIBUTION_BODY,
            "forward",
        )


def _create_function(cursor, name, body):
    cursor.execute(
        f"CREATE OR REPLACE FUNCTION public.{name}() "
        f"RETURNS trigger LANGUAGE plpgsql AS $${body}$$"
    )


def _snapshot_commitment_body():
    return r"""
DECLARE organism_uuid uuid; preimage text;
BEGIN
    IF NEW.snapshot_commitment IS NOT NULL THEN
        RAISE EXCEPTION 'SB Birthing snapshot commitment is database-assigned'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT lo.organism_id INTO organism_uuid
      FROM public.core_livingorganism lo
     WHERE lo.id = NEW.living_organism_id;
    IF organism_uuid IS NULL THEN
        RAISE EXCEPTION 'SB Birthing snapshot organism is missing'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    preimage := '{"body":' || COALESCE(to_json(NEW.body)::text, 'null')
        || ',"effective_at":' || COALESCE(to_json(NEW.effective_at)::text, 'null')
        || ',"living_organism_uuid":' || COALESCE(to_json(organism_uuid::text)::text, 'null')
        || ',"received_at":' || COALESCE(to_json(NEW.received_at)::text, 'null')
        || ',"snapshot_uuid":' || COALESCE(to_json(NEW.snapshot_uuid::text)::text, 'null')
        || '}';
    NEW.snapshot_commitment := public.s015_0022_digest('s015r1', preimage);
    RETURN NEW;
END
"""


def install_birthing(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        _create_function(cursor, "s3fu_guard_gateway_effect", NEW_S3FU_GUARD_GATEWAY_EFFECT_BODY)
        _create_function(cursor, "sb_guard_phase_event", NEW_SB_GUARD_PHASE_EVENT_BODY)
        _create_function(
            cursor,
            "s2b_guard_office_attribution",
            NEW_S2B_GUARD_OFFICE_ATTRIBUTION_BODY,
        )
        cursor.execute(
            "CREATE FUNCTION public.sb_assign_lobirthingsnapshot_commitment() "
            "RETURNS trigger LANGUAGE plpgsql AS $$"
            + _snapshot_commitment_body()
            + "$$"
        )
        cursor.execute(
            "CREATE TRIGGER sb_lobirthingsnapshot_record_time BEFORE INSERT "
            "ON public.core_lobirthingsnapshot FOR EACH ROW "
            "EXECUTE FUNCTION public.s3_assign_record_time()"
        )
        cursor.execute(
            "CREATE TRIGGER sb_lobirthingsnapshot_immutable BEFORE UPDATE OR DELETE "
            "ON public.core_lobirthingsnapshot FOR EACH ROW "
            "EXECUTE FUNCTION public.s015_refuse_row_mutation()"
        )
        cursor.execute(
            "CREATE TRIGGER s015_lobirthingsnapshot_truncate_immutable BEFORE TRUNCATE "
            "ON public.core_lobirthingsnapshot FOR EACH STATEMENT "
            "EXECUTE FUNCTION public.s015_refuse_row_mutation()"
        )
        cursor.execute(
            "CREATE TRIGGER sb_lobirthingsnapshot_commitment BEFORE INSERT "
            "ON public.core_lobirthingsnapshot FOR EACH ROW "
            "EXECUTE FUNCTION public.sb_assign_lobirthingsnapshot_commitment()"
        )


def uninstall_birthing(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT EXISTS (SELECT 1 FROM public.core_lobirthingsnapshot LIMIT 1)")
        if cursor.fetchone()[0]:
            raise RuntimeError("SB-2 reverse refused while core_lobirthingsnapshot contains rows")
        _assert_body(cursor, "s3fu_guard_gateway_effect", NEW_S3FU_GUARD_GATEWAY_EFFECT_BODY, "reverse")
        _assert_body(cursor, "sb_guard_phase_event", NEW_SB_GUARD_PHASE_EVENT_BODY, "reverse")
        _assert_body(
            cursor,
            "s2b_guard_office_attribution",
            NEW_S2B_GUARD_OFFICE_ATTRIBUTION_BODY,
            "reverse",
        )
        for trigger_name in (
            "sb_lobirthingsnapshot_record_time",
            "sb_lobirthingsnapshot_immutable",
            "s015_lobirthingsnapshot_truncate_immutable",
            "sb_lobirthingsnapshot_commitment",
        ):
            cursor.execute(f"DROP TRIGGER {trigger_name} ON public.core_lobirthingsnapshot")
        cursor.execute("DROP FUNCTION public.sb_assign_lobirthingsnapshot_commitment()")
        _create_function(cursor, "s3fu_guard_gateway_effect", PREVIOUS_S3FU_GUARD_GATEWAY_EFFECT_BODY)
        _create_function(cursor, "sb_guard_phase_event", PREVIOUS_SB_GUARD_PHASE_EVENT_BODY)
        _create_function(
            cursor,
            "s2b_guard_office_attribution",
            PREVIOUS_S2B_GUARD_OFFICE_ATTRIBUTION_BODY,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0043_p1_governed_registration"),
    ]

    operations = [
        migrations.RunPython(check_previous_bodies, migrations.RunPython.noop),
        migrations.CreateModel(
            name="LOBirthingSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("snapshot_uuid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("body", models.JSONField()),
                ("snapshot_commitment", models.CharField(editable=False, max_length=71, null=True)),
                ("effective_at", models.DateTimeField()),
                ("received_at", models.DateTimeField()),
                ("recorded_at", models.DateTimeField(editable=False, null=True)),
                ("living_organism", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="birthing_snapshots",
                    to="core.livingorganism",
                )),
            ],
            options={"abstract": False},
        ),
        migrations.AddConstraint(
            model_name="lobirthingsnapshot",
            constraint=models.UniqueConstraint(
                fields=("living_organism",),
                name="core_lobirthingsnapshot_living_organism_id_uniq",
            ),
        ),
        migrations.RunPython(install_birthing, uninstall_birthing),
    ]
