from __future__ import annotations

import hashlib
import pathlib
import re
import textwrap

from django.db import migrations


POSTGRESQL_ONLY_ERROR = "S015 0022 requires PostgreSQL"
APPEND_ONLY_GUARDED_TABLES = (
    "core_livingorganismevent",
    "core_circlestateevent",
    "core_organismmembershiptransition",
    "core_contextualroleassignmentstateevent",
    "core_membershipconditionstateevent",
    "core_authorityinvalidationevent",
    "core_determinationcontestevent",
    "core_coverageassessment",
    "core_restrictedcontinuityevent",
    "core_visibilitygrantstateevent",
    "core_obligationstateevent",
    "core_planningclassificationevent",
    "core_essentialcoveragerequirement",
)

EXPECTED_L2_PREIMAGE_BODY = textwrap.dedent(
    """
    BEGIN
        RAISE EXCEPTION 'S015 U-14: L2 body canonical form is undefined and outside this bound'
            USING ERRCODE = 'integrity_constraint_violation';
    END;
    """
)

RESOLUTION_REUSE_GUARD_BODY = textwrap.dedent(
    """
    BEGIN
        IF EXISTS (
            SELECT 1
            FROM public.core_identityresolutionsevered severed
            WHERE severed.identity_id = NEW.identity_id
        ) THEN
            RAISE EXCEPTION 'S015 R-d: identity reference % is severed and cannot receive another resolution', NEW.identity_id
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END;
    """
)

IDENTITY_GUARD_BODY = textwrap.dedent(
    """
    BEGIN
        RETURN NEW;
    END;
    """
)

RECORD_SEVERANCE_BODY = textwrap.dedent(
    """
    BEGIN
        INSERT INTO public.core_identityresolutionsevered (identity_id) VALUES (OLD.identity_id);
        RETURN OLD;
    END;
    """
)

ASSIGN_PART_RECORD_TIME_BODY = textwrap.dedent(
    """
    BEGIN
        IF NEW.recorded_at IS NOT NULL THEN
            RAISE EXCEPTION 'S015 recorded_at is database-assigned'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        NEW.recorded_at := statement_timestamp();
        RETURN NEW;
    END;
    """
)

DIGEST_BODY = textwrap.dedent(
    """
    BEGIN
        RETURN prefix || ':' || encode(sha256(convert_to(payload, 'UTF8')), 'hex');
    END;
    """
)

L1_PREIMAGE_BODY = textwrap.dedent(
    """
    BEGIN
        RETURN jsonb_build_array(
            's015r1',
            p_table::text,
            public.s015_canonical_event_record(
                p_table,
                jsonb_set(p_row, '{l1_commitment}', 'null'::jsonb, true)
            )::jsonb
        )::text;
    END;
    """
)

PARTS_PREIMAGE_BODY = textwrap.dedent(
    """
    BEGIN
        RETURN jsonb_build_array(
            's015p1',
            p_event_table,
            p_event_uuid::text,
            COALESCE(
                (
                    SELECT jsonb_agg(
                        jsonb_build_array(
                            part_ordinal,
                            part_class,
                            posture,
                            ground,
                            office
                        )
                        ORDER BY part_ordinal
                    )
                    FROM public.core_governedeventpart part
                    WHERE part.event_table = p_event_table
                      AND part.event_uuid = p_event_uuid
                ),
                '[]'::jsonb
            )
        )::text;
    END;
    """
)

GUARD_PARTS_BINDING_BODY = textwrap.dedent(
    """
    DECLARE
        expected_digest text;
        part_stats record;
    BEGIN
        expected_digest := public.s015_0022_digest(
            's015p1',
            public.s015_0022_parts_preimage(TG_TABLE_NAME, NEW.event_uuid)
        );

        SELECT count(*) AS part_count, min(part_ordinal) AS min_ordinal, max(part_ordinal) AS max_ordinal
        INTO part_stats
        FROM public.core_governedeventpart part
        WHERE part.event_table = TG_TABLE_NAME
          AND part.event_uuid = NEW.event_uuid;

        IF part_stats.part_count > 0 AND (part_stats.min_ordinal <> 1 OR part_stats.max_ordinal <> part_stats.part_count) THEN
            RAISE EXCEPTION 'S015 parts commitment refused: ordinal density broken for % %', TG_TABLE_NAME, NEW.event_uuid
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;

        IF NEW.parts_commitment IS DISTINCT FROM expected_digest THEN
            RAISE EXCEPTION 'S015 parts commitment refused: commitment mismatch for % %', TG_TABLE_NAME, NEW.event_uuid
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;

        RETURN NEW;
    END;
    """
)

GUARD_L2_BINDING_BODY = textwrap.dedent(
    """
    DECLARE
        content_count integer;
    BEGIN
        SELECT count(*)
        INTO content_count
        FROM public.core_governedeventcontent content
        WHERE content.event_table = TG_TABLE_NAME
          AND content.event_uuid = NEW.event_uuid;

        IF content_count = 0 AND NEW.l2_commitment IS NOT NULL THEN
            RAISE EXCEPTION 'S015 L2 binding refused: commitment without content for % %', TG_TABLE_NAME, NEW.event_uuid
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;

        IF content_count > 0 AND NEW.l2_commitment IS NULL THEN
            RAISE EXCEPTION 'S015 L2 binding refused: content without commitment for % %', TG_TABLE_NAME, NEW.event_uuid
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;

        RETURN NEW;
    END;
    """
)

GUARD_PREDECESSOR_LINK_BODY = textwrap.dedent(
    """
    DECLARE
        m record;
        anchor_id bigint;
        predecessor_commitment text;
    BEGIN
        SELECT * INTO m FROM public.s015_anchor_map() WHERE event_table = TG_TABLE_NAME;
        IF m IS NULL THEN
            RAISE EXCEPTION 'S015 predecessor link refused: unknown event table %', TG_TABLE_NAME
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;

        anchor_id := (to_jsonb(NEW) ->> m.fk_col)::bigint;

        IF NEW.sequence = 1 THEN
            IF NEW.predecessor_l1_commitment IS NOT NULL THEN
                RAISE EXCEPTION 'S015 predecessor link refused: sequence 1 must not name a predecessor commitment'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END IF;

        EXECUTE format(
            'SELECT l1_commitment FROM public.%I WHERE %I = $1 AND sequence = $2',
            TG_TABLE_NAME,
            m.fk_col
        ) INTO predecessor_commitment USING anchor_id, NEW.sequence - 1;

        IF predecessor_commitment IS NULL OR NEW.predecessor_l1_commitment IS DISTINCT FROM predecessor_commitment THEN
            RAISE EXCEPTION 'S015 predecessor link refused: commitment mismatch for % anchor % sequence %',
                TG_TABLE_NAME, anchor_id, NEW.sequence
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;

        RETURN NEW;
    END;
    """
)

GUARD_CAPACITY_BODY = textwrap.dedent(
    """
    BEGIN
        IF NEW.actor_state = 'PARTY' AND NEW.actor_capacity <> 'STANDING' THEN
            RAISE EXCEPTION 'S015 capacity refused: PARTY rows must be STANDING'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;

        IF NEW.actor_state <> 'PARTY' AND NEW.actor_capacity <> 'NA_NO_PARTY' THEN
            RAISE EXCEPTION 'S015 capacity refused: non-PARTY rows must be NA_NO_PARTY'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;

        RETURN NEW;
    END;
    """
)

GUARD_PART_REFERENT_BODY = textwrap.dedent(
    """
    DECLARE
        m record;
        parent_parts_commitment text;
        expected_parts_commitment text;
    BEGIN
        SELECT * INTO m FROM public.s015_anchor_map() WHERE event_table = NEW.event_table;
        IF m IS NULL THEN
            RAISE EXCEPTION 'S015 part referent refused: unknown event table %', NEW.event_table
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;

        EXECUTE format(
            'SELECT parts_commitment FROM public.%I WHERE event_uuid = $1',
            NEW.event_table
        ) INTO parent_parts_commitment USING NEW.event_uuid;

        expected_parts_commitment := public.s015_0022_digest(
            's015p1',
            public.s015_0022_parts_preimage(NEW.event_table, NEW.event_uuid)
        );

        IF parent_parts_commitment IS NULL OR parent_parts_commitment IS DISTINCT FROM expected_parts_commitment THEN
            RAISE EXCEPTION 'S015 part referent refused: parts commitment mismatch for % %', NEW.event_table, NEW.event_uuid
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;

        RETURN NEW;
    END;
    """
)

GUARD_CONTENT_REFERENT_BODY = textwrap.dedent(
    """
    DECLARE
        parent_l2_commitment text;
    BEGIN
        EXECUTE format(
            'SELECT l2_commitment FROM public.%I WHERE event_uuid = $1',
            NEW.event_table
        ) INTO parent_l2_commitment USING NEW.event_uuid;

        IF parent_l2_commitment IS NULL THEN
            RAISE EXCEPTION 'S015 content referent refused: missing parent commitment for % %', NEW.event_table, NEW.event_uuid
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;

        RETURN NEW;
    END;
    """
)

EVENT_TABLE_SPECS = (
    ("core_livingorganismevent", "living_organism_id"),
    ("core_circlestateevent", "circle_id"),
    ("core_organismmembershiptransition", "membership_id"),
    ("core_contextualroleassignmentstateevent", "assignment_id"),
    ("core_membershipconditionstateevent", "condition_id"),
    ("core_authorityinvalidationevent", "basis_id"),
    ("core_determinationcontestevent", "case_id"),
    ("core_coverageassessment", "case_id"),
    ("core_restrictedcontinuityevent", "case_id"),
    ("core_visibilitygrantstateevent", "grant_id"),
    ("core_obligationstateevent", "case_id"),
    ("core_planningclassificationevent", "case_id"),
)

EVENT_BASE_COLUMNS = (
    ("actor_state", "varchar(15) NOT NULL"),
    ("actor_capacity", "varchar(16) NOT NULL"),
    ("enterer_identity_id", "bigint NULL REFERENCES public.core_identity(id) ON DELETE RESTRICT"),
    ("entry_mode", "varchar(9) NOT NULL"),
    ("composing_rule_state", "varchar(12) NOT NULL"),
    ("composing_rule_reference", "varchar(255) NULL"),
    ("composer_state", "varchar(15) NOT NULL"),
    ("composer_identity_id", "bigint NULL REFERENCES public.core_identity(id) ON DELETE RESTRICT"),
    ("effective_until", "timestamptz NULL"),
    ("effective_until_state", "varchar(15) NOT NULL"),
    ("parts_commitment", "varchar(71) NOT NULL"),
    ("l1_commitment", "varchar(71) NOT NULL"),
    ("predecessor_l1_commitment", "varchar(71) NULL"),
    ("l2_commitment", "varchar(71) NULL"),
)

EVENT_BASE_CHECKS = (
    ("actor_state_vocab_ck", "actor_state IN ('PARTY','NONE','NOT_ESTABLISHED')"),
    ("actor_pointer_ck", "(actor_state = 'PARTY') = (actor_id IS NOT NULL)"),
    ("actor_epoch_ck2", "(actor_state = 'PARTY') = (actor_access_epoch IS NOT NULL)"),
    ("actor_capacity_vocab_ck", "actor_capacity IN ('STANDING','NA_NO_PARTY')"),
    ("actor_capacity_state_ck", "(actor_state = 'PARTY') = (actor_capacity = 'STANDING')"),
    ("entry_mode_vocab_ck", "entry_mode IN ('TRANSPORT','COMPOSED')"),
    ("rule_state_vocab_ck", "composing_rule_state IN ('NA_TRANSPORT','REFERENCE','NOT_NAMED')"),
    ("rule_state_mode_ck", "(entry_mode = 'TRANSPORT') = (composing_rule_state = 'NA_TRANSPORT')"),
    ("rule_reference_ck", "(composing_rule_state = 'REFERENCE') = (composing_rule_reference IS NOT NULL)"),
    ("rule_reference_canonical_ck", "composing_rule_reference IS NULL OR (length(composing_rule_reference) >= 1 AND composing_rule_reference = btrim(composing_rule_reference) AND composing_rule_reference IS NFC NORMALIZED)"),
    ("actorless_composed_ck", "actor_state <> 'NONE' OR (entry_mode = 'COMPOSED' AND composing_rule_state = 'REFERENCE')"),
    ("composer_state_vocab_ck", "composer_state IN ('REFERENCE','NOT_ESTABLISHED','NA_TRANSPORT')"),
    ("composer_state_mode_ck", "(entry_mode = 'TRANSPORT') = (composer_state = 'NA_TRANSPORT')"),
    ("composer_reference_ck", "(composer_state = 'REFERENCE') = (composer_identity_id IS NOT NULL)"),
    ("until_state_vocab_ck", "effective_until_state IN ('FIXED','NONE','NOT_ESTABLISHED')"),
    ("until_value_ck", "(effective_until_state = 'FIXED') = (effective_until IS NOT NULL)"),
    ("until_after_from_ck", "effective_until IS NULL OR effective_until > effective_at"),
    ("until_domain_ck", "effective_until IS NULL OR effective_until BETWEEN '0001-01-01' AND '9999-12-31 23:59:59.999999'"),
    ("parts_commitment_shape_ck", "parts_commitment ~ '^s015p1:[0-9a-f]{64}$'"),
    ("l1_commitment_shape_ck", "l1_commitment ~ '^s015r1:[0-9a-f]{64}$'"),
    ("pred_commitment_shape_ck", "predecessor_l1_commitment IS NULL OR predecessor_l1_commitment ~ '^s015r1:[0-9a-f]{64}$'"),
    ("pred_commitment_seq_ck", "(sequence = 1) = (predecessor_l1_commitment IS NULL)"),
    ("l2_commitment_shape_ck", "l2_commitment IS NULL OR l2_commitment ~ '^s015c1:[0-9a-f]{64}$'"),
)

EVENT_BASE_UNIQUE_DROPPED = ("idem_uniq",)
EVENT_BASE_UNIQUE_ADDED = (
    ("named_actor_idem_uidx", "UNIQUE (actor_id, action, idempotency_key) WHERE actor_state = 'PARTY' AND actor_id IS NOT NULL"),
    ("actorless_op_uidx", "UNIQUE ({fk_col}, actor_state, action, idempotency_key) WHERE actor_state <> 'PARTY'"),
)

EVENT_TRIGGER_SUFFIXES = (
    ("l1_commit", "BEFORE INSERT", "public.s015_0022_assign_l1_commitment()", False),
    ("parts_binding", "AFTER INSERT", "public.s015_0022_guard_parts_binding()", True),
    ("l2_binding", "AFTER INSERT", "public.s015_0022_guard_l2_binding()", True),
    ("predecessor_link", "AFTER INSERT", "public.s015_0022_guard_predecessor_link()", True),
    ("capacity", "AFTER INSERT", "public.s015_0022_guard_capacity()", True),
)

PART_TABLE_SQL = """
        CREATE TABLE public.core_governedeventpart (
            id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            event_table varchar(48) NOT NULL,
            event_uuid uuid NOT NULL,
            part_ordinal integer NOT NULL,
            part_class varchar(16) NOT NULL,
            posture varchar(11) NOT NULL,
            ground varchar(255) NOT NULL,
            office varchar(16) NULL,
            recorded_at timestamptz NOT NULL,
            CONSTRAINT s015_0022_part_identity_uniq UNIQUE (event_table, event_uuid, part_ordinal),
            CONSTRAINT s015_0022_part_table_vocab_ck CHECK (event_table IN ('core_livingorganismevent','core_circlestateevent','core_organismmembershiptransition','core_contextualroleassignmentstateevent','core_membershipconditionstateevent','core_authorityinvalidationevent','core_determinationcontestevent','core_coverageassessment','core_restrictedcontinuityevent','core_visibilitygrantstateevent','core_obligationstateevent','core_planningclassificationevent')),
            CONSTRAINT s015_0022_part_ordinal_ck CHECK (part_ordinal >= 1),
            CONSTRAINT s015_0022_part_posture_vocab_ck CHECK (posture IN ('OPEN','HELD_CLOSED')),
            CONSTRAINT s015_0022_part_office_entailed_ck CHECK ((posture = 'HELD_CLOSED') = (office IS NOT NULL)),
            CONSTRAINT s015_0022_part_ground_canonical_ck CHECK (length(ground) >= 1 AND ground = btrim(ground) AND ground IS NFC NORMALIZED),
            CONSTRAINT s015_0022_part_class_canonical_ck CHECK (length(part_class) >= 1 AND part_class = btrim(part_class) AND part_class IS NFC NORMALIZED),
            CONSTRAINT s015_0022_part_office_canonical_ck CHECK (office IS NULL OR (length(office) >= 1 AND office = btrim(office) AND office IS NFC NORMALIZED)),
            CONSTRAINT s015_0022_part_recorded_not_null_ck CHECK (recorded_at IS NOT NULL)
        )
"""

CONTENT_TABLE_SQL = """
        CREATE TABLE public.core_governedeventcontent (
            id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            event_table varchar(48) NOT NULL,
            event_uuid uuid NOT NULL,
            body jsonb NOT NULL,
            commitment_key bytea NOT NULL,
            recorded_at timestamptz NOT NULL,
            CONSTRAINT s015_0022_content_identity_uniq UNIQUE (event_table, event_uuid),
            CONSTRAINT s015_0022_content_table_vocab_ck CHECK (event_table IN ('core_livingorganismevent','core_circlestateevent','core_organismmembershiptransition','core_contextualroleassignmentstateevent','core_membershipconditionstateevent','core_authorityinvalidationevent','core_determinationcontestevent','core_coverageassessment','core_restrictedcontinuityevent','core_visibilitygrantstateevent','core_obligationstateevent','core_planningclassificationevent')),
            CONSTRAINT s015_0022_content_key_length_ck CHECK (octet_length(commitment_key) = 32),
            CONSTRAINT s015_0022_content_recorded_not_null_ck CHECK (recorded_at IS NOT NULL)
        )
"""

FUNCTIONS_SQL = f"""
CREATE OR REPLACE FUNCTION public.s015_0022_digest(prefix text, payload text)
RETURNS text LANGUAGE plpgsql IMMUTABLE AS $$
{DIGEST_BODY}$$;

CREATE OR REPLACE FUNCTION public.s015_0022_assign_part_record_time()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
{ASSIGN_PART_RECORD_TIME_BODY}$$;

CREATE OR REPLACE FUNCTION public.s015_0022_l1_preimage(p_table regclass, p_row jsonb)
RETURNS text LANGUAGE plpgsql STABLE AS $$
{L1_PREIMAGE_BODY}$$;

CREATE OR REPLACE FUNCTION public.s015_0022_parts_preimage(p_event_table text, p_event_uuid uuid)
RETURNS text LANGUAGE plpgsql STABLE AS $$
{PARTS_PREIMAGE_BODY}$$;

CREATE OR REPLACE FUNCTION public.s015_0022_assign_l1_commitment()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    IF NEW.l1_commitment IS NOT NULL THEN
        RAISE EXCEPTION 'S015 l1_commitment is database-assigned'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    NEW.l1_commitment := public.s015_0022_digest('s015r1', public.s015_0022_l1_preimage(TG_TABLE_NAME::regclass, to_jsonb(NEW)));
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.s015_0022_guard_parts_binding()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
{GUARD_PARTS_BINDING_BODY}$$;

CREATE OR REPLACE FUNCTION public.s015_0022_guard_l2_binding()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
{GUARD_L2_BINDING_BODY}$$;

CREATE OR REPLACE FUNCTION public.s015_0022_guard_predecessor_link()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
{GUARD_PREDECESSOR_LINK_BODY}$$;

CREATE OR REPLACE FUNCTION public.s015_0022_guard_capacity()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
{GUARD_CAPACITY_BODY}$$;

CREATE OR REPLACE FUNCTION public.s015_0022_guard_part_referent()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
{GUARD_PART_REFERENT_BODY}$$;

CREATE OR REPLACE FUNCTION public.s015_0022_guard_content_referent()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
{GUARD_CONTENT_REFERENT_BODY}$$;
"""


def _event_model_suffix(table_name):
    return table_name.removeprefix("core_")


def _event_tables():
    return [table for table, _ in EVENT_TABLE_SPECS]


def _event_fk(table_name):
    for candidate, fk_col in EVENT_TABLE_SPECS:
        if candidate == table_name:
            return fk_col
    raise KeyError(table_name)


def _add_event_table_updates(schema_editor):
    for table_name, fk_col in EVENT_TABLE_SPECS:
        suffix = _event_model_suffix(table_name)
        for column_name, column_sql in EVENT_BASE_COLUMNS:
            _execute(
                schema_editor,
                f"ALTER TABLE public.{table_name} ADD COLUMN IF NOT EXISTS {column_name} {column_sql}",
            )
        _execute(schema_editor, f"ALTER TABLE public.{table_name} ALTER COLUMN actor_id DROP NOT NULL")
        _execute(schema_editor, f"ALTER TABLE public.{table_name} ALTER COLUMN actor_access_epoch DROP NOT NULL")

        for constraint_name, expression in EVENT_BASE_CHECKS:
            _execute(
                schema_editor,
                f"ALTER TABLE public.{table_name} ADD CONSTRAINT s015_0022_{suffix}_{constraint_name} CHECK ({expression})",
            )

        _execute(
            schema_editor,
            f"ALTER TABLE public.{table_name} DROP CONSTRAINT IF EXISTS s015_0021_{suffix}_idem_uniq",
        )
        _execute(
            schema_editor,
            f"CREATE UNIQUE INDEX IF NOT EXISTS s015_0022_{suffix}_named_actor_idem_uidx ON public.{table_name} (actor_id, action, idempotency_key) WHERE actor_state = 'PARTY' AND actor_id IS NOT NULL",
        )
        _execute(
            schema_editor,
            f"CREATE UNIQUE INDEX IF NOT EXISTS s015_0022_{suffix}_actorless_op_uidx ON public.{table_name} ({fk_col}, actor_state, action, idempotency_key) WHERE actor_state <> 'PARTY'",
        )

        _execute(
            schema_editor,
            f"DROP TRIGGER IF EXISTS s015_0022_{suffix}_l1_commit ON public.{table_name}",
        )
        _execute(
            schema_editor,
            f"DROP TRIGGER IF EXISTS s015_0022_{suffix}_parts_binding ON public.{table_name}",
        )
        _execute(
            schema_editor,
            f"DROP TRIGGER IF EXISTS s015_0022_{suffix}_l2_binding ON public.{table_name}",
        )
        _execute(
            schema_editor,
            f"DROP TRIGGER IF EXISTS s015_0022_{suffix}_predecessor_link ON public.{table_name}",
        )
        _execute(
            schema_editor,
            f"DROP TRIGGER IF EXISTS s015_0022_{suffix}_capacity ON public.{table_name}",
        )
        for trigger_suffix, timing, function_signature, constraint in EVENT_TRIGGER_SUFFIXES:
            if constraint:
                trigger_sql = f"CREATE CONSTRAINT TRIGGER s015_0022_{suffix}_{trigger_suffix} AFTER INSERT ON public.{table_name} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {function_signature}"
            elif trigger_suffix == "l1_commit":
                trigger_sql = f"CREATE TRIGGER s015_0022_{suffix}_{trigger_suffix} BEFORE INSERT ON public.{table_name} FOR EACH ROW EXECUTE FUNCTION {function_signature}"
            else:
                trigger_sql = f"CREATE CONSTRAINT TRIGGER s015_0022_{suffix}_{trigger_suffix} AFTER INSERT ON public.{table_name} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {function_signature}"
            _execute(schema_editor, trigger_sql)


def _add_part_and_content_tables(schema_editor):
    _execute(schema_editor, PART_TABLE_SQL)
    _execute(schema_editor, CONTENT_TABLE_SQL)

    for trigger_name, table_name, timing, function_signature in (
        ("s015_0022_part_record_time", "public.core_governedeventpart", "BEFORE INSERT", "public.s015_0022_assign_part_record_time()"),
        ("s015_0022_part_append_only_update", "public.core_governedeventpart", "BEFORE UPDATE", "public.s015_refuse_row_mutation()"),
        ("s015_0022_part_append_only_delete", "public.core_governedeventpart", "BEFORE DELETE", "public.s015_refuse_row_mutation()"),
        ("s015_0022_part_append_only_truncate", "public.core_governedeventpart", "BEFORE TRUNCATE", "public.s015_refuse_row_mutation()"),
        ("s015_0022_part_referent", "public.core_governedeventpart", "AFTER INSERT", "public.s015_0022_guard_part_referent()"),
        ("s015_0022_content_record_time", "public.core_governedeventcontent", "BEFORE INSERT", "public.s015_0022_assign_part_record_time()"),
        ("s015_0022_content_append_only_update", "public.core_governedeventcontent", "BEFORE UPDATE", "public.s015_refuse_row_mutation()"),
        ("s015_0022_content_append_only_delete", "public.core_governedeventcontent", "BEFORE DELETE", "public.s015_refuse_row_mutation()"),
        ("s015_0022_content_append_only_truncate", "public.core_governedeventcontent", "BEFORE TRUNCATE", "public.s015_refuse_row_mutation()"),
        ("s015_0022_content_referent", "public.core_governedeventcontent", "AFTER INSERT", "public.s015_0022_guard_content_referent()"),
    ):
        if "TRUNCATE" in timing:
            _execute(
                schema_editor,
                f"CREATE TRIGGER {trigger_name} {timing} ON {table_name} FOR EACH STATEMENT EXECUTE FUNCTION {function_signature}",
            )
        elif trigger_name in ("s015_0022_part_referent", "s015_0022_content_referent"):
            _execute(
                schema_editor,
                f"CREATE CONSTRAINT TRIGGER {trigger_name} {timing} ON {table_name} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {function_signature}",
            )
        else:
            _execute(
                schema_editor,
                f"CREATE TRIGGER {trigger_name} {timing} ON {table_name} FOR EACH ROW EXECUTE FUNCTION {function_signature}",
            )


def _drop_event_table_updates(schema_editor):
    for table_name, fk_col in EVENT_TABLE_SPECS:
        suffix = _event_model_suffix(table_name)
        for trigger_suffix in ("l1_commit", "parts_binding", "l2_binding", "predecessor_link", "capacity"):
            _execute(schema_editor, f"DROP TRIGGER IF EXISTS s015_0022_{suffix}_{trigger_suffix} ON public.{table_name}")
        _execute(schema_editor, f"DROP INDEX IF EXISTS public.s015_0022_{suffix}_named_actor_idem_uidx")
        _execute(schema_editor, f"DROP INDEX IF EXISTS public.s015_0022_{suffix}_actorless_op_uidx")
        _execute(schema_editor, f"ALTER TABLE public.{table_name} DROP CONSTRAINT IF EXISTS s015_0022_{suffix}_actor_state_vocab_ck")
        for constraint_name, _expression in EVENT_BASE_CHECKS:
            _execute(schema_editor, f"ALTER TABLE public.{table_name} DROP CONSTRAINT IF EXISTS s015_0022_{suffix}_{constraint_name}")
        _execute(schema_editor, f"ALTER TABLE public.{table_name} ALTER COLUMN actor_id SET NOT NULL")
        _execute(schema_editor, f"ALTER TABLE public.{table_name} ALTER COLUMN actor_access_epoch SET NOT NULL")
        _execute(
            schema_editor,
            f"ALTER TABLE public.{table_name} ADD CONSTRAINT s015_0021_{suffix}_idem_uniq UNIQUE (actor_id, action, idempotency_key)",
        )
        for column_name, _column_sql in EVENT_BASE_COLUMNS:
            _execute(schema_editor, f"ALTER TABLE public.{table_name} DROP COLUMN IF EXISTS {column_name}")


def _drop_part_and_content_tables(schema_editor):
    for trigger_name, table_name in (
        ("s015_0022_content_referent", "public.core_governedeventcontent"),
        ("s015_0022_content_append_only_truncate", "public.core_governedeventcontent"),
        ("s015_0022_content_append_only_delete", "public.core_governedeventcontent"),
        ("s015_0022_content_append_only_update", "public.core_governedeventcontent"),
        ("s015_0022_content_record_time", "public.core_governedeventcontent"),
        ("s015_0022_part_referent", "public.core_governedeventpart"),
        ("s015_0022_part_append_only_truncate", "public.core_governedeventpart"),
        ("s015_0022_part_append_only_delete", "public.core_governedeventpart"),
        ("s015_0022_part_append_only_update", "public.core_governedeventpart"),
        ("s015_0022_part_record_time", "public.core_governedeventpart"),
    ):
        _execute(schema_editor, f"DROP TRIGGER IF EXISTS {trigger_name} ON {table_name}")
    _execute(schema_editor, "DROP TABLE IF EXISTS public.core_governedeventcontent")
    _execute(schema_editor, "DROP TABLE IF EXISTS public.core_governedeventpart")


def _create_resolution_tables(schema_editor):
    _execute(
        schema_editor,
        """
        CREATE TABLE public.core_identityresolution (
            identity_id bigint PRIMARY KEY REFERENCES public.core_identity(id) ON DELETE RESTRICT,
            display_name varchar(255) NOT NULL,
            canonical_username varchar(150) NOT NULL UNIQUE,
            credential_link bigint NOT NULL REFERENCES public.auth_user(id) ON DELETE RESTRICT
        )
        """,
    )

    _execute(
        schema_editor,
        """
        CREATE TABLE public.core_identityresolutionsevered (
            identity_id bigint PRIMARY KEY REFERENCES public.core_identity(id) ON DELETE RESTRICT,
            severed_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """,
    )


def noop(apps, schema_editor):
    return None


def _execute(schema_editor, sql):
    schema_editor.execute(sql, params=None)


def _require_postgresql(schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        raise RuntimeError(POSTGRESQL_ONLY_ERROR)


def _normalize_line_endings(text):
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _derive_refuse_row_mutation_body():
    source_path = pathlib.Path(__file__).with_name("0019_s015_living_organism_foundation.py")
    with source_path.open("r", encoding="utf-8", newline="") as source_file:
        source_text = source_file.read()
    block_match = re.search(
        r"(CREATE OR REPLACE FUNCTION s015_refuse_row_mutation\(\).*?\$\$;)",
        source_text,
        re.DOTALL,
    )
    if not block_match:
        raise RuntimeError("S015 0022 preflight halt: unable to derive s015_refuse_row_mutation() body from 0019")
    block = block_match.group(1)
    body_match = re.search(r"\$\$(.*?)\$\$", block, re.DOTALL)
    if not body_match:
        raise RuntimeError("S015 0022 preflight halt: s015_refuse_row_mutation() body not found in 0019")
    body = _normalize_line_endings(body_match.group(1))
    if not body.startswith("\n"):
        raise RuntimeError("S015 0022 preflight halt: s015_refuse_row_mutation() body lost its leading newline")
    return body, hashlib.sha256(body.encode("utf-8")).hexdigest()


def _digest_mismatch_message(expected_body, actual_body, expected_digest, actual_digest):
    return (
        "S015 0022 preflight halt: s015_refuse_row_mutation() body digest mismatch\n"
        f"expected_digest={expected_digest}\n"
        f"actual_pg_proc_digest={actual_digest}\n"
        f"len(expected_body)={len(expected_body)}\n"
        f"len(prosrc)={len(actual_body)}\n"
        f"expected_preview={repr(expected_body[:80])}\n"
        f"actual_preview={repr(actual_body[:80])}"
    )


def s015_0022_preflight(apps, schema_editor):
    _require_postgresql(schema_editor)
    expected_body, expected_digest = _derive_refuse_row_mutation_body()

    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            """
                        SELECT p.prosrc
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'public'
              AND p.proname = 's015_refuse_row_mutation'
              AND p.pronargs = 0
              AND p.prorettype = 'trigger'::regtype
            """
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("S015 0022 preflight halt: s015_refuse_row_mutation() missing")
        (actual_body,) = row
        actual_body = _normalize_line_endings(actual_body)
        actual_digest = hashlib.sha256(actual_body.encode("utf-8")).hexdigest()
        if actual_digest != expected_digest or actual_body != expected_body:
            raise RuntimeError(
                _digest_mismatch_message(expected_body, actual_body, expected_digest, actual_digest)
            )

        cursor.execute(
            """
            SELECT t.tgname, c.relname, p.proname, pn.nspname, p.pronargs, p.prorettype = 'trigger'::regtype, t.tgtype, t.tgenabled, t.tgqual IS NULL, t.tgattr = ''::int2vector
            FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
            JOIN pg_proc p ON p.oid = t.tgfoid
            JOIN pg_namespace pn ON pn.oid = p.pronamespace
            WHERE c.relname = ANY(%s) AND NOT t.tgisinternal
            ORDER BY t.tgname
            """,
            [list(APPEND_ONLY_GUARDED_TABLES)],
        )
        trigger_rows = cursor.fetchall()

    required_trigger_names = {
        f"s015_0021_{token}_{operation}_append_only"
        for token in (
            "loevent",
            "circleevent",
            "memtransition",
            "craevent",
            "mcevent",
            "aievent",
            "dcevent",
            "assessment",
            "rcevent",
            "vgevent",
            "obevent",
            "pcevent",
            "essentialcoveragerequirement",
        )
        for operation in ("update", "delete", "truncate")
    }
    found_trigger_names = {row[0] for row in trigger_rows if row[0] in required_trigger_names}
    missing = sorted(required_trigger_names - found_trigger_names)
    if missing:
        raise RuntimeError("S015 0022 preflight halt: required 0021 append-only triggers missing: " + ", ".join(missing))

    trigger_index = {row[0]: row for row in trigger_rows}
    for trigger_name in required_trigger_names:
        tgname, relname, proname, proc_schema, pronargs, returns_trigger, tgtype, tgenabled, unconditional, all_columns = trigger_index[trigger_name]
        if relname not in APPEND_ONLY_GUARDED_TABLES:
            raise RuntimeError(f"S015 0022 preflight halt: {tgname} bound to wrong table {relname}")
        if proname != "s015_refuse_row_mutation" or proc_schema != "public" or pronargs != 0 or not returns_trigger:
            raise RuntimeError(f"S015 0022 preflight halt: {tgname} does not execute public.s015_refuse_row_mutation()")
        if tgenabled not in ("O", "A"):
            raise RuntimeError(f"S015 0022 preflight halt: {tgname} disabled")
        if not unconditional or not all_columns:
            raise RuntimeError(f"S015 0022 preflight halt: {tgname} has restricted firing contract")


def install_s015_0022(apps, schema_editor):
    _require_postgresql(schema_editor)
    _create_resolution_tables(schema_editor)

    _execute(
        schema_editor,
        f"""
        CREATE OR REPLACE FUNCTION public.s015_0022_l2_preimage(p_commitment_key bytea, p_body jsonb)
        RETURNS bytea LANGUAGE plpgsql STABLE AS $$
{EXPECTED_L2_PREIMAGE_BODY}$$;

        CREATE OR REPLACE FUNCTION public.s015_0022_guard_resolution_reuse()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
{RESOLUTION_REUSE_GUARD_BODY}$$;

        CREATE OR REPLACE FUNCTION public.s015_0022_record_severance()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
{RECORD_SEVERANCE_BODY}$$;

        CREATE OR REPLACE FUNCTION public.s015_0022_guard_identity_resolution()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
{IDENTITY_GUARD_BODY}$$;
        """,
    )

    _execute(schema_editor, FUNCTIONS_SQL)

    _execute(schema_editor, "REVOKE ALL ON public.core_identityresolutionsevered FROM PUBLIC")
    _execute(schema_editor, "REVOKE ALL ON FUNCTION public.s015_0022_guard_resolution_reuse() FROM PUBLIC")
    _execute(schema_editor, "REVOKE ALL ON FUNCTION public.s015_0022_record_severance() FROM PUBLIC")
    _execute(schema_editor, "REVOKE ALL ON FUNCTION public.s015_0022_guard_identity_resolution() FROM PUBLIC")
    _execute(schema_editor, "REVOKE ALL ON FUNCTION public.s015_0022_l2_preimage(bytea, jsonb) FROM PUBLIC")

    _execute(
        schema_editor,
        """
        CREATE TRIGGER s015_0022_resolution_reuse
        BEFORE INSERT ON public.core_identityresolution
        FOR EACH ROW EXECUTE FUNCTION public.s015_0022_guard_resolution_reuse()
        """,
    )
    _execute(
        schema_editor,
        """
        CREATE TRIGGER s015_0022_resolution_severance
        AFTER DELETE ON public.core_identityresolution
        FOR EACH ROW EXECUTE FUNCTION public.s015_0022_record_severance()
        """,
    )
    _execute(
        schema_editor,
        """
        CREATE TRIGGER s015_0022_guard_identity_resolution
        AFTER INSERT ON public.core_identity
        FOR EACH ROW EXECUTE FUNCTION public.s015_0022_guard_identity_resolution()
        """,
    )

    _add_event_table_updates(schema_editor)
    _add_part_and_content_tables(schema_editor)

    for operation in ("update", "delete", "truncate"):
        trigger_name = f"s015_0022_severed_{operation}_append_only"
        timing = "BEFORE TRUNCATE" if operation == "truncate" else f"BEFORE {operation.upper()}"
        level = "STATEMENT" if operation == "truncate" else "ROW"
        _execute(
            schema_editor,
            f"""
            CREATE TRIGGER {trigger_name}
            {timing} ON public.core_identityresolutionsevered
            FOR EACH {level} EXECUTE FUNCTION public.s015_refuse_row_mutation()
            """,
        )


def remove_s015_0022(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    _drop_part_and_content_tables(schema_editor)
    _drop_event_table_updates(schema_editor)

    for trigger_name, table_name in (
        ("s015_0022_guard_identity_resolution", "public.core_identity"),
        ("s015_0022_resolution_severance", "public.core_identityresolution"),
        ("s015_0022_resolution_reuse", "public.core_identityresolution"),
        ("s015_0022_severed_update_append_only", "public.core_identityresolutionsevered"),
        ("s015_0022_severed_delete_append_only", "public.core_identityresolutionsevered"),
        ("s015_0022_severed_truncate_append_only", "public.core_identityresolutionsevered"),
    ):
        _execute(schema_editor, f"DROP TRIGGER IF EXISTS {trigger_name} ON {table_name}")

    for function_signature in (
        "public.s015_0022_guard_identity_resolution()",
        "public.s015_0022_record_severance()",
        "public.s015_0022_guard_resolution_reuse()",
        "public.s015_0022_l2_preimage(bytea, jsonb)",
        "public.s015_0022_digest(text, text)",
        "public.s015_0022_assign_part_record_time()",
        "public.s015_0022_l1_preimage(regclass, jsonb)",
        "public.s015_0022_parts_preimage(text, uuid)",
        "public.s015_0022_assign_l1_commitment()",
        "public.s015_0022_guard_parts_binding()",
        "public.s015_0022_guard_l2_binding()",
        "public.s015_0022_guard_predecessor_link()",
        "public.s015_0022_guard_capacity()",
        "public.s015_0022_guard_part_referent()",
        "public.s015_0022_guard_content_referent()",
    ):
        _execute(schema_editor, f"DROP FUNCTION IF EXISTS {function_signature}")

    _execute(schema_editor, "DROP TABLE IF EXISTS public.core_identityresolutionsevered")
    _execute(schema_editor, "DROP TABLE IF EXISTS public.core_identityresolution")


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0021_s015_recorded_chain_and_bitemporal_fold"),
    ]

    operations = [
        migrations.RunPython(s015_0022_preflight, noop),
        migrations.RunPython(install_s015_0022, remove_s015_0022),
    ]