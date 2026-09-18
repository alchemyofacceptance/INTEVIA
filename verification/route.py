"""S015 verification route - the single entry point used locally and by CI (route v0.5, Change C v0.6).

    python -m verification.run [--evidence-dir NEW_DIR] [--run-id ID] [--skip-mutations]

Run from the repository root, in an environment with requirements-verification.txt installed, with a PostgreSQL server
reachable through INTEVIA_POSTGRES_HOST / _PORT / _USER / _PASSWORD (prompted for when absent and a terminal is attached).
The role must be able to create and drop databases.

Evidence: a NEW directory (refused if it exists; nothing is written into an existing directory) holding summary.json,
SUMMARY.md, per-step collection, raw log, parsed results, isolation records and database receipts, a transcript, and
MANIFEST.sha256.

Steps (each PASS, FAIL or INCOMPLETE):
  IDENTITY    the commit, git tree of the working files, and every change with its bytes; a failure makes the run INCOMPLETE
  OFFLINE     verification.selftest_parser, verification.selftest_route, verification.selftest_recording and verification.selftest_bootstrap (no database)
  SELF        the isolation instrument's self-check under the isolation runner
  S015        the S015 test set (tests/, pattern test_s015_*.py) under the isolation runner
  OWNERSHIP   live checks of the database safeguards: a colliding database is refused and left untouched; a replaced
              database is not dropped; an owned database is dropped only by its oid
  LOCK        live check that two route runs configured with DIFFERENT connection databases still contend for the lock
  MUT-<name>  each discrimination check in verification.mutations: its target tests must fail by assertion

Database safety (A1 C-A1-B3; RC-B2, RC-B3 and RC-O1 in v0.3):
  Lock      the route holds a PostgreSQL advisory lock for the whole run, always taken through a connection to the
            administrative database LOCK_DATABASE ("postgres"), whatever INTEVIA_POSTGRES_DB says. PostgreSQL advisory locks
            are scoped to one database, so a common lock database is what makes route runs on one server contend. The
            lock serialises route runs with each other and nothing else: it does not stop any other client of the server,
            and it is not an ownership check. Prerequisite: the role can connect to "postgres"; if it cannot, the run is
            refused (INCOMPLETE) rather than run unserialised.
  Names     test databases are named test_intevia_living_organism_v<run id>_<step code>.
  Creation  the runners create a database only if no database of that name exists, never clobber, and write a receipt
            with its oid after creation. The route's own fixture databases are registered BEFORE CREATE is issued and move
            through ATTEMPTED -> CREATE ISSUED -> CREATED -> CONFIRMED (oid known); an exception at any point leaves the
            record at the last state reached.
  Deletion  every DROP - runner, route cleanup, fixture cleanup and the deliberate replacement transition - runs only after
            the current oid, read on the same connection immediately before, equals the confirmed creation oid. Anything
            else, and any database whose creation or identity was never confirmed, is left untouched and reported as
            unresolved cleanup (exit 3).
  Limit     PostgreSQL cannot drop a database by oid. Between that final oid check and the DROP statement another client
            could replace the database; the route narrows this window to two consecutive statements on one connection and
            does not claim to close it.

Exit status: 0 every step PASS and cleanup clean; 1 a step FAIL; 2 a step INCOMPLETE (over 1), including an existing
evidence directory or an unavailable lock; 3 cleanup not clean or unresolved (over all).
"""
import argparse
import datetime
import getpass
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import traceback
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from verification import parse_results  # noqa: E402
from verification.mutations import MUTATIONS  # noqa: E402
from verification.ownership import NONCE_ENV, RECEIPT_ENV, read_receipts  # noqa: E402

ROUTE = "LIVING_ORGANISM_TEST_LIFECYCLE"
DB_PREFIX = "test_intevia_living_organism_"
S015_PATTERN = "test_s015_*.py"
ISOLATION_RUNNER = "verification.isolation.runner.IsolationRunner"
MUTATION_RUNNER = "verification.mutations.MutationRunner"
LOCK_KEY = int(hashlib.sha256(b"intevia-s015-verification-route").hexdigest()[:15], 16)
LOCK_DATABASE = "postgres"  # common to every route run on a server (RC-O1); not configurable by design
OWNERSHIP_PROBE_TEST = "test_s015_0022_contract.S0150022ContractTests.test_l2_preimage_refuses_every_body_while_u14_stands"
STEP_CODES = {"SELF": "self", "S015": "s015", "OWN-COLLIDE": "own1", "OWN-REPLACE": "own2", "OWN-OWNED": "own3", "LOCK": "lock1"}
STEP_CODES.update({"MUT-" + name: "mut%d" % (i + 1) for i, name in enumerate(sorted(MUTATIONS))})

COLLECT = r"""
import hashlib, json, os, sys
sys.path.insert(0, os.getcwd())
import django
django.setup()
from django.test.runner import DiscoverRunner
mode, out_path, rest = sys.argv[1], sys.argv[2], sys.argv[3:]
if mode == "label":
    suite = DiscoverRunner(verbosity=0).build_suite(rest)
else:
    suite = DiscoverRunner(verbosity=0, pattern=rest[0], top_level="tests").build_suite(["tests"])
ids = sorted(t.id() for t in suite)
failed = [i for i in ids if "loader._FailedTest" in i]
with open(out_path, "x", encoding="utf-8") as f:
    json.dump({"mode": mode, "args": rest, "count": len(ids), "failed_loads": failed, "run_nonce": os.environ.get("VERIFICATION_RUN_NONCE"),
               "digest": hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest(), "ids": ids}, f, indent=1)
"""


class RouteRefused(RuntimeError):
    pass


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run_git(args, env=None, cwd=None):
    """Returns (exit code, stdout bytes, stderr text). Never converts a failure into an empty result."""
    p = subprocess.run(["git", *args], cwd=cwd or ROOT, capture_output=True, env=env)
    return p.returncode, p.stdout, p.stderr.decode("utf-8", "replace").strip()


def parse_status_z(raw):
    """Parse `git status --porcelain=v1 -z`: entries 'XY path', with a second NUL-separated field (the source path) for
    renames and copies. Paths are raw bytes decoded as UTF-8 (git's -z form is unquoted)."""
    parts = raw.split(b"\0")
    entries, i = [], 0
    while i < len(parts):
        item = parts[i]
        i += 1
        if not item:
            continue
        if len(item) < 4 or item[2:3] != b" ":
            raise ValueError("unrecognised status entry %r" % item[:60])
        xy, path = item[:2].decode("ascii"), item[3:].decode("utf-8", "surrogateescape")
        orig = None
        if "R" in xy or "C" in xy:
            if i >= len(parts) or not parts[i]:
                raise ValueError("rename or copy entry without its source path: %r" % path)
            orig = parts[i].decode("utf-8", "surrogateescape")
            i += 1
        entries.append({"xy": xy, "path": path, "orig_path": orig})
    return entries


def _read_pyvenv_home(pyvenv_cfg):
    with open(pyvenv_cfg, encoding="utf-8") as handle:
        for line in handle:
            key, sep, value = line.partition("=")
            if sep and key.strip().lower() == "home":
                return os.path.realpath(value.strip())
    return None


def _purelib_for_root(root=None):
    scheme = sysconfig.get_default_scheme()
    if root is None:
        paths = sysconfig.get_paths(scheme=scheme)
    else:
        root = os.path.realpath(root)
        paths = sysconfig.get_paths(scheme=scheme, vars={"base": root, "platbase": root})
    return os.path.realpath(paths["purelib"])


def resolve_dependency_environment(executable=None):
    executable_path = os.path.realpath(executable or sys.executable)
    executable_dir = os.path.dirname(executable_path)
    venv_root = None
    pyvenv_cfg_home = None
    for candidate in (executable_dir, os.path.dirname(executable_dir)):
        pyvenv_cfg = os.path.join(candidate, "pyvenv.cfg")
        if os.path.isfile(pyvenv_cfg):
            venv_root = os.path.realpath(candidate)
            pyvenv_cfg_home = _read_pyvenv_home(pyvenv_cfg)
            break
    base_prefix = os.path.realpath(sys.base_prefix)
    dependency_root = venv_root or base_prefix
    site_packages = _purelib_for_root(dependency_root if venv_root is not None else None)
    return {
        "executable": executable_path,
        "base_prefix": base_prefix,
        "dependency_root": dependency_root,
        "site_packages": site_packages,
        "venv_root": venv_root,
        "pyvenv_cfg_home": pyvenv_cfg_home,
    }


class Route:
    def __init__(self, evidence_dir, run_id):
        self.dir = os.path.abspath(evidence_dir)
        parent = os.path.dirname(self.dir)
        os.makedirs(parent, exist_ok=True)
        try:
            os.mkdir(self.dir)  # atomic: refuses an existing directory, so earlier evidence is never touched (C-A1-B4)
        except FileExistsError:
            raise RouteRefused("evidence directory already exists: %s (nothing was written)" % self.dir)
        self.run_id = run_id
        self.nonce = uuid.uuid4().hex
        self.steps = []
        self.attempted = []   # [(step id, database name, receipts path)] - attempts, not ownership
        self.lifecycle = []   # the route's own fixture databases, registered before CREATE (see create_fixture)
        self.lock_conn = None
        self.transcript = open(os.path.join(self.dir, "transcript.txt"), "x", encoding="utf-8")

    def path(self, name):
        return os.path.join(self.dir, name)

    def say(self, text):
        print(text, flush=True)
        if not self.transcript.closed:
            self.transcript.write(text + "\n")
            self.transcript.flush()

    # ------------------------------------------------------------------ identity (C-A1-B5)
    def identity(self, checkout=None):
        cwd = os.path.abspath(checkout or ROOT)
        out = {"valid": False, "problems": []}
        code, head, err = run_git(["rev-parse", "--verify", "HEAD"], cwd=cwd)
        if code != 0:
            out["problems"].append("git rev-parse HEAD failed (%d): %s" % (code, err))
            return out
        out["commit"] = head.decode().strip()
        code, tree, err = run_git(["rev-parse", "--verify", "HEAD^{tree}"], cwd=cwd)
        if code != 0:
            out["problems"].append("git rev-parse HEAD^{tree} failed (%d): %s" % (code, err))
            return out
        out["commit_tree"] = tree.decode().strip()
        code, raw, err = run_git(["status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=none"], cwd=cwd)
        if code != 0:
            out["problems"].append("git status failed (%d): %s" % (code, err))
            return out
        try:
            entries = parse_status_z(raw)
        except ValueError as exc:
            out["problems"].append("git status could not be parsed: %s" % exc)
            return out
        # RC-B4: index flags make git status skip a file's working bytes, so the change inventory above cannot see it.
        # The route refuses such a state (it never clears flags or touches the index): list the flagged paths and stop.
        code, flags_raw, err = run_git(["ls-files", "-v", "-z"], cwd=cwd)
        if code != 0:
            out["problems"].append("git ls-files -v failed (%d): %s" % (code, err))
            return out
        flagged = []
        for item in flags_raw.split(b"\0"):
            if len(item) < 3:
                continue
            tag, path = item[:1].decode("ascii", "replace"), item[2:].decode("utf-8", "surrogateescape")
            kinds = (["assume-unchanged"] if tag.islower() else []) + (["skip-worktree"] if tag.upper() == "S" else [])
            if kinds:
                flagged.append({"path": path, "flags": kinds})
        out["index_flags"] = flagged
        if flagged:
            out["problems"].append("%d path(s) carry git index flags that hide working-tree changes from git status, so the change "
                                   "inventory cannot be trusted: %s. The route does not clear them; clear them (git update-index "
                                   "--no-assume-unchanged / --no-skip-worktree) or use a checkout without them"
                                   % (len(flagged), "; ".join("%s (%s)" % (f["path"], ", ".join(f["flags"])) for f in flagged[:20])))
        rel_evidence = os.path.relpath(self.dir, cwd).replace(os.sep, "/")
        inside = not rel_evidence.startswith("..")
        changes = []
        for e in entries:
            if inside and (e["path"] == rel_evidence or e["path"].startswith(rel_evidence + "/")):
                continue
            full = os.path.join(cwd, e["path"])
            rec = {"status": e["xy"], "path": e["path"], "orig_path": e["orig_path"]}
            if "D" in e["xy"] and not os.path.lexists(full):
                rec.update(kind="deleted", sha256=None)
            elif os.path.islink(full) or (os.path.lexists(full) and not os.path.isfile(full)):
                rec.update(kind="unsupported", sha256=None)
                out["problems"].append("unsupported working-tree entry (not a regular file): %s" % e["path"])
            elif not os.path.isfile(full):
                rec.update(kind="missing", sha256=None)
                out["problems"].append("changed path has no file and is not a deletion: %s" % e["path"])
            else:
                rec.update(kind="file", sha256=sha256_file(full), bytes=os.path.getsize(full))
            changes.append(rec)
        out["working_tree_changes"] = changes
        out["working_tree_clean"] = not changes
        # git's canonical tree of the working files (after git's own normalisation, e.g. line endings), computed in a
        # temporary index so the real index is not touched; distinct from the raw working-file bytes above
        tmp = tempfile.mkdtemp(prefix="verification_index_")
        try:
            env = dict(os.environ, GIT_INDEX_FILE=os.path.join(tmp, "index"))
            code, _, err = run_git(["read-tree", "HEAD"], env=env, cwd=cwd)
            pathspec = ["--", "."]
            if inside:
                # an evidence directory inside the repository is left out of the tree. If .gitignore already ignores it,
                # naming it in an exclude pathspec makes `git add` fail ("paths are ignored"), so it is named only when
                # git does not ignore it (found in CI run 35215316126, where the evidence directory is ignored)
                ignored, _, err_ci = run_git(["check-ignore", "-q", rel_evidence + "/"], cwd=cwd)
                if ignored not in (0, 1):
                    out["problems"].append("git check-ignore failed (%d): %s" % (ignored, err_ci))
                    return out
                if ignored == 1:
                    pathspec.append(":(exclude)" + rel_evidence)
            if code == 0:
                code, _, err = run_git(["add", "-A"] + pathspec, env=env, cwd=cwd)
            if code == 0:
                code, wt, err = run_git(["write-tree"], env=env, cwd=cwd)
            if code != 0:
                out["problems"].append("could not compute the working-tree git tree (%d): %s" % (code, err))
                return out
            out["working_tree_git_tree"] = wt.decode().strip()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        # RC-B4: the independently computed tree must be explained by the change inventory. Every path in which the git tree
        # of the working files differs from the commit's tree must appear in the inventory; a clean inventory with a
        # different tree is therefore invalid. (The inventory may list more - e.g. a file whose only difference is line
        # endings git normalises away.)
        code, delta, err = run_git(["diff-tree", "-r", "-z", "--no-renames", "--name-only", out["commit_tree"], out["working_tree_git_tree"]], cwd=cwd)
        if code != 0:
            out["problems"].append("git diff-tree of the commit tree and the working-files tree failed (%d): %s" % (code, err))
            return out
        delta_paths = sorted(p.decode("utf-8", "surrogateescape") for p in delta.split(b"\0") if p)
        listed = {c["path"] for c in changes} | {c["orig_path"] for c in changes if c.get("orig_path")}
        unlisted = [p for p in delta_paths if p not in listed]
        out["tree_delta_paths"] = delta_paths
        if unlisted:
            out["problems"].append("the git tree of the working files differs from the commit in %d path(s) the change inventory does "
                                   "not list: %s" % (len(unlisted), "; ".join(unlisted[:20])))
        out["core_migration_head"] = self.core_head()
        out["valid"] = not out["problems"]
        out["working_tree_clean"] = bool(out["valid"] and not changes and out["working_tree_git_tree"] == out["commit_tree"])
        if not out["valid"]:
            out["tested"] = "IDENTITY NOT ESTABLISHED: " + "; ".join(out["problems"])
        elif not changes and out["working_tree_git_tree"] == out["commit_tree"]:
            out["tested"] = "commit %s exactly" % out["commit"]
        else:
            out["tested"] = "commit %s plus the listed working-tree changes; git tree of the working files %s" % (out["commit"], out["working_tree_git_tree"])
        return out

    @staticmethod
    def core_head():
        names = sorted(n[:-3] for n in os.listdir(os.path.join(ROOT, "core", "migrations")) if re.match(r"^\d{4}_\w+\.py$", n))
        return names[-1] if names else None

    def environment(self):
        env = {"platform": platform.platform(), "python": sys.version.split()[0], "python_executable": sys.executable,
               "virtual_environment": sys.prefix != sys.base_prefix}
        for mod in ("django", "psycopg"):
            try:
                env[mod] = __import__(mod).__version__
            except Exception as exc:
                env[mod] = "unavailable: %s" % exc
        ci = {k: os.environ[k] for k in ("CI", "GITHUB_ACTIONS", "GITHUB_REPOSITORY", "GITHUB_SHA", "GITHUB_REF", "GITHUB_RUN_ID",
                                          "GITHUB_RUN_ATTEMPT", "GITHUB_WORKFLOW", "RUNNER_OS") if k in os.environ}
        env["ci"] = ci or None
        return env

    # ------------------------------------------------------------------ database administration
    def connect(self, dbname=None):
        import psycopg
        return psycopg.connect(host=os.environ.get("INTEVIA_POSTGRES_HOST", "127.0.0.1"), port=os.environ.get("INTEVIA_POSTGRES_PORT", "5432"),
                               user=os.environ["INTEVIA_POSTGRES_USER"], password=os.environ["INTEVIA_POSTGRES_PASSWORD"],
                               dbname=dbname or os.environ.get("INTEVIA_POSTGRES_DB", "postgres"), autocommit=True)

    def server(self, conn):
        row = conn.execute("SELECT version(), pg_postmaster_start_time(), current_user").fetchone()
        out = {"version": row[0], "postmaster_start": row[1].isoformat(), "role": row[2]}
        try:
            out["system_identifier"] = str(conn.execute("SELECT system_identifier FROM pg_control_system()").fetchone()[0])
        except Exception as exc:
            out["system_identifier"] = "unavailable: %s" % type(exc).__name__
        return out

    def oid_of(self, name, conn=None):
        if conn is not None:
            row = conn.execute("SELECT oid FROM pg_database WHERE datname = %s", (name,)).fetchone()
            return int(row[0]) if row else None
        with self.connect() as c:
            return self.oid_of(name, c)

    def lock_session(self):
        """A connection to the common lock database, whatever INTEVIA_POSTGRES_DB is set to (RC-O1)."""
        try:
            return self.connect(dbname=LOCK_DATABASE)
        except Exception as exc:
            raise RouteRefused("the route's lock database %r is not reachable by this role (%s: %s); the run was not started "
                               "unserialised" % (LOCK_DATABASE, type(exc).__name__, exc))

    def acquire_lock(self):
        self.lock_conn = self.lock_session()
        got = self.lock_conn.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,)).fetchone()[0]
        if not got:
            self.lock_conn.close()
            self.lock_conn = None
            raise RouteRefused("another verification route run holds the advisory lock on this server")

    def lock_record(self):
        row = self.lock_conn.execute("SELECT current_database(), (SELECT oid FROM pg_database WHERE datname = current_database()), pg_backend_pid()").fetchone()
        return {"key": LOCK_KEY, "held": True, "database": row[0], "database_oid": int(row[1]), "backend_pid": int(row[2]),
                "scope": "serialises verification route runs on this server with each other; not other clients, not an ownership check"}

    def release_lock(self):
        if self.lock_conn is not None:
            try:
                self.lock_conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
            finally:
                self.lock_conn.close()
                self.lock_conn = None

    # ------------------------------------------------------------------ steps
    def django_env(self, db_name, sid, isolation=True, mutation=None):
        env = dict(os.environ, DJANGO_SETTINGS_MODULE="intevia.test_settings", INTEVIA_S015_TEST_ROUTE=ROUTE, INTEVIA_DATABASE_ENGINE="postgresql",
                   INTEVIA_POSTGRES_TEST_DB=db_name, INTEVIA_POSTGRES_DB=os.environ.get("INTEVIA_POSTGRES_DB", "postgres"),
                   PYTHONPATH=os.pathsep.join([ROOT, os.path.join(ROOT, "tests")]), PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
                   VERIFICATION_EXPECTED_CORE_HEAD=self.core_head() or "")
        env[NONCE_ENV] = self.nonce
        env[RECEIPT_ENV] = self.path(sid + "_db_receipts.jsonl")
        env["VERIFICATION_ISOLATION_JSON"] = self.path(sid + "_isolation.jsonl")
        env.pop("VERIFICATION_MUTATION", None)
        if mutation:
            env["VERIFICATION_MUTATION"] = mutation
        return env

    def run_logged(self, cmd, log_path, env):
        with open(log_path, "x", encoding="utf-8", newline="\n") as log:
            log.write("COMMAND: " + " ".join(cmd[1:]) + "\n")
            log.flush()
            proc = subprocess.run(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env)
            log.write("\nEXIT_STATUS: %d\n" % proc.returncode)
        return proc.returncode

    def db_name(self, step):
        code = STEP_CODES.get(step, step.lower().replace("-", "_"))
        name = "%sv%s_%s" % (DB_PREFIX, self.run_id, code)
        if len(name) > 63:
            raise RuntimeError("database name %r is longer than 63 characters" % name)
        return name

    @staticmethod
    def classify(out, code, expect_fail=False):
        """(outcome, reason) from parsed results. INCOMPLETE unless every collected test has one lawful terminal outcome."""
        if not out["reconciled"]:
            bad = [k for k, v in out["reconciliation"].items() if not v["ok"]]
            return "INCOMPLETE", "the log does not account for every collected test exactly once (%s)" % "; ".join(bad)
        bodies = [t["result"] for t in out["tests"]]
        if expect_fail:
            if bodies and all(b == "FAIL" for b in bodies) and out["summary"]["errors"] == 0 and code != 0:
                return "PASS", ""
            return "FAIL", "not every target failed by assertion (outcomes %s)" % sorted(set(bodies))
        if any(b in ("FAIL", "ERROR", "SUBTEST FAILURES", "NOT REACHED (setup error)") for b in bodies) or \
                any(t["teardown_errors"] or t["setup_errors"] or t["subtest_failures"] or t["subtest_errors"] for t in out["tests"]):
            return "FAIL", "tests did not all pass (including setup, sub-test or teardown errors)"
        if any(b != "ok" for b in bodies):
            return "INCOMPLETE", "a collected test did not run to an ok outcome (%s)" % sorted(set(b for b in bodies if b != "ok"))
        if code != 0 or not (out["summary"]["final"] or "").startswith("OK"):
            return "INCOMPLETE", "the runner's verdict or exit status disagrees with the per-test outcomes"
        return "PASS", ""

    def step_offline(self):
        step = {"id": "OFFLINE", "scope": "verification.selftest_parser, verification.selftest_route, verification.selftest_recording, verification.selftest_bootstrap (no database)"}
        log = self.path("OFFLINE_test_output.log")
        env = dict(os.environ, PYTHONPATH=ROOT, PYTHONIOENCODING="utf-8")
        code = self.run_logged([sys.executable, "-m", "unittest", "-v", "verification.selftest_parser", "verification.selftest_route", "verification.selftest_recording", "verification.selftest_bootstrap"], log, env)
        out = parse_results.parse(read_text(log), None, log)
        with open(self.path("OFFLINE_results.json"), "x", encoding="utf-8") as f:
            json.dump(out, f, indent=1)
        step["tests"] = {"ran": out["summary"]["ran"], "verdict": out["summary"]["final"], "exit": code, "reconciled": out["reconciled"]}
        step["outcome"], reason = self.classify(out, code)
        if reason:
            step["reason"] = reason
        return step

    def step_suite(self, sid, mode, args, isolation=True, mutation=None, expect_fail=False):
        step = {"id": sid, "scope": {"mode": mode, "args": args}}
        db = self.db_name(sid)
        step["database"] = db
        env = self.django_env(db, sid, isolation, mutation)
        collection_path = self.path(sid + "_collection.json")
        c = subprocess.run([sys.executable, "-c", COLLECT, mode, collection_path, *args], cwd=ROOT, capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
        if c.returncode != 0 or not os.path.exists(collection_path):
            with open(self.path(sid + "_collection_error.txt"), "x", encoding="utf-8") as f:
                f.write(c.stdout + c.stderr)
            step["outcome"], step["reason"] = "INCOMPLETE", "collection failed (exit %d)" % c.returncode
            return step
        collection = json.loads(read_text(collection_path))
        step["collection"] = {"count": collection["count"], "digest": collection["digest"], "failed_loads": collection["failed_loads"]}
        if collection.get("run_nonce") != self.nonce or collection["failed_loads"] or not collection["count"]:
            step["outcome"], step["reason"] = "INCOMPLETE", "collection not bound to this run, a module failed to load, or nothing was collected"
            return step
        if self.oid_of(db) is not None:
            step["outcome"], step["reason"] = "INCOMPLETE", "a database named %s already exists; the step was not run and it was left untouched" % db
            return step
        labels = list(args) if mode == "label" else ["tests", "--top-level-directory", "tests", "--pattern", args[0]]
        runner = MUTATION_RUNNER if mutation else ISOLATION_RUNNER
        log = self.path(sid + "_test_output.log")
        self.attempted.append((sid, db, env[RECEIPT_ENV]))
        code = self.run_logged([sys.executable, "manage.py", "test", *labels, "--testrunner", runner, "--noinput", "-v", "2"], log, env)
        out = parse_results.parse(read_text(log), collection["ids"], log)
        with open(self.path(sid + "_results.json"), "x", encoding="utf-8") as f:
            json.dump(out, f, indent=1)
        step["tests"] = {"ran": out["summary"]["ran"], "verdict": out["summary"]["final"], "exit": code, "reconciled": out["reconciled"],
                         "by_outcome": out["test_totals_by_result"], "accounting_violations": out["accounting_violations"],
                         "not_ok": [{"id": t["id"], "result": t["result"], "exception": t["body_exception"]} for t in out["tests"] if t["result"] != "ok"]}
        if mutation:
            step["mutation_applied"] = ("VERIFICATION MUTATION APPLIED: %s" % mutation) in read_text(log)
        outcome, reason = self.classify(out, code, expect_fail)
        if expect_fail and not step.get("mutation_applied") and outcome == "PASS":
            outcome, reason = "FAIL", "the mutation was not applied"
        if outcome != "INCOMPLETE" and isolation:
            step["isolation"] = self.read_isolation(env["VERIFICATION_ISOLATION_JSON"], collection["ids"])
            if step["isolation"]["verdict"] is None:
                outcome, reason = "INCOMPLETE", "isolation evidence missing or not bound to this run: %s" % step["isolation"]["problem"]
            elif step["isolation"]["verdict"] != "PASS" and outcome == "PASS":
                outcome, reason = "FAIL", "isolation not established"
        step["outcome"] = outcome
        if expect_fail:
            step["expectation"] = "every target test fails by assertion under the mutation"
        if reason:
            step["reason"] = reason
        return step

    def read_isolation(self, path, collected):
        """Recompute the isolation verdict from this run's own records (C-A1-B4); never trust a summary line alone."""
        res = {"verdict": None, "problem": None}
        if not os.path.exists(path):
            res["problem"] = "no isolation file"
            return res
        records = []
        for n, line in enumerate(read_text(path).splitlines(), 1):
            if line.strip():
                try:
                    records.append(json.loads(line))
                except ValueError:
                    res["problem"] = "line %d is not JSON" % n
                    return res
        if not records or any(r.get("run_nonce") != self.nonce for r in records):
            res["problem"] = "records absent or not all bound to this run"
            return res
        cp0 = [r for r in records if r.get("checkpoint") == "CP-0"]
        cpa = [r for r in records if r.get("checkpoint") == "CP-A"]
        summ = [r for r in records if r.get("checkpoint") == "SUMMARY"]
        if len(cp0) != 1 or len(summ) != 1:
            res["problem"] = "expected exactly one CP-0 and one SUMMARY record, found %d and %d" % (len(cp0), len(summ))
            return res
        ids = [r.get("test") for r in cpa]
        lawful_na = sorted(r["test"] for r in cpa if r.get("applicable") is False and r.get("database_access") == "forbidden")
        established = [r["test"] for r in cpa if r.get("established") is True and r.get("applicable") is not False]
        reasons = []
        if not cp0[0].get("requirements_met"):
            reasons.append("CP-0 requirements not met")
        if len(ids) != len(set(ids)):
            reasons.append("duplicate CP-A checkpoints")
        if set(ids) != set(collected):
            reasons.append("CP-A identities differ from the collected tests (missing %d, extra %d)" % (len(set(collected) - set(ids)), len(set(ids) - set(collected))))
        unresolved = sorted(set(ids) - set(established) - set(lawful_na))
        if unresolved:
            reasons.append("%d applicable checkpoint(s) not established" % len(unresolved))
        verdict = "PASS" if not reasons else "NOT ESTABLISHED"
        if summ[0].get("isolation_verdict") != verdict:
            reasons.append("the runner's SUMMARY verdict %r disagrees with the recomputed verdict" % summ[0].get("isolation_verdict"))
            verdict = "NOT ESTABLISHED"
        res.update(verdict=verdict, reasons=reasons, checkpoints=len(cpa), collected=len(collected), applicable=len(cpa) - len(lawful_na),
                   established=len(established), not_applicable=len(lawful_na), not_applicable_ids=lawful_na)
        return res

    # ------------------------------------------------------------------ database lifecycle (C-A1-B2, C-A1-B3; RC-B2, RC-B3)
    def create_fixture(self, sid):
        """Create one route-owned database. The record is registered BEFORE any SQL, so an exception at any later point
        still leaves a truthful record: ATTEMPTED (no CREATE issued), CREATE ISSUED (outcome unknown), CREATED (exists,
        identity not confirmed) or CONFIRMED (oid read on the creating connection). Returns the record; raises on failure."""
        name = self.db_name(sid)
        rec = {"step": sid, "name": name, "state": "ATTEMPTED", "oid": None}
        self.lifecycle.append(rec)
        with self.connect() as c:
            existing = self.oid_of(name, c)
            if existing is not None:
                rec.update(state="NOT CREATED - a database of this name already existed", existing_oid=existing)
                raise RouteRefused("fixture database %s already exists (oid %s); left untouched" % (name, existing))
            rec["state"] = "CREATE ISSUED"
            try:
                c.execute('CREATE DATABASE "%s"' % name)
            except Exception as exc:
                if getattr(exc, "sqlstate", None) == "42P04" or type(exc).__name__ == "DuplicateDatabase":
                    rec["state"] = "NOT CREATED - another client created the name first"
                raise
            rec["state"] = "CREATED"
            oid = self.oid_of(name, c)
            if oid is None:
                raise RuntimeError("fixture database %s not found after CREATE DATABASE" % name)
            rec.update(state="CONFIRMED", oid=oid)
        return rec

    def guarded_drop(self, name, owned_oid):
        """The only way the route drops a database: on one connection, read the current oid and drop only if it equals the
        confirmed creation oid. Returns (state, resolved). See the module docstring for the residual limit."""
        with self.connect() as c:
            current = self.oid_of(name, c)
            if current is None:
                return "ABSENT - no longer present (owned oid %s)" % owned_oid, True
            if current != owned_oid:
                return "REPLACED - present with oid %s, not the owned oid %s; left untouched" % (current, owned_oid), False
            c.execute('DROP DATABASE "%s"' % name.replace('"', ""))
            gone = self.oid_of(name, c) is None
        return (("DROPPED BY THE ROUTE (owned oid %s)" % owned_oid) if gone else "UNRESOLVED - drop issued but the database is still present"), gone

    def write_created_receipt(self, name, oid, sid):
        receipts_path = self.path(sid + "_db_receipts.jsonl")
        with open(receipts_path, "x", encoding="utf-8") as f:
            f.write(json.dumps({"event": "created", "run_nonce": self.nonce, "name": name, "oid": oid}) + "\n")
        return receipts_path

    def step_ownership(self):
        """Live checks of the database safeguards against this server (C-A1-B3)."""
        step = {"id": "OWNERSHIP", "checks": {}}
        ok = True
        # 1. collision: a database the runner did not create is refused and left untouched
        collide = self.create_fixture("OWN-COLLIDE")
        env = self.django_env(collide["name"], "OWN-COLLIDE")
        log = self.path("OWN-COLLIDE_test_output.log")
        code = self.run_logged([sys.executable, "manage.py", "test", OWNERSHIP_PROBE_TEST, "--testrunner", ISOLATION_RUNNER, "--noinput", "-v", "2"], log, env)
        receipts, problem = read_receipts(env[RECEIPT_ENV], self.nonce)
        parsed = parse_results.parse(read_text(log))
        check = {"runner_exit_nonzero": code != 0,
                 "collision_refused_receipt": any(r["event"] == "collision_refused" and r.get("existing_oid") == collide["oid"] for r in receipts),
                 "no_created_receipt": not any(r["event"] == "created" for r in receipts),
                 # RC-O2: no test reported ANY outcome (ok, failure, error or skip) and the runner reported no test run
                 "no_test_outcome_reported": not any(t["status_events"] for t in parsed["tests"]) and not parsed["summary"]["ran"],
                 "database_still_present_same_oid": self.oid_of(collide["name"]) == collide["oid"], "receipts_readable": problem is None}
        step["checks"]["collision"] = check
        ok &= all(check.values())
        # 2. replacement: a receipt for oid A does not authorise dropping the same name at oid B. The transition from A to B
        #    is itself a guarded drop (RC-B3): if A has been replaced by anyone else, it is left untouched and the check fails.
        first = self.create_fixture("OWN-REPLACE")
        receipts_path = self.write_created_receipt(first["name"], first["oid"], "OWN-REPLACE")
        state, dropped = self.guarded_drop(first["name"], first["oid"])
        check = {"transition_dropped_only_the_owned_oid": dropped and state.startswith("DROPPED")}
        if check["transition_dropped_only_the_owned_oid"]:
            first["state"] = state
            second = self.create_fixture("OWN-REPLACE")
            disposition = self.cleanup_one("OWN-REPLACE", second["name"], receipts_path)
            check.update(oid_changed=first["oid"] != second["oid"],
                         disposition_is_replaced_unresolved=disposition["state"].startswith("REPLACED") and not disposition["resolved"],
                         database_still_present_second_oid=self.oid_of(second["name"]) == second["oid"])
        else:
            check["transition_state"] = state
        step["checks"]["replacement"] = check
        ok &= all(v for k, v in check.items() if k != "transition_state") and "transition_state" not in check
        # 3. owned: the receipt's oid authorises the drop, and absence is confirmed
        owned = self.create_fixture("OWN-OWNED")
        receipts_path = self.write_created_receipt(owned["name"], owned["oid"], "OWN-OWNED")
        disposition = self.cleanup_one("OWN-OWNED", owned["name"], receipts_path)
        if disposition["resolved"] and disposition["state"].startswith("DROPPED"):
            owned["state"] = disposition["state"]
        check = {"disposition_dropped": disposition["state"].startswith("DROPPED") and disposition["resolved"], "database_absent": self.oid_of(owned["name"]) is None}
        step["checks"]["owned_drop"] = check
        ok &= all(check.values())
        step["outcome"] = "PASS" if ok else "FAIL"
        if not ok:
            step["reason"] = "a database safeguard did not behave as required (see checks)"
        return step

    def step_lock(self):
        """Live check of RC-O1: a second route run configured with a DIFFERENT connection database contends for this run's
        lock. Discriminating control: on a session connected to that other database the same key is free, which is what
        let two runs proceed at once when the lock followed INTEVIA_POSTGRES_DB."""
        step = {"id": "LOCK", "checks": {}}
        other = self.create_fixture("LOCK")
        saved = os.environ.get("INTEVIA_POSTGRES_DB")
        os.environ["INTEVIA_POSTGRES_DB"] = other["name"]
        try:
            probe = self.lock_session()
            try:
                contended = probe.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,)).fetchone()[0] is False
                probe_db = probe.execute("SELECT current_database()").fetchone()[0]
            finally:
                probe.close()
            with self.connect() as direct:  # honours INTEVIA_POSTGRES_DB, now the other database
                direct_db = direct.execute("SELECT current_database()").fetchone()[0]
                free_there = direct.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,)).fetchone()[0] is True
                if free_there:
                    direct.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
        finally:
            if saved is None:
                os.environ.pop("INTEVIA_POSTGRES_DB", None)
            else:
                os.environ["INTEVIA_POSTGRES_DB"] = saved
        held = self.lock_conn.execute("SELECT count(*) FROM pg_locks l JOIN pg_database d ON d.oid = l.database WHERE l.locktype = 'advisory' "
                                      "AND l.granted AND l.pid = pg_backend_pid() AND d.datname = %s", (LOCK_DATABASE,)).fetchone()[0]
        step["checks"] = {"second_run_configured_for_another_database_is_refused": contended, "that_run_used_the_lock_database": probe_db == LOCK_DATABASE,
                          "control_key_is_free_on_the_other_database": free_there and direct_db == other["name"],
                          "this_run_holds_the_lock_in_the_lock_database": held == 1}
        step["outcome"] = "PASS" if all(step["checks"].values()) else "FAIL"
        if step["outcome"] != "PASS":
            step["reason"] = "the lock did not serialise route runs configured with different connection databases (see checks)"
        return step

    # ------------------------------------------------------------------ cleanup (C-A1-B2, C-A1-B3)
    def cleanup_one(self, sid, name, receipts_path):
        rec = {"step": sid, "name": name, "resolved": False}
        try:
            receipts, problem = read_receipts(receipts_path, self.nonce)
            rec["receipts"] = [{k: r.get(k) for k in ("event", "oid", "existing_oid", "current_oid", "receipt_oid", "confirmed_absent")} for r in receipts]
            if problem:
                rec["state"] = "UNRESOLVED - receipts not trustworthy: %s" % problem
                return rec
            created = [r for r in receipts if r.get("event") == "created" and r.get("name") == name]
            current = self.oid_of(name)
            rec["current_oid"] = current
            attempted_create = any(r.get("event") == "create_attempt" and r.get("name") == name for r in receipts)
            if not created:
                if current is None:
                    rec.update(state=("ABSENT - creation attempted, nothing present" if attempted_create else "ABSENT - never created by this run"), resolved=True)
                elif attempted_create and not any(r.get("event") == "collision_refused" for r in receipts):
                    rec["state"] = "UNRESOLVED - CREATE was issued but no creation receipt confirms this database's identity; left untouched"
                elif any(r.get("event") == "collision_refused" for r in receipts):
                    rec.update(state="NOT OWNED - pre-existing database, creation refused, left untouched", resolved=True)
                else:
                    rec["state"] = "UNRESOLVED - present without a creation receipt; left untouched"
                return rec
            owned_oid = created[-1]["oid"]
            rec["owned_oid"] = owned_oid
            if current is None:
                rec.update(state="ABSENT - owned database no longer present", resolved=True)
                return rec
            if current != owned_oid:
                rec["state"] = "REPLACED - present with oid %s, not the owned oid %s; left untouched" % (current, owned_oid)
                return rec
            state, resolved = self.guarded_drop(name, owned_oid)  # re-checks on its own connection immediately before DROP
            rec.update(state=state, resolved=resolved)
        except Exception as exc:
            rec["state"] = "UNRESOLVED - %s: %s" % (type(exc).__name__, exc)
        return rec

    def cleanup_fixture(self, fx):
        rec = {"step": "fixture " + fx["step"], "name": fx["name"], "owned_oid": fx["oid"], "lifecycle_state": fx["state"], "resolved": False}
        try:
            if fx["state"].startswith(("DROPPED", "NOT CREATED")) or fx["state"] == "ATTEMPTED":
                rec.update(state=("%s - nothing further to do; any database now under this name was not created by this record" % fx["state"]), resolved=True)
            elif fx["state"] == "CONFIRMED":
                state, resolved = self.guarded_drop(fx["name"], fx["oid"])
                rec.update(state=state, resolved=resolved)
            else:  # CREATE ISSUED or CREATED: a database may exist whose identity was never confirmed
                current = self.oid_of(fx["name"])
                rec["current_oid"] = current
                if current is None:
                    rec.update(state="ABSENT - creation %s, nothing present" % fx["state"], resolved=True)
                else:
                    rec["state"] = "UNRESOLVED - present with oid %s but its creation identity was never confirmed (%s); left untouched" % (current, fx["state"])
        except Exception as exc:
            rec["state"] = "UNRESOLVED - %s: %s" % (type(exc).__name__, exc)
        return rec

    def finalise_cleanup(self):
        """Runs whatever happened before it. Every attempted name keeps its own record; nothing is replaced by a summary."""
        records = [self.cleanup_one(sid, name, receipts_path) for sid, name, receipts_path in self.attempted]
        records += [self.cleanup_fixture(fx) for fx in self.lifecycle]
        if not records:
            return {"outcome": "NOTHING TO CLEAN", "names": []}
        unresolved = [r for r in records if not r["resolved"]]
        return {"outcome": "CLEAN" if not unresolved else "NOT CLEAN", "names": records, "unresolved": len(unresolved)}

    # ------------------------------------------------------------------ orchestration
    def run(self, skip_mutations, launch_token=None, snapshot=None, checkout=None, attested_commit=None):
        summary = {"route": "S015 verification route v0.5", "run_id": self.run_id, "run_nonce": self.nonce, "started": now()}
        self.say("S015 VERIFICATION ROUTE v0.5  run %s  %s" % (self.run_id, summary["started"]))
        dependency = resolve_dependency_environment()
        summary["execution"] = {"launch_token": launch_token, "root": os.path.abspath(snapshot or ROOT), "entry": os.path.realpath(__file__), "mode": "snapshot-entry" if snapshot else "checkout-entry", "site_dirs": [dependency["site_packages"]]}
        planned = ["IDENTITY", "OFFLINE", "SELF", "S015", "OWNERSHIP", "LOCK"] + ([] if skip_mutations else ["MUT-" + n for n in MUTATIONS])
        summary["environment"] = self.environment()
        cleanup = {"outcome": "NOT REACHED", "names": []}
        try:
            ident = self.identity(checkout=checkout)
            summary["identity"] = dict(ident)
            if attested_commit is not None:
                summary["identity"]["attested_commit"] = attested_commit
            self.steps.append({"id": "IDENTITY", "outcome": "PASS" if ident["valid"] else "INCOMPLETE", **({} if ident["valid"] else {"reason": "; ".join(ident["problems"])})})
            self.say("tested     : %s" % ident.get("tested", "IDENTITY NOT ESTABLISHED: " + "; ".join(ident["problems"])))
            if not os.environ.get("INTEVIA_POSTGRES_PASSWORD"):
                if sys.stdin.isatty():
                    os.environ["INTEVIA_POSTGRES_PASSWORD"] = getpass.getpass("PostgreSQL password for %s: " % os.environ.get("INTEVIA_POSTGRES_USER"))
                else:
                    raise RuntimeError("INTEVIA_POSTGRES_PASSWORD is not set and no terminal is attached")
            if not os.environ.get("INTEVIA_POSTGRES_USER"):
                raise RuntimeError("INTEVIA_POSTGRES_USER is not set")
            self.acquire_lock()
            summary["server"] = self.server(self.lock_conn)
            summary["advisory_lock"] = self.lock_record()
            self.say("server     : %s" % summary["server"]["version"].split(",")[0])
            mine = [n for (n,) in self.lock_conn.execute("SELECT datname FROM pg_database WHERE datname LIKE %s", (DB_PREFIX + "v" + self.run_id + "\\_%",)).fetchall()]
            if mine:
                raise RouteRefused("databases for this run id already exist and were left untouched: %s" % mine)
            plan = [("OFFLINE", self.step_offline),
                    ("SELF", lambda: self.step_suite("SELF", "label", ["verification.isolation.selfcheck_tests"])),
                    ("S015", lambda: self.step_suite("S015", "discover", [S015_PATTERN])),
                    ("OWNERSHIP", self.step_ownership),
                    ("LOCK", self.step_lock)]
            if not skip_mutations:
                for name, m in MUTATIONS.items():
                    sid = "MUT-" + name
                    plan.append((sid, lambda sid=sid, name=name, m=m: self.step_suite(sid, "label", m["targets"], isolation=False, mutation=name, expect_fail=True)))
            for sid, fn in plan:
                self.say("--- step %s" % sid)
                try:
                    step = fn()
                except Exception as exc:
                    step = {"id": sid, "outcome": "INCOMPLETE", "reason": "step raised %s: %s" % (type(exc).__name__, exc), "traceback": traceback.format_exc()[-2000:]}
                self.steps.append(step)
                self.say("    %s%s" % (step["outcome"], (" - " + step["reason"]) if step.get("reason") else ""))
                if sid == "SELF" and step["outcome"] != "PASS":
                    self.say("    the isolation self-check did not pass: later database steps are not run")
                    break
        except Exception as exc:
            self.steps.append({"id": "SETUP", "outcome": "INCOMPLETE", "reason": "%s: %s" % (type(exc).__name__, exc)})
            self.say("SETUP INCOMPLETE: %s" % exc)
        finally:
            try:
                cleanup = self.finalise_cleanup()
            except Exception as exc:  # should not happen: finalise_cleanup records per-name failures itself
                cleanup = {"outcome": "NOT CLEAN", "names": [{"step": s, "name": n, "resolved": False, "state": "UNRESOLVED - cleanup stage raised %s: %s" % (type(exc).__name__, exc)} for s, n, _ in self.attempted]
                           + [{"step": "fixture " + fx["step"], "name": fx["name"], "resolved": False, "state": "UNRESOLVED - cleanup stage raised %s: %s" % (type(exc).__name__, exc)} for fx in self.lifecycle],
                           "stage_error": "%s: %s" % (type(exc).__name__, exc)}
            try:
                self.release_lock()
            except Exception as exc:
                summary["advisory_lock_release_error"] = str(exc)
        ran = {s["id"] for s in self.steps}
        for sid in planned:
            if sid not in ran:
                self.steps.append({"id": sid, "outcome": "INCOMPLETE", "reason": "not run"})
        outcomes = [s["outcome"] for s in self.steps]
        result = "PASS" if all(o == "PASS" for o in outcomes) else ("INCOMPLETE" if "INCOMPLETE" in outcomes else "FAIL")
        if skip_mutations:
            summary["scope_note"] = "discrimination checks skipped by request"
        cleanup_problem = cleanup["outcome"] not in ("CLEAN", "NOTHING TO CLEAN")
        exit_code = 3 if cleanup_problem else {"PASS": 0, "FAIL": 1, "INCOMPLETE": 2}[result]
        summary.update(steps=self.steps, cleanup=cleanup, result=result, exit_status=exit_code, finished=now())
        self.write(summary)
        return exit_code

    def write(self, summary):
        with open(self.path("summary.json"), "x", encoding="utf-8") as f:
            json.dump(summary, f, indent=1, default=str)
        ident, env = summary.get("identity", {}), summary["environment"]
        lines = ["# S015 verification route v0.5 - %s" % summary["result"], "",
                 "- **Result:** %s (exit status %d); cleanup %s" % (summary["result"], summary["exit_status"], summary["cleanup"]["outcome"]),
                 "- **Tested:** %s" % ident.get("tested", "IDENTITY NOT ESTABLISHED"),
                 "- **Commit tree:** `%s`; working-files git tree `%s`; core migration head `%s`" % (ident.get("commit_tree"), ident.get("working_tree_git_tree"), ident.get("core_migration_head")),
                 "- **Environment:** Python %s, Django %s, psycopg %s, %s" % (env["python"], env.get("django"), env.get("psycopg"), env["platform"]),
                 "- **Server:** %s" % (summary.get("server", {}).get("version", "not reached")),
                 "- **CI:** %s" % (json.dumps(env["ci"]) if env["ci"] else "not a CI run"),
                 "- **Run:** %s (nonce %s), %s to %s" % (summary["run_id"], summary["run_nonce"], summary["started"], summary["finished"]), ""]
        if ident.get("working_tree_changes"):
            lines += ["## Working-tree changes tested", ""] + ["- `%s` %s%s `%s`" % (c["status"], c["path"], (" (from %s)" % c["orig_path"]) if c.get("orig_path") else "", c.get("sha256") or c.get("kind")) for c in ident["working_tree_changes"]] + [""]
        lines += ["## Steps", "", "| Step | Outcome | Tests | Isolation | Note |", "|---|---|---|---|---|"]
        for s in summary["steps"]:
            t = s.get("tests") if isinstance(s.get("tests"), dict) else None
            tests = ("%s ran, %s" % (t["ran"], t["verdict"])) if t else ""
            iso = s.get("isolation")
            iso_txt = ("%s; %d applicable, %d established; %d not applicable" % (iso["verdict"], iso["applicable"], iso["established"], iso["not_applicable"])) if iso and iso.get("verdict") else ""
            lines.append("| %s | %s | %s | %s | %s |" % (s["id"], s["outcome"], tests, iso_txt, s.get("reason", s.get("expectation", ""))))
        lines += ["", "## Cleanup", ""] + ["- %s (%s): %s" % (n["name"], n.get("step"), n["state"]) for n in summary["cleanup"]["names"]]
        lines += ["", "A PASS is evidence at the checked properties for the tested commit and environment. It is not review, landing, external reproduction or acceptance.", ""]
        with open(self.path("SUMMARY.md"), "x", encoding="utf-8") as f:
            f.write("\n".join(lines))
        self.transcript.close()
        entries = []
        for base, _, files in os.walk(self.dir):
            for name in sorted(files):
                if name != "MANIFEST.sha256":
                    p = os.path.join(base, name)
                    entries.append("%s  %s" % (sha256_file(p), os.path.relpath(p, self.dir).replace(os.sep, "/")))
        with open(self.path("MANIFEST.sha256"), "x", encoding="utf-8") as f:
            f.write("\n".join(sorted(entries, key=lambda e: e[66:])) + "\n")
        print("RESULT: %s  exit status %d  cleanup %s  evidence %s" % (summary["result"], summary["exit_status"], summary["cleanup"]["outcome"], self.dir), flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--evidence-dir", default=None, help="a directory that does not exist yet")
    ap.add_argument("--run-id", default=None, help="1-20 lowercase letters or digits; default: time-based")
    ap.add_argument("--snapshot", default=None)
    ap.add_argument("--checkout", default=None)
    ap.add_argument("--launch-token", default=None)
    ap.add_argument("--commit", default=None)
    ap.add_argument("--skip-mutations", action="store_true")
    a = ap.parse_known_args(argv)[0]
    run_id = a.run_id or (datetime.datetime.now(datetime.timezone.utc).strftime("%y%m%d%H%M%S") + uuid.uuid4().hex[:4])
    if not re.fullmatch(r"[a-z0-9]{1,20}", run_id):
        ap.error("--run-id must be 1-20 lowercase letters or digits")
    evidence = a.evidence_dir or os.path.join(ROOT, "verification-evidence", run_id)
    try:
        route = Route(evidence, run_id)
    except RouteRefused as exc:
        print("REFUSED: %s" % exc, file=sys.stderr, flush=True)
        sys.exit(2)
    sys.exit(route.run(a.skip_mutations, launch_token=a.launch_token, snapshot=a.snapshot, checkout=a.checkout, attested_commit=a.commit))


if __name__ == "__main__":
    main()
