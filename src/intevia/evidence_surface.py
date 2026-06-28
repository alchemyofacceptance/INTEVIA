"""Evidence Surface Skeleton v1.0.

This module defines the minimal governed evidence structure required
for v1.0 test-case protection.

Boundary:
    - structural only
    - no behaviour
    - no automation
    - no activation
    - no evidence interpretation
    - no evidence routing
    - no evidence review
    - no audit workflows
    - no CARE workflows
    - no evidence cadence
    - no cohort logic
    - no v1.1 surfaces
    - no runtime activation
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from intevia.consent_surface import ConsentSurface
from intevia.human_node import HumanNode
from intevia.notification_surface import NotificationSurface
from intevia.queue_surface import QueueSurface


class EvidenceState(str, Enum):
    """Minimal evidence-state vocabulary for v1.0 protection."""

    EMPTY = "empty"
    HOLDING = "holding"
    SEALED = "sealed"


class EvidenceBand(str, Enum):
    """Minimal evidence-band vocabulary for v1.0 protection."""

    DEFAULT = "default"
    FUTURE = "future"


@dataclass(slots=True)
class EvidenceSurface:
    """Minimal governed Evidence Surface skeleton.

    The EvidenceSurface skeleton links a HumanNode, ConsentSurface,
    NotificationSurface, and QueueSurface to non-activating evidence
    posture fields for v1.0 test-case protection.

    It does not interpret, route, review, audit, automate, cadence,
    activate, or enact evidence movement. It does not create audit
    workflows, CARE workflows, review routing, cohort logic, dashboards,
    or runtime surfaces.

    The Human remains the governor. Any future evidence behaviour must be
    separately designed, reviewed, authorised, and evidenced.
    """

    human_node_ref: HumanNode
    consent_surface_ref: ConsentSurface
    notification_surface_ref: NotificationSurface
    queue_surface_ref: QueueSurface

    evidence_state: EvidenceState = EvidenceState.EMPTY
    evidence_band: EvidenceBand = EvidenceBand.DEFAULT
    evidence_flags: dict = field(default_factory=dict)
    evidence_window: datetime | None = None
    evidence_history: list = field(default_factory=list)
    evidence_items: list = field(default_factory=list)


__all__ = [
    "EvidenceBand",
    "EvidenceState",
    "EvidenceSurface",
]
