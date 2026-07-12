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


@dataclass(eq=False)
class ContributionService:
    """In-memory orchestration surface for Activities and Contributions.

    Responsibilities:
    - Maintain internal, instance-isolated registries; privacy is conventional rather than enforced.
    - Delegate all domain rules to Unit 1 domain objects.
    - Enforce orchestration concerns: canonical keys, duplicate ids, lookup, decision-input mode.

    The service does not claim exclusive access to domain objects; direct domain use remains
    subject to all Unit 1 invariants.
    """

    _activities: Dict[str, Activity] = field(default_factory=dict, init=False, repr=False)
    _contributions: Dict[str, Contribution] = field(default_factory=dict, init=False, repr=False)

    # --- helpers ---------------------------------------------------------

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

    # --- activity lifecycle ---------------------------------------------

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

        # Only treat explicit None as "no timestamp provided"; do not use truthiness.
        if created_at is None:
            activity = Activity(
                activity_id=activity_id,
                title=title,
                completion_criteria=completion_criteria,
            )
        else:
            # Pass supplied datetime through to domain; domain enforces tz and chronology.
            activity = Activity(
                activity_id=activity_id,
                title=title,
                completion_criteria=completion_criteria,
                created_at=created_at,
            )

        self._activities[activity_id] = activity
        return activity

    def get_activity(self, activity_id: str) -> Activity:
        activity_id = self._canonical_id(activity_id)
        try:
            return self._activities[activity_id]
        except KeyError:
            raise ActivityNotFound(f"activity {activity_id} not found")

    # --- contribution lifecycle -----------------------------------------

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
            raise DuplicateContribution(f"contribution {contribution_id} already exists")

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
        return contribution

    def get_contribution(self, contribution_id: str) -> Contribution:
        contribution_id = self._canonical_id(contribution_id)
        try:
            return self._contributions[contribution_id]
        except KeyError:
            raise ContributionNotFound(f"contribution {contribution_id} not found")

    # --- submission and decision orchestration --------------------------

    def submit_contribution(
        self,
        *,
        contribution_id: str,
        submitted_at: Optional[datetime] = None,
    ) -> TransitionRecord:
        canonical_contribution_id = self._canonical_id(contribution_id)
        contribution = self.get_contribution(canonical_contribution_id)

        # Only pass submitted_at if explicitly provided; domain will validate tz and chronology.
        if submitted_at is None:
            transition = contribution.submit()
        else:
            transition = contribution.submit(submitted_at=submitted_at)
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

        # Enforce single input mode
        primitives_provided = any([human_actor_ref is not None, outcome is not None, reason is not None, decided_at is not None])
        if decision is not None and primitives_provided:
            raise ContributionServiceError("provide either a pre-built decision or primitive fields, not both")

        if decision is None:
            # primitives mode: require human_actor_ref and outcome
            if human_actor_ref is None or outcome is None:
                raise ContributionServiceError("either decision or (human_actor_ref and outcome) must be provided")

            # Construct decision bound to the canonical target contribution
            if decided_at is None:
                decision = HumanDecision(
                    human_actor_ref=human_actor_ref,
                    contribution_ref=canonical_contribution_id,
                    outcome=outcome,
                    reason=reason,
                )
            else:
                decision = HumanDecision(
                    human_actor_ref=human_actor_ref,
                    contribution_ref=canonical_contribution_id,
                    outcome=outcome,
                    reason=reason,
                    decided_at=decided_at,
                )
        else:
            # pre-built decision mode: do not duplicate domain target validation;
            # delegate target integrity to the domain by passing the decision unchanged.
            pass

        # Delegate to domain; domain enforces self-review, chronology, tz, reasons, etc.
        transition = contribution.record_human_decision(decision)
        return transition
