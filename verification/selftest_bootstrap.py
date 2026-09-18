"""Self-test of the verification/bootstrap.py launcher surface."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from verification import bootstrap as bootstrap_mod
from verification.recording import ParallelExecutionRefused


def _write(path, text):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text, encoding="utf-8")


def _git(cwd, *args):
    proc = subprocess.run(["git", "-C", str(cwd), "-c", "user.name=probe", "-c", "user.email=probe@example.invalid", *args], capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def _fixture_repo(root, helper_mode=False):
    trusted_v = Path(root) / "V"
    checkout = Path(root) / "checkout"
    checkout.mkdir()
    trusted_v.mkdir()
    production_repo_root = Path(__file__).resolve().parents[1]
    production_route_path = Path(__file__).with_name("route.py").resolve()
    _write(checkout / "core" / "migrations" / "0001_fixture.py", "# fixture migration\n")
    _write(checkout / "verification" / "__init__.py", "")
    _write(checkout / "verification" / "fixture_v" / "v_only_helper.py", "print('A1_V_HELPER_EXECUTED', flush=True)\n")
    _write(checkout / "verification" / "repo_helper.py", "print('A1_REPO_HELPER_EXECUTED', flush=True)\n")
    route_text = (
        "# Fixture-only substitute identity: production route.identity() cannot be exercised at unit-test level because\n"
        "# production verification.route imports django and intevia.test_postgresql_backend at module import time, and this\n"
        "# minimal fixture does not provide that environment. The positive control therefore uses a substitute identity record.\n"
        "import argparse\n"
        "import json\n"
        "import os\n"
        "import subprocess\n"
        "import sys\n"
        "class Route:\n"
        "    def __init__(self, evidence_dir, run_id):\n"
        "        self.dir = evidence_dir\n"
        "        self.run_id = run_id\n"
        "    def identity(self, checkout=None):\n"
        "        cwd = os.path.abspath(checkout or os.getcwd())\n"
        "        commit = subprocess.run(['git', '-C', cwd, 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True).stdout.strip()\n"
        "        commit_tree = subprocess.run(['git', '-C', cwd, 'rev-parse', 'HEAD^{tree}'], capture_output=True, text=True, check=True).stdout.strip()\n"
        "        return {\n"
        "            'valid': True,\n"
        "            'problems': [],\n"
        "            'commit': commit,\n"
        "            'commit_tree': commit_tree,\n"
        "            'working_tree_changes': [],\n"
        "            'working_tree_clean': True,\n"
        "            'working_tree_git_tree': commit_tree,\n"
        "            'tree_delta_paths': [],\n"
        "            'tested': 'commit %s exactly' % commit,\n"
        "        }\n\n"
        "def _site_packages():\n"
        "    executable_dir = os.path.dirname(os.path.realpath(sys.executable))\n"
        "    for candidate in (executable_dir, os.path.dirname(executable_dir)):\n"
        "        if os.path.isfile(os.path.join(candidate, 'pyvenv.cfg')):\n"
        "            dependency_root = os.path.realpath(candidate)\n"
        "            break\n"
        "    else:\n"
        "        dependency_root = os.path.realpath(sys.base_prefix)\n"
        "    if os.name == 'nt':\n"
        "        return os.path.join(dependency_root, 'Lib', 'site-packages')\n"
        "    return os.path.join(dependency_root, 'lib', 'python%d.%d' % sys.version_info[:2], 'site-packages')\n\n"
        "def main(argv=None):\n"
        "    parser = argparse.ArgumentParser()\n"
        "    parser.add_argument(\"--evidence-dir\", default=None)\n"
        "    parser.add_argument(\"--run-id\", default=None)\n"
        "    parser.add_argument(\"--launch-token\", default=None)\n"
        "    parser.add_argument(\"--commit\", default=None)\n"
        "    parser.add_argument(\"--snapshot\", default=None)\n"
        "    parser.add_argument(\"--checkout\", default=None)\n"
        "    parser.add_argument(\"--skip-mutations\", action=\"store_true\")\n"
        "    args = parser.parse_args(argv)\n"
        "    checkout = args.checkout or os.getcwd()\n"
        "    evidence_dir = args.evidence_dir or os.path.join(checkout, \"evidence\")\n"
        "    route = Route(evidence_dir, args.run_id or \"run\")\n"
        "    ident = route.identity(checkout=checkout)\n"
        "    from verification import parse_results\n"
        "    summary = {\n"
        "        \"route\": \"S015 verification route v0.5\",\n"
        "        \"run_id\": args.run_id or \"run\",\n"
        "        \"result\": \"PASS\",\n"
        "        \"exit_status\": 0,\n"
        "        \"execution\": {\n"
        "            \"launch_token\": args.launch_token,\n"
        "            \"root\": os.path.abspath(args.snapshot or checkout),\n"
        "            \"entry\": os.path.realpath(__file__),\n"
        "            \"mode\": \"snapshot-entry\",\n"
        "            \"site_dirs\": [_site_packages()],\n"
        "        },\n"
        "        \"identity\": ident,\n"
        "    }\n"
        "    if args.snapshot is None:\n"
        "        summary[\"execution\"][\"mode\"] = \"checkout-entry\"\n"
        "    if args.commit is not None:\n"
        "        summary[\"identity\"][\"attested_commit\"] = args.commit\n"
        "    os.makedirs(evidence_dir, exist_ok=True)\n"
        "    with open(os.path.join(evidence_dir, \"summary.json\"), \"x\", encoding=\"utf-8\") as handle:\n"
        "        json.dump(summary, handle, indent=1)\n"
        "    sys.exit(0)\n"
    )
    _write(checkout / "verification" / "route.py", route_text)
    parse_results = """import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'fixture_v'))
from verification import repo_helper
import v_only_helper
OPTIONAL_HELPER = False
"""
    if helper_mode:
        parse_results = (
            "import os, sys\n"
            "sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'fixture_v'))\n"
            "from verification import repo_helper\n"
            "sys.path.insert(0, os.environ['INTEVIA_A1_PROBE_W'])\n"
            "try:\n    import w_only_helper\nexcept ImportError:\n    pass\n"
            "OPTIONAL_HELPER = False\n"
        )
    _write(checkout / "verification" / "parse_results.py", parse_results)
    production_run = Path(__file__).with_name("run.py").read_text(encoding="utf-8")
    _write(checkout / "verification" / "run.py", production_run)
    _write(checkout / "w_only_helper.py", "print('A1_W_HELPER_EXECUTED', flush=True)\n")
    _git(checkout, "init", "-q")
    _git(checkout, "add", "-A")
    _git(checkout, "commit", "-qm", "fixture")
    commit = _git(checkout, "rev-parse", "HEAD")
    tree = _git(checkout, "rev-parse", "HEAD^{tree}")
    return checkout, commit, tree


class BootstrapSurfaceTests(unittest.TestCase):
    def _run(self, checkout, commit, run_root, extra_args=None, env=None):
        fixture_root = Path(checkout).parent
        merged_env = dict(env or {}, INTEVIA_A1_PROBE_W=str(checkout))
        patch = mock.patch.dict(os.environ, merged_env, clear=False)
        with patch:
            return bootstrap_mod.bootstrap(sys.executable, str(checkout), commit, str(run_root), extra_args or ["--run-id", Path(run_root).name])

    def _gate(self, run_root):
        return bootstrap_mod.gate(run_root)

    def test_clean_run_qualifies(self):
        with tempfile.TemporaryDirectory(prefix="bootstrap-surface-") as td:
            checkout, commit, _ = _fixture_repo(td)
            run_root = Path(td) / "run-1"
            result = self._run(checkout, commit, run_root)
            summary = json.loads((run_root / "evidence" / "summary.json").read_text(encoding="utf-8"))
            attestation = json.loads((run_root / "LAUNCH_ATTESTATION.json").read_text(encoding="utf-8"))
            surface = json.loads((run_root / "SURFACE_RESULT.json").read_text(encoding="utf-8"))
            audit = json.loads((run_root / "evidence" / "SOURCE_AUDIT.json").read_text(encoding="utf-8"))
            gate = self._gate(run_root)
            self.assertEqual(result["exit"], 0, result)
            self.assertTrue(gate["qualifying"], gate)
            self.assertFalse(gate["refusals"], gate)
            self.assertEqual(summary["route"], "S015 verification route v0.5")
            self.assertEqual(summary["identity"]["attested_commit"], commit)
            self.assertEqual(summary["execution"]["launch_token"], surface["launch_token"])
            self.assertEqual(attestation["site_prequalification"]["validated_dependency_root"], attestation["site_prequalification"]["interpreter"]["dependency_root"])
            self.assertEqual(attestation["site_prequalification"]["validated_site_packages"], summary["execution"]["site_dirs"][0])
            self.assertEqual(attestation["site_prequalification"]["interpreter"]["site"], summary["execution"]["site_dirs"])
            self.assertTrue((run_root / "evidence" / "summary.json").exists())
            self.assertTrue((run_root / "evidence" / "SOURCE_AUDIT.json").exists())
            self.assertTrue(gate["qualifying"], gate)
            self.assertIn("A1_REPO_HELPER_EXECUTED", surface["coordinator_stdout"])
            self.assertIn("A1_V_HELPER_EXECUTED", surface["coordinator_stdout"])
            self.assertGreaterEqual(audit["history_by_origin"].get("E(root)", 0), 1, audit)
            self.assertIn("verification.route", audit["history_modules_by_origin"].get("E(root)", []), audit)
            self.assertIn("verification.repo_helper", audit["repository_modules"])
            self.assertFalse(audit["final_cache_is_complete_history"])

    def test_bogus_dependency_root_is_refused_by_name(self):
        with tempfile.TemporaryDirectory(prefix="bootstrap-surface-") as td:
            checkout, _, _ = _fixture_repo(td)
            snapshot = Path(td) / "snapshot"
            snapshot.mkdir()
            bogus_root = Path(td) / "checkout" / "venv"
            bogus_site_packages = bogus_root / "Lib" / "site-packages"
            bogus_site_packages.mkdir(parents=True)
            fake_environment = {
                "executable": sys.executable,
                "base_prefix": str(Path(td) / "base"),
                "dependency_root": str(bogus_root),
                "site_packages": str(bogus_site_packages),
                "venv_root": str(bogus_root),
                "pyvenv_cfg_home": str(Path(td) / "base"),
            }
            fake_distribution = type("FakeDist", (), {"metadata": {"Name": "Django"}})()
            fake_psycopg = type("FakeDist", (), {"metadata": {"Name": "psycopg"}})()
            with mock.patch.object(bootstrap_mod, "resolve_dependency_environment", return_value=fake_environment), \
                    mock.patch.object(bootstrap_mod.importlib.metadata, "distributions", return_value=[fake_distribution, fake_psycopg]):
                with self.assertRaises(bootstrap_mod.DependencyPrequalificationError) as exc:
                    bootstrap_mod.validate_dependency_environment(str(checkout), str(snapshot))
            self.assertIn("lies inside the checkout", str(exc.exception))

    def test_parallel_gt_one_is_refused_before_database_setup(self):
        from verification.isolation.runner import IsolationRunner
        from verification.mutations import MutationRunner

        with tempfile.TemporaryDirectory(prefix="bootstrap-surface-") as td:
            _fixture_repo(td)
            with mock.patch.object(IsolationRunner, "setup_databases", autospec=True) as setup_databases:
                runner = IsolationRunner(parallel=0, verbosity=0, nonce="test-nonce", step="SELF")
                self.assertEqual(runner.parallel, 0)
                runner.setup_databases()
                setup_databases.assert_called_once()
            with mock.patch.object(IsolationRunner, "setup_databases", autospec=True) as setup_databases:
                with self.assertRaises(ParallelExecutionRefused) as exc:
                    IsolationRunner(parallel=2, verbosity=0, nonce="test-nonce", step="SELF")
                setup_databases.assert_not_called()
            self.assertIn("IsolationRunner was configured for 2 parallel processes", str(exc.exception))

            with mock.patch.object(MutationRunner, "setup_databases", autospec=True) as setup_databases:
                runner = MutationRunner(parallel=0, verbosity=0, nonce="test-nonce", step="MUT-test")
                self.assertEqual(runner.parallel, 0)
                runner.setup_databases()
                setup_databases.assert_called_once()
            with mock.patch.object(MutationRunner, "setup_databases", autospec=True) as setup_databases:
                with self.assertRaises(ParallelExecutionRefused) as exc:
                    MutationRunner(parallel=3, verbosity=0, nonce="test-nonce", step="MUT-test")
                setup_databases.assert_not_called()
            self.assertIn("MutationRunner was configured for 3 parallel processes", str(exc.exception))

    def test_direct_run_records_checkout_entry(self):
        with tempfile.TemporaryDirectory(prefix="bootstrap-surface-") as td:
            checkout, _, _ = _fixture_repo(td)
            run_root = Path(td) / "run-direct"
            evidence_dir = run_root / "evidence"
            dependency = bootstrap_mod.resolve_dependency_environment()
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "verification.run",
                    "--evidence-dir",
                    str(evidence_dir),
                    "--run-id",
                    "direct-run",
                    "--validated-dependency-root",
                    dependency["dependency_root"],
                    "--validated-site-packages",
                    dependency["site_packages"],
                ],
                cwd=str(checkout),
                capture_output=True,
                text=True,
            )
            summary = json.loads((evidence_dir / "summary.json").read_text(encoding="utf-8"))
            audit = json.loads((evidence_dir / "SOURCE_AUDIT.json").read_text(encoding="utf-8"))
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(summary["execution"]["mode"], "checkout-entry")
            self.assertTrue((evidence_dir / "SOURCE_AUDIT.json").exists())
            self.assertTrue(any("verification.route" in refusal and "outside the verified snapshot" in refusal for refusal in audit["refusals"]), audit)
            self.assertEqual(audit["run_id"], "direct-run")

    def test_missing_dependency_root_is_refused_by_name(self):
        with tempfile.TemporaryDirectory(prefix="bootstrap-surface-") as td:
            checkout, _, _ = _fixture_repo(td)
            evidence_dir = Path(td) / "run-missing" / "evidence"
            missing_root = Path(td) / "missing-root"
            missing_site = missing_root / "Lib" / "site-packages"
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "verification.run",
                    "--evidence-dir",
                    str(evidence_dir),
                    "--run-id",
                    "missing-root",
                    "--validated-dependency-root",
                    str(missing_root),
                    "--validated-site-packages",
                    str(missing_site),
                ],
                cwd=str(checkout),
                capture_output=True,
                text=True,
            )
            audit = json.loads((evidence_dir / "SOURCE_AUDIT.json").read_text(encoding="utf-8"))
            self.assertEqual(proc.returncode, 2)
            self.assertIn("validated dependency root does not exist", audit["route_failure"]["message"])

    def test_w_helper_success_is_refused_by_history_origin(self):
        with tempfile.TemporaryDirectory(prefix="bootstrap-surface-") as td:
            checkout, commit, _ = _fixture_repo(td, helper_mode=True)
            run_root = Path(td) / "run-2a"
            result = self._run(checkout, commit, run_root)
            surface = json.loads((run_root / "SURFACE_RESULT.json").read_text(encoding="utf-8"))
            audit = json.loads((run_root / "evidence" / "SOURCE_AUDIT.json").read_text(encoding="utf-8"))
            gate = self._gate(run_root)
            self.assertEqual(result["exit"], 2, result)
            self.assertFalse(gate["qualifying"], gate)
            self.assertIn("A1_W_HELPER_EXECUTED", surface["coordinator_stdout"])
            self.assertTrue(any(entry[0] == "w_only_helper" and entry[3] == "W" for entry in audit["history_refused"]), audit)
            self.assertTrue(any("w_only_helper" in refusal and "does not clear it" in refusal for refusal in audit["refusals"]), audit)

    def test_w_helper_importerror_is_refused_by_history_origin(self):
        with tempfile.TemporaryDirectory(prefix="bootstrap-surface-") as td:
            checkout, commit, _ = _fixture_repo(td, helper_mode=True)
            helper_path = Path(td) / "checkout" / "w_only_helper.py"
            helper_path.write_text("print('A1_W_HELPER_EXECUTED', flush=True)\nraise ImportError('optional dependency unavailable')\n", encoding="utf-8")
            run_root = Path(td) / "run-2b"
            result = self._run(checkout, commit, run_root)
            surface = json.loads((run_root / "SURFACE_RESULT.json").read_text(encoding="utf-8"))
            audit = json.loads((run_root / "evidence" / "SOURCE_AUDIT.json").read_text(encoding="utf-8"))
            gate = self._gate(run_root)
            self.assertEqual(result["exit"], 2, result)
            self.assertFalse(gate["qualifying"], gate)
            self.assertIn("A1_W_HELPER_EXECUTED", surface["coordinator_stdout"])
            self.assertGreaterEqual(audit["history_by_origin"].get("W", 0), 1, audit)
            self.assertTrue(any(entry[0] == "w_only_helper" and entry[3] == "W" for entry in audit["history_refused"]), audit)
            self.assertTrue(any("w_only_helper" in refusal and "does not clear it" in refusal for refusal in audit["refusals"]), audit)
            self.assertEqual(audit["modules_by_origin"].get("W", 0), 0, audit)
            self.assertFalse(audit["final_cache_is_complete_history"])

    def test_caught_importerror_qualifies_when_source_audit_uses_final_inventory_only(self):
        with tempfile.TemporaryDirectory(prefix="bootstrap-surface-") as td:
            checkout, commit, _ = _fixture_repo(td, helper_mode=True)
            run_py = Path(checkout) / "verification" / "run.py"
            original = run_py.read_text(encoding="utf-8")
            mutated_source_audit = textwrap.dedent(
                '''
                def source_audit(rec, roots):
                    modules_by_origin = {}
                    repository_modules = []
                    for name, mod in list(sys.modules.items()):
                        f = getattr(mod, "__file__", None)
                        if not f or name == "__main__":
                            modules_by_origin["(no file)"] = modules_by_origin.get("(no file)", 0) + 1
                            continue
                        cls = classify(f, roots)
                        modules_by_origin[cls] = modules_by_origin.get(cls, 0) + 1
                        if cls == "E(root)":
                            repository_modules.append(name)
                    return {
                        "run_id": rec["run_id"],
                        "launch_token": rec["launch_token"],
                        "refusals": [],
                        "modules_by_origin": modules_by_origin,
                        "repository_modules": sorted(repository_modules),
                        "history_by_origin": {},
                        "history_modules_by_origin": {},
                        "history_entries": 0,
                        "history_refused": [],
                        "final_cache_is_complete_history": True,
                    }
                '''
            ).lstrip()
            try:
                prefix, suffix = original.rsplit('if __name__ == "__main__":\n    sys.exit(main())', 1)
                mutated = prefix + '\n\n' + mutated_source_audit + '\nif __name__ == "__main__":\n    sys.exit(main())' + suffix
                run_py.write_text(mutated, encoding="utf-8")
                _git(checkout, "add", "verification/run.py")
                _git(checkout, "commit", "-qm", "mutated source audit")
                commit = _git(checkout, "rev-parse", "HEAD")
                helper_path = Path(td) / "checkout" / "w_only_helper.py"
                helper_path.write_text("print('A1_W_HELPER_EXECUTED', flush=True)\nraise ImportError('optional dependency unavailable')\n", encoding="utf-8")
                run_root = Path(td) / "run-mutated"
                result = self._run(checkout, commit, run_root)
                gate = self._gate(run_root)
                self.assertEqual(result["exit"], 0, result)
                self.assertTrue((run_root / "evidence" / "SOURCE_AUDIT.json").exists())
                self.assertTrue(gate["qualifying"], gate)
            finally:
                run_py.write_text(original, encoding="utf-8")

    def test_missing_launch_attestation_run_id_is_refused(self):
        with tempfile.TemporaryDirectory(prefix="bootstrap-surface-") as td:
            checkout, commit, _ = _fixture_repo(td)
            run_root = Path(td) / "run-2"
            self._run(checkout, commit, run_root)
            att_path = run_root / "LAUNCH_ATTESTATION.json"
            att = json.loads(att_path.read_text(encoding="utf-8"))
            att.pop("run_id")
            att_path.write_text(json.dumps(att), encoding="utf-8")
            gate = self._gate(run_root)
            self.assertFalse(gate["qualifying"], gate)
            self.assertTrue(any("run_id" in refusal and "launcher attestation" in refusal for refusal in gate["refusals"]), gate)

    def test_foreign_witness_binding_fields_are_refused(self):
        with tempfile.TemporaryDirectory(prefix="bootstrap-surface-") as td:
            checkout, commit, _ = _fixture_repo(td)
            run_root = Path(td) / "run-3"
            self._run(checkout, commit, run_root)
            post_path = run_root / "POST_RUN_SNAPSHOT_CHECK.json"
            post = json.loads(post_path.read_text(encoding="utf-8"))
            post.update(run_id="other-run", launch_token="other-token", commit="f" * 40)
            post_path.write_text(json.dumps(post), encoding="utf-8")
            gate = self._gate(run_root)
            self.assertFalse(gate["qualifying"], gate)
            self.assertTrue(any("launch token" in refusal or "run id" in refusal or "commit" in refusal for refusal in gate["refusals"]), gate)

    def test_witness_from_different_launch_is_refused(self):
        with tempfile.TemporaryDirectory(prefix="bootstrap-surface-") as td:
            checkout, commit, _ = _fixture_repo(td)
            run_root1 = Path(td) / "run-4"
            run_root2 = Path(td) / "run-5"
            self._run(checkout, commit, run_root1)
            self._run(checkout, commit, run_root2)
            shutil.copyfile(run_root2 / "POST_RUN_SNAPSHOT_CHECK.json", run_root1 / "POST_RUN_SNAPSHOT_CHECK.json")
            gate = self._gate(run_root1)
            self.assertFalse(gate["qualifying"], gate)
            binding = [refusal for refusal in gate["refusals"] if "launch token" in refusal or "run id" in refusal]
            self.assertTrue(binding, gate)

            post_path = run_root1 / "POST_RUN_SNAPSHOT_CHECK.json"
            surface_path = run_root1 / "SURFACE_RESULT.json"
            surface = json.loads(surface_path.read_text(encoding="utf-8"))
            surface["post_run_witness_sha256"] = hashlib.sha256(post_path.read_bytes()).hexdigest()
            surface_path.write_text(json.dumps(surface), encoding="utf-8")

            gate_resealed = self._gate(run_root1)
            self.assertFalse(gate_resealed["qualifying"], gate_resealed)
            binding_resealed = [refusal for refusal in gate_resealed["refusals"] if "launch token" in refusal or "run id" in refusal]
            self.assertTrue(binding_resealed, gate_resealed)
            self.assertFalse(any("sealed over" in refusal for refusal in gate_resealed["refusals"]), gate_resealed)

    def test_witness_newline_breaks_the_seal(self):
        with tempfile.TemporaryDirectory(prefix="bootstrap-surface-") as td:
            checkout, commit, _ = _fixture_repo(td)
            run_root = Path(td) / "run-6"
            self._run(checkout, commit, run_root)
            with open(run_root / "POST_RUN_SNAPSHOT_CHECK.json", "ab") as handle:
                handle.write(b"\n")
            gate = self._gate(run_root)
            self.assertFalse(gate["qualifying"], gate)
            self.assertTrue(any("sealed over" in refusal for refusal in gate["refusals"]), gate)


if __name__ == "__main__":
    unittest.main()