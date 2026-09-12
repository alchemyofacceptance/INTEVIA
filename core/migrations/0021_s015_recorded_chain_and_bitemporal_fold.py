"""S015 migration 0021 — recorded-chain closure and bitemporal fold (CANDIDATE, U21g Phase 1).

Built to S015_PKT_A_2_SPECIFICATION_v0_7.md (controlling) following the construction of
IDOP097_S015_MIGRATION_0021_DESIGN_v0_6.md (not certified). Phase 1 build: far enough to apply
to a throwaway database and answer the eight empirical questions of the Making Engine
commission v0.1 §4. Not committed, not authorised, not certified.
"""

import datetime
import textwrap
import uuid

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q

UTC = datetime.timezone.utc
TS_MIN = datetime.datetime(1, 1, 1, 0, 0, 0, tzinfo=UTC)
TS_MAX = datetime.datetime(9999, 12, 31, 23, 59, 59, 999999, tzinfo=UTC)
PROTECT = django.db.models.deletion.PROTECT

# --------------------------------------------------------------------------------------
# The closed anchor map (design §7). token, identity col, event table, head col, fk col,
# state col in the event table, has cache, empty chain permitted, state width.
# --------------------------------------------------------------------------------------
ANCHORS = [
    # token, identity, event table, head col, fk col, state col, cache, empty_ok, width
    ("core_livingorganism", "organism_id", "core_livingorganismevent", "head_event_id", "living_organism_id", "resulting_state", True, False, 24),
    ("core_circle", "circle_uuid", "core_circlestateevent", "head_event_id", "circle_id", "resulting_state", True, False, 16),
    ("core_organismmembership", "membership_uuid", "core_organismmembershiptransition", "head_transition_id", "membership_id", "resulting_state", True, False, 19),
    ("core_contextualroleassignment", "assignment_uuid", "core_contextualroleassignmentstateevent", "head_state_event_id", "assignment_id", "resulting_state", True, False, 19),
    ("core_membershipcondition", "condition_uuid", "core_membershipconditionstateevent", "head_state_event_id", "condition_id", "resulting_state", False, False, 48),
    ("core_authoritybasis", "basis_uuid", "core_authorityinvalidationevent", "head_invalidation_event_id", "basis_id", "resulting_state", False, True, 64),
    ("core_determinationcontestcase", "case_uuid", "core_determinationcontestevent", "head_event_id", "case_id", "resulting_state", False, False, 64),
    ("core_essentialcoveragecase", "case_uuid", "core_coverageassessment", "head_assessment_id", "case_id", "result", False, False, 11),
    ("core_restrictedcontinuitycase", "case_uuid", "core_restrictedcontinuityevent", "head_event_id", "case_id", "resulting_state", False, False, 14),
    ("core_governedvisibilitygrant", "grant_uuid", "core_visibilitygrantstateevent", "head_state_event_id", "grant_id", "resulting_state", False, False, 10),
    ("core_governedobligationcase", "case_uuid", "core_obligationstateevent", "head_state_event_id", "case_id", "resulting_state", True, False, 40),
    ("core_planningclassificationcase", "case_uuid", "core_planningclassificationevent", "head_event_id", "case_id", "result", False, False, 20),
]
EVENT_TABLES = [a[2] for a in ANCHORS]
CACHE_ANCHORS = [a for a in ANCHORS if a[6]]

# Shipped tables 0021 alters, extends or attaches triggers to — the preflight covered set (design §12).
COVERED_TABLES = [
    "core_authoritybasis",
    "core_circle",
    "core_contextualroleassignment",
    "core_governeddetermination",
    "core_governedvisibilitygrant",
    "core_livingorganism",
    "core_organismmembership",
    "core_organismroledefinition",
]

# Owning-organism lock acquisition tables (design §11.4); organism resolution is inside the trigger function.
ORGANISM_LOCK_TABLES = [
    "core_organismmembershiptransition",
    "core_membershipconditionstateevent",
    "core_contextualroleassignmentstateevent",
    "core_circlestateevent",
    "core_coverageassessment",
    "core_essentialcoveragerequirement",
    "core_restrictedcontinuityevent",
]

# Fixed (immutable) columns on the six new anchors and spines (design §13 arithmetic).
NEW_ANCHOR_FIXED = {
    "core_membershipcondition": ("condition_uuid", "condition_kind", "subject_membership_id", "mentor_membership_id"),
    "core_determinationcontestcase": ("case_uuid", "determination_id"),
    "core_essentialcoveragecase": ("case_uuid", "living_organism_id"),
    "core_restrictedcontinuitycase": ("case_uuid", "living_organism_id"),
    "core_governedobligationcase": ("case_uuid", "asserted_obligation_reference", "affected_scope_reference", "responsible_capacities_reference", "operational_escalation_at"),
    "core_planningclassificationcase": ("case_uuid", "item_identifier"),
}
ROLE_DEFINITION_FIXED = ("code", "scope", "living_organism_id", "definition_version", "role_uuid")
# U21h fix dispatch 2.2 — aggregate-specific canonical reference columns, by table.
AGGREGATE_REFERENCE_COLUMNS = {
    "core_governedobligationcase": ("asserted_obligation_reference", "affected_scope_reference", "responsible_capacities_reference"),
    "core_coverageassessment": ("determiner_capacity_reference",),
    "core_restrictedcontinuityevent": ("permitted_measures_reference", "prohibited_effects_reference"),
    "core_obligationstateevent": ("extension_basis_reference", "escalation_path_reference", "interim_measures_reference", "consequences_reference"),
    "core_planningclassificationevent": ("criterion_reference",),
}

PRIOR_STATE_COMMENT = (
    "S015: the effective-order predecessor''s resulting state as the chain stood when this event "
    "was recorded; NULL at sequence 1, and at any later sequence where no eligible predecessor stood "
    "before this event in effective order when it was recorded. Not the recorded-chain predecessor. "
    "A later retrospective insert does not make this value false."
)
CACHE_COMMENT = (
    "S015: non-authoritative governed-state cache. Authoritative state is the bitemporal fold over "
    "the immutable event chain, read through s015_effective_state(anchor_token, anchor_id, state_at, "
    "known_at); this column is valid only at its stored (state_at, known_at, "
    "source_event_set_fingerprint) coordinates. A comment is not a refusal."
)


# --------------------------------------------------------------------------------------
# Django field builders
# --------------------------------------------------------------------------------------
def common_member_fields(model_name, anchor_model, fk_name, state_width, state_col="resulting_state"):
    """The §6.1 common chain member as Django fields (the anchor FK is named per table)."""
    fields = [
        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
        ("event_uuid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
        ("sequence", models.PositiveIntegerField()),
        ("predecessor_sequence", models.PositiveIntegerField(null=True, blank=True)),
        ("action", models.CharField(max_length=48)),
        ("prior_state", models.CharField(max_length=state_width, null=True, blank=True, help_text=PRIOR_STATE_COMMENT.replace("''", "'"))),
        (state_col, models.CharField(max_length=state_width)),
        ("actor_access_epoch", models.PositiveBigIntegerField()),
        ("authority_decision_reference", models.CharField(max_length=71)),
        ("evidence_reference", models.CharField(max_length=255)),
        ("request_reference", models.CharField(max_length=255)),
        ("idempotency_key", models.CharField(max_length=255)),
        ("payload_fingerprint", models.CharField(max_length=64)),
        ("lineage_reference", models.CharField(max_length=71, unique=True)),
        ("temporal_basis_kind", models.CharField(max_length=13, null=True, blank=True)),
        ("temporal_basis_reference", models.CharField(max_length=255, null=True, blank=True)),
        ("occurred_at", models.DateTimeField()),
        ("effective_at", models.DateTimeField()),
        ("received_at", models.DateTimeField()),
        ("recorded_at", models.DateTimeField(null=True, editable=False)),
        (fk_name, models.ForeignKey(on_delete=PROTECT, related_name="+", to=anchor_model)),
        ("predecessor", models.ForeignKey(null=True, blank=True, on_delete=PROTECT, related_name="+", to=f"core.{model_name.lower()}")),
        ("actor", models.ForeignKey(on_delete=PROTECT, related_name="+", to="core.identity")),
        ("authority_basis", models.ForeignKey(on_delete=PROTECT, related_name="+", to="core.authoritybasis")),
    ]
    return fields


def event_model(name, anchor_model, fk_name, state_width, extra_fields=(), state_col="resulting_state"):
    fields = common_member_fields(name, anchor_model, fk_name, state_width, state_col) + list(extra_fields)
    return migrations.CreateModel(
        name=name,
        fields=fields,
        options={
            "constraints": [
                models.UniqueConstraint(fields=(fk_name, "sequence"), name=f"s015_0021_{name.lower()}_anchor_seq_uniq"),
                models.UniqueConstraint(fields=("predecessor",), name=f"s015_0021_{name.lower()}_pred_uniq"),
                models.UniqueConstraint(fields=("actor", "action", "idempotency_key"), name=f"s015_0021_{name.lower()}_idem_uniq"),
            ]
        },
    )


def ts_domain_q(col):
    return Q(**{f"{col}__gte": TS_MIN}) & Q(**{f"{col}__lte": TS_MAX})


# --------------------------------------------------------------------------------------
# Preflight (design §12, D-16): lock, then count, the eight covered shipped tables.
# --------------------------------------------------------------------------------------
def s015_refuse_unless_covered_tables_empty(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        raise RuntimeError("S015 0021 requires PostgreSQL")
    with schema_editor.connection.cursor() as cursor:
        for table in COVERED_TABLES:  # fixed alphabetical order
            cursor.execute(f"LOCK TABLE public.{table} IN SHARE ROW EXCLUSIVE MODE")
        populated = []
        for table in COVERED_TABLES:
            cursor.execute(f"SELECT count(*) FROM public.{table}")
            count = cursor.fetchone()[0]
            if count:
                populated.append(f"{table}={count}")
    if populated:
        raise RuntimeError(
            "S015 0021 preflight halt: covered shipped tables are populated: " + ", ".join(populated)
            + ". Backfill is reserved to the Human Governor; the migration does not apply."
        )


def noop(apps, schema_editor):
    return None


# --------------------------------------------------------------------------------------
# SQL installer
# --------------------------------------------------------------------------------------
def _anchor_map_values():
    rows = []
    for tok, ident, ev, head, fk, state_col, cache, empty_ok, width in ANCHORS:
        rows.append(
            f"('{tok}','{ident}','{ev}','{head}','{fk}','{state_col}',{str(cache).lower()},{str(empty_ok).lower()})"
        )
    return ",\n        ".join(rows)


SEC = "SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp"

SHIPPED_FOUNDING_COMPLETION_GUARDIAN_SQL = textwrap.indent(
    """
CREATE OR REPLACE FUNCTION s015_guard_founding_completion_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.founding_authority_basis_id IS NULL
       AND OLD.founder_identity_id IS NULL
       AND NEW.founding_authority_basis_id IS NOT NULL
       AND NEW.founder_identity_id IS NOT NULL
       AND OLD.founding_insert_xid = pg_current_xact_id()
       AND NEW.founding_insert_xid = OLD.founding_insert_xid
       AND EXISTS (
           SELECT 1 FROM core_organismmembership membership
           WHERE membership.living_organism_id = OLD.id
             AND membership.identity_id = NEW.founder_identity_id
             AND membership.state IN ('ACTIVE', 'PROBATIONARY')
       )
       AND EXISTS (
           SELECT 1
           FROM core_contextualroleassignment assignment
           JOIN core_organismmembership membership
             ON membership.id = assignment.membership_id
           JOIN core_organismroledefinition role
             ON role.id = assignment.role_definition_id
           WHERE membership.living_organism_id = OLD.id
             AND membership.identity_id = NEW.founder_identity_id
             AND role.code = 'FOUNDER_STEWARD'
             AND assignment.state = 'ACTIVE'
       )
    THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'S015 founding completion update refused'
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;
""",
    "        ",
)

FUNCTIONS_SQL = f"""
CREATE UNLOGGED TABLE public.s015_transaction_register (
    xid xid8 NOT NULL,
    kind text NOT NULL CHECK (kind IN ('REFRESH_TICKET','ORGANISM_LOCK')),
    anchor_token text,
    anchor_id bigint,
    organism_uuid uuid,
    ordinal integer,
    consumed boolean NOT NULL DEFAULT FALSE,
    CHECK ((kind = 'REFRESH_TICKET') = (anchor_token IS NOT NULL AND anchor_id IS NOT NULL)),
    CHECK ((kind = 'ORGANISM_LOCK') = (organism_uuid IS NOT NULL AND ordinal IS NOT NULL))
);
REVOKE ALL ON public.s015_transaction_register FROM PUBLIC;

CREATE TYPE public.s015_effective_state_t AS (
    anchor_token text, anchor_id bigint, state_at timestamptz, known_at timestamptz,
    projection text, legal_deadline_status text, qualified_legal_deadline timestamptz,
    effective_deadline timestamptz, legal_basis_determination_id bigint,
    fingerprint varchar(71), eligible_count integer, served_from text
);
CREATE TYPE public.s015_obligation_projection_t AS (
    resulting_state text, legal_deadline_status text, qualified_legal_deadline timestamptz,
    effective_deadline timestamptz, legal_basis_determination_id bigint
);

CREATE OR REPLACE FUNCTION public.s015_guard_founding_completion_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
        IF OLD.founding_authority_basis_id IS NULL
             AND OLD.founder_identity_id IS NULL
             AND NEW.founding_authority_basis_id IS NOT NULL
             AND NEW.founder_identity_id IS NOT NULL
             AND OLD.founding_insert_xid = pg_current_xact_id()
             AND NEW.founding_insert_xid = OLD.founding_insert_xid
             AND EXISTS (
                     SELECT 1 FROM public.core_organismmembership membership
                     WHERE membership.living_organism_id = OLD.id
                         AND membership.identity_id = NEW.founder_identity_id
                         AND membership.state IN ('ACTIVE', 'PROBATIONARY')
             )
             AND (
                     OLD.platform_root IS NOT TRUE
                     OR EXISTS (
                             SELECT 1
                             FROM public.core_contextualroleassignment assignment
                             JOIN public.core_organismmembership membership
                                 ON membership.id = assignment.membership_id
                             JOIN public.core_organismroledefinition role
                                 ON role.id = assignment.role_definition_id
                             WHERE membership.living_organism_id = OLD.id
                                 AND membership.identity_id = NEW.founder_identity_id
                                 AND role.code = 'INTEVIA_FOUNDER_STEWARD'
                                 AND assignment.state = 'ACTIVE'
                     )
             )
        THEN
                RETURN NEW;
        END IF;
        RAISE EXCEPTION 'S015 founding completion update refused'
                USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

CREATE FUNCTION public.s015_anchor_map()
RETURNS TABLE(anchor_token text, identity_col text, event_table text, head_col text, fk_col text,
              state_col text, has_cache boolean, empty_chain_permitted boolean)
LANGUAGE sql IMMUTABLE AS $$
    SELECT * FROM (VALUES
        {_anchor_map_values()}
    ) AS v(anchor_token, identity_col, event_table, head_col, fk_col, state_col, has_cache, empty_chain_permitted)
$$;

CREATE FUNCTION public.s015_reserved_role_codes() RETURNS text[] LANGUAGE sql IMMUTABLE AS $$
    SELECT ARRAY['INTEVIA_FOUNDER_STEWARD','INTEVIA_STEWARD','INTEVIA_GUIDING_STEWARD','INTEVIA_EMERGENCY_STEWARD','INTEVIA_PRIVACY_COORDINATOR']::text[]
$$;

-- ---------------- canonical form and digest (design §8; form version s015f3 per U21g-27: every column of every event row) ----------------
-- The rule is recorded in S015_CANONICAL_FORM_s015f3_RULE_v0_1.md. One grammar for twelve tables: a record is a JSON
-- object whose keys are the table's catalogue columns in bytewise (C-collation) name order and whose values follow one
-- rule per PostgreSQL type; NULL is the literal null with the key present; a row lacking a catalogue column, carrying an
-- unknown key, or holding a type without a rule is refused, never guessed.
CREATE FUNCTION public.s015_canonical_ts(p timestamptz) RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT to_char(p AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
$$;
-- JSON string literal: mandatory escapes only (" \ and control characters), non-ASCII emitted as raw UTF-8; NULL -> null
CREATE FUNCTION public.s015_canonical_json_string(p text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE WHEN p IS NULL THEN 'null' ELSE to_json(p)::text END
$$;
CREATE FUNCTION public.s015_canonical_json_ts(p timestamptz) RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE WHEN p IS NULL THEN 'null' ELSE '"' || public.s015_canonical_ts(p) || '"' END
$$;
-- one value rule per catalogue type; p_value is the jsonb-carried column value (NULL -> null); anything else is refused
CREATE FUNCTION public.s015_canonical_value(p_type regtype, p_value jsonb) RETURNS text LANGUAGE plpgsql STABLE AS $$
BEGIN
    IF p_value IS NULL OR jsonb_typeof(p_value) = 'null' THEN RETURN 'null'; END IF;
    CASE p_type::text
        WHEN 'timestamp with time zone' THEN RETURN public.s015_canonical_json_ts((p_value #>> '{{}}')::timestamptz);
        WHEN 'uuid' THEN RETURN '"' || ((p_value #>> '{{}}')::uuid)::text || '"';
        WHEN 'character varying', 'text' THEN RETURN public.s015_canonical_json_string(p_value #>> '{{}}');
        WHEN 'bigint', 'integer', 'smallint' THEN RETURN ((p_value #>> '{{}}')::bigint)::text;
        ELSE RAISE EXCEPTION 'S015 canonical form s015f3 has no rule for type %', p_type::text;
    END CASE;
END $$;
-- canonical column list of an event table: catalogue columns in bytewise name order (the published inventory)
CREATE FUNCTION public.s015_canonical_columns(p_table regclass) RETURNS text[] LANGUAGE sql STABLE AS $$
    SELECT coalesce(array_agg(a.attname::text ORDER BY a.attname COLLATE "C"), '{{}}'::text[])
      FROM pg_catalog.pg_attribute a
     WHERE a.attrelid = p_table AND a.attnum > 0 AND NOT a.attisdropped
$$;
-- per-event record: JSON object of every column of the row, keys in bytewise name order, values by s015_canonical_value
CREATE FUNCTION public.s015_canonical_event_record(p_table regclass, p_row jsonb) RETURNS text LANGUAGE plpgsql STABLE AS $$
DECLARE v_parts text[]; v_cols text[]; v_extra text[];
BEGIN
    IF p_row IS NULL OR jsonb_typeof(p_row) <> 'object' THEN RAISE EXCEPTION 'S015 canonical record requires a row object'; END IF;
    SELECT array_agg('"' || a.attname::text || '":' || public.s015_canonical_value(a.atttypid::regtype, p_row -> a.attname::text)
                     ORDER BY a.attname COLLATE "C"),
           array_agg(a.attname::text ORDER BY a.attname COLLATE "C")
      INTO v_parts, v_cols
      FROM pg_catalog.pg_attribute a
     WHERE a.attrelid = p_table AND a.attnum > 0 AND NOT a.attisdropped;
    IF v_cols IS NULL THEN RAISE EXCEPTION 'S015 canonical record: % has no columns', p_table::text; END IF;
    IF EXISTS (SELECT 1 FROM unnest(v_cols) c WHERE NOT (p_row ? c)) THEN
        RAISE EXCEPTION 'S015 canonical record: row for % lacks column(s) %', p_table::text,
            (SELECT array_agg(c ORDER BY c COLLATE "C") FROM unnest(v_cols) c WHERE NOT (p_row ? c));
    END IF;
    SELECT array_agg(k ORDER BY k COLLATE "C") INTO v_extra FROM jsonb_object_keys(p_row) k WHERE k <> ALL (v_cols);
    IF v_extra IS NOT NULL THEN
        RAISE EXCEPTION 'S015 canonical record: row for % carries unknown key(s) %', p_table::text, v_extra;
    END IF;
    RETURN '{{' || array_to_string(v_parts, ',') || '}}';
END $$;
CREATE FUNCTION public.s015_canonical_envelope(p_token text, p_identity uuid, p_records text[])
RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT '["s015f3","' || p_token || '","' || p_identity::text || '",['
        || coalesce(array_to_string(p_records, ','), '') || ']]'
$$;
CREATE FUNCTION public.s015_digest_envelope(p_preimage text) RETURNS varchar(71) LANGUAGE sql IMMUTABLE AS $$
    SELECT ('s015f3:' || encode(sha256(convert_to(p_preimage, 'UTF8')), 'hex'))::varchar(71)
$$;

-- eligible set in fold order, as ids
CREATE FUNCTION public.s015_eligible_event_ids(p_token text, p_anchor_id bigint, p_state_at timestamptz, p_known_at timestamptz)
RETURNS bigint[] LANGUAGE plpgsql STABLE AS $$
DECLARE m record; v_ids bigint[];
BEGIN
    SELECT * INTO m FROM public.s015_anchor_map() WHERE anchor_token = p_token;
    IF m IS NULL THEN RAISE EXCEPTION 'S015 unknown anchor token %', p_token; END IF;
    EXECUTE format(
        'SELECT coalesce(array_agg(id ORDER BY effective_at, occurred_at, received_at, recorded_at, sequence, event_uuid), ''{{}}''::bigint[])
           FROM public.%I WHERE %I = $1 AND effective_at <= $2 AND received_at <= $3 AND recorded_at <= $3',
        m.event_table, m.fk_col) INTO v_ids USING p_anchor_id, p_state_at, p_known_at;
    RETURN v_ids;
END $$;

CREATE FUNCTION public.s015_event_set_preimage(p_token text, p_anchor_id bigint, p_state_at timestamptz, p_known_at timestamptz)
RETURNS text LANGUAGE plpgsql STABLE AS $$
DECLARE m record; v_identity uuid; v_records text[];
BEGIN
    SELECT * INTO m FROM public.s015_anchor_map() WHERE anchor_token = p_token;
    IF m IS NULL THEN RAISE EXCEPTION 'S015 unknown anchor token %', p_token; END IF;
    EXECUTE format('SELECT %I FROM public.%I WHERE id = $1', m.identity_col, m.anchor_token) INTO v_identity USING p_anchor_id;
    IF v_identity IS NULL THEN RAISE EXCEPTION 'S015 anchor % id % not found', p_token, p_anchor_id; END IF;
    -- every column of every eligible row travels: the whole row is carried as jsonb and serialised by the one record rule
    EXECUTE format(
        'SELECT coalesce(array_agg(public.s015_canonical_event_record(%L::regclass, to_jsonb(t))
                    ORDER BY t.effective_at, t.occurred_at, t.received_at, t.recorded_at, t.sequence, t.event_uuid), ''{{}}''::text[])
           FROM public.%I t WHERE t.%I = $1 AND t.effective_at <= $2 AND t.received_at <= $3 AND t.recorded_at <= $3',
        'public.' || m.event_table, m.event_table, m.fk_col) INTO v_records USING p_anchor_id, p_state_at, p_known_at;
    RETURN public.s015_canonical_envelope(p_token, v_identity, v_records);
END $$;

CREATE FUNCTION public.s015_fingerprint_event_set(p_token text, p_anchor_id bigint, p_state_at timestamptz, p_known_at timestamptz)
RETURNS varchar(71) LANGUAGE sql STABLE AS $$
    SELECT public.s015_digest_envelope(public.s015_event_set_preimage(p_token, p_anchor_id, p_state_at, p_known_at))
$$;

-- ---------------- reducers (design §9) ----------------
-- The transition function proper: projection of an ordered eligible set (ids in fold order).
CREATE FUNCTION public.s015_reduce_projection(p_token text, p_event_ids bigint[]) RETURNS text
LANGUAGE plpgsql STABLE AS $$
DECLARE m record; v_state text;
BEGIN
    IF p_event_ids IS NULL OR cardinality(p_event_ids) = 0 THEN RETURN NULL; END IF;
    SELECT * INTO m FROM public.s015_anchor_map() WHERE anchor_token = p_token;
    EXECUTE format('SELECT %I FROM public.%I WHERE id = $1', m.state_col, m.event_table)
        INTO v_state USING p_event_ids[cardinality(p_event_ids)];
    RETURN v_state;
END $$;

CREATE FUNCTION public.s015_reduce_obligation(p_event_ids bigint[]) RETURNS public.s015_obligation_projection_t
LANGUAGE plpgsql STABLE AS $$
DECLARE r public.s015_obligation_projection_t;
BEGIN
    IF p_event_ids IS NULL OR cardinality(p_event_ids) = 0 THEN RETURN NULL; END IF;
    SELECT resulting_state, legal_deadline_status, qualified_legal_deadline, effective_deadline, legal_basis_determination_id
      INTO r FROM public.core_obligationstateevent WHERE id = p_event_ids[cardinality(p_event_ids)];
    RETURN r;
END $$;

CREATE FUNCTION public.s015_fold_effective_state(p_token text, p_anchor_id bigint, p_state_at timestamptz, p_known_at timestamptz)
RETURNS text LANGUAGE sql STABLE AS $$
    SELECT public.s015_reduce_projection(p_token, public.s015_eligible_event_ids(p_token, p_anchor_id, p_state_at, p_known_at))
$$;
CREATE FUNCTION public.s015_fold_obligation_projection(p_anchor_id bigint, p_state_at timestamptz, p_known_at timestamptz)
RETURNS public.s015_obligation_projection_t LANGUAGE sql STABLE AS $$
    SELECT public.s015_reduce_obligation(public.s015_eligible_event_ids('core_governedobligationcase', p_anchor_id, p_state_at, p_known_at))
$$;

-- ---------------- record time + append order (design §6.3, D-30) ----------------
CREATE FUNCTION public.s015_assign_event_record_time() RETURNS trigger LANGUAGE plpgsql AS $$
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
END $$;

-- ---------------- head pointer (design §6.5) ----------------
CREATE FUNCTION public.s015_advance_recorded_head() RETURNS trigger LANGUAGE plpgsql {SEC} AS $$
DECLARE m record; v_anchor bigint;
BEGIN
    SELECT * INTO m FROM public.s015_anchor_map() WHERE event_table = TG_TABLE_NAME;
    v_anchor := (to_jsonb(NEW) ->> m.fk_col)::bigint;
    EXECUTE format(
        'UPDATE public.%I a SET %I = $1 WHERE a.id = $2 AND (a.%I IS NULL OR (SELECT e.sequence FROM public.%I e WHERE e.id = a.%I) < $3)',
        m.anchor_token, m.head_col, m.head_col, m.event_table, m.head_col) USING NEW.id, v_anchor, NEW.sequence;
    RETURN NULL;
END $$;

CREATE FUNCTION public.s015_guard_head_pointer() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE m record; v_head bigint; v_count bigint; v_max integer; v_seq integer;
BEGIN
    SELECT * INTO m FROM public.s015_anchor_map() WHERE anchor_token = TG_TABLE_NAME;
    v_head := (to_jsonb(NEW) ->> m.head_col)::bigint;
    EXECUTE format('SELECT count(*), max(sequence) FROM public.%I WHERE %I = $1', m.event_table, m.fk_col)
        INTO v_count, v_max USING NEW.id;
    IF v_head IS NULL AND v_count > 0 THEN
        RAISE EXCEPTION 'S015 head pointer: NULL refused while % events exist on % id %', v_count, m.anchor_token, NEW.id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF v_head IS NOT NULL THEN
        EXECUTE format('SELECT sequence FROM public.%I WHERE id = $1 AND %I = $2', m.event_table, m.fk_col)
            INTO v_seq USING v_head, NEW.id;
        IF v_seq IS NULL OR v_seq <> v_max THEN
            RAISE EXCEPTION 'S015 head pointer: % is not the maximum-sequence event (% vs max %) on % id %',
                v_head, v_seq, v_max, m.anchor_token, NEW.id USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    RETURN NEW;
END $$;

-- ---------------- cache requalification (design §10.4, §11.2 predicate 4) ----------------
CREATE FUNCTION public.s015_requalify_governed_cache(p_token text, p_anchor_id bigint) RETURNS boolean
LANGUAGE plpgsql {SEC} AS $$
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
END $$;

-- ---------------- the commit-time chain-and-fold guardian (design §11.2) ----------------
CREATE FUNCTION public.s015_guard_aggregate_chain() RETURNS trigger LANGUAGE plpgsql {SEC} AS $$
DECLARE m record; v_anchor bigint; v_is_event boolean; v_count bigint; v_max integer; v_head bigint; v_head_seq integer;
        v_p_ids bigint[]; v_expected text; v_prior text; v_ok boolean;
BEGIN
    SELECT * INTO m FROM public.s015_anchor_map() WHERE event_table = TG_TABLE_NAME;
    IF m IS NOT NULL THEN
        v_is_event := TRUE; v_anchor := (to_jsonb(NEW) ->> m.fk_col)::bigint;
    ELSE
        SELECT * INTO m FROM public.s015_anchor_map() WHERE anchor_token = TG_TABLE_NAME;
        v_is_event := FALSE; v_anchor := NEW.id;
    END IF;
    EXECUTE format('SELECT count(*), max(sequence) FROM public.%I WHERE %I = $1', m.event_table, m.fk_col)
        INTO v_count, v_max USING v_anchor;
    -- 1. non-empty chain (basis excepted)
    IF v_count = 0 AND NOT m.empty_chain_permitted THEN
        RAISE EXCEPTION 'S015 chain: % id % has no sequence-1 event at commit', m.anchor_token, v_anchor
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    -- 2. consecutive
    IF v_count > 0 AND v_max <> v_count THEN
        RAISE EXCEPTION 'S015 chain: % id % max sequence % <> count %', m.anchor_token, v_anchor, v_max, v_count
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    -- 3. head
    EXECUTE format('SELECT %I FROM public.%I WHERE id = $1', m.head_col, m.anchor_token) INTO v_head USING v_anchor;
    IF (v_head IS NULL) <> (v_count = 0) THEN
        RAISE EXCEPTION 'S015 chain: % id % head % inconsistent with % events', m.anchor_token, v_anchor, v_head, v_count
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF v_head IS NOT NULL THEN
        EXECUTE format('SELECT sequence FROM public.%I WHERE id = $1 AND %I = $2', m.event_table, m.fk_col)
            INTO v_head_seq USING v_head, v_anchor;
        IF v_head_seq IS DISTINCT FROM v_max THEN
            RAISE EXCEPTION 'S015 chain: % id % head sequence % <> max %', m.anchor_token, v_anchor, v_head_seq, v_max
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    -- 4. cache = fold at stored coordinates (cache anchors only)
    IF m.has_cache THEN
        v_ok := public.s015_requalify_governed_cache(m.anchor_token, v_anchor);
        IF v_ok IS DISTINCT FROM TRUE THEN
            RAISE EXCEPTION 'S015 chain: % id % cache does not equal the fold at its stored coordinates', m.anchor_token, v_anchor
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    -- 5. prior_state = reducer over the record-time effective-order predecessor set (U21g-13)
    IF v_is_event THEN
      IF NEW.sequence > 1 THEN
        EXECUTE format(
            'SELECT coalesce(array_agg(id ORDER BY effective_at, occurred_at, received_at, recorded_at, sequence, event_uuid), ''{{}}''::bigint[])
               FROM public.%I WHERE %I = $1 AND sequence < $2
                AND (effective_at, occurred_at, received_at, recorded_at, sequence, event_uuid) < ($3, $4, $5, $6, $2, $7)',
            m.event_table, m.fk_col) INTO v_p_ids
            USING v_anchor, NEW.sequence, NEW.effective_at, NEW.occurred_at, NEW.received_at, NEW.recorded_at, NEW.event_uuid;
        v_expected := public.s015_reduce_projection(m.anchor_token, v_p_ids);
        v_prior := NEW.prior_state;
        IF v_prior IS DISTINCT FROM v_expected THEN
            RAISE EXCEPTION 'S015 chain: % event % prior_state % should be % (record-time effective-order predecessor)',
                m.event_table, NEW.id, coalesce(v_prior, 'NULL'), coalesce(v_expected, 'NULL')
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
      END IF;
    END IF;
    RETURN NULL;
END $$;

-- ---------------- insert-time cache fingerprint (design §10.1) ----------------
CREATE FUNCTION public.s015_guard_anchor_insert_cache() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE m record; v_identity uuid; v_expected text; v_actual text; v_fp_col text;
BEGIN
    SELECT * INTO m FROM public.s015_anchor_map() WHERE anchor_token = TG_TABLE_NAME;
    v_identity := (to_jsonb(NEW) ->> m.identity_col)::uuid;
    v_fp_col := CASE WHEN TG_TABLE_NAME = 'core_governedobligationcase' THEN 'status_source_event_set_fingerprint' ELSE 'state_source_event_set_fingerprint' END;
    v_expected := public.s015_digest_envelope(public.s015_canonical_envelope(TG_TABLE_NAME, v_identity, '{{}}'::text[]));
    v_actual := to_jsonb(NEW) ->> v_fp_col;
    IF v_actual IS DISTINCT FROM v_expected THEN
        RAISE EXCEPTION 'S015 insert-time fingerprint must be the empty-set value % (got %)', v_expected, coalesce(v_actual, 'NULL')
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;

-- ---------------- cache writer boundary (design §10.2, D-35, D-37) ----------------
CREATE FUNCTION public.s015_refuse_cache_value_write() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'S015 cache values are database-computed' USING ERRCODE = 'integrity_constraint_violation';
END $$;

CREATE FUNCTION public.s015_compute_governed_cache() RETURNS trigger LANGUAGE plpgsql {SEC} AS $$
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

CREATE FUNCTION public.s015_refresh_governed_cache(p_anchor_token text, p_anchor_id bigint, p_state_at timestamptz, p_known_at timestamptz)
RETURNS varchar(71) LANGUAGE plpgsql {SEC} AS $$
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

-- ---------------- the authoritative read route (design §10.4) ----------------
CREATE FUNCTION public.s015_effective_state(p_anchor_token text, p_anchor_id bigint, p_state_at timestamptz, p_known_at timestamptz)
RETURNS public.s015_effective_state_t LANGUAGE plpgsql {SEC} AS $$
DECLARE m record; r public.s015_effective_state_t; v_ids bigint[]; v_fp text; v_row jsonb; ob public.s015_obligation_projection_t;
BEGIN
    SELECT * INTO m FROM public.s015_anchor_map() WHERE anchor_token = p_anchor_token;
    IF m IS NULL THEN RAISE EXCEPTION 'S015 unknown anchor token %', p_anchor_token; END IF;
    IF p_state_at IS NULL OR p_known_at IS NULL THEN RAISE EXCEPTION 'S015 read route requires both coordinates'; END IF;
    v_ids := public.s015_eligible_event_ids(p_anchor_token, p_anchor_id, p_state_at, p_known_at);
    v_fp := public.s015_fingerprint_event_set(p_anchor_token, p_anchor_id, p_state_at, p_known_at);
    r.anchor_token := p_anchor_token; r.anchor_id := p_anchor_id; r.state_at := p_state_at; r.known_at := p_known_at;
    r.fingerprint := v_fp; r.eligible_count := cardinality(v_ids); r.served_from := 'FOLD';
    IF p_anchor_token = 'core_governedobligationcase' THEN
        ob := public.s015_reduce_obligation(v_ids);
        r.projection := ob.resulting_state; r.legal_deadline_status := ob.legal_deadline_status;
        r.qualified_legal_deadline := ob.qualified_legal_deadline; r.effective_deadline := ob.effective_deadline;
        r.legal_basis_determination_id := ob.legal_basis_determination_id;
        SELECT to_jsonb(c) INTO v_row FROM public.core_governedobligationcase c WHERE c.id = p_anchor_id;
        IF v_row IS NOT NULL AND (v_row->>'status_state_at')::timestamptz = p_state_at AND (v_row->>'status_known_at')::timestamptz = p_known_at
           AND NOT ((v_row->>'status_source_event_set_fingerprint') IS DISTINCT FROM v_fp)
           AND NOT ((v_row->>'status_state') IS DISTINCT FROM ob.resulting_state)
           AND NOT ((v_row->>'legal_deadline_status') IS DISTINCT FROM ob.legal_deadline_status)
           AND NOT ((v_row->>'qualified_legal_deadline')::timestamptz IS DISTINCT FROM ob.qualified_legal_deadline)
           AND NOT ((v_row->>'effective_deadline')::timestamptz IS DISTINCT FROM ob.effective_deadline)
           AND NOT ((v_row->>'legal_basis_determination_id')::bigint IS DISTINCT FROM ob.legal_basis_determination_id)
        THEN r.served_from := 'CACHE_REQUALIFIED'; END IF;
    ELSE
        r.projection := public.s015_reduce_projection(p_anchor_token, v_ids);
        IF m.has_cache THEN
            EXECUTE format('SELECT to_jsonb(a) FROM public.%I a WHERE a.id = $1', p_anchor_token) INTO v_row USING p_anchor_id;
            IF v_row IS NOT NULL AND (v_row->>'current_state_effective_at')::timestamptz = p_state_at AND (v_row->>'state_known_at')::timestamptz = p_known_at
               AND NOT ((v_row->>'state_source_event_set_fingerprint') IS DISTINCT FROM v_fp)
               AND NOT ((v_row->>'state') IS DISTINCT FROM r.projection)
            THEN r.served_from := 'CACHE_REQUALIFIED'; END IF;
        END IF;
    END IF;
    RETURN r;
END $$;

-- ---------------- serialization: register, organism lock, helper, route-general acquisition (design §11.4) ----------------
CREATE FUNCTION public.s015_register_organism_lock(p_organism_id uuid) RETURNS void LANGUAGE plpgsql {SEC} AS $$
DECLARE v_greater uuid; v_next integer; v_found bigint;
BEGIN
    DELETE FROM public.s015_transaction_register WHERE kind = 'ORGANISM_LOCK' AND pg_xact_status(xid) IS DISTINCT FROM 'in progress';
    IF EXISTS (SELECT 1 FROM public.s015_transaction_register WHERE kind = 'ORGANISM_LOCK' AND xid = pg_current_xact_id() AND organism_uuid = p_organism_id) THEN
        RETURN;
    END IF;
    SELECT organism_uuid INTO v_greater FROM public.s015_transaction_register
     WHERE kind = 'ORGANISM_LOCK' AND xid = pg_current_xact_id() AND organism_uuid > p_organism_id
     ORDER BY organism_uuid DESC LIMIT 1;
    IF v_greater IS NOT NULL THEN
        RAISE EXCEPTION 'S015 lock order: organism % requested after greater organism % in this transaction', p_organism_id, v_greater
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT id INTO v_found FROM public.core_livingorganism WHERE organism_id = p_organism_id FOR UPDATE;
    IF v_found IS NULL THEN RAISE EXCEPTION 'S015 lock: organism % not found', p_organism_id; END IF;
    SELECT coalesce(max(ordinal), 0) + 1 INTO v_next FROM public.s015_transaction_register WHERE kind = 'ORGANISM_LOCK' AND xid = pg_current_xact_id();
    INSERT INTO public.s015_transaction_register (xid, kind, organism_uuid, ordinal) VALUES (pg_current_xact_id(), 'ORGANISM_LOCK', p_organism_id, v_next);
END $$;

CREATE FUNCTION public.s015_lock_organisms(p_organism_ids uuid[]) RETURNS void LANGUAGE plpgsql {SEC} AS $$
DECLARE u uuid;
BEGIN
    FOR u IN SELECT DISTINCT x FROM unnest(p_organism_ids) AS x ORDER BY x LOOP
        PERFORM public.s015_register_organism_lock(u);
    END LOOP;
END $$;

CREATE FUNCTION public.s015_acquire_owning_organism_lock() RETURNS trigger LANGUAGE plpgsql {SEC} AS $$
DECLARE v_key bigint; v_org uuid; v_sql text;
BEGIN
    v_sql := CASE TG_TABLE_NAME
        WHEN 'core_organismmembershiptransition' THEN 'SELECT lo.organism_id FROM public.core_organismmembership m JOIN public.core_livingorganism lo ON lo.id = m.living_organism_id WHERE m.id = $1'
        WHEN 'core_membershipconditionstateevent' THEN 'SELECT lo.organism_id FROM public.core_membershipcondition c JOIN public.core_organismmembership m ON m.id = c.subject_membership_id JOIN public.core_livingorganism lo ON lo.id = m.living_organism_id WHERE c.id = $1'
        WHEN 'core_contextualroleassignmentstateevent' THEN 'SELECT lo.organism_id FROM public.core_contextualroleassignment a JOIN public.core_organismmembership m ON m.id = a.membership_id JOIN public.core_livingorganism lo ON lo.id = m.living_organism_id WHERE a.id = $1'
        WHEN 'core_circlestateevent' THEN 'SELECT lo.organism_id FROM public.core_circle c JOIN public.core_livingorganism lo ON lo.id = c.parent_organism_id WHERE c.id = $1'
        WHEN 'core_coverageassessment' THEN 'SELECT lo.organism_id FROM public.core_essentialcoveragecase c JOIN public.core_livingorganism lo ON lo.id = c.living_organism_id WHERE c.id = $1'
        WHEN 'core_essentialcoveragerequirement' THEN 'SELECT lo.organism_id FROM public.core_livingorganism lo WHERE lo.id = $1'
        WHEN 'core_restrictedcontinuityevent' THEN 'SELECT lo.organism_id FROM public.core_restrictedcontinuitycase c JOIN public.core_livingorganism lo ON lo.id = c.living_organism_id WHERE c.id = $1'
    END;
    v_key := (to_jsonb(NEW) ->> CASE TG_TABLE_NAME
        WHEN 'core_organismmembershiptransition' THEN 'membership_id'
        WHEN 'core_membershipconditionstateevent' THEN 'condition_id'
        WHEN 'core_contextualroleassignmentstateevent' THEN 'assignment_id'
        WHEN 'core_circlestateevent' THEN 'circle_id'
        WHEN 'core_coverageassessment' THEN 'case_id'
        WHEN 'core_essentialcoveragerequirement' THEN 'living_organism_id'
        WHEN 'core_restrictedcontinuityevent' THEN 'case_id' END)::bigint;
    EXECUTE v_sql INTO v_org USING v_key;
    IF v_org IS NULL THEN RAISE EXCEPTION 'S015 organism lock: owning organism not resolvable for % row', TG_TABLE_NAME; END IF;
    PERFORM public.s015_register_organism_lock(v_org);
    RETURN NEW;
END $$;

-- ---------------- structural guardians (design §11.3) ----------------
CREATE FUNCTION public.s015_guard_assignment_structure() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE m_lo bigint; m_identity bigint; r_lo bigint; r_scope text; r_code text; c_lo bigint; lo_founder bigint;
BEGIN
    SELECT living_organism_id, identity_id INTO m_lo, m_identity FROM public.core_organismmembership WHERE id = NEW.membership_id;
    SELECT living_organism_id, scope, code INTO r_lo, r_scope, r_code FROM public.core_organismroledefinition WHERE id = NEW.role_definition_id;
    IF m_lo IS DISTINCT FROM r_lo THEN
        RAISE EXCEPTION 'S015 assignment structure: membership organism % <> role definition organism %', m_lo, r_lo USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (r_scope = 'CIRCLE') <> (NEW.circle_id IS NOT NULL) THEN
        RAISE EXCEPTION 'S015 assignment structure: role scope % inconsistent with circle_id %', r_scope, NEW.circle_id USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.circle_id IS NOT NULL THEN
        SELECT parent_organism_id INTO c_lo FROM public.core_circle WHERE id = NEW.circle_id;
        IF c_lo IS DISTINCT FROM m_lo THEN
            RAISE EXCEPTION 'S015 assignment structure: circle organism % <> membership organism %', c_lo, m_lo USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    IF r_code = 'INTEVIA_FOUNDER_STEWARD' THEN
        SELECT founder_identity_id INTO lo_founder FROM public.core_livingorganism WHERE id = m_lo;
        IF lo_founder IS NULL OR lo_founder <> m_identity THEN
            RAISE EXCEPTION 'S015 assignment structure: INTEVIA_FOUNDER_STEWARD anchor must be held by the founder identity' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    RETURN NULL;
END $$;

CREATE FUNCTION public.s015_guard_membership_condition_structure() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE s_lo bigint; m_lo bigint;
BEGIN
    IF NEW.mentor_membership_id IS NOT NULL THEN
        SELECT living_organism_id INTO s_lo FROM public.core_organismmembership WHERE id = NEW.subject_membership_id;
        SELECT living_organism_id INTO m_lo FROM public.core_organismmembership WHERE id = NEW.mentor_membership_id;
        IF s_lo IS DISTINCT FROM m_lo THEN
            RAISE EXCEPTION 'S015 condition structure: mentor organism % <> subject organism %', m_lo, s_lo USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    RETURN NULL;
END $$;

CREATE FUNCTION public.s015_guard_coverage_assessment_roster() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE c_lo bigint;
BEGIN
    SELECT living_organism_id INTO c_lo FROM public.core_essentialcoveragecase WHERE id = NEW.case_id;
    IF NOT EXISTS (SELECT 1 FROM public.core_essentialcoveragerequirement WHERE living_organism_id = c_lo AND roster_version = NEW.roster_version) THEN
        RAISE EXCEPTION 'S015 coverage: assessment declares roster version % that does not exist for organism %', NEW.roster_version, c_lo USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NULL;
END $$;

CREATE FUNCTION public.s015_guard_reserved_role_code() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_root boolean;
BEGIN
    IF starts_with(NEW.code, 'INTEVIA_') THEN
        SELECT platform_root INTO v_root FROM public.core_livingorganism WHERE id = NEW.living_organism_id;
        IF v_root IS DISTINCT FROM TRUE THEN
            RAISE EXCEPTION 'S015 reserved role code % may be defined only by the platform root (organism %)', NEW.code, NEW.living_organism_id
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    RETURN NULL;
END $$;

-- ---------------- founding population (design §11.5) ----------------
CREATE FUNCTION public.s015_validate_founding_population() RETURNS trigger LANGUAGE plpgsql {SEC} AS $$
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
END $$;
"""

PRIVILEGED_FUNCTIONS = [
    "s015_advance_recorded_head()", "s015_refresh_governed_cache(text,bigint,timestamptz,timestamptz)",
    "s015_compute_governed_cache()", "s015_lock_organisms(uuid[])", "s015_register_organism_lock(uuid)",
    "s015_acquire_owning_organism_lock()", "s015_effective_state(text,bigint,timestamptz,timestamptz)",
    "s015_requalify_governed_cache(text,bigint)", "s015_guard_aggregate_chain()", "s015_validate_founding_population()",
]

ALL_FUNCTIONS = [
    "s015_anchor_map()", "s015_reserved_role_codes()", "s015_canonical_ts(timestamptz)",
    "s015_canonical_json_string(text)", "s015_canonical_json_ts(timestamptz)",
    "s015_canonical_value(regtype,jsonb)", "s015_canonical_columns(regclass)",
    "s015_canonical_event_record(regclass,jsonb)",
    "s015_canonical_envelope(text,uuid,text[])", "s015_digest_envelope(text)",
    "s015_eligible_event_ids(text,bigint,timestamptz,timestamptz)", "s015_event_set_preimage(text,bigint,timestamptz,timestamptz)",
    "s015_fingerprint_event_set(text,bigint,timestamptz,timestamptz)", "s015_reduce_projection(text,bigint[])",
    "s015_reduce_obligation(bigint[])", "s015_fold_effective_state(text,bigint,timestamptz,timestamptz)",
    "s015_fold_obligation_projection(bigint,timestamptz,timestamptz)", "s015_assign_event_record_time()",
    "s015_advance_recorded_head()", "s015_guard_head_pointer()", "s015_requalify_governed_cache(text,bigint)",
    "s015_guard_aggregate_chain()", "s015_guard_anchor_insert_cache()", "s015_refuse_cache_value_write()",
    "s015_compute_governed_cache()", "s015_refresh_governed_cache(text,bigint,timestamptz,timestamptz)",
    "s015_effective_state(text,bigint,timestamptz,timestamptz)", "s015_register_organism_lock(uuid)",
    "s015_lock_organisms(uuid[])", "s015_acquire_owning_organism_lock()", "s015_guard_assignment_structure()",
    "s015_guard_membership_condition_structure()", "s015_guard_coverage_assessment_roster()",
    "s015_guard_reserved_role_code()", "s015_validate_founding_population()",
]


# Short tokens for generated identifier names: PostgreSQL truncates identifiers at 63 bytes, silently, and a truncated
# name is not an identity. Tables whose full name fits every generated pattern keep `core_` stripped.
NAME_TOKEN = {
    "core_livingorganismevent": "loevent", "core_circlestateevent": "circleevent",
    "core_organismmembershiptransition": "memtransition", "core_contextualroleassignmentstateevent": "craevent",
    "core_membershipconditionstateevent": "mcevent", "core_authorityinvalidationevent": "aievent",
    "core_determinationcontestevent": "dcevent", "core_coverageassessment": "assessment",
    "core_restrictedcontinuityevent": "rcevent", "core_visibilitygrantstateevent": "vgevent",
    "core_obligationstateevent": "obevent", "core_planningclassificationevent": "pcevent",
    "core_contextualroleassignment": "cra", "core_membershipcondition": "mcondition",
    "core_determinationcontestcase": "dccase", "core_essentialcoveragecase": "eccase",
    "core_restrictedcontinuitycase": "rccase", "core_governedvisibilitygrant": "vgrant",
    "core_governedobligationcase": "obcase", "core_planningclassificationcase": "pccase",
}


def _sq(name):
    return NAME_TOKEN.get(name, name[5:])  # strip 'core_' where no short token is needed


def install_s015_0021(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        raise RuntimeError("S015 0021 requires PostgreSQL")

    def ex(sql):  # params=None: plpgsql bodies carry literal '%' that must not be read as placeholders
        schema_editor.execute(sql, params=None)

    ex(FUNCTIONS_SQL)
    for fn in PRIVILEGED_FUNCTIONS:
        ex(f"REVOKE EXECUTE ON FUNCTION public.{fn} FROM PUBLIC")

    # ---- declarative additions on the new tables (design §6.1–§6.4, §7) ----
    for tok, ident, ev, head, fk, state_col, cache, empty_ok, width in ANCHORS:
        e = _sq(ev)
        ex(f"ALTER TABLE public.{ev} ADD CONSTRAINT s015_0021_{e}_id_anchor_uniq UNIQUE (id, {fk})")
        ex(f"ALTER TABLE public.{ev} ADD CONSTRAINT s015_0021_{e}_id_anchor_seq_uniq UNIQUE (id, {fk}, sequence)")
        ex(f"ALTER TABLE public.{ev} ADD CONSTRAINT s015_0021_{e}_predecessor_fk FOREIGN KEY (predecessor_id, {fk}, predecessor_sequence) "
           f"REFERENCES public.{ev}(id, {fk}, sequence) DEFERRABLE INITIALLY DEFERRED")
        ex(f"ALTER TABLE public.{ev} ADD CONSTRAINT s015_0021_{e}_adjacency_ck CHECK ("
           "(sequence = 1 AND predecessor_id IS NULL AND predecessor_sequence IS NULL) OR "
           "(sequence > 1 AND predecessor_id IS NOT NULL AND predecessor_sequence = sequence - 1))")
        ex(f"ALTER TABLE public.{ev} ADD CONSTRAINT s015_0021_{e}_sequence_ck CHECK (sequence >= 1)")
        ex(f"ALTER TABLE public.{ev} ADD CONSTRAINT s015_0021_{e}_prior_seq1_ck CHECK (sequence > 1 OR prior_state IS NULL)")
        ex(f"ALTER TABLE public.{ev} ADD CONSTRAINT s015_0021_{e}_recorded_not_null_ck CHECK (recorded_at IS NOT NULL)")
        ex(f"ALTER TABLE public.{ev} ADD CONSTRAINT s015_0021_{e}_four_time_ck CHECK (occurred_at <= received_at AND received_at <= recorded_at)")
        for ts in ("occurred_at", "effective_at", "received_at", "recorded_at"):
            ex(f"ALTER TABLE public.{ev} ADD CONSTRAINT s015_0021_{e}_{ts}_domain_ck CHECK ("
               f"{ts} >= '0001-01-01 00:00:00+00'::timestamptz AND {ts} <= '9999-12-31 23:59:59.999999+00'::timestamptz)")
        ex(f"ALTER TABLE public.{ev} ADD CONSTRAINT s015_0021_{e}_temporal_basis_ck CHECK ("
              "((effective_at = received_at AND temporal_basis_kind IS NULL AND temporal_basis_reference IS NULL) OR "
           "(effective_at < received_at AND temporal_basis_kind = 'RETROSPECTIVE' AND temporal_basis_reference IS NOT NULL) OR "
              "(effective_at > received_at AND temporal_basis_kind = 'PROSPECTIVE' AND temporal_basis_reference IS NOT NULL)) IS TRUE)")
        ex(f"ALTER TABLE public.{ev} ADD CONSTRAINT s015_0021_{e}_decision_shape_ck CHECK (authority_decision_reference ~ '^s015d1:[0-9a-f]{{64}}$')")
        ex(f"ALTER TABLE public.{ev} ADD CONSTRAINT s015_0021_{e}_payload_shape_ck CHECK (payload_fingerprint ~ '^[0-9a-f]{{64}}$')")
        ex(f"ALTER TABLE public.{ev} ADD CONSTRAINT s015_0021_{e}_lineage_shape_ck CHECK (lineage_reference ~ '^s015l1:[0-9a-f]{{64}}$')")
        for col in ("action", "evidence_reference", "request_reference", "idempotency_key", state_col):
            ex(f"ALTER TABLE public.{ev} ADD CONSTRAINT s015_0021_{e}_{col}_canonical_ck CHECK (length({col}) >= 1 AND {col} = btrim({col}) AND {col} IS NFC NORMALIZED)")
        ex(f"ALTER TABLE public.{ev} ADD CONSTRAINT s015_0021_{e}_temporal_ref_canonical_ck CHECK (temporal_basis_reference IS NULL OR "
           "(length(temporal_basis_reference) >= 1 AND temporal_basis_reference = btrim(temporal_basis_reference) AND temporal_basis_reference IS NFC NORMALIZED))")
        ex(f"ALTER TABLE public.{ev} ADD CONSTRAINT s015_0021_{e}_actor_epoch_ck CHECK (actor_access_epoch >= 0)")
        # aggregate-specific canonical references (U21h fix dispatch 2.2)
        for col in AGGREGATE_REFERENCE_COLUMNS.get(ev, ()):
            ex(f"ALTER TABLE public.{ev} ADD CONSTRAINT s015_0021_{e}_{col}_canonical_ck CHECK ({col} IS NULL OR (length({col}) >= 1 AND {col} = btrim({col}) AND {col} IS NFC NORMALIZED))")
        ex(f"COMMENT ON COLUMN public.{ev}.prior_state IS '{PRIOR_STATE_COMMENT}'")
        # head composite FK: the head is an event of this anchor
        ex(f"ALTER TABLE public.{tok} ADD CONSTRAINT s015_0021_{_sq(tok)}_head_fk FOREIGN KEY ({head}, id) REFERENCES public.{ev}(id, {fk}) DEFERRABLE INITIALLY DEFERRED")

    # closed vocabularies on event state columns (specification §5.5 and no other)
    vocab = {
        "core_livingorganismevent": ("resulting_state", ["FOUNDING_PENDING", "ACTIVE", "RESTRICTED_CONTINUITY", "DORMANT", "CLOSED"]),
        "core_circlestateevent": ("resulting_state", ["DORMANT", "ELIGIBLE", "SUSPENDED", "CLOSED"]),
        "core_organismmembershiptransition": ("resulting_state", ["PROPOSED", "ACTIVE", "PROBATIONARY", "RESTRICTED", "SUSPENDED", "ENDED", "PRIVACY_TRANSFORMED"]),
        "core_contextualroleassignmentstateevent": ("resulting_state", ["PROPOSED", "ACTIVE", "PROBATIONARY", "SUSPENDED", "ENDED", "PRIVACY_TRANSFORMED"]),
        "core_coverageassessment": ("result", ["SATISFIED", "UNSATISFIED", "UNKNOWN"]),
        "core_restrictedcontinuityevent": ("resulting_state", ["RESTRICTED", "NOT_RESTRICTED"]),
        "core_visibilitygrantstateevent": ("resulting_state", ["ISSUED", "REVOKED", "EXPIRED", "SUPERSEDED"]),
        "core_planningclassificationevent": ("result", ["ROUTINE_ELIGIBLE", "EXCEPTIONAL_REQUIRED", "UNKNOWN"]),
    }
    for ev, (col, tokens) in vocab.items():
        lst = ", ".join(f"'{t}'" for t in tokens)
        ex(f"ALTER TABLE public.{ev} ADD CONSTRAINT s015_0021_{_sq(ev)}_{col}_vocab_ck CHECK ({col} IN ({lst}))")
        ex(f"ALTER TABLE public.{ev} ADD CONSTRAINT s015_0021_{_sq(ev)}_prior_vocab_ck CHECK (prior_state IS NULL OR prior_state IN ({lst}))")
    ex("ALTER TABLE public.core_circlestateevent ADD CONSTRAINT s015_0021_circlestateevent_no_active_ck CHECK (resulting_state <> 'ACTIVE' AND (prior_state IS NULL OR prior_state <> 'ACTIVE'))")
    ex("ALTER TABLE public.core_contextualroleassignmentstateevent ADD CONSTRAINT s015_0021_cra_action_ck CHECK (action IN ('PROPOSE','ASSIGN','SUSPEND','REASSIGN','END','PRIVACY_TRANSFORM'))")
    ex("ALTER TABLE public.core_authorityinvalidationevent ADD CONSTRAINT s015_0021_ai_kind_ck CHECK (kind IN ('SUSPENSION','INVALIDATION','SUPERSESSION','RESTORATION'))")
    ex("ALTER TABLE public.core_obligationstateevent ADD CONSTRAINT s015_0021_ob_lds_vocab_ck CHECK (legal_deadline_status IN ('QUALIFIED','QUALIFIED_NOT_APPLICABLE','UNKNOWN'))")
    ex("ALTER TABLE public.core_obligationstateevent ADD CONSTRAINT s015_0021_ob_lds_basis_ck CHECK (legal_deadline_status = 'UNKNOWN' OR legal_basis_determination_id IS NOT NULL)")
    ex("ALTER TABLE public.core_obligationstateevent ADD CONSTRAINT s015_0021_ob_unknown_deadline_ck CHECK (legal_deadline_status <> 'UNKNOWN' OR effective_deadline IS NULL)")
    ex("ALTER TABLE public.core_governedobligationcase ADD CONSTRAINT s015_0021_goc_lds_vocab_ck CHECK (legal_deadline_status IN ('QUALIFIED','QUALIFIED_NOT_APPLICABLE','UNKNOWN'))")
    ex("ALTER TABLE public.core_governedobligationcase ADD CONSTRAINT s015_0021_goc_lds_basis_ck CHECK (legal_deadline_status = 'UNKNOWN' OR legal_basis_determination_id IS NOT NULL)")
    ex("ALTER TABLE public.core_governedobligationcase ADD CONSTRAINT s015_0021_goc_unknown_deadline_ck CHECK (legal_deadline_status <> 'UNKNOWN' OR effective_deadline IS NULL)")
    ex("ALTER TABLE public.core_governedobligationcase ADD CONSTRAINT s015_0021_goc_fp_shape_ck CHECK (status_source_event_set_fingerprint ~ '^s015f3:[0-9a-f]{64}$')")
    for col in AGGREGATE_REFERENCE_COLUMNS["core_governedobligationcase"]:
        ex(f"ALTER TABLE public.core_governedobligationcase ADD CONSTRAINT s015_0021_goc_{col}_canonical_ck CHECK ({col} IS NULL OR (length({col}) >= 1 AND {col} = btrim({col}) AND {col} IS NFC NORMALIZED))")
    for ts in ("status_state_at", "status_known_at", "operational_escalation_at"):
        ex(f"ALTER TABLE public.core_governedobligationcase ADD CONSTRAINT s015_0021_goc_{ts}_domain_ck CHECK ({ts} >= '0001-01-01 00:00:00+00'::timestamptz AND {ts} <= '9999-12-31 23:59:59.999999+00'::timestamptz)")
    ex("ALTER TABLE public.core_membershipcondition ADD CONSTRAINT s015_0021_mc_mentor_distinct_ck CHECK (mentor_membership_id IS DISTINCT FROM subject_membership_id)")
    ex("ALTER TABLE public.core_membershipcondition ADD CONSTRAINT s015_0021_mc_mentorship_requires_mentor_ck CHECK (condition_kind <> 'MENTORSHIP' OR mentor_membership_id IS NOT NULL)")
    ex("ALTER TABLE public.core_membershipcondition ADD CONSTRAINT s015_0021_mc_kind_canonical_ck CHECK (length(condition_kind) >= 1 AND condition_kind = btrim(condition_kind) AND condition_kind IS NFC NORMALIZED)")
    ex("ALTER TABLE public.core_essentialcoveragerequirement ADD CONSTRAINT s015_0021_ecr_roster_ck CHECK (roster_version >= 1)")
    ex("ALTER TABLE public.core_essentialcoveragerequirement ADD CONSTRAINT s015_0021_ecr_min_ck CHECK (minimum_active_occupants >= 0)")
    ex("ALTER TABLE public.core_essentialcoveragerequirement ADD CONSTRAINT s015_0021_ecr_interval_ck CHECK (effective_until IS NULL OR effective_from < effective_until)")
    ex("ALTER TABLE public.core_coverageassessment ADD CONSTRAINT s015_0021_ca_roster_ck CHECK (roster_version >= 1)")
    for ts in ("evaluated_state_at", "evaluated_known_at"):
        ex(f"ALTER TABLE public.core_coverageassessment ADD CONSTRAINT s015_0021_ca_{ts}_domain_ck CHECK ({ts} >= '0001-01-01 00:00:00+00'::timestamptz AND {ts} <= '9999-12-31 23:59:59.999999+00'::timestamptz)")

    # cache column comments (design §13 step 6, D-36)
    for tok, *_ in CACHE_ANCHORS:
        cols = ["status_state", "legal_deadline_status", "qualified_legal_deadline", "effective_deadline", "legal_basis_determination_id",
                "status_source_event_set_fingerprint"] if tok == "core_governedobligationcase" else ["state", "state_source_event_set_fingerprint"]
        for c in cols:
            ex(f"COMMENT ON COLUMN public.{tok}.{c} IS '{CACHE_COMMENT}'")

    # ---- triggers ----
    for tok, ident, ev, head, fk, state_col, cache, empty_ok, width in ANCHORS:
        e, a = _sq(ev), _sq(tok)
        ex(f"CREATE TRIGGER s015_0021_{e}_record_time BEFORE INSERT ON public.{ev} FOR EACH ROW EXECUTE FUNCTION public.s015_assign_event_record_time()")
        ex(f"CREATE TRIGGER s015_0021_{e}_update_append_only BEFORE UPDATE ON public.{ev} FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation()")
        ex(f"CREATE TRIGGER s015_0021_{e}_delete_append_only BEFORE DELETE ON public.{ev} FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation()")
        ex(f"CREATE TRIGGER s015_0021_{e}_truncate_append_only BEFORE TRUNCATE ON public.{ev} FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation()")
        ex(f"CREATE TRIGGER s015_0021_{e}_head_advance AFTER INSERT ON public.{ev} FOR EACH ROW EXECUTE FUNCTION public.s015_advance_recorded_head()")
        ex(f"CREATE CONSTRAINT TRIGGER s015_0021_{e}_chain_guard AFTER INSERT ON public.{ev} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.s015_guard_aggregate_chain()")
        ex(f"CREATE TRIGGER s015_0021_{a}_head_guard BEFORE UPDATE OF {head} ON public.{tok} FOR EACH ROW EXECUTE FUNCTION public.s015_guard_head_pointer()")
        if cache:
            if tok == "core_governedobligationcase":
                cache_cols = "status_state, legal_deadline_status, qualified_legal_deadline, effective_deadline, legal_basis_determination_id, status_state_at, status_known_at, status_source_event_set_fingerprint"
                value_cols = "status_state, legal_deadline_status, qualified_legal_deadline, effective_deadline, legal_basis_determination_id, status_source_event_set_fingerprint"
                coord_cols = "status_state_at, status_known_at"
            else:
                cache_cols = "state, current_state_effective_at, state_known_at, state_source_event_set_fingerprint"
                value_cols = "state, state_source_event_set_fingerprint"
                coord_cols = "current_state_effective_at, state_known_at"
            ex(f"CREATE CONSTRAINT TRIGGER s015_0021_{a}_chain_guard AFTER INSERT OR UPDATE OF {head}, {cache_cols} ON public.{tok} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.s015_guard_aggregate_chain()")
            ex(f"CREATE TRIGGER s015_0021_{a}_cache_insert BEFORE INSERT ON public.{tok} FOR EACH ROW EXECUTE FUNCTION public.s015_guard_anchor_insert_cache()")
            ex(f"CREATE TRIGGER s015_0021_{a}_cache_values_refuse BEFORE UPDATE OF {value_cols} ON public.{tok} FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_cache_value_write()")
            ex(f"CREATE TRIGGER s015_0021_{a}_cache_compute BEFORE UPDATE OF {coord_cols} ON public.{tok} FOR EACH ROW EXECUTE FUNCTION public.s015_compute_governed_cache()")
        else:
            ex(f"CREATE CONSTRAINT TRIGGER s015_0021_{a}_chain_guard AFTER INSERT OR UPDATE OF {head} ON public.{tok} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.s015_guard_aggregate_chain()")
    # append-only requirement versions
    ex("CREATE TRIGGER s015_0021_essentialcoveragerequirement_update_append_only BEFORE UPDATE ON public.core_essentialcoveragerequirement FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation()")
    ex("CREATE TRIGGER s015_0021_essentialcoveragerequirement_delete_append_only BEFORE DELETE ON public.core_essentialcoveragerequirement FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation()")
    ex("CREATE TRIGGER s015_0021_essentialcoveragerequirement_truncate_append_only BEFORE TRUNCATE ON public.core_essentialcoveragerequirement FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation()")
    # fixed-column immutability on the six new anchors and spines
    for table, cols in NEW_ANCHOR_FIXED.items():
        for col in cols:
            ex(f"CREATE TRIGGER s015_0021_{_sq(table)}_{col}_immutable BEFORE UPDATE OF {col} ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation()")
        ex(f"CREATE TRIGGER s015_0021_{_sq(table)}_delete_immutable BEFORE DELETE ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation()")
        ex(f"CREATE TRIGGER s015_0021_{_sq(table)}_truncate_immutable BEFORE TRUNCATE ON public.{table} FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation()")
    # core_organismroledefinition: five columns + delete + truncate (U21g-5, U21g-14)
    for col in ROLE_DEFINITION_FIXED:
        ex(f"CREATE TRIGGER s015_0021_organismroledefinition_{col}_immutable BEFORE UPDATE OF {col} ON public.core_organismroledefinition FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation()")
    ex("CREATE TRIGGER s015_0021_organismroledefinition_delete_immutable BEFORE DELETE ON public.core_organismroledefinition FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation()")
    ex("CREATE TRIGGER s015_0021_organismroledefinition_truncate_immutable BEFORE TRUNCATE ON public.core_organismroledefinition FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation()")
    # platform_root immutability (U21g-18)
    ex("CREATE TRIGGER s015_0021_livingorganism_platform_root_immutable BEFORE UPDATE OF platform_root ON public.core_livingorganism FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation()")
    # structural and founding guardians
    ex("CREATE CONSTRAINT TRIGGER s015_0021_contextualroleassignment_structure AFTER INSERT ON public.core_contextualroleassignment DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.s015_guard_assignment_structure()")
    ex("CREATE CONSTRAINT TRIGGER s015_0021_membershipcondition_structure AFTER INSERT ON public.core_membershipcondition DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.s015_guard_membership_condition_structure()")
    ex("CREATE CONSTRAINT TRIGGER s015_0021_coverageassessment_roster AFTER INSERT ON public.core_coverageassessment DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.s015_guard_coverage_assessment_roster()")
    ex("CREATE CONSTRAINT TRIGGER s015_0021_livingorganism_founding_population AFTER INSERT ON public.core_livingorganism DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.s015_validate_founding_population()")
    ex("CREATE CONSTRAINT TRIGGER s015_0021_organismroledefinition_reserved_code AFTER INSERT ON public.core_organismroledefinition DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.s015_guard_reserved_role_code()")

    # route-general owning-organism lock acquisition (seven tables)
    for table in ORGANISM_LOCK_TABLES:
        ex(f"CREATE TRIGGER s015_0021_{_sq(table)}_organism_lock BEFORE INSERT ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.s015_acquire_owning_organism_lock()")


def remove_s015_0021(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    def ex(sql):
        schema_editor.execute(sql, params=None)

    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM public.core_livingorganism WHERE platform_root = TRUE")
        if cursor.fetchone()[0]:
            raise RuntimeError("S015 0021 reversal refused: a designated platform root exists (permanent by Unit Record ruling 22)")
        # U21h-12: the reverse deletes every event table, and with them the recorded
        # chain. Rollback is available only on an empty database; a populated one is
        # corrected by a forward migration, never by erasing its lineage.
        for ev in EVENT_TABLES:
            cursor.execute(f"SELECT count(*) FROM public.{ev}")
            n = cursor.fetchone()[0]
            if n:
                raise RuntimeError(
                    f"S015 0021 reversal refused: {ev} holds {n} governed event(s); "
                    "0021 is irreversible once any governed event exists (U21h-12). "
                    "Correct forward with a successor migration."
                )
    # triggers on shipped tables that survive (new tables are dropped with their triggers)
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("""
            SELECT t.tgname, c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
            WHERE NOT t.tgisinternal AND t.tgname LIKE 's015\\_0021\\_%' ESCAPE '\\'
        """)
        for tgname, relname in cursor.fetchall():
            ex(f"DROP TRIGGER IF EXISTS {tgname} ON public.{relname}")
    for tok, ident, ev, head, fk, *_ in ANCHORS:
        ex(f"ALTER TABLE public.{tok} DROP CONSTRAINT IF EXISTS s015_0021_{_sq(tok)}_head_fk")
    for tok, *_ in CACHE_ANCHORS:
        cols = ["status_state", "legal_deadline_status", "qualified_legal_deadline", "effective_deadline", "legal_basis_determination_id",
                "status_source_event_set_fingerprint"] if tok == "core_governedobligationcase" else ["state", "state_source_event_set_fingerprint"]
        for c in cols:
            ex(f"COMMENT ON COLUMN public.{tok}.{c} IS NULL")
    for fn in ALL_FUNCTIONS:
        ex(f"DROP FUNCTION IF EXISTS public.{fn} CASCADE")
    ex(SHIPPED_FOUNDING_COMPLETION_GUARDIAN_SQL)
    ex("DROP TYPE IF EXISTS public.s015_effective_state_t")
    ex("DROP TYPE IF EXISTS public.s015_obligation_projection_t")
    ex("DROP TABLE IF EXISTS public.s015_transaction_register")


# --------------------------------------------------------------------------------------
# The migration
# --------------------------------------------------------------------------------------
MEMBERSHIP_STATES = [("PROPOSED", "Proposed"), ("ACTIVE", "Active"), ("PROBATIONARY", "Probationary"), ("RESTRICTED", "Restricted"),
                     ("SUSPENDED", "Suspended"), ("ENDED", "Ended"), ("PRIVACY_TRANSFORMED", "Privacy Transformed")]
CRA_STATES = [("PROPOSED", "Proposed"), ("ACTIVE", "Active"), ("PROBATIONARY", "Probationary"), ("SUSPENDED", "Suspended"),
              ("ENDED", "Ended"), ("PRIVACY_TRANSFORMED", "Privacy Transformed")]


def canonical_cf(max_length, **kw):
    return models.CharField(max_length=max_length, **kw)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0020_s015_genesis_identity_completeness"),
    ]

    operations = [
        # 1. preflight — lock, then count, the eight covered shipped tables
        migrations.RunPython(s015_refuse_unless_covered_tables_empty, noop),

        # 2. six AlterField — four fingerprint widenings 64→71; two state widenings 16→19
        migrations.AlterField(model_name="livingorganism", name="state_source_event_set_fingerprint", field=models.CharField(max_length=71)),
        migrations.AlterField(model_name="circle", name="state_source_event_set_fingerprint", field=models.CharField(max_length=71)),
        migrations.AlterField(model_name="organismmembership", name="state_source_event_set_fingerprint", field=models.CharField(max_length=71)),
        migrations.AlterField(model_name="contextualroleassignment", name="state_source_event_set_fingerprint", field=models.CharField(max_length=71)),
        migrations.AlterField(model_name="organismmembership", name="state", field=models.CharField(choices=MEMBERSHIP_STATES, max_length=19)),
        migrations.AlterField(model_name="contextualroleassignment", name="state", field=models.CharField(choices=CRA_STATES, max_length=19)),

        # 3. nineteen CreateModel — seven support tables, then twelve event tables
        migrations.CreateModel(
            name="MembershipCondition",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("condition_uuid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("condition_kind", canonical_cf(48)),
                ("subject_membership", models.ForeignKey(on_delete=PROTECT, related_name="+", to="core.organismmembership")),
                ("mentor_membership", models.ForeignKey(null=True, blank=True, on_delete=PROTECT, related_name="+", to="core.organismmembership")),
            ],
        ),
        migrations.CreateModel(
            name="DeterminationContestCase",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("case_uuid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("determination", models.OneToOneField(on_delete=PROTECT, related_name="+", to="core.governeddetermination")),
            ],
        ),
        migrations.CreateModel(
            name="EssentialCoverageRequirement",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("requirement_uuid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("roster_version", models.PositiveIntegerField()),
                ("responsibility_domain_code", canonical_cf(48)),
                ("qualifying_role_code", canonical_cf(48)),
                ("minimum_active_occupants", models.PositiveIntegerField()),
                ("scope", canonical_cf(20)),
                ("effective_from", models.DateTimeField()),
                ("effective_until", models.DateTimeField(null=True, blank=True)),
                ("living_organism", models.ForeignKey(on_delete=PROTECT, related_name="+", to="core.livingorganism")),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("living_organism", "roster_version", "responsibility_domain_code"), name="s015_0021_requirement_roster_domain_uniq")]},
        ),
        migrations.CreateModel(
            name="EssentialCoverageCase",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("case_uuid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("living_organism", models.OneToOneField(on_delete=PROTECT, related_name="+", to="core.livingorganism")),
            ],
        ),
        migrations.CreateModel(
            name="RestrictedContinuityCase",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("case_uuid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("living_organism", models.OneToOneField(on_delete=PROTECT, related_name="+", to="core.livingorganism")),
            ],
        ),
        migrations.CreateModel(
            name="GovernedObligationCase",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("case_uuid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("asserted_obligation_reference", canonical_cf(255)),
                ("affected_scope_reference", canonical_cf(255)),
                ("responsible_capacities_reference", canonical_cf(255)),
                ("operational_escalation_at", models.DateTimeField()),
                ("status_state", models.CharField(max_length=40)),
                ("legal_deadline_status", models.CharField(max_length=24)),
                ("qualified_legal_deadline", models.DateTimeField(null=True, blank=True)),
                ("effective_deadline", models.DateTimeField(null=True, blank=True)),
                ("status_state_at", models.DateTimeField()),
                ("status_known_at", models.DateTimeField()),
                ("status_source_event_set_fingerprint", models.CharField(max_length=71)),
                ("legal_basis_determination", models.ForeignKey(null=True, blank=True, on_delete=PROTECT, related_name="+", to="core.governeddetermination")),
            ],
        ),
        migrations.CreateModel(
            name="PlanningClassificationCase",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("case_uuid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("item_identifier", models.CharField(max_length=255, unique=True)),
            ],
        ),
        event_model("LivingOrganismEvent", "core.livingorganism", "living_organism", 24),
        event_model("CircleStateEvent", "core.circle", "circle", 16),
        event_model("OrganismMembershipTransition", "core.organismmembership", "membership", 19,
                    extra_fields=[("reason_class", canonical_cf(48))]),
        event_model("ContextualRoleAssignmentStateEvent", "core.contextualroleassignment", "assignment", 19),
        event_model("MembershipConditionStateEvent", "core.membershipcondition", "condition", 48),
        event_model("AuthorityInvalidationEvent", "core.authoritybasis", "basis", 64,
                    extra_fields=[("kind", models.CharField(max_length=12))]),
        event_model("DeterminationContestEvent", "core.determinationcontestcase", "case", 64),
        event_model("CoverageAssessment", "core.essentialcoveragecase", "case", 11, state_col="result",
                    extra_fields=[("roster_version", models.PositiveIntegerField()),
                                  ("evaluated_state_at", models.DateTimeField()),
                                  ("evaluated_known_at", models.DateTimeField()),
                                  ("determiner_capacity_reference", canonical_cf(255))]),
        event_model("RestrictedContinuityEvent", "core.restrictedcontinuitycase", "case", 14,
                    extra_fields=[("permitted_measures_reference", canonical_cf(255)),
                                  ("prohibited_effects_reference", canonical_cf(255)),
                                  ("triggering_assessment", models.ForeignKey(on_delete=PROTECT, related_name="+", to="core.coverageassessment")),
                                  ("accountable_actor", models.ForeignKey(on_delete=PROTECT, related_name="+", to="core.identity"))]),
        event_model("VisibilityGrantStateEvent", "core.governedvisibilitygrant", "grant", 10),
        event_model("ObligationStateEvent", "core.governedobligationcase", "case", 40,
                    extra_fields=[("legal_deadline_status", models.CharField(max_length=24)),
                                  ("qualified_legal_deadline", models.DateTimeField(null=True, blank=True)),
                                  ("effective_deadline", models.DateTimeField(null=True, blank=True)),
                                  ("extension_basis_reference", models.CharField(max_length=255, null=True, blank=True)),
                                  ("escalation_path_reference", models.CharField(max_length=255, null=True, blank=True)),
                                  ("interim_measures_reference", models.CharField(max_length=255, null=True, blank=True)),
                                  ("consequences_reference", models.CharField(max_length=255, null=True, blank=True)),
                                  ("legal_basis_determination", models.ForeignKey(null=True, blank=True, on_delete=PROTECT, related_name="+", to="core.governeddetermination"))]),
        event_model("PlanningClassificationEvent", "core.planningclassificationcase", "case", 20, state_col="result",
                    extra_fields=[("criterion_reference", canonical_cf(255))]),

        # 4. thirteen AddField — twelve head pointers and the root discriminator
        migrations.AddField(model_name="livingorganism", name="head_event", field=models.OneToOneField(null=True, blank=True, on_delete=PROTECT, related_name="+", to="core.livingorganismevent")),
        migrations.AddField(model_name="circle", name="head_event", field=models.OneToOneField(null=True, blank=True, on_delete=PROTECT, related_name="+", to="core.circlestateevent")),
        migrations.AddField(model_name="organismmembership", name="head_transition", field=models.OneToOneField(null=True, blank=True, on_delete=PROTECT, related_name="+", to="core.organismmembershiptransition")),
        migrations.AddField(model_name="contextualroleassignment", name="head_state_event", field=models.OneToOneField(null=True, blank=True, on_delete=PROTECT, related_name="+", to="core.contextualroleassignmentstateevent")),
        migrations.AddField(model_name="membershipcondition", name="head_state_event", field=models.OneToOneField(null=True, blank=True, on_delete=PROTECT, related_name="+", to="core.membershipconditionstateevent")),
        migrations.AddField(model_name="authoritybasis", name="head_invalidation_event", field=models.OneToOneField(null=True, blank=True, on_delete=PROTECT, related_name="+", to="core.authorityinvalidationevent")),
        migrations.AddField(model_name="determinationcontestcase", name="head_event", field=models.OneToOneField(null=True, blank=True, on_delete=PROTECT, related_name="+", to="core.determinationcontestevent")),
        migrations.AddField(model_name="essentialcoveragecase", name="head_assessment", field=models.OneToOneField(null=True, blank=True, on_delete=PROTECT, related_name="+", to="core.coverageassessment")),
        migrations.AddField(model_name="restrictedcontinuitycase", name="head_event", field=models.OneToOneField(null=True, blank=True, on_delete=PROTECT, related_name="+", to="core.restrictedcontinuityevent")),
        migrations.AddField(model_name="governedvisibilitygrant", name="head_state_event", field=models.OneToOneField(null=True, blank=True, on_delete=PROTECT, related_name="+", to="core.visibilitygrantstateevent")),
        migrations.AddField(model_name="governedobligationcase", name="head_state_event", field=models.OneToOneField(null=True, blank=True, on_delete=PROTECT, related_name="+", to="core.obligationstateevent")),
        migrations.AddField(model_name="planningclassificationcase", name="head_event", field=models.OneToOneField(null=True, blank=True, on_delete=PROTECT, related_name="+", to="core.planningclassificationevent")),
        migrations.AddField(model_name="livingorganism", name="platform_root", field=models.BooleanField(default=False, editable=False)),

        # 5. AddConstraint set on shipped tables (constraint additions permitted, U21g-4)
        migrations.AddConstraint(model_name="livingorganism", constraint=models.CheckConstraint(condition=Q(state_source_event_set_fingerprint__regex=r"^s015f3:[0-9a-f]{64}$"), name="s015_0021_livingorganism_fp_shape_ck")),
        migrations.AddConstraint(model_name="circle", constraint=models.CheckConstraint(condition=Q(state_source_event_set_fingerprint__regex=r"^s015f3:[0-9a-f]{64}$"), name="s015_0021_circle_fp_shape_ck")),
        migrations.AddConstraint(model_name="organismmembership", constraint=models.CheckConstraint(condition=Q(state_source_event_set_fingerprint__regex=r"^s015f3:[0-9a-f]{64}$"), name="s015_0021_organismmembership_fp_shape_ck")),
        migrations.AddConstraint(model_name="contextualroleassignment", constraint=models.CheckConstraint(condition=Q(state_source_event_set_fingerprint__regex=r"^s015f3:[0-9a-f]{64}$"), name="s015_0021_contextualroleassignment_fp_shape_ck")),
        migrations.AddConstraint(model_name="livingorganism", constraint=models.CheckConstraint(condition=Q(state__in=["FOUNDING_PENDING", "ACTIVE", "RESTRICTED_CONTINUITY", "DORMANT", "CLOSED"]), name="s015_0021_livingorganism_state_vocab_ck")),
        migrations.AddConstraint(model_name="circle", constraint=models.CheckConstraint(condition=Q(state__in=["DORMANT", "ELIGIBLE", "SUSPENDED", "CLOSED"]), name="s015_0021_circle_state_vocab_ck")),
        migrations.AddConstraint(model_name="organismmembership", constraint=models.CheckConstraint(condition=Q(state__in=[s for s, _ in MEMBERSHIP_STATES]), name="s015_0021_organismmembership_state_vocab_ck")),
        migrations.AddConstraint(model_name="contextualroleassignment", constraint=models.CheckConstraint(condition=Q(state__in=[s for s, _ in CRA_STATES]), name="s015_0021_contextualroleassignment_state_vocab_ck")),
        migrations.AddConstraint(model_name="livingorganism", constraint=models.CheckConstraint(condition=ts_domain_q("current_state_effective_at") & ts_domain_q("state_known_at"), name="s015_0021_livingorganism_coord_domain_ck")),
        migrations.AddConstraint(model_name="circle", constraint=models.CheckConstraint(condition=ts_domain_q("current_state_effective_at") & ts_domain_q("state_known_at"), name="s015_0021_circle_coord_domain_ck")),
        migrations.AddConstraint(model_name="organismmembership", constraint=models.CheckConstraint(condition=ts_domain_q("current_state_effective_at") & ts_domain_q("state_known_at"), name="s015_0021_organismmembership_coord_domain_ck")),
        migrations.AddConstraint(model_name="contextualroleassignment", constraint=models.CheckConstraint(condition=ts_domain_q("current_state_effective_at") & ts_domain_q("state_known_at"), name="s015_0021_contextualroleassignment_coord_domain_ck")),
        migrations.AddConstraint(model_name="organismroledefinition", constraint=models.CheckConstraint(condition=Q(scope__in=["LIVING_ORGANISM", "CIRCLE"]), name="s015_0021_role_scope_domain_ck")),
        migrations.AddConstraint(model_name="organismroledefinition", constraint=models.UniqueConstraint(fields=["living_organism", "code"], condition=Q(active=True), name="s015_0021_role_active_uniq")),
        migrations.AddConstraint(model_name="livingorganism", constraint=models.UniqueConstraint(fields=["platform_root"], condition=Q(platform_root=True), name="s015_platform_root_singleton_uniq")),
        migrations.AddConstraint(model_name="livingorganism", constraint=models.CheckConstraint(condition=Q(platform_root=False), name="s015_platform_root_unset_ck")),

        # 6. the installer
        migrations.RunPython(install_s015_0021, remove_s015_0021),
    ]
