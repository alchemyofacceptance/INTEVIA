"""CLK-MONO-1 diagnostic: name the operands when a cache-coordinate check refuses.

PKT-CLK-3 CLK3-3, attempts 73 and 83: the cache trigger refused with
"S015 cache coordinates must satisfy state_at <= known_at <= now" after the
refresh function's check had passed. The logs carry the message and its
CONTEXT lines, not the operand values, so a backwards step of the database
clock between the two readings cannot be told from a changed coordinate
(LD_RETURN_PKT_CLK_3_C3_ATTEMPT83_v1_1). That the trigger was reached is
established by the logs; that it saw the same values the refresh had passed
is an inference from the shipped text, not a recorded fact.

This migration replaces the two functions. Each check captures its
database-clock reading immediately before evaluating the existing predicate
(``v_now := clock_timestamp()``, on every path); on refusal it reports that
reading and the coordinates in the error DETAIL (``state_at``, ``known_at``,
``now``, ``statement_timestamp()`` and ``transaction_timestamp()``). The two
inequalities and their conjunction, the primary messages, the error codes and
the ticket mechanism are unchanged. CREATE OR REPLACE keeps the function
identities, so the five cache triggers created by 0021 stay bound, subject to
the replacement succeeding.

What DETAIL does and does not prove: ``known_at - now`` is the violation
margin of the check that failed, not the size of a clock step; a refresh
refusal with a supplied future ``known_at`` shows the same shape without any
clock movement; a trigger refusal after the refresh's check passed, with the
coordinates unchanged, evidences backwards movement of the database clock
between the two readings, whose interval is not measured.

At import, the two previous function texts are checked to be present exactly
once in 0021's Python SQL constant (``FUNCTIONS_SQL``); this checks the
migration source, not the live catalog. ``backwards`` restores those
historical function bodies.
"""

from importlib import import_module

from django.db import migrations


_S015_MIGRATION = import_module(
    "core.migrations.0021_s015_recorded_chain_and_bitemporal_fold"
)
_FUNCTIONS_SQL = _S015_MIGRATION.FUNCTIONS_SQL

_PREVIOUS_COMPUTE = """\
CREATE FUNCTION public.s015_compute_governed_cache() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE v_updated integer; v_state_at timestamptz; v_known_at timestamptz; v_ids bigint[]; v_fp text; v_state text;
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
    IF NOT (v_state_at <= v_known_at AND v_known_at <= clock_timestamp()) THEN
        RAISE EXCEPTION 'S015 cache coordinates must satisfy state_at <= known_at <= now' USING ERRCODE = 'integrity_constraint_violation';
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
END $$;
"""

_PREVIOUS_REFRESH = """\
CREATE FUNCTION public.s015_refresh_governed_cache(p_anchor_token text, p_anchor_id bigint, p_state_at timestamptz, p_known_at timestamptz)
RETURNS varchar(71) LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE m record; v_fp text; v_lock bigint;
BEGIN
    SELECT * INTO m FROM public.s015_anchor_map() WHERE anchor_token = p_anchor_token AND has_cache;
    IF m IS NULL THEN RAISE EXCEPTION 'S015 refresh: % is not a cache-bearing anchor', p_anchor_token; END IF;
    IF NOT (p_state_at <= p_known_at AND p_known_at <= clock_timestamp()) THEN
        RAISE EXCEPTION 'S015 refresh: coordinates must satisfy state_at <= known_at <= now';
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
END $$;
"""

_COMPUTE = """\
CREATE FUNCTION public.s015_compute_governed_cache() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
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
END $$;
"""

_REFRESH = """\
CREATE FUNCTION public.s015_refresh_governed_cache(p_anchor_token text, p_anchor_id bigint, p_state_at timestamptz, p_known_at timestamptz)
RETURNS varchar(71) LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
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
END $$;
"""

for _name, _previous in (("compute", _PREVIOUS_COMPUTE), ("refresh", _PREVIOUS_REFRESH)):
    if _FUNCTIONS_SQL.count(_previous) != 1:
        raise RuntimeError(
            "0021 %s-function text no longer matches CLK-MONO-1 diagnostic's previous text" % _name
        )


def _replace(text):
    assert text.startswith("CREATE FUNCTION ")
    return "CREATE OR REPLACE FUNCTION " + text[len("CREATE FUNCTION "):]


def forwards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(_replace(_COMPUTE))
        cursor.execute(_replace(_REFRESH))


def backwards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(_replace(_PREVIOUS_COMPUTE))
        cursor.execute(_replace(_PREVIOUS_REFRESH))


class Migration(migrations.Migration):
    dependencies = [("core", "0049_h100_command_intake")]

    operations = [migrations.RunPython(forwards, backwards)]
