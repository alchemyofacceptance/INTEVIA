"""Consent Surface Skeleton v1.0.

This module defines the minimal governed consent structure required
for v1.0 test-case protection.

Boundary:
    - structural only
    - no behaviour
    - no automation
    - no activation
    - no consent workflows
    - no consent interpretation
    - no notification systems
    - no CARE workflows
    - no evidence cadence
    - no queue logic
    - no review routing
    - no cohort logic
    - no v1.1 surfaces
    - no runtime activation
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from intevia.human_node import HumanNode
from intevia.relationship_surface import ConsentState


class ConsentBand(str, Enum):
    """Minimal consent-band vocabulary for v1.0 protection."""

    NONE = "none"
    MINIMAL = "minimal"
    STANDARD = "standard"
    EXTENDED = "extended"
    RESTRICTED = "restricted"


@dataclass(slots=True)
class ConsentSurface:
    """Minimal governed Consent Surface skeleton.

    The ConsentSurface skeleton links a HumanNode to non-activating consent
    posture fields for v1.0 test-case protection.

    It does not activate, interpret, request, enact, automate, route, or
    enforce consent. It does not create consent workflows, notification
    systems, CARE workflows, evidence cadence, queue logic, review routing,
    cohort logic, dashboards, or runtime surfaces.

    The Human remains the governor. Any future consent behaviour must be
    separately designed, reviewed, authorised, and evidenced.
    """

    human_node_ref: HumanNode
    consent_state: ConsentState = ConsentState.UNKNOWN
    consent_band: ConsentBand = ConsentBand.NONE
    consent_flags: dict = field(default_factory=dict)
    consent_window: datetime | None = None
    consent_history: list = field(default_factory=list)


__all__ = [
    "ConsentBand",
    "ConsentSurface",
]
