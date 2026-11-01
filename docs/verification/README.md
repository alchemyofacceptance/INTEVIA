# Verifying INTEVIA's database foundations

This folder holds the rules INTEVIA's automated verification decision follows, and the reports of the
runs that decision has accepted. It is written to be read without a conversation transcript.

For the current position and its limits, see [the README](../../README.md#current-position).

The current development practice is **IDOPT (formerly IDOP)** — INTEVIA Development Operating Protocol and Thinking. Historical records and protocol filenames retain IDOP.

---

## Start here

| If you want to know… | Read |
|---|---|
| the September design and its historical pass boundary | [Report 1 — Design](reports/REPORT_1_DESIGN_v1_0.md) |
| what the September implementation and review recorded | [Report 2 — Implementation](reports/REPORT_2_IMPLEMENTATION_v1_0.md) |
| the historical September developer-machine result | [Report 3 — Local qualification](reports/REPORT_3_LOCAL_QUALIFICATION_v1_0.md) |
| the historical September GitHub result | [Report 4 — CI qualification](reports/REPORT_4_CI_QUALIFICATION_v1_0.md) |
| the exact decision rules a consumer must apply | **[CONSUMER_REQUIREMENT_v1_2.md](CONSUMER_REQUIREMENT_v1_2.md)** |

## Current suite and evidence

GitHub continuous integration (CI) is the evidence of record for the exact candidate. At SR-H, run [41352656205](https://github.com/alchemyofacceptance/INTEVIA/actions/runs/41352656205) recorded 134 product tests and 19 deliberate-defect checks. These are the registry entries: each deliberately introduces a fault to show that a selected test detects it. New tests follow six test admission rules (TAR).

The old suite of about 1,800 tests was archived on 29 October and is not run. Reuse is selective and must follow the [archive restrictions](../../archive/tests_2026_10/00_ARCHIVE_RECORD.md), including the named test that must never run again. A smaller suite is not proven to give equivalent coverage.

The [current workflow](../../.github/workflows/s015-verification.yml) and [run requirements](../operations/RUN_REQUIREMENTS.md) describe the current route. The reports below retain September evidence; their old test totals and eight-step description are not the current selection.

## The governing contract

**`CONSUMER_REQUIREMENT_v1_2.md` governs.** SHA-256
`19985675fca35f97ca51ba6dec64d5f1311c999a71d26063f502e992a70c1466`.

It is **authoritative over its implementation**: where [the gate](../../.github/s015_gate.py) and this statement differ,
the statement governs and the difference is a defect in the gate. The rules are not to be inferred from
the code.

| File | Status |
|---|---|
| `CONSUMER_REQUIREMENT_v1_2.md` | **governing** |
| `CONSUMER_REQUIREMENT_v1_0.md` | **historical** — the version the first qualifications were taken against. Retained for provenance. It does not govern |

Versions v0.5 to v1.1 were prepared and superseded before or without adoption and are not in the
repository; their history is described in Report 2.

## How a run is qualified

1. The GitHub workflow verifies the exact candidate, prepares Python 3.12 and PostgreSQL 17, and checks that PowerShell 7 and Node.js are available. It extracts the startup code from the committed version and verifies it before running.
2. The route checks its tools, the selected live tests, database ownership and locking, and each deliberate-defect entry. It records outcomes and cleanup. The old eight-step count is historical.
3. A separate decision program reads the records. GitHub requires both a successful route and a successful qualification decision. A route exit of zero alone cannot make the check green.

Ordinary test failures do not enable fail-fast: the selected tests run so related failures can be seen together. Isolation or safety failures can stop execution. Unreached checks cannot count as a complete pass. The [run requirements](../operations/RUN_REQUIREMENTS.md) govern the details.

## The controls

[The gate controls](../../.github/gate_conformance.py) are separate from product tests. The September design recorded **49 permanent controls**: a valid positive that must
be preserved, plus omitted, duplicated, malformed, mistyped, contradictory and incomplete evidence. Each
was exercised through both ways of invoking the gate — 98 decisions in that recorded design.

## September qualified state — historical record

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

For an authorised diagnostic run, follow [the current route guide](../../verification/README.md) and [run requirements](../operations/RUN_REQUIREMENTS.md). The environment needs PostgreSQL and a role able to create and drop disposable databases; the GitHub workflow also requires Python 3.12, PowerShell 7 and Node.js.

The route's self-check step, called SELF, builds one application database from scratch by applying the database changes in order, records it as a run-owned template, then uses copies for its tests and final inspection. This is clone mode. A missing or inconsistent template causes refusal, never a silent fresh-build fallback. Named empty control probes are the approved exception. Ownership checks and cleanup still apply.

Local diagnosis does not replace the qualifying GitHub run. Do not use an old report's invocation to claim current qualification.

## What a passing check does not mean

It is not review, landing, external reproduction, or acceptance of the wider product. It is evidence, at
the checked properties, for the tested file tree and environment. The limitations are stated in Report 1
§5 and Report 2 §8 — including that **nothing in an evidence package is signed**, so a package rewritten
wholly and consistently would satisfy every check.
