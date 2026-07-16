from __future__ import annotations

from typing import Iterable

from src.intevia.observation.journal import (
    ObservationEntry,
    ObservationJournal,
)


def _format_entry(entry: ObservationEntry) -> str:
    """Return a single-line, newline-free rendering of one ObservationEntry.

    Format:
    event=<kind> activity=<id> contribution=<id-or-> actor=<actor-or-> prior=<state-or-> new=<state-or-> outcome=<outcome-or-> at=<timestamp>
    """
    kind = entry.event_kind.value
    activity = entry.activity_id if entry.activity_id is not None else "-"
    contribution = (
        entry.contribution_id if entry.contribution_id is not None else "-"
    )
    actor = entry.actor_ref if entry.actor_ref is not None else "-"
    prior = entry.prior_state if entry.prior_state is not None else "-"
    new = entry.new_state if entry.new_state is not None else "-"
    outcome = entry.outcome if entry.outcome is not None else "-"
    at = entry.occurred_at.isoformat()

    return (
        f"event={kind} "
        f"activity={activity} "
        f"contribution={contribution} "
        f"actor={actor} "
        f"prior={prior} "
        f"new={new} "
        f"outcome={outcome} "
        f"at={at}"
    )


def _render_snapshot(entries: Iterable[ObservationEntry]) -> str:
    """Render a complete journal snapshot with one physical line per entry.

    Returns a string with no trailing newline; entries are rendered in snapshot order.
    """
    lines = [_format_entry(entry) for entry in entries]
    return "\n".join(lines)


def observation_snapshot(journal: ObservationJournal) -> str:
    """Return a complete snapshot rendering with exactly one trailing newline.

    Calls journal.list_entries() exactly once; any exception raised by the journal
    is propagated unchanged.
    """
    entries = journal.list_entries()
    rendered = _render_snapshot(entries)
    return f"{rendered}\n" if rendered else "\n"


def main(journal: ObservationJournal | None = None) -> int:
    """CLI entry point for observation snapshot rendering.

    Supports optional direct journal injection for testing and controlled invocation.
    Default CLI behaviour remains unchanged.
    """
    journal = journal if journal is not None else ObservationJournal()
    snapshot = observation_snapshot(journal)
    print(snapshot, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())