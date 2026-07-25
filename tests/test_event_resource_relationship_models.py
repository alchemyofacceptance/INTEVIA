import hashlib
from datetime import datetime, timezone
from uuid import uuid4

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import (
    Event,
    EventResourceAssertion,
    EventResourceRelationship,
    EventResourceRelationshipEvidence,
    EventResourceRelationshipTransition,
    Identity,
    LibraryResource,
    LibraryResourceVersion,
)


NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)


class EventResourceRelationshipModelTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username="s011b-models")
        self.identity = Identity.objects.create(
            credential=user,
            access_state=Identity.AccessState.ACTIVE,
            access_epoch=3,
        )
        self.event = Event.objects.create(
            event_id="event.models",
            title="Model Event",
            description="Model guardians",
            owner=self.identity,
            created_at=NOW,
        )
        self.resource = LibraryResource.objects.create(
            resource_id="library.models",
            created_by=self.identity,
            state=LibraryResource.State.PUBLISHED,
            created_at=NOW,
        )
        self.version = LibraryResourceVersion.objects.create(
            resource=self.resource,
            version_number=1,
            content="model content",
            created_by=self.identity,
            created_at=NOW,
        )

    def create_aggregate(self):
        relationship = EventResourceRelationship.objects.create(
            relationship_id="relationship.models",
            event=self.event,
            library_resource=self.resource,
            created_at=NOW,
        )
        assertion = EventResourceAssertion.objects.create(
            relationship=relationship,
            revision=1,
            library_resource_version=self.version,
            purpose=EventResourceAssertion.Purpose.PREPARATION,
            state=EventResourceAssertion.State.CURRENT,
            created_by=self.identity,
            actor_access_epoch=self.identity.access_epoch,
            created_at=NOW,
        )
        transition = EventResourceRelationshipTransition.objects.create(
            relationship=relationship,
            sequence=1,
            action=EventResourceRelationshipTransition.Action.CREATE,
            resulting_assertion=assertion,
            actor=self.identity,
            actor_access_epoch=self.identity.access_epoch,
            authority_scope="create",
            event_authority_reference="event-resource-determination:sha256:" + "a" * 64,
            event_authority_evaluated_at=NOW,
            request_reference="request.models",
            consumer_reference="consumer.s011b",
            idempotency_key="models-create",
            payload_fingerprint="b" * 64,
            transaction_reference=uuid4(),
            occurred_at=NOW,
        )
        relationship.head_assertion = assertion
        relationship.save(update_fields=("head_assertion",))
        return relationship, assertion, transition

    def test_complete_initial_aggregate_and_typed_evidence(self):
        relationship, assertion, transition = self.create_aggregate()
        payload = b'{"result":"QUALIFIED"}'
        evidence = EventResourceRelationshipEvidence.objects.create(
            transition=transition,
            kind=EventResourceRelationshipEvidence.Kind.EVENT_AUTHORITY,
            schema_id="intevia.s011b.event-resource-relationship-determination",
            schema_version=1,
            canonicalization="RFC8785+INTEVIA-S011B-v1",
            result="QUALIFIED",
            determination_reference="event-resource-determination:sha256:" + "c" * 64,
            policy_reference="policy:EVENT-RESOURCE-RELATIONSHIP-AUTHORITY-PREALPHA-001:v1",
            canonical_payload=payload,
            payload_sha256=hashlib.sha256(payload).hexdigest(),
            actor_identity_id=self.identity.identity_id,
            actor_access_epoch=self.identity.access_epoch,
            request_reference="request.models",
            consumer_reference="consumer.s011b",
            evaluated_at=NOW,
        )
        self.assertEqual(relationship.head_assertion, assertion)
        self.assertEqual(transition.resulting_assertion, assertion)
        self.assertEqual(evidence.transition, transition)

    def test_rows_are_immutable_and_non_deletable(self):
        relationship, assertion, transition = self.create_aggregate()
        assertion.purpose = EventResourceAssertion.Purpose.REFERENCE
        with self.assertRaises(ValidationError):
            assertion.save()
        with self.assertRaises(ValidationError):
            transition.delete()
        relationship.relationship_id = "relationship.changed"
        with self.assertRaises(ValidationError):
            relationship.save()

    def test_head_integrity_is_guarded_not_schema_guaranteed(self):
        relationship = EventResourceRelationship.objects.create(
            relationship_id="relationship.null-head",
            event=self.event,
            library_resource=self.resource,
            created_at=NOW,
        )
        self.assertIsNone(relationship.head_assertion_id)
        self.assertEqual(
            EventResourceRelationship.objects.filter(
                pk=relationship.pk,
                head_assertion__isnull=True,
            ).count(),
            1,
        )

    def test_cross_lineage_head_direct_save_is_rejected(self):
        relationship, _, _ = self.create_aggregate()
        other_resource = LibraryResource.objects.create(
            resource_id="library.models.other",
            created_by=self.identity,
            state=LibraryResource.State.PUBLISHED,
            created_at=NOW,
        )
        other_version = LibraryResourceVersion.objects.create(
            resource=other_resource,
            version_number=1,
            content="other",
            created_by=self.identity,
            created_at=NOW,
        )
        other = EventResourceRelationship.objects.create(
            relationship_id="relationship.models.other",
            event=self.event,
            library_resource=other_resource,
            created_at=NOW,
        )
        other_assertion = EventResourceAssertion.objects.create(
            relationship=other,
            revision=1,
            library_resource_version=other_version,
            purpose=EventResourceAssertion.Purpose.REFERENCE,
            state=EventResourceAssertion.State.CURRENT,
            created_by=self.identity,
            actor_access_epoch=3,
            created_at=NOW,
        )
        relationship.head_assertion = other_assertion
        with self.assertRaises(ValidationError):
            relationship.save(update_fields=("head_assertion",))