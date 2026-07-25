from datetime import datetime, timedelta, timezone
import re
from threading import Event as ThreadEvent, Thread
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext

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
)
from src.intevia.services.event_resource_relationship_policy import (
    EventResourceRelationshipPolicyV1,
    ImmutableEventAuthorityBindingProvider,
    ImmutableRelationshipDisclosureBindingProvider,
)
from src.intevia.services.event_resource_relationship_service import (
    EventResourceRelationshipHold,
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


NOW = datetime(2026, 7, 25, 19, 0, tzinfo=timezone.utc)


class EventResourceRelationshipPostgreSQLTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        if connection.vendor != "postgresql":
            self.fail("mandatory S011-B PostgreSQL guardians require PostgreSQL")
        user = User.objects.create_user(username="s011b-postgresql")
        self.identity = Identity.objects.create(
            credential=user,
            access_state=Identity.AccessState.ACTIVE,
            access_epoch=9,
        )
        self.event = Event.objects.create(
            event_id="event.s011b.postgresql",
            title="S011-B PostgreSQL",
            description="S011-B transaction guardians",
            owner=self.identity,
            state=Event.State.DRAFT,
            created_at=NOW,
        )
        self.resource = LibraryResource.objects.create(
            resource_id="library.s011b.postgresql",
            created_by=self.identity,
            state=LibraryResource.State.PUBLISHED,
            created_at=NOW,
        )
        self.version = LibraryResourceVersion.objects.create(
            resource=self.resource,
            version_number=1,
            content="S011-B PostgreSQL governed content",
            created_by=self.identity,
            created_at=NOW,
        )
        create_context = self.library_context(LibraryAction.CREATE)
        create_service = self.service(
            action=EventRelationshipAction.CREATE,
            library_context=create_context,
        )
        self.create_transition = create_service.create(
            relationship_id="relationship.s011b.postgresql",
            event_id=self.event.event_id,
            resource_id=self.resource.resource_id,
            version_number=1,
            purpose=RelationshipPurpose.PREPARATION,
            actor_identity_id=self.identity.identity_id,
            authority_scope="create",
            library_context=create_context,
            idempotency_key="s011b-postgresql-create",
            occurred_at=NOW,
        )

    @staticmethod
    def binding_reference(action: LibraryAction) -> str:
        return f"lib-authority-binding:s011b.postgresql.{action.value.lower()}:v1"

    def library_context(self, action: LibraryAction) -> LibraryRequestContext:
        return LibraryRequestContext(
            request_reference=f"request.s011b.postgresql.{action.value.lower()}",
            consumer_reference="consumer.s011b",
            authority_binding_reference=self.binding_reference(action),
            policy_reference=POLICY_REFERENCE,
            requested_at=NOW,
        )

    def library_binding(
        self,
        action: LibraryAction,
        *,
        viewer: bool = False,
    ) -> BindingSnapshot:
        return BindingSnapshot(
            binding_reference=(
                "lib-authority-binding:s011b.postgresql.viewer:v1"
                if viewer
                else self.binding_reference(action)
            ),
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
            provider_snapshot_reference="lib-binding-snapshot:sha256:" + "b" * 64,
            decision=BindingDecision.ALLOW,
            action=None if viewer else action,
            resource_id=None if viewer else self.resource.resource_id,
            version_number=None if viewer else "1",
            viewer_scope="LIBRARY_EXACT_VERSION_CONTENT" if viewer else None,
        )

    def event_binding(
        self,
        action: EventRelationshipAction,
    ) -> EventAuthorityBindingSnapshot:
        return EventAuthorityBindingSnapshot(
            binding_reference=f"event-binding:s011b.postgresql.{action.value.lower()}:v1",
            binding_version=1,
            subject_identity_id=str(self.identity.identity_id),
            action=action,
            authority_scope=action.value.lower(),
            event_id=self.event.event_id,
            enabled=True,
            effective_at=NOW - timedelta(hours=1),
            expires_at=NOW + timedelta(hours=1),
            revoked_at=None,
            superseding_binding_reference=None,
            provider_snapshot_reference=(
                f"event-snapshot:s011b.postgresql.{action.value.lower()}:v1"
            ),
            decision=EventBindingDecision.ALLOW,
        )

    def disclosure_binding(self) -> RelationshipDisclosureBindingSnapshot:
        return RelationshipDisclosureBindingSnapshot(
            binding_reference="event-binding:s011b.postgresql.disclosure:v1",
            binding_version=1,
            subject_identity_id=str(self.identity.identity_id),
            event_id=self.event.event_id,
            relationship_id="relationship.s011b.postgresql",
            enabled=True,
            effective_at=NOW - timedelta(hours=1),
            expires_at=NOW + timedelta(hours=1),
            revoked_at=None,
            superseding_binding_reference=None,
            provider_snapshot_reference="event-snapshot:s011b.postgresql.disclosure:v1",
            decision=EventBindingDecision.ALLOW,
        )

    def service(
        self,
        *,
        action: EventRelationshipAction,
        library_context: LibraryRequestContext,
    ) -> EventResourceRelationshipService:
        library_action = LibraryAction(action.value)
        library_bindings = [self.library_binding(library_action)]
        disclosure_bindings = ()
        if action is EventRelationshipAction.AMEND_PURPOSE:
            library_bindings.append(self.library_binding(library_action, viewer=True))
            disclosure_bindings = (self.disclosure_binding(),)
        library_provider = ImmutableLibraryBindingProvider(
            library_bindings,
            enabled=True,
            complete_for_policy=True,
        )
        event_policy = EventResourceRelationshipPolicyV1(
            authority_provider=ImmutableEventAuthorityBindingProvider(
                (self.event_binding(action),),
                enabled=True,
                complete_for_policy=True,
            ),
            disclosure_provider=ImmutableRelationshipDisclosureBindingProvider(
                disclosure_bindings,
                enabled=True,
                complete_for_policy=True,
            ),
        )
        self.assertEqual(
            library_context.authority_binding_reference,
            self.binding_reference(library_action),
        )
        return EventResourceRelationshipService(
            library_contract=LibraryExactVersionContract(
                policy=LibraryExactVersionPolicy(provider=library_provider)
            ),
            event_authority=event_policy,
            relationship_disclosure=event_policy,
        )

    def amend_values(self, context: LibraryRequestContext, **overrides):
        values = {
            "relationship_id": "relationship.s011b.postgresql",
            "event_id": self.event.event_id,
            "resource_id": self.resource.resource_id,
            "version_number": 1,
            "purpose": RelationshipPurpose.REFERENCE,
            "actor_identity_id": self.identity.identity_id,
            "authority_scope": "amend_purpose",
            "library_context": context,
            "idempotency_key": "s011b-postgresql-amend",
            "occurred_at": NOW,
        }
        values.update(overrides)
        return values

    def amend_service(self):
        context = self.library_context(LibraryAction.AMEND_PURPOSE)
        return (
            self.service(
                action=EventRelationshipAction.AMEND_PURPOSE,
                library_context=context,
            ),
            context,
        )

    def test_amend_lock_queries_follow_governed_order_once_without_version_lock(self):
        service, context = self.amend_service()

        with CaptureQueriesContext(connection) as queries:
            service.amend_purpose(**self.amend_values(context))

        locking_queries = [
            query["sql"].lower()
            for query in queries
            if "for update" in query["sql"].lower()
        ]
        root_tables = []
        for query in locking_queries:
            match = re.search(r'from\s+"([^"]+)"', query)
            if match is not None:
                root_tables.append(match.group(1))
        governed_tables = [
            table
            for table in root_tables
            if table
            in {
                "core_libraryresource",
                "core_event",
                "core_eventresourcerelationship",
                "core_identity",
            }
        ]
        self.assertEqual(
            governed_tables[:3],
            [
                "core_libraryresource",
                "core_event",
                "core_eventresourcerelationship",
            ],
        )
        self.assertEqual(governed_tables[3:], ["core_identity", "core_identity"])
        identity_queries = [
            query for query in locking_queries if 'from "core_identity"' in query
        ]
        self.assertTrue(
            all(self.identity.identity_id.hex in query for query in identity_queries),
            identity_queries,
        )
        self.assertFalse(
            any(
                re.search(r'from\s+"core_libraryresourceversion"', query)
                or 'for update of "core_libraryresourceversion"' in query
                for query in locking_queries
            ),
            locking_queries,
        )

    def test_deprecated_amend_uses_disclosure_not_linkability_and_one_identity_epoch(self):
        self.resource.state = LibraryResource.State.DEPRECATED
        self.resource.save(update_fields=("state", "updated_at"))
        service, context = self.amend_service()

        with patch.object(
            service.library_contract,
            "determine_linkability",
            wraps=service.library_contract.determine_linkability,
        ) as determine_linkability:
            transition = service.amend_purpose(**self.amend_values(context))

        determine_linkability.assert_not_called()
        evidence = {item.kind: item for item in transition.evidence.all()}
        self.assertEqual(
            set(evidence),
            {
                EventResourceRelationshipEvidence.Kind.EVENT_AUTHORITY,
                EventResourceRelationshipEvidence.Kind.LIBRARY_AUTHORITY,
                EventResourceRelationshipEvidence.Kind.LIBRARY_DISCLOSURE_ELIGIBILITY,
                EventResourceRelationshipEvidence.Kind.RELATIONSHIP_DISCLOSURE_ELIGIBILITY,
            },
        )
        self.assertNotIn(
            EventResourceRelationshipEvidence.Kind.LIBRARY_LINKABILITY,
            evidence,
        )
        identity_id = str(self.identity.identity_id)
        epoch = self.identity.access_epoch
        self.assertEqual(transition.actor.identity_id, self.identity.identity_id)
        self.assertEqual(transition.actor_access_epoch, epoch)
        self.assertEqual(
            (
                str(evidence[EventResourceRelationshipEvidence.Kind.LIBRARY_AUTHORITY].actor_identity_id),
                evidence[EventResourceRelationshipEvidence.Kind.LIBRARY_AUTHORITY].actor_access_epoch,
            ),
            (identity_id, epoch),
        )
        self.assertEqual(
            (
                str(evidence[EventResourceRelationshipEvidence.Kind.LIBRARY_DISCLOSURE_ELIGIBILITY].viewer_identity_id),
                evidence[EventResourceRelationshipEvidence.Kind.LIBRARY_DISCLOSURE_ELIGIBILITY].viewer_access_epoch,
            ),
            (identity_id, epoch),
        )
        self.assertEqual(
            (
                str(evidence[EventResourceRelationshipEvidence.Kind.EVENT_AUTHORITY].actor_identity_id),
                evidence[EventResourceRelationshipEvidence.Kind.EVENT_AUTHORITY].actor_access_epoch,
            ),
            (identity_id, epoch),
        )
        self.assertEqual(
            (
                str(evidence[EventResourceRelationshipEvidence.Kind.RELATIONSHIP_DISCLOSURE_ELIGIBILITY].viewer_identity_id),
                evidence[EventResourceRelationshipEvidence.Kind.RELATIONSHIP_DISCLOSURE_ELIGIBILITY].viewer_access_epoch,
            ),
            (identity_id, epoch),
        )

    def test_identity_state_epoch_race_waits_for_commit_and_never_stale_succeeds(self):
        service, context = self.amend_service()
        identity_changed = ThreadEvent()
        release_identity = ThreadEvent()
        determination_entered = ThreadEvent()
        action_finished = ThreadEvent()
        updater_results = []
        action_results = []
        original_evaluate = service.library_contract.evaluate_consequential_library_truth

        def evaluate_with_signal(**kwargs):
            determination_entered.set()
            return original_evaluate(**kwargs)

        def restrict_identity():
            close_old_connections()
            try:
                with transaction.atomic():
                    identity = Identity.objects.select_for_update().get(pk=self.identity.pk)
                    identity.access_state = Identity.AccessState.RESTRICTED
                    identity.access_epoch += 1
                    identity.restricted_at = NOW + timedelta(minutes=1)
                    identity.save(
                        update_fields=("access_state", "access_epoch", "restricted_at")
                    )
                    identity_changed.set()
                    if not release_identity.wait(timeout=5):
                        raise AssertionError("identity race release timed out")
                updater_results.append("restricted")
            except Exception as error:
                updater_results.append(error)
            finally:
                close_old_connections()

        def amend():
            close_old_connections()
            try:
                action_results.append(
                    service.amend_purpose(**self.amend_values(context))
                )
            except Exception as error:
                action_results.append(error)
            finally:
                action_finished.set()
                close_old_connections()

        updater = Thread(target=restrict_identity)
        with patch.object(
            service.library_contract,
            "evaluate_consequential_library_truth",
            side_effect=evaluate_with_signal,
        ):
            updater.start()
            self.assertTrue(identity_changed.wait(timeout=5))
            action = Thread(target=amend)
            action.start()
            self.assertTrue(determination_entered.wait(timeout=5))
            self.assertFalse(action_finished.wait(timeout=0.2))
            release_identity.set()
            updater.join(timeout=10)
            action.join(timeout=10)

        self.assertFalse(updater.is_alive() or action.is_alive())
        self.assertEqual(updater_results, ["restricted"])
        self.assertEqual(len(action_results), 1)
        self.assertIsInstance(action_results[0], EventResourceRelationshipHold)
        self.assertEqual(EventResourceAssertion.objects.count(), 1)
        self.assertEqual(EventResourceRelationshipTransition.objects.count(), 1)

    def test_rollback_after_determinations_restores_aggregate_and_evidence(self):
        service, context = self.amend_service()
        relationship = EventResourceRelationship.objects.get(
            relationship_id="relationship.s011b.postgresql"
        )
        original_head_id = relationship.head_assertion_id
        original_counts = (
            EventResourceAssertion.objects.count(),
            EventResourceRelationshipTransition.objects.count(),
            EventResourceRelationshipEvidence.objects.count(),
        )

        with patch.object(
            service,
            "_library_evidence",
            side_effect=RuntimeError("forced evidence persistence failure"),
        ), self.assertRaisesRegex(RuntimeError, "forced evidence persistence failure"):
            service.amend_purpose(**self.amend_values(context))

        relationship.refresh_from_db()
        self.assertEqual(relationship.head_assertion_id, original_head_id)
        self.assertEqual(
            (
                EventResourceAssertion.objects.count(),
                EventResourceRelationshipTransition.objects.count(),
                EventResourceRelationshipEvidence.objects.count(),
            ),
            original_counts,
        )