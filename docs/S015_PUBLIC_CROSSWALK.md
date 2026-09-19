# S015 — public crosswalk for PKT-A-2 and PKT-A-3

**v1.0 · 19 September 2026**

This document ties INTEVIA's public description of the S015 slice to the commits that actually
implemented it, and states the limitations the lineage record already admits. It was written because an
external reviewer, inspecting the public repository in September 2026 without running anything, asked
for exactly this: a crosswalk citing the commits and the admitted limitations, rather than more
doctrine.

A reader should be able to check every claim below from the repository alone.

---

## 1. The commits

| Commit | Date | What it did |
|---|---|---|
| [`859960d`](https://github.com/alchemyofacceptance/INTEVIA/commit/859960d) | 12 Sep 2026 | **PKT-A-2** — migration 0021: the recorded chain and the bitemporal fold, with models, test harness and canonical-form fixtures |
| [`0f0b122`](https://github.com/alchemyofacceptance/INTEVIA/commit/0f0b122) | 12 Sep 2026 | the packet lineage record for that landing |
| [`845b3f0`](https://github.com/alchemyofacceptance/INTEVIA/commit/845b3f0) | 13 Sep 2026 | a docstring correction on 0021 — **no SQL change** |
| [`5eb7783`](https://github.com/alchemyofacceptance/INTEVIA/commit/5eb7783) | 15 Sep 2026 | **PKT-A-3** — migration 0022: the INTEVIA Lineage-Chain, with six contract tests |
| [`b11b246`](https://github.com/alchemyofacceptance/INTEVIA/commit/b11b246) | 15 Sep 2026 | the packet lineage record for the ILC landing |

`b11b246` is the commit the external review inspected.

## 2. What those commits deliver

**PKT-A-2 — the recorded chain and bitemporal fold (0021).** Schema and database-level constraints for
recording state transitions so that history is append-only and the sequence of events is checkable in
the database rather than by convention in application code.

**PKT-A-3 — the INTEVIA Lineage-Chain (0022).** Schema, triggers and functions establishing the chain
of identity records, including severance and reuse guards, and append-only protection on the severance
table.

Both are **schema-level**: the rules are installed in PostgreSQL. The accompanying tests exercise them
by direct SQL as well as through the application layer.

## 3. What has changed since the review

| Commit | Change |
|---|---|
| [`dae2b94`](https://github.com/alchemyofacceptance/INTEVIA/commit/dae2b94) | the repository front door reconciled — it had described the July state while the schema had moved on. **Closed and confirmed by the reviewer** |
| [`4282ebc`](https://github.com/alchemyofacceptance/INTEVIA/commit/4282ebc) | the lineage record's over-claim about the identity split corrected, with the old text retained beside the new. **Closed and confirmed by the reviewer** |
| [`2eb05e4`](https://github.com/alchemyofacceptance/INTEVIA/commit/2eb05e4) | migration 0023 — the canonical form moved by forward migration |
| [`8797e43`](https://github.com/alchemyofacceptance/INTEVIA/commit/8797e43) | migration 0024 — L1 and parts preimages built as compact canonical text |
| [`23aabdec`](https://github.com/alchemyofacceptance/INTEVIA/commit/23aabdec8df202685f19b5744cf41f149a808f9f) | lawful 0022 fixtures and the 0022 contract test brought into the S015 set; the expected resolution schema corrected, and second-layer test expectations replaced by assertions of the specific refusal |
| [`57015b13`](https://github.com/alchemyofacceptance/INTEVIA/commit/57015b13739aaedabf51f792ed0684bae18428e1) | **Change C** — a re-runnable verification route, an authoritative qualification decision separate from the code that produces the evidence, and retained evidence. See [docs/verification](verification/README.md) |

## 4. The limitations the lineage record admits

These are carried in the lineage record at `b11b246` and remain open. They are stated here because a
crosswalk that omitted them would be the thing the review objected to.

| | Limitation |
|---|---|
| **U-14** | **There is no working second-layer verification.** The second layer does not perform the check its name implies; the tests assert the specific refusal it raises |
| **BD-1** | **No Organism-as-actor.** The organism cannot act as a party in its own right |
| **U-6** | **The identity-resolution admission policy is unruled.** What may be admitted has not been decided |
| **F-U21l-04** | **The application still connects to the database as a superuser.** The privilege separation the design assumes is not in place |

## 5. Findings from the external review that remain open

Beyond the four above, the review returned findings that are not yet closed:

- the application object model and the 0022 schema have diverged; no model class was found for the
  identity-resolution table;
- the identity-split guard is installed as a function that does not act;
- the 0022 test mass is not comparable to 0021's;
- the second layer checks presence rather than correspondence — it does not recompute a digest, and a
  decision is outstanding on whether it should.

Two findings about **test expectations** — the identity-resolution schema disagreeing with its own
tests, and second-layer tests expecting output from a function that raises — were corrected at
`23aabdec`. **The implementation gaps behind them are not closed** and are listed above.

All nine findings with their current status:
[Report 2 §7](verification/reports/REPORT_2_IMPLEMENTATION_v1_0.md).

## 6. What can be checked today

The headline claim an assessor can verify: **the S015 tests pass against a live PostgreSQL server, on
two independent environments, and the result is decided by a program separate from the one producing
the evidence.**

| | |
|---|---|
| Qualified tree | `84aea3aa1d6114ae38c5918e3e066c8d0950a6c4` |
| CI run | [actions/runs/35436023009](https://github.com/alchemyofacceptance/INTEVIA/actions/runs/35436023009) — check `s015` SUCCESS, evidence artifact retained |
| Tests executed | 264 across eight control steps |
| Decision rules | [`CONSUMER_REQUIREMENT_v1_2.md`](verification/CONSUMER_REQUIREMENT_v1_2.md) |

Two of the eight steps deliberately break a database protection and require the corresponding test to
**fail by assertion**, naming the refusal it did not receive — evidence that those negative tests check
a specific protection rather than accepting any error.

**This does not establish that the schema is correct, complete, or free of the defects in §4 and §5.**
It establishes that a defined set of properties is checked, that the check is re-runnable by a third
party, and that the evidence is retained.

---

**Provenance.** Every claim here cites a commit. Superseded documents are retained rather than deleted.
Where a finding was corrected, both the correction and what remains open are named.

**Final authority remains with Carmian Owen, Human Governor.**
