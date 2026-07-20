from datetime import datetime, timedelta, timezone
from unittest import TestCase

from src.intevia.core.activity import Activity
from src.intevia.core.contribution import (
    Contribution,
    ContributionState,
    DecisionOutcome,
    HumanDecision,
    InvalidContributionTransition,
    SelfReviewNotPermitted,
)


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


class ContributionFlowTests(TestCase):
    def contribution(self):
        activity = Activity(
            activity_id="activity:flow",
            title="Flow",
            completion_criteria="Review complete",
            created_at=NOW,
        )
        return Contribution(
            contribution_id="contribution:flow",
            activity_ref=activity,
            contributor_ref="human:contributor",
            content="content",
            created_at=NOW,
        )

    def test_review_gate_precedes_human_decision(self):
        contribution = self.contribution()
        contribution.submit(submitted_at=NOW + timedelta(seconds=1))
        decision = HumanDecision(
            human_actor_ref="human:reviewer",
            contribution_ref=contribution.contribution_id,
            outcome=DecisionOutcome.ACCEPTED,
            decided_at=NOW + timedelta(seconds=3),
        )
        with self.assertRaises(InvalidContributionTransition):
            contribution.record_human_decision(decision)
        contribution.begin_review(
            reviewer_ref="human:reviewer",
            reviewed_at=NOW + timedelta(seconds=2),
        )
        contribution.record_human_decision(decision)
        self.assertEqual(contribution.state, ContributionState.ACCEPTED)
        self.assertEqual(len(contribution.transition_history), 3)

    def test_rejection_is_terminal(self):
        contribution = self.contribution()
        contribution.submit(submitted_at=NOW)
        contribution.begin_review(reviewer_ref="human:reviewer", reviewed_at=NOW)
        contribution.record_human_decision(
            HumanDecision(
                human_actor_ref="human:reviewer",
                contribution_ref=contribution.contribution_id,
                outcome=DecisionOutcome.REJECTED,
                reason="Criteria not met",
                decided_at=NOW,
            )
        )
        self.assertEqual(contribution.state, ContributionState.REJECTED)
        self.assertTrue(contribution.is_terminal)

    def test_contributor_cannot_decide_own_work(self):
        contribution = self.contribution()
        contribution.submit(submitted_at=NOW)
        contribution.begin_review(reviewer_ref="human:reviewer", reviewed_at=NOW)
        with self.assertRaises(SelfReviewNotPermitted):
            contribution.record_human_decision(
                HumanDecision(
                    human_actor_ref="human:contributor",
                    contribution_ref=contribution.contribution_id,
                    outcome=DecisionOutcome.ACCEPTED,
                    decided_at=NOW,
                )
            )
