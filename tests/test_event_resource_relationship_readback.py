from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone
import hashlib
from unittest.mock import Mock, patch
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
    EventAuthorityResult,
    EventRelationshipAction,
    EventRelationshipAuthorityEnvelope,
    EventRelationshipAuthorityTarget,
    ExistenceDisclosureResult,
    RelationshipDisclosureEnvelope,
    RelationshipDisclosureResult,
    RelationshipDisclosureBindingSnapshot,
    RelationshipPurpose,
    RelationshipState,
    VoidReason,
    authority_payload,
    determination_reference_for,
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
from src.intevia.services.event_resource_relationship_service import (
    EventResourceRelationshipService,
)
from src.intevia.services.library_exact_version_contract import (
    AuthorityResult,
    BasisCode,
    BindingDecision,
    BindingKind,
    BindingSnapshot,
    CANONICALIZATION,
    DeterminationKind,
    DeterminationPayload,
    DisclosureResult,
    LibraryAction,
    LibraryExactVersionContract,
    LinkabilityResult,
    POLICY_ENVIRONMENT,
    POLICY_REFERENCE,
    RevalidationBoundary,
    SCHEMA_ID,
    SCHEMA_VERSION,
    envelope_for,
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

    @staticmethod
    def canonical_time(value):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    def library_evidence(self, transition, kind):
        assertion = transition.resulting_assertion
        action = LibraryAction(transition.action)
        actor_axis = kind == EventResourceRelationshipEvidence.Kind.LIBRARY_AUTHORITY
        viewer_axis = (
            kind
            == EventResourceRelationshipEvidence.Kind.LIBRARY_DISCLOSURE_ELIGIBILITY
        )
        if actor_axis:
            determination_kind = DeterminationKind.AUTHORITY
            result = AuthorityResult.QUALIFIED
            basis = BasisCode.AUTHORITY_EXPLICIT_BINDING_QUALIFIED
            boundary = RevalidationBoundary.CONSEQUENTIAL_ACTION_SAME_TRANSACTION
            binding_kind = BindingKind.ACTION
            binding_reference = "lib-authority-binding:readback.action:v1"
        elif viewer_axis:
            determination_kind = DeterminationKind.DISCLOSURE
            result = DisclosureResult.CONTENT_VISIBLE
            basis = BasisCode.VIEWER_BOUND_PUBLISHED_CONTENT_VISIBLE
            boundary = RevalidationBoundary.READ_TIME_ONLY
            binding_kind = BindingKind.VIEWER
            binding_reference = "lib-authority-binding:readback.viewer:v1"
        else:
            determination_kind = DeterminationKind.LINKABILITY
            result = LinkabilityResult.LINKABLE
            basis = BasisCode.STATE_PUBLISHED_LINKABLE
            boundary = RevalidationBoundary.CURRENT_EVALUATION_ONLY
            binding_kind = None
            binding_reference = None
        payload = DeterminationPayload(
            action=action.value if actor_axis else None,
            actor_access_epoch=str(transition.actor_access_epoch) if actor_axis else None,
            actor_identity_id=str(transition.actor.identity_id) if actor_axis else None,
            authority_binding_reference=binding_reference if actor_axis else None,
            basis_code=basis.value,
            binding_kind=binding_kind.value if binding_kind else None,
            binding_reference=binding_reference,
            binding_version="1" if binding_reference else None,
            canonicalization=CANONICALIZATION,
            consumer_reference=transition.consumer_reference if actor_axis else None,
            determination_kind=determination_kind.value,
            environment=POLICY_ENVIRONMENT,
            evaluated_at=self.canonical_time(transition.occurred_at),
            policy_reference=POLICY_REFERENCE,
            provider_snapshot_reference=(
                "lib-binding-snapshot:sha256:" + "e" * 64
                if binding_reference
                else None
            ),
            request_reference=transition.request_reference if actor_axis else None,
            requested_at=self.canonical_time(transition.occurred_at) if actor_axis else None,
            resource_id=transition.relationship.library_resource.resource_id,
            resource_version_pk=str(assertion.library_resource_version_id),
            result=result.value,
            revalidation_boundary=boundary.value,
            schema_id=SCHEMA_ID,
            schema_version=SCHEMA_VERSION,
            source_state="PUBLISHED",
            unresolved_limitation_code=None,
            version_number=str(assertion.library_resource_version.version_number),
            viewer_access_epoch=(
                str(transition.actor_access_epoch) if viewer_axis else None
            ),
            viewer_identity_id=(
                str(transition.actor.identity_id) if viewer_axis else None
            ),
        )
        EventResourceRelationshipService._library_evidence(
            transition,
            kind,
            envelope_for(payload),
        )

    def evidence(self, transition, suffix):
        assertion = transition.resulting_assertion
        predecessor = transition.from_assertion
        action = EventRelationshipAction(transition.action)
        target = EventRelationshipAuthorityTarget(
            actor_identity_id=str(transition.actor.identity_id),
            actor_access_epoch=transition.actor_access_epoch,
            action=action,
            authority_scope=transition.authority_scope,
            event_id=transition.relationship.event.event_id,
            event_state=transition.relationship.event.state,
            relationship_id=(
                None
                if action is EventRelationshipAction.CREATE
                else transition.relationship.relationship_id
            ),
            current_assertion_id=getattr(predecessor, "pk", None),
            resource_id=transition.relationship.library_resource.resource_id,
            version_number=assertion.library_resource_version.version_number,
            current_purpose=(
                RelationshipPurpose(predecessor.purpose) if predecessor else None
            ),
            proposed_purpose=RelationshipPurpose(assertion.purpose),
            occurred_at=transition.occurred_at,
        )
        canonical = authority_payload(
            target,
            result=EventAuthorityResult.QUALIFIED,
            binding_reference=f"event-binding:evidence.{suffix}:v1",
            provider_snapshot_reference=f"event-snapshot:evidence.{suffix}:v1",
            evaluated_at=transition.occurred_at,
        )
        envelope = EventRelationshipAuthorityEnvelope(
            result=EventAuthorityResult.QUALIFIED,
            target=target,
            policy_reference="policy:EVENT-RESOURCE-RELATIONSHIP-AUTHORITY-PREALPHA-001:v1",
            binding_reference=f"event-binding:evidence.{suffix}:v1",
            provider_snapshot_reference=f"event-snapshot:evidence.{suffix}:v1",
            evaluated_at=transition.occurred_at,
            canonical_payload=canonical,
            determination_reference=determination_reference_for(canonical),
        )
        EventResourceRelationshipTransition.objects.filter(pk=transition.pk).update(
            event_authority_reference=envelope.determination_reference,
            event_authority_evaluated_at=transition.occurred_at,
        )
        transition.refresh_from_db()
        EventResourceRelationshipService._event_evidence(transition, envelope)
        if action in {
            EventRelationshipAction.CREATE,
            EventRelationshipAction.SUPERSEDE_VERSION,
        }:
            self.library_evidence(
                transition,
                EventResourceRelationshipEvidence.Kind.LIBRARY_AUTHORITY,
            )
            self.library_evidence(
                transition,
                EventResourceRelationshipEvidence.Kind.LIBRARY_LINKABILITY,
            )
        elif action is EventRelationshipAction.AMEND_PURPOSE:
            self.library_evidence(
                transition,
                EventResourceRelationshipEvidence.Kind.LIBRARY_AUTHORITY,
            )
            self.library_evidence(
                transition,
                EventResourceRelationshipEvidence.Kind.LIBRARY_DISCLOSURE_ELIGIBILITY,
            )
            disclosure = EventResourceRelationshipPolicyV1(
                authority_provider=ImmutableEventAuthorityBindingProvider(),
                disclosure_provider=ImmutableRelationshipDisclosureBindingProvider(
                    (self.event_binding(transition.relationship),),
                    enabled=True,
                    complete_for_policy=True,
                ),
            ).determine_disclosure(
                identity=self.viewer,
                event_id=self.event.event_id,
                relationship_id=transition.relationship.relationship_id,
                assertion_id=predecessor.pk,
                state=RelationshipState(predecessor.state),
                purpose=RelationshipPurpose(predecessor.purpose),
                evaluated_at=transition.occurred_at,
            )
            EventResourceRelationshipService._relationship_disclosure_evidence(
                transition,
                disclosure,
            )
        elif action is EventRelationshipAction.VOID:
            EventResourceRelationshipService._correction_evidence(
                transition,
                reason=VoidReason.WRONG_VERSION,
                survivor_relationship_id=None,
                survivor_assertion_id=None,
                rationale_reference=None,
                correction_evidence_reference=None,
                occurred_at=transition.occurred_at,
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

    def test_supersede_and_amend_canonical_evidence_reconstruct_latest_head(self):
        relationship, first, create_transition, first_version = self.relationship(
            "canonical-lifecycle"
        )
        second_version = LibraryResourceVersion.objects.create(
            resource=relationship.library_resource,
            version_number=2,
            content="Latest governed version",
            predecessor=first_version,
            created_by=self.viewer,
            created_at=NOW + timedelta(minutes=1),
        )
        superseded = EventResourceAssertion.objects.create(
            relationship=relationship,
            revision=2,
            predecessor=first,
            library_resource_version=second_version,
            purpose=first.purpose,
            state=EventResourceAssertion.State.CURRENT,
            created_by=self.viewer,
            actor_access_epoch=self.viewer.access_epoch,
            created_at=NOW + timedelta(minutes=1),
        )
        supersede_transition = EventResourceRelationshipTransition.objects.create(
            relationship=relationship,
            previous_transition=create_transition,
            sequence=2,
            action=EventResourceRelationshipTransition.Action.SUPERSEDE_VERSION,
            from_assertion=first,
            resulting_assertion=superseded,
            prior_disposition=EventResourceAssertion.State.SUPERSEDED,
            actor=self.viewer,
            actor_access_epoch=self.viewer.access_epoch,
            authority_scope="supersede_version",
            event_authority_reference="pending",
            event_authority_evaluated_at=NOW + timedelta(minutes=1),
            request_reference="request.canonical-lifecycle.supersede",
            consumer_reference="consumer.s011b",
            idempotency_key="key.canonical-lifecycle.supersede",
            payload_fingerprint="b" * 64,
            transaction_reference=uuid4(),
            occurred_at=NOW + timedelta(minutes=1),
        )
        self.evidence(supersede_transition, "canonical-lifecycle.supersede")
        amended = EventResourceAssertion.objects.create(
            relationship=relationship,
            revision=3,
            predecessor=superseded,
            library_resource_version=second_version,
            purpose=RelationshipPurpose.REFERENCE,
            state=EventResourceAssertion.State.CURRENT,
            created_by=self.viewer,
            actor_access_epoch=self.viewer.access_epoch,
            created_at=NOW + timedelta(minutes=2),
        )
        amend_transition = EventResourceRelationshipTransition.objects.create(
            relationship=relationship,
            previous_transition=supersede_transition,
            sequence=3,
            action=EventResourceRelationshipTransition.Action.AMEND_PURPOSE,
            from_assertion=superseded,
            resulting_assertion=amended,
            prior_disposition=EventResourceAssertion.State.CURRENT,
            actor=self.viewer,
            actor_access_epoch=self.viewer.access_epoch,
            authority_scope="amend_purpose",
            event_authority_reference="pending",
            event_authority_evaluated_at=NOW + timedelta(minutes=2),
            request_reference="request.canonical-lifecycle.amend",
            consumer_reference="consumer.s011b",
            idempotency_key="key.canonical-lifecycle.amend",
            payload_fingerprint="c" * 64,
            transaction_reference=uuid4(),
            occurred_at=NOW + timedelta(minutes=2),
        )
        self.evidence(amend_transition, "canonical-lifecycle.amend")
        EventResourceRelationship.objects.filter(pk=relationship.pk).update(
            head_assertion=amended
        )
        relationship.refresh_from_db()

        result = self.present(self.reader((relationship,)))

        self.assertEqual(
            result,
            (EventResourcePresentation("Latest governed version", "Reference"),),
        )
        self.assertNotEqual(first_version.pk, second_version.pk)

    def test_incomplete_malformed_and_digest_mismatched_evidence_omit_candidate(self):
        qualified, _, _, _ = self.relationship(
            "evidence-qualified", content="Qualified evidence sibling"
        )
        incomplete, _, incomplete_transition, _ = self.relationship(
            "evidence-incomplete"
        )
        incomplete_transition.evidence.filter(
            kind=EventResourceRelationshipEvidence.Kind.LIBRARY_LINKABILITY
        ).delete()
        malformed, _, malformed_transition, _ = self.relationship(
            "evidence-malformed"
        )
        malformed_transition.evidence.filter(
            kind=EventResourceRelationshipEvidence.Kind.EVENT_AUTHORITY
        ).update(canonical_payload=b"not-json")
        digest, _, digest_transition, _ = self.relationship("evidence-digest")
        digest_transition.evidence.filter(
            kind=EventResourceRelationshipEvidence.Kind.LIBRARY_AUTHORITY
        ).update(payload_sha256="0" * 64)

        result = self.present(
            self.reader((qualified, incomplete, malformed, digest))
        )

        self.assertEqual(
            result,
            (EventResourcePresentation("Qualified evidence sibling", "Preparation"),),
        )

    def test_wrong_action_scope_actor_epoch_policy_and_receipt_evidence_omit(self):
        qualified, _, _, _ = self.relationship(
            "binding-qualified", content="Qualified binding sibling"
        )
        corrupt = []
        for suffix, mutate in (
            (
                "wrong-action",
                lambda transition: EventResourceRelationshipTransition.objects.filter(
                    pk=transition.pk
                ).update(action=EventResourceRelationshipTransition.Action.RETIRE),
            ),
            (
                "wrong-scope",
                lambda transition: EventResourceRelationshipTransition.objects.filter(
                    pk=transition.pk
                ).update(authority_scope="wrong-scope"),
            ),
            (
                "wrong-actor",
                lambda transition: transition.evidence.filter(
                    kind=EventResourceRelationshipEvidence.Kind.EVENT_AUTHORITY
                ).update(actor_identity_id=uuid4()),
            ),
            (
                "wrong-epoch",
                lambda transition: transition.evidence.filter(
                    kind=EventResourceRelationshipEvidence.Kind.EVENT_AUTHORITY
                ).update(actor_access_epoch=transition.actor_access_epoch + 1),
            ),
            (
                "wrong-policy",
                lambda transition: transition.evidence.filter(
                    kind=EventResourceRelationshipEvidence.Kind.LIBRARY_AUTHORITY
                ).update(policy_reference="policy:wrong:v1"),
            ),
            (
                "wrong-receipt",
                lambda transition: transition.evidence.filter(
                    kind=EventResourceRelationshipEvidence.Kind.LIBRARY_AUTHORITY
                ).update(determination_reference="library-determination:sha256:" + "0" * 64),
            ),
        ):
            relationship, _, transition, _ = self.relationship(suffix)
            mutate(transition)
            corrupt.append(relationship)

        result = self.present(self.reader((qualified, *corrupt)))

        self.assertEqual(
            result,
            (EventResourcePresentation("Qualified binding sibling", "Preparation"),),
        )

    def test_stale_viewer_epoch_is_rejected_before_disclosure(self):
        relationship, _, _, _ = self.relationship("stale-viewer")
        stale = Identity.objects.get(pk=self.viewer.pk)
        Identity.objects.filter(pk=self.viewer.pk).update(access_epoch=12)
        reader = self.reader((relationship,))

        result = reader.present(viewer=stale, event=self.event, evaluated_at=NOW)

        self.assertEqual(result, ())

    def test_identity_epoch_change_between_disclosure_gates_omits(self):
        relationship, _, _, _ = self.relationship("epoch-between-gates")
        reader = self.reader((relationship,))
        original = reader.library_contract.determine_disclosure

        def disclose_then_change_epoch(**kwargs):
            envelope = original(**kwargs)
            Identity.objects.filter(pk=self.viewer.pk).update(access_epoch=12)
            return envelope

        with patch.object(
            reader.library_contract,
            "determine_disclosure",
            side_effect=disclose_then_change_epoch,
        ):
            result = self.present(reader)

        self.assertEqual(result, ())

    def test_credential_revocation_after_event_gate_omits(self):
        relationship, _, _, _ = self.relationship("revoked-between-gates")
        reader = self.reader((relationship,))
        original = reader.relationship_disclosure.determine_disclosure

        def disclose_then_revoke(**kwargs):
            envelope = original(**kwargs)
            User.objects.filter(pk=self.viewer.credential_id).update(is_active=False)
            return envelope

        with patch.object(
            reader.relationship_disclosure,
            "determine_disclosure",
            side_effect=disclose_then_revoke,
        ):
            result = self.present(reader)

        self.assertEqual(result, ())

    def test_asymmetric_library_viewer_receipt_omits_before_event_gate(self):
        relationship, _, _, _ = self.relationship("asymmetric-viewer")
        reader = self.reader((relationship,))
        original = reader.library_contract.determine_disclosure

        def disclosure_for_other_epoch(**kwargs):
            envelope = original(**kwargs)
            return replace(
                envelope,
                payload=replace(envelope.payload, viewer_access_epoch="999"),
            )

        with patch.object(
            reader.library_contract,
            "determine_disclosure",
            side_effect=disclosure_for_other_epoch,
        ), patch.object(
            reader.relationship_disclosure,
            "determine_disclosure",
            wraps=reader.relationship_disclosure.determine_disclosure,
        ) as event_disclosure:
            result = self.present(reader)

        self.assertEqual(result, ())
        event_disclosure.assert_not_called()

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
        evidence = transition.evidence.get(
            kind=EventResourceRelationshipEvidence.Kind.EVENT_AUTHORITY
        )
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
