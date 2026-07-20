"""Governed Contribution lifecycle for INTEVIA v1.0."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
import unicodedata
from enum import Enum
from types import MappingProxyType
from typing import Final, Mapping
from src.intevia.core.activity import Activity
class ContributionDomainError(ValueError):
    """Base error for bounded Contribution domain failures."""
class ContributionValidationError(ContributionDomainError):
    """Raised when Contribution data is incomplete or invalid."""
class InvalidContributionTransition(ContributionDomainError):
    """Raised when a requested state transition is not permitted."""
class DecisionReasonRequired(ContributionDomainError):
    """Raised when a Human decision requires an explanatory reason."""
class TimezoneRequiredError(ContributionDomainError):
    """Raised when a domain datetime is not timezone-aware."""
class TransitionChronologyError(ContributionDomainError):
    """Raised when a transition would move domain time backwards."""
class SelfReviewNotPermitted(ContributionDomainError):
    """Raised when a contributor attempts to decide their own work."""
class ContributionState(str, Enum):
    """States supported by the first governed Contribution slice."""
    DRAFT = "draft"
    SUBMITTED = "submitted"
    UNDER_REVIEW = "under_review"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CORRECTION_REQUESTED = "correction_requested"
    CORRECTION_PENDING_REVIEW = "correction_pending_review"
    WITHDRAWN = "withdrawn"
    ARCHIVED = "archived"


class ContributionVersionState(str, Enum):
    """Privacy-aware states of one immutable Contribution version."""

    CURRENT = "current"
    SUPERSEDED = "superseded"
    RESTRICTED = "restricted"
    ERASED_CONTENT = "erased_content"
class DecisionOutcome(str, Enum):
    """Human decision outcomes supported by Slice 1."""
    ACCEPTED = "accepted"
    REJECTED = "rejected"
class TransitionType(str, Enum):
    """Transition classifications recorded by the domain."""
    SUBMISSION = "submission"
    REVIEW = "review"
    HUMAN_DECISION = "human_decision"
OUTCOME_TO_STATE: Final[Mapping[DecisionOutcome, ContributionState]] = (
    MappingProxyType(
        {
            DecisionOutcome.ACCEPTED: ContributionState.ACCEPTED,
            DecisionOutcome.REJECTED: ContributionState.REJECTED,
        }
    )
)
def _required_text(value: object, *, field_name: str) -> str:
    """Return stripped required text or raise a validation error."""
    if not isinstance(value, str) or not value.strip():
        raise ContributionValidationError(
            f"{field_name} is required"
        )
    return value.strip()
def _human_ref(value: object, *, field_name: str) -> str:
    """Validate a temporary Unit 1 Human attribution reference.
    The ``human:<identifier>`` syntax provides traceable attribution only.
    It does not establish authentication, role eligibility, or complete
    authority validation.
    """
    normalized = unicodedata.normalize(
        "NFKC",
        _required_text(value, field_name=field_name),
    ).casefold()
    prefix, separator, identifier = normalized.partition(":")
    identifier = identifier.strip()
    if (
        separator != ":"
        or prefix != "human"
        or not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", identifier)
    ):
        raise ContributionValidationError(
            f"{field_name} must use human:<ascii-identifier>"
        )
    return f"human:{identifier}"
def _as_utc(value: object, *, field_name: str) -> datetime:
    """Require a timezone-aware datetime and normalise it to UTC."""
    if not isinstance(value, datetime):
        raise ContributionValidationError(
            f"{field_name} must be a datetime"
        )
    if value.tzinfo is None or value.utcoffset() is None:
        raise TimezoneRequiredError(
            f"{field_name} must be timezone-aware"
        )
    return value.astimezone(timezone.utc)
def _optional_reason(value: object) -> str | None:
    """Normalise an optional decision reason."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ContributionValidationError(
            "reason must be text when provided"
        )
    normalized = value.strip()
    return normalized or None
@dataclass(frozen=True, slots=True)
class TransitionRecord:
    """Immutable evidence of one successful state transition."""
    prior_state: ContributionState
    new_state: ContributionState
    actor_ref: str
    timestamp: datetime
    transition_type: TransitionType
    reason: str | None = None
    _PERMITTED: Final = frozenset(
        {
            (
                ContributionState.DRAFT,
                ContributionState.SUBMITTED,
                TransitionType.SUBMISSION,
            ),
            (
                ContributionState.SUBMITTED,
                ContributionState.UNDER_REVIEW,
                TransitionType.REVIEW,
            ),
            (
                ContributionState.UNDER_REVIEW,
                ContributionState.ACCEPTED,
                TransitionType.HUMAN_DECISION,
            ),
            (
                ContributionState.UNDER_REVIEW,
                ContributionState.REJECTED,
                TransitionType.HUMAN_DECISION,
            ),
        }
    )
    def __post_init__(self) -> None:
        if not isinstance(self.prior_state, ContributionState):
            raise ContributionValidationError(
                "prior_state must be a ContributionState"
            )
        if not isinstance(self.new_state, ContributionState):
            raise ContributionValidationError(
                "new_state must be a ContributionState"
            )
        if not isinstance(self.transition_type, TransitionType):
            raise ContributionValidationError(
                "transition_type must be a TransitionType"
            )
        if (
            self.prior_state,
            self.new_state,
            self.transition_type,
        ) not in self._PERMITTED:
            raise InvalidContributionTransition(
                f"{self.prior_state.value} -> {self.new_state.value} "
                f"({self.transition_type.value}) is not a permitted transition"
            )
        if (
            self.new_state is ContributionState.REJECTED
            and not _optional_reason(self.reason)
        ):
            raise DecisionReasonRequired(
                f"{self.new_state.value} records require a reason"
            )
        object.__setattr__(
            self,
            "actor_ref",
            _human_ref(
                self.actor_ref,
                field_name="actor_ref",
            ),
        )
        object.__setattr__(
            self,
            "timestamp",
            _as_utc(
                self.timestamp,
                field_name="timestamp",
            ),
        )
        object.__setattr__(
            self,
            "reason",
            _optional_reason(self.reason),
        )
@dataclass(frozen=True, slots=True)
class HumanDecision:
    """An explicit Human-attributed decision concerning a Contribution.
    Unit 1 validates traceable Human attribution and contributor/reviewer
    separation. Full authentication, role eligibility, and authorisation
    remain deferred.
    """
    human_actor_ref: str
    contribution_ref: str
    outcome: DecisionOutcome
    reason: str | None = None
    decided_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "human_actor_ref",
            _human_ref(
                self.human_actor_ref,
                field_name="human_actor_ref",
            ),
        )
        object.__setattr__(
            self,
            "contribution_ref",
            _required_text(
                self.contribution_ref,
                field_name="contribution_ref",
            ),
        )
        if not isinstance(self.outcome, DecisionOutcome):
            raise ContributionValidationError(
                "outcome must be a DecisionOutcome"
            )
        normalized_reason = _optional_reason(self.reason)
        object.__setattr__(
            self,
            "reason",
            normalized_reason,
        )
        object.__setattr__(
            self,
            "decided_at",
            _as_utc(
                self.decided_at,
                field_name="decided_at",
            ),
        )
        if (
            self.outcome is DecisionOutcome.REJECTED
            and not normalized_reason
        ):
            raise DecisionReasonRequired(
                f"{self.outcome.value} decisions require a reason"
            )
@dataclass(frozen=True, eq=False, slots=True)
class Contribution:
    """A governed contribution submitted against an Activity."""
    contribution_id: str
    activity_ref: Activity
    contributor_ref: str
    content: str
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    state: ContributionState = field(
        default=ContributionState.DRAFT,
        init=False,
    )
    _transition_history: tuple[TransitionRecord, ...] = field(
        default_factory=tuple,
        init=False,
        repr=False,
    )
    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "contribution_id",
            _required_text(
                self.contribution_id,
                field_name="contribution_id",
            ),
        )
        if not isinstance(self.activity_ref, Activity):
            raise ContributionValidationError(
                "activity_ref must be an Activity"
            )
        object.__setattr__(
            self,
            "contributor_ref",
            _human_ref(
                self.contributor_ref,
                field_name="contributor_ref",
            ),
        )
        object.__setattr__(
            self,
            "content",
            _required_text(
                self.content,
                field_name="content",
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
        if self.created_at < self.activity_ref.created_at:
            raise TransitionChronologyError(
                "Contribution creation cannot precede Activity creation"
            )
    @property
    def transition_history(self) -> tuple[TransitionRecord, ...]:
        """Return the append-only transition history (records immutable)."""
        return self._transition_history
    @property
    def is_terminal(self) -> bool:
        """Report whether the Contribution has reached a terminal state."""
        return self.state in {
            ContributionState.ACCEPTED,
            ContributionState.REJECTED,
        }
    def _latest_domain_time(self) -> datetime:
        """Return the latest recorded datetime for chronology checks."""
        if self._transition_history:
            return self._transition_history[-1].timestamp
        return self.created_at
    def _require_non_regressing_time(
        self,
        value: object,
        *,
        field_name: str,
    ) -> datetime:
        """Normalise time and refuse chronology regression."""
        normalized = _as_utc(
            value,
            field_name=field_name,
        )
        if normalized < self._latest_domain_time():
            raise TransitionChronologyError(
                f"{field_name} cannot precede the latest domain timestamp"
            )
        return normalized
    def submit(
        self,
        *,
        submitted_at: datetime | None = None,
    ) -> TransitionRecord:
        """Submit a draft Contribution for Human review."""
        if self.state is not ContributionState.DRAFT:
            raise InvalidContributionTransition(
                f"cannot submit Contribution from {self.state.value}"
            )
        if submitted_at is None:
            submitted_at = datetime.now(timezone.utc)
        effective_time = self._require_non_regressing_time(
            submitted_at,
            field_name="submitted_at",
        )
        transition = TransitionRecord(
            prior_state=self.state,
            new_state=ContributionState.SUBMITTED,
            actor_ref=self.contributor_ref,
            timestamp=effective_time,
            transition_type=TransitionType.SUBMISSION,
        )
        object.__setattr__(self, "state", ContributionState.SUBMITTED)
        object.__setattr__(
            self,
            "_transition_history",
            self._transition_history + (transition,),
        )
        return transition
    def begin_review(
        self,
        *,
        reviewer_ref: str,
        reviewed_at: datetime | None = None,
    ) -> TransitionRecord:
        """Record that an attributed Human reviewer began review."""
        if self.state is not ContributionState.SUBMITTED:
            raise InvalidContributionTransition(
                f"cannot begin review from {self.state.value}"
            )
        if reviewed_at is None:
            reviewed_at = datetime.now(timezone.utc)
        effective_time = self._require_non_regressing_time(
            reviewed_at,
            field_name="reviewed_at",
        )
        transition = TransitionRecord(
            prior_state=self.state,
            new_state=ContributionState.UNDER_REVIEW,
            actor_ref=reviewer_ref,
            timestamp=effective_time,
            transition_type=TransitionType.REVIEW,
        )
        object.__setattr__(self, "state", ContributionState.UNDER_REVIEW)
        object.__setattr__(
            self,
            "_transition_history",
            self._transition_history + (transition,),
        )
        return transition
    def record_human_decision(
        self,
        decision: HumanDecision,
    ) -> TransitionRecord:
        """Record an explicit Human decision after review has begun."""
        if not isinstance(decision, HumanDecision):
            raise ContributionValidationError(
                "an explicit HumanDecision is required"
            )
        if self.state is not ContributionState.UNDER_REVIEW:
            raise InvalidContributionTransition(
                f"cannot record a Human decision from {self.state.value}"
            )
        if decision.contribution_ref != self.contribution_id:
            raise ContributionValidationError(
                "decision does not reference this Contribution"
            )
        if decision.human_actor_ref == self.contributor_ref:
            raise SelfReviewNotPermitted(
                "a contributor cannot decide their own Contribution"
            )
        effective_time = self._require_non_regressing_time(
            decision.decided_at,
            field_name="decided_at",
        )
        new_state = OUTCOME_TO_STATE[decision.outcome]
        transition = TransitionRecord(
            prior_state=self.state,
            new_state=new_state,
            actor_ref=decision.human_actor_ref,
            timestamp=effective_time,
            transition_type=TransitionType.HUMAN_DECISION,
            reason=decision.reason,
        )
        object.__setattr__(self, "state", new_state)
        object.__setattr__(
            self,
            "_transition_history",
            self._transition_history + (transition,),
        )
        return transition
__all__ = [
    "Contribution",
    "ContributionDomainError",
    "ContributionState",
    "ContributionVersionState",
    "ContributionValidationError",
    "DecisionOutcome",
    "DecisionReasonRequired",
    "HumanDecision",
    "InvalidContributionTransition",
    "OUTCOME_TO_STATE",
    "SelfReviewNotPermitted",
    "TimezoneRequiredError",
    "TransitionChronologyError",
    "TransitionRecord",
    "TransitionType",
]
