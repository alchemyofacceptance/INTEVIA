"""Thin application service for Contribution orchestration (Unit 2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional

from src.intevia.core.activity import Activity
from src.intevia.core.contribution import (
    Contribution,
    DecisionOutcome,
    HumanDecision,
    TransitionRecord,
)
from src.intevia.observation.journal import (
    ObservationEntry,
    ObservationEventKind,
    ObservationJournal,
)


class ContributionServiceError(Exception):
    """Base service error for orchestration failures."""


class ActivityNotFound(ContributionServiceError):
    """Raised when an Activity id is unknown to the service."""


class ContributionNotFound(ContributionServiceError):
    """Raised when a Contribution id is unknown to the service."""


class DuplicateActivity(ContributionServiceError):
    """Raised when attempting to create an Activity with an existing id."""


class DuplicateContribution(ContributionServiceError):
    """Raised when attempting to create a Contribution with an existing id."""


class ObservationEmissionError(ContributionServiceError):
    """Raised after a governed operation succeeds but observation fails."""

    def __init__(
        self,
        *,
        operation_result: object,
        event_kind: ObservationEventKind,
    ) -> None:
        self.operation_result = operation_result
        self.event_kind = event_kind
        super().__init__(
            "Governed operation completed successfully, but observation "
            "emission failed; automatic retry may be unsafe."
        )


@dataclass(eq=False)
class ContributionService:
    """In-memory orchestration surface for Activities and Contributions.

    Responsibilities:
    - Maintain internal, instance-isolated registries; privacy is conventional rather than enforced.
    - Delegate all domain rules to Unit 1 domain objects.
    - Enforce orchestration concerns: canonical keys, duplicate ids, lookup, decision-input mode.
    - Optionally emit current-process operational observations after successful operations.

    The service does not claim exclusive access to domain objects; direct domain use remains
    subject to all Unit 1 invariants.
    """

    observation_journal: Optional[ObservationJournal] = field(
        default=None,
        repr=False,
    )
    _activities: Dict[str, Activity] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _contributions: Dict[str, Contribution] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if (
            self.observation_journal is not None
            and not isinstance(self.observation_journal, ObservationJournal)
        ):
            raise ContributionServiceError(
                "observation_journal must be an ObservationJournal or None"
            )

    @staticmethod
    def _canonical_id(value: str) -> str:
        if value is None:
            raise ContributionServiceError("id cannot be None")
        if not isinstance(value, str):
            raise ContributionServiceError("id must be a string")
        canonical = value.strip()
        if not canonical:
            raise ContributionServiceError("id cannot be empty or whitespace")
        return canonical

    def _emit_observation(
        self,
        *,
        entry: ObservationEntry,
        operation_result: object,
        event_kind: ObservationEventKind,
    ) -> None:
        if self.observation_journal is None:
            return

        try:
            self.observation_journal.append(entry)
        except Exception as exc:
            raise ObservationEmissionError(
                operation_result=operation_result,
                event_kind=event_kind,
            ) from exc

    def create_activity(
        self,
        *,
        activity_id: str,
        title: str,
        completion_criteria: str,
        created_at: Optional[datetime] = None,
    ) -> Activity:
        activity_id = self._canonical_id(activity_id)
        if activity_id in self._activities:
            raise DuplicateActivity(f"activity {activity_id} already exists")

        if created_at is None:
            activity = Activity(
                activity_id=activity_id,
                title=title,
                completion_criteria=completion_criteria,
            )
        else:
            activity = Activity(
                activity_id=activity_id,
                title=title,
                completion_criteria=completion_criteria,
                created_at=created_at,
            )

        self._activities[activity_id] = activity

        if self.observation_journal is not None:
            event_kind = ObservationEventKind.ACTIVITY_CREATED
            try:
                entry = ObservationEntry(
                    event_kind=event_kind,
                    activity_id=activity.activity_id,
                    occurred_at=activity.created_at,
                )
                self._emit_observation(
                    entry=entry,
                    operation_result=activity,
                    event_kind=event_kind,
                )
            except ObservationEmissionError:
                raise
            except Exception as exc:
                raise ObservationEmissionError(
                    operation_result=activity,
                    event_kind=event_kind,
                ) from exc

        return activity

    def get_activity(self, activity_id: str) -> Activity:
        activity_id = self._canonical_id(activity_id)
        try:
            return self._activities[activity_id]
        except KeyError:
            raise ActivityNotFound(f"activity {activity_id} not found")

    def create_contribution(
        self,
        *,
        contribution_id: str,
        activity_id: str,
        contributor_ref: str,
        content: str,
        created_at: Optional[datetime] = None,
    ) -> Contribution:
        contribution_id = self._canonical_id(contribution_id)
        activity_id = self._canonical_id(activity_id)

        if contribution_id in self._contributions:
            raise DuplicateContribution(
                f"contribution {contribution_id} already exists"
            )

        if activity_id not in self._activities:
            raise ActivityNotFound(f"activity {activity_id} not found")

        activity = self._activities[activity_id]

        if created_at is None:
            contribution = Contribution(
                contribution_id=contribution_id,
                activity_ref=activity,
                contributor_ref=contributor_ref,
                content=content,
            )
        else:
            contribution = Contribution(
                contribution_id=contribution_id,
                activity_ref=activity,
                contributor_ref=contributor_ref,
                content=content,
                created_at=created_at,
            )

        self._contributions[contribution_id] = contribution

        if self.observation_journal is not None:
            event_kind = ObservationEventKind.CONTRIBUTION_CREATED
            try:
                entry = ObservationEntry(
                    event_kind=event_kind,
                    activity_id=contribution.activity_ref.activity_id,
                    contribution_id=contribution.contribution_id,
                    actor_ref=contribution.contributor_ref,
                    occurred_at=contribution.created_at,
                )
                self._emit_observation(
                    entry=entry,
                    operation_result=contribution,
                    event_kind=event_kind,
                )
            except ObservationEmissionError:
                raise
            except Exception as exc:
                raise ObservationEmissionError(
                    operation_result=contribution,
                    event_kind=event_kind,
                ) from exc

        return contribution

    def get_contribution(self, contribution_id: str) -> Contribution:
        contribution_id = self._canonical_id(contribution_id)
        try:
            return self._contributions[contribution_id]
        except KeyError:
            raise ContributionNotFound(
                f"contribution {contribution_id} not found"
            )

    def submit_contribution(
        self,
        *,
        contribution_id: str,
        submitted_at: Optional[datetime] = None,
    ) -> TransitionRecord:
        canonical_contribution_id = self._canonical_id(contribution_id)
        contribution = self.get_contribution(canonical_contribution_id)

        if submitted_at is None:
            transition = contribution.submit()
        else:
            transition = contribution.submit(submitted_at=submitted_at)

        if self.observation_journal is not None:
            event_kind = ObservationEventKind.CONTRIBUTION_SUBMITTED
            try:
                entry = ObservationEntry(
                    event_kind=event_kind,
                    activity_id=contribution.activity_ref.activity_id,
                    contribution_id=contribution.contribution_id,
                    actor_ref=transition.actor_ref,
                    prior_state=transition.prior_state.value,
                    new_state=transition.new_state.value,
                    occurred_at=transition.timestamp,
                )
                self._emit_observation(
                    entry=entry,
                    operation_result=transition,
                    event_kind=event_kind,
                )
            except ObservationEmissionError:
                raise
            except Exception as exc:
                raise ObservationEmissionError(
                    operation_result=transition,
                    event_kind=event_kind,
                ) from exc

        return transition

    def record_human_decision(
        self,
        *,
        contribution_id: str,
        decision: Optional[HumanDecision] = None,
        human_actor_ref: Optional[str] = None,
        outcome: Optional[DecisionOutcome] = None,
        reason: Optional[str] = None,
        decided_at: Optional[datetime] = None,
    ) -> TransitionRecord:
        """Accept either a pre-built HumanDecision OR primitive fields — never both."""

        canonical_contribution_id = self._canonical_id(contribution_id)
        contribution = self.get_contribution(canonical_contribution_id)

        primitives_provided = any(
            [
                human_actor_ref is not None,
                outcome is not None,
                reason is not None,
                decided_at is not None,
            ]
        )
        if decision is not None and primitives_provided:
            raise ContributionServiceError(
                "provide either a pre-built decision or primitive fields, not both"
            )

        if decision is None:
            if human_actor_ref is None or outcome is None:
                raise ContributionServiceError(
                    "either decision or (human_actor_ref and outcome) must be provided"
                )

            if decided_at is None:
                effective_decision = HumanDecision(
                    human_actor_ref=human_actor_ref,
                    contribution_ref=canonical_contribution_id,
                    outcome=outcome,
                    reason=reason,
                )
            else:
                effective_decision = HumanDecision(
                    human_actor_ref=human_actor_ref,
                    contribution_ref=canonical_contribution_id,
                    outcome=outcome,
                    reason=reason,
                    decided_at=decided_at,
                )
        else:
            effective_decision = decision

        transition = contribution.record_human_decision(effective_decision)

        if self.observation_journal is not None:
            event_kind = ObservationEventKind.HUMAN_DECISION_RECORDED
            try:
                entry = ObservationEntry(
                    event_kind=event_kind,
                    activity_id=contribution.activity_ref.activity_id,
                    contribution_id=contribution.contribution_id,
                    actor_ref=transition.actor_ref,
                    outcome=effective_decision.outcome.value,
                    prior_state=transition.prior_state.value,
                    new_state=transition.new_state.value,
                    occurred_at=transition.timestamp,
                )
                self._emit_observation(
                    entry=entry,
                    operation_result=transition,
                    event_kind=event_kind,
                )
            except ObservationEmissionError:
                raise
            except Exception as exc:
                raise ObservationEmissionError(
                    operation_result=transition,
                    event_kind=event_kind,
                ) from exc

        return transition
