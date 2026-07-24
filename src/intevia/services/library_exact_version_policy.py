"""Exact implementation of LIB-EXACT-VERSION-PREALPHA-001 v1."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
from typing import Iterable

from core.models import Identity, LibraryResource, LibraryResourceVersion
from src.intevia.services.library_exact_version_contract import (
    BindingDecision,
    BindingKind,
    BindingLookup,
    BindingLookupStatus,
    BindingSnapshot,
    BasisCode,
    DisclosureResult,
    LibraryAction,
    LibraryBindingProvider,
    LibraryRequestContext,
    LimitationCode,
    POLICY_ENVIRONMENT,
    POLICY_REFERENCE,
    AuthorityResult,
    canonical_timestamp,
)


VIEWER_SCOPE = "LIBRARY_EXACT_VERSION_CONTENT"
_DECIMAL = re.compile(r"0|[1-9][0-9]*\Z")
_BINDING_REFERENCE = re.compile(
    r"lib-authority-binding:[A-Za-z0-9][A-Za-z0-9._~-]{0,127}:v1\Z"
)
_SNAPSHOT_REFERENCE = re.compile(r"lib-binding-snapshot:sha256:[0-9a-f]{64}\Z")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")
_VIEWER_SCOPE = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")
_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z\Z")
_ARTIFACT_REFERENCE = re.compile(r"lib-binding-artifact:sha256:[0-9a-f]{64}\Z")
_ARTIFACT_DOMAIN = b"INTEVIA:S011A:LIB-BINDING-ARTIFACT:v1\n"
_SNAPSHOT_DOMAIN = b"INTEVIA:S011A:LIB-BINDING-SNAPSHOT:v1\n"


def _valid_snapshot(snapshot: BindingSnapshot) -> bool:
    if (
        type(snapshot.binding_reference) is not str
        or _BINDING_REFERENCE.fullmatch(snapshot.binding_reference) is None
        or type(snapshot.binding_version) is not str
        or _DECIMAL.fullmatch(snapshot.binding_version) is None
        or snapshot.policy_reference != POLICY_REFERENCE
        or snapshot.environment != POLICY_ENVIRONMENT
        or type(snapshot.subject_identity_id) is not str
        or _UUID.fullmatch(snapshot.subject_identity_id) is None
        or type(snapshot.enabled) is not bool
        or type(snapshot.effective_at) is not datetime
        or type(snapshot.expires_at) is not datetime
        or snapshot.effective_at >= snapshot.expires_at
        or type(snapshot.provider_snapshot_reference) is not str
        or _SNAPSHOT_REFERENCE.fullmatch(snapshot.provider_snapshot_reference) is None
        or type(snapshot.decision) is not BindingDecision
    ):
        return False
    try:
        canonical_timestamp(snapshot.effective_at)
        canonical_timestamp(snapshot.expires_at)
        if snapshot.revoked_at is not None:
            canonical_timestamp(snapshot.revoked_at)
    except ValueError:
        return False
    if snapshot.superseding_binding_reference is not None:
        if _BINDING_REFERENCE.fullmatch(snapshot.superseding_binding_reference) is None:
            return False
    if snapshot.binding_kind is BindingKind.ACTION:
        return (
            snapshot.action is not None
            and type(snapshot.resource_id) is str
            and bool(snapshot.resource_id)
            and type(snapshot.version_number) is str
            and _DECIMAL.fullmatch(snapshot.version_number) is not None
            and snapshot.viewer_scope is None
        )
    return (
        snapshot.binding_kind is BindingKind.VIEWER
        and snapshot.action is None
        and snapshot.resource_id is None
        and snapshot.version_number is None
        and type(snapshot.viewer_scope) is str
        and _VIEWER_SCOPE.fullmatch(snapshot.viewer_scope) is not None
    )


@dataclass(frozen=True, slots=True)
class ImmutableLibraryBindingProvider(LibraryBindingProvider):
    bindings: tuple[BindingSnapshot, ...] = ()
    enabled: bool = False
    complete_for_policy: bool = False
    available: bool = True

    def __init__(
        self,
        bindings: Iterable[BindingSnapshot] = (),
        *,
        enabled: bool = False,
        complete_for_policy: bool = False,
        available: bool = True,
    ) -> None:
        records = tuple(bindings)
        object.__setattr__(self, "bindings", records)
        object.__setattr__(self, "enabled", enabled)
        object.__setattr__(self, "complete_for_policy", complete_for_policy)
        object.__setattr__(self, "available", available)

    def lookup(
        self,
        *,
        claimed_binding_reference: str | None,
        subject_identity_id: str,
        binding_kind: BindingKind,
        action: LibraryAction | None,
        resource_id: str,
        version_number: str,
        evaluated_at: datetime,
    ) -> BindingLookup:
        if not self.available or not self.enabled:
            return BindingLookup(BindingLookupStatus.UNAVAILABLE)
        if any(not _valid_snapshot(record) for record in self.bindings):
            return BindingLookup(BindingLookupStatus.UNAVAILABLE)
        candidates = [
            record
            for record in self.bindings
            if record.subject_identity_id == subject_identity_id
            and record.binding_kind is binding_kind
            and (claimed_binding_reference is None or record.binding_reference == claimed_binding_reference)
        ]
        if len(candidates) > 1:
            return BindingLookup(BindingLookupStatus.UNAVAILABLE)
        if not candidates:
            status = BindingLookupStatus.NO_MATCH if self.complete_for_policy else BindingLookupStatus.UNAVAILABLE
            return BindingLookup(status)
        record = candidates[0]
        return BindingLookup(BindingLookupStatus.MATCH, record)


def binding_artifact_references(canonical_artifact_payload: bytes) -> tuple[str, str]:
    if type(canonical_artifact_payload) is not bytes:
        raise TypeError("canonical artifact payload must be bytes")
    artifact_digest = hashlib.sha256(_ARTIFACT_DOMAIN + canonical_artifact_payload).hexdigest()
    snapshot_digest = hashlib.sha256(_SNAPSHOT_DOMAIN + canonical_artifact_payload).hexdigest()
    return (
        f"lib-binding-artifact:sha256:{artifact_digest}",
        f"lib-binding-snapshot:sha256:{snapshot_digest}",
    )


def canonical_binding_artifact_bytes(payload: dict) -> bytes:
    expected = {
        "schema_id", "schema_version", "canonicalization", "policy_reference",
        "environment", "artifact_version", "enabled", "effective_at", "expires_at",
        "revoked_at", "superseding_artifact_reference", "complete_for_policy", "bindings",
    }
    if type(payload) is not dict or set(payload) != expected:
        raise ValueError("binding artifact schema mismatch")
    if payload["schema_id"] != "intevia.s011a.library-binding-artifact" or payload["schema_version"] != 1:
        raise ValueError("binding artifact identity mismatch")
    if payload["policy_reference"] != POLICY_REFERENCE or payload["environment"] != POLICY_ENVIRONMENT:
        raise ValueError("binding artifact policy mismatch")
    if payload["canonicalization"] != "RFC8785+INTEVIA-S011A-v1":
        raise ValueError("binding artifact canonicalization mismatch")
    if type(payload["artifact_version"]) is not str or _DECIMAL.fullmatch(payload["artifact_version"]) is None:
        raise ValueError("artifact_version is not canonical")
    if type(payload["enabled"]) is not bool or type(payload["complete_for_policy"]) is not bool:
        raise ValueError("artifact booleans are invalid")
    for name in ("effective_at", "expires_at"):
        if type(payload[name]) is not str or _TIMESTAMP.fullmatch(payload[name]) is None:
            raise ValueError(f"{name} is not canonical")
    effective_at = datetime.strptime(payload["effective_at"], "%Y-%m-%dT%H:%M:%S.%fZ")
    expires_at = datetime.strptime(payload["expires_at"], "%Y-%m-%dT%H:%M:%S.%fZ")
    if effective_at >= expires_at:
        raise ValueError("artifact expiry must follow effective time")
    revoked_at = payload["revoked_at"]
    if revoked_at is not None and (type(revoked_at) is not str or _TIMESTAMP.fullmatch(revoked_at) is None):
        raise ValueError("revoked_at is not canonical")
    superseding = payload["superseding_artifact_reference"]
    if superseding is not None and (type(superseding) is not str or _ARTIFACT_REFERENCE.fullmatch(superseding) is None):
        raise ValueError("superseding artifact reference is invalid")
    if type(payload["bindings"]) is not list:
        raise ValueError("bindings must be an array")
    references = [record.get("binding_reference") for record in payload["bindings"] if type(record) is dict]
    if len(references) != len(payload["bindings"]) or references != sorted(references) or len(references) != len(set(references)):
        raise ValueError("bindings must be complete, unique, and sorted")
    if not references and (payload["enabled"] or payload["complete_for_policy"]):
        raise ValueError("an empty artifact must remain disabled and incomplete")
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False).encode("utf-8")


class LibraryExactVersionPolicyV1:
    def __init__(self, *, provider: LibraryBindingProvider) -> None:
        if provider is None:
            raise TypeError("provider is required")
        self.provider = provider

    @staticmethod
    def _identity_negative(identity: Identity) -> tuple[BasisCode, bool] | None:
        if identity.access_state != Identity.AccessState.ACTIVE:
            return BasisCode.AUTHORITY_IDENTITY_REFUSED, True
        credential = identity.credential
        if credential.is_staff or credential.is_superuser:
            return BasisCode.AUTHORITY_STAFF_OR_SUPERUSER_REFUSED, True
        return None

    @staticmethod
    def _current(snapshot: BindingSnapshot, evaluated_at: datetime) -> bool:
        return (
            snapshot.enabled
            and snapshot.effective_at <= evaluated_at < snapshot.expires_at
            and snapshot.revoked_at is None
            and snapshot.superseding_binding_reference is None
        )

    def determine_authority(
        self,
        *,
        identity: Identity,
        action: LibraryAction,
        resource: LibraryResource,
        version: LibraryResourceVersion,
        context: LibraryRequestContext,
        evaluated_at: datetime,
    ):
        negative = self._identity_negative(identity)
        if negative is not None:
            basis = negative[0]
            return AuthorityResult.REFUSED, basis, None, None
        lookup = self.provider.lookup(
            claimed_binding_reference=context.authority_binding_reference,
            subject_identity_id=str(identity.identity_id),
            binding_kind=BindingKind.ACTION,
            action=action,
            resource_id=resource.resource_id,
            version_number=str(version.version_number),
            evaluated_at=evaluated_at,
        )
        if lookup.status is BindingLookupStatus.UNAVAILABLE:
            return AuthorityResult.HOLD, BasisCode.UNRESOLVED, None, LimitationCode.BINDING_UNAVAILABLE
        if lookup.status is BindingLookupStatus.NO_MATCH:
            return AuthorityResult.REFUSED, BasisCode.AUTHORITY_NO_BINDING_REFUSED, None, None
        snapshot = lookup.snapshot
        if snapshot is None or not self._current(snapshot, evaluated_at):
            return AuthorityResult.HOLD, BasisCode.UNRESOLVED, None, LimitationCode.BINDING_UNAVAILABLE
        if (
            snapshot.policy_reference != POLICY_REFERENCE
            or snapshot.environment != POLICY_ENVIRONMENT
            or snapshot.subject_identity_id != str(identity.identity_id)
        ):
            return AuthorityResult.HOLD, BasisCode.UNRESOLVED, None, LimitationCode.BINDING_UNAVAILABLE
        if (
            snapshot.action is not action
            or snapshot.resource_id != resource.resource_id
            or snapshot.version_number != str(version.version_number)
        ):
            return AuthorityResult.REFUSED, BasisCode.AUTHORITY_TARGET_MISMATCH_REFUSED, snapshot, None
        if snapshot.decision is BindingDecision.DENY:
            return AuthorityResult.REFUSED, BasisCode.AUTHORITY_BINDING_DENIED, snapshot, None
        return AuthorityResult.QUALIFIED, BasisCode.AUTHORITY_EXPLICIT_BINDING_QUALIFIED, snapshot, None

    def determine_disclosure(
        self,
        *,
        identity: Identity,
        resource: LibraryResource,
        version: LibraryResourceVersion,
        evaluated_at: datetime,
    ):
        negative = self._identity_negative(identity)
        if negative is not None:
            basis = (
                BasisCode.VIEWER_STAFF_OR_SUPERUSER_HIDDEN
                if negative[0] is BasisCode.AUTHORITY_STAFF_OR_SUPERUSER_REFUSED
                else BasisCode.VIEWER_IDENTITY_HIDDEN
            )
            return DisclosureResult.HIDDEN, basis, None, None
        lookup = self.provider.lookup(
            claimed_binding_reference=None,
            subject_identity_id=str(identity.identity_id),
            binding_kind=BindingKind.VIEWER,
            action=None,
            resource_id=resource.resource_id,
            version_number=str(version.version_number),
            evaluated_at=evaluated_at,
        )
        if lookup.status is BindingLookupStatus.UNAVAILABLE:
            return DisclosureResult.HOLD, BasisCode.UNRESOLVED, None, LimitationCode.BINDING_UNAVAILABLE
        if lookup.status is BindingLookupStatus.NO_MATCH:
            return DisclosureResult.HIDDEN, BasisCode.VIEWER_NO_BINDING_HIDDEN, None, None
        snapshot = lookup.snapshot
        if snapshot is None or not self._current(snapshot, evaluated_at):
            return DisclosureResult.HOLD, BasisCode.UNRESOLVED, None, LimitationCode.BINDING_UNAVAILABLE
        if snapshot.viewer_scope != VIEWER_SCOPE:
            return DisclosureResult.HIDDEN, BasisCode.VIEWER_SCOPE_MISMATCH_HIDDEN, snapshot, None
        if snapshot.decision is BindingDecision.DENY:
            return DisclosureResult.HIDDEN, BasisCode.VIEWER_BINDING_DENIED, snapshot, None
        state_table = {
            LibraryResource.State.PUBLISHED: (
                DisclosureResult.CONTENT_VISIBLE,
                BasisCode.VIEWER_BOUND_PUBLISHED_CONTENT_VISIBLE,
            ),
            LibraryResource.State.DRAFT: (DisclosureResult.HIDDEN, BasisCode.VIEWER_BOUND_DRAFT_HIDDEN),
            LibraryResource.State.DEPRECATED: (
                DisclosureResult.CONTENT_VISIBLE,
                BasisCode.VIEWER_BOUND_DEPRECATED_CONTENT_VISIBLE,
            ),
            LibraryResource.State.ARCHIVED: (DisclosureResult.HIDDEN, BasisCode.VIEWER_BOUND_ARCHIVED_HIDDEN),
        }
        if resource.state not in state_table:
            return DisclosureResult.HOLD, BasisCode.UNRESOLVED, snapshot, LimitationCode.STATE_UNAVAILABLE
        result, basis = state_table[resource.state]
        return result, basis, snapshot, None


DisabledLibraryBindingProvider = ImmutableLibraryBindingProvider
LibraryExactVersionPolicy = LibraryExactVersionPolicyV1


__all__ = [
    "DisabledLibraryBindingProvider",
    "ImmutableLibraryBindingProvider",
    "LibraryExactVersionPolicy",
    "LibraryExactVersionPolicyV1",
    "VIEWER_SCOPE",
    "binding_artifact_references",
    "canonical_binding_artifact_bytes",
]