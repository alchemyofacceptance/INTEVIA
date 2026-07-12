"""Notification Surface Skeleton v1.0.

This module defines the minimal governed notification structure required
for v1.0 test-case protection.

Boundary:
    - structural only
    - no behaviour
    - no automation
    - no activation
    - no notification workflows
    - no notification interpretation
    - no delivery systems
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

from src.intevia.consent_surface import ConsentSurface
from src.intevia.human_node import HumanNode
from src.intevia.relationship_surface import NotificationChannel


@dataclass(slots=True)
class NotificationSurface:
    """Minimal governed Notification Surface skeleton.

    The NotificationSurface skeleton links a HumanNode and ConsentSurface
    to non-activating notification posture fields for v1.0 test-case
    protection.

    It does not activate, interpret, deliver, schedule, automate, route,
    or enact notifications. It does not create notification workflows,
    delivery systems, CARE workflows, evidence cadence, queue logic,
    review routing, cohort logic, dashboards, or runtime surfaces.

    The Human remains the governor. Any future notification behaviour
    must be separately designed, reviewed, authorised, and evidenced.
    """

    human_node_ref: HumanNode
    consent_surface_ref: ConsentSurface
    notification_channel: NotificationChannel = NotificationChannel.NONE
    notification_flags: dict = field(default_factory=dict)
    notification_window: datetime | None = None
    notification_history: list = field(default_factory=list)


__all__ = [
    "NotificationSurface",
]
