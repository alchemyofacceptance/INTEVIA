"""SB-G GOVERNANCE formation schema and database guards.

Adds governed-rule, seat-request, admission-decision, participation and
participation-ending relations, together with Circle kinds and their
constraints. On PostgreSQL, install() installs eleven new PL/pgSQL
functions, replaces two guard functions from migration 0044, creates
twenty-five triggers, widens two Circle-event actor constraints and
invokes the PLATFORM-rule seeder.

The replacement guards add GOVERNANCE readiness, the constitutional
reference in the Birthing snapshot, and support for the adoption act.

Function bodies are assembled from Python string constants. Import
performs marker and body-structure checks. render_sql(table_columns)
additionally invokes variable checks: column-collision checks for new
functions and declaration/prefix checks for variables added to the
replacement functions.

On PostgreSQL, uninstall() refuses if LO-scoped governed rules or any
seat requests, admission decisions, participations or participation
endings exist. Otherwise it removes this migration's triggers and new
functions and restores the replaced guard bodies and Circle-event
constraints.

This describes migration 0045. The definition present in a database
depends on which migrations have been applied or reversed.

Sources: SB-G build commission v1.0 and amendment 1.
"""

import importlib
import re
import uuid

from django.db import migrations
from django.db import models
import django.db.models.deletion


_PREVIOUS = importlib.import_module("core.migrations.0044_sb2_birthing_snapshot")
PREVIOUS_SB_GUARD_PHASE_EVENT_BODY = _PREVIOUS.NEW_SB_GUARD_PHASE_EVENT_BODY
PREVIOUS_S3FU_GUARD_GATEWAY_EFFECT_BODY = _PREVIOUS.NEW_S3FU_GUARD_GATEWAY_EFFECT_BODY


def _replace_once(body, marker, replacement, label):
    if body.count(marker) != 1:
        raise RuntimeError(f"0045 expected one {label} marker")
    return body.replace(marker, replacement, 1)


PHASE_ADVANCE_MARKER = (
    "ELSIF effective_governance_enabled THEN\n"
    "                        RAISE EXCEPTION 'SB-IL-GOVCONST: The phase was not advanced"
)
BIRTHING_MARKER = (
    "IF effective_governance_enabled THEN\n"
    "                        RAISE EXCEPTION 'SB-IL-GOVCONST: The LO was not born"
)
PHASE_DECLARATION_MARKER = "effective_governance_enabled boolean;"
SNAPSHOT_MARKER = (
    "OR snapshot_row.body->'governance_constitution' IS DISTINCT FROM 'null'::jsonb"
)
GATEWAY_ACTION_MARKER = (
    "IF NEW.action NOT IN "
    "('EFFECT_APPLICATION_SETTINGS','EFFECT_APPLICATION_QUESTION',"
    "'EFFECT_GOVERNANCE_CONFIGURATION','PHASE_ADVANCE') THEN"
)
GATEWAY_ADOPTION_MARKER = "version_table := CASE NEW.action"
GATEWAY_READINESS_MARKER = (
    "IF (version_row->>'supersedes_id')::bigint IS DISTINCT FROM previous_id THEN"
)
GATEWAY_DECLARATION_MARKER = "options jsonb; option_count integer;"
GATEWAY_READINESS_OLD_BLOCK = (
    GATEWAY_READINESS_MARKER
    + "\n        RAISE EXCEPTION 'S3FU gateway effect: continuity' "
    "USING ERRCODE = 'integrity_constraint_violation';\n"
    "    END IF;"
)

_STAGE0_MARKERS = (
    (
        PREVIOUS_SB_GUARD_PHASE_EVENT_BODY,
        PHASE_ADVANCE_MARKER,
        "PHASE_ADVANCE",
    ),
    (PREVIOUS_SB_GUARD_PHASE_EVENT_BODY, BIRTHING_MARKER, "BIRTHING"    ),
    (PREVIOUS_SB_GUARD_PHASE_EVENT_BODY, SNAPSHOT_MARKER, "snapshot"),
    (
        PREVIOUS_S3FU_GUARD_GATEWAY_EFFECT_BODY,
        GATEWAY_ACTION_MARKER,
        "gateway admitted-action",
    ),
    (
        PREVIOUS_S3FU_GUARD_GATEWAY_EFFECT_BODY,
        GATEWAY_ADOPTION_MARKER,
        "gateway adoption",
    ),
    (
        PREVIOUS_S3FU_GUARD_GATEWAY_EFFECT_BODY,
        GATEWAY_READINESS_MARKER,
        "gateway readiness",
    ),
)
for source_body, marker, label in _STAGE0_MARKERS:
    if source_body.count(marker) != 1:
        raise RuntimeError(f"0045 expected one Stage 0 {label} marker")

PHASE_ADVANCE_OLD_BLOCK = (
    PHASE_ADVANCE_MARKER
    + " and nothing changed: the LO remains in Germinating. This LO has GOVERNANCE enabled, so moving to Birthing "
    "needs its GOVERNANCE Circle and that Circle''s constitutional admission route, with its deciding body, to be "
    "prescribed, adopted and recorded. Those records cannot be made in this release. This is a known limit of the "
    "current build, not a refusal of the LO''s choice; it lifts when GOVERNANCE formation is delivered. The LO''s "
    "other formation work can continue.'\n"
    "                            USING ERRCODE = 'integrity_constraint_violation';\n"
    "                    END IF;"
)
BIRTHING_OLD_BLOCK = (
    BIRTHING_MARKER
    + " and nothing changed. Its GOVERNANCE choice in effect is Yes, so Birthing needs its GOVERNANCE Circle and "
    "that Circle''s constitutional admission route to be established, and those records cannot be made in this "
    "release. This is a known limit of the current build, not a refusal of the LO''s choice; it lifts when "
    "GOVERNANCE formation is delivered.'\n"
    "                            USING ERRCODE = 'integrity_constraint_violation';\n"
    "                    END IF;"
)

_READINESS_FUNCTION_BODY = r"""
DECLARE
    circle_id bigint;
    circle_state text;
    activated boolean;
    rule_row public.core_governedrule%ROWTYPE;
    seat jsonb;
    seat_id text;
    route text;
    deciding_body text;
    seat_start timestamptz;
    seat_count integer;
    distinct_seat_count integer;
    in_effect_seat_count integer := 0;
    available_seat boolean;
BEGIN
    IF lifecycle NOT IN ('PHASE_ADVANCE', 'BIRTHING', 'ENABLING') THEN
        RAISE EXCEPTION 'invalid SBG governance lifecycle'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    SELECT c.id INTO circle_id
      FROM public.core_circle c
     WHERE c.parent_organism_id = organism_id
       AND c.kind = 'GOVERNANCE'
       AND EXISTS (
            SELECT 1 FROM public.core_circlestateevent created
             WHERE created.circle_id = c.id
               AND created.action = 'CREATE_DORMANT'
               AND created.recorded_at <= at_recorded
       )
       AND public.s015_fold_effective_state(
            'core_circle', c.id, at_effective, at_recorded
       ) IS DISTINCT FROM 'CLOSED'
     ORDER BY c.id
     LIMIT 1;
    IF circle_id IS NULL THEN
        RETURN 'SBG_GOVERNANCE_CIRCLE_MISSING';
    END IF;

    SELECT r.* INTO rule_row
      FROM public.core_governedrule r
     WHERE r.scope_kind = 'LIVING_ORGANISM'
       AND r.scope_organism_id = organism_id
       AND r.code = 'GOVERNANCE_CONSTITUTION'
       AND r.effective_from <= at_effective
       AND r.recorded_at <= at_recorded
     ORDER BY r.effective_from DESC, r.recorded_at DESC, r.version DESC
     LIMIT 1;
    IF NOT FOUND THEN
        RETURN 'SBG_ARRANGEMENT_MISSING';
    END IF;

    IF jsonb_typeof(rule_row.body) IS DISTINCT FROM 'object'
       OR NOT (rule_row.body ? 'seats')
       OR NOT (rule_row.body ? 'officer_roles')
       OR NOT (rule_row.body ? 'election_schedule') THEN
        RETURN 'SBG_ARRANGEMENT_INCOMPLETE';
    END IF;
    IF rule_row.body - ARRAY['seats', 'officer_roles', 'election_schedule']::text[] <> '{}'::jsonb
       OR rule_row.body->'officer_roles' IS DISTINCT FROM '[]'::jsonb
       OR rule_row.body->'election_schedule' IS DISTINCT FROM 'null'::jsonb THEN
        RETURN 'SBG_ARRANGEMENT_FIELD_UNSUPPORTED';
    END IF;
    IF jsonb_typeof(rule_row.body->'seats') IS DISTINCT FROM 'array' THEN
        RETURN 'SBG_ARRANGEMENT_INCOMPLETE';
    END IF;
    IF jsonb_array_length(rule_row.body->'seats') = 0 THEN
        RETURN 'SBG_ARRANGEMENT_INCOMPLETE';
    END IF;

    SELECT count(*), count(DISTINCT s.value->>'seat_id')
      INTO seat_count, distinct_seat_count
      FROM jsonb_array_elements(rule_row.body->'seats') AS s(value);
    IF seat_count <> distinct_seat_count THEN
        RETURN 'SBG_ARRANGEMENT_INCOMPLETE';
    END IF;

    FOR seat IN
        SELECT value FROM jsonb_array_elements(rule_row.body->'seats')
    LOOP
        IF jsonb_typeof(seat) IS DISTINCT FROM 'object'
           OR NOT (seat ? 'seat_id')
           OR NOT (seat ? 'eligibility')
           OR NOT (seat ? 'admission_route')
           OR NOT (seat ? 'deciding_body')
           OR NOT (seat ? 'term')
           OR NOT (seat ? 'vacancy_rule')
           OR NOT (seat ? 'effective_from') THEN
            RETURN 'SBG_ARRANGEMENT_INCOMPLETE';
        END IF;
        IF seat - ARRAY[
            'seat_id', 'eligibility', 'admission_route', 'deciding_body',
            'term', 'vacancy_rule', 'effective_from'
        ]::text[] <> '{}'::jsonb
           OR seat->'term' IS DISTINCT FROM 'null'::jsonb
           OR seat->>'vacancy_rule' IS DISTINCT FROM 'BY_SEAT_ROUTE' THEN
            RETURN 'SBG_ARRANGEMENT_FIELD_UNSUPPORTED';
        END IF;
        seat_id := seat->>'seat_id';
        IF jsonb_typeof(seat->'seat_id') IS DISTINCT FROM 'string'
           OR seat_id !~ '^[a-z0-9_-]{1,64}$' THEN
            RETURN 'SBG_ARRANGEMENT_INCOMPLETE';
        END IF;
        IF jsonb_typeof(seat->'eligibility') IS DISTINCT FROM 'string'
           OR seat->>'eligibility' IS DISTINCT FROM 'LO_MEMBER' THEN
            RETURN 'SBG_ELIGIBILITY_UNSUPPORTED';
        END IF;
        route := seat->>'admission_route';
        deciding_body := seat->>'deciding_body';
        IF jsonb_typeof(seat->'admission_route') IS DISTINCT FROM 'string'
           OR jsonb_typeof(seat->'deciding_body') IS DISTINCT FROM 'string' THEN
            RETURN 'SBG_ARRANGEMENT_INCOMPLETE';
        END IF;
        IF NOT (
            (route = 'ELECTION' AND deciding_body = 'body:LO_MEMBERSHIP')
            OR (route = 'COOPTION' AND deciding_body = 'body:GOVERNANCE')
            OR (
                route = 'APPOINTMENT'
                AND (
                    deciding_body = 'office:LO_COORDINATOR'
                    OR (
                        left(deciding_body, 9) = 'external:'
                        AND length(substr(deciding_body, 10)) BETWEEN 1 AND 120
                    )
                )
            )
            OR (
                route = 'EX_OFFICIO'
                AND left(deciding_body, 7) = 'office:'
                AND substring(deciding_body FROM 8) ~*
                    '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
            )
        ) THEN
            RETURN 'SBG_ARRANGEMENT_INCOMPLETE';
        END IF;
        IF jsonb_typeof(seat->'effective_from') IS DISTINCT FROM 'string'
           OR seat->>'effective_from' !~
            '^[0-9]{4}-[0-9]{2}-[0-9]{2}T.*(Z|[+-][0-9]{2}:[0-9]{2})$' THEN
            RETURN 'SBG_ARRANGEMENT_INCOMPLETE';
        END IF;
        BEGIN
            seat_start := (seat->>'effective_from')::timestamptz;
        EXCEPTION
            WHEN invalid_datetime_format OR datetime_field_overflow THEN
                RETURN 'SBG_ARRANGEMENT_INCOMPLETE';
        END;
        IF seat_start <= at_effective THEN
            in_effect_seat_count := in_effect_seat_count + 1;
        END IF;
    END LOOP;

    IF in_effect_seat_count = 0 THEN
        RETURN 'SBG_ARRANGEMENT_INCOMPLETE';
    END IF;

    IF lifecycle = 'PHASE_ADVANCE' THEN
        RETURN NULL;
    END IF;

    SELECT public.s015_fold_effective_state(
               'core_circle', circle_id, at_effective, at_recorded
           ),
           EXISTS (
               SELECT 1 FROM public.core_circlestateevent eligible
                WHERE eligible.circle_id = circle_id
                  AND eligible.resulting_state = 'ELIGIBLE'
                  AND eligible.effective_at <= at_effective
                  AND eligible.recorded_at <= at_recorded
           )
      INTO circle_state, activated;
    IF lifecycle = 'ENABLING' AND activated THEN
        IF circle_state = 'DORMANT' THEN
            RETURN 'SBG_IL_GRESD';
        END IF;
        IF circle_state = 'ELIGIBLE' THEN
            RETURN NULL;
        END IF;
    END IF;

    IF EXISTS (
        SELECT 1
          FROM public.core_circleparticipation p
          JOIN public.core_organismmembership m ON m.id = p.membership_id
         WHERE p.circle_id = circle_id
           AND p.effective_at <= at_effective
           AND p.recorded_at <= at_recorded
           AND public.s015_fold_effective_state(
                'core_organismmembership', m.id, at_effective, at_recorded
           ) = 'ACTIVE'
           AND NOT EXISTS (
                SELECT 1 FROM public.core_circleparticipationend ending
                 WHERE ending.participation_id = p.id
                   AND ending.effective_at <= at_effective
                   AND ending.recorded_at <= at_recorded
           )
    ) THEN
        RETURN NULL;
    END IF;

    SELECT EXISTS (
        SELECT 1
          FROM jsonb_array_elements(rule_row.body->'seats') AS s(value)
          JOIN public.core_organismmembership m
            ON m.living_organism_id = organism_id
          JOIN public.core_contextualroleassignment office
            ON office.membership_id = m.id
           AND office.circle_id IS NULL
          JOIN public.core_organismroledefinition definition
            ON definition.id = office.role_definition_id
           AND definition.living_organism_id = organism_id
           AND definition.code = 'COORDINATOR'
           AND definition.scope = 'LIVING_ORGANISM'
           AND definition.active
         WHERE s.value->>'admission_route' = 'APPOINTMENT'
           AND s.value->>'deciding_body' = 'office:LO_COORDINATOR'
           AND (s.value->>'effective_from')::timestamptz <= at_effective
           AND public.s015_fold_effective_state(
                'core_organismmembership', m.id, at_effective, at_recorded
           ) = 'ACTIVE'
           AND public.s015_fold_effective_state(
                'core_contextualroleassignment', office.id, at_effective, at_recorded
           ) = 'ACTIVE'
           AND NOT EXISTS (
                SELECT 1
                  FROM public.core_circleparticipation p
                 WHERE p.seat_rule_uuid = rule_row.rule_uuid
                    AND p.circle_id = circle_id
                   AND p.seat_id = s.value->>'seat_id'
                   AND p.effective_at <= at_effective
                   AND p.recorded_at <= at_recorded
                   AND NOT EXISTS (
                        SELECT 1 FROM public.core_circleparticipationend ending
                         WHERE ending.participation_id = p.id
                           AND ending.effective_at <= at_effective
                           AND ending.recorded_at <= at_recorded
                   )
           )
    ) INTO available_seat;
    IF available_seat THEN
        RETURN NULL;
    END IF;
    RETURN 'SBG_NO_INITIAL_PARTICIPATION_ROUTE';
END
"""

# sbg_assign_governedrule_commitment: BEFORE INSERT on core_governedrule
# through sbg_governedrule_commitment. Rejects a non-null supplied
# text_commitment. Resolves the scope UUID from the referenced LO or Circle;
# a non-PLATFORM scope with no resolved UUID is refused. PLATFORM uses null
# as the scope_uuid value.
#
# Builds a text preimage with outer keys in this order: body, code,
# effective_from, rule_uuid, scope_kind, scope_uuid, version. The body
# value is to_json(NEW.body)::text; this function does not separately
# canonicalise or recursively sort it. Assigns text_commitment using
# s015_0022_digest('s015r1', preimage).
#
# Governing: SB-G build commission v1.0, section 5.2 (text_commitment).
_GOVERNEDRULE_COMMITMENT_BODY = r"""
DECLARE
    v_scope_uuid uuid;
    v_preimage text;
BEGIN
    IF NEW.text_commitment IS NOT NULL THEN
        RAISE EXCEPTION 'SBG governed-rule commitment is database-assigned'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.scope_kind = 'LIVING_ORGANISM' THEN
        SELECT lo.organism_id INTO v_scope_uuid
          FROM public.core_livingorganism lo
         WHERE lo.id = NEW.scope_organism_id;
    ELSIF NEW.scope_kind = 'CIRCLE' THEN
        SELECT c.circle_uuid INTO v_scope_uuid
          FROM public.core_circle c
         WHERE c.id = NEW.scope_circle_id;
    END IF;
    IF NEW.scope_kind <> 'PLATFORM' AND v_scope_uuid IS NULL THEN
        RAISE EXCEPTION 'SBG governed-rule scope does not exist'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    v_preimage := '{"body":' || COALESCE(to_json(NEW.body)::text, 'null')
        || ',"code":' || COALESCE(to_json(NEW.code)::text, 'null')
        || ',"effective_from":' || COALESCE(to_json(NEW.effective_from)::text, 'null')
        || ',"rule_uuid":' || COALESCE(to_json(NEW.rule_uuid::text)::text, 'null')
        || ',"scope_kind":' || COALESCE(to_json(NEW.scope_kind)::text, 'null')
        || ',"scope_uuid":' || COALESCE(to_json(v_scope_uuid::text)::text, 'null')
        || ',"version":' || COALESCE(to_json(NEW.version)::text, 'null')
        || '}';
    NEW.text_commitment := public.s015_0022_digest('s015r1', v_preimage);
    RETURN NEW;
END
"""

# sbg_guard_governedrule: BEFORE INSERT on core_governedrule through
# sbg_governedrule_validate.
#
# First checks version, rule_uuid, text_commitment and
# decided_at <= effective_from, raising SBG_GOVERNEDRULE_INVALID on the
# stated invalid conditions. PLATFORM rows must have no scope target or
# enterer and must name the U-3 rule with source PLATFORM_RULING.
# LIVING_ORGANISM rows must target an LO, have no Circle target, have an
# enterer, name GOVERNANCE_CONSTITUTION, use a founding-body or LO source,
# and match the lo-event: reference pattern checked here. Other scopes
# are refused with SBG_GOVERNEDRULE_SCOPE_INVALID.
#
# Checks that version is one greater than the visible maximum for
# rule_uuid and that existing versions have the same scope targets and
# code (SBG_GOVERNEDRULE_VERSION_INVALID). An existing constitution with
# another rule_uuid for the same LO produces SBG_GOVERNEDRULE_ALREADY_EXISTS.
# The U-3 platform rule must match its fixed UUID, version, authority
# reference, body and dates (SBG_GOVERNEDRULE_PLATFORM_INVALID).
#
# For a constitution, a non-object body, missing or extra top-level keys,
# officer_roles other than [], or election_schedule other than JSON null
# produces SBG_ARRANGEMENT_FIELD_UNSUPPORTED. seats must be a nonempty
# array whose extracted seat_id values are distinct and non-null
# (SBG_ARRANGEMENT_INCOMPLETE).
#
# Each seat must be an object with exactly seat_id, eligibility,
# admission_route, deciding_body, term, vacancy_rule and effective_from;
# missing or extra seat keys produce SBG_ARRANGEMENT_INCOMPLETE.
# A term other than JSON null or vacancy_rule other than BY_SEAT_ROUTE
# produces SBG_ARRANGEMENT_FIELD_UNSUPPORTED.
#
# Requires a string seat_id matching the stated identifier pattern,
# eligibility LO_MEMBER (SBG_ELIGIBILITY_UNSUPPORTED otherwise), and
# string route/body values matching ELECTION/body:LO_MEMBERSHIP,
# COOPTION/body:GOVERNANCE, APPOINTMENT/office:LO_COORDINATOR or
# external:<1-120 characters>, or EX_OFFICIO/office:<hyphenated UUID>.
# The EX_OFFICIO UUID pattern accepts either letter case.
#
# Requires a string effective_from matching the timestamp pattern with
# Z or a signed HH:MM offset, then casts it to timestamptz. Invalid
# identifiers, route/body combinations, timestamp shapes, and the
# caught timestamp-format or overflow errors produce
# SBG_ARRANGEMENT_INCOMPLETE.
#
# Governing: commission v1.0 sections 5.2 and 5.2.1, amendment 1 row 4,
# and amendment 2. This describes the implemented refusal codes;
# the extra-seat-key discrepancy is recorded separately.
_GOVERNEDRULE_GUARD_BODY = r"""
DECLARE
    v_max_version integer;
    v_seat_count integer;
    v_distinct_seat_count integer;
    v_seat jsonb;
    v_seat_id text;
    v_route text;
    v_deciding_body text;
    v_seat_start timestamptz;
BEGIN
    IF NEW.version IS NULL OR NEW.version <= 0
       OR NEW.rule_uuid IS NULL
       OR NEW.text_commitment IS NULL
       OR NEW.decided_at > NEW.effective_from THEN
        RAISE EXCEPTION 'SBG_GOVERNEDRULE_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.scope_kind = 'PLATFORM' THEN
        IF NEW.scope_organism_id IS NOT NULL OR NEW.scope_circle_id IS NOT NULL
           OR NEW.enterer_identity_id IS NOT NULL
           OR NEW.code <> 'U3_MEMBERSHIP_END_ENDS_CIRCLE_PARTICIPATION'
           OR NEW.source <> 'PLATFORM_RULING' THEN
            RAISE EXCEPTION 'SBG_GOVERNEDRULE_SCOPE_INVALID'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSIF NEW.scope_kind = 'LIVING_ORGANISM' THEN
        IF NEW.scope_organism_id IS NULL OR NEW.scope_circle_id IS NOT NULL
           OR NEW.enterer_identity_id IS NULL
           OR NEW.code <> 'GOVERNANCE_CONSTITUTION'
           OR NEW.source NOT IN ('FOUNDING_BODY_PRESCRIPTION', 'LO_PRESCRIPTION')
           OR NEW.authority_reference !~ '^lo-event:[0-9a-fA-F-]{36}$' THEN
            RAISE EXCEPTION 'SBG_GOVERNEDRULE_SCOPE_INVALID'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSE
        RAISE EXCEPTION 'SBG_GOVERNEDRULE_SCOPE_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    SELECT COALESCE(MAX(r.version), 0)
      INTO v_max_version
      FROM public.core_governedrule r
     WHERE r.rule_uuid = NEW.rule_uuid;
    IF NEW.version <> v_max_version + 1 OR EXISTS (
        SELECT 1
          FROM public.core_governedrule r
         WHERE r.rule_uuid = NEW.rule_uuid
           AND (
                r.scope_kind IS DISTINCT FROM NEW.scope_kind
                OR r.scope_organism_id IS DISTINCT FROM NEW.scope_organism_id
                OR r.scope_circle_id IS DISTINCT FROM NEW.scope_circle_id
                OR r.code IS DISTINCT FROM NEW.code
           )
    ) THEN
        RAISE EXCEPTION 'SBG_GOVERNEDRULE_VERSION_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.scope_kind = 'LIVING_ORGANISM'
       AND EXISTS (
            SELECT 1
              FROM public.core_governedrule r
             WHERE r.scope_kind = 'LIVING_ORGANISM'
               AND r.scope_organism_id = NEW.scope_organism_id
               AND r.code = 'GOVERNANCE_CONSTITUTION'
               AND r.rule_uuid <> NEW.rule_uuid
       ) THEN
        RAISE EXCEPTION 'SBG_GOVERNEDRULE_ALREADY_EXISTS'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    IF NEW.code = 'U3_MEMBERSHIP_END_ENDS_CIRCLE_PARTICIPATION' THEN
        IF NEW.scope_kind <> 'PLATFORM'
           OR NEW.rule_uuid <> '52345b7a-2fab-45ce-954e-e3ff922a8a37'::uuid
           OR NEW.version <> 1
           OR NEW.authority_reference <> 'ruling:U-3@2026-09-21#departure'
           OR NEW.body IS DISTINCT FROM
                '{"composes":"core_circleparticipationend","condition":"The Living Organism membership referenced by the participation folds ENDED at effective_at.","on":"core_organismmembershiptransition.resulting_state=ENDED"}'::jsonb
           OR NEW.effective_from <> '0001-01-01 00:00:00+00'::timestamptz
           OR NEW.decided_at <> '0001-01-01 00:00:00+00'::timestamptz THEN
            RAISE EXCEPTION 'SBG_GOVERNEDRULE_PLATFORM_INVALID'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;

    IF jsonb_typeof(NEW.body) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'SBG_ARRANGEMENT_FIELD_UNSUPPORTED'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.body - ARRAY['seats', 'officer_roles', 'election_schedule']::text[] <> '{}'::jsonb
       OR NOT (NEW.body ? 'seats')
       OR NOT (NEW.body ? 'officer_roles')
       OR NOT (NEW.body ? 'election_schedule') THEN
        RAISE EXCEPTION 'SBG_ARRANGEMENT_FIELD_UNSUPPORTED'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.body->'officer_roles' IS DISTINCT FROM '[]'::jsonb
       OR NEW.body->'election_schedule' IS DISTINCT FROM 'null'::jsonb THEN
        RAISE EXCEPTION 'SBG_ARRANGEMENT_FIELD_UNSUPPORTED'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF jsonb_typeof(NEW.body->'seats') IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'SBG_ARRANGEMENT_INCOMPLETE'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF jsonb_array_length(NEW.body->'seats') = 0 THEN
        RAISE EXCEPTION 'SBG_ARRANGEMENT_INCOMPLETE'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT count(*), count(DISTINCT s.value->>'seat_id')
      INTO v_seat_count, v_distinct_seat_count
      FROM jsonb_array_elements(NEW.body->'seats') AS s(value);
    IF v_seat_count <> v_distinct_seat_count THEN
        RAISE EXCEPTION 'SBG_ARRANGEMENT_INCOMPLETE'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    FOR v_seat IN
        SELECT value FROM jsonb_array_elements(NEW.body->'seats')
    LOOP
        IF jsonb_typeof(v_seat) IS DISTINCT FROM 'object' THEN
            RAISE EXCEPTION 'SBG_ARRANGEMENT_INCOMPLETE'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF v_seat - ARRAY[
                'seat_id', 'eligibility', 'admission_route', 'deciding_body',
                'term', 'vacancy_rule', 'effective_from'
           ]::text[] <> '{}'::jsonb
           OR NOT (v_seat ? 'seat_id')
           OR NOT (v_seat ? 'eligibility')
           OR NOT (v_seat ? 'admission_route')
           OR NOT (v_seat ? 'deciding_body')
           OR NOT (v_seat ? 'term')
           OR NOT (v_seat ? 'vacancy_rule')
           OR NOT (v_seat ? 'effective_from') THEN
            RAISE EXCEPTION 'SBG_ARRANGEMENT_INCOMPLETE'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF v_seat->'term' IS DISTINCT FROM 'null'::jsonb
           OR v_seat->>'vacancy_rule' IS DISTINCT FROM 'BY_SEAT_ROUTE' THEN
            RAISE EXCEPTION 'SBG_ARRANGEMENT_FIELD_UNSUPPORTED'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        v_seat_id := v_seat->>'seat_id';
        IF jsonb_typeof(v_seat->'seat_id') IS DISTINCT FROM 'string'
           OR v_seat_id !~ '^[a-z0-9_-]{1,64}$' THEN
            RAISE EXCEPTION 'SBG_ARRANGEMENT_INCOMPLETE'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF v_seat->>'eligibility' IS DISTINCT FROM 'LO_MEMBER' THEN
            RAISE EXCEPTION 'SBG_ELIGIBILITY_UNSUPPORTED'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        v_route := v_seat->>'admission_route';
        v_deciding_body := v_seat->>'deciding_body';
        IF jsonb_typeof(v_seat->'admission_route') IS DISTINCT FROM 'string'
           OR jsonb_typeof(v_seat->'deciding_body') IS DISTINCT FROM 'string'
           OR NOT (
                (v_route = 'ELECTION' AND v_deciding_body = 'body:LO_MEMBERSHIP')
                OR (v_route = 'COOPTION' AND v_deciding_body = 'body:GOVERNANCE')
                OR (v_route = 'APPOINTMENT' AND (
                    v_deciding_body = 'office:LO_COORDINATOR'
                    OR (left(v_deciding_body, 9) = 'external:'
                        AND length(substr(v_deciding_body, 10)) BETWEEN 1 AND 120)
                ))
                OR (v_route = 'EX_OFFICIO'
                    AND left(v_deciding_body, 7) = 'office:'
                    AND substring(v_deciding_body FROM 8) ~*
                        '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
           ) THEN
            RAISE EXCEPTION 'SBG_ARRANGEMENT_INCOMPLETE'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF jsonb_typeof(v_seat->'effective_from') IS DISTINCT FROM 'string'
           OR v_seat->>'effective_from' !~
                '^[0-9]{4}-[0-9]{2}-[0-9]{2}T.*(Z|[+-][0-9]{2}:[0-9]{2})$' THEN
            RAISE EXCEPTION 'SBG_ARRANGEMENT_INCOMPLETE'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        BEGIN
            v_seat_start := (v_seat->>'effective_from')::timestamptz;
        EXCEPTION
            WHEN invalid_datetime_format OR datetime_field_overflow THEN
                RAISE EXCEPTION 'SBG_ARRANGEMENT_INCOMPLETE'
                    USING ERRCODE = 'integrity_constraint_violation';
        END;
    END LOOP;
    RETURN NEW;
END
"""

# sbg_guard_governedrule_adopting_event: AFTER INSERT FOR EACH ROW on
# core_governedrule through the DEFERRABLE INITIALLY DEFERRED constraint
# trigger sbg_governedrule_adopting_event. Checking can be forced earlier
# with SET CONSTRAINTS. PLATFORM rows return without these event checks.
#
# Requires authority_reference to match lo-event:<hyphenated UUID> and
# resolve to an ADOPT_GOVERNING_INSTRUMENT event of the same LO.
# The rule's effective_from must not precede the event's effective_at;
# decided_at must equal its occurred_at; and the event's evidence_reference
# must equal rule:<rule_uuid>@<version>.
#
# Tests for a BIRTHING event of the same LO with effective_at no later
# than the adopting event's effective_at and recorded_at no later than
# its recorded_at. Requires LO_PRESCRIPTION when such an event exists,
# otherwise FOUNDING_BODY_PRESCRIPTION. This query has no received_at
# condition. Failed checks raise SBG_GOVERNEDRULE_EVENT_INVALID.
#
# Governing: commission v1.0 section 5.2 (authority_reference and
# effective_from), amendment 1 row 7, amendment 2 (decided_at).
_GOVERNEDRULE_ADOPTING_EVENT_BODY = r"""
DECLARE
    v_event_uuid uuid;
    v_event public.core_livingorganismevent%ROWTYPE;
    v_birthed boolean;
BEGIN
    IF NEW.scope_kind = 'PLATFORM' THEN
        RETURN NEW;
    END IF;
    IF NEW.authority_reference !~ '^lo-event:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' THEN
        RAISE EXCEPTION 'SBG_GOVERNEDRULE_EVENT_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    v_event_uuid := substring(NEW.authority_reference FROM 10)::uuid;
    SELECT e.* INTO v_event
      FROM public.core_livingorganismevent e
     WHERE e.event_uuid = v_event_uuid;
    IF NOT FOUND
       OR v_event.living_organism_id IS DISTINCT FROM NEW.scope_organism_id
       OR v_event.action IS DISTINCT FROM 'ADOPT_GOVERNING_INSTRUMENT'
       OR NEW.effective_from < v_event.effective_at
       OR NEW.decided_at IS DISTINCT FROM v_event.occurred_at
       OR v_event.evidence_reference IS DISTINCT FROM
            'rule:' || NEW.rule_uuid::text || '@' || NEW.version::text THEN
        RAISE EXCEPTION 'SBG_GOVERNEDRULE_EVENT_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT EXISTS (
        SELECT 1
          FROM public.core_livingorganismevent birth
         WHERE birth.living_organism_id = NEW.scope_organism_id
           AND birth.action = 'BIRTHING'
           AND birth.effective_at <= v_event.effective_at
           AND birth.recorded_at <= v_event.recorded_at
    ) INTO v_birthed;
    IF (v_birthed AND NEW.source <> 'LO_PRESCRIPTION')
       OR (NOT v_birthed AND NEW.source <> 'FOUNDING_BODY_PRESCRIPTION') THEN
        RAISE EXCEPTION 'SBG_GOVERNEDRULE_EVENT_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
"""

_SEED_PLATFORM_RULES_BODY = r"""
DECLARE
    v_existing public.core_governedrule%ROWTYPE;
BEGIN
    SELECT r.* INTO v_existing
      FROM public.core_governedrule r
     WHERE r.rule_uuid = '52345b7a-2fab-45ce-954e-e3ff922a8a37'::uuid;
    IF FOUND THEN
        IF v_existing.version <> 1
           OR v_existing.scope_kind <> 'PLATFORM'
           OR v_existing.scope_organism_id IS NOT NULL
           OR v_existing.scope_circle_id IS NOT NULL
           OR v_existing.code <> 'U3_MEMBERSHIP_END_ENDS_CIRCLE_PARTICIPATION'
           OR v_existing.body IS DISTINCT FROM
                '{"composes":"core_circleparticipationend","condition":"The Living Organism membership referenced by the participation folds ENDED at effective_at.","on":"core_organismmembershiptransition.resulting_state=ENDED"}'::jsonb
           OR v_existing.authority_reference <> 'ruling:U-3@2026-09-21#departure'
           OR v_existing.source <> 'PLATFORM_RULING'
           OR v_existing.effective_from <> '0001-01-01 00:00:00+00'::timestamptz
           OR v_existing.decided_at <> '0001-01-01 00:00:00+00'::timestamptz
           OR v_existing.enterer_identity_id IS NOT NULL
           OR v_existing.observations IS NOT NULL
           OR v_existing.text_commitment IS NULL
           OR EXISTS (
                SELECT 1
                  FROM public.core_governedrule r
                 WHERE r.rule_uuid = v_existing.rule_uuid
                   AND r.version <> 1
           ) THEN
            RAISE EXCEPTION 'SBG platform seed conflicts with existing governed rule'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN;
    END IF;
    INSERT INTO public.core_governedrule (
        rule_uuid, version, scope_kind, scope_organism_id, scope_circle_id,
        code, body, text_commitment, authority_reference, source,
        effective_from, decided_at, observations, enterer_identity_id, recorded_at
    ) VALUES (
        '52345b7a-2fab-45ce-954e-e3ff922a8a37'::uuid, 1, 'PLATFORM', NULL, NULL,
        'U3_MEMBERSHIP_END_ENDS_CIRCLE_PARTICIPATION',
        '{"composes":"core_circleparticipationend","condition":"The Living Organism membership referenced by the participation folds ENDED at effective_at.","on":"core_organismmembershiptransition.resulting_state=ENDED"}'::jsonb,
        NULL, 'ruling:U-3@2026-09-21#departure', 'PLATFORM_RULING',
        '0001-01-01 00:00:00+00'::timestamptz,
        '0001-01-01 00:00:00+00'::timestamptz, NULL, NULL, NULL
    );
    RETURN;
END
"""

# sbg_guard_circlejoinrequest: BEFORE INSERT on core_circlejoinrequest through
# sbg_circlejoinrequest_validate. In order:
# - SBG_CIRCLEJOINREQUEST_INVALID unless initiated_by is APPLICANT and
#   consent_reference is non-NULL with btrim(consent_reference) nonempty;
# - SBG_CIRCLEJOINREQUEST_CONSENT_INVALID unless the membership exists, has an
#   LO, and its identity matches enterer_identity_id;
# - SBG_CIRCLEJOINREQUEST_CIRCLE_INVALID unless the Circle belongs to that LO,
#   is of kind GOVERNANCE, and folds DORMANT or ELIGIBLE at the request's
#   effective_at and recorded_at;
# - SBG_MEMBER_NOT_ELIGIBLE unless the membership folds ACTIVE at those
#   coordinates;
# - SBG_SEAT_ABSENT unless the LO's GOVERNANCE_CONSTITUTION version for
#   seat_rule_uuid, latest by effective_from, recorded_at and version among
#   those effective and recorded by the request's coordinates, lists seat_id
#   with a start no later than the request's effective_at.
# Governing: commission v1.0 sections 5.3 and 9; amendment 1.
_CIRCLEJOINREQUEST_GUARD_BODY = r"""
DECLARE
    v_identity_id bigint;
    v_organism_id bigint;
    v_circle_kind text;
    v_rule public.core_governedrule%ROWTYPE;
BEGIN
    IF NEW.initiated_by IS DISTINCT FROM 'APPLICANT'
       OR NEW.consent_reference IS NULL
       OR btrim(NEW.consent_reference) = '' THEN
        RAISE EXCEPTION 'SBG_CIRCLEJOINREQUEST_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT m.identity_id, m.living_organism_id
      INTO v_identity_id, v_organism_id
      FROM public.core_organismmembership m
     WHERE m.id = NEW.membership_id;
    IF NOT FOUND OR v_organism_id IS NULL
       OR NEW.enterer_identity_id IS DISTINCT FROM v_identity_id THEN
        RAISE EXCEPTION 'SBG_CIRCLEJOINREQUEST_CONSENT_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT c.kind
      INTO v_circle_kind
      FROM public.core_circle c
     WHERE c.id = NEW.circle_id
       AND c.parent_organism_id = v_organism_id;
    IF NOT FOUND OR v_circle_kind IS DISTINCT FROM 'GOVERNANCE'
       OR public.s015_fold_effective_state(
            'core_circle', NEW.circle_id, NEW.effective_at, NEW.recorded_at
       ) IS DISTINCT FROM 'DORMANT'
          AND public.s015_fold_effective_state(
            'core_circle', NEW.circle_id, NEW.effective_at, NEW.recorded_at
          ) IS DISTINCT FROM 'ELIGIBLE' THEN
        RAISE EXCEPTION 'SBG_CIRCLEJOINREQUEST_CIRCLE_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF public.s015_fold_effective_state(
            'core_organismmembership', NEW.membership_id,
            NEW.effective_at, NEW.recorded_at
       ) IS DISTINCT FROM 'ACTIVE' THEN
        RAISE EXCEPTION 'SBG_MEMBER_NOT_ELIGIBLE'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT r.* INTO v_rule
      FROM public.core_governedrule r
     WHERE r.scope_kind = 'LIVING_ORGANISM'
       AND r.scope_organism_id = v_organism_id
       AND r.code = 'GOVERNANCE_CONSTITUTION'
       AND r.rule_uuid = NEW.seat_rule_uuid
       AND r.effective_from <= NEW.effective_at
       AND r.recorded_at <= NEW.recorded_at
     ORDER BY r.effective_from DESC, r.recorded_at DESC, r.version DESC
     LIMIT 1;
    IF NOT FOUND OR NOT EXISTS (
        SELECT 1
          FROM jsonb_array_elements(v_rule.body->'seats') AS s(value)
         WHERE s.value->>'seat_id' = NEW.seat_id
           AND (s.value->>'effective_from')::timestamptz <= NEW.effective_at
    ) THEN
        RAISE EXCEPTION 'SBG_SEAT_ABSENT'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
"""

# sbg_guard_circleadmissiondecision: BEFORE INSERT on core_circleadmissiondecision
# through sbg_circleadmissiondecision_validate. In order:
# - SBG_CIRCLEDECISION_REQUEST_INVALID unless the request exists and the
#   decision's seat_rule_uuid and seat_id match the request's;
# - SBG_CIRCLEDECISION_INVALID for an invalid route combination, outcome or
#   authority-reference format: CIRCLE_FOUNDING requires NOT_APPLICABLE and
#   FOUNDING_BODY; GOVERNANCE_CONSTITUTIONAL requires APPOINTMENT and
#   office:LO_COORDINATOR; outcome is ACCEPT or DECLINE; the reference matches
#   ^s015d1:[0-9a-f]{64}$. This branch relies on column constraints for NULLs;
# - SBG_CIRCLEDECISION_IDENTITY_INVALID if the membership lookup fails, the
#   request's Circle and membership have different LO identifiers, or
#   self_decision differs from (deciding_identity_id = membership.identity_id);
# - SBG_SEAT_ABSENT unless the matching LO GOVERNANCE_CONSTITUTION version for
#   seat_rule_uuid, latest by effective_from, recorded_at and version among
#   those effective and recorded by the decision's coordinates, lists seat_id
#   with a start no later than the decision's effective_at;
# - SBG_CIRCLEDECISION_SETTINGS_BASIS_INVALID unless settings_basis is exactly
#   rule:<rule_uuid>@<version>#<seat_id> for that selected version and seat;
# - SBG_CIRCLEDECISION_TRANSPORT_INVALID unless actor_state is LIVING_ORGANISM,
#   actor_id and actor_access_epoch are NULL, actor_capacity is STANDING,
#   entry_mode is TRANSPORT, enterer_identity_id equals deciding_identity_id,
#   composing_rule_state and composer_state are NA_TRANSPORT, and
#   composing_rule_reference and composer_identity_id are NULL.
# This function checks authority-reference syntax, not the decider's authority
# or the referenced decision. It does not fold applicant membership state or
# check Birthing. The separate deferred participation occupancy guard checks
# ACTIVE membership when a participation is inserted. One decision per request
# is enforced by the request column's uniqueness, not by this function.
# Governing: commission v1.0 sections 5.4 and 9; transport shape referenced
# there to section 5.6; amendment 1.
_CIRCLEADMISSIONDECISION_GUARD_BODY = r"""
DECLARE
    v_request public.core_circlejoinrequest%ROWTYPE;
    v_circle public.core_circle%ROWTYPE;
    v_membership public.core_organismmembership%ROWTYPE;
    v_rule public.core_governedrule%ROWTYPE;
    v_expected_settings_basis text;
BEGIN
    SELECT q.* INTO v_request
      FROM public.core_circlejoinrequest q
     WHERE q.id = NEW.request_id;
    IF NOT FOUND
       OR NEW.seat_rule_uuid IS DISTINCT FROM v_request.seat_rule_uuid
       OR NEW.seat_id IS DISTINCT FROM v_request.seat_id THEN
        RAISE EXCEPTION 'SBG_CIRCLEDECISION_REQUEST_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.route NOT IN ('CIRCLE_FOUNDING', 'GOVERNANCE_CONSTITUTIONAL')
       OR (NEW.route = 'CIRCLE_FOUNDING'
           AND (NEW.constitutional_route <> 'NOT_APPLICABLE'
                OR NEW.deciding_party <> 'FOUNDING_BODY'))
       OR (NEW.route = 'GOVERNANCE_CONSTITUTIONAL'
           AND (NEW.constitutional_route <> 'APPOINTMENT'
                OR NEW.deciding_party <> 'office:LO_COORDINATOR'))
       OR NEW.outcome NOT IN ('ACCEPT', 'DECLINE')
       OR NEW.authority_decision_reference !~ '^s015d1:[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'SBG_CIRCLEDECISION_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT c.* INTO v_circle
      FROM public.core_circle c
     WHERE c.id = v_request.circle_id;
    SELECT m.* INTO v_membership
      FROM public.core_organismmembership m
     WHERE m.id = v_request.membership_id;
    IF NOT FOUND OR v_circle.parent_organism_id IS DISTINCT FROM
                         v_membership.living_organism_id
       OR NEW.self_decision IS DISTINCT FROM
                         (NEW.deciding_identity_id = v_membership.identity_id) THEN
        RAISE EXCEPTION 'SBG_CIRCLEDECISION_IDENTITY_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT r.* INTO v_rule
      FROM public.core_governedrule r
     WHERE r.scope_kind = 'LIVING_ORGANISM'
       AND r.scope_organism_id = v_circle.parent_organism_id
       AND r.code = 'GOVERNANCE_CONSTITUTION'
       AND r.rule_uuid = NEW.seat_rule_uuid
       AND r.effective_from <= NEW.effective_at
       AND r.recorded_at <= NEW.recorded_at
     ORDER BY r.effective_from DESC, r.recorded_at DESC, r.version DESC
     LIMIT 1;
    IF NOT FOUND OR NOT EXISTS (
        SELECT 1
          FROM jsonb_array_elements(v_rule.body->'seats') AS s(value)
         WHERE s.value->>'seat_id' = NEW.seat_id
           AND (s.value->>'effective_from')::timestamptz <= NEW.effective_at
    ) THEN
        RAISE EXCEPTION 'SBG_SEAT_ABSENT'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    v_expected_settings_basis := 'rule:' || v_rule.rule_uuid::text
        || '@' || v_rule.version::text || '#' || NEW.seat_id;
    IF NEW.settings_basis IS DISTINCT FROM v_expected_settings_basis THEN
        RAISE EXCEPTION 'SBG_CIRCLEDECISION_SETTINGS_BASIS_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.actor_state IS DISTINCT FROM 'LIVING_ORGANISM'
       OR NEW.actor_id IS NOT NULL
       OR NEW.actor_access_epoch IS NOT NULL
       OR NEW.actor_capacity IS DISTINCT FROM 'STANDING'
       OR NEW.entry_mode IS DISTINCT FROM 'TRANSPORT'
       OR NEW.enterer_identity_id IS DISTINCT FROM NEW.deciding_identity_id
       OR NEW.composing_rule_state IS DISTINCT FROM 'NA_TRANSPORT'
       OR NEW.composing_rule_reference IS NOT NULL
       OR NEW.composer_state IS DISTINCT FROM 'NA_TRANSPORT'
       OR NEW.composer_identity_id IS NOT NULL THEN
        RAISE EXCEPTION 'SBG_CIRCLEDECISION_TRANSPORT_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
"""

# sbg_guard_circleparticipation: BEFORE INSERT on core_circleparticipation
# through sbg_circleparticipation_validate. In order:
# - SBG_CIRCLEPARTICIPATION_DECISION_INVALID unless the admission decision
#   exists with outcome ACCEPT and a request, the participation's Circle and
#   membership match the request's, its seat_rule_uuid and seat_id match the
#   decision's, its effective_at equals the decision's effective_at, and that
#   time is not before the decision's occurred_at;
# - SBG_CIRCLEPARTICIPATION_TRANSPORT_INVALID unless actor_state is
#   LIVING_ORGANISM, actor_id and actor_access_epoch are NULL, actor_capacity
#   is STANDING, entry_mode is TRANSPORT, enterer_identity_id matches the
#   decision's enterer, composing_rule_state and composer_state are
#   NA_TRANSPORT, and composing_rule_reference and composer_identity_id
#   are NULL.
# Membership state, seat occupancy and one-seat-per-person are checked by
# sbg_guard_circleparticipation_occupancy, initially deferred.
# The decision column's uniqueness enforces at most one participation per
# decision. This function does not establish that the decision was inserted
# in the same transaction as the participation.
# Governing: commission v1.0 sections 5.5 and 9; transport shape referenced
# there to section 5.6; amendment 1.
_CIRCLEPARTICIPATION_GUARD_BODY = r"""
DECLARE
    v_decision public.core_circleadmissiondecision%ROWTYPE;
BEGIN
    SELECT d.* INTO v_decision
      FROM public.core_circleadmissiondecision d
     WHERE d.id = NEW.decision_id;
    IF NOT FOUND
       OR v_decision.outcome IS DISTINCT FROM 'ACCEPT'
       OR v_decision.request_id IS NULL
       OR (SELECT q.circle_id FROM public.core_circlejoinrequest q
            WHERE q.id = v_decision.request_id) IS DISTINCT FROM NEW.circle_id
       OR (SELECT q.membership_id FROM public.core_circlejoinrequest q
            WHERE q.id = v_decision.request_id) IS DISTINCT FROM NEW.membership_id
       OR v_decision.seat_rule_uuid IS DISTINCT FROM NEW.seat_rule_uuid
       OR v_decision.seat_id IS DISTINCT FROM NEW.seat_id
       OR NEW.effective_at IS DISTINCT FROM v_decision.effective_at
       OR NEW.effective_at < v_decision.occurred_at THEN
        RAISE EXCEPTION 'SBG_CIRCLEPARTICIPATION_DECISION_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.actor_state IS DISTINCT FROM 'LIVING_ORGANISM'
       OR NEW.actor_id IS NOT NULL
       OR NEW.actor_access_epoch IS NOT NULL
       OR NEW.actor_capacity IS DISTINCT FROM 'STANDING'
       OR NEW.entry_mode IS DISTINCT FROM 'TRANSPORT'
       OR NEW.enterer_identity_id IS DISTINCT FROM v_decision.enterer_identity_id
       OR NEW.composing_rule_state IS DISTINCT FROM 'NA_TRANSPORT'
       OR NEW.composing_rule_reference IS NOT NULL
       OR NEW.composer_state IS DISTINCT FROM 'NA_TRANSPORT'
       OR NEW.composer_identity_id IS NOT NULL THEN
        RAISE EXCEPTION 'SBG_CIRCLEPARTICIPATION_TRANSPORT_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
"""

# sbg_guard_circleparticipation_occupancy: AFTER INSERT FOR EACH ROW through
# the DEFERRABLE INITIALLY DEFERRED constraint trigger
# sbg_circleparticipation_occupancy; SET CONSTRAINTS can force earlier checking.
# First takes pg_advisory_xact_lock(1396852529,
# hashtext(seat_rule_uuid::text || '#' || seat_id)). After the membership/LO
# check, also locks hashtext('person#' || circle_id || '#' || identity_id)
# in the same advisory-lock class. These transaction-scoped locks serialize
# checks sharing either key; hash collisions can add unrelated contention.
# The commission's visibility argument relies on READ COMMITTED reads after
# lock acquisition, not on the locks alone.
#
# In order:
# - SBG_MEMBER_NOT_ELIGIBLE if the membership lookup fails or its LO differs
#   from the Circle's LO;
# - after the person lock, samples clock_timestamp() once into v_known_at;
# - SBG_MEMBER_NOT_ELIGIBLE unless membership folds ACTIVE at NEW.effective_at
#   and NEW.recorded_at;
# - SBG_SEAT_ABSENT unless the matching LO GOVERNANCE_CONSTITUTION version,
#   selected by effective_from DESC, recorded_at DESC, version DESC among
#   versions effective and recorded by NEW's coordinates, lists seat_id with
#   a start no later than NEW.effective_at;
# - SBG_SEAT_ABSENT if any matching later-effective version recorded by
#   NEW.recorded_at omits seat_id;
# - SBG_SEAT_OCCUPIED if another participation in this Circle for the same
#   rule UUID and seat, recorded by v_known_at, has no ending recorded by
#   v_known_at or has such an ending effective after NEW.effective_at;
# - SBG_ALREADY_SEATED if another participation in this Circle for the same
#   identity, recorded by v_known_at, has no ending recorded by v_known_at
#   effective at or before NEW.effective_at.
# Neither occupancy query filters the other participation by its start time.
# v_known_at is this invocation's check-time cutoff, not a commit timestamp.
# Governing: commission v1.0 section 9; amendment 1.
_CIRCLEPARTICIPATION_OCCUPANCY_BODY = r"""
DECLARE
    v_circle public.core_circle%ROWTYPE;
    v_membership public.core_organismmembership%ROWTYPE;
    v_rule public.core_governedrule%ROWTYPE;
    v_seat_occupied boolean;
    v_person_seated boolean;
    v_known_at timestamptz;
BEGIN
    PERFORM pg_advisory_xact_lock(
        1396852529,
        hashtext(NEW.seat_rule_uuid::text || '#' || NEW.seat_id)
    );
    SELECT c.* INTO v_circle
      FROM public.core_circle c
     WHERE c.id = NEW.circle_id;
    SELECT m.* INTO v_membership
      FROM public.core_organismmembership m
     WHERE m.id = NEW.membership_id;
    IF NOT FOUND
       OR v_circle.parent_organism_id IS DISTINCT FROM
            v_membership.living_organism_id THEN
        RAISE EXCEPTION 'SBG_MEMBER_NOT_ELIGIBLE'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    PERFORM pg_advisory_xact_lock(
        1396852529,
        hashtext(
            'person#' || NEW.circle_id::text || '#' || v_membership.identity_id::text
        )
    );
    v_known_at := clock_timestamp();
    IF public.s015_fold_effective_state(
            'core_organismmembership', NEW.membership_id,
            NEW.effective_at, NEW.recorded_at
       ) IS DISTINCT FROM 'ACTIVE' THEN
        RAISE EXCEPTION 'SBG_MEMBER_NOT_ELIGIBLE'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT r.* INTO v_rule
      FROM public.core_governedrule r
     WHERE r.scope_kind = 'LIVING_ORGANISM'
       AND r.scope_organism_id = v_circle.parent_organism_id
       AND r.code = 'GOVERNANCE_CONSTITUTION'
       AND r.rule_uuid = NEW.seat_rule_uuid
       AND r.effective_from <= NEW.effective_at
       AND r.recorded_at <= NEW.recorded_at
     ORDER BY r.effective_from DESC, r.recorded_at DESC, r.version DESC
     LIMIT 1;
    IF NOT FOUND OR NOT EXISTS (
        SELECT 1
          FROM jsonb_array_elements(v_rule.body->'seats') AS s(value)
         WHERE s.value->>'seat_id' = NEW.seat_id
           AND (s.value->>'effective_from')::timestamptz <= NEW.effective_at
    ) THEN
        RAISE EXCEPTION 'SBG_SEAT_ABSENT'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM public.core_governedrule future_rule
         WHERE future_rule.scope_kind = 'LIVING_ORGANISM'
           AND future_rule.scope_organism_id = v_circle.parent_organism_id
           AND future_rule.code = 'GOVERNANCE_CONSTITUTION'
           AND future_rule.rule_uuid = NEW.seat_rule_uuid
           AND future_rule.effective_from > NEW.effective_at
           AND future_rule.recorded_at <= NEW.recorded_at
           AND NOT EXISTS (
                SELECT 1
                  FROM jsonb_array_elements(future_rule.body->'seats') AS s(value)
                 WHERE s.value->>'seat_id' = NEW.seat_id
           )
    ) THEN
        RAISE EXCEPTION 'SBG_SEAT_ABSENT'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT EXISTS (
        SELECT 1
          FROM public.core_circleparticipation p
         WHERE p.circle_id = NEW.circle_id
           AND p.id <> NEW.id
           AND p.seat_rule_uuid = NEW.seat_rule_uuid
           AND p.seat_id = NEW.seat_id
           AND p.recorded_at <= v_known_at
           AND (
                NOT EXISTS (
                    SELECT 1
                      FROM public.core_circleparticipationend e
                     WHERE e.participation_id = p.id
                       AND e.recorded_at <= v_known_at
                )
                OR EXISTS (
                    SELECT 1
                      FROM public.core_circleparticipationend e
                     WHERE e.participation_id = p.id
                       AND e.recorded_at <= v_known_at
                       AND e.effective_at > NEW.effective_at
                )
           )
    ) INTO v_seat_occupied;
    IF v_seat_occupied THEN
        RAISE EXCEPTION 'SBG_SEAT_OCCUPIED'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT EXISTS (
        SELECT 1
          FROM public.core_circleparticipation p
          JOIN public.core_organismmembership m ON m.id = p.membership_id
         WHERE p.circle_id = NEW.circle_id
           AND p.id <> NEW.id
           AND m.identity_id = v_membership.identity_id
           AND p.recorded_at <= v_known_at
           AND NOT EXISTS (
                SELECT 1
                  FROM public.core_circleparticipationend e
                 WHERE e.participation_id = p.id
                   AND e.recorded_at <= v_known_at
                   AND e.effective_at <= NEW.effective_at
            )
    ) INTO v_person_seated;
    IF v_person_seated THEN
        RAISE EXCEPTION 'SBG_ALREADY_SEATED'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
"""

# sbg_guard_circleparticipationend: BEFORE INSERT on core_circleparticipationend
# through sbg_circleparticipationend_validate (guard 2 — validity). Only the
# composed ending of the departure cascade is admitted. In order:
# - SBG_CIRCLEPARTICIPATIONEND_ACTOR_INVALID unless actor_state is NONE with
#   actor_id and actor_access_epoch NULL, actor_capacity NA_NO_PARTY,
#   entry_mode COMPOSED, composing_rule_state and composer_state REFERENCE,
#   enterer_identity_id present and equal to composer_identity_id, and
#   composing_rule_id and composed_from_event_uuid present;
# - SBG_CIRCLEPARTICIPATIONEND_TIME_INVALID if the participation is missing or
#   the ending's effective_at is before the participation's effective_at;
# - SBG_CIRCLEPARTICIPATIONEND_ACTOR_INVALID unless the enterer is the
#   participating member's identity;
# - SBG_CIRCLEPARTICIPATIONEND_RULE_INVALID unless the composing rule is the
#   PLATFORM U3_MEMBERSHIP_END_ENDS_CIRCLE_PARTICIPATION rule composing
#   core_circleparticipationend, is the operative version at the ending's
#   effective_at and recorded_at, and composing_rule_reference is
#   rule:<rule_uuid>@<version> of that row;
# - SBG_CIRCLEPARTICIPATIONEND_TRANSITION_INVALID unless the named transition
#   belongs to the participation's membership, results in ENDED, has the
#   ending's effective_at, and the ending's occurred_at and received_at equal
#   the transition's effective_at and received_at.
# Governing: commission v1.0 sections 8.3 and 8.4 (guard 2); amendment 1, row 22.
_CIRCLEPARTICIPATIONEND_GUARD_BODY = r"""
DECLARE
    v_participation public.core_circleparticipation%ROWTYPE;
    v_rule public.core_governedrule%ROWTYPE;
    v_operative_rule public.core_governedrule%ROWTYPE;
    v_transition public.core_organismmembershiptransition%ROWTYPE;
    v_membership_identity_id bigint;
BEGIN
    IF NEW.actor_state IS DISTINCT FROM 'NONE'
       OR NEW.actor_id IS NOT NULL
       OR NEW.actor_access_epoch IS NOT NULL
       OR NEW.actor_capacity IS DISTINCT FROM 'NA_NO_PARTY'
       OR NEW.entry_mode IS DISTINCT FROM 'COMPOSED'
       OR NEW.enterer_identity_id IS NULL
       OR NEW.composing_rule_id IS NULL
       OR NEW.composing_rule_state IS DISTINCT FROM 'REFERENCE'
       OR NEW.composer_state IS DISTINCT FROM 'REFERENCE'
       OR NEW.composer_identity_id IS DISTINCT FROM NEW.enterer_identity_id
       OR NEW.composed_from_event_uuid IS NULL THEN
        RAISE EXCEPTION 'SBG_CIRCLEPARTICIPATIONEND_ACTOR_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT p.* INTO v_participation
      FROM public.core_circleparticipation p
     WHERE p.id = NEW.participation_id;
    IF NOT FOUND OR NEW.effective_at < v_participation.effective_at THEN
        RAISE EXCEPTION 'SBG_CIRCLEPARTICIPATIONEND_TIME_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT m.identity_id INTO v_membership_identity_id
      FROM public.core_organismmembership m
     WHERE m.id = v_participation.membership_id;
    IF NOT FOUND OR NEW.enterer_identity_id IS DISTINCT FROM
                         v_membership_identity_id THEN
        RAISE EXCEPTION 'SBG_CIRCLEPARTICIPATIONEND_ACTOR_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT r.* INTO v_rule
      FROM public.core_governedrule r
     WHERE r.id = NEW.composing_rule_id;
    IF NOT FOUND
       OR v_rule.code IS DISTINCT FROM 'U3_MEMBERSHIP_END_ENDS_CIRCLE_PARTICIPATION'
       OR v_rule.body->>'composes' IS DISTINCT FROM 'core_circleparticipationend'
       OR v_rule.scope_kind IS DISTINCT FROM 'PLATFORM' THEN
        RAISE EXCEPTION 'SBG_CIRCLEPARTICIPATIONEND_RULE_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT r.* INTO v_operative_rule
      FROM public.core_governedrule r
     WHERE r.scope_kind = 'PLATFORM'
       AND r.code = 'U3_MEMBERSHIP_END_ENDS_CIRCLE_PARTICIPATION'
       AND r.effective_from <= NEW.effective_at
       AND r.recorded_at <= NEW.recorded_at
     ORDER BY r.effective_from DESC, r.recorded_at DESC, r.version DESC
     LIMIT 1;
    IF NOT FOUND
       OR v_operative_rule.id IS DISTINCT FROM v_rule.id
       OR NEW.composing_rule_reference IS DISTINCT FROM
            'rule:' || v_rule.rule_uuid::text || '@' || v_rule.version::text THEN
        RAISE EXCEPTION 'SBG_CIRCLEPARTICIPATIONEND_RULE_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT t.* INTO v_transition
      FROM public.core_organismmembershiptransition t
     WHERE t.event_uuid = NEW.composed_from_event_uuid;
    IF NOT FOUND
       OR v_transition.membership_id IS DISTINCT FROM v_participation.membership_id
       OR v_transition.resulting_state IS DISTINCT FROM 'ENDED'
       OR v_transition.effective_at IS DISTINCT FROM NEW.effective_at
       OR NEW.occurred_at IS DISTINCT FROM v_transition.effective_at
       OR NEW.received_at IS DISTINCT FROM v_transition.received_at THEN
        RAISE EXCEPTION 'SBG_CIRCLEPARTICIPATIONEND_TRANSITION_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
"""

# sbg_guard_membership_end_completeness: AFTER INSERT FOR EACH ROW on
# core_organismmembershiptransition through the DEFERRABLE INITIALLY DEFERRED
# constraint trigger sbg_membership_end_completeness (guard 1 - completeness).
# SET CONSTRAINTS can force earlier checking.
# Transitions not resulting in ENDED return without further checks.
#
# For an ENDED transition, checks for any participation of NEW.membership_id
# that starts after NEW.effective_at or has no ending effective at or before
# NEW.effective_at.
# If one exists:
# - selects a future-starting participation with no ending effective by the
#   departure, ordered by effective_at, recorded_at, then id;
# - if found, raises SBG_DEPARTURE_PRECEDES_PARTICIPATION with JSON DETAIL
#   containing seat_id and started_at (UTC with six fractional-second digits);
# - otherwise raises SBG_MEMBERSHIP_END_PARTICIPATION_INCOMPLETE.
# Each query uses the rows visible to it, without a recorded_at filter.
# This function takes no explicit lock of its own.
# Governing: commission v1.0 section 8.4 (guard 1); amendment 1, row 25.
_MEMBERSHIP_END_COMPLETENESS_BODY = r"""
DECLARE
    v_incomplete boolean;
    v_future_seat_id text;
    v_future_start text;
BEGIN
    IF NEW.resulting_state IS DISTINCT FROM 'ENDED' THEN
        RETURN NEW;
    END IF;
    SELECT EXISTS (
        SELECT 1
          FROM public.core_circleparticipation p
         WHERE p.membership_id = NEW.membership_id
           AND (
                p.effective_at > NEW.effective_at
                OR NOT EXISTS (
                    SELECT 1
                      FROM public.core_circleparticipationend e
                     WHERE e.participation_id = p.id
                       AND e.effective_at <= NEW.effective_at
                )
           )
    ) INTO v_incomplete;
    IF v_incomplete THEN
        SELECT p.seat_id,
               to_char(
                   p.effective_at AT TIME ZONE 'UTC',
                   'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
               )
          INTO v_future_seat_id, v_future_start
          FROM public.core_circleparticipation p
         WHERE p.membership_id = NEW.membership_id
           AND p.effective_at > NEW.effective_at
           AND NOT EXISTS (
                SELECT 1
                  FROM public.core_circleparticipationend e
                 WHERE e.participation_id = p.id
                   AND e.effective_at <= NEW.effective_at
           )
         ORDER BY p.effective_at, p.recorded_at, p.id
         LIMIT 1;
        IF FOUND THEN
            RAISE EXCEPTION 'SBG_DEPARTURE_PRECEDES_PARTICIPATION'
                USING ERRCODE = 'integrity_constraint_violation',
                      DETAIL = json_build_object(
                          'seat_id', v_future_seat_id,
                          'started_at', v_future_start
                      )::text;
        END IF;
        RAISE EXCEPTION 'SBG_MEMBERSHIP_END_PARTICIPATION_INCOMPLETE'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
"""

_PHASE_READINESS = """readiness_code := public.sbg_governance_readiness(
                            NEW.living_organism_id, NEW.effective_at, NEW.recorded_at,
                            'PHASE_ADVANCE'
                        );
                        IF readiness_code IS NOT NULL THEN
                            RAISE EXCEPTION '%', readiness_code
                                USING ERRCODE = 'integrity_constraint_violation';
                        END IF;"""
_BIRTHING_READINESS = """readiness_code := public.sbg_governance_readiness(
                            NEW.living_organism_id, NEW.effective_at, NEW.recorded_at,
                            'BIRTHING'
                        );
                        IF readiness_code IS NOT NULL THEN
                            RAISE EXCEPTION '%', readiness_code
                                USING ERRCODE = 'integrity_constraint_violation';
                        END IF;"""

NEW_SB_GUARD_PHASE_EVENT_BODY = _replace_once(
    PREVIOUS_SB_GUARD_PHASE_EVENT_BODY,
    PHASE_ADVANCE_OLD_BLOCK,
    "ELSIF effective_governance_enabled THEN\n                        "
    + _PHASE_READINESS.replace("\n", "\n                        ")
    + "\n                    END IF;",
    "phase-advance readiness",
)
NEW_SB_GUARD_PHASE_EVENT_BODY = _replace_once(
    NEW_SB_GUARD_PHASE_EVENT_BODY,
    BIRTHING_OLD_BLOCK,
    "IF effective_governance_enabled THEN\n                        "
    + _BIRTHING_READINESS.replace("\n", "\n                        ")
    + "\n                    END IF;",
    "Birthing readiness",
)
NEW_SB_GUARD_PHASE_EVENT_BODY = _replace_once(
    NEW_SB_GUARD_PHASE_EVENT_BODY,
    PHASE_DECLARATION_MARKER,
    PHASE_DECLARATION_MARKER + "\n                    readiness_code text;",
    "phase readiness variable",
)
NEW_SB_GUARD_PHASE_EVENT_BODY = _replace_once(
    NEW_SB_GUARD_PHASE_EVENT_BODY,
    SNAPSHOT_MARKER,
    """OR (effective_governance_enabled AND
                           snapshot_row.body->>'governance_constitution' IS DISTINCT FROM (
                               SELECT 'rule:' || rule.rule_uuid::text || '@' || rule.version::text
                                 FROM public.core_governedrule rule
                                WHERE rule.scope_kind = 'LIVING_ORGANISM'
                                  AND rule.scope_organism_id = organism_row.id
                                  AND rule.code = 'GOVERNANCE_CONSTITUTION'
                                  AND rule.effective_from <= NEW.effective_at
                                  AND rule.recorded_at <= NEW.recorded_at
                                ORDER BY rule.effective_from DESC, rule.recorded_at DESC,
                                         rule.version DESC
                                LIMIT 1
                           ))
                       OR (NOT effective_governance_enabled AND
                           snapshot_row.body->'governance_constitution' IS DISTINCT FROM 'null'::jsonb)""",
    "Birthing snapshot",
)

NEW_S3FU_GUARD_GATEWAY_EFFECT_BODY = _replace_once(
    PREVIOUS_S3FU_GUARD_GATEWAY_EFFECT_BODY,
    GATEWAY_ACTION_MARKER,
    "IF NEW.action NOT IN "
    "('EFFECT_APPLICATION_SETTINGS','EFFECT_APPLICATION_QUESTION',"
    "'EFFECT_GOVERNANCE_CONFIGURATION','PHASE_ADVANCE',"
    "'ADOPT_GOVERNING_INSTRUMENT') THEN",
    "gateway admitted-action",
)
NEW_S3FU_GUARD_GATEWAY_EFFECT_BODY = _replace_once(
    NEW_S3FU_GUARD_GATEWAY_EFFECT_BODY,
    GATEWAY_ADOPTION_MARKER,
    """IF NEW.action = 'ADOPT_GOVERNING_INSTRUMENT' THEN
            IF NEW.prior_state IS DISTINCT FROM NEW.resulting_state
               OR NEW.actor_state IS DISTINCT FROM 'LIVING_ORGANISM'
               OR NEW.actor_id IS NOT NULL
               OR NEW.actor_capacity IS DISTINCT FROM 'STANDING'
               OR NEW.entry_mode IS DISTINCT FROM 'TRANSPORT'
               OR NEW.evidence_reference IS NULL
               OR NOT EXISTS (
                    SELECT 1 FROM public.core_governedrule r
                     WHERE r.authority_reference = 'lo-event:' || NEW.event_uuid::text
                       AND r.scope_organism_id = NEW.living_organism_id
                       AND r.code = 'GOVERNANCE_CONSTITUTION'
                       AND NEW.evidence_reference =
                           'rule:' || r.rule_uuid::text || '@' || r.version::text
               ) THEN
                RAISE EXCEPTION 'SBG_GOVERNEDRULE_EVENT_INVALID'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END IF;
        version_table := CASE NEW.action""",
    "governed-rule event branch",
)
NEW_S3FU_GUARD_GATEWAY_EFFECT_BODY = _replace_once(
    NEW_S3FU_GUARD_GATEWAY_EFFECT_BODY,
    GATEWAY_READINESS_OLD_BLOCK,
    """IF (version_row->>'supersedes_id')::bigint IS DISTINCT FROM previous_id THEN
        RAISE EXCEPTION 'S3FU gateway effect: continuity' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.action = 'EFFECT_GOVERNANCE_CONFIGURATION'
       AND COALESCE((version_row->>'governance_enabled')::boolean, false)
       AND EXISTS (
            SELECT 1 FROM public.core_livingorganismevent birth
             WHERE birth.living_organism_id = NEW.living_organism_id
               AND birth.action = 'BIRTHING'
               AND birth.effective_at <= NEW.effective_at
               AND birth.received_at <= NEW.recorded_at
               AND birth.recorded_at <= NEW.recorded_at
       ) THEN
        SELECT prior.governance_enabled INTO previous_governance_enabled
          FROM public.core_logovernanceconfigurationversion prior
         WHERE prior.id = previous_id;
        IF previous_governance_enabled IS DISTINCT FROM true THEN
            readiness_code := public.sbg_governance_readiness(
                NEW.living_organism_id, NEW.effective_at, NEW.recorded_at, 'ENABLING'
            );
            IF readiness_code IS NOT NULL THEN
                RAISE EXCEPTION '%', readiness_code
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
        END IF;
    END IF;""",
    "gateway readiness",
)
NEW_S3FU_GUARD_GATEWAY_EFFECT_BODY = _replace_once(
    NEW_S3FU_GUARD_GATEWAY_EFFECT_BODY,
    GATEWAY_DECLARATION_MARKER,
    GATEWAY_DECLARATION_MARKER
    + "\n        readiness_code text; previous_governance_enabled boolean;",
    "gateway readiness variables",
)


def _prefix_plpgsql_variables(body, names):
    parts = re.split(r"('(?:''|[^'])*')", body)
    for index in range(0, len(parts), 2):
        for old_name, new_name in names.items():
            parts[index] = re.sub(
                rf"(?<![\w.]){re.escape(old_name)}(?!\w)",
                new_name,
                parts[index],
            )
    return "".join(parts)


_READINESS_VARIABLES = {
    "organism_id": "p_organism_id",
    "at_effective": "p_at_effective",
    "at_recorded": "p_at_recorded",
    "lifecycle": "p_lifecycle",
    "circle_id": "v_circle_id",
    "circle_state": "v_circle_state",
    "activated": "v_activated",
    "rule_row": "v_rule_row",
    "seat": "v_seat",
    "seat_id": "v_seat_id",
    "route": "v_route",
    "deciding_body": "v_deciding_body",
    "seat_start": "v_seat_start",
    "seat_count": "v_seat_count",
    "distinct_seat_count": "v_distinct_seat_count",
    "in_effect_seat_count": "v_in_effect_seat_count",
    "available_seat": "v_available_seat",
}
_PHASE_VARIABLES = {
    "readiness_code": "v_readiness_code",
}
_GATEWAY_VARIABLES = {
    "readiness_code": "v_readiness_code",
    "previous_governance_enabled": "v_previous_governance_enabled",
}

_READINESS_FUNCTION_BODY = _prefix_plpgsql_variables(
    _READINESS_FUNCTION_BODY,
    _READINESS_VARIABLES,
)
NEW_SB_GUARD_PHASE_EVENT_BODY = _prefix_plpgsql_variables(
    NEW_SB_GUARD_PHASE_EVENT_BODY,
    _PHASE_VARIABLES,
)
NEW_S3FU_GUARD_GATEWAY_EFFECT_BODY = _prefix_plpgsql_variables(
    NEW_S3FU_GUARD_GATEWAY_EFFECT_BODY,
    _GATEWAY_VARIABLES,
)


def _function_sql(name, body, arguments="", return_type="trigger"):
    return (
        f"CREATE OR REPLACE FUNCTION public.{name}({arguments}) "
        f"RETURNS {return_type} LANGUAGE plpgsql AS $${body}$$"
    )


def _validate_body_structure(name, body):
    sql = re.sub(r"'(?:''|[^'])*'", "''", body)
    sql = re.sub(r"--[^\n]*", "", sql)
    end_if = len(re.findall(r"\bEND\s+IF\b", sql, flags=re.IGNORECASE))
    if_tokens = len(re.findall(r"\bIF\b", sql, flags=re.IGNORECASE))
    begin_blocks = len(re.findall(r"\bBEGIN\b", sql, flags=re.IGNORECASE))
    end_tokens = len(re.findall(r"\bEND\b", sql, flags=re.IGNORECASE))
    case_blocks = len(re.findall(r"\bCASE\b", sql, flags=re.IGNORECASE))
    end_loop = len(re.findall(r"\bEND\s+LOOP\b", sql, flags=re.IGNORECASE))
    if_blocks = if_tokens - end_if
    end_blocks = end_tokens - end_if - case_blocks - end_loop
    if if_blocks != end_if or begin_blocks != end_blocks:
        raise RuntimeError(
            f"0045 SQL structure imbalance in {name}: "
            f"IF={if_blocks}, END IF={end_if}, BEGIN={begin_blocks}, END={end_blocks}"
        )
    return if_blocks, end_if, begin_blocks, end_blocks


NEW_FUNCTION_BODIES = (
    ("sbg_governance_readiness", _READINESS_FUNCTION_BODY),
    ("sbg_assign_governedrule_commitment", _GOVERNEDRULE_COMMITMENT_BODY),
    ("sbg_guard_governedrule", _GOVERNEDRULE_GUARD_BODY),
    ("sbg_guard_governedrule_adopting_event", _GOVERNEDRULE_ADOPTING_EVENT_BODY),
    ("sbg_seed_platform_rules", _SEED_PLATFORM_RULES_BODY),
    ("sbg_guard_circlejoinrequest", _CIRCLEJOINREQUEST_GUARD_BODY),
    ("sbg_guard_circleadmissiondecision", _CIRCLEADMISSIONDECISION_GUARD_BODY),
    ("sbg_guard_circleparticipation", _CIRCLEPARTICIPATION_GUARD_BODY),
    ("sbg_guard_circleparticipation_occupancy", _CIRCLEPARTICIPATION_OCCUPANCY_BODY),
    ("sbg_guard_circleparticipationend", _CIRCLEPARTICIPATIONEND_GUARD_BODY),
    ("sbg_guard_membership_end_completeness", _MEMBERSHIP_END_COMPLETENESS_BODY),
)
REPLACED_FUNCTION_BODIES = (
    ("sb_guard_phase_event", NEW_SB_GUARD_PHASE_EVENT_BODY),
    ("s3fu_guard_gateway_effect", NEW_S3FU_GUARD_GATEWAY_EFFECT_BODY),
)
BUILT_FUNCTION_BODIES = NEW_FUNCTION_BODIES + REPLACED_FUNCTION_BODIES
BODY_STRUCTURE_COUNTS = {
    name: _validate_body_structure(name, body)
    for name, body in BUILT_FUNCTION_BODIES
}

FUNCTION_PARAMETERS = {
    "sbg_governance_readiness": {
        "p_organism_id",
        "p_at_effective",
        "p_at_recorded",
        "p_lifecycle",
    },
    "sbg_assign_governedrule_commitment": set(),
    "sbg_guard_governedrule": set(),
    "sbg_guard_governedrule_adopting_event": set(),
    "sbg_seed_platform_rules": set(),
    "sbg_guard_circlejoinrequest": set(),
    "sbg_guard_circleadmissiondecision": set(),
    "sbg_guard_circleparticipation": set(),
    "sbg_guard_circleparticipation_occupancy": set(),
    "sbg_guard_circleparticipationend": set(),
    "sbg_guard_membership_end_completeness": set(),
}


def _declared_local_variables(body):
    match = re.search(r"\bDECLARE\b(.*?)\bBEGIN\b", body, flags=re.IGNORECASE | re.DOTALL)
    if match is None:
        raise RuntimeError("0045 trigger function has no DECLARE section")
    names = set()
    for declaration in match.group(1).split(";"):
        declaration = re.sub(r"^\s*DECLARE\b", "", declaration, flags=re.IGNORECASE).strip()
        if not declaration:
            continue
        variable = re.match(r"([a-z_][a-z0-9_]*)\s+", declaration, flags=re.IGNORECASE)
        if variable is None:
            raise RuntimeError(f"unable to parse 0045 PL/pgSQL declaration: {declaration}")
        names.add(variable.group(1).lower())
    return names


def validate_function_variable_columns(table_columns):
    for name, body in NEW_FUNCTION_BODIES:
        variables = FUNCTION_PARAMETERS[name] | _declared_local_variables(body)
        if any(not variable.startswith(("p_", "v_")) for variable in variables):
            raise RuntimeError(f"0045 function {name} has an unprefixed PL/pgSQL variable")
        tables = set(re.findall(r"\bcore_[a-z0-9_]+\b", body, flags=re.IGNORECASE))
        columns = set()
        for table in tables:
            if table not in table_columns:
                raise RuntimeError(f"0045 function {name} references unknown model table {table}")
            columns.update(table_columns[table])
        collisions = variables & columns
        if collisions:
            raise RuntimeError(
                f"0045 function {name} variable/column collision: {sorted(collisions)}"
            )
    for name, body in REPLACED_FUNCTION_BODIES:
        added_variables = (
            {"v_readiness_code"}
            if name == "sb_guard_phase_event"
            else {"v_readiness_code", "v_previous_governance_enabled"}
        )
        declared_variables = _declared_local_variables(body)
        if not added_variables.issubset(declared_variables):
            raise RuntimeError(f"0045 function {name} is missing an SB-G variable declaration")
        if any(not variable.startswith(("p_", "v_")) for variable in added_variables):
            raise RuntimeError(f"0045 function {name} has an unprefixed SB-G variable")


def render_sql(table_columns):
    validate_function_variable_columns(table_columns)
    return SQL_RENDER


FUNCTION_SQL = (
    _function_sql("sbg_guard_circlejoinrequest", _CIRCLEJOINREQUEST_GUARD_BODY),
    _function_sql(
        "sbg_guard_circleadmissiondecision",
        _CIRCLEADMISSIONDECISION_GUARD_BODY,
    ),
    _function_sql(
        "sbg_guard_circleparticipation",
        _CIRCLEPARTICIPATION_GUARD_BODY,
    ),
    _function_sql(
        "sbg_guard_circleparticipation_occupancy",
        _CIRCLEPARTICIPATION_OCCUPANCY_BODY,
    ),
    _function_sql(
        "sbg_guard_circleparticipationend",
        _CIRCLEPARTICIPATIONEND_GUARD_BODY,
    ),
    _function_sql(
        "sbg_guard_membership_end_completeness",
        _MEMBERSHIP_END_COMPLETENESS_BODY,
    ),
    _function_sql(
        "sbg_assign_governedrule_commitment",
        _GOVERNEDRULE_COMMITMENT_BODY,
    ),
    _function_sql(
        "sbg_guard_governedrule",
        _GOVERNEDRULE_GUARD_BODY,
    ),
    _function_sql(
        "sbg_guard_governedrule_adopting_event",
        _GOVERNEDRULE_ADOPTING_EVENT_BODY,
    ),
    _function_sql(
        "sbg_seed_platform_rules",
        _SEED_PLATFORM_RULES_BODY,
        return_type="void",
    ),
    _function_sql(
        "sbg_governance_readiness",
        _READINESS_FUNCTION_BODY,
        "p_organism_id bigint, p_at_effective timestamptz, "
        "p_at_recorded timestamptz, p_lifecycle text",
        "text",
    ),
    _function_sql("sb_guard_phase_event", NEW_SB_GUARD_PHASE_EVENT_BODY),
    _function_sql("s3fu_guard_gateway_effect", NEW_S3FU_GUARD_GATEWAY_EFFECT_BODY),
)
SCHEMA_SQL = (
    "ALTER TABLE public.core_circlestateevent "
    "DROP CONSTRAINT s015_0022_circlestateevent_actor_state_vocab_ck",
    "ALTER TABLE public.core_circlestateevent "
    "ADD CONSTRAINT s015_0022_circlestateevent_actor_state_vocab_ck "
    "CHECK (actor_state IN ('PARTY','NONE','NOT_ESTABLISHED','LIVING_ORGANISM'))",
    "ALTER TABLE public.core_circlestateevent "
    "DROP CONSTRAINT s015_0022_circlestateevent_actor_capacity_state_ck",
    "ALTER TABLE public.core_circlestateevent "
    "ADD CONSTRAINT s015_0022_circlestateevent_actor_capacity_state_ck "
    "CHECK ((actor_state IN ('PARTY','LIVING_ORGANISM')) = (actor_capacity = 'STANDING'))",
    "CREATE TRIGGER s015_circle_kind_immutable BEFORE UPDATE OF kind "
    "ON public.core_circle FOR EACH ROW "
    "EXECUTE FUNCTION public.s015_refuse_row_mutation()",
    "CREATE TRIGGER sbg_governedrule_commitment BEFORE INSERT "
    "ON public.core_governedrule FOR EACH ROW "
    "EXECUTE FUNCTION public.sbg_assign_governedrule_commitment()",
    "CREATE TRIGGER sbg_governedrule_record_time BEFORE INSERT "
    "ON public.core_governedrule FOR EACH ROW "
    "EXECUTE FUNCTION public.s3_assign_record_time()",
    "CREATE TRIGGER sbg_governedrule_validate BEFORE INSERT "
    "ON public.core_governedrule FOR EACH ROW "
    "EXECUTE FUNCTION public.sbg_guard_governedrule()",
    "CREATE TRIGGER sbg_governedrule_immutable BEFORE UPDATE OR DELETE "
    "ON public.core_governedrule FOR EACH ROW "
    "EXECUTE FUNCTION public.s015_refuse_row_mutation()",
    "CREATE TRIGGER s015_governedrule_truncate_immutable BEFORE TRUNCATE "
    "ON public.core_governedrule FOR EACH STATEMENT "
    "EXECUTE FUNCTION public.s015_refuse_row_mutation()",
    "CREATE CONSTRAINT TRIGGER sbg_governedrule_adopting_event AFTER INSERT "
    "ON public.core_governedrule DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
    "EXECUTE FUNCTION public.sbg_guard_governedrule_adopting_event()",
    "CREATE TRIGGER sbg_circlejoinrequest_record_time BEFORE INSERT "
    "ON public.core_circlejoinrequest FOR EACH ROW "
    "EXECUTE FUNCTION public.s3_assign_record_time()",
    "CREATE TRIGGER sbg_circlejoinrequest_validate BEFORE INSERT "
    "ON public.core_circlejoinrequest FOR EACH ROW "
    "EXECUTE FUNCTION public.sbg_guard_circlejoinrequest()",
    "CREATE TRIGGER sbg_circlejoinrequest_immutable BEFORE UPDATE OR DELETE "
    "ON public.core_circlejoinrequest FOR EACH ROW "
    "EXECUTE FUNCTION public.s015_refuse_row_mutation()",
    "CREATE TRIGGER s015_circlejoinrequest_truncate_immutable BEFORE TRUNCATE "
    "ON public.core_circlejoinrequest FOR EACH STATEMENT "
    "EXECUTE FUNCTION public.s015_refuse_row_mutation()",
    "CREATE TRIGGER sbg_circleadmissiondecision_record_time BEFORE INSERT "
    "ON public.core_circleadmissiondecision FOR EACH ROW "
    "EXECUTE FUNCTION public.s3_assign_record_time()",
    "CREATE TRIGGER sbg_circleadmissiondecision_validate BEFORE INSERT "
    "ON public.core_circleadmissiondecision FOR EACH ROW "
    "EXECUTE FUNCTION public.sbg_guard_circleadmissiondecision()",
    "CREATE TRIGGER sbg_circleadmissiondecision_immutable BEFORE UPDATE OR DELETE "
    "ON public.core_circleadmissiondecision FOR EACH ROW "
    "EXECUTE FUNCTION public.s015_refuse_row_mutation()",
    "CREATE TRIGGER s015_circleadmissiondecision_truncate_immutable BEFORE TRUNCATE "
    "ON public.core_circleadmissiondecision FOR EACH STATEMENT "
    "EXECUTE FUNCTION public.s015_refuse_row_mutation()",
    "CREATE TRIGGER sbg_circleparticipation_record_time BEFORE INSERT "
    "ON public.core_circleparticipation FOR EACH ROW "
    "EXECUTE FUNCTION public.s3_assign_record_time()",
    "CREATE TRIGGER sbg_circleparticipation_validate BEFORE INSERT "
    "ON public.core_circleparticipation FOR EACH ROW "
    "EXECUTE FUNCTION public.sbg_guard_circleparticipation()",
    "CREATE CONSTRAINT TRIGGER sbg_circleparticipation_occupancy AFTER INSERT "
    "ON public.core_circleparticipation DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
    "EXECUTE FUNCTION public.sbg_guard_circleparticipation_occupancy()",
    "CREATE TRIGGER sbg_circleparticipation_immutable BEFORE UPDATE OR DELETE "
    "ON public.core_circleparticipation FOR EACH ROW "
    "EXECUTE FUNCTION public.s015_refuse_row_mutation()",
    "CREATE TRIGGER s015_circleparticipation_truncate_immutable BEFORE TRUNCATE "
    "ON public.core_circleparticipation FOR EACH STATEMENT "
    "EXECUTE FUNCTION public.s015_refuse_row_mutation()",
    "CREATE TRIGGER sbg_circleparticipationend_record_time BEFORE INSERT "
    "ON public.core_circleparticipationend FOR EACH ROW "
    "EXECUTE FUNCTION public.s3_assign_record_time()",
    "CREATE TRIGGER sbg_circleparticipationend_validate BEFORE INSERT "
    "ON public.core_circleparticipationend FOR EACH ROW "
    "EXECUTE FUNCTION public.sbg_guard_circleparticipationend()",
    "CREATE TRIGGER sbg_circleparticipationend_immutable BEFORE UPDATE OR DELETE "
    "ON public.core_circleparticipationend FOR EACH ROW "
    "EXECUTE FUNCTION public.s015_refuse_row_mutation()",
    "CREATE TRIGGER s015_circleparticipationend_truncate_immutable BEFORE TRUNCATE "
    "ON public.core_circleparticipationend FOR EACH STATEMENT "
    "EXECUTE FUNCTION public.s015_refuse_row_mutation()",
    "CREATE CONSTRAINT TRIGGER sbg_membership_end_completeness AFTER INSERT "
    "ON public.core_organismmembershiptransition "
    "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
    "EXECUTE FUNCTION public.sbg_guard_membership_end_completeness()",
    "SELECT public.sbg_seed_platform_rules()",
    "SET CONSTRAINTS ALL IMMEDIATE",
)
MODEL_CONSTRAINT_SQL = (
    "ALTER TABLE public.core_circle ADD COLUMN kind varchar(16) "
    "DEFAULT 'GENERIC' NOT NULL",
    "ALTER TABLE public.core_circle ADD CONSTRAINT sbg_circle_kind_vocab_ck "
    "CHECK (kind IN ('GENERIC','GOVERNANCE','APPLICATION','FINANCE','OPERATIONS'))",
    "CREATE UNIQUE INDEX s015_circle_one_governance_uidx "
    "ON public.core_circle (parent_organism_id) WHERE kind = 'GOVERNANCE'",
    "ALTER TABLE public.core_governedrule ADD CONSTRAINT sbg_governedrule_uuid_version_uniq "
    "UNIQUE (rule_uuid, version)",
    "ALTER TABLE public.core_governedrule ADD CONSTRAINT sbg_governedrule_version_positive_ck "
    "CHECK (version > 0)",
    "ALTER TABLE public.core_governedrule ADD CONSTRAINT sbg_governedrule_scope_vocab_ck "
    "CHECK (scope_kind IN ('PLATFORM','LIVING_ORGANISM','CIRCLE'))",
    "ALTER TABLE public.core_governedrule ADD CONSTRAINT sbg_governedrule_code_vocab_ck "
    "CHECK (code IN ('GOVERNANCE_CONSTITUTION','U3_MEMBERSHIP_END_ENDS_CIRCLE_PARTICIPATION'))",
    "ALTER TABLE public.core_governedrule ADD CONSTRAINT sbg_governedrule_scope_target_ck "
    "CHECK ((scope_kind = 'PLATFORM' AND scope_organism_id IS NULL AND scope_circle_id IS NULL) "
    "OR (scope_kind = 'LIVING_ORGANISM' AND scope_organism_id IS NOT NULL AND scope_circle_id IS NULL) "
    "OR (scope_kind = 'CIRCLE' AND scope_organism_id IS NULL AND scope_circle_id IS NOT NULL))",
    "ALTER TABLE public.core_governedrule ADD CONSTRAINT sbg_governedrule_source_vocab_ck "
    "CHECK (source IN ('FOUNDING_BODY_PRESCRIPTION','LO_PRESCRIPTION','PLATFORM_RULING'))",
    "ALTER TABLE public.core_circlejoinrequest ADD CONSTRAINT sbg_circlejoinrequest_initiator_ck "
    "CHECK (initiated_by IN ('APPLICANT','DECIDER'))",
    "ALTER TABLE public.core_circleadmissiondecision ADD CONSTRAINT sbg_circledecision_route_ck "
    "CHECK (route IN ('CIRCLE_FOUNDING','GOVERNANCE_CONSTITUTIONAL'))",
    "ALTER TABLE public.core_circleadmissiondecision ADD CONSTRAINT sbg_circledecision_route_shape_ck "
    "CHECK ((route = 'CIRCLE_FOUNDING' AND constitutional_route = 'NOT_APPLICABLE' "
    "AND deciding_party = 'FOUNDING_BODY') OR (route = 'GOVERNANCE_CONSTITUTIONAL' "
    "AND constitutional_route = 'APPOINTMENT' AND deciding_party = 'office:LO_COORDINATOR'))",
    "ALTER TABLE public.core_circleadmissiondecision ADD CONSTRAINT sbg_circledecision_outcome_ck "
    "CHECK (outcome IN ('ACCEPT','DECLINE'))",
    "ALTER TABLE public.core_circleadmissiondecision ADD CONSTRAINT sbg_circledecision_transport_ck "
    "CHECK (actor_state = 'LIVING_ORGANISM' AND actor_id IS NULL "
    "AND actor_access_epoch IS NULL AND actor_capacity = 'STANDING' "
    "AND entry_mode = 'TRANSPORT' AND composing_rule_state = 'NA_TRANSPORT' "
    "AND composing_rule_reference IS NULL AND composer_state = 'NA_TRANSPORT' "
    "AND composer_identity_id IS NULL)",
    "ALTER TABLE public.core_circleparticipation ADD CONSTRAINT sbg_circleparticipation_transport_ck "
    "CHECK (actor_state = 'LIVING_ORGANISM' AND actor_id IS NULL "
    "AND actor_access_epoch IS NULL AND actor_capacity = 'STANDING' "
    "AND entry_mode = 'TRANSPORT' AND composing_rule_state = 'NA_TRANSPORT' "
    "AND composing_rule_reference IS NULL AND composer_state = 'NA_TRANSPORT' "
    "AND composer_identity_id IS NULL)",
    "ALTER TABLE public.core_circleparticipationend ADD CONSTRAINT sbg_circleparticipationend_composed_ck "
    "CHECK (actor_state = 'NONE' AND actor_id IS NULL AND actor_access_epoch IS NULL "
    "AND actor_capacity = 'NA_NO_PARTY' AND entry_mode = 'COMPOSED' "
    "AND composing_rule_state = 'REFERENCE' AND composing_rule_id IS NOT NULL "
    "AND composing_rule_reference IS NOT NULL AND composer_state = 'REFERENCE' "
    "AND composer_identity_id IS NOT NULL AND composed_from_event_uuid IS NOT NULL)",
)
SQL_RENDER = "\n\n".join(MODEL_CONSTRAINT_SQL + FUNCTION_SQL + SCHEMA_SQL)


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for statement in FUNCTION_SQL:
            cursor.execute(statement)
        for statement in SCHEMA_SQL:
            cursor.execute(statement)


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM public.core_governedrule "
            "WHERE scope_kind = 'LIVING_ORGANISM') "
            "OR EXISTS (SELECT 1 FROM public.core_circlejoinrequest) "
            "OR EXISTS (SELECT 1 FROM public.core_circleadmissiondecision) "
            "OR EXISTS (SELECT 1 FROM public.core_circleparticipation) "
            "OR EXISTS (SELECT 1 FROM public.core_circleparticipationend)"
        )
        if cursor.fetchone()[0]:
            raise RuntimeError(
                "0045 reverse refused while SB-G governed data exists"
            )
        for trigger_name, table_name in (
            ("sbg_membership_end_completeness", "core_organismmembershiptransition"),
            ("s015_circleparticipationend_truncate_immutable", "core_circleparticipationend"),
            ("sbg_circleparticipationend_immutable", "core_circleparticipationend"),
            ("sbg_circleparticipationend_validate", "core_circleparticipationend"),
            ("sbg_circleparticipationend_record_time", "core_circleparticipationend"),
            ("s015_circleparticipation_truncate_immutable", "core_circleparticipation"),
            ("sbg_circleparticipation_immutable", "core_circleparticipation"),
            ("sbg_circleparticipation_occupancy", "core_circleparticipation"),
            ("sbg_circleparticipation_validate", "core_circleparticipation"),
            ("sbg_circleparticipation_record_time", "core_circleparticipation"),
            ("s015_circleadmissiondecision_truncate_immutable", "core_circleadmissiondecision"),
            ("sbg_circleadmissiondecision_immutable", "core_circleadmissiondecision"),
            ("sbg_circleadmissiondecision_validate", "core_circleadmissiondecision"),
            ("sbg_circleadmissiondecision_record_time", "core_circleadmissiondecision"),
            ("s015_circlejoinrequest_truncate_immutable", "core_circlejoinrequest"),
            ("sbg_circlejoinrequest_immutable", "core_circlejoinrequest"),
            ("sbg_circlejoinrequest_validate", "core_circlejoinrequest"),
            ("sbg_circlejoinrequest_record_time", "core_circlejoinrequest"),
            ("sbg_governedrule_adopting_event", "core_governedrule"),
            ("s015_governedrule_truncate_immutable", "core_governedrule"),
            ("sbg_governedrule_immutable", "core_governedrule"),
            ("sbg_governedrule_validate", "core_governedrule"),
            ("sbg_governedrule_record_time", "core_governedrule"),
            ("sbg_governedrule_commitment", "core_governedrule"),
            ("s015_circle_kind_immutable", "core_circle"),
        ):
            cursor.execute(
                f"DROP TRIGGER IF EXISTS {trigger_name} ON public.{table_name}"
            )
        cursor.execute(
            "ALTER TABLE public.core_circlestateevent "
            "DROP CONSTRAINT s015_0022_circlestateevent_actor_state_vocab_ck"
        )
        cursor.execute(
            "ALTER TABLE public.core_circlestateevent "
            "ADD CONSTRAINT s015_0022_circlestateevent_actor_state_vocab_ck "
            "CHECK (actor_state IN ('PARTY','NONE','NOT_ESTABLISHED'))"
        )
        cursor.execute(
            "ALTER TABLE public.core_circlestateevent "
            "DROP CONSTRAINT s015_0022_circlestateevent_actor_capacity_state_ck"
        )
        cursor.execute(
            "ALTER TABLE public.core_circlestateevent "
            "ADD CONSTRAINT s015_0022_circlestateevent_actor_capacity_state_ck "
            "CHECK ((actor_state = 'PARTY') = (actor_capacity = 'STANDING'))"
        )
        cursor.execute("DROP FUNCTION public.sbg_seed_platform_rules()")
        cursor.execute("DROP FUNCTION public.sbg_guard_membership_end_completeness()")
        cursor.execute("DROP FUNCTION public.sbg_guard_circleparticipationend()")
        cursor.execute("DROP FUNCTION public.sbg_guard_circleparticipation_occupancy()")
        cursor.execute("DROP FUNCTION public.sbg_guard_circleparticipation()")
        cursor.execute("DROP FUNCTION public.sbg_guard_circleadmissiondecision()")
        cursor.execute("DROP FUNCTION public.sbg_guard_circlejoinrequest()")
        cursor.execute("DROP FUNCTION public.sbg_guard_governedrule_adopting_event()")
        cursor.execute("DROP FUNCTION public.sbg_guard_governedrule()")
        cursor.execute("DROP FUNCTION public.sbg_assign_governedrule_commitment()")
        cursor.execute(
            "DROP FUNCTION public.sbg_governance_readiness(bigint, timestamptz, timestamptz, text)"
        )
        cursor.execute(
            "CREATE OR REPLACE FUNCTION public.sb_guard_phase_event() "
            "RETURNS trigger LANGUAGE plpgsql AS $$"
            + PREVIOUS_SB_GUARD_PHASE_EVENT_BODY
            + "$$"
        )
        cursor.execute(
            "CREATE OR REPLACE FUNCTION public.s3fu_guard_gateway_effect() "
            "RETURNS trigger LANGUAGE plpgsql AS $$"
            + PREVIOUS_S3FU_GUARD_GATEWAY_EFFECT_BODY
            + "$$"
        )


def restore_platform_rules(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT public.sbg_seed_platform_rules()")


class Migration(migrations.Migration):
    dependencies = [("core", "0044_sb2_birthing_snapshot")]
    operations = [
        migrations.AddField(
            model_name="circle",
            name="kind",
            field=models.CharField(
                choices=[
                    ("GENERIC", "Generic"),
                    ("GOVERNANCE", "Governance"),
                    ("APPLICATION", "Application"),
                    ("FINANCE", "Finance"),
                    ("OPERATIONS", "Operations"),
                ],
                db_default="GENERIC",
                default="GENERIC",
                max_length=16,
            ),
        ),
        migrations.AddConstraint(
            model_name="circle",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    kind__in=("GENERIC", "GOVERNANCE", "APPLICATION", "FINANCE", "OPERATIONS")
                ),
                name="sbg_circle_kind_vocab_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="circle",
            constraint=models.UniqueConstraint(
                fields=("parent_organism",),
                condition=models.Q(kind="GOVERNANCE"),
                name="s015_circle_one_governance_uidx",
            ),
        ),
        migrations.CreateModel(
            name="GovernedRule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("rule_uuid", models.UUIDField()),
                ("version", models.PositiveIntegerField()),
                ("scope_kind", models.CharField(max_length=20)),
                ("code", models.CharField(max_length=64)),
                ("body", models.JSONField()),
                ("text_commitment", models.CharField(editable=False, max_length=71, null=True)),
                ("authority_reference", models.CharField(max_length=255)),
                ("source", models.CharField(max_length=32)),
                ("effective_from", models.DateTimeField()),
                ("decided_at", models.DateTimeField()),
                ("observations", models.JSONField(blank=True, null=True)),
                ("recorded_at", models.DateTimeField(editable=False, null=True)),
                ("enterer_identity", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="core.identity")),
                ("scope_circle", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="core.circle")),
                ("scope_organism", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="core.livingorganism")),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(fields=("rule_uuid", "version"), name="sbg_governedrule_uuid_version_uniq"),
                    models.CheckConstraint(condition=models.Q(version__gt=0), name="sbg_governedrule_version_positive_ck"),
                    models.CheckConstraint(condition=models.Q(scope_kind__in=("PLATFORM", "LIVING_ORGANISM", "CIRCLE")), name="sbg_governedrule_scope_vocab_ck"),
                    models.CheckConstraint(condition=models.Q(code__in=("GOVERNANCE_CONSTITUTION", "U3_MEMBERSHIP_END_ENDS_CIRCLE_PARTICIPATION")), name="sbg_governedrule_code_vocab_ck"),
                    models.CheckConstraint(
                        condition=(
                            models.Q(scope_kind="PLATFORM", scope_organism__isnull=True, scope_circle__isnull=True)
                            | models.Q(scope_kind="LIVING_ORGANISM", scope_organism__isnull=False, scope_circle__isnull=True)
                            | models.Q(scope_kind="CIRCLE", scope_organism__isnull=True, scope_circle__isnull=False)
                        ),
                        name="sbg_governedrule_scope_target_ck",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            source__in=(
                                "FOUNDING_BODY_PRESCRIPTION",
                                "LO_PRESCRIPTION",
                                "PLATFORM_RULING",
                            )
                        ),
                        name="sbg_governedrule_source_vocab_ck",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="CircleJoinRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("request_uuid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("seat_rule_uuid", models.UUIDField()),
                ("seat_id", models.CharField(max_length=64)),
                ("initiated_by", models.CharField(max_length=16)),
                ("consent_reference", models.CharField(max_length=255)),
                ("occurred_at", models.DateTimeField()),
                ("effective_at", models.DateTimeField()),
                ("received_at", models.DateTimeField()),
                ("recorded_at", models.DateTimeField(editable=False, null=True)),
                ("circle", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="+", to="core.circle")),
                ("enterer_identity", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="+", to="core.identity")),
                ("membership", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="+", to="core.organismmembership")),
            ],
            options={
                "constraints": [
                    models.CheckConstraint(condition=models.Q(initiated_by__in=("APPLICANT", "DECIDER")), name="sbg_circlejoinrequest_initiator_ck"),
                ],
            },
        ),
        migrations.CreateModel(
            name="CircleAdmissionDecision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("decision_uuid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("route", models.CharField(max_length=32)),
                ("constitutional_route", models.CharField(max_length=32)),
                ("deciding_party", models.CharField(max_length=64)),
                ("self_decision", models.BooleanField()),
                ("outcome", models.CharField(max_length=16)),
                ("authority_decision_reference", models.CharField(max_length=71)),
                ("settings_basis", models.CharField(max_length=255)),
                ("seat_rule_uuid", models.UUIDField()),
                ("seat_id", models.CharField(max_length=64)),
                ("actor_state", models.CharField(max_length=24)),
                ("actor_access_epoch", models.PositiveBigIntegerField(blank=True, null=True)),
                ("actor_capacity", models.CharField(max_length=24)),
                ("entry_mode", models.CharField(max_length=24)),
                ("composing_rule_state", models.CharField(max_length=24)),
                ("composing_rule_reference", models.CharField(blank=True, max_length=255, null=True)),
                ("composer_state", models.CharField(max_length=24)),
                ("occurred_at", models.DateTimeField()),
                ("effective_at", models.DateTimeField()),
                ("received_at", models.DateTimeField()),
                ("recorded_at", models.DateTimeField(editable=False, null=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="core.identity")),
                ("composer_identity", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="core.identity")),
                ("deciding_identity", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="+", to="core.identity")),
                ("enterer_identity", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="+", to="core.identity")),
                ("request", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="+", to="core.circlejoinrequest")),
            ],
            options={
                "constraints": [
                    models.CheckConstraint(condition=models.Q(route__in=("CIRCLE_FOUNDING", "GOVERNANCE_CONSTITUTIONAL")), name="sbg_circledecision_route_ck"),
                    models.CheckConstraint(
                        condition=(
                            models.Q(
                                route="CIRCLE_FOUNDING",
                                constitutional_route="NOT_APPLICABLE",
                                deciding_party="FOUNDING_BODY",
                            )
                            | models.Q(
                                route="GOVERNANCE_CONSTITUTIONAL",
                                constitutional_route="APPOINTMENT",
                                deciding_party="office:LO_COORDINATOR",
                            )
                        ),
                        name="sbg_circledecision_route_shape_ck",
                    ),
                    models.CheckConstraint(condition=models.Q(outcome__in=("ACCEPT", "DECLINE")), name="sbg_circledecision_outcome_ck"),
                    models.CheckConstraint(
                        condition=models.Q(
                            actor_state="LIVING_ORGANISM",
                            actor__isnull=True,
                            actor_access_epoch__isnull=True,
                            actor_capacity="STANDING",
                            entry_mode="TRANSPORT",
                            composing_rule_state="NA_TRANSPORT",
                            composing_rule_reference__isnull=True,
                            composer_state="NA_TRANSPORT",
                            composer_identity__isnull=True,
                        ),
                        name="sbg_circledecision_transport_ck",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="CircleParticipation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("participation_uuid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("seat_rule_uuid", models.UUIDField()),
                ("seat_id", models.CharField(max_length=64)),
                ("actor_state", models.CharField(max_length=24)),
                ("actor_access_epoch", models.PositiveBigIntegerField(blank=True, null=True)),
                ("actor_capacity", models.CharField(max_length=24)),
                ("entry_mode", models.CharField(max_length=24)),
                ("composing_rule_state", models.CharField(max_length=24)),
                ("composing_rule_reference", models.CharField(blank=True, max_length=255, null=True)),
                ("composer_state", models.CharField(max_length=24)),
                ("occurred_at", models.DateTimeField()),
                ("effective_at", models.DateTimeField()),
                ("received_at", models.DateTimeField()),
                ("recorded_at", models.DateTimeField(editable=False, null=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="core.identity")),
                ("circle", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="+", to="core.circle")),
                ("composer_identity", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="core.identity")),
                ("decision", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="+", to="core.circleadmissiondecision")),
                ("enterer_identity", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="+", to="core.identity")),
                ("membership", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="+", to="core.organismmembership")),
            ],
            options={
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(
                            actor_state="LIVING_ORGANISM",
                            actor__isnull=True,
                            actor_access_epoch__isnull=True,
                            actor_capacity="STANDING",
                            entry_mode="TRANSPORT",
                            composing_rule_state="NA_TRANSPORT",
                            composing_rule_reference__isnull=True,
                            composer_state="NA_TRANSPORT",
                            composer_identity__isnull=True,
                        ),
                        name="sbg_circleparticipation_transport_ck",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="CircleParticipationEnd",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("actor_state", models.CharField(max_length=24)),
                ("actor_access_epoch", models.PositiveBigIntegerField(blank=True, null=True)),
                ("actor_capacity", models.CharField(max_length=24)),
                ("entry_mode", models.CharField(max_length=24)),
                ("composing_rule_state", models.CharField(max_length=24)),
                ("composing_rule_reference", models.CharField(blank=True, max_length=255, null=True)),
                ("composed_from_event_uuid", models.UUIDField(blank=True, null=True)),
                ("composer_state", models.CharField(max_length=24)),
                ("occurred_at", models.DateTimeField()),
                ("effective_at", models.DateTimeField()),
                ("received_at", models.DateTimeField()),
                ("recorded_at", models.DateTimeField(editable=False, null=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="core.identity")),
                ("composer_identity", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="core.identity")),
                ("composing_rule", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="core.governedrule")),
                ("enterer_identity", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="+", to="core.identity")),
                ("participation", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="+", to="core.circleparticipation")),
            ],
            options={
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(
                            actor_state="NONE",
                            actor__isnull=True,
                            actor_access_epoch__isnull=True,
                            actor_capacity="NA_NO_PARTY",
                            entry_mode="COMPOSED",
                            composing_rule_state="REFERENCE",
                            composing_rule__isnull=False,
                            composing_rule_reference__isnull=False,
                            composer_state="REFERENCE",
                            composer_identity__isnull=False,
                            composed_from_event_uuid__isnull=False,
                        ),
                        name="sbg_circleparticipationend_composed_ck",
                    ),
                ],
            },
        ),
        migrations.RunPython(install, uninstall),
    ]
