"""Outcome model for the S015 verification route.

This module holds the shared decision vocabulary and the per-test terminal-outcome derivation used by the parser.
"""
from __future__ import annotations

from collections import Counter


TERMINAL = ("ok", "ERROR", "FAIL", "skipped", "expected failure", "unexpected success")
NON_FAILURE = ("ok", "skipped", "expected failure", "unexpected success")
FAILURE = ("FAIL", "ERROR")


def validate_envelope(summary, collected=None, order=None):
    """Return a list of aggregate accounting problems for one parsed log."""
    problems = []
    if summary.get("ran") is None:
        problems.append("missing test count")
    if summary.get("final") is None:
        problems.append("missing final verdict")
    if collected is not None and order is not None and sorted(order) != sorted(collected):
        problems.append("reported tests do not match the collected identities")
    return problems


def _accounting(mains, subs, blocks):
    if any(m not in TERMINAL for m in mains) or any(st not in TERMINAL for st, _ in subs):
        return False, "unrecognised status %s %s" % (mains, [st for st, _ in subs])
    if not mains and not subs:
        return False, "no terminal outcome reported"
    if any(st in ("ok", "expected failure", "unexpected success") for st, _ in subs):
        return False, "sub-test status %s is not a sub-test outcome" % [st for st, _ in subs]
    sub_events = Counter((st, p.strip()) for st, p in subs if st in FAILURE)
    sub_blocks = Counter((b["kind"], b.get("subtest", "").strip()) for b in blocks if b["phase"] == "subtest")
    if sub_events != sub_blocks:
        return False, "sub-test events %s disagree with sub-test failure blocks %s" % (sorted(sub_events.items()), sorted(sub_blocks.items()))
    unexpected_blocks = sum(1 for b in blocks if b["phase"] == "unexpected")
    if unexpected_blocks != mains.count("unexpected success"):
        return False, "%d unexpected success event(s) but %d UNEXPECTED SUCCESS block(s)" % (mains.count("unexpected success"), unexpected_blocks)
    main_blocks = [b for b in blocks if b["phase"] not in ("subtest", "unexpected")]
    for kind in FAILURE:
        events_n, blocks_n = mains.count(kind), sum(1 for b in main_blocks if b["kind"] == kind)
        if events_n != blocks_n:
            return False, "%d main %s event(s) but %d %s block(s)" % (events_n, kind, blocks_n, kind)
    outside_teardown = [b["phase"] for b in main_blocks if b["phase"] != "teardown"]
    if len(outside_teardown) > 1:
        return False, "more than one failure block outside teardown %s" % outside_teardown
    non_failure = [m for m in mains if m in NON_FAILURE]
    if non_failure:
        if len(non_failure) > 1 or mains[0] not in NON_FAILURE:
            return False, "status %s contradicted by the order or number of status events %s" % (non_failure, mains)
        if sub_events:
            return False, "status %s contradicted by sub-test failure event(s) %s" % (mains[0], sorted(sub_events.elements()))
        if outside_teardown:
            return False, "status %s contradicted by %s block(s)" % (mains[0], outside_teardown)
        return True, "one terminal status" if len(mains) == 1 else "terminal status then %d teardown event(s)" % (len(mains) - 1)
    if not mains:
        return True, "sub-test outcomes only"
    return True, "one failure status" if len(mains) == 1 else "failure status then %d teardown event(s)" % (len(mains) - 1)


def _result_from_blocks(mains, subs, blocks):
    body_blocks = [x for x in blocks if x["phase"] == "body"]
    sub_blocks = [x for x in blocks if x["phase"] == "subtest"]
    setup_blocks = [x for x in blocks if x["phase"] == "setup"]
    unclassified_blocks = [x for x in blocks if x["phase"] == "unclassified"]
    if setup_blocks:
        return "NOT REACHED (setup error)"
    if body_blocks:
        return body_blocks[0]["kind"]
    if unclassified_blocks:
        return "UNCLASSIFIED"
    if "skipped" in mains or (not mains and subs and all(st == "skipped" for st, _ in subs)):
        return "skipped"
    if sub_blocks:
        return "SUBTEST FAILURES"
    if "ok" in mains:
        return "ok"
    return "NO BODY STATUS REPORTED"


def derive(mains, subs, blocks):
    """Return the terminal-outcome verdict and its projected result label for one test."""
    lawful, reason = _accounting(mains, subs, blocks)
    return {"lawful": lawful, "reason": reason, "result": _result_from_blocks(mains, subs, blocks)}
