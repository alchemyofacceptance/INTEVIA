"""Shared conformance harness for the S015 qualification decision — CONSUMER_REQUIREMENT_v0_7.md.

    Author  : Vision Chamber (Claude Opus 5), UFUND-5, 18 September 2026
    Status  : CANDIDATE. Member 5 of the coordinated correction of v0_7 section 6.1.
              **Depends on the contract's approval and is not adopted until the contract is.**

    python gate_conformance_v0_8.py BASE_RUN_ROOT [--only ID,ID] [--impl ID,ID]

BASE_RUN_ROOT is a retained run root. The harness copies it per case, mutates ONE thing, RE-SEALS the
summary so that only the binding under test can carry a refusal, and requires the stated disposition
and the stated refusal texts of every implementation.

WHAT THIS HARNESS IS FOR, AND WHAT IT CANNOT DO

  It exercises the THREE ACTUAL IMPLEMENTATIONS of section 6.1 over one evidence set each time, and
  compares their decisions AND their required refusal texts against the contract, and against each
  other. A divergence between implementations is a finding to be reported, never reconciled here.

  It does not establish that the implementation under test is the one in use. That is integration's
  business, not conformance's.

TWO KINDS OF EVIDENCE, NEVER CONFLATED (section EVIDENCE PROVENANCE below)

  SYNTHETIC   a run root whose five records were constructed, or whose real records were repointed
              at a container path. Exercises the code paths. Establishes nothing about a real run.
  RETAINED    a run root written by a real qualifying run and not modified except by the one case
              mutation. This is the only kind that may be cited in a qualification report.

  The harness prints which kind it was given, derives it rather than taking it on trust, and stamps
  every result line with it.

CASE COVERAGE — sets 1-10 are CONSUMER_REQUIREMENT_v0_5's, sets 11-17 and P/R are v0.7's

  Seven of v0.5's ten sets require a REAL RUN to construct and cannot be made by mutating records:
  a second genuine launch (8), and a committed module importing a W-only helper (9, 10). Where a set
  is executed elsewhere, its row says exactly where. A set marked EXECUTED-ELSEWHERE is NOT counted
  as passing here and never contributes to a conformance claim made from this harness alone.
"""
import copy
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

SUMMARY = os.path.join("evidence", "summary.json")
HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------------------------
# HISTORICAL NEGATIVE COMPARISONS — retained with exact identities, per the Human Governor's
# direction of 18 September 2026. These are records of runs already made, not live cases.
# ---------------------------------------------------------------------------------------------
HISTORICAL = [
    {"what": "s015_gate.py as landed at 7622cce (and unchanged at cc3f2e89, b18b367)",
     "sha256": "70d4d74efe61b30f1be01233aa5fbbaff429b5ff2cbc5cabb55e1dfb8eb871f7",
     "result": "5 of 19 cases as required, against a SYNTHETIC fixture, 18 September 2026",
     "why": "it implements no control-completeness, tree, exit-validation or registry-binding check; "
            "the fourteen it fails are exactly the checks that did not exist"},
    {"what": "s015_gate_v0_7.py, first candidate, before the advisory check",
     "sha256": "24d36a18940b9e9e95699a71e8a7f16245eb9002c0c91c9335973ae763cbaafc",
     "result": "qualified P1-P5 wrongly and raised TypeError on P6, against a SYNTHETIC fixture",
     "why": "exit checked as != 0 rather than exactly 1; registry located but its bytes unverified; "
            "gate() not total"},
]

# ---------------------------------------------------------------------------------------------
# THE THREE IMPLEMENTATIONS. Each is invoked as it actually is, never through a reimplementation.
# ---------------------------------------------------------------------------------------------
IMPLEMENTATIONS = [
    {"id": "gate-cli", "label": "s015_gate.py, command line",
     "kind": "python-cli", "path": os.path.join(HERE, "s015_gate.py")},
    {"id": "gate-direct", "label": "s015_gate.py, gate() called directly",
     "kind": "python-direct", "path": os.path.join(HERE, "s015_gate.py")},
    {"id": "pwsh", "label": "Copilot CI task PowerShell consumer",
     "kind": "powershell", "path": os.path.join(HERE, "S015_Qualification_Decision_v0_7c.ps1")},
    {"id": "bootstrap", "label": "bootstrap.gate, --decide entry",
     "kind": "bootstrap", "path": os.path.join(HERE, "bootstrap.py")},
]

# ---------------------------------------------------------------------------------------------
# REQUIRED SHARED REFUSAL TEXTS. Full texts from the contract, not substrings. <> marks a value the
# consumer interpolates; everything outside <> must match exactly, and the whole line must match.
# ---------------------------------------------------------------------------------------------
def _matches(template, actual):
    pattern = "".join(".+?" if part.startswith("<") else re.escape(part)
                      for part in re.split(r"(<[^>]*>)", template))
    return re.fullmatch(pattern, actual) is not None


def _check_refusals(required, actual):
    missing = [t for t in required if not any(_matches(t, a) for a in actual)]
    return missing


# ------------------------------------------------------------------ mutations --
def _read(root, rel):
    with open(os.path.join(root, rel), "rb") as fh:
        return fh.read()


def _write_json(path, obj):
    data = (json.dumps(obj, indent=1) + "\n").encode("utf-8")
    with open(path, "wb") as fh:
        fh.write(data)
    return data


def _reseal_summary(root, summary):
    """Satisfy E10 so that ONLY the binding under test can carry a refusal."""
    data = _write_json(os.path.join(root, SUMMARY), summary)
    surface = json.loads(_read(root, "SURFACE_RESULT.json").decode("utf-8"))
    surface["summary_sha256"] = hashlib.sha256(data).hexdigest()
    _write_json(os.path.join(root, "SURFACE_RESULT.json"), surface)


def _edit_summary(fn):
    def apply(root):
        summary = json.loads(_read(root, SUMMARY).decode("utf-8"))
        fn(summary)
        _reseal_summary(root, summary)
    return apply


def _edit_record(name, fn, sub=""):
    def apply(root):
        path = os.path.join(root, sub, name) if sub else os.path.join(root, name)
        obj = json.loads(_read(root, os.path.join(sub, name) if sub else name).decode("utf-8"))
        fn(obj)
        _write_json(path, obj)
    return apply


def _steps(fn):
    return _edit_summary(lambda s: s.__setitem__("steps", fn(copy.deepcopy(s["steps"]))))


def _drop_record(name, sub=""):
    return lambda r: os.remove(os.path.join(r, sub, name) if sub else os.path.join(r, name))


def _corrupt_record(name, sub=""):
    def apply(r):
        path = os.path.join(r, sub, name) if sub else os.path.join(r, name)
        with open(path, "wb") as fh:
            fh.write(b"{ not json")
    return apply


def _mutation_field(field, value):
    _ABSENT = object()
    def fn(steps):
        for step in steps:
            if str(step["id"]).startswith("MUT-"):
                if value is _ABSENT:
                    step["tests"].pop(field, None)
                else:
                    step["tests"][field] = value
                break
        return steps
    return _steps(fn)


def _drop_mutation_exit(root):
    def fn(steps):
        for step in steps:
            if str(step["id"]).startswith("MUT-"):
                step["tests"].pop("exit", None)
                break
        return steps
    _steps(fn)(root)


def _error_not_assert(root):
    def fn(steps):
        for step in steps:
            if str(step["id"]).startswith("MUT-"):
                step["tests"]["by_outcome"] = {"ERROR": 1}
                step["tests"]["not_ok"][0]["result"] = "ERROR"
                step["tests"]["not_ok"][0]["exception"] = "RuntimeError: unrelated blow-up"
                break
        return steps
    _steps(fn)(root)


def _snapshot_changed(root):
    """Set 2. The snapshot changed after the coordinator sealed PASS/0."""
    _edit_record("POST_RUN_SNAPSHOT_CHECK.json",
                 lambda p: (p.__setitem__("unchanged", False),
                            p.__setitem__("digest_after", "9" * 64)))(root)
    surface = json.loads(_read(root, "SURFACE_RESULT.json").decode("utf-8"))
    surface["post_run_snapshot_unchanged"] = False
    surface["digest_after"] = "9" * 64
    surface["final_exit"] = 2
    surface["post_run_witness_sha256"] = hashlib.sha256(
        _read(root, "POST_RUN_SNAPSHOT_CHECK.json")).hexdigest()
    _write_json(os.path.join(root, "SURFACE_RESULT.json"), surface)


def _direct_run(root):
    """Sets 4 and 5. A direct, non-qualifying invocation writes none of the bootstrap's records."""
    for name in ("LAUNCH_ATTESTATION.json", "POST_RUN_SNAPSHOT_CHECK.json", "SURFACE_RESULT.json"):
        os.remove(os.path.join(root, name))


def _direct_run_dressed(root):
    """Set 5. As set 4, with the summary edited to look like a snapshot entry and re-sealed."""
    summary = json.loads(_read(root, SUMMARY).decode("utf-8"))
    summary["execution"]["mode"] = "snapshot-entry"
    summary["result"] = "PASS"
    _write_json(os.path.join(root, SUMMARY), summary)
    _direct_run(root)


def _foreign_witness(root):
    """Set 7. Foreign binding fields written into the witness."""
    _edit_record("POST_RUN_SNAPSHOT_CHECK.json",
                 lambda p: (p.__setitem__("run_id", "foreign-run"),
                            p.__setitem__("launch_token", "f" * 32),
                            p.__setitem__("commit", "f" * 40)))(root)


def _empty_registry_and_drop_mutations(root):
    """P5, the advisory probe. Recorded commit, tree and inventory values are left untouched."""
    with open(os.path.join(root, "snapshot", "verification", "mutations.py"), "w",
              encoding="utf-8") as fh:
        fh.write('"""Replaced by the conformance harness (P5)."""\nMUTATIONS = {}\n')
    _steps(lambda s: [x for x in s if not str(x["id"]).startswith("MUT-")])(root)


def _obj_sha1(kind, data):
    return hashlib.sha1(("%s %d\0" % (kind, len(data))).encode() + data).hexdigest()


def rewrite_registry(content):
    """Replace the retained registry AND rebuild the inventory and every recorded digest to match.

    This is a WHOLE-PACKAGE REWRITE. It is the documented authenticity limitation, used here
    deliberately: without it a registry case refuses at the inventory binding and never reaches the
    grammar, so the grammar would go untested. A case built this way tests the derivation, and it
    also demonstrates that a consistently rewritten package passes the binding - which is exactly
    what the limitation says.
    """
    def apply(root):
        with open(os.path.join(root, "snapshot", "verification", "mutations.py"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        rows = []
        snapshot = os.path.join(root, "snapshot")
        for base, _, files in os.walk(snapshot):
            for name in sorted(files):
                full = os.path.join(base, name)
                rel = os.path.relpath(full, snapshot).replace(os.sep, "/")
                with open(full, "rb") as fh:
                    data = fh.read()
                rows.append({"path": rel, "blob": _obj_sha1("blob", data),
                             "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
        rows.sort(key=lambda r: r["path"])
        body = json.dumps(rows, indent=1, sort_keys=True).encode("utf-8")
        with open(os.path.join(root, "execution_inventory.json"), "wb") as fh:
            fh.write(body)
        digest = hashlib.sha256(body).hexdigest()
        _edit_record("LAUNCH_ATTESTATION.json", lambda a: a.__setitem__("inventory_digest", digest))(root)
        _edit_record("POST_RUN_SNAPSHOT_CHECK.json",
                     lambda w: (w.__setitem__("inventory_digest", digest),
                                w.__setitem__("digest_after", digest)))(root)
        surface = json.loads(_read(root, "SURFACE_RESULT.json").decode("utf-8"))
        surface["inventory_digest"] = digest
        surface["digest_after"] = digest
        surface["post_run_witness_sha256"] = hashlib.sha256(
            _read(root, "POST_RUN_SNAPSHOT_CHECK.json")).hexdigest()
        _write_json(os.path.join(root, "SURFACE_RESULT.json"), surface)
    return apply


_MIXED_QUOTE_REGISTRY = (
    'MUTATIONS = {\n'
    '    "fu-b2-unrelated-reuse-refusal": {\n        "x": 1,\n    },\n'
    "    'fu-b3-unrelated-circle-check': {\n        'x': 1,\n    },\n}\n")


def _edit_audit(fn):
    """Edit SOURCE_AUDIT.json and RE-SEAL it, so only the binding under test can refuse."""
    def apply(root):
        audit = json.loads(_read(root, os.path.join("evidence", "SOURCE_AUDIT.json")).decode("utf-8"))
        fn(audit)
        data = _write_json(os.path.join(root, "evidence", "SOURCE_AUDIT.json"), audit)
        surface = json.loads(_read(root, "SURFACE_RESULT.json").decode("utf-8"))
        surface["source_audit_sha256"] = hashlib.sha256(data).hexdigest()
        _write_json(os.path.join(root, "SURFACE_RESULT.json"), surface)
    return apply


def _empty_not_ok(root):
    def fn(steps):
        for step in steps:
            if str(step["id"]).startswith("MUT-"):
                step["tests"]["not_ok"] = []
                break
        return steps
    _steps(fn)(root)


def _edit_surface(fn):
    """SURFACE_RESULT.json is sealed over by nothing, so it needs no re-seal."""
    def apply(root):
        surface = json.loads(_read(root, "SURFACE_RESULT.json").decode("utf-8"))
        fn(surface)
        _write_json(os.path.join(root, "SURFACE_RESULT.json"), surface)
    return apply


def _raw_duplicate_member(record, member, first, second):
    """Write a record with the SAME member twice. A default JSON reader keeps the last value."""
    def apply(root):
        text = '{ "%s": %s, "%s": %s' % (member, json.dumps(first), member, json.dumps(second))
        rest = json.loads(_read(root, record).decode("utf-8"))
        rest.pop(member, None)
        for key, value in rest.items():
            text += ', %s: %s' % (json.dumps(key), json.dumps(value))
        text += ' }\n'
        with open(os.path.join(root, record), "wb") as fh:
            fh.write(text.encode("utf-8"))
    return apply


def _duplicate_inventory_row(good_first):
    """Two rows for the registry with conflicting digests, and every inventory binding rebuilt.

    A whole-package rewrite, as case R3 is: without it the inventory seal refuses first and the row
    cardinality rule is never reached.
    """
    def apply(root):
        inv = json.loads(_read(root, "execution_inventory.json").decode("utf-8"))
        rows = [r for r in inv if r.get("path") != "verification/mutations.py"]
        real = [r for r in inv if r.get("path") == "verification/mutations.py"][0]
        fake = dict(real); fake["sha256"] = "0" * 64
        pair = [real, fake] if good_first else [fake, real]
        rows = rows + pair
        body = json.dumps(rows, indent=1, sort_keys=True).encode("utf-8")
        with open(os.path.join(root, "execution_inventory.json"), "wb") as fh:
            fh.write(body)
        digest = hashlib.sha256(body).hexdigest()
        _edit_record("LAUNCH_ATTESTATION.json", lambda a: a.__setitem__("inventory_digest", digest))(root)
        _edit_record("POST_RUN_SNAPSHOT_CHECK.json",
                     lambda w: (w.__setitem__("inventory_digest", digest),
                                w.__setitem__("digest_after", digest)))(root)
        surface = json.loads(_read(root, "SURFACE_RESULT.json").decode("utf-8"))
        surface["inventory_digest"] = digest
        surface["digest_after"] = digest
        surface["post_run_witness_sha256"] = hashlib.sha256(
            _read(root, "POST_RUN_SNAPSHOT_CHECK.json")).hexdigest()
        _write_json(os.path.join(root, "SURFACE_RESULT.json"), surface)
    return apply


def _mutation_tests_field(field, value):
    def fn(steps):
        for step in steps:
            if str(step["id"]).startswith("MUT-"):
                step["tests"][field] = value
                break
        return steps
    return _steps(fn)


def _edit_pair(prequalified, live):
    """Set the attestation's interpreter.site AND the summary's execution.site_dirs together.

    E14 compares the two, so a case that changes only one tests the comparison, not the predicate.
    These cases test the predicate, so both sides carry the same shape.
    """
    def apply(root):
        def set_site(att):
            att["site_prequalification"]["interpreter"]["site"] = prequalified
        _edit_record("LAUNCH_ATTESTATION.json", set_site)(root)
        _edit_summary(lambda s: s["execution"].__setitem__("site_dirs", live))(root)
    return apply


def _relocate(root):
    """Set 17. The artifact is examined away from the machine that produced it."""
    gone = "/nonexistent/producing/machine/s015-verification/snapshot"
    _edit_record("LAUNCH_ATTESTATION.json", lambda a: a.__setitem__("snapshot", gone))(root)
    _edit_summary(lambda s: (s["execution"].__setitem__("root", gone),
                             s["execution"].__setitem__("entry", gone + "/verification/route.py")))(root)


# ---------------------------------------------------------------------------------------------
# THE CASES. `expect` is specified independently of any implementation: it is what the CONTRACT
# requires, written out before any implementation was run against it.
# ---------------------------------------------------------------------------------------------
CASES = [
    ("1",  "complete valid evidence, unmodified", None, True, []),

    ("2",  "snapshot changed after the coordinator sealed PASS/0", _snapshot_changed, False,
     ["the surface's final exit is <value>, not 0: the run did not complete cleanly "
      "(post-run check, coordinator or cleanup)",
      "the final snapshot witness reports a change during the run (Q6)"]),

    ("3",  "final witness absent", _drop_record("POST_RUN_SNAPSHOT_CHECK.json"), False,
     ["final evidence missing: no final snapshot witness (POST_RUN_SNAPSHOT_CHECK.json) in the run"]),

    ("3b", "a record present but malformed", _corrupt_record("SURFACE_RESULT.json"), False,
     ["final evidence unreadable: completed surface result (SURFACE_RESULT.json) could not be "
      "parsed as JSON (<exc>): refused"]),

    ("4",  "direct non-qualifying run", _direct_run, False,
     ["final evidence missing: no launch attestation in the run",
      "final evidence missing: no final snapshot witness (POST_RUN_SNAPSHOT_CHECK.json) in the run",
      "final evidence missing: no completed surface result (SURFACE_RESULT.json) in the run"]),

    ("5",  "direct run dressed as a snapshot entry and re-sealed", _direct_run_dressed, False,
     ["final evidence missing: no launch attestation in the run",
      "final evidence missing: no completed surface result (SURFACE_RESULT.json) in the run"]),

    ("6",  "LAUNCH_ATTESTATION.run_id removed",
     _edit_record("LAUNCH_ATTESTATION.json", lambda a: a.pop("run_id", None)), False,
     ["required binding field run_id is absent from the launch attestation "
      "(LAUNCH_ATTESTATION.json): refused before any comparison"]),

    ("7",  "foreign run_id, launch_token and commit in the witness", _foreign_witness, False,
     ["launch token disagrees across attestation, final witness, surface result, summary and audit",
      "run id disagrees across attestation, final witness, surface result, summary and audit",
      "the commit disagrees between attestation, final witness, surface result and identity",
      "POST_RUN_SNAPSHOT_CHECK.json differs from the one the surface result sealed over"]),

    ("8",  "witness copied from a second real clean launch", "EXECUTED-ELSEWHERE", None,
     ["A1's unchanged probe, a1_v05_focused.json, witness_from_different_launch. It needs a second "
      "GENUINE launch of the same snapshot and cannot be made by mutating records: a fabricated "
      "witness is set 7, not set 8."]),

    ("9",  "committed module imports a W-only helper that raises a caught ImportError",
     "EXECUTED-ELSEWHERE", None,
     ["A1's unchanged probe, w_import_failed. It needs a real run whose snapshot contains the helper "
      "and which executes it; the record-level shadow (U.refusals non-empty) is exercised by the "
      "integration step against a real run, not here."]),

    ("10", "the same helper importing successfully", "EXECUTED-ELSEWHERE", None,
     ["A1's unchanged probe, w_import_success_negative. Same reason as set 9."]),

    ("11", "all mutation controls omitted",
     _steps(lambda s: [x for x in s if not str(x["id"]).startswith("MUT-")]), False,
     ["the run's mutation controls are incomplete or unsound: <found>"]),

    ("12", "ONE of two mutation controls omitted",
     _steps(lambda s: [x for x in s if x["id"] != "MUT-fu-b3-unrelated-circle-check"]), False,
     ["the run's mutation controls are incomplete or unsound: <found>"]),

    ("13", "a fixed control omitted (LOCK)",
     _steps(lambda s: [x for x in s if x["id"] != "LOCK"]), False,
     ["a required control is missing, duplicated or did not pass: <found>"]),

    ("14", "a control duplicated (S015 twice)",
     _steps(lambda s: s + [x for x in s if x["id"] == "S015"]), False,
     ["the coordinator summary records step id S015 more than once: the step record is contradictory"]),

    ("15", "mutation target ERRORed instead of asserting", _error_not_assert, False,
     ["the run's mutation controls are incomplete or unsound: <found>"]),

    ("16", "identity.commit_tree altered",
     _edit_summary(lambda s: s["identity"].__setitem__("commit_tree", "0" * 40)), False,
     ["the candidate tree disagrees between the launch attestation and the coordinator summary identity"]),

    ("17", "artifact examined away from its producing machine", _relocate, True, []),

    ("P1", "mutation target exit ABSENT", _drop_mutation_exit, False,
     ["the run's mutation controls are incomplete or unsound: <found>"]),
    ("P2", "mutation target exit 2", _mutation_field("exit", 2), False,
     ["the run's mutation controls are incomplete or unsound: <found>"]),
    ("P3", "mutation target exit -9", _mutation_field("exit", -9), False,
     ["the run's mutation controls are incomplete or unsound: <found>"]),
    ("P4", "mutation target exit the string \"1\"", _mutation_field("exit", "1"), False,
     ["the run's mutation controls are incomplete or unsound: <found>"]),

    ("P5", "registry emptied, mutation steps removed, re-sealed", _empty_registry_and_drop_mutations,
     False, ["the mutation registry cannot be derived: the retained verification/mutations.py is not "
             "the bound candidate's registry"]),

    ("S1", "site lists both EMPTY arrays",
     _edit_pair([], []), True, []),
    ("S2", "site lists both SINGLETON arrays (the baseline's own shape)",
     _edit_pair(["C:/one"], ["C:/one"]), True, []),
    ("S3", "site lists both MULTIPLE strings, same members, different order",
     _edit_pair(["C:/a", "C:/b"], ["C:/b", "C:/a"]), True, []),
    ("S4", "site_dirs MISSING from the execution record",
     _edit_summary(lambda s: s["execution"].pop("site_dirs", None)), False,
     ["the prequalified site list or the recorded site directories are not a list of paths: "
      "refused rather than compared"]),
    ("S5", "site_dirs NULL",
     _edit_summary(lambda s: s["execution"].__setitem__("site_dirs", None)), False,
     ["the prequalified site list or the recorded site directories are not a list of paths: "
      "refused rather than compared"]),
    ("S6", "site_dirs a SCALAR string, not an array",
     _edit_summary(lambda s: s["execution"].__setitem__("site_dirs", "C:/one")), False,
     ["binding field site_dirs of the coordinator summary execution record (summary.json execution) is not a JSON array of strings: refused before any comparison"]),
    ("S7", "site_dirs an array of MIXED types",
     _edit_summary(lambda s: s["execution"].__setitem__("site_dirs", ["C:/one", 2])), False,
     ["binding field site_dirs of the coordinator summary execution record (summary.json execution) is not a JSON array of strings: refused before any comparison"]),
    ("S8", "both are lists of strings but their MEMBERS DIFFER",
     _edit_pair(["C:/a"], ["C:/b"]), False,
     ["the live site directories differ from the prequalified list"]),

    ("V1", "summary.identity.valid is false",
     _edit_summary(lambda s: s["identity"].__setitem__("valid", False)), False,
     ["the coordinator summary records an invalid identity: <value>"]),
    ("V2", "summary.route names a different route version",
     _edit_summary(lambda s: s.__setitem__("route", "S015 verification route v0.4")), False,
     ["the evidence is from <found>, not the route this decision is written against (<expected>)"]),

    ("A1-01", "SOURCE_AUDIT.refusals is false, not an array",
     _edit_audit(lambda u: u.__setitem__("refusals", False)), False,
     ["binding field refusals of the source audit (SOURCE_AUDIT.json) is a <found>, not a JSON array: refused before any comparison"]),
    ("A1-02", "SOURCE_AUDIT.refusals is 0, not an array",
     _edit_audit(lambda u: u.__setitem__("refusals", 0)), False,
     ["binding field refusals of the source audit (SOURCE_AUDIT.json) is a <found>, not a JSON array: refused before any comparison"]),
    ("A1-03", "SOURCE_AUDIT.refusals is an empty OBJECT, not an array",
     _edit_audit(lambda u: u.__setitem__("refusals", {})), False,
     ["binding field refusals of the source audit (SOURCE_AUDIT.json) is a <found>, not a JSON array: refused before any comparison"]),
    ("A1-04", "site_prequalification.refusals is false, not an array",
     _edit_record("LAUNCH_ATTESTATION.json",
                  lambda a: a["site_prequalification"].__setitem__("refusals", False)), False,
     ["binding field refusals of the launch attestation site prequalification (LAUNCH_ATTESTATION.json site_prequalification) is a <found>, not a JSON array: refused before any comparison"]),
    ("A1-05", "SURFACE_RESULT.final_exit is false, not an integer",
     _edit_surface(lambda s: s.__setitem__("final_exit", False)), False,
     ["binding field final_exit of the completed surface result (SURFACE_RESULT.json) is a <found>, not a JSON integer: refused before any comparison"]),
    ("A1-06", "SURFACE_RESULT.final_exit is 0.0, not an integer",
     _edit_surface(lambda s: s.__setitem__("final_exit", 0.0)), False,
     ["binding field final_exit of the completed surface result (SURFACE_RESULT.json) is a <found>, not a JSON integer: refused before any comparison"]),
    ("A1-07", "a mutation records FAIL count 0 beside a failing target",
     _mutation_tests_field("by_outcome", {"FAIL": 0}), False,
     ["the run's mutation controls are incomplete or unsound: <found>"]),
    ("A1-08", "a mutation records FAIL count null",
     _mutation_tests_field("by_outcome", {"FAIL": None}), False,
     ["the run's mutation controls are incomplete or unsound: <found>"]),
    ("A1-09", "a mutation records reconciled false",
     _mutation_tests_field("reconciled", False), False,
     ["the run's mutation controls are incomplete or unsound: <found>"]),
    ("A1-10", "SURFACE_RESULT carries final_exit TWICE, 2 then 0",
     _raw_duplicate_member("SURFACE_RESULT.json", "final_exit", 2, 0), False,
     ["final evidence contradictory: completed surface result (SURFACE_RESULT.json) contains a repeated member <name>: refused"]),
    ("A1-11", "two inventory rows for the registry, the good one first",
     _duplicate_inventory_row(True), False,
     ["the mutation registry cannot be derived: the bound inventory carries <n> rows for verification/mutations.py: exactly one is required"]),
    ("A1-12", "two inventory rows for the registry, the bad one first",
     _duplicate_inventory_row(False), False,
     ["the mutation registry cannot be derived: the bound inventory carries <n> rows for verification/mutations.py: exactly one is required"]),

    ("T1", "attestation site_prequalification.refusals NON-EMPTY",
     _edit_record("LAUNCH_ATTESTATION.json",
                  lambda a: a["site_prequalification"].__setitem__("refusals", ["a startup refusal"])),
     False, ["the attestation records startup refusals"]),
    ("T2", "source audit refusals NON-EMPTY, re-sealed",
     _edit_audit(lambda u: u.__setitem__("refusals", ["a module was refused"])), False,
     ["the source audit refused (detected after execution): <refusals>"]),
    ("T3", "a mutation control's not_ok list EMPTIED", _empty_not_ok, False,
     ["the run's mutation controls are incomplete or unsound: <found>"]),

    ("P6", "site_dirs malformed (a list of non-strings)",
     _edit_summary(lambda s: s["execution"].__setitem__("site_dirs", [1, {"a": 2}])), False,
     ["binding field site_dirs of the coordinator summary execution record (summary.json execution) is not a JSON array of strings: refused before any comparison"]),

    ("R1", "registry absent from the retained snapshot",
     lambda r: os.remove(os.path.join(r, "snapshot", "verification", "mutations.py")), False,
     ["the mutation registry cannot be derived: verification/mutations.py is absent from the "
      "attested snapshot"]),

    ("R3", "registry with a mixed double- and single-quoted key, package rebuilt",
     rewrite_registry(_MIXED_QUOTE_REGISTRY), False,
     ["the mutation registry cannot be derived: verification/mutations.py line <n> is not an "
      "accepted MUTATIONS entry: <line>"]),

    ("R2", "snapshot absent from the retained run root",
     lambda r: shutil.rmtree(os.path.join(r, "snapshot")), False,
     ["the mutation registry cannot be derived: the attested snapshot is not present in the "
      "retained run root"]),
]


# ------------------------------------------------------------------ invocation --
def _load_gate_module(path):
    spec = importlib.util.spec_from_file_location("s015_gate_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _from_file(out, proc):
    """A consumer's decision is the record it wrote, never its console text."""
    if not os.path.isfile(out):
        return None, [], "wrote no decision record", ([], [])
    try:
        with open(out, "rb") as fh:
            decision = json.loads(fh.read().decode("utf-8"))
    except Exception as exc:
        return None, [], "decision record unreadable (%s)" % type(exc).__name__, ([], [])
    for key in ("qualifying", "reasons"):
        if key not in decision:
            return None, [], "decision record omits %s" % key, ([], [])
    if bool(decision["qualifying"]) != (proc.returncode == 0):
        return None, [], "decision record and exit status disagree", ([], [])
    return (bool(decision["qualifying"]), list(decision["reasons"]), "",
            (decision.get("expected_controls", []), decision.get("observed_controls", [])))


def decide(impl, root):
    """Return (qualifying, refusals, note, (expected_controls, observed_controls))."""
    kind, path = impl["kind"], impl["path"]
    if not os.path.isfile(path):
        return None, [], "not present: %s" % os.path.basename(path), ([], [])
    out = os.path.join(tempfile.mkdtemp(prefix="s015-dec-"), "decision.json")
    if kind == "python-cli":
        proc = subprocess.run([sys.executable, path, root, out], capture_output=True, text=True)
        if proc.returncode not in (0, 1):
            return None, [], "exited %d (a decision must be 0 or 1)" % proc.returncode, ([], [])
        if proc.stderr.strip():
            return None, [], "wrote to stderr: " + proc.stderr.strip().splitlines()[-1][:120], ([], [])
        return _from_file(out, proc)
    if kind == "python-direct":
        # The direct path. A harness or another consumer calling gate() must get the same non-green
        # behaviour from malformed evidence that the CLI gives: a decision that raises did not happen.
        try:
            module = _load_gate_module(path)
            decision = module.gate(root)
        except Exception as exc:
            return None, [], "gate() raised %s: a decision must never raise" % type(exc).__name__, ([], [])
        if not isinstance(decision, dict) or "qualifying" not in decision or "reasons" not in decision:
            return None, [], "gate() did not return a decision", ([], [])
        return (bool(decision["qualifying"]), list(decision["reasons"]), "",
                (decision.get("expected_controls", []), decision.get("observed_controls", [])))
    if kind == "powershell":
        exe = shutil.which("pwsh") or shutil.which("powershell")
        if not exe:
            return None, [], "no PowerShell on this host", ([], [])
        proc = subprocess.run([exe, "-ExecutionPolicy", "Bypass", "-File", path,
                               "-RunRoot", root, "-OutJson", out],
                              capture_output=True, text=True)
        if proc.returncode not in (0, 1):
            return None, [], "exited %d (a decision must be 0 or 1)" % proc.returncode, ([], [])
        return _from_file(out, proc)
    if kind == "bootstrap":
        proc = subprocess.run([sys.executable, "-I", "-S", path, "--decide", root, "--out", out],
                              capture_output=True, text=True)
        if proc.returncode not in (0, 1):
            return None, [], "no --decide entry, or exited %d" % proc.returncode, ([], [])
        return _from_file(out, proc)
    return None, [], "unknown implementation kind", ([], [])


def classify_evidence(base, provenance_path=None):
    """Return (provenance, notes).

    RETAINED is asserted ONLY against a provenance record naming the qualified source artifact and
    the run it came from. It is never inferred from the shape of a path: a path says where a
    directory is, not where its contents came from, and an earlier version of this harness called a
    repointed fixture RETAINED on that basis alone. With no record the answer is UNESTABLISHED -
    an accurate attribution, not an authenticity mechanism.
    """
    notes = []
    try:
        att = json.loads(_read(base, "LAUNCH_ATTESTATION.json").decode("utf-8"))
    except Exception:
        return "UNKNOWN", ["the launch attestation could not be read"]
    notes.append("attested run_id %s, commit %s, tree %s"
                 % (att.get("run_id"), str(att.get("commit"))[:12], str(att.get("tree"))[:12]))
    inv = os.path.join(base, "execution_inventory.json")
    bound = os.path.isfile(inv) and hashlib.sha256(
        _read(base, "execution_inventory.json")).hexdigest() == att.get("inventory_digest")
    notes.append("execution_inventory.json %s the attested digest"
                 % ("matches" if bound else "does NOT match"))
    if not provenance_path:
        notes.append("no provenance record supplied (--provenance)")
        return "UNESTABLISHED", notes
    try:
        with open(provenance_path, "rb") as fh:
            prov = json.loads(fh.read().decode("utf-8"))
    except Exception as exc:
        notes.append("the provenance record could not be read (%s)" % type(exc).__name__)
        return "UNESTABLISHED", notes
    absent = [k for k in ("source", "run_id", "commit", "tree") if not prov.get(k)]
    if absent:
        notes.append("the provenance record omits %s" % ", ".join(absent))
        return "UNESTABLISHED", notes
    wrong = [k for k in ("run_id", "commit", "tree") if prov[k] != att.get(k)]
    if wrong:
        notes.append("the provenance record disagrees with the attestation on %s" % ", ".join(wrong))
        return "UNESTABLISHED", notes
    notes.append("bound to the qualified source artifact: %s" % prov["source"])
    notes.append("NOTE: this harness verifies the record AGREES with the attestation. It cannot verify")
    notes.append("      that the record came from a source INDEPENDENT of the run - one copied out of")
    notes.append("      the attestation agrees with it trivially (contract 6.2b). Establishing the")
    notes.append("      independence of the named source is the reporter's job, not this check's.")
    return "RETAINED", notes


# C10, Human Governor, 18 September 2026: ONE authoritative implementation, invoked on both paths.
# Required here are the two INVOCATION PATHS of that one gate - the command line and a direct call -
# because a consumer calling gate() must get the same non-green behaviour from malformed evidence
# that the CLI gives. The PowerShell and bootstrap adapters below are retained so an optional
# implementation can still be exercised; neither is required and neither's absence makes a run
# INCOMPLETE.
REQUIRED_IMPLEMENTATIONS = ("gate-cli", "gate-direct")

# C10 retired sets 8, 9 and 10 from this candidate's completion criteria: they exist to compare a
# second genuine launch and a W-only-helper run ACROSS IMPLEMENTATIONS, and there is one
# implementation. They carry to PKT-B. They are still listed and still reported, and they no longer
# make a run INCOMPLETE.
ELSEWHERE_SETS = ("8", "9", "10")
ELSEWHERE_RETIRED = True
ELSEWHERE_FIELDS = ("set", "executed_by", "artefact", "sha256", "date", "disposition")


def load_elsewhere(path):
    """Return ({set_id: record}, notes).

    Sets 8, 9 and 10 need a real run and cannot be constructed here. They stay OUTSTANDING until
    their integration evidence is supplied through this record - which must NAME the artefact and
    its digest, not merely assert that someone ran it. Without that there was no route to a complete
    result at all, which made the outstanding status permanent rather than pending.
    """
    if not path:
        return {}, ["no integration-evidence record supplied (--elsewhere): sets 8, 9, 10 outstanding"]
    notes, out = [], {}
    try:
        with open(path, "rb") as fh:
            rows = json.loads(fh.read().decode("utf-8"))
    except Exception as exc:
        return {}, ["the integration-evidence record could not be read (%s)" % type(exc).__name__]
    if not isinstance(rows, list):
        return {}, ["the integration-evidence record is not a list of set records"]
    for row in rows:
        if not isinstance(row, dict):
            notes.append("an entry is not an object")
            continue
        absent = [f for f in ELSEWHERE_FIELDS if not row.get(f)]
        if absent:
            notes.append("entry %r omits %s" % (row.get("set"), ", ".join(absent)))
            continue
        if str(row["set"]) not in ELSEWHERE_SETS:
            notes.append("entry names set %r, which is not executed elsewhere" % row["set"])
            continue
        if str(row["disposition"]).lower() != "as required":
            notes.append("set %s is recorded as %r, not 'as required'" % (row["set"], row["disposition"]))
            continue
        out[str(row["set"])] = row
        notes.append("set %s satisfied by %s (%s), run by %s on %s"
                     % (row["set"], row["artefact"], str(row["sha256"])[:12], row["executed_by"], row["date"]))
    for set_id in ELSEWHERE_SETS:
        if set_id not in out:
            notes.append("set %s remains OUTSTANDING" % set_id)
    return out, notes


def run(base, only=None, impls=None, provenance=None, elsewhere=None):
    partial = bool(only or impls)
    kind, notes = classify_evidence(base, provenance)
    satisfied, elsewhere_notes = load_elsewhere(elsewhere)
    print("run mode       : %s" % ("PARTIAL DEVELOPMENT RUN - selected cases or consumers; it cannot "
                                   "establish conformance"
                                   if partial else "INTEGRATION RUN - every case, every required consumer"))
    print("base run root  : %s" % os.path.abspath(base))
    print("provenance     : %s" % kind)
    for n in notes:
        print("                 - %s" % n)
    if kind != "RETAINED":
        print("                 * provenance unestablished: results below may not be cited in a")
        print("                   qualification report. Only RETAINED evidence may.")
    print()
    print("sets 8-10      : %s" % ("all satisfied by supplied integration evidence"
                                   if len(satisfied) == len(ELSEWHERE_SETS) else "OUTSTANDING"))
    for n in elsewhere_notes:
        print("                 - %s" % n)
    print()
    active = [i for i in IMPLEMENTATIONS if not impls or i["id"] in impls]
    for impl in active:
        digest = (hashlib.sha256(open(impl["path"], "rb").read()).hexdigest()
                  if os.path.isfile(impl["path"]) else "<absent>")
        print("implementation : %-12s %s" % (impl["id"], impl["label"]))
        print("                 %s" % digest)
    print()

    failures, skipped, divergences, executions = 0, 0, 0, 0
    unavailable = {}
    for case_id, description, mutation, expect_q, required in CASES:
        if only and case_id not in only:
            continue
        if mutation == "EXECUTED-ELSEWHERE":
            row = satisfied.get(case_id)
            state = "SATISFIED" if row else ("RETIRED->PKT-B" if ELSEWHERE_RETIRED else "OUTSTANDING")
            print("%-4s %-11s %-52s  %s" % (case_id, "ELSEWHERE", description, state))
            print("       %s" % required[0])
            if row:
                print("       evidence: %s  sha256 %s  by %s, %s"
                      % (row["artefact"], row["sha256"], row["executed_by"], row["date"]))
            elif not ELSEWHERE_RETIRED:
                skipped += 1
            continue
        tmp = tempfile.mkdtemp(prefix="s015-conf-")
        root = os.path.join(tmp, "run")
        shutil.copytree(base, root)
        try:
            if mutation is not None:
                mutation(root)
            shared = {}
            for impl in active:
                qualifying, refusals, note, controls = decide(impl, root)
                if note:
                    print("%-4s %-11s %-52s  [%s] %s" % (case_id, "NO DECISION", description,
                                                        impl["id"], note))
                    unavailable.setdefault(impl["id"], note)
                    continue
                executions += 1
                ok = (qualifying == expect_q)
                missing = _check_refusals(required, refusals) if not expect_q else []
                if missing:
                    ok = False
                shared[impl["id"]] = {"qualifying": qualifying, "refusals": sorted(refusals),
                                      "expected_controls": sorted(controls[0]),
                                      "observed_controls": sorted(controls[1])}
                print("%-4s %-11s %-52s  [%s] %s" % (
                    case_id, "QUALIFIES" if qualifying else "REFUSES", description, impl["id"],
                    "ok" if ok else "*** FAIL ***"))
                if not ok:
                    failures += 1
                    print("       expected %s" % ("QUALIFIES" if expect_q else "REFUSES"))
                    for t in missing:
                        print("       required refusal not produced: " + t)
                    for r in refusals[:3]:
                        print("       got: " + r[:150])
            if len(shared) > 1:
                # The ACTUAL decision, the ACTUAL refusal strings with their interpolated values, and
                # the derived control sets. Reducing refusals to "which template matched" made
                # "missing LOCK" and "missing S015" indistinguishable and reported no divergence
                # where two consumers disagreed about WHICH control was missing.
                base_id = sorted(shared)[0]
                base_row = shared[base_id]
                for impl_id in sorted(shared)[1:]:
                    for field in ("qualifying", "refusals", "expected_controls", "observed_controls"):
                        if shared[impl_id][field] != base_row[field]:
                            divergences += 1
                            print("       *** DIVERGENCE on %s: %s=%r  %s=%r"
                                  % (field, base_id, base_row[field], impl_id, shared[impl_id][field]))
                            print("       report it, do not reconcile it")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    print()
    constructible = [c for c in CASES if c[2] != "EXECUTED-ELSEWHERE"]
    selected = [c for c in constructible if not only or c[0] in only]
    # Only a REQUIRED consumer's silence makes a run incomplete. An optional adapter that is absent
    # (the PowerShell one, the bootstrap one) is reported and does not block - C10.
    missing_required = sorted(((set(REQUIRED_IMPLEMENTATIONS) - {i["id"] for i in active})
                               | set(unavailable)) & set(REQUIRED_IMPLEMENTATIONS))
    print("cases constructible here : %d" % len(constructible))
    print("cases selected           : %d" % len(selected))
    print("decisions obtained       : %d  (cases x consumers that actually decided)" % executions)
    print("executed elsewhere       : %d  - sets 8, 9, 10 remain OUTSTANDING until the integration" % skipped)
    print("                             evidence is supplied; they are not counted here")
    print("failures                 : %d" % failures)
    print("divergences              : %d" % divergences)
    for impl_id in sorted(unavailable):
        role = "REQUIRED" if impl_id in REQUIRED_IMPLEMENTATIONS else "optional"
        print("no decision from         : %-12s [%s] %s" % (impl_id, role, unavailable[impl_id]))
    print()
    if failures or divergences:
        print("RESULT: FAILED. %d failure(s), %d divergence(s)." % (failures, divergences))
        verdict = 1
    elif partial or missing_required or skipped or kind != "RETAINED":
        print("RESULT: INCOMPLETE - no conformance claim may be made from this run.")
        if partial:
            print("  - a partial development run: cases or consumers were selected explicitly")
        for impl_id in missing_required:
            print("  - required consumer produced no decision: %s" % impl_id)
        if skipped:
            print("  - sets 8, 9 and 10 are outstanding and executed elsewhere")
        if kind != "RETAINED":
            print("  - provenance is %s" % kind)
        verdict = 2
    else:
        print("RESULT: CONFORMANCE COMPLETE for the cases constructible here.")
        verdict = 0
    if ELSEWHERE_RETIRED:
        print("NOTE: sets 8, 9 and 10 are RETIRED to PKT-B by C10 - listed and reported above, not")
        print("      counted, and no longer a reason for INCOMPLETE on this candidate.")
    print()
    print("HISTORICAL NEGATIVE COMPARISONS, retained:")
    for h in HISTORICAL:
        print("  %s" % h["what"])
        print("    sha256 %s" % h["sha256"])
        print("    %s" % h["result"])
        print("    %s" % h["why"])
    return verdict


def main():
    args = sys.argv[1:]
    only = impls = None
    provenance = None
    if "--provenance" in args:
        i = args.index("--provenance")
        provenance = args[i + 1]
        del args[i:i + 2]
    bootstrap = None
    if "--bootstrap" in args:
        i = args.index("--bootstrap")
        bootstrap = args[i + 1]
        del args[i:i + 2]
    elsewhere = None
    if "--elsewhere" in args:
        i = args.index("--elsewhere")
        elsewhere = args[i + 1]
        del args[i:i + 2]
    for flag, setter in (("--only", "only"), ("--impl", "impls")):
        if flag in args:
            i = args.index(flag)
            value = set(args[i + 1].split(","))
            del args[i:i + 2]
            if setter == "only":
                only = value
            else:
                impls = value
    if len(args) != 1:
        print(__doc__.strip().splitlines()[0])
        return 2
    if bootstrap:
        for impl in IMPLEMENTATIONS:
            if impl["id"] == "bootstrap":
                impl["path"] = os.path.abspath(bootstrap)
    return run(args[0], only, impls, provenance, elsewhere)


if __name__ == "__main__":
    sys.exit(main())
