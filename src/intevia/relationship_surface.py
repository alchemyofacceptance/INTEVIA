"""Relationship Surface Skeleton v1.0.

This module defines the minimal governed relationship structure required
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

from dataclasses import dataclass
from enum import Enum

from src.intevia.identity_profile import IdentityProfile


class RelationshipState(str, Enum):
    """Minimal relationship-state vocabulary for v1.0 protection."""

    UNASSIGNED = "unassigned"
    OBSERVED = "observed"
    ACTIVE_REVIEW = "active_review"
    PAUSED = "paused"
    EXITED = "exited"


class ConsentState(str, Enum):
    """Minimal consent-state vocabulary."""

    UNKNOWN = "unknown"
    NOT_REQUESTED = "not_requested"
    REQUESTED = "requested"
    GRANTED = "granted"
    WITHDRAWN = "withdrawn"


class NotificationChannel(str, Enum):
    """Non-activating notification-channel posture."""

    NONE = "none"
    EMAIL = "email"
    MANUAL = "manual"


class CareChannel(str, Enum):
    """Non-activating CARE-channel posture."""

    NONE = "none"
    PLACEHOLDER = "placeholder"
    MANUAL_REVIEW = "manual_review"


class EvidenceChannel(str, Enum):
    """Non-activating evidence-channel posture."""

    NONE = "none"
    MANUAL_CAPTURE = "manual_capture"
    REVIEW_PACKET = "review_packet"


class QueueChannel(str, Enum):
    """Non-activating queue-channel posture."""

    NONE = "none"
    OBSERVATION = "observation"
    PARKED = "parked"


class ReviewChannel(str, Enum):
    """Non-activating review-channel posture."""

    NONE = "none"
    HUMAN_REVIEW = "human_review"
    GOVERNANCE_REVIEW = "governance_review"


@dataclass(slots=True)
class RelationshipSurface:
    """Minimal governed relationship surface skeleton.

    The RelationshipSurface skeleton does not activate relationship
    governance, behaviour, automation, CARE workflows, notifications,
    evidence cadence, queue logic, review routing, cohort logic, or
    runtime surfaces.

    It provides a bounded, inspectable structure for v1.0 test-case
    relationship handling.

    The Human remains the governor. Any future runtime behaviour must be
    separately designed, reviewed, authorised, and evidenced.
    """

    identity_ref: IdentityProfile
    relationship_state: RelationshipState = RelationshipState.UNASSIGNED
    consent_state: ConsentState = ConsentState.UNKNOWN

    notification_channel: NotificationChannel = NotificationChannel.NONE
    care_channel: CareChannel = CareChannel.NONE
    evidence_channel: EvidenceChannel = EvidenceChannel.NONE
    queue_channel: QueueChannel = QueueChannel.NONE
    review_channel: ReviewChannel = ReviewChannel.NONE


__all__ = [
    "CareChannel",
    "ConsentState",
    "EvidenceChannel",
    "NotificationChannel",
    "QueueChannel",
    "RelationshipState",
    "RelationshipSurface",
    "ReviewChannel",
]
