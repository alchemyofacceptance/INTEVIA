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
    _write(checkout / "verification" / "__init__.py", "")
    _write(checkout / "verification" / "fixture_v" / "v_only_helper.py", "print('A1_V_HELPER_EXECUTED', flush=True)\n")
    _write(checkout / "verification" / "repo_helper.py", "print('A1_REPO_HELPER_EXECUTED', flush=True)\n")
    _write(checkout / "verification" / "route.py", textwrap.dedent(
        '''
        import argparse
        import json
        import os
        import site
        import sys

        def main(argv=None):
            from verification import parse_results
            parser = argparse.ArgumentParser()
            parser.add_argument("--evidence-dir", required=True)
            parser.add_argument("--run-id", required=True)
            parser.add_argument("--launch-token", required=True)
            parser.add_argument("--commit", required=True)
            parser.add_argument("--snapshot", required=True)
            parser.add_argument("--checkout", required=True)
            parser.add_argument("--skip-mutations", action="store_true")
            args = parser.parse_args(argv)
            _ = parse_results.OPTIONAL_HELPER
            summary = {
                "route": "S015 verification route v0.5",
                "run_id": args.run_id,
                "result": "PASS",
                "exit_status": 0,
                "execution": {
                    "launch_token": args.launch_token,
                    "root": args.snapshot,
                    "entry": os.path.join(args.snapshot, "verification", "route.py"),
                    "mode": "snapshot-entry",
                    "site_dirs": list(site.getsitepackages()) if hasattr(site, "getsitepackages") else [],
                },
                "identity": {
                    "commit": args.commit,
                    "attested_commit": args.commit,
                },
            }
            os.makedirs(args.evidence_dir, exist_ok=True)
            with open(os.path.join(args.evidence_dir, "summary.json"), "x", encoding="utf-8") as handle:
                json.dump(summary, handle, indent=1)
            sys.exit(0)
        '''
    ).lstrip())
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
            surface = json.loads((run_root / "SURFACE_RESULT.json").read_text(encoding="utf-8"))
            audit = json.loads((run_root / "evidence" / "SOURCE_AUDIT.json").read_text(encoding="utf-8"))
            gate = self._gate(run_root)
            self.assertEqual(result["exit"], 0, result)
            self.assertTrue(gate["qualifying"], gate)
            self.assertFalse(gate["refusals"], gate)
            self.assertEqual(summary["route"], "S015 verification route v0.5")
            self.assertEqual(summary["identity"]["attested_commit"], commit)
            self.assertEqual(summary["execution"]["launch_token"], surface["launch_token"])
            self.assertTrue((run_root / "evidence" / "summary.json").exists())
            self.assertTrue((run_root / "evidence" / "SOURCE_AUDIT.json").exists())
            self.assertTrue(gate["qualifying"], gate)
            self.assertIn("A1_REPO_HELPER_EXECUTED", surface["coordinator_stdout"])
            self.assertIn("A1_V_HELPER_EXECUTED", surface["coordinator_stdout"])
            self.assertGreaterEqual(audit["history_by_origin"].get("E(root)", 0), 1, audit)
            self.assertIn("verification.route", audit["history_modules_by_origin"].get("E(root)", []), audit)
            self.assertIn("verification.repo_helper", audit["repository_modules"])
            self.assertFalse(audit["final_cache_is_complete_history"])

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