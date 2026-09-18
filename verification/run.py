from __future__ import annotations

import argparse
import datetime
import hashlib
import os
import subprocess
import sys
import tempfile


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


def main(argv=None):
    parser = argparse.ArgumentParser(description="S015 verification route entry point")
    parser.add_argument("--evidence-dir", default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--skip-mutations", action="store_true")
    parser.parse_known_args(argv)

    from verification import route as route_mod

    return route_mod.main()


if __name__ == "__main__":
    sys.exit(main())