"""Self-test of the verification route's safeguards (Change C v0.2): A1 findings C-A1-B1 to C-A1-B5.

No database server and no network: route methods run against controlled substitutes (fake cursors and connections,
temporary git repositories). Each class pairs A1's counterexamples with positive controls, so the route is shown both to
accept valid evidence and to reject defective evidence. The live database behaviour of the same safeguards is checked by
the route's OWNERSHIP step.

Run: python -m unittest -v verification.selftest_route   (needs Django installed; no settings or database are used)
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from verification import ownership
from verification import run as route_mod
from verification.parse_results import parse

TID = "probe.C.test_a"
TAIL = "\n" + "-" * 70 + "\nRan 1 test in 0.001s\n\nOK\nEXIT_STATUS: 0\n"


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="selftest_route_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def route(self, name="ev", run_id="probe"):
        r = route_mod.Route(os.path.join(self.tmp, name), run_id)
        self.addCleanup(lambda: r.transcript.close())
        return r


# ------------------------------------------------------------------ B1
class B1StepGating(_Tmp):
    def test_b1_missing_status_step_is_incomplete_not_pass(self):
        r = self.route()

        def fake_collection(cmd, **kw):
            with open(cmd[4], "x") as f:
                json.dump({"ids": [TID], "count": 1, "digest": "d", "failed_loads": [], "run_nonce": r.nonce}, f)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        def fake_log(cmd, path, env):
            with open(path, "x") as f:
                f.write("test_a (probe.C.test_a) ...\n" + TAIL)
            return 0

        with mock.patch.object(route_mod.subprocess, "run", side_effect=fake_collection), mock.patch.object(r, "run_logged", side_effect=fake_log), \
                mock.patch.object(r, "oid_of", return_value=None), mock.patch.object(r, "read_isolation", return_value={"verdict": "PASS"}):
            step = r.step_suite("S015", "label", [TID])
        self.assertEqual(step["outcome"], "INCOMPLETE")

    def test_b1_positive_ok_is_pass_and_teardown_error_is_fail(self):
        ok = parse("test_a (probe.C.test_a) ... ok\n" + TAIL, [TID])
        self.assertEqual(route_mod.Route.classify(ok, 0)[0], "PASS")
        td = ("test_a (probe.C.test_a) ... ok\ntest_a (probe.C.test_a) ... ERROR\n\n" + "=" * 70 + "\nERROR: test_a (probe.C.test_a)\n" + "-" * 70 +
              '\nTraceback (most recent call last):\n  File "x.py", line 9, in _post_teardown\n    raise RuntimeError("flush")\nRuntimeError: flush\n\n' +
              "-" * 70 + "\nRan 1 test in 0.001s\n\nFAILED (errors=1)\nEXIT_STATUS: 1\n")
        parsed = parse(td, [TID])
        self.assertTrue(parsed["reconciled"])
        self.assertEqual(route_mod.Route.classify(parsed, 1)[0], "FAIL")


# ------------------------------------------------------------------ B2
class B2CleanupRecords(_Tmp):
    def _run_with_cleanup_failure(self):
        r = self.route()
        fake_lock = mock.MagicMock()
        fake_lock.execute.return_value.fetchall.return_value = []

        def lock():
            r.lock_conn = fake_lock

        def suite(sid, *a, **k):
            r.attempted.append((sid, r.db_name(sid), r.path(sid + "_db_receipts.jsonl")))
            return {"id": sid, "outcome": "PASS"}

        env = {"INTEVIA_POSTGRES_USER": "probe", "INTEVIA_POSTGRES_PASSWORD": "not-a-credential"}
        with mock.patch.dict(os.environ, env), mock.patch.object(r, "identity", return_value={"valid": True, "problems": [], "tested": "x"}), \
                mock.patch.object(r, "acquire_lock", side_effect=lock), mock.patch.object(r, "server", return_value={"version": "simulated"}), \
                mock.patch.object(r, "step_offline", return_value={"id": "OFFLINE", "outcome": "PASS"}), mock.patch.object(r, "step_suite", side_effect=suite), \
                mock.patch.object(r, "step_ownership", return_value={"id": "OWNERSHIP", "outcome": "PASS"}), \
                mock.patch.object(r, "oid_of", side_effect=RuntimeError("simulated server unavailable during cleanup")), \
                contextlib.redirect_stdout(io.StringIO()):
            code = r.run(skip_mutations=True)
        return r, code, json.loads(route_mod.read_text(r.path("summary.json")))

    def test_b2_cleanup_exception_keeps_every_name_and_exits_3(self):
        r, code, summary = self._run_with_cleanup_failure()
        self.assertEqual(code, 3)
        self.assertEqual(summary["cleanup"]["outcome"], "NOT CLEAN")
        names = {n["name"]: n for n in summary["cleanup"]["names"]}
        self.assertEqual(set(names), {r.db_name("SELF"), r.db_name("S015")})
        self.assertTrue(all(n["state"].startswith("UNRESOLVED") and "simulated server unavailable" in n["state"] for n in names.values()))
        self.assertNotIn("SETUP", [s["id"] for s in summary["steps"]])

    def test_b2_positive_clean_cleanup_exits_0(self):
        r = self.route()
        r.attempted.append(("S015", r.db_name("S015"), r.path("S015_db_receipts.jsonl")))
        with mock.patch.object(r, "oid_of", return_value=None):
            result = r.finalise_cleanup()
        self.assertEqual(result["outcome"], "CLEAN")
        self.assertEqual(result["names"][0]["state"], "ABSENT - never created by this run")


# ------------------------------------------------------------------ B3
class _Conn:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.log.append(sql)
        return mock.MagicMock()


class B3RouteCleanupOwnership(_Tmp):
    def _receipts(self, r, name, *records):
        p = r.path("S015_db_receipts.jsonl")
        with open(p, "w") as f:
            for rec in records:
                f.write(json.dumps(dict(rec, run_nonce=r.nonce, name=name)) + "\n")
        return p

    def _cleanup(self, r, name, path, oids):
        sql = []
        seq = iter(oids)
        with mock.patch.object(r, "oid_of", side_effect=lambda *a, **k: next(seq)), mock.patch.object(r, "connect", return_value=_Conn(sql)):
            return r.cleanup_one("S015", name, path), sql

    def test_b3_no_receipt_present_database_is_left_untouched(self):
        r = self.route(); name = r.db_name("S015")
        rec, sql = self._cleanup(r, name, r.path("none.jsonl"), [4242])
        self.assertFalse(rec["resolved"]); self.assertEqual(sql, [])

    def test_b3_replaced_database_is_left_untouched(self):
        r = self.route(); name = r.db_name("S015")
        rec, sql = self._cleanup(r, name, self._receipts(r, name, {"event": "created", "oid": 100}), [200])
        self.assertTrue(rec["state"].startswith("REPLACED")); self.assertFalse(rec["resolved"]); self.assertEqual(sql, [])

    def test_b3_collision_refused_database_is_not_owned_and_untouched(self):
        r = self.route(); name = r.db_name("S015")
        rec, sql = self._cleanup(r, name, self._receipts(r, name, {"event": "collision_refused", "existing_oid": 7}), [7])
        self.assertTrue(rec["resolved"]); self.assertTrue(rec["state"].startswith("NOT OWNED")); self.assertEqual(sql, [])

    def test_b3_other_runs_receipts_are_not_trusted(self):
        r = self.route(); name = r.db_name("S015")
        p = r.path("S015_db_receipts.jsonl")
        with open(p, "w") as f:
            f.write(json.dumps({"event": "created", "oid": 100, "name": name, "run_nonce": "another-run"}) + "\n")
        rec, sql = self._cleanup(r, name, p, [100])
        self.assertFalse(rec["resolved"]); self.assertEqual(sql, [])

    def test_b3_positive_owned_database_is_dropped_by_oid(self):
        r = self.route(); name = r.db_name("S015")
        rec, sql = self._cleanup(r, name, self._receipts(r, name, {"event": "created", "oid": 100}), [100, 100, None])
        self.assertTrue(rec["resolved"]); self.assertTrue(rec["state"].startswith("DROPPED")); self.assertEqual(len([s for s in sql if s.startswith("DROP DATABASE")]), 1)


class _Cursor:
    def __init__(self, sql, existing):
        self.sql, self.existing, self._row = sql, existing, None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, query, params=None):
        self.sql.append(query)
        if query.startswith("SELECT oid FROM pg_database"):
            self._row = (self.existing[params[0]],) if params[0] in self.existing else None
        elif query.startswith("DROP DATABASE"):
            self.existing.pop(query.split()[-1].strip('"'), None)

    def fetchone(self):
        return self._row


class B3RunnerCreationAndDestruction(_Tmp):
    NAME = "test_intevia_living_organism_vprobe_s015"

    def _creation(self, sql, existing, duplicate_on_create=False):
        from django.db.backends.base.creation import BaseDatabaseCreation

        class Creation(BaseDatabaseCreation):
            def _get_test_db_name(inner):
                return self.NAME

            def _nodb_cursor(inner):
                return _Cursor(sql, existing)

            def sql_table_creation_suffix(inner):
                return ""

            def log(inner, msg):
                pass

            def _execute_create_test_db(inner, cursor, parameters, keepdb=False):
                cursor.execute("CREATE DATABASE %s" % parameters["dbname"])
                if duplicate_on_create:
                    raise RuntimeError('database "%s" already exists' % self.NAME)
                existing[self.NAME] = 5555

        return Creation(SimpleNamespace(ops=SimpleNamespace(quote_name=lambda x: '"%s"' % x), settings_dict={"NAME": "postgres", "TEST": {}}))

    def _env(self):
        return mock.patch.dict(os.environ, {ownership.RECEIPT_ENV: os.path.join(self.tmp, "receipts.jsonl"), ownership.NONCE_ENV: "nonce-1"})

    def receipts(self):
        return ownership.read_receipts(os.path.join(self.tmp, "receipts.jsonl"), "nonce-1")[0]

    def test_b3_control_unmodified_django_clobbers_on_collision(self):
        sql, existing = [], {self.NAME: 1}
        c = self._creation(sql, existing, duplicate_on_create=True)
        attempts = {"n": 0}
        original = c._execute_create_test_db

        def once(cursor, parameters, keepdb=False):
            attempts["n"] += 1
            cursor.execute("CREATE DATABASE %s" % parameters["dbname"])
            if attempts["n"] == 1:
                raise RuntimeError("already exists")
        c._execute_create_test_db = once
        c._create_test_db(verbosity=0, autoclobber=True, keepdb=False)
        self.assertTrue(any(s.startswith("DROP DATABASE") for s in sql), sql)  # the hazard A1 identified

    def test_b3_existing_database_is_refused_without_create_or_drop(self):
        sql, existing = [], {self.NAME: 1}
        c = self._creation(sql, existing)
        with self._env():
            ownership.install(c)
            with self.assertRaises(ownership.CollisionRefused):
                c._create_test_db(verbosity=0, autoclobber=True, keepdb=False)
            self.assertEqual([r["event"] for r in self.receipts()], ["collision_refused"])
        self.assertFalse(any(s.startswith(("DROP", "CREATE")) for s in sql)); self.assertEqual(existing, {self.NAME: 1})

    def test_b3_concurrent_duplicate_raises_and_nothing_is_dropped(self):
        sql, existing = [], {}
        c = self._creation(sql, existing, duplicate_on_create=True)
        with self._env():
            ownership.install(c)
            with self.assertRaises(RuntimeError):
                c._create_test_db(verbosity=0, autoclobber=True, keepdb=False)
            self.assertEqual(self.receipts(), [])
        self.assertFalse(any(s.startswith("DROP") for s in sql))

    def test_b3_replaced_database_is_not_destroyed_by_the_runner(self):
        sql, existing = [], {}
        c = self._creation(sql, existing)
        with self._env():
            ownership.install(c)
            c._create_test_db(verbosity=0, autoclobber=True, keepdb=False)
            existing[self.NAME] = 9999  # replaced by another actor
            c._destroy_test_db(self.NAME, verbosity=0)
            events = [r["event"] for r in self.receipts()]
        self.assertEqual(events, ["created", "destroy_refused"]); self.assertFalse(any(s.startswith("DROP") for s in sql))

    def test_b3_positive_owned_database_is_created_then_destroyed(self):
        sql, existing = [], {}
        c = self._creation(sql, existing)
        with self._env():
            ownership.install(c)
            self.assertEqual(c._create_test_db(verbosity=0, autoclobber=True, keepdb=False), self.NAME)
            c._destroy_test_db(self.NAME, verbosity=0)
            recs = self.receipts()
        self.assertEqual([r["event"] for r in recs], ["created", "destroyed"]); self.assertTrue(recs[1]["confirmed_absent"])


# ------------------------------------------------------------------ B4
class B4FreshEvidence(_Tmp):
    def test_b4_existing_evidence_directory_is_refused_and_preserved(self):
        d = os.path.join(self.tmp, "reused"); os.mkdir(d)
        with open(os.path.join(d, "transcript.txt"), "w") as f:
            f.write("EARLIER RUN EVIDENCE\n")
        with self.assertRaises(route_mod.RouteRefused):
            route_mod.Route(d, "probe")
        self.assertEqual(route_mod.read_text(os.path.join(d, "transcript.txt")), "EARLIER RUN EVIDENCE\n"); self.assertEqual(os.listdir(d), ["transcript.txt"])

    def _iso(self, r, records):
        p = r.path("S015_isolation.jsonl")
        with open(p, "w") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")
        return p

    def _good(self, r, ids):
        n = r.nonce
        return ([{"checkpoint": "CP-0", "requirements_met": True, "run_nonce": n}] +
                [{"checkpoint": "CP-A", "test": t, "established": True, "applicable": True, "database_access": "permitted", "run_nonce": n} for t in ids] +
                [{"checkpoint": "SUMMARY", "isolation_verdict": "PASS", "run_nonce": n}])

    def test_b4_summary_only_isolation_is_not_pass(self):
        r = self.route()
        res = r.read_isolation(self._iso(r, [{"checkpoint": "SUMMARY", "isolation_verdict": "PASS", "run_nonce": r.nonce}]), [TID])
        self.assertNotEqual(res["verdict"], "PASS")

    def test_b4_records_from_another_run_are_not_evidence(self):
        r = self.route()
        recs = self._good(r, [TID])
        for rec in recs:
            rec["run_nonce"] = "earlier-run"
        self.assertIsNone(r.read_isolation(self._iso(r, recs), [TID])["verdict"])

    def test_b4_duplicate_or_missing_checkpoints_are_not_established(self):
        r = self.route()
        dup = self._good(r, [TID]); dup.insert(1, dict(dup[1]))
        self.assertEqual(r.read_isolation(self._iso(r, dup), [TID])["verdict"], "NOT ESTABLISHED")
        os.remove(r.path("S015_isolation.jsonl"))
        self.assertEqual(r.read_isolation(self._iso(r, self._good(r, [TID])), [TID, "probe.C.test_b"])["verdict"], "NOT ESTABLISHED")

    def test_b4_summary_disagreeing_with_records_is_not_established(self):
        r = self.route()
        recs = self._good(r, [TID]); recs[1]["established"] = False
        self.assertEqual(r.read_isolation(self._iso(r, recs), [TID])["verdict"], "NOT ESTABLISHED")

    def test_b4_positive_current_complete_records_pass(self):
        r = self.route()
        na = {"checkpoint": "CP-A", "test": "probe.C.test_b", "established": None, "applicable": False, "database_access": "forbidden", "run_nonce": r.nonce}
        recs = self._good(r, [TID]); recs.insert(2, na)
        res = r.read_isolation(self._iso(r, recs), [TID, "probe.C.test_b"])
        self.assertEqual((res["verdict"], res["established"], res["not_applicable"]), ("PASS", 1, 1))


# ------------------------------------------------------------------ B5
class B5Identity(_Tmp):
    def _repo(self):
        g = os.path.join(self.tmp, "repo"); os.makedirs(os.path.join(g, "core", "migrations"))

        def git(*a):
            subprocess.run(["git", "-C", g, "-c", "user.name=probe", "-c", "user.email=probe@example.invalid", *a], check=True, capture_output=True)
        subprocess.run(["git", "init", "-q", g], check=True, capture_output=True)
        git("config", "core.autocrlf", "false")
        # files are written as bytes with LF endings, so the committed content is the same on every platform
        # (text mode would write CRLF on Windows - found in the qualifying run CHC_20260917T103926Z)
        for rel, data in (("core/migrations/0001_initial.py", b"# initial\n"), ("old.py", b"# content\n" * 30), ("gone.py", b"# to delete\n")):
            with open(os.path.join(g, *rel.split("/")), "wb") as f:
                f.write(data)
        git("add", "-A"); git("commit", "-qm", "base")
        return g, git

    def identity(self, g):
        r = self.route(name="ev-identity")
        with mock.patch.object(route_mod, "ROOT", g):
            return r.identity()

    def test_b5_failed_git_is_invalid_and_never_clean(self):
        r = self.route()
        with mock.patch.object(route_mod, "run_git", return_value=(128, b"", "fatal: not a git repository")):
            ident = r.identity()
        self.assertFalse(ident["valid"]); self.assertNotIn("working_tree_clean", ident); self.assertIn("not a git repository", ident["problems"][0])

    def test_b5_failed_identity_makes_the_run_incomplete(self):
        r = self.route()
        with mock.patch.object(r, "identity", return_value={"valid": False, "problems": ["git failed"]}), \
                mock.patch.dict(os.environ, {"INTEVIA_POSTGRES_PASSWORD": ""}, clear=False), mock.patch.object(route_mod.sys.stdin, "isatty", return_value=False), \
                contextlib.redirect_stdout(io.StringIO()):
            code = r.run(skip_mutations=True)
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(route_mod.read_text(r.path("summary.json")))["steps"][0], {"id": "IDENTITY", "outcome": "INCOMPLETE", "reason": "git failed"})

    def test_b5_rename_quoted_path_and_deletion_are_identified_by_their_bytes(self):
        g, git = self._repo()
        git("mv", "old.py", "new.py")
        with open(os.path.join(g, "new.py"), "ab") as f:
            f.write(b"# changed\n")
        # a path git quotes in its ordinary porcelain output (non-ASCII, spaces, an apostrophe and a hash), using only
        # characters valid on NTFS as well as POSIX file systems (a double quote is not - found in CHC_20260917T103926Z)
        odd = "dir with space/caf\u00e9 'quoted' #1.py"
        os.makedirs(os.path.join(g, "dir with space"))
        with open(os.path.join(g, *odd.split("/")), "wb") as f:
            f.write(b"# odd path\n")
        os.remove(os.path.join(g, "gone.py"))
        ident = self.identity(g)
        self.assertTrue(ident["valid"], ident["problems"])
        by = {c["path"]: c for c in ident["working_tree_changes"]}
        self.assertEqual(by["new.py"]["orig_path"], "old.py")
        self.assertEqual(by["new.py"]["sha256"], route_mod.sha256_file(os.path.join(g, "new.py")))
        self.assertEqual(by[odd]["sha256"], route_mod.sha256_file(os.path.join(g, *odd.split("/"))))
        self.assertEqual((by["gone.py"]["kind"], by["gone.py"]["sha256"]), ("deleted", None))

    def test_b5_non_file_entry_is_unsupported_and_invalid(self):
        # an untracked nested repository is reported by git as a directory entry, not a regular file; it needs no special
        # privilege on any platform (a symlink needs one on Windows - found in CHC_20260917T103926Z)
        g, git = self._repo()
        nested = os.path.join(g, "nested")
        subprocess.run(["git", "init", "-q", nested], check=True, capture_output=True)
        with open(os.path.join(nested, "x.py"), "wb") as f:
            f.write(b"# nested\n")
        ident = self.identity(g)
        self.assertFalse(ident["valid"]); self.assertTrue(any("unsupported" in p for p in ident["problems"]), ident["problems"])

    def test_b5_positive_clean_tree_and_working_tree_git_tree(self):
        g, git = self._repo()
        ident = self.identity(g)
        self.assertTrue(ident["valid"]); self.assertTrue(ident["working_tree_clean"])
        self.assertEqual(ident["working_tree_git_tree"], ident["commit_tree"])

    def test_b5_line_endings_differ_in_bytes_not_in_git_tree(self):
        g, git = self._repo()
        git("config", "core.autocrlf", "true")
        with open(os.path.join(g, "old.py"), "rb") as f:
            data = f.read()
        with open(os.path.join(g, "old.py"), "wb") as f:
            f.write(data.replace(b"\n", b"\r\n"))
        ident = self.identity(g)
        self.assertTrue(ident["valid"], ident["problems"])
        self.assertEqual(ident["working_tree_git_tree"], ident["commit_tree"])  # same git content (the O-1 situation)


class B5EvidenceInsideRepository(_Tmp):
    """CI writes its evidence into the repository (verification-evidence/, which .gitignore ignores). Found in CI run
    35215316126: identity failed there and the route correctly reported INCOMPLETE.

    Every git call in these self-tests captures its output: Git for Windows prints line-ending warnings, which would
    otherwise land inside the verbose runner's status lines (found in the v0.4 qualifying run)."""

    def _repo(self, gitignore):
        g = os.path.join(self.tmp, "repo"); os.makedirs(os.path.join(g, "core", "migrations"))
        subprocess.run(["git", "init", "-q", g], check=True, capture_output=True)
        files = {"core/migrations/0001_initial.py": b"# initial\n", "a.py": b"# a\n"}
        if gitignore is not None:
            files[".gitignore"] = gitignore
        for rel, data in files.items():
            with open(os.path.join(g, *rel.split("/")), "wb") as f:
                f.write(data)
        subprocess.run(["git", "-C", g, "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", g, "-c", "user.name=probe", "-c", "user.email=probe@example.invalid", "commit", "-qm", "base"], check=True, capture_output=True)
        return g

    def _identity_with_evidence_inside(self, g):
        r = route_mod.Route(os.path.join(g, "verification-evidence"), "probe")
        self.addCleanup(lambda: r.transcript.close())
        with mock.patch.object(route_mod, "ROOT", g):
            return r.identity()

    def test_b5_ignored_evidence_directory_inside_the_repository(self):
        g = self._repo(b"verification-evidence/\n")
        ident = self._identity_with_evidence_inside(g)
        self.assertTrue(ident["valid"], ident["problems"])
        self.assertTrue(ident["working_tree_clean"]); self.assertEqual(ident["working_tree_git_tree"], ident["commit_tree"])

    def test_b5_unignored_evidence_directory_inside_the_repository_is_excluded(self):
        g = self._repo(None)
        ident = self._identity_with_evidence_inside(g)
        self.assertTrue(ident["valid"], ident["problems"])
        self.assertTrue(ident["working_tree_clean"]); self.assertEqual(ident["working_tree_git_tree"], ident["commit_tree"])
