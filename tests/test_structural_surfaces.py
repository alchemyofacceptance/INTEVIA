import importlib
import unittest

from src.intevia.consent_surface import ConsentSurface
from src.intevia.evidence_surface import EvidenceSurface
from src.intevia.human_node import HumanNode
from src.intevia.identity_profile import (
    CadenceBand,
    CohortState,
    IdentityProfile,
    PublicationBoundary,
    QueueBand as IdentityQueueBand,
)
from src.intevia.notification_surface import NotificationSurface
from src.intevia.queue_surface import (
    QueueBand as SurfaceQueueBand,
    QueueSurface,
)
from src.intevia.relationship_surface import RelationshipSurface
from src.intevia.review_surface import ReviewSurface


class StructuralSurfaceSmokeTests(unittest.TestCase):
    def build_identity(self, anchor: str = "human:test-governor") -> IdentityProfile:
        return IdentityProfile(
            human_identity_anchor=anchor,
            cohort_state=CohortState.NOT_ACTIVE,
            queue_band=IdentityQueueBand.NOT_QUEUED,
            evidence_consent=False,
            cadence_band=CadenceBand.NOT_SET,
            publication_boundary=PublicationBoundary.NOT_PUBLICATION_AUTHORISED,
            email_opt_in=False,
            email_last_sent=None,
            email_next_window=None,
            pause_state=False,
            exit_state=False,
        )

    def build_graph(self, anchor: str = "human:test-governor"):
        identity = self.build_identity(anchor)
        relationship = RelationshipSurface(identity_ref=identity)
        human = HumanNode(
            identity_ref=identity,
            relationship_ref=relationship,
        )
        consent = ConsentSurface(human_node_ref=human)
        notification = NotificationSurface(
            human_node_ref=human,
            consent_surface_ref=consent,
        )
        queue = QueueSurface(
            human_node_ref=human,
            consent_surface_ref=consent,
            notification_surface_ref=notification,
        )
        evidence = EvidenceSurface(
            human_node_ref=human,
            consent_surface_ref=consent,
            notification_surface_ref=notification,
            queue_surface_ref=queue,
        )
        review = ReviewSurface(
            human_node_ref=human,
            consent_surface_ref=consent,
            notification_surface_ref=notification,
            queue_surface_ref=queue,
            evidence_surface_ref=evidence,
        )

        return {
            "identity": identity,
            "relationship": relationship,
            "human": human,
            "consent": consent,
            "notification": notification,
            "queue": queue,
            "evidence": evidence,
            "review": review,
        }

    def test_all_structural_modules_import(self):
        module_names = [
            "src.intevia.consent_surface",
            "src.intevia.evidence_surface",
            "src.intevia.human_node",
            "src.intevia.identity_profile",
            "src.intevia.notification_surface",
            "src.intevia.queue_surface",
            "src.intevia.relationship_surface",
            "src.intevia.review_surface",
        ]

        for module_name in module_names:
            with self.subTest(module=module_name):
                self.assertIsNotNone(importlib.import_module(module_name))

    def test_minimum_structural_graph_constructs(self):
        graph = self.build_graph()

        self.assertIs(graph["human"].identity_ref, graph["identity"])
        self.assertIs(graph["human"].relationship_ref, graph["relationship"])
        self.assertIs(graph["consent"].human_node_ref, graph["human"])
        self.assertIs(
            graph["notification"].consent_surface_ref,
            graph["consent"],
        )
        self.assertIs(
            graph["queue"].notification_surface_ref,
            graph["notification"],
        )
        self.assertIs(
            graph["evidence"].queue_surface_ref,
            graph["queue"],
        )
        self.assertIs(
            graph["review"].evidence_surface_ref,
            graph["evidence"],
        )

    def test_mutable_defaults_are_instance_isolated(self):
        first = self.build_graph("human:first")
        second = self.build_graph("human:second")

        first["human"].governance_flags["flag"] = True
        first["consent"].consent_history.append("consent")
        first["notification"].notification_history.append("notification")
        first["queue"].queue_history.append("queue-history")
        first["queue"].queue_items.append("queue-item")
        first["evidence"].evidence_history.append("evidence-history")
        first["evidence"].evidence_items.append("evidence-item")
        first["review"].review_history.append("review-history")
        first["review"].review_items.append("review-item")

        self.assertEqual(second["human"].governance_flags, {})
        self.assertEqual(second["consent"].consent_history, [])
        self.assertEqual(second["notification"].notification_history, [])
        self.assertEqual(second["queue"].queue_history, [])
        self.assertEqual(second["queue"].queue_items, [])
        self.assertEqual(second["evidence"].evidence_history, [])
        self.assertEqual(second["evidence"].evidence_items, [])
        self.assertEqual(second["review"].review_history, [])
        self.assertEqual(second["review"].review_items, [])

    def test_queue_band_types_remain_distinct(self):
        self.assertIsNot(IdentityQueueBand, SurfaceQueueBand)
        self.assertEqual(
            IdentityQueueBand.NOT_QUEUED.value,
            "not_queued",
        )
        self.assertEqual(
            SurfaceQueueBand.DEFAULT.value,
            "default",
        )


if __name__ == "__main__":
    unittest.main()
