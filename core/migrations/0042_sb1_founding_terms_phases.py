"""PKT-B SB-1: founding content, platform terms, and formation phases.

The previous installed bodies are pinned to the accepted base:
s3_guard_lo_actor dd3a095091919f32eaa0e0803c809ee48d0c329b7527a082085777dc3350af75
s1fu_guard_admission_route 21d310251201826c48f8f4beac0f25c3fbb2a931af5692206cddef29af08026a
s3fu_guard_gateway_effect 3a48a43c351519a8c7d9c576350b6226c4192d2608a52ef7b905735f031b70e4
"""

import hashlib

from django.db import migrations, models
from django.db.models.deletion import PROTECT
import uuid


PREVIOUS_S3_GUARD_LO_ACTOR_BODY = r"""
BEGIN
    IF NEW.actor_state <> 'LIVING_ORGANISM' THEN RETURN NEW; END IF;

    IF NEW.entry_mode = 'COMPOSED' THEN
        IF NEW.prior_state IS DISTINCT FROM 'PROPOSED' OR NEW.resulting_state IS DISTINCT FROM 'ACTIVE'
           OR NEW.actor_id IS NOT NULL OR NEW.actor_access_epoch IS NOT NULL
           OR NEW.actor_capacity <> 'STANDING' OR NEW.enterer_identity_id IS NULL
           OR NEW.composer_identity_id IS DISTINCT FROM NEW.enterer_identity_id
           OR NEW.composer_state <> 'REFERENCE' OR NEW.composing_rule_state <> 'REFERENCE'
           OR NEW.evidence_reference !~ '^application-decision:[0-9a-f-]{36}$'
           OR NOT EXISTS (
               SELECT 1 FROM public.core_applicationdecision d
               JOIN public.core_application app ON app.id = d.application_id
               JOIN public.core_organismmembership m ON m.id = NEW.membership_id
               JOIN public.core_loapplicationsettingsversion v ON v.id = app.settings_version_id
               WHERE NEW.evidence_reference = 'application-decision:' || d.decision_uuid::text
                 AND d.outcome = 'ACCEPT' AND d.route = 'LO_AUTOMATIC'
                 AND d.enterer_id = NEW.enterer_identity_id
                 AND app.living_organism_id = m.living_organism_id AND app.applicant_id = m.identity_id
                 AND v.living_organism_id = app.living_organism_id
                 AND v.applications_open AND v.automatic_acceptance
                 AND NEW.composing_rule_reference = 'application-settings-version:' || v.version_uuid::text
           ) THEN
            RAISE EXCEPTION 'S3FU composed LO admission refused: recorded automatic setting and CARE entry required'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.prior_state IS DISTINCT FROM 'PROPOSED' OR NEW.resulting_state <> 'ACTIVE'
       OR NEW.actor_id IS NOT NULL OR NEW.actor_access_epoch IS NOT NULL
       OR NEW.actor_capacity <> 'STANDING' OR NEW.entry_mode <> 'TRANSPORT'
       OR NEW.composer_state <> 'NA_TRANSPORT'
       OR NEW.evidence_reference !~ '^application-decision:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
        RAISE EXCEPTION 'S3 malformed LO admission actor' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.core_applicationdecision decision
        JOIN public.core_application application ON application.id = decision.application_id
        JOIN public.core_organismmembership member ON member.id = NEW.membership_id
        WHERE decision.decision_uuid::text = substring(NEW.evidence_reference from 22)
          AND decision.outcome = 'ACCEPT' AND decision.route <> 'LO_AUTOMATIC' AND application.living_organism_id = member.living_organism_id
          AND application.applicant_id = member.identity_id
    ) THEN
        RAISE EXCEPTION 'S3 LO actor lacks matching ACCEPT decision' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END """

NEW_S3_GUARD_LO_ACTOR_BODY = r"""
BEGIN
    IF NEW.action IN ('OPEN', 'ACTIVATE') AND NEW.reason_class = 'FOUNDING' THEN
        IF EXISTS (
            SELECT 1
              FROM public.core_organismmembership m
              JOIN public.core_livingorganism lo ON lo.id = m.living_organism_id
              JOIN public.core_livingorganismevent founding
                ON founding.living_organism_id = lo.id AND founding.sequence = 1
             WHERE m.id = NEW.membership_id
               AND m.identity_id = lo.founder_identity_id
               AND lo.founding_insert_xid = pg_catalog.pg_current_xact_id()
               AND founding.xmin = lo.founding_insert_xid::xid
               AND founding.actor_id = lo.founder_identity_id
               AND founding.actor_state = 'PARTY'
               AND founding.actor_capacity = 'STANDING'
               AND founding.enterer_identity_id = lo.founder_identity_id
               AND founding.authority_basis_id = lo.founding_authority_basis_id
               AND NEW.authority_basis_id = lo.founding_authority_basis_id
               AND NEW.actor_state = 'PARTY'
               AND NEW.actor_id = lo.founder_identity_id
               AND NEW.actor_access_epoch = founding.actor_access_epoch
               AND NEW.actor_access_epoch IS NOT NULL
               AND NEW.enterer_identity_id = lo.founder_identity_id
               AND NEW.entry_mode = 'TRANSPORT'
               AND NEW.evidence_reference = 'founding-batch:' || founding.event_uuid::text
               AND ((NEW.action = 'OPEN' AND NEW.sequence = 1
                     AND NEW.prior_state IS NULL AND NEW.resulting_state = 'PROPOSED')
                 OR (NEW.action = 'ACTIVATE' AND NEW.sequence = 2
                     AND NEW.prior_state = 'PROPOSED' AND NEW.resulting_state = 'ACTIVE'))
        ) THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'S3 founding membership attribution refused'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.actor_state <> 'LIVING_ORGANISM' THEN RETURN NEW; END IF;

    IF NEW.entry_mode = 'COMPOSED' THEN
        IF NEW.prior_state IS DISTINCT FROM 'PROPOSED' OR NEW.resulting_state IS DISTINCT FROM 'ACTIVE'
           OR NEW.actor_id IS NOT NULL OR NEW.actor_access_epoch IS NOT NULL
           OR NEW.actor_capacity <> 'STANDING' OR NEW.enterer_identity_id IS NULL
           OR NEW.composer_identity_id IS DISTINCT FROM NEW.enterer_identity_id
           OR NEW.composer_state <> 'REFERENCE' OR NEW.composing_rule_state <> 'REFERENCE'
           OR NEW.evidence_reference !~ '^application-decision:[0-9a-f-]{36}$'
           OR NOT EXISTS (
               SELECT 1 FROM public.core_applicationdecision d
               JOIN public.core_application app ON app.id = d.application_id
               JOIN public.core_organismmembership m ON m.id = NEW.membership_id
               JOIN public.core_loapplicationsettingsversion v ON v.id = app.settings_version_id
               WHERE NEW.evidence_reference = 'application-decision:' || d.decision_uuid::text
                 AND d.outcome = 'ACCEPT' AND d.route = 'LO_AUTOMATIC'
                 AND d.enterer_id = NEW.enterer_identity_id
                 AND app.living_organism_id = m.living_organism_id AND app.applicant_id = m.identity_id
                 AND v.living_organism_id = app.living_organism_id
                 AND v.applications_open AND v.automatic_acceptance
                 AND NEW.composing_rule_reference = 'application-settings-version:' || v.version_uuid::text
           ) THEN
            RAISE EXCEPTION 'S3FU composed LO admission refused: recorded automatic setting and CARE entry required'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.prior_state IS DISTINCT FROM 'PROPOSED' OR NEW.resulting_state <> 'ACTIVE'
       OR NEW.actor_id IS NOT NULL OR NEW.actor_access_epoch IS NOT NULL
       OR NEW.actor_capacity <> 'STANDING' OR NEW.entry_mode <> 'TRANSPORT'
       OR NEW.composer_state <> 'NA_TRANSPORT'
       OR NEW.evidence_reference !~ '^application-decision:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
        RAISE EXCEPTION 'S3 malformed LO admission actor' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.core_applicationdecision decision
        JOIN public.core_application application ON application.id = decision.application_id
        JOIN public.core_organismmembership member ON member.id = NEW.membership_id
        WHERE decision.decision_uuid::text = substring(NEW.evidence_reference from 22)
          AND decision.outcome = 'ACCEPT' AND decision.route <> 'LO_AUTOMATIC' AND application.living_organism_id = member.living_organism_id
          AND application.applicant_id = member.identity_id
    ) THEN
        RAISE EXCEPTION 'S3 LO actor lacks matching ACCEPT decision' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END """

PREVIOUS_S1FU_GUARD_ADMISSION_ROUTE_BODY = r"""
BEGIN
    -- Mapped PROPOSED -> ACTIVE routes: Coordinator ACCEPT (TRANSPORT) and
    -- LO_AUTOMATIC ACCEPT (COMPOSED). s3_guard_lo_actor validates both facts.
    IF (NEW.prior_state IS DISTINCT FROM 'PROPOSED' OR
        NEW.resulting_state NOT IN ('ACTIVE', 'PROBATIONARY')) AND
       (NEW.prior_state IS DISTINCT FROM 'PROBATIONARY' OR
        NEW.resulting_state IS DISTINCT FROM 'ACTIVE') THEN
        RETURN NEW;
    END IF;
    IF NEW.resulting_state = 'PROBATIONARY' OR NEW.prior_state = 'PROBATIONARY' THEN
        RAISE EXCEPTION 'S1FU admission refused: no mapped probation route'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.actor_state IS DISTINCT FROM 'LIVING_ORGANISM' THEN
        RAISE EXCEPTION 'S1FU admission refused: an LO-decided admission requires the LO as actor'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END """

NEW_S1FU_GUARD_ADMISSION_ROUTE_BODY = r"""
BEGIN
    IF NEW.action IN ('OPEN', 'ACTIVATE') AND NEW.reason_class = 'FOUNDING' THEN
        IF EXISTS (
            SELECT 1
              FROM public.core_organismmembership m
              JOIN public.core_livingorganism lo ON lo.id = m.living_organism_id
              JOIN public.core_livingorganismevent founding
                ON founding.living_organism_id = lo.id AND founding.sequence = 1
             WHERE m.id = NEW.membership_id
               AND m.identity_id = lo.founder_identity_id
               AND lo.founding_insert_xid = pg_catalog.pg_current_xact_id()
               AND founding.xmin = lo.founding_insert_xid::xid
               AND founding.actor_id = lo.founder_identity_id
               AND founding.actor_state = 'PARTY'
               AND founding.actor_capacity = 'STANDING'
               AND founding.enterer_identity_id = lo.founder_identity_id
               AND founding.authority_basis_id = lo.founding_authority_basis_id
               AND NEW.authority_basis_id = lo.founding_authority_basis_id
               AND NEW.actor_state = 'PARTY'
               AND NEW.actor_id = lo.founder_identity_id
               AND NEW.actor_access_epoch = founding.actor_access_epoch
               AND NEW.actor_access_epoch IS NOT NULL
               AND NEW.enterer_identity_id = lo.founder_identity_id
               AND NEW.entry_mode = 'TRANSPORT'
               AND NEW.evidence_reference = 'founding-batch:' || founding.event_uuid::text
               AND ((NEW.action = 'OPEN' AND NEW.sequence = 1
                     AND NEW.prior_state IS NULL AND NEW.resulting_state = 'PROPOSED')
                 OR (NEW.action = 'ACTIVATE' AND NEW.sequence = 2
                     AND NEW.prior_state = 'PROPOSED' AND NEW.resulting_state = 'ACTIVE'))
        ) THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'S1FU founding admission refused'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    -- Mapped PROPOSED -> ACTIVE routes: Coordinator ACCEPT (TRANSPORT) and
    -- LO_AUTOMATIC ACCEPT (COMPOSED). s3_guard_lo_actor validates both facts.
    IF (NEW.prior_state IS DISTINCT FROM 'PROPOSED' OR
        NEW.resulting_state NOT IN ('ACTIVE', 'PROBATIONARY')) AND
       (NEW.prior_state IS DISTINCT FROM 'PROBATIONARY' OR
        NEW.resulting_state IS DISTINCT FROM 'ACTIVE') THEN
        RETURN NEW;
    END IF;
    IF NEW.resulting_state = 'PROBATIONARY' OR NEW.prior_state = 'PROBATIONARY' THEN
        RAISE EXCEPTION 'S1FU admission refused: no mapped probation route'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.actor_state IS DISTINCT FROM 'LIVING_ORGANISM' THEN
        RAISE EXCEPTION 'S1FU admission refused: an LO-decided admission requires the LO as actor'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END """

PREVIOUS_S3FU_GUARD_GATEWAY_EFFECT_BODY = r"""
DECLARE version_row jsonb; version_id bigint; previous_id bigint; version_table text; prefix text;
        options jsonb; option_count integer;
BEGIN
    IF NEW.action NOT IN ('EFFECT_APPLICATION_SETTINGS','EFFECT_APPLICATION_QUESTION','EFFECT_GOVERNANCE_CONFIGURATION') THEN
        IF NEW.prior_state IS NOT DISTINCT FROM NEW.resulting_state THEN
            RAISE EXCEPTION 'S3FU gateway effect: same-state event without effect action' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.prior_state IS DISTINCT FROM NEW.resulting_state OR NEW.sequence = 1
       OR NEW.actor_state <> 'LIVING_ORGANISM' OR NEW.actor_id IS NOT NULL
       OR NEW.actor_access_epoch IS NOT NULL OR NEW.actor_capacity <> 'STANDING'
       OR NEW.entry_mode <> 'TRANSPORT' OR NEW.enterer_identity_id IS NULL
       OR NEW.composing_rule_state <> 'NA_TRANSPORT' OR NEW.composer_state <> 'NA_TRANSPORT' THEN
        RAISE EXCEPTION 'S3FU gateway effect: same-state LO transport required' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    version_table := CASE NEW.action
        WHEN 'EFFECT_APPLICATION_SETTINGS' THEN 'core_loapplicationsettingsversion'
        WHEN 'EFFECT_APPLICATION_QUESTION' THEN 'core_loapplicationquestionversion'
        ELSE 'core_logovernanceconfigurationversion' END;
    prefix := CASE NEW.action
        WHEN 'EFFECT_APPLICATION_SETTINGS' THEN 'application-settings-version:'
        WHEN 'EFFECT_APPLICATION_QUESTION' THEN 'application-question-version:'
        ELSE 'governance-configuration-version:' END;
    IF NEW.evidence_reference !~ ('^' || prefix || '[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$') THEN
        RAISE EXCEPTION 'S3FU gateway effect: evidence reference malformed' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    EXECUTE format('SELECT id, to_jsonb(v) FROM public.%I v WHERE version_uuid = $1::uuid', version_table)
        INTO version_id, version_row USING substring(NEW.evidence_reference from length(prefix) + 1);
    IF version_id IS NULL THEN
        RAISE EXCEPTION 'S3FU gateway effect: version not found' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (version_row->>'living_organism_id')::bigint IS DISTINCT FROM NEW.living_organism_id THEN
        RAISE EXCEPTION 'S3FU gateway effect: version of another organism' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.action = 'EFFECT_APPLICATION_SETTINGS' THEN
        IF version_row->>'applications_open' IS NULL OR version_row->>'automatic_acceptance' IS NULL
           OR version_row->>'processing_responsibility' IS NULL
           OR version_row->>'processing_responsibility' NOT IN ('LO','APPLICATION_CIRCLE') THEN
            RAISE EXCEPTION 'S3FU gateway effect: version incomplete' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSIF NEW.action = 'EFFECT_APPLICATION_QUESTION' THEN
        options := version_row->'options';
        option_count := CASE WHEN jsonb_typeof(options) = 'array' THEN jsonb_array_length(options) ELSE 0 END;
        IF (version_row->>'question_text') LIKE '%[LO name]%' THEN
            RAISE EXCEPTION 'S3FU gateway effect: unresolved LO name placeholder' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
          IF nullif(btrim(version_row->>'question_text'), '') IS NULL
              OR version_row->>'answer_format' IS NULL
           OR version_row->>'answer_format' NOT IN ('SHORT_ANSWER','LONG_ANSWER','YES_NO','MULTIPLE_CHOICE','SCALE_COMPLEX')
           OR (version_row->>'answer_format' IN ('MULTIPLE_CHOICE','SCALE_COMPLEX') AND (option_count < 2 OR option_count > 7))
           OR (version_row->>'answer_format' NOT IN ('MULTIPLE_CHOICE','SCALE_COMPLEX') AND option_count <> 0) THEN
            RAISE EXCEPTION 'S3FU gateway effect: version incomplete' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSIF version_row->>'cq_active' IS NULL OR version_row->>'inherit_portal_cq_settings' IS NULL
          OR version_row->>'require_cq_to_change_lo_settings' IS NULL
          OR version_row->>'require_cq_to_change_application_question' IS NULL THEN
        RAISE EXCEPTION 'S3FU gateway effect: version incomplete' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    EXECUTE format('SELECT 1 FROM public.core_livingorganismevent e JOIN public.%I v ON e.evidence_reference = $1 || v.version_uuid::text WHERE v.id = $2 LIMIT 1', version_table)
        INTO previous_id USING prefix, version_id;
    IF previous_id IS NOT NULL THEN
        RAISE EXCEPTION 'S3FU gateway effect: already effective' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    previous_id := NULL;
    EXECUTE format('SELECT v.id FROM public.core_livingorganismevent e JOIN public.%I v ON e.evidence_reference = $1 || v.version_uuid::text WHERE e.living_organism_id = $2 AND e.action = $3 AND e.effective_at <= $4 AND e.received_at <= $5 AND e.recorded_at <= $5 ORDER BY e.effective_at DESC, e.occurred_at DESC, e.received_at DESC, e.recorded_at DESC, e.sequence DESC, e.event_uuid DESC LIMIT 1', version_table)
        INTO previous_id USING prefix, NEW.living_organism_id, NEW.action, NEW.effective_at, NEW.recorded_at;
    IF (version_row->>'supersedes_id')::bigint IS DISTINCT FROM previous_id THEN
        RAISE EXCEPTION 'S3FU gateway effect: continuity' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END """

NEW_S3FU_GUARD_GATEWAY_EFFECT_BODY = r"""
DECLARE version_row jsonb; version_id bigint; previous_id bigint; version_table text; prefix text;
        options jsonb; option_count integer;
BEGIN
    IF NEW.action NOT IN ('EFFECT_APPLICATION_SETTINGS','EFFECT_APPLICATION_QUESTION','EFFECT_GOVERNANCE_CONFIGURATION','PHASE_ADVANCE') THEN
        IF NEW.prior_state IS NOT DISTINCT FROM NEW.resulting_state THEN
            RAISE EXCEPTION 'S3FU gateway effect: same-state event without effect action' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.action = 'PHASE_ADVANCE' THEN
        RETURN NEW;
    END IF;
    IF NEW.prior_state IS DISTINCT FROM NEW.resulting_state OR NEW.sequence = 1
       OR NEW.actor_state <> 'LIVING_ORGANISM' OR NEW.actor_id IS NOT NULL
       OR NEW.actor_access_epoch IS NOT NULL OR NEW.actor_capacity <> 'STANDING'
       OR NEW.entry_mode <> 'TRANSPORT' OR NEW.enterer_identity_id IS NULL
       OR NEW.composing_rule_state <> 'NA_TRANSPORT' OR NEW.composer_state <> 'NA_TRANSPORT' THEN
        RAISE EXCEPTION 'S3FU gateway effect: same-state LO transport required' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    version_table := CASE NEW.action
        WHEN 'EFFECT_APPLICATION_SETTINGS' THEN 'core_loapplicationsettingsversion'
        WHEN 'EFFECT_APPLICATION_QUESTION' THEN 'core_loapplicationquestionversion'
        ELSE 'core_logovernanceconfigurationversion' END;
    prefix := CASE NEW.action
        WHEN 'EFFECT_APPLICATION_SETTINGS' THEN 'application-settings-version:'
        WHEN 'EFFECT_APPLICATION_QUESTION' THEN 'application-question-version:'
        ELSE 'governance-configuration-version:' END;
    IF NEW.evidence_reference !~ ('^' || prefix || '[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$') THEN
        RAISE EXCEPTION 'S3FU gateway effect: evidence reference malformed' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    EXECUTE format('SELECT id, to_jsonb(v) FROM public.%I v WHERE version_uuid = $1::uuid', version_table)
        INTO version_id, version_row USING substring(NEW.evidence_reference from length(prefix) + 1);
    IF version_id IS NULL THEN
        RAISE EXCEPTION 'S3FU gateway effect: version not found' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (version_row->>'living_organism_id')::bigint IS DISTINCT FROM NEW.living_organism_id THEN
        RAISE EXCEPTION 'S3FU gateway effect: version of another organism' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.action = 'EFFECT_APPLICATION_SETTINGS' THEN
        IF version_row->>'applications_open' IS NULL OR version_row->>'automatic_acceptance' IS NULL
           OR version_row->>'processing_responsibility' IS NULL
           OR version_row->>'processing_responsibility' NOT IN ('LO','APPLICATION_CIRCLE') THEN
            RAISE EXCEPTION 'S3FU gateway effect: version incomplete' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSIF NEW.action = 'EFFECT_APPLICATION_QUESTION' THEN
        options := version_row->'options';
        option_count := CASE WHEN jsonb_typeof(options) = 'array' THEN jsonb_array_length(options) ELSE 0 END;
        IF (version_row->>'question_text') LIKE '%[LO name]%' THEN
            RAISE EXCEPTION 'S3FU gateway effect: unresolved LO name placeholder' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
          IF nullif(btrim(version_row->>'question_text'), '') IS NULL
              OR version_row->>'answer_format' IS NULL
           OR version_row->>'answer_format' NOT IN ('SHORT_ANSWER','LONG_ANSWER','YES_NO','MULTIPLE_CHOICE','SCALE_COMPLEX')
           OR (version_row->>'answer_format' IN ('MULTIPLE_CHOICE','SCALE_COMPLEX') AND (option_count < 2 OR option_count > 7))
           OR (version_row->>'answer_format' NOT IN ('MULTIPLE_CHOICE','SCALE_COMPLEX') AND option_count <> 0) THEN
            RAISE EXCEPTION 'S3FU gateway effect: version incomplete' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSIF version_row->>'cq_active' IS NULL OR version_row->>'inherit_portal_cq_settings' IS NULL
          OR version_row->>'require_cq_to_change_lo_settings' IS NULL
          OR version_row->>'require_cq_to_change_application_question' IS NULL THEN
        RAISE EXCEPTION 'S3FU gateway effect: version incomplete' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    EXECUTE format('SELECT 1 FROM public.core_livingorganismevent e JOIN public.%I v ON e.evidence_reference = $1 || v.version_uuid::text WHERE v.id = $2 LIMIT 1', version_table)
        INTO previous_id USING prefix, version_id;
    IF previous_id IS NOT NULL THEN
        RAISE EXCEPTION 'S3FU gateway effect: already effective' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    previous_id := NULL;
    EXECUTE format('SELECT v.id FROM public.core_livingorganismevent e JOIN public.%I v ON e.evidence_reference = $1 || v.version_uuid::text WHERE e.living_organism_id = $2 AND e.action = $3 AND e.effective_at <= $4 AND e.received_at <= $5 AND e.recorded_at <= $5 ORDER BY e.effective_at DESC, e.occurred_at DESC, e.received_at DESC, e.recorded_at DESC, e.sequence DESC, e.event_uuid DESC LIMIT 1', version_table)
        INTO previous_id USING prefix, NEW.living_organism_id, NEW.action, NEW.effective_at, NEW.recorded_at;
    IF (version_row->>'supersedes_id')::bigint IS DISTINCT FROM previous_id THEN
        RAISE EXCEPTION 'S3FU gateway effect: continuity' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END """


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
        actual = None if row is None else hashlib.sha256(row[0].encode("utf-8")).hexdigest()
        expected = hashlib.sha256(expected_body.encode("utf-8")).hexdigest()
        raise RuntimeError(
            f"SB-1 {direction} body preflight failed for {function_name}: "
            f"expected_sha256={expected} actual_sha256={actual}"
        )


def check_previous_bodies(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        for name, body in (
            ("s3_guard_lo_actor", PREVIOUS_S3_GUARD_LO_ACTOR_BODY),
            ("s1fu_guard_admission_route", PREVIOUS_S1FU_GUARD_ADMISSION_ROUTE_BODY),
            ("s3fu_guard_gateway_effect", PREVIOUS_S3FU_GUARD_GATEWAY_EFFECT_BODY),
        ):
            _assert_body(cursor, name, body, "forward")


def _create_function(cursor, name, body):
    cursor.execute(
        f"CREATE OR REPLACE FUNCTION public.{name}() "
        f"RETURNS trigger LANGUAGE plpgsql AS $${body}$$"
    )


def install_sb1(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        _create_function(cursor, "s3_guard_lo_actor", NEW_S3_GUARD_LO_ACTOR_BODY)
        _create_function(cursor, "s1fu_guard_admission_route", NEW_S1FU_GUARD_ADMISSION_ROUTE_BODY)
        _create_function(cursor, "s3fu_guard_gateway_effect", NEW_S3FU_GUARD_GATEWAY_EFFECT_BODY)
        cursor.execute(
            """
            CREATE FUNCTION public.sb_assign_founding_content_commitment()
            RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE organism_uuid uuid; preimage text;
            BEGIN
                IF NEW.content_commitment IS NOT NULL THEN
                    RAISE EXCEPTION 'SB founding content commitment is database-assigned'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                SELECT lo.organism_id INTO organism_uuid
                  FROM public.core_livingorganism lo
                 WHERE lo.id = NEW.living_organism_id;
                IF organism_uuid IS NULL THEN
                    RAISE EXCEPTION 'SB founding content organism is missing'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                preimage := '{"mission":' || COALESCE(to_json(NEW.mission)::text, 'null')
                    || ',"name":' || COALESCE(to_json(NEW.name)::text, 'null')
                    || ',"organism_uuid":' || COALESCE(to_json(organism_uuid::text)::text, 'null')
                    || ',"purpose":' || COALESCE(to_json(NEW.purpose)::text, 'null')
                    || ',"values":' || COALESCE(to_json(NEW."values")::text, 'null')
                    || ',"version":' || COALESCE(to_json(NEW.version)::text, 'null')
                    || ',"vision":' || COALESCE(to_json(NEW.vision)::text, 'null') || '}';
                NEW.content_commitment := public.s015_0022_digest('s015r1', preimage);
                RETURN NEW;
            END
            $$;

            CREATE FUNCTION public.sb_guard_terms_acceptance()
            RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE founding_event record; expected_acceptance_kind text;
            BEGIN
                IF NEW.founding_authority_basis_id IS NULL THEN RETURN NEW; END IF;
                SELECT event.event_uuid, event.effective_at, event.recorded_at
                  INTO founding_event
                  FROM public.core_livingorganismevent event
                 WHERE event.living_organism_id = NEW.id AND event.sequence = 1;
                IF founding_event.event_uuid IS NULL THEN
                    RAISE EXCEPTION 'SB platform terms acceptance requires the founding event'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                expected_acceptance_kind := CASE WHEN NEW.platform_root THEN 'GENESIS' ELSE 'FOUNDING' END;
                IF NOT EXISTS (
                    SELECT 1
                      FROM public.core_platformtermsacceptance a
                      JOIN public.core_platformtermsversion v ON v.id = a.terms_version_id
                     WHERE a.acceptance_kind = expected_acceptance_kind
                       AND a.subject_reference = 'living-organism:' || NEW.organism_id::text
                       AND a.accepted_by_identity_id = NEW.founder_identity_id
                       AND a.accepted_at = founding_event.effective_at
                       AND v.effective_from <= founding_event.effective_at
                       AND v.recorded_at <= founding_event.recorded_at
                       AND NOT EXISTS (
                            SELECT 1
                              FROM public.core_platformtermsversion newer
                             WHERE newer.supersedes_id = v.id
                               AND newer.effective_from <= founding_event.effective_at
                               AND newer.recorded_at <= founding_event.recorded_at
                       )
                ) THEN
                    RAISE EXCEPTION 'SB platform terms acceptance is missing or not in force'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                RETURN NEW;
            END
            $$;

            CREATE FUNCTION public.sb_guard_phase_event()
            RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE organism_row public.core_livingorganism%ROWTYPE;
                    current_phase text := 'CONCEIVING';
                    prior_phase text;
                    next_phase text;
                    electorate_count integer;
                    effective_governance_enabled boolean;
            BEGIN
                IF NEW.action <> 'PHASE_ADVANCE' THEN RETURN NEW; END IF;
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
                   OR NEW.evidence_reference !~ '^formation-phase:(CONCEIVING->GERMINATING|GERMINATING->BIRTHING)$' THEN
                    RAISE EXCEPTION 'SB phase guard: malformed phase event'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                IF EXISTS (
                    SELECT 1 FROM public.core_livingorganismevent birthing
                     WHERE birthing.living_organism_id = NEW.living_organism_id
                       AND birthing.action = 'BIRTHING'
                       AND birthing.effective_at <= NEW.effective_at
                       AND birthing.recorded_at <= NEW.recorded_at
                       AND birthing.received_at <= NEW.recorded_at
                ) THEN
                    RAISE EXCEPTION 'PHASE_OUT_OF_ORDER'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                SELECT split_part(e.evidence_reference, '->', 2)
                  INTO current_phase
                  FROM public.core_livingorganismevent e
                 WHERE e.living_organism_id = NEW.living_organism_id
                   AND e.action = 'PHASE_ADVANCE'
                   AND e.effective_at <= NEW.effective_at
                   AND e.recorded_at <= NEW.recorded_at
                   AND e.received_at <= NEW.recorded_at
                 ORDER BY e.effective_at DESC, e.occurred_at DESC, e.received_at DESC,
                          e.recorded_at DESC, e.sequence DESC, e.event_uuid DESC
                 LIMIT 1;
                current_phase := COALESCE(current_phase, 'CONCEIVING');
                prior_phase := split_part(split_part(NEW.evidence_reference, ':', 2), '->', 1);
                next_phase := split_part(NEW.evidence_reference, '->', 2);
                IF prior_phase IS DISTINCT FROM current_phase
                   OR NOT ((current_phase = 'CONCEIVING' AND next_phase = 'GERMINATING')
                        OR (current_phase = 'GERMINATING' AND next_phase = 'BIRTHING')) THEN
                    RAISE EXCEPTION 'PHASE_OUT_OF_ORDER'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                SELECT count(*) INTO electorate_count
                  FROM public.core_organismmembership m
                 WHERE m.living_organism_id = NEW.living_organism_id
                   AND public.s015_fold_effective_state(
                       'core_organismmembership', m.id, NEW.effective_at, NEW.recorded_at
                   ) = 'ACTIVE';
                IF electorate_count > 1 THEN
                    RAISE EXCEPTION 'INV_CQ_ROUTE_REQUIRED'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                IF next_phase = 'GERMINATING' THEN
                    IF NOT EXISTS (
                        SELECT 1 FROM public.core_lofoundingcontentversion content
                         WHERE content.living_organism_id = NEW.living_organism_id
                           AND content.version = 1
                           AND content.effective_at <= NEW.effective_at
                           AND content.recorded_at <= NEW.recorded_at
                           AND nullif(btrim(content.name), '') IS NOT NULL
                           AND nullif(btrim(content.purpose), '') IS NOT NULL
                    ) THEN
                        RAISE EXCEPTION 'PHASE_CONDITION_UNMET: founding content (name and purpose)'
                            USING ERRCODE = 'integrity_constraint_violation';
                    END IF;
                ELSE
                    SELECT v.governance_enabled
                      INTO effective_governance_enabled
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
                    IF NOT FOUND THEN
                        RAISE EXCEPTION 'PHASE_CONDITION_UNMET: an effective governance configuration'
                            USING ERRCODE = 'integrity_constraint_violation';
                    END IF;
                    IF effective_governance_enabled IS NULL THEN
                        RAISE EXCEPTION 'PHASE_CONDITION_UNMET: GOVERNANCE choice: The phase was not advanced and nothing changed. Moving to Birthing needs this LO''s GOVERNANCE choice to be confirmed in its governance configuration, and no choice is recorded yet: the offered default is not a confirmation. Confirm Yes or No by effecting the governance configuration, then advance.'
                            USING ERRCODE = 'integrity_constraint_violation';
                    ELSIF effective_governance_enabled THEN
                        RAISE EXCEPTION 'SB-IL-GOVCONST: The phase was not advanced and nothing changed: the LO remains in Germinating. This LO has GOVERNANCE enabled, so moving to Birthing needs its GOVERNANCE Circle and that Circle''s constitutional admission route, with its deciding body, to be prescribed, adopted and recorded. Those records cannot be made in this release. This is a known limit of the current build, not a refusal of the LO''s choice; it lifts when GOVERNANCE formation is delivered. The LO''s other formation work can continue.'
                            USING ERRCODE = 'integrity_constraint_violation';
                    END IF;
                END IF;
                RETURN NEW;
            END
            $$;
            """
        )
        cursor.execute(
            "CREATE TRIGGER sb_platformtermsversion_record_time BEFORE INSERT "
            "ON public.core_platformtermsversion FOR EACH ROW "
            "EXECUTE FUNCTION public.s3_assign_record_time()"
        )
        cursor.execute(
            "CREATE TRIGGER sb_platformtermsversion_immutable BEFORE UPDATE OR DELETE "
            "ON public.core_platformtermsversion FOR EACH ROW "
            "EXECUTE FUNCTION public.s015_refuse_row_mutation()"
        )
        cursor.execute(
            "CREATE TRIGGER s015_platformtermsversion_truncate_immutable BEFORE TRUNCATE "
            "ON public.core_platformtermsversion FOR EACH STATEMENT "
            "EXECUTE FUNCTION public.s015_refuse_row_mutation()"
        )
        cursor.execute(
            "CREATE TRIGGER sb_platformtermsacceptance_record_time BEFORE INSERT "
            "ON public.core_platformtermsacceptance FOR EACH ROW "
            "EXECUTE FUNCTION public.s3_assign_record_time()"
        )
        cursor.execute(
            "CREATE TRIGGER sb_platformtermsacceptance_immutable BEFORE UPDATE OR DELETE "
            "ON public.core_platformtermsacceptance FOR EACH ROW "
            "EXECUTE FUNCTION public.s015_refuse_row_mutation()"
        )
        cursor.execute(
            "CREATE TRIGGER s015_platformtermsacceptance_truncate_immutable BEFORE TRUNCATE "
            "ON public.core_platformtermsacceptance FOR EACH STATEMENT "
            "EXECUTE FUNCTION public.s015_refuse_row_mutation()"
        )
        cursor.execute(
            "CREATE TRIGGER sb_lofoundingcontentversion_record_time BEFORE INSERT "
            "ON public.core_lofoundingcontentversion FOR EACH ROW "
            "EXECUTE FUNCTION public.s3_assign_record_time()"
        )
        cursor.execute(
            "CREATE TRIGGER sb_lofoundingcontentversion_immutable BEFORE UPDATE OR DELETE "
            "ON public.core_lofoundingcontentversion FOR EACH ROW "
            "EXECUTE FUNCTION public.s015_refuse_row_mutation()"
        )
        cursor.execute(
            "CREATE TRIGGER s015_lofoundingcontentversion_truncate_immutable BEFORE TRUNCATE "
            "ON public.core_lofoundingcontentversion FOR EACH STATEMENT "
            "EXECUTE FUNCTION public.s015_refuse_row_mutation()"
        )
        cursor.execute(
            "CREATE TRIGGER sb_lofoundingcontentversion_commitment BEFORE INSERT "
            "ON public.core_lofoundingcontentversion FOR EACH ROW "
            "EXECUTE FUNCTION public.sb_assign_founding_content_commitment()"
        )
        cursor.execute(
            """
            CREATE CONSTRAINT TRIGGER sb_livingorganism_terms_acceptance
            AFTER UPDATE OF founding_authority_basis_id ON public.core_livingorganism
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION public.sb_guard_terms_acceptance()
            """
        )
        cursor.execute(
            "CREATE TRIGGER sb_livingorganismevent_phase_guard BEFORE INSERT "
            "ON public.core_livingorganismevent FOR EACH ROW "
            "EXECUTE FUNCTION public.sb_guard_phase_event()"
        )


def uninstall_sb1(apps, schema_editor):
    table_names = (
        "core_platformtermsversion",
        "core_platformtermsacceptance",
        "core_lofoundingcontentversion",
    )
    with schema_editor.connection.cursor() as cursor:
        for table_name in table_names:
            cursor.execute(f"SELECT EXISTS (SELECT 1 FROM public.{table_name} LIMIT 1)")
            if cursor.fetchone()[0]:
                raise RuntimeError(f"SB-1 reverse refused while {table_name} contains rows")
        for name, body in (
            ("s3_guard_lo_actor", NEW_S3_GUARD_LO_ACTOR_BODY),
            ("s1fu_guard_admission_route", NEW_S1FU_GUARD_ADMISSION_ROUTE_BODY),
            ("s3fu_guard_gateway_effect", NEW_S3FU_GUARD_GATEWAY_EFFECT_BODY),
        ):
            _assert_body(cursor, name, body, "reverse")
        for table_name, trigger_name in (
            ("core_platformtermsversion", "sb_platformtermsversion_record_time"),
            ("core_platformtermsversion", "sb_platformtermsversion_immutable"),
            ("core_platformtermsversion", "s015_platformtermsversion_truncate_immutable"),
            ("core_platformtermsacceptance", "sb_platformtermsacceptance_record_time"),
            ("core_platformtermsacceptance", "sb_platformtermsacceptance_immutable"),
            ("core_platformtermsacceptance", "s015_platformtermsacceptance_truncate_immutable"),
            ("core_lofoundingcontentversion", "sb_lofoundingcontentversion_record_time"),
            ("core_lofoundingcontentversion", "sb_lofoundingcontentversion_immutable"),
            ("core_lofoundingcontentversion", "s015_lofoundingcontentversion_truncate_immutable"),
            ("core_lofoundingcontentversion", "sb_lofoundingcontentversion_commitment"),
            ("core_livingorganism", "sb_livingorganism_terms_acceptance"),
            ("core_livingorganismevent", "sb_livingorganismevent_phase_guard"),
        ):
            cursor.execute(f"DROP TRIGGER {trigger_name} ON public.{table_name}")
        cursor.execute("DROP FUNCTION public.sb_assign_founding_content_commitment()")
        cursor.execute("DROP FUNCTION public.sb_guard_terms_acceptance()")
        cursor.execute("DROP FUNCTION public.sb_guard_phase_event()")
        _create_function(cursor, "s3_guard_lo_actor", PREVIOUS_S3_GUARD_LO_ACTOR_BODY)
        _create_function(cursor, "s1fu_guard_admission_route", PREVIOUS_S1FU_GUARD_ADMISSION_ROUTE_BODY)
        _create_function(cursor, "s3fu_guard_gateway_effect", PREVIOUS_S3FU_GUARD_GATEWAY_EFFECT_BODY)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0041_r4s2_assign_current_membership"),
    ]

    operations = [
        migrations.RunPython(check_previous_bodies, migrations.RunPython.noop),
        migrations.CreateModel(
            name="PlatformTermsVersion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("terms_version_uuid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("version_label", models.CharField(max_length=32)),
                ("text_commitment", models.CharField(max_length=71)),
                ("text_body", models.TextField()),
                ("cited_rulings", models.JSONField(blank=True, default=list)),
                ("published_by_authority_reference", models.CharField(max_length=255)),
                ("is_fixture", models.BooleanField(default=False)),
                ("effective_from", models.DateTimeField()),
                ("effective_until", models.DateTimeField(blank=True, null=True)),
                ("recorded_at", models.DateTimeField(editable=False, null=True)),
                ("supersedes", models.ForeignKey(blank=True, null=True, on_delete=PROTECT, related_name="+", to="core.platformtermsversion")),
            ],
            options={
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="PlatformTermsAcceptance",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("acceptance_uuid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("acceptance_kind", models.CharField(choices=[("REGISTRATION", "Registration"), ("FOUNDING", "Founding"), ("GENESIS", "Genesis")], max_length=12)),
                ("subject_reference", models.CharField(max_length=255)),
                ("accepted_by_access_epoch", models.PositiveBigIntegerField()),
                ("accepted_at", models.DateTimeField()),
                ("received_at", models.DateTimeField()),
                ("recorded_at", models.DateTimeField(editable=False, null=True)),
                ("acceptance_commitment", models.CharField(max_length=71)),
                ("accepted_by_identity", models.ForeignKey(on_delete=PROTECT, related_name="+", to="core.identity")),
                ("terms_version", models.ForeignKey(on_delete=PROTECT, related_name="acceptances", to="core.platformtermsversion")),
            ],
            options={
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="LOFoundingContentVersion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("content_uuid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("version", models.PositiveIntegerField()),
                ("name", models.CharField(max_length=90)),
                ("purpose", models.TextField()),
                ("vision", models.TextField(blank=True, null=True)),
                ("mission", models.TextField(blank=True, null=True)),
                ("values", models.TextField(blank=True, null=True)),
                ("actor_access_epoch", models.PositiveBigIntegerField()),
                ("authority_decision_reference", models.CharField(max_length=71)),
                ("effective_at", models.DateTimeField()),
                ("received_at", models.DateTimeField()),
                ("recorded_at", models.DateTimeField(editable=False, null=True)),
                ("content_commitment", models.CharField(editable=False, max_length=71)),
                ("actor_identity", models.ForeignKey(on_delete=PROTECT, related_name="+", to="core.identity")),
                ("living_organism", models.ForeignKey(on_delete=PROTECT, related_name="founding_content_versions", to="core.livingorganism")),
                ("supersedes", models.ForeignKey(blank=True, null=True, on_delete=PROTECT, related_name="+", to="core.lofoundingcontentversion")),
            ],
            options={
                "abstract": False,
            },
        ),
        migrations.AddConstraint(
            model_name="lofoundingcontentversion",
            constraint=models.UniqueConstraint(fields=("living_organism", "version"), name="sb_lofoundingcontentversion_lo_version_uniq"),
        ),
        migrations.AddField(
            model_name="logovernanceconfigurationversion",
            name="governance_enabled",
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.RunPython(install_sb1, uninstall_sb1),
    ]
