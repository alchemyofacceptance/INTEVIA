"""Human Node Skeleton v1.0.

This module defines the minimal governed Human Node structure required
for v1.0 test-case protection.

Boundary:
    - structural only
    - no behaviour
    - no automation
    - no activation
    - no CARE workflows
    - no notification systems
    - no evidence cadence
    - no queue logic
    - no review routing
    - no cohort logic
    - no v1.1 surfaces
    - no runtime activation
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.intevia.identity_profile import IdentityProfile
from src.intevia.relationship_surface import (
    CareChannel,
    ConsentState,
    EvidenceChannel,
    NotificationChannel,
    QueueChannel,
    RelationshipSurface,
    ReviewChannel,
)


@dataclass(slots=True)
class HumanNode:
    """Minimal governed Human Node skeleton.

    The HumanNode skeleton unifies an IdentityProfile and a
    RelationshipSurface into a single inspectable structure for v1.0
    test-case protection.

    It does not activate Human Node governance, behaviour, automation,
    CARE workflows, notifications, evidence cadence, queue logic,
    review routing, cohort logic, dashboards, or runtime surfaces.

    The Human remains the governor. Any future runtime behaviour must be
    separately designed, reviewed, authorised, and evidenced.
    """

    identity_ref: IdentityProfile
    relationship_ref: RelationshipSurface

    consent_state: ConsentState = ConsentState.UNKNOWN
    notification_channel: NotificationChannel = NotificationChannel.NONE
    care_channel: CareChannel = CareChannel.NONE
    evidence_channel: EvidenceChannel = EvidenceChannel.NONE
    queue_channel: QueueChannel = QueueChannel.NONE
    review_channel: ReviewChannel = ReviewChannel.NONE

    governance_flags: dict = field(default_factory=dict)


__all__ = [
    "HumanNode",
]
