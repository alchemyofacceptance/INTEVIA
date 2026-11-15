"""The INTEVIA Clock, storage 2 of 7: the installed functions of migrations 0050 to 0058 (CK-2a2).

14 installed functions read the time through PostgreSQL's own calls. This migration replaces
each under G2-4 with a successor that differs from it only where a time call becomes the
INTEVIA Clock's face with the same meaning (36 calls: 17 statement start, 17 instant,
2 transaction start):

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

# public.s015_compute_governed_cache()
# Its landed text: 0050_clk_mono_1_cache_coordinate_detail.py (found there verbatim). Time calls moved: 1 statement, 1 wall, 1 transaction.
S015_COMPUTE_GOVERNED_CACHE_LANDED = r"""
DECLARE v_updated integer; v_state_at timestamptz; v_known_at timestamptz; v_now timestamptz; v_ids bigint[]; v_fp text; v_state text;
        ob public.s015_obligation_projection_t;
BEGIN
    UPDATE public.s015_transaction_register SET consumed = TRUE
     WHERE xid = pg_current_xact_id() AND kind = 'REFRESH_TICKET' AND anchor_token = TG_TABLE_NAME AND anchor_id = NEW.id AND NOT consumed;
    GET DIAGNOSTICS v_updated = ROW_COUNT;
    IF v_updated = 0 THEN
        RAISE EXCEPTION 'S015 cache coordinates are service-only: no refresh ticket for this row in this transaction'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF TG_TABLE_NAME = 'core_governedobligationcase' THEN
        v_state_at := NEW.status_state_at; v_known_at := NEW.status_known_at;
    ELSE
        v_state_at := (to_jsonb(NEW)->>'current_state_effective_at')::timestamptz; v_known_at := (to_jsonb(NEW)->>'state_known_at')::timestamptz;
    END IF;
    v_now := clock_timestamp();
    IF NOT (v_state_at <= v_known_at AND v_known_at <= v_now) THEN
        RAISE EXCEPTION 'S015 cache coordinates must satisfy state_at <= known_at <= now'
            USING ERRCODE = 'integrity_constraint_violation',
                  DETAIL = format('S015 cache coordinate diagnostic: anchor=%s id=%s state_at=%s known_at=%s now=%s statement_start=%s xact_start=%s',
                                  TG_TABLE_NAME, NEW.id, v_state_at, v_known_at, v_now, statement_timestamp(), transaction_timestamp());
    END IF;
    v_ids := public.s015_eligible_event_ids(TG_TABLE_NAME, NEW.id, v_state_at, v_known_at);
    v_fp := public.s015_fingerprint_event_set(TG_TABLE_NAME, NEW.id, v_state_at, v_known_at);
    IF TG_TABLE_NAME = 'core_governedobligationcase' THEN
        ob := public.s015_reduce_obligation(v_ids);
        NEW.status_state := ob.resulting_state; NEW.legal_deadline_status := ob.legal_deadline_status;
        NEW.qualified_legal_deadline := ob.qualified_legal_deadline; NEW.effective_deadline := ob.effective_deadline;
        NEW.legal_basis_determination_id := ob.legal_basis_determination_id;
        NEW.status_source_event_set_fingerprint := v_fp;
    ELSE
        v_state := public.s015_reduce_projection(TG_TABLE_NAME, v_ids);
        NEW := jsonb_populate_record(NEW, jsonb_build_object('state', v_state, 'state_source_event_set_fingerprint', v_fp));
    END IF;
    RETURN NEW;
END """
S015_COMPUTE_GOVERNED_CACHE_CLOCK = r"""
DECLARE v_updated integer; v_state_at timestamptz; v_known_at timestamptz; v_now timestamptz; v_ids bigint[]; v_fp text; v_state text;
        ob public.s015_obligation_projection_t;
BEGIN
    UPDATE public.s015_transaction_register SET consumed = TRUE
     WHERE xid = pg_current_xact_id() AND kind = 'REFRESH_TICKET' AND anchor_token = TG_TABLE_NAME AND anchor_id = NEW.id AND NOT consumed;
    GET DIAGNOSTICS v_updated = ROW_COUNT;
    IF v_updated = 0 THEN
        RAISE EXCEPTION 'S015 cache coordinates are service-only: no refresh ticket for this row in this transaction'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF TG_TABLE_NAME = 'core_governedobligationcase' THEN
        v_state_at := NEW.status_state_at; v_known_at := NEW.status_known_at;
    ELSE
        v_state_at := (to_jsonb(NEW)->>'current_state_effective_at')::timestamptz; v_known_at := (to_jsonb(NEW)->>'state_known_at')::timestamptz;
    END IF;
    v_now := public.intevia_clock_instant();
    IF NOT (v_state_at <= v_known_at AND v_known_at <= v_now) THEN
        RAISE EXCEPTION 'S015 cache coordinates must satisfy state_at <= known_at <= now'
            USING ERRCODE = 'integrity_constraint_violation',
                  DETAIL = format('S015 cache coordinate diagnostic: anchor=%s id=%s state_at=%s known_at=%s now=%s statement_start=%s xact_start=%s',
                                  TG_TABLE_NAME, NEW.id, v_state_at, v_known_at, v_now, public.intevia_clock_statement(), public.intevia_clock_transaction());
    END IF;
    v_ids := public.s015_eligible_event_ids(TG_TABLE_NAME, NEW.id, v_state_at, v_known_at);
    v_fp := public.s015_fingerprint_event_set(TG_TABLE_NAME, NEW.id, v_state_at, v_known_at);
    IF TG_TABLE_NAME = 'core_governedobligationcase' THEN
        ob := public.s015_reduce_obligation(v_ids);
        NEW.status_state := ob.resulting_state; NEW.legal_deadline_status := ob.legal_deadline_status;
        NEW.qualified_legal_deadline := ob.qualified_legal_deadline; NEW.effective_deadline := ob.effective_deadline;
        NEW.legal_basis_determination_id := ob.legal_basis_determination_id;
        NEW.status_source_event_set_fingerprint := v_fp;
    ELSE
        v_state := public.s015_reduce_projection(TG_TABLE_NAME, v_ids);
        NEW := jsonb_populate_record(NEW, jsonb_build_object('state', v_state, 'state_source_event_set_fingerprint', v_fp));
    END IF;
    RETURN NEW;
END """

# public.s015_refresh_governed_cache(text, bigint, timestamp with time zone, timestamp with time zone)
# Its landed text: 0050_clk_mono_1_cache_coordinate_detail.py (found there verbatim). Time calls moved: 1 statement, 1 wall, 1 transaction.
S015_REFRESH_GOVERNED_CACHE_LANDED = r"""
DECLARE m record; v_fp text; v_lock bigint; v_now timestamptz;
BEGIN
    SELECT * INTO m FROM public.s015_anchor_map() WHERE anchor_token = p_anchor_token AND has_cache;
    IF m IS NULL THEN RAISE EXCEPTION 'S015 refresh: % is not a cache-bearing anchor', p_anchor_token; END IF;
    v_now := clock_timestamp();
    IF NOT (p_state_at <= p_known_at AND p_known_at <= v_now) THEN
        RAISE EXCEPTION 'S015 refresh: coordinates must satisfy state_at <= known_at <= now'
            USING DETAIL = format('S015 cache coordinate diagnostic: anchor=%s id=%s state_at=%s known_at=%s now=%s statement_start=%s xact_start=%s',
                                  p_anchor_token, p_anchor_id, p_state_at, p_known_at, v_now, statement_timestamp(), transaction_timestamp());
    END IF;
    EXECUTE format('SELECT id FROM public.%I WHERE id = $1 FOR UPDATE', p_anchor_token) INTO v_lock USING p_anchor_id;
    IF v_lock IS NULL THEN RAISE EXCEPTION 'S015 refresh: % id % not found', p_anchor_token, p_anchor_id; END IF;
    INSERT INTO public.s015_transaction_register (xid, kind, anchor_token, anchor_id)
        VALUES (pg_current_xact_id(), 'REFRESH_TICKET', p_anchor_token, p_anchor_id);
    IF p_anchor_token = 'core_governedobligationcase' THEN
        UPDATE public.core_governedobligationcase SET status_state_at = p_state_at, status_known_at = p_known_at WHERE id = p_anchor_id;
        SELECT status_source_event_set_fingerprint INTO v_fp FROM public.core_governedobligationcase WHERE id = p_anchor_id;
    ELSE
        EXECUTE format('UPDATE public.%I SET current_state_effective_at = $1, state_known_at = $2 WHERE id = $3', p_anchor_token)
            USING p_state_at, p_known_at, p_anchor_id;
        EXECUTE format('SELECT state_source_event_set_fingerprint FROM public.%I WHERE id = $1', p_anchor_token) INTO v_fp USING p_anchor_id;
    END IF;
    DELETE FROM public.s015_transaction_register WHERE xid = pg_current_xact_id() AND kind = 'REFRESH_TICKET' AND anchor_token = p_anchor_token AND anchor_id = p_anchor_id;
    RETURN v_fp;
END """
S015_REFRESH_GOVERNED_CACHE_CLOCK = r"""
DECLARE m record; v_fp text; v_lock bigint; v_now timestamptz;
BEGIN
    SELECT * INTO m FROM public.s015_anchor_map() WHERE anchor_token = p_anchor_token AND has_cache;
    IF m IS NULL THEN RAISE EXCEPTION 'S015 refresh: % is not a cache-bearing anchor', p_anchor_token; END IF;
    v_now := public.intevia_clock_instant();
    IF NOT (p_state_at <= p_known_at AND p_known_at <= v_now) THEN
        RAISE EXCEPTION 'S015 refresh: coordinates must satisfy state_at <= known_at <= now'
            USING DETAIL = format('S015 cache coordinate diagnostic: anchor=%s id=%s state_at=%s known_at=%s now=%s statement_start=%s xact_start=%s',
                                  p_anchor_token, p_anchor_id, p_state_at, p_known_at, v_now, public.intevia_clock_statement(), public.intevia_clock_transaction());
    END IF;
    EXECUTE format('SELECT id FROM public.%I WHERE id = $1 FOR UPDATE', p_anchor_token) INTO v_lock USING p_anchor_id;
    IF v_lock IS NULL THEN RAISE EXCEPTION 'S015 refresh: % id % not found', p_anchor_token, p_anchor_id; END IF;
    INSERT INTO public.s015_transaction_register (xid, kind, anchor_token, anchor_id)
        VALUES (pg_current_xact_id(), 'REFRESH_TICKET', p_anchor_token, p_anchor_id);
    IF p_anchor_token = 'core_governedobligationcase' THEN
        UPDATE public.core_governedobligationcase SET status_state_at = p_state_at, status_known_at = p_known_at WHERE id = p_anchor_id;
        SELECT status_source_event_set_fingerprint INTO v_fp FROM public.core_governedobligationcase WHERE id = p_anchor_id;
    ELSE
        EXECUTE format('UPDATE public.%I SET current_state_effective_at = $1, state_known_at = $2 WHERE id = $3', p_anchor_token)
            USING p_state_at, p_known_at, p_anchor_id;
        EXECUTE format('SELECT state_source_event_set_fingerprint FROM public.%I WHERE id = $1', p_anchor_token) INTO v_fp USING p_anchor_id;
    END IF;
    DELETE FROM public.s015_transaction_register WHERE xid = pg_current_xact_id() AND kind = 'REFRESH_TICKET' AND anchor_token = p_anchor_token AND anchor_id = p_anchor_id;
    RETURN v_fp;
END """

# public.sr_provision_insert_guard()
# Its landed text: 0051_sr_role_definitions.py (found there verbatim). Time calls moved: 1 statement.
SR_PROVISION_INSERT_GUARD_LANDED = r"""
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
SR_PROVISION_INSERT_GUARD_CLOCK = r"""
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
    NEW.recorded_at := public.intevia_clock_statement();
    RETURN NEW;
END
"""

# public.sr_definition_update_guard()
# Its landed text: 0053_sr_office_safeguards.py (assembled by that migration). Time calls moved: 1 statement, 1 wall.
SR_DEFINITION_UPDATE_GUARD_LANDED = r"""
DECLARE v_decision_row record; v_open_assignment bigint; v_now timestamptz;
BEGIN
    IF OLD.scope IS DISTINCT FROM 'LIVING_ORGANISM' THEN
        RETURN NEW;
    END IF;
    IF OLD.code = 'COORDINATOR' THEN
        RAISE EXCEPTION 'SR definition refused: the Coordinator office is established at founding and cannot be changed here'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF starts_with(OLD.code, 'INTEVIA_') THEN
        RAISE EXCEPTION 'SR definition refused: reserved root office'
            USING ERRCODE = 'integrity_constraint_violation';
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
    IF OLD.kind = 'BOARD_OFFICE' THEN
        RAISE EXCEPTION 'SR definition refused: board offices are permanent; amend the definition instead of retiring the office'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
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
SR_DEFINITION_UPDATE_GUARD_CLOCK = r"""
DECLARE v_decision_row record; v_open_assignment bigint; v_now timestamptz;
BEGIN
    IF OLD.scope IS DISTINCT FROM 'LIVING_ORGANISM' THEN
        RETURN NEW;
    END IF;
    IF OLD.code = 'COORDINATOR' THEN
        RAISE EXCEPTION 'SR definition refused: the Coordinator office is established at founding and cannot be changed here'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF starts_with(OLD.code, 'INTEVIA_') THEN
        RAISE EXCEPTION 'SR definition refused: reserved root office'
            USING ERRCODE = 'integrity_constraint_violation';
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
    IF OLD.kind = 'BOARD_OFFICE' THEN
        RAISE EXCEPTION 'SR definition refused: board offices are permanent; amend the definition instead of retiring the office'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.retired_by_decision_reference IS NULL OR NEW.retired_effective_at IS NULL
       OR NEW.retired_received_at IS NULL OR NEW.retirement_reason IS NULL THEN
        RAISE EXCEPTION 'SR definition refused: retirement attribution incomplete'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.retired_recorded_at IS NOT NULL THEN
        RAISE EXCEPTION 'SR definition refused: retired_recorded_at is database-assigned'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    NEW.retired_recorded_at := public.intevia_clock_statement();
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
    v_now := public.intevia_clock_instant();
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

# public.c2_future_cuts(bigint)
# Its landed text: 0055_c2_coverage.py (found there verbatim). Time calls moved: 1 statement.
C2_FUTURE_CUTS_LANDED = r"""
DECLARE cut record; v integer; actual text; previous public.core_coverageassessment; origin public.core_livingorganismevent;
        known timestamptz:=statement_timestamp(); key_ text;
BEGIN
 SELECT * INTO origin FROM public.core_livingorganismevent WHERE living_organism_id=lo_id_ AND sequence=1;
 FOR cut IN SELECT i.due,min(i.source) AS first_source FROM public.c2_timed_input i
   WHERE i.lo_id=lo_id_ AND i.due>known GROUP BY i.due ORDER BY i.due LOOP
   v:=public.c2_roster_version(lo_id_,cut.due,known);
   IF v IS NULL THEN CONTINUE; END IF;
   actual:=public.c2_result(lo_id_,v,cut.due,known);
   SELECT a.* INTO previous FROM public.core_coverageassessment a JOIN public.core_essentialcoveragecase c ON c.id=a.case_id
   WHERE c.living_organism_id=lo_id_ AND a.effective_at=cut.due AND a.received_at<=known AND a.recorded_at<=known
   ORDER BY a.occurred_at DESC,a.received_at DESC,a.recorded_at DESC,a.sequence DESC LIMIT 1;
   IF previous.id IS NOT NULL AND previous.result=actual AND previous.roster_version=v
      AND public.c2_result(lo_id_,v,cut.due,previous.evaluated_known_at,true)=public.c2_result(lo_id_,v,cut.due,known,true)
   THEN CONTINUE; END IF;
   key_:='c2-forecast:'||gen_random_uuid();
   PERFORM public.c2_publish(lo_id_,cut.due,known,known,origin.enterer_identity_id,origin.authority_basis_id,
     origin.authority_decision_reference,'c2-forecast:'||cut.first_source,key_,encode(sha256(convert_to(key_,'UTF8')),'hex'));
 END LOOP;
END """
C2_FUTURE_CUTS_CLOCK = r"""
DECLARE cut record; v integer; actual text; previous public.core_coverageassessment; origin public.core_livingorganismevent;
        known timestamptz:=public.intevia_clock_statement(); key_ text;
BEGIN
 SELECT * INTO origin FROM public.core_livingorganismevent WHERE living_organism_id=lo_id_ AND sequence=1;
 FOR cut IN SELECT i.due,min(i.source) AS first_source FROM public.c2_timed_input i
   WHERE i.lo_id=lo_id_ AND i.due>known GROUP BY i.due ORDER BY i.due LOOP
   v:=public.c2_roster_version(lo_id_,cut.due,known);
   IF v IS NULL THEN CONTINUE; END IF;
   actual:=public.c2_result(lo_id_,v,cut.due,known);
   SELECT a.* INTO previous FROM public.core_coverageassessment a JOIN public.core_essentialcoveragecase c ON c.id=a.case_id
   WHERE c.living_organism_id=lo_id_ AND a.effective_at=cut.due AND a.received_at<=known AND a.recorded_at<=known
   ORDER BY a.occurred_at DESC,a.received_at DESC,a.recorded_at DESC,a.sequence DESC LIMIT 1;
   IF previous.id IS NOT NULL AND previous.result=actual AND previous.roster_version=v
      AND public.c2_result(lo_id_,v,cut.due,previous.evaluated_known_at,true)=public.c2_result(lo_id_,v,cut.due,known,true)
   THEN CONTINUE; END IF;
   key_:='c2-forecast:'||gen_random_uuid();
   PERFORM public.c2_publish(lo_id_,cut.due,known,known,origin.enterer_identity_id,origin.authority_basis_id,
     origin.authority_decision_reference,'c2-forecast:'||cut.first_source,key_,encode(sha256(convert_to(key_,'UTF8')),'hex'));
 END LOOP;
END """

# public.c2_guard_finding()
# Its landed text: 0055_c2_coverage.py (found there verbatim). Time calls moved: 1 statement, 3 wall.
C2_GUARD_FINDING_LANDED = r"""
DECLARE reason text; source uuid;
BEGIN
 IF NEW.method_reference LIKE 'S:finding:1|%' OR NEW.method_reference LIKE 'S:instrument:1|%' THEN
    NEW.recorded_at:=statement_timestamp();
 END IF;
 IF NEW.method_reference LIKE 'S:finding:1|%' THEN
    IF NEW.result NOT IN ('QUALIFIED','REFUSED') OR NEW.effective_at>NEW.received_at OR NEW.received_at>clock_timestamp()
       OR NEW.determination_type NOT IN ('EXECUTION_ELIGIBILITY_QUALIFICATION','UNAVAILABILITY_DETERMINATION','INDEPENDENCE_DETERMINATION') THEN
       RAISE EXCEPTION 'S finding has an invalid type, result or time' USING ERRCODE='23514'; END IF;
    source:=split_part(NEW.method_reference,'|',5)::uuid;
    PERFORM id FROM public.core_authoritybasis WHERE basis_uuid IN(source,split_part(NEW.method_reference,'|',3)::uuid)
       ORDER BY id FOR UPDATE;
    reason:=coalesce(public.c2_finding_problem(NEW,source,NEW.effective_at,NEW.received_at),
                     public.c2_finding_problem(NEW,source,clock_timestamp(),clock_timestamp()));
    IF reason IS NOT NULL THEN RAISE EXCEPTION 'S finding refused: %',reason USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END """
C2_GUARD_FINDING_CLOCK = r"""
DECLARE reason text; source uuid;
BEGIN
 IF NEW.method_reference LIKE 'S:finding:1|%' OR NEW.method_reference LIKE 'S:instrument:1|%' THEN
    NEW.recorded_at:=public.intevia_clock_statement();
 END IF;
 IF NEW.method_reference LIKE 'S:finding:1|%' THEN
    IF NEW.result NOT IN ('QUALIFIED','REFUSED') OR NEW.effective_at>NEW.received_at OR NEW.received_at>public.intevia_clock_instant()
       OR NEW.determination_type NOT IN ('EXECUTION_ELIGIBILITY_QUALIFICATION','UNAVAILABILITY_DETERMINATION','INDEPENDENCE_DETERMINATION') THEN
       RAISE EXCEPTION 'S finding has an invalid type, result or time' USING ERRCODE='23514'; END IF;
    source:=split_part(NEW.method_reference,'|',5)::uuid;
    PERFORM id FROM public.core_authoritybasis WHERE basis_uuid IN(source,split_part(NEW.method_reference,'|',3)::uuid)
       ORDER BY id FOR UPDATE;
    reason:=coalesce(public.c2_finding_problem(NEW,source,NEW.effective_at,NEW.received_at),
                     public.c2_finding_problem(NEW,source,public.intevia_clock_instant(),public.intevia_clock_instant()));
    IF reason IS NOT NULL THEN RAISE EXCEPTION 'S finding refused: %',reason USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END """

# public.c2_guard_volunteer()
# Its landed text: 0055_c2_coverage.py (found there verbatim). Time calls moved: 1 wall.
C2_GUARD_VOLUNTEER_LANDED = r"""
DECLARE m public.core_organismmembership; role public.core_organismroledefinition; assignment public.core_contextualroleassignment;
        original public.core_livingorganismevent; current_ public.core_livingorganismevent; now_ timestamptz:=clock_timestamp();
BEGIN
 IF NEW.action NOT IN ('PROPOSE','ASSIGN') THEN RETURN NULL; END IF;
 SELECT * INTO assignment FROM public.core_contextualroleassignment WHERE id=NEW.assignment_id;
 SELECT * INTO m FROM public.core_organismmembership WHERE id=assignment.membership_id;
 SELECT * INTO role FROM public.core_organismroledefinition WHERE id=assignment.role_definition_id;
 SELECT * INTO original FROM public.c2_phase_at(m.living_organism_id,NEW.effective_at,NEW.received_at);
 SELECT * INTO current_ FROM public.c2_phase_at(m.living_organism_id,NEW.recorded_at,NEW.recorded_at);
 IF coalesce(original.action,'') NOT IN ('WITHOUT_COORDINATOR','K2_DEACTIVATE','K2_DISSOLVE') AND
    coalesce(current_.action,'') NOT IN ('WITHOUT_COORDINATOR','K2_DEACTIVATE','K2_DISSOLVE') THEN RETURN NULL; END IF;
 IF original.event_uuid IS DISTINCT FROM current_.event_uuid OR coalesce(original.action,'') NOT IN ('WITHOUT_COORDINATOR','K2_DEACTIVATE')
    OR NEW.effective_at>=original.effective_until OR now_>=original.effective_until OR
    role.code<>'COORDINATOR' OR role.scope<>'LIVING_ORGANISM' OR NEW.enterer_identity_id<>m.identity_id OR
    public.s015_fold_effective_state('core_organismmembership',m.id,NEW.effective_at,NEW.received_at) IS DISTINCT FROM 'ACTIVE'
    OR public.s015_fold_effective_state('core_organismmembership',m.id,now_,now_) IS DISTINCT FROM 'ACTIVE'
    OR NOT EXISTS(SELECT FROM public.core_authoritydecision d WHERE d.decision_reference=NEW.authority_decision_reference
       AND d.outcome='QUALIFIED' AND d.action='TRANSITION_CONTEXTUAL_ROLE' AND d.actor_id=m.identity_id
       AND d.target->>'k2_episode'=CASE WHEN original.action='WITHOUT_COORDINATOR' THEN original.event_uuid::text
           ELSE substring(original.evidence_reference FROM 4) END AND d.target->>'event_action'=NEW.action
       AND d.effective_at=NEW.effective_at AND d.evaluated_at=NEW.received_at
       AND ((NEW.action='PROPOSE' AND d.target->>'membership_uuid'=m.membership_uuid::text
             AND d.target->>'role_definition_uuid'=role.role_uuid::text)
         OR (NEW.action='ASSIGN' AND d.target->>'assignment_uuid'=assignment.assignment_uuid::text))) THEN
    RAISE EXCEPTION 'Coordinator volunteer evidence does not match this member and current recovery window' USING ERRCODE='23514'; END IF;
 IF NEW.action='ASSIGN' AND NOT EXISTS(SELECT FROM public.core_livingorganismevent e
    JOIN public.core_coverageassessment assessment ON e.evidence_reference='coverage-assessment:'||assessment.event_uuid
    WHERE assessment.evidence_reference='office:'||NEW.event_uuid AND e.living_organism_id=m.living_organism_id AND e.action='K2_RESTORED' AND e.effective_at=NEW.effective_at
    AND e.authority_decision_reference=NEW.authority_decision_reference AND e.xmin=pg_current_xact_id()::xid) THEN
    RAISE EXCEPTION 'A recovery appointment requires a fresh assessment and restoration event' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END """
C2_GUARD_VOLUNTEER_CLOCK = r"""
DECLARE m public.core_organismmembership; role public.core_organismroledefinition; assignment public.core_contextualroleassignment;
        original public.core_livingorganismevent; current_ public.core_livingorganismevent; now_ timestamptz:=public.intevia_clock_instant();
BEGIN
 IF NEW.action NOT IN ('PROPOSE','ASSIGN') THEN RETURN NULL; END IF;
 SELECT * INTO assignment FROM public.core_contextualroleassignment WHERE id=NEW.assignment_id;
 SELECT * INTO m FROM public.core_organismmembership WHERE id=assignment.membership_id;
 SELECT * INTO role FROM public.core_organismroledefinition WHERE id=assignment.role_definition_id;
 SELECT * INTO original FROM public.c2_phase_at(m.living_organism_id,NEW.effective_at,NEW.received_at);
 SELECT * INTO current_ FROM public.c2_phase_at(m.living_organism_id,NEW.recorded_at,NEW.recorded_at);
 IF coalesce(original.action,'') NOT IN ('WITHOUT_COORDINATOR','K2_DEACTIVATE','K2_DISSOLVE') AND
    coalesce(current_.action,'') NOT IN ('WITHOUT_COORDINATOR','K2_DEACTIVATE','K2_DISSOLVE') THEN RETURN NULL; END IF;
 IF original.event_uuid IS DISTINCT FROM current_.event_uuid OR coalesce(original.action,'') NOT IN ('WITHOUT_COORDINATOR','K2_DEACTIVATE')
    OR NEW.effective_at>=original.effective_until OR now_>=original.effective_until OR
    role.code<>'COORDINATOR' OR role.scope<>'LIVING_ORGANISM' OR NEW.enterer_identity_id<>m.identity_id OR
    public.s015_fold_effective_state('core_organismmembership',m.id,NEW.effective_at,NEW.received_at) IS DISTINCT FROM 'ACTIVE'
    OR public.s015_fold_effective_state('core_organismmembership',m.id,now_,now_) IS DISTINCT FROM 'ACTIVE'
    OR NOT EXISTS(SELECT FROM public.core_authoritydecision d WHERE d.decision_reference=NEW.authority_decision_reference
       AND d.outcome='QUALIFIED' AND d.action='TRANSITION_CONTEXTUAL_ROLE' AND d.actor_id=m.identity_id
       AND d.target->>'k2_episode'=CASE WHEN original.action='WITHOUT_COORDINATOR' THEN original.event_uuid::text
           ELSE substring(original.evidence_reference FROM 4) END AND d.target->>'event_action'=NEW.action
       AND d.effective_at=NEW.effective_at AND d.evaluated_at=NEW.received_at
       AND ((NEW.action='PROPOSE' AND d.target->>'membership_uuid'=m.membership_uuid::text
             AND d.target->>'role_definition_uuid'=role.role_uuid::text)
         OR (NEW.action='ASSIGN' AND d.target->>'assignment_uuid'=assignment.assignment_uuid::text))) THEN
    RAISE EXCEPTION 'Coordinator volunteer evidence does not match this member and current recovery window' USING ERRCODE='23514'; END IF;
 IF NEW.action='ASSIGN' AND NOT EXISTS(SELECT FROM public.core_livingorganismevent e
    JOIN public.core_coverageassessment assessment ON e.evidence_reference='coverage-assessment:'||assessment.event_uuid
    WHERE assessment.evidence_reference='office:'||NEW.event_uuid AND e.living_organism_id=m.living_organism_id AND e.action='K2_RESTORED' AND e.effective_at=NEW.effective_at
    AND e.authority_decision_reference=NEW.authority_decision_reference AND e.xmin=pg_current_xact_id()::xid) THEN
    RAISE EXCEPTION 'A recovery appointment requires a fresh assessment and restoration event' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END """

# public.c2_roster_stamp()
# Its landed text: 0055_c2_coverage.py (found there verbatim). Time calls moved: 1 statement.
C2_ROSTER_STAMP_LANDED = r"""
BEGIN NEW.c2_recorded_at:=statement_timestamp(); RETURN NEW; END """
C2_ROSTER_STAMP_CLOCK = r"""
BEGIN NEW.c2_recorded_at:=public.intevia_clock_statement(); RETURN NEW; END """

# public.c3_case_finding_guard()
# Its landed text: 0056_c3_authority_lifecycle.py (found there verbatim). Time calls moved: 5 statement.
C3_CASE_FINDING_GUARD_LANDED = r"""
DECLARE app public.core_application; claim public.core_csaassignment; op text; base public.core_authoritybasis;
        person bigint; purpose text; kind text; seat public.core_contextualroleassignment;
        reviewer_basis bigint; fingerprint text; source uuid; lo uuid;
BEGIN
 IF NEW.method_reference NOT LIKE 'S:finding:1|%' THEN RETURN NEW; END IF;
 SELECT * INTO app FROM public.core_application WHERE application_uuid=split_part(NEW.evidence_reference,':',3)::uuid FOR UPDATE;
 IF app.id IS NULL OR EXISTS(SELECT FROM public.core_applicationdecision WHERE application_id=app.id)
    OR EXISTS(SELECT FROM public.core_applicationwithdrawal WHERE application_id=app.id) THEN
    RAISE EXCEPTION 'C3 case finding requires an open application' USING ERRCODE='23514'; END IF;
 SELECT c.* INTO claim FROM public.core_applicationengagement e JOIN public.core_csaassignment c
 ON c.application_id=e.application_id AND c.assignment_uuid::text=e.engagement_reference
 WHERE e.application_id=app.id AND e.route='LO_CSA' AND NOT EXISTS (
 SELECT FROM public.core_csaassignmentend ending WHERE ending.assignment_id=c.id)
 ORDER BY e.engagement_ordinal DESC LIMIT 1;
 IF claim.id IS NULL OR NEW.safeguard_beneficiary_id IS DISTINCT FROM app.applicant_id THEN
    RAISE EXCEPTION 'C3 finding names no current claim or the wrong beneficiary' USING ERRCODE='23514'; END IF;
 SELECT organism_id INTO lo FROM public.core_livingorganism WHERE id=app.living_organism_id;
 op:=split_part(NEW.evidence_reference,':',2); person:=NEW.tested_actor_id;
 source:=split_part(NEW.method_reference,'|',5)::uuid;
 SELECT * INTO STRICT base FROM public.core_authoritybasis WHERE basis_uuid=source;
 IF op='REPLACEMENT' THEN
    SELECT a.* INTO seat FROM public.core_contextualroleassignment a
    JOIN public.core_organismmembership m ON m.id=a.membership_id AND m.identity_id=person
    WHERE m.living_organism_id=app.living_organism_id AND public.c3_seat(app.id,a.id,person,NEW.effective_at,NEW.received_at)
    AND public.c3_seat(app.id,a.id,person,statement_timestamp(),statement_timestamp())
    AND NEW.scope_fingerprint=public.c3_scope(public.c3_act(app.id,'REPLACE:'||claim.assignment_uuid||':'||a.assignment_uuid),
      source,app.applicant_id,'EXECUTION_ELIGIBILITY_QUALIFICATION',lo,app.application_uuid,person);
    IF seat.id IS NULL OR person IN (claim.actor_id,app.applicant_id) OR NEW.determiner_id=claim.actor_id
       OR public.c3_independent(base.id,public.c3_claim_basis(claim),NEW.received_at) IS NOT TRUE
       OR public.c3_independent(base.id,public.c3_claim_basis(claim),statement_timestamp()) IS NOT TRUE THEN
       RAISE EXCEPTION 'C3 replacement lacks its independent source and effective office' USING ERRCODE='23514'; END IF;
    purpose:='REPLACE:'||claim.assignment_uuid||':'||seat.assignment_uuid; kind:='EXECUTION_ELIGIBILITY_QUALIFICATION';
 ELSIF op='PROCEDURAL_REVIEW' THEN
    SELECT id INTO STRICT reviewer_basis FROM public.core_authoritybasis WHERE basis_uuid=split_part(NEW.method_reference,'|',3)::uuid;
    IF person IS DISTINCT FROM claim.actor_id OR NOT public.c3_independent(reviewer_basis,public.c3_claim_basis(claim),NEW.received_at)
       OR NOT public.c3_available(app.id,NEW.determiner_id,NEW.effective_at,NEW.received_at)
       OR NOT public.c3_available(app.id,NEW.determiner_id,statement_timestamp(),statement_timestamp()) THEN
       RAISE EXCEPTION 'C3 procedural reviewer cannot act on this claim' USING ERRCODE='23514'; END IF;
    purpose:='REVIEW:'||claim.assignment_uuid; kind:='INDEPENDENCE_DETERMINATION';
 ELSIF op IN ('RECUSE','UNAVAILABLE','AVAILABLE') THEN
    IF (op='AVAILABLE') IS DISTINCT FROM (NEW.result='REFUSED') THEN
       RAISE EXCEPTION 'C3 availability outcome does not match the recorded act' USING ERRCODE='23514'; END IF;
    purpose:='STEP_ASIDE'; kind:='UNAVAILABILITY_DETERMINATION';
 ELSE RAISE EXCEPTION 'C3 case finding has no recognised purpose' USING ERRCODE='23514'; END IF;
 fingerprint:=public.c3_scope(public.c3_act(app.id,purpose),source,app.applicant_id,kind,lo,app.application_uuid,person);
 IF NEW.scope_fingerprint IS DISTINCT FROM fingerprint OR NEW.determination_type<>kind THEN
    RAISE EXCEPTION 'C3 finding scope differs from the case' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END """
C3_CASE_FINDING_GUARD_CLOCK = r"""
DECLARE app public.core_application; claim public.core_csaassignment; op text; base public.core_authoritybasis;
        person bigint; purpose text; kind text; seat public.core_contextualroleassignment;
        reviewer_basis bigint; fingerprint text; source uuid; lo uuid;
BEGIN
 IF NEW.method_reference NOT LIKE 'S:finding:1|%' THEN RETURN NEW; END IF;
 SELECT * INTO app FROM public.core_application WHERE application_uuid=split_part(NEW.evidence_reference,':',3)::uuid FOR UPDATE;
 IF app.id IS NULL OR EXISTS(SELECT FROM public.core_applicationdecision WHERE application_id=app.id)
    OR EXISTS(SELECT FROM public.core_applicationwithdrawal WHERE application_id=app.id) THEN
    RAISE EXCEPTION 'C3 case finding requires an open application' USING ERRCODE='23514'; END IF;
 SELECT c.* INTO claim FROM public.core_applicationengagement e JOIN public.core_csaassignment c
 ON c.application_id=e.application_id AND c.assignment_uuid::text=e.engagement_reference
 WHERE e.application_id=app.id AND e.route='LO_CSA' AND NOT EXISTS (
 SELECT FROM public.core_csaassignmentend ending WHERE ending.assignment_id=c.id)
 ORDER BY e.engagement_ordinal DESC LIMIT 1;
 IF claim.id IS NULL OR NEW.safeguard_beneficiary_id IS DISTINCT FROM app.applicant_id THEN
    RAISE EXCEPTION 'C3 finding names no current claim or the wrong beneficiary' USING ERRCODE='23514'; END IF;
 SELECT organism_id INTO lo FROM public.core_livingorganism WHERE id=app.living_organism_id;
 op:=split_part(NEW.evidence_reference,':',2); person:=NEW.tested_actor_id;
 source:=split_part(NEW.method_reference,'|',5)::uuid;
 SELECT * INTO STRICT base FROM public.core_authoritybasis WHERE basis_uuid=source;
 IF op='REPLACEMENT' THEN
    SELECT a.* INTO seat FROM public.core_contextualroleassignment a
    JOIN public.core_organismmembership m ON m.id=a.membership_id AND m.identity_id=person
    WHERE m.living_organism_id=app.living_organism_id AND public.c3_seat(app.id,a.id,person,NEW.effective_at,NEW.received_at)
    AND public.c3_seat(app.id,a.id,person,public.intevia_clock_statement(),public.intevia_clock_statement())
    AND NEW.scope_fingerprint=public.c3_scope(public.c3_act(app.id,'REPLACE:'||claim.assignment_uuid||':'||a.assignment_uuid),
      source,app.applicant_id,'EXECUTION_ELIGIBILITY_QUALIFICATION',lo,app.application_uuid,person);
    IF seat.id IS NULL OR person IN (claim.actor_id,app.applicant_id) OR NEW.determiner_id=claim.actor_id
       OR public.c3_independent(base.id,public.c3_claim_basis(claim),NEW.received_at) IS NOT TRUE
       OR public.c3_independent(base.id,public.c3_claim_basis(claim),public.intevia_clock_statement()) IS NOT TRUE THEN
       RAISE EXCEPTION 'C3 replacement lacks its independent source and effective office' USING ERRCODE='23514'; END IF;
    purpose:='REPLACE:'||claim.assignment_uuid||':'||seat.assignment_uuid; kind:='EXECUTION_ELIGIBILITY_QUALIFICATION';
 ELSIF op='PROCEDURAL_REVIEW' THEN
    SELECT id INTO STRICT reviewer_basis FROM public.core_authoritybasis WHERE basis_uuid=split_part(NEW.method_reference,'|',3)::uuid;
    IF person IS DISTINCT FROM claim.actor_id OR NOT public.c3_independent(reviewer_basis,public.c3_claim_basis(claim),NEW.received_at)
       OR NOT public.c3_available(app.id,NEW.determiner_id,NEW.effective_at,NEW.received_at)
       OR NOT public.c3_available(app.id,NEW.determiner_id,public.intevia_clock_statement(),public.intevia_clock_statement()) THEN
       RAISE EXCEPTION 'C3 procedural reviewer cannot act on this claim' USING ERRCODE='23514'; END IF;
    purpose:='REVIEW:'||claim.assignment_uuid; kind:='INDEPENDENCE_DETERMINATION';
 ELSIF op IN ('RECUSE','UNAVAILABLE','AVAILABLE') THEN
    IF (op='AVAILABLE') IS DISTINCT FROM (NEW.result='REFUSED') THEN
       RAISE EXCEPTION 'C3 availability outcome does not match the recorded act' USING ERRCODE='23514'; END IF;
    purpose:='STEP_ASIDE'; kind:='UNAVAILABILITY_DETERMINATION';
 ELSE RAISE EXCEPTION 'C3 case finding has no recognised purpose' USING ERRCODE='23514'; END IF;
 fingerprint:=public.c3_scope(public.c3_act(app.id,purpose),source,app.applicant_id,kind,lo,app.application_uuid,person);
 IF NEW.scope_fingerprint IS DISTINCT FROM fingerprint OR NEW.determination_type<>kind THEN
    RAISE EXCEPTION 'C3 finding scope differs from the case' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END """

# public.c3_successor_check()
# Its landed text: 0056_c3_authority_lifecycle.py (found there verbatim). Time calls moved: 1 statement.
C3_SUCCESSOR_CHECK_LANDED = r"""
DECLARE hash text;
BEGIN
 IF NEW.superseded_basis_id IS NULL THEN RETURN NEW; END IF;
 NEW.recorded_at:=statement_timestamp();
 SELECT public.c2_target(format(
 '{"authority_class":%s,"basis_uuid":"%s","evidence_reference":%s,"instrument_reference":%s,"principal_uuid":"%s","root_uuid":"%s","scope_fingerprint":%s}',
 to_json(NEW.authority_class),NEW.basis_uuid,to_json(NEW.evidence_reference),to_json(NEW.instrument_reference),
 p.principal_uuid,r.principal_uuid,to_json(NEW.scope_fingerprint))) INTO hash
 FROM public.core_authorityprincipal p,public.core_authorityprincipal r WHERE p.id=NEW.authority_principal_id AND r.id=NEW.derivation_root_principal_id;
 IF NOT EXISTS(SELECT FROM public.core_authorityinvalidationevent e WHERE e.basis_id=NEW.superseded_basis_id AND e.kind='SUPERSESSION'
 AND e.actor_id=NEW.issuer_id AND e.effective_at=NEW.effective_at AND e.effective_at=NEW.issued_at AND e.received_at=NEW.received_at
 AND e.request_reference LIKE 'C3:%' AND split_part(e.request_reference,':',4)=hash)
 THEN RAISE EXCEPTION 'C3 successor lacks the exact authorised supersession' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END """
C3_SUCCESSOR_CHECK_CLOCK = r"""
DECLARE hash text;
BEGIN
 IF NEW.superseded_basis_id IS NULL THEN RETURN NEW; END IF;
 NEW.recorded_at:=public.intevia_clock_statement();
 SELECT public.c2_target(format(
 '{"authority_class":%s,"basis_uuid":"%s","evidence_reference":%s,"instrument_reference":%s,"principal_uuid":"%s","root_uuid":"%s","scope_fingerprint":%s}',
 to_json(NEW.authority_class),NEW.basis_uuid,to_json(NEW.evidence_reference),to_json(NEW.instrument_reference),
 p.principal_uuid,r.principal_uuid,to_json(NEW.scope_fingerprint))) INTO hash
 FROM public.core_authorityprincipal p,public.core_authorityprincipal r WHERE p.id=NEW.authority_principal_id AND r.id=NEW.derivation_root_principal_id;
 IF NOT EXISTS(SELECT FROM public.core_authorityinvalidationevent e WHERE e.basis_id=NEW.superseded_basis_id AND e.kind='SUPERSESSION'
 AND e.actor_id=NEW.issuer_id AND e.effective_at=NEW.effective_at AND e.effective_at=NEW.issued_at AND e.received_at=NEW.received_at
 AND e.request_reference LIKE 'C3:%' AND split_part(e.request_reference,':',4)=hash)
 THEN RAISE EXCEPTION 'C3 successor lacks the exact authorised supersession' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END """

# public.c3_validate_basis_event()
# Its landed text: 0056_c3_authority_lifecycle.py (found there verbatim). Time calls moved: 4 statement.
C3_VALIDATE_BASIS_EVENT_LANDED = r"""
DECLARE ref text[]; grant_row public.core_governeddetermination; subject public.core_authoritybasis;
        acting public.core_authoritybasis; probe public.core_governeddetermination; purpose text; reason text;
BEGIN
 IF NEW.kind NOT IN ('SUPERSESSION','INVALIDATION','RESTORATION') THEN RETURN NEW; END IF;
 SELECT * INTO STRICT subject FROM public.core_authoritybasis WHERE id=NEW.basis_id FOR UPDATE;
 IF EXISTS(SELECT FROM public.core_authorityinvalidationevent WHERE basis_id=subject.id
           AND kind IN ('SUPERSESSION','INVALIDATION') AND request_reference LIKE 'C3:%') THEN
    RAISE EXCEPTION 'C3 the whole instrument has already ended' USING ERRCODE='23514'; END IF;
 IF NEW.kind='RESTORATION' THEN RETURN NEW; END IF;
 ref:=string_to_array(NEW.request_reference,':');
 IF NEW.evidence_reference IS NULL OR NEW.evidence_reference NOT LIKE 'C3:%' OR cardinality(ref)<>4 OR ref[1]<>'C3' OR NEW.actor_state<>'PARTY' OR NEW.actor_id IS DISTINCT FROM NEW.enterer_identity_id
 OR NEW.effective_at<>NEW.occurred_at OR NEW.received_at<>NEW.effective_at OR NEW.received_at>statement_timestamp()
 OR NOT EXISTS(SELECT FROM public.core_livingorganism WHERE organism_id=ref[2]::uuid) THEN
    RAISE EXCEPTION 'C3 whole-basis act lacks its exact authority or coordinates' USING ERRCODE='23514'; END IF;
 IF NEW.kind='SUPERSESSION' AND ref[4]~'^[a-f0-9]{64}$' AND NEW.action='SUPERSEDE' AND NEW.resulting_state='SUPERSEDED' THEN
    purpose:='C3:SUPERSEDE:'||ref[4];
 ELSIF NEW.kind='INVALIDATION' AND ref[4]='END' AND NEW.action='INVALIDATE' AND NEW.resulting_state='INVALIDATED' THEN
    purpose:='C3:END_WHOLE_BASIS';
 ELSE RAISE EXCEPTION 'C3 invalid basis-ending act' USING ERRCODE='23514'; END IF;
 SELECT * INTO STRICT acting FROM public.core_authoritybasis WHERE id=NEW.authority_basis_id;
 SELECT * INTO grant_row FROM public.core_governeddetermination WHERE determination_uuid=ref[3]::uuid;
 probe.scope_fingerprint:=public.c3_scope(purpose,subject.basis_uuid,subject.issuer_id,
 'EXECUTION_ELIGIBILITY_QUALIFICATION',ref[2]::uuid,subject.basis_uuid,subject.issuer_id);
 probe.result:='QUALIFIED'; probe.determination_type:='EXECUTION_ELIGIBILITY_QUALIFICATION';
 probe.effective_at:=NEW.effective_at; probe.occurred_at:=NEW.occurred_at;
 probe.received_at:=NEW.received_at; probe.recorded_at:=statement_timestamp();
 probe.determiner_id:=NEW.actor_id; probe.tested_actor_id:=subject.issuer_id; probe.safeguard_beneficiary_id:=subject.issuer_id;
 probe.independence_state:='INDEPENDENT'; probe.relied_upon_authority_principal_id:=acting.authority_principal_id;
 probe.evidence_reference:=NEW.evidence_reference;
 probe.method_reference:='S:finding:1|'||ref[3]||'|'||acting.basis_uuid||'|'||split_part(grant_row.method_reference,'|',2)||'|'||subject.basis_uuid;
 IF grant_row.id IS NULL THEN RAISE EXCEPTION 'C3 missing whole-instrument mandate' USING ERRCODE='23514'; END IF;
 reason:=coalesce(public.c2_finding_problem(probe,subject.basis_uuid,NEW.effective_at,NEW.received_at),
                  public.c2_finding_problem(probe,subject.basis_uuid,statement_timestamp(),statement_timestamp()));
 IF reason IS NOT NULL THEN RAISE EXCEPTION 'C3 whole-instrument authority refused: %',reason USING ERRCODE='23514'; END IF;
 RETURN NEW;
END """
C3_VALIDATE_BASIS_EVENT_CLOCK = r"""
DECLARE ref text[]; grant_row public.core_governeddetermination; subject public.core_authoritybasis;
        acting public.core_authoritybasis; probe public.core_governeddetermination; purpose text; reason text;
BEGIN
 IF NEW.kind NOT IN ('SUPERSESSION','INVALIDATION','RESTORATION') THEN RETURN NEW; END IF;
 SELECT * INTO STRICT subject FROM public.core_authoritybasis WHERE id=NEW.basis_id FOR UPDATE;
 IF EXISTS(SELECT FROM public.core_authorityinvalidationevent WHERE basis_id=subject.id
           AND kind IN ('SUPERSESSION','INVALIDATION') AND request_reference LIKE 'C3:%') THEN
    RAISE EXCEPTION 'C3 the whole instrument has already ended' USING ERRCODE='23514'; END IF;
 IF NEW.kind='RESTORATION' THEN RETURN NEW; END IF;
 ref:=string_to_array(NEW.request_reference,':');
 IF NEW.evidence_reference IS NULL OR NEW.evidence_reference NOT LIKE 'C3:%' OR cardinality(ref)<>4 OR ref[1]<>'C3' OR NEW.actor_state<>'PARTY' OR NEW.actor_id IS DISTINCT FROM NEW.enterer_identity_id
 OR NEW.effective_at<>NEW.occurred_at OR NEW.received_at<>NEW.effective_at OR NEW.received_at>public.intevia_clock_statement()
 OR NOT EXISTS(SELECT FROM public.core_livingorganism WHERE organism_id=ref[2]::uuid) THEN
    RAISE EXCEPTION 'C3 whole-basis act lacks its exact authority or coordinates' USING ERRCODE='23514'; END IF;
 IF NEW.kind='SUPERSESSION' AND ref[4]~'^[a-f0-9]{64}$' AND NEW.action='SUPERSEDE' AND NEW.resulting_state='SUPERSEDED' THEN
    purpose:='C3:SUPERSEDE:'||ref[4];
 ELSIF NEW.kind='INVALIDATION' AND ref[4]='END' AND NEW.action='INVALIDATE' AND NEW.resulting_state='INVALIDATED' THEN
    purpose:='C3:END_WHOLE_BASIS';
 ELSE RAISE EXCEPTION 'C3 invalid basis-ending act' USING ERRCODE='23514'; END IF;
 SELECT * INTO STRICT acting FROM public.core_authoritybasis WHERE id=NEW.authority_basis_id;
 SELECT * INTO grant_row FROM public.core_governeddetermination WHERE determination_uuid=ref[3]::uuid;
 probe.scope_fingerprint:=public.c3_scope(purpose,subject.basis_uuid,subject.issuer_id,
 'EXECUTION_ELIGIBILITY_QUALIFICATION',ref[2]::uuid,subject.basis_uuid,subject.issuer_id);
 probe.result:='QUALIFIED'; probe.determination_type:='EXECUTION_ELIGIBILITY_QUALIFICATION';
 probe.effective_at:=NEW.effective_at; probe.occurred_at:=NEW.occurred_at;
 probe.received_at:=NEW.received_at; probe.recorded_at:=public.intevia_clock_statement();
 probe.determiner_id:=NEW.actor_id; probe.tested_actor_id:=subject.issuer_id; probe.safeguard_beneficiary_id:=subject.issuer_id;
 probe.independence_state:='INDEPENDENT'; probe.relied_upon_authority_principal_id:=acting.authority_principal_id;
 probe.evidence_reference:=NEW.evidence_reference;
 probe.method_reference:='S:finding:1|'||ref[3]||'|'||acting.basis_uuid||'|'||split_part(grant_row.method_reference,'|',2)||'|'||subject.basis_uuid;
 IF grant_row.id IS NULL THEN RAISE EXCEPTION 'C3 missing whole-instrument mandate' USING ERRCODE='23514'; END IF;
 reason:=coalesce(public.c2_finding_problem(probe,subject.basis_uuid,NEW.effective_at,NEW.received_at),
                  public.c2_finding_problem(probe,subject.basis_uuid,public.intevia_clock_statement(),public.intevia_clock_statement()));
 IF reason IS NOT NULL THEN RAISE EXCEPTION 'C3 whole-instrument authority refused: %',reason USING ERRCODE='23514'; END IF;
 RETURN NEW;
END """

# public.s3_claim_guard()
# Its landed text: 0056_c3_authority_lifecycle.py (assembled by that migration). Time calls moved: 3 wall.
S3_CLAIM_GUARD_LANDED = r"""
DECLARE previous public.core_csaassignment%ROWTYPE;
BEGIN
    PERFORM public.s3_lock_application(NEW.application_id);
    NEW.recorded_at := clock_timestamp();
    IF EXISTS (SELECT 1 FROM public.core_applicationdecision WHERE application_id = NEW.application_id)
       OR EXISTS (SELECT 1 FROM public.core_applicationwithdrawal WHERE application_id = NEW.application_id) THEN
        RAISE EXCEPTION 'S3 claim after terminal row' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.replaces_id IS NOT NULL THEN
        SELECT * INTO previous FROM public.core_csaassignment WHERE id = NEW.replaces_id AND application_id = NEW.application_id;
        IF NOT FOUND OR NOT EXISTS (
            SELECT 1 FROM public.core_csaassignmentend ending WHERE ending.assignment_id = previous.id
              AND ending.effective_at <= NEW.effective_at AND ending.recorded_at <= NEW.recorded_at
        ) THEN
            RAISE EXCEPTION 'S3 replacement requires an ended assignment on this application' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.core_csaassignment claim
        WHERE claim.application_id = NEW.application_id
                    AND claim.effective_at <= NEW.effective_at
                    AND NOT EXISTS (
                            SELECT 1 FROM public.core_csaassignmentend ending WHERE ending.assignment_id = claim.id
                                AND ending.effective_at <= NEW.effective_at AND ending.recorded_at <= NEW.recorded_at
                    )
    ) THEN
        RAISE EXCEPTION 'S3 single effective claim' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.core_organismmembership member
        JOIN public.core_application application ON application.id = NEW.application_id
        JOIN public.core_contextualroleassignment office ON office.id = NEW.assignee_office_id
        JOIN public.core_organismroledefinition definition ON definition.id = office.role_definition_id
        WHERE member.id = NEW.assignee_membership_id AND member.living_organism_id = application.living_organism_id
          AND office.membership_id = member.id AND member.identity_id = NEW.actor_id
          AND definition.code = 'COORDINATOR' AND definition.scope = 'LIVING_ORGANISM'
          AND public.s015_fold_effective_state('core_contextualroleassignment', office.id, NEW.effective_at, NEW.recorded_at) = 'ACTIVE'
    ) THEN
        RAISE EXCEPTION 'S3 claim requires an effective self-assigned Coordinator office' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    PERFORM public.c3_lock_sources(NEW.application_id,NEW.assignee_office_id);
    IF NEW.replaces_id IS NULL AND NEW.lifecycle_finding_id IS NOT NULL
        OR public.c3_act_problem(NEW,NEW.effective_at,NEW.received_at) IS NOT NULL
        OR public.c3_act_problem(NEW,clock_timestamp(),clock_timestamp()) IS NOT NULL THEN
        RAISE EXCEPTION 'C3 assignment lacks current case authority' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END """
S3_CLAIM_GUARD_CLOCK = r"""
DECLARE previous public.core_csaassignment%ROWTYPE;
BEGIN
    PERFORM public.s3_lock_application(NEW.application_id);
    NEW.recorded_at := public.intevia_clock_instant();
    IF EXISTS (SELECT 1 FROM public.core_applicationdecision WHERE application_id = NEW.application_id)
       OR EXISTS (SELECT 1 FROM public.core_applicationwithdrawal WHERE application_id = NEW.application_id) THEN
        RAISE EXCEPTION 'S3 claim after terminal row' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.replaces_id IS NOT NULL THEN
        SELECT * INTO previous FROM public.core_csaassignment WHERE id = NEW.replaces_id AND application_id = NEW.application_id;
        IF NOT FOUND OR NOT EXISTS (
            SELECT 1 FROM public.core_csaassignmentend ending WHERE ending.assignment_id = previous.id
              AND ending.effective_at <= NEW.effective_at AND ending.recorded_at <= NEW.recorded_at
        ) THEN
            RAISE EXCEPTION 'S3 replacement requires an ended assignment on this application' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.core_csaassignment claim
        WHERE claim.application_id = NEW.application_id
                    AND claim.effective_at <= NEW.effective_at
                    AND NOT EXISTS (
                            SELECT 1 FROM public.core_csaassignmentend ending WHERE ending.assignment_id = claim.id
                                AND ending.effective_at <= NEW.effective_at AND ending.recorded_at <= NEW.recorded_at
                    )
    ) THEN
        RAISE EXCEPTION 'S3 single effective claim' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.core_organismmembership member
        JOIN public.core_application application ON application.id = NEW.application_id
        JOIN public.core_contextualroleassignment office ON office.id = NEW.assignee_office_id
        JOIN public.core_organismroledefinition definition ON definition.id = office.role_definition_id
        WHERE member.id = NEW.assignee_membership_id AND member.living_organism_id = application.living_organism_id
          AND office.membership_id = member.id AND member.identity_id = NEW.actor_id
          AND definition.code = 'COORDINATOR' AND definition.scope = 'LIVING_ORGANISM'
          AND public.s015_fold_effective_state('core_contextualroleassignment', office.id, NEW.effective_at, NEW.recorded_at) = 'ACTIVE'
    ) THEN
        RAISE EXCEPTION 'S3 claim requires an effective self-assigned Coordinator office' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    PERFORM public.c3_lock_sources(NEW.application_id,NEW.assignee_office_id);
    IF NEW.replaces_id IS NULL AND NEW.lifecycle_finding_id IS NOT NULL
        OR public.c3_act_problem(NEW,NEW.effective_at,NEW.received_at) IS NOT NULL
        OR public.c3_act_problem(NEW,public.intevia_clock_instant(),public.intevia_clock_instant()) IS NOT NULL THEN
        RAISE EXCEPTION 'C3 assignment lacks current case authority' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END """

# public.s3_decision_guard_a26()
# Its landed text: 0056_c3_authority_lifecycle.py (assembled by that migration). Time calls moved: 2 wall.
S3_DECISION_GUARD_A26_LANDED = r"""
DECLARE engagement public.core_applicationengagement%ROWTYPE;
BEGIN
    PERFORM public.c3_lock_sources(NEW.application_id,NEW.qualifying_office_id);
    SELECT * INTO engagement FROM public.core_applicationengagement
    WHERE application_id = NEW.application_id AND effective_at <= NEW.effective_at AND recorded_at <= NEW.recorded_at
    ORDER BY engagement_ordinal DESC LIMIT 1;
    IF NOT FOUND OR NEW.outcome NOT IN ('ACCEPT','DECLINE') OR NEW.route <> engagement.route
       OR NEW.deciding_party_id <> engagement.deciding_party_id OR engagement.settings_basis IS NULL
       OR length(engagement.settings_basis) = 0 THEN
        RAISE EXCEPTION 'S3 decision lacks current frozen engagement' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.route IN ('LO_CQ','CIRCLE_CQ') THEN
        RAISE EXCEPTION 'S3 CQ result owner unavailable' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
     IF NEW.route <> 'LO_CSA'
         OR NOT (CASE WHEN engagement.engagement_ordinal=1 THEN public.s3fu_application_settings_basis_valid(
             engagement.settings_basis,engagement.deciding_party_id,engagement.effective_at,engagement.recorded_at)
             ELSE public.c3_pin_valid(engagement.application_id,engagement.settings_basis,engagement.deciding_party_id) END)
         OR EXISTS(SELECT FROM public.core_csaassignment c WHERE c.id=NEW.assignment_reference_id AND
             (public.c3_act_problem(c,NEW.effective_at,NEW.received_at) IS NOT NULL OR
              public.c3_act_problem(c,clock_timestamp(),clock_timestamp()) IS NOT NULL))
         OR NEW.assignment_reference_id IS NULL
       OR NEW.responsible_capacity <> 'COORDINATOR'
       OR NEW.responsible_coordinator_id IS DISTINCT FROM NEW.enterer_id
       OR NEW.qualifying_office_id IS NULL
       OR NOT EXISTS (
          SELECT 1 FROM public.core_csaassignment claim
          JOIN public.core_organismmembership member ON member.id = claim.assignee_membership_id
          JOIN public.core_contextualroleassignment office ON office.id = claim.assignee_office_id
          JOIN public.core_organismroledefinition definition ON definition.id = office.role_definition_id
          WHERE claim.id = NEW.assignment_reference_id AND claim.application_id = NEW.application_id
            AND claim.assignment_uuid::text = engagement.engagement_reference
            AND member.identity_id = NEW.enterer_id AND member.living_organism_id = NEW.deciding_party_id
            AND office.id = NEW.qualifying_office_id AND office.membership_id = member.id
            AND definition.code = 'COORDINATOR' AND definition.scope = 'LIVING_ORGANISM'
            AND claim.effective_at <= NEW.effective_at AND claim.recorded_at <= NEW.recorded_at
            AND public.s015_fold_effective_state('core_contextualroleassignment', office.id, NEW.effective_at, NEW.recorded_at) = 'ACTIVE'
            AND public.s015_fold_effective_state('core_organismmembership', member.id, NEW.effective_at, NEW.recorded_at) = 'ACTIVE'
            AND NOT EXISTS (
                SELECT 1 FROM public.core_csaassignmentend ending
                WHERE ending.assignment_id = claim.id AND ending.effective_at <= NEW.effective_at
                  AND ending.recorded_at <= NEW.recorded_at
            )
            AND NOT EXISTS (
                SELECT 1 FROM public.core_contextualroleassignmentstateevent term
                WHERE term.assignment_id = office.id AND term.effective_until_state = 'FIXED'
                  AND term.effective_until <= NEW.effective_at AND term.recorded_at <= NEW.recorded_at
                  AND term.id = (
                      SELECT last_term.id FROM public.core_contextualroleassignmentstateevent last_term
                      WHERE last_term.assignment_id = office.id AND last_term.effective_at <= NEW.effective_at
                        AND last_term.recorded_at <= NEW.recorded_at
                      ORDER BY last_term.effective_at DESC, last_term.sequence DESC LIMIT 1
                  )
            )
       ) THEN
        RAISE EXCEPTION 'S3 CSA completion authority refused' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END """
S3_DECISION_GUARD_A26_CLOCK = r"""
DECLARE engagement public.core_applicationengagement%ROWTYPE;
BEGIN
    PERFORM public.c3_lock_sources(NEW.application_id,NEW.qualifying_office_id);
    SELECT * INTO engagement FROM public.core_applicationengagement
    WHERE application_id = NEW.application_id AND effective_at <= NEW.effective_at AND recorded_at <= NEW.recorded_at
    ORDER BY engagement_ordinal DESC LIMIT 1;
    IF NOT FOUND OR NEW.outcome NOT IN ('ACCEPT','DECLINE') OR NEW.route <> engagement.route
       OR NEW.deciding_party_id <> engagement.deciding_party_id OR engagement.settings_basis IS NULL
       OR length(engagement.settings_basis) = 0 THEN
        RAISE EXCEPTION 'S3 decision lacks current frozen engagement' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.route IN ('LO_CQ','CIRCLE_CQ') THEN
        RAISE EXCEPTION 'S3 CQ result owner unavailable' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
     IF NEW.route <> 'LO_CSA'
         OR NOT (CASE WHEN engagement.engagement_ordinal=1 THEN public.s3fu_application_settings_basis_valid(
             engagement.settings_basis,engagement.deciding_party_id,engagement.effective_at,engagement.recorded_at)
             ELSE public.c3_pin_valid(engagement.application_id,engagement.settings_basis,engagement.deciding_party_id) END)
         OR EXISTS(SELECT FROM public.core_csaassignment c WHERE c.id=NEW.assignment_reference_id AND
             (public.c3_act_problem(c,NEW.effective_at,NEW.received_at) IS NOT NULL OR
              public.c3_act_problem(c,public.intevia_clock_instant(),public.intevia_clock_instant()) IS NOT NULL))
         OR NEW.assignment_reference_id IS NULL
       OR NEW.responsible_capacity <> 'COORDINATOR'
       OR NEW.responsible_coordinator_id IS DISTINCT FROM NEW.enterer_id
       OR NEW.qualifying_office_id IS NULL
       OR NOT EXISTS (
          SELECT 1 FROM public.core_csaassignment claim
          JOIN public.core_organismmembership member ON member.id = claim.assignee_membership_id
          JOIN public.core_contextualroleassignment office ON office.id = claim.assignee_office_id
          JOIN public.core_organismroledefinition definition ON definition.id = office.role_definition_id
          WHERE claim.id = NEW.assignment_reference_id AND claim.application_id = NEW.application_id
            AND claim.assignment_uuid::text = engagement.engagement_reference
            AND member.identity_id = NEW.enterer_id AND member.living_organism_id = NEW.deciding_party_id
            AND office.id = NEW.qualifying_office_id AND office.membership_id = member.id
            AND definition.code = 'COORDINATOR' AND definition.scope = 'LIVING_ORGANISM'
            AND claim.effective_at <= NEW.effective_at AND claim.recorded_at <= NEW.recorded_at
            AND public.s015_fold_effective_state('core_contextualroleassignment', office.id, NEW.effective_at, NEW.recorded_at) = 'ACTIVE'
            AND public.s015_fold_effective_state('core_organismmembership', member.id, NEW.effective_at, NEW.recorded_at) = 'ACTIVE'
            AND NOT EXISTS (
                SELECT 1 FROM public.core_csaassignmentend ending
                WHERE ending.assignment_id = claim.id AND ending.effective_at <= NEW.effective_at
                  AND ending.recorded_at <= NEW.recorded_at
            )
            AND NOT EXISTS (
                SELECT 1 FROM public.core_contextualroleassignmentstateevent term
                WHERE term.assignment_id = office.id AND term.effective_until_state = 'FIXED'
                  AND term.effective_until <= NEW.effective_at AND term.recorded_at <= NEW.recorded_at
                  AND term.id = (
                      SELECT last_term.id FROM public.core_contextualroleassignmentstateevent last_term
                      WHERE last_term.assignment_id = office.id AND last_term.effective_at <= NEW.effective_at
                        AND last_term.recorded_at <= NEW.recorded_at
                      ORDER BY last_term.effective_at DESC, last_term.sequence DESC LIMIT 1
                  )
            )
       ) THEN
        RAISE EXCEPTION 'S3 CSA completion authority refused' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END """

# public.s3_end_guard()
# Its landed text: 0056_c3_authority_lifecycle.py (assembled by that migration). Time calls moved: 5 wall.
S3_END_GUARD_LANDED = r"""
DECLARE application_id bigint;
BEGIN
    SELECT claim.application_id INTO application_id FROM public.core_csaassignment claim WHERE claim.id = NEW.assignment_id;
    PERFORM public.s3_lock_application(application_id);
    NEW.recorded_at := clock_timestamp();
    IF NOT EXISTS (SELECT FROM public.core_csaassignment displaced
          JOIN public.core_organismmembership m ON m.identity_id=NEW.actor_id
          JOIN public.core_contextualroleassignment seat ON seat.membership_id=m.id
          WHERE displaced.id=NEW.assignment_id AND NEW.enterer_id=NEW.actor_id
          AND public.c3_act_problem(displaced,NEW.effective_at,NEW.received_at) IS NOT NULL
          AND public.c3_act_problem(displaced,clock_timestamp(),clock_timestamp()) IS NOT NULL
          AND public.c3_replacement(displaced.application_id,displaced.id,seat.id,NEW.actor_id,NEW.effective_at,NEW.received_at) IS NOT NULL
          AND public.c3_replacement(displaced.application_id,displaced.id,seat.id,NEW.actor_id,clock_timestamp(),clock_timestamp()) IS NOT NULL)
        THEN RAISE EXCEPTION 'C3 no independently qualified replacement; keep the claim' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END """
S3_END_GUARD_CLOCK = r"""
DECLARE application_id bigint;
BEGIN
    SELECT claim.application_id INTO application_id FROM public.core_csaassignment claim WHERE claim.id = NEW.assignment_id;
    PERFORM public.s3_lock_application(application_id);
    NEW.recorded_at := public.intevia_clock_instant();
    IF NOT EXISTS (SELECT FROM public.core_csaassignment displaced
          JOIN public.core_organismmembership m ON m.identity_id=NEW.actor_id
          JOIN public.core_contextualroleassignment seat ON seat.membership_id=m.id
          WHERE displaced.id=NEW.assignment_id AND NEW.enterer_id=NEW.actor_id
          AND public.c3_act_problem(displaced,NEW.effective_at,NEW.received_at) IS NOT NULL
          AND public.c3_act_problem(displaced,public.intevia_clock_instant(),public.intevia_clock_instant()) IS NOT NULL
          AND public.c3_replacement(displaced.application_id,displaced.id,seat.id,NEW.actor_id,NEW.effective_at,NEW.received_at) IS NOT NULL
          AND public.c3_replacement(displaced.application_id,displaced.id,seat.id,NEW.actor_id,public.intevia_clock_instant(),public.intevia_clock_instant()) IS NOT NULL)
        THEN RAISE EXCEPTION 'C3 no independently qualified replacement; keep the claim' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END """

FUNCTIONS = (
    ("public.s015_compute_governed_cache()", S015_COMPUTE_GOVERNED_CACHE_LANDED, S015_COMPUTE_GOVERNED_CACHE_CLOCK),
    ("public.s015_refresh_governed_cache(text, bigint, timestamp with time zone, timestamp with time zone)", S015_REFRESH_GOVERNED_CACHE_LANDED, S015_REFRESH_GOVERNED_CACHE_CLOCK),
    ("public.sr_provision_insert_guard()", SR_PROVISION_INSERT_GUARD_LANDED, SR_PROVISION_INSERT_GUARD_CLOCK),
    ("public.sr_definition_update_guard()", SR_DEFINITION_UPDATE_GUARD_LANDED, SR_DEFINITION_UPDATE_GUARD_CLOCK),
    ("public.c2_future_cuts(bigint)", C2_FUTURE_CUTS_LANDED, C2_FUTURE_CUTS_CLOCK),
    ("public.c2_guard_finding()", C2_GUARD_FINDING_LANDED, C2_GUARD_FINDING_CLOCK),
    ("public.c2_guard_volunteer()", C2_GUARD_VOLUNTEER_LANDED, C2_GUARD_VOLUNTEER_CLOCK),
    ("public.c2_roster_stamp()", C2_ROSTER_STAMP_LANDED, C2_ROSTER_STAMP_CLOCK),
    ("public.c3_case_finding_guard()", C3_CASE_FINDING_GUARD_LANDED, C3_CASE_FINDING_GUARD_CLOCK),
    ("public.c3_successor_check()", C3_SUCCESSOR_CHECK_LANDED, C3_SUCCESSOR_CHECK_CLOCK),
    ("public.c3_validate_basis_event()", C3_VALIDATE_BASIS_EVENT_LANDED, C3_VALIDATE_BASIS_EVENT_CLOCK),
    ("public.s3_claim_guard()", S3_CLAIM_GUARD_LANDED, S3_CLAIM_GUARD_CLOCK),
    ("public.s3_decision_guard_a26()", S3_DECISION_GUARD_A26_LANDED, S3_DECISION_GUARD_A26_CLOCK),
    ("public.s3_end_guard()", S3_END_GUARD_LANDED, S3_END_GUARD_CLOCK),
)


def install(apps, schema_editor):
    """Move these functions onto the INTEVIA Clock."""
    clock.convert(schema_editor, FUNCTIONS)


def uninstall(apps, schema_editor):
    """Put the landed functions back."""
    clock.restore(schema_editor, FUNCTIONS)


class Migration(migrations.Migration):
    dependencies = (("core", "0097_clock_storage_0021_0049"),)
    operations = (migrations.RunPython(install, uninstall),)
