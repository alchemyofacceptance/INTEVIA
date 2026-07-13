from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from src.intevia.core.contribution import (
    DecisionOutcome,
    HumanDecision,
    InvalidContributionTransition,
)
from src.intevia.observation.journal import (
    ObservationEntry,
    ObservationEventKind,
    ObservationJournal,
)
from src.intevia.services.contribution_service import (
    ActivityNotFound,
    ContributionService,
    ContributionServiceError,
    DuplicateActivity,
    ObservationEmissionError,
)


BASE_TIME = datetime(2026, 7, 13, 14, 0, tzinfo=timezone.utc)


class FailingObservationJournal(ObservationJournal):
    def append(self, entry: ObservationEntry) -> None:
        raise RuntimeError("controlled observation append failure")


class DerivedObservationJournal(ObservationJournal):
    pass


class ObservationIntegrationTests(unittest.TestCase):
    def create_activity(
        self,
        service: ContributionService,
        *,
        activity_id: str = "activity:001",
    ):
        return service.create_activity(
            activity_id=activity_id,
            title="Governed activity",
            completion_criteria="An explicit Human decision is recorded.",
            created_at=BASE_TIME,
        )

    def create_contribution(
        self,
        service: ContributionService,
        *,
        contribution_id: str = "contribution:001",
    ):
        return service.create_contribution(
            contribution_id=contribution_id,
            activity_id="activity:001",
            contributor_ref="human:contributor",
            content="Governed contribution",
            created_at=BASE_TIME + timedelta(seconds=1),
        )

    def test_default_construction_completes_existing_governed_path(self) -> None:
        service = ContributionService()
        self.create_activity(service)
        self.create_contribution(service)
        transition = service.submit_contribution(
            contribution_id="contribution:001",
            submitted_at=BASE_TIME + timedelta(seconds=2),
        )
        decision_transition = service.record_human_decision(
            contribution_id="contribution:001",
            human_actor_ref="human:reviewer",
            outcome=DecisionOutcome.ACCEPTED,
            decided_at=BASE_TIME + timedelta(seconds=3),
        )

        self.assertEqual(transition.new_state.value, "submitted")
        self.assertEqual(decision_transition.new_state.value, "accepted")
        self.assertIsNone(service.observation_journal)

    def test_explicit_journal_and_subclass_are_accepted(self) -> None:
        journal = ObservationJournal()
        derived = DerivedObservationJournal()

        self.assertIs(
            ContributionService(observation_journal=journal).observation_journal,
            journal,
        )
        self.assertIs(
            ContributionService(observation_journal=derived).observation_journal,
            derived,
        )

    def test_invalid_injected_object_is_rejected_immediately(self) -> None:
        with self.assertRaises(ContributionServiceError):
            ContributionService(observation_journal=object())

    def test_activity_creation_emits_exact_entry_and_returns_same_result(
        self,
    ) -> None:
        journal = ObservationJournal()
        service = ContributionService(observation_journal=journal)

        activity = self.create_activity(service)
        entries = journal.list_entries()

        self.assertIs(service.get_activity("activity:001"), activity)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry.event_kind, ObservationEventKind.ACTIVITY_CREATED)
        self.assertEqual(entry.activity_id, activity.activity_id)
        self.assertEqual(entry.occurred_at, activity.created_at)
        self.assertIsNone(entry.contribution_id)
        self.assertIsNone(entry.actor_ref)
        self.assertIsNone(entry.outcome)
        self.assertIsNone(entry.prior_state)
        self.assertIsNone(entry.new_state)

    def test_contribution_creation_emits_exact_entry_and_returns_same_result(
        self,
    ) -> None:
        journal = ObservationJournal()
        service = ContributionService(observation_journal=journal)
        self.create_activity(service)

        contribution = self.create_contribution(service)
        entry = journal.list_entries()[-1]

        self.assertIs(
            service.get_contribution("contribution:001"),
            contribution,
        )
        self.assertEqual(
            entry.event_kind,
            ObservationEventKind.CONTRIBUTION_CREATED,
        )
        self.assertEqual(entry.activity_id, "activity:001")
        self.assertEqual(entry.contribution_id, contribution.contribution_id)
        self.assertEqual(entry.actor_ref, contribution.contributor_ref)
        self.assertEqual(entry.occurred_at, contribution.created_at)
        self.assertIsNone(entry.outcome)
        self.assertIsNone(entry.prior_state)
        self.assertIsNone(entry.new_state)

    def test_submission_emits_exact_transition_entry_and_returns_same_result(
        self,
    ) -> None:
        journal = ObservationJournal()
        service = ContributionService(observation_journal=journal)
        self.create_activity(service)
        contribution = self.create_contribution(service)

        transition = service.submit_contribution(
            contribution_id=contribution.contribution_id,
            submitted_at=BASE_TIME + timedelta(seconds=2),
        )
        entry = journal.list_entries()[-1]

        self.assertIs(contribution.transition_history[-1], transition)
        self.assertEqual(
            entry.event_kind,
            ObservationEventKind.CONTRIBUTION_SUBMITTED,
        )
        self.assertEqual(entry.activity_id, "activity:001")
        self.assertEqual(entry.contribution_id, "contribution:001")
        self.assertEqual(entry.actor_ref, transition.actor_ref)
        self.assertEqual(entry.prior_state, transition.prior_state.value)
        self.assertEqual(entry.new_state, transition.new_state.value)
        self.assertEqual(entry.occurred_at, transition.timestamp)
        self.assertIsNone(entry.outcome)

    def test_primitive_decision_emits_exact_entry_and_constructs_once(
        self,
    ) -> None:
        journal = ObservationJournal()
        service = ContributionService(observation_journal=journal)
        self.create_activity(service)
        contribution = self.create_contribution(service)
        service.submit_contribution(
            contribution_id=contribution.contribution_id,
            submitted_at=BASE_TIME + timedelta(seconds=2),
        )

        from src.intevia.services import contribution_service as service_module

        original_decision = service_module.HumanDecision
        constructed = []

        def construct_once(*args, **kwargs):
            decision = original_decision(*args, **kwargs)
            constructed.append(decision)
            return decision

        with patch.object(
            service_module,
            "HumanDecision",
            side_effect=construct_once,
        ) as constructor:
            transition = service.record_human_decision(
                contribution_id=contribution.contribution_id,
                human_actor_ref="human:reviewer",
                outcome=DecisionOutcome.REJECTED,
                reason="Does not satisfy the completion criteria.",
                decided_at=BASE_TIME + timedelta(seconds=3),
            )

        self.assertEqual(constructor.call_count, 1)
        self.assertEqual(len(constructed), 1)
        entry = journal.list_entries()[-1]
        self.assertEqual(
            entry.event_kind,
            ObservationEventKind.HUMAN_DECISION_RECORDED,
        )
        self.assertEqual(entry.activity_id, "activity:001")
        self.assertEqual(
            entry.contribution_id,
            contribution.contribution_id,
        )
        self.assertEqual(entry.outcome, constructed[0].outcome.value)
        self.assertEqual(entry.outcome, "rejected")
        self.assertEqual(entry.actor_ref, transition.actor_ref)
        self.assertEqual(entry.prior_state, transition.prior_state.value)
        self.assertEqual(entry.new_state, transition.new_state.value)
        self.assertEqual(entry.occurred_at, transition.timestamp)
        self.assertIs(
            contribution.transition_history[-1],
            transition,
        )

    def test_prebuilt_decision_uses_same_object_outcome(self) -> None:
        journal = ObservationJournal()
        service = ContributionService(observation_journal=journal)
        self.create_activity(service)
        contribution = self.create_contribution(service)
        service.submit_contribution(
            contribution_id=contribution.contribution_id,
            submitted_at=BASE_TIME + timedelta(seconds=2),
        )
        decision = HumanDecision(
            human_actor_ref="human:reviewer",
            contribution_ref=contribution.contribution_id,
            outcome=DecisionOutcome.RETURNED,
            reason="Please add evidence.",
            decided_at=BASE_TIME + timedelta(seconds=3),
        )

        from src.intevia.core.contribution import Contribution

        original = Contribution.record_human_decision
        seen = []

        def capture(self, effective_decision):
            seen.append(effective_decision)
            return original(self, effective_decision)

        with patch.object(
            Contribution,
            "record_human_decision",
            new=capture,
        ):
            transition = service.record_human_decision(
                contribution_id=contribution.contribution_id,
                decision=decision,
            )

        self.assertEqual(seen, [decision])
        entry = journal.list_entries()[-1]
        self.assertEqual(
            entry.event_kind,
            ObservationEventKind.HUMAN_DECISION_RECORDED,
        )
        self.assertEqual(entry.activity_id, "activity:001")
        self.assertEqual(
            entry.contribution_id,
            contribution.contribution_id,
        )
        self.assertEqual(
            entry.actor_ref,
            transition.actor_ref,
        )
        self.assertEqual(entry.outcome, decision.outcome.value)
        self.assertEqual(entry.outcome, "returned")
        self.assertEqual(
            entry.prior_state,
            transition.prior_state.value,
        )
        self.assertEqual(entry.new_state, transition.new_state.value)
        self.assertEqual(
            entry.occurred_at,
            transition.timestamp,
        )

    def test_domain_failure_is_not_observed_or_wrapped(self) -> None:
        journal = ObservationJournal()
        service = ContributionService(observation_journal=journal)

        with self.assertRaises(ActivityNotFound):
            service.create_contribution(
                contribution_id="contribution:001",
                activity_id="activity:missing",
                contributor_ref="human:contributor",
                content="payload",
                created_at=BASE_TIME,
            )

        self.assertEqual(journal.list_entries(), ())

    def test_mixed_decision_input_failure_is_not_observed_or_wrapped(
        self,
    ) -> None:
        journal = ObservationJournal()
        service = ContributionService(observation_journal=journal)
        self.create_activity(service)
        contribution = self.create_contribution(service)
        service.submit_contribution(
            contribution_id=contribution.contribution_id,
            submitted_at=BASE_TIME + timedelta(seconds=2),
        )
        before = journal.list_entries()
        decision = HumanDecision(
            human_actor_ref="human:reviewer",
            contribution_ref=contribution.contribution_id,
            outcome=DecisionOutcome.ACCEPTED,
            decided_at=BASE_TIME + timedelta(seconds=3),
        )

        with self.assertRaises(ContributionServiceError) as context:
            service.record_human_decision(
                contribution_id=contribution.contribution_id,
                decision=decision,
                human_actor_ref="human:other",
                outcome=DecisionOutcome.ACCEPTED,
            )

        self.assertNotIsInstance(context.exception, ObservationEmissionError)
        self.assertEqual(journal.list_entries(), before)

    def test_activity_observation_failure_preserves_success_and_cause(
        self,
    ) -> None:
        journal = FailingObservationJournal()
        service = ContributionService(observation_journal=journal)

        with self.assertRaises(ObservationEmissionError) as context:
            self.create_activity(service)

        error = context.exception
        self.assertEqual(
            error.event_kind,
            ObservationEventKind.ACTIVITY_CREATED,
        )
        self.assertIs(
            service.get_activity("activity:001"),
            error.operation_result,
        )
        self.assertIsInstance(error.__cause__, RuntimeError)
        self.assertEqual(
            str(error.__cause__),
            "controlled observation append failure",
        )
        self.assertEqual(journal.list_entries(), ())

    def test_activity_retry_after_observation_failure_is_duplicate(self) -> None:
        service = ContributionService(
            observation_journal=FailingObservationJournal()
        )

        with self.assertRaises(ObservationEmissionError):
            self.create_activity(service)

        with self.assertRaises(DuplicateActivity):
            self.create_activity(service)

    def test_submission_observation_failure_preserves_transition_and_state(
        self,
    ) -> None:
        journal = ObservationJournal()
        service = ContributionService(observation_journal=journal)
        self.create_activity(service)
        contribution = self.create_contribution(service)
        service.observation_journal = FailingObservationJournal()

        with self.assertRaises(ObservationEmissionError) as context:
            service.submit_contribution(
                contribution_id=contribution.contribution_id,
                submitted_at=BASE_TIME + timedelta(seconds=2),
            )

        error = context.exception
        self.assertEqual(
            error.event_kind,
            ObservationEventKind.CONTRIBUTION_SUBMITTED,
        )
        self.assertIs(
            contribution.transition_history[-1],
            error.operation_result,
        )
        self.assertEqual(contribution.state.value, "submitted")
        self.assertIsInstance(error.__cause__, RuntimeError)

    def test_submission_retry_after_observation_failure_is_invalid_transition(
        self,
    ) -> None:
        service = ContributionService(
            observation_journal=ObservationJournal()
        )
        self.create_activity(service)
        contribution = self.create_contribution(service)
        service.observation_journal = FailingObservationJournal()

        with self.assertRaises(ObservationEmissionError):
            service.submit_contribution(
                contribution_id=contribution.contribution_id,
                submitted_at=BASE_TIME + timedelta(seconds=2),
            )

        with self.assertRaises(InvalidContributionTransition):
            service.submit_contribution(
                contribution_id=contribution.contribution_id,
                submitted_at=BASE_TIME + timedelta(seconds=3),
            )


if __name__ == "__main__":
    raise SystemExit(unittest.main())
