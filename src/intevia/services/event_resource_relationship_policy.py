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
    """Return whether a binding snapshot applies at evaluated_at: enabled
    is exactly True, effective_at <= evaluated_at < expires_at, and it is
    neither revoked nor superseded. Comparing naive with aware times
    raises TypeError."""
    return (
        type(snapshot.enabled) is bool
        and snapshot.enabled
        and snapshot.effective_at <= evaluated_at < snapshot.expires_at
        and snapshot.revoked_at is None
        and snapshot.superseding_binding_reference is None
    )


def _identity_matches(identity: Identity, identity_id: str, epoch: int) -> bool:
    """Return whether an Identity is the expected one and may act: its
    identity id text and access epoch match, it is ACTIVE, and its
    credential is active and neither staff nor superuser."""
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
    """An in-memory, immutable source of authority bindings.

    Holds a tuple of snapshots and three flags: enabled (default False),
    complete_for_policy (default False) and available (default True).
    With the defaults it is disabled and every lookup is UNAVAILABLE.
    """
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
        """Store the bindings as a tuple and the three flags as given;
        nothing is validated here."""
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
        """Find the one binding for a target.

        UNAVAILABLE when the provider is unavailable or disabled, or when
        more than one exact EventAuthorityBindingSnapshot has the target's
        subject, action, scope and Event. None found gives NO_MATCH if the
        provider is complete for the policy, else UNAVAILABLE. Otherwise
        MATCH with that snapshot. evaluated_at is not used here; the policy
        checks the window.
        """
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
    """An in-memory, immutable source of disclosure bindings, with the
    same three flags and defaults as the authority provider: disabled
    by default, so every lookup is UNAVAILABLE."""
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
        """Store the bindings as a tuple and the three flags as given;
        nothing is validated here."""
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
        """Find the one disclosure binding for a viewer, Event and
        relationship, with the same UNAVAILABLE, NO_MATCH and MATCH rules
        as lookup_authority. evaluated_at is not used here."""
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
    """The pre-alpha authority and disclosure policy for Event-resource
    relationships.

    Maps the injected providers' binding snapshots to determinations
    and records each as a canonical payload with a determination
    reference. It holds no bindings itself and writes nothing.
    """
    def __init__(
        self,
        *,
        authority_provider: EventAuthorityBindingProvider,
        disclosure_provider: RelationshipDisclosureBindingProvider,
    ) -> None:
        """Keep the two providers; None for either raises TypeError. Their
        methods are not checked here."""
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
        """Build the authority envelope for a result, with the snapshot's
        binding and provider references (None without a snapshot), the
        canonical payload and its determination reference."""
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
        """Select an authority result, then build its envelope if possible.

        First qualify the supplied Identity against the target actor/epoch,
        an ACTIVE state and an active credential that is neither staff nor
        superuser. A false result selects REFUSED with no snapshot and skips
        lookup. Otherwise call the
        authority provider: the exact UNAVAILABLE status selects HOLD and
        the exact NO_MATCH status REFUSED, both without a snapshot.

        Every other status enters snapshot processing; MATCH is not required.
        An exact EventAuthorityBindingSnapshot must pass _current, else
        select HOLD without a snapshot. Exact DENY selects REFUSED and exact
        ALLOW QUALIFIED, retaining its references; another decision selects
        HOLD without a snapshot. Status and decision checks use enum identity.

        The policy relies on the provider to match subject, action, scope
        and Event; it does not repeat those comparisons. It does not refresh
        or lock Identity, validate Event state or evaluate resource content.
        Result selection is not an exception, but provider, attribute,
        time-comparison and payload errors propagate and can prevent any
        envelope from being returned. No persistence is performed here.
        """
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
        """Select disclosure results and build their envelope if possible.

        Read the viewer id, call the provider, and read lookup.snapshot
        before checking the supplied Identity. Qualification uses its own
        id/epoch plus an ACTIVE state and an active credential that is
        neither staff nor superuser; it does not compare against an
        independent expected epoch or refresh it.

        Failed qualification or exact UNAVAILABLE gives HOLD/HOLD;
        exact NO_MATCH gives HIDDEN/HIDDEN. Otherwise an exact disclosure
        snapshot must pass _current, else HOLD/HOLD. Exact DENY gives
        HIDDEN/HIDDEN and exact ALLOW gives VISIBLE/EXISTENCE_VISIBLE,
        retaining references; another decision gives HOLD/HOLD without them.
        Other HOLD/NO_MATCH branches also discard the snapshot. MATCH is
        not explicitly required, and provider matching of viewer, Event
        and relationship is not independently rechecked.

        Build bytes using the local JSON profile. Use evaluated_at.isoformat
        without UTC conversion or an explicit awareness check. Access
        state.value and purpose.value without checking their enum types;
        stringify assertion id and viewer epoch without range checks.
        Neither the asserted relationship facts nor their lifecycle are
        verified here. Provider, attribute, comparison and encoding errors
        propagate, even when HOLD or HIDDEN was selected. No persistence
        is performed here.
        """
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