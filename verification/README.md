# S015 verification route

For the current position and its limits, see [the README](../README.md#current-position).

The current development practice is **IDOPT (formerly IDOP)** — INTEVIA Development Operating Protocol and Thinking. Historical records and protocol filenames retain IDOP.

## Rules

**GitHub continuous integration (CI) is the evidence of record.** The [workflow](../.github/workflows/s015-verification.yml) checks the exact candidate, extracts and verifies its committed `verification/bootstrap.py`, runs it with an explicit candidate and tree identity, then applies [the separate gate](../.github/s015_gate.py). The full route and the gate must both pass. The [run requirements](../docs/operations/RUN_REQUIREMENTS.md), especially R10 and R16, govern closure and merge evidence.

The current suite follows six test admission rules (TAR). C5 recorded 126 product tests and 19 deliberate-defect checks. The old suite of about 1,800 tests is archived and not run. Its [archive record](../archive/tests_2026_10/00_ARCHIVE_RECORD.md) controls any selective reuse and names the test that must never run again.

Direct entrypoints remain available for authorised local inspection; they are not merge evidence:

```powershell
python -m verification.run
python -m verification.route
```

The required offline controls are:

```powershell
python -m unittest verification.selftest_bootstrap verification.selftest_parser verification.selftest_route verification.selftest_recording -v
```

`verification/selftest_parser.py`, `verification/selftest_route.py`, and `verification/runner_captures.json` preserve the named self-test closures and the real captured runner shapes. The captures file is evidence, not a mechanism.

## Invocation

The workflow starts the verified `verification/bootstrap.py`. That startup code prepares the checked snapshot and starts `verification.run`, which observes imported code and calls `verification.route`. The route records its steps and cleanup; it does not launch the bootstrap. The gate later reads the linked records and decides whether the run qualifies.

Generated evidence files include `LAUNCH_ATTESTATION.json`, `POST_RUN_SNAPSHOT_CHECK.json`, `summary.json`, `SOURCE_AUDIT.json` and `SURFACE_RESULT.json`. These are outputs of a run, not repository files. The workflow requires Python 3.12, PostgreSQL 17, PowerShell 7 and Node.js. The offline self-tests check the tools; they do not replace product tests or the qualifying GitHub run.

All 19 registry entries — the deliberate-defect checks listed in `verification/mutations.py` — are part of the recorded C5 route. Labelling a check for admission (when a test first joins the suite) does not by itself make it run only once.

## Current route

The route checks candidate identity, tools without a database, the isolation instrument, the live tests, ownership and locking, and deliberate-defect detection. It then inspects and cleans up its databases. This is no longer the September eight-step selection. Ordinary failures are reported across the selected tests; isolation or safety stops remain mandatory. See [the current run requirements](../docs/operations/RUN_REQUIREMENTS.md).

## Claims

### C5 — historical route-control label, not offices chunk C5

Prevented: the startup-hook escape and `.pth` escape are prevented by the approved launcher before the route is trusted.

Detected after execution: the launcher records observed import and execution evidence, including repository modules seen from `E(root)` and refused modules seen outside the verified snapshot or trusted roots.

Excluded: the final module cache is not the complete execution history, and the route does not claim to prevent a concurrent writer from changing the checkout after the observed run.

### C13 — route-control label

Prevented: the shadowing top-level package is refused before its initializer can be trusted.

Detected after execution: the source audit records import and execution history, not just the final cache, and it keeps refused history entries when a module was observed from `W` or `OTHER`.

Excluded: the final cache is not the complete execution history, and concurrent-writer replacement is excluded from the claim because it is only detectable after execution.

## Result and exit status

| Exit | Meaning |
|---|---|
| 0 | every step `PASS` and cleanup clean |
| 1 | at least one step `FAIL` |
| 2 | at least one step `INCOMPLETE` (takes precedence over 1) |
| 3 | cleanup not clean (takes precedence over everything) |

## Evidence

`--evidence-dir` must name a directory that does not exist yet; an existing one is refused and nothing is written into it.

These generated files are not repository paths. The route evidence folder holds `SUMMARY.md`, `summary.json`, per-step collection, raw log, parsed results, isolation records, database receipts, and `MANIFEST.sha256`.

The summary records the commit, the working-files tree, the run nonce, the live environment, the step outcomes, the cleanup result, and the route claim text used by A1 to check that wording matches mechanism.

## Databases

**Clone mode is current.** The route's self-check step, called SELF, builds one application database template from scratch by applying the database changes in order. Later test steps and final inspection require copies of that same recorded template. They refuse if it is missing or inconsistent; there is no fresh-build fallback. The approved ownership, locking and template-sealing probes may create empty control databases. [R2, R8 and R9](../docs/operations/RUN_REQUIREMENTS.md) retain identity, timing and cleanup requirements. Development reuse follows its separate identity-validated template rule.


- **One route run at a time per server:** the route holds a PostgreSQL advisory lock for the whole run, always taken in the server's `postgres` database whatever `INTEVIA_POSTGRES_DB` is set to. If another run holds it, the route stops `INCOMPLETE` before touching any database. If the role cannot connect to `postgres`, the route stops `INCOMPLETE` rather than run unserialised.
- **Scope:** the lock serialises verification route runs with each other. It does not restrain any other client of the server, and it is not an ownership check.
- **Names:** `test_intevia_living_organism_v<run id>_<step>`, at most 63 characters.
- **Creation:** the test runners create a database only if none of that name exists, record the attempt before issuing `CREATE`, never drop and recreate, and write a receipt with the new database's oid only after creation succeeds.
- **Destruction:** every drop - by the runners, by route cleanup, by fixture cleanup, and the deliberate replacement inside `OWNERSHIP` - happens only when the current oid, read on the same connection immediately before, equals the confirmed creation oid. A database that exists without a confirmed identity, or was replaced, is left untouched and reported as unresolved cleanup (exit 3).
- **Limit:** PostgreSQL cannot drop a database by oid. The final oid check and the drop are two consecutive statements on one connection; another client could replace the database between them. The route does not claim to prevent that.

## When a packet changes the schema or the tests

- **A new core migration:** the current instrument checks the migration head and declared protection totals in `verification/isolation/state.py`. A changed head must be accounted for before the run can pass. This describes the present instrument; changing its count policy belongs in a separately authorised change.
- **A new or changed test:** apply the six test admission rules and record the obligation and intended safeguard. Deliberate-defect checks are listed in `verification/mutations.py`. Their failure must demonstrate the intended protection, not merely any unrelated error.

## What a PASS means

Evidence at the checked properties, for the tested commit and environment. It is not review, landing, external reproduction or Human acceptance.
