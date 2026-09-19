# Change C — Local qualification report

## Checking the candidate in the developer's Windows environment

**Report 3 of 4 · v1.0 · 19 September 2026**
**For:** Carmian Owen, independent reviewers and prospective funders
**Run:** `CHC_20260919T101422Z`, 18–19 September 2026

---

### At a glance

Change C completed its local verification run on Windows, using a purpose-built Python environment and a
live PostgreSQL database in a local container. The qualification gate then accepted the retained
evidence with no refusal reasons.

The run tested a locally reconstructed copy of the candidate's tracked files. Its file-tree identity
matched the one tested in continuous integration, although the two runs recorded different commit
identifiers. That distinction is explained in §3 and matters when reading either report.

**Reported result: QUALIFYING.** The launcher and the qualification gate each returned exit status 0.
Both were required.

**Status.** Change C was merged into the repository's main branch on 19 September 2026 as merge commit
`57015b13739aaedabf51f792ed0684bae18428e1`, and the main branch's file tree was verified to equal the
tree this report describes. Merging is not acceptance of the wider product, external reproduction, or
adoption of any governance amendment.

**Evidence basis.** This report is written from the run's own retained records, which are held. It is
not an independent reviewer's reproduction of the run.

---

### 1. What was run, and what "qualifying" means

The local launcher prepared the candidate and started the verification route. Afterwards the
authoritative Python gate examined the retained records against the declared consumer contract.

| Recorded outcome | Result | Meaning |
|---|---|---|
| Launcher exit | **0** | the launcher completed its execution path |
| Gate exit | **0** | the qualification program accepted the evidence |
| Decision record | **`qualifying: true`, no reasons** | no check produced a refusal |
| Expected control steps | **8** | six fixed controls and two mutation controls |
| Observed control steps | **the same eight** | |

**Both are required.** An exit of 0 from the route alone does not establish qualification; under the
contract every path that reports qualification must invoke the gate and require its successful
decision.

The eight steps are `IDENTITY`, `OFFLINE`, `SELF`, `S015`, `OWNERSHIP`, `LOCK`, and two mutation
controls. **Eight steps is not eight tests.** The steps contain **264 tests** in total: 105 in
`OFFLINE`, 15 in `SELF`, 142 in `S015`, and one in each mutation control. `IDENTITY`, `OWNERSHIP` and
`LOCK` record structural checks rather than a test count.

The gate found, from the records: each required control present exactly once with a successful outcome;
both mutations applied with their target tests failing by assertion; the execution snapshot unchanged
during the run; the specified records and digests in agreement; and cleanup reporting CLEAN with
nothing unresolved.

These statements describe the declared checks and nothing wider.

---

### 2. How the local candidate was prepared

The launcher rebuilt the candidate in an isolated copy of the repository:

1. checked out the specified base commit;
2. applied the supplied change file, a *diff*;
3. confirmed the resulting tracked-file tree matched the expected candidate tree;
4. created a local commit using fixed author and date values;
5. built a fresh Python environment from the candidate's own requirements, and verified the committed
   startup file against its recorded identity before running it.

The same base-and-diff reconstruction had been checked beforehand in a separate throwaway clone, and
both reached the same tree.

Tree comparison establishes the relationship between the reconstructed tracked files and the candidate.
It does not by itself establish the runtime environment; the environment records and the source checks
address that separately, within the boundaries §6 states.

---

### 3. Why the local and CI commit identifiers differ

A Git **commit** records a version together with its history and metadata. A Git **tree** identifies the
tracked file structure and contents. Two commits can therefore differ while naming the same tree.

| Environment | Commit recorded for the run | Shared candidate tree |
|---|---|---|
| Local Windows run | `4a5991af5e9f1be9d46522926bc4ab425c92caad` | `84aea3aa1d6114ae38c5918e3e066c8d0950a6c4` |
| GitHub CI run | `faea7d29ae9bd4d7581e6596a3cb7e86318f822f` | `84aea3aa1d6114ae38c5918e3e066c8d0950a6c4` |

**The local commit `4a5991af…` was not publicly pushed. It is resolvable in the run's isolated clone
for as long as that clone is retained**, and it will not be found in the public repository. It can also
be reconstructed from the base commit, the package diff and the launcher's fixed metadata, all
identified in §5.

The accurate claim is: **two environments qualified the same tracked-file tree**, using the same
committed startup code and the same qualification gate. It does not mean the environments were
identical, that the same commit ran in both, or that either was an external reviewer's reproduction.

---

### 4. Recorded local environment

| Item | Configuration |
|---|---|
| Operating system | Windows 11 |
| Shell | Windows PowerShell 5.1.26100.9168, Desktop edition |
| Starting interpreter | base CPython 3.12.10, with no virtual environment active |
| Verification environment | a fresh virtual environment built from the candidate's requirements |
| Database | PostgreSQL **17.10** (Debian 17.10-1.pgdg13+1) in a local container, on the loopback address |
| Framework and driver | Django 5.2.15; psycopg 3.3.6 with its binary package |
| Other recorded dependencies | asgiref 3.12.1; sqlparse 0.6.0; typing_extensions 4.16.0; tzdata 2026.4 |

The CI run used PostgreSQL **17.11** — the same major version, a different patch release. The two runs
therefore agree across two operating systems, two interpreters and two database builds.

Before the route started, the launcher exercised four controls on its own handling of external
commands, including a deliberate failure case, to confirm the shell reports command failures truthfully.

An earlier start was refused because a virtual environment was active. That is the launcher enforcing
its own precondition, and it is a separate event from the run reported here.

---

### 5. Evidence identities

| Object | Identity |
|---|---|
| Run | `CHC_20260919T101422Z` |
| Base commit | `23aabdec8df202685f19b5744cf41f149a808f9f` |
| Public candidate commit | `faea7d29ae9bd4d7581e6596a3cb7e86318f822f` |
| Local commit (this run; not publicly pushed — see §3) | `4a5991af5e9f1be9d46522926bc4ab425c92caad` |
| Candidate tree | `84aea3aa1d6114ae38c5918e3e066c8d0950a6c4` |
| Package diff | 517,603 bytes; SHA-256 `c5a4d5d8f86646ea269e9f725625da97b0a16944e05e79344e620887d725b2d8` |
| Launcher | v0.11; SHA-256 `8ea9c30c078dc8f18a92c157ef96b14b6aae4a6ec3bf8173e50bdaffafcaa437` |
| Startup file | Git blob `938f8d47cc2ab9d80b365925d23e8317f83e7408`, extracted from the candidate and verified before use |
| Qualification gate | `s015_gate.py`; SHA-256 `82b06de08ca947ae2d40b28d728072ce45b384ba6c37b2ce3144423fcd6693c1` |
| Consumer contract | `CONSUMER_REQUIREMENT_v1_2.md`; SHA-256 `19985675fca35f97ca51ba6dec64d5f1311c999a71d26063f502e992a70c1466` |

A Git blob identifier and a SHA-256 file digest use different hashing conventions and are not
interchangeable.

#### Durable evidence reference

The run directory has been archived and its evidence manifest verified.

| | |
|---|---|
| Archive | `LOCAL_RUN_CHC_20260919T101422Z.zip` |
| Size | 87,897,559 bytes |
| SHA-256 | `db8a239529c690a267c40524623ff660f6563149a8c9e586b45a0105c18bb2b1` |
| Evidence manifest | verified at archiving: **27 members, 0 failed, 0 unrecognised lines** |
| Location | `C:\Users\Carewen\Downloads\CHANGE_C_FINAL_LOCAL_EVIDENCE\` on the machine that produced the run |

Copying the archive to storage independent of that machine is outstanding work. Until it is done, this
report does not claim off-machine retention.

**An earlier local run is retained as historical evidence only**: `CHC_20260918T214620Z`, 87,852,429
bytes, SHA-256 `73c778111aa4a162a7ef2cf3825e0dcd4c6e34e2bb04cf6a1584f304b87605be`, manifest verified
27 of 27. It belongs to the superseded candidate `8c82d5e8f869eb185ce7690220aead701363c6dd` and **is
not evidence for the result reported here**.

---

### 6. Limits of this result

The run supports the declared verification result for this candidate tree in this environment. It does
not close the product findings discussed in Report 2, establish a result for any other tree, or
exercise every test in the repository.

Internal digest and consistency checks show agreement with recorded values. They are **not** a
cryptographic signature: an evidence package rewritten wholly and consistently would pass every check.
The package's authenticity, and its connection to the execution that produced it, require a separate
provenance account. A provenance record can be shown to **agree** with the run's attestation; showing
it came from an **independent** source is the reporter's responsibility, not a property the gate can
check.

The trusted-interpreter boundary and the concurrent-database-interference limitation described in
Report 1 remain in force. A successful run does not remove them.

---

### 7. Earlier runs, retained as historical records

| Run | Candidate | Tree | Note |
|---|---|---|---|
| `CHC_20260918T192029Z` | `b18b36775bd37a382167ef20bb5ef3726463a260` | `12baae33f2d2de7c7d007f8788a87ade81a3c3b6` | qualified under an earlier, less strict gate |
| `CHC_20260918T214620Z` | `8c82d5e8f869eb185ce7690220aead701363c6dd` | `7acf4b9b383d167fa90bae75e61919cac7012054` | qualified; superseded by the final candidate |

Each is evidence for its own candidate. **Neither is substituted for the result reported here**, and
neither transfers to it.

---

### 8. Status and authority

Change C was merged on 19 September 2026. Remaining programme work, including the reconciliation of
historical external-review findings, the assessor-facing repository improvements and PKT-B, is
described in Report 2.

Qualification does not authorise merging, changes to the main branch, or adoption of governance
amendments. **Final authority remains with Carmian Owen, Human Governor.**
