from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from enum import Enum
from unittest.mock import Mock

from src.intevia.observation.journal import (
    InvalidObservationEntry,
    ObservationEntry,
    ObservationEventKind,
    ObservationJournal,
)


UTC_TIME = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)


class DomainState(str, Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    REJECTED = "rejected"


class DomainOutcome(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


def activity_entry(
    *,
    activity_id: str = "activity:001",
    occurred_at: datetime = UTC_TIME,
) -> ObservationEntry:
    return ObservationEntry(
        event_kind=ObservationEventKind.ACTIVITY_CREATED,
        occurred_at=occurred_at,
        activity_id=activity_id,
    )


def contribution_entry(
    *,
    contribution_id: str = "contribution:001",
) -> ObservationEntry:
    return ObservationEntry(
        event_kind=ObservationEventKind.CONTRIBUTION_CREATED,
        occurred_at=UTC_TIME,
        activity_id="activity:001",
        contribution_id=contribution_id,
        actor_ref="human:contributor",
    )


class ObservationEntryTests(unittest.TestCase):
    def test_observation_entry_accepts_minimal_activity_event(self) -> None:
        entry = activity_entry()

        self.assertEqual(
            entry.event_kind,
            ObservationEventKind.ACTIVITY_CREATED,
        )
        self.assertEqual(entry.activity_id, "activity:001")
        self.assertEqual(entry.occurred_at, UTC_TIME)
        self.assertIsNone(entry.contribution_id)
        self.assertIsNone(entry.actor_ref)
        self.assertIsNone(entry.outcome)
        self.assertIsNone(entry.prior_state)
        self.assertIsNone(entry.new_state)

    def test_observation_entry_accepts_rejected_state_and_outcome_values(
        self,
    ) -> None:
        entry = ObservationEntry(
            event_kind=ObservationEventKind.HUMAN_DECISION_RECORDED,
            occurred_at=UTC_TIME,
            activity_id="activity:001",
            contribution_id="contribution:001",
            actor_ref="human:reviewer",
            outcome="rejected",
            prior_state="submitted",
            new_state="rejected",
        )

        self.assertEqual(entry.outcome, "rejected")
        self.assertEqual(entry.new_state, "rejected")

    def test_observation_entry_rejects_naive_timestamp(self) -> None:
        with self.assertRaises(InvalidObservationEntry):
            activity_entry(occurred_at=datetime(2026, 7, 13, 12, 0))

    def test_observation_entry_rejects_non_datetime_timestamp(self) -> None:
        with self.assertRaises(InvalidObservationEntry):
            ObservationEntry(
                event_kind=ObservationEventKind.ACTIVITY_CREATED,
                occurred_at="2026-07-13T12:00:00Z",  # type: ignore[arg-type]
                activity_id="activity:001",
            )

    def test_observation_entry_normalises_timestamp_to_utc(self) -> None:
        source_timezone = timezone(timedelta(hours=5, minutes=30))
        source_time = datetime(
            2026,
            7,
            13,
            17,
            30,
            tzinfo=source_timezone,
        )

        entry = activity_entry(occurred_at=source_time)

        self.assertEqual(entry.occurred_at, UTC_TIME)
        self.assertIs(entry.occurred_at.tzinfo, timezone.utc)

    def test_observation_entry_is_frozen(self) -> None:
        entry = activity_entry()

        with self.assertRaises(FrozenInstanceError):
            entry.activity_id = "activity:changed"  # type: ignore[misc]

    def test_observation_entry_requires_event_kind_enum(self) -> None:
        with self.assertRaises(InvalidObservationEntry):
            ObservationEntry(
                event_kind="activity_created",  # type: ignore[arg-type]
                occurred_at=UTC_TIME,
                activity_id="activity:001",
            )

    def test_observation_entry_rejects_missing_activity_id_for_activity_created(
        self,
    ) -> None:
        with self.assertRaises(InvalidObservationEntry):
            ObservationEntry(
                event_kind=ObservationEventKind.ACTIVITY_CREATED,
                occurred_at=UTC_TIME,
            )

    def test_observation_entry_rejects_missing_required_fields_by_event_kind(
        self,
    ) -> None:
        with self.assertRaises(InvalidObservationEntry):
            ObservationEntry(
                event_kind=ObservationEventKind.CONTRIBUTION_CREATED,
                occurred_at=UTC_TIME,
                activity_id="activity:001",
                contribution_id="contribution:001",
            )

        with self.assertRaises(InvalidObservationEntry):
            ObservationEntry(
                event_kind=ObservationEventKind.CONTRIBUTION_SUBMITTED,
                occurred_at=UTC_TIME,
                activity_id="activity:001",
                contribution_id="contribution:001",
                actor_ref="human:contributor",
                prior_state="draft",
            )

        with self.assertRaises(InvalidObservationEntry):
            ObservationEntry(
                event_kind=ObservationEventKind.HUMAN_DECISION_RECORDED,
                occurred_at=UTC_TIME,
                activity_id="activity:001",
                contribution_id="contribution:001",
                actor_ref="human:reviewer",
                prior_state="submitted",
                new_state="accepted",
            )

    def test_observation_entry_rejects_irrelevant_field_combinations(
        self,
    ) -> None:
        with self.assertRaises(InvalidObservationEntry):
            ObservationEntry(
                event_kind=ObservationEventKind.ACTIVITY_CREATED,
                occurred_at=UTC_TIME,
                activity_id="activity:001",
                contribution_id="contribution:001",
            )

        with self.assertRaises(InvalidObservationEntry):
            ObservationEntry(
                event_kind=ObservationEventKind.CONTRIBUTION_SUBMITTED,
                occurred_at=UTC_TIME,
                activity_id="activity:001",
                contribution_id="contribution:001",
                actor_ref="human:contributor",
                outcome="accepted",
                prior_state="draft",
                new_state="submitted",
            )

    def test_observation_entry_rejects_invalid_state_values(self) -> None:
        with self.assertRaises(InvalidObservationEntry):
            ObservationEntry(
                event_kind=ObservationEventKind.CONTRIBUTION_SUBMITTED,
                occurred_at=UTC_TIME,
                activity_id="activity:001",
                contribution_id="contribution:001",
                actor_ref="human:contributor",
                prior_state="DRAFT",
                new_state="submitted",
            )

    def test_observation_entry_rejects_invalid_outcome_values(self) -> None:
        with self.assertRaises(InvalidObservationEntry):
            ObservationEntry(
                event_kind=ObservationEventKind.HUMAN_DECISION_RECORDED,
                occurred_at=UTC_TIME,
                activity_id="activity:001",
                contribution_id="contribution:001",
                actor_ref="human:reviewer",
                outcome="approved",
                prior_state="submitted",
                new_state="accepted",
            )

    def test_observation_entry_rejects_domain_enum_state_values(self) -> None:
        with self.assertRaises(InvalidObservationEntry):
            ObservationEntry(
                event_kind=ObservationEventKind.CONTRIBUTION_SUBMITTED,
                occurred_at=UTC_TIME,
                activity_id="activity:001",
                contribution_id="contribution:001",
                actor_ref="human:contributor",
                prior_state=DomainState.DRAFT,  # type: ignore[arg-type]
                new_state="submitted",
            )

    def test_observation_entry_rejects_domain_enum_outcome_values(self) -> None:
        with self.assertRaises(InvalidObservationEntry):
            ObservationEntry(
                event_kind=ObservationEventKind.HUMAN_DECISION_RECORDED,
                occurred_at=UTC_TIME,
                activity_id="activity:001",
                contribution_id="contribution:001",
                actor_ref="human:reviewer",
                outcome=DomainOutcome.REJECTED,  # type: ignore[arg-type]
                prior_state="submitted",
                new_state="rejected",
            )

    def test_observation_entry_trims_required_references(self) -> None:
        entry = ObservationEntry(
            event_kind=ObservationEventKind.CONTRIBUTION_CREATED,
            occurred_at=UTC_TIME,
            activity_id="  activity:001  ",
            contribution_id="  contribution:001  ",
            actor_ref="  human:contributor  ",
        )

        self.assertEqual(entry.activity_id, "activity:001")
        self.assertEqual(entry.contribution_id, "contribution:001")
        self.assertEqual(entry.actor_ref, "human:contributor")

    def test_observation_entry_rejects_blank_required_references(self) -> None:
        with self.assertRaises(InvalidObservationEntry):
            activity_entry(activity_id="   ")

    def test_observation_entry_rejects_non_string_required_references(
        self,
    ) -> None:
        with self.assertRaises(InvalidObservationEntry):
            ObservationEntry(
                event_kind=ObservationEventKind.CONTRIBUTION_CREATED,
                occurred_at=UTC_TIME,
                activity_id=123,  # type: ignore[arg-type]
                contribution_id="contribution:001",
                actor_ref="human:contributor",
            )

    def test_observation_entry_defensively_rejects_unhandled_event_kind(
        self,
    ) -> None:
        unknown_kind = Mock(spec=ObservationEventKind)

        with self.assertRaisesRegex(
            InvalidObservationEntry,
            "unhandled observation event kind",
        ):
            ObservationEntry(
                event_kind=unknown_kind,
                occurred_at=UTC_TIME,
                activity_id="activity:001",
            )


class ObservationJournalTests(unittest.TestCase):
    def test_journal_appends_entries_in_order(self) -> None:
        first = activity_entry()
        second = contribution_entry()
        journal = ObservationJournal()

        journal.append(first)
        journal.append(second)

        self.assertEqual(journal.list_entries(), (first, second))

    def test_journal_preserves_duplicate_appends(self) -> None:
        entry = activity_entry()
        journal = ObservationJournal()

        journal.append(entry)
        journal.append(entry)

        self.assertEqual(journal.list_entries(), (entry, entry))

    def test_journal_list_entries_returns_tuple_snapshot(self) -> None:
        entry = activity_entry()
        journal = ObservationJournal()
        journal.append(entry)

        snapshot = journal.list_entries()

        self.assertIsInstance(snapshot, tuple)
        self.assertEqual(snapshot, (entry,))

    def test_journal_snapshot_is_not_affected_by_later_append(self) -> None:
        first = activity_entry()
        second = contribution_entry()
        journal = ObservationJournal()
        journal.append(first)

        snapshot = journal.list_entries()
        journal.append(second)

        self.assertEqual(snapshot, (first,))
        self.assertEqual(journal.list_entries(), (first, second))

    def test_journal_rejects_non_entry_values(self) -> None:
        journal = ObservationJournal()

        with self.assertRaises(InvalidObservationEntry):
            journal.append("not an entry")  # type: ignore[arg-type]

    def test_journal_instances_are_isolated(self) -> None:
        first_journal = ObservationJournal()
        second_journal = ObservationJournal()
        first_journal.append(activity_entry())

        self.assertEqual(len(first_journal.list_entries()), 1)
        self.assertEqual(second_journal.list_entries(), ())


if __name__ == "__main__":
    raise SystemExit(unittest.main())
