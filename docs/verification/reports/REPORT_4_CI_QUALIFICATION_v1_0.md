# Change C — CI qualification report

## Automated verification of the candidate on GitHub

**Report 4 of 4 · v1.0 · 19 September 2026**
**For:** Carmian Owen, independent reviewers and prospective funders
**Platform:** GitHub Actions continuous integration

---

### At a glance

The GitHub Actions check for Change C candidate `faea7d29ae9bd4d7581e6596a3cb7e86318f822f` completed
successfully in 118 seconds. Continuous integration, or *CI*, runs an automated verification process
against an identified version of the repository. In this workflow a successful result requires **both**
successful execution of the verification route **and** acceptance of its evidence by the qualification
gate.

The local qualification in Report 3 provides a complementary result for the same tracked-file tree in a
different environment.

**Reported result: PASSED**, with the gate's decision recorded as qualifying.

**Status.** Change C was merged into the main branch on 19 September 2026 as merge commit
`57015b13739aaedabf51f792ed0684bae18428e1`, whose file tree was verified to equal the tree this report
describes. Merging is not acceptance of the wider product, external reproduction, or adoption of any
governance amendment.

**Evidence basis.** Written from the run's own retained artifact, which is held and archived.

---

### 1. What the successful check means

The workflow requires two results:

| Requirement | What it contributes |
|---|---|
| the verification route exits 0 | the required route completed, including its checks and cleanup |
| the authoritative qualification gate accepts the retained evidence | the required records are present, agree with one another, and the declared controls are complete |

**Both must hold.** A successful route exit alone is insufficient, and the gate's acceptance cannot
override an unsuccessful route execution. The green check is the workflow's summary of that
conjunction.

For a reviewable claim, the retained gate decision, route result, candidate identity and workflow
definition must all support that summary. §4 and §7 give them. A badge or a screenshot does not.

This run supports the declared S015 verification result. It does not establish that every repository
test ran, that all product requirements are met, or that the system is ready for production use.

---

### 2. Which candidate was tested

The workflow uses the pull request's **head commit** — the branch version proposed for review — for the
checkout, the extraction of the committed startup file, and the identity recorded in the evidence. A
step in the workflow confirms the checkout equals that commit and stops on a mismatch.

| Item | Identity |
|---|---|
| Candidate commit | `faea7d29ae9bd4d7581e6596a3cb7e86318f822f` |
| Candidate file tree | `84aea3aa1d6114ae38c5918e3e066c8d0950a6c4` |
| Recorded base commit | `23aabdec8df202685f19b5744cf41f149a808f9f` |
| Pull request | [#3](https://github.com/alchemyofacceptance/INTEVIA/pull/3), merged 2026-09-19T12:04:43Z |
| Merge commit | [`57015b13739aaedabf51f792ed0684bae18428e1`](https://github.com/alchemyofacceptance/INTEVIA/commit/57015b13739aaedabf51f792ed0684bae18428e1) |

A pull-request workflow can instead test a proposed *merge* of the branch and its base. Head-commit
qualification was chosen for this task so the evidence plainly names the candidate under review. **This
does not imply that merge-result testing is invalid or untraceable.**

The result is for the candidate, not for a merge against a changed base — so the base was rechecked
immediately before merging and was unchanged at `23aabdec8df202685f19b5744cf41f149a808f9f`. After
merging, the main branch's tree was verified to equal the candidate tree above.

---

### 3. Recorded execution environment

| Item | Configuration |
|---|---|
| Platform | GitHub Actions |
| Runner | Ubuntu, Linux kernel 6.17.0-1022-azure |
| Python | CPython 3.12.14 from the runner's tool cache |
| Database | PostgreSQL **17.11** (Debian 17.11-1.pgdg13+2) in a service container |
| Framework and driver | Django 5.2.15; psycopg 3.3.6 |

A dependency probe runs before the route on every execution and records the resolved dependency root,
the package directory, whether it exists, and whether the framework imports under the intended isolated
interpreter settings. It aids diagnosis; its success is not a substitute for the route completing.

The local run in Report 3 used PostgreSQL 17.10. The two runs therefore agree across two operating
systems, two interpreters and two database builds.

---

### 4. What the run recorded

| Recorded outcome | Result |
|---|---|
| Run | `35436023009`, attempt 1 |
| Run identifier in the evidence | `gh354360230091` |
| Started / completed | 2026-09-19T09:55:52Z → 09:57:50Z, **118 seconds** |
| Check on pull request #3 | `s015` — **SUCCESS** |
| Route | every step PASS; coordinator exit 0; final exit 0 |
| Execution snapshot | unchanged during the run |
| Cleanup | CLEAN, nothing unresolved |
| Identity validity | recorded valid |
| **Gate decision** | **`qualifying: true`, no reasons**, eight controls expected and observed |

**Per-step test counts.** Eight steps is not eight tests:

| Step | Outcome | Tests run |
|---|---|---|
| `IDENTITY` | PASS | structural |
| `OFFLINE` | PASS | 105 |
| `SELF` | PASS | 15 |
| `S015` | PASS | 142 |
| `OWNERSHIP` | PASS | structural |
| `LOCK` | PASS | structural |
| `MUT-fu-b2-unrelated-reuse-refusal` | PASS | 1 |
| `MUT-fu-b3-unrelated-circle-check` | PASS | 1 |
| **Total** | | **264** |

The two mutation controls each pass by their target test **failing an assertion** under a deliberately
substituted protection. That is the intended result and it is what the control demonstrates; Report 1
§3 explains why.

---

### 5. Evidence retained for inspection

The whole run directory is archived: the startup attestation, the final snapshot check, the overall
execution result, the test summary, the source audit, the executed snapshot and its inventory,
per-step logs, database lifecycle records, and **the gate's decision record**.

| Item | Identity |
|---|---|
| Artifact | `s015-verification-evidence-faea7d29ae9bd4d7581e6596a3cb7e86318f822f-1` |
| Artifact id | `10582089766` |
| Size | 35,892,619 bytes |
| SHA-256 | `6418c4b529d1fee0ec767e16509fbc7cd9c2c559aa9f59adb03f7ba7e7d3db85` |
| Platform retention expires | 2026-12-18 |
| Durable copy | **downloaded and retained outside the platform**, so the evidence outlives the 90-day retention |

The decision is written **before** the archive is taken. In an earlier revision the archive was taken
first, so the decision existed only in the workflow log and not in the retained evidence. That was
corrected on 18 September 2026.

---

### 6. Development runs on the way here

Recorded rather than hidden. Each is an early development run for its own commit and is never evidence
for a later one.

| Commit | Result | What the record shows |
|---|---|---|
| `e19f41f8523e3a849f9290ed0fde981e31722418` | failed, 28s | the workflow invoked the route directly without the required validated dependency environment; the route refused and preserved the exception in its audit. **A workflow defect correctly refused by the repository** |
| `7622cce2aafd457bd4780daf71a5056b9d403689` | failed | every route step passed; compiled Python cache files appeared in the watched directory and the final source check refused the run |
| `cc3f2e894bc3d300d3f23aaa990f01b0f539cc24` | failed | the same refusal on a commit that changed only workflow files — showing the defect was deterministic |
| `b18b36775bd37a382167ef20bb5ef3726463a260` | passed | first success; qualified that tree under the then-current gate |
| `8c82d5e8f869eb185ce7690220aead701363c6dd` | passed | qualified under the authoritative gate as first landed |
| `faea7d29ae9bd4d7581e6596a3cb7e86318f822f` | **passed** | this report; qualified under the gate as corrected after independent review |

Earlier successes remain evidence for their own candidates and are **not substituted for** the result
reported here. Runs numbered before `e19f41f…` exist on this branch but were not examined; this table
is the documented sequence, not a claim to be the complete CI history.

---

### 7. Technical traceability

| Object | Identity |
|---|---|
| Run | [actions/runs/35436023009](https://github.com/alchemyofacceptance/INTEVIA/actions/runs/35436023009) |
| Candidate commit | [`faea7d29ae9bd4d7581e6596a3cb7e86318f822f`](https://github.com/alchemyofacceptance/INTEVIA/commit/faea7d29ae9bd4d7581e6596a3cb7e86318f822f) |
| Workflow as executed | [`.github/workflows/s015-verification.yml`](https://github.com/alchemyofacceptance/INTEVIA/blob/faea7d29ae9bd4d7581e6596a3cb7e86318f822f/.github/workflows/s015-verification.yml) |
| Startup file | Git blob `938f8d47cc2ab9d80b365925d23e8317f83e7408`, extracted from the candidate's own object store and verified before use |
| Qualification gate | `.github/s015_gate.py`; SHA-256 `82b06de08ca947ae2d40b28d728072ce45b384ba6c37b2ce3144423fcd6693c1` |
| Consumer contract | `docs/verification/CONSUMER_REQUIREMENT_v1_2.md`; SHA-256 `19985675fca35f97ca51ba6dec64d5f1311c999a71d26063f502e992a70c1466` |
| Gate controls | `.github/gate_conformance.py`; SHA-256 `ad80bc4ee107820b3a830222b09740a1b1ecdc0fdb66f454942446782f24ed95` — 49 controls, 98 decisions across both invocation paths, 0 failures |

---

### 8. Limits, and what remains

The result is scoped to this candidate and its recorded environment. It does not establish closure of
the historical product findings discussed in Report 2, verify all repository functionality, or remove
the trust and concurrency limitations described in Report 1.

The evidence checks do not authenticate a package against a party who rewrites all of its records
consistently. Internal digests and agreement between records are integrity checks, not a cryptographic
signature or an independent provenance source.

Two limitations of the decision program are recorded and were accepted at landing: it returns the
*refusal* exit code when it cannot decide at all, for example when given an unwritable output path —
**it cannot reach a successful result that way, because the workflow requires the decision record to
exist**; and its success message names an earlier contract version than the one governing, the checks
it performed being those of the governing version.

An independent focused review **closed three of its four findings** against the contract and the gate.
The fourth, a staging issue, received a **statement-only correction** which the Human Governor accepted
for this landing. **The reviewer has not subsequently confirmed closure of that finding, and the
acceptance is not reviewer clearance.**

---

**Authority.** A successful CI check does not authorise merging, changes to the main branch, or
adoption of governance amendments. **Final authority remains with Carmian Owen, Human Governor.**
