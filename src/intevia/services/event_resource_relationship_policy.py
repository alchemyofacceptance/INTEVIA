"""Inactive-provider policy evaluators for S011-B test qualification."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from core.models import Identity
from src.intevia.services.event_resource_relationship_contract import (
    AUTHORITY_POLICY_REFERENCE,
    DISCLOSURE_POLICY_REFERENCE,
    BindingDecision,
    BindingLookup,
    BindingLookupStatus,
    EventAuthorityBindingProvider,
    EventAuthorityBindingSnapshot,
    EventAuthorityResult,
    EventRelationshipAuthorityEnvelope,
    EventRelationshipAuthorityTarget,
    ExistenceDisclosureResult,
    RelationshipDisclosureBindingProvider,
    RelationshipDisclosureBindingSnapshot,
    RelationshipDisclosureEnvelope,
    RelationshipDisclosureResult,
    RelationshipPurpose,
    RelationshipState,
    authority_payload,
    canonical_event_payload_bytes,
    determination_reference_for,
)


def _current(snapshot, evaluated_at: datetime) -> bool:
    return (
        type(snapshot.enabled) is bool
        and snapshot.enabled
        and snapshot.effective_at <= evaluated_at < snapshot.expires_at
        and snapshot.revoked_at is None
        and snapshot.superseding_binding_reference is None
    )


def _identity_matches(identity: Identity, identity_id: str, epoch: int) -> bool:
    credential = identity.credential
    return (
        str(identity.identity_id) == identity_id
        and identity.access_epoch == epoch
        and identity.access_state == Identity.AccessState.ACTIVE
        and credential.is_active
        and not credential.is_staff
        and not credential.is_superuser
    )


@dataclass(frozen=True, slots=True)
class ImmutableEventAuthorityBindingProvider(EventAuthorityBindingProvider):
    bindings: tuple[EventAuthorityBindingSnapshot, ...] = ()
    enabled: bool = False
    complete_for_policy: bool = False
    available: bool = True

    def __init__(
        self,
        bindings: Iterable[EventAuthorityBindingSnapshot] = (),
        *,
        enabled: bool = False,
        complete_for_policy: bool = False,
        available: bool = True,
    ) -> None:
        object.__setattr__(self, "bindings", tuple(bindings))
        object.__setattr__(self, "enabled", enabled)
        object.__setattr__(self, "complete_for_policy", complete_for_policy)
        object.__setattr__(self, "available", available)

    def lookup_authority(
        self,
        *,
        target: EventRelationshipAuthorityTarget,
        evaluated_at: datetime,
    ) -> BindingLookup:
        if not self.available or not self.enabled:
            return BindingLookup(BindingLookupStatus.UNAVAILABLE)
        candidates = [
            item
            for item in self.bindings
            if type(item) is EventAuthorityBindingSnapshot
            and item.subject_identity_id == target.actor_identity_id
            and item.action is target.action
            and item.authority_scope == target.authority_scope
            and item.event_id == target.event_id
        ]
        if len(candidates) > 1:
            return BindingLookup(BindingLookupStatus.UNAVAILABLE)
        if not candidates:
            return BindingLookup(
                BindingLookupStatus.NO_MATCH
                if self.complete_for_policy
                else BindingLookupStatus.UNAVAILABLE
            )
        return BindingLookup(BindingLookupStatus.MATCH, candidates[0])


@dataclass(frozen=True, slots=True)
class ImmutableRelationshipDisclosureBindingProvider(
    RelationshipDisclosureBindingProvider
):
    bindings: tuple[RelationshipDisclosureBindingSnapshot, ...] = ()
    enabled: bool = False
    complete_for_policy: bool = False
    available: bool = True

    def __init__(
        self,
        bindings: Iterable[RelationshipDisclosureBindingSnapshot] = (),
        *,
        enabled: bool = False,
        complete_for_policy: bool = False,
        available: bool = True,
    ) -> None:
        object.__setattr__(self, "bindings", tuple(bindings))
        object.__setattr__(self, "enabled", enabled)
        object.__setattr__(self, "complete_for_policy", complete_for_policy)
        object.__setattr__(self, "available", available)

    def lookup_disclosure(
        self,
        *,
        viewer_identity_id: str,
        event_id: str,
        relationship_id: str,
        evaluated_at: datetime,
    ) -> BindingLookup:
        if not self.available or not self.enabled:
            return BindingLookup(BindingLookupStatus.UNAVAILABLE)
        candidates = [
            item
            for item in self.bindings
            if type(item) is RelationshipDisclosureBindingSnapshot
            and item.subject_identity_id == viewer_identity_id
            and item.event_id == event_id
            and item.relationship_id == relationship_id
        ]
        if len(candidates) > 1:
            return BindingLookup(BindingLookupStatus.UNAVAILABLE)
        if not candidates:
            return BindingLookup(
                BindingLookupStatus.NO_MATCH
                if self.complete_for_policy
                else BindingLookupStatus.UNAVAILABLE
            )
        return BindingLookup(BindingLookupStatus.MATCH, candidates[0])


class EventResourceRelationshipPolicyV1:
    def __init__(
        self,
        *,
        authority_provider: EventAuthorityBindingProvider,
        disclosure_provider: RelationshipDisclosureBindingProvider,
    ) -> None:
        if authority_provider is None or disclosure_provider is None:
            raise TypeError("both EVENT providers are required")
        self.authority_provider = authority_provider
        self.disclosure_provider = disclosure_provider

    @staticmethod
    def _authority_envelope(
        *,
        result: EventAuthorityResult,
        target: EventRelationshipAuthorityTarget,
        snapshot: EventAuthorityBindingSnapshot | None,
        evaluated_at: datetime,
    ) -> EventRelationshipAuthorityEnvelope:
        payload = authority_payload(
            target,
            result=result,
            binding_reference=snapshot.binding_reference if snapshot else None,
            provider_snapshot_reference=(
                snapshot.provider_snapshot_reference if snapshot else None
            ),
            evaluated_at=evaluated_at,
        )
        return EventRelationshipAuthorityEnvelope(
            result=result,
            target=target,
            policy_reference=AUTHORITY_POLICY_REFERENCE,
            binding_reference=snapshot.binding_reference if snapshot else None,
            provider_snapshot_reference=(
                snapshot.provider_snapshot_reference if snapshot else None
            ),
            evaluated_at=evaluated_at,
            canonical_payload=payload,
            determination_reference=determination_reference_for(payload),
        )

    def determine_authority(
        self,
        *,
        identity: Identity,
        target: EventRelationshipAuthorityTarget,
        evaluated_at: datetime,
    ) -> EventRelationshipAuthorityEnvelope:
        if not _identity_matches(
            identity,
            target.actor_identity_id,
            target.actor_access_epoch,
        ):
            return self._authority_envelope(
                result=EventAuthorityResult.REFUSED,
                target=target,
                snapshot=None,
                evaluated_at=evaluated_at,
            )
        lookup = self.authority_provider.lookup_authority(
            target=target,
            evaluated_at=evaluated_at,
        )
        if lookup.status is BindingLookupStatus.UNAVAILABLE:
            result, snapshot = EventAuthorityResult.HOLD, None
        elif lookup.status is BindingLookupStatus.NO_MATCH:
            result, snapshot = EventAuthorityResult.REFUSED, None
        else:
            snapshot = lookup.snapshot
            if type(snapshot) is not EventAuthorityBindingSnapshot or not _current(
                snapshot, evaluated_at
            ):
                result, snapshot = EventAuthorityResult.HOLD, None
            elif snapshot.decision is BindingDecision.DENY:
                result = EventAuthorityResult.REFUSED
            elif snapshot.decision is BindingDecision.ALLOW:
                result = EventAuthorityResult.QUALIFIED
            else:
                result, snapshot = EventAuthorityResult.HOLD, None
        return self._authority_envelope(
            result=result,
            target=target,
            snapshot=snapshot,
            evaluated_at=evaluated_at,
        )

    def determine_disclosure(
        self,
        *,
        identity: Identity,
        event_id: str,
        relationship_id: str,
        assertion_id: int,
        state: RelationshipState,
        purpose: RelationshipPurpose,
        evaluated_at: datetime,
    ) -> RelationshipDisclosureEnvelope:
        viewer_id = str(identity.identity_id)
        lookup = self.disclosure_provider.lookup_disclosure(
            viewer_identity_id=viewer_id,
            event_id=event_id,
            relationship_id=relationship_id,
            evaluated_at=evaluated_at,
        )
        snapshot = lookup.snapshot
        active = _identity_matches(identity, viewer_id, identity.access_epoch)
        if not active or lookup.status is BindingLookupStatus.UNAVAILABLE:
            result = RelationshipDisclosureResult.HOLD
            existence = ExistenceDisclosureResult.HOLD
            snapshot = None
        elif lookup.status is BindingLookupStatus.NO_MATCH:
            result = RelationshipDisclosureResult.HIDDEN
            existence = ExistenceDisclosureResult.HIDDEN
            snapshot = None
        elif type(snapshot) is not RelationshipDisclosureBindingSnapshot or not _current(
            snapshot, evaluated_at
        ):
            result = RelationshipDisclosureResult.HOLD
            existence = ExistenceDisclosureResult.HOLD
            snapshot = None
        elif snapshot.decision is BindingDecision.DENY:
            result = RelationshipDisclosureResult.HIDDEN
            existence = ExistenceDisclosureResult.HIDDEN
        elif snapshot.decision is BindingDecision.ALLOW:
            result = RelationshipDisclosureResult.VISIBLE
            existence = ExistenceDisclosureResult.EXISTENCE_VISIBLE
        else:
            result = RelationshipDisclosureResult.HOLD
            existence = ExistenceDisclosureResult.HOLD
            snapshot = None
        payload = canonical_event_payload_bytes(
            {
                "assertion_id": str(assertion_id),
                "binding_reference": snapshot.binding_reference if snapshot else None,
                "event_id": event_id,
                "evaluated_at": evaluated_at.isoformat(),
                "existence_result": existence.value,
                "policy_reference": DISCLOSURE_POLICY_REFERENCE,
                "provider_snapshot_reference": (
                    snapshot.provider_snapshot_reference if snapshot else None
                ),
                "purpose": purpose.value,
                "relationship_id": relationship_id,
                "result": result.value,
                "state": state.value,
                "viewer_access_epoch": str(identity.access_epoch),
                "viewer_identity_id": viewer_id,
            }
        )
        return RelationshipDisclosureEnvelope(
            result=result,
            existence_result=existence,
            viewer_identity_id=viewer_id,
            viewer_access_epoch=identity.access_epoch,
            event_id=event_id,
            relationship_id=relationship_id,
            assertion_id=assertion_id,
            state=state,
            purpose=purpose,
            policy_reference=DISCLOSURE_POLICY_REFERENCE,
            binding_reference=snapshot.binding_reference if snapshot else None,
            provider_snapshot_reference=(
                snapshot.provider_snapshot_reference if snapshot else None
            ),
            evaluated_at=evaluated_at,
            canonical_payload=payload,
            determination_reference=determination_reference_for(payload),
        )


__all__ = [
    "EventResourceRelationshipPolicyV1",
    "ImmutableEventAuthorityBindingProvider",
    "ImmutableRelationshipDisclosureBindingProvider",
]