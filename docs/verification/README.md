# Verifying INTEVIA's database foundations

This folder holds the rules INTEVIA's automated verification decision follows, and the reports of the
runs that decision has accepted. It is written to be read without a conversation transcript.

---

## Start here

| If you want to know… | Read |
|---|---|
| why this exists and what a passing check means | [Report 1 — Design](reports/REPORT_1_DESIGN_v1_0.md) |
| what was built, what broke, and what is still open | [Report 2 — Implementation](reports/REPORT_2_IMPLEMENTATION_v1_0.md) |
| the result on a developer machine, with identities | [Report 3 — Local qualification](reports/REPORT_3_LOCAL_QUALIFICATION_v1_0.md) |
| the result in continuous integration, with identities | [Report 4 — CI qualification](reports/REPORT_4_CI_QUALIFICATION_v1_0.md) |
| the exact decision rules a consumer must apply | **[CONSUMER_REQUIREMENT_v1_2.md](CONSUMER_REQUIREMENT_v1_2.md)** |

## The governing contract

**`CONSUMER_REQUIREMENT_v1_2.md` governs.** SHA-256
`19985675fca35f97ca51ba6dec64d5f1311c999a71d26063f502e992a70c1466`.

It is **authoritative over its implementation**: where `.github/s015_gate.py` and this statement differ,
the statement governs and the difference is a defect in the gate. The rules are not to be inferred from
the code.

| File | Status |
|---|---|
| `CONSUMER_REQUIREMENT_v1_2.md` | **governing** |
| `CONSUMER_REQUIREMENT_v1_0.md` | **historical** — the version the first qualifications were taken against. Retained for provenance. It does not govern |

Versions v0.5 to v1.1 were prepared and superseded before or without adoption and are not in the
repository; their history is described in Report 2.

## How a run is qualified

1. A **launcher** (locally) or the **workflow** (in CI) rebuilds the candidate, verifies the file tree
   and the committed startup code, prepares an environment, and runs the verification route.
2. The **route** runs eight control steps against a live PostgreSQL server and writes its records.
3. The **gate** — `.github/s015_gate.py` — reads those records afterwards and decides.

**A route exit of 0 never qualifies a run.** Every path reporting qualification must invoke the gate and
require its successful decision. A green CI check is the conjunction of both.

## The controls

`.github/gate_conformance.py` holds **49 permanent controls** over the gate: a valid positive that must
be preserved, plus omitted, duplicated, malformed, mistyped, contradictory and incomplete evidence. Each
is exercised through both ways of invoking the gate — 98 decisions.

## Current qualified state

| | |
|---|---|
| Merge commit on `main` | [`57015b13739aaedabf51f792ed0684bae18428e1`](https://github.com/alchemyofacceptance/INTEVIA/commit/57015b13739aaedabf51f792ed0684bae18428e1) |
| Qualified candidate | [`faea7d29ae9bd4d7581e6596a3cb7e86318f822f`](https://github.com/alchemyofacceptance/INTEVIA/commit/faea7d29ae9bd4d7581e6596a3cb7e86318f822f) |
| Qualified tree | `84aea3aa1d6114ae38c5918e3e066c8d0950a6c4` — verified equal to `main`'s tree after merge |
| CI run | [actions/runs/35436023009](https://github.com/alchemyofacceptance/INTEVIA/actions/runs/35436023009) — 118s, check `s015` SUCCESS |
| Pull request | [#3](https://github.com/alchemyofacceptance/INTEVIA/pull/3), merged 2026-09-19 |
| Tests executed | 264 across eight control steps, on both surfaces |

## Running it yourself

```
pip install -r requirements-verification.txt
```

The route needs a live PostgreSQL server and credentials for a role that can create and drop databases.
It creates disposable databases, proves it only destroys ones it created, and reports cleanup. See
Report 1 §1 for the step sequence and Report 3 §4 for a recorded environment.

## What a passing check does not mean

It is not review, landing, external reproduction, or acceptance of the wider product. It is evidence, at
the checked properties, for the tested file tree and environment. The limitations are stated in Report 1
§5 and Report 2 §8 — including that **nothing in an evidence package is signed**, so a package rewritten
wholly and consistently would satisfy every check.
