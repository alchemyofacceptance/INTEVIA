"""The INTEVIA Clock, storage 1 of 7: the installed functions of migrations 0021 to 0049 (CK-2a1).

16 installed functions read the time through PostgreSQL's own calls. This migration replaces
each under G2-4 with a successor that differs from it only where a time call becomes the
INTEVIA Clock's face with the same meaning (17 calls: 8 statement start, 9 instant,
0 transaction start):

    statement_timestamp()    public.intevia_clock_statement()
    clock_timestamp()        public.intevia_clock_instant()
    transaction_timestamp()  public.intevia_clock_transaction()

In production each face is exactly its built-in (migration 0096), so nothing changes there.
In an owned test database the faces are the ones a test may move.

Each function is held here twice, as landed and as it reads the clock. Forward: the installed
text must equal the landed text; it is replaced; the function's every other attribute must be
unchanged. Reverse: the same, the other way. ``core.migrations._clock_conversion`` has the step.
The two texts of a function are compared with the database character for character, so
neither is ever edited.
"""

from importlib import import_module

from django.db import migrations

clock = import_module("core.migrations._clock_conversion")

# public.s015_assign_event_record_time()
# Its landed text: 0021_s015_recorded_chain_and_bitemporal_fold.py (found there verbatim). Time calls moved: 1 statement.
S015_ASSIGN_EVENT_RECORD_TIME_LANDED = r"""
DECLARE m record; v_anchor bigint; v_exists boolean;
BEGIN
    IF NEW.recorded_at IS NOT NULL THEN
        RAISE EXCEPTION 'S015 recorded_at is database-assigned' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    NEW.recorded_at := statement_timestamp();
    SELECT * INTO m FROM public.s015_anchor_map() WHERE event_table = TG_TABLE_NAME;
    IF NEW.sequence > 1 THEN
        v_anchor := (to_jsonb(NEW) ->> m.fk_col)::bigint;
        EXECUTE format('SELECT EXISTS (SELECT 1 FROM public.%I WHERE %I = $1 AND sequence = $2)', TG_TABLE_NAME, m.fk_col)
            INTO v_exists USING v_anchor, NEW.sequence - 1;
        IF NOT v_exists THEN
            RAISE EXCEPTION 'S015 append order: sequence % inserted before sequence % on % anchor %',
                NEW.sequence, NEW.sequence - 1, m.anchor_token, v_anchor USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    RETURN NEW;
END """
S015_ASSIGN_EVENT_RECORD_TIME_CLOCK = r"""
DECLARE m record; v_anchor bigint; v_exists boolean;
BEGIN
    IF NEW.recorded_at IS NOT NULL THEN
        RAISE EXCEPTION 'S015 recorded_at is database-assigned' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    NEW.recorded_at := public.intevia_clock_statement();
    SELECT * INTO m FROM public.s015_anchor_map() WHERE event_table = TG_TABLE_NAME;
    IF NEW.sequence > 1 THEN
        v_anchor := (to_jsonb(NEW) ->> m.fk_col)::bigint;
        EXECUTE format('SELECT EXISTS (SELECT 1 FROM public.%I WHERE %I = $1 AND sequence = $2)', TG_TABLE_NAME, m.fk_col)
            INTO v_exists USING v_anchor, NEW.sequence - 1;
        IF NOT v_exists THEN
            RAISE EXCEPTION 'S015 append order: sequence % inserted before sequence % on % anchor %',
                NEW.sequence, NEW.sequence - 1, m.anchor_token, v_anchor USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    RETURN NEW;
END """

# public.s015_requalify_governed_cache(text, bigint)
# Its landed text: 0021_s015_recorded_chain_and_bitemporal_fold.py (found there verbatim). Time calls moved: 2 wall.
S015_REQUALIFY_GOVERNED_CACHE_LANDED = r"""
DECLARE v_row jsonb; v_state_at timestamptz; v_known_at timestamptz; v_fp text; v_fold_fp text;
        v_state text; v_fold_state text; ob public.s015_obligation_projection_t;
BEGIN
    IF p_token = 'core_governedobligationcase' THEN
        SELECT to_jsonb(c) INTO v_row FROM public.core_governedobligationcase c WHERE c.id = p_anchor_id;
        v_state_at := (v_row->>'status_state_at')::timestamptz; v_known_at := (v_row->>'status_known_at')::timestamptz;
        v_fp := v_row->>'status_source_event_set_fingerprint';
        IF v_known_at > clock_timestamp() THEN RETURN FALSE; END IF;
        v_fold_fp := public.s015_fingerprint_event_set(p_token, p_anchor_id, v_state_at, v_known_at);
        ob := public.s015_fold_obligation_projection(p_anchor_id, v_state_at, v_known_at);
        RETURN NOT (v_fp IS DISTINCT FROM v_fold_fp)
           AND NOT ((v_row->>'status_state') IS DISTINCT FROM ob.resulting_state)
           AND NOT ((v_row->>'legal_deadline_status') IS DISTINCT FROM ob.legal_deadline_status)
           AND NOT ((v_row->>'qualified_legal_deadline')::timestamptz IS DISTINCT FROM ob.qualified_legal_deadline)
           AND NOT ((v_row->>'effective_deadline')::timestamptz IS DISTINCT FROM ob.effective_deadline)
           AND NOT ((v_row->>'legal_basis_determination_id')::bigint IS DISTINCT FROM ob.legal_basis_determination_id);
    ELSE
        EXECUTE format('SELECT to_jsonb(a) FROM public.%I a WHERE a.id = $1', p_token) INTO v_row USING p_anchor_id;
        v_state_at := (v_row->>'current_state_effective_at')::timestamptz; v_known_at := (v_row->>'state_known_at')::timestamptz;
        v_fp := v_row->>'state_source_event_set_fingerprint'; v_state := v_row->>'state';
        IF v_known_at > clock_timestamp() THEN RETURN FALSE; END IF;
        v_fold_fp := public.s015_fingerprint_event_set(p_token, p_anchor_id, v_state_at, v_known_at);
        v_fold_state := public.s015_fold_effective_state(p_token, p_anchor_id, v_state_at, v_known_at);
        RETURN NOT (v_fp IS DISTINCT FROM v_fold_fp) AND NOT (v_state IS DISTINCT FROM v_fold_state);
    END IF;
END """
S015_REQUALIFY_GOVERNED_CACHE_CLOCK = r"""
DECLARE v_row jsonb; v_state_at timestamptz; v_known_at timestamptz; v_fp text; v_fold_fp text;
        v_state text; v_fold_state text; ob public.s015_obligation_projection_t;
BEGIN
    IF p_token = 'core_governedobligationcase' THEN
        SELECT to_jsonb(c) INTO v_row FROM public.core_governedobligationcase c WHERE c.id = p_anchor_id;
        v_state_at := (v_row->>'status_state_at')::timestamptz; v_known_at := (v_row->>'status_known_at')::timestamptz;
        v_fp := v_row->>'status_source_event_set_fingerprint';
        IF v_known_at > public.intevia_clock_instant() THEN RETURN FALSE; END IF;
        v_fold_fp := public.s015_fingerprint_event_set(p_token, p_anchor_id, v_state_at, v_known_at);
        ob := public.s015_fold_obligation_projection(p_anchor_id, v_state_at, v_known_at);
        RETURN NOT (v_fp IS DISTINCT FROM v_fold_fp)
           AND NOT ((v_row->>'status_state') IS DISTINCT FROM ob.resulting_state)
           AND NOT ((v_row->>'legal_deadline_status') IS DISTINCT FROM ob.legal_deadline_status)
           AND NOT ((v_row->>'qualified_legal_deadline')::timestamptz IS DISTINCT FROM ob.qualified_legal_deadline)
           AND NOT ((v_row->>'effective_deadline')::timestamptz IS DISTINCT FROM ob.effective_deadline)
           AND NOT ((v_row->>'legal_basis_determination_id')::bigint IS DISTINCT FROM ob.legal_basis_determination_id);
    ELSE
        EXECUTE format('SELECT to_jsonb(a) FROM public.%I a WHERE a.id = $1', p_token) INTO v_row USING p_anchor_id;
        v_state_at := (v_row->>'current_state_effective_at')::timestamptz; v_known_at := (v_row->>'state_known_at')::timestamptz;
        v_fp := v_row->>'state_source_event_set_fingerprint'; v_state := v_row->>'state';
        IF v_known_at > public.intevia_clock_instant() THEN RETURN FALSE; END IF;
        v_fold_fp := public.s015_fingerprint_event_set(p_token, p_anchor_id, v_state_at, v_known_at);
        v_fold_state := public.s015_fold_effective_state(p_token, p_anchor_id, v_state_at, v_known_at);
        RETURN NOT (v_fp IS DISTINCT FROM v_fold_fp) AND NOT (v_state IS DISTINCT FROM v_fold_state);
    END IF;
END """

# public.s015_validate_founding_population()
# Its landed text: 0021_s015_recorded_chain_and_bitemporal_fold.py (found there verbatim). Time calls moved: 1 wall.
S015_VALIDATE_FOUNDING_POPULATION_LANDED = r"""
DECLARE lo record; v_seq1 bigint; v_membership bigint; v_first_eff timestamptz; v_case bigint; v_assess bigint; v_roster integer;
        v_founding_known_at timestamptz; v_codes text[]; v_code text; v_active_count integer; v_active_version integer;
        v_max_version integer; v_n integer; v_n_held integer;
BEGIN
    SELECT * INTO lo FROM public.core_livingorganism WHERE id = NEW.id;
    v_founding_known_at := clock_timestamp();
    -- limb 1
    SELECT count(*), min(effective_at) INTO v_seq1, v_first_eff FROM public.core_livingorganismevent WHERE living_organism_id = lo.id AND sequence = 1;
    IF v_seq1 <> 1 THEN RAISE EXCEPTION 'S015 founding limb 1: exactly one sequence-1 organism event required (found %)', v_seq1 USING ERRCODE = 'integrity_constraint_violation'; END IF;
    -- limb 2
    IF lo.founder_identity_id IS NULL OR lo.founding_authority_basis_id IS NULL THEN
        RAISE EXCEPTION 'S015 founding limb 2: founder_identity and founding_authority_basis must both be set' USING ERRCODE = 'integrity_constraint_violation'; END IF;
    -- limb 3
    SELECT m.id INTO v_membership FROM public.core_organismmembership m WHERE m.living_organism_id = lo.id AND m.identity_id = lo.founder_identity_id;
    IF v_membership IS NULL OR NOT EXISTS (SELECT 1 FROM public.core_organismmembershiptransition WHERE membership_id = v_membership) THEN
        RAISE EXCEPTION 'S015 founding limb 3: founder membership with a non-empty chain required' USING ERRCODE = 'integrity_constraint_violation'; END IF;
    -- limb 4 (root only)
    IF lo.platform_root THEN
        IF NOT EXISTS (
            SELECT 1 FROM public.core_contextualroleassignment a JOIN public.core_organismroledefinition r ON r.id = a.role_definition_id
                         WHERE a.membership_id = v_membership AND r.living_organism_id = lo.id AND r.code = 'INTEVIA_FOUNDER_STEWARD' AND r.active = TRUE
                             AND EXISTS (
                                     SELECT 1 FROM public.s015_effective_state(
                                             'core_contextualroleassignment', a.id, v_first_eff, v_founding_known_at
                                     ) state_at_founding WHERE state_at_founding.projection = 'ACTIVE'
                             )) THEN
                        RAISE EXCEPTION 'S015 founding limb 4: root requires a FOUNDER_STEWARD assignment ACTIVE at the founding coordinates' USING ERRCODE = 'integrity_constraint_violation'; END IF;
    END IF;
    -- limb 5
    IF NOT EXISTS (SELECT 1 FROM public.core_essentialcoveragerequirement q WHERE q.living_organism_id = lo.id
                     AND q.effective_from <= v_first_eff AND (q.effective_until IS NULL OR q.effective_until > v_first_eff)) THEN
        RAISE EXCEPTION 'S015 founding limb 5: at least one requirement row effective at the first event is required' USING ERRCODE = 'integrity_constraint_violation'; END IF;
    -- limb 6
    SELECT c.id INTO v_case FROM public.core_essentialcoveragecase c WHERE c.living_organism_id = lo.id;
    SELECT count(*) FILTER (WHERE sequence = 1), min(roster_version) FILTER (WHERE sequence = 1) INTO v_n, v_roster FROM public.core_coverageassessment WHERE case_id = v_case;
    IF v_case IS NULL OR v_n <> 1 OR NOT EXISTS (SELECT 1 FROM public.core_coverageassessment WHERE case_id = v_case AND sequence = 1)
       OR NOT EXISTS (SELECT 1 FROM public.core_essentialcoveragerequirement q WHERE q.living_organism_id = lo.id AND q.roster_version = v_roster) THEN
        RAISE EXCEPTION 'S015 founding limb 6: one coverage case with exactly one sequence-1 assessment declaring an existing roster version is required' USING ERRCODE = 'integrity_constraint_violation'; END IF;
    -- limb 7 (root only)
    IF lo.platform_root THEN
        SELECT count(*), count(*) FILTER (WHERE a.membership_id = v_membership) INTO v_n, v_n_held
                 FROM public.core_contextualroleassignment a JOIN public.core_organismroledefinition r ON r.id = a.role_definition_id
                 WHERE r.living_organism_id = lo.id AND r.code = 'INTEVIA_PRIVACY_COORDINATOR' AND r.active = TRUE
                     AND EXISTS (
                             SELECT 1 FROM public.s015_effective_state(
                                     'core_contextualroleassignment', a.id, v_first_eff, v_founding_known_at
                             ) state_at_founding WHERE state_at_founding.projection = 'ACTIVE'
                     );
                IF v_n <> 1 THEN RAISE EXCEPTION 'S015 founding limb 7: root requires exactly one INTEVIA_PRIVACY_COORDINATOR assignment ACTIVE at the founding coordinates (found %)', v_n USING ERRCODE = 'integrity_constraint_violation'; END IF;
                IF v_n_held <> 1 THEN RAISE EXCEPTION 'S015 founding limb 7: the INTEVIA_PRIVACY_COORDINATOR assignment must be held by the founder membership' USING ERRCODE = 'integrity_constraint_violation'; END IF;
    END IF;
    -- limb 8 (both branches; D-45 exact-one active at greatest version)
    v_codes := CASE WHEN lo.platform_root THEN ARRAY['INTEVIA_FOUNDER_STEWARD','INTEVIA_STEWARD','INTEVIA_GUIDING_STEWARD','INTEVIA_EMERGENCY_STEWARD','INTEVIA_PRIVACY_COORDINATOR','COORDINATOR'] ELSE ARRAY['COORDINATOR'] END;
    FOREACH v_code IN ARRAY v_codes LOOP
        SELECT count(*) FILTER (WHERE active), max(definition_version) FILTER (WHERE active), max(definition_version)
          INTO v_active_count, v_active_version, v_max_version
          FROM public.core_organismroledefinition WHERE living_organism_id = lo.id AND code = v_code;
        IF v_active_count IS DISTINCT FROM 1 THEN
            RAISE EXCEPTION 'S015 founding limb 8: exactly one active % definition required (found %)', v_code, coalesce(v_active_count, 0) USING ERRCODE = 'integrity_constraint_violation'; END IF;
        IF v_active_version <> v_max_version THEN
            RAISE EXCEPTION 'S015 founding limb 8: active % definition (version %) is not the current version (%)', v_code, v_active_version, v_max_version USING ERRCODE = 'integrity_constraint_violation'; END IF;
    END LOOP;
    RETURN NULL;
END """
S015_VALIDATE_FOUNDING_POPULATION_CLOCK = r"""
DECLARE lo record; v_seq1 bigint; v_membership bigint; v_first_eff timestamptz; v_case bigint; v_assess bigint; v_roster integer;
        v_founding_known_at timestamptz; v_codes text[]; v_code text; v_active_count integer; v_active_version integer;
        v_max_version integer; v_n integer; v_n_held integer;
BEGIN
    SELECT * INTO lo FROM public.core_livingorganism WHERE id = NEW.id;
    v_founding_known_at := public.intevia_clock_instant();
    -- limb 1
    SELECT count(*), min(effective_at) INTO v_seq1, v_first_eff FROM public.core_livingorganismevent WHERE living_organism_id = lo.id AND sequence = 1;
    IF v_seq1 <> 1 THEN RAISE EXCEPTION 'S015 founding limb 1: exactly one sequence-1 organism event required (found %)', v_seq1 USING ERRCODE = 'integrity_constraint_violation'; END IF;
    -- limb 2
    IF lo.founder_identity_id IS NULL OR lo.founding_authority_basis_id IS NULL THEN
        RAISE EXCEPTION 'S015 founding limb 2: founder_identity and founding_authority_basis must both be set' USING ERRCODE = 'integrity_constraint_violation'; END IF;
    -- limb 3
    SELECT m.id INTO v_membership FROM public.core_organismmembership m WHERE m.living_organism_id = lo.id AND m.identity_id = lo.founder_identity_id;
    IF v_membership IS NULL OR NOT EXISTS (SELECT 1 FROM public.core_organismmembershiptransition WHERE membership_id = v_membership) THEN
        RAISE EXCEPTION 'S015 founding limb 3: founder membership with a non-empty chain required' USING ERRCODE = 'integrity_constraint_violation'; END IF;
    -- limb 4 (root only)
    IF lo.platform_root THEN
        IF NOT EXISTS (
            SELECT 1 FROM public.core_contextualroleassignment a JOIN public.core_organismroledefinition r ON r.id = a.role_definition_id
                         WHERE a.membership_id = v_membership AND r.living_organism_id = lo.id AND r.code = 'INTEVIA_FOUNDER_STEWARD' AND r.active = TRUE
                             AND EXISTS (
                                     SELECT 1 FROM public.s015_effective_state(
                                             'core_contextualroleassignment', a.id, v_first_eff, v_founding_known_at
                                     ) state_at_founding WHERE state_at_founding.projection = 'ACTIVE'
                             )) THEN
                        RAISE EXCEPTION 'S015 founding limb 4: root requires a FOUNDER_STEWARD assignment ACTIVE at the founding coordinates' USING ERRCODE = 'integrity_constraint_violation'; END IF;
    END IF;
    -- limb 5
    IF NOT EXISTS (SELECT 1 FROM public.core_essentialcoveragerequirement q WHERE q.living_organism_id = lo.id
                     AND q.effective_from <= v_first_eff AND (q.effective_until IS NULL OR q.effective_until > v_first_eff)) THEN
        RAISE EXCEPTION 'S015 founding limb 5: at least one requirement row effective at the first event is required' USING ERRCODE = 'integrity_constraint_violation'; END IF;
    -- limb 6
    SELECT c.id INTO v_case FROM public.core_essentialcoveragecase c WHERE c.living_organism_id = lo.id;
    SELECT count(*) FILTER (WHERE sequence = 1), min(roster_version) FILTER (WHERE sequence = 1) INTO v_n, v_roster FROM public.core_coverageassessment WHERE case_id = v_case;
    IF v_case IS NULL OR v_n <> 1 OR NOT EXISTS (SELECT 1 FROM public.core_coverageassessment WHERE case_id = v_case AND sequence = 1)
       OR NOT EXISTS (SELECT 1 FROM public.core_essentialcoveragerequirement q WHERE q.living_organism_id = lo.id AND q.roster_version = v_roster) THEN
        RAISE EXCEPTION 'S015 founding limb 6: one coverage case with exactly one sequence-1 assessment declaring an existing roster version is required' USING ERRCODE = 'integrity_constraint_violation'; END IF;
    -- limb 7 (root only)
    IF lo.platform_root THEN
        SELECT count(*), count(*) FILTER (WHERE a.membership_id = v_membership) INTO v_n, v_n_held
                 FROM public.core_contextualroleassignment a JOIN public.core_organismroledefinition r ON r.id = a.role_definition_id
                 WHERE r.living_organism_id = lo.id AND r.code = 'INTEVIA_PRIVACY_COORDINATOR' AND r.active = TRUE
                     AND EXISTS (
                             SELECT 1 FROM public.s015_effective_state(
                                     'core_contextualroleassignment', a.id, v_first_eff, v_founding_known_at
                             ) state_at_founding WHERE state_at_founding.projection = 'ACTIVE'
                     );
                IF v_n <> 1 THEN RAISE EXCEPTION 'S015 founding limb 7: root requires exactly one INTEVIA_PRIVACY_COORDINATOR assignment ACTIVE at the founding coordinates (found %)', v_n USING ERRCODE = 'integrity_constraint_violation'; END IF;
                IF v_n_held <> 1 THEN RAISE EXCEPTION 'S015 founding limb 7: the INTEVIA_PRIVACY_COORDINATOR assignment must be held by the founder membership' USING ERRCODE = 'integrity_constraint_violation'; END IF;
    END IF;
    -- limb 8 (both branches; D-45 exact-one active at greatest version)
    v_codes := CASE WHEN lo.platform_root THEN ARRAY['INTEVIA_FOUNDER_STEWARD','INTEVIA_STEWARD','INTEVIA_GUIDING_STEWARD','INTEVIA_EMERGENCY_STEWARD','INTEVIA_PRIVACY_COORDINATOR','COORDINATOR'] ELSE ARRAY['COORDINATOR'] END;
    FOREACH v_code IN ARRAY v_codes LOOP
        SELECT count(*) FILTER (WHERE active), max(definition_version) FILTER (WHERE active), max(definition_version)
          INTO v_active_count, v_active_version, v_max_version
          FROM public.core_organismroledefinition WHERE living_organism_id = lo.id AND code = v_code;
        IF v_active_count IS DISTINCT FROM 1 THEN
            RAISE EXCEPTION 'S015 founding limb 8: exactly one active % definition required (found %)', v_code, coalesce(v_active_count, 0) USING ERRCODE = 'integrity_constraint_violation'; END IF;
        IF v_active_version <> v_max_version THEN
            RAISE EXCEPTION 'S015 founding limb 8: active % definition (version %) is not the current version (%)', v_code, v_active_version, v_max_version USING ERRCODE = 'integrity_constraint_violation'; END IF;
    END LOOP;
    RETURN NULL;
END """

# public.s015_0022_assign_part_record_time()
# Its landed text: 0022_s015_governed_chain_contract.py (assembled by that migration). Time calls moved: 1 statement.
S015_0022_ASSIGN_PART_RECORD_TIME_LANDED = r"""

BEGIN
    IF NEW.recorded_at IS NOT NULL THEN
        RAISE EXCEPTION 'S015 recorded_at is database-assigned'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    NEW.recorded_at := statement_timestamp();
    RETURN NEW;
END;
"""
S015_0022_ASSIGN_PART_RECORD_TIME_CLOCK = r"""

BEGIN
    IF NEW.recorded_at IS NOT NULL THEN
        RAISE EXCEPTION 'S015 recorded_at is database-assigned'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    NEW.recorded_at := public.intevia_clock_statement();
    RETURN NEW;
END;
"""

# public.s3_assign_record_time()
# Its landed text: 0025_s015_application_routes.py (found there verbatim). Time calls moved: 1 statement.
S3_ASSIGN_RECORD_TIME_LANDED = r"""
BEGIN
    IF NEW.recorded_at IS NOT NULL THEN
        RAISE EXCEPTION 'S3 recorded_at is database-assigned' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    NEW.recorded_at := statement_timestamp();
    RETURN NEW;
END """
S3_ASSIGN_RECORD_TIME_CLOCK = r"""
BEGIN
    IF NEW.recorded_at IS NOT NULL THEN
        RAISE EXCEPTION 'S3 recorded_at is database-assigned' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    NEW.recorded_at := public.intevia_clock_statement();
    RETURN NEW;
END """

# public.s3_terminal_lock()
# Its landed text: 0026_s015_application_admission_time.py (assembled by that migration). Time calls moved: 1 wall.
S3_TERMINAL_LOCK_LANDED = r"""
BEGIN
    PERFORM public.s3_lock_application(NEW.application_id);
    NEW.recorded_at := clock_timestamp();
    IF TG_TABLE_NAME = 'core_applicationdecision' AND EXISTS (
        SELECT 1 FROM public.core_applicationwithdrawal WHERE application_id = NEW.application_id
    ) OR TG_TABLE_NAME = 'core_applicationwithdrawal' AND EXISTS (
        SELECT 1 FROM public.core_applicationdecision WHERE application_id = NEW.application_id
    ) THEN
        RAISE EXCEPTION 'S3 terminal exclusion' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END """
S3_TERMINAL_LOCK_CLOCK = r"""
BEGIN
    PERFORM public.s3_lock_application(NEW.application_id);
    NEW.recorded_at := public.intevia_clock_instant();
    IF TG_TABLE_NAME = 'core_applicationdecision' AND EXISTS (
        SELECT 1 FROM public.core_applicationwithdrawal WHERE application_id = NEW.application_id
    ) OR TG_TABLE_NAME = 'core_applicationwithdrawal' AND EXISTS (
        SELECT 1 FROM public.core_applicationdecision WHERE application_id = NEW.application_id
    ) THEN
        RAISE EXCEPTION 'S3 terminal exclusion' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END """

# public.s3fu_guard_auto_decision()
# Its landed text: 0030_s015_automatic_application.py (found there verbatim). Time calls moved: 1 wall.
S3FU_GUARD_AUTO_DECISION_LANDED = r"""
DECLARE app public.core_application%ROWTYPE; engagement public.core_applicationengagement%ROWTYPE;
BEGIN
    PERFORM public.s3_lock_application(NEW.application_id);
    NEW.recorded_at := clock_timestamp();
    SELECT * INTO app FROM public.core_application WHERE id = NEW.application_id;
    SELECT * INTO engagement FROM public.core_applicationengagement WHERE application_id = app.id
        AND effective_at <= NEW.effective_at AND recorded_at <= NEW.recorded_at
        ORDER BY engagement_ordinal DESC LIMIT 1;
    IF NEW.outcome IS DISTINCT FROM 'ACCEPT' OR NEW.deciding_party_id IS DISTINCT FROM app.living_organism_id
       OR NEW.assignment_reference_id IS NOT NULL OR NEW.supporting_result_reference IS NOT NULL
       OR NEW.responsible_coordinator_id IS NOT NULL OR NEW.responsible_capacity IS NOT NULL
       OR NEW.qualifying_office_id IS NOT NULL OR NEW.enterer_id IS DISTINCT FROM NEW.executor_id
       OR engagement.route IS DISTINCT FROM 'LO_AUTOMATIC'
       OR engagement.settings_basis IS DISTINCT FROM 'application-settings-version:' || (
           SELECT version_uuid::text FROM public.core_loapplicationsettingsversion WHERE id = app.settings_version_id)
       OR NOT EXISTS (SELECT 1 FROM public.core_loapplicationsettingsversion v
                      WHERE v.id = app.settings_version_id AND v.living_organism_id = app.living_organism_id
                        AND v.applications_open AND v.automatic_acceptance) THEN
        RAISE EXCEPTION 'S3FU automatic decision refused: recorded open setting and LO ACCEPT required'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END """
S3FU_GUARD_AUTO_DECISION_CLOCK = r"""
DECLARE app public.core_application%ROWTYPE; engagement public.core_applicationengagement%ROWTYPE;
BEGIN
    PERFORM public.s3_lock_application(NEW.application_id);
    NEW.recorded_at := public.intevia_clock_instant();
    SELECT * INTO app FROM public.core_application WHERE id = NEW.application_id;
    SELECT * INTO engagement FROM public.core_applicationengagement WHERE application_id = app.id
        AND effective_at <= NEW.effective_at AND recorded_at <= NEW.recorded_at
        ORDER BY engagement_ordinal DESC LIMIT 1;
    IF NEW.outcome IS DISTINCT FROM 'ACCEPT' OR NEW.deciding_party_id IS DISTINCT FROM app.living_organism_id
       OR NEW.assignment_reference_id IS NOT NULL OR NEW.supporting_result_reference IS NOT NULL
       OR NEW.responsible_coordinator_id IS NOT NULL OR NEW.responsible_capacity IS NOT NULL
       OR NEW.qualifying_office_id IS NOT NULL OR NEW.enterer_id IS DISTINCT FROM NEW.executor_id
       OR engagement.route IS DISTINCT FROM 'LO_AUTOMATIC'
       OR engagement.settings_basis IS DISTINCT FROM 'application-settings-version:' || (
           SELECT version_uuid::text FROM public.core_loapplicationsettingsversion WHERE id = app.settings_version_id)
       OR NOT EXISTS (SELECT 1 FROM public.core_loapplicationsettingsversion v
                      WHERE v.id = app.settings_version_id AND v.living_organism_id = app.living_organism_id
                        AND v.applications_open AND v.automatic_acceptance) THEN
        RAISE EXCEPTION 'S3FU automatic decision refused: recorded open setting and LO ACCEPT required'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END """

# public.s3fu_guard_auto_engagement()
# Its landed text: 0030_s015_automatic_application.py (found there verbatim). Time calls moved: 1 wall.
S3FU_GUARD_AUTO_ENGAGEMENT_LANDED = r"""
DECLARE app public.core_application%ROWTYPE;
BEGIN
    PERFORM public.s3_lock_application(NEW.application_id);
    NEW.recorded_at := clock_timestamp();
    SELECT * INTO app FROM public.core_application WHERE id = NEW.application_id;
    IF NEW.engagement_ordinal <> 1 OR NEW.actor_id IS NOT NULL OR NEW.enterer_id IS NULL
       OR NEW.deciding_party_id IS DISTINCT FROM app.living_organism_id
       OR NEW.settings_basis IS DISTINCT FROM 'application-settings-version:' || (
           SELECT version_uuid::text FROM public.core_loapplicationsettingsversion WHERE id = app.settings_version_id)
       OR NEW.engagement_reference IS DISTINCT FROM NEW.settings_basis OR NEW.basis_reference IS NOT NULL
       OR EXISTS (SELECT 1 FROM public.core_applicationengagement WHERE application_id = app.id)
       OR EXISTS (SELECT 1 FROM public.core_applicationwithdrawal WHERE application_id = app.id)
       OR EXISTS (SELECT 1 FROM public.core_applicationdecision WHERE application_id = app.id)
       OR NOT EXISTS (SELECT 1 FROM public.core_loapplicationsettingsversion v
                      WHERE v.id = app.settings_version_id AND v.living_organism_id = app.living_organism_id
                        AND v.applications_open AND v.automatic_acceptance) THEN
        RAISE EXCEPTION 'S3FU automatic engagement refused: recorded open automatic setting required'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END """
S3FU_GUARD_AUTO_ENGAGEMENT_CLOCK = r"""
DECLARE app public.core_application%ROWTYPE;
BEGIN
    PERFORM public.s3_lock_application(NEW.application_id);
    NEW.recorded_at := public.intevia_clock_instant();
    SELECT * INTO app FROM public.core_application WHERE id = NEW.application_id;
    IF NEW.engagement_ordinal <> 1 OR NEW.actor_id IS NOT NULL OR NEW.enterer_id IS NULL
       OR NEW.deciding_party_id IS DISTINCT FROM app.living_organism_id
       OR NEW.settings_basis IS DISTINCT FROM 'application-settings-version:' || (
           SELECT version_uuid::text FROM public.core_loapplicationsettingsversion WHERE id = app.settings_version_id)
       OR NEW.engagement_reference IS DISTINCT FROM NEW.settings_basis OR NEW.basis_reference IS NOT NULL
       OR EXISTS (SELECT 1 FROM public.core_applicationengagement WHERE application_id = app.id)
       OR EXISTS (SELECT 1 FROM public.core_applicationwithdrawal WHERE application_id = app.id)
       OR EXISTS (SELECT 1 FROM public.core_applicationdecision WHERE application_id = app.id)
       OR NOT EXISTS (SELECT 1 FROM public.core_loapplicationsettingsversion v
                      WHERE v.id = app.settings_version_id AND v.living_organism_id = app.living_organism_id
                        AND v.applications_open AND v.automatic_acceptance) THEN
        RAISE EXCEPTION 'S3FU automatic engagement refused: recorded open automatic setting required'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END """

# public.s3_engagement_guard()
# Its landed text: 0033_s015_application_authority.py (assembled by that migration). Time calls moved: 1 wall.
S3_ENGAGEMENT_GUARD_LANDED = r"""
DECLARE previous public.core_applicationengagement%ROWTYPE;
BEGIN
    PERFORM public.s3_lock_application(NEW.application_id);
    NEW.recorded_at := clock_timestamp();
    IF NEW.engagement_ordinal < 1 OR NEW.route NOT IN ('LO_CQ','LO_CSA','CIRCLE_CQ','CIRCLE_CSA')
       OR EXISTS (SELECT 1 FROM public.core_applicationwithdrawal WHERE application_id = NEW.application_id)
       OR EXISTS (SELECT 1 FROM public.core_applicationdecision WHERE application_id = NEW.application_id AND effective_at <= NEW.effective_at AND recorded_at <= NEW.recorded_at) THEN
        RAISE EXCEPTION 'S3 engagement refused' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.engagement_ordinal = 1 THEN
        IF EXISTS (SELECT 1 FROM public.core_applicationengagement WHERE application_id = NEW.application_id) THEN
            RAISE EXCEPTION 'S3 first engagement must be first' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSE
        SELECT * INTO previous FROM public.core_applicationengagement
        WHERE application_id = NEW.application_id AND engagement_ordinal = NEW.engagement_ordinal - 1;
        IF NOT FOUND OR NEW.basis_reference IS NULL OR NOT EXISTS (
            SELECT 1 FROM public.core_csaassignment claim JOIN public.core_csaassignmentend ending ON ending.assignment_id = claim.id
            WHERE previous.route IN ('LO_CSA','CIRCLE_CSA') AND previous.engagement_reference = claim.assignment_uuid::text
              AND claim.application_id = NEW.application_id AND ending.ending_uuid::text = NEW.basis_reference
              AND ending.effective_at <= NEW.effective_at AND ending.recorded_at <= NEW.recorded_at
        ) THEN
            RAISE EXCEPTION 'S3 re-engagement requires prior UNABLE owner record' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    RETURN NEW;
END """
S3_ENGAGEMENT_GUARD_CLOCK = r"""
DECLARE previous public.core_applicationengagement%ROWTYPE;
BEGIN
    PERFORM public.s3_lock_application(NEW.application_id);
    NEW.recorded_at := public.intevia_clock_instant();
    IF NEW.engagement_ordinal < 1 OR NEW.route NOT IN ('LO_CQ','LO_CSA','CIRCLE_CQ','CIRCLE_CSA')
       OR EXISTS (SELECT 1 FROM public.core_applicationwithdrawal WHERE application_id = NEW.application_id)
       OR EXISTS (SELECT 1 FROM public.core_applicationdecision WHERE application_id = NEW.application_id AND effective_at <= NEW.effective_at AND recorded_at <= NEW.recorded_at) THEN
        RAISE EXCEPTION 'S3 engagement refused' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.engagement_ordinal = 1 THEN
        IF EXISTS (SELECT 1 FROM public.core_applicationengagement WHERE application_id = NEW.application_id) THEN
            RAISE EXCEPTION 'S3 first engagement must be first' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSE
        SELECT * INTO previous FROM public.core_applicationengagement
        WHERE application_id = NEW.application_id AND engagement_ordinal = NEW.engagement_ordinal - 1;
        IF NOT FOUND OR NEW.basis_reference IS NULL OR NOT EXISTS (
            SELECT 1 FROM public.core_csaassignment claim JOIN public.core_csaassignmentend ending ON ending.assignment_id = claim.id
            WHERE previous.route IN ('LO_CSA','CIRCLE_CSA') AND previous.engagement_reference = claim.assignment_uuid::text
              AND claim.application_id = NEW.application_id AND ending.ending_uuid::text = NEW.basis_reference
              AND ending.effective_at <= NEW.effective_at AND ending.recorded_at <= NEW.recorded_at
        ) THEN
            RAISE EXCEPTION 'S3 re-engagement requires prior UNABLE owner record' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    RETURN NEW;
END """

# public.s015_authoritydecision_guard()
# Its landed text: 0034_s015_authority_decisions.py (found there verbatim). Time calls moved: 1 statement.
S015_AUTHORITYDECISION_GUARD_LANDED = r"""
            DECLARE expected_reference text;
            DECLARE expected_target_fingerprint text;
            BEGIN
                IF NEW.recorded_at IS NOT NULL THEN
                    RAISE EXCEPTION 'S015 authority decision recorded_at is database-assigned'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                IF NEW.decision_payload IS DISTINCT FROM
                    convert_from(NEW.decision_payload_bytes, 'UTF8')::jsonb THEN
                    RAISE EXCEPTION 'S015 authority decision payload bytes do not match JSON'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                IF NEW.target IS DISTINCT FROM convert_from(NEW.target_bytes, 'UTF8')::jsonb THEN
                    RAISE EXCEPTION 'S015 authority decision target bytes do not match JSON'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                expected_target_fingerprint := encode(
                    sha256(
                        convert_to('INTEVIA:S015:ORGANISM_AUTHORITY_TARGET:v1', 'UTF8')
                        || decode('00', 'hex')
                        || NEW.target_bytes
                    ),
                    'hex'
                );
                IF NEW.target_fingerprint <> expected_target_fingerprint THEN
                    RAISE EXCEPTION 'S015 authority decision target fingerprint does not recompute'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                expected_reference := 's015d1:' || encode(
                    sha256(
                        convert_to('INTEVIA:S015:ORGANISM_AUTHORITY_DECISION:v1', 'UTF8')
                        || decode('00', 'hex')
                        || NEW.decision_payload_bytes
                    ),
                    'hex'
                );
                IF NEW.decision_reference <> expected_reference THEN
                    RAISE EXCEPTION 'S015 authority decision digest does not recompute'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                NEW.recorded_at := statement_timestamp();
                RETURN NEW;
            END;
            """
S015_AUTHORITYDECISION_GUARD_CLOCK = r"""
            DECLARE expected_reference text;
            DECLARE expected_target_fingerprint text;
            BEGIN
                IF NEW.recorded_at IS NOT NULL THEN
                    RAISE EXCEPTION 'S015 authority decision recorded_at is database-assigned'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                IF NEW.decision_payload IS DISTINCT FROM
                    convert_from(NEW.decision_payload_bytes, 'UTF8')::jsonb THEN
                    RAISE EXCEPTION 'S015 authority decision payload bytes do not match JSON'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                IF NEW.target IS DISTINCT FROM convert_from(NEW.target_bytes, 'UTF8')::jsonb THEN
                    RAISE EXCEPTION 'S015 authority decision target bytes do not match JSON'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                expected_target_fingerprint := encode(
                    sha256(
                        convert_to('INTEVIA:S015:ORGANISM_AUTHORITY_TARGET:v1', 'UTF8')
                        || decode('00', 'hex')
                        || NEW.target_bytes
                    ),
                    'hex'
                );
                IF NEW.target_fingerprint <> expected_target_fingerprint THEN
                    RAISE EXCEPTION 'S015 authority decision target fingerprint does not recompute'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                expected_reference := 's015d1:' || encode(
                    sha256(
                        convert_to('INTEVIA:S015:ORGANISM_AUTHORITY_DECISION:v1', 'UTF8')
                        || decode('00', 'hex')
                        || NEW.decision_payload_bytes
                    ),
                    'hex'
                );
                IF NEW.decision_reference <> expected_reference THEN
                    RAISE EXCEPTION 'S015 authority decision digest does not recompute'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                NEW.recorded_at := public.intevia_clock_statement();
                RETURN NEW;
            END;
            """

# public.s2b_guard_office_consent()
# Its landed text: 0037_s2b_office_appointment_consent.py (found there verbatim). Time calls moved: 1 statement.
S2B_GUARD_OFFICE_CONSENT_LANDED = r"""
DECLARE v_assignment_uuid uuid; v_assignment_membership_id bigint; v_membership_uuid uuid;
    v_nominee_identity_id bigint; proposal record; previous_action text;
    previous_ordinal integer; role_uuid uuid;
        decision_row record; reference_facts jsonb;
BEGIN
    IF NEW.recorded_at IS NOT NULL THEN
        RAISE EXCEPTION 'S2B office consent recorded_at is database-assigned'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    NEW.recorded_at := statement_timestamp();
    IF NEW.actor_state IS DISTINCT FROM 'PARTY'
       OR NEW.actor_capacity IS DISTINCT FROM 'STANDING'
       OR NEW.actor_id IS DISTINCT FROM NEW.enterer_id
       OR NEW.actor_access_epoch IS NULL
       OR NEW.occurred_at > NEW.received_at
       OR NEW.received_at > NEW.recorded_at THEN
        RAISE EXCEPTION 'S2B office consent attribution or time order refused'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
        SELECT a.assignment_uuid, a.membership_id, m.membership_uuid, m.identity_id, d.role_uuid
         INTO v_assignment_uuid, v_assignment_membership_id, v_membership_uuid,
             v_nominee_identity_id, role_uuid
      FROM public.core_contextualroleassignment a
      JOIN public.core_organismmembership m ON m.id = a.membership_id
         JOIN public.core_organismroledefinition d ON d.id = a.role_definition_id
     WHERE a.id = NEW.assignment_id;
        IF v_assignment_uuid IS NULL
          OR NEW.nominee_membership_id IS DISTINCT FROM v_assignment_membership_id THEN
        RAISE EXCEPTION 'S2B office consent nomination context refused'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT event_uuid, actor_id, action, sequence, resulting_state, assignment_id
      INTO proposal FROM public.core_contextualroleassignmentstateevent
     WHERE id = NEW.proposal_event_id;
    IF proposal.event_uuid IS NULL OR proposal.assignment_id <> NEW.assignment_id
       OR proposal.sequence <> 1 OR proposal.action <> 'PROPOSE'
       OR proposal.resulting_state <> 'PROPOSED'
      OR NEW.actor_id <> v_nominee_identity_id
       OR NEW.actor_access_epoch <> (
          SELECT access_epoch FROM public.core_identity WHERE id = v_nominee_identity_id
       ) THEN
        RAISE EXCEPTION 'S2B office consent nominee or proposal refused'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.core_contextualroleassignmentstateevent assigned
         WHERE assigned.assignment_id = NEW.assignment_id AND assigned.action = 'ASSIGN'
           AND assigned.recorded_at <= NEW.recorded_at
    ) THEN
        RAISE EXCEPTION 'S2B office consent after appointment refused'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT COALESCE(MAX(ordinal), 0) INTO previous_ordinal
      FROM public.core_officeappointmentconsent WHERE assignment_id = NEW.assignment_id;
    IF NEW.ordinal <> previous_ordinal + 1 THEN
        RAISE EXCEPTION 'S2B office consent ordinal refused'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT action INTO previous_action FROM public.core_officeappointmentconsent
     WHERE assignment_id = NEW.assignment_id ORDER BY ordinal DESC LIMIT 1;
    IF previous_action IN ('DECLINE', 'WITHDRAW') THEN
        RAISE EXCEPTION 'S2B office consent terminal history refused'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF proposal.actor_id = v_nominee_identity_id THEN
        IF NEW.action <> 'WITHDRAW' OR previous_action IS NOT NULL THEN
            RAISE EXCEPTION 'S2B self-proposal consent history refused'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSIF (previous_action IS NULL AND NEW.action NOT IN ('CONSENT', 'DECLINE'))
       OR (previous_action = 'CONSENT' AND NEW.action <> 'WITHDRAW') THEN
        RAISE EXCEPTION 'S2B office consent sequence refused'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT * INTO decision_row FROM public.core_authoritydecision
     WHERE decision_reference = NEW.authority_decision_reference;
    IF decision_row.decision_reference IS NULL
       OR decision_row.outcome <> 'QUALIFIED'
       OR decision_row.action <> 'RECORD_OFFICE_CONSENT'
       OR decision_row.command_subtype <> 'RECORD_OFFICE_CONSENT'
       OR decision_row.actor_id <> NEW.actor_id
    OR decision_row.actor_access_epoch <> NEW.actor_access_epoch
    OR decision_row.target->>'assignment_uuid' IS DISTINCT FROM v_assignment_uuid::text
       OR decision_row.target->>'proposal_event_uuid' IS DISTINCT FROM proposal.event_uuid::text
    OR decision_row.target->>'membership_uuid' IS DISTINCT FROM v_membership_uuid::text
       OR decision_row.target->>'event_action' IS DISTINCT FROM NEW.action
       OR decision_row.authority_reference IS NULL
       OR decision_row.authority_reference NOT LIKE 's015a1:%' THEN
        RAISE EXCEPTION 'S2B office consent authority decision refused'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    reference_facts := substring(decision_row.authority_reference FROM 8)::jsonb;
        IF reference_facts->>'a' IS DISTINCT FROM v_assignment_uuid::text
          OR reference_facts->>'g' IS DISTINCT FROM role_uuid::text
          OR reference_facts->>'p' IS DISTINCT FROM proposal.event_uuid::text
          OR reference_facts->>'u' IS DISTINCT FROM v_membership_uuid::text
          OR NOT decision_row.facts_read @> jsonb_build_array(jsonb_build_object(
              'relation', 'core_contextualroleassignment', 'reference', v_assignment_uuid::text
          ))
          OR NOT decision_row.facts_read @> jsonb_build_array(jsonb_build_object(
              'relation', 'core_contextualroleassignmentstateevent', 'reference', proposal.event_uuid::text
          ))
          OR NOT decision_row.facts_read @> jsonb_build_array(jsonb_build_object(
              'relation', 'core_organismmembership', 'reference', v_membership_uuid::text
          ))
          OR NOT decision_row.facts_read @> jsonb_build_array(jsonb_build_object(
              'relation', 'core_organismroledefinition', 'reference', role_uuid::text
          )) THEN
        RAISE EXCEPTION 'S2B office consent governed references refused'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END """
S2B_GUARD_OFFICE_CONSENT_CLOCK = r"""
DECLARE v_assignment_uuid uuid; v_assignment_membership_id bigint; v_membership_uuid uuid;
    v_nominee_identity_id bigint; proposal record; previous_action text;
    previous_ordinal integer; role_uuid uuid;
        decision_row record; reference_facts jsonb;
BEGIN
    IF NEW.recorded_at IS NOT NULL THEN
        RAISE EXCEPTION 'S2B office consent recorded_at is database-assigned'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    NEW.recorded_at := public.intevia_clock_statement();
    IF NEW.actor_state IS DISTINCT FROM 'PARTY'
       OR NEW.actor_capacity IS DISTINCT FROM 'STANDING'
       OR NEW.actor_id IS DISTINCT FROM NEW.enterer_id
       OR NEW.actor_access_epoch IS NULL
       OR NEW.occurred_at > NEW.received_at
       OR NEW.received_at > NEW.recorded_at THEN
        RAISE EXCEPTION 'S2B office consent attribution or time order refused'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
        SELECT a.assignment_uuid, a.membership_id, m.membership_uuid, m.identity_id, d.role_uuid
         INTO v_assignment_uuid, v_assignment_membership_id, v_membership_uuid,
             v_nominee_identity_id, role_uuid
      FROM public.core_contextualroleassignment a
      JOIN public.core_organismmembership m ON m.id = a.membership_id
         JOIN public.core_organismroledefinition d ON d.id = a.role_definition_id
     WHERE a.id = NEW.assignment_id;
        IF v_assignment_uuid IS NULL
          OR NEW.nominee_membership_id IS DISTINCT FROM v_assignment_membership_id THEN
        RAISE EXCEPTION 'S2B office consent nomination context refused'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT event_uuid, actor_id, action, sequence, resulting_state, assignment_id
      INTO proposal FROM public.core_contextualroleassignmentstateevent
     WHERE id = NEW.proposal_event_id;
    IF proposal.event_uuid IS NULL OR proposal.assignment_id <> NEW.assignment_id
       OR proposal.sequence <> 1 OR proposal.action <> 'PROPOSE'
       OR proposal.resulting_state <> 'PROPOSED'
      OR NEW.actor_id <> v_nominee_identity_id
       OR NEW.actor_access_epoch <> (
          SELECT access_epoch FROM public.core_identity WHERE id = v_nominee_identity_id
       ) THEN
        RAISE EXCEPTION 'S2B office consent nominee or proposal refused'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.core_contextualroleassignmentstateevent assigned
         WHERE assigned.assignment_id = NEW.assignment_id AND assigned.action = 'ASSIGN'
           AND assigned.recorded_at <= NEW.recorded_at
    ) THEN
        RAISE EXCEPTION 'S2B office consent after appointment refused'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT COALESCE(MAX(ordinal), 0) INTO previous_ordinal
      FROM public.core_officeappointmentconsent WHERE assignment_id = NEW.assignment_id;
    IF NEW.ordinal <> previous_ordinal + 1 THEN
        RAISE EXCEPTION 'S2B office consent ordinal refused'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT action INTO previous_action FROM public.core_officeappointmentconsent
     WHERE assignment_id = NEW.assignment_id ORDER BY ordinal DESC LIMIT 1;
    IF previous_action IN ('DECLINE', 'WITHDRAW') THEN
        RAISE EXCEPTION 'S2B office consent terminal history refused'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF proposal.actor_id = v_nominee_identity_id THEN
        IF NEW.action <> 'WITHDRAW' OR previous_action IS NOT NULL THEN
            RAISE EXCEPTION 'S2B self-proposal consent history refused'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSIF (previous_action IS NULL AND NEW.action NOT IN ('CONSENT', 'DECLINE'))
       OR (previous_action = 'CONSENT' AND NEW.action <> 'WITHDRAW') THEN
        RAISE EXCEPTION 'S2B office consent sequence refused'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT * INTO decision_row FROM public.core_authoritydecision
     WHERE decision_reference = NEW.authority_decision_reference;
    IF decision_row.decision_reference IS NULL
       OR decision_row.outcome <> 'QUALIFIED'
       OR decision_row.action <> 'RECORD_OFFICE_CONSENT'
       OR decision_row.command_subtype <> 'RECORD_OFFICE_CONSENT'
       OR decision_row.actor_id <> NEW.actor_id
    OR decision_row.actor_access_epoch <> NEW.actor_access_epoch
    OR decision_row.target->>'assignment_uuid' IS DISTINCT FROM v_assignment_uuid::text
       OR decision_row.target->>'proposal_event_uuid' IS DISTINCT FROM proposal.event_uuid::text
    OR decision_row.target->>'membership_uuid' IS DISTINCT FROM v_membership_uuid::text
       OR decision_row.target->>'event_action' IS DISTINCT FROM NEW.action
       OR decision_row.authority_reference IS NULL
       OR decision_row.authority_reference NOT LIKE 's015a1:%' THEN
        RAISE EXCEPTION 'S2B office consent authority decision refused'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    reference_facts := substring(decision_row.authority_reference FROM 8)::jsonb;
        IF reference_facts->>'a' IS DISTINCT FROM v_assignment_uuid::text
          OR reference_facts->>'g' IS DISTINCT FROM role_uuid::text
          OR reference_facts->>'p' IS DISTINCT FROM proposal.event_uuid::text
          OR reference_facts->>'u' IS DISTINCT FROM v_membership_uuid::text
          OR NOT decision_row.facts_read @> jsonb_build_array(jsonb_build_object(
              'relation', 'core_contextualroleassignment', 'reference', v_assignment_uuid::text
          ))
          OR NOT decision_row.facts_read @> jsonb_build_array(jsonb_build_object(
              'relation', 'core_contextualroleassignmentstateevent', 'reference', proposal.event_uuid::text
          ))
          OR NOT decision_row.facts_read @> jsonb_build_array(jsonb_build_object(
              'relation', 'core_organismmembership', 'reference', v_membership_uuid::text
          ))
          OR NOT decision_row.facts_read @> jsonb_build_array(jsonb_build_object(
              'relation', 'core_organismroledefinition', 'reference', role_uuid::text
          )) THEN
        RAISE EXCEPTION 'S2B office consent governed references refused'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END """

# public.s015_r4s2_guard_assign_current_membership()
# Its landed text: 0041_r4s2_assign_current_membership.py (found there verbatim). Time calls moved: 1 statement.
S015_R4S2_GUARD_ASSIGN_CURRENT_MEMBERSHIP_LANDED = r"""
DECLARE v_known timestamptz;
BEGIN
    IF NEW.action <> 'ASSIGN' THEN RETURN NEW; END IF;
    v_known := coalesce(NEW.recorded_at, statement_timestamp());
    IF NOT public.s015_r4s2_assign_membership_current(NEW.assignment_id, NEW.effective_at, v_known) THEN
        RAISE EXCEPTION 'S015 R4S2: ASSIGN requires the office''s membership to be ACTIVE at record time (assignment %)', NEW.assignment_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END """
S015_R4S2_GUARD_ASSIGN_CURRENT_MEMBERSHIP_CLOCK = r"""
DECLARE v_known timestamptz;
BEGIN
    IF NEW.action <> 'ASSIGN' THEN RETURN NEW; END IF;
    v_known := coalesce(NEW.recorded_at, public.intevia_clock_statement());
    IF NOT public.s015_r4s2_assign_membership_current(NEW.assignment_id, NEW.effective_at, v_known) THEN
        RAISE EXCEPTION 'S015 R4S2: ASSIGN requires the office''s membership to be ACTIVE at record time (assignment %)', NEW.assignment_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END """

# public.p1_assign_registration_commitment()
# Its landed text: 0043_p1_governed_registration.py (found there verbatim). Time calls moved: 1 statement.
P1_ASSIGN_REGISTRATION_COMMITMENT_LANDED = r"""
        DECLARE preimage text;
        BEGIN
            IF NEW.registration_commitment IS NOT NULL THEN
                RAISE EXCEPTION 'P1 registration commitment is database-assigned'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            -- PostgreSQL orders same-event triggers by name; commitment runs before record_time.
            preimage := '{"actor_access_epoch":' ||
                    public.s015_canonical_value('bigint'::regtype, to_jsonb(NEW.actor_access_epoch)) ||
                ',"authority_reference":' ||
                    public.s015_canonical_value('character varying'::regtype, to_jsonb(NEW.authority_reference)) ||
                ',"effective_at":' ||
                    public.s015_canonical_value('timestamp with time zone'::regtype, to_jsonb(NEW.effective_at)) ||
                ',"enterer_executor":' ||
                    public.s015_canonical_value('character varying'::regtype, to_jsonb(NEW.enterer_executor)) ||
                ',"entry_mode":' ||
                    public.s015_canonical_value('character varying'::regtype, to_jsonb(NEW.entry_mode)) ||
                ',"evidence_reference":' ||
                    public.s015_canonical_value('character varying'::regtype, to_jsonb(NEW.evidence_reference)) ||
                ',"identity_id":' ||
                    public.s015_canonical_value('bigint'::regtype, to_jsonb(NEW.identity_id)) ||
                ',"received_at":' ||
                    public.s015_canonical_value('timestamp with time zone'::regtype, to_jsonb(NEW.received_at)) ||
                ',"recorded_at":' ||
                    public.s015_canonical_value(
                        'timestamp with time zone'::regtype,
                        to_jsonb(statement_timestamp())
                    ) ||
                ',"registration_receipt_id":' ||
                    public.s015_canonical_value(
                        'bigint'::regtype,
                        to_jsonb(NEW.registration_receipt_id)
                    ) ||
                ',"registration_uuid":' ||
                    public.s015_canonical_value('uuid'::regtype, to_jsonb(NEW.registration_uuid)) || '}';
            NEW.registration_commitment := public.s015_0022_digest('s015r1', preimage);
            RETURN NEW;
        END """
P1_ASSIGN_REGISTRATION_COMMITMENT_CLOCK = r"""
        DECLARE preimage text;
        BEGIN
            IF NEW.registration_commitment IS NOT NULL THEN
                RAISE EXCEPTION 'P1 registration commitment is database-assigned'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            -- PostgreSQL orders same-event triggers by name; commitment runs before record_time.
            preimage := '{"actor_access_epoch":' ||
                    public.s015_canonical_value('bigint'::regtype, to_jsonb(NEW.actor_access_epoch)) ||
                ',"authority_reference":' ||
                    public.s015_canonical_value('character varying'::regtype, to_jsonb(NEW.authority_reference)) ||
                ',"effective_at":' ||
                    public.s015_canonical_value('timestamp with time zone'::regtype, to_jsonb(NEW.effective_at)) ||
                ',"enterer_executor":' ||
                    public.s015_canonical_value('character varying'::regtype, to_jsonb(NEW.enterer_executor)) ||
                ',"entry_mode":' ||
                    public.s015_canonical_value('character varying'::regtype, to_jsonb(NEW.entry_mode)) ||
                ',"evidence_reference":' ||
                    public.s015_canonical_value('character varying'::regtype, to_jsonb(NEW.evidence_reference)) ||
                ',"identity_id":' ||
                    public.s015_canonical_value('bigint'::regtype, to_jsonb(NEW.identity_id)) ||
                ',"received_at":' ||
                    public.s015_canonical_value('timestamp with time zone'::regtype, to_jsonb(NEW.received_at)) ||
                ',"recorded_at":' ||
                    public.s015_canonical_value(
                        'timestamp with time zone'::regtype,
                        to_jsonb(public.intevia_clock_statement())
                    ) ||
                ',"registration_receipt_id":' ||
                    public.s015_canonical_value(
                        'bigint'::regtype,
                        to_jsonb(NEW.registration_receipt_id)
                    ) ||
                ',"registration_uuid":' ||
                    public.s015_canonical_value('uuid'::regtype, to_jsonb(NEW.registration_uuid)) || '}';
            NEW.registration_commitment := public.s015_0022_digest('s015r1', preimage);
            RETURN NEW;
        END """

# public.p1_assign_registrationreceipt()
# Its landed text: 0043_p1_governed_registration.py (found there verbatim). Time calls moved: 1 wall.
P1_ASSIGN_REGISTRATIONRECEIPT_LANDED = r"""
        BEGIN
            IF NEW.received_at IS NOT NULL OR NEW.transaction_id IS NOT NULL THEN
                RAISE EXCEPTION 'P1 registration receipt fields are database-assigned'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            NEW.received_at := clock_timestamp();
            NEW.transaction_id := txid_current();
            RETURN NEW;
        END """
P1_ASSIGN_REGISTRATIONRECEIPT_CLOCK = r"""
        BEGIN
            IF NEW.received_at IS NOT NULL OR NEW.transaction_id IS NOT NULL THEN
                RAISE EXCEPTION 'P1 registration receipt fields are database-assigned'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            NEW.received_at := public.intevia_clock_instant();
            NEW.transaction_id := txid_current();
            RETURN NEW;
        END """

# public.s015_entitlement_limit(uuid, text, timestamp with time zone)
# Its landed text: 0047_sek1_platform_rule_statement_start.py (assembled by that migration). Time calls moved: 1 statement.
S015_ENTITLEMENT_LIMIT_LANDED = r"""
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
           AND rule.recorded_at <= statement_timestamp() /* HG_RULING_O1_O3_AND_PKT_CLK_1_20261014, O-3 option (b) */
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
S015_ENTITLEMENT_LIMIT_CLOCK = r"""
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
           AND rule.recorded_at <= public.intevia_clock_statement() /* HG_RULING_O1_O3_AND_PKT_CLK_1_20261014, O-3 option (b) */
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

# public.s015_h100_assign_command_intake_time()
# Its landed text: 0049_h100_command_intake.py (found there verbatim). Time calls moved: 1 wall.
S015_H100_ASSIGN_COMMAND_INTAKE_TIME_LANDED = r"""
        BEGIN
            IF NEW.received_at IS NOT NULL THEN
                RAISE EXCEPTION 'H100 command intake received_at is database-assigned'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            NEW.received_at := clock_timestamp();
            RETURN NEW;
        END
        """
S015_H100_ASSIGN_COMMAND_INTAKE_TIME_CLOCK = r"""
        BEGIN
            IF NEW.received_at IS NOT NULL THEN
                RAISE EXCEPTION 'H100 command intake received_at is database-assigned'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            NEW.received_at := public.intevia_clock_instant();
            RETURN NEW;
        END
        """

FUNCTIONS = (
    ("public.s015_assign_event_record_time()", S015_ASSIGN_EVENT_RECORD_TIME_LANDED, S015_ASSIGN_EVENT_RECORD_TIME_CLOCK),
    ("public.s015_requalify_governed_cache(text, bigint)", S015_REQUALIFY_GOVERNED_CACHE_LANDED, S015_REQUALIFY_GOVERNED_CACHE_CLOCK),
    ("public.s015_validate_founding_population()", S015_VALIDATE_FOUNDING_POPULATION_LANDED, S015_VALIDATE_FOUNDING_POPULATION_CLOCK),
    ("public.s015_0022_assign_part_record_time()", S015_0022_ASSIGN_PART_RECORD_TIME_LANDED, S015_0022_ASSIGN_PART_RECORD_TIME_CLOCK),
    ("public.s3_assign_record_time()", S3_ASSIGN_RECORD_TIME_LANDED, S3_ASSIGN_RECORD_TIME_CLOCK),
    ("public.s3_terminal_lock()", S3_TERMINAL_LOCK_LANDED, S3_TERMINAL_LOCK_CLOCK),
    ("public.s3fu_guard_auto_decision()", S3FU_GUARD_AUTO_DECISION_LANDED, S3FU_GUARD_AUTO_DECISION_CLOCK),
    ("public.s3fu_guard_auto_engagement()", S3FU_GUARD_AUTO_ENGAGEMENT_LANDED, S3FU_GUARD_AUTO_ENGAGEMENT_CLOCK),
    ("public.s3_engagement_guard()", S3_ENGAGEMENT_GUARD_LANDED, S3_ENGAGEMENT_GUARD_CLOCK),
    ("public.s015_authoritydecision_guard()", S015_AUTHORITYDECISION_GUARD_LANDED, S015_AUTHORITYDECISION_GUARD_CLOCK),
    ("public.s2b_guard_office_consent()", S2B_GUARD_OFFICE_CONSENT_LANDED, S2B_GUARD_OFFICE_CONSENT_CLOCK),
    ("public.s015_r4s2_guard_assign_current_membership()", S015_R4S2_GUARD_ASSIGN_CURRENT_MEMBERSHIP_LANDED, S015_R4S2_GUARD_ASSIGN_CURRENT_MEMBERSHIP_CLOCK),
    ("public.p1_assign_registration_commitment()", P1_ASSIGN_REGISTRATION_COMMITMENT_LANDED, P1_ASSIGN_REGISTRATION_COMMITMENT_CLOCK),
    ("public.p1_assign_registrationreceipt()", P1_ASSIGN_REGISTRATIONRECEIPT_LANDED, P1_ASSIGN_REGISTRATIONRECEIPT_CLOCK),
    ("public.s015_entitlement_limit(uuid, text, timestamp with time zone)", S015_ENTITLEMENT_LIMIT_LANDED, S015_ENTITLEMENT_LIMIT_CLOCK),
    ("public.s015_h100_assign_command_intake_time()", S015_H100_ASSIGN_COMMAND_INTAKE_TIME_LANDED, S015_H100_ASSIGN_COMMAND_INTAKE_TIME_CLOCK),
)


def install(apps, schema_editor):
    """Move these functions onto the INTEVIA Clock."""
    clock.convert(schema_editor, FUNCTIONS)


def uninstall(apps, schema_editor):
    """Put the landed functions back."""
    clock.restore(schema_editor, FUNCTIONS)


class Migration(migrations.Migration):
    dependencies = (("core", "0096_intevia_clock"),)
    operations = (migrations.RunPython(install, uninstall),)
