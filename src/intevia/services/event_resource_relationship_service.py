"""Transactional commands for governed Event resource relationships."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import re
from uuid import UUID, uuid4

from django.core.exceptions import ValidationError
from django.db import transaction

from core.models import (
    Event,
    EventResourceAssertion,
    EventResourceRelationship,
    EventResourceRelationshipEvidence,
    EventResourceRelationshipTransition,
    Identity,
)
from src.intevia.services.event_resource_relationship_contract import (
    CANONICALIZATION,
    DISCLOSURE_POLICY_REFERENCE,
    SCHEMA_ID,
    SCHEMA_VERSION,
    EventAuthorityResult,
    EventRelationshipAction,
    EventRelationshipAuthority,
    EventRelationshipAuthorityEnvelope,
    EventRelationshipAuthorityTarget,
    EventRelationshipDisclosure,
    ExistenceDisclosureResult,
    RelationshipDisclosureEnvelope,
    RelationshipDisclosureResult,
    RelationshipPurpose,
    RelationshipState,
    VoidReason,
    canonical_event_payload_bytes,
    determination_reference_for,
)
from src.intevia.services.library_exact_version_contract import (
    AuthorityResult,
    ConsequentialLibraryEvidence,
    DeterminationEnvelope,
    DisclosureResult,
    LibraryAction,
    LibraryExactVersionContractService,
    LibraryRequestContext,
    LinkabilityResult,
)


_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~:-]{0,119}\Z")
_CONSUMER_REFERENCE = "consumer.s011b"
_ELIGIBLE_EVENT_STATES = frozenset(
    (Event.State.DRAFT, Event.State.PUBLISHED, Event.State.ACTIVE)
)
_TERMINAL_EVENT_STATES = frozenset((*_ELIGIBLE_EVENT_STATES, Event.State.COMPLETED))


class EventResourceRelationshipCommandError(Exception):
    """Base class for non-revealing command failure."""


class EventResourceRelationshipRefused(EventResourceRelationshipCommandError):
    pass


class EventResourceRelationshipHold(EventResourceRelationshipCommandError):
    pass


class EventResourceRelationshipIdempotencyConflict(
    EventResourceRelationshipCommandError
):
    pass


class EventResourceRelationshipService:
    def __init__(
        self,
        *,
        library_contract: LibraryExactVersionContractService,
        event_authority: EventRelationshipAuthority,
        relationship_disclosure: EventRelationshipDisclosure,
        database_alias: str = "default",
    ) -> None:
        if library_contract is None or event_authority is None or relationship_disclosure is None:
            raise TypeError("Library contract and both EVENT evaluators are required")
        if library_contract.database_alias != database_alias:
            raise ValueError("Library contract must use the command database alias")
        self.library_contract = library_contract
        self.event_authority = event_authority
        self.relationship_disclosure = relationship_disclosure
        self.database_alias = database_alias

    @staticmethod
    def _reference(value: str, name: str) -> str:
        if type(value) is not str or _REFERENCE.fullmatch(value) is None:
            raise ValidationError(f"{name} is invalid")
        return value

    @staticmethod
    def _occurred_at(value: datetime) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValidationError("occurred_at must be timezone-aware")
        return value

    @staticmethod
    def _actor_id(value: UUID | str) -> str:
        try:
            parsed = value if isinstance(value, UUID) else UUID(value)
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValidationError("actor_identity_id is invalid") from exc
        canonical = str(parsed)
        if isinstance(value, str) and value != canonical:
            raise ValidationError("actor_identity_id is invalid")
        return canonical

    @staticmethod
    def _fingerprint(payload: dict[str, object]) -> str:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def _locked_actor(self, evidence: ConsequentialLibraryEvidence) -> Identity:
        payload = evidence.authority_envelope.payload
        if payload.actor_identity_id is None or payload.actor_access_epoch is None:
            raise EventResourceRelationshipHold("applicable Identity is unresolved")
        try:
            actor = (
                Identity.objects.using(self.database_alias)
                .select_for_update()
                .select_related("credential")
                .get(identity_id=payload.actor_identity_id)
            )
        except (Identity.DoesNotExist, Identity.MultipleObjectsReturned) as exc:
            raise EventResourceRelationshipHold("applicable Identity is unresolved") from exc
        if (
            str(actor.identity_id) != payload.actor_identity_id
            or str(actor.access_epoch) != payload.actor_access_epoch
            or actor.access_state != Identity.AccessState.ACTIVE
            or not actor.credential.is_active
        ):
            raise EventResourceRelationshipHold("applicable Identity is inconsistent")
        return actor

    def _locked_actor_id(self, actor_identity_id: str) -> Identity:
        try:
            actor = (
                Identity.objects.using(self.database_alias)
                .select_for_update()
                .select_related("credential")
                .get(identity_id=actor_identity_id)
            )
        except (Identity.DoesNotExist, Identity.MultipleObjectsReturned) as exc:
            raise EventResourceRelationshipHold("applicable Identity is unresolved") from exc
        if actor.access_state != Identity.AccessState.ACTIVE or not actor.credential.is_active:
            raise EventResourceRelationshipHold("applicable Identity is inconsistent")
        return actor

    @staticmethod
    def _require_library_create(evidence: ConsequentialLibraryEvidence) -> None:
        authority = evidence.authority_envelope.payload.result
        if authority == AuthorityResult.HOLD:
            raise EventResourceRelationshipHold("Library authority is unresolved")
        if authority != AuthorityResult.QUALIFIED:
            raise EventResourceRelationshipRefused("Library authority refused")
        if evidence.linkability_envelope is None:
            raise EventResourceRelationshipHold("Library linkability is unresolved")
        linkability = evidence.linkability_envelope.payload.result
        if linkability == LinkabilityResult.HOLD:
            raise EventResourceRelationshipHold("Library linkability is unresolved")
        if linkability != LinkabilityResult.LINKABLE:
            raise EventResourceRelationshipRefused("Library target is not linkable")

    @staticmethod
    def _require_library_amend(evidence: ConsequentialLibraryEvidence) -> None:
        authority = evidence.authority_envelope.payload.result
        if authority == AuthorityResult.HOLD:
            raise EventResourceRelationshipHold("Library authority is unresolved")
        if authority != AuthorityResult.QUALIFIED:
            raise EventResourceRelationshipRefused("Library authority refused")
        if evidence.linkability_envelope is not None:
            raise EventResourceRelationshipHold("AMEND_PURPOSE produced linkability")
        if evidence.disclosure_envelope is None:
            raise EventResourceRelationshipHold("Library disclosure is unresolved")
        disclosure = evidence.disclosure_envelope.payload.result
        if disclosure == DisclosureResult.HOLD:
            raise EventResourceRelationshipHold("Library disclosure is unresolved")
        if disclosure != DisclosureResult.CONTENT_VISIBLE:
            raise EventResourceRelationshipRefused("Library content is hidden")

    @staticmethod
    def _require_event_authority(envelope: EventRelationshipAuthorityEnvelope) -> None:
        if envelope.result is EventAuthorityResult.HOLD:
            raise EventResourceRelationshipHold("EVENT authority is unresolved")
        if envelope.result is not EventAuthorityResult.QUALIFIED:
            raise EventResourceRelationshipRefused("EVENT authority refused")

    @staticmethod
    def _require_relationship_disclosure(envelope: RelationshipDisclosureEnvelope) -> None:
        if (
            envelope.result is RelationshipDisclosureResult.HOLD
            or envelope.existence_result is ExistenceDisclosureResult.HOLD
        ):
            raise EventResourceRelationshipHold("relationship disclosure is unresolved")
        if (
            envelope.result is not RelationshipDisclosureResult.VISIBLE
            or envelope.existence_result is not ExistenceDisclosureResult.EXISTENCE_VISIBLE
        ):
            raise EventResourceRelationshipRefused("relationship is hidden")

    @staticmethod
    def _event_evidence(
        transition: EventResourceRelationshipTransition,
        envelope: EventRelationshipAuthorityEnvelope,
    ) -> None:
        payload = envelope.canonical_payload
        EventResourceRelationshipEvidence.objects.create(
            transition=transition,
            kind=EventResourceRelationshipEvidence.Kind.EVENT_AUTHORITY,
            schema_id=SCHEMA_ID,
            schema_version=SCHEMA_VERSION,
            canonicalization=CANONICALIZATION,
            result=envelope.result.value,
            determination_reference=envelope.determination_reference,
            policy_reference=envelope.policy_reference,
            authority_binding_reference=envelope.binding_reference,
            provider_snapshot_reference=envelope.provider_snapshot_reference,
            canonical_payload=payload,
            payload_sha256=hashlib.sha256(payload).hexdigest(),
            actor_identity_id=envelope.target.actor_identity_id,
            actor_access_epoch=envelope.target.actor_access_epoch,
            evaluated_at=envelope.evaluated_at,
        )

    @staticmethod
    def _library_evidence(
        transition: EventResourceRelationshipTransition,
        kind: str,
        envelope: DeterminationEnvelope,
    ) -> None:
        payload = envelope.payload
        canonical = envelope.canonical_payload
        EventResourceRelationshipEvidence.objects.create(
            transition=transition,
            kind=kind,
            schema_id=payload.schema_id,
            schema_version=payload.schema_version,
            canonicalization=payload.canonicalization,
            result=payload.result,
            determination_reference=envelope.determination_reference,
            policy_reference=payload.policy_reference,
            authority_binding_reference=payload.authority_binding_reference,
            provider_snapshot_reference=payload.provider_snapshot_reference,
            canonical_payload=canonical,
            payload_sha256=hashlib.sha256(canonical).hexdigest(),
            actor_identity_id=payload.actor_identity_id,
            actor_access_epoch=(
                int(payload.actor_access_epoch)
                if payload.actor_access_epoch is not None
                else None
            ),
            viewer_identity_id=payload.viewer_identity_id,
            viewer_access_epoch=(
                int(payload.viewer_access_epoch)
                if payload.viewer_access_epoch is not None
                else None
            ),
            request_reference=payload.request_reference,
            consumer_reference=payload.consumer_reference,
            evaluated_at=envelope.payload.evaluated_at,
        )

    @staticmethod
    def _relationship_disclosure_evidence(
        transition: EventResourceRelationshipTransition,
        envelope: RelationshipDisclosureEnvelope,
    ) -> None:
        payload = envelope.canonical_payload
        EventResourceRelationshipEvidence.objects.create(
            transition=transition,
            kind=EventResourceRelationshipEvidence.Kind.RELATIONSHIP_DISCLOSURE_ELIGIBILITY,
            schema_id=SCHEMA_ID,
            schema_version=SCHEMA_VERSION,
            canonicalization=CANONICALIZATION,
            result=envelope.result.value,
            determination_reference=envelope.determination_reference,
            policy_reference=DISCLOSURE_POLICY_REFERENCE,
            authority_binding_reference=envelope.binding_reference,
            provider_snapshot_reference=envelope.provider_snapshot_reference,
            canonical_payload=payload,
            payload_sha256=hashlib.sha256(payload).hexdigest(),
            viewer_identity_id=envelope.viewer_identity_id,
            viewer_access_epoch=envelope.viewer_access_epoch,
            evaluated_at=envelope.evaluated_at,
        )

    @staticmethod
    def _correction_evidence(
        transition: EventResourceRelationshipTransition,
        *,
        reason: VoidReason,
        survivor_relationship_id: str | None,
        survivor_assertion_id: int | None,
        rationale_reference: str | None,
        correction_evidence_reference: str | None,
        occurred_at: datetime,
    ) -> None:
        payload = canonical_event_payload_bytes(
            {
                "correction_evidence_reference": correction_evidence_reference,
                "occurred_at": occurred_at.isoformat(),
                "rationale_reference": rationale_reference,
                "reason": reason.value,
                "survivor_assertion_id": (
                    str(survivor_assertion_id)
                    if survivor_assertion_id is not None
                    else None
                ),
                "survivor_relationship_id": survivor_relationship_id,
                "transition_id": str(transition.pk),
            }
        )
        event_evidence = transition.evidence.get(
            kind=EventResourceRelationshipEvidence.Kind.EVENT_AUTHORITY
        )
        EventResourceRelationshipEvidence.objects.create(
            transition=transition,
            kind=EventResourceRelationshipEvidence.Kind.CORRECTION,
            schema_id=SCHEMA_ID,
            schema_version=SCHEMA_VERSION,
            canonicalization=CANONICALIZATION,
            result=reason.value,
            determination_reference=determination_reference_for(payload),
            policy_reference=event_evidence.policy_reference,
            canonical_payload=payload,
            payload_sha256=hashlib.sha256(payload).hexdigest(),
            actor_identity_id=transition.actor.identity_id,
            actor_access_epoch=transition.actor_access_epoch,
            evaluated_at=occurred_at,
        )

    def _event_target(
        self,
        *,
        actor: Identity,
        action: EventRelationshipAction,
        authority_scope: str,
        event: Event,
        relationship: EventResourceRelationship,
        assertion: EventResourceAssertion,
        version_number: int,
        proposed_purpose: RelationshipPurpose | None,
        occurred_at: datetime,
    ) -> EventRelationshipAuthorityTarget:
        return EventRelationshipAuthorityTarget(
            actor_identity_id=str(actor.identity_id),
            actor_access_epoch=actor.access_epoch,
            action=action,
            authority_scope=authority_scope,
            event_id=event.event_id,
            event_state=event.state,
            relationship_id=relationship.relationship_id,
            current_assertion_id=assertion.pk,
            resource_id=relationship.library_resource.resource_id,
            version_number=version_number,
            current_purpose=RelationshipPurpose(assertion.purpose),
            proposed_purpose=proposed_purpose,
            occurred_at=occurred_at,
        )

    def _replay(
        self,
        *,
        actor: Identity,
        action: EventRelationshipAction,
        idempotency_key: str,
        fingerprint: str,
    ) -> EventResourceRelationshipTransition | None:
        replay = (
            EventResourceRelationshipTransition.objects.using(self.database_alias)
            .filter(actor=actor, action=action.value, idempotency_key=idempotency_key)
            .first()
        )
        if replay is None:
            return None
        if replay.payload_fingerprint != fingerprint:
            raise EventResourceRelationshipIdempotencyConflict(
                "idempotency key was used with a different payload"
            )
        return replay

    def _append(
        self,
        *,
        relationship: EventResourceRelationship,
        current: EventResourceAssertion,
        previous_transition: EventResourceRelationshipTransition,
        action: EventRelationshipAction,
        version,
        purpose: RelationshipPurpose,
        state: str,
        prior_disposition: str,
        actor: Identity,
        authority_scope: str,
        event_evidence: EventRelationshipAuthorityEnvelope,
        request_reference: str,
        idempotency_key: str,
        fingerprint: str,
        occurred_at: datetime,
    ) -> EventResourceRelationshipTransition:
        assertion = EventResourceAssertion.objects.create(
            relationship=relationship,
            revision=current.revision + 1,
            predecessor=current,
            library_resource_version=version,
            purpose=purpose.value,
            state=state,
            created_by=actor,
            actor_access_epoch=actor.access_epoch,
            created_at=occurred_at,
        )
        transition = EventResourceRelationshipTransition.objects.create(
            relationship=relationship,
            previous_transition=previous_transition,
            sequence=previous_transition.sequence + 1,
            action=action.value,
            from_assertion=current,
            resulting_assertion=assertion,
            prior_disposition=prior_disposition,
            actor=actor,
            actor_access_epoch=actor.access_epoch,
            authority_scope=authority_scope,
            event_authority_reference=event_evidence.determination_reference,
            event_authority_evaluated_at=event_evidence.evaluated_at,
            request_reference=request_reference,
            consumer_reference=_CONSUMER_REFERENCE,
            idempotency_key=idempotency_key,
            payload_fingerprint=fingerprint,
            transaction_reference=uuid4(),
            occurred_at=occurred_at,
        )
        self._event_evidence(transition, event_evidence)
        relationship.head_assertion = assertion
        relationship.save(update_fields=("head_assertion",))
        return transition

    def create(
        self,
        *,
        relationship_id: str,
        event_id: str,
        resource_id: str,
        version_number: int,
        purpose: RelationshipPurpose,
        actor_identity_id: UUID | str,
        authority_scope: str,
        library_context: LibraryRequestContext,
        idempotency_key: str,
        occurred_at: datetime,
    ) -> EventResourceRelationshipTransition:
        relationship_id = self._reference(relationship_id, "relationship_id")
        event_id = self._reference(event_id, "event_id")
        resource_id = self._reference(resource_id, "resource_id")
        authority_scope = self._reference(authority_scope, "authority_scope")
        idempotency_key = self._reference(idempotency_key, "idempotency_key")
        actor_identity_id = self._actor_id(actor_identity_id)
        occurred_at = self._occurred_at(occurred_at)
        if type(version_number) is not int or version_number < 1:
            raise ValidationError("version_number is invalid")
        if not isinstance(purpose, RelationshipPurpose):
            raise ValidationError("purpose is invalid")
        if not isinstance(library_context, LibraryRequestContext):
            raise ValidationError("library_context is invalid")
        fingerprint = self._fingerprint(
            {
                "action": EventRelationshipAction.CREATE.value,
                "actor_identity_id": actor_identity_id,
                "authority_scope": authority_scope,
                "event_id": event_id,
                "idempotency_key": idempotency_key,
                "occurred_at": occurred_at.isoformat(),
                "purpose": purpose.value,
                "relationship_id": relationship_id,
                "request_reference": library_context.request_reference,
                "resource_id": resource_id,
                "version_number": str(version_number),
            }
        )

        with transaction.atomic(using=self.database_alias):
            scope = self.library_contract.acquire_consequential_library_scope(
                resource_id=resource_id,
                version_number=version_number,
            )
            resource = scope._resource
            version = scope._version
            try:
                event = (
                    Event.objects.using(self.database_alias)
                    .select_for_update()
                    .get(event_id=event_id)
                )
            except Event.DoesNotExist as exc:
                raise EventResourceRelationshipHold("Event is unresolved") from exc
            existing = list(
                EventResourceRelationship.objects.using(self.database_alias)
                .select_for_update()
                .filter(event=event, library_resource=resource)
                .order_by("pk")
            )
            library_evidence = self.library_contract.evaluate_consequential_library_truth(
                scope=scope,
                actor_identity_id=actor_identity_id,
                action=LibraryAction.CREATE,
                context=library_context,
                evaluated_at=occurred_at,
            )
            actor = self._locked_actor(library_evidence)
            self._require_library_create(library_evidence)
            if event.state not in _ELIGIBLE_EVENT_STATES:
                raise EventResourceRelationshipRefused("Event state refuses CREATE")
            if existing:
                replay = (
                    EventResourceRelationshipTransition.objects.using(self.database_alias)
                    .filter(
                        actor=actor,
                        action=EventResourceRelationshipTransition.Action.CREATE,
                        idempotency_key=idempotency_key,
                    )
                    .first()
                )
                if replay is not None and replay.payload_fingerprint == fingerprint:
                    return replay
                if replay is not None:
                    raise EventResourceRelationshipIdempotencyConflict(
                        "idempotency key was used with a different payload"
                    )
                raise EventResourceRelationshipRefused("relationship already exists")
            target = EventRelationshipAuthorityTarget(
                actor_identity_id=str(actor.identity_id),
                actor_access_epoch=actor.access_epoch,
                action=EventRelationshipAction.CREATE,
                authority_scope=authority_scope,
                event_id=event.event_id,
                event_state=event.state,
                relationship_id=None,
                current_assertion_id=None,
                resource_id=resource_id,
                version_number=version_number,
                current_purpose=None,
                proposed_purpose=purpose,
                occurred_at=occurred_at,
            )
            event_evidence = self.event_authority.determine_authority(
                identity=actor,
                target=target,
                evaluated_at=occurred_at,
            )
            self._require_event_authority(event_evidence)
            if resource is None or version is None:
                raise EventResourceRelationshipHold("exact Library target is unresolved")

            relationship = EventResourceRelationship.objects.create(
                relationship_id=relationship_id,
                event=event,
                library_resource=resource,
                created_at=occurred_at,
            )
            assertion = EventResourceAssertion.objects.create(
                relationship=relationship,
                revision=1,
                library_resource_version=version,
                purpose=purpose.value,
                state=EventResourceAssertion.State.CURRENT,
                created_by=actor,
                actor_access_epoch=actor.access_epoch,
                created_at=occurred_at,
            )
            transition = EventResourceRelationshipTransition.objects.create(
                relationship=relationship,
                sequence=1,
                action=EventResourceRelationshipTransition.Action.CREATE,
                resulting_assertion=assertion,
                actor=actor,
                actor_access_epoch=actor.access_epoch,
                authority_scope=authority_scope,
                event_authority_reference=event_evidence.determination_reference,
                event_authority_evaluated_at=event_evidence.evaluated_at,
                request_reference=library_context.request_reference,
                consumer_reference=_CONSUMER_REFERENCE,
                idempotency_key=idempotency_key,
                payload_fingerprint=fingerprint,
                transaction_reference=uuid4(),
                occurred_at=occurred_at,
            )
            self._event_evidence(transition, event_evidence)
            self._library_evidence(
                transition,
                EventResourceRelationshipEvidence.Kind.LIBRARY_AUTHORITY,
                library_evidence.authority_envelope,
            )
            self._library_evidence(
                transition,
                EventResourceRelationshipEvidence.Kind.LIBRARY_LINKABILITY,
                library_evidence.linkability_envelope,
            )
            relationship.head_assertion = assertion
            relationship.save(update_fields=("head_assertion",))
            return transition

    def supersede_version(
        self,
        *,
        relationship_id: str,
        event_id: str,
        resource_id: str,
        version_number: int,
        actor_identity_id: UUID | str,
        authority_scope: str,
        library_context: LibraryRequestContext,
        idempotency_key: str,
        occurred_at: datetime,
    ) -> EventResourceRelationshipTransition:
        return self._library_successor(
            relationship_id=relationship_id,
            event_id=event_id,
            resource_id=resource_id,
            version_number=version_number,
            purpose=None,
            actor_identity_id=actor_identity_id,
            action=EventRelationshipAction.SUPERSEDE_VERSION,
            authority_scope=authority_scope,
            library_context=library_context,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
        )

    def amend_purpose(
        self,
        *,
        relationship_id: str,
        event_id: str,
        resource_id: str,
        version_number: int,
        purpose: RelationshipPurpose,
        actor_identity_id: UUID | str,
        authority_scope: str,
        library_context: LibraryRequestContext,
        idempotency_key: str,
        occurred_at: datetime,
    ) -> EventResourceRelationshipTransition:
        if not isinstance(purpose, RelationshipPurpose):
            raise ValidationError("purpose is invalid")
        return self._library_successor(
            relationship_id=relationship_id,
            event_id=event_id,
            resource_id=resource_id,
            version_number=version_number,
            purpose=purpose,
            actor_identity_id=actor_identity_id,
            action=EventRelationshipAction.AMEND_PURPOSE,
            authority_scope=authority_scope,
            library_context=library_context,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
        )

    def _library_successor(
        self,
        *,
        relationship_id: str,
        event_id: str,
        resource_id: str,
        version_number: int,
        purpose: RelationshipPurpose | None,
        actor_identity_id: UUID | str,
        action: EventRelationshipAction,
        authority_scope: str,
        library_context: LibraryRequestContext,
        idempotency_key: str,
        occurred_at: datetime,
    ) -> EventResourceRelationshipTransition:
        relationship_id = self._reference(relationship_id, "relationship_id")
        event_id = self._reference(event_id, "event_id")
        resource_id = self._reference(resource_id, "resource_id")
        authority_scope = self._reference(authority_scope, "authority_scope")
        idempotency_key = self._reference(idempotency_key, "idempotency_key")
        actor_identity_id = self._actor_id(actor_identity_id)
        occurred_at = self._occurred_at(occurred_at)
        if type(version_number) is not int or version_number < 1:
            raise ValidationError("version_number is invalid")
        if not isinstance(library_context, LibraryRequestContext):
            raise ValidationError("library_context is invalid")
        fingerprint = self._fingerprint(
            {
                "action": action.value,
                "actor_identity_id": actor_identity_id,
                "authority_scope": authority_scope,
                "event_id": event_id,
                "idempotency_key": idempotency_key,
                "occurred_at": occurred_at.isoformat(),
                "purpose": purpose.value if purpose else None,
                "relationship_id": relationship_id,
                "request_reference": library_context.request_reference,
                "resource_id": resource_id,
                "version_number": str(version_number),
            }
        )
        with transaction.atomic(using=self.database_alias):
            scope = self.library_contract.acquire_consequential_library_scope(
                resource_id=resource_id,
                version_number=version_number,
            )
            resource, version = scope._resource, scope._version
            try:
                event = Event.objects.using(self.database_alias).select_for_update().get(
                    event_id=event_id
                )
                relationship = (
                    EventResourceRelationship.objects.using(self.database_alias)
                    .select_for_update(of=("self",))
                    .select_related("library_resource")
                    .get(
                        relationship_id=relationship_id,
                        event=event,
                        library_resource=resource,
                    )
                )
                assertions = list(
                    EventResourceAssertion.objects.using(self.database_alias)
                    .select_for_update(of=("self",))
                    .select_related("library_resource_version")
                    .filter(relationship=relationship)
                    .order_by("revision", "pk")
                )
                transitions = list(
                    EventResourceRelationshipTransition.objects.using(self.database_alias)
                    .select_for_update()
                    .filter(relationship=relationship)
                    .order_by("sequence", "pk")
                )
            except (Event.DoesNotExist, EventResourceRelationship.DoesNotExist) as exc:
                raise EventResourceRelationshipHold("relationship header is unresolved") from exc
            current = next(
                (item for item in assertions if item.pk == relationship.head_assertion_id),
                None,
            )
            if current is None or not transitions:
                raise EventResourceRelationshipHold("relationship header is unresolved")
            previous = transitions[-1]
            if action is EventRelationshipAction.AMEND_PURPOSE and (
                current.library_resource_version_id != getattr(version, "pk", None)
            ):
                raise EventResourceRelationshipHold("exact current assertion is unresolved")
            library_evidence = self.library_contract.evaluate_consequential_library_truth(
                scope=scope,
                actor_identity_id=actor_identity_id,
                action=LibraryAction(action.value),
                context=library_context,
                evaluated_at=occurred_at,
            )
            actor = self._locked_actor(library_evidence)
            if action is EventRelationshipAction.AMEND_PURPOSE:
                self._require_library_amend(library_evidence)
            else:
                self._require_library_create(library_evidence)
            replay = self._replay(
                actor=actor,
                action=action,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
            )
            if replay is not None:
                return replay
            if (
                event.state not in _ELIGIBLE_EVENT_STATES
                or current.state != EventResourceAssertion.State.CURRENT
            ):
                raise EventResourceRelationshipRefused(f"relationship refuses {action.value}")
            if resource is None or version is None:
                raise EventResourceRelationshipHold("exact Library target is unresolved")
            proposed_purpose = purpose or RelationshipPurpose(current.purpose)
            if (
                action is EventRelationshipAction.SUPERSEDE_VERSION
                and current.library_resource_version_id == version.pk
            ):
                raise EventResourceRelationshipRefused(
                    "SUPERSEDE_VERSION requires a different exact version"
                )
            if (
                action is EventRelationshipAction.AMEND_PURPOSE
                and proposed_purpose is RelationshipPurpose(current.purpose)
            ):
                raise EventResourceRelationshipRefused(
                    "AMEND_PURPOSE requires a changed purpose"
                )
            event_evidence = self.event_authority.determine_authority(
                identity=actor,
                target=self._event_target(
                    actor=actor,
                    action=action,
                    authority_scope=authority_scope,
                    event=event,
                    relationship=relationship,
                    assertion=current,
                    version_number=version_number,
                    proposed_purpose=proposed_purpose,
                    occurred_at=occurred_at,
                ),
                evaluated_at=occurred_at,
            )
            self._require_event_authority(event_evidence)
            relationship_evidence = None
            if action is EventRelationshipAction.AMEND_PURPOSE:
                relationship_evidence = self.relationship_disclosure.determine_disclosure(
                    identity=actor,
                    event_id=event.event_id,
                    relationship_id=relationship.relationship_id,
                    assertion_id=current.pk,
                    state=RelationshipState(current.state),
                    purpose=RelationshipPurpose(current.purpose),
                    evaluated_at=occurred_at,
                )
                self._require_relationship_disclosure(relationship_evidence)
            transition = self._append(
                relationship=relationship,
                current=current,
                previous_transition=previous,
                action=action,
                version=version,
                purpose=proposed_purpose,
                state=EventResourceAssertion.State.CURRENT,
                prior_disposition=(
                    EventResourceAssertion.State.CURRENT
                    if action is EventRelationshipAction.AMEND_PURPOSE
                    else EventResourceAssertion.State.SUPERSEDED
                ),
                actor=actor,
                authority_scope=authority_scope,
                event_evidence=event_evidence,
                request_reference=library_context.request_reference,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                occurred_at=occurred_at,
            )
            self._library_evidence(
                transition,
                EventResourceRelationshipEvidence.Kind.LIBRARY_AUTHORITY,
                library_evidence.authority_envelope,
            )
            if action is EventRelationshipAction.AMEND_PURPOSE:
                self._library_evidence(
                    transition,
                    EventResourceRelationshipEvidence.Kind.LIBRARY_DISCLOSURE_ELIGIBILITY,
                    library_evidence.disclosure_envelope,
                )
                self._relationship_disclosure_evidence(transition, relationship_evidence)
            else:
                self._library_evidence(
                    transition,
                    EventResourceRelationshipEvidence.Kind.LIBRARY_LINKABILITY,
                    library_evidence.linkability_envelope,
                )
            return transition

    def retire(
        self,
        *,
        relationship_id: str,
        event_id: str,
        actor_identity_id: UUID | str,
        authority_scope: str,
        request_reference: str,
        idempotency_key: str,
        occurred_at: datetime,
    ) -> EventResourceRelationshipTransition:
        return self._terminal(
            relationship_id=relationship_id,
            event_id=event_id,
            actor_identity_id=actor_identity_id,
            action=EventRelationshipAction.RETIRE,
            authority_scope=authority_scope,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
        )

    def void(
        self,
        *,
        relationship_id: str,
        event_id: str,
        actor_identity_id: UUID | str,
        authority_scope: str,
        reason: VoidReason,
        request_reference: str,
        idempotency_key: str,
        occurred_at: datetime,
        survivor_relationship_id: str | None = None,
        survivor_assertion_id: int | None = None,
        rationale_reference: str | None = None,
        correction_evidence_reference: str | None = None,
    ) -> EventResourceRelationshipTransition:
        if not isinstance(reason, VoidReason):
            raise ValidationError("reason is invalid")
        if survivor_relationship_id is not None:
            survivor_relationship_id = self._reference(
                survivor_relationship_id, "survivor_relationship_id"
            )
        if rationale_reference is not None:
            rationale_reference = self._reference(rationale_reference, "rationale_reference")
        if correction_evidence_reference is not None:
            correction_evidence_reference = self._reference(
                correction_evidence_reference, "correction_evidence_reference"
            )
        if survivor_assertion_id is not None and (
            type(survivor_assertion_id) is not int or survivor_assertion_id < 1
        ):
            raise ValidationError("survivor_assertion_id is invalid")
        if (
            reason is VoidReason.DUPLICATE_ASSERTION
            and (
                survivor_relationship_id is None
                or (
                    survivor_relationship_id == "NO_SURVIVOR"
                    and (
                        rationale_reference is None
                        or survivor_assertion_id is not None
                    )
                )
                or (
                    survivor_relationship_id != "NO_SURVIVOR"
                    and survivor_assertion_id is None
                )
            )
        ):
            raise ValidationError(
                "duplicate correction requires an exact survivor or structured NO_SURVIVOR"
            )
        if reason is VoidReason.OTHER_GOVERNED_CORRECTION:
            raise EventResourceRelationshipHold(
                "OTHER correction classification is unresolved"
            )
        return self._terminal(
            relationship_id=relationship_id,
            event_id=event_id,
            actor_identity_id=actor_identity_id,
            action=EventRelationshipAction.VOID,
            authority_scope=authority_scope,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
            reason=reason,
            survivor_relationship_id=survivor_relationship_id,
            survivor_assertion_id=survivor_assertion_id,
            rationale_reference=rationale_reference,
            correction_evidence_reference=correction_evidence_reference,
        )

    def _terminal(
        self,
        *,
        relationship_id: str,
        event_id: str,
        actor_identity_id: UUID | str,
        action: EventRelationshipAction,
        authority_scope: str,
        request_reference: str,
        idempotency_key: str,
        occurred_at: datetime,
        reason: VoidReason | None = None,
        survivor_relationship_id: str | None = None,
        survivor_assertion_id: int | None = None,
        rationale_reference: str | None = None,
        correction_evidence_reference: str | None = None,
    ) -> EventResourceRelationshipTransition:
        relationship_id = self._reference(relationship_id, "relationship_id")
        event_id = self._reference(event_id, "event_id")
        actor_identity_id = self._actor_id(actor_identity_id)
        authority_scope = self._reference(authority_scope, "authority_scope")
        request_reference = self._reference(request_reference, "request_reference")
        idempotency_key = self._reference(idempotency_key, "idempotency_key")
        occurred_at = self._occurred_at(occurred_at)
        fingerprint = self._fingerprint(
            {
                "action": action.value,
                "actor_identity_id": actor_identity_id,
                "authority_scope": authority_scope,
                "correction_evidence_reference": correction_evidence_reference,
                "event_id": event_id,
                "idempotency_key": idempotency_key,
                "occurred_at": occurred_at.isoformat(),
                "rationale_reference": rationale_reference,
                "reason": reason.value if reason else None,
                "relationship_id": relationship_id,
                "request_reference": request_reference,
                "survivor_assertion_id": survivor_assertion_id,
                "survivor_relationship_id": survivor_relationship_id,
            }
        )
        with transaction.atomic(using=self.database_alias):
            try:
                event = Event.objects.using(self.database_alias).select_for_update().get(
                    event_id=event_id
                )
                relationship = (
                    EventResourceRelationship.objects.using(self.database_alias)
                    .select_for_update(of=("self",))
                    .select_related("library_resource")
                    .get(relationship_id=relationship_id, event=event)
                )
                assertions = list(
                    EventResourceAssertion.objects.using(self.database_alias)
                    .select_for_update(of=("self",))
                    .select_related("library_resource_version")
                    .filter(relationship=relationship)
                    .order_by("revision", "pk")
                )
                transitions = list(
                    EventResourceRelationshipTransition.objects.using(self.database_alias)
                    .select_for_update()
                    .filter(relationship=relationship)
                    .order_by("sequence", "pk")
                )
            except (Event.DoesNotExist, EventResourceRelationship.DoesNotExist) as exc:
                raise EventResourceRelationshipHold("relationship header is unresolved") from exc
            current = next(
                (item for item in assertions if item.pk == relationship.head_assertion_id),
                None,
            )
            if current is None or not transitions:
                raise EventResourceRelationshipHold("relationship header is unresolved")
            previous = transitions[-1]
            actor = self._locked_actor_id(actor_identity_id)
            replay = self._replay(
                actor=actor,
                action=action,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
            )
            if replay is not None:
                return replay
            if (
                action is EventRelationshipAction.VOID
                and reason is VoidReason.DUPLICATE_ASSERTION
                and survivor_relationship_id != "NO_SURVIVOR"
            ):
                try:
                    survivor_relationship = (
                        EventResourceRelationship.objects.using(self.database_alias)
                        .select_for_update(of=("self",))
                        .get(
                            relationship_id=survivor_relationship_id,
                            event=event,
                        )
                    )
                    survivor = EventResourceAssertion.objects.using(
                        self.database_alias
                    ).select_for_update(of=("self",)).get(
                        pk=survivor_assertion_id,
                        relationship=survivor_relationship,
                    )
                except (
                    EventResourceRelationship.DoesNotExist,
                    EventResourceAssertion.DoesNotExist,
                ) as exc:
                    raise EventResourceRelationshipHold(
                        "duplicate survivor is unresolved"
                    ) from exc
                if (
                    survivor_relationship.pk != relationship.pk
                    or survivor.pk == current.pk
                    or survivor.pk not in {item.pk for item in assertions[:-1]}
                    or survivor.library_resource_version_id
                    != current.library_resource_version_id
                    or survivor.purpose != current.purpose
                ):
                    raise EventResourceRelationshipRefused(
                        "duplicate survivor is outside the correction boundary"
                    )
            if current.state != EventResourceAssertion.State.CURRENT:
                raise EventResourceRelationshipRefused("relationship is terminal")
            if action is EventRelationshipAction.RETIRE and event.state not in _TERMINAL_EVENT_STATES:
                raise EventResourceRelationshipRefused("Event state refuses RETIRE")
            if (
                action is EventRelationshipAction.VOID
                and event.state not in _TERMINAL_EVENT_STATES
                and event.state != Event.State.ARCHIVED
            ):
                raise EventResourceRelationshipRefused("Event state refuses VOID")
            if (
                action is EventRelationshipAction.VOID
                and event.state == Event.State.ARCHIVED
                and authority_scope != "historical_correction"
            ):
                raise EventResourceRelationshipRefused(
                    "archived Event requires historical correction"
                )
            if (
                action is EventRelationshipAction.VOID
                and reason is VoidReason.WRONG_PURPOSE
                and authority_scope != "historical_correction"
            ):
                raise EventResourceRelationshipRefused(
                    "ordinary wrong purpose requires AMEND_PURPOSE"
                )
            event_evidence = self.event_authority.determine_authority(
                identity=actor,
                target=self._event_target(
                    actor=actor,
                    action=action,
                    authority_scope=authority_scope,
                    event=event,
                    relationship=relationship,
                    assertion=current,
                    version_number=current.library_resource_version.version_number,
                    proposed_purpose=RelationshipPurpose(current.purpose),
                    occurred_at=occurred_at,
                ),
                evaluated_at=occurred_at,
            )
            self._require_event_authority(event_evidence)
            state = (
                EventResourceAssertion.State.RETIRED
                if action is EventRelationshipAction.RETIRE
                else EventResourceAssertion.State.VOIDED
            )
            transition = self._append(
                relationship=relationship,
                current=current,
                previous_transition=previous,
                action=action,
                version=current.library_resource_version,
                purpose=RelationshipPurpose(current.purpose),
                state=state,
                prior_disposition=EventResourceAssertion.State.CURRENT,
                actor=actor,
                authority_scope=authority_scope,
                event_evidence=event_evidence,
                request_reference=request_reference,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                occurred_at=occurred_at,
            )
            if action is EventRelationshipAction.VOID:
                self._correction_evidence(
                    transition,
                    reason=reason,
                    survivor_relationship_id=survivor_relationship_id,
                    survivor_assertion_id=survivor_assertion_id,
                    rationale_reference=rationale_reference,
                    correction_evidence_reference=correction_evidence_reference,
                    occurred_at=occurred_at,
                )
            return transition


__all__ = [
    "EventResourceRelationshipCommandError",
    "EventResourceRelationshipHold",
    "EventResourceRelationshipIdempotencyConflict",
    "EventResourceRelationshipRefused",
    "EventResourceRelationshipService",
]