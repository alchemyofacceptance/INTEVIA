# S015 verification route

One entry point, used the same way on a developer's machine and in CI:

```
python -m verification.run
```

It tests the repository it is run from and writes an evidence folder recording exactly what was tested and what
happened. It is the route described in the proposed HAT/IDOP amendment on cross-packet integration verification.

## What it runs

| Step | What | Database |
|---|---|---|
| `IDENTITY` | the commit, its tree, every working-tree change with its bytes (renames, deletions and unusual paths included), and git's tree of the working files, computed separately and reconciled with the change list. A failure, an unsupported entry such as a symlink, a working-files tree that differs from the commit in a path the change list does not name, or any path carrying the git index flags `assume-unchanged` or `skip-worktree` (which hide changes from `git status`) makes the run `INCOMPLETE`, naming the paths. The route never clears flags or alters the checkout | none |
| `OFFLINE` | `verification.selftest_parser` and `verification.selftest_route`: the parser's outcome accounting and the route's safeguards, each shown to accept valid evidence and reject defective evidence | none |
| `SELF` | `verification.isolation.selfcheck_tests`: the isolation instrument's own self-check | a disposable test database |
| `S015` | every `tests/test_s015_*.py` module under the isolation runner | a disposable test database |
| `OWNERSHIP` | live checks on the server: a database that already exists is refused and left untouched, and no test reports any outcome; a replaced database is not dropped; an owned database is dropped only by its oid | databases the step creates and removes itself |
| `LOCK` | live check that a second run configured with a different `INTEVIA_POSTGRES_DB` is refused the lock this run holds, with a control showing the same key is free inside that other database | a database the step creates and removes itself |
| `MUT-<name>` | each discrimination check in `verification/mutations.py`: its target tests must **fail by assertion** when the intended protection is replaced by an unrelated refusal | a disposable test database |

Each step is `PASS`, `FAIL` or `INCOMPLETE`. Every collected test must have exactly one lawful terminal outcome, and each
status line must agree with the failure reports of its own test: a sub-test `FAIL` or `ERROR` needs its own report, a
report needs its status line, and an `ok` contradicted by a failure event or report is refused. A missing, repeated or
contradictory outcome, a module that fails to load, isolation evidence that is missing or not bound to the run, a skipped
test (including a skipped sub-test), or a setup failure is `INCOMPLETE`, never `PASS`. A setup, sub-test or teardown
error is `FAIL`; several teardown errors on one test are counted, not refused.

## Result and exit status

| Exit | Meaning |
|---|---|
| 0 | every step `PASS` and cleanup clean |
| 1 | at least one step `FAIL` |
| 2 | at least one step `INCOMPLETE` (takes precedence over 1) |
| 3 | cleanup not clean (takes precedence over everything) |

## Evidence

`--evidence-dir` must name a directory that does not exist yet; an existing one is refused and nothing is written into
it. The default is `verification-evidence/<run id>/` (not committed). It holds:

- **`SUMMARY.md` and `summary.json`:**
  - the commit and its tree, the git tree of the working files, and every uncommitted change with its SHA-256 (or
    `deleted`);
  - the core migration head;
  - Python, Django, psycopg and platform; the PostgreSQL server version and identity; the CI run, when there is one;
  - the run nonce, which binds each step's collection, isolation records and database receipts to this run;
  - each step's scope, collection digest, outcome, test totals, every test that did not pass, and any accounting
    violations;
  - isolation checkpoints recomputed from the run's own records: applicable, established, not applicable (with ids);
  - cleanup per database, with its ownership evidence, and the overall result.
- **Per step:** collection, raw log, parsed results, isolation records, database receipts.
- **`MANIFEST.sha256`:** the digest of every file in the folder.

## Running it locally

Requirements: Python 3.12, a PostgreSQL server (17 is the project's version), and a role that can create databases and
can connect to the server's `postgres` database (the lock database; see below).

```
python -m venv .venv-verification
.venv-verification\Scripts\activate          # Windows; on Linux: . .venv-verification/bin/activate
python -m pip install -r requirements-verification.txt
set INTEVIA_POSTGRES_USER=intevia            # Linux: export ...
set INTEVIA_POSTGRES_HOST=127.0.0.1
set INTEVIA_POSTGRES_PORT=5432
python -m verification.run                   # prompts for the password if INTEVIA_POSTGRES_PASSWORD is not set
```

## Databases

- **One route run at a time per server:** the route holds a PostgreSQL advisory lock for the whole run, always taken in the
  server's `postgres` database whatever `INTEVIA_POSTGRES_DB` is set to (advisory locks belong to one database, so a
  common database is what makes two runs contend). If another run holds it, the route stops `INCOMPLETE` before touching
  any database. If the role cannot connect to `postgres`, the route stops `INCOMPLETE` rather than run unserialised.
  **Scope:** the lock serialises verification route runs with each other. It does not restrain any other client of the
  server, and it is not an ownership check - ownership is decided only by the oid checks below.
- **Names:** `test_intevia_living_organism_v<run id>_<step>`, at most 63 characters.
- **Creation:** the test runners create a database only if none of that name exists, record the attempt before issuing
  `CREATE`, never drop and recreate, and write a receipt with the new database's oid only after creation succeeds. The
  route's own fixture databases are recorded before `CREATE` and carry their state (attempted, create issued, created,
  confirmed with oid) through any exception.
- **Destruction:** every drop - by the runners, by route cleanup, by fixture cleanup, and the deliberate replacement inside
  `OWNERSHIP` - happens only when the current oid, read on the same connection immediately before, equals the confirmed
  creation oid. A database that exists without a confirmed identity (no receipt, or a creation whose oid was never read),
  or was replaced (same name, different oid), is left untouched and reported as unresolved cleanup (exit 3).
- **Limit:** PostgreSQL cannot drop a database by oid. The final oid check and the drop are two consecutive statements on
  one connection; another client could replace the database between them. The route does not claim to prevent that.

## When a packet changes the schema or the tests

- **A new core migration:** declare its head and the required trigger count, with the ground, in
  `verification/isolation/state.py`. Until then, isolation is `NOT ESTABLISHED` for that head and the run cannot pass.
- **A negative test that must demand a specific refusal:** add a mutation in `verification/mutations.py` that replaces
  the intended protection with an unrelated refusal, and name the test as its target.

## What a PASS means

Evidence at the checked properties, for the tested commit and environment. It is not review, landing, external
reproduction or Human acceptance.
