from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from typing import Tuple

from src.intevia.commands.observation import (
    _format_entry,
    _render_snapshot,
    observation_snapshot,
    main,
)
from src.intevia.observation.journal import (
    ObservationEntry,
    ObservationEventKind,
    ObservationJournal,
)


UTC_TIME = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)


class CountingJournal(ObservationJournal):
    def __init__(self, entries: Tuple[ObservationEntry, ...]) -> None:
        super().__init__()
        self._entries = entries
        self.list_calls = 0

    def list_entries(self) -> Tuple[ObservationEntry, ...]:
        self.list_calls += 1
        return self._entries


class FailingJournal(ObservationJournal):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self._error = error

    def list_entries(self):
        raise self._error


def make_entry(
    *,
    kind: ObservationEventKind,
    activity_id: str | None = None,
    contribution_id: str | None = None,
    actor_ref: str | None = None,
    prior_state: str | None = None,
    new_state: str | None = None,
    outcome: str | None = None,
    occurred_at: datetime = UTC_TIME,
) -> ObservationEntry:
    return ObservationEntry(
        event_kind=kind,
        activity_id=activity_id,
        contribution_id=contribution_id,
        actor_ref=actor_ref,
        prior_state=prior_state,
        new_state=new_state,
        outcome=outcome,
        occurred_at=occurred_at,
    )


# ---------------------------------------------------------------------------
# FORMATTER TESTS
# ---------------------------------------------------------------------------

class ObservationCommandFormattingTests(unittest.TestCase):
    def test_format_entry_renders_all_fields_in_fixed_order(self) -> None:
        entry = make_entry(
            kind=ObservationEventKind.HUMAN_DECISION_RECORDED,
            activity_id="activity:001",
            contribution_id="contribution:001",
            actor_ref="human:reviewer",
            prior_state="submitted",
            new_state="accepted",
            outcome="accepted",
        )

        rendered = _format_entry(entry)

        self.assertEqual(
            rendered,
            (
                "event=human_decision_recorded "
                "activity=activity:001 "
                "contribution=contribution:001 "
                "actor=human:reviewer "
                "prior=submitted "
                "new=accepted "
                "outcome=accepted "
                "at=2026-07-13T12:00:00+00:00"
            ),
        )
        self.assertFalse(rendered.endswith("\n"))

    def test_format_entry_renders_missing_optional_fields_as_dash(self) -> None:
        entry = make_entry(
            kind=ObservationEventKind.ACTIVITY_CREATED,
            activity_id="activity:001",
        )

        rendered = _format_entry(entry)

        self.assertEqual(
            rendered,
            (
                "event=activity_created "
                "activity=activity:001 "
                "contribution=- "
                "actor=- "
                "prior=- "
                "new=- "
                "outcome=- "
                "at=2026-07-13T12:00:00+00:00"
            ),
        )

    def test_render_snapshot_empty_returns_empty_string(self) -> None:
        rendered = _render_snapshot(())
        self.assertEqual(rendered, "")

    def test_render_snapshot_multiple_entries_one_line_each(self) -> None:
        first = make_entry(
            kind=ObservationEventKind.ACTIVITY_CREATED,
            activity_id="activity:001",
        )
        second = make_entry(
            kind=ObservationEventKind.CONTRIBUTION_CREATED,
            activity_id="activity:001",
            contribution_id="contribution:001",
            actor_ref="human:contributor",
        )

        rendered = _render_snapshot((first, second))
        lines = rendered.split("\n")

        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0].split()[0], "event=activity_created")
        self.assertEqual(lines[1].split()[0], "event=contribution_created")
        self.assertFalse(rendered.endswith("\n"))


# ---------------------------------------------------------------------------
# WRAPPER TESTS
# ---------------------------------------------------------------------------

class ObservationCommandWrapperTests(unittest.TestCase):
    def test_observation_snapshot_empty_journal_renders_single_newline(self) -> None:
        journal = CountingJournal(entries=())
        snapshot = observation_snapshot(journal)

        self.assertEqual(snapshot, "\n")
        self.assertEqual(journal.list_calls, 1)

    def test_observation_snapshot_renders_entries_in_snapshot_order(self) -> None:
        first = make_entry(
            kind=ObservationEventKind.ACTIVITY_CREATED,
            activity_id="activity:001",
        )
        second = make_entry(
            kind=ObservationEventKind.CONTRIBUTION_SUBMITTED,
            activity_id="activity:001",
            contribution_id="contribution:001",
            actor_ref="human:contributor",
            prior_state="draft",
            new_state="submitted",
        )
        journal = CountingJournal(entries=(first, second))

        snapshot = observation_snapshot(journal)

        self.assertTrue(snapshot.endswith("\n"))
        body = snapshot[:-1]
        lines = body.split("\n")
        self.assertEqual(len(lines), 2)
        self.assertIn("event=activity_created", lines[0])
        self.assertIn("event=contribution_submitted", lines[1])
        self.assertEqual(journal.list_calls, 1)

    def test_observation_snapshot_is_deterministic_on_repeated_calls(self) -> None:
        entry = make_entry(
            kind=ObservationEventKind.CONTRIBUTION_CREATED,
            activity_id="activity:001",
            contribution_id="contribution:001",
            actor_ref="human:contributor",
        )
        journal = CountingJournal(entries=(entry,))

        first = observation_snapshot(journal)
        second = observation_snapshot(journal)

        self.assertEqual(first, second)
        self.assertEqual(journal.list_calls, 2)

    def test_subclass_snapshot_exception_is_propagated_unchanged(self) -> None:
        error = RuntimeError("controlled snapshot failure")
        journal = FailingJournal(error=error)

        with self.assertRaises(RuntimeError) as context:
            _ = observation_snapshot(journal)

        self.assertIs(context.exception, error)
        self.assertEqual(str(context.exception), "controlled snapshot failure")


# ---------------------------------------------------------------------------
# EXACT-OUTPUT TESTS
# ---------------------------------------------------------------------------

class ObservationCommandExactOutputTests(unittest.TestCase):
    def test_single_entry_exact_output(self) -> None:
        entry = make_entry(
            kind=ObservationEventKind.CONTRIBUTION_CREATED,
            activity_id="activity:001",
            contribution_id="contribution:001",
            actor_ref="human:contributor",
        )
        journal = CountingJournal(entries=(entry,))

        snapshot = observation_snapshot(journal)

        expected = (
            "event=contribution_created "
            "activity=activity:001 "
            "contribution=contribution:001 "
            "actor=human:contributor "
            "prior=- "
            "new=- "
            "outcome=- "
            "at=2026-07-13T12:00:00+00:00\n"
        )

        self.assertEqual(snapshot, expected)
        self.assertEqual(journal.list_calls, 1)

    def test_multiple_entries_exact_output(self) -> None:
        first = make_entry(
            kind=ObservationEventKind.ACTIVITY_CREATED,
            activity_id="activity:001",
        )
        second = make_entry(
            kind=ObservationEventKind.HUMAN_DECISION_RECORDED,
            activity_id="activity:001",
            contribution_id="contribution:001",
            actor_ref="human:reviewer",
            prior_state="submitted",
            new_state="accepted",
            outcome="accepted",
        )
        journal = CountingJournal(entries=(first, second))

        snapshot = observation_snapshot(journal)

        expected = (
            "event=activity_created "
            "activity=activity:001 "
            "contribution=- "
            "actor=- "
            "prior=- "
            "new=- "
            "outcome=- "
            "at=2026-07-13T12:00:00+00:00\n"
            "event=human_decision_recorded "
            "activity=activity:001 "
            "contribution=contribution:001 "
            "actor=human:reviewer "
            "prior=submitted "
            "new=accepted "
            "outcome=accepted "
            "at=2026-07-13T12:00:00+00:00\n"
        )

        self.assertEqual(snapshot, expected)
        self.assertEqual(journal.list_calls, 1)


# ---------------------------------------------------------------------------
# AUTHENTIC CLI TEST — UPDATED FOR DIRECT INJECTION
# ---------------------------------------------------------------------------

class ObservationCommandCLITests(unittest.TestCase):
    def test_main_writes_snapshot_with_single_newline(self) -> None:
        journal = CountingJournal(entries=())

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            rc = main(journal)

        rendered = buffer.getvalue()

        self.assertEqual(rc, 0)
        self.assertEqual(rendered, "\n")
        self.assertEqual(journal.list_calls, 1)


if __name__ == "__main__":
    raise SystemExit(unittest.main())