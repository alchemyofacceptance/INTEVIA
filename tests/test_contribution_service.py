import unittest
from datetime import datetime, timezone, timedelta

from src.intevia.services.contribution_service import (
    ContributionService,
    ActivityNotFound,
    ContributionNotFound,
    DuplicateActivity,
    DuplicateContribution,
    ContributionServiceError,
)
from src.intevia.core.contribution import (
    HumanDecision,
    DecisionOutcome,
    ContributionState,
    InvalidContributionTransition,
    ContributionValidationError,
    SelfReviewNotPermitted,
    TransitionChronologyError,
    TimezoneRequiredError,
)
from src.intevia.core.activity import ActivityTimezoneError  # domain-specific timezone error


BASE_TIME = datetime(2026, 7, 12, 20, 0, tzinfo=timezone.utc)


class ContributionServiceTests(unittest.TestCase):
    def setUp(self):
        self.svc = ContributionService()

    def test_create_activity_and_retrieve_with_canonicalisation(self):
        a = self.svc.create_activity(
            activity_id=" activity:001 ",
            title="Test activity",
            completion_criteria="Complete the test",
            created_at=BASE_TIME,
        )
        # canonicalisation: stored key is stripped; retrieval with padded id works
        self.assertEqual(self.svc.get_activity("activity:001").activity_id, "activity:001")

    def test_duplicate_activity_refused_with_canonicalisation(self):
        self.svc.create_activity(activity_id="activity:001", title="T", completion_criteria="C", created_at=BASE_TIME)
        with self.assertRaises(DuplicateActivity):
            self.svc.create_activity(activity_id=" activity:001 ", title="T2", completion_criteria="C2", created_at=BASE_TIME)

    def test_create_contribution_against_existing_activity(self):
        self.svc.create_activity(activity_id="activity:001", title="T", completion_criteria="C", created_at=BASE_TIME)
        c = self.svc.create_contribution(
            contribution_id="contrib:001",
            activity_id="activity:001",
            contributor_ref="human:alice",
            content="payload",
            created_at=BASE_TIME + timedelta(seconds=1),
        )
        self.assertEqual(c.contribution_id, "contrib:001")

    def test_create_contribution_for_unknown_activity_refused(self):
        with self.assertRaises(ActivityNotFound):
            self.svc.create_contribution(
                contribution_id="contrib:001",
                activity_id="activity:unknown",
                contributor_ref="human:alice",
                content="payload",
            )

    def test_duplicate_contribution_refused_with_canonicalisation(self):
        self.svc.create_activity(activity_id="activity:001", title="T", completion_criteria="C", created_at=BASE_TIME)
        self.svc.create_contribution(
            contribution_id="contrib:001",
            activity_id="activity:001",
            contributor_ref="human:alice",
            content="payload",
            created_at=BASE_TIME + timedelta(seconds=1),
        )
        with self.assertRaises(DuplicateContribution):
            self.svc.create_contribution(
                contribution_id=" contrib:001 ",
                activity_id="activity:001",
                contributor_ref="human:bob",
                content="other",
                created_at=BASE_TIME + timedelta(seconds=2),
            )

    def test_submit_contribution_delegates_and_records_and_history(self):
        self.svc.create_activity(activity_id="activity:001", title="T", completion_criteria="C", created_at=BASE_TIME)
        self.svc.create_contribution(
            contribution_id="contrib:001",
            activity_id="activity:001",
            contributor_ref="human:alice",
            content="payload",
            created_at=BASE_TIME + timedelta(seconds=1),
        )
        submitted_at = BASE_TIME + timedelta(seconds=2)
        transition = self.svc.submit_contribution(contribution_id="contrib:001", submitted_at=submitted_at)
        self.assertEqual(transition.new_state, ContributionState.SUBMITTED)
        contribution = self.svc.get_contribution("contrib:001")
        self.assertEqual(contribution.state, ContributionState.SUBMITTED)
        self.assertEqual(contribution.transition_history[-1], transition)

    def test_record_human_decision_delegates_and_records_with_service_constructed_decision_and_history(self):
        self.svc.create_activity(activity_id="activity:001", title="T", completion_criteria="C", created_at=BASE_TIME)
        self.svc.create_contribution(
            contribution_id="contrib:001",
            activity_id="activity:001",
            contributor_ref="human:alice",
            content="payload",
            created_at=BASE_TIME + timedelta(seconds=1),
        )
        self.svc.submit_contribution(contribution_id="contrib:001", submitted_at=BASE_TIME + timedelta(seconds=2))
        decision_time = BASE_TIME + timedelta(seconds=3)
        transition = self.svc.record_human_decision(
            contribution_id=" contrib:001 ",
            human_actor_ref="human:reviewer-001",
            outcome=DecisionOutcome.ACCEPTED,
            decided_at=decision_time,
        )
        self.assertEqual(transition.new_state, ContributionState.ACCEPTED)
        contribution = self.svc.get_contribution("contrib:001")
        self.assertEqual(contribution.state, ContributionState.ACCEPTED)
        self.assertEqual(contribution.transition_history[-1], transition)
        self.assertEqual(len(contribution.transition_history), 2)

    def test_record_human_decision_prebuilt_decision_mode(self):
        self.svc.create_activity(activity_id="activity:001", title="T", completion_criteria="C", created_at=BASE_TIME)
        self.svc.create_contribution(
            contribution_id="contrib:001",
            activity_id="activity:001",
            contributor_ref="human:alice",
            content="payload",
            created_at=BASE_TIME + timedelta(seconds=1),
        )
        self.svc.submit_contribution(contribution_id="contrib:001", submitted_at=BASE_TIME + timedelta(seconds=2))
        decision = HumanDecision(
            human_actor_ref="human:reviewer-001",
            contribution_ref="contrib:001",
            outcome=DecisionOutcome.ACCEPTED,
            decided_at=BASE_TIME + timedelta(seconds=3),
        )
        transition = self.svc.record_human_decision(contribution_id="contrib:001", decision=decision)
        self.assertEqual(transition.new_state, ContributionState.ACCEPTED)

    def test_mixed_decision_input_mode_refused(self):
        self.svc.create_activity(activity_id="activity:001", title="T", completion_criteria="C", created_at=BASE_TIME)
        self.svc.create_contribution(
            contribution_id="contrib:001",
            activity_id="activity:001",
            contributor_ref="human:alice",
            content="payload",
            created_at=BASE_TIME + timedelta(seconds=1),
        )
        self.svc.submit_contribution(contribution_id="contrib:001", submitted_at=BASE_TIME + timedelta(seconds=2))
        with self.assertRaises(ContributionServiceError):
            self.svc.record_human_decision(
                contribution_id="contrib:001",
                decision=HumanDecision(
                    human_actor_ref="human:reviewer-001",
                    contribution_ref="contrib:001",
                    outcome=DecisionOutcome.ACCEPTED,
                    decided_at=BASE_TIME + timedelta(seconds=3),
                ),
                human_actor_ref="human:someone-else",
                outcome=DecisionOutcome.ACCEPTED,
            )

    def test_self_review_refused_through_service_asserts_domain_exception(self):
        self.svc.create_activity(activity_id="activity:001", title="T", completion_criteria="C", created_at=BASE_TIME)
        self.svc.create_contribution(
            contribution_id="contrib:001",
            activity_id="activity:001",
            contributor_ref="human:alice",
            content="payload",
            created_at=BASE_TIME + timedelta(seconds=1),
        )
        self.svc.submit_contribution(contribution_id="contrib:001", submitted_at=BASE_TIME + timedelta(seconds=2))
        # Attempt to have the contributor act as reviewer — domain should raise SelfReviewNotPermitted
        with self.assertRaises(SelfReviewNotPermitted):
            self.svc.record_human_decision(
                contribution_id="contrib:001",
                human_actor_ref="human:alice",
                outcome=DecisionOutcome.ACCEPTED,
                decided_at=BASE_TIME + timedelta(seconds=3),
            )

    def test_wrong_target_decision_refused(self):
        # Create two contributions and submit both
        self.svc.create_activity(activity_id="activity:001", title="T", completion_criteria="C", created_at=BASE_TIME)
        self.svc.create_contribution(contribution_id="contrib:001", activity_id="activity:001", contributor_ref="human:alice", content="payload", created_at=BASE_TIME + timedelta(seconds=1))
        self.svc.create_contribution(contribution_id="contrib:002", activity_id="activity:001", contributor_ref="human:bob", content="payload", created_at=BASE_TIME + timedelta(seconds=2))
        self.svc.submit_contribution(contribution_id="contrib:001", submitted_at=BASE_TIME + timedelta(seconds=3))
        self.svc.submit_contribution(contribution_id="contrib:002", submitted_at=BASE_TIME + timedelta(seconds=4))
        # Build a decision that targets contrib:001 but attempt to apply it to contrib:002
        decision = HumanDecision(
            human_actor_ref="human:reviewer-001",
            contribution_ref="contrib:001",
            outcome=DecisionOutcome.ACCEPTED,
            decided_at=BASE_TIME + timedelta(seconds=5),
        )
        with self.assertRaises(ContributionValidationError):
            self.svc.record_human_decision(contribution_id="contrib:002", decision=decision)

    def test_naive_activity_datetime_surfaces_domain_error(self):
        # Passing naive datetime should surface a domain error (exact domain exception)
        with self.assertRaises(ActivityTimezoneError):
            self.svc.create_activity(activity_id="activity:002", title="T2", completion_criteria="C2", created_at=datetime.now())

    def test_naive_contribution_created_at_surfaces_domain_error(self):
        self.svc.create_activity(activity_id="activity:001", title="T", completion_criteria="C", created_at=BASE_TIME)
        # Domain will raise the appropriate timezone error for contribution created_at
        with self.assertRaises(TimezoneRequiredError):
            self.svc.create_contribution(contribution_id="contrib:naive", activity_id="activity:001", contributor_ref="human:alice", content="payload", created_at=datetime.now())

    def test_naive_submission_timestamp_surfaces_domain_error(self):
        self.svc.create_activity(activity_id="activity:001", title="T", completion_criteria="C", created_at=BASE_TIME)
        self.svc.create_contribution(contribution_id="contrib:001", activity_id="activity:001", contributor_ref="human:alice", content="payload", created_at=BASE_TIME + timedelta(seconds=1))
        # Submit with naive timestamp should surface domain timezone error
        with self.assertRaises(TimezoneRequiredError):
            self.svc.submit_contribution(contribution_id="contrib:001", submitted_at=datetime.now())

    def test_naive_decision_timestamp_surfaces_domain_error(self):
        self.svc.create_activity(activity_id="activity:001", title="T", completion_criteria="C", created_at=BASE_TIME)
        self.svc.create_contribution(contribution_id="contrib:001", activity_id="activity:001", contributor_ref="human:alice", content="payload", created_at=BASE_TIME + timedelta(seconds=1))
        self.svc.submit_contribution(contribution_id="contrib:001", submitted_at=BASE_TIME + timedelta(seconds=2))
        # Decision with naive timestamp should surface domain timezone error
        with self.assertRaises(TimezoneRequiredError):
            self.svc.record_human_decision(contribution_id="contrib:001", human_actor_ref="human:reviewer-001", outcome=DecisionOutcome.ACCEPTED, decided_at=datetime.now())

    def test_chronological_regression_tested_via_decision_timestamp(self):
        self.svc.create_activity(activity_id="activity:001", title="T", completion_criteria="C", created_at=BASE_TIME)
        self.svc.create_contribution(contribution_id="contrib:001", activity_id="activity:001", contributor_ref="human:alice", content="payload", created_at=BASE_TIME + timedelta(seconds=1))
        t2 = BASE_TIME + timedelta(seconds=2)
        self.svc.submit_contribution(contribution_id="contrib:001", submitted_at=t2)
        # Construct a decision with an earlier timestamp t1
        t1 = t2 - timedelta(seconds=10)
        with self.assertRaises(TransitionChronologyError):
            self.svc.record_human_decision(contribution_id="contrib:001", human_actor_ref="human:reviewer-001", outcome=DecisionOutcome.ACCEPTED, decided_at=t1)

    def test_equal_timestamps_permitted(self):
        self.svc.create_activity(activity_id="activity:001", title="T", completion_criteria="C", created_at=BASE_TIME)
        self.svc.create_contribution(contribution_id="contrib:001", activity_id="activity:001", contributor_ref="human:alice", content="payload", created_at=BASE_TIME + timedelta(seconds=1))
        t = BASE_TIME + timedelta(seconds=2)
        self.svc.submit_contribution(contribution_id="contrib:001", submitted_at=t)
        # Decision with same timestamp is permitted
        self.svc.record_human_decision(contribution_id="contrib:001", human_actor_ref="human:reviewer-001", outcome=DecisionOutcome.ACCEPTED, decided_at=t)

    def test_returned_state_resubmission_refused(self):
        self.svc.create_activity(activity_id="activity:001", title="T", completion_criteria="C", created_at=BASE_TIME)
        self.svc.create_contribution(contribution_id="contrib:001", activity_id="activity:001", contributor_ref="human:alice", content="payload", created_at=BASE_TIME + timedelta(seconds=1))
        self.svc.submit_contribution(contribution_id="contrib:001", submitted_at=BASE_TIME + timedelta(seconds=2))
        # Record a RETURNED decision with reason
        self.svc.record_human_decision(contribution_id="contrib:001", human_actor_ref="human:reviewer-001", outcome=DecisionOutcome.RETURNED, reason="Please add evidence.", decided_at=BASE_TIME + timedelta(seconds=3))
        # Attempt to resubmit should be refused by domain
        with self.assertRaises(InvalidContributionTransition):
            self.svc.submit_contribution(contribution_id="contrib:001", submitted_at=BASE_TIME + timedelta(seconds=4))

    def test_unknown_activity_and_contribution_retrieval_refused(self):
        with self.assertRaises(ActivityNotFound):
            self.svc.get_activity("nope")
        with self.assertRaises(ContributionNotFound):
            self.svc.get_contribution("nope")

    # --- Additional required ContributionServiceError tests ----------------

    def test_no_decision_input_supplied_raises_service_error(self):
        self.svc.create_activity(activity_id="activity:001", title="T", completion_criteria="C", created_at=BASE_TIME)
        self.svc.create_contribution(contribution_id="contrib:001", activity_id="activity:001", contributor_ref="human:alice", content="payload", created_at=BASE_TIME + timedelta(seconds=1))
        self.svc.submit_contribution(contribution_id="contrib:001", submitted_at=BASE_TIME + timedelta(seconds=2))
        with self.assertRaises(ContributionServiceError):
            self.svc.record_human_decision(contribution_id="contrib:001")

    def test_human_actor_without_outcome_raises_service_error(self):
        self.svc.create_activity(activity_id="activity:001", title="T", completion_criteria="C", created_at=BASE_TIME)
        self.svc.create_contribution(contribution_id="contrib:001", activity_id="activity:001", contributor_ref="human:alice", content="payload", created_at=BASE_TIME + timedelta(seconds=1))
        self.svc.submit_contribution(contribution_id="contrib:001", submitted_at=BASE_TIME + timedelta(seconds=2))
        with self.assertRaises(ContributionServiceError):
            self.svc.record_human_decision(contribution_id="contrib:001", human_actor_ref="human:reviewer-001")

    def test_reason_without_required_primitive_fields_raises_service_error(self):
        self.svc.create_activity(activity_id="activity:001", title="T", completion_criteria="C", created_at=BASE_TIME)
        self.svc.create_contribution(contribution_id="contrib:001", activity_id="activity:001", contributor_ref="human:alice", content="payload", created_at=BASE_TIME + timedelta(seconds=1))
        self.svc.submit_contribution(contribution_id="contrib:001", submitted_at=BASE_TIME + timedelta(seconds=2))
        # Supplying only a reason without actor/outcome is invalid
        with self.assertRaises(ContributionServiceError):
            self.svc.record_human_decision(contribution_id="contrib:001", reason="Because")

    def test_none_identifier_raises_service_error(self):
        with self.assertRaises(ContributionServiceError):
            self.svc.create_activity(activity_id=None, title="T", completion_criteria="C")

    def test_non_string_identifier_raises_service_error(self):
        with self.assertRaises(ContributionServiceError):
            self.svc.create_activity(activity_id=123, title="T", completion_criteria="C")

    def test_whitespace_only_identifier_raises_service_error(self):
        with self.assertRaises(ContributionServiceError):
            self.svc.create_activity(activity_id="   ", title="T", completion_criteria="C")

    # --- Recommended bounded branch tests --------------------------------

    def test_activity_creation_without_created_at_uses_domain_default(self):
        a = self.svc.create_activity(activity_id="activity:002", title="T", completion_criteria="C")
        self.assertEqual(a.activity_id, "activity:002")

    def test_contribution_creation_without_created_at_uses_domain_default(self):
        self.svc.create_activity(activity_id="activity:003", title="T", completion_criteria="C", created_at=BASE_TIME)
        c = self.svc.create_contribution(contribution_id="contrib:002", activity_id="activity:003", contributor_ref="human:alice", content="payload")
        self.assertEqual(c.contribution_id, "contrib:002")

    def test_submission_without_submitted_at_uses_domain_default(self):
        self.svc.create_activity(activity_id="activity:004", title="T", completion_criteria="C", created_at=BASE_TIME)
        self.svc.create_contribution(contribution_id="contrib:003", activity_id="activity:004", contributor_ref="human:alice", content="payload", created_at=BASE_TIME + timedelta(seconds=1))
        transition = self.svc.submit_contribution(contribution_id="contrib:003")
        self.assertEqual(transition.new_state, ContributionState.SUBMITTED)

    def test_contribution_registry_isolation_between_service_instances(self):
        svc2 = ContributionService()
        self.svc.create_activity(activity_id="activity:005", title="T", completion_criteria="C", created_at=BASE_TIME)
        self.svc.create_contribution(contribution_id="contrib:isolated", activity_id="activity:005", contributor_ref="human:alice", content="payload", created_at=BASE_TIME + timedelta(seconds=1))
        with self.assertRaises(ContributionNotFound):
            svc2.get_contribution("contrib:isolated")

    def test_registries_are_instance_isolated(self):
        svc2 = ContributionService()
        self.svc.create_activity(activity_id="activity:001", title="T", completion_criteria="C", created_at=BASE_TIME)
        with self.assertRaises(ActivityNotFound):
            svc2.get_activity("activity:001")


if __name__ == "__main__":
    unittest.main()
