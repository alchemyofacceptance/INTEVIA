"""Review Surface Skeleton v1.0.

This module defines the minimal governed review structure required
for v1.0 test-case protection.

Boundary:
    - structural only
    - no behaviour
    - no automation
    - no activation
    - no review interpretation
    - no review routing
    - no review execution
    - no audit workflows
    - no CARE workflows
    - no review cadence
    - no cohort logic
    - no v1.1 surfaces
    - no runtime activation

Manual Human review may occur outside runtime automation.
This skeleton does not activate review logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from intevia.consent_surface import ConsentSurface
from intevia.evidence_surface import EvidenceSurface
from intevia.human_node import HumanNode
from intevia.notification_surface import NotificationSurface
from intevia.queue_surface import QueueSurface


class ReviewState(str, Enum):
    """Minimal review-state vocabulary for v1.0 protection."""

    EMPTY = "empty"
    HOLDING = "holding"
    SEALED = "sealed"


class ReviewBand(str, Enum):
    """Minimal review-band vocabulary for v1.0 protection."""

    DEFAULT = "default"
    FUTURE = "future"


@dataclass(slots=True)
class ReviewSurface:
    """Minimal governed Review Surface skeleton.

    The ReviewSurface skeleton links a HumanNode, ConsentSurface,
    NotificationSurface, QueueSurface, and EvidenceSurface to
    non-activating review posture fields for v1.0 test-case protection.

    It does not interpret, route, execute, automate, cadence, activate,
    or enact review. It does not create audit workflows, CARE workflows,
    cohort logic, dashboards, or runtime surfaces.

    Manual Human review may occur outside runtime automation. This
    skeleton only provides an inspectable structural place where review
    posture can be described.

    The Human remains the governor. Any future review behaviour must be
    separately designed, reviewed, authorised, and evidenced.
    """

    human_node_ref: HumanNode
    consent_surface_ref: ConsentSurface
    notification_surface_ref: NotificationSurface
    queue_surface_ref: QueueSurface
    evidence_surface_ref: EvidenceSurface

    review_state: ReviewState = ReviewState.EMPTY
    review_band: ReviewBand = ReviewBand.DEFAULT
    review_flags: dict = field(default_factory=dict)
    review_window: datetime | None = None
    review_history: list = field(default_factory=list)
    review_items: list = field(default_factory=list)


__all__ = [
    "ReviewBand",
    "ReviewState",
    "ReviewSurface",
]
