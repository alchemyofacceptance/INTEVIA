"""Core Activity domain model for INTEVIA v1.0."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
class ActivityValidationError(ValueError):
    """Raised when an Activity cannot be constructed safely."""
class ActivityTimezoneError(ActivityValidationError):
    """Raised when an Activity datetime is missing timezone information."""
def _required_text(value: object, *, field_name: str) -> str:
    """Return stripped required text or raise a bounded validation error."""
    if not isinstance(value, str) or not value.strip():
        raise ActivityValidationError(f"{field_name} is required")
    return value.strip()
def _as_utc(value: object, *, field_name: str) -> datetime:
    """Require a timezone-aware datetime and normalise it to UTC."""
    if not isinstance(value, datetime):
        raise ActivityValidationError(
            f"{field_name} must be a datetime"
        )
    if value.tzinfo is None or value.utcoffset() is None:
        raise ActivityTimezoneError(
            f"{field_name} must be timezone-aware"
        )
    return value.astimezone(timezone.utc)
@dataclass(frozen=True, slots=True)
class Activity:
    """A bounded unit of work with observable completion criteria."""
    activity_id: str
    title: str
    completion_criteria: str
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "activity_id",
            _required_text(
                self.activity_id,
                field_name="activity_id",
            ),
        )
        object.__setattr__(
            self,
            "title",
            _required_text(
                self.title,
                field_name="title",
            ),
        )
        object.__setattr__(
            self,
            "completion_criteria",
            _required_text(
                self.completion_criteria,
                field_name="completion_criteria",
            ),
        )
        object.__setattr__(
            self,
            "created_at",
            _as_utc(
                self.created_at,
                field_name="created_at",
            ),
        )
__all__ = [
    "Activity",
    "ActivityTimezoneError",
    "ActivityValidationError",
]
