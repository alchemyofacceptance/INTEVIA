"""TIMED EVENTS engine storage: receipts, pace, the busy meter, the consumer registry and the ruled limits.

A receipt is the engine's immutable lineage for one due occurrence. It binds the LO,
the registered consumer and purpose, the source, the occurrence, the actual effect,
the rule, the original due time, the time the effect opened and the time the engine
recorded it. Domain tables stay authoritative: a receipt never decides what is due.

The limits are one platform GovernedRule version, bound to the Human Governor's
ruling of 11 November 2026. No later version is admitted and no setter exists; with
no valid version the engine skips the wake. Changing the limits needs a migration
that cites a new ruling, until the controlled root act is built (C1-105 item 2).
"""

from importlib import import_module

from django.db import migrations, models

formation = import_module("core.migrations.0045_sbg_governance_formation")
entitlement = import_module("core.migrations.0046_se_entitlement_foundation")

LIMITS_CODE = "TIMED_EVENTS_LIMITS"
LIMITS_RULE_UUID = "7e11a1e0-0091-5c10-a105-0091e0000001"
LIMITS_RULING = "HG_RULING_TIMED_EVENTS_T1A_LIMITS_FIXED_ROUTE_LATER_20261111"
LIMITS_RULING_SHA256 = "3641b78bba9dce8eae1b23e33a71d7e8f1d6a30f39478a074dbbf4eff4d5ab72"
LIMITS_AUTHORITY = "ruling:" + LIMITS_RULING + "#sha256=" + LIMITS_RULING_SHA256
# BPM-D11 A: one consumer checked and at most one due item opened per eligible
# request; no interval. P0b-7 A: no statement budget until Alpha is measured.
LIMITS_BODY_JSON = (
    '{"consumers_per_request":1,"items_per_request":1,'
    '"minimum_probe_gap":null,"statement_budget":null}'
)
SENTINEL = "0001-01-01 00:00:00+00"
CODES = (
    "GOVERNANCE_CONSTITUTION",
    "U3_MEMBERSHIP_END_ENDS_CIRCLE_PARTICIPATION",
    "BETA_COMPLIMENTARY_ACCESS",
    "FREE_TIER",
)


def one(text, old, new):
    """Replace exactly one occurrence, or stop: the predecessor is not the one expected."""
    if text.count(old) != 1:
        raise RuntimeError("0091 predecessor text no longer matches: " + old[:60])
    return text.replace(old, new, 1)


def guard_bodies():
    """Return the installed 0046 guard body and its T1a successor (G2-4)."""
    old = entitlement._GOVERNEDRULE_GUARD_BODY
    new = one(
        old,
        "'BETA_COMPLIMENTARY_ACCESS','FREE_TIER')",
        "'BETA_COMPLIMENTARY_ACCESS','FREE_TIER','" + LIMITS_CODE + "')",
    )
    branch = f"""    IF NEW.code = '{LIMITS_CODE}' THEN
        IF NEW.scope_kind <> 'PLATFORM'
           OR NEW.source <> 'PLATFORM_RULING'
           OR NEW.rule_uuid <> '{LIMITS_RULE_UUID}'::uuid
           OR NEW.version <> 1
           OR NEW.authority_reference IS DISTINCT FROM '{LIMITS_AUTHORITY}'
           OR NEW.effective_from <> '{SENTINEL}'::timestamptz
           OR NEW.decided_at <> '{SENTINEL}'::timestamptz
           OR NEW.enterer_identity_id IS NOT NULL
           OR NEW.observations IS NOT NULL
           OR NEW.body IS DISTINCT FROM '{LIMITS_BODY_JSON}'::jsonb THEN
            RAISE EXCEPTION 'TE_GOVERNEDRULE_LIMITS_INVALID'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;
"""
    marker = "    IF NEW.code IN ('BETA_COMPLIMENTARY_ACCESS', 'FREE_TIER') THEN"
    return old, one(new, marker, branch + marker)


SEED_BODY = f"""
DECLARE
    v_existing public.core_governedrule%ROWTYPE;
BEGIN
    SELECT r.* INTO v_existing
      FROM public.core_governedrule r
     WHERE r.rule_uuid = '{LIMITS_RULE_UUID}'::uuid;
    IF FOUND THEN
        IF v_existing.version <> 1
           OR v_existing.code <> '{LIMITS_CODE}'
           OR v_existing.body IS DISTINCT FROM '{LIMITS_BODY_JSON}'::jsonb
           OR v_existing.authority_reference <> '{LIMITS_AUTHORITY}'
           OR v_existing.source <> 'PLATFORM_RULING'
           OR v_existing.text_commitment IS NULL THEN
            RAISE EXCEPTION 'TE platform seed conflicts with the limits rule'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSE
        INSERT INTO public.core_governedrule (
            rule_uuid, version, scope_kind, scope_organism_id, scope_circle_id,
            code, body, text_commitment, authority_reference, source,
            effective_from, decided_at, observations, enterer_identity_id, recorded_at
        ) VALUES (
            '{LIMITS_RULE_UUID}'::uuid, 1, 'PLATFORM', NULL, NULL,
            '{LIMITS_CODE}', '{LIMITS_BODY_JSON}'::jsonb,
            NULL, '{LIMITS_AUTHORITY}', 'PLATFORM_RULING',
            '{SENTINEL}'::timestamptz, '{SENTINEL}'::timestamptz, NULL, NULL, NULL
        );
    END IF;
END
"""

# The registry is a function, replaced by each migration that admits a consumer
# (G2-4), so a test reset cannot empty it and no row can be forged into it.
REGISTRY_COLUMNS = (
    "consumer text, purpose text, ordinal integer, rule text, selector text, validator text, claim text"
)


def registry_sql(rows=()):
    """Compose te_consumers() from (consumer, purpose, ordinal, rule, selector, validator, claim) rows."""
    values = ",\n ".join(
        "(" + ",".join("'" + str(item) + "'" if isinstance(item, str) else str(item) for item in row) + ")"
        for row in rows
    ) or "(NULL::text,NULL::text,NULL::integer,NULL::text,NULL::text,NULL::text,NULL::text)"
    return f"""CREATE FUNCTION public.te_consumers()
RETURNS TABLE({REGISTRY_COLUMNS}) LANGUAGE sql IMMUTABLE AS $$
 SELECT v.consumer,v.purpose,v.ordinal,v.rule,v.selector,v.validator,v.claim FROM (VALUES
 {values}
 ) v(consumer,purpose,ordinal,rule,selector,validator,claim) WHERE v.consumer IS NOT NULL
$$;"""


REGISTRY = registry_sql()

SQL = r"""
CREATE TABLE public.core_tereceipt (
 receipt_uuid uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 organism_id bigint NOT NULL REFERENCES public.core_livingorganism(id),
 consumer text NOT NULL, purpose text NOT NULL, source_ref text NOT NULL, occurrence text NOT NULL,
 outcome text NOT NULL, effect_ref text NOT NULL, rule_ref text NOT NULL,
 enterer_id bigint NOT NULL REFERENCES public.core_identity(id),
 due_at timestamptz NOT NULL, effect_at timestamptz NOT NULL, discovered_at timestamptz,
 CONSTRAINT te_one_receipt UNIQUE (organism_id, consumer, purpose, source_ref, occurrence),
 CONSTRAINT te_receipt_outcome_ck CHECK (outcome IN ('OPENED','FOUND_DONE')),
 CONSTRAINT te_receipt_refs_ck CHECK (btrim(source_ref)<>'' AND btrim(occurrence)<>''
   AND btrim(effect_ref)<>'' AND btrim(rule_ref)<>''),
 CONSTRAINT te_receipt_opened_late_ck CHECK (outcome<>'OPENED' OR effect_at>=due_at)
);
CREATE INDEX te_receipt_by_consumer ON public.core_tereceipt(organism_id, consumer, discovered_at);

CREATE TABLE public.core_tepace (
 organism_id bigint NOT NULL REFERENCES public.core_livingorganism(id),
 consumer text NOT NULL,
 last_probe_at timestamptz, last_opened_at timestamptz,
 probes bigint NOT NULL DEFAULT 0, opened bigint NOT NULL DEFAULT 0, found_done bigint NOT NULL DEFAULT 0,
 blocked bigint NOT NULL DEFAULT 0, failed bigint NOT NULL DEFAULT 0,
 cursor_due timestamptz, cursor_purpose text, cursor_source text, cursor_occurrence text,
 last_blocked_source text, last_blocked_code text, last_blocked_at timestamptz,
 last_failure_class text, last_failure_code text, last_failure_at timestamptz,
 last_ms integer, max_ms integer,
 PRIMARY KEY (organism_id, consumer),
 CONSTRAINT te_pace_counts_ck CHECK (probes>=0 AND opened>=0 AND found_done>=0 AND blocked>=0 AND failed>=0),
 CONSTRAINT te_pace_failure_class_ck CHECK (last_failure_class IS NULL
   OR last_failure_class IN ('DOMAIN_REFUSED','ERROR','COMMIT_FAILED'))
);

-- No foreign key to the LO on purpose: the first count for an LO is written while
-- another transaction holds that LO's row, and a key check would wait for it. The
-- writer inserts only for an LO it has just read.
CREATE TABLE public.core_temeter (
 organism_id bigint PRIMARY KEY,
 busy_lo bigint NOT NULL DEFAULT 0, busy_inner bigint NOT NULL DEFAULT 0,
 in_transaction bigint NOT NULL DEFAULT 0, not_eligible bigint NOT NULL DEFAULT 0,
 no_limits bigint NOT NULL DEFAULT 0, commit_failed bigint NOT NULL DEFAULT 0,
 errors bigint NOT NULL DEFAULT 0,
 last_skip_class text, last_skip_at timestamptz,
 CONSTRAINT te_meter_counts_ck CHECK (busy_lo>=0 AND busy_inner>=0 AND in_transaction>=0
   AND not_eligible>=0 AND no_limits>=0 AND commit_failed>=0 AND errors>=0),
 CONSTRAINT te_meter_class_ck CHECK (last_skip_class IS NULL OR last_skip_class IN
   ('BUSY_LO','BUSY_INNER','IN_TRANSACTION','NOT_ELIGIBLE','NO_LIMITS','COMMIT_FAILED','ERROR'))
);

CREATE FUNCTION public.te_receipt_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE reg record; proof record; stamp timestamptz:=clock_timestamp();
BEGIN
 IF NEW.discovered_at IS NOT NULL THEN
    RAISE EXCEPTION 'TE_RECEIPT_TIME' USING ERRCODE='23514';
 END IF;
 SELECT r.* INTO reg FROM public.te_consumers() r
 WHERE r.consumer=NEW.consumer AND r.purpose=NEW.purpose;
 IF NOT FOUND THEN
    RAISE EXCEPTION 'TE_RECEIPT_CONSUMER' USING ERRCODE='23514';
 END IF;
 -- The rule is the actor. It is the registered rule of this purpose, never asserted.
 IF NEW.rule_ref IS DISTINCT FROM reg.rule THEN
    RAISE EXCEPTION 'TE_RECEIPT_RULE' USING ERRCODE='23514';
 END IF;
 -- The consumer's own validator proves that the cited effect is this source's
 -- effect in this LO, and supplies both times. A receipt cannot assert them.
 EXECUTE format('SELECT v.due_at,v.effect_at,v.enterer_id,v.made_here FROM public.%I($1,$2,$3,$4,$5) v',reg.validator)
   INTO proof USING NEW.organism_id,NEW.purpose,NEW.source_ref,NEW.occurrence,NEW.effect_ref;
 IF proof.due_at IS NULL OR proof.effect_at IS NULL
    OR proof.due_at IS DISTINCT FROM NEW.due_at OR proof.effect_at IS DISTINCT FROM NEW.effect_at THEN
    RAISE EXCEPTION 'TE_RECEIPT_EFFECT' USING ERRCODE='23514';
 END IF;
 -- OPENED means this transaction made the effect, and the receipt names the effect's own
 -- enterer. FOUND_DONE means an earlier transaction made it; its discoverer may differ.
 IF (NEW.outcome='OPENED') IS DISTINCT FROM proof.made_here THEN
    RAISE EXCEPTION 'TE_RECEIPT_ORIGIN' USING ERRCODE='23514';
 END IF;
 IF NEW.outcome='OPENED' AND NEW.enterer_id IS DISTINCT FROM proof.enterer_id THEN
    RAISE EXCEPTION 'TE_RECEIPT_ENTERER' USING ERRCODE='23514';
 END IF;
 -- The receipt is always written after its effect, so the database clock at this
 -- point is never earlier than the effect's own recorded time.
 IF NEW.effect_at>stamp THEN
    RAISE EXCEPTION 'TE_RECEIPT_TIME' USING ERRCODE='23514';
 END IF;
 NEW.discovered_at:=stamp;
 RETURN NEW;
END $$;

CREATE FUNCTION public.te_pace_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 -- Counts may change. Which LO and which consumer a row belongs to may not.
 IF TG_OP='UPDATE' AND (NEW.organism_id IS DISTINCT FROM OLD.organism_id OR NEW.consumer IS DISTINCT FROM OLD.consumer) THEN
    RAISE EXCEPTION 'TE_PACE_IDENTITY' USING ERRCODE='23514';
 END IF;
 IF TG_OP='INSERT' AND NOT EXISTS(SELECT FROM public.te_consumers() r WHERE r.consumer=NEW.consumer) THEN
    RAISE EXCEPTION 'TE_PACE_CONSUMER' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;

-- Origin evidence: which top-level transaction wrote an effect. One row per effect, written
-- by the effect table's own insert trigger in the same (sub)transaction as the effect, so it
-- is kept when a savepoint is released and goes when the effect is rolled back. It is written
-- before the effect row itself, and the table accepts it on no other terms: see the guards. The id is
-- the database's full 64-bit top-level transaction id, which is never reused. A row's own
-- xmin is not used: it is 32 bits, it names a savepoint, not the transaction, and an old
-- row keeps the number after the number has been given out again.
CREATE TABLE public.core_teorigin (
 effect_table text NOT NULL, effect_key text NOT NULL, top_xid xid8 NOT NULL,
 CONSTRAINT te_one_origin PRIMARY KEY (effect_table, effect_key)
);

-- The approved source relations, each with the column that identifies its effect. An origin
-- can be recorded for no other relation. All six are in the public schema.
CREATE FUNCTION public.te_origin_key(p_table text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
 SELECT CASE p_table
  WHEN 'core_contextualroleassignmentstateevent' THEN 'event_uuid'
  WHEN 'core_coverageassessment' THEN 'event_uuid'
  WHEN 'core_livingorganismevent' THEN 'event_uuid'
  WHEN 'core_electvacancy' THEN 'ending'
  WHEN 'core_cqact' THEN 'act_uuid'
  WHEN 'core_cmtmandateend' THEN 'mandate' END
$$;

-- Which rows of an approved relation are effects whose origin is recorded: the one statement
-- of it, read by the recorder for the row being inserted and by the check at commit for the
-- row that is there. A purpose whose effects are left out of it has no origin, so it can
-- never report OPENED: see the design register, BR5, for what that means for each adapter.
CREATE FUNCTION public.te_origin_applies(p_table text,p_row jsonb) RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
 SELECT COALESCE(CASE p_table
  WHEN 'core_contextualroleassignmentstateevent'
   THEN p_row->>'action'='END' AND p_row->>'evidence_reference' LIKE 'elect-expiry:%'
  WHEN 'core_cqact' THEN p_row->'authority_source'->>'kind'='RULE'
  WHEN 'core_livingorganismevent' THEN p_row->>'action' IN ('K2_DEACTIVATE','K2_DISSOLVE')
  WHEN 'core_coverageassessment' THEN true
  WHEN 'core_electvacancy' THEN true
  WHEN 'core_cmtmandateend' THEN true END,false)
$$;

-- Does the effect exist, as this transaction sees it now: any row with the key, or, when
-- p_recorded, a row with the key that is an effect whose origin is recorded? Null when the
-- relation is not an approved source, or no key is given.
CREATE FUNCTION public.te_effect_exists(p_table text,p_key text,p_recorded boolean) RETURNS boolean LANGUAGE plpgsql AS $$
DECLARE key_column text:=public.te_origin_key(p_table); found_ boolean;
BEGIN
 IF key_column IS NULL OR p_key IS NULL THEN RETURN NULL; END IF;
 EXECUTE format('SELECT EXISTS(SELECT FROM public.%I e WHERE e.%I::text=$1
                  AND (NOT $2 OR public.te_origin_applies($3,to_jsonb(e))))',p_table,key_column)
   INTO found_ USING p_key,p_recorded,p_table;
 RETURN found_;
END $$;

-- The writer boundary, kept by the origin table itself and not by trust in its caller.
-- An origin is accepted
--   only from inside a trigger, and never with an id of the writer's choosing;
--   only for an approved source relation;
--   only for an effect that does not exist yet (TE_ORIGIN_EXISTS). So no writer, nested or
--   not, can attribute an origin to an effect that is already there: an older effect with
--   no origin stays without one for ever.
-- And te_origin_complete, deferred to commit, refuses an origin unless an effect whose
-- origin is recorded has come to exist by then (TE_ORIGIN_ORPHAN). Between the two, an origin
-- that commits is the origin of a recorded effect that did not exist when it was written and
-- does exist at commit. That is true whichever trigger wrote the origin row.
-- Another transaction cannot have supplied that effect: inserting a recorded effect writes its
-- origin first, and the primary key makes the second writer of one key wait for the first,
-- which cannot commit without the effect. An effect outside te_origin_applies, which takes no
-- such key, is for that reason never ground for an origin.
CREATE FUNCTION public.te_origin_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE there boolean;
BEGIN
 IF pg_trigger_depth()<2 OR NEW.top_xid IS NOT NULL THEN
    RAISE EXCEPTION 'TE_ORIGIN_INPUT' USING ERRCODE='23514';
 END IF;
 there:=public.te_effect_exists(NEW.effect_table,NEW.effect_key,false);
 IF there IS NULL THEN
    RAISE EXCEPTION 'TE_ORIGIN_SOURCE' USING ERRCODE='23514';
 END IF;
 IF there THEN
    RAISE EXCEPTION 'TE_ORIGIN_EXISTS' USING ERRCODE='23514';
 END IF;
 NEW.top_xid:=pg_current_xact_id();
 RETURN NEW;
END $$;

CREATE FUNCTION public.te_origin_complete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF public.te_effect_exists(NEW.effect_table,NEW.effect_key,true) IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'TE_ORIGIN_ORPHAN' USING ERRCODE='23514';
 END IF;
 RETURN NULL;
END $$;

-- The effect tables' insert trigger: BEFORE INSERT, FOR EACH ROW, on an approved relation, and
-- on nothing else. It takes the key from the row being inserted, by the relation's own key
-- column, and records nothing for a row te_origin_applies leaves out. An insert that meets an
-- existing row (a replay under ON CONFLICT DO NOTHING) will write no effect, so it records
-- nothing either. The check at commit is put back to deferred first: a transaction that has
-- set its constraints immediate (as two landed root commands do, last thing) would otherwise
-- have the origin refused before its effect is written. Deferred, it is still made, at commit
-- or whenever the transaction next asks for its constraints.
CREATE FUNCTION public.te_record_origin() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE key_column text:=public.te_origin_key(TG_TABLE_NAME); key_ text;
BEGIN
 IF TG_TABLE_SCHEMA<>'public' OR key_column IS NULL OR TG_OP<>'INSERT' OR TG_WHEN<>'BEFORE' OR TG_LEVEL<>'ROW' THEN
    RAISE EXCEPTION 'TE_ORIGIN_SOURCE' USING ERRCODE='23514';
 END IF;
 IF NOT public.te_origin_applies(TG_TABLE_NAME,to_jsonb(NEW)) THEN RETURN NEW; END IF;
 key_:=to_jsonb(NEW)->>key_column;
 IF NOT public.te_effect_exists(TG_TABLE_NAME,key_,false) THEN
    SET CONSTRAINTS public.te_origin_complete DEFERRED;
    INSERT INTO public.core_teorigin(effect_table,effect_key) VALUES (TG_TABLE_NAME,key_) ON CONFLICT DO NOTHING;
 END IF;
 RETURN NEW;
END $$;

-- The one comparison: a stored full id against this transaction's own top-level id.
CREATE FUNCTION public.te_same_transaction(p_top xid8) RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT p_top=pg_current_xact_id()
$$;

-- True when this transaction wrote the effect, at its top level or in a savepoint of it
-- that was not rolled back. An effect with no origin (one that predates this record, or a
-- kind no trigger records) is never "made here": it can be found done, and nothing more.
CREATE FUNCTION public.te_made_here(p_table text,p_key text) RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT EXISTS(SELECT FROM public.core_teorigin o WHERE o.effect_table=p_table AND o.effect_key=p_key
               AND public.te_same_transaction(o.top_xid))
$$;

CREATE FUNCTION public.te_limits_at(p_at timestamptz) RETURNS jsonb LANGUAGE sql STABLE AS $$
 SELECT r.body FROM public.core_governedrule r
 WHERE r.rule_uuid='@LIMITS_RULE_UUID@'::uuid AND r.code='@LIMITS_CODE@' AND r.scope_kind='PLATFORM'
   AND r.source='PLATFORM_RULING' AND r.effective_from<=p_at AND r.recorded_at<=statement_timestamp()
   AND r.body='@LIMITS_BODY_JSON@'::jsonb
 ORDER BY r.effective_from DESC,r.recorded_at DESC,r.version DESC,r.id DESC LIMIT 1
$$;

CREATE TRIGGER te_receipt_input BEFORE INSERT ON public.core_tereceipt
 FOR EACH ROW EXECUTE FUNCTION public.te_receipt_guard();
CREATE TRIGGER te_receipt_immutable BEFORE UPDATE OR DELETE ON public.core_tereceipt
 FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE TRIGGER s015_tereceipt_no_truncate BEFORE TRUNCATE ON public.core_tereceipt
 FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE TRIGGER te_pace_input BEFORE INSERT OR UPDATE ON public.core_tepace
 FOR EACH ROW EXECUTE FUNCTION public.te_pace_guard();
CREATE TRIGGER te_origin_input BEFORE INSERT ON public.core_teorigin
 FOR EACH ROW EXECUTE FUNCTION public.te_origin_guard();
CREATE CONSTRAINT TRIGGER te_origin_complete AFTER INSERT ON public.core_teorigin
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.te_origin_complete();
CREATE TRIGGER te_origin_immutable BEFORE UPDATE OR DELETE ON public.core_teorigin
 FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE TRIGGER s015_teorigin_no_truncate BEFORE TRUNCATE ON public.core_teorigin
 FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation();
""".replace("@LIMITS_RULE_UUID@", LIMITS_RULE_UUID).replace("@LIMITS_CODE@", LIMITS_CODE).replace(
    "@LIMITS_BODY_JSON@", LIMITS_BODY_JSON
)

REMOVE = """
DROP TABLE public.core_temeter;
DROP TABLE public.core_tepace;
DROP TABLE public.core_tereceipt;
DROP FUNCTION public.te_limits_at(timestamptz);
DROP FUNCTION public.te_pace_guard();
DROP FUNCTION public.te_made_here(text,text);
DROP FUNCTION public.te_same_transaction(xid8);
DROP FUNCTION public.te_record_origin();
DROP TABLE public.core_teorigin;
DROP FUNCTION public.te_origin_complete();
DROP FUNCTION public.te_origin_guard();
DROP FUNCTION public.te_effect_exists(text,text,boolean);
DROP FUNCTION public.te_origin_applies(text,jsonb);
DROP FUNCTION public.te_origin_key(text);
DROP FUNCTION public.te_receipt_guard();
DROP FUNCTION public.te_consumers();
DROP FUNCTION public.te_seed_limits();
"""

GUARD = "public.sbg_guard_governedrule()"


def installed(cursor, signature):
    """Return the installed body and properties of one function, or None."""
    cursor.execute(
        """SELECT p.prosrc,p.proisstrict,p.provolatile::text,l.lanname::text
        FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang WHERE p.oid=to_regprocedure(%s)""",
        [signature],
    )
    return cursor.fetchone()


def expect(cursor, signature, body):
    """G2-4: refuse unless the installed plpgsql trigger body is exactly the expected one."""
    if installed(cursor, signature) != (body, False, "v", "plpgsql"):
        raise RuntimeError("Unexpected installed body: " + signature)


def restore_limits(apps, schema_editor):
    """Restore the ruled limits version after an S015 database reset."""
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT public.te_seed_limits()")


def install(apps, schema_editor):
    """Admit the limits code in the guard, then create the engine's storage and seed version 1."""
    if schema_editor.connection.vendor != "postgresql":
        return
    old, new = guard_bodies()
    with schema_editor.connection.cursor() as cursor:
        expect(cursor, GUARD, old)
        cursor.execute(formation._function_sql("sbg_guard_governedrule", new))
        cursor.execute(REGISTRY)
        cursor.execute(SQL)
        cursor.execute(formation._function_sql("te_seed_limits", SEED_BODY, return_type="void"))
    restore_limits(apps, schema_editor)


def uninstall(apps, schema_editor):
    """Refuse reversal while receipts exist; remove only the owned seed; restore the 0046 guard."""
    if schema_editor.connection.vendor != "postgresql":
        return
    old, new = guard_bodies()
    with schema_editor.connection.cursor() as cursor:
        expect(cursor, GUARD, new)
        # Fence receipt writes before looking: no receipt can arrive between this
        # check and the end of the migration's transaction.
        cursor.execute("LOCK TABLE public.core_tereceipt IN ACCESS EXCLUSIVE MODE")
        cursor.execute("SELECT EXISTS (SELECT 1 FROM public.core_tereceipt)")
        if cursor.fetchone()[0]:
            raise RuntimeError("0091 reverse refused while TIMED EVENTS receipts exist")
        cursor.execute(
            "SELECT rule_uuid::text, version FROM public.core_governedrule WHERE code = %s",
            [LIMITS_CODE],
        )
        if cursor.fetchall() != [(LIMITS_RULE_UUID, 1)]:
            raise RuntimeError("0091 reverse refused while the limits rule history differs")
        cursor.execute("DROP TRIGGER IF EXISTS sbg_governedrule_immutable ON public.core_governedrule")
        cursor.execute(
            "DELETE FROM public.core_governedrule WHERE rule_uuid = %s::uuid",
            [LIMITS_RULE_UUID],
        )
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute(
            "CREATE TRIGGER sbg_governedrule_immutable BEFORE UPDATE OR DELETE "
            "ON public.core_governedrule FOR EACH ROW "
            "EXECUTE FUNCTION public.s015_refuse_row_mutation()"
        )
        cursor.execute(formation._function_sql("sbg_guard_governedrule", old))
        cursor.execute(REMOVE)


def code_constraint(codes):
    """The GovernedRule code vocabulary check for a given list of codes."""
    return models.CheckConstraint(
        condition=models.Q(("code__in", tuple(codes))),
        name="sbg_governedrule_code_vocab_ck",
    )


class Migration(migrations.Migration):
    dependencies = (("core", "0090_stand_down_supersession"),)
    operations = (
        migrations.RemoveConstraint(model_name="governedrule", name="sbg_governedrule_code_vocab_ck"),
        migrations.AddConstraint(model_name="governedrule", constraint=code_constraint((*CODES, LIMITS_CODE))),
        migrations.RunPython(install, uninstall),
    )
