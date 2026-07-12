import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from src.intevia.core.activity import (
    Activity,
    ActivityTimezoneError,
    ActivityValidationError,
)
from src.intevia.core.contribution import (
    Contribution,
    TransitionRecord,
    ContributionState,
    ContributionValidationError,
    DecisionOutcome,
    DecisionReasonRequired,
    HumanDecision,
    InvalidContributionTransition,
    OUTCOME_TO_STATE,
    SelfReviewNotPermitted,
    TimezoneRequiredError,
    TransitionChronologyError,
    TransitionType,
)
BASE_TIME = datetime(2026, 7, 12, 20, 0, tzinfo=timezone.utc)
class ContributionFlowTests(unittest.TestCase):
    def build_activity(
        self,
        *,
        created_at: datetime = BASE_TIME,
    ) -> Activity:
        return Activity(
            activity_id="activity:001",
            title="Write governed implementation note",
            completion_criteria="A reviewable note is submitted.",
            created_at=created_at,
        )
    def build_contribution(
        self,
        *,
        created_at: datetime = BASE_TIME + timedelta(minutes=1),
    ) -> Contribution:
        return Contribution(
            contribution_id="contribution:001",
            activity_ref=self.build_activity(),
            contributor_ref="human:learner-001",
            content="Governed implementation note.",
            created_at=created_at,
        )
    def test_activity_constructs_with_required_fields(self):
        activity = self.build_activity()
        self.assertEqual(activity.activity_id, "activity:001")
        self.assertEqual(activity.created_at.tzinfo, timezone.utc)
    def test_activity_rejects_missing_title(self):
        with self.assertRaises(ActivityValidationError):
            Activity(
                activity_id="activity:001",
                title="",
                completion_criteria="Observable completion.",
                created_at=BASE_TIME,
            )
    def test_activity_rejects_naive_created_at(self):
        with self.assertRaises(ActivityTimezoneError):
            self.build_activity(
                created_at=datetime(2026, 7, 12, 20, 0)
            )
    def test_activity_normalises_aware_time_to_utc(self):
        offset = timezone(timedelta(hours=5, minutes=30))
        local_time = datetime(2026, 7, 13, 1, 30, tzinfo=offset)
        activity = self.build_activity(created_at=local_time)
        self.assertEqual(activity.created_at, BASE_TIME)
        self.assertEqual(activity.created_at.tzinfo, timezone.utc)
    def test_contribution_begins_in_draft(self):
        contribution = self.build_contribution()
        self.assertEqual(contribution.state, ContributionState.DRAFT)
        self.assertEqual(contribution.transition_history, ())
    def test_contribution_rejects_naive_created_at(self):
        with self.assertRaises(TimezoneRequiredError):
            self.build_contribution(
                created_at=datetime(2026, 7, 12, 20, 1)
            )
    def test_contribution_cannot_precede_activity(self):
        with self.assertRaises(TransitionChronologyError):
            Contribution(
                contribution_id="contribution:001",
                activity_ref=self.build_activity(),
                contributor_ref="human:learner-001",
                content="Governed implementation note.",
                created_at=BASE_TIME - timedelta(seconds=1),
            )
    def test_contributor_ref_requires_human_namespace(self):
        with self.assertRaises(ContributionValidationError):
            Contribution(
                contribution_id="contribution:001",
                activity_ref=self.build_activity(),
                contributor_ref="learner-001",
                content="Governed implementation note.",
                created_at=BASE_TIME + timedelta(minutes=1),
            )
    def test_submission_records_transition(self):
        contribution = self.build_contribution()
        submitted_at = BASE_TIME + timedelta(minutes=2)
        transition = contribution.submit(
            submitted_at=submitted_at
        )
        self.assertEqual(
            contribution.state,
            ContributionState.SUBMITTED,
        )
        self.assertEqual(transition.prior_state, ContributionState.DRAFT)
        self.assertEqual(
            transition.new_state,
            ContributionState.SUBMITTED,
        )
        self.assertEqual(
            transition.actor_ref,
            "human:learner-001",
        )
        self.assertEqual(
            transition.transition_type,
            TransitionType.SUBMISSION,
        )
        self.assertEqual(transition.timestamp, submitted_at)
        self.assertEqual(contribution.transition_history, (transition,))
    def test_submission_rejects_naive_timestamp(self):
        contribution = self.build_contribution()
        with self.assertRaises(TimezoneRequiredError):
            contribution.submit(
                submitted_at=datetime(2026, 7, 12, 20, 2)
            )
    def test_submission_cannot_precede_contribution_creation(self):
        contribution = self.build_contribution()
        with self.assertRaises(TransitionChronologyError):
            contribution.submit(submitted_at=BASE_TIME)
    def test_repeated_submission_is_refused(self):
        contribution = self.build_contribution()
        contribution.submit(
            submitted_at=BASE_TIME + timedelta(minutes=2)
        )
        with self.assertRaises(InvalidContributionTransition):
            contribution.submit(
                submitted_at=BASE_TIME + timedelta(minutes=3)
            )
    def test_human_acceptance_is_recorded(self):
        contribution = self.build_contribution()
        contribution.submit(
            submitted_at=BASE_TIME + timedelta(minutes=2)
        )
        decision = HumanDecision(
            human_actor_ref="human:reviewer-001",
                contribution_ref="contribution:001",
            outcome=DecisionOutcome.ACCEPTED,
            decided_at=BASE_TIME + timedelta(minutes=3),
        )
        transition = contribution.record_human_decision(decision)
        self.assertEqual(
            contribution.state,
            ContributionState.ACCEPTED,
        )
        self.assertEqual(
            transition.actor_ref,
            "human:reviewer-001",
        )
        self.assertEqual(
            transition.transition_type,
            TransitionType.HUMAN_DECISION,
        )
        self.assertIsNone(transition.reason)
    def test_contributor_cannot_be_reviewer(self):
        contribution = self.build_contribution()
        contribution.submit(
            submitted_at=BASE_TIME + timedelta(minutes=2)
        )
        decision = HumanDecision(
            human_actor_ref="human:learner-001",
            contribution_ref="contribution:001",
            outcome=DecisionOutcome.ACCEPTED,
            decided_at=BASE_TIME + timedelta(minutes=3),
        )
        with self.assertRaises(SelfReviewNotPermitted):
            contribution.record_human_decision(decision)
    def test_human_actor_requires_human_namespace(self):
        with self.assertRaises(ContributionValidationError):
            HumanDecision(
                human_actor_ref="reviewer-001",
                contribution_ref="contribution:001",
                outcome=DecisionOutcome.ACCEPTED,
                decided_at=BASE_TIME,
            )
    def test_blank_human_actor_is_rejected(self):
        with self.assertRaises(ContributionValidationError):
            HumanDecision(
                human_actor_ref="   ",
                contribution_ref="contribution:001",
                outcome=DecisionOutcome.ACCEPTED,
                decided_at=BASE_TIME,
            )
    def test_human_decision_rejects_naive_timestamp(self):
        with self.assertRaises(TimezoneRequiredError):
            HumanDecision(
                human_actor_ref="human:reviewer-001",
                contribution_ref="contribution:001",
                outcome=DecisionOutcome.ACCEPTED,
                decided_at=datetime(2026, 7, 12, 20, 3),
            )
    def test_return_requires_reason(self):
        with self.assertRaises(DecisionReasonRequired):
            HumanDecision(
                human_actor_ref="human:reviewer-001",
                contribution_ref="contribution:001",
                outcome=DecisionOutcome.RETURNED,
                decided_at=BASE_TIME,
            )
    def test_whitespace_return_reason_is_rejected(self):
        with self.assertRaises(DecisionReasonRequired):
            HumanDecision(
                human_actor_ref="human:reviewer-001",
                contribution_ref="contribution:001",
                outcome=DecisionOutcome.RETURNED,
                reason="   ",
                decided_at=BASE_TIME,
            )
    def test_rejection_requires_reason(self):
        with self.assertRaises(DecisionReasonRequired):
            HumanDecision(
                human_actor_ref="human:reviewer-001",
                contribution_ref="contribution:001",
                outcome=DecisionOutcome.REJECTED,
                decided_at=BASE_TIME,
            )
    def test_return_with_reason_is_recorded(self):
        contribution = self.build_contribution()
        contribution.submit(
            submitted_at=BASE_TIME + timedelta(minutes=2)
        )
        decision = HumanDecision(
            human_actor_ref="human:reviewer-001",
                contribution_ref="contribution:001",
            outcome=DecisionOutcome.RETURNED,
            reason="Please provide supporting evidence.",
            decided_at=BASE_TIME + timedelta(minutes=3),
        )
        contribution.record_human_decision(decision)
        self.assertEqual(
            contribution.state,
            ContributionState.RETURNED,
        )
        self.assertFalse(contribution.is_terminal)
        self.assertEqual(
            contribution.transition_history[-1].reason,
            "Please provide supporting evidence.",
        )
    def test_rejection_with_reason_is_recorded(self):
        contribution = self.build_contribution()
        contribution.submit(
            submitted_at=BASE_TIME + timedelta(minutes=2)
        )
        decision = HumanDecision(
            human_actor_ref="human:reviewer-001",
                contribution_ref="contribution:001",
            outcome=DecisionOutcome.REJECTED,
            reason="The submission does not meet the criteria.",
            decided_at=BASE_TIME + timedelta(minutes=3),
        )
        contribution.record_human_decision(decision)
        self.assertEqual(
            contribution.state,
            ContributionState.REJECTED,
        )
        self.assertTrue(contribution.is_terminal)
    def test_decision_before_submission_is_refused(self):
        contribution = self.build_contribution()
        decision = HumanDecision(
            human_actor_ref="human:reviewer-001",
                contribution_ref="contribution:001",
            outcome=DecisionOutcome.ACCEPTED,
            decided_at=BASE_TIME + timedelta(minutes=2),
        )
        with self.assertRaises(InvalidContributionTransition):
            contribution.record_human_decision(decision)
    def test_decision_cannot_precede_submission(self):
        contribution = self.build_contribution()
        contribution.submit(
            submitted_at=BASE_TIME + timedelta(minutes=3)
        )
        decision = HumanDecision(
            human_actor_ref="human:reviewer-001",
                contribution_ref="contribution:001",
            outcome=DecisionOutcome.ACCEPTED,
            decided_at=BASE_TIME + timedelta(minutes=2),
        )
        with self.assertRaises(TransitionChronologyError):
            contribution.record_human_decision(decision)
    def test_decision_after_terminal_state_is_refused(self):
        contribution = self.build_contribution()
        contribution.submit(
            submitted_at=BASE_TIME + timedelta(minutes=2)
        )
        contribution.record_human_decision(
            HumanDecision(
                human_actor_ref="human:reviewer-001",
                contribution_ref="contribution:001",
                outcome=DecisionOutcome.ACCEPTED,
                decided_at=BASE_TIME + timedelta(minutes=3),
            )
        )
        with self.assertRaises(InvalidContributionTransition):
            contribution.record_human_decision(
                HumanDecision(
                    human_actor_ref="human:reviewer-002",
                contribution_ref="contribution:001",
                    outcome=DecisionOutcome.REJECTED,
                    reason="A second decision is not permitted.",
                    decided_at=BASE_TIME + timedelta(minutes=4),
                )
            )
    def test_contribution_object_cannot_supply_decision(self):
        contribution = self.build_contribution()
        contribution.submit(
            submitted_at=BASE_TIME + timedelta(minutes=2)
        )
        with self.assertRaises(ContributionValidationError):
            contribution.record_human_decision(contribution)
    def test_outcome_mapping_is_explicit(self):
        self.assertEqual(
            OUTCOME_TO_STATE[DecisionOutcome.ACCEPTED],
            ContributionState.ACCEPTED,
        )
        self.assertEqual(
            OUTCOME_TO_STATE[DecisionOutcome.RETURNED],
            ContributionState.RETURNED,
        )
        self.assertEqual(
            OUTCOME_TO_STATE[DecisionOutcome.REJECTED],
            ContributionState.REJECTED,
        )
    def test_transition_records_are_immutable(self):
        contribution = self.build_contribution()
        transition = contribution.submit(
            submitted_at=BASE_TIME + timedelta(minutes=2)
        )
        with self.assertRaises(FrozenInstanceError):
            transition.actor_ref = "human:changed"
    def test_history_is_tuple_and_matches_current_state(self):
        contribution = self.build_contribution()
        submission = contribution.submit(
            submitted_at=BASE_TIME + timedelta(minutes=2)
        )
        decision = contribution.record_human_decision(
            HumanDecision(
                human_actor_ref="human:reviewer-001",
                contribution_ref="contribution:001",
                outcome=DecisionOutcome.ACCEPTED,
                reason="Criteria met.",
                decided_at=BASE_TIME + timedelta(minutes=3),
            )
        )
        self.assertIsInstance(
            contribution.transition_history,
            tuple,
        )
        self.assertEqual(
            contribution.transition_history,
            (submission, decision),
        )
        self.assertEqual(
            contribution.transition_history[-1].new_state,
            contribution.state,
        )
    def test_equal_timestamps_are_deliberately_permitted(self):
        contribution = self.build_contribution()
        stamp = BASE_TIME + timedelta(minutes=2)
        contribution.submit(submitted_at=stamp)
        decision = HumanDecision(
            human_actor_ref="human:reviewer-001",
            contribution_ref="contribution:001",
            outcome=DecisionOutcome.ACCEPTED,
            decided_at=stamp,
        )
        contribution.record_human_decision(decision)
        self.assertEqual(contribution.state, ContributionState.ACCEPTED)

    def test_resubmission_from_returned_remains_refused(self):
        contribution = self.build_contribution()
        contribution.submit(submitted_at=BASE_TIME + timedelta(minutes=2))
        contribution.record_human_decision(
            HumanDecision(
                human_actor_ref="human:reviewer-001",
                contribution_ref="contribution:001",
                outcome=DecisionOutcome.RETURNED,
                reason="Needs evidence.",
                decided_at=BASE_TIME + timedelta(minutes=3),
            )
        )
        with self.assertRaises(InvalidContributionTransition):
            contribution.submit(submitted_at=BASE_TIME + timedelta(minutes=4))

    def test_falsy_submitted_at_is_rejected(self):
        contribution = self.build_contribution()
        with self.assertRaises(ContributionValidationError):
            contribution.submit(submitted_at=0)

    def test_contribution_state_cannot_be_assigned(self):
        contribution = self.build_contribution()
        with self.assertRaises(FrozenInstanceError):
            contribution.state = ContributionState.ACCEPTED

    def test_case_variant_reviewer_cannot_self_review(self):
        contribution = self.build_contribution()
        contribution.submit(submitted_at=BASE_TIME + timedelta(minutes=2))
        decision = HumanDecision(
            human_actor_ref="human:LEARNER-001",
            contribution_ref="contribution:001",
            outcome=DecisionOutcome.ACCEPTED,
            decided_at=BASE_TIME + timedelta(minutes=3),
        )
        with self.assertRaises(SelfReviewNotPermitted):
            contribution.record_human_decision(decision)

    def test_decision_for_other_contribution_is_rejected(self):
        contribution = self.build_contribution()
        contribution.submit(submitted_at=BASE_TIME + timedelta(minutes=2))
        decision = HumanDecision(
            human_actor_ref="human:reviewer-001",
            contribution_ref="contribution:999",
            outcome=DecisionOutcome.ACCEPTED,
            decided_at=BASE_TIME + timedelta(minutes=3),
        )
        with self.assertRaises(ContributionValidationError):
            contribution.record_human_decision(decision)

    def test_transition_record_validates_states_and_semantics(self):
        with self.assertRaises(ContributionValidationError):
            TransitionRecord(
                prior_state="banana",
                new_state=42,
                actor_ref="human:x",
                timestamp=BASE_TIME,
                transition_type="soup",
            )
        with self.assertRaises(InvalidContributionTransition):
            TransitionRecord(
                prior_state=ContributionState.ACCEPTED,
                new_state=ContributionState.DRAFT,
                actor_ref="human:x",
                timestamp=BASE_TIME,
                transition_type=TransitionType.HUMAN_DECISION,
            )
        with self.assertRaises(DecisionReasonRequired):
            TransitionRecord(
                prior_state=ContributionState.SUBMITTED,
                new_state=ContributionState.RETURNED,
                actor_ref="human:x",
                timestamp=BASE_TIME,
                transition_type=TransitionType.HUMAN_DECISION,
            )

if __name__ == "__main__":
    unittest.main()
