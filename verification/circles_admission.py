"""Once-only Circle witness faults, installed on the qualified clone by the existing runner."""
from pathlib import Path

from verification.c2_admission import C2AdmissionRunner


class CircleAdmissionRunner(C2AdmissionRunner):
    unit = "CIRCLES"
    registry = Path(__file__).with_suffix(".json")

    @property
    def migration_module(self):
        if self.entry["id"] in {"CIR-08", "CIR-08-OFFICE"}:
            return "core.migrations.0060_circle_coordinators"
        return "core.migrations.0059_operating_circles"
