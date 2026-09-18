"""S015 qualification decision — CONSUMER_REQUIREMENT_v0_5.md sections 1-3, exactly.

    Author  : Vision Chamber (Claude Opus 5), UFUND-4, 18 September 2026
    Against : CONSUMER_REQUIREMENT_v0_5.md, 11,618 B,
              sha256 995073dbf74a8722dbdb03a55d68acba29893534360e5e8e5364f903a3aafd20
    Status  : CONSUMER-SIDE. It decides; it implements nothing in the repository and is not repository code.

    python s015_gate.py RUN_ROOT [OUT_JSON]
      exit 0  qualifying
      exit 1  not qualifying (every refusal named on stdout and in OUT_JSON)

Order is the requirement's and is not an optimisation to be reordered:
  1. all five records present            -> on any absence, refuse; NO field or equality check is made
  2. every required binding field present -> on any absence, refuse; NO equality check is made
  3. the equality checks E1-E18
A record that will not parse is a named refusal at step 1, never an exception: a parsing failure must not be
able to produce a qualifying outcome (Human Governor, 18 September 2026).
"""
import hashlib
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
     ("run_id", "launch_token", "commit", "inventory_digest", "snapshot", "site_prequalification")),
    ("P", "final snapshot witness (POST_RUN_SNAPSHOT_CHECK.json)",
     ("run_id", "launch_token", "commit", "inventory_digest", "unchanged", "digest_after")),
    ("S", "completed surface result (SURFACE_RESULT.json)",
     ("run_id", "launch_token", "commit", "inventory_digest", "digest_after", "post_run_snapshot_unchanged",
      "post_run_witness_sha256", "coordinator_exit", "summary_sha256", "source_audit_sha256", "final_exit")),
    ("M", "coordinator summary (summary.json)", ("route", "run_id", "result", "exit_status", "execution", "identity")),
    ("X", "coordinator summary execution record (summary.json execution)", ("launch_token", "root", "entry", "mode")),
    ("I", "coordinator summary identity record (summary.json identity)", ("commit", "attested_commit")),
    ("U", "source audit (SOURCE_AUDIT.json)", ("run_id", "launch_token", "refusals")),
)


def _digest(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def gate(run_root):
    """Return {'qualifying': bool, 'reasons': [str, ...]}. Never raises on evidence content."""
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
        return {"qualifying": False, "reasons": reasons}

    rec["X"] = rec["M"].get("execution") if isinstance(rec["M"].get("execution"), dict) else {}
    rec["I"] = rec["M"].get("identity") if isinstance(rec["M"].get("identity"), dict) else {}

    for key, label, fields in REQUIRED:                            # section 2: every absence named, then stop
        holder = rec.get(key)
        for field in fields:
            if not isinstance(holder, dict) or field not in holder or holder[field] is None:
                reasons.append("required binding field %s is absent from the %s: refused before any comparison"
                               % (field, label))
    if reasons:                                                    # no comparison on an absent value, ever
        return {"qualifying": False, "reasons": reasons}

    A, P, S, M, X, I, U = (rec["A"], rec["P"], rec["S"], rec["M"], rec["X"], rec["I"], rec["U"])

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
    if A["commit"] != I["attested_commit"]:
        reasons.append("the attested commit disagrees between attestation and identity")
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
    prequal = A["site_prequalification"] if isinstance(A["site_prequalification"], dict) else {}
    interp = prequal.get("interpreter") if isinstance(prequal.get("interpreter"), dict) else {}
    if sorted(interp.get("site") or []) != sorted(X.get("site_dirs") or []):
        reasons.append("the live site directories differ from the prequalified list")
    if prequal.get("refusals"):
        reasons.append("the attestation records startup refusals")
    if not str(X["mode"]).startswith("snapshot-entry"):
        reasons.append("the route recorded a checkout-entry (non-qualifying) run")
    if U["refusals"]:
        reasons.append("the source audit refused (detected after execution): "
                       + "; ".join(str(r) for r in U["refusals"])[:300])
    if not (M["result"] == "PASS" and M["exit_status"] == 0):
        reasons.append("result not PASS/0")

    return {"qualifying": not reasons, "reasons": reasons}


def main():
    if not 2 <= len(sys.argv) <= 3:
        print(__doc__.strip().splitlines()[0])
        return 2
    try:
        decision = gate(sys.argv[1])
    except Exception as exc:                                       # a decision failure is never green
        decision = {"qualifying": False,
                    "reasons": ["the qualification decision itself failed (%s: %s): refused"
                                % (type(exc).__name__, exc)]}
    if len(sys.argv) == 3:
        with open(sys.argv[2], "w", encoding="utf-8") as fh:
            json.dump(decision, fh, indent=1)
            fh.write("\n")
    if decision["qualifying"]:
        print("QUALIFYING: every record, binding field and equality of CONSUMER_REQUIREMENT_v0_5 sections 1-3 holds.")
        return 0
    print("NOT QUALIFYING:")
    for reason in decision["reasons"]:
        print("  - " + reason)
    return 1


if __name__ == "__main__":
    sys.exit(main())
