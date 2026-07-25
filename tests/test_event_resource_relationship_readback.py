from dataclasses import fields
from datetime import datetime, timedelta, timezone
import hashlib
from unittest.mock import Mock
from uuid import uuid4

from django.contrib.auth.models import User
from django.db import connection
from django.template.loader import render_to_string
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
from src.intevia.services.event_read_service import (
    EventNotVisible,
    EventReadService,
)
from src.intevia.services.event_resource_relationship_contract import (
    BindingDecision as EventBindingDecision,
    RelationshipDisclosureBindingSnapshot,
    RelationshipPurpose,
)
from src.intevia.services.event_resource_relationship_policy import (
    EventResourceRelationshipPolicyV1,
    ImmutableEventAuthorityBindingProvider,
    ImmutableRelationshipDisclosureBindingProvider,
)
from src.intevia.services.event_resource_relationship_read_service import (
    EventResourcePresentation,
    EventResourceRelationshipReadService,
)
from src.intevia.services.library_exact_version_contract import (
    BindingDecision,
    BindingKind,
    BindingSnapshot,
    LibraryExactVersionContract,
    POLICY_ENVIRONMENT,
    POLICY_REFERENCE,
)
from src.intevia.services.library_exact_version_policy import (
    ImmutableLibraryBindingProvider,
    LibraryExactVersionPolicy,
)


NOW = datetime(2026, 7, 25, 20, 0, tzinfo=timezone.utc)


class EventResourceRelationshipReadbackTests(TestCase):
    def setUp(self):
        credential = User.objects.create_user(username="s011b-readback")
        self.viewer = Identity.objects.create(
            credential=credential,
            display_name="S011-B reader",
            access_state=Identity.AccessState.ACTIVE,
            access_epoch=11,
        )
        self.event = Event.objects.create(
            event_id="event.s011b.readback",
            title="S011-B readback",
            description="Governed readback",
            owner=self.viewer,
            state=Event.State.PUBLISHED,
            created_at=NOW,
        )

    def evidence(self, transition, suffix):
        payload = f'{{"private_evidence":"{suffix}"}}'.encode()
        return EventResourceRelationshipEvidence.objects.create(
            transition=transition,
            kind=EventResourceRelationshipEvidence.Kind.EVENT_AUTHORITY,
            schema_id="intevia.s011b.test-evidence",
            schema_version=1,
            canonicalization="test-only",
            result="QUALIFIED",
            determination_reference=f"determination.private.{suffix}",
            policy_reference=f"policy.private.{suffix}",
            authority_binding_reference=f"binding.private.{suffix}",
            provider_snapshot_reference=f"snapshot.private.{suffix}",
            canonical_payload=payload,
            payload_sha256=hashlib.sha256(payload).hexdigest(),
            actor_identity_id=self.viewer.identity_id,
            actor_access_epoch=self.viewer.access_epoch,
            request_reference=f"evidence.request.{suffix}",
            consumer_reference="consumer.s011b",
            evaluated_at=NOW,
        )

    def relationship(
        self,
        suffix,
        *,
        purpose=RelationshipPurpose.PREPARATION,
        content=None,
        head=True,
        with_evidence=True,
    ):
        resource = LibraryResource.objects.create(
            resource_id=f"library.s011b.{suffix}",
            created_by=self.viewer,
            state=LibraryResource.State.PUBLISHED,
            created_at=NOW,
        )
        version = LibraryResourceVersion.objects.create(
            resource=resource,
            version_number=1,
            content=content or f"Qualified content {suffix}",
            created_by=self.viewer,
            created_at=NOW,
        )
        relationship = EventResourceRelationship.objects.create(
            relationship_id=f"relationship.s011b.{suffix}",
            event=self.event,
            library_resource=resource,
            created_at=NOW,
        )
        assertion = EventResourceAssertion.objects.create(
            relationship=relationship,
            revision=1,
            library_resource_version=version,
            purpose=purpose,
            state=EventResourceAssertion.State.CURRENT,
            created_by=self.viewer,
            actor_access_epoch=self.viewer.access_epoch,
            created_at=NOW,
        )
        transition = EventResourceRelationshipTransition.objects.create(
            relationship=relationship,
            sequence=1,
            action=EventResourceRelationshipTransition.Action.CREATE,
            resulting_assertion=assertion,
            actor=self.viewer,
            actor_access_epoch=self.viewer.access_epoch,
            authority_scope="create",
            event_authority_reference=f"event-authority:{suffix}",
            event_authority_evaluated_at=NOW,
            request_reference=f"request.{suffix}",
            consumer_reference="consumer.s011b",
            idempotency_key=f"key.{suffix}",
            payload_fingerprint="a" * 64,
            transaction_reference=uuid4(),
            occurred_at=NOW,
        )
        if with_evidence:
            self.evidence(transition, suffix)
        if head:
            EventResourceRelationship.objects.filter(pk=relationship.pk).update(
                head_assertion=assertion
            )
            relationship.refresh_from_db()
        return relationship, assertion, transition, version

    def terminal_relationship(self, suffix, action):
        relationship, first, first_transition, version = self.relationship(suffix)
        state = {
            EventResourceRelationshipTransition.Action.RETIRE:
                EventResourceAssertion.State.RETIRED,
            EventResourceRelationshipTransition.Action.VOID:
                EventResourceAssertion.State.VOIDED,
        }[action]
        terminal = EventResourceAssertion.objects.create(
            relationship=relationship,
            revision=2,
            predecessor=first,
            library_resource_version=version,
            purpose=first.purpose,
            state=state,
            created_by=self.viewer,
            actor_access_epoch=self.viewer.access_epoch,
            created_at=NOW + timedelta(minutes=1),
        )
        terminal_transition = EventResourceRelationshipTransition.objects.create(
            relationship=relationship,
            previous_transition=first_transition,
            sequence=2,
            action=action,
            from_assertion=first,
            resulting_assertion=terminal,
            prior_disposition=EventResourceAssertion.State.CURRENT,
            actor=self.viewer,
            actor_access_epoch=self.viewer.access_epoch,
            authority_scope=action.lower(),
            event_authority_reference=f"event-authority:{suffix}:terminal",
            event_authority_evaluated_at=NOW,
            request_reference=f"request.{suffix}.terminal",
            consumer_reference="consumer.s011b",
            idempotency_key=f"key.{suffix}.terminal",
            payload_fingerprint="b" * 64,
            transaction_reference=uuid4(),
            occurred_at=NOW + timedelta(minutes=1),
        )
        self.evidence(terminal_transition, f"{suffix}.terminal")
        EventResourceRelationship.objects.filter(pk=relationship.pk).update(
            head_assertion=terminal
        )
        relationship.refresh_from_db()
        return relationship

    def library_binding(self, *, decision=BindingDecision.ALLOW):
        return BindingSnapshot(
            binding_reference="lib-authority-binding:s011b.readback.viewer:v1",
            binding_version="1",
            policy_reference=POLICY_REFERENCE,
            environment=POLICY_ENVIRONMENT,
            binding_kind=BindingKind.VIEWER,
            subject_identity_id=str(self.viewer.identity_id),
            enabled=True,
            effective_at=NOW - timedelta(hours=1),
            expires_at=NOW + timedelta(hours=1),
            revoked_at=None,
            superseding_binding_reference=None,
            provider_snapshot_reference="lib-binding-snapshot:sha256:" + "c" * 64,
            decision=decision,
            action=None,
            resource_id=None,
            version_number=None,
            viewer_scope="LIBRARY_EXACT_VERSION_CONTENT",
        )

    def event_binding(self, relationship, *, decision=EventBindingDecision.ALLOW):
        return RelationshipDisclosureBindingSnapshot(
            binding_reference=f"event-binding:readback:{relationship.pk}:v1",
            binding_version=1,
            subject_identity_id=str(self.viewer.identity_id),
            event_id=self.event.event_id,
            relationship_id=relationship.relationship_id,
            enabled=True,
            effective_at=NOW - timedelta(hours=1),
            expires_at=NOW + timedelta(hours=1),
            revoked_at=None,
            superseding_binding_reference=None,
            provider_snapshot_reference=f"event-snapshot:readback:{relationship.pk}:v1",
            decision=decision,
        )

    def reader(
        self,
        relationships=(),
        *,
        library_decision=BindingDecision.ALLOW,
        library_available=True,
        event_available=True,
    ):
        library_provider = ImmutableLibraryBindingProvider(
            (self.library_binding(decision=library_decision),),
            enabled=True,
            complete_for_policy=True,
            available=library_available,
        )
        event_provider = ImmutableRelationshipDisclosureBindingProvider(
            tuple(self.event_binding(item) for item in relationships),
            enabled=True,
            complete_for_policy=True,
            available=event_available,
        )
        return EventResourceRelationshipReadService(
            library_contract=LibraryExactVersionContract(
                policy=LibraryExactVersionPolicy(provider=library_provider)
            ),
            relationship_disclosure=EventResourceRelationshipPolicyV1(
                authority_provider=ImmutableEventAuthorityBindingProvider(),
                disclosure_provider=event_provider,
            ),
        )

    def present(self, reader):
        return reader.present(viewer=self.viewer, event=self.event, evaluated_at=NOW)

    def test_positive_projection_is_content_and_exact_purpose_only(self):
        relationship, _, _, _ = self.relationship(
            "during",
            purpose=RelationshipPurpose.DURING_EVENT,
            content="Visible governed handout",
        )
        result = self.present(self.reader((relationship,)))

        self.assertEqual(
            result,
            (EventResourcePresentation("Visible governed handout", "During the Event"),),
        )
        self.assertEqual([field.name for field in fields(EventResourcePresentation)], ["content", "purpose"])

    def test_library_hidden_and_hold_both_omit(self):
        relationship, _, _, _ = self.relationship("library-negative")
        for label, reader in (
            ("hidden", self.reader((relationship,), library_decision=BindingDecision.DENY)),
            ("hold", self.reader((relationship,), library_available=False)),
        ):
            with self.subTest(label=label):
                self.assertEqual(self.present(reader), ())

    def test_event_hidden_and_hold_both_omit(self):
        relationship, _, _, _ = self.relationship("event-negative")
        for label, reader in (
            ("hidden", self.reader(())),
            ("hold", self.reader((relationship,), event_available=False)),
        ):
            with self.subTest(label=label):
                self.assertEqual(self.present(reader), ())

    def test_mixed_candidates_filter_before_projection(self):
        visible, _, _, _ = self.relationship("mixed-visible", content="Only qualified")
        self.relationship("mixed-hidden", content="Must not appear")

        result = self.present(self.reader((visible,)))

        self.assertEqual(result, (EventResourcePresentation("Only qualified", "Preparation"),))

    def test_corrupt_candidates_collapse_without_affecting_qualified_sibling(self):
        qualified, _, _, _ = self.relationship("qualified", content="Qualified sibling")
        corrupt, _, corrupt_transition, _ = self.relationship("corrupt-sequence")
        null_head, _, _, _ = self.relationship("null-head", head=False)
        cross_chain, _, cross_transition, _ = self.relationship("cross-chain")
        donor, donor_assertion, donor_transition, _ = self.relationship(
            "cross-chain-donor", head=False, with_evidence=False
        )
        head_mismatch, _, _, _ = self.relationship("head-mismatch")
        head_donor, head_donor_assertion, _, _ = self.relationship("head-donor", head=False)

        EventResourceRelationshipTransition.objects.filter(pk=corrupt_transition.pk).update(sequence=2)
        EventResourceRelationshipTransition.objects.filter(pk=donor_transition.pk).delete()
        EventResourceRelationshipTransition.objects.filter(pk=cross_transition.pk).update(
            resulting_assertion=donor_assertion
        )
        EventResourceRelationship.objects.filter(pk=head_mismatch.pk).update(
            head_assertion=head_donor_assertion
        )
        candidates = (
            qualified,
            corrupt,
            null_head,
            cross_chain,
            donor,
            head_mismatch,
            head_donor,
        )

        result = self.present(self.reader(candidates))

        self.assertEqual(result, (EventResourcePresentation("Qualified sibling", "Preparation"),))

    def test_raw_sql_contradictory_head_collapses_without_output(self):
        relationship, _, _, _ = self.relationship("raw-sql-head")
        _, donor_assertion, _, _ = self.relationship("raw-sql-donor", head=False)
        table_name = connection.ops.quote_name(
            EventResourceRelationship._meta.db_table
        )
        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {table_name} SET head_assertion_id = %s WHERE id = %s",
                (donor_assertion.pk, relationship.pk),
            )
        relationship.refresh_from_db()

        self.assertEqual(self.present(self.reader((relationship,))), ())

    def test_terminal_heads_are_omitted(self):
        retired = self.terminal_relationship(
            "retired", EventResourceRelationshipTransition.Action.RETIRE
        )
        voided = self.terminal_relationship(
            "voided", EventResourceRelationshipTransition.Action.VOID
        )
        self.assertEqual(self.present(self.reader((retired, voided))), ())

    def test_event_read_service_defaults_empty_and_checks_visibility_before_reader(self):
        presented = EventReadService.present_event(self.viewer, self.event.event_id)
        self.assertEqual(presented.resources, ())

        other_user = User.objects.create_user(username="s011b-hidden-reader")
        other = Identity.objects.create(
            credential=other_user,
            access_state=Identity.AccessState.ACTIVE,
        )
        reader = Mock()
        with self.assertRaises(EventNotVisible):
            EventReadService.present_event(
                other,
                self.event.event_id,
                resource_reader=reader,
                evaluated_at=NOW,
            )
        reader.present.assert_not_called()

    def test_template_has_no_empty_placeholder_and_renders_only_public_projection(self):
        empty = EventReadService.present_event(self.viewer, self.event.event_id)
        empty_body = render_to_string("core/event_detail.html", {"event": empty})
        self.assertNotIn("Event resources", empty_body)
        self.assertNotIn("unavailable", empty_body.lower())

        relationship, _, transition, version = self.relationship(
            "rendered",
            purpose=RelationshipPurpose.DURING_EVENT,
            content="Public resource content",
        )
        presented = EventReadService.present_event(
            self.viewer,
            self.event.event_id,
            resource_reader=self.reader((relationship,)),
            evaluated_at=NOW,
        )
        body = render_to_string("core/event_detail.html", {"event": presented})
        evidence = transition.evidence.get()
        self.assertIn("Event resources", body)
        self.assertIn("During the Event", body)
        self.assertIn("Public resource content", body)
        for private_value in (
            relationship.relationship_id,
            relationship.library_resource.resource_id,
            "version 1",
            "version_number",
            EventResourceAssertion.State.CURRENT,
            str(self.viewer.identity_id),
            transition.event_authority_reference,
            transition.request_reference,
            transition.idempotency_key,
            evidence.determination_reference,
            evidence.policy_reference,
            bytes(evidence.canonical_payload).decode(),
            POLICY_REFERENCE,
            "relationship history",
        ):
            self.assertNotIn(private_value, body.lower() if private_value.islower() else body)
