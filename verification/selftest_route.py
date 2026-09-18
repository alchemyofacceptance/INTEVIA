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
from verification import route as route_mod
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


class B0PycachePrefixEnv(_Tmp):
    def test_child_envs_follow_sys_pycache_prefix_and_omit_none(self):
        r = self.route()

        with mock.patch.object(route_mod.sys, "pycache_prefix", os.path.join(self.tmp, "pycache")):
            django_env = r.django_env("test_db", "SELF")
            offline_env = route_mod._child_python_env(PYTHONPATH=route_mod.ROOT, PYTHONIOENCODING="utf-8")
            self.assertEqual(django_env["PYTHONPYCACHEPREFIX"], route_mod.sys.pycache_prefix)
            self.assertEqual(offline_env["PYTHONPYCACHEPREFIX"], route_mod.sys.pycache_prefix)

        with mock.patch.object(route_mod.sys, "pycache_prefix", None):
            django_env = r.django_env("test_db", "SELF")
            offline_env = route_mod._child_python_env(PYTHONPATH=route_mod.ROOT, PYTHONIOENCODING="utf-8")
            self.assertNotIn("PYTHONPYCACHEPREFIX", django_env)
            self.assertNotIn("PYTHONPYCACHEPREFIX", offline_env)

    def test_offline_step_forwards_sys_pycache_prefix_to_run_logged(self):
        r = self.route()
        captured = {}
        expected_prefix = os.path.join(self.tmp, "pycache")

        def fake_run_logged(cmd, log_path, env):
            captured["env"] = dict(env)
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("offline log\n")
            return 0

        fake_out = {"summary": {"ran": 0, "final": "OK", "errors": 0}, "reconciled": True, "tests": [], "reconciliation": {},
                    "test_totals_by_result": {}, "accounting_violations": []}

        with mock.patch.object(route_mod.sys, "pycache_prefix", expected_prefix), \
                mock.patch.object(r, "run_logged", side_effect=fake_run_logged), \
                mock.patch.object(route_mod.parse_results, "parse", return_value=fake_out):
            step = r.step_offline()

        self.assertEqual(captured["env"]["PYTHONPYCACHEPREFIX"], expected_prefix)
        self.assertEqual(step["outcome"], "PASS")

        captured.clear()
        os.remove(r.path("OFFLINE_results.json"))
        with mock.patch.object(route_mod.sys, "pycache_prefix", None), \
                mock.patch.object(r, "run_logged", side_effect=fake_run_logged), \
                mock.patch.object(route_mod.parse_results, "parse", return_value=fake_out):
            step = r.step_offline()

        self.assertNotIn("PYTHONPYCACHEPREFIX", captured["env"])
        self.assertEqual(step["outcome"], "PASS")


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
                mock.patch.object(r, "step_lock", return_value={"id": "LOCK", "outcome": "PASS"}), \
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
            self.assertEqual([r["event"] for r in self.receipts()], ["create_attempt"])
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
        self.assertEqual(events, ["create_attempt", "created", "destroy_refused"]); self.assertFalse(any(s.startswith("DROP") for s in sql))

    def test_b3_positive_owned_database_is_created_then_destroyed(self):
        sql, existing = [], {}
        c = self._creation(sql, existing)
        with self._env():
            ownership.install(c)
            self.assertEqual(c._create_test_db(verbosity=0, autoclobber=True, keepdb=False), self.NAME)
            c._destroy_test_db(self.NAME, verbosity=0)
            recs = self.receipts()
        self.assertEqual([r["event"] for r in recs], ["create_attempt", "created", "destroyed"]); self.assertTrue(recs[2]["confirmed_absent"])


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


# ------------------------------------------------------------------ UFUND-3 Change C v0.6: A1 residuals RC-B2, RC-B3, RC-B4, RC-O1, RC-O2
class _Server:
    """An in-memory PostgreSQL stand-in for route-level control flow: databases by name and oid, CREATE/DROP by name, and
    injectable failures. It is the route's orchestration that is under test, not PostgreSQL."""

    def __init__(self, route):
        self.r, self.dbs, self.next_oid, self.sql, self.drops = route, {}, 100, [], []
        self.fail_oid_once_for = None       # name: the next oid lookup on a connection raises (after a successful CREATE)
        self.replace_after_lookup = None    # name: after the next oid lookup on a connection, another client replaces it
        self.create_raises_after = None     # name: CREATE succeeds on the server but the client sees an error
        self.connect_dbnames = []

    def connect(self, dbname=None):
        self.connect_dbnames.append(dbname)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        pass

    def execute(self, sql, params=None):
        self.sql.append(sql)
        if sql.startswith("CREATE DATABASE"):
            name = sql.split('"')[1]
            if name in self.dbs:
                exc = RuntimeError('database "%s" already exists' % name); exc.sqlstate = "42P04"
                raise exc
            self.next_oid += 1; self.dbs[name] = self.next_oid
            if self.create_raises_after == name:
                self.create_raises_after = None
                raise RuntimeError("connection lost after CREATE was sent")
        elif sql.startswith("DROP DATABASE"):
            name = sql.split('"')[1]
            self.drops.append((name, self.dbs.get(name))); self.dbs.pop(name, None)
        return SimpleNamespace(fetchone=lambda: (None,), fetchall=lambda: [])

    def oid_of(self, name, conn=None):
        if conn is not None and self.fail_oid_once_for == name and name in self.dbs:
            self.fail_oid_once_for = None
            raise RuntimeError("injected oid lookup failure")
        oid = self.dbs.get(name)
        if conn is not None and self.replace_after_lookup == name and oid is not None:
            self.replace_after_lookup = None
            self.dbs[name] = 9001  # another client dropped and recreated the name
        return oid


class RCB2B3FixtureLifecycle(_Tmp):
    """RC-B2 (every fixture creation attempt is accounted for, exceptions included) and RC-B3 (every drop, including the
    deliberate replacement transition, checks ownership first). A1's three failure injections and its replacement
    counterexample, plus an uncertain CREATE and a duplicate at CREATE, against the positive control."""

    def _run(self, inject=None, collision_log="CollisionRefused: test database exists\n"):
        r = self.route()
        srv = _Server(r)
        if inject:
            inject(r, srv)

        def collide(cmd, path, env):
            name = r.db_name("OWN-COLLIDE")
            with open(path, "x") as f:
                f.write(collision_log + "EXIT_STATUS: 1\n")
            ownership_env = {ownership.RECEIPT_ENV: env[ownership.RECEIPT_ENV], ownership.NONCE_ENV: r.nonce}
            with mock.patch.dict(os.environ, ownership_env):
                ownership.write_receipt("collision_refused", name=name, existing_oid=srv.dbs.get(name))
            return 1

        env = {"INTEVIA_POSTGRES_USER": "probe", "INTEVIA_POSTGRES_PASSWORD": "not-a-credential"}
        fake_lock = mock.MagicMock(); fake_lock.execute.return_value.fetchall.return_value = []
        with mock.patch.dict(os.environ, env), mock.patch.object(r, "identity", return_value={"valid": True, "problems": [], "tested": "x"}), \
                mock.patch.object(r, "acquire_lock", side_effect=lambda: setattr(r, "lock_conn", fake_lock)), \
                mock.patch.object(r, "lock_record", return_value={"held": True}), mock.patch.object(r, "server", return_value={"version": "simulated"}), \
                mock.patch.object(r, "step_offline", return_value={"id": "OFFLINE", "outcome": "PASS"}), \
                mock.patch.object(r, "step_suite", side_effect=lambda sid, *a, **k: {"id": sid, "outcome": "PASS"}), \
                mock.patch.object(r, "step_lock", return_value={"id": "LOCK", "outcome": "PASS"}), \
                mock.patch.object(r, "connect", side_effect=srv.connect), mock.patch.object(r, "oid_of", side_effect=srv.oid_of), \
                mock.patch.object(r, "run_logged", side_effect=collide), contextlib.redirect_stdout(io.StringIO()):
            code = r.run(skip_mutations=True)
        summary = json.loads(route_mod.read_text(r.path("summary.json")))
        return r, srv, code, summary

    def names(self, summary):
        return {n["name"]: n for n in summary["cleanup"]["names"]}

    def test_v06_1_positive_control_exit_0_clean_and_every_drop_by_its_own_oid(self):
        r, srv, code, summary = self._run()
        self.assertEqual((code, summary["cleanup"]["outcome"]), (0, "CLEAN"), summary)
        self.assertEqual(srv.dbs, {})
        self.assertTrue(all(oid is not None and oid < 9000 for _, oid in srv.drops))
        own = next(s for s in summary["steps"] if s["id"] == "OWNERSHIP")
        self.assertEqual(own["outcome"], "PASS", own)

    def test_v06_2_oid_failure_after_collision_fixture_create_is_unresolved_exit_3(self):
        def inject(r, srv):
            srv.fail_oid_once_for = r.db_name("OWN-COLLIDE")
        r, srv, code, summary = self._run(inject)
        name = r.db_name("OWN-COLLIDE")
        self.assertEqual((code, summary["cleanup"]["outcome"]), (3, "NOT CLEAN"))
        self.assertIn(name, srv.dbs)  # left untouched: its identity was never confirmed
        self.assertTrue(self.names(summary)[name]["state"].startswith("UNRESOLVED"))

    def test_v06_3_oid_failure_after_first_replacement_fixture_create_is_unresolved_exit_3(self):
        def inject(r, srv):
            srv.fail_oid_once_for = r.db_name("OWN-REPLACE")
        r, srv, code, summary = self._run(inject)
        name = r.db_name("OWN-REPLACE")
        self.assertEqual(code, 3); self.assertIn(name, srv.dbs)
        self.assertTrue(self.names(summary)[name]["state"].startswith("UNRESOLVED"))

    def test_v06_4_receipt_write_failure_after_owned_create_is_cleaned_by_its_oid(self):
        real_open = open

        def failing_open(path, *a, **k):
            if str(path).endswith("OWN-OWNED_db_receipts.jsonl"):
                raise OSError("injected receipt write failure")
            return real_open(path, *a, **k)

        with mock.patch("builtins.open", side_effect=failing_open):
            r, srv, code, summary = self._run()
        name = r.db_name("OWN-OWNED")
        self.assertEqual((code, summary["cleanup"]["outcome"]), (2, "CLEAN"))  # step INCOMPLETE; the fixture is accounted for
        self.assertNotIn(name, srv.dbs)
        self.assertTrue(self.names(summary)[name]["state"].startswith("DROPPED BY THE ROUTE"))

    def test_v06_5_a1_replacement_before_the_transition_drop_is_left_untouched(self):
        def inject(r, srv):
            srv.replace_after_lookup = r.db_name("OWN-REPLACE")
        r, srv, code, summary = self._run(inject)
        name = r.db_name("OWN-REPLACE")
        self.assertNotIn((name, 9001), srv.drops)            # the replacement was not dropped
        self.assertEqual(srv.dbs.get(name), 9001)
        own = next(s for s in summary["steps"] if s["id"] == "OWNERSHIP")
        self.assertEqual(own["outcome"], "FAIL")
        self.assertEqual((code, summary["cleanup"]["outcome"]), (3, "NOT CLEAN"))
        self.assertTrue(self.names(summary)[name]["state"].startswith("REPLACED"))

    def test_v06_6_create_outcome_unknown_is_left_untouched_and_unresolved(self):
        def inject(r, srv):
            srv.create_raises_after = r.db_name("OWN-OWNED")
        r, srv, code, summary = self._run(inject)
        name = r.db_name("OWN-OWNED")
        self.assertEqual(code, 3); self.assertIn(name, srv.dbs)
        self.assertIn("never confirmed", self.names(summary)[name]["state"])

    def test_v06_7_name_taken_by_another_client_is_not_touched_and_is_not_claimed(self):
        def inject(r, srv):
            srv.dbs[r.db_name("OWN-OWNED")] = 7777
        r, srv, code, summary = self._run(inject)
        name = r.db_name("OWN-OWNED")
        self.assertEqual(srv.dbs.get(name), 7777); self.assertNotIn((name, 7777), srv.drops)
        self.assertEqual((code, summary["cleanup"]["outcome"]), (2, "CLEAN"))
        self.assertTrue(self.names(summary)[name]["state"].startswith("NOT CREATED"))

    def test_v06_8_rc_o2_a_collision_run_that_reports_a_test_outcome_fails_the_check(self):
        log = "test_x (probe.C.test_x) ... ERROR\n\n" + "-" * 70 + "\nRan 1 test in 0.001s\n\nFAILED (errors=1)\n"
        r, srv, code, summary = self._run(collision_log=log)
        own = next(s for s in summary["steps"] if s["id"] == "OWNERSHIP")
        self.assertFalse(own["checks"]["collision"]["no_test_outcome_reported"]); self.assertEqual(own["outcome"], "FAIL")

    def test_v06_9_runner_database_whose_creation_receipt_failed_is_left_untouched(self):
        r = self.route(); name = r.db_name("S015")
        p = r.path("S015_db_receipts.jsonl")
        with open(p, "w") as f:
            f.write(json.dumps({"event": "create_attempt", "name": name, "run_nonce": r.nonce}) + "\n")
        sql = []
        with mock.patch.object(r, "oid_of", return_value=4321), mock.patch.object(r, "connect", return_value=_Conn(sql)):
            rec = r.cleanup_one("S015", name, p)
        self.assertFalse(rec["resolved"]); self.assertIn("CREATE was issued", rec["state"]); self.assertEqual(sql, [])


class RCO1LockDatabase(_Tmp):
    """RC-O1: the lock is always taken in the common lock database, whatever INTEVIA_POSTGRES_DB names; an unreachable
    lock database refuses the run instead of running it unserialised. Live contention is the route's LOCK step."""

    def test_v06_10_lock_uses_the_common_database_whatever_the_connection_database(self):
        r = self.route()
        srv = _Server(r)
        with mock.patch.dict(os.environ, {"INTEVIA_POSTGRES_DB": "some_other_database"}), mock.patch.object(r, "connect", side_effect=srv.connect), \
                mock.patch.object(srv, "execute", return_value=SimpleNamespace(fetchone=lambda: (True,))):
            r.acquire_lock()
        self.assertEqual(srv.connect_dbnames, [route_mod.LOCK_DATABASE])

    def test_v06_11_unreachable_lock_database_refuses_the_run(self):
        r = self.route()
        env = {"INTEVIA_POSTGRES_USER": "probe", "INTEVIA_POSTGRES_PASSWORD": "not-a-credential"}
        with mock.patch.dict(os.environ, env), mock.patch.object(r, "identity", return_value={"valid": True, "problems": [], "tested": "x"}), \
                mock.patch.object(r, "connect", side_effect=RuntimeError("permission denied for database postgres")), \
                contextlib.redirect_stdout(io.StringIO()):
            code = r.run(skip_mutations=True)
        summary = json.loads(route_mod.read_text(r.path("summary.json")))
        self.assertEqual(code, 2)
        self.assertIn("lock database", summary["steps"][1]["reason"])
        self.assertEqual(summary["cleanup"]["outcome"], "NOTHING TO CLEAN")


class RCB4IndexFlags(B5Identity):
    """RC-B4 (Human Governor D-3, 17 Sep 2026): flagged index states are refused with the affected paths; the route never
    clears flags or touches the checkout; the computed working tree is still compared with the commit."""

    def _flag(self, g, git, flag, change=True):
        git("update-index", flag, "old.py")
        if change:
            with open(os.path.join(g, "old.py"), "ab") as f:
                f.write(b"# hidden change\n")
        with open(os.path.join(g, "old.py"), "rb") as f:
            return f.read()

    def _assert_refused_and_untouched(self, g, ident, before, expect_letter):
        self.assertFalse(ident["valid"])
        self.assertNotIn("exactly", ident["tested"]); self.assertFalse(ident["working_tree_clean"])
        self.assertTrue(any("old.py" in p and "index flags" in p for p in ident["problems"]), ident["problems"])
        with open(os.path.join(g, "old.py"), "rb") as f:
            self.assertEqual(f.read(), before)  # the working file is untouched
        tags = subprocess.run(["git", "-C", g, "ls-files", "-v", "old.py"], check=True, capture_output=True, text=True).stdout
        self.assertEqual(tags[0], expect_letter)  # the flag is still set: the route did not clear it

    def test_v06_12_skip_worktree_with_a_hidden_change_is_refused(self):
        g, git = self._repo(); before = self._flag(g, git, "--skip-worktree")
        ident = self.identity(g)
        self._assert_refused_and_untouched(g, ident, before, "S")
        self.assertNotEqual(ident["working_tree_git_tree"], ident["commit_tree"])  # the independent comparison is retained

    def test_v06_13_assume_unchanged_with_a_hidden_change_is_refused(self):
        g, git = self._repo(); before = self._flag(g, git, "--assume-unchanged")
        self._assert_refused_and_untouched(g, self.identity(g), before, "h")

    def test_v06_14_a_flag_without_a_change_is_still_refused(self):
        g, git = self._repo(); before = self._flag(g, git, "--skip-worktree", change=False)
        self._assert_refused_and_untouched(g, self.identity(g), before, "S")

    def test_v06_15_tree_that_the_inventory_does_not_explain_is_invalid_without_flags(self):
        g, git = self._repo()
        with open(os.path.join(g, "old.py"), "ab") as f:
            f.write(b"# change\n")
        real = route_mod.run_git

        def status_sees_nothing(args, env=None, cwd=None):
            if args[:1] == ["status"]:
                return 0, b"", ""
            return real(args, env=env, cwd=cwd)

        with mock.patch.object(route_mod, "run_git", side_effect=status_sees_nothing):
            ident = self.identity(g)
        self.assertFalse(ident["valid"]); self.assertTrue(any("does not list" in p and "old.py" in p for p in ident["problems"]))
        self.assertNotIn("exactly", ident["tested"])

    def test_v06_16_positive_listed_change_and_clean_tree_remain_valid(self):
        g, git = self._repo()
        self.assertEqual(self.identity(g)["tested"].split()[-1], "exactly")
        with open(os.path.join(g, "old.py"), "ab") as f:
            f.write(b"# listed change\n")
        r = self.route(name="ev-identity-2")
        with mock.patch.object(route_mod, "ROOT", g):
            ident = r.identity()
        self.assertTrue(ident["valid"], ident["problems"]); self.assertEqual(ident["tree_delta_paths"], ["old.py"])
