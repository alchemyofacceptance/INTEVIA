"""Explicit once-only F0 source-fault checks, in a disposable copy only.

Never used by ordinary CI. A normal PASS must precede each single-source fault;
that one target must FAIL by assertion without ERROR; restored bytes must PASS.
These source faults do not fit the SQL mutation registry. No database is used.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    entries=json.loads((ROOT/"tools/f0_admission.json").read_text())
    results=[]
    with tempfile.TemporaryDirectory(prefix="intevia-f0-admission-") as temp:
        copy=Path(temp)/"candidate"
        shutil.copytree(ROOT,copy,ignore=shutil.ignore_patterns(".git",".venv","venv","__pycache__",".pytest_cache"))
        for entry in entries:
            if entry["class"]!="ADMISSION": raise SystemExit("Only explicit admission entries are supported.")
            path=copy/entry["path"];original=path.read_text()
            if original.count(entry["before"])!=1: raise SystemExit("Fault anchor is not unique: "+entry["id"])
            record={"id":entry["id"],"class":entry["class"],"target":entry["target"],"phases":{}}
            try:
                for phase in ("normal","fault","restored"):
                    path.write_text(original.replace(entry["before"],entry["after"],1) if phase=="fault" else original)
                    # Python bytecode timestamps must not hide a source replacement.
                    for cache in copy.rglob("__pycache__"): shutil.rmtree(cache)
                    report=out/(entry["id"]+"-"+phase+".json")
                    start=time.perf_counter()
                    run=subprocess.run([sys.executable,"tools/f0_tests.py",entry["target"],"--report",str(report)],cwd=copy,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
                    (out/(entry["id"]+"-"+phase+".log")).write_text(run.stdout)
                    facts=json.loads(report.read_text()) if report.exists() else {}
                    correct=(facts.get("tests")==1 and facts.get("errors")==0 and facts.get("skips")==0 and facts.get("failures")==int(phase=="fault") and run.returncode==int(phase=="fault"))
                    record["phases"][phase]={"ok":correct,"seconds":round(time.perf_counter()-start,3),**facts}
                    if not correct:
                        results.append(record);(out/"admission-summary.json").write_text(json.dumps(results,indent=2)+"\n")
                        raise SystemExit(f"{entry['id']} {phase}: expected assertion-only FAIL or normal PASS not obtained; see retained log.")
            finally: path.write_text(original)
            results.append(record);print(entry["id"]+": PASS → assertion FAIL → PASS")
    (out/"admission-summary.json").write_text(json.dumps(results,indent=2)+"\n")
if __name__=="__main__":main()
