from __future__ import annotations

import unittest
from copy import deepcopy
from datetime import datetime

from src.intevia.commands.review_snapshot import review_snapshot
from src.intevia.review_surface import ReviewBand, ReviewState, ReviewSurface


SENTINEL_HUMAN = "HUMAN_NODE_SENTINEL"
SENTINEL_CONSENT = "CONSENT_SURFACE_SENTINEL"
SENTINEL_NOTIFICATION = "NOTIFICATION_SURFACE_SENTINEL"
SENTINEL_QUEUE = "QUEUE_SURFACE_SENTINEL"
SENTINEL_EVIDENCE = "EVIDENCE_SURFACE_SENTINEL"


class ReviewSnapshotTests(unittest.TestCase):
    def make_populated_surface(self) -> ReviewSurface:
        surface = ReviewSurface(
            human_node_ref=SENTINEL_HUMAN,
            consent_surface_ref=SENTINEL_CONSENT,
            notification_surface_ref=SENTINEL_NOTIFICATION,
            queue_surface_ref=SENTINEL_QUEUE,
            evidence_surface_ref=SENTINEL_EVIDENCE,
        )
        surface.review_state = ReviewState.HOLDING
        surface.review_band = ReviewBand.FUTURE
        surface.review_flags = {"z-flag": 1, "a-flag": True}
        surface.review_window = datetime(2026, 7, 17, 21, 0, 0)
        surface.review_history = ["first", 2, False]
        surface.review_items = ["alpha", "beta", "gamma"]
        return surface

    def make_empty_surface(self) -> ReviewSurface:
        return ReviewSurface(
            human_node_ref=SENTINEL_HUMAN,
            consent_surface_ref=SENTINEL_CONSENT,
            notification_surface_ref=SENTINEL_NOTIFICATION,
            queue_surface_ref=SENTINEL_QUEUE,
            evidence_surface_ref=SENTINEL_EVIDENCE,
        )

    def test_review_snapshot_returns_expected_output_for_populated_surface(
        self,
    ) -> None:
        surface = self.make_populated_surface()

        snapshot = review_snapshot(surface)

        expected = (
            "Review State:\n"
            "holding\n"
            "\n"
            "Review Band:\n"
            "future\n"
            "\n"
            "Review Flags:\n"
            "a-flag: True\n"
            "z-flag: 1\n"
            "\n"
            "Review Window:\n"
            "2026-07-17T21:00:00\n"
            "\n"
            "Review History:\n"
            "first\n"
            "2\n"
            "False\n"
            "\n"
            "Review Items:\n"
            "alpha\n"
            "beta\n"
            "gamma"
        )

        self.assertEqual(snapshot, expected)
        self.assertFalse(snapshot.endswith("\n"))
        self.assertNotIn(SENTINEL_HUMAN, snapshot)
        self.assertNotIn(SENTINEL_CONSENT, snapshot)
        self.assertNotIn(SENTINEL_NOTIFICATION, snapshot)
        self.assertNotIn(SENTINEL_QUEUE, snapshot)
        self.assertNotIn(SENTINEL_EVIDENCE, snapshot)

    def test_review_snapshot_handles_none_and_empty_collections(
        self,
    ) -> None:
        surface = self.make_empty_surface()

        snapshot = review_snapshot(surface)

        expected = (
            "Review State:\n"
            "empty\n"
            "\n"
            "Review Band:\n"
            "default\n"
            "\n"
            "Review Flags:\n"
            "[]\n"
            "\n"
            "Review Window:\n"
            "None\n"
            "\n"
            "Review History:\n"
            "[]\n"
            "\n"
            "Review Items:\n"
            "[]"
        )

        self.assertEqual(snapshot, expected)
        self.assertFalse(snapshot.endswith("\n"))

    def test_review_snapshot_does_not_mutate_surface(self) -> None:
        surface = self.make_populated_surface()

        before_state = surface.review_state
        before_band = surface.review_band
        before_flags = deepcopy(surface.review_flags)
        before_window = surface.review_window
        before_history = list(surface.review_history)
        before_items = list(surface.review_items)

        flags_identity = id(surface.review_flags)
        history_identity = id(surface.review_history)
        items_identity = id(surface.review_items)

        review_snapshot(surface)

        self.assertEqual(surface.review_state, before_state)
        self.assertEqual(surface.review_band, before_band)
        self.assertEqual(surface.review_flags, before_flags)
        self.assertEqual(surface.review_window, before_window)
        self.assertEqual(surface.review_history, before_history)
        self.assertEqual(surface.review_items, before_items)
        self.assertEqual(id(surface.review_flags), flags_identity)
        self.assertEqual(id(surface.review_history), history_identity)
        self.assertEqual(id(surface.review_items), items_identity)

    def test_review_snapshot_is_deterministic_across_repeated_calls(
        self,
    ) -> None:
        surface = self.make_populated_surface()

        first = review_snapshot(surface)
        second = review_snapshot(surface)

        self.assertEqual(first, second)

    def test_review_snapshot_rejects_unsupported_flag_value(self) -> None:
        surface = self.make_empty_surface()
        surface.review_flags = {"bad": object()}

        with self.assertRaises(TypeError) as context:
            review_snapshot(surface)

        self.assertEqual(
            str(context.exception),
            "review_flags contains unsupported value type: object",
        )

    def test_review_snapshot_rejects_unsupported_history_item(self) -> None:
        surface = self.make_empty_surface()
        surface.review_history = [object()]

        with self.assertRaises(TypeError) as context:
            review_snapshot(surface)

        self.assertEqual(
            str(context.exception),
            "review_history contains unsupported item type: object",
        )

    def test_review_snapshot_rejects_unsupported_review_item(self) -> None:
        surface = self.make_empty_surface()
        surface.review_items = [object()]

        with self.assertRaises(TypeError) as context:
            review_snapshot(surface)

        self.assertEqual(
            str(context.exception),
            "review_items contains unsupported item type: object",
        )


if __name__ == "__main__":
    unittest.main()
