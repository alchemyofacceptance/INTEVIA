from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from uuid import uuid4

from django.test import SimpleTestCase

from src.intevia.services.event_resource_relationship_contract import (
    AUTHORITY_POLICY_REFERENCE,
    EventAuthorityResult,
    EventRelationshipAction,
    EventRelationshipAuthorityTarget,
    ExistenceDisclosureResult,
    RelationshipDisclosureResult,
    RelationshipPurpose,
    RelationshipState,
    VoidReason,
    authority_payload,
    determination_reference_for,
)
from src.intevia.services.library_exact_version_contract import LibraryRequestContext


NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)


class EventResourceRelationshipContractTests(SimpleTestCase):
    def target(self):
        return EventRelationshipAuthorityTarget(
            actor_identity_id=str(uuid4()),
            actor_access_epoch=7,
            action=EventRelationshipAction.CREATE,
            authority_scope="create",
            event_id="event.contract",
            event_state="draft",
            relationship_id=None,
            current_assertion_id=None,
            resource_id="library.resource",
            version_number=1,
            current_purpose=None,
            proposed_purpose=RelationshipPurpose.PREPARATION,
            occurred_at=NOW,
        )

    def test_closed_vocabulary_is_exact(self):
        self.assertEqual(
            [item.value for item in EventRelationshipAction],
            ["CREATE", "SUPERSEDE_VERSION", "AMEND_PURPOSE", "RETIRE", "VOID"],
        )
        self.assertEqual(
            [item.value for item in RelationshipPurpose],
            ["PREPARATION", "DURING_EVENT", "FOLLOW_UP", "REFERENCE"],
        )
        self.assertEqual(
            [item.value for item in RelationshipState],
            ["CURRENT", "RETIRED", "SUPERSEDED", "VOIDED"],
        )
        self.assertEqual(len(VoidReason), 6)
        self.assertEqual(len(EventAuthorityResult), 3)
        self.assertEqual(len(RelationshipDisclosureResult), 3)
        self.assertEqual(len(ExistenceDisclosureResult), 3)

    def test_target_is_frozen_and_rejects_invalid_epoch(self):
        target = self.target()
        with self.assertRaises(FrozenInstanceError):
            target.actor_access_epoch = 8
        values = target.__dict__ if hasattr(target, "__dict__") else {}
        self.assertNotIn("purpose_authority", values)
        with self.assertRaises(ValueError):
            EventRelationshipAuthorityTarget(
                actor_identity_id=target.actor_identity_id,
                actor_access_epoch=-1,
                action=target.action,
                authority_scope=target.authority_scope,
                event_id=target.event_id,
                event_state=target.event_state,
                relationship_id=None,
                current_assertion_id=None,
                resource_id=target.resource_id,
                version_number=1,
                current_purpose=None,
                proposed_purpose=RelationshipPurpose.PREPARATION,
                occurred_at=NOW,
            )

    def test_canonical_payload_binds_target_and_is_content_addressed(self):
        target = self.target()
        payload = authority_payload(
            target,
            result=EventAuthorityResult.QUALIFIED,
            binding_reference="event-binding:contract:v1",
            provider_snapshot_reference="event-snapshot:contract:v1",
            evaluated_at=NOW,
        )
        self.assertIn(AUTHORITY_POLICY_REFERENCE.encode(), payload)
        self.assertIn(b'"actor_access_epoch":"7"', payload)
        self.assertEqual(payload, authority_payload(
            target,
            result=EventAuthorityResult.QUALIFIED,
            binding_reference="event-binding:contract:v1",
            provider_snapshot_reference="event-snapshot:contract:v1",
            evaluated_at=NOW,
        ))
        self.assertRegex(
            determination_reference_for(payload),
            r"\Aevent-resource-determination:sha256:[0-9a-f]{64}\Z",
        )

    def test_purpose_remains_outside_closed_library_request_context(self):
        with self.assertRaises(TypeError):
            LibraryRequestContext(
                request_reference="request.contract",
                consumer_reference="consumer.s011b",
                authority_binding_reference="lib-authority-binding:contract:v1",
                policy_reference="policy:LIB-EXACT-VERSION-PREALPHA-001:v1",
                requested_at=NOW,
                purpose=RelationshipPurpose.PREPARATION,
            )

    def test_during_event_display_grants_no_entitlement_wording(self):
        self.assertEqual(RelationshipPurpose.DURING_EVENT.display, "During the Event")