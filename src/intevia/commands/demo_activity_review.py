from __future__ import annotations

import sys
from typing import TextIO


def run_demo(*, output: TextIO | None = None) -> int:
    """Render the bounded Slice 001 lifecycle contract without mutation."""
    if output is None:
        output = sys.stdout

    print("INTEVIA v1.0 — Governed Contribution Lifecycle", file=output)
    print("", file=output)
    print("Identity: authenticated Django User and Profile", file=output)
    print("Authority: role assignment plus entitlement capability", file=output)
    print("Attribution: actor strings do not establish authority", file=output)
    print("", file=output)
    print("Initial flow:", file=output)
    print("draft -> submitted -> under_review -> accepted|rejected", file=output)
    print("", file=output)
    print("Correction flow:", file=output)
    print(
        "accepted|rejected -> correction_requested -> "
        "correction_pending_review -> submitted",
        file=output,
    )
    print("Correction creates an immutable successor version.", file=output)
    print("Every successor version requires a new Human decision.", file=output)
    print("Transitions and decisions retain authority and lineage.", file=output)
    return 0


def main() -> int:
    return run_demo()


if __name__ == "__main__":
    raise SystemExit(main())