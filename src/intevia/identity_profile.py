"""Identity Profile Skeleton v1.0.

This module defines the minimal governed identity structure required for
v1.0 test-case protection.

It does not activate runtime, cohort logic, notification workflows, CARE
workflows, dashboards, or v1.1 surfaces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class CohortState(Enum):
    """Bounded v1.0 cohort-state markers."""

    NOT_ACTIVE = "not_active"
    STRUCTURAL_PLACEHOLDER = "structural_placeholder"
    REVIEW_HELD = "review_held"
    PAUSED = "paused"
    EXITED = "exited"


class QueueBand(Enum):
    """Bounded v1.0 queue-band markers."""

    NOT_QUEUED = "not_queued"
    STRUCTURAL_PLACEHOLDER = "structural_placeholder"
    LOW_TOUCH = "low_touch"
    REVIEW_HELD = "review_held"
    PAUSED = "paused"
    EXITED = "exited"


class CadenceBand(Enum):
    """Bounded v1.0 notification-preference cadence markers."""

    NOT_SET = "not_set"
    NO_CONTACT = "no_contact"
    LOW = "low"
    STANDARD = "standard"
    PAUSED = "paused"


class PublicationBoundary(Enum):
    """Bounded v1.0 publication-boundary markers."""

    PRIVATE = "private"
    INTERNAL_GOVERNANCE = "internal_governance"
    PUBLIC_ANONYMIZED = "public_anonymized"
    PUBLIC_NAMED_WITH_CONSENT = "public_named_with_consent"
    NOT_PUBLICATION_AUTHORISED = "not_publication_authorised"


@dataclass(slots=True)
class IdentityProfile:
    """Minimal governed identity profile structure for v1.0."""

    human_identity_anchor: str
    cohort_state: CohortState
    queue_band: QueueBand
    evidence_consent: bool
    cadence_band: CadenceBand
    publication_boundary: PublicationBoundary
    email_opt_in: bool
    email_last_sent: datetime | None
    email_next_window: datetime | None
    pause_state: bool
    exit_state: bool
    care_touchpoint_placeholder: dict[str, Any] = field(default_factory=dict)
    review_packet_linkage: dict[str, Any] = field(default_factory=dict)
    governance_flags: dict[str, Any] = field(default_factory=dict)
