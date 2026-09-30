import importlib
import uuid

from django.db import migrations, models
from django.db.models.deletion import PROTECT


TABLE = "core_applicationlineageevent"
ANCHOR = "core_applicationlineage"
EVENT_TOKEN = "alineageevent"
ANCHOR_TOKEN = "alineage"
FK = "application_lineage_id"
STATES = ("SUBMITTED", "IN_REVIEW", "ACCEPTED", "DECLINED", "WITHDRAWN")


def source(number):
    names = {
        21: "0021_s015_recorded_chain_and_bitemporal_fold",
        22: "0022_s015_governed_chain_contract",
    }
    return importlib.import_module("core.migrations." + names[number])


def member_fields():
    fields = source(21).common_member_fields(
        "ApplicationLineageEvent", "core.applicationlineage", "application_lineage", 10,
    )
    for index, (name, field) in enumerate(fields):
        if name == "actor":
            fields[index] = (name, models.ForeignKey(to="core.identity", on_delete=PROTECT, related_name="+", null=True, blank=True))
        if name == "actor_access_epoch":
            fields[index] = (name, models.PositiveBigIntegerField(null=True, blank=True))
    return fields


def map_sql(include_application):
    values = source(21)._anchor_map_values()
    if include_application:
        values += (
            ",\n        ('core_applicationlineage','lineage_uuid','core_applicationlineageevent',"
            "'head_event_id','application_lineage_id','resulting_state',false,false)"
        )
    return """CREATE OR REPLACE FUNCTION public.s015_anchor_map()
RETURNS TABLE(anchor_token text, identity_col text, event_table text, head_col text, fk_col text,
              state_col text, has_cache boolean, empty_chain_permitted boolean)
LANGUAGE sql IMMUTABLE AS $$
    SELECT * FROM (VALUES
        """ + values + """
    ) AS v(anchor_token, identity_col, event_table, head_col, fk_col, state_col, has_cache, empty_chain_permitted)
$$;"""


def lock_sql(include_application):
    original = source(21).FUNCTIONS_SQL
    start = original.index("CREATE FUNCTION public.s015_acquire_owning_organism_lock()")
    end = original.index("END $$;", start) + len("END $$;")
    definition = original[start:end].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
    if include_application:
        definition = definition.replace(
            "        WHEN 'core_organismmembershiptransition' THEN",
            "        WHEN 'core_applicationlineageevent' THEN 'SELECT lo.organism_id FROM public.core_applicationlineage l JOIN public.core_application a ON a.id = l.application_id JOIN public.core_livingorganism lo ON lo.id = a.living_organism_id WHERE l.id = $1'\n"
            "        WHEN 'core_organismmembershiptransition' THEN", 1,
        ).replace(
            "        WHEN 'core_organismmembershiptransition' THEN 'membership_id'",
            "        WHEN 'core_applicationlineageevent' THEN 'application_lineage_id'\n"
            "        WHEN 'core_organismmembershiptransition' THEN 'membership_id'", 1,
        )
    return definition


def vocab_sql(table, include_application):
    tables = [row[0] for row in source(22).EVENT_TABLE_SPECS]
    if include_application:
        tables.append(TABLE)
    values = ",".join("'" + name + "'" for name in tables)
    name = "s015_0022_" + ("part" if table == "core_governedeventpart" else "content") + "_table_vocab_ck"
    return (f"ALTER TABLE public.{table} DROP CONSTRAINT {name}",
            f"ALTER TABLE public.{table} ADD CONSTRAINT {name} CHECK (event_table IN ({values}))")


CAPACITY_SQL = """
CREATE FUNCTION public.s3fu_guard_lo_capacity() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE lo_id bigint;
BEGIN
    IF NEW.actor_state <> 'LIVING_ORGANISM' THEN RETURN NEW; END IF;
    IF TG_TABLE_NAME = 'core_applicationlineageevent' THEN
        SELECT a.living_organism_id INTO lo_id FROM public.core_applicationlineage l
        JOIN public.core_application a ON a.id = l.application_id WHERE l.id = NEW.application_lineage_id;
    ELSE
        SELECT m.living_organism_id INTO lo_id FROM public.core_organismmembership m WHERE m.id = NEW.membership_id;
    END IF;
    IF lo_id IS NULL OR NOT EXISTS (
          SELECT 1 FROM public.core_livingorganism lo
          JOIN public.core_livingorganismevent founding ON founding.living_organism_id = lo.id
          WHERE lo.id = lo_id AND lo.founding_authority_basis_id IS NOT NULL
            AND lo.founder_identity_id IS NOT NULL AND founding.sequence = 1
            AND founding.effective_at <= NEW.effective_at AND founding.received_at <= NEW.recorded_at
            AND founding.recorded_at <= NEW.recorded_at
       ) OR EXISTS (
          SELECT 1 FROM public.core_livingorganismevent closed
          WHERE closed.living_organism_id = lo_id AND closed.resulting_state = 'CLOSED'
            AND closed.effective_at <= NEW.effective_at AND closed.received_at <= NEW.recorded_at
            AND closed.recorded_at <= NEW.recorded_at
       ) THEN
        RAISE EXCEPTION 'S3FU LO capacity refused' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;
"""

PERMISSION_SQL = """
CREATE FUNCTION public.s3fu_guard_lo_admission_permission() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE lo_id bigint; state text;
BEGIN
    IF NEW.actor_state <> 'LIVING_ORGANISM' OR
       (TG_TABLE_NAME = 'core_applicationlineageevent' AND NEW.resulting_state <> 'ACCEPTED') THEN
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME = 'core_applicationlineageevent' THEN
        SELECT a.living_organism_id INTO lo_id FROM public.core_applicationlineage l
        JOIN public.core_application a ON a.id = l.application_id WHERE l.id = NEW.application_lineage_id;
    ELSE
        SELECT m.living_organism_id INTO lo_id FROM public.core_organismmembership m WHERE m.id = NEW.membership_id;
    END IF;
    state := public.s015_fold_effective_state('core_livingorganism', lo_id, NEW.effective_at, NEW.recorded_at);
    IF state = 'RESTRICTED_CONTINUITY' THEN
        RAISE EXCEPTION 'S3-IL-RC: temporary K-2/S4 application route not implemented'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF state IS DISTINCT FROM 'ACTIVE' THEN
        RAISE EXCEPTION 'S3FU LO admission not permitted' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;
"""

ACTOR_SQL = """
CREATE FUNCTION public.s3fu_guard_application_lineage_actor() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE application_row record; decision_row record;
BEGIN
    SELECT a.* INTO application_row FROM public.core_applicationlineage l
    JOIN public.core_application a ON a.id = l.application_id WHERE l.id = NEW.application_lineage_id;
    IF application_row.id IS NULL OR NEW.enterer_identity_id IS NULL OR NEW.entry_mode <> 'TRANSPORT'
         OR NEW.actor_state NOT IN ('PARTY', 'LIVING_ORGANISM')
       OR NEW.composing_rule_state <> 'NA_TRANSPORT' OR NEW.composer_state <> 'NA_TRANSPORT'
       OR NEW.l2_commitment IS NOT NULL THEN
        RAISE EXCEPTION 'S3FU application actor/entry refused' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.action <> 'SUBMIT_APPLICATION' AND NEW.sequence = 1 THEN
        RAISE EXCEPTION 'S3FU application chain must begin with its submission' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.action = 'SUBMIT_APPLICATION' THEN
        IF NEW.sequence <> 1 OR NEW.prior_state IS NOT NULL OR NEW.resulting_state <> 'SUBMITTED'
           OR NEW.actor_state <> 'PARTY' OR NEW.actor_id IS DISTINCT FROM application_row.applicant_id
           OR NEW.enterer_identity_id IS DISTINCT FROM application_row.enterer_id
           OR NEW.actor_access_epoch IS DISTINCT FROM application_row.actor_access_epoch
           OR NEW.authority_decision_reference IS DISTINCT FROM application_row.authority_decision_reference
           OR NEW.evidence_reference IS DISTINCT FROM 'application:' || application_row.application_uuid::text THEN
            RAISE EXCEPTION 'S3FU application submission attribution refused' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSIF NEW.action IN ('CLAIM_APPLICATION', 'REASSIGN_APPLICATION') THEN
        IF NEW.actor_state <> 'PARTY' OR NEW.resulting_state <> 'IN_REVIEW'
           OR NEW.evidence_reference !~ '^csa-assignment:[0-9a-f-]{36}$' OR NOT EXISTS (
              SELECT 1 FROM public.core_csaassignment claim
              WHERE claim.application_id = application_row.id AND claim.assignment_uuid::text = substring(NEW.evidence_reference from 16)
                AND claim.actor_id = NEW.actor_id AND claim.enterer_id = NEW.enterer_identity_id
           ) THEN
            RAISE EXCEPTION 'S3FU application claim attribution refused' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSIF NEW.action = 'WITHDRAW_APPLICATION' THEN
        IF NEW.actor_state <> 'PARTY' OR NEW.resulting_state <> 'WITHDRAWN'
           OR NEW.actor_id IS DISTINCT FROM application_row.applicant_id OR NOT EXISTS (
              SELECT 1 FROM public.core_applicationwithdrawal withdrawal
              WHERE withdrawal.application_id = application_row.id
                     AND NEW.evidence_reference = 'application-withdrawal:' || application_row.application_uuid::text
                AND NEW.authority_decision_reference = withdrawal.authority_decision_reference
                AND NEW.actor_access_epoch = withdrawal.actor_access_epoch
                AND withdrawal.enterer_id = NEW.enterer_identity_id
           ) THEN
            RAISE EXCEPTION 'S3FU application withdrawal attribution refused' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSIF NEW.action = 'DECIDE_APPLICATION' THEN
        SELECT d.* INTO decision_row FROM public.core_applicationdecision d
        WHERE d.application_id = application_row.id
          AND NEW.evidence_reference = 'application-decision:' || d.decision_uuid::text;
        IF decision_row.id IS NULL OR NEW.actor_state <> 'LIVING_ORGANISM'
           OR NEW.actor_id IS NOT NULL OR NEW.actor_access_epoch IS NOT NULL
           OR NEW.authority_decision_reference IS DISTINCT FROM decision_row.authority_decision_reference
           OR NEW.enterer_identity_id IS DISTINCT FROM decision_row.enterer_id
           OR NEW.resulting_state IS DISTINCT FROM (CASE WHEN decision_row.outcome = 'ACCEPT' THEN 'ACCEPTED' ELSE 'DECLINED' END) THEN
            RAISE EXCEPTION 'S3FU application decision attribution refused' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSE
        RAISE EXCEPTION 'S3FU application act not admitted' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;
"""


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        raise RuntimeError("S015 0028 requires PostgreSQL")
    ex = lambda sql: schema_editor.execute(sql, params=None)
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("LOCK TABLE public.core_application IN SHARE ROW EXCLUSIVE MODE")
        cursor.execute("SELECT count(*) FROM public.core_application")
        if cursor.fetchone()[0]:
            raise RuntimeError("S015 0028 preflight halt: existing applications require authorised backfill")

    ex(map_sql(True))
    ex(lock_sql(True))
    prefix = f"s015_0021_{EVENT_TOKEN}"
    for suffix, sql in (
        ("id_anchor_uniq", f"UNIQUE (id, {FK})"),
        ("id_anchor_seq_uniq", f"UNIQUE (id, {FK}, sequence)"),
        ("predecessor_fk", f"FOREIGN KEY (predecessor_id, {FK}, predecessor_sequence) REFERENCES public.{TABLE}(id, {FK}, sequence) DEFERRABLE INITIALLY DEFERRED"),
        ("adjacency_ck", "CHECK ((sequence = 1 AND predecessor_id IS NULL AND predecessor_sequence IS NULL) OR (sequence > 1 AND predecessor_id IS NOT NULL AND predecessor_sequence = sequence - 1))"),
        ("sequence_ck", "CHECK (sequence >= 1)"),
        ("prior_seq1_ck", "CHECK (sequence > 1 OR prior_state IS NULL)"),
        ("recorded_not_null_ck", "CHECK (recorded_at IS NOT NULL)"),
        ("four_time_ck", "CHECK (occurred_at <= received_at AND received_at <= recorded_at)"),
        ("temporal_basis_ck", "CHECK (((effective_at = received_at AND temporal_basis_kind IS NULL AND temporal_basis_reference IS NULL) OR (effective_at < received_at AND temporal_basis_kind = 'RETROSPECTIVE' AND temporal_basis_reference IS NOT NULL) OR (effective_at > received_at AND temporal_basis_kind = 'PROSPECTIVE' AND temporal_basis_reference IS NOT NULL)) IS TRUE)"),
        ("decision_shape_ck", "CHECK (authority_decision_reference ~ '^s015d1:[0-9a-f]{64}$')"),
        ("payload_shape_ck", "CHECK (payload_fingerprint ~ '^[0-9a-f]{64}$')"),
        ("lineage_shape_ck", "CHECK (lineage_reference ~ '^s015l1:[0-9a-f]{64}$')"),
        ("actor_epoch_ck", "CHECK (actor_access_epoch >= 0)"),
        ("temporal_ref_canonical_ck", "CHECK (temporal_basis_reference IS NULL OR (length(temporal_basis_reference) >= 1 AND temporal_basis_reference = btrim(temporal_basis_reference) AND temporal_basis_reference IS NFC NORMALIZED))"),
    ):
        ex(f"ALTER TABLE public.{TABLE} ADD CONSTRAINT {prefix}_{suffix} {sql}")
    for ts in ("occurred_at", "effective_at", "received_at", "recorded_at"):
        ex(f"ALTER TABLE public.{TABLE} ADD CONSTRAINT {prefix}_{ts}_domain_ck CHECK ({ts} >= '0001-01-01 00:00:00+00'::timestamptz AND {ts} <= '9999-12-31 23:59:59.999999+00'::timestamptz)")
    for col in ("action", "evidence_reference", "request_reference", "idempotency_key", "resulting_state"):
        ex(f"ALTER TABLE public.{TABLE} ADD CONSTRAINT {prefix}_{col}_canonical_ck CHECK (length({col}) >= 1 AND {col} = btrim({col}) AND {col} IS NFC NORMALIZED)")
    ex(f"COMMENT ON COLUMN public.{TABLE}.prior_state IS '{source(21).PRIOR_STATE_COMMENT}'")
    ex(f"ALTER TABLE public.{ANCHOR} ADD CONSTRAINT s015_0021_{ANCHOR_TOKEN}_head_fk FOREIGN KEY (head_event_id, id) REFERENCES public.{TABLE}(id, {FK}) DEFERRABLE INITIALLY DEFERRED")
    states = ",".join("'" + value + "'" for value in STATES)
    ex(f"ALTER TABLE public.{TABLE} ADD CONSTRAINT {prefix}_resulting_state_vocab_ck CHECK (resulting_state IN ({states}))")
    ex(f"ALTER TABLE public.{TABLE} ADD CONSTRAINT {prefix}_prior_vocab_ck CHECK (prior_state IS NULL OR prior_state IN ({states}))")
    for table in ("core_governedeventpart", "core_governedeventcontent"):
        for sql in vocab_sql(table, True):
            ex(sql)
    for name, timing, function in (
        (f"{prefix}_record_time", "BEFORE INSERT", "s015_assign_event_record_time"),
        (f"{prefix}_update_append_only", "BEFORE UPDATE", "s015_refuse_row_mutation"),
        (f"{prefix}_delete_append_only", "BEFORE DELETE", "s015_refuse_row_mutation"),
        (f"{prefix}_truncate_append_only", "BEFORE TRUNCATE", "s015_refuse_row_mutation"),
        (f"{prefix}_head_advance", "AFTER INSERT", "s015_advance_recorded_head"),
        (f"{prefix}_organism_lock", "BEFORE INSERT", "s015_acquire_owning_organism_lock"),
    ):
        level = "STATEMENT" if "TRUNCATE" in timing else "ROW"
        ex(f"CREATE TRIGGER {name} {timing} ON public.{TABLE} FOR EACH {level} EXECUTE FUNCTION public.{function}()")
    ex(f"CREATE CONSTRAINT TRIGGER {prefix}_chain_guard AFTER INSERT ON public.{TABLE} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.s015_guard_aggregate_chain()")
    for name, timing, function in (
        ("head_guard", "BEFORE UPDATE OF head_event_id", "s015_guard_head_pointer"),
        ("chain_guard", "AFTER INSERT OR UPDATE OF head_event_id", "s015_guard_aggregate_chain"),
    ):
        statement = "CREATE CONSTRAINT TRIGGER" if name == "chain_guard" else "CREATE TRIGGER"
        deferred = " DEFERRABLE INITIALLY DEFERRED" if name == "chain_guard" else ""
        ex(f"{statement} s015_0021_{ANCHOR_TOKEN}_{name} {timing} ON public.{ANCHOR}{deferred} FOR EACH ROW EXECUTE FUNCTION public.{function}()")
    for col in ("lineage_uuid", "application_id"):
        ex(f"CREATE TRIGGER s015_0021_{ANCHOR_TOKEN}_{col}_immutable BEFORE UPDATE OF {col} ON public.{ANCHOR} FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation()")
    for operation in ("DELETE", "TRUNCATE"):
        level = "STATEMENT" if operation == "TRUNCATE" else "ROW"
        ex(f"CREATE TRIGGER s015_0021_{ANCHOR_TOKEN}_{operation.lower()}_immutable BEFORE {operation} ON public.{ANCHOR} FOR EACH {level} EXECUTE FUNCTION public.s015_refuse_row_mutation()")
    for column, sql in source(22).EVENT_BASE_COLUMNS:
        ex(f"ALTER TABLE public.{TABLE} ADD COLUMN {column} {sql}")
    for name, expression in source(22).EVENT_BASE_CHECKS:
        if name == "actor_state_vocab_ck":
            expression = "actor_state IN ('PARTY','NONE','NOT_ESTABLISHED','LIVING_ORGANISM')"
        elif name == "actor_pointer_ck" or name == "actor_epoch_ck2":
            field = "actor_id" if name == "actor_pointer_ck" else "actor_access_epoch"
            expression = f"(actor_state = 'PARTY') = ({field} IS NOT NULL)"
        elif name == "actor_capacity_state_ck":
            expression = "(actor_state IN ('PARTY','LIVING_ORGANISM')) = (actor_capacity = 'STANDING')"
        ex(f"ALTER TABLE public.{TABLE} ADD CONSTRAINT s015_0022_applicationlineageevent_{name} CHECK ({expression})")
    ex(f"ALTER TABLE public.{TABLE} DROP CONSTRAINT s015_0021_applicationlineageevent_idem_uniq")
    ex(f"CREATE UNIQUE INDEX s015_0022_applicationlineageevent_named_actor_idem_uidx ON public.{TABLE} (actor_id, action, idempotency_key) WHERE actor_state = 'PARTY' AND actor_id IS NOT NULL")
    ex(f"CREATE UNIQUE INDEX s015_0022_applicationlineageevent_actorless_op_uidx ON public.{TABLE} ({FK}, actor_state, action, idempotency_key) WHERE actor_state <> 'PARTY'")
    for suffix, timing, function, constraint in source(22).EVENT_TRIGGER_SUFFIXES:
        statement = "CREATE CONSTRAINT TRIGGER" if constraint else "CREATE TRIGGER"
        deferred = " DEFERRABLE INITIALLY DEFERRED" if constraint else ""
        ex(f"{statement} s015_0022_applicationlineageevent_{suffix} {timing} ON public.{TABLE}{deferred} FOR EACH ROW EXECUTE FUNCTION {function}")
    ex("ALTER TABLE public.core_organismmembershiptransition DROP CONSTRAINT s015_0022_organismmembershiptransition_actor_capacity_state_ck")
    ex("ALTER TABLE public.core_organismmembershiptransition ADD CONSTRAINT s015_0022_organismmembershiptransition_actor_capacity_state_ck CHECK ((actor_state IN ('PARTY','LIVING_ORGANISM')) = (actor_capacity = 'STANDING'))")
    ex("CREATE OR REPLACE FUNCTION public.s015_0022_guard_capacity() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$ BEGIN IF NEW.actor_state IN ('PARTY','LIVING_ORGANISM') AND NEW.actor_capacity <> 'STANDING' THEN RAISE EXCEPTION 'S015 capacity refused: acting party rows must be STANDING' USING ERRCODE = 'integrity_constraint_violation'; END IF; IF NEW.actor_state NOT IN ('PARTY','LIVING_ORGANISM') AND NEW.actor_capacity <> 'NA_NO_PARTY' THEN RAISE EXCEPTION 'S015 capacity refused: non-party rows must be NA_NO_PARTY' USING ERRCODE = 'integrity_constraint_violation'; END IF; RETURN NEW; END $$;")
    previous = importlib.import_module("core.migrations.0025_s015_application_routes")
    actor_sql, = (sql for sql in previous.INSTALL_SQL if sql.lstrip().startswith("CREATE FUNCTION public.s3_guard_lo_actor()"))
    ex(actor_sql.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1).replace("NEW.actor_capacity <> 'NA_NO_PARTY'", "NEW.actor_capacity <> 'STANDING'"))
    for statement in (CAPACITY_SQL, PERMISSION_SQL, ACTOR_SQL):
        ex(statement)
    for table in ("core_organismmembershiptransition", TABLE):
        for name, function in (("00_capacity", "s3fu_guard_lo_capacity"), ("20_permission", "s3fu_guard_lo_admission_permission")):
            ex(f"CREATE TRIGGER s3fu_{name} BEFORE INSERT ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.{function}()")
    ex(f"CREATE TRIGGER s3fu_10_actor BEFORE INSERT ON public.{TABLE} FOR EACH ROW EXECUTE FUNCTION public.s3fu_guard_application_lineage_actor()")


def reverse(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        raise RuntimeError("S015 0028 requires PostgreSQL")
    ex = lambda sql: schema_editor.execute(sql, params=None)
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"SELECT count(*) FROM public.{ANCHOR}")
        if cursor.fetchone()[0]:
            raise RuntimeError("S015 0028 reversal refused: application lineage exists; correct forward")
        cursor.execute("SELECT count(*) FROM public.core_organismmembershiptransition "
                       "WHERE actor_state = 'LIVING_ORGANISM'")
        if cursor.fetchone()[0]:
            raise RuntimeError("S015 0028 reversal refused: LO-actor STANDING membership rows require forward correction")
    for table in ("core_organismmembershiptransition", TABLE):
        for name in ("00_capacity", "20_permission"):
            ex(f"DROP TRIGGER s3fu_{name} ON public.{table}")
    ex(f"DROP TRIGGER s3fu_10_actor ON public.{TABLE}")
    for function in ("s3fu_guard_application_lineage_actor", "s3fu_guard_lo_admission_permission", "s3fu_guard_lo_capacity"):
        ex(f"DROP FUNCTION public.{function}()")
    ex("ALTER TABLE public.core_organismmembershiptransition DROP CONSTRAINT s015_0022_organismmembershiptransition_actor_capacity_state_ck")
    ex("ALTER TABLE public.core_organismmembershiptransition ADD CONSTRAINT s015_0022_organismmembershiptransition_actor_capacity_state_ck CHECK ((actor_state = 'PARTY') = (actor_capacity = 'STANDING'))")
    ex("CREATE OR REPLACE FUNCTION public.s015_0022_guard_capacity() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$\n" + source(22).GUARD_CAPACITY_BODY + "$$;")
    previous = importlib.import_module("core.migrations.0025_s015_application_routes")
    actor_sql, = (sql for sql in previous.INSTALL_SQL if sql.lstrip().startswith("CREATE FUNCTION public.s3_guard_lo_actor()"))
    ex(actor_sql.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
    for table in ("core_governedeventpart", "core_governedeventcontent"):
        for sql in vocab_sql(table, False):
            ex(sql)
    ex(f"ALTER TABLE public.{ANCHOR} DROP CONSTRAINT s015_0021_{ANCHOR_TOKEN}_head_fk")
    ex(lock_sql(False))
    ex(map_sql(False))


class Migration(migrations.Migration):
    atomic = True
    dependencies = [("core", "0027_s015_application_authority_lineage")]
    operations = [
        migrations.CreateModel(
            name="ApplicationLineage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("lineage_uuid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("application", models.OneToOneField(to="core.application", on_delete=PROTECT, related_name="lineage")),
            ],
        ),
        migrations.CreateModel(
            name="ApplicationLineageEvent",
            fields=member_fields(),
            options={"constraints": [
                models.UniqueConstraint(fields=("application_lineage", "sequence"), name="s015_0021_applicationlineageevent_anchor_seq_uniq"),
                models.UniqueConstraint(fields=("predecessor",), name="s015_0021_applicationlineageevent_pred_uniq"),
                models.UniqueConstraint(fields=("actor", "action", "idempotency_key"), name="s015_0021_applicationlineageevent_idem_uniq"),
            ]},
        ),
        migrations.AddField(
            model_name="applicationlineage", name="head_event",
            field=models.OneToOneField(to="core.applicationlineageevent", on_delete=PROTECT, related_name="+", null=True, blank=True),
        ),
        migrations.RunPython(install, reverse),
    ]