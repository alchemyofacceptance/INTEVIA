# Change C — Implementation report

## Building and testing INTEVIA's verification process

**Report 2 of 4 · v1.0 · 19 September 2026**
**For:** Carmian Owen, independent reviewers and prospective funders
**Programme:** INTEVIA — S015 verification and continuous integration

---

### At a glance

Change C implements a repeatable process for checking INTEVIA's S015 database foundations, both on a
developer's machine and through GitHub's continuous integration service. It brings together the test
route, the recording of evidence, the automated workflow, and the program that decides whether a run
qualifies.

The work also exposed defects in the verification tools themselves. Corrections addressed incomplete
test records, unintended changes to the files under examination, a decision missing from the archive,
and a gate that could accept a run with required controls omitted. Permanent tests were added for each.

**Status.** Merged into the main branch on 19 September 2026 as merge commit
`57015b13739aaedabf51f792ed0684bae18428e1`, from candidate
`faea7d29ae9bd4d7581e6596a3cb7e86318f822f`. The main branch's file tree was verified to equal the
qualified tree `84aea3aa1d6114ae38c5918e3e066c8d0950a6c4`.

---

### 1. What was implemented

| Component | What it contributes |
|---|---|
| **Shared verification route** | runs the declared S015 checks, records outcomes, and checks cleanup |
| **Local launcher and startup code** | prepare an isolated candidate and environment, verify the expected file identities, and start the run |
| **Evidence recorder and source checks** | record test events and detect whether the execution files changed during the run |
| **CI workflow** | runs verification on GitHub for the identified candidate and retains its evidence |
| **Authoritative qualification gate** | reads the retained records and decides whether they meet the declared requirements. A successful process exit alone is insufficient |
| **Gate controls and consumer contract** | state the decision rules and test the gate against valid, incomplete, contradictory and malformed evidence |

Developed on branch `ufund2/change-c-verification-route`. Normal pushes only: no history rewrite, no
force, and no change to the main branch until the authorised merge.

### 1.1 What merging placed on the main branch

**26 files added, 3 modified**, counted from the recorded file list: 21 + 3 + 2 = 26.

- **Verification route and supporting code (21):** `verification/README.md`, `__init__.py`,
  `bootstrap.py`, `mutations.py`, `outcome.py`, `ownership.py`, `parse_results.py`,
  `recorded_unittest.py`, `recording.py`, `route.py`, `run.py`, `runner_captures.json`, four
  `selftest_*.py`, and `isolation/` — `__init__.py`, `runner.py`, `selfcheck_tests.py`, `state.py`;
  plus `requirements-verification.txt`.
- **CI and the decision (3):** `.github/workflows/s015-verification.yml`, `.github/s015_gate.py`,
  `.github/gate_conformance.py`.
- **The consumer contract (2):** `docs/verification/CONSUMER_REQUIREMENT_v1_0.md` and
  `CONSUMER_REQUIREMENT_v1_2.md`. **v1.2 governs; v1.0 is retained as historical material.**
- **Modified (3):** `.gitignore`, and the two mutation-control target tests
  `tests/test_s015_0021_negative_direct_sql.py` and `tests/test_s015_0022_contract.py`.

### 1.2 The six documented milestones

The branch carries 21 commits from the base. Six were added on 18–19 September and are the subject of
this report; the remainder implemented the route in earlier work.

| Commit | Change | Why it matters |
|---|---|---|
| `59da62c` | consolidated two record-writing paths into one | removes the risk that one path silently records less than another |
| `e19f41f` | permanent tests for the previously unexercised recording path | makes the discovered defect observable without a one-off probe |
| `7622cce` | CI runs through the committed startup code; the check becomes a conjunction | connects the automated check to the intended process |
| `cc3f2e8` | the decision is written before the evidence is archived | retains the decision alongside the records it was based on |
| `b18b367` | child processes inherit the intended bytecode-cache location | stops routine cache files modifying the directory under examination |
| `faea7d29ae9bd4d7581e6596a3cb7e86318f822f` | contract v1.2, the typed gate, 49 controls | closes the findings of the independent review |

---

### 2. Defects found in the verification tools, and how

**Incomplete recording on an unexercised path.** Two ways of writing test records had diverged; one
discarded fields including the identity of the test being entered. Every existing control exercised the
other path, so no test could have caught it. A targeted probe exposed it; the correction consolidated
record writing and added a permanent behavioural test plus a structural check that the two callers'
interfaces stay aligned. **A large passing test count does not establish coverage of every execution
path.**

**Cache files changed the watched directory.** Python writes compiled cache files when it loads source.
The coordinator was configured to write them outside the examined snapshot; its child processes were
not, because the setting is a command-line flag and children inherit only environment variables. 80
cache files appeared inside the watched directory and the source check refused the run — the safeguard
working, and the process-launching code needing correction. **The defect had been present all along and
was masked**: the route was already stopping for the recording defects, and both conditions produce the
same exit status.

**The decision was missing from the archive.** An earlier workflow archived the run before the gate
wrote its decision, so the decision existed only in the workflow log. Corrected; confirmed by the
archived file count rising by exactly one.

**Required controls could be omitted without refusal.** The earlier gate accepted the reported PASS
without establishing that the complete required control set had run — including a run started with the
flag that skips mutation controls. The revised gate derives the expected mutation controls from the
candidate's own retained registry, verifying the inventory against its recorded digest and then the
registry's bytes against that inventory, and requires each expected control exactly once with its
assertion evidence.

---

### 3. Independent review, and what it changed

An independent reviewer examined the consumer contract and the gate against synthetic evidence and
returned **four blockers**, each with reproducible counterexamples: the authoritative tables were
incomplete; wrong-type evidence could qualify because truthiness stood in for type validation;
duplicate JSON members and duplicate inventory rows were silently resolved; and mutation evidence could
contradict itself.

All four were reproduced by the Vision Chamber on the same gate and **addressed as field families
rather than as the individual examples**, in contract v1.2 and the gate landed in this candidate. The
permanent control suite grew from 37 to 49, the reviewer's twelve counterexamples among them: 98
decisions across both invocation paths, no failures. Against the uncorrected gate the same 49 controls
produce 30 failures, so the suite discriminates.

A bounded closure check then **closed three of the four findings**. The fourth, a staging issue — the
contract promised type checks before every comparison while some ran later — received a **statement-only
correction**: the contract now describes the stage it performs and names where the other types are
checked, with no change to the gate, the controls or any evidence.

**The Human Governor accepted that correction for this landing. The reviewer has not subsequently
confirmed closure of that finding, and the acceptance is not reviewer clearance.** Every malformed
input the reviewer demonstrated still refuses; no new false qualification and no rejection of a valid
positive was found.

The reviewer's non-blocking observations — entry-path prefix matching by string, an accepted mode
suffix, and three registry rebinding forms — are **recorded as carried limitations, not closed**.

---

### 4. Tests of the verification tools

| Measure | Result | What it means |
|---|---|---|
| Offline self-tests | 101 → **105** | four added tests of verification-tool behaviour; **not** additional S015 product tests |
| Gate controls | **49** | valid evidence plus specified adverse cases |
| Gate decisions | **98**, 0 failures | each control exercised through both ways of invoking the same gate. **Not two independent implementations**, and not 98 database runs |

Where a control changes a record to test one rule, the related digests are updated so a simpler
checksum mismatch cannot mask the intended check. Other controls deliberately test missing or
unreadable material. Not every control is "one changed field with all other checks satisfied".

---

### 5. Authorship

| Work | Author | Executed by |
|---|---|---|
| recorder self-test control | Vision Chamber | Human Governor |
| bytecode-cache correction and its control | **Making Engine** (Copilot Max, GPT 5.4 mini) | Human Governor |
| CI workflow, gate, controls, contract | Vision Chamber | Human Governor; CI execution on GitHub |

The program that produces the evidence and the program that judges it are written by different parties.
Executing supplied code does not make the executor its author. This is a component-level account of the
work in this report, not a complete attribution of every earlier contribution, and it does not by itself
establish correctness.

---

### 6. Work withdrawn, and preserved

The programme initially pursued additional decision implementations in PowerShell and in the startup
code. The Human Governor withdrew both, choosing **one authoritative gate invoked on both paths**. The
unfinished startup-code changes were reverted and the diff and modified files retained; the PowerShell
attempts are likewise preserved as records. **Every defect those attempts produced was in the checking
apparatus; none was in the repository under test.**

---

### 7. What Change C does not establish, and the external-review findings individually

Change C improves verification infrastructure. A green check does not establish that the product model,
database schema, identity protections or second-layer correspondence rules are complete.

An external reviewer inspected the public main branch at `b11b2465989ae266135d7e6627e34e7481daf578` on
15 September 2026 — byte inspection, no tests executed — and returned nine findings. Their current
status, each stated individually:

| Finding | Status | Owner |
|---|---|---|
| Front door pinned to July | **Resolved** by `dae2b94`; confirmed by the reviewer | — |
| Lineage Datacron over-claimed the identity split | **Resolved** by `4282ebc`; confirmed by the reviewer | — |
| **G-1** application models and the 0022 schema diverged | **Remains.** No model class for `core_identityresolution` was found on the main branch. An **ORM gap**, not a test disagreement | PKT-B |
| **G-2** the identity-split guard is a named no-op | **Remains.** An **implementation gap** | PKT-B |
| **G-3** `core_identityresolution` schema and its own tests disagree | **The test mismatch is resolved.** Change B (`23aabdec8df202685f19b5744cf41f149a808f9f`, correction C-1) supplied `credential_link` and corrected the expected resolution schema to four columns. See §7.1 for the landed evidence. What remains under this heading is the ORM gap recorded as G-1 | test half resolved; ORM half → PKT-B under G-1 |
| **G-4** second-layer tests and the second-layer function disagree | **The test mismatch is resolved.** Change B correction C-2 replaced the expectation of successful second-layer output with assertions of the **specific U-14 refusal**, for object and non-object bodies. See §7.1. What remains is that the second layer has no working implementation — carried separately as **U-14** | test half resolved; implementation → U-14 |
| **G-5** 0022 test mass not comparable to 0021 | **Trust posture answered** by Change C: results are now re-runnable and evidenced. The test-mass question itself remains | PKT-B |
| **G-6** the second layer checks presence, not correspondence | **Remains.** An implementation gap, and it needs a decision on whether the second layer becomes a correspondence check | PKT-B, with a Human Governor decision |
| **G-7** headline inventories cannot be confirmed from source text | **Trust posture answered** by Change C. The public crosswalk that states those inventories with their commit identities is outstanding | assessor-facing work |

#### 7.1 What the evidence for G-3 and G-4 rests on, item by item

Each item below is stated for what it establishes and no more.

**The programme record.** Change B (`23aabdec8df202685f19b5744cf41f149a808f9f`) made corrections C-1 —
`credential_link` supplied, the expected resolution schema corrected to four columns — and C-2 — the
expectation of successful second-layer output replaced by assertions of the specific U-14 refusal, for
object and non-object bodies. **This is the source for the content of those corrections.** This report
has not read the corrected test files directly and does not quote their assertions.

**Ancestry.** Change B is the base commit of Change C, so its corrections are ancestors of the
qualified tree. **Ancestry is context, not proof that a change survived**: a later commit could have
altered or reverted either correction.

**The passing suite.** The `S015` control ran 142 tests with no failures on the landed tree, in both the
local and the CI qualification. This establishes that **the S015 set as landed passes in full**. It is
an aggregate count: **it does not identify which tests ran, and does not establish that the C-1 or C-2
assertions remained unchanged.**

**The reuse-refusal mutation.** `MUT-fu-b2-unrelated-reuse-refusal` records, from the landed tree, the
target `test_s015_0022_contract.S0150022ContractTests.test_reinsert_after_severance_is_refused` failing
with `AssertionError: 'S015 R-d: identity reference' not found in 'VERIFICATION MUTATION: an unrelated
refusal …'`. This establishes that **that one test exists on the landed tree and demands a specific
refusal rather than accepting any error.** It is a different test from the ones C-1 and C-2 corrected,
and **it does not test either correction.**

**Reading these together:** the content of C-1 and C-2 rests on the programme record. Confirming that
those specific assertions are present and unchanged on the landed tree would require reading the files,
which is available work and is not claimed here.

**The historical test mismatches under G-3 and G-4 are distinct from the remaining ORM, identity-guard
and second-layer implementation gaps.** Change B corrected what the tests expected; it did not build
the missing model, repair the guard, or implement the second layer. **Landing Change C closes none of
G-1, G-2, G-5, G-6, or the implementation halves of G-3 and G-4.**

Carried separately by the lineage Datacron, and not part of G-1 to G-7: **U-14** no working
second-layer verification; **BD-1** no Organism-as-actor; **U-6** identity-resolution admission policy
unruled; **F-U21l-04** the application still connects as a superuser.

---

### 8. Accepted limitations

Recorded at landing and accepted by the Human Governor:

- the decision program returns the *refusal* exit code when it cannot decide at all, for example when
  given an unwritable output path. **It cannot reach a successful result that way**, because the
  workflow requires the decision record to exist;
- its success message names contract v1.1 while v1.2 governs. The checks it performed are v1.2's; v1.2
  changed no code;
- a provenance record can be shown to **agree** with a run's attestation, not to be **independent** of
  it;
- whole-package authenticity is not addressed: nothing in an evidence package is signed, and a package
  rewritten wholly and consistently would pass every check.

---

**Authority.** Merging is not acceptance of the wider product, external reproduction, or adoption of
any governance amendment. **Final authority remains with Carmian Owen, Human Governor.**
