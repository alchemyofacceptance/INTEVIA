"""Once-only equivalence faults, installed on the qualified clone by the existing runner."""
from pathlib import Path

from verification.c2_admission import C2AdmissionRunner


class SharedAppendAdmissionRunner(C2AdmissionRunner):
    unit = "C1_16"
    registry = Path(__file__).with_suffix(".json")
    migration_module = "core.migrations.0057_shared_event_append"
