"""Install the v1.0 entitlement records, guards, and platform rules."""

from importlib import import_module

from django.db import connection, migrations, models
import django.db.models.deletion
import uuid


PREVIOUS_MIGRATION = "0045_sbg_governance_formation"
MIGRATION = "0046_se_entitlement_foundation"
EXPECTED_TRIGGER_COUNT = 470
DECLARED_TRIGGER_COUNT = 12

BETA_RULE_UUID = "c81640de-f776-5b31-a046-0046e0000001"
FREE_RULE_UUID = "c81640de-f776-5b31-a046-0046e0000002"
BETA_RULE_AUTHORITY = (
    "ruling:HG_RULING_HD_X_1_D1_CAPABILITY_SET_AND_DASHBOARD_20261011"
)
FREE_RULE_AUTHORITY = (
    "ruling:HG_RULING_SE_D_SE_1_NOT_SET_MEASURED_NOT_ENFORCED_20261011"
)

CAPABILITY_CODES = (
    "APPLICATION_CIRCLE",
    "FINANCE_CIRCLE",
    "OPERATIONS_CIRCLE",
    "UNLIMITED_CIRCLES",
    "PROBATION",
    "MENTORSHIP",
    "CUSTOM_ROLES",
    "GOVERNANCE_SUSPENSION",
    "PREMIUM_SEALING",
    "OPTIONAL_TRANSPARENCY",
    "CONFIGURABLE_RECOVERY_WINDOWS",
    "CIRCLE_SCOPED_EXPORT",
    "SEALED_EXPORT",
)

DIMENSION_LIMITS = (
    '{"circles":{"kind":"UNLIMITED"},'
    '"document_footprint":{"kind":"NOT_SET"},'
    '"organism_learners":{"kind":"NOT_SET"}}'
)
FREE_TIER_LIMITS = (
    '{"circles":{"kind":"LIMIT","value":9},'
    '"document_footprint":{"kind":"NOT_SET"},'
    '"organism_learners":{"kind":"NOT_SET"}}'
)
BETA_CAPABILITIES_JSON = (
    '["APPLICATION_CIRCLE","FINANCE_CIRCLE","OPERATIONS_CIRCLE",'
    '"UNLIMITED_CIRCLES","PROBATION","MENTORSHIP","CUSTOM_ROLES",'
    '"GOVERNANCE_SUSPENSION","PREMIUM_SEALING","OPTIONAL_TRANSPARENCY",'
    '"CONFIGURABLE_RECOVERY_WINDOWS","CIRCLE_SCOPED_EXPORT","SEALED_EXPORT"]'
)
BETA_RULE_BODY_JSON = (
    '{"capabilities":' + BETA_CAPABILITIES_JSON
    + ',"limits":' + DIMENSION_LIMITS + '}'
)
FREE_RULE_BODY_JSON = '{"capabilities":[],"limits":' + FREE_TIER_LIMITS + '}'


def _governed_rule_guard_body():
    """Extend the 0045 guard only for the two commissioned SE platform rules."""
    formation = import_module("core.migrations.0045_sbg_governance_formation")
    body = formation._GOVERNEDRULE_GUARD_BODY
    old_scope = "OR NEW.code <> 'U3_MEMBERSHIP_END_ENDS_CIRCLE_PARTICIPATION'"
    new_scope = (
        "OR NEW.code NOT IN ("
        "'U3_MEMBERSHIP_END_ENDS_CIRCLE_PARTICIPATION',"
        "'BETA_COMPLIMENTARY_ACCESS','FREE_TIER')"
    )
    if body.count(old_scope) != 1:
        raise RuntimeError("0045 governed-rule guard no longer matches the SE migration")
    body = body.replace(old_scope, new_scope, 1)
    insertion = r"""    IF NEW.code IN ('BETA_COMPLIMENTARY_ACCESS', 'FREE_TIER') THEN
        IF NEW.rule_uuid <> (
                CASE
                    WHEN NEW.code = 'BETA_COMPLIMENTARY_ACCESS'
                        THEN 'c81640de-f776-5b31-a046-0046e0000001'::uuid
                    ELSE 'c81640de-f776-5b31-a046-0046e0000002'::uuid
                END
            ) THEN
            RAISE EXCEPTION 'SE_GOVERNEDRULE_PLATFORM_INVALID'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF jsonb_typeof(NEW.body) IS DISTINCT FROM 'object' THEN
            RAISE EXCEPTION 'SE_GOVERNEDRULE_PLATFORM_INVALID'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF NEW.body - ARRAY['capabilities','limits']::text[] <> '{}'::jsonb
           OR jsonb_typeof(NEW.body->'capabilities') IS DISTINCT FROM 'array'
           OR public.se_entitlement_limits_valid(NEW.body->'limits') IS DISTINCT FROM TRUE THEN
            RAISE EXCEPTION 'SE_GOVERNEDRULE_PLATFORM_INVALID'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF EXISTS (
            SELECT 1
              FROM jsonb_array_elements_text(NEW.body->'capabilities') AS capability(code)
             WHERE capability.code NOT IN (
                 'APPLICATION_CIRCLE','FINANCE_CIRCLE','OPERATIONS_CIRCLE',
                 'UNLIMITED_CIRCLES','PROBATION','MENTORSHIP','CUSTOM_ROLES',
                 'GOVERNANCE_SUSPENSION','PREMIUM_SEALING','OPTIONAL_TRANSPARENCY',
                 'CONFIGURABLE_RECOVERY_WINDOWS','CIRCLE_SCOPED_EXPORT','SEALED_EXPORT'
             )
        ) OR (
            SELECT count(*) FROM jsonb_array_elements_text(NEW.body->'capabilities')
        ) <> (
            SELECT count(DISTINCT capability.code)
              FROM jsonb_array_elements_text(NEW.body->'capabilities') AS capability(code)
        ) THEN
            RAISE EXCEPTION 'SE_GOVERNEDRULE_PLATFORM_INVALID'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF NEW.code = 'FREE_TIER'
           AND NEW.body->'capabilities' <> '[]'::jsonb THEN
            RAISE EXCEPTION 'SE_GOVERNEDRULE_PLATFORM_INVALID'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF NEW.version = 1 AND (
            NEW.authority_reference IS DISTINCT FROM (
                CASE
                    WHEN NEW.code = 'BETA_COMPLIMENTARY_ACCESS'
                        THEN 'ruling:HG_RULING_HD_X_1_D1_CAPABILITY_SET_AND_DASHBOARD_20261011'
                    ELSE 'ruling:HG_RULING_SE_D_SE_1_NOT_SET_MEASURED_NOT_ENFORCED_20261011'
                END
            )
            OR NEW.effective_from <> '0001-01-01 00:00:00+00'::timestamptz
            OR NEW.decided_at <> '0001-01-01 00:00:00+00'::timestamptz
            OR NEW.enterer_identity_id IS NOT NULL
            OR NEW.observations IS NOT NULL
            OR (NEW.code = 'BETA_COMPLIMENTARY_ACCESS'
                AND NEW.body IS DISTINCT FROM '@BETA_RULE_BODY_JSON@'::jsonb)
            OR (NEW.code = 'FREE_TIER'
                AND NEW.body IS DISTINCT FROM '@FREE_RULE_BODY_JSON@'::jsonb)
        ) THEN
            RAISE EXCEPTION 'SE_GOVERNEDRULE_PLATFORM_INVALID'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;
"""
    insertion = (
        insertion.replace("@BETA_RULE_BODY_JSON@", BETA_RULE_BODY_JSON)
        .replace("@FREE_RULE_BODY_JSON@", FREE_RULE_BODY_JSON)
    )
    marker = "    IF jsonb_typeof(NEW.body) IS DISTINCT FROM 'object' THEN"
    if body.count(marker) != 1:
        raise RuntimeError("0045 governed-rule body branch no longer matches the SE migration")
    return body.replace(marker, insertion + marker, 1)


_GOVERNEDRULE_GUARD_BODY = _governed_rule_guard_body()

_LIMITS_VALID_BODY = r"""
DECLARE
    v_entry record;
    v_kind text;
BEGIN
    IF jsonb_typeof(p_limits) IS DISTINCT FROM 'object' THEN
        RETURN FALSE;
    END IF;
    IF p_limits - ARRAY['circles','organism_learners','document_footprint']::text[]
            <> '{}'::jsonb
       OR NOT (p_limits ?& ARRAY['circles','organism_learners','document_footprint']) THEN
        RETURN FALSE;
    END IF;
    FOR v_entry IN SELECT * FROM jsonb_each(p_limits) LOOP
        IF jsonb_typeof(v_entry.value) IS DISTINCT FROM 'object' THEN
            RETURN FALSE;
        END IF;
        v_kind := v_entry.value->>'kind';
        IF v_kind = 'LIMIT' THEN
            IF v_entry.value - ARRAY['kind','value']::text[] <> '{}'::jsonb
               OR (SELECT count(*) FROM jsonb_each(v_entry.value)) <> 2
               OR jsonb_typeof(v_entry.value->'value') IS DISTINCT FROM 'number'
               OR (v_entry.value->>'value') !~ '^(0|[1-9][0-9]*)$'
               OR (v_entry.value->>'value')::numeric < 0 THEN
                RETURN FALSE;
            END IF;
        ELSIF v_kind IN ('UNLIMITED','NOT_SET') THEN
            IF v_entry.value - ARRAY['kind']::text[] <> '{}'::jsonb
               OR (SELECT count(*) FROM jsonb_each(v_entry.value)) <> 1 THEN
                RETURN FALSE;
            END IF;
        ELSE
            RETURN FALSE;
        END IF;
    END LOOP;
    RETURN TRUE;
END
"""

_ENTITLEMENT_GUARD_BODY = r"""
DECLARE
    v_unknown_capability text;
BEGIN
    IF jsonb_typeof(NEW.capabilities) IS DISTINCT FROM 'array'
       OR NOT public.se_entitlement_limits_valid(NEW.limits)
       OR NEW.effective_until IS NOT NULL
          AND NEW.effective_until <= NEW.effective_at THEN
        RAISE EXCEPTION 'SE_ENTITLEMENT_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT capability.code
      INTO v_unknown_capability
      FROM jsonb_array_elements_text(NEW.capabilities) AS capability(code)
     WHERE capability.code NOT IN (
         'APPLICATION_CIRCLE','FINANCE_CIRCLE','OPERATIONS_CIRCLE',
         'UNLIMITED_CIRCLES','PROBATION','MENTORSHIP','CUSTOM_ROLES',
         'GOVERNANCE_SUSPENSION','PREMIUM_SEALING','OPTIONAL_TRANSPARENCY',
         'CONFIGURABLE_RECOVERY_WINDOWS','CIRCLE_SCOPED_EXPORT','SEALED_EXPORT'
     )
     LIMIT 1;
    IF v_unknown_capability IS NOT NULL THEN
        RAISE EXCEPTION 'SE_ENTITLEMENT_CAPABILITY_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (
        SELECT count(*)
          FROM jsonb_array_elements_text(NEW.capabilities) AS capability(code)
    ) <> (
        SELECT count(DISTINCT capability.code)
          FROM jsonb_array_elements_text(NEW.capabilities) AS capability(code)
    ) THEN
        RAISE EXCEPTION 'SE_ENTITLEMENT_CAPABILITY_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.basis = 'BETA_OPT_IN'
       AND (NEW.source <> 'COMPLIMENTARY'
            OR NEW.effective_until IS NOT NULL
            OR NEW.ending_condition_reference IS NULL) THEN
        RAISE EXCEPTION 'SE_ENTITLEMENT_BETA_SHAPE_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
"""

_POLICY_TEXT_GUARD_BODY = r"""
BEGIN
    IF NEW.policy_version IS NULL OR NEW.policy_version <= 0
       OR NEW.accepted_text IS NULL OR btrim(NEW.accepted_text) = ''
       OR NEW.text_sha256 !~ '^[0-9a-f]{64}$'
       OR encode(sha256(convert_to(NEW.accepted_text, 'UTF8')), 'hex')
            IS DISTINCT FROM NEW.text_sha256 THEN
        RAISE EXCEPTION 'SE_POLICY_TEXT_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
"""

_ENTITLEMENT_EVENT_GUARD_BODY = r"""
DECLARE
    v_latest_id bigint;
    v_latest_sequence integer;
    v_entitlement_organism_id bigint;
    v_entitlement_source text;
    v_entitlement_basis text;
    v_entitlement_capabilities jsonb;
    v_entitlement_limits jsonb;
    v_rule_code text;
    v_rule_uuid uuid;
    v_rule_version integer;
    v_rule_body jsonb;
BEGIN
    IF NEW.actor_state NOT IN ('PARTY','NONE','NOT_ESTABLISHED')
       OR NEW.actor_capacity NOT IN ('STANDING','NA_NO_PARTY')
       OR (NEW.actor_state = 'PARTY') IS DISTINCT FROM (NEW.actor_id IS NOT NULL)
       OR (NEW.actor_state = 'PARTY') IS DISTINCT FROM (NEW.actor_access_epoch IS NOT NULL)
       OR (NEW.actor_state = 'PARTY') IS DISTINCT FROM (NEW.actor_capacity = 'STANDING')
       OR NEW.entry_mode NOT IN ('TRANSPORT','COMPOSED')
       OR NEW.composing_rule_state NOT IN ('NA_TRANSPORT','REFERENCE','NOT_NAMED')
       OR (NEW.entry_mode = 'TRANSPORT')
            IS DISTINCT FROM (NEW.composing_rule_state = 'NA_TRANSPORT')
       OR (NEW.composing_rule_state = 'REFERENCE')
            IS DISTINCT FROM (NEW.composing_rule_id IS NOT NULL)
       OR (NEW.composing_rule_state = 'REFERENCE')
            IS DISTINCT FROM (NEW.composing_rule_reference IS NOT NULL)
       OR NEW.composer_state NOT IN ('REFERENCE','NOT_ESTABLISHED','NA_TRANSPORT')
       OR (NEW.entry_mode = 'TRANSPORT')
            IS DISTINCT FROM (NEW.composer_state = 'NA_TRANSPORT')
       OR (NEW.composer_state = 'REFERENCE')
            IS DISTINCT FROM (NEW.composer_identity_id IS NOT NULL)
       OR (NEW.actor_state = 'NONE'
            AND (NEW.entry_mode <> 'COMPOSED'
                 OR NEW.composing_rule_state <> 'REFERENCE')) THEN
        RAISE EXCEPTION 'SE_ENTITLEMENT_EVENT_ATTRIBUTION_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT e.living_organism_id, e.source, e.basis, e.capabilities, e.limits
      INTO v_entitlement_organism_id, v_entitlement_source, v_entitlement_basis,
           v_entitlement_capabilities, v_entitlement_limits
      FROM public.core_organismentitlement e
     WHERE e.id = NEW.entitlement_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'SE_ENTITLEMENT_EVENT_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT event.id, event.sequence
      INTO v_latest_id, v_latest_sequence
      FROM public.core_organismentitlementevent event
     WHERE event.entitlement_id = NEW.entitlement_id
     ORDER BY event.sequence DESC
     LIMIT 1;
    IF v_latest_id IS NULL THEN
        IF NEW.sequence <> 1 OR NEW.predecessor_id IS NOT NULL THEN
            RAISE EXCEPTION 'SE_ENTITLEMENT_EVENT_CHAIN_INVALID'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSIF NEW.sequence <> v_latest_sequence + 1
       OR NEW.predecessor_id IS DISTINCT FROM v_latest_id THEN
        RAISE EXCEPTION 'SE_ENTITLEMENT_EVENT_CHAIN_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.event_type = 'GRANTED' THEN
        IF NEW.actor_state = 'PARTY' OR NEW.entry_mode = 'TRANSPORT' THEN
            RAISE EXCEPTION 'SE_GRANT_NOT_BY_HAND'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        SELECT r.code, r.rule_uuid, r.version, r.body
          INTO v_rule_code, v_rule_uuid, v_rule_version, v_rule_body
          FROM public.core_governedrule r
         WHERE r.id = NEW.composing_rule_id
           AND r.scope_kind = 'PLATFORM'
           AND r.source = 'PLATFORM_RULING';
        IF NEW.sequence <> 1
           OR v_entitlement_source <> 'COMPLIMENTARY'
           OR v_entitlement_basis <> 'BETA_OPT_IN'
           OR NEW.actor_state <> 'NONE'
           OR NEW.entry_mode <> 'COMPOSED'
           OR NEW.deciding_identity_id IS NOT NULL
           OR NEW.authoriser_identity_id IS NOT NULL
           OR NEW.authoriser_office IS NOT NULL
           OR NEW.reason IS NOT NULL
           OR NEW.composing_rule_state <> 'REFERENCE'
           OR NEW.composing_rule_reference IS NULL
           OR NEW.composer_state <> 'NOT_ESTABLISHED'
           OR NEW.composer_identity_id IS NOT NULL
           OR v_rule_code IS DISTINCT FROM 'BETA_COMPLIMENTARY_ACCESS'
           OR v_rule_uuid IS DISTINCT FROM
                'c81640de-f776-5b31-a046-0046e0000001'::uuid
           OR NEW.composing_rule_reference IS DISTINCT FROM
                'rule:' || v_rule_uuid::text || '@' || v_rule_version::text
           OR v_entitlement_capabilities IS DISTINCT FROM v_rule_body->'capabilities'
           OR v_entitlement_limits IS DISTINCT FROM v_rule_body->'limits' THEN
            RAISE EXCEPTION 'SE_ENTITLEMENT_GRANT_SHAPE_INVALID'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSIF NEW.event_type = 'EXTENDED' THEN
        IF NEW.actor_state <> 'PARTY'
           OR NEW.entry_mode <> 'TRANSPORT'
           OR NEW.actor_id IS NULL
           OR NEW.enterer_identity_id IS NULL
           OR NEW.deciding_identity_id IS NULL
           OR NEW.authoriser_identity_id IS DISTINCT FROM NEW.deciding_identity_id
           OR NEW.authoriser_office IS DISTINCT FROM 'RELATIONSHIP_CIRCLE_COORDINATOR'
           OR NEW.effective_at IS DISTINCT FROM NEW.received_at
           OR NEW.reason IS NULL
           OR btrim(NEW.reason) = ''
           OR NEW.composer_state <> 'NA_TRANSPORT'
           OR NEW.composer_identity_id IS NOT NULL THEN
            RAISE EXCEPTION 'SE_ENTITLEMENT_EXTENSION_SHAPE_INVALID'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF EXISTS (
            SELECT 1
              FROM public.core_organismmembership membership
             WHERE membership.living_organism_id = v_entitlement_organism_id
               AND membership.identity_id = NEW.actor_id
               AND public.s015_fold_effective_state(
                    'core_organismmembership',
                    membership.id,
                    NEW.received_at,
                    NEW.received_at
               ) = 'ACTIVE'
        ) THEN
            RAISE EXCEPTION 'SE_SELF_EXTENSION'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        -- Fail closed in v1.0 until H-7 supplies the office and root-policy route (E-4; "Extensions").
        RAISE EXCEPTION 'SE_EXTENSION_AUTHORITY'
            USING ERRCODE = 'integrity_constraint_violation';
    ELSIF NEW.event_type NOT IN ('GRACE_BEGUN','GRACE_ENDED','ENDED','REPLACED') THEN
        RAISE EXCEPTION 'SE_ENTITLEMENT_EVENT_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.effective_at > NEW.occurred_at
       OR NEW.received_at < NEW.occurred_at THEN
        RAISE EXCEPTION 'SE_ENTITLEMENT_EVENT_TIME_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
"""

_ENTITLEMENT_EFFECTIVE_BODY = r"""
SELECT EXISTS (
    SELECT 1
      FROM public.core_organismentitlement entitlement
      JOIN public.core_livingorganism organism
        ON organism.id = entitlement.living_organism_id
     WHERE organism.organism_id = p_living_organism_uuid
       AND entitlement.effective_at <= p_at
       AND (
            entitlement.effective_until IS NULL
            OR p_at < entitlement.effective_until
            OR (
                entitlement.source = 'PAID'
                AND p_at < entitlement.effective_until + INTERVAL '7 days'
            )
       )
       AND entitlement.capabilities ? p_capability
)
"""

_ENTITLEMENT_LIMIT_BODY = r"""
DECLARE
    v_organism_id bigint;
    v_limit jsonb;
    v_kind text;
    v_value bigint;
BEGIN
    IF p_dimension NOT IN ('circles', 'organism_learners', 'document_footprint') THEN
        RAISE EXCEPTION 'SE entitlement dimension invalid'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT organism.id
      INTO v_organism_id
      FROM public.core_livingorganism organism
     WHERE organism.organism_id = p_living_organism_uuid;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'SE entitlement organism unavailable'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT entitlement.limits->p_dimension
      INTO v_limit
      FROM public.core_organismentitlement entitlement
     WHERE entitlement.living_organism_id = v_organism_id
       AND entitlement.effective_at <= p_at
       AND (
            entitlement.effective_until IS NULL
            OR p_at < entitlement.effective_until
            OR (
                entitlement.source = 'PAID'
                AND p_at < entitlement.effective_until + INTERVAL '7 days'
            )
       )
     ORDER BY entitlement.effective_at DESC, entitlement.recorded_at DESC, entitlement.id DESC
     LIMIT 1;
    IF NOT FOUND THEN
        SELECT rule.body->'limits'->p_dimension
          INTO v_limit
          FROM public.core_governedrule rule
         WHERE rule.scope_kind = 'PLATFORM'
           AND rule.scope_organism_id IS NULL
           AND rule.scope_circle_id IS NULL
           AND rule.code = 'FREE_TIER'
           AND rule.effective_from <= p_at
           AND rule.recorded_at <= CURRENT_TIMESTAMP
         ORDER BY rule.effective_from DESC, rule.recorded_at DESC, rule.version DESC, rule.id DESC
         LIMIT 1;
    END IF;
    IF v_limit IS NULL OR jsonb_typeof(v_limit) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'SE entitlement limit unavailable'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    v_kind := v_limit->>'kind';
    IF v_kind = 'LIMIT' THEN
        v_value := (v_limit->>'value')::bigint;
    ELSIF v_kind IN ('UNLIMITED', 'NOT_SET') THEN
        v_value := NULL;
    ELSE
        RAISE EXCEPTION 'SE entitlement limit invalid'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN QUERY SELECT v_kind, v_value;
END
"""

def _entitlement_election_gateway_body():
    """Extend the 0045 gateway guard with disjoint SE election payload validation."""
    formation = import_module("core.migrations.0045_sbg_governance_formation")
    body = formation.NEW_S3FU_GUARD_GATEWAY_EFFECT_BODY
    body = formation._replace_once(
        body,
        "IF NEW.action NOT IN "
        "('EFFECT_APPLICATION_SETTINGS','EFFECT_APPLICATION_QUESTION',"
        "'EFFECT_GOVERNANCE_CONFIGURATION','PHASE_ADVANCE',"
        "'ADOPT_GOVERNING_INSTRUMENT') THEN",
        "IF NEW.action NOT IN "
        "('EFFECT_APPLICATION_SETTINGS','EFFECT_APPLICATION_QUESTION',"
        "'EFFECT_GOVERNANCE_CONFIGURATION','EFFECT_ENTITLEMENT_ELECTION',"
        "'PHASE_ADVANCE','ADOPT_GOVERNING_INSTRUMENT') THEN",
        "entitlement election admitted-action",
    )
    body = formation._replace_once(
        body,
        "version_table := CASE NEW.action",
        "version_table := CASE NEW.action\n"
        "            WHEN 'EFFECT_ENTITLEMENT_ELECTION' "
        "THEN 'core_loapplicationsettingsversion'",
        "entitlement election version table",
    )
    body = formation._replace_once(
        body,
        "prefix := CASE NEW.action",
        "prefix := CASE NEW.action\n"
        "            WHEN 'EFFECT_ENTITLEMENT_ELECTION' "
        "THEN 'application-settings-version:'",
        "entitlement election version reference",
    )
    body = formation._replace_once(
        body,
        "IF NEW.action = 'EFFECT_APPLICATION_SETTINGS' THEN",
        "    -- SE-GATE-01 fixed under HG_DECISION_SE_GATE_01_FIX_IN_SE_20261012: "
        "settings and election payloads remain disjoint; shared gateway fields are unchanged.\n"
        "IF NEW.action = 'EFFECT_ENTITLEMENT_ELECTION' AND ("
        "COALESCE(version_row->>'entitlement_choice', '') NOT IN ('FREE','COMPLIMENTARY_BETA') "
        "OR COALESCE(version_row->>'entitlement_election_route', '') "
        "NOT IN ('BIRTHING','POST_BIRTHING') "
        "OR COALESCE((version_row->>'entitlement_policy_version')::integer, 0) <= 0 "
        "OR COALESCE(version_row->>'entitlement_policy_text_sha256', '') !~ '^[0-9a-f]{64}$' "
        "OR nullif(btrim(version_row->>'entitlement_authority_source'), '') IS NULL "
        "OR nullif(btrim(version_row->>'entitlement_authority_version'), '') IS NULL) THEN\n"
        "        RAISE EXCEPTION 'SE_ENTITLEMENT_ELECTION_INVALID' "
        "USING ERRCODE = 'integrity_constraint_violation';\n"
        "    END IF;\n"
        "    IF NEW.action IN ('EFFECT_APPLICATION_SETTINGS','EFFECT_ENTITLEMENT_ELECTION') THEN",
        "entitlement election version validation",
    )
    body = formation._replace_once(
        body,
        "    IF NEW.action IN ('EFFECT_APPLICATION_SETTINGS','EFFECT_ENTITLEMENT_ELECTION') THEN",
        "    IF NEW.action = 'EFFECT_APPLICATION_SETTINGS' AND ("
        "version_row->>'entitlement_choice' IS NOT NULL "
        "OR version_row->>'entitlement_election_route' IS NOT NULL "
        "OR version_row->>'entitlement_policy_version' IS NOT NULL "
        "OR version_row->>'entitlement_policy_text_sha256' IS NOT NULL "
        "OR version_row->>'entitlement_authority_source' IS NOT NULL "
        "OR version_row->>'entitlement_authority_version' IS NOT NULL) THEN\n"
        "        RAISE EXCEPTION 'S3FU gateway effect: version incomplete' "
        "USING ERRCODE = 'integrity_constraint_violation';\n"
        "    END IF;\n"
        "    IF NEW.action = 'EFFECT_ENTITLEMENT_ELECTION' AND ("
        "version_row->>'applications_open' IS NOT NULL "
        "OR version_row->>'automatic_acceptance' IS NOT NULL "
        "OR version_row->>'processing_responsibility' IS NOT NULL) THEN\n"
        "        RAISE EXCEPTION 'S3FU gateway effect: version incomplete' "
        "USING ERRCODE = 'integrity_constraint_violation';\n"
        "    END IF;\n"
        "    IF NEW.action = 'EFFECT_APPLICATION_SETTINGS' THEN",
        "SE-GATE-01 gateway payload partition",
    )
    body = formation._replace_once(
        body,
        "    ELSIF version_row->>'cq_active' IS NULL OR version_row->>'inherit_portal_cq_settings' IS NULL\n"
        "          OR version_row->>'require_cq_to_change_lo_settings' IS NULL\n"
        "          OR version_row->>'require_cq_to_change_application_question' IS NULL THEN",
        "    ELSIF NEW.action = 'EFFECT_GOVERNANCE_CONFIGURATION' AND (\n"
        "          version_row->>'cq_active' IS NULL\n"
        "          OR version_row->>'inherit_portal_cq_settings' IS NULL\n"
        "          OR version_row->>'require_cq_to_change_lo_settings' IS NULL\n"
        "          OR version_row->>'require_cq_to_change_application_question' IS NULL) THEN",
        "SE-GATE-01 governance payload validation",
    )
    return body


_ENTITLEMENT_ELECTION_GATEWAY_BODY = _entitlement_election_gateway_body()

SE_FUNCTION_BODIES = (
    ("sbg_guard_governedrule", _GOVERNEDRULE_GUARD_BODY),
    ("se_entitlement_limits_valid", _LIMITS_VALID_BODY),
    ("s015_guard_organismentitlement", _ENTITLEMENT_GUARD_BODY),
    ("s015_guard_organismentitlementpolicytext", _POLICY_TEXT_GUARD_BODY),
    ("s015_guard_organismentitlementevent", _ENTITLEMENT_EVENT_GUARD_BODY),
    ("s015_entitlement_effective", _ENTITLEMENT_EFFECTIVE_BODY),
    ("s015_entitlement_limit", _ENTITLEMENT_LIMIT_BODY),
    ("s3fu_guard_gateway_effect", _ENTITLEMENT_ELECTION_GATEWAY_BODY),
)

FUNCTION_SQL = (
    import_module("core.migrations.0045_sbg_governance_formation")._function_sql(
        "sbg_guard_governedrule", _GOVERNEDRULE_GUARD_BODY
    ),
    "CREATE OR REPLACE FUNCTION public.se_entitlement_limits_valid(p_limits jsonb) "
    "RETURNS boolean LANGUAGE plpgsql IMMUTABLE STRICT AS $$"
    + _LIMITS_VALID_BODY
    + "$$",
    import_module("core.migrations.0045_sbg_governance_formation")._function_sql(
        "s015_guard_organismentitlement", _ENTITLEMENT_GUARD_BODY
    ),
    import_module("core.migrations.0045_sbg_governance_formation")._function_sql(
        "s015_guard_organismentitlementpolicytext", _POLICY_TEXT_GUARD_BODY
    ),
    import_module("core.migrations.0045_sbg_governance_formation")._function_sql(
        "s015_guard_organismentitlementevent", _ENTITLEMENT_EVENT_GUARD_BODY
    ),
    "CREATE OR REPLACE FUNCTION public.s015_entitlement_effective("
    "p_living_organism_uuid uuid, p_capability text, p_at timestamptz) "
    "RETURNS boolean LANGUAGE sql STABLE STRICT AS $$"
    + _ENTITLEMENT_EFFECTIVE_BODY
    + "$$",
    "CREATE OR REPLACE FUNCTION public.s015_entitlement_limit("
    "p_living_organism_uuid uuid, p_dimension text, p_at timestamptz) "
    "RETURNS TABLE(kind text, value bigint) LANGUAGE plpgsql STABLE STRICT AS $$"
    + _ENTITLEMENT_LIMIT_BODY
    + "$$",
    import_module("core.migrations.0045_sbg_governance_formation")._function_sql(
        "s3fu_guard_gateway_effect", _ENTITLEMENT_ELECTION_GATEWAY_BODY
    ),
)

TRIGGER_SQL = (
    "CREATE TRIGGER s015_organismentitlement_record_time BEFORE INSERT "
    "ON public.core_organismentitlement FOR EACH ROW "
    "EXECUTE FUNCTION public.s3_assign_record_time()",
    "CREATE TRIGGER s015_organismentitlement_validate BEFORE INSERT "
    "ON public.core_organismentitlement FOR EACH ROW "
    "EXECUTE FUNCTION public.s015_guard_organismentitlement()",
    "CREATE TRIGGER s015_organismentitlement_immutable BEFORE UPDATE OR DELETE "
    "ON public.core_organismentitlement FOR EACH ROW "
    "EXECUTE FUNCTION public.s015_refuse_row_mutation()",
    "CREATE TRIGGER s015_organismentitlement_truncate_immutable BEFORE TRUNCATE "
    "ON public.core_organismentitlement FOR EACH STATEMENT "
    "EXECUTE FUNCTION public.s015_refuse_row_mutation()",
    "CREATE TRIGGER s015_organismentitlementevent_record_time BEFORE INSERT "
    "ON public.core_organismentitlementevent FOR EACH ROW "
    "EXECUTE FUNCTION public.s3_assign_record_time()",
    "CREATE TRIGGER s015_organismentitlementevent_validate BEFORE INSERT "
    "ON public.core_organismentitlementevent FOR EACH ROW "
    "EXECUTE FUNCTION public.s015_guard_organismentitlementevent()",
    "CREATE TRIGGER s015_organismentitlementevent_immutable BEFORE UPDATE OR DELETE "
    "ON public.core_organismentitlementevent FOR EACH ROW "
    "EXECUTE FUNCTION public.s015_refuse_row_mutation()",
    "CREATE TRIGGER s015_organismentitlementevent_truncate_immutable BEFORE TRUNCATE "
    "ON public.core_organismentitlementevent FOR EACH STATEMENT "
    "EXECUTE FUNCTION public.s015_refuse_row_mutation()",
    "CREATE TRIGGER s015_organismentitlementpolicytext_record_time BEFORE INSERT "
    "ON public.core_organismentitlementpolicytext FOR EACH ROW "
    "EXECUTE FUNCTION public.s3_assign_record_time()",
    "CREATE TRIGGER s015_organismentitlementpolicytext_validate BEFORE INSERT "
    "ON public.core_organismentitlementpolicytext FOR EACH ROW "
    "EXECUTE FUNCTION public.s015_guard_organismentitlementpolicytext()",
    "CREATE TRIGGER s015_organismentitlementpolicytext_immutable BEFORE UPDATE OR DELETE "
    "ON public.core_organismentitlementpolicytext FOR EACH ROW "
    "EXECUTE FUNCTION public.s015_refuse_row_mutation()",
    "CREATE TRIGGER s015_organismentitlementpolicytext_truncate_immutable BEFORE TRUNCATE "
    "ON public.core_organismentitlementpolicytext FOR EACH STATEMENT "
    "EXECUTE FUNCTION public.s015_refuse_row_mutation()",
)

_SEED_RULES_BODY = r"""
DECLARE
    v_existing public.core_governedrule%ROWTYPE;
BEGIN
    SELECT r.* INTO v_existing
      FROM public.core_governedrule r
     WHERE r.rule_uuid = 'c81640de-f776-5b31-a046-0046e0000001'::uuid;
    IF FOUND THEN
        IF v_existing.version <> 1
           OR v_existing.code <> 'BETA_COMPLIMENTARY_ACCESS'
           OR v_existing.body IS DISTINCT FROM '@BETA_RULE_BODY_JSON@'::jsonb
           OR v_existing.authority_reference <> '@BETA_RULE_AUTHORITY@'
           OR v_existing.source <> 'PLATFORM_RULING'
           OR v_existing.text_commitment IS NULL THEN
            RAISE EXCEPTION 'SE platform seed conflicts with the BETA rule'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSE
        INSERT INTO public.core_governedrule (
            rule_uuid, version, scope_kind, scope_organism_id, scope_circle_id,
            code, body, text_commitment, authority_reference, source,
            effective_from, decided_at, observations, enterer_identity_id, recorded_at
        ) VALUES (
            'c81640de-f776-5b31-a046-0046e0000001'::uuid, 1, 'PLATFORM', NULL, NULL,
            'BETA_COMPLIMENTARY_ACCESS',
            '@BETA_RULE_BODY_JSON@'::jsonb,
            NULL, '@BETA_RULE_AUTHORITY@', 'PLATFORM_RULING',
            '0001-01-01 00:00:00+00'::timestamptz,
            '0001-01-01 00:00:00+00'::timestamptz, NULL, NULL, NULL
        );
    END IF;

    SELECT r.* INTO v_existing
      FROM public.core_governedrule r
     WHERE r.rule_uuid = 'c81640de-f776-5b31-a046-0046e0000002'::uuid;
    IF FOUND THEN
        IF v_existing.version <> 1
           OR v_existing.code <> 'FREE_TIER'
           OR v_existing.body IS DISTINCT FROM '@FREE_RULE_BODY_JSON@'::jsonb
           OR v_existing.authority_reference <> '@FREE_RULE_AUTHORITY@'
           OR v_existing.source <> 'PLATFORM_RULING'
           OR v_existing.text_commitment IS NULL THEN
            RAISE EXCEPTION 'SE platform seed conflicts with the FREE_TIER rule'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSE
        INSERT INTO public.core_governedrule (
            rule_uuid, version, scope_kind, scope_organism_id, scope_circle_id,
            code, body, text_commitment, authority_reference, source,
            effective_from, decided_at, observations, enterer_identity_id, recorded_at
        ) VALUES (
            'c81640de-f776-5b31-a046-0046e0000002'::uuid, 1, 'PLATFORM', NULL, NULL,
            'FREE_TIER',
            '@FREE_RULE_BODY_JSON@'::jsonb,
            NULL, '@FREE_RULE_AUTHORITY@', 'PLATFORM_RULING',
            '0001-01-01 00:00:00+00'::timestamptz,
            '0001-01-01 00:00:00+00'::timestamptz, NULL, NULL, NULL
        );
    END IF;
END
"""
_SEED_RULES_BODY = (
    _SEED_RULES_BODY.replace("@BETA_RULE_BODY_JSON@", BETA_RULE_BODY_JSON)
    .replace("@FREE_RULE_BODY_JSON@", FREE_RULE_BODY_JSON)
    .replace("@BETA_RULE_AUTHORITY@", BETA_RULE_AUTHORITY)
    .replace("@FREE_RULE_AUTHORITY@", FREE_RULE_AUTHORITY)
)

SEED_SQL = (
    import_module("core.migrations.0045_sbg_governance_formation")._function_sql(
        "se_seed_entitlement_rules", _SEED_RULES_BODY, return_type="void"
    ),
    "SELECT public.se_seed_entitlement_rules()",
)


def install(apps, schema_editor):
    """Install PostgreSQL guards and seed the two commissioned platform rules."""
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for statement in FUNCTION_SQL:
            cursor.execute(statement)
        for statement in TRIGGER_SQL:
            cursor.execute(statement)
        for statement in SEED_SQL:
            cursor.execute(statement)


def restore_entitlement_rules(apps, schema_editor):
    """Restore the SE-owned platform rule seeds after an S015 database reset."""
    database_connection = schema_editor.connection if schema_editor is not None else connection
    if database_connection.vendor != "postgresql":
        return
    with database_connection.cursor() as cursor:
        cursor.execute("SELECT public.se_seed_entitlement_rules()")


def uninstall(apps, schema_editor):
    """Refuse reversal with SE data, remove only the two owned seeds, and restore 0045."""
    if schema_editor.connection.vendor != "postgresql":
        return
    formation = import_module("core.migrations.0045_sbg_governance_formation")
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM public.core_organismentitlement) "
            "OR EXISTS (SELECT 1 FROM public.core_organismentitlementevent) "
            "OR EXISTS (SELECT 1 FROM public.core_organismentitlementpolicytext)"
        )
        if cursor.fetchone()[0]:
            raise RuntimeError("0046 reverse refused while SE entitlement data exists")
        cursor.execute(
            "SELECT rule_uuid::text, version, code FROM public.core_governedrule "
            "WHERE code IN ('BETA_COMPLIMENTARY_ACCESS','FREE_TIER') "
            "ORDER BY code"
        )
        rules = cursor.fetchall()
        expected = [
            (BETA_RULE_UUID, 1, "BETA_COMPLIMENTARY_ACCESS"),
            (FREE_RULE_UUID, 1, "FREE_TIER"),
        ]
        if rules != expected:
            raise RuntimeError("0046 reverse refused while SE governed-rule history differs")
        cursor.execute(
            "DROP TRIGGER IF EXISTS sbg_governedrule_immutable "
            "ON public.core_governedrule"
        )
        cursor.execute(
            "DELETE FROM public.core_governedrule "
            "WHERE rule_uuid IN (%s::uuid, %s::uuid)",
            [BETA_RULE_UUID, FREE_RULE_UUID],
        )
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute(
            "CREATE TRIGGER sbg_governedrule_immutable BEFORE UPDATE OR DELETE "
            "ON public.core_governedrule FOR EACH ROW "
            "EXECUTE FUNCTION public.s015_refuse_row_mutation()"
        )
        cursor.execute(
            formation._function_sql(
                "sbg_guard_governedrule",
                formation._GOVERNEDRULE_GUARD_BODY,
            )
        )
        cursor.execute(
            formation._function_sql(
                "s3fu_guard_gateway_effect",
                formation.NEW_S3FU_GUARD_GATEWAY_EFFECT_BODY,
            )
        )
        for trigger_name, table_name in (
            ("s015_organismentitlementevent_truncate_immutable", "core_organismentitlementevent"),
            ("s015_organismentitlementevent_immutable", "core_organismentitlementevent"),
            ("s015_organismentitlementevent_validate", "core_organismentitlementevent"),
            ("s015_organismentitlementevent_record_time", "core_organismentitlementevent"),
            ("s015_organismentitlementpolicytext_truncate_immutable", "core_organismentitlementpolicytext"),
            ("s015_organismentitlementpolicytext_immutable", "core_organismentitlementpolicytext"),
            ("s015_organismentitlementpolicytext_validate", "core_organismentitlementpolicytext"),
            ("s015_organismentitlementpolicytext_record_time", "core_organismentitlementpolicytext"),
            ("s015_organismentitlement_truncate_immutable", "core_organismentitlement"),
            ("s015_organismentitlement_immutable", "core_organismentitlement"),
            ("s015_organismentitlement_validate", "core_organismentitlement"),
            ("s015_organismentitlement_record_time", "core_organismentitlement"),
        ):
            cursor.execute(
                f"DROP TRIGGER IF EXISTS {trigger_name} ON public.{table_name}"
            )
        cursor.execute(
            "DROP FUNCTION IF EXISTS public.se_seed_entitlement_rules()"
        )
        cursor.execute(
            "DROP FUNCTION IF EXISTS public.s015_guard_organismentitlementevent()"
        )
        cursor.execute(
            "DROP FUNCTION IF EXISTS public.s015_guard_organismentitlementpolicytext()"
        )
        cursor.execute(
            "DROP FUNCTION IF EXISTS public.s015_guard_organismentitlement()"
        )
        cursor.execute(
            "DROP FUNCTION IF EXISTS public.se_entitlement_limits_valid(jsonb)"
        )
        cursor.execute(
            "DROP FUNCTION IF EXISTS public.s015_entitlement_effective(uuid, text, timestamptz)"
        )
        cursor.execute(
            "DROP FUNCTION IF EXISTS public.s015_entitlement_limit(uuid, text, timestamptz)"
        )


class Migration(migrations.Migration):
    """SE entitlement foundation: adds the entitlement record, its event chain and the
    policy-text record; records the offering election on the LO settings chain
    (commission §4.1) by adding the entitlement election fields to
    core_loapplicationsettingsversion and admitting EFFECT_ENTITLEMENT_ELECTION in
    s3fu_guard_gateway_effect; replaces sbg_guard_governedrule to admit the two
    seeded entitlement rules."""

    dependencies = [
        ("core", PREVIOUS_MIGRATION),
    ]

    operations = [
        migrations.CreateModel(
            name="OrganismEntitlementPolicyText",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("policy_version", models.PositiveIntegerField()),
                ("accepted_text", models.TextField()),
                ("text_sha256", models.CharField(max_length=64)),
                ("recorded_at", models.DateTimeField(editable=False, null=True)),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(fields=("policy_version",), name="se_entitlement_policy_version_uniq"),
                    models.CheckConstraint(condition=models.Q(("policy_version__gt", 0)), name="se_entitlementpolicytext_policy_version_positive_ck"),
                ],
            },
        ),
        migrations.CreateModel(
            name="OrganismEntitlement",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("entitlement_uuid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("source", models.CharField(choices=[("PAID", "PAID"), ("COMPLIMENTARY", "COMPLIMENTARY"), ("TRIAL", "TRIAL")], max_length=16)),
                ("basis", models.CharField(choices=[("BETA_OPT_IN", "BETA_OPT_IN"), ("TRANSITION_TRIAL", "TRANSITION_TRIAL"), ("AUTHORISED_EXTENSION", "AUTHORISED_EXTENSION"), ("PARTNER", "PARTNER"), ("PAID", "PAID")], max_length=24)),
                ("granting_party_id", models.CharField(blank=True, max_length=255, null=True)),
                ("policy_reference", models.CharField(max_length=255)),
                ("policy_version", models.PositiveIntegerField()),
                ("capabilities", models.JSONField()),
                ("limits", models.JSONField()),
                ("effective_at", models.DateTimeField()),
                ("effective_until", models.DateTimeField(blank=True, null=True)),
                ("ending_condition_reference", models.CharField(blank=True, max_length=255, null=True)),
                ("tier_reference", models.CharField(blank=True, max_length=255, null=True)),
                ("pricing_basis_reference", models.CharField(blank=True, max_length=255, null=True)),
                ("recorded_at", models.DateTimeField(editable=False, null=True)),
                ("living_organism", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="entitlements", to="core.livingorganism")),
            ],
            options={
                "constraints": [
                    models.CheckConstraint(condition=models.Q(("source__in", ("PAID", "COMPLIMENTARY", "TRIAL"))), name="se_entitlement_source_vocab_ck"),
                    models.CheckConstraint(condition=models.Q(("basis__in", ("BETA_OPT_IN", "TRANSITION_TRIAL", "AUTHORISED_EXTENSION", "PARTNER", "PAID"))), name="se_entitlement_basis_vocab_ck"),
                    models.CheckConstraint(condition=models.Q(("policy_version__gt", 0)), name="se_entitlement_policy_version_positive_ck"),
                    models.UniqueConstraint(condition=models.Q(("basis", "BETA_OPT_IN")), fields=("living_organism",), name="se_entitlement_beta_one_per_lo_uniq"),
                ],
            },
        ),
        migrations.CreateModel(
            name="OrganismEntitlementEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_uuid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("sequence", models.PositiveIntegerField()),
                ("event_type", models.CharField(choices=[("GRANTED", "GRANTED"), ("EXTENDED", "EXTENDED"), ("GRACE_BEGUN", "GRACE_BEGUN"), ("GRACE_ENDED", "GRACE_ENDED"), ("ENDED", "ENDED"), ("REPLACED", "REPLACED")], max_length=16)),
                ("actor_state", models.CharField(max_length=24)),
                ("actor_capacity", models.CharField(max_length=24)),
                ("actor_access_epoch", models.PositiveBigIntegerField(blank=True, null=True)),
                ("entry_mode", models.CharField(max_length=16)),
                ("composing_rule_state", models.CharField(max_length=16)),
                ("composed_from_event_uuid", models.UUIDField(blank=True, null=True)),
                ("composing_rule_reference", models.CharField(blank=True, max_length=255, null=True)),
                ("composer_state", models.CharField(max_length=16)),
                ("occurred_at", models.DateTimeField()),
                ("effective_at", models.DateTimeField()),
                ("received_at", models.DateTimeField()),
                ("authoriser_office", models.CharField(blank=True, max_length=48, null=True)),
                ("reason", models.TextField(blank=True, null=True)),
                ("recorded_at", models.DateTimeField(editable=False, null=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="core.identity")),
                ("authoriser_identity", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="core.identity")),
                ("composer_identity", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="core.identity")),
                ("deciding_identity", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="core.identity")),
                ("enterer_identity", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="+", to="core.identity")),
                ("composing_rule", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="core.governedrule")),
                ("entitlement", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="events", to="core.organismentitlement")),
                ("predecessor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="core.organismentitlementevent")),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(fields=("entitlement", "sequence"), name="se_entitlement_event_anchor_seq_uniq"),
                    models.UniqueConstraint(fields=("predecessor",), name="se_entitlement_event_predecessor_uniq"),
                    models.CheckConstraint(condition=models.Q(("sequence__gt", 0)), name="se_entitlement_event_sequence_positive_ck"),
                    models.CheckConstraint(condition=models.Q(("event_type__in", ("GRANTED", "EXTENDED", "GRACE_BEGUN", "GRACE_ENDED", "ENDED", "REPLACED"))), name="se_entitlement_event_type_vocab_ck"),
                ],
            },
        ),
        migrations.AddField(
            model_name="loapplicationsettingsversion",
            name="entitlement_choice",
            field=models.CharField(blank=True, max_length=24, null=True),
        ),
        migrations.AddField(
            model_name="loapplicationsettingsversion",
            name="entitlement_election_route",
            field=models.CharField(blank=True, max_length=24, null=True),
        ),
        migrations.AddField(
            model_name="loapplicationsettingsversion",
            name="entitlement_policy_version",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="loapplicationsettingsversion",
            name="entitlement_policy_text_sha256",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="loapplicationsettingsversion",
            name="entitlement_authority_source",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="loapplicationsettingsversion",
            name="entitlement_authority_version",
            field=models.CharField(blank=True, max_length=32, null=True),
        ),
        migrations.AddConstraint(
            model_name="loapplicationsettingsversion",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        entitlement_choice__isnull=True,
                        entitlement_election_route__isnull=True,
                        entitlement_policy_version__isnull=True,
                        entitlement_policy_text_sha256__isnull=True,
                        entitlement_authority_source__isnull=True,
                        entitlement_authority_version__isnull=True,
                    )
                    | models.Q(
                        entitlement_choice__in=("FREE", "COMPLIMENTARY_BETA"),
                        entitlement_election_route__in=("BIRTHING", "POST_BIRTHING"),
                        entitlement_policy_version__gt=0,
                        entitlement_policy_text_sha256__regex=r"^[0-9a-f]{64}$",
                        entitlement_authority_source__gt="",
                        entitlement_authority_version__gt="",
                    )
                ),
                name="se_entitlement_election_shape_ck",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="governedrule",
            name="sbg_governedrule_code_vocab_ck",
        ),
        migrations.AddConstraint(
            model_name="governedrule",
            constraint=models.CheckConstraint(condition=models.Q(("code__in", ("GOVERNANCE_CONSTITUTION", "U3_MEMBERSHIP_END_ENDS_CIRCLE_PARTICIPATION", "BETA_COMPLIMENTARY_ACCESS", "FREE_TIER"))), name="sbg_governedrule_code_vocab_ck"),
        ),
        migrations.RunPython(install, uninstall),
    ]
