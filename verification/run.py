"""S015 verification route - the single entry point used locally and by CI.

    python -m verification.run [--evidence-dir DIR] [--run-id ID] [--skip-mutations]

Run from the repository root, in an environment with requirements-verification.txt installed, with a PostgreSQL server
reachable through INTEVIA_POSTGRES_HOST / _PORT / _USER / _PASSWORD (the password is prompted for when absent and a
terminal is attached). The role must be able to create and drop databases.

What it records (evidence directory):
  summary.json, SUMMARY.md   exact commit and tree tested, working-tree changes with their digests, environment,
                             server identity, test scope, each step's outcome, isolation, cleanup, overall result
  <step>_*                   collection, raw log, parsed results, isolation records
  MANIFEST.sha256            sha256 of every evidence file

Steps (each PASS, FAIL or INCOMPLETE):
  PARSER      verification.selftest_parser - the result parser, including description lines (no database)
  SELF        the isolation instrument's self-check under the isolation runner
  S015        the S015 test set (tests/, pattern test_s015_*.py) under the isolation runner
  MUT-<name>  each discrimination check in verification.mutations: its target tests must fail under the mutation

Only databases named test_intevia_living_organism_v<run id>_* are created, and only those are ever dropped, and only if
absent before the run. Existing databases are never touched.

Exit status: 0 every step PASS and cleanup clean; 1 a step FAIL; 2 a step INCOMPLETE (takes precedence over 1);
3 cleanup not clean (takes precedence over all).
"""
import argparse
import datetime
import getpass
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from verification import parse_results  # noqa: E402
from verification.mutations import MUTATIONS  # noqa: E402

ROUTE = "LIVING_ORGANISM_TEST_LIFECYCLE"
DB_PREFIX = "test_intevia_living_organism_"
S015_PATTERN = "test_s015_*.py"
ISOLATION_RUNNER = "verification.isolation.runner.IsolationRunner"
MUTATION_RUNNER = "verification.mutations.MutationRunner"
STEP_CODES = {"SELF": "self", "S015": "s015"}
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
json.dump({"mode": mode, "args": rest, "count": len(ids), "failed_loads": failed,
           "digest": hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest(), "ids": ids}, open(out_path, "w", encoding="utf-8"), indent=1)
"""


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*args, strip=True):
    p = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if p.returncode != 0:
        return None
    return p.stdout.strip() if strip else p.stdout


class Route:
    def __init__(self, evidence_dir, run_id):
        self.dir = evidence_dir
        self.run_id = run_id
        self.steps = []
        self.created = []
        os.makedirs(self.dir, exist_ok=True)
        self.transcript = open(os.path.join(self.dir, "transcript.txt"), "w", encoding="utf-8")

    def say(self, text):
        print(text, flush=True)
        self.transcript.write(text + "\n")
        self.transcript.flush()

    # ------------------------------------------------------------------ identity and environment
    def identity(self):
        head, tree = git("rev-parse", "HEAD"), git("rev-parse", "HEAD^{tree}")
        status = git("status", "--porcelain", "--untracked-files=all", strip=False) or ""
        changes = []
        for line in status.splitlines():
            code, path = line[:2], line[3:]
            if path.startswith(os.path.relpath(self.dir, ROOT).replace(os.sep, "/") + "/"):
                continue
            full = os.path.join(ROOT, path)
            changes.append({"status": code.strip(), "path": path, "sha256": sha256_file(full) if os.path.isfile(full) else None})
        return {"commit": head, "tree": tree, "working_tree_clean": not changes, "working_tree_changes": changes,
                "tested": "commit %s exactly" % head if not changes else "commit %s plus the listed working-tree changes" % head,
                "core_migration_head": self.core_head()}

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
            except Exception as exc:  # recorded, and the steps will fail to run
                env[mod] = "unavailable: %s" % exc
        ci = {k: os.environ[k] for k in ("CI", "GITHUB_ACTIONS", "GITHUB_REPOSITORY", "GITHUB_SHA", "GITHUB_REF", "GITHUB_RUN_ID",
                                          "GITHUB_RUN_ATTEMPT", "GITHUB_WORKFLOW", "RUNNER_OS") if k in os.environ}
        env["ci"] = ci or None
        return env

    # ------------------------------------------------------------------ database administration (census, cleanup)
    def connect(self):
        import psycopg
        return psycopg.connect(host=os.environ.get("INTEVIA_POSTGRES_HOST", "127.0.0.1"), port=os.environ.get("INTEVIA_POSTGRES_PORT", "5432"),
                               user=os.environ["INTEVIA_POSTGRES_USER"], password=os.environ["INTEVIA_POSTGRES_PASSWORD"],
                               dbname=os.environ.get("INTEVIA_POSTGRES_DB", "postgres"), autocommit=True)

    def server(self):
        with self.connect() as c:
            row = c.execute("SELECT version(), pg_postmaster_start_time(), current_user").fetchone()
            out = {"version": row[0], "postmaster_start": row[1].isoformat(), "role": row[2]}
            try:
                out["system_identifier"] = str(c.execute("SELECT system_identifier FROM pg_control_system()").fetchone()[0])
            except Exception as exc:  # restricted on some servers; recorded rather than assumed
                out["system_identifier"] = "unavailable: %s" % type(exc).__name__
            return out

    def census(self):
        with self.connect() as c:
            return sorted(r[0] for r in c.execute("SELECT datname FROM pg_database WHERE datname LIKE %s", (DB_PREFIX + "%",)))

    # ------------------------------------------------------------------ steps
    def django_env(self, db_name, isolation_path=None, mutation=None):
        env = dict(os.environ, DJANGO_SETTINGS_MODULE="intevia.test_settings", INTEVIA_S015_TEST_ROUTE=ROUTE, INTEVIA_DATABASE_ENGINE="postgresql",
                   INTEVIA_POSTGRES_TEST_DB=db_name, INTEVIA_POSTGRES_DB=os.environ.get("INTEVIA_POSTGRES_DB", "postgres"),
                   PYTHONPATH=os.pathsep.join([ROOT, os.path.join(ROOT, "tests")]), PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
                   VERIFICATION_EXPECTED_CORE_HEAD=self.core_head() or "")
        if isolation_path:
            env["VERIFICATION_ISOLATION_JSON"] = isolation_path
        if mutation:
            env["VERIFICATION_MUTATION"] = mutation
        return env

    def run_logged(self, cmd, log_path, env):
        with open(log_path, "w", encoding="utf-8", newline="\n") as log:
            log.write("COMMAND: " + " ".join(cmd[1:]) + "\n")
            log.flush()
            proc = subprocess.run(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, env=env)
            log.write("\nEXIT_STATUS: %d\n" % proc.returncode)
        return proc.returncode

    def db_name(self, step):
        """test_intevia_living_organism_v<run id>_<step code>; refused if longer than PostgreSQL's 63-character limit,
        because PostgreSQL would silently truncate it and the route could no longer recognise its own database."""
        code = STEP_CODES.get(step, step.lower().replace("-", "_"))
        name = "%sv%s_%s" % (DB_PREFIX, self.run_id, code)
        if len(name) > 63:
            raise RuntimeError("database name %r is longer than 63 characters" % name)
        return name

    def step_parser(self):
        step = {"id": "PARSER", "scope": "verification.selftest_parser (no database)"}
        log = os.path.join(self.dir, "PARSER_test_output.log")
        env = dict(os.environ, PYTHONPATH=ROOT, PYTHONIOENCODING="utf-8")
        code = self.run_logged([sys.executable, "-m", "unittest", "verification.selftest_parser", "-v"], log, env)
        text = open(log, encoding="utf-8").read()
        out = parse_results.parse(text, None, log)
        json.dump(out, open(os.path.join(self.dir, "PARSER_results.json"), "w", encoding="utf-8"), indent=1)
        step.update(tests=out["summary"]["ran"], verdict=out["summary"]["final"], reconciled=out["reconciled"])
        if not out["reconciled"]:
            step["outcome"], step["reason"] = "INCOMPLETE", "log did not reconcile"
        elif code == 0 and (out["summary"]["final"] or "").startswith("OK") and out["summary"]["ran"]:
            step["outcome"] = "PASS"
        else:
            step["outcome"], step["reason"] = "FAIL", "self-test failed"
        return step

    def step_suite(self, sid, mode, args, isolation=True, mutation=None, expect_fail=False):
        step = {"id": sid, "scope": {"mode": mode, "args": args}}
        db = self.db_name(sid)
        step["database"] = db
        collection_path = os.path.join(self.dir, sid + "_collection.json")
        env = self.django_env(db, os.path.join(self.dir, sid + "_isolation.jsonl") if isolation else None, mutation)
        c = subprocess.run([sys.executable, "-c", COLLECT, mode, collection_path, *args], cwd=ROOT, capture_output=True, text=True, env=env)
        if c.returncode != 0 or not os.path.exists(collection_path):
            open(os.path.join(self.dir, sid + "_collection_error.txt"), "w", encoding="utf-8").write(c.stdout + c.stderr)
            step["outcome"], step["reason"] = "INCOMPLETE", "collection failed (exit %d)" % c.returncode
            return step
        collection = json.load(open(collection_path, encoding="utf-8"))
        step["collection"] = {"count": collection["count"], "digest": collection["digest"], "failed_loads": collection["failed_loads"]}
        if collection["failed_loads"] or not collection["count"]:
            step["outcome"], step["reason"] = "INCOMPLETE", "a test module failed to load, or nothing was collected"
            return step
        labels = list(args) if mode == "label" else ["tests", "--top-level-directory", "tests", "--pattern", args[0]]
        runner = MUTATION_RUNNER if mutation else ISOLATION_RUNNER
        log = os.path.join(self.dir, sid + "_test_output.log")
        self.created.append(db)
        code = self.run_logged([sys.executable, "manage.py", "test", *labels, "--testrunner", runner, "--noinput", "-v", "2"], log, env)
        out = parse_results.parse(open(log, encoding="utf-8").read(), collection["ids"], log)
        json.dump(out, open(os.path.join(self.dir, sid + "_results.json"), "w", encoding="utf-8"), indent=1)
        step["tests"] = {"ran": out["summary"]["ran"], "verdict": out["summary"]["final"], "exit": code, "reconciled": out["reconciled"],
                         "by_outcome": out["test_totals_by_body_outcome"],
                         "not_ok": [{"id": t["id"], "body": t["body"], "exception": t["body_exception"]} for t in out["tests"] if t["body"] != "ok"]}
        if not out["reconciled"]:
            step["outcome"], step["reason"] = "INCOMPLETE", "the log does not account for every collected test (see results reconciliation)"
            return step
        if isolation:
            step["isolation"] = self.read_isolation(os.path.join(self.dir, sid + "_isolation.jsonl"), collection["ids"])
            if step["isolation"]["verdict"] is None:
                step["outcome"], step["reason"] = "INCOMPLETE", "isolation evidence missing"
                return step
        if expect_fail:
            failed_by_assertion = all(t["body"] == "FAIL" for t in out["tests"]) and out["summary"]["errors"] == 0
            applied = "VERIFICATION MUTATION APPLIED: %s" % mutation in open(log, encoding="utf-8").read()
            step["expectation"] = "every target test fails by assertion under the mutation"
            step["outcome"] = "PASS" if (applied and failed_by_assertion and code != 0) else "FAIL"
            if step["outcome"] == "FAIL":
                step["reason"] = "mutation applied %s; all targets failed by assertion %s" % (applied, failed_by_assertion)
            return step
        ok = (out["summary"]["final"] or "").startswith("OK") and code == 0
        iso_ok = (not isolation) or step["isolation"]["verdict"] == "PASS"
        step["outcome"] = "PASS" if ok and iso_ok else "FAIL"
        if not ok:
            step["reason"] = "tests did not all pass"
        elif not iso_ok:
            step["reason"] = "isolation not established"
        return step

    @staticmethod
    def read_isolation(path, collected):
        if not os.path.exists(path):
            return {"verdict": None}
        records = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
        summary = [r for r in records if r.get("checkpoint") == "SUMMARY"]
        cpa = [r for r in records if r.get("checkpoint") == "CP-A"]
        lawful_na = sorted(r["test"] for r in cpa if r.get("applicable") is False and r.get("database_access") == "forbidden")
        established = sum(1 for r in cpa if r.get("established") is True)
        return {"verdict": summary[-1].get("isolation_verdict") if summary else None, "reasons": summary[-1].get("reasons", []) if summary else [],
                "checkpoints": len(cpa), "collected": len(collected), "applicable": len(cpa) - len(lawful_na), "established": established,
                "not_applicable": len(lawful_na), "not_applicable_ids": lawful_na}

    # ------------------------------------------------------------------ orchestration
    def run(self, skip_mutations):
        started = now()
        summary = {"route": "S015 verification route", "run_id": self.run_id, "started": started}
        self.say("S015 VERIFICATION ROUTE  run %s  %s" % (self.run_id, started))
        summary["identity"] = self.identity()
        summary["environment"] = self.environment()
        self.say("tested     : " + summary["identity"]["tested"])
        self.say("core head  : %s" % summary["identity"]["core_migration_head"])
        cleanup = {"outcome": "NOT REACHED", "names": []}
        try:
            if not os.environ.get("INTEVIA_POSTGRES_PASSWORD"):
                if sys.stdin.isatty():
                    os.environ["INTEVIA_POSTGRES_PASSWORD"] = getpass.getpass("PostgreSQL password for %s: " % os.environ.get("INTEVIA_POSTGRES_USER"))
                else:
                    raise RuntimeError("INTEVIA_POSTGRES_PASSWORD is not set and no terminal is attached")
            for var in ("INTEVIA_POSTGRES_USER",):
                if not os.environ.get(var):
                    raise RuntimeError(var + " is not set")
            summary["server"] = self.server()
            before = self.census()
            mine = [n for n in before if n.startswith(DB_PREFIX + "v" + self.run_id + "_")]
            if mine:
                raise RuntimeError("databases for this run id already exist: %s" % mine)
            summary["databases_present_before"] = before
            self.say("server     : %s" % summary["server"]["version"].split(",")[0])
            plan = [("PARSER", lambda: self.step_parser()),
                    ("SELF", lambda: self.step_suite("SELF", "label", ["verification.isolation.selfcheck_tests"])),
                    ("S015", lambda: self.step_suite("S015", "discover", [S015_PATTERN]))]
            if not skip_mutations:
                for name, m in MUTATIONS.items():
                    sid = "MUT-" + name
                    plan.append((sid, lambda sid=sid, name=name, m=m: self.step_suite(sid, "label", m["targets"], isolation=False, mutation=name, expect_fail=True)))
            for sid, fn in plan:
                self.say("--- step %s" % sid)
                try:
                    step = fn()
                except Exception as exc:
                    step = {"id": sid, "outcome": "INCOMPLETE", "reason": "step raised %s: %s" % (type(exc).__name__, exc)}
                self.steps.append(step)
                self.say("    %s%s" % (step["outcome"], (" - " + step["reason"]) if step.get("reason") else ""))
                if sid == "SELF" and step["outcome"] != "PASS":
                    self.say("    the isolation self-check did not pass: later database steps are not run")
                    break
            cleanup = self.cleanup(before)
        except Exception as exc:
            self.steps.append({"id": "SETUP", "outcome": "INCOMPLETE", "reason": "%s: %s" % (type(exc).__name__, exc)})
            self.say("SETUP INCOMPLETE: %s" % exc)
            try:
                cleanup = self.cleanup(None)
            except Exception as exc2:
                cleanup = {"outcome": "NOT CHECKED", "reason": str(exc2), "names": self.created}
        planned = ["PARSER", "SELF", "S015"] + ([] if skip_mutations else ["MUT-" + n for n in MUTATIONS])
        ran = {s["id"] for s in self.steps}
        for sid in planned:
            if sid not in ran:
                self.steps.append({"id": sid, "outcome": "INCOMPLETE", "reason": "not run"})
        outcomes = [s["outcome"] for s in self.steps]
        result = "PASS" if all(o == "PASS" for o in outcomes) else ("INCOMPLETE" if "INCOMPLETE" in outcomes else "FAIL")
        if skip_mutations:
            summary["scope_note"] = "discrimination checks skipped by request"
        cleanup_problem = cleanup["outcome"] == "NOT CLEAN" or (cleanup["outcome"] != "CLEAN" and self.created)
        exit_code = 3 if cleanup_problem else {"PASS": 0, "FAIL": 1, "INCOMPLETE": 2}[result]
        summary.update(steps=self.steps, cleanup=cleanup, result=result, exit_status=exit_code, finished=now())
        self.write(summary)
        return exit_code

    def cleanup(self, before):
        if before is None:
            return {"outcome": "NOT REACHED", "names": []}
        after = self.census()
        names = []
        for db in self.created:
            if db not in after:
                names.append({"name": db, "state": "ABSENT (destroyed by the test runner)"})
            elif db in before:
                names.append({"name": db, "state": "PRESENT BEFORE THE RUN - left untouched"})
            else:
                with self.connect() as c:
                    c.execute('DROP DATABASE "%s"' % db.replace('"', ""))
                names.append({"name": db, "state": "DROPPED BY THE ROUTE" if db not in self.census() else "DROP FAILED"})
        clean = all(n["state"].startswith(("ABSENT", "DROPPED")) for n in names)
        stray = [n for n in self.census() if n.startswith(DB_PREFIX + "v" + self.run_id + "_")]
        return {"outcome": "CLEAN" if clean and not stray else "NOT CLEAN", "names": names, "remaining_for_this_run": stray}

    def write(self, summary):
        json.dump(summary, open(os.path.join(self.dir, "summary.json"), "w", encoding="utf-8"), indent=1, default=str)
        ident, env = summary["identity"], summary["environment"]
        lines = ["# S015 verification route - %s" % summary["result"], "",
                 "- **Result:** %s (exit status %d); cleanup %s" % (summary["result"], summary["exit_status"], summary["cleanup"]["outcome"]),
                 "- **Tested:** %s" % ident["tested"], "- **Tree:** `%s`; core migration head `%s`" % (ident["tree"], ident["core_migration_head"]),
                 "- **Environment:** Python %s, Django %s, psycopg %s, %s" % (env["python"], env.get("django"), env.get("psycopg"), env["platform"]),
                 "- **Server:** %s" % (summary.get("server", {}).get("version", "not reached")),
                 "- **CI:** %s" % (json.dumps(env["ci"]) if env["ci"] else "not a CI run"), "- **Run:** %s, %s to %s" % (summary["run_id"], summary["started"], summary["finished"]), ""]
        if ident["working_tree_changes"]:
            lines += ["## Working-tree changes tested", ""] + ["- `%s` %s `%s`" % (c["status"], c["path"], c["sha256"]) for c in ident["working_tree_changes"]] + [""]
        lines += ["## Steps", "", "| Step | Outcome | Tests | Isolation | Note |", "|---|---|---|---|---|"]
        for s in summary["steps"]:
            t = s.get("tests")
            tests = ("%s ran, %s" % (t["ran"], t["verdict"])) if isinstance(t, dict) else (("%s ran, %s" % (s.get("tests"), s.get("verdict"))) if s.get("verdict") else "")
            iso = s.get("isolation")
            iso_txt = ("%s; %d applicable, %d established; %d not applicable" % (iso["verdict"], iso["applicable"], iso["established"], iso["not_applicable"])) if iso and iso.get("verdict") else ""
            lines.append("| %s | %s | %s | %s | %s |" % (s["id"], s["outcome"], tests, iso_txt, s.get("reason", s.get("expectation", ""))))
        lines += ["", "## Cleanup", ""] + ["- %s: %s" % (n["name"], n["state"]) for n in summary["cleanup"]["names"]]
        lines += ["", "A PASS is evidence at the checked properties for the tested commit and environment. It is not review, landing, external reproduction or acceptance.", ""]
        open(os.path.join(self.dir, "SUMMARY.md"), "w", encoding="utf-8").write("\n".join(lines))
        self.transcript.close()
        entries = []
        for base, _, files in os.walk(self.dir):
            for f in sorted(files):
                if f != "MANIFEST.sha256":
                    p = os.path.join(base, f)
                    entries.append("%s  %s" % (sha256_file(p), os.path.relpath(p, self.dir).replace(os.sep, "/")))
        open(os.path.join(self.dir, "MANIFEST.sha256"), "w", encoding="utf-8").write("\n".join(sorted(entries, key=lambda e: e[66:])) + "\n")
        print("RESULT: %s  exit status %d  cleanup %s  evidence %s" % (summary["result"], summary["exit_status"], summary["cleanup"]["outcome"], self.dir), flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--evidence-dir", default=None)
    ap.add_argument("--run-id", default=None, help="1-20 lowercase letters or digits; default: time-based")
    ap.add_argument("--skip-mutations", action="store_true")
    a = ap.parse_args()
    run_id = a.run_id or (datetime.datetime.now(datetime.timezone.utc).strftime("%y%m%d%H%M%S") + uuid.uuid4().hex[:4])
    if not re.fullmatch(r"[a-z0-9]{1,20}", run_id):
        ap.error("--run-id must be 1-20 lowercase letters or digits")
    evidence = a.evidence_dir or os.path.join(ROOT, "verification-evidence", run_id)
    sys.exit(Route(os.path.abspath(evidence), run_id).run(a.skip_mutations))


if __name__ == "__main__":
    main()
