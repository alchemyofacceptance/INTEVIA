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
    checkout = Path(root) / "checkout"
    checkout.mkdir()
    _write(checkout / "verification" / "__init__.py", "")
    _write(checkout / "verification" / "route.py", textwrap.dedent(
        '''
        def main(a, rec, ident, evidence_dir):
            from verification import parse_results
            rec["route_optional_helper"] = parse_results.OPTIONAL_HELPER
            return 0
        '''
    ).lstrip())
    parse_results = """try:\n    import optional_helper\n    OPTIONAL_HELPER = True\nexcept ImportError:\n    OPTIONAL_HELPER = False\n"""
    if helper_mode:
        parse_results = (
            "import os, sys\n"
            "sys.path.insert(0, os.environ['INTEVIA_A1_PROBE_W'])\n"
            "try:\n    import w_only_helper\nexcept ImportError:\n    pass\n"
            "OPTIONAL_HELPER = False\n"
        )
    _write(checkout / "verification" / "parse_results.py", parse_results)
    _write(checkout / "verification" / "run.py", textwrap.dedent(
        '''
        from __future__ import annotations

        import argparse
        import importlib.machinery
        import json
        import os
        import sys

        def _forms(path):
            return os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.realpath(path))

        def _under(path, root):
            return path == root or path.startswith(root + os.sep)

        def _classify(path, roots):
            if path is None or path in ("built-in", "frozen", "namespace"):
                return "(no file)"
            lit, real = _forms(path)
            if _under(real, roots["checkout"][1]) and not _under(real, roots["snapshot"][1]):
                return "W"
            if _under(real, roots["snapshot"][1]):
                return "E"
            if _under(real, roots["V"][1]):
                return "V"
            if _under(real, roots["A"][1]):
                return "A"
            return "OTHER"

        def _audit(rec, roots):
            refusals = []
            for name, mod in list(sys.modules.items()):
                f = getattr(mod, "__file__", None)
                cls = _classify(f, roots)
                if cls in ("W", "OTHER"):
                    refusals.append("module %s executed from outside the verified snapshot and the trusted roots: %s (%s)" % (name, f, cls))
            return {"run_id": rec["run_id"], "launch_token": rec["launch_token"], "refusals": refusals}

        def main(argv=None):
            parser = argparse.ArgumentParser()
            parser.add_argument("--snapshot", required=True)
            parser.add_argument("--checkout", required=True)
            parser.add_argument("--evidence-dir", required=True)
            parser.add_argument("--run-id", required=True)
            parser.add_argument("--launch-token", required=True)
            parser.add_argument("--commit", required=True)
            parser.add_argument("--skip-mutations", action="store_true")
            args = parser.parse_args(argv)
            if not (sys.flags.isolated and sys.flags.no_site):
                raise SystemExit(2)
            snapshot = os.path.abspath(args.snapshot)
            checkout = os.path.abspath(args.checkout)
            rec = {
                "run_id": args.run_id,
                "launch_token": args.launch_token,
                "root": snapshot,
                "entry": os.path.realpath(__file__),
                "mode": "snapshot-entry",
                "site_dirs": list(__import__("site").getsitepackages()),
            }
            history = []
            class Recorder(importlib.machinery.PathFinder.__class__):
                pass
            sys.path.append(snapshot)
            from verification import route
            result = route.main(args, rec, {"commit": args.commit}, args.evidence_dir)
            audit = _audit(rec, {"checkout": _forms(checkout), "snapshot": _forms(snapshot), "V": _forms(sys.prefix), "A": _forms(sys.base_prefix)})
            final_result = "PASS" if result == 0 and not audit["refusals"] else "INCOMPLETE"
            final_exit = 0 if final_result == "PASS" else 2
            summary = {"run_id": args.run_id, "result": final_result, "exit_status": final_exit, "execution": rec, "identity": {"commit": args.commit}}
            os.makedirs(args.evidence_dir, exist_ok=True)
            with open(os.path.join(args.evidence_dir, "summary.json"), "x", encoding="utf-8") as f:
                json.dump(summary, f, indent=1)
            with open(os.path.join(args.evidence_dir, "SOURCE_AUDIT.json"), "x", encoding="utf-8") as f:
                json.dump(audit, f, indent=1)
            return final_exit

        if __name__ == "__main__":
            raise SystemExit(main())
        '''
    ).lstrip())
    _write(checkout / "w_only_helper.py", "print('A1_W_HELPER_EXECUTED', flush=True)\n")
    _git(checkout, "init", "-q")
    _git(checkout, "add", "-A")
    _git(checkout, "commit", "-qm", "fixture")
    commit = _git(checkout, "rev-parse", "HEAD")
    tree = _git(checkout, "rev-parse", "HEAD^{tree}")
    return checkout, commit, tree


class BootstrapSurfaceTests(unittest.TestCase):
    def _run(self, checkout, commit, run_root, extra_args=None, env=None):
        patch = mock.patch.dict(os.environ, env or {}, clear=False)
        with patch:
            return bootstrap_mod.bootstrap(sys.executable, str(checkout), commit, str(run_root), extra_args or ["--run-id", Path(run_root).name])

    def _gate(self, run_root):
        return bootstrap_mod.gate(run_root)

    def test_clean_run_qualifies(self):
        with tempfile.TemporaryDirectory(prefix="bootstrap-surface-") as td:
            checkout, commit, _ = _fixture_repo(td)
            run_root = Path(td) / "run-1"
            result = self._run(checkout, commit, run_root)
            gate = self._gate(run_root)
            self.assertEqual(result["exit"], 0, result)
            self.assertTrue(gate["qualifying"], gate)
            self.assertFalse(gate["refusals"], gate)

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