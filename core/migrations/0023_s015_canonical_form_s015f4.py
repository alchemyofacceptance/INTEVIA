"""S015 PKT-A-3 repair: the canonical form moves from s015f3 to s015f4.

Requirement (designated design LD_RETURN_PKT_A_3_MIGRATION_0022_DESIGN_v0_6.md, sha256 59a07477..., section 9;
census LD_PKT_A_3_DESIGN_CENSUS_v2.py, sha256 fb50d418..., REPLACED_FUNCTIONS and FP_SHAPE_CHECKS; ruling U21h
section 3, 12 Sep 2026: PKT-A-3 bumps the canonical form): the literal s015f3 in s015_canonical_envelope,
s015_digest_envelope and the five fingerprint shape CHECKs moves to s015f4. Nothing else about those objects changes.

Finding it repairs: migration 0022 added fourteen columns to every event table, which entered the canonical record,
but did not move the form tag. 0022 is left unchanged.

Candidate v0.2 (UFUND-2). What each run of this migration establishes, in both directions:

1. Exclusion. Inside the migration transaction, the seventeen fingerprint-bearing tables are locked in one fixed
   alphabetical order before anything is read: ACCESS EXCLUSIVE on the five tables whose shape check is replaced,
   SHARE ROW EXCLUSIVE on the twelve event tables. Both modes conflict with INSERT, UPDATE and DELETE, and the locks
   are held until the migration commits or rolls back. Precedent: 0021's preflight.
2. Refusal. If any of the seventeen tables has a row, the migration refuses before any change and names each
   populated table with its row count. Every such row carries or implies an s015f3-labelled fingerprint over the
   0022 column set, and no ruling defines its conversion.
3. Source state and target state, verified exactly, not by substring:
   - each envelope function is found by its exact signature, is the only function of that name in any schema,
     carries the exact 0021 attributes and the exact 0021 body with only the form literal differing, and returns
     the values the published rule requires for fixed probes;
   - each shape check is found by exact table and name, is a validated, local CHECK on exactly its fingerprint
     column, has a definition identical to a reference built by the same installer route from the required
     pattern, and that reference accepts the required shape and refuses near misses;
   - the five named checks are the only CHECKs in the public schema that mention an s015f form literal.
   Any difference refuses and rolls the whole migration back.

The comparison of function bodies normalises CRLF to LF only, as 0022's preflight does; nothing else is normalised.
"""
import hashlib

from django.db import migrations, models, transaction
from django.db.models import Q
from django.db.utils import DatabaseError

S015F3 = "s015f3"
S015F4 = "s015f4"
FORMS = (S015F3, S015F4)


def shape_pattern(tag):
    return "^" + tag + ":[0-9a-f]{64}$"


# (model name, table, fingerprint column) for the four checks created through Django's AddConstraint in 0021
CACHE_ANCHORS = (
    ("livingorganism", "core_livingorganism", "state_source_event_set_fingerprint"),
    ("circle", "core_circle", "state_source_event_set_fingerprint"),
    ("organismmembership", "core_organismmembership", "state_source_event_set_fingerprint"),
    ("contextualroleassignment", "core_contextualroleassignment", "state_source_event_set_fingerprint"),
)
# (table, constraint, column) for the check created in raw SQL by 0021's installer
RAW_SHAPE_CHECK = ("core_governedobligationcase", "s015_0021_goc_fp_shape_ck", "status_source_event_set_fingerprint")
EVENT_TABLES = (
    "core_livingorganismevent", "core_circlestateevent", "core_organismmembershiptransition",
    "core_contextualroleassignmentstateevent", "core_membershipconditionstateevent", "core_authorityinvalidationevent",
    "core_determinationcontestevent", "core_coverageassessment", "core_restrictedcontinuityevent",
    "core_visibilitygrantstateevent", "core_obligationstateevent", "core_planningclassificationevent",
)
ALTERED_TABLES = frozenset([t for _, t, _ in CACHE_ANCHORS] + [RAW_SHAPE_CHECK[0]])
ROW_TABLES = tuple(sorted(ALTERED_TABLES | frozenset(EVENT_TABLES)))  # fixed lock order

SHAPE_CHECKS = tuple(
    {"table": t, "name": f"s015_0021_{m}_fp_shape_ck", "column": c, "route": "django", "model": m}
    for m, t, c in CACHE_ANCHORS
) + ({"table": RAW_SHAPE_CHECK[0], "name": RAW_SHAPE_CHECK[1], "column": RAW_SHAPE_CHECK[2], "route": "raw", "model": None},)


# ---------------------------------------------------------------------------------------------------------------
# The two envelope functions: exact definitions. The bodies are 0021's installed bodies with the form literal as a
# parameter; the preparation record shows the s015f3 instance equals the text 0021 installs.
# ---------------------------------------------------------------------------------------------------------------
def envelope_body(tag):
    return (
        "\n    SELECT '[\"" + tag + "\",\"' || p_token || '\",\"' || p_identity::text || '\",['\n"
        "        || coalesce(array_to_string(p_records, ','), '') || ']]'\n"
    )


def digest_body(tag):
    return "\n    SELECT ('" + tag + ":' || encode(sha256(convert_to(p_preimage, 'UTF8')), 'hex'))::varchar(71)\n"


FUNCTION_SPECS = (
    {"signature": "public.s015_canonical_envelope(text,uuid,text[])", "name": "s015_canonical_envelope",
     "qualified": "public.s015_canonical_envelope(text, uuid, text[])",
     "header": "public.s015_canonical_envelope(p_token text, p_identity uuid, p_records text[])\nRETURNS text LANGUAGE sql IMMUTABLE",
     "body": envelope_body, "rettype": "text", "argtypes": "text, uuid, text[]", "argnames": ["p_token", "p_identity", "p_records"]},
    {"signature": "public.s015_digest_envelope(text)", "name": "s015_digest_envelope",
     "qualified": "public.s015_digest_envelope(text)",
     "header": "public.s015_digest_envelope(p_preimage text) RETURNS varchar(71) LANGUAGE sql IMMUTABLE",
     "body": digest_body, "rettype": "character varying", "argtypes": "text", "argnames": ["p_preimage"]},
)
FUNCTION_FIXED_ATTRIBUTES = {
    "nspname": "public", "prokind": "f", "lanname": "sql", "provolatile": "i", "proisstrict": False,
    "prosecdef": False, "proleakproof": False, "proparallel": "u", "proretset": False, "proconfig": None,
    "acl_default": True, "sqlbody_absent": True,
}

# Published V1 (empty set, Living Organism) of each corpus: preimage and digest, from vectors/<form>_fixed_vectors.json
V1_IDENTITY = "11111111-2222-3333-4444-555555555555"
V1_DIGEST = {
    S015F3: "s015f3:cdf72ff704c88d0d47f52ebc1b133cebdbaa7832d41f5066096903aa6ae3d99b",
    S015F4: "s015f4:c4148153c2522da7455f4f41b20fa10a4cd9be15720181e66bd3ea5ef7e8ab91",
}


def function_probes(tag):
    """(token, identity, records) -> expected preimage, each by the published rule; digest = tag + ':' + sha256(UTF-8)."""
    v1 = '["' + tag + '","core_livingorganism","' + V1_IDENTITY + '",[]]'
    return (
        (("core_livingorganism", V1_IDENTITY, []), v1),
        (("core_livingorganism", V1_IDENTITY, None), v1),
        (("t", "AAAAAAAA-0000-4000-8000-000000000001", ['{"k":"\u00e9"}', "{}"]),
         '["' + tag + '","t","aaaaaaaa-0000-4000-8000-000000000001",[{"k":"\u00e9"},{}]]'),
    )


def expected_digest(tag, preimage):
    return tag + ":" + hashlib.sha256(preimage.encode("utf-8")).hexdigest()


def shape_probes(tag):
    """(value, must_be_accepted) for a reference column carrying the required shape check."""
    other = S015F3 if tag == S015F4 else S015F4
    hexes = "0123456789abcdef" * 4
    return (
        (tag + ":" + hexes, True),
        (other + ":" + hexes, False),
        (tag + ":" + hexes.upper(), False),
        (tag + ":" + hexes[:63], False),
        (tag + ":" + hexes[:63] + "\n", False),
        (tag + ":g" + hexes[:63], False),
    )


def _normalise(text):
    return None if text is None else text.replace("\r\n", "\n")


# ---------------------------------------------------------------------------------------------------------------
# Pure judgement: observations in, failures out. No database access below this line until OBSERVATION.
# ---------------------------------------------------------------------------------------------------------------
def judge_functions(tag, observed):
    failures = []
    required = sorted(spec["qualified"] for spec in FUNCTION_SPECS)
    same_name = sorted(observed["same_name"])
    if same_name != required:
        failures.append(f"functions with these names, in any schema, must be exactly {required}; found {same_name}")
    for spec in FUNCTION_SPECS:
        row = observed["functions"].get(spec["signature"])
        if row is None:
            failures.append(f"{spec['signature']} missing")
            continue
        wanted = dict(FUNCTION_FIXED_ATTRIBUTES, rettype=spec["rettype"], argtypes=spec["argtypes"], argnames=spec["argnames"])
        for key, value in wanted.items():
            if row.get(key) != value:
                failures.append(f"{spec['signature']} {key} is {row.get(key)!r}, required {value!r}")
        if _normalise(row.get("prosrc")) != spec["body"](tag):
            failures.append(f"{spec['signature']} body is not the exact {tag} body")
    for (args, preimage), got in zip(function_probes(tag), observed["envelope_probes"]):
        if got != preimage:
            failures.append(f"s015_canonical_envelope{args!r} returned {got!r}, required {preimage!r}")
    for (_, preimage), got in zip(function_probes(tag), observed["digest_probes"]):
        if got != expected_digest(tag, preimage):
            failures.append(f"s015_digest_envelope({preimage!r}) returned {got!r}, required {expected_digest(tag, preimage)!r}")
    if observed["v1_digest"] != V1_DIGEST[tag]:
        failures.append(f"s015_digest_envelope(V1) returned {observed['v1_digest']!r}, required the published {V1_DIGEST[tag]!r}")
    return failures


def judge_check(tag, spec, observed):
    label = f"{spec['table']}.{spec['name']}"
    row = observed.get("constraint")
    if row is None:
        return [f"{label} missing"]
    failures = []
    wanted = {"contype": "c", "convalidated": True, "conislocal": True, "coninhcount": 0, "connoinherit": False,
              "columns": [spec["column"]], "column_type": "character varying(71)"}
    for key, value in wanted.items():
        if row.get(key) != value:
            failures.append(f"{label} {key} is {row.get(key)!r}, required {value!r}")
    reference = observed.get("reference") or {}
    if reference.get("error"):
        failures.append(f"{label} reference could not be built: {reference['error']}")
    else:
        if row.get("definition") != reference.get("definition"):
            failures.append(f"{label} definition {row.get('definition')!r} differs from the required {reference.get('definition')!r}")
        for (value, accept), outcome in zip(shape_probes(tag), reference.get("probes", ())):
            if outcome != ("accepted" if accept else "check_violation"):
                failures.append(f"{label} reference {'accept' if accept else 'refusal'} probe {value!r} gave {outcome!r}")
    return failures


def judge_form_literal_checks(observed_pairs):
    required = sorted((s["table"], s["name"]) for s in SHAPE_CHECKS)
    found = sorted(observed_pairs)
    return [] if found == required else [f"CHECKs mentioning an s015f form literal must be exactly {required}; found {found}"]


# ---------------------------------------------------------------------------------------------------------------
# OBSERVATION
# ---------------------------------------------------------------------------------------------------------------
FUNCTION_QUERY = """
SELECT n.nspname, p.prokind, l.lanname, format_type(p.prorettype, NULL), oidvectortypes(p.proargtypes),
       p.proargnames, p.provolatile, p.proisstrict, p.prosecdef, p.proleakproof, p.proparallel, p.proretset,
       p.proconfig, p.proacl IS NULL, p.prosqlbody IS NULL, p.prosrc
  FROM pg_catalog.pg_proc p
  JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
  JOIN pg_catalog.pg_language l ON l.oid = p.prolang
 WHERE p.oid = to_regprocedure(%s)
"""
FUNCTION_KEYS = ("nspname", "prokind", "lanname", "rettype", "argtypes", "argnames", "provolatile", "proisstrict",
                 "prosecdef", "proleakproof", "proparallel", "proretset", "proconfig", "acl_default", "sqlbody_absent", "prosrc")

CONSTRAINT_QUERY = """
SELECT con.contype, con.convalidated, con.conislocal, con.coninhcount, con.connoinherit,
       ARRAY(SELECT a.attname::text FROM unnest(con.conkey) k JOIN pg_catalog.pg_attribute a
               ON a.attrelid = con.conrelid AND a.attnum = k ORDER BY a.attname),
       pg_catalog.pg_get_constraintdef(con.oid),
       (SELECT format_type(a.atttypid, a.atttypmod) FROM pg_catalog.pg_attribute a
         WHERE a.attrelid = con.conrelid AND a.attname = %s AND NOT a.attisdropped)
  FROM pg_catalog.pg_constraint con
 WHERE con.conrelid = to_regclass(%s) AND con.conname = %s
"""
CONSTRAINT_KEYS = ("contype", "convalidated", "conislocal", "coninhcount", "connoinherit", "columns", "definition", "column_type")

FORM_LITERAL_CHECKS_QUERY = """
SELECT c.relname::text, con.conname::text
  FROM pg_catalog.pg_constraint con
  JOIN pg_catalog.pg_class c ON c.oid = con.conrelid
  JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public' AND con.contype = 'c' AND pg_catalog.pg_get_constraintdef(con.oid) ~ 's015f[0-9]+:'
"""

REFERENCE_TABLE = "s015_0023_reference"


def _reference_clause(apps, schema_editor, tag, spec):
    if spec["route"] == "raw":  # the exact text 0021's installer executes, with the pattern for `tag`
        return f"CONSTRAINT {spec['name']} CHECK ({spec['column']} ~ '{shape_pattern(tag)}')"
    model = apps.get_model("core", spec["model"])  # the SQL Django's AddConstraint executes for this condition
    constraint = models.CheckConstraint(condition=Q(**{f"{spec['column']}__regex": shape_pattern(tag)}), name=spec["name"])
    return str(constraint.constraint_sql(model, schema_editor))


def _observe_reference(apps, schema_editor, cursor, tag, spec):
    connection = schema_editor.connection
    try:
        with transaction.atomic(using=connection.alias):  # a failure here cannot abort the migration transaction unseen
            cursor.execute(f"DROP TABLE IF EXISTS pg_temp.{REFERENCE_TABLE}")
            cursor.execute(f"CREATE TEMPORARY TABLE {REFERENCE_TABLE} ({spec['column']} varchar(71)) ON COMMIT DROP")
            cursor.execute(f"ALTER TABLE pg_temp.{REFERENCE_TABLE} ADD " + _reference_clause(apps, schema_editor, tag, spec))
            cursor.execute("SELECT pg_catalog.pg_get_constraintdef(oid) FROM pg_catalog.pg_constraint "
                           "WHERE conrelid = %s::regclass AND conname = %s", [f"pg_temp.{REFERENCE_TABLE}", spec["name"]])
            definition = cursor.fetchone()[0]
            probes = []
            for value, _ in shape_probes(tag):
                try:
                    with transaction.atomic(using=connection.alias):
                        cursor.execute(f"INSERT INTO pg_temp.{REFERENCE_TABLE} ({spec['column']}) VALUES (%s)", [value])
                    probes.append("accepted")
                except DatabaseError as exc:
                    state = getattr(exc.__cause__, "sqlstate", None)
                    probes.append("check_violation" if state == "23514" else f"refused:{state}")
            cursor.execute(f"DROP TABLE pg_temp.{REFERENCE_TABLE}")
            return {"definition": definition, "probes": probes}
    except Exception as exc:  # reported as a failure, never as a pass
        return {"error": f"{type(exc).__name__}: {exc}"}


def observe(apps, schema_editor, cursor, tag):
    functions = {}
    for spec in FUNCTION_SPECS:
        cursor.execute(FUNCTION_QUERY, [spec["signature"]])
        row = cursor.fetchone()
        functions[spec["signature"]] = None if row is None else dict(zip(FUNCTION_KEYS, row))
    cursor.execute("SELECT n.nspname || '.' || p.proname || '(' || oidvectortypes(p.proargtypes) || ')' "
                   "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace "
                   "WHERE p.proname IN (%s, %s)", [spec["name"] for spec in FUNCTION_SPECS])
    same_name = [r[0] for r in cursor.fetchall()]
    envelope_probes, digest_probes, v1_digest = [], [], None
    if all(functions.values()):
        try:
            with transaction.atomic(using=schema_editor.connection.alias):  # a probe error is a failure, never an abort
                for (token, identity, records), _ in function_probes(tag):
                    cursor.execute("SELECT public.s015_canonical_envelope(%s::text, %s::uuid, %s::text[])", [token, identity, records])
                    envelope_probes.append(cursor.fetchone()[0])
                for _, preimage in function_probes(tag):
                    cursor.execute("SELECT public.s015_digest_envelope(%s::text)", [preimage])
                    digest_probes.append(cursor.fetchone()[0])
                cursor.execute("SELECT public.s015_digest_envelope(%s::text)", [function_probes(tag)[0][1]])
                v1_digest = cursor.fetchone()[0]
        except DatabaseError as exc:
            envelope_probes = digest_probes = [f"probe error: {type(exc).__name__}: {exc}"] * len(function_probes(tag))
            v1_digest = f"probe error: {type(exc).__name__}"
    checks = []
    for spec in SHAPE_CHECKS:
        cursor.execute(CONSTRAINT_QUERY, [spec["column"], f"public.{spec['table']}", spec["name"]])
        row = cursor.fetchone()
        checks.append((spec, {"constraint": None if row is None else dict(zip(CONSTRAINT_KEYS, row)),
                              "reference": _observe_reference(apps, schema_editor, cursor, tag, spec)}))
    cursor.execute(FORM_LITERAL_CHECKS_QUERY)
    literal_pairs = cursor.fetchall()
    return {"functions": functions, "same_name": same_name, "envelope_probes": envelope_probes,
            "digest_probes": digest_probes, "v1_digest": v1_digest}, checks, literal_pairs


def verify_state(apps, schema_editor, cursor, tag, when):
    """Refuse unless the installed state is exactly the `tag` state. Returns nothing on success."""
    if tag not in FORMS:
        raise RuntimeError(f"S015 0023: unknown form {tag!r}")
    function_obs, checks, literal_pairs = observe(apps, schema_editor, cursor, tag)
    failures = judge_functions(tag, function_obs)
    for spec, obs in checks:
        failures.extend(judge_check(tag, spec, obs))
    failures.extend(judge_form_literal_checks(literal_pairs))
    if failures:
        raise RuntimeError(f"S015 0023 {when}: installed state is not exactly the {tag} state:\n  - " + "\n  - ".join(failures))


def lock_and_refuse_if_rows(schema_editor, cursor, direction):
    connection = schema_editor.connection
    if not connection.in_atomic_block:
        raise RuntimeError(f"S015 0023 {direction} refused: must run inside the atomic migration transaction")
    cursor.execute("SELECT t FROM unnest(%s::text[]) t WHERE to_regclass('public.' || t) IS NULL ORDER BY t", [list(ROW_TABLES)])
    missing = [r[0] for r in cursor.fetchall()]
    if missing:
        raise RuntimeError(f"S015 0023 {direction} refused: expected tables missing: {', '.join(missing)}")
    for table in ROW_TABLES:
        mode = "ACCESS EXCLUSIVE" if table in ALTERED_TABLES else "SHARE ROW EXCLUSIVE"
        cursor.execute(f"LOCK TABLE public.{table} IN {mode} MODE")
    populated = []
    for table in ROW_TABLES:
        cursor.execute(f"SELECT count(*) FROM public.{table}")
        count = cursor.fetchone()[0]
        if count:
            populated.append(f"{table}={count}")
    if populated:
        raise RuntimeError(
            f"S015 0023 {direction} refused: rows exist in {', '.join(populated)}. Their fingerprints are labelled "
            "for the other form over the 0022 column set and no ruling defines their conversion; this migration does not rewrite them."
        )


# ---------------------------------------------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------------------------------------------
def _envelope_sql(tag):
    return "\n".join(f"CREATE OR REPLACE FUNCTION {spec['header']} AS $${spec['body'](tag)}$$;" for spec in FUNCTION_SPECS)


def _replace(schema_editor, tag):
    table, name, column = RAW_SHAPE_CHECK
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"ALTER TABLE public.{table} DROP CONSTRAINT {name}")
        cursor.execute(f"ALTER TABLE public.{table} ADD CONSTRAINT {name} CHECK ({column} ~ '{shape_pattern(tag)}')")
        cursor.execute(_envelope_sql(tag))


def _require_postgresql(schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        raise RuntimeError("S015 0023 requires PostgreSQL")


def pre_forward(apps, schema_editor):
    _require_postgresql(schema_editor)
    with schema_editor.connection.cursor() as cursor:
        lock_and_refuse_if_rows(schema_editor, cursor, "forward")
        verify_state(apps, schema_editor, cursor, S015F3, "forward source-state check")


def post_reverse(apps, schema_editor):
    _require_postgresql(schema_editor)
    with schema_editor.connection.cursor() as cursor:
        verify_state(apps, schema_editor, cursor, S015F3, "reverse target-state check")


def replace_to_s015f4(apps, schema_editor):
    _require_postgresql(schema_editor)
    _replace(schema_editor, S015F4)


def replace_to_s015f3(apps, schema_editor):
    _require_postgresql(schema_editor)
    _replace(schema_editor, S015F3)


def post_forward(apps, schema_editor):
    _require_postgresql(schema_editor)
    with schema_editor.connection.cursor() as cursor:
        verify_state(apps, schema_editor, cursor, S015F4, "forward target-state check")


def pre_reverse(apps, schema_editor):
    _require_postgresql(schema_editor)
    with schema_editor.connection.cursor() as cursor:
        lock_and_refuse_if_rows(schema_editor, cursor, "reverse")
        verify_state(apps, schema_editor, cursor, S015F4, "reverse source-state check")


def _constraint_ops():
    ops = []
    for model_name, _, column in CACHE_ANCHORS:
        name = f"s015_0021_{model_name}_fp_shape_ck"
        ops.append(migrations.RemoveConstraint(model_name=model_name, name=name))
        ops.append(migrations.AddConstraint(
            model_name=model_name,
            constraint=models.CheckConstraint(condition=Q(**{f"{column}__regex": shape_pattern(S015F4)}), name=name),
        ))
    return ops


class Migration(migrations.Migration):
    atomic = True  # the locks, the refusal and every verification depend on one transaction

    dependencies = [
        ("core", "0022_s015_governed_chain_contract"),
    ]

    operations = [
        # forward: lock, refuse on rows, verify exact s015f3 -> functions and raw check -> four model checks -> verify exact s015f4
        # reverse: lock, refuse on rows, verify exact s015f4 -> four model checks -> functions and raw check -> verify exact s015f3
        migrations.RunPython(pre_forward, post_reverse),
        migrations.RunPython(replace_to_s015f4, replace_to_s015f3),
        *_constraint_ops(),
        migrations.RunPython(post_forward, pre_reverse),
    ]
