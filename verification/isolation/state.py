"""Reference requirements and state capture for the isolation instrument (repository route; lineage: candidate v0.4, UFUND-2).

MAINTENANCE: a packet that adds a core migration declares its head in REQUIRED_TRIGGER_COUNT, with the ground for the
count. Until it does, the route reports isolation NOT ESTABLISHED at CP-0 for that head: the declaration is part of the
packet, not an assumption of the route.

v0.3 changes one thing: the schema version is declared, not assumed. The run declares its expected core
migration head in VERIFICATION_EXPECTED_CORE_HEAD; the head must be exactly that (not merely present), and the
required trigger count is looked up for that head. An undeclared or unknown head fails CP-0. Everything else
is unchanged from v0.2.

Checkpoints
  CP-0  after the test runner has created and migrated the test database, before the first test.
        The captured state must first satisfy the DECLARED REQUIREMENTS below; only then is it
        used as the per-test comparison reference.
  CP-A  unittest startTest for each test: after Django's _pre_setup, before the test's own setUp.
Read-only: every query here is a SELECT.

CP-A' uses the four light queries planned from CP-0: protection counts, row counts, sequence
discipline and seed/framework/migration/privilege content. A mismatch, a declared DDL test or
the per-test switch causes a full capture for diagnosis and comparison.
"""
import hashlib
import importlib
import json
import os

from django.db import connection

# ---------------------------------------------------------------- declared requirements
# REQ-T1  every non-internal trigger in the schema is enabled ('O')
#         ground: no migration 0001-0022 disables a trigger or enables one in another mode [D]
# REQ-T2  non-internal trigger count equals REQUIRED_TRIGGER_COUNT for the declared head
#         0022: 322 - D-S015-PKT-A-3 section 7 (0021's 246 + 0022's 76) [S]; U0 runs 2 and 3 observed 322 [O]
#         0023: 322 - 0023 creates and drops no trigger (it replaces two functions and five CHECKs) [D]
#         0024: 322 - 0024 creates and drops no trigger (it replaces two function bodies) [D]
#         0025: 354 - seven record-time, seven row and seven truncate, six route guards,
#               one decision guard, one scoped actor guard, three deferred link guards [D]
#         0026: 354 - replaces four guard-function bodies; adds or removes no triggers [D]
#         0027: 354 - adds columns and two CHECKs; adds or removes no triggers [D]
#         0028: 377 - 7 event + 2 anchor + 4 immutable + 5 contract + 3 application
#               guards + 2 membership guards = 23 additional non-internal triggers [D]
#         0029: 390 - three version record-time + six version update/delete + two gateway
#               update/delete + two LO event insert = 13 additional non-internal triggers [D]
#         0030: 392 - replaces two existing route triggers with scoped equivalents and adds
#               one automatic engagement and one automatic decision insert trigger [D]
#         0031: 393 - one new admission-route BEFORE INSERT trigger; no existing trigger replaced [D]
#         0032: 393 - replaces one function body; adds or removes no triggers [D]
#         0033: 395 - two engagement-basis triggers added; decision trigger is rebound in place [D]
#         0034: 398 - one decision insert, one update/delete, and one truncate guard added [D]
#         0035: 398 - widens the role-chain actor-state CHECK and changes its capacity-state CHECK; adds or removes no triggers [D]
#         0036: 401 - adds role capacity and attribution guards plus the membership proposed-end actor guard [D]
#         0037: 405 - adds consent insert, append-only update/delete, truncate, and ASSIGN-consent guards [D]
#         0038: 405 - adds the nullable Coordinator nomination-policy field; no triggers added [D]
#         0039: 405 - adds two nullable role-suspension settings; no triggers added [D]
#         0040: 405 - state-only; columns relaxed by 0022
#         0041: 406 - one current-membership assign trigger [D]
#         0042: 418 - eleven event/snapshot guards and one snapshot commitment trigger [D]
#         0043: 429 - eleven registration triggers [D]
#         0044: 433 - four Birthing snapshot/terms triggers [D]
#         0045: 433 + 25 = 458 - Circle kind (1), governed rule (6), request (4),
#               decision (4), participation (5), ending (4), membership-end completeness (1) [D]
#         0046: 458 + 12 = 470 - record-time, validate, immutable, and truncate
#               triggers for each of the three SE tables [D]
#         0048: 470 + 1 = 471 - deferred first-GRANTED-event constraint trigger [D]
#         0049: 471 + 4 = 475 - intake insert-time, update, delete, and truncate triggers [D]
#         0050: 475 + 0 = 475 - two cache functions replaced in place (CREATE OR REPLACE); no trigger created or dropped [D]
#         0051: 475 + 8 = 483 - SR definition guards sr_00/sr_10/sr_20/sr_30 (insert, update, deferred superseded
#               and citations) on core_organismroledefinition; s015_sr_provision_* record-time insert, update,
#               delete and truncate on core_organismroledefinitionprovision [D]
#         0052: 483 + 0 = 483 - two office-guard functions replaced in place (CREATE OR REPLACE:
#               s2b_guard_office_attribution, s2b_guard_assign_consent); no trigger created or dropped [D]
# REQ-G1  the fifteen required truncate guardians of the standard backend are present and enabled [D]
# REQ-C1  every constraint named s015_* is validated
#         ground: no migration 0001-0025 creates a constraint NOT VALID [D]
# REQ-D1  core_role holds exactly the rows migration 0003's create_triad_roles defines
#         ground: the migration function itself, executed against a recording stub (values not copied)
# REQ-D2  every other table holds zero rows, except the framework tables below
# REQ-D3  django_content_type and auth_permission are non-empty; their natural keys are
#         framework-generated by post_migrate, so they are compared with CP-0 (declared snapshot use)
# 0053: 483 + 0 = 483; three existing guard bodies replaced, no trigger or seed row added.
# REQ-D4  the latest core migration recorded is exactly the declared head (VERIFICATION_EXPECTED_CORE_HEAD)
# REQ-P1  PUBLIC holds no INSERT privilege on s015_transaction_register
#         ground: 0021 'REVOKE ALL ON public.s015_transaction_register FROM PUBLIC' [D]; the selected
#         test test_neg_non_owner_cannot_write_register relies on it [D]
# OWNERSHIP AND GRANTS: no selected test statically alters grants, ownership or roles (no GRANT,
#         REVOKE, OWNER, SET ROLE or CREATE ROLE in the three suites or their support module) [D];
#         one relies on a privilege (REQ-P1). Table and function owners and ACLs are therefore
#         captured and compared at every CP-A rather than excluded.
# DECLARED EXCLUSIONS (accepted by the Human Governor for this bounded check, 16 Sep 2026):
#         sequence and identity positions; primary keys of framework rows.
# EVIDENCE CLAIM: equality at the compared properties only. NOT complete database-state equivalence.
FRAMEWORK_TABLES = ("django_content_type", "auth_permission")
SEED_TABLES = ("core_role",)
MIGRATION_SEED_ROW_COUNTS_BY_HEAD = {
    "0045_sbg_governance_formation": {"core_governedrule": 1},
    "0046_se_entitlement_foundation": {"core_governedrule": 3},
    "0047_sek1_platform_rule_statement_start": {"core_governedrule": 3},
    "0048_se_entitlement_granted_event_guard": {"core_governedrule": 3},
    "0049_h100_command_intake": {"core_governedrule": 3},
    "0050_clk_mono_1_cache_coordinate_detail": {"core_governedrule": 3},
    "0051_sr_role_definitions": {"core_governedrule": 3},
    "0052_sr_office_guard_widening": {"core_governedrule": 3},
    "0053_sr_office_safeguards": {"core_governedrule": 3},
}
LEDGER_TABLES = ("django_migrations",)
REQUIRED_TRIGGER_COUNT = {"0022_s015_governed_chain_contract": 322, "0023_s015_canonical_form_s015f4": 322,
                          "0024_s015_compact_commitment_preimages": 322, "0025_s015_application_routes": 354,
                          "0026_s015_application_admission_time": 354,
                          "0027_s015_application_authority_lineage": 354,
                          "0028_s015_application_lineage_chain": 377,
                          "0029_s015_application_gateway": 390,
                          "0030_s015_automatic_application": 392,
                          "0031_s1fu_admission_actor_requirement": 393,
                          "0032_s015_register_cleanup_nonblocking": 393,
                          "0033_s015_application_authority": 395,
                          "0034_s015_authority_decisions": 398,
                          "0035_s015_role_chain_actor_capacity": 398,
                          "0036_s2b_role_attribution_guards": 401,
                          "0037_s2b_office_appointment_consent": 405,
                          "0038_s2b_coordinator_nomination_policy": 405,
                          "0039_s2b_role_suspension_settings": 405,
                          "0040_s2b_role_actor_nullable_state": 405,
                          "0041_r4s2_assign_current_membership": 406,
                          "0042_sb1_founding_terms_phases": 418,
                          "0043_p1_governed_registration": 429,
                          "0044_sb2_birthing_snapshot": 433,
                          "0045_sbg_governance_formation": 458,
                          "0046_se_entitlement_foundation": 470,
                          "0047_sek1_platform_rule_statement_start": 470,
                          "0048_se_entitlement_granted_event_guard": 471,
                          "0049_h100_command_intake": 475,
                          "0050_clk_mono_1_cache_coordinate_detail": 475,
                          "0051_sr_role_definitions": 483,
                          "0052_sr_office_guard_widening": 483,
                          "0053_sr_office_safeguards": 483}
EXPECTED_HEAD_ENV = "VERIFICATION_EXPECTED_CORE_HEAD"
OBSERVATION_KEYS = ("sequence_discipline",)


def _rows(sql, params=None, conn=None):
    active_connection = conn if conn is not None else connection
    with active_connection.cursor() as cursor:
        cursor.execute(sql, params)
        return [tuple(r) for r in cursor.fetchall()]


def _sequence_discipline_observations(row_counts, conn=None):
    """Capture sequence configuration and next-id state without consuming values."""
    active_connection = conn if conn is not None else connection
    sequence_rows = _rows(
        """
        SELECT table_relation.relname, column_info.attname, sequence_relation.relname,
               sequence.seqincrement, sequence.seqmin, sequence.seqmax,
               sequence.seqstart, sequence.seqcycle, sequence.seqcache
        FROM pg_class sequence_relation
        JOIN pg_sequence sequence ON sequence.seqrelid = sequence_relation.oid
        JOIN pg_depend dependency
          ON dependency.objid = sequence_relation.oid
         AND dependency.classid = 'pg_class'::regclass
         AND dependency.refclassid = 'pg_class'::regclass
         AND dependency.deptype IN ('a', 'i')
        JOIN pg_class table_relation ON table_relation.oid = dependency.refobjid
        JOIN pg_attribute column_info
          ON column_info.attrelid = table_relation.oid
         AND column_info.attnum = dependency.refobjsubid
        JOIN pg_namespace namespace ON namespace.oid = table_relation.relnamespace
        WHERE namespace.nspname = current_schema()
          AND sequence_relation.relkind = 'S'
          AND table_relation.relkind IN ('r', 'p')
        ORDER BY 1, 2
        """,
        conn=active_connection,
    )
    observations = []
    quote_name = active_connection.ops.quote_name
    for (
        table,
        column,
        sequence,
        increment,
        minimum,
        maximum,
        start,
        cycle,
        cache,
    ) in sequence_rows:
        observation = {
            "table": table,
            "column": column,
            "sequence": sequence,
            "increment": increment,
            "minimum": minimum,
            "maximum": maximum,
            "start": start,
            "cycle": cycle,
            "cache": cache,
        }
        supported = (
            increment == 1
            and minimum == 1
            and start == 1
            and cycle is False
            and cache == 1
        )
        if not supported:
            observation["status"] = "UNSUPPORTED"
            observations.append(observation)
            continue

        row_count = row_counts.get(table)
        if row_count is None:
            observation["status"] = "UNOBSERVED"
            observations.append(observation)
            continue
        if row_count == 0:
            observation["status"] = "EMPTY"
            observations.append(observation)
            continue

        highest = _rows(
            "SELECT max(%s) FROM %s" % (quote_name(column), quote_name(table))
        )[0][0]
        if highest is None:
            observation["status"] = "INDETERMINATE"
            observations.append(observation)
            continue

        last_value, is_called = _rows(
            "SELECT last_value, is_called FROM %s" % quote_name(sequence)
        )[0]
        next_value = last_value + 1 if is_called else last_value
        observation.update(
            highest=highest,
            last_value=last_value,
            is_called=is_called,
            next=next_value,
        )
        if next_value > maximum:
            observation["status"] = "EXHAUSTED"
        elif next_value <= highest:
            observation["status"] = "BEHIND"
        else:
            observation["status"] = "HEALTHY"
        observations.append(observation)
    return observations


def _sequence_configuration_violation(observation):
    return (
        "REQ-S0 unsupported sequence configuration: %s "
        "(increment %s, min %s, max %s, start %s, cycle %s, cache %s)"
        % (
            observation["sequence"],
            observation["increment"],
            observation["minimum"],
            observation["maximum"],
            observation["start"],
            observation["cycle"],
            observation["cache"],
        )
    )


def _sequence_invariant_violation(observation):
    common = (
        "(sequence %s; last_value %s, is_called %s)"
        % (
            observation["sequence"],
            observation.get("last_value"),
            str(observation.get("is_called")).lower(),
        )
    )
    if observation["status"] == "EXHAUSTED":
        return (
            "REQ-S1 %s.%s counter next %s exceeds sequence maximum %s %s"
            % (
                observation["table"],
                observation["column"],
                observation["next"],
                observation["maximum"],
                common,
            )
        )
    if observation["status"] == "BEHIND":
        return (
            "REQ-S1 %s.%s counter next %s is at or behind highest row %s %s"
            % (
                observation["table"],
                observation["column"],
                observation["next"],
                observation["highest"],
                common,
            )
        )
    return (
        "REQ-S1 %s.%s invariant could not be established (%s)"
        % (
            observation["table"],
            observation["column"],
            observation["status"],
        )
    )


def expected_seed_rows():
    """Run 0003's own seed function against a stub that records what it would create."""
    recorded = []
    write_aliases = []

    class _Manager:
        def using(self, alias):
            write_aliases.append(alias)
            return self

        def get_or_create(self, **kwargs):
            defaults = kwargs.pop("defaults", {})
            recorded.append(tuple(sorted({**kwargs, **defaults}.items())))
            return None, True

    class _Model:
        objects = _Manager()

    class _Connection:
        alias = "default"

    class _SchemaEditor:
        connection = _Connection()

    class _Apps:
        def get_model(self, _app_label, _model_name):
            return _Model

    module = importlib.import_module("core.migrations.0003_seed_triad_roles")
    module.create_triad_roles(_Apps(), _SchemaEditor())
    if write_aliases != ["default"] * 3:
        raise RuntimeError("0003 seed stub did not route every write through its schema editor")
    return sorted(recorded)


def capture(conn=None):
    s = {}
    s["triggers"] = _rows(
        """SELECT c.relname, t.tgname, t.tgenabled, t.tgtype, p.proname, md5(p.prosrc)
           FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
           JOIN pg_namespace n ON n.oid = c.relnamespace JOIN pg_proc p ON p.oid = t.tgfoid
           WHERE n.nspname = current_schema() AND NOT t.tgisinternal ORDER BY 1, 2""", conn=conn)
    s["constraints"] = _rows(
        """SELECT c.relname, co.conname, co.contype, co.convalidated, pg_get_constraintdef(co.oid)
           FROM pg_constraint co JOIN pg_class c ON c.oid = co.conrelid
           JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = current_schema() ORDER BY 1, 2""", conn=conn)
    s["ownership_grants"] = _rows(
        """SELECT 'table', c.relname, pg_get_userbyid(c.relowner), coalesce(c.relacl::text, '')
           FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = current_schema() AND c.relkind IN ('r', 'p', 'v', 'S')
           UNION ALL
           SELECT 'function', p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')',
                  pg_get_userbyid(p.proowner), coalesce(p.proacl::text, '')
           FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
           WHERE n.nspname = current_schema() ORDER BY 1, 2""", conn=conn)
    s["public_register_insert"] = _rows(
        "SELECT has_table_privilege('public', 'public.s015_transaction_register', 'INSERT')", conn=conn)[0][0]
    s["functions"] = _rows(
        """SELECT p.proname, pg_get_function_identity_arguments(p.oid), md5(p.prosrc), p.prosecdef, p.provolatile
           FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
           WHERE n.nspname = current_schema() ORDER BY 1, 2""", conn=conn)
    tables = [r[0] for r in _rows(
        """SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = current_schema() AND c.relkind IN ('r', 'p') ORDER BY 1""", conn=conn)]
    counts = {}
    active_connection = conn if conn is not None else connection
    with active_connection.cursor() as cursor:
        for t in tables:
            cursor.execute('SELECT count(*) FROM "%s"' % t.replace('"', '""'))
            counts[t] = cursor.fetchone()[0]
    s["row_counts"] = counts
    s["sequence_discipline"] = _sequence_discipline_observations(counts, conn=active_connection)
    s["core_role"] = sorted(
        tuple(sorted({"name": n, "description": d}.items()))
        for n, d in _rows("SELECT name, description FROM core_role", conn=conn))
    s["content_types"] = _rows("SELECT app_label, model FROM django_content_type ORDER BY 1, 2", conn=conn)
    s["permissions"] = _rows(
        """SELECT ct.app_label, ct.model, p.codename FROM auth_permission p
           JOIN django_content_type ct ON ct.id = p.content_type_id ORDER BY 1, 2, 3""", conn=conn)
    s["migrations"] = _rows("SELECT app, name FROM django_migrations ORDER BY 1, 2", conn=conn)
    return s


def build_light_plan(reference):
    table_names = sorted(reference["row_counts"])
    if not table_names:
        raise RuntimeError("CP-A' cannot be planned without captured tables")
    row_count_sql = "SELECT " + ", ".join(
        "(SELECT count(*) FROM %s)" % connection.ops.quote_name(table)
        for table in table_names
    )
    return {
        "table_names": table_names,
        "row_count_sql": row_count_sql,
        "protection_sql": """
SELECT
    (SELECT count(*) FROM pg_trigger t
     JOIN pg_class c ON c.oid = t.tgrelid
     JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = current_schema() AND NOT t.tgisinternal),
    (SELECT count(*) FROM pg_trigger t
     JOIN pg_class c ON c.oid = t.tgrelid
     JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = current_schema() AND NOT t.tgisinternal
       AND t.tgenabled <> 'O'),
    (SELECT count(*) FROM pg_constraint co
     JOIN pg_class c ON c.oid = co.conrelid
     JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = current_schema()),
    (SELECT count(*) FROM pg_constraint co
     JOIN pg_class c ON c.oid = co.conrelid
     JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = current_schema()
       AND left(co.conname, 5) = 's015_' AND NOT co.convalidated),
    (SELECT count(*) FROM pg_proc p
     JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = current_schema())
""",
        "content_sql": """
SELECT
    (SELECT md5(COALESCE(
        jsonb_agg(jsonb_build_array(name, description) ORDER BY name, description)::text,
        '[]'
    )) FROM core_role),
    (SELECT md5(COALESCE(
        jsonb_agg(jsonb_build_array(app_label, model) ORDER BY app_label, model)::text,
        '[]'
    )) FROM django_content_type),
    (SELECT md5(COALESCE(
        jsonb_agg(jsonb_build_array(ct.app_label, ct.model, p.codename)
                  ORDER BY ct.app_label, ct.model, p.codename)::text,
        '[]'
    ))
     FROM auth_permission p
     JOIN django_content_type ct ON ct.id = p.content_type_id),
    (SELECT md5(COALESCE(
        jsonb_agg(jsonb_build_array(app, name) ORDER BY app, name)::text,
        '[]'
    )) FROM django_migrations),
    has_table_privilege(
        'public', 'public.s015_transaction_register', 'INSERT'
    )
""",
    }


def capture_light(light_plan, conn=None):
    active_connection = conn if conn is not None else connection
    with active_connection.cursor() as cursor:
        cursor.execute(light_plan["protection_sql"])
        protection = cursor.fetchone()
        cursor.execute(light_plan["row_count_sql"])
        row_counts = dict(zip(light_plan["table_names"], cursor.fetchone()))
    nonempty_tables = [
        table for table, count in row_counts.items() if count > 0
    ]
    sequence_report = active_connection.ops._sequence_discipline_report(nonempty_tables)
    with active_connection.cursor() as cursor:
        cursor.execute(light_plan["content_sql"])
        content = cursor.fetchone()
    return {
        "protection": protection,
        "row_counts": row_counts,
        "sequence": sequence_report,
        "content": content,
    }


def light_baseline_violations(reference, observed):
    expected_protection = (
        len(reference["triggers"]),
        sum(1 for trigger in reference["triggers"] if trigger[2] != "O"),
        len(reference["constraints"]),
        sum(
            1
            for constraint in reference["constraints"]
            if constraint[1].startswith("s015_") and not constraint[3]
        ),
        len(reference["functions"]),
    )
    violations = []
    if observed["protection"] != expected_protection:
        violations.append("CP-0 Q1 protection counts differ from the full capture")
    if observed["row_counts"] != reference["row_counts"]:
        violations.append("CP-0 Q2 row counts differ from the full capture")
    if observed["sequence"]["findings"]:
        violations.append("CP-0 Q3 sequence check found unsupported or invalid sequences")
    if observed["content"][4] != reference["public_register_insert"]:
        violations.append("CP-0 Q4 register privilege differs from the full capture")
    return violations


def compare_light(reference_light, observed):
    violations = []
    if observed["protection"] != reference_light["protection"]:
        violations.append("Q1 protection counts differ")
    if observed["row_counts"] != reference_light["row_counts"]:
        violations.append("Q2 row counts differ")
    if (
        observed["sequence"]["sequences_observed"]
        != reference_light["sequence"]["sequences_observed"]
    ):
        violations.append("Q3 observed sequence set differs")
    if observed["sequence"]["findings"]:
        violations.append("Q3 sequence check found unsupported or invalid sequences")
    if observed["content"] != reference_light["content"]:
        violations.append("Q4 seed, framework, migration, or privilege content differs")
    return violations


def unexpected_data_rows(captured):
    """Return row-count excesses beyond framework, ledger, and declared migration seed rows."""
    head = max(
        (name for app, name in captured["migrations"] if app == "core"),
        default=None,
    )
    allowed_seed_rows = MIGRATION_SEED_ROW_COUNTS_BY_HEAD.get(head, {})
    excluded_tables = set(FRAMEWORK_TABLES + SEED_TABLES + LEDGER_TABLES)
    unexpected = {}
    for table, count in captured["row_counts"].items():
        if table in excluded_tables:
            continue
        extra = count - allowed_seed_rows.get(table, 0)
        if extra > 0:
            unexpected[table] = extra
    return unexpected


def digest(value):
    if isinstance(value, dict):
        value = {
            key: item for key, item in value.items() if key not in OBSERVATION_KEYS
        }
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def check_requirements(s, required_guardians):
    """CP-0 requirements. Returns a list of violations (empty = requirements met)."""
    v = []
    disabled = [(t, n, e) for t, n, e, _, _, _ in s["triggers"] if e != "O"]
    if disabled:
        v.append("REQ-T1 triggers not enabled: %s" % disabled[:10])
    head = os.environ.get(EXPECTED_HEAD_ENV)
    if head not in REQUIRED_TRIGGER_COUNT:
        v.append("REQ-D4 expected core head %r is not declared or not known to this instrument" % head)
    elif len(s["triggers"]) != REQUIRED_TRIGGER_COUNT[head]:
        v.append("REQ-T2 trigger count %d, required %d at %s" % (len(s["triggers"]), REQUIRED_TRIGGER_COUNT[head], head))
    present = {(t, n): e for t, n, e, _, _, _ in s["triggers"]}
    for table, name in required_guardians.items():
        if present.get((table, name)) != "O":
            v.append("REQ-G1 required guardian missing or disabled: %s.%s" % (table, name))
    invalid = [(t, n) for t, n, _, ok, _ in s["constraints"] if n.startswith("s015_") and not ok]
    if invalid:
        v.append("REQ-C1 s015 constraints not validated: %s" % invalid[:10])
    expected = expected_seed_rows()
    if s["core_role"] != expected:
        v.append("REQ-D1 core_role rows %s, required %s" % (s["core_role"], expected))
    expected_migration_seeds = MIGRATION_SEED_ROW_COUNTS_BY_HEAD.get(head, {})
    seed_mismatches = {
        table: (s["row_counts"].get(table, 0), count)
        for table, count in expected_migration_seeds.items()
        if s["row_counts"].get(table, 0) != count
    }
    if seed_mismatches:
        v.append("REQ-D2 migration seed rows %s" % seed_mismatches)
    unexpected = unexpected_data_rows(s)
    if unexpected:
        v.append("REQ-D2 tables holding rows: %s" % dict(list(unexpected.items())[:15]))
    if not s["content_types"] or not s["permissions"]:
        v.append("REQ-D3 framework tables empty")
    if s["public_register_insert"] is not False:
        v.append("REQ-P1 PUBLIC holds INSERT on s015_transaction_register")
    for observation in s.get("sequence_discipline", ()):
        if observation["status"] == "UNSUPPORTED":
            v.append(_sequence_configuration_violation(observation))
        elif observation["status"] in ("BEHIND", "EXHAUSTED"):
            v.append(_sequence_invariant_violation(observation))
        elif observation["status"] in ("UNOBSERVED", "INDETERMINATE"):
            v.append(_sequence_invariant_violation(observation))
    core = sorted(name for app, name in s["migrations"] if app == "core")
    if head in REQUIRED_TRIGGER_COUNT and (not core or core[-1] != head):
        v.append("REQ-D4 latest core migration %s, declared head %s" % (core[-1] if core else None, head))
    return v


def compare(reference, s, ignore_tables=()):
    """CP-A comparison. Returns (data_violations, protection_violations)."""
    data, prot = [], []
    counts = {t: c for t, c in s["row_counts"].items() if t not in ignore_tables}
    ref_counts = {t: c for t, c in reference["row_counts"].items() if t not in ignore_tables}
    if set(counts) != set(ref_counts):
        data.append("table set changed: added %s removed %s" % (sorted(set(counts) - set(ref_counts)), sorted(set(ref_counts) - set(counts))))
    for t in sorted(set(counts) & set(ref_counts)):
        if t in FRAMEWORK_TABLES or t in LEDGER_TABLES:
            continue
        if counts[t] != ref_counts[t]:
            data.append("%s rows %d, reference %d" % (t, counts[t], ref_counts[t]))
    for key in ("core_role", "content_types", "permissions", "migrations"):
        if s[key] != reference[key]:
            data.append("%s differs from reference" % key)
    if s["public_register_insert"] != reference["public_register_insert"]:
        prot.append("public_register_insert %s, reference %s" % (s["public_register_insert"], reference["public_register_insert"]))
    for key in ("triggers", "constraints", "functions", "ownership_grants"):
        a, b = set(reference[key]), set(s[key])
        if a != b:
            prot.append("%s: missing %s; unexpected %s" % (key, sorted(a - b)[:5], sorted(b - a)[:5]))
    for observation in s.get("sequence_discipline", ()):
        if observation["table"] in ignore_tables:
            continue
        if observation["status"] in (
            "BEHIND",
            "EXHAUSTED",
            "UNOBSERVED",
            "INDETERMINATE",
        ):
            data.append(_sequence_invariant_violation(observation))
    return data, prot
