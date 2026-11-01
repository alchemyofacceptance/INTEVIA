# S015 — public crosswalk for PKT-A foundations and PKT-B

**v1.1 · 1 November 2026 · current at main `f829d1b2b714cb282d96a273fbd0887eac9c3522`**

The September v1.0 foundation account is retained below and marked as history. Current PKT-B implementation paths and restrictions are added in §3.

This document ties INTEVIA's public description of the S015 slice to the commits that actually
implemented it, and states the limitations the lineage record already admits. It was written because an
external reviewer, inspecting the public repository in September 2026 without running anything, asked
for exactly this: a crosswalk citing the commits and the admitted limitations, rather than more
doctrine.

For the current position and its limits, see [the README](../README.md#current-position).

The current development practice is **IDOPT (formerly IDOP)** — INTEVIA Development Operating Protocol and Thinking. Historical records and protocol filenames retain IDOP.

Read a path as a place to inspect the claim, not proof that every behaviour in that file has current test coverage.

---

## 1. The foundation commits — September history

| Commit | Date | What it did |
|---|---|---|
| [`859960d`](https://github.com/alchemyofacceptance/INTEVIA/commit/859960d) | 12 Sep 2026 | **PKT-A-2** — migration 0021: the recorded chain and the bitemporal fold, with models, test harness and canonical-form fixtures |
| [`0f0b122`](https://github.com/alchemyofacceptance/INTEVIA/commit/0f0b122) | 12 Sep 2026 | the packet lineage record for that landing |
| [`845b3f0`](https://github.com/alchemyofacceptance/INTEVIA/commit/845b3f0) | 13 Sep 2026 | a docstring correction on 0021 — **no SQL change** |
| [`5eb7783`](https://github.com/alchemyofacceptance/INTEVIA/commit/5eb7783) | 15 Sep 2026 | **PKT-A-3** — migration 0022: the INTEVIA Lineage-Chain, with six contract tests |
| [`b11b246`](https://github.com/alchemyofacceptance/INTEVIA/commit/b11b246) | 15 Sep 2026 | the packet lineage record for the ILC landing |

`b11b246` is the commit the external review inspected.

## 2. What those foundation commits delivered

**PKT-A-2 — the recorded chain and bitemporal fold (0021).** Schema and database-level constraints for
recording state transitions so that history is append-only and the sequence of events is checkable in
the database rather than by convention in application code.

**PKT-A-3 — the INTEVIA Lineage-Chain (0022).** Schema, triggers and functions for four things, not one:

- **actor states** — the recorded states an actor may hold;
- **parts** — the constituent records the chain binds together;
- **L1 commitments** across the twelve governed event tables;
- **identity resolution and severance** — a resolution table with an append-only severance ledger, and
  the reuse and severance guards over it.

Identity and severance are **one limb of the packet**, not the whole of it. Describing 0022 as "the
identity chain" under-describes what landed, and the earlier version of this document did exactly that.

Both are **schema-level**: the rules are installed in PostgreSQL. The accompanying tests exercised them
by direct SQL as well as through the application layer. The old suite is now archived. Current selected foundation tests are `tests/test_s015_0021_negative_direct_sql.py` and `tests/test_s015_0022_contract.py`; they do not inherit the whole old suite's coverage.

## 3. What has changed since the review

### September corrections — historical record

| Commit | Change |
|---|---|
| [`dae2b94`](https://github.com/alchemyofacceptance/INTEVIA/commit/dae2b94) | the repository front door reconciled — it had described the July state while the schema had moved on. **Closed and confirmed by the reviewer** |
| [`4282ebc`](https://github.com/alchemyofacceptance/INTEVIA/commit/4282ebc) | the lineage record's over-claim about the identity split corrected, with the old text retained beside the new. **Closed and confirmed by the reviewer** |
| [`2eb05e4`](https://github.com/alchemyofacceptance/INTEVIA/commit/2eb05e4) | migration 0023 — the canonical form moved by forward migration |
| [`8797e43`](https://github.com/alchemyofacceptance/INTEVIA/commit/8797e43) | migration 0024 — L1 and parts preimages built as compact canonical text |
| [`23aabdec`](https://github.com/alchemyofacceptance/INTEVIA/commit/23aabdec8df202685f19b5744cf41f149a808f9f) | lawful 0022 fixtures and the 0022 contract test brought into the S015 set; the expected resolution schema corrected, and second-layer test expectations replaced by assertions of the specific refusal |
| [`57015b13`](https://github.com/alchemyofacceptance/INTEVIA/commit/57015b13739aaedabf51f792ed0684bae18428e1) | **Change C** — a re-runnable verification route, an authoritative qualification decision separate from the code that produces the evidence, and retained evidence. See [docs/verification](verification/README.md) |

### Current PKT-B mapping

The [detailed PKT-B mapping](architecture/CURRENT_IMPLEMENTATION_CROSSWALK.md#9-pkt-b-organisations-membership-and-offices) lists the current implementation paths, related tests and limits in one place. A test-file reference does not establish coverage of the whole capability.

The [PKT-B Datacron](holocron/datacrons/D-S015-PKT-B-GOVERNED-ORGANISATIONS-MEMBERSHIP-AND-OFFICES.md) supplies the capability boundaries, completed changes and open work. The current route and archive policy are mapped in §6.

## 4. The limitations the earlier lineage record admitted

The table preserves the September findings at `b11b246`. It is historical, not a current assertion that PKT-B delivered no organisation actions. Current office restrictions are above and in the PKT-B Datacron. This documentation pass does not adjudicate closure of all earlier foundation findings.

| | Limitation |
|---|---|
| **U-14** | **There is no working second-layer verification.** The second layer does not perform the check its name implies; the tests assert the specific refusal it raises |
| **BD-1** | **No Organism-as-actor.** The organism cannot act as a party in its own right |
| **U-6** | **The identity-resolution admission policy is unruled.** What may be admitted has not been decided |
| **F-U21l-04** | **The application still connects to the database as a superuser.** The privilege separation the design assumes is not in place |

## 5. Further findings from the September external review

Beyond the four above, the September review recorded these implementation gaps:

- the application object model and the 0022 schema have diverged; no model class was found for the
  identity-resolution table;
- the identity-split guard is installed as a function that does not act;
- the 0022 test mass is not comparable to 0021's;
- the second layer checks presence rather than correspondence — it does not recompute a digest, and a
  decision is outstanding on whether it should.

Two findings about **test expectations** — the identity-resolution schema disagreeing with its own
tests, and second-layer tests expecting output from a function that raises — were corrected at
`23aabdec`. **That September test repair did not close the underlying implementation gaps** listed above. Their later disposition must be read from the applicable finding record, not inferred here.

All nine findings with their September status:
[Report 2 §7](verification/reports/REPORT_2_IMPLEMENTATION_v1_0.md).

## 6. What can be checked today

GitHub continuous integration (CI) is the evidence of record. The [C5 run](https://github.com/alchemyofacceptance/INTEVIA/actions/runs/41308978727) records 126 product tests plus 19 deliberate-defect checks. New tests follow six test admission rules (TAR). The old suite of about 1,800 tests is archived and not run; the current route builds one template database and uses copies in clone mode. The smaller suite is not proven to provide equivalent coverage.

| Current route or record | Repository path |
|---|---|
| GitHub workflow and separate qualification decision | `.github/workflows/s015-verification.yml`; `.github/s015_gate.py` |
| Route and deliberate-defect checks | `verification/route.py`; `verification/mutations.py` |
| Current run requirements | `docs/operations/RUN_REQUIREMENTS.md` |
| Archived suite and reuse restrictions | `archive/tests_2026_10/00_ARCHIVE_RECORD.md` |

See [the verification guide](verification/README.md) for the current process. A pass is scoped to the tested candidate and does not establish release readiness.

### September qualification — historical evidence

The claim recorded in September was: **the S015 tests pass against a live PostgreSQL server, on
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
