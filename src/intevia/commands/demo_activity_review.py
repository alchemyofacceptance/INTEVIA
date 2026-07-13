from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from typing import Optional, TextIO

from src.intevia.services.contribution_service import ContributionService
from src.intevia.core.contribution import DecisionOutcome


DEMO_BASE_TIME = datetime(
    2026,
    7,
    13,
    8,
    30,
    tzinfo=timezone.utc,
)


def _iso(value: datetime) -> str:
    return value.isoformat()


def run_demo(*, output: Optional[TextIO] = None) -> int:
    """
    Deterministic, bounded CLI demonstration of a governed Activity review.

    Uses a fresh ContributionService and only its public methods:
      - create_activity
      - create_contribution
      - submit_contribution
      - record_human_decision
      - get_contribution

    The output is derived from returned domain objects and transition records.
    """
    if output is None:
        output = sys.stdout

    service = ContributionService()

    activity_id = "activity:demo-review"
    contribution_id = "contribution:demo-001"
    contributor_ref = "human:contributor-001"
    reviewer_ref = "human:reviewer-001"

    t0 = DEMO_BASE_TIME

    activity = service.create_activity(
        activity_id=activity_id,
        title="Review governed contribution",
        completion_criteria="A Human reviewer records an explicit decision.",
        created_at=t0,
    )

    created_contribution = service.create_contribution(
        activity_id=activity.activity_id,
        contribution_id=contribution_id,
        contributor_ref=contributor_ref,
        content="Demonstration contribution for governed review.",
        created_at=t0 + timedelta(seconds=1),
    )

    service.submit_contribution(
        contribution_id=created_contribution.contribution_id,
        submitted_at=t0 + timedelta(seconds=2),
    )

    service.record_human_decision(
        contribution_id=created_contribution.contribution_id,
        human_actor_ref=reviewer_ref,
        outcome=DecisionOutcome.ACCEPTED,
        decided_at=t0 + timedelta(seconds=3),
    )

    contribution = service.get_contribution(
        contribution_id=created_contribution.contribution_id
    )
    history = contribution.transition_history
    final_transition = history[-1]

    print("INTEVIA v1.0 — Governed Activity Review Demo", file=output)
    print("", file=output)

    print(f"Activity: {activity.activity_id}", file=output)
    print(f"Contribution: {contribution.contribution_id}", file=output)
    print(f"Contributor: {contribution.contributor_ref}", file=output)
    print(f"Reviewer: {final_transition.actor_ref}", file=output)
    print("", file=output)

    states = [
        history[0].prior_state,
        *[record.new_state for record in history],
    ]

    print("Flow:", file=output)
    print(" -> ".join(state.value for state in states), file=output)
    print("", file=output)

    print(f"Final state: {contribution.state.value}", file=output)
    print("", file=output)

    print("Transition history:", file=output)
    for index, record in enumerate(history, start=1):
        print(
            f"{index}. {record.prior_state.value} -> {record.new_state.value}",
            file=output,
        )
        print(f"   Actor: {record.actor_ref}", file=output)
        print(f"   Timestamp: {_iso(record.timestamp)}", file=output)
        print("", file=output)

    print(f"Human decision: {final_transition.new_state.value}", file=output)
    print("Authority: explicit Human decision recorded", file=output)

    return 0


def main() -> int:
    return run_demo()


if __name__ == "__main__":
    raise SystemExit(main())
