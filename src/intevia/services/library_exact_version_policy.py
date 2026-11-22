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
    """Check the shape of one v1 binding snapshot.

    First require a v1 binding reference and a decimal
    binding_version (0 or digits without a leading zero). Check
    policy_reference and environment against the v1 constants,
    then subject_identity_id against the lowercase UUID pattern
    for versions 1 to 5 and variants 8, 9, a or b. Require
    enabled to be an exact bool and both bounds to be exact
    datetimes, with effective_at before expires_at. Check
    provider_snapshot_reference for the snapshot prefix and 64
    lowercase hex digits, then require a BindingDecision.

    Convert the start, end and any revocation time to canonical
    UTC text. ValueError here gives False. Check any superseding
    reference against the binding reference pattern.

    ACTION needs a non-None action, a nonempty string resource
    ID, a canonical decimal version string and no viewer scope.
    VIEWER needs all three target fields None and an ASCII scope
    of 1 to 128 letters, digits or underscores, starting with an
    uppercase letter and containing no lowercase letters. Other
    kinds fail.

    Return False on a failed check. No clock, digest
    recomputation, revocation ordering or LibraryAction type
    check is involved. Missing attributes can raise
    AttributeError. Mixed naive and aware times or an invalid
    regex input can raise TypeError. Comparison and timezone
    errors outside the caught ValueError propagate, including
    OverflowError on UTC conversion.
    """
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
    """Fixed, in-memory LibraryBindingProvider for pre-alpha.

    Holds a tuple of binding snapshots and three switches: enabled
    and complete_for_policy (both default False) and available
    (default True). Frozen. DisabledLibraryBindingProvider is this
    class; with its defaults every lookup is UNAVAILABLE. It reads
    no database and no artifact file.
    """
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
        """Collect the supplied bindings into a tuple.

        A non-iterable raises TypeError. Errors raised while
        consuming the iterable propagate. Store all three
        switches as supplied, without checking their types or
        validating any snapshot.
        """
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
        """Find a snapshot by subject, kind and optional claim.

        Return UNAVAILABLE if unavailable or disabled. Otherwise
        validate records in tuple order. The first False gives
        UNAVAILABLE, even for an unrelated subject. Validation
        exceptions propagate, including AttributeError,
        TypeError and OverflowError. Errors testing the supplied
        switches also propagate.

        Match subject ID by equality and kind by identity. When
        the claimed reference is not None, also require the same
        binding reference. Multiple candidates give UNAVAILABLE.
        Zero gives NO_MATCH if complete_for_policy is truthy,
        otherwise UNAVAILABLE. These statuses carry no snapshot.
        One candidate gives MATCH with that stored snapshot.

        Action, resource ID, version and evaluation time do not
        select a candidate. Decision, revocation and current
        eligibility do not break ties. Lookup grants no
        permission.
        """
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
    """Return the artifact and snapshot references, in that order.

    Hash each distinct domain prefix followed by the supplied
    bytes with SHA-256. Prefix the lowercase hex digests with
    lib-binding-artifact:sha256: and
    lib-binding-snapshot:sha256: respectively. Require exact
    bytes, including for an empty payload. Other types, even
    bytes subclasses, raise TypeError. The function does not
    validate the payload's form.
    """
    if type(canonical_artifact_payload) is not bytes:
        raise TypeError("canonical artifact payload must be bytes")
    artifact_digest = hashlib.sha256(_ARTIFACT_DOMAIN + canonical_artifact_payload).hexdigest()
    snapshot_digest = hashlib.sha256(_SNAPSHOT_DOMAIN + canonical_artifact_payload).hexdigest()
    return (
        f"lib-binding-artifact:sha256:{artifact_digest}",
        f"lib-binding-snapshot:sha256:{snapshot_digest}",
    )


def canonical_binding_artifact_bytes(payload: dict) -> bytes:
    """Check the artifact envelope and return its JSON as UTF-8.

    Require an exact dict with the thirteen artifact keys. Check
    the schema ID intevia.s011a.library-binding-artifact and a
    schema version equal to 1 (True and 1.0 also pass). Check
    the v1 policy reference and environment, then the label
    RFC8785+INTEVIA-S011A-v1, a canonical decimal
    artifact_version string and exact bools for enabled and
    complete_for_policy.

    Check both time strings for the UTC form with six fractional
    digits and Z. Parse effective_at and expires_at, requiring
    the first to precede the second. Optional revoked_at gets
    only the same text-pattern check. Its calendar date and
    position in the window are unchecked. An optional
    superseding reference must match the artifact SHA-256
    reference pattern.

    Require an exact list of exact dicts. Take binding_reference
    from each, using None for a missing key. Require sorted
    order, then uniqueness. An empty list requires both flags
    false. No record schema or reference type is enforced. A
    lone missing reference can pass. Explicit validation
    failures and invalid parsed dates raise ValueError.
    Incomparable references or unhashable references can raise
    TypeError.

    Serialize with json.dumps, sorted keys, compact separators,
    ensure_ascii=False and allow_nan=False. Encode as UTF-8.
    Unsupported objects or unsortable keys can raise TypeError,
    cycles or nonfinite numbers ValueError, excessive nesting
    RecursionError, and an unpaired surrogate
    UnicodeEncodeError.
    """
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
    """The v1 exact-version policy: authority and disclosure.

    Implements LIB-EXACT-VERSION-PREALPHA-001 v1 over a binding
    provider. Each determination returns a tuple of the result, its
    basis code, the snapshot relied on or None, and a limitation
    code or None. LibraryExactVersionPolicy names this class.
    """
    def __init__(self, *, provider: LibraryBindingProvider) -> None:
        """Keep the binding provider; None raises TypeError.

        Nothing else is checked: any other object is accepted and is
        used only through its lookup method.
        """
        if provider is None:
            raise TypeError("provider is required")
        self.provider = provider

    @staticmethod
    def _identity_negative(identity: Identity) -> tuple[BasisCode, bool] | None:
        """Return an identity refusal pair, or None.

        A non-ACTIVE identity gives (AUTHORITY_IDENTITY_REFUSED,
        True) before credential access. For an ACTIVE identity,
        test is_staff, then is_superuser if needed. Either gives
        (AUTHORITY_STAFF_OR_SUPERUSER_REFUSED, True). Otherwise
        return None. Both callers use only the basis.

        Reading the credential can load its relation. Attribute
        and database access errors propagate. A None credential
        raises AttributeError when its flags are read. The
        credential's is_active flag is not tested.
        """
        if identity.access_state != Identity.AccessState.ACTIVE:
            return BasisCode.AUTHORITY_IDENTITY_REFUSED, True
        credential = identity.credential
        if credential.is_staff or credential.is_superuser:
            return BasisCode.AUTHORITY_STAFF_OR_SUPERUSER_REFUSED, True
        return None

    @staticmethod
    def _current(snapshot: BindingSnapshot, evaluated_at: datetime) -> bool:
        """Test currency at the supplied evaluation time.

        Check enabled first, then effective_at <= evaluated_at <
        expires_at, then require both revoked_at and the
        superseding binding reference to be None. Any revocation
        date disqualifies the snapshot, including a future date.

        Checks short-circuit. With aware bounds, a naive
        evaluation time raises TypeError only if the comparison
        is reached. Attribute and comparison errors propagate.
        """
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
        """Decide authority for one action and exact version.

        Return (result, basis, snapshot, limitation). The first
        decisions carry no snapshot. A non-ACTIVE identity is
        REFUSED with AUTHORITY_IDENTITY_REFUSED. Next, staff or
        superuser is REFUSED with
        AUTHORITY_STAFF_OR_SUPERUSER_REFUSED.

        Request ACTION for the string identity ID, context's
        claimed binding reference, action, resource ID, string
        version number and evaluated_at. UNAVAILABLE gives HOLD.
        NO_MATCH gives REFUSED with
        AUTHORITY_NO_BINDING_REFUSED. Otherwise a missing or
        non-current snapshot gives HOLD. Next check policy
        reference, environment and subject ID. Any mismatch
        gives HOLD. Every HOLD has basis UNRESOLVED, no snapshot
        and limitation BINDING_UNAVAILABLE.

        The remaining decisions carry the snapshot. Compare
        action by identity, resource ID and string version by
        equality. Any mismatch is REFUSED with
        AUTHORITY_TARGET_MISMATCH_REFUSED. Next DENY is REFUSED
        with AUTHORITY_BINDING_DENIED. Otherwise return
        QUALIFIED with AUTHORITY_EXPLICIT_BINDING_QUALIFIED.
        Every non-HOLD result has limitation None.

        The provider supplies the binding kind,
        claimed-reference match and typed decision. These are
        not revalidated here. No other context field, resource
        state or ownership of the version is checked here. The
        method itself takes no lock and writes nothing.

        Provider and attribute errors propagate, including
        errors loading a credential. A reached comparison of
        naive and aware times raises TypeError. Earlier returns
        skip it.
        """
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
        """Decide content disclosure for the supplied resource.

        Return (result, basis, snapshot, limitation). A
        non-ACTIVE viewer is HIDDEN with VIEWER_IDENTITY_HIDDEN.
        Next, staff or superuser is HIDDEN with
        VIEWER_STAFF_OR_SUPERUSER_HIDDEN.

        Request VIEWER for the string identity ID, resource ID,
        string version number and evaluated_at, with no action
        or claimed reference. UNAVAILABLE gives HOLD. NO_MATCH
        gives HIDDEN with VIEWER_NO_BINDING_HIDDEN. Otherwise a
        missing or non-current snapshot gives HOLD. These
        decisions carry no snapshot. Both binding HOLDs use
        UNRESOLVED and BINDING_UNAVAILABLE.

        All remaining decisions carry the snapshot. A scope
        other than LIBRARY_EXACT_VERSION_CONTENT gives HIDDEN
        with VIEWER_SCOPE_MISMATCH_HIDDEN. Next DENY gives
        HIDDEN with VIEWER_BINDING_DENIED. Otherwise use the
        resource state:

        PUBLISHED gives CONTENT_VISIBLE with
        VIEWER_BOUND_PUBLISHED_CONTENT_VISIBLE.
        DRAFT gives HIDDEN with VIEWER_BOUND_DRAFT_HIDDEN.
        DEPRECATED gives CONTENT_VISIBLE with
        VIEWER_BOUND_DEPRECATED_CONTENT_VISIBLE.
        ARCHIVED gives HIDDEN with VIEWER_BOUND_ARCHIVED_HIDDEN.
        An unknown hashable state gives HOLD with UNRESOLVED and
        STATE_UNAVAILABLE. Every non-HOLD result has limitation
        None.

        The method trusts the provider for kind, subject,
        policy, environment and a typed decision. It never tests
        for an explicit ALLOW. It does not match the snapshot to
        a resource or version or establish ownership of the
        version. It takes no lock itself and writes nothing.

        Provider, attribute and credential-loading errors
        propagate. A reached naive/aware time comparison or
        lookup of an unhashable state raises TypeError.
        """
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