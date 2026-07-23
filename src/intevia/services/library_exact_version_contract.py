"""Governed exact-version determinations owned by the Library domain."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
import re
import unicodedata
from typing import Protocol
from uuid import UUID

from django.db import connections

from core.models import Identity, LibraryResource, LibraryResourceVersion


POLICY_REFERENCE = "policy:LIB-EXACT-VERSION-PREALPHA-001:v1"
POLICY_ENVIRONMENT = "internal-pre-alpha"
SCHEMA_ID = "intevia.s011a.library-determination"
SCHEMA_VERSION = 1
CANONICALIZATION = "RFC8785+INTEVIA-S011A-v1"
DOMAIN_SEPARATOR = b"INTEVIA:S011A:LIB-DETERMINATION:v1\n"

_DECIMAL = re.compile(r"0|[1-9][0-9]*\Z")
_OPAQUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~-]{0,127}\Z")
_BINDING_REFERENCE = re.compile(
    r"lib-authority-binding:([A-Za-z0-9][A-Za-z0-9._~-]{0,127}):v1\Z"
)
_SNAPSHOT_REFERENCE = re.compile(r"lib-binding-snapshot:sha256:[0-9a-f]{64}\Z")
_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z\Z")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")


class LibraryAction(StrEnum):
    CREATE = "CREATE"
    SUPERSEDE_VERSION = "SUPERSEDE_VERSION"
    AMEND_PURPOSE = "AMEND_PURPOSE"


class AuthorityResult(StrEnum):
    QUALIFIED = "QUALIFIED"
    REFUSED = "REFUSED"
    HOLD = "HOLD"


class LinkabilityResult(StrEnum):
    LINKABLE = "LINKABLE"
    NOT_LINKABLE = "NOT_LINKABLE"
    HOLD = "HOLD"


class DisclosureResult(StrEnum):
    CONTENT_VISIBLE = "CONTENT_VISIBLE"
    HIDDEN = "HIDDEN"
    HOLD = "HOLD"


class DeterminationKind(StrEnum):
    AUTHORITY = "AUTHORITY"
    LINKABILITY = "LINKABILITY"
    DISCLOSURE = "DISCLOSURE"


class RevalidationBoundary(StrEnum):
    CONSEQUENTIAL_ACTION_SAME_TRANSACTION = "CONSEQUENTIAL_ACTION_SAME_TRANSACTION"
    CURRENT_EVALUATION_ONLY = "CURRENT_EVALUATION_ONLY"
    READ_TIME_ONLY = "READ_TIME_ONLY"


class BindingKind(StrEnum):
    ACTION = "ACTION"
    VIEWER = "VIEWER"


class BindingDecision(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"


class BindingLookupStatus(StrEnum):
    MATCH = "MATCH"
    NO_MATCH = "NO_MATCH"
    UNAVAILABLE = "UNAVAILABLE"


class BasisCode(StrEnum):
    AUTHORITY_EXPLICIT_BINDING_QUALIFIED = "AUTHORITY_EXPLICIT_BINDING_QUALIFIED"
    AUTHORITY_IDENTITY_REFUSED = "AUTHORITY_IDENTITY_REFUSED"
    AUTHORITY_STAFF_OR_SUPERUSER_REFUSED = "AUTHORITY_STAFF_OR_SUPERUSER_REFUSED"
    AUTHORITY_BINDING_DENIED = "AUTHORITY_BINDING_DENIED"
    AUTHORITY_NO_BINDING_REFUSED = "AUTHORITY_NO_BINDING_REFUSED"
    AUTHORITY_TARGET_MISMATCH_REFUSED = "AUTHORITY_TARGET_MISMATCH_REFUSED"
    STATE_PUBLISHED_LINKABLE = "STATE_PUBLISHED_LINKABLE"
    STATE_DRAFT_NOT_LINKABLE = "STATE_DRAFT_NOT_LINKABLE"
    STATE_DEPRECATED_NOT_LINKABLE = "STATE_DEPRECATED_NOT_LINKABLE"
    STATE_ARCHIVED_NOT_LINKABLE = "STATE_ARCHIVED_NOT_LINKABLE"
    VIEWER_BOUND_PUBLISHED_CONTENT_VISIBLE = "VIEWER_BOUND_PUBLISHED_CONTENT_VISIBLE"
    VIEWER_BOUND_DRAFT_HIDDEN = "VIEWER_BOUND_DRAFT_HIDDEN"
    VIEWER_BOUND_DEPRECATED_CONTENT_VISIBLE = "VIEWER_BOUND_DEPRECATED_CONTENT_VISIBLE"
    VIEWER_BOUND_ARCHIVED_HIDDEN = "VIEWER_BOUND_ARCHIVED_HIDDEN"
    VIEWER_IDENTITY_HIDDEN = "VIEWER_IDENTITY_HIDDEN"
    VIEWER_STAFF_OR_SUPERUSER_HIDDEN = "VIEWER_STAFF_OR_SUPERUSER_HIDDEN"
    VIEWER_BINDING_DENIED = "VIEWER_BINDING_DENIED"
    VIEWER_NO_BINDING_HIDDEN = "VIEWER_NO_BINDING_HIDDEN"
    VIEWER_SCOPE_MISMATCH_HIDDEN = "VIEWER_SCOPE_MISMATCH_HIDDEN"
    UNRESOLVED = "UNRESOLVED"


class LimitationCode(StrEnum):
    INVALID_INPUT = "INVALID_INPUT"
    RESOURCE_OR_VERSION_UNAVAILABLE = "RESOURCE_OR_VERSION_UNAVAILABLE"
    IDENTITY_UNAVAILABLE = "IDENTITY_UNAVAILABLE"
    BINDING_UNAVAILABLE = "BINDING_UNAVAILABLE"
    STATE_UNAVAILABLE = "STATE_UNAVAILABLE"
    TRANSACTION_UNAVAILABLE = "TRANSACTION_UNAVAILABLE"


def canonical_decimal(value: int) -> str:
    if type(value) is not int or value < 0:
        raise ValueError("wide integer must be a non-negative integer")
    return str(value)


def canonical_timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _nfc(value: str, name: str) -> str:
    if type(value) is not str or unicodedata.normalize("NFC", value) != value:
        raise ValueError(f"{name} must be NFC text")
    return value


def _opaque(value: str, name: str) -> str:
    value = _nfc(value, name)
    if _OPAQUE.fullmatch(value) is None:
        raise ValueError(f"{name} is not a bounded opaque component")
    return value


def _binding_reference(value: str) -> str:
    value = _nfc(value, "binding reference")
    if _BINDING_REFERENCE.fullmatch(value) is None:
        raise ValueError("binding reference is invalid")
    return value


def _uuid(value: UUID | str, name: str) -> str:
    try:
        parsed = value if isinstance(value, UUID) else UUID(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{name} must be a UUID") from exc
    canonical = str(parsed)
    if isinstance(value, str) and value != canonical:
        raise ValueError(f"{name} must be a lowercase canonical UUID")
    return canonical


@dataclass(frozen=True, slots=True)
class LibraryRequestContext:
    request_reference: str
    consumer_reference: str
    authority_binding_reference: str
    policy_reference: str
    requested_at: datetime

    def __post_init__(self) -> None:
        _opaque(self.request_reference, "request_reference")
        _opaque(self.consumer_reference, "consumer_reference")
        _binding_reference(self.authority_binding_reference)
        if self.policy_reference != POLICY_REFERENCE:
            raise ValueError("policy reference is not the governed S011-A policy")
        canonical_timestamp(self.requested_at)


@dataclass(frozen=True, slots=True)
class BindingSnapshot:
    binding_reference: str
    binding_version: str
    policy_reference: str
    environment: str
    binding_kind: BindingKind
    subject_identity_id: str
    enabled: bool
    effective_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    superseding_binding_reference: str | None
    provider_snapshot_reference: str
    decision: BindingDecision
    action: LibraryAction | None
    resource_id: str | None
    version_number: str | None
    viewer_scope: str | None


@dataclass(frozen=True, slots=True)
class BindingLookup:
    status: BindingLookupStatus
    snapshot: BindingSnapshot | None = None


class LibraryBindingProvider(Protocol):
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
    ) -> BindingLookup: ...


class LibraryExactVersionPolicy(Protocol):
    def determine_authority(
        self,
        *,
        identity: Identity,
        action: LibraryAction,
        resource: LibraryResource,
        version: LibraryResourceVersion,
        context: LibraryRequestContext,
        evaluated_at: datetime,
    ) -> tuple[AuthorityResult, BasisCode, BindingSnapshot | None, LimitationCode | None]: ...

    def determine_disclosure(
        self,
        *,
        identity: Identity,
        resource: LibraryResource,
        version: LibraryResourceVersion,
        evaluated_at: datetime,
    ) -> tuple[DisclosureResult, BasisCode, BindingSnapshot | None, LimitationCode | None]: ...


@dataclass(frozen=True, slots=True)
class DeterminationPayload:
    action: str | None
    actor_access_epoch: str | None
    actor_identity_id: str | None
    authority_binding_reference: str | None
    basis_code: str
    binding_kind: str | None
    binding_reference: str | None
    binding_version: str | None
    canonicalization: str
    consumer_reference: str | None
    determination_kind: str
    environment: str
    evaluated_at: str
    policy_reference: str
    provider_snapshot_reference: str | None
    request_reference: str | None
    requested_at: str | None
    resource_id: str
    resource_version_pk: str | None
    result: str
    revalidation_boundary: str
    schema_id: str
    schema_version: int
    source_state: str | None
    unresolved_limitation_code: str | None
    version_number: str | None
    viewer_access_epoch: str | None
    viewer_identity_id: str | None


@dataclass(frozen=True, slots=True)
class DeterminationEnvelope:
    payload: DeterminationPayload
    canonical_payload: bytes
    determination_reference: str


_PAYLOAD_FIELDS = frozenset(DeterminationPayload.__dataclass_fields__)
_WIDE_FIELDS = {
    "actor_access_epoch",
    "binding_version",
    "resource_version_pk",
    "version_number",
    "viewer_access_epoch",
}


def canonical_payload_bytes(payload: DeterminationPayload) -> bytes:
    values = asdict(payload)
    if frozenset(values) != _PAYLOAD_FIELDS:
        raise ValueError("determination payload schema mismatch")
    if (
        values["schema_id"] != SCHEMA_ID
        or type(values["schema_version"]) is not int
        or values["schema_version"] != SCHEMA_VERSION
    ):
        raise ValueError("determination schema identity mismatch")
    if values["canonicalization"] != CANONICALIZATION:
        raise ValueError("canonicalization profile mismatch")
    if values["policy_reference"] != POLICY_REFERENCE or values["environment"] != POLICY_ENVIRONMENT:
        raise ValueError("policy identity mismatch")
    if _TIMESTAMP.fullmatch(values["evaluated_at"]) is None:
        raise ValueError("evaluated_at is not canonical")
    if values["requested_at"] is not None and _TIMESTAMP.fullmatch(values["requested_at"]) is None:
        raise ValueError("requested_at is not canonical")
    kinds = {item.value for item in DeterminationKind}
    results = {
        DeterminationKind.AUTHORITY.value: {item.value for item in AuthorityResult},
        DeterminationKind.LINKABILITY.value: {item.value for item in LinkabilityResult},
        DeterminationKind.DISCLOSURE.value: {item.value for item in DisclosureResult},
    }
    if values["determination_kind"] not in kinds or values["result"] not in results[values["determination_kind"]]:
        raise ValueError("determination kind or result is invalid")
    if values["basis_code"] not in {item.value for item in BasisCode}:
        raise ValueError("basis code is invalid")
    if values["revalidation_boundary"] not in {item.value for item in RevalidationBoundary}:
        raise ValueError("revalidation boundary is invalid")
    if values["source_state"] not in {None, "PUBLISHED", "DRAFT", "DEPRECATED", "ARCHIVED"}:
        raise ValueError("source state is invalid")
    if values["action"] not in {None, *(item.value for item in LibraryAction)}:
        raise ValueError("action is invalid")
    if values["binding_kind"] not in {None, *(item.value for item in BindingKind)}:
        raise ValueError("binding kind is invalid")
    if values["unresolved_limitation_code"] not in {None, *(item.value for item in LimitationCode)}:
        raise ValueError("limitation code is invalid")
    for name in ("actor_identity_id", "viewer_identity_id"):
        value = values[name]
        if value is not None and (type(value) is not str or _UUID.fullmatch(value) is None):
            raise ValueError(f"{name} is not a canonical UUID")
    for name in ("binding_reference", "authority_binding_reference"):
        value = values[name]
        if value is not None and _BINDING_REFERENCE.fullmatch(value) is None:
            raise ValueError(f"{name} is invalid")
    snapshot_reference = values["provider_snapshot_reference"]
    if snapshot_reference is not None and _SNAPSHOT_REFERENCE.fullmatch(snapshot_reference) is None:
        raise ValueError("provider snapshot reference is invalid")
    for name in ("request_reference", "consumer_reference"):
        value = values[name]
        if value is not None and _OPAQUE.fullmatch(value) is None:
            raise ValueError(f"{name} is invalid")
    if type(values["resource_id"]) is not str or not values["resource_id"] or len(values["resource_id"]) > 120:
        raise ValueError("resource_id is invalid")
    for name in _WIDE_FIELDS:
        value = values[name]
        if value is not None and (type(value) is not str or _DECIMAL.fullmatch(value) is None):
            raise ValueError(f"{name} is not a canonical decimal string")
    for name, value in values.items():
        if type(value) is float or (type(value) is str and unicodedata.normalize("NFC", value) != value):
            raise ValueError(f"{name} is outside the bounded canonical profile")
    text = json.dumps(
        values,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )
    return text.encode("utf-8")


def envelope_for(payload: DeterminationPayload) -> DeterminationEnvelope:
    canonical = canonical_payload_bytes(payload)
    digest = hashlib.sha256(DOMAIN_SEPARATOR + canonical).hexdigest()
    return DeterminationEnvelope(
        payload=payload,
        canonical_payload=canonical,
        determination_reference=f"lib-determination:sha256:{digest}",
    )


@dataclass(slots=True)
class ConsequentialLibraryScope:
    _resource: LibraryResource | None
    _version: LibraryResourceVersion | None
    _database_alias: str
    _atomic_identity: int
    _consumed: bool = False

    def __getstate__(self):
        raise TypeError("consequential Library scope is not serializable")


@dataclass(frozen=True, slots=True)
class ConsequentialLibraryEvidence:
    authority_envelope: DeterminationEnvelope
    linkability_envelope: DeterminationEnvelope
    resource_id: str
    resource_version_pk: str | None
    version_number: str | None
    policy_reference: str
    provider_snapshot_reference: str | None
    evaluated_at: str


class LibraryExactVersionContractService:
    def __init__(self, *, policy: LibraryExactVersionPolicy, database_alias: str = "default") -> None:
        if policy is None:
            raise TypeError("policy is required")
        self.policy = policy
        self.database_alias = database_alias

    def _resource_version(
        self,
        resource_id: str,
        version_number: int,
        *,
        lock_resource: bool = False,
    ) -> tuple[LibraryResource | None, LibraryResourceVersion | None]:
        if type(resource_id) is not str or not resource_id or type(version_number) is not int or version_number < 1:
            return None, None
        resources = LibraryResource.objects.using(self.database_alias)
        if lock_resource:
            resources = resources.select_for_update()
        try:
            resource = resources.get(resource_id=resource_id)
            version = LibraryResourceVersion.objects.using(self.database_alias).get(
                resource=resource,
                version_number=version_number,
            )
        except (LibraryResource.DoesNotExist, LibraryResourceVersion.DoesNotExist):
            return None, None
        return resource, version

    @staticmethod
    def _identity(identity_id: UUID | str, *, database_alias: str, lock: bool) -> Identity | None:
        try:
            canonical = _uuid(identity_id, "identity_id")
        except ValueError:
            return None
        identities = Identity.objects.using(database_alias).select_related("credential")
        if lock:
            identities = identities.select_for_update()
        try:
            return identities.get(identity_id=canonical)
        except (Identity.DoesNotExist, Identity.MultipleObjectsReturned):
            return None

    @staticmethod
    def _base_payload(
        *,
        kind: DeterminationKind,
        result: StrEnum,
        basis: BasisCode,
        resource_id: str,
        version: LibraryResourceVersion | None,
        resource: LibraryResource | None,
        evaluated_at: datetime,
        revalidation: RevalidationBoundary,
        limitation: LimitationCode | None,
        identity: Identity | None = None,
        viewer: bool = False,
        action: LibraryAction | None = None,
        context: LibraryRequestContext | None = None,
        binding: BindingSnapshot | None = None,
    ) -> DeterminationPayload:
        actor = identity if identity is not None and not viewer else None
        viewer_identity = identity if viewer else None
        return DeterminationPayload(
            action=action.value if action else None,
            actor_access_epoch=canonical_decimal(actor.access_epoch) if actor else None,
            actor_identity_id=str(actor.identity_id) if actor else None,
            authority_binding_reference=context.authority_binding_reference if context else None,
            basis_code=basis.value,
            binding_kind=binding.binding_kind.value if binding else None,
            binding_reference=binding.binding_reference if binding else None,
            binding_version=binding.binding_version if binding else None,
            canonicalization=CANONICALIZATION,
            consumer_reference=context.consumer_reference if context else None,
            determination_kind=kind.value,
            environment=POLICY_ENVIRONMENT,
            evaluated_at=canonical_timestamp(evaluated_at),
            policy_reference=POLICY_REFERENCE,
            provider_snapshot_reference=binding.provider_snapshot_reference if binding else None,
            request_reference=context.request_reference if context else None,
            requested_at=canonical_timestamp(context.requested_at) if context else None,
            resource_id=resource_id,
            resource_version_pk=canonical_decimal(version.pk) if version else None,
            result=result.value,
            revalidation_boundary=revalidation.value,
            schema_id=SCHEMA_ID,
            schema_version=SCHEMA_VERSION,
            source_state=resource.state.upper() if resource else None,
            unresolved_limitation_code=limitation.value if limitation else None,
            version_number=canonical_decimal(version.version_number) if version else None,
            viewer_access_epoch=canonical_decimal(viewer_identity.access_epoch) if viewer_identity else None,
            viewer_identity_id=str(viewer_identity.identity_id) if viewer_identity else None,
        )

    def determine_linkability(
        self,
        *,
        resource_id: str,
        version_number: int,
        evaluated_at: datetime,
    ) -> DeterminationEnvelope:
        resource, version = self._resource_version(resource_id, version_number)
        if resource is None or version is None:
            result, basis, limitation = LinkabilityResult.HOLD, BasisCode.UNRESOLVED, LimitationCode.RESOURCE_OR_VERSION_UNAVAILABLE
        else:
            table = {
                LibraryResource.State.PUBLISHED: (LinkabilityResult.LINKABLE, BasisCode.STATE_PUBLISHED_LINKABLE),
                LibraryResource.State.DRAFT: (LinkabilityResult.NOT_LINKABLE, BasisCode.STATE_DRAFT_NOT_LINKABLE),
                LibraryResource.State.DEPRECATED: (LinkabilityResult.NOT_LINKABLE, BasisCode.STATE_DEPRECATED_NOT_LINKABLE),
                LibraryResource.State.ARCHIVED: (LinkabilityResult.NOT_LINKABLE, BasisCode.STATE_ARCHIVED_NOT_LINKABLE),
            }
            result, basis = table.get(resource.state, (LinkabilityResult.HOLD, BasisCode.UNRESOLVED))
            limitation = None if result is not LinkabilityResult.HOLD else LimitationCode.STATE_UNAVAILABLE
        return envelope_for(self._base_payload(
            kind=DeterminationKind.LINKABILITY,
            result=result,
            basis=basis,
            resource_id=resource_id,
            version=version,
            resource=resource,
            evaluated_at=evaluated_at,
            revalidation=RevalidationBoundary.CURRENT_EVALUATION_ONLY,
            limitation=limitation,
        ))

    def determine_action_authority(
        self,
        *,
        actor_identity_id: UUID | str,
        resource_id: str,
        version_number: int,
        action: LibraryAction,
        context: LibraryRequestContext,
        evaluated_at: datetime,
    ) -> DeterminationEnvelope:
        resource, version = self._resource_version(resource_id, version_number)
        identity = self._identity(actor_identity_id, database_alias=self.database_alias, lock=False)
        if resource is None or version is None:
            result, basis, binding, limitation = AuthorityResult.HOLD, BasisCode.UNRESOLVED, None, LimitationCode.RESOURCE_OR_VERSION_UNAVAILABLE
        elif identity is None:
            result, basis, binding, limitation = AuthorityResult.HOLD, BasisCode.UNRESOLVED, None, LimitationCode.IDENTITY_UNAVAILABLE
        else:
            result, basis, binding, limitation = self.policy.determine_authority(
                identity=identity,
                action=action,
                resource=resource,
                version=version,
                context=context,
                evaluated_at=evaluated_at,
            )
        return envelope_for(self._base_payload(
            kind=DeterminationKind.AUTHORITY,
            result=result,
            basis=basis,
            resource_id=resource_id,
            version=version,
            resource=resource,
            evaluated_at=evaluated_at,
            revalidation=RevalidationBoundary.CONSEQUENTIAL_ACTION_SAME_TRANSACTION,
            limitation=limitation,
            identity=identity,
            action=action,
            context=context,
            binding=binding,
        ))

    def determine_disclosure(
        self,
        *,
        viewer_identity_id: UUID | str,
        resource_id: str,
        version_number: int,
        evaluated_at: datetime,
    ) -> DeterminationEnvelope:
        resource, version = self._resource_version(resource_id, version_number)
        identity = self._identity(viewer_identity_id, database_alias=self.database_alias, lock=False)
        if resource is None or version is None:
            result, basis, binding, limitation = DisclosureResult.HOLD, BasisCode.UNRESOLVED, None, LimitationCode.RESOURCE_OR_VERSION_UNAVAILABLE
        elif identity is None:
            result, basis, binding, limitation = DisclosureResult.HOLD, BasisCode.UNRESOLVED, None, LimitationCode.IDENTITY_UNAVAILABLE
        else:
            result, basis, binding, limitation = self.policy.determine_disclosure(
                identity=identity,
                resource=resource,
                version=version,
                evaluated_at=evaluated_at,
            )
        return envelope_for(self._base_payload(
            kind=DeterminationKind.DISCLOSURE,
            result=result,
            basis=basis,
            resource_id=resource_id,
            version=version,
            resource=resource,
            evaluated_at=evaluated_at,
            revalidation=RevalidationBoundary.READ_TIME_ONLY,
            limitation=limitation,
            identity=identity,
            viewer=True,
            binding=binding,
        ))

    def acquire_consequential_library_scope(
        self,
        *,
        resource_id: str,
        version_number: int,
    ) -> ConsequentialLibraryScope:
        connection = connections[self.database_alias]
        if not connection.in_atomic_block or not connection.atomic_blocks:
            raise RuntimeError("an active outer transaction is required")
        resource, version = self._resource_version(resource_id, version_number, lock_resource=True)
        return ConsequentialLibraryScope(
            _resource=resource,
            _version=version,
            _database_alias=self.database_alias,
            _atomic_identity=id(connection.atomic_blocks[0]),
        )

    def evaluate_consequential_library_truth(
        self,
        *,
        scope: ConsequentialLibraryScope,
        actor_identity_id: UUID | str,
        action: LibraryAction,
        context: LibraryRequestContext,
        evaluated_at: datetime,
    ) -> ConsequentialLibraryEvidence:
        if not isinstance(scope, ConsequentialLibraryScope) or scope._consumed:
            raise RuntimeError("scope is invalid or already consumed")
        connection = connections[scope._database_alias]
        if (
            scope._database_alias != self.database_alias
            or not connection.in_atomic_block
            or not connection.atomic_blocks
            or id(connection.atomic_blocks[0]) != scope._atomic_identity
        ):
            raise RuntimeError("scope is outside its owning transaction")
        scope._consumed = True
        resource = scope._resource
        version = scope._version
        resource_id = resource.resource_id if resource else "unresolved"
        identity = self._identity(actor_identity_id, database_alias=self.database_alias, lock=True)
        if resource is None or version is None:
            result, basis, binding, limitation = AuthorityResult.HOLD, BasisCode.UNRESOLVED, None, LimitationCode.RESOURCE_OR_VERSION_UNAVAILABLE
        elif identity is None:
            result, basis, binding, limitation = AuthorityResult.HOLD, BasisCode.UNRESOLVED, None, LimitationCode.IDENTITY_UNAVAILABLE
        else:
            result, basis, binding, limitation = self.policy.determine_authority(
                identity=identity,
                action=action,
                resource=resource,
                version=version,
                context=context,
                evaluated_at=evaluated_at,
            )
        authority = envelope_for(self._base_payload(
            kind=DeterminationKind.AUTHORITY,
            result=result,
            basis=basis,
            resource_id=resource_id,
            version=version,
            resource=resource,
            evaluated_at=evaluated_at,
            revalidation=RevalidationBoundary.CONSEQUENTIAL_ACTION_SAME_TRANSACTION,
            limitation=limitation,
            identity=identity,
            action=action,
            context=context,
            binding=binding,
        ))
        linkability = self.determine_linkability(
            resource_id=resource_id,
            version_number=version.version_number if version else 0,
            evaluated_at=evaluated_at,
        )
        return ConsequentialLibraryEvidence(
            authority_envelope=authority,
            linkability_envelope=linkability,
            resource_id=resource_id,
            resource_version_pk=canonical_decimal(version.pk) if version else None,
            version_number=canonical_decimal(version.version_number) if version else None,
            policy_reference=POLICY_REFERENCE,
            provider_snapshot_reference=binding.provider_snapshot_reference if binding else None,
            evaluated_at=canonical_timestamp(evaluated_at),
        )


LibraryExactVersionContract = LibraryExactVersionContractService


__all__ = [
    "AuthorityResult",
    "BasisCode",
    "BindingDecision",
    "BindingKind",
    "BindingLookup",
    "BindingLookupStatus",
    "BindingSnapshot",
    "ConsequentialLibraryEvidence",
    "ConsequentialLibraryScope",
    "DeterminationEnvelope",
    "DeterminationKind",
    "DeterminationPayload",
    "DisclosureResult",
    "LibraryAction",
    "LibraryBindingProvider",
    "LibraryExactVersionContract",
    "LibraryExactVersionContractService",
    "LibraryRequestContext",
    "LimitationCode",
    "LinkabilityResult",
    "canonical_decimal",
    "canonical_payload_bytes",
    "canonical_timestamp",
    "envelope_for",
]