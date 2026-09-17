"""S015 verification route - parse a Django 'manage.py test -v 2' log (r5).

Lineage: u1r4_parse_results.py (UFUND-1/UFUND-2 route), unchanged in its outcome model: per-test body, sub-test, setup and
teardown outcomes, every total reconciled against the runner's own summary, a recognised final verdict required, and
database freshness taken only from lifecycle evidence in the log.

r5 (UFUND-2 Change C, finding C-P1): unittest's verbose output prints a test whose method has a docstring on two lines -
the test header alone, then the docstring's first line followed by " ... <status>". r4 did not recognise the second line,
so such a test's status was lost and the log did not reconcile. r5 joins a header line that carries no " ..." to the
following description line when, and only when, that line carries " ..."; the status is what follows its last " ... ".
A header with no such following line is left alone, so its result stays missing and the log stays unreconciled.

Usage: python -m verification.parse_results <log> <json_out> [<collected_ids_json>]   exit 0 reconciled, 3 not
"""
import json
import re
import sys
from collections import OrderedDict

HEADER_ALONE = re.compile(r"^(?P<indent>\s*)(?P<header>test\w+ \([\w\.]+\)(?: \(.*\))?)\s*$")


def join_description_lines(lines):
    """Yield log lines with each (header, description ... status) pair joined into one r4-style status line."""
    i = 0
    while i < len(lines):
        line = lines[i]
        m = HEADER_ALONE.match(line)
        if m and " ..." not in line and i + 1 < len(lines):
            nxt = lines[i + 1]
            if " ..." in nxt and not HEADER_ALONE.match(nxt):
                before, sep, status = nxt.rpartition(" ...")
                yield m.group("indent") + m.group("header") + " ..." + status
                i += 2
                continue
        yield line
        i += 1


def parse(text, collected=None, log_path=""):
    SEP = "=" * 70
    STATUS = r"(ok|ERROR|FAIL|skipped.*|expected failure|unexpected success)"
    line_re = re.compile(r"^(?P<indent>\s*)(?:.*?\.\.\.)?(?P<name>test\w+) \((?P<id>[\w\.]+)\)(?P<params> \(.*\))? \.\.\.(?: (?P<status>" + STATUS + r"))?\s*$")

    events = OrderedDict()      # test id -> list of (kind, status)
    order = []
    for raw in join_description_lines(text.splitlines()):
        # a status line can be glued to migration output ("Applying ...OK"); start at the first test token that begins
        # the line, follows only indentation, or follows a migration's "..." / "OK"
        cand, indent = None, ""
        for mt in re.finditer(r"test\w+ \([\w\.]+\)", raw):
            prefix = raw[:mt.start()]
            if prefix.strip() == "":
                cand, indent = raw[mt.start():], prefix
                break
            if prefix.rstrip().endswith(("...", "OK")):
                cand = raw[mt.start():]
                break
        m = line_re.match(indent + cand) if cand is not None else None
        if not m:
            continue
        if not m.group("status"):
            if m.group("id") not in events:
                events[m.group("id")] = []; order.append(m.group("id"))
            continue
        tid = m.group("id")
        if tid not in events:
            events[tid] = []; order.append(tid)
        kind = "subtest" if (m.group("params") or m.group("indent")) else "main"
        st = m.group("status")
        events[tid].append((kind, "skipped" if st.startswith("skipped") else st))

    # failure blocks
    body_part = text.split("\n" + "-" * 70 + "\nRan ")[0]
    blocks = body_part.split("\n" + SEP + "\n")[1:]
    head_re = re.compile(r"^(ERROR|FAIL): (test\w+) \(([\w\.]+)\)( \(.*\))?\s*$")
    per = OrderedDict((t, {"blocks": []}) for t in order)
    unmatched_blocks = 0
    for b in blocks:
        head = b.split("\n", 1)[0]
        m = head_re.match(head)
        if not m:
            unmatched_blocks += 1
            continue
        kind, tid, params = m.group(1), m.group(3), m.group(4)
        if params:
            phase = "subtest"
        elif "couldn't be flushed" in b or "\\management\\commands\\flush.py" in b or "/management/commands/flush.py" in b:
            phase = "teardown"
        elif re.search(r'", line \d+, in (tearDown|tearDownClass|_fixture_teardown|_post_teardown)\b', b):
            phase = "teardown"
        elif re.search(r'", line \d+, in (setUp|setUpClass|_fixture_setup|_pre_setup)\b', b):
            phase = "setup"
        elif re.search(r'", line \d+, in ' + re.escape(m.group(2)) + r"\b", b):
            phase = "body"
        else:
            phase = "unclassified"
        exc = re.findall(r"(?m)^([A-Za-z_][\w\.]*(?:Error|Exception|Failure)): (.*)$", b)
        last = exc[-1] if exc else ("", "")
        per.setdefault(tid, {"blocks": []})["blocks"].append(
            {"kind": kind, "phase": phase, "subtest": (params or "").strip(), "exception": last[0], "message": last[1][:300]})

    summary = {}
    m = re.search(r"(?m)^Ran (\d+) tests? in ", text); summary["ran"] = int(m.group(1)) if m else None
    m = re.search(r"(?m)^(OK|FAILED)(?: \((.*)\))?\s*$", text)
    summary["final"] = m.group(0).strip() if m else None
    counts = dict(re.findall(r"(\w+)=(\d+)", m.group(2) or "")) if m and m.group(2) else {}
    summary["failures"] = int(counts.get("failures", 0)); summary["errors"] = int(counts.get("errors", 0))
    summary["skipped"] = int(counts.get("skipped", 0))
    m = re.search(r"(?m)^EXIT_STATUS: (-?\d+)", text); summary["exit"] = int(m.group(1)) if m else None

    first_status_pos = None
    for mt in re.finditer(r"(?m)^.*test\w+ \([\w\.]+\).* \.\.\.", text):
        first_status_pos = mt.start(); break
    created_m = re.search(r"(?m)^Creating test database for alias .*$", text)
    kept_m = re.search(r"(?m)^(Using existing test database for alias|Got an error creating the test database).*$", text)
    lifecycle = {
        "creation_line": created_m.group(0) if created_m else None,
        "creation_before_first_test": bool(created_m and first_status_pos is not None and created_m.start() < first_status_pos),
        "existing_database_line": kept_m.group(0) if kept_m else None,
    }
    tests = []
    first_teardown_failure_index = None
    for i, tid in enumerate(order):
        bl = per.get(tid, {"blocks": []})["blocks"]
        ev = events.get(tid, [])
        body_blocks = [x for x in bl if x["phase"] == "body"]
        sub_blocks = [x for x in bl if x["phase"] == "subtest"]
        td = [x for x in bl if x["phase"] == "teardown"]
        su = [x for x in bl if x["phase"] == "setup"]
        un = [x for x in bl if x["phase"] == "unclassified"]
        mains = [s for k, s in ev if k == "main"]
        if su:
            body = "NOT REACHED (setup error)"
        elif body_blocks:
            body = body_blocks[0]["kind"]
        elif un:
            body = "UNCLASSIFIED"
        elif "skipped" in mains:
            body = "skipped"
        elif sub_blocks:
            body = "SUBTEST FAILURES"
        elif "ok" in mains:
            body = "ok"
        else:
            body = "NO BODY STATUS REPORTED"
        if i == 0:
            if lifecycle["creation_before_first_test"] and not lifecycle["existing_database_line"]:
                isolation = "database created for this run before this test (lifecycle line); no earlier test"
            else:
                isolation = "freshness not established by the log (no creation line before the first test, or an existing database was used)"
        elif first_teardown_failure_index is not None:
            isolation = "not established (an earlier teardown failed)"
        else:
            isolation = "not established by the log (requires an isolation checkpoint)"
        if td and first_teardown_failure_index is None:
            first_teardown_failure_index = i
        tests.append({"id": tid, "position": i + 1, "body": body, "body_exception": (body_blocks[0]["exception"] + ": " + body_blocks[0]["message"]) if body_blocks else "",
                      "subtest_failures": len([x for x in sub_blocks if x["kind"] == "FAIL"]), "subtest_errors": len([x for x in sub_blocks if x["kind"] == "ERROR"]),
                      "setup_errors": len(su), "teardown_errors": len(td), "unclassified_entries": len(un),
                      "teardown_exception": (td[0]["exception"] + ": " + td[0]["message"]) if td else "", "isolation": isolation, "status_events": ev})

    all_blocks = [x for t in per.values() for x in t["blocks"]]
    verdict_present = summary["final"] is not None and summary["ran"] is not None
    recon = OrderedDict()
    recon["recognised final verdict present"] = (verdict_present, summary["final"], "OK or FAILED line")
    recon["ran equals distinct tests reported"] = (summary["ran"] == len(order), summary["ran"], len(order))
    recon["error entries equal summary errors"] = (len([x for x in all_blocks if x["kind"] == "ERROR"]) == summary["errors"], len([x for x in all_blocks if x["kind"] == "ERROR"]), summary["errors"])
    recon["failure entries equal summary failures"] = (len([x for x in all_blocks if x["kind"] == "FAIL"]) == summary["failures"], len([x for x in all_blocks if x["kind"] == "FAIL"]), summary["failures"])
    recon["skipped equals summary skipped"] = (sum(1 for t in tests if t["body"] == "skipped") == summary["skipped"], sum(1 for t in tests if t["body"] == "skipped"), summary["skipped"])
    recon["every failure block matched to a reported test"] = (unmatched_blocks == 0 and all(t in events for t in per), unmatched_blocks, 0)
    recon["status line exit and log exit agree"] = (summary["exit"] is not None and ((summary["final"] or "").startswith("OK")) == (summary["exit"] == 0), summary["exit"], summary["final"])
    if collected is not None:
        recon["reported tests equal collected identities"] = (sorted(order) == sorted(collected), len(order), len(collected))
    numerically_reconciled = all(v[0] for v in recon.values())
    unclassified_entries = len([x for x in all_blocks if x["phase"] == "unclassified"])
    phase_classification_complete = unclassified_entries == 0
    reconciled = numerically_reconciled and verdict_present

    phase_totals = OrderedDict((p, len([x for x in all_blocks if x["phase"] == p])) for p in ("body", "subtest", "setup", "teardown", "unclassified"))
    body_totals = OrderedDict()
    for t in tests:
        body_totals[t["body"]] = body_totals.get(t["body"], 0) + 1
    out = {"log": log_path, "summary": summary, "lifecycle": lifecycle,
           "verdict_present": verdict_present, "numerically_reconciled": numerically_reconciled,
           "phase_classification_complete": phase_classification_complete, "unclassified_entries": unclassified_entries, "reconciliation": {k: {"ok": v[0], "observed": v[1], "reference": v[2]} for k, v in recon.items()},
           "reconciled": reconciled, "entry_totals_by_phase": phase_totals, "test_totals_by_body_outcome": body_totals,
           "tests_with_teardown_errors": sum(1 for t in tests if t["teardown_errors"]),
           "first_teardown_failure_position": (first_teardown_failure_index + 1) if first_teardown_failure_index is not None else None,
           "tests": tests}
    return out


def report_lines(out):
    summary, recon, lifecycle, reconciled = out["summary"], out["reconciliation"], out["lifecycle"], out["reconciled"]
    phase_classification_complete, unclassified_entries = out["phase_classification_complete"], out["unclassified_entries"]
    phase_totals, body_totals, tests = out["entry_totals_by_phase"], out["test_totals_by_body_outcome"], out["tests"]
    verdict_present = out["verdict_present"]
    lines = []
    lines.append("PARSE: runner summary      : ran=%s %s exit=%s" % (summary["ran"], summary["final"], summary["exit"]))
    for k, v in recon.items():
        lines.append("PARSE: reconcile %-45s %s (observed %s, reference %s)" % (k, "OK" if v["ok"] else "MISMATCH", v["observed"], v["reference"]))
    lines.append("PARSE: verdict present     : %s" % verdict_present)
    lines.append("PARSE: NUMERICALLY RECONCILED (with verdict): %s" % reconciled)
    lines.append("PARSE: PHASE CLASSIFICATION COMPLETE: %s (unclassified entries %d)" % (phase_classification_complete, unclassified_entries))
    lines.append("PARSE: lifecycle           : creation line %s; before first test %s; existing-database line %s" % (bool(lifecycle["creation_line"]), lifecycle["creation_before_first_test"], bool(lifecycle["existing_database_line"])))
    lines.append("PARSE: entries by phase    : " + ", ".join("%s=%d" % kv for kv in phase_totals.items()))
    lines.append("PARSE: tests by body       : " + ", ".join("%s=%d" % kv for kv in body_totals.items()))
    lines.append("PARSE: tests with teardown errors: %d; first at position %s" % (out["tests_with_teardown_errors"], out["first_teardown_failure_position"]))
    for t in tests:
        lines.append("PARSE: %3d %-55s body=%-22s sub F/E=%d/%d setup=%d teardown=%d | %s" % (t["position"], t["id"][-55:], t["body"], t["subtest_failures"], t["subtest_errors"], t["setup_errors"], t["teardown_errors"], t["isolation"]))

    return lines


def main(argv):
    log_path, out_path = argv[1], argv[2]
    collected = None
    if len(argv) > 3:
        with open(argv[3], encoding="utf-8") as f:
            collected = json.load(f)["ids"]
    text = open(log_path, encoding="utf-8").read()
    out = parse(text, collected, log_path)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    for line in report_lines(out):
        print(line)
    return 0 if out["reconciled"] else 3


if __name__ == "__main__":
    sys.exit(main(sys.argv))
