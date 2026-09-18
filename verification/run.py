"""Stage A of the verification route.

`run.py` installs the import witness on `sys.meta_path` before any repository
import, delegates to `verification.route`, derives its exit from the route's
`SystemExit`, writes `SOURCE_AUDIT.json` from both the recorded import-origin
history and the final module inventory, and returns the final exit. The route
owns `summary.json`. An invocation without `--snapshot` records
`checkout-entry` and is non-qualifying.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib.abc
import json
import os
import subprocess
import sys
import tempfile
import traceback


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_git(args, env=None, cwd=None):
    """Returns (exit code, stdout bytes, stderr text). Never converts a failure into an empty result."""
    process = subprocess.run(["git", *args], cwd=cwd or ROOT, capture_output=True, env=env)
    return process.returncode, process.stdout, process.stderr.decode("utf-8", "replace").strip()


def parse_status_z(raw):
    """Parse `git status --porcelain=v1 -z` entries."""
    parts = raw.split(b"\0")
    entries, index = [], 0
    while index < len(parts):
        item = parts[index]
        index += 1
        if not item:
            continue
        if len(item) < 4 or item[2:3] != b" ":
            raise ValueError("unrecognised status entry %r" % item[:60])
        xy, path = item[:2].decode("ascii"), item[3:].decode("utf-8", "surrogateescape")
        orig = None
        if "R" in xy or "C" in xy:
            if index >= len(parts) or not parts[index]:
                raise ValueError("rename or copy entry without its source path: %r" % path)
            orig = parts[index].decode("utf-8", "surrogateescape")
            index += 1
        entries.append({"xy": xy, "path": path, "orig_path": orig})
    return entries


class DependencyValidationError(RuntimeError):
    pass


def resolve_validated_dependency_environment(args):
    dependency_root = getattr(args, "validated_dependency_root", None)
    site_packages = getattr(args, "validated_site_packages", None)
    if not dependency_root:
        raise DependencyValidationError("validated dependency root was not supplied by the launcher")
    if not site_packages:
        raise DependencyValidationError("validated site-packages was not supplied by the launcher")
    dependency_root = os.path.realpath(dependency_root)
    site_packages = os.path.realpath(site_packages)
    if not os.path.isdir(dependency_root):
        raise DependencyValidationError("validated dependency root does not exist: %s" % dependency_root)
    if not os.path.isdir(site_packages):
        raise DependencyValidationError("validated site-packages does not exist: %s" % site_packages)
    return dependency_root, site_packages


def identity():
    """Compute repository identity and working-tree change inventory."""
    out = {"valid": False, "problems": []}
    code, head, err = run_git(["rev-parse", "--verify", "HEAD"])
    if code != 0:
        out["problems"].append("git rev-parse HEAD failed (%d): %s" % (code, err))
        return out
    out["commit"] = head.decode().strip()
    code, tree, err = run_git(["rev-parse", "--verify", "HEAD^{tree}"])
    if code != 0:
        out["problems"].append("git rev-parse HEAD^{tree} failed (%d): %s" % (code, err))
        return out
    out["commit_tree"] = tree.decode().strip()
    code, raw, err = run_git(["status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=none"])
    if code != 0:
        out["problems"].append("git status failed (%d): %s" % (code, err))
        return out
    try:
        entries = parse_status_z(raw)
    except ValueError as exc:
        out["problems"].append("git status could not be parsed: %s" % exc)
        return out

    code, flags_raw, err = run_git(["ls-files", "-v", "-z"])
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
        out["problems"].append("%d path(s) carry git index flags that hide working-tree changes from git status, so the change inventory cannot be trusted: %s. The route does not clear them; clear them (git update-index --no-assume-unchanged / --no-skip-worktree) or use a checkout without them" % (len(flagged), "; ".join("%s (%s)" % (f["path"], ", ".join(f["flags"])) for f in flagged[:20])))

    rel_evidence = os.path.relpath(os.getcwd(), ROOT).replace(os.sep, "/")
    inside = not rel_evidence.startswith("..")
    changes = []
    for entry in entries:
        if inside and (entry["path"] == rel_evidence or entry["path"].startswith(rel_evidence + "/")):
            continue
        full = os.path.join(ROOT, entry["path"])
        rec = {"status": entry["xy"], "path": entry["path"], "orig_path": entry["orig_path"]}
        if "D" in entry["xy"] and not os.path.lexists(full):
            rec.update(kind="deleted", sha256=None)
        elif os.path.islink(full) or (os.path.lexists(full) and not os.path.isfile(full)):
            rec.update(kind="unsupported", sha256=None)
            out["problems"].append("unsupported working-tree entry (not a regular file): %s" % entry["path"])
        elif not os.path.isfile(full):
            rec.update(kind="missing", sha256=None)
            out["problems"].append("changed path has no file and is not a deletion: %s" % entry["path"])
        else:
            rec.update(kind="file", sha256=sha256_file(full), bytes=os.path.getsize(full))
        changes.append(rec)
    out["working_tree_changes"] = changes
    out["working_tree_clean"] = not changes

    tmp = tempfile.mkdtemp(prefix="verification_index_")
    try:
        env = dict(os.environ, GIT_INDEX_FILE=os.path.join(tmp, "index"))
        code, _, err = run_git(["read-tree", "HEAD"], env=env)
        pathspec = ["--", "."]
        if inside:
            ignored, _, err_ci = run_git(["check-ignore", "-q", rel_evidence + "/"])
            if ignored not in (0, 1):
                out["problems"].append("git check-ignore failed (%d): %s" % (ignored, err_ci))
                return out
            if ignored == 1:
                pathspec.append(":(exclude)" + rel_evidence)
        if code == 0:
            code, _, err = run_git(["add", "-A"] + pathspec, env=env)
        if code == 0:
            code, wt, err = run_git(["write-tree"], env=env)
        if code != 0:
            out["problems"].append("could not compute the working-tree git tree (%d): %s" % (code, err))
            return out
        out["working_tree_git_tree"] = wt.decode().strip()
    finally:
        pass

    code, delta, err = run_git(["diff-tree", "-r", "-z", "--no-renames", "--name-only", out["commit_tree"], out["working_tree_git_tree"]])
    if code != 0:
        out["problems"].append("git diff-tree of the commit tree and the working-files tree failed (%d): %s" % (code, err))
        return out
    delta_paths = sorted(path.decode("utf-8", "surrogateescape") for path in delta.split(b"\0") if path)
    listed = {change["path"] for change in changes} | {change["orig_path"] for change in changes if change.get("orig_path")}
    unlisted = [path for path in delta_paths if path not in listed]
    out["tree_delta_paths"] = delta_paths
    if unlisted:
        out["problems"].append("the git tree of the working files differs from the commit in %d path(s) the change inventory does not list: %s" % (len(unlisted), "; ".join(unlisted[:20])))
    out["valid"] = not out["problems"]
    if not out["valid"]:
        out["tested"] = "IDENTITY NOT ESTABLISHED: " + "; ".join(out["problems"])
    elif not changes and out["working_tree_git_tree"] == out["commit_tree"]:
        out["tested"] = "commit %s exactly" % out["commit"]
    else:
        out["tested"] = "commit %s plus the listed working-tree changes; git tree of the working files %s" % (out["commit"], out["working_tree_git_tree"])
    return out


def forms(path):
    return os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.realpath(path))


def under(path, root):
    return path == root or path.startswith(root + os.sep)


def classify(path, roots):
    if path is None or path in ("built-in", "frozen", "namespace"):
        return "(no file)"
    lit, real = forms(path)
    v_root = roots.get("V")
    if under(real, roots["W"][1]) and not (v_root and under(real, v_root[1])):
        return "W"
    if under(real, roots["E"][1]):
        return "E(root)"
    if v_root and under(real, v_root[1]):
        return "V"
    if under(real, roots["A"][1]):
        return "A"
    if under(lit, roots["A"][0]):
        return "A(interpreter-owned symlink)"
    if v_root and under(lit, v_root[0]):
        return "V(symlink out of V)"
    return "OTHER"


def install_witness(rec):
    origins = []

    class Recorder(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path, target=None):
            for finder in sys.meta_path[1:]:
                spec = finder.find_spec(name, path, target) if hasattr(finder, "find_spec") else None
                if spec is not None:
                    origins.append((name, spec.origin, type(spec.loader).__name__ if spec.loader else None))
                    return spec
            return None

    sys.meta_path.insert(0, Recorder())
    rec["witnesses"] = {"meta_path_origins": origins}


def source_audit(rec, roots):
    refusals = []
    history_by_origin = {}
    history_modules_by_origin = {}
    history_refused = []
    seen = set()
    repository_modules = []

    history = rec.get("witnesses", {}).get("meta_path_origins", [])
    for name, origin, loader in history:
        if origin is None or origin in ("built-in", "frozen", "namespace"):
            history_by_origin["(no file)"] = history_by_origin.get("(no file)", 0) + 1
            continue
        cls = classify(origin, roots)
        history_by_origin[cls] = history_by_origin.get(cls, 0) + 1
        history_modules_by_origin.setdefault(cls, []).append(name)
        resolved = forms(origin)[1]
        if cls == "E(root)" and name not in repository_modules:
            repository_modules.append(name)
        if cls in ("W", "OTHER") and (name, resolved) not in seen:
            seen.add((name, resolved))
            history_refused.append([name, resolved, loader, cls])
            refusals.append("module %s was resolved and executed from outside the verified snapshot and the trusted roots: %s (%s, %s); its absence from the final module inventory (import failed or module removed) does not clear it" % (name, resolved, cls, loader))

    modules_by_origin = {}
    for name, mod in list(sys.modules.items()):
        f = getattr(mod, "__file__", None)
        if not f or name == "__main__":
            modules_by_origin["(no file)"] = modules_by_origin.get("(no file)", 0) + 1
            continue
        cls = classify(f, roots)
        modules_by_origin[cls] = modules_by_origin.get(cls, 0) + 1
        resolved = forms(f)[1]
        if cls == "E(root)" and name not in repository_modules:
            repository_modules.append(name)
        elif cls in ("W", "OTHER") and (name, resolved) not in seen:
            seen.add((name, resolved))
            refusals.append("module %s executed from outside the verified snapshot and the trusted roots: %s (%s)" % (name, resolved, cls))

    return {
        "run_id": rec["run_id"],
        "launch_token": rec["launch_token"],
        "route_failure": rec.get("route_failure"),
        "refusals": refusals,
        "modules_by_origin": modules_by_origin,
        "repository_modules": sorted(repository_modules),
        "history_by_origin": history_by_origin,
        "history_modules_by_origin": {key: sorted(value) for key, value in history_modules_by_origin.items()},
        "history_entries": len(history),
        "history_refused": history_refused,
        "final_cache_is_complete_history": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="S015 verification route entry point")
    parser.add_argument("--snapshot", default=None)
    parser.add_argument("--checkout", default=None)
    parser.add_argument("--evidence-dir", default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--launch-token", default=None)
    parser.add_argument("--commit", default=None)
    parser.add_argument("--validated-dependency-root", default=None)
    parser.add_argument("--validated-site-packages", default=None)
    parser.add_argument("--skip-mutations", action="store_true")
    args, remainder = parser.parse_known_args(argv)

    rec = {
        "run_id": args.run_id,
        "launch_token": args.launch_token,
        "root": os.path.abspath(args.snapshot or ROOT),
        "entry": os.path.realpath(__file__),
        "mode": "snapshot-entry" if args.snapshot else "checkout-entry",
    }
    install_witness(rec)
    dependency_root = None
    site_packages = None
    route_exit = 0
    route_failure = None
    try:
        dependency_root, site_packages = resolve_validated_dependency_environment(args)
        sys.path.insert(0, site_packages)
        sys.path.insert(0, os.path.abspath(args.snapshot or ROOT))
        route_argv = list(remainder)
        if args.snapshot and "--snapshot" not in route_argv:
            route_argv.extend(["--snapshot", args.snapshot])
        if args.checkout and "--checkout" not in route_argv:
            route_argv.extend(["--checkout", args.checkout])
        if args.evidence_dir and "--evidence-dir" not in route_argv:
            route_argv.extend(["--evidence-dir", args.evidence_dir])
        if args.run_id and "--run-id" not in route_argv:
            route_argv.extend(["--run-id", args.run_id])
        if args.launch_token and "--launch-token" not in route_argv:
            route_argv.extend(["--launch-token", args.launch_token])
        if args.commit and "--commit" not in route_argv:
            route_argv.extend(["--commit", args.commit])
        if args.skip_mutations and "--skip-mutations" not in route_argv:
            route_argv.append("--skip-mutations")

        from verification import route as route_mod
        route_mod.main(route_argv)
    except DependencyValidationError as exc:
        route_exit = 2
        route_failure = {"type": type(exc).__name__, "message": str(exc), "traceback": ""}
        rec["route_failure"] = route_failure
    except SystemExit as exc:
        if isinstance(exc.code, int):
            route_exit = exc.code
        elif exc.code is None:
            route_exit = 0
        else:
            route_exit = 1
    except Exception as exc:
        route_exit = 1
        route_failure = {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()[-4000:]}
        rec["route_failure"] = route_failure
    finally:
        if route_exit == 0 and route_failure is None:
            rec["site_dirs"] = [site_packages]
        roots = {"W": forms(args.checkout or ROOT), "E": forms(args.snapshot or ROOT), "V": forms(dependency_root) if dependency_root is not None else None, "A": forms(sys.base_prefix)}
        audit = source_audit(rec, roots)
        evidence_dir = args.evidence_dir or os.path.join(ROOT, "verification-evidence", args.run_id or "run")
        os.makedirs(evidence_dir, exist_ok=True)
        try:
            with open(os.path.join(evidence_dir, "SOURCE_AUDIT.json"), "x", encoding="utf-8") as handle:
                json.dump(audit, handle, indent=1)
        except Exception as exc:
            if route_failure is None:
                route_exit = 1
                route_failure = {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()[-4000:]}
                rec["route_failure"] = route_failure
    if route_failure is not None and route_failure["type"] != "DependencyValidationError":
        raise RuntimeError("%s: %s" % (route_failure["type"], route_failure["message"]))
    final_result = "PASS" if route_exit == 0 and not audit["refusals"] else "INCOMPLETE"
    final_exit = 0 if final_result == "PASS" else (2 if route_exit == 0 else route_exit)
    return final_exit


if __name__ == "__main__":
    sys.exit(main())