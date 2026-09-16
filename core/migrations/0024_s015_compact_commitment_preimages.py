"""S015 F-L1 repair: the L1 (s015r1) and parts (s015p1) preimages are built as compact canonical text.

Requirement:
- designated design LD_RETURN_PKT_A_3_MIGRATION_0022_DESIGN_v0_6.md (sha256 59a07477...), section 5.4: the L1 domain
  is the canonical record of the whole event row, the s015_canonical_event_record grammar, keys in bytewise order,
  with l1_commitment present and null; preimage ["s015r1", <table>, <record>]; digest s015r1: + SHA-256 hex;
- the same design, section 6 binding contract row 2: parts_commitment = s015p1: + SHA-256 over
  ["s015p1", table, event_uuid, [(ordinal, class, posture, ground, office) ... in ordinal order]];
- Human rulings L1-a and L1-d, 16 Sep 2026: compact, no formatting whitespace; meaningful spaces inside text values
  preserved;
- the byte contract: vectors/S015_COMMITMENT_FORMS_s015r1_s015p1_RULE_v0_1.md.

Finding it repairs (F-L1, confirmed by execution in run CHA_20260916T163916Z): 0022 casts the canonical text to jsonb
and back, which orders object keys by length and writes ", " and ": ".

Changes only the bodies of public.s015_0022_l1_preimage(regclass, jsonb) and
public.s015_0022_parts_preimage(text, uuid). Name, signature, return type, language, volatility, security and
grants are unchanged, so every trigger and guardian that calls them is unchanged. 0022 and 0023 are unchanged.

Existing data: the recorded position is that no operational data exists. This migration does not rely on it. In
both directions it locks the twelve event tables and core_governedeventpart (SHARE ROW EXCLUSIVE, fixed order, held
to commit), counts their rows, and refuses if any row exists: such rows carry commitments computed by the other
preimage, and no conversion is authorised. core_governedeventcontent is not locked: no commitment it holds or binds
is computed by either function.

Source and target state are verified exactly (signature, attributes, body, same-name census, behaviour on probes
whose expected bytes are derived here from the rule) before and after the change, in both directions.
"""
import hashlib
import json

from django.db import migrations, transaction
from django.db.utils import DatabaseError

JSONB = "jsonb_round_trip_0022"
COMPACT = "compact_0024"
STATES = (JSONB, COMPACT)

EVENT_TABLES = (
    "core_livingorganismevent", "core_circlestateevent", "core_organismmembershiptransition",
    "core_contextualroleassignmentstateevent", "core_membershipconditionstateevent", "core_authorityinvalidationevent",
    "core_determinationcontestevent", "core_coverageassessment", "core_restrictedcontinuityevent",
    "core_visibilitygrantstateevent", "core_obligationstateevent", "core_planningclassificationevent",
)
ROW_TABLES = tuple(sorted(EVENT_TABLES + ("core_governedeventpart",)))

# ---------------------------------------------------------------------------------------------------------------
# Exact bodies. JSONB: the text 0022 installs (checked against 0022's own constants in preparation). COMPACT: the repair.
# ---------------------------------------------------------------------------------------------------------------
L1_BODY = {
    JSONB: (
        "\n\nBEGIN\n    RETURN jsonb_build_array(\n        's015r1',\n        p_table::text,\n"
        "        public.s015_canonical_event_record(\n            p_table,\n"
        "            jsonb_set(p_row, '{l1_commitment}', 'null'::jsonb, true)\n        )::jsonb\n    )::text;\nEND;\n"
    ),
    COMPACT: (
        "\nDECLARE\n    v_table text;\nBEGIN\n"
        "    SELECT c.relname::text INTO v_table\n"
        "      FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace\n"
        "     WHERE c.oid = p_table AND n.nspname = 'public';\n"
        "    IF v_table IS NULL OR p_row IS NULL\n"
        "       OR NOT EXISTS (SELECT 1 FROM public.s015_anchor_map() m WHERE m.event_table = v_table) THEN\n"
        "        RAISE EXCEPTION 'S015 s015r1 preimage refused: % is not an S015 event table, or the row is null', p_table\n"
        "            USING ERRCODE = 'integrity_constraint_violation';\n"
        "    END IF;\n"
        "    RETURN '[\"s015r1\",' || public.s015_canonical_json_string(v_table) || ','\n"
        "        || public.s015_canonical_event_record(p_table, jsonb_set(p_row, '{l1_commitment}', 'null'::jsonb, true))\n"
        "        || ']';\nEND;\n"
    ),
}
PARTS_BODY = {
    JSONB: (
        "\n\nBEGIN\n    RETURN jsonb_build_array(\n        's015p1',\n        p_event_table,\n        p_event_uuid::text,\n"
        "        COALESCE(\n            (\n                SELECT jsonb_agg(\n                    jsonb_build_array(\n"
        "                        part_ordinal,\n                        part_class,\n                        posture,\n"
        "                        ground,\n                        office\n                    )\n"
        "                    ORDER BY part_ordinal\n                )\n"
        "                FROM public.core_governedeventpart part\n"
        "                WHERE part.event_table = p_event_table\n                  AND part.event_uuid = p_event_uuid\n"
        "            ),\n            '[]'::jsonb\n        )\n    )::text;\nEND;\n"
    ),
    COMPACT: (
        "\nBEGIN\n"
        "    IF p_event_table IS NULL OR p_event_uuid IS NULL\n"
        "       OR NOT EXISTS (SELECT 1 FROM public.s015_anchor_map() m WHERE m.event_table = p_event_table) THEN\n"
        "        RAISE EXCEPTION 'S015 s015p1 preimage refused: % is not an S015 event table, or the event uuid is null', p_event_table\n"
        "            USING ERRCODE = 'integrity_constraint_violation';\n"
        "    END IF;\n"
        "    RETURN '[\"s015p1\",' || public.s015_canonical_json_string(p_event_table) || ',\"' || p_event_uuid::text || '\",['\n"
        "        || coalesce((\n"
        "            SELECT string_agg(\n"
        "                '[' || part.part_ordinal::bigint::text\n"
        "                || ',' || public.s015_canonical_json_string(part.part_class)\n"
        "                || ',' || public.s015_canonical_json_string(part.posture)\n"
        "                || ',' || public.s015_canonical_json_string(part.ground)\n"
        "                || ',' || public.s015_canonical_json_string(part.office) || ']',\n"
        "                ',' ORDER BY part.part_ordinal)\n"
        "              FROM public.core_governedeventpart part\n"
        "             WHERE part.event_table = p_event_table AND part.event_uuid = p_event_uuid\n"
        "        ), '')\n"
        "        || ']]';\nEND;\n"
    ),
}

FUNCTION_SPECS = (
    {"signature": "public.s015_0022_l1_preimage(regclass,jsonb)", "name": "s015_0022_l1_preimage",
     "qualified": "public.s015_0022_l1_preimage(regclass, jsonb)",
     "header": "public.s015_0022_l1_preimage(p_table regclass, p_row jsonb)\nRETURNS text LANGUAGE plpgsql STABLE",
     "bodies": L1_BODY, "argtypes": "regclass, jsonb", "argnames": ["p_table", "p_row"]},
    {"signature": "public.s015_0022_parts_preimage(text,uuid)", "name": "s015_0022_parts_preimage",
     "qualified": "public.s015_0022_parts_preimage(text, uuid)",
     "header": "public.s015_0022_parts_preimage(p_event_table text, p_event_uuid uuid)\nRETURNS text LANGUAGE plpgsql STABLE",
     "bodies": PARTS_BODY, "argtypes": "text, uuid", "argnames": ["p_event_table", "p_event_uuid"]},
)
FIXED_ATTRIBUTES = {"nspname": "public", "prokind": "f", "lanname": "plpgsql", "rettype": "text", "provolatile": "s",
                    "proisstrict": False, "prosecdef": False, "proleakproof": False, "proparallel": "u",
                    "proretset": False, "proconfig": None, "acl_default": True, "sqlbody_absent": True}

# ---------------------------------------------------------------------------------------------------------------
# Behavioural probes, expected bytes derived here from the rule (no rows read, none written).
# ---------------------------------------------------------------------------------------------------------------
PROBE_TABLE = "core_organismmembershiptransition"
PROBE_UUID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


def _json_string(v):
    return json.dumps(v, ensure_ascii=False)


def expected_parts_empty(state):
    if state == COMPACT:
        return '["s015p1",' + _json_string(PROBE_TABLE) + ',"' + PROBE_UUID + '",[]]'
    return '["s015p1", ' + _json_string(PROBE_TABLE) + ', "' + PROBE_UUID + '", []]'


def l1_probe_row(columns):
    """A row over the installed column list: every column null except two text columns carrying a space, a quote,
    a backslash and a non-ASCII character. Serialization input only."""
    row = {c: None for c in columns}
    row["evidence_reference"] = 'minute 12 "as read" \\ caf\u00e9'
    row["action"] = "JOIN"
    return row


def expected_l1(state, columns, row):
    r = dict(row); r["l1_commitment"] = None
    if state == COMPACT:
        rec = "{" + ",".join(_json_string(c) + ":" + (_json_string(r[c]) if r[c] is not None else "null") for c in columns) + "}"
        return '["s015r1",' + _json_string(PROBE_TABLE) + "," + rec + "]"
    order = sorted(columns, key=lambda c: (len(c.encode("utf-8")), c.encode("utf-8")))
    rec = "{" + ", ".join(_json_string(c) + ": " + (_json_string(r[c]) if r[c] is not None else "null") for c in order) + "}"
    return '["s015r1", ' + _json_string(PROBE_TABLE) + ", " + rec + "]"


def _normalise(text):
    return None if text is None else text.replace("\r\n", "\n")


def judge(state, observed):
    failures = []
    required = sorted(spec["qualified"] for spec in FUNCTION_SPECS)
    if sorted(observed["same_name"]) != required:
        failures.append(f"functions with these names, in any schema, must be exactly {required}; found {sorted(observed['same_name'])}")
    for spec in FUNCTION_SPECS:
        row = observed["functions"].get(spec["signature"])
        if row is None:
            failures.append(f"{spec['signature']} missing")
            continue
        for key, value in dict(FIXED_ATTRIBUTES, argtypes=spec["argtypes"], argnames=spec["argnames"]).items():
            if row.get(key) != value:
                failures.append(f"{spec['signature']} {key} is {row.get(key)!r}, required {value!r}")
        if _normalise(row.get("prosrc")) != spec["bodies"][state]:
            failures.append(f"{spec['signature']} body is not the exact {state} body")
    probes = observed.get("probes", {})
    if probes.get("parts_empty") != expected_parts_empty(state):
        failures.append(f"s015_0022_parts_preimage probe returned {probes.get('parts_empty')!r}, required {expected_parts_empty(state)!r}")
    if probes.get("l1") != probes.get("l1_expected"):
        failures.append(f"s015_0022_l1_preimage probe returned {str(probes.get('l1'))[:120]!r}..., required the {state} bytes")
    if probes.get("l1_expected") is None:
        failures.append("s015_0022_l1_preimage probe could not be prepared")
    return failures


FUNCTION_QUERY = """
SELECT n.nspname, p.prokind, l.lanname, format_type(p.prorettype, NULL), oidvectortypes(p.proargtypes),
       p.proargnames, p.provolatile, p.proisstrict, p.prosecdef, p.proleakproof, p.proparallel, p.proretset,
       p.proconfig, p.proacl IS NULL, p.prosqlbody IS NULL, p.prosrc
  FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
  JOIN pg_catalog.pg_language l ON l.oid = p.prolang
 WHERE p.oid = to_regprocedure(%s)
"""
FUNCTION_KEYS = ("nspname", "prokind", "lanname", "rettype", "argtypes", "argnames", "provolatile", "proisstrict",
                 "prosecdef", "proleakproof", "proparallel", "proretset", "proconfig", "acl_default", "sqlbody_absent", "prosrc")


def observe(schema_editor, cursor, state):
    functions = {}
    for spec in FUNCTION_SPECS:
        cursor.execute(FUNCTION_QUERY, [spec["signature"]])
        row = cursor.fetchone()
        functions[spec["signature"]] = None if row is None else dict(zip(FUNCTION_KEYS, row))
    cursor.execute("SELECT n.nspname || '.' || p.proname || '(' || oidvectortypes(p.proargtypes) || ')' "
                   "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace "
                   "WHERE p.proname IN (%s, %s)", [spec["name"] for spec in FUNCTION_SPECS])
    same_name = [r[0] for r in cursor.fetchall()]
    probes = {}
    if all(functions.values()):
        try:
            with transaction.atomic(using=schema_editor.connection.alias):
                cursor.execute("SELECT public.s015_0022_parts_preimage(%s::text, %s::uuid)", [PROBE_TABLE, PROBE_UUID])
                probes["parts_empty"] = cursor.fetchone()[0]
                cursor.execute("SELECT public.s015_canonical_columns(%s::regclass)", [f"public.{PROBE_TABLE}"])
                columns = list(cursor.fetchone()[0])
                row = l1_probe_row(columns)
                probes["l1_expected"] = expected_l1(state, columns, row)
                cursor.execute("SELECT public.s015_0022_l1_preimage(%s::regclass, %s::jsonb)", [f"public.{PROBE_TABLE}", json.dumps(row)])
                probes["l1"] = cursor.fetchone()[0]
        except DatabaseError as exc:
            probes["error"] = f"{type(exc).__name__}: {exc}"
    return {"functions": functions, "same_name": same_name, "probes": probes}


def verify_state(schema_editor, cursor, state, when):
    if state not in STATES:
        raise RuntimeError(f"S015 0024: unknown state {state!r}")
    observed = observe(schema_editor, cursor, state)
    failures = judge(state, observed)
    if observed["probes"].get("error"):
        failures.append("probe error: " + observed["probes"]["error"])
    if failures:
        raise RuntimeError(f"S015 0024 {when}: installed state is not exactly the {state} state:\n  - " + "\n  - ".join(failures))


def lock_and_refuse_if_rows(schema_editor, cursor, direction):
    if not schema_editor.connection.in_atomic_block:
        raise RuntimeError(f"S015 0024 {direction} refused: must run inside the atomic migration transaction")
    cursor.execute("SELECT t FROM unnest(%s::text[]) t WHERE to_regclass('public.' || t) IS NULL ORDER BY t", [list(ROW_TABLES)])
    missing = [r[0] for r in cursor.fetchall()]
    if missing:
        raise RuntimeError(f"S015 0024 {direction} refused: expected tables missing: {', '.join(missing)}")
    for table in ROW_TABLES:
        cursor.execute(f"LOCK TABLE public.{table} IN SHARE ROW EXCLUSIVE MODE")
    populated = []
    for table in ROW_TABLES:
        cursor.execute(f"SELECT count(*) FROM public.{table}")
        count = cursor.fetchone()[0]
        if count:
            populated.append(f"{table}={count}")
    if populated:
        raise RuntimeError(
            f"S015 0024 {direction} refused: rows exist in {', '.join(populated)}. Their commitments were computed by the "
            "other preimage and no conversion is authorised; this migration does not rewrite them."
        )


def _install(schema_editor, state):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("\n".join(f"CREATE OR REPLACE FUNCTION {spec['header']} AS $${spec['bodies'][state]}$$;" for spec in FUNCTION_SPECS))


def _require_postgresql(schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        raise RuntimeError("S015 0024 requires PostgreSQL")


def pre_forward(apps, schema_editor):
    _require_postgresql(schema_editor)
    with schema_editor.connection.cursor() as cursor:
        lock_and_refuse_if_rows(schema_editor, cursor, "forward")
        verify_state(schema_editor, cursor, JSONB, "forward source-state check")


def post_reverse(apps, schema_editor):
    _require_postgresql(schema_editor)
    with schema_editor.connection.cursor() as cursor:
        verify_state(schema_editor, cursor, JSONB, "reverse target-state check")


def install_compact(apps, schema_editor):
    _require_postgresql(schema_editor)
    _install(schema_editor, COMPACT)


def install_jsonb(apps, schema_editor):
    _require_postgresql(schema_editor)
    _install(schema_editor, JSONB)


def post_forward(apps, schema_editor):
    _require_postgresql(schema_editor)
    with schema_editor.connection.cursor() as cursor:
        verify_state(schema_editor, cursor, COMPACT, "forward target-state check")


def pre_reverse(apps, schema_editor):
    _require_postgresql(schema_editor)
    with schema_editor.connection.cursor() as cursor:
        lock_and_refuse_if_rows(schema_editor, cursor, "reverse")
        verify_state(schema_editor, cursor, COMPACT, "reverse source-state check")


class Migration(migrations.Migration):
    atomic = True  # the locks, the refusal and every verification depend on one transaction

    dependencies = [
        ("core", "0023_s015_canonical_form_s015f4"),
    ]

    operations = [
        migrations.RunPython(pre_forward, post_reverse),
        migrations.RunPython(install_compact, install_jsonb),
        migrations.RunPython(post_forward, pre_reverse),
    ]
