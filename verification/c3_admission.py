"""Once-only C3 faults on the run-owned qualified clone."""
from pathlib import Path

from verification.c2_admission import C2AdmissionRunner


class C3AdmissionRunner(C2AdmissionRunner):
    registry = Path(__file__).with_suffix(".json")
    unit = "C3"
    migration_module = "core.migrations.0056_c3_authority_lifecycle"
