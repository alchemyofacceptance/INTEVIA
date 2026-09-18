"""verification/bootstrap.py - qualifying launcher surface for the verification route.

This module is stdlib-only. It materialises a verified snapshot from a committed
checkout, writes the launch attestation, starts the coordinator, records the
post-run snapshot witness, and seals the surface result last.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.machinery
import importlib.metadata
import json
import os
import stat
import subprocess
import sys
import sysconfig
import tempfile
import time
import uuid
from pathlib import Path


RECORDS = (
    "LAUNCH_ATTESTATION.json",
    "POST_RUN_SNAPSHOT_CHECK.json",
    "SURFACE_RESULT.json",
)


def die(message, code=2):
    print("BOOTSTRAP REFUSED: %s" % message, flush=True)
    raise SystemExit(code)


def forms(path):
    return os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.realpath(path))


def under(path, root):
    return path == root or path.startswith(root + os.sep)


class DependencyPrequalificationError(RuntimeError):
    pass


def _read_pyvenv_home(pyvenv_cfg):
    for line in pyvenv_cfg.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip().lower() == "home":
            return os.path.realpath(value.strip())
    return None


def _purelib_for_root(root=None):
    scheme = sysconfig.get_default_scheme()
    if root is None:
        paths = sysconfig.get_paths(scheme=scheme)
    else:
        root = os.path.realpath(str(root))
        paths = sysconfig.get_paths(scheme=scheme, vars={"base": root, "platbase": root})
    return os.path.realpath(paths["purelib"])


def resolve_dependency_environment(executable=None):
    executable_path = Path(executable or sys.executable).resolve()
    executable_dir = executable_path.parent
    venv_root = None
    pyvenv_cfg_home = None
    for candidate in (executable_dir, executable_dir.parent):
        pyvenv_cfg = candidate / "pyvenv.cfg"
        if pyvenv_cfg.is_file():
            venv_root = candidate.resolve()
            pyvenv_cfg_home = _read_pyvenv_home(pyvenv_cfg)
            break

    base_prefix = Path(sys.base_prefix).resolve()
    dependency_root = venv_root or base_prefix
    site_packages = Path(_purelib_for_root(dependency_root if venv_root is not None else None))
    return {
        "executable": os.path.realpath(str(executable_path)),
        "base_prefix": os.path.realpath(str(base_prefix)),
        "dependency_root": os.path.realpath(str(dependency_root)),
        "site_packages": os.path.realpath(str(site_packages)),
        "venv_root": os.path.realpath(str(venv_root)) if venv_root is not None else None,
        "pyvenv_cfg_home": pyvenv_cfg_home,
    }


def validate_dependency_environment(checkout, snapshot, executable=None):
    env = resolve_dependency_environment(executable)
    refusals = []
    site_packages = Path(env["site_packages"])
    base_prefix = Path(env["base_prefix"])
    dependency_root = Path(env["dependency_root"])
    if not site_packages.is_dir():
        refusals.append("validated dependency root is missing its site-packages directory: %s" % site_packages)
    if env["venv_root"] is not None:
        pyvenv_cfg = Path(env["venv_root"]) / "pyvenv.cfg"
        if not pyvenv_cfg.is_file():
            refusals.append("claimed venv root %s is missing pyvenv.cfg" % pyvenv_cfg.parent)
        home = env["pyvenv_cfg_home"]
        if home is None:
            refusals.append("claimed venv root %s has no home entry in pyvenv.cfg" % pyvenv_cfg.parent)
        elif os.path.realpath(home) != str(base_prefix):
            refusals.append("pyvenv.cfg home %s does not resolve to the base prefix %s" % (home, base_prefix))
    checkout_real = Path(os.path.realpath(checkout))
    snapshot_real = Path(os.path.realpath(snapshot))
    if site_packages.is_relative_to(checkout_real):
        refusals.append("validated dependency root %s lies inside the checkout %s" % (site_packages, checkout_real))
    if site_packages.is_relative_to(snapshot_real):
        refusals.append("validated dependency root %s lies inside the snapshot %s" % (site_packages, snapshot_real))
    names = set()
    if site_packages.is_dir():
        for dist in importlib.metadata.distributions(path=[str(site_packages)]):
            name = (dist.metadata.get("Name") or "").strip().lower().replace("-", "_")
            if name:
                names.add(name)
    for required in ("django", "psycopg"):
        if not any(name == required or name.startswith(required + "_") for name in names):
            refusals.append("required distribution %s is absent from the validated dependency root %s" % (required, site_packages))
    if refusals:
        raise DependencyPrequalificationError("; ".join(refusals))
    env["dependency_root"] = str(dependency_root)
    env["site_packages"] = str(site_packages)
    return env


def git(args, cwd, input_bytes=None):
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    proc = subprocess.run(["git", "-C", cwd, *args], capture_output=True, env=env, input=input_bytes)
    if proc.returncode != 0:
        raise RuntimeError("git %s failed (%d): %s" % (args[0], proc.returncode, proc.stderr.decode("utf-8", "replace").strip()))
    return proc.stdout


def obj_sha1(kind, data):
    return hashlib.sha1(b"%s %d\0" % (kind.encode("ascii"), len(data)) + data).hexdigest()


def parse_tree(data):
    index = 0
    entries = []
    while index < len(data):
        space = data.index(b" ", index)
        nul = data.index(b"\0", space)
        mode = data[index:space].decode("ascii")
        name = data[space + 1:nul].decode("utf-8")
        oid = data[nul + 1:nul + 21].hex()
        entries.append((mode, name, oid))
        index = nul + 21
    return entries


def materialise(checkout, commit, dest):
    raw = git(["cat-file", "commit", commit], checkout)
    if obj_sha1("commit", raw) != commit:
        die("commit object does not hash to %s" % commit)
    tree = raw.split(b"\n", 1)[0].split(b" ", 1)[1].decode("ascii")
    entries = []

    def walk(tree_id, prefix):
        data = git(["cat-file", "tree", tree_id], checkout)
        if obj_sha1("tree", data) != tree_id:
            die("tree object does not hash to %s" % tree_id)
        for mode, name, oid in parse_tree(data):
            if name in ("", ".", "..", ".git") or "/" in name or "\\" in name:
                die("unsafe tree entry %r" % name)
            path = prefix + name
            if mode == "40000":
                walk(oid, path + "/")
            elif mode in ("100644", "100755"):
                entries.append((path, mode, oid))
            else:
                die("unsupported entry %s %s" % (mode, path))

    walk(tree, "")
    batch = git(["cat-file", "--batch"], checkout, input_bytes=("\n".join(oid for _, _, oid in entries) + "\n").encode("ascii"))
    blobs = {}
    position = 0
    for path, mode, oid in entries:
        newline = batch.index(b"\n", position)
        head = batch[position:newline].decode("ascii").split()
        if len(head) != 3 or head[0] != oid or head[1] != "blob":
            die("cat-file --batch returned %r for %s" % (head, oid))
        size = int(head[2])
        data = batch[newline + 1:newline + 1 + size]
        position = newline + 1 + size + 1
        if obj_sha1("blob", data) != oid:
            die("blob does not hash to %s" % oid)
        blobs[oid] = data

    os.makedirs(dest)
    for path, mode, oid in entries:
        full = os.path.join(dest, *path.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "xb") as handle:
            handle.write(blobs[oid])
        if mode == "100755":
            os.chmod(full, os.stat(full).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return commit, tree, entries


def inventory_of(root, entries=None):
    rows = []
    for base, _, files in os.walk(root):
        for name in sorted(files):
            full = os.path.join(base, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            data = Path(full).read_bytes()
            rows.append({"path": rel, "blob": obj_sha1("blob", data), "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
    rows.sort(key=lambda row: row["path"])
    if entries is not None:
        expected = {path: oid for path, _, oid in entries}
        observed = {row["path"]: row["blob"] for row in rows}
        if observed != expected:
            missing = sorted(set(expected) - set(observed))
            extra = sorted(set(observed) - set(expected))
            changed = sorted(path for path in set(expected) & set(observed) if expected[path] != observed[path])
            die("snapshot does not equal the committed tree: missing %s extra %s changed %s" % (missing[:5], extra[:5], changed[:5]))
    body = json.dumps(rows, indent=1, sort_keys=True).encode("utf-8")
    return rows, body, hashlib.sha256(body).hexdigest()


def prequalify_site(checkout, snapshot):
    env = validate_dependency_environment(checkout, snapshot)
    site_packages = env["site_packages"]
    site_prequalification = {
        "interpreter": {
            "executable": sys.executable,
            "version": sys.version.split()[0],
            "base_prefix": env["base_prefix"],
            "dependency_root": env["dependency_root"],
            "site_packages": site_packages,
            "site": [site_packages],
        },
        "validated_dependency_root": env["dependency_root"],
        "validated_site_packages": site_packages,
        "venv_root": env["venv_root"],
        "pyvenv_cfg_home": env["pyvenv_cfg_home"],
        "base_prefix": env["base_prefix"],
        "refusals": [],
    }
    if env["venv_root"] is not None:
        site_prequalification["interpreter"]["venv_root"] = env["venv_root"]
    return site_prequalification


def digest_of(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if os.path.exists(path) else None


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _check_required(record, record_name, fields, refusal_template, refusals):
    for field in fields:
        if field not in record or record[field] is None:
            refusals.append(refusal_template.format(field=field, record=record_name))


def gate(run_root):
    root = Path(run_root)
    refusals = []
    records = {}
    for name in RECORDS:
        path = root / name
        if not path.exists():
            if name == "LAUNCH_ATTESTATION.json":
                refusals.append("final evidence missing: no launcher attestation in the run")
            elif name == "POST_RUN_SNAPSHOT_CHECK.json":
                refusals.append("final evidence missing: no final snapshot witness (POST_RUN_SNAPSHOT_CHECK.json) in the run")
            else:
                refusals.append("final evidence missing: no completed surface result (SURFACE_RESULT.json) in the run")
            records[name] = None
            continue
        records[name] = read_json(path)

    evidence_dir = root / "evidence"
    for name in ("summary.json", "SOURCE_AUDIT.json"):
        path = evidence_dir / name
        if not path.exists():
            if name == "summary.json":
                refusals.append("final evidence missing: no coordinator summary in the run")
            else:
                refusals.append("final evidence missing: no source audit (SOURCE_AUDIT.json) in the run")
            records[name] = None
        else:
            records[name] = read_json(path)

    if refusals:
        return {"qualifying": False, "refusals": refusals, "records": records}

    att = records["LAUNCH_ATTESTATION.json"]
    post = records["POST_RUN_SNAPSHOT_CHECK.json"]
    surface = records["SURFACE_RESULT.json"]
    summary = records["summary.json"]
    audit = records["SOURCE_AUDIT.json"]

    _check_required(att, "launcher attestation (LAUNCH_ATTESTATION.json)", ["run_id", "launch_token", "commit", "inventory_digest", "snapshot", "site_prequalification"],
                    "required binding field {field} is absent from the launcher attestation (LAUNCH_ATTESTATION.json): refused before any comparison", refusals)
    _check_required(post, "final snapshot witness (POST_RUN_SNAPSHOT_CHECK.json)", ["run_id", "launch_token", "commit", "inventory_digest", "unchanged", "digest_after"],
                    "required binding field {field} is absent from the final snapshot witness (POST_RUN_SNAPSHOT_CHECK.json): refused before any comparison", refusals)
    _check_required(surface, "completed surface result (SURFACE_RESULT.json)", ["run_id", "launch_token", "commit", "inventory_digest", "digest_after", "post_run_snapshot_unchanged", "post_run_witness_sha256", "coordinator_exit", "summary_sha256", "source_audit_sha256", "final_exit"],
                    "required binding field {field} is absent from the completed surface result (SURFACE_RESULT.json): refused before any comparison", refusals)
    _check_required(summary, "coordinator summary (summary.json)", ["route", "run_id", "result", "exit_status", "execution", "identity"],
                    "required binding field {field} is absent from the coordinator summary (summary.json): refused before any comparison", refusals)
    _check_required(summary.get("execution", {}) if isinstance(summary.get("execution"), dict) else {}, "coordinator summary execution record (summary.json execution)", ["launch_token", "root", "entry", "mode"],
                    "required binding field {field} is absent from the coordinator summary execution record (summary.json execution): refused before any comparison", refusals)
    _check_required(summary.get("identity", {}) if isinstance(summary.get("identity"), dict) else {}, "coordinator summary identity record (summary.json identity)", ["commit", "attested_commit"],
                    "required binding field {field} is absent from the coordinator summary identity record (summary.json identity): refused before any comparison", refusals)
    _check_required(audit, "source audit (SOURCE_AUDIT.json)", ["run_id", "launch_token", "refusals"],
                    "required binding field {field} is absent from the source audit (SOURCE_AUDIT.json): refused before any comparison", refusals)

    if refusals:
        return {"qualifying": False, "refusals": refusals, "records": records}

    checks = []
    def check(ok, refusal, details):
        checks.append({"ok": bool(ok), "details": details})
        if not ok:
            refusals.append(refusal)

    check(surface["final_exit"] == 0, "the surface's final exit is %r, not 0: the run did not complete cleanly (post-run check, coordinator or cleanup)" % surface["final_exit"], {"final_exit": surface["final_exit"]})
    check(surface["coordinator_exit"] == 0, "the coordinator exited %r" % surface["coordinator_exit"], {"coordinator_exit": surface["coordinator_exit"]})
    check(surface["post_run_snapshot_unchanged"] is True and post["unchanged"] is True, "the final snapshot witness reports a change during the run (Q6)", {"surface": surface["post_run_snapshot_unchanged"], "witness": post["unchanged"]})
    check(att["launch_token"] == post["launch_token"] == surface["launch_token"] == summary["execution"]["launch_token"] == audit["launch_token"], "launch token disagrees across attestation, final witness, surface result, summary and audit", {"attestation": att["launch_token"], "witness": post["launch_token"], "surface": surface["launch_token"], "summary": summary["execution"]["launch_token"], "audit": audit["launch_token"]})
    check(att["run_id"] == post["run_id"] == surface["run_id"] == summary["run_id"] == audit["run_id"], "run id disagrees across attestation, final witness, surface result, summary and audit", {"attestation": att["run_id"], "witness": post["run_id"], "surface": surface["run_id"], "summary": summary["run_id"], "audit": audit["run_id"]})
    check(att["commit"] == post["commit"] == surface["commit"] == summary["identity"]["commit"], "the commit disagrees between attestation, final witness, surface result and identity", {"attestation": att["commit"], "witness": post["commit"], "surface": surface["commit"], "identity": summary["identity"]["commit"]})
    check(summary["identity"]["attested_commit"] == att["commit"], "the attested commit disagrees between attestation and identity", {"attestation": att["commit"], "identity": summary["identity"]["attested_commit"]})
    check(att["inventory_digest"] == post["inventory_digest"] == surface["inventory_digest"], "the pre-run snapshot inventory digest disagrees between attestation, final witness and surface result", {"attestation": att["inventory_digest"], "witness": post["inventory_digest"], "surface": surface["inventory_digest"]})
    check(att["inventory_digest"] == post["digest_after"] == surface["digest_after"], "the snapshot inventory digest disagrees between attestation, surface result and final witness", {"attestation": att["inventory_digest"], "witness": post["digest_after"], "surface": surface["digest_after"]})
    check(digest_of(root / "POST_RUN_SNAPSHOT_CHECK.json") == surface["post_run_witness_sha256"], "POST_RUN_SNAPSHOT_CHECK.json differs from the one the surface result sealed over", {"actual": digest_of(root / "POST_RUN_SNAPSHOT_CHECK.json"), "sealed": surface["post_run_witness_sha256"]})
    check(digest_of(evidence_dir / "summary.json") == surface["summary_sha256"], "summary.json differs from the one the surface result sealed over", {"actual": digest_of(evidence_dir / "summary.json"), "sealed": surface["summary_sha256"]})
    check(digest_of(evidence_dir / "SOURCE_AUDIT.json") == surface["source_audit_sha256"], "SOURCE_AUDIT.json differs from the one the surface result sealed over", {"actual": digest_of(evidence_dir / "SOURCE_AUDIT.json"), "sealed": surface["source_audit_sha256"]})
    check(att["snapshot"] == summary["execution"]["root"], "the evidence's root is not the attested snapshot", {"attestation": att["snapshot"], "summary_root": summary["execution"]["root"]})
    check(str(summary["execution"]["entry"]).startswith(str(att["snapshot"])), "the entry did not execute from the attested snapshot", {"attestation": att["snapshot"], "entry": summary["execution"]["entry"]})
    site_dirs = sorted(att["site_prequalification"]["interpreter"]["site"])
    check(site_dirs == sorted(summary["execution"].get("site_dirs", [])), "the live site directories differ from the prequalified list", {"attestation": site_dirs, "summary": sorted(summary["execution"].get("site_dirs", []))})
    check(att["site_prequalification"].get("refusals", []) == [], "the attestation records startup refusals", {"refusals": att["site_prequalification"].get("refusals", [])})
    check(str(summary["execution"]["mode"]).startswith("snapshot-entry"), "the route recorded a checkout-entry (non-qualifying) run", {"mode": summary["execution"]["mode"]})
    check(audit["refusals"] == [], "the source audit refused (detected after execution): %s" % "; ".join(map(str, audit["refusals"])), {"refusals": audit["refusals"]})
    check(summary["result"] == "PASS" and summary["exit_status"] == 0, "result not PASS/0", {"result": summary["result"], "exit_status": summary["exit_status"]})

    return {"qualifying": not refusals, "refusals": refusals, "checks": checks, "records": records}


def launch_coordinator(snapshot, checkout, evidence_dir, run_id, launch_token, commit, pycache_prefix, validated_dependency_root, validated_site_packages, skip_mutations=False):
    entry = os.path.join(snapshot, "verification", "run.py")
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "USERPROFILE", "TEMP", "TMP", "TMPDIR", "SystemRoot", "SYSTEMROOT", "LANG") or k.startswith(("INTEVIA_", "LC_", "SSL_CERT_"))}
    cmd = [sys.executable, "-I", "-S", "-u", "-X", "utf8", "-X", "pycache_prefix=" + pycache_prefix, entry, "--snapshot", snapshot, "--checkout", checkout, "--evidence-dir", evidence_dir, "--run-id", run_id, "--launch-token", launch_token, "--commit", commit, "--validated-dependency-root", validated_dependency_root, "--validated-site-packages", validated_site_packages]
    if skip_mutations:
        cmd.append("--skip-mutations")
    proc = subprocess.run(cmd, cwd=os.path.dirname(snapshot), env=env, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--expected-tree")
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--skip-mutations", action="store_true")
    args = parser.parse_args(argv)

    if not (getattr(sys.flags, "isolated", 0) and getattr(sys.flags, "no_site", 0)):
        die("the bootstrap must run under -I -S")

    checkout = os.path.abspath(args.checkout)
    run_root = os.path.abspath(args.run_root)
    evidence_dir = os.path.abspath(args.evidence_dir)
    os.makedirs(run_root, exist_ok=True)
    snapshot = os.path.join(run_root, "snapshot")
    pycache_prefix = os.path.join(run_root, "pycache")

    head = git(["rev-parse", "--verify", "HEAD"], checkout).decode("ascii").strip()
    if head != args.commit:
        die("checkout HEAD %s is not the declared commit %s" % (head, args.commit))

    commit, tree, entries = materialise(checkout, args.commit, snapshot)
    if args.expected_tree and tree != args.expected_tree:
        die("tree %s is not the expected tree %s" % (tree, args.expected_tree))

    rows, body, digest = inventory_of(snapshot, entries)
    with open(os.path.join(run_root, "execution_inventory.json"), "xb") as handle:
        handle.write(body)

    token = uuid.uuid4().hex
    try:
        site_prequalification = prequalify_site(checkout, snapshot)
    except DependencyPrequalificationError as exc:
        die(str(exc))
    attestation = {
        "run_id": args.run_id,
        "launch_token": token,
        "commit": commit,
        "inventory_digest": digest,
        "snapshot": snapshot,
        "site_prequalification": site_prequalification,
        "tree": tree,
        "snapshot_files": len(rows),
        "checkout": checkout,
        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with open(os.path.join(run_root, "LAUNCH_ATTESTATION.json"), "x", encoding="utf-8") as handle:
        json.dump(attestation, handle, indent=1)

    coordinator_exit, coordinator_stdout, coordinator_stderr = launch_coordinator(snapshot, checkout, evidence_dir, args.run_id, token, commit, pycache_prefix, site_prequalification["validated_dependency_root"], site_prequalification["validated_site_packages"], args.skip_mutations)
    post_rows, _, digest_after = inventory_of(snapshot)
    post = {
        "run_id": args.run_id,
        "launch_token": token,
        "commit": commit,
        "inventory_digest": digest,
        "unchanged": digest_after == digest,
        "digest_after": digest_after,
    }
    if not post["unchanged"]:
        before = {row["path"]: row for row in rows}
        after = {row["path"]: row for row in post_rows}
        post["created"] = sorted(set(after) - set(before))
        post["deleted"] = sorted(set(before) - set(after))
        post["changed"] = sorted(path for path in set(before) & set(after) if before[path]["sha256"] != after[path]["sha256"])
    with open(os.path.join(run_root, "POST_RUN_SNAPSHOT_CHECK.json"), "x", encoding="utf-8") as handle:
        json.dump(post, handle, indent=1)

    summary_path = os.path.join(evidence_dir, "summary.json")
    audit_path = os.path.join(evidence_dir, "SOURCE_AUDIT.json")
    summary_sha = digest_of(summary_path)
    audit_sha = digest_of(audit_path)

    final_exit = coordinator_exit
    if summary_sha is None or audit_sha is None:
        final_exit = 2 if final_exit == 0 else final_exit
    if not post["unchanged"]:
        final_exit = max(final_exit, 2) if final_exit != 3 else 3

    surface = {
        "run_id": args.run_id,
        "launch_token": token,
        "commit": commit,
        "inventory_digest": digest,
        "digest_after": digest_after,
        "post_run_snapshot_unchanged": post["unchanged"],
        "post_run_witness_sha256": digest_of(os.path.join(run_root, "POST_RUN_SNAPSHOT_CHECK.json")),
        "coordinator_exit": coordinator_exit,
        "summary_sha256": summary_sha,
        "source_audit_sha256": audit_sha,
        "final_exit": final_exit,
        "finished": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "coordinator_stdout": coordinator_stdout,
        "coordinator_stderr": coordinator_stderr,
    }
    with open(os.path.join(run_root, "SURFACE_RESULT.json"), "x", encoding="utf-8") as handle:
        json.dump(surface, handle, indent=1)

    print("BOOTSTRAP EXIT %d" % final_exit, flush=True)
    raise SystemExit(final_exit)


def bootstrap(python, checkout, commit, run_root, extra_args=None):
    run_root = os.path.abspath(str(run_root))
    evidence_dir = os.path.join(run_root, "evidence")
    cmd = [python, "-I", "-S", os.path.abspath(__file__), "--checkout", checkout, "--commit", commit, "--run-root", run_root, "--evidence-dir", evidence_dir]
    if extra_args:
        cmd.extend(extra_args)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return {"exit": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


if __name__ == "__main__":
    main()