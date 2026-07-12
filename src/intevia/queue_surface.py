"""Queue Surface Skeleton v1.0.

This module defines the minimal governed queue structure required
for v1.0 test-case protection.

Boundary:
    - structural only
    - no behaviour
    - no automation
    - no activation
    - no queue processing
    - no queue routing
    - no queue delivery
    - no queue scheduling
    - no queue semantics
    - no notification workflows
    - no CARE workflows
    - no evidence cadence
    - no review routing
    - no cohort logic
    - no v1.1 surfaces
    - no runtime activation
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from src.intevia.consent_surface import ConsentSurface
from src.intevia.human_node import HumanNode
from src.intevia.notification_surface import NotificationSurface


class QueueState(str, Enum):
    """Minimal queue-state vocabulary for v1.0 protection."""

    EMPTY = "empty"
    HOLDING = "holding"
    SEALED = "sealed"


class QueueBand(str, Enum):
    """Minimal queue-band vocabulary for v1.0 protection."""

    DEFAULT = "default"
    FUTURE = "future"


@dataclass(slots=True)
class QueueSurface:
    """Minimal governed Queue Surface skeleton.

    The QueueSurface skeleton links a HumanNode, ConsentSurface, and
    NotificationSurface to non-activating queue posture fields for v1.0
    test-case protection.

    It does not process, route, deliver, schedule, prioritise, automate,
    or enact queued movement. It does not create notification workflows,
    CARE workflows, evidence cadence, review routing, cohort logic,
    dashboards, or runtime surfaces.

    The Human remains the governor. Any future queue behaviour must be
    separately designed, reviewed, authorised, and evidenced.
    """

    human_node_ref: HumanNode
    consent_surface_ref: ConsentSurface
    notification_surface_ref: NotificationSurface

    queue_state: QueueState = QueueState.EMPTY
    queue_band: QueueBand = QueueBand.DEFAULT
    queue_flags: dict = field(default_factory=dict)
    queue_window: datetime | None = None
    queue_history: list = field(default_factory=list)
    queue_items: list = field(default_factory=list)


__all__ = [
    "QueueBand",
    "QueueState",
    "QueueSurface",
]
