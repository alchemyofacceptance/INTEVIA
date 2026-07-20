from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class ObservationValidationError(Exception):
    """Base validation error for observation primitives."""


class InvalidObservationEntry(ObservationValidationError):
    """Raised when an observation entry or journal append is invalid."""


class ObservationEventKind(str, Enum):
    ACTIVITY_CREATED = "activity_created"
    CONTRIBUTION_CREATED = "contribution_created"
    CONTRIBUTION_SUBMITTED = "contribution_submitted"
    HUMAN_DECISION_RECORDED = "human_decision_recorded"
    CONTRIBUTION_TRANSITION_RECORDED = "contribution_transition_recorded"
    EVENT_CREATED = "event_created"
    EVENT_TRANSITION_RECORDED = "event_transition_recorded"


_ALLOWED_STATES = frozenset(
    {
        "draft",
        "submitted",
        "under_review",
        "accepted",
        "rejected",
        "correction_requested",
        "correction_pending_review",
        "withdrawn",
        "archived",
        "current",
        "superseded",
        "restricted",
        "erased_content",
        "published",
        "active",
        "completed",
    }
)
_ALLOWED_OUTCOMES = frozenset({"accepted", "rejected"})


@dataclass(frozen=True, slots=True)
class ObservationEntry:
    event_kind: ObservationEventKind
    occurred_at: datetime
    activity_id: str | None = None
    contribution_id: str | None = None
    event_id: str | None = None
    actor_ref: str | None = None
    outcome: str | None = None
    prior_state: str | None = None
    new_state: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.event_kind, ObservationEventKind):
            raise InvalidObservationEntry(
                "event_kind must be an ObservationEventKind"
            )

        if not isinstance(self.occurred_at, datetime):
            raise InvalidObservationEntry("occurred_at must be a datetime")

        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise InvalidObservationEntry("occurred_at must be timezone-aware")

        object.__setattr__(
            self,
            "occurred_at",
            self.occurred_at.astimezone(timezone.utc),
        )

        if self.event_kind is ObservationEventKind.ACTIVITY_CREATED:
            required_fields = {"activity_id"}
            irrelevant_fields = {
                "contribution_id",
                "event_id",
                "actor_ref",
                "outcome",
                "prior_state",
                "new_state",
            }
        elif self.event_kind is ObservationEventKind.CONTRIBUTION_CREATED:
            required_fields = {
                "activity_id",
                "contribution_id",
                "actor_ref",
            }
            irrelevant_fields = {"event_id", "outcome", "prior_state", "new_state"}
        elif self.event_kind is ObservationEventKind.CONTRIBUTION_SUBMITTED:
            required_fields = {
                "activity_id",
                "contribution_id",
                "actor_ref",
                "prior_state",
                "new_state",
            }
            irrelevant_fields = {"event_id", "outcome"}
        elif self.event_kind is ObservationEventKind.HUMAN_DECISION_RECORDED:
            required_fields = {
                "activity_id",
                "contribution_id",
                "actor_ref",
                "outcome",
                "prior_state",
                "new_state",
            }
            irrelevant_fields = {"event_id"}
        elif (
            self.event_kind
            is ObservationEventKind.CONTRIBUTION_TRANSITION_RECORDED
        ):
            required_fields = {
                "contribution_id",
                "actor_ref",
                "prior_state",
                "new_state",
            }
            irrelevant_fields = {"activity_id", "event_id", "outcome"}
        elif self.event_kind is ObservationEventKind.EVENT_CREATED:
            required_fields = {"event_id", "actor_ref", "new_state"}
            irrelevant_fields = {
                "activity_id",
                "contribution_id",
                "outcome",
                "prior_state",
            }
        elif self.event_kind is ObservationEventKind.EVENT_TRANSITION_RECORDED:
            required_fields = {
                "event_id",
                "actor_ref",
                "prior_state",
                "new_state",
            }
            irrelevant_fields = {"activity_id", "contribution_id", "outcome"}
        else:
            raise InvalidObservationEntry("unhandled observation event kind")

        for field_name in required_fields:
            value = getattr(self, field_name)

            if field_name in {
                "activity_id",
                "contribution_id",
                "event_id",
                "actor_ref",
            }:
                if type(value) is not str or not value.strip():
                    raise InvalidObservationEntry(
                        f"{field_name} must be a non-blank plain string"
                    )
                object.__setattr__(self, field_name, value.strip())
            elif value is None:
                raise InvalidObservationEntry(f"{field_name} is required")

        for field_name in irrelevant_fields:
            if getattr(self, field_name) is not None:
                raise InvalidObservationEntry(
                    f"{field_name} must be None for {self.event_kind.value}"
                )

        for field_name in ("prior_state", "new_state"):
            value = getattr(self, field_name)
            if value is not None:
                if type(value) is not str:
                    raise InvalidObservationEntry(
                        f"{field_name} must be a plain string"
                    )
                if value not in _ALLOWED_STATES:
                    raise InvalidObservationEntry(
                        f"{field_name} must be a canonical observation state"
                    )

        if self.outcome is not None:
            if type(self.outcome) is not str:
                raise InvalidObservationEntry("outcome must be a plain string")
            if self.outcome not in _ALLOWED_OUTCOMES:
                raise InvalidObservationEntry(
                    "outcome must be a canonical decision outcome"
                )


class ObservationJournal:
    def __init__(self) -> None:
        self._entries: list[ObservationEntry] = []

    def append(self, entry: ObservationEntry) -> None:
        if not isinstance(entry, ObservationEntry):
            raise InvalidObservationEntry(
                "journal entries must be ObservationEntry instances"
            )
        self._entries.append(entry)

    def list_entries(self) -> tuple[ObservationEntry, ...]:
        return tuple(self._entries)
