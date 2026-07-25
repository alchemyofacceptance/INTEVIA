from dataclasses import replace
from datetime import datetime, timedelta, timezone

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
from src.intevia.services.event_resource_relationship_contract import (
    BindingDecision as EventBindingDecision,
    EventAuthorityBindingSnapshot,
    EventRelationshipAction,
    RelationshipDisclosureBindingSnapshot,
    RelationshipPurpose,
    VoidReason,
)
from src.intevia.services.event_resource_relationship_policy import (
    EventResourceRelationshipPolicyV1,
    ImmutableEventAuthorityBindingProvider,
    ImmutableRelationshipDisclosureBindingProvider,
)
from src.intevia.services.event_resource_relationship_service import (
    EventResourceRelationshipHold,
    EventResourceRelationshipIdempotencyConflict,
    EventResourceRelationshipRefused,
    EventResourceRelationshipService,
)
from src.intevia.services.library_exact_version_contract import (
    BindingDecision,
    BindingKind,
    BindingSnapshot,
    LibraryAction,
    LibraryExactVersionContract,
    LibraryRequestContext,
    POLICY_ENVIRONMENT,
    POLICY_REFERENCE,
)
from src.intevia.services.library_exact_version_policy import (
    ImmutableLibraryBindingProvider,
    LibraryExactVersionPolicy,
)


NOW = datetime(2026, 7, 25, 18, 0, tzinfo=timezone.utc)


class EventResourceRelationshipServiceTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username="s011b-service")
        self.identity = Identity.objects.create(
            credential=user,
            access_state=Identity.AccessState.ACTIVE,
            access_epoch=9,
        )
        self.event = Event.objects.create(
            event_id="event.service",
            title="Service Event",
            description="S011-B service guardians",
            owner=self.identity,
            state=Event.State.DRAFT,
            created_at=NOW,
        )
        self.resource = LibraryResource.objects.create(
            resource_id="library.service",
            created_by=self.identity,
            state=LibraryResource.State.PUBLISHED,
            created_at=NOW,
        )
        self.version = LibraryResourceVersion.objects.create(
            resource=self.resource,
            version_number=1,
            content="service content",
            created_by=self.identity,
            created_at=NOW,
        )
        self.context = LibraryRequestContext(
            request_reference="request.service",
            consumer_reference="consumer.s011b",
            authority_binding_reference="lib-authority-binding:service.create:v1",
            policy_reference=POLICY_REFERENCE,
            requested_at=NOW,
        )

    def library_binding(
        self,
        action=LibraryAction.CREATE,
        *,
        viewer=False,
        version_number=1,
    ):
        return BindingSnapshot(
            binding_reference=self.context.authority_binding_reference,
            binding_version="1",
            policy_reference=POLICY_REFERENCE,
            environment=POLICY_ENVIRONMENT,
            binding_kind=BindingKind.VIEWER if viewer else BindingKind.ACTION,
            subject_identity_id=str(self.identity.identity_id),
            enabled=True,
            effective_at=NOW - timedelta(hours=1),
            expires_at=NOW + timedelta(hours=1),
            revoked_at=None,
            superseding_binding_reference=None,
            provider_snapshot_reference="lib-binding-snapshot:sha256:" + "a" * 64,
            decision=BindingDecision.ALLOW,
            action=None if viewer else action,
            resource_id=None if viewer else self.resource.resource_id,
            version_number=None if viewer else str(version_number),
            viewer_scope="LIBRARY_EXACT_VERSION_CONTENT" if viewer else None,
        )

    def event_binding(self, action=EventRelationshipAction.CREATE, scope=None):
        return EventAuthorityBindingSnapshot(
            binding_reference="event-binding:service:create:v1",
            binding_version=1,
            subject_identity_id=str(self.identity.identity_id),
            action=action,
            authority_scope=scope or action.value.lower(),
            event_id=self.event.event_id,
            enabled=True,
            effective_at=NOW - timedelta(hours=1),
            expires_at=NOW + timedelta(hours=1),
            revoked_at=None,
            superseding_binding_reference=None,
            provider_snapshot_reference="event-snapshot:service:create:v1",
            decision=EventBindingDecision.ALLOW,
        )

    def disclosure_binding(self):
        return RelationshipDisclosureBindingSnapshot(
            binding_reference="event-binding:service:disclosure:v1",
            binding_version=1,
            subject_identity_id=str(self.identity.identity_id),
            event_id=self.event.event_id,
            relationship_id="relationship.service",
            enabled=True,
            effective_at=NOW - timedelta(hours=1),
            expires_at=NOW + timedelta(hours=1),
            revoked_at=None,
            superseding_binding_reference=None,
            provider_snapshot_reference="event-snapshot:service:disclosure:v1",
            decision=EventBindingDecision.ALLOW,
        )

    def service(
        self,
        *,
        library_bindings=None,
        event_bindings=None,
        disclosure_bindings=(),
        event_available=True,
    ):
        library_provider = ImmutableLibraryBindingProvider(
            (self.library_binding(),) if library_bindings is None else library_bindings,
            enabled=True,
            complete_for_policy=True,
        )
        event_provider = ImmutableEventAuthorityBindingProvider(
            (self.event_binding(),) if event_bindings is None else event_bindings,
            enabled=True,
            complete_for_policy=True,
            available=event_available,
        )
        event_policy = EventResourceRelationshipPolicyV1(
            authority_provider=event_provider,
            disclosure_provider=ImmutableRelationshipDisclosureBindingProvider(
                disclosure_bindings,
                enabled=True,
                complete_for_policy=True,
            ),
        )
        return EventResourceRelationshipService(
            library_contract=LibraryExactVersionContract(
                policy=LibraryExactVersionPolicy(provider=library_provider)
            ),
            event_authority=event_policy,
            relationship_disclosure=event_policy,
        )

    def create(self, service=None, **overrides):
        values = {
            "relationship_id": "relationship.service",
            "event_id": self.event.event_id,
            "resource_id": self.resource.resource_id,
            "version_number": 1,
            "purpose": RelationshipPurpose.PREPARATION,
            "actor_identity_id": self.identity.identity_id,
            "authority_scope": "create",
            "library_context": self.context,
            "idempotency_key": "service-create",
            "occurred_at": NOW,
        }
        values.update(overrides)
        return (service or self.service()).create(**values)

    def test_create_appends_complete_aggregate_and_three_evidence_axes(self):
        transition = self.create()

        relationship = transition.relationship
        self.assertEqual(relationship.head_assertion_id, transition.resulting_assertion_id)
        self.assertEqual(relationship.head_assertion.revision, 1)
        self.assertEqual(relationship.head_assertion.purpose, RelationshipPurpose.PREPARATION)
        self.assertEqual(
            set(transition.evidence.values_list("kind", flat=True)),
            {
                EventResourceRelationshipEvidence.Kind.EVENT_AUTHORITY,
                EventResourceRelationshipEvidence.Kind.LIBRARY_AUTHORITY,
                EventResourceRelationshipEvidence.Kind.LIBRARY_LINKABILITY,
            },
        )
        self.assertEqual(transition.actor_id, self.identity.pk)
        self.assertEqual(transition.actor_access_epoch, self.identity.access_epoch)

    def test_create_exact_replay_and_conflicting_payload(self):
        first = self.create()
        replay = self.create()
        self.assertEqual(replay.pk, first.pk)
        with self.assertRaises(EventResourceRelationshipIdempotencyConflict):
            self.create(purpose=RelationshipPurpose.REFERENCE)
        self.assertEqual(EventResourceRelationshipTransition.objects.count(), 1)

    def test_create_unavailable_event_authority_rolls_back_every_row(self):
        service = self.service(event_available=False)
        with self.assertRaises(EventResourceRelationshipHold):
            self.create(service)
        self.assertEqual(EventResourceRelationship.objects.count(), 0)
        self.assertEqual(EventResourceAssertion.objects.count(), 0)
        self.assertEqual(EventResourceRelationshipTransition.objects.count(), 0)
        self.assertEqual(EventResourceRelationshipEvidence.objects.count(), 0)

    def successor_version(self):
        return LibraryResourceVersion.objects.create(
            resource=self.resource,
            version_number=2,
            predecessor=self.version,
            content="successor content",
            created_by=self.identity,
            created_at=NOW,
        )

    def command_values(self, action, **overrides):
        values = {
            "relationship_id": "relationship.service",
            "event_id": self.event.event_id,
            "actor_identity_id": self.identity.identity_id,
            "authority_scope": action.value.lower(),
            "idempotency_key": "service-" + action.value.lower(),
            "occurred_at": NOW,
        }
        values.update(overrides)
        return values

    def test_supersede_appends_current_successor_and_three_evidence_axes(self):
        self.create()
        successor = self.successor_version()
        service = self.service(
            library_bindings=(
                self.library_binding(
                    LibraryAction.SUPERSEDE_VERSION,
                    version_number=2,
                ),
            ),
            event_bindings=(self.event_binding(EventRelationshipAction.SUPERSEDE_VERSION),),
        )
        transition = service.supersede_version(
            **self.command_values(EventRelationshipAction.SUPERSEDE_VERSION),
            resource_id=self.resource.resource_id,
            version_number=2,
            library_context=self.context,
        )
        self.assertEqual(transition.resulting_assertion.state, EventResourceAssertion.State.CURRENT)
        self.assertEqual(transition.resulting_assertion.library_resource_version, successor)
        self.assertEqual(
            set(transition.evidence.values_list("kind", flat=True)),
            {
                EventResourceRelationshipEvidence.Kind.EVENT_AUTHORITY,
                EventResourceRelationshipEvidence.Kind.LIBRARY_AUTHORITY,
                EventResourceRelationshipEvidence.Kind.LIBRARY_LINKABILITY,
            },
        )

    def test_amend_deprecated_non_linkable_but_visible_succeeds_without_linkability_evidence(self):
        self.create()
        self.resource.state = LibraryResource.State.DEPRECATED
        self.resource.save(update_fields=("state",))
        service = self.service(
            library_bindings=(
                self.library_binding(LibraryAction.AMEND_PURPOSE),
                self.library_binding(viewer=True),
            ),
            event_bindings=(self.event_binding(EventRelationshipAction.AMEND_PURPOSE),),
            disclosure_bindings=(self.disclosure_binding(),),
        )
        transition = service.amend_purpose(
            **self.command_values(EventRelationshipAction.AMEND_PURPOSE),
            resource_id=self.resource.resource_id,
            version_number=1,
            purpose=RelationshipPurpose.REFERENCE,
            library_context=self.context,
        )
        self.assertEqual(transition.resulting_assertion.library_resource_version, self.version)
        self.assertEqual(transition.resulting_assertion.purpose, RelationshipPurpose.REFERENCE)
        kinds = set(transition.evidence.values_list("kind", flat=True))
        self.assertNotIn(EventResourceRelationshipEvidence.Kind.LIBRARY_LINKABILITY, kinds)
        self.assertEqual(
            kinds,
            {
                EventResourceRelationshipEvidence.Kind.EVENT_AUTHORITY,
                EventResourceRelationshipEvidence.Kind.LIBRARY_AUTHORITY,
                EventResourceRelationshipEvidence.Kind.LIBRARY_DISCLOSURE_ELIGIBILITY,
                EventResourceRelationshipEvidence.Kind.RELATIONSHIP_DISCLOSURE_ELIGIBILITY,
            },
        )

    def test_retire_is_terminal_preserves_target_and_uses_no_library_determination(self):
        self.create()
        service = self.service(
            library_bindings=(),
            event_bindings=(self.event_binding(EventRelationshipAction.RETIRE),),
        )
        transition = service.retire(
            **self.command_values(EventRelationshipAction.RETIRE),
            request_reference="request.retire",
        )
        self.assertEqual(transition.resulting_assertion.state, EventResourceAssertion.State.RETIRED)
        self.assertEqual(transition.resulting_assertion.library_resource_version, self.version)
        self.assertEqual(transition.resulting_assertion.purpose, RelationshipPurpose.PREPARATION)
        self.assertEqual(
            list(transition.evidence.values_list("kind", flat=True)),
            [EventResourceRelationshipEvidence.Kind.EVENT_AUTHORITY],
        )

    def test_void_records_event_and_correction_evidence_and_replays(self):
        self.create()
        service = self.service(
            library_bindings=(),
            event_bindings=(self.event_binding(EventRelationshipAction.VOID),),
        )
        values = self.command_values(EventRelationshipAction.VOID)
        first = service.void(
            **values,
            reason=VoidReason.DUPLICATE_ASSERTION,
            survivor_relationship_id="relationship.survivor",
            request_reference="request.void",
        )
        replay = service.void(
            **values,
            reason=VoidReason.DUPLICATE_ASSERTION,
            survivor_relationship_id="relationship.survivor",
            request_reference="request.void",
        )
        self.assertEqual(replay.pk, first.pk)
        self.assertEqual(first.resulting_assertion.state, EventResourceAssertion.State.VOIDED)
        self.assertEqual(
            set(first.evidence.values_list("kind", flat=True)),
            {
                EventResourceRelationshipEvidence.Kind.EVENT_AUTHORITY,
                EventResourceRelationshipEvidence.Kind.CORRECTION,
            },
        )
        with self.assertRaises(EventResourceRelationshipIdempotencyConflict):
            service.void(
                **values,
                reason=VoidReason.DUPLICATE_ASSERTION,
                survivor_relationship_id="relationship.other-survivor",
                request_reference="request.void",
            )

    def test_void_rejects_ordinary_wrong_purpose_and_unbounded_other(self):
        self.create()
        service = self.service(
            library_bindings=(),
            event_bindings=(self.event_binding(EventRelationshipAction.VOID),),
        )
        with self.assertRaises(EventResourceRelationshipRefused):
            service.void(
                **self.command_values(EventRelationshipAction.VOID),
                reason=VoidReason.WRONG_PURPOSE,
                request_reference="request.void",
            )
        with self.assertRaisesMessage(ValidationError, "OTHER correction requires"):
            service.void(
                **self.command_values(EventRelationshipAction.VOID),
                reason=VoidReason.OTHER_GOVERNED_CORRECTION,
                request_reference="request.void",
            )
        with self.assertRaisesMessage(ValidationError, "NO_SURVIVOR"):
            service.void(
                **self.command_values(EventRelationshipAction.VOID),
                reason=VoidReason.DUPLICATE_ASSERTION,
                survivor_relationship_id="NO_SURVIVOR",
                request_reference="request.void",
            )

    def test_archived_void_requires_historical_correction_and_rolls_back_on_hold(self):
        self.create()
        self.event.state = Event.State.ARCHIVED
        self.event.save(update_fields=("state",))
        service = self.service(library_bindings=(), event_available=False)
        with self.assertRaises(EventResourceRelationshipRefused):
            service.void(
                **self.command_values(EventRelationshipAction.VOID),
                reason=VoidReason.WRONG_VERSION,
                request_reference="request.void",
            )
        historical = replace(
            self.event_binding(EventRelationshipAction.VOID),
            authority_scope="historical_correction",
        )
        service = self.service(
            library_bindings=(),
            event_bindings=(historical,),
            event_available=False,
        )
        with self.assertRaises(EventResourceRelationshipHold):
            service.void(
                **self.command_values(
                    EventRelationshipAction.VOID,
                    authority_scope="historical_correction",
                ),
                reason=VoidReason.WRONG_VERSION,
                request_reference="request.void.historical",
            )
        self.assertEqual(EventResourceAssertion.objects.count(), 1)
        self.assertEqual(EventResourceRelationshipTransition.objects.count(), 1)
