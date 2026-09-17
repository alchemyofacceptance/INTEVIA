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
| `PARSER` | `verification.selftest_parser`: the result parser against real unittest output, including tests with docstrings, sub-tests, skips, a missing result and a missing verdict | none |
| `SELF` | `verification.isolation.selfcheck_tests`: the isolation instrument's own self-check | a disposable test database |
| `S015` | every `tests/test_s015_*.py` module under the isolation runner | a disposable test database |
| `MUT-<name>` | each discrimination check in `verification/mutations.py`: its target tests must **fail** when the intended protection is replaced by an unrelated refusal | a disposable test database |

Each step is `PASS`, `FAIL` or `INCOMPLETE`. A log that does not account for every collected test, a module that fails
to load, missing isolation evidence or a setup failure is `INCOMPLETE`, never `PASS`.

## Result and exit status

| Exit | Meaning |
|---|---|
| 0 | every step `PASS` and cleanup clean |
| 1 | at least one step `FAIL` |
| 2 | at least one step `INCOMPLETE` (takes precedence over 1) |
| 3 | cleanup not clean (takes precedence over everything) |

## Evidence

The evidence folder (default `verification-evidence/<run id>/`, not committed) holds:

- **`SUMMARY.md` and `summary.json`:**
  - the commit and tree tested, and every uncommitted change with its SHA-256;
  - the core migration head;
  - Python, Django, psycopg and platform; the PostgreSQL server version and identity; the CI run, when there is one;
  - the test scope and collection digest for each step;
  - each step's outcome, test totals and every test that did not pass;
  - isolation checkpoints: applicable, established and not applicable (with the reason and the test ids);
  - cleanup per database, and the overall result.
- **Per step:** collection, raw log, parsed results, isolation records.
- **`MANIFEST.sha256`:** the digest of every file in the folder.

## Running it locally

Requirements: Python 3.12, a PostgreSQL server (17 is the project's version) and a role that can create databases.

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

The route creates only `test_intevia_living_organism_v<run id>_<step>` databases, refuses to start if any of that run's
names already exist, and drops only its own names that survive the test runner. It never touches any other database.

## When a packet changes the schema or the tests

- **A new core migration:** declare its head and the required trigger count, with the ground, in
  `verification/isolation/state.py`. Until then, isolation is `NOT ESTABLISHED` for that head and the run cannot pass.
- **A negative test that must demand a specific refusal:** add a mutation in `verification/mutations.py` that replaces
  the intended protection with an unrelated refusal, and name the test as its target.

## What a PASS means

Evidence at the checked properties, for the tested commit and environment. It is not review, landing, external
reproduction or Human acceptance.
