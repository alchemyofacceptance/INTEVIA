"""S015 qualification decision — CONSUMER_REQUIREMENT_v0_7.md sections 1-3, exactly.

    Author  : Vision Chamber (Claude Opus 5), UFUND-5, 18 September 2026
    Against : CONSUMER_REQUIREMENT_v0_7.md
    Status  : CANDIDATE. Member 2 of the coordinated correction of v0_7 section 6.1.
              **It depends on the contract's approval and is not adopted until the contract is.**
              Until then the landed gate decides against v0.5 as described below.
              CONSUMER-SIDE. It decides; it implements nothing in the repository.

    python s015_gate.py RUN_ROOT [OUT_JSON]
      exit 0  qualifying
      exit 1  not qualifying (every refusal named on stdout and in OUT_JSON)

Order is the requirement's and is not an optimisation to be reordered:
  1. all five records present, parseable, JSON objects -> on any failure, refuse; NO field or
     equality check is made
  2. every required binding field present and non-null, and the step record well formed and free of
     repeated ids -> on any failure, refuse; NO equality or completeness check is made
  3. the equality and completeness checks E1-E23

A record that will not parse is a named refusal at step 1, never an exception: a parsing failure must
not be able to produce a qualifying outcome (Human Governor, 18 September 2026).

WHAT CHANGED FROM THE GATE LANDED AT 7622cce, AND WHY

  Disclosure first. The landed gate's docstring says "CONSUMER_REQUIREMENT_v0_5.md sections 1-3,
  exactly". That is not accurate and is corrected here. The landed gate requires `route` and
  `attested_commit` (v0.5 section 2 requires neither) and applies an attested-commit comparison
  (v0.5 section 3 has no such check). It is therefore a strict superset of v0.5: it could refuse a
  run v0.5 would qualify, never the reverse. Both qualifying runs of 18 September 2026 were decided
  by it. This version states what it implements and implements what it states.

  E19  the attested-commit comparison, named and with its rationale recorded: TRANSPORT CONSISTENCY
       ONLY. `attested_commit` is the same supplied value along a longer path; E6 is the
       independence check. Never report E19 as corroboration of the commit.
  E20  the six fixed controls, EACH EXACTLY ONCE, and every recorded step at PASS.
  E21  the COMPLETE applicable mutation-control set, derived from the bound candidate's registry
       (see derive_mutation_registry), each exactly once, with its assertion and exit evidence.
       "At least one mutation step" does not establish completeness and is not what this does.
  E22  cleanup CLEAN.
  E23  the candidate tree agreeing across the attestation and the summary identity. An integrity
       check on one run's records, and only that: it says nothing about whether a commit id is
       resolvable by a third party, and nothing about what a report must contain.
  C6   the record is the "launch attestation". It is written by the bootstrap, not the launcher.
       This diverges from v0.5's verbatim text and is disclosed in v0_7 section 6.2; the wording
       moves here, in bootstrap.gate, in the PowerShell consumer and in gate_conformance.py
       together, or in none.
"""
import ast
import hashlib
import re
import json
import os
import sys

RECORDS = (
    ("A", "LAUNCH_ATTESTATION.json", "", "launch attestation", "launch attestation (LAUNCH_ATTESTATION.json)"),
    ("P", "POST_RUN_SNAPSHOT_CHECK.json", "", "final snapshot witness (POST_RUN_SNAPSHOT_CHECK.json)", "final snapshot witness (POST_RUN_SNAPSHOT_CHECK.json)"),
    ("S", "SURFACE_RESULT.json", "", "completed surface result (SURFACE_RESULT.json)", "completed surface result (SURFACE_RESULT.json)"),
    ("M", "summary.json", "evidence", "coordinator summary", "coordinator summary (summary.json)"),
    ("U", "SOURCE_AUDIT.json", "evidence", "source audit (SOURCE_AUDIT.json)", "source audit (SOURCE_AUDIT.json)"),
)

REQUIRED = (
    ("A", "launch attestation (LAUNCH_ATTESTATION.json)",
     ("run_id", "launch_token", "commit", "inventory_digest", "snapshot", "site_prequalification", "tree")),
    ("SP", "launch attestation site prequalification (LAUNCH_ATTESTATION.json site_prequalification)",
     ("validated_dependency_root", "validated_site_packages", "refusals")),
    ("P", "final snapshot witness (POST_RUN_SNAPSHOT_CHECK.json)",
     ("run_id", "launch_token", "commit", "inventory_digest", "unchanged", "digest_after")),
    ("S", "completed surface result (SURFACE_RESULT.json)",
     ("run_id", "launch_token", "commit", "inventory_digest", "digest_after", "post_run_snapshot_unchanged",
      "post_run_witness_sha256", "coordinator_exit", "summary_sha256", "source_audit_sha256", "final_exit")),
    ("M", "coordinator summary (summary.json)",
     ("run_id", "result", "exit_status", "execution", "identity", "route", "steps", "cleanup")),
    ("X", "coordinator summary execution record (summary.json execution)", ("launch_token", "root", "entry", "mode")),
    ("I", "coordinator summary identity record (summary.json identity)",
     ("commit", "attested_commit", "commit_tree", "working_tree_git_tree", "valid")),
    ("C", "coordinator summary cleanup record (summary.json cleanup)", ("outcome",)),
    ("U", "source audit (SOURCE_AUDIT.json)", ("run_id", "launch_token", "refusals")),
)

# The controls every qualifying run must record. Mutation controls are NOT here: they come from the
# bound candidate's own registry, because they change as packets add mutations (v0_7 section 3.3).
FIXED_CONTROLS = ("IDENTITY", "OFFLINE", "SELF", "S015", "OWNERSHIP", "LOCK")

# The only exit a mutation control's target may report. Under the mutation the target must FAIL BY
# ASSERTION, so the test process reports exactly one failure and exits 1. Recorded as an exact value
# and an exact type: a missing exit, 2, -9, or the string "1" each mean something other than a clean
# assertion failure and none of them may qualify.
PERMITTED_MUTATION_TARGET_EXIT = 1

# The route version this statement is written against (contract section 3, E25; RD-5 confirmed).
# It is an exact value: when the route's version changes, the contract changes with it, deliberately,
# so that a consumer cannot silently accept evidence from a route it was not written for.
ROUTE_VERSION = "S015 verification route v0.5"

# An empty list means "no refusals", not "nothing checked". A field's absence is section 2's business.
_REFUSAL_REGISTRY = "the mutation registry cannot be derived: %s"


def derive_mutation_registry(run_root, attested_snapshot, attested_inventory_digest):
    """Return (expected_step_ids, refusals) from the BOUND candidate's verification/mutations.py.

    PARSED, NEVER IMPORTED. The module imports Django at module level; importing it would execute
    code belonging to the artefact under test inside the consumer that judges it.

    LOCATED RELATIVE TO THE RUN ROOT, never by the absolute path the attestation records. That path
    is where the snapshot stood on the machine that produced the run; a consumer examining a
    downloaded artifact is somewhere else entirely, and on the producing machine the path may still
    exist while holding something unrelated. Reading the registry through it would let a decision be
    taken over one candidate's evidence using another candidate's registry. The attested path is
    still bound - E12 requires it to equal the summary's execution root - but binding a path and
    using it to find a file are different jobs.

    There is no fallback: if the set cannot be derived the decision is refused by name, and a
    producer-declared set is never accepted in its place.
    """
    if os.path.basename(str(attested_snapshot).replace("\\", "/").rstrip("/")) != "snapshot":
        return [], [_REFUSAL_REGISTRY % "the attested snapshot is not the run root's snapshot directory"]
    snapshot = os.path.join(run_root, "snapshot")
    if not os.path.isdir(snapshot):
        return [], [_REFUSAL_REGISTRY % "the attested snapshot is not present in the retained run root"]
    path = os.path.join(str(snapshot), "verification", "mutations.py")
    if not os.path.isfile(path):
        return [], [_REFUSAL_REGISTRY % "verification/mutations.py is absent from the attested snapshot"]
    try:
        with open(path, "rb") as fh:
            source = fh.read()
    except Exception as exc:
        return [], [_REFUSAL_REGISTRY % ("verification/mutations.py could not be read (%s)" % type(exc).__name__)]

    # The registry's BYTES are verified against the bound candidate before they define anything.
    # Locating the file relative to the run root says only WHERE to look; it says nothing about
    # WHAT was found. Without this, an empty registry can be written into the retained snapshot and
    # the mutation steps dropped, and the run qualifies with commit, tree and inventory values
    # untouched - because those are RECORDED values that nothing recomputes.
    inv_path = os.path.join(run_root, "execution_inventory.json")
    if not os.path.isfile(inv_path):
        return [], [_REFUSAL_REGISTRY % "execution_inventory.json is absent from the retained run root"]
    try:
        with open(inv_path, "rb") as fh:
            inv_bytes = fh.read()
        rows = json.loads(inv_bytes.decode("utf-8"))
    except Exception as exc:
        return [], [_REFUSAL_REGISTRY % ("execution_inventory.json could not be parsed (%s)" % type(exc).__name__)]
    if hashlib.sha256(inv_bytes).hexdigest() != attested_inventory_digest:
        return [], [_REFUSAL_REGISTRY %
                    "execution_inventory.json is not the inventory the launch attestation recorded"]
    if not isinstance(rows, list):
        return [], [_REFUSAL_REGISTRY % "execution_inventory.json is not a list of inventory rows"]
    row = None
    for entry in rows:
        if isinstance(entry, dict) and entry.get("path") == "verification/mutations.py":
            row = entry
            break
    if row is None or not isinstance(row.get("sha256"), str):
        return [], [_REFUSAL_REGISTRY %
                    "the bound inventory carries no verified row for verification/mutations.py"]
    if hashlib.sha256(source).hexdigest() != row["sha256"]:
        return [], [_REFUSAL_REGISTRY %
                    "the retained verification/mutations.py is not the bound candidate's registry"]

    by_grammar, grammar_problem = registry_by_grammar(source)
    by_parser, parser_problem = _registry_by_parser(source)
    if grammar_problem:
        return [], [_REFUSAL_REGISTRY % grammar_problem]
    if parser_problem:
        return [], [_REFUSAL_REGISTRY % parser_problem]
    if by_grammar != by_parser:
        # Two derivations, one accepted format. A consumer without a Python parser implements the
        # grammar; this consumer implements both and refuses when they disagree, so "the PowerShell
        # consumer would have read this differently" is a refusal here rather than a divergence later.
        return [], [_REFUSAL_REGISTRY %
                    ("the strict grammar and the parser disagree on the registry: grammar %s, parser %s"
                     % (by_grammar, by_parser))]
    dupes = sorted({k for k in by_grammar if by_grammar.count(k) > 1})
    if dupes:
        return [], ["the mutation registry is ambiguous: MUTATIONS declares %s more than once" % d for d in dupes]
    return sorted("MUT-" + k for k in by_grammar), []


# ---------------------------------------------------------------------------------------------
# THE ACCEPTED REGISTRY FORMAT, normative and shared by every consumer.
#
# A consumer without a Python parser (the PowerShell one) cannot use `ast`. If each consumer read
# the registry by whatever means it had, the two would accept different files and the expected
# control sets could differ without either being wrong. So ONE format is accepted, stated as a line
# grammar any consumer can implement, and a consumer that also has a parser must require the two to
# agree. Comparing derived control sets across consumers would catch a disagreement only when the
# sets happened to differ on the file at hand; this makes the formats identical by construction.
#
#   line 1 of the block : MUTATIONS = {            exactly, at column 0
#   key lines          :     "<key>": {            exactly four spaces, double quotes, then ": {"
#   last line          : }                          exactly, at column 0
#   every other line at the block's own four-space indent must be "}," or "}"; anything else is
#     REFUSED, never skipped - skipping is how a consumer without a parser derives a smaller set
#   lines indented deeper, and blank lines, belong to a value and are ignored
#   <key> is one or more of A-Z a-z 0-9 . _ -
# ---------------------------------------------------------------------------------------------
_KEY_LINE = re.compile(r'^    "([A-Za-z0-9._\-]+)": \{$')
_TOP_LEVEL_LINE = re.compile(r'^    \S')


def registry_by_grammar(source):
    """Return (keys, problem). The normative derivation; see the block comment above."""
    try:
        lines = source.decode("utf-8").replace("\r\n", "\n").split("\n")
    except Exception as exc:
        return [], "verification/mutations.py is not readable as UTF-8 text (%s)" % type(exc).__name__
    opens = [i for i, line in enumerate(lines) if line == "MUTATIONS = {"]
    if len(opens) != 1:
        return [], "verification/mutations.py has no single top-level MUTATIONS assignment"
    start = opens[0]
    closes = [i for i, line in enumerate(lines) if i > start and line == "}"]
    if not closes:
        return [], "the MUTATIONS block is not closed by a brace at column 0"
    keys = []
    for number, line in enumerate(lines[start + 1:closes[0]], start + 2):
        if not _TOP_LEVEL_LINE.match(line):
            continue                                   # deeper-indented, blank: part of a value
        match = _KEY_LINE.match(line)
        if match:
            keys.append(match.group(1))
            continue
        if line in ("    },", "    }"):
            continue                                   # the close of a key's own block, at its indent
        # A line at the block's own indent that is not an accepted key and not a close. It might be
        # a key in an unsupported form - a single-quoted key, say. SILENTLY SKIPPING IT would make a
        # consumer without a Python parser derive a SMALLER registry than one with a parser, and a
        # smaller registry means fewer expected controls, which is the completeness hole this whole
        # check exists to close. So it is refused by name.
        return [], ("verification/mutations.py line %d is not an accepted MUTATIONS entry: %r"
                    % (number, line[:60]))
    if not keys:
        return [], "the MUTATIONS block declares no keys in the accepted format"
    return keys, ""


def _registry_by_parser(source):
    """The same set, derived with ast. PARSED, NEVER IMPORTED."""
    try:
        tree = ast.parse(source.decode("utf-8"))
    except Exception as exc:
        return [], "verification/mutations.py could not be parsed (%s)" % type(exc).__name__
    assigns = [n for n in tree.body
               if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "MUTATIONS" for t in n.targets)]
    if len(assigns) != 1:
        return [], "verification/mutations.py has no single top-level MUTATIONS assignment"
    value = assigns[0].value
    if not isinstance(value, ast.Dict):
        return [], "MUTATIONS is not a dictionary of string-literal keys"
    keys = []
    for k in value.keys:
        if not isinstance(k, ast.Constant) or not isinstance(k.value, str):
            return [], "MUTATIONS is not a dictionary of string-literal keys"
        keys.append(k.value)
    return keys, ""


def _is_path_list(value):
    """A list of strings, or nothing. Malformed evidence is refused by name, never coerced or sorted."""
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _mutation_control_problems(step):
    """The assertion and exit evidence a mutation control must carry (v0_7 section 3.3)."""
    found = []
    if step.get("mutation_applied") is not True:
        found.append("mutation_applied is %r" % step.get("mutation_applied"))
    tests = step.get("tests") if isinstance(step.get("tests"), dict) else {}
    if not tests:
        found.append("no test record")
        return found
    exit_code = tests.get("exit")
    # The contract's permitted target exit is EXACTLY integer 1: the target failed by assertion, so the
    # test process reports one failure and nothing else. A missing exit, 2, -9 or "1" are all refused.
    # bool is excluded explicitly because True == 1 in Python.
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        found.append("the target exit is %r (%s), not the permitted integer 1"
                     % (exit_code, type(exit_code).__name__))
    elif exit_code != PERMITTED_MUTATION_TARGET_EXIT:
        found.append("the target exited %d under the mutation, not the permitted %d"
                     % (exit_code, PERMITTED_MUTATION_TARGET_EXIT))
    by_outcome = tests.get("by_outcome") if isinstance(tests.get("by_outcome"), dict) else {}
    if set(by_outcome) != {"FAIL"}:
        found.append("target outcomes %s, not FAIL only" % (sorted(by_outcome) or "none recorded"))
    not_ok = tests.get("not_ok") if isinstance(tests.get("not_ok"), list) else []
    if not not_ok:
        found.append("no failing target recorded")
    for entry in not_ok:
        entry = entry if isinstance(entry, dict) else {}
        if entry.get("result") != "FAIL":
            found.append("target %s recorded %r, not FAIL" % (entry.get("id"), entry.get("result")))
        elif not str(entry.get("exception", "")).startswith("AssertionError"):
            found.append("target %s did not fail by assertion" % entry.get("id"))
    return found


def gate(run_root):
    """Return the decision. TOTAL: it never raises, on any invocation path.

    The CLI in main() also guards, but a harness or another consumer that calls gate() directly must
    get the same non-green behaviour from malformed evidence. A decision that raises is a decision
    that did not happen, and a caller which treats "no exception" as "qualified" would read it green.
    """
    try:
        return _gate(run_root)
    except Exception as exc:
        return {"qualifying": False,
                "reasons": ["the qualification decision itself failed (%s: %s): refused"
                            % (type(exc).__name__, exc)],
                "expected_controls": [], "observed_controls": []}


def _gate(run_root):
    reasons = []
    rec, raw = {}, {}
    for key, name, sub, absent_label, _field_label in RECORDS:
        path = os.path.join(run_root, sub, name) if sub else os.path.join(run_root, name)
        if not os.path.isfile(path):
            reasons.append("final evidence missing: no %s in the run" % absent_label)
            continue
        try:
            with open(path, "rb") as fh:
                data = fh.read()
            parsed = json.loads(data.decode("utf-8"))
        except Exception as exc:                                   # malformed is a refusal, never an exception
            reasons.append("final evidence unreadable: %s could not be parsed as JSON (%s): refused"
                           % (absent_label, type(exc).__name__))
            continue
        if not isinstance(parsed, dict):
            reasons.append("final evidence unreadable: %s is not a JSON object: refused" % absent_label)
            continue
        rec[key], raw[key] = parsed, data
    if reasons:                                                    # section 1: no field or equality check is made
        return {"qualifying": False, "reasons": reasons, "expected_controls": [], "observed_controls": []}

    def _sub(parent, field):
        holder = rec.get(parent, {})
        value = holder.get(field) if isinstance(holder, dict) else None
        return value if isinstance(value, dict) else {}

    rec["X"] = _sub("M", "execution")
    rec["I"] = _sub("M", "identity")
    rec["C"] = _sub("M", "cleanup")
    rec["SP"] = _sub("A", "site_prequalification")

    for key, label, fields in REQUIRED:                            # section 2: every absence named, then stop
        holder = rec.get(key)
        for field in fields:
            if not isinstance(holder, dict) or field not in holder or holder[field] is None:
                reasons.append("required binding field %s is absent from the %s: refused before any comparison"
                               % (field, label))

    # section 2 rule (e): the step record's shape and the uniqueness of its ids, before any comparison
    steps = rec["M"].get("steps")
    observed = []
    if "steps" in rec["M"] and rec["M"]["steps"] is not None:
        if (not isinstance(steps, list) or not steps
                or not all(isinstance(s, dict) and isinstance(s.get("id"), str) and s.get("id")
                           and "outcome" in s for s in steps)):
            reasons.append("the coordinator summary's step record is not a list of steps each carrying "
                           "id and outcome")
            steps = None
        else:
            observed = [s["id"] for s in steps]
            for repeated in sorted({i for i in observed if observed.count(i) > 1}):
                reasons.append("the coordinator summary records step id %s more than once: the step record "
                               "is contradictory" % repeated)
    if reasons:                                                    # no comparison on an absent or contradictory value
        return {"qualifying": False, "reasons": reasons, "expected_controls": [], "observed_controls": observed}

    A, P, S, M, X, I, U, C, SP = (rec["A"], rec["P"], rec["S"], rec["M"], rec["X"],
                                  rec["I"], rec["U"], rec["C"], rec["SP"])

    # ---- E1-E18, the requirement's own order and refusal text, unchanged from v0.5 ----------------
    if S["final_exit"] != 0:
        reasons.append("the surface's final exit is %r, not 0: the run did not complete cleanly "
                       "(post-run check, coordinator or cleanup)" % S["final_exit"])
    if S["coordinator_exit"] != 0:
        reasons.append("the coordinator exited %r" % S["coordinator_exit"])
    if S["post_run_snapshot_unchanged"] is not True or P["unchanged"] is not True:
        reasons.append("the final snapshot witness reports a change during the run (Q6)")
    if not (A["launch_token"] == P["launch_token"] == S["launch_token"] == X["launch_token"] == U["launch_token"]):
        reasons.append("launch token disagrees across attestation, final witness, surface result, summary and audit")
    if not (A["run_id"] == P["run_id"] == S["run_id"] == M["run_id"] == U["run_id"]):
        reasons.append("run id disagrees across attestation, final witness, surface result, summary and audit")
    if not (A["commit"] == P["commit"] == S["commit"] == I["commit"]):
        reasons.append("the commit disagrees between attestation, final witness, surface result and identity")
    if not (A["inventory_digest"] == P["inventory_digest"] == S["inventory_digest"]):
        reasons.append("the pre-run snapshot inventory digest disagrees between attestation, final witness and surface result")
    if not (A["inventory_digest"] == P["digest_after"] == S["digest_after"]):
        reasons.append("the snapshot inventory digest disagrees between attestation, surface result and final witness")
    if hashlib.sha256(raw["P"]).hexdigest() != S["post_run_witness_sha256"]:
        reasons.append("POST_RUN_SNAPSHOT_CHECK.json differs from the one the surface result sealed over")
    if hashlib.sha256(raw["M"]).hexdigest() != S["summary_sha256"]:
        reasons.append("summary.json differs from the one the surface result sealed over")
    if hashlib.sha256(raw["U"]).hexdigest() != S["source_audit_sha256"]:
        reasons.append("SOURCE_AUDIT.json differs from the one the surface result sealed over")
    if A["snapshot"] != X["root"]:
        reasons.append("the evidence's root is not the attested snapshot")
    if not str(X["entry"]).startswith(str(A["snapshot"])):
        reasons.append("the entry did not execute from the attested snapshot")
    interp = SP.get("interpreter") if isinstance(SP.get("interpreter"), dict) else {}
    prequalified, live = interp.get("site"), X.get("site_dirs")
    if not _is_path_list(prequalified) or not _is_path_list(live):
        reasons.append("the prequalified site list or the recorded site directories are not a list of "
                       "paths: refused rather than compared")
    elif sorted(prequalified) != sorted(live):
        reasons.append("the live site directories differ from the prequalified list")
    if SP.get("refusals"):
        reasons.append("the attestation records startup refusals")
    if not str(X["mode"]).startswith("snapshot-entry"):
        reasons.append("the route recorded a checkout-entry (non-qualifying) run")
    if U["refusals"]:
        reasons.append("the source audit refused (detected after execution): "
                       + "; ".join(str(r) for r in U["refusals"])[:300])
    if not (M["result"] == "PASS" and M["exit_status"] == 0):
        reasons.append("result not PASS/0")

    # ---- E19 transport consistency ONLY. E6 above is the independence check. --------------------
    if I["attested_commit"] != A["commit"]:
        reasons.append("the attested commit did not survive transport to the coordinator summary")

    # ---- E20 the fixed controls, each exactly once, and every recorded step at PASS -------------
    missing, counted, not_passed = [], [], []
    for control in FIXED_CONTROLS:
        n = observed.count(control)
        if n == 0:
            missing.append(control)
        elif n != 1:
            counted.append("%s appears %d times" % (control, n))
    for step in steps:
        if step.get("outcome") != "PASS":
            not_passed.append("%s recorded %r" % (step["id"], step.get("outcome")))
    if missing or counted or not_passed:
        reasons.append("a required control is missing, duplicated or did not pass: "
                       + "; ".join(["missing " + c for c in missing] + counted + not_passed))

    # ---- E21 the COMPLETE applicable mutation-control set, from the bound candidate -------------
    expected_mut, registry_refusals = derive_mutation_registry(run_root, A.get("snapshot"), A.get("inventory_digest"))
    if registry_refusals:
        reasons.extend(registry_refusals)
    else:
        found = []
        by_id = {}
        for step in steps:
            if str(step["id"]).startswith("MUT-"):
                by_id.setdefault(step["id"], []).append(step)
        for control in expected_mut:
            n = observed.count(control)
            if n == 0:
                found.append("missing " + control)
            elif n != 1:
                found.append("%s appears %d times" % (control, n))
        for step_id in sorted(by_id):
            if step_id not in expected_mut:
                found.append("%s is not in the bound registry" % step_id)
                continue
            for problem in _mutation_control_problems(by_id[step_id][0]):
                found.append("%s: %s" % (step_id, problem))
        if found:
            reasons.append("the run's mutation controls are incomplete or unsound: " + "; ".join(found))

    # ---- E22 cleanup, E23 the tree across the run's records -------------------------------------
    if C["outcome"] != "CLEAN":
        reasons.append("cleanup did not report CLEAN: %r" % C["outcome"])
    if not (A["tree"] == I["commit_tree"] == I["working_tree_git_tree"]):
        reasons.append("the candidate tree disagrees between the launch attestation and the coordinator "
                       "summary identity")

    # E24 and E25 were local-task predicates of the Copilot CI task alone. C7 removed the separate
    # PowerShell decision engine, and a predicate enforced by only one consumer would have gone with
    # it. Both are surface-independent, so they belong to the shared decision.
    if I["valid"] is not True:
        reasons.append("the coordinator summary records an invalid identity: %r" % I["valid"])
    if M["route"] != ROUTE_VERSION:
        reasons.append("the evidence is from %r, not the route this decision is written against (%r)"
                       % (M["route"], ROUTE_VERSION))

    return {"qualifying": not reasons, "reasons": reasons,
            "expected_controls": sorted(FIXED_CONTROLS) + expected_mut,
            "observed_controls": observed}


def main():
    if not 2 <= len(sys.argv) <= 3:
        print(__doc__.strip().splitlines()[0])
        return 2
    try:
        decision = gate(sys.argv[1])
    except Exception as exc:                                       # a decision failure is never green
        decision = {"qualifying": False,
                    "reasons": ["the qualification decision itself failed (%s: %s): refused"
                                % (type(exc).__name__, exc)],
                    "expected_controls": [], "observed_controls": []}
    if len(sys.argv) == 3:
        with open(sys.argv[2], "w", encoding="utf-8") as fh:
            json.dump(decision, fh, indent=1)
            fh.write("\n")
    if decision["qualifying"]:
        print("QUALIFYING: every record, binding field, equality and control-completeness check of "
              "CONSUMER_REQUIREMENT_v0_7 sections 1-3 holds.")
        print("  controls expected: " + ", ".join(decision["expected_controls"]))
        print("  controls observed: " + ", ".join(decision["observed_controls"]))
        return 0
    print("NOT QUALIFYING:")
    for reason in decision["reasons"]:
        print("  - " + reason)
    return 1


if __name__ == "__main__":
    sys.exit(main())
