# S015 verification route

## Rules

The qualifying surface is the approved launcher: it extracts and blob-verifies the committed `verification/bootstrap.py` from the candidate commit's object store, then runs it with `--snapshot`. A direct invocation of `python -m verification.run` or `python -m verification.route` is for local inspection only and is non-qualifying.

```powershell
python -m verification.run --snapshot <snapshot-root>
```

The direct launcher and route entrypoints are still available for local inspection only:

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

`verification.run` is the coordinator that prepares the approved launcher, passes `--snapshot` to the committed `verification/bootstrap.py` surface, and consumes five run-bound records - `LAUNCH_ATTESTATION.json`, `POST_RUN_SNAPSHOT_CHECK.json`, `summary.json`, `SOURCE_AUDIT.json`, and `SURFACE_RESULT.json` - bound to one run. `verification.route` is the implementation surface that records the route summary, the per-step evidence, and the cleanup result. The offline self-tests only validate parser and route controls; they do not replace the approved launcher.

## Claims

### C5

Prevented: the startup-hook escape and `.pth` escape are prevented by the approved launcher before the route is trusted.

Detected after execution: the launcher records observed import and execution evidence, including repository modules seen from `E(root)` and refused modules seen outside the verified snapshot or trusted roots.

Excluded: the final module cache is not the complete execution history, and the route does not claim to prevent a concurrent writer from changing the checkout after the observed run.

### C13

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

The route evidence folder holds `SUMMARY.md`, `summary.json`, per-step collection, raw log, parsed results, isolation records, database receipts, and `MANIFEST.sha256`.

The summary records the commit, the working-files tree, the run nonce, the live environment, the step outcomes, the cleanup result, and the route claim text used by A1 to check that wording matches mechanism.

## Databases

- **One route run at a time per server:** the route holds a PostgreSQL advisory lock for the whole run, always taken in the server's `postgres` database whatever `INTEVIA_POSTGRES_DB` is set to. If another run holds it, the route stops `INCOMPLETE` before touching any database. If the role cannot connect to `postgres`, the route stops `INCOMPLETE` rather than run unserialised.
- **Scope:** the lock serialises verification route runs with each other. It does not restrain any other client of the server, and it is not an ownership check.
- **Names:** `test_intevia_living_organism_v<run id>_<step>`, at most 63 characters.
- **Creation:** the test runners create a database only if none of that name exists, record the attempt before issuing `CREATE`, never drop and recreate, and write a receipt with the new database's oid only after creation succeeds.
- **Destruction:** every drop - by the runners, by route cleanup, by fixture cleanup, and the deliberate replacement inside `OWNERSHIP` - happens only when the current oid, read on the same connection immediately before, equals the confirmed creation oid. A database that exists without a confirmed identity, or was replaced, is left untouched and reported as unresolved cleanup (exit 3).
- **Limit:** PostgreSQL cannot drop a database by oid. The final oid check and the drop are two consecutive statements on one connection; another client could replace the database between them. The route does not claim to prevent that.

## When a packet changes the schema or the tests

- **A new core migration:** declare its head and the required trigger count, with the ground, in `verification/isolation/state.py`. Until then, isolation is `NOT ESTABLISHED` for that head and the run cannot pass.
- **A negative test that must demand a specific refusal:** add a mutation in `verification/mutations.py` that replaces the intended protection with an unrelated refusal, and name the test as its target.

## What a PASS means

Evidence at the checked properties, for the tested commit and environment. It is not review, landing, external reproduction or Human acceptance.
