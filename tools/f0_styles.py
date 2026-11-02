"""Fetch the pinned standalone compiler; build or verify committed F0 CSS."""
from __future__ import annotations
import argparse
import hashlib
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

# Isolated mode removes the script directory; only this known sibling path is restored.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from f0_theme_reference import reference_bytes

VERSION = "4.2.3"
ASSETS = {
    "Linux": ("tailwindcss-linux-x64", "addc85c21c2bba8c95082b3b936f9fea36592988707f86e1b4df5aab9b5df429"),
    "Windows": ("tailwindcss-windows-x64.exe", "29c13ec752ea3e30363ee9930092119eefddc44c584e76527cdf73aec4145f4c"),
}
ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "core/static/core/f0.css"

def digest(path):
    return hashlib.file_digest(path.open("rb"), "sha256").hexdigest()

def compiler(cache):
    if platform.machine().lower() not in {"x86_64", "amd64"} or platform.system() not in ASSETS:
        raise SystemExit("F0's compiler pin supports Windows x64 and Linux x64 only.")
    name, expected = ASSETS[platform.system()]
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / name
    if not target.exists():
        url = f"https://github.com/tailwindlabs/tailwindcss/releases/download/v{VERSION}/{name}"
        fd, temp = tempfile.mkstemp(dir=cache, prefix="download-")
        try:
            with os.fdopen(fd, "wb") as output, urllib.request.urlopen(url, timeout=120) as response:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            if digest(Path(temp)) != expected:
                raise SystemExit(f"Compiler hash mismatch: {name}; the download was not installed.")
            os.replace(temp, target)
        except (urllib.error.URLError, TimeoutError):
            raise SystemExit("Compiler download failed: network. This is not a stylesheet mismatch.") from None
        finally:
            Path(temp).unlink(missing_ok=True)
    actual = digest(target)
    if actual != expected:
        raise SystemExit(f"Compiler hash mismatch: {name}; expected {expected}; actual {actual}. Refusing to run it.")
    if platform.system() != "Windows":
        target.chmod(0o700)
    return target

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("fetch", "build", "check"))
    parser.add_argument("--cache", type=Path, default=Path(tempfile.gettempdir()) / "intevia-tailwind-4.2.3")
    args = parser.parse_args()
    started = time.perf_counter()
    exe = compiler(args.cache.resolve())
    if args.mode != "fetch":
        # Output always lives outside the scan tree; only explicit @source paths are scanned.
        with tempfile.TemporaryDirectory(prefix="intevia-f0-css-") as work:
            output = Path(work) / "f0.css"
            subprocess.run([str(exe), "-i", "core/styles/theme.css", "-o", str(output), "--minify"], cwd=ROOT, check=True)
            built = output.read_bytes().replace(b"\r\n", b"\n")
            # The commission excludes even informational external URLs in CSS.
            # Keep the version/licence banner; the full MIT text sits beside CSS.
            built = built.replace(b"https://tailwindcss.com", b"see TAILWIND-LICENSE.txt")
            reference = ROOT / "core/static/core/theme-reference.json"
            if args.mode == "check":
                if not reference.exists() or reference.read_bytes() != reference_bytes():
                    raise SystemExit("Theme reference rebuild mismatch: core/static/core/theme-reference.json")
                if not CSS.exists() or CSS.read_bytes() != built:
                    raise SystemExit("Stylesheet rebuild mismatch: core/static/core/f0.css. Rebuild and commit that file.")
            else:
                CSS.parent.mkdir(parents=True, exist_ok=True)
                CSS.write_bytes(built)
                reference.write_bytes(reference_bytes())
    print(f"F0 styles {args.mode}: PASS ({time.perf_counter()-started:.3f}s)")

if __name__ == "__main__":
    main()
