"""Authority contracts for S013 PROFILE_EFFECT proposal and projection decisions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
from typing import Protocol

from django.db import connections

from src.intevia.services.profile_effect_contract import (
    ProjectionAction,
    ProposalAction,
    bounded_reference,
    canonical_json_bytes,
    canonical_timestamp,
)


PROPOSAL_AUTHORITY_DECISION_DOMAIN = (
    b"INTEVIA:S013:PROFILE_EFFECT:PROPOSAL_AUTHORITY_DECISION:v1\x00"
)
PROPOSAL_AUTHORITY_DECISION_SCHEMA = (
    "intevia.s013.profile-effect.proposal-authority-decision.v1"
)
PROJECTION_AUTHORITY_DECISION_DOMAIN = (
    b"INTEVIA:S013:PROFILE_EFFECT:PROJECTION_AUTHORITY_DECISION:v1\x00"
)
PROJECTION_AUTHORITY_DECISION_SCHEMA = (
    "intevia.s013.profile-effect.projection-authority-decision.v1"
)


class ProposalAuthorityNotAuthorised(PermissionError):
    """Refusal of a proposal authority qualification (a
    PermissionError).
    """
    pass


class ProjectionAuthorityNotAuthorised(PermissionError):
    """Refusal of a projection authority qualification (a
    PermissionError).
    """
    pass


@dataclass(frozen=True, slots=True)
class ProposalAuthorityRequest:
    """What a proposal command asks the authority provider to decide.

    The database alias, actor (key, identity, access epoch), the
    proposal action, the target fingerprint, the request reference and
    key, and evaluated_at. The class performs no validation.
    """
    database_alias: str
    actor_pk: int
    actor_identity_id: object
    actor_access_epoch: int
    action: ProposalAction
    target_fingerprint: str
    request_reference: str
    idempotency_key: str
    evaluated_at: datetime


@dataclass(frozen=True, slots=True)
class ProposalAuthorityResponse:
    """A provider's answer to a ProposalAuthorityRequest: the request's
    nine fields echoed, plus the provider's authority_reference.
    """
    database_alias: str
    actor_pk: int
    actor_identity_id: object
    actor_access_epoch: int
    action: ProposalAction
    target_fingerprint: str
    request_reference: str
    idempotency_key: str
    evaluated_at: datetime
    authority_reference: str


@dataclass(frozen=True, slots=True)
class QualifiedProposalAuthorityDecision:
    """The result structure returned by ProposalAuthority.qualify.

    Carries the echoed request fields, caller-supplied target_reference,
    canonical authority_reference and derived decision_reference
    (s013pa1:...). The class itself performs no qualification or value
    validation and can be constructed directly.
    """
    database_alias: str
    actor_pk: int
    actor_identity_id: object
    actor_access_epoch: int
    action: ProposalAction
    target_fingerprint: str
    target_reference: str
    request_reference: str
    idempotency_key: str
    authority_reference: str
    evaluated_at: datetime
    decision_reference: str


class ProposalAuthorityProvider(Protocol):
    """The interface an injected proposal authority provider must meet:
    authorise(request=...) returning a ProposalAuthorityResponse, or None
    to decline. No implementation exists in this module.
    """
    def authorise(
        self,
        *,
        request: ProposalAuthorityRequest,
    ) -> ProposalAuthorityResponse | None:
        """Decide a proposal request; return a response echoing it, or
        None.
        """
        ...


@dataclass(frozen=True, slots=True)
class ProjectionAuthorityRequest:
    """What a projection command asks the authority provider to decide.

    The database alias, actor (key, identity, access epoch), the
    projection action, the target fingerprint, the request reference
    and key, and evaluated_at. The class performs no validation.
    """
    database_alias: str
    actor_pk: int
    actor_identity_id: object
    actor_access_epoch: int
    action: ProjectionAction
    target_fingerprint: str
    request_reference: str
    idempotency_key: str
    evaluated_at: datetime


@dataclass(frozen=True, slots=True)
class ProjectionAuthorityResponse:
    """A provider's answer to a ProjectionAuthorityRequest: the
    request's nine fields echoed, plus the provider's
    authority_reference.
    """
    database_alias: str
    actor_pk: int
    actor_identity_id: object
    actor_access_epoch: int
    action: ProjectionAction
    target_fingerprint: str
    request_reference: str
    idempotency_key: str
    evaluated_at: datetime
    authority_reference: str


@dataclass(frozen=True, slots=True)
class QualifiedProjectionAuthorityDecision:
    """The result structure returned by ProjectionAuthority.qualify.

    Carries the echoed request fields, caller-supplied target_reference,
    canonical authority_reference and derived decision_reference
    (s013px1:...). The class itself performs no qualification or value
    validation and can be constructed directly.
    """
    database_alias: str
    actor_pk: int
    actor_identity_id: object
    actor_access_epoch: int
    action: ProjectionAction
    target_fingerprint: str
    target_reference: str
    request_reference: str
    idempotency_key: str
    authority_reference: str
    evaluated_at: datetime
    decision_reference: str


class ProjectionAuthorityProvider(Protocol):
    """The interface an injected projection authority provider must
    meet: authorise(request=...) returning a ProjectionAuthorityResponse,
    or None to decline. No implementation exists in this module.
    """
    def authorise(
        self,
        *,
        request: ProjectionAuthorityRequest,
    ) -> ProjectionAuthorityResponse | None:
        """Decide a projection request; return a response echoing it, or
        None.
        """
        ...


def _decision_reference_for(domain: bytes, schema: str, response: object) -> str:
    """Hash a dataclass response into a domain-separated decision digest.

    Start with asdict(response); replace action with its value,
    actor_identity_id with str(), and evaluated_at with its canonical
    timestamp. Set schema to the supplied schema, then hash domain
    followed by canonical_json_bytes(values), returning SHA-256 hex.

    Does not require an exact response class, compare a request, validate
    authority_reference with bounded_reference, or qualify authority.
    Conversion, serialization and hashing errors propagate.
    """
    values = asdict(response)
    action = values["action"]
    values["action"] = action.value
    values["actor_identity_id"] = str(values["actor_identity_id"])
    values["evaluated_at"] = canonical_timestamp(values["evaluated_at"])
    values["schema"] = schema
    return hashlib.sha256(domain + canonical_json_bytes(values)).hexdigest()


def proposal_decision_reference_for(response: ProposalAuthorityResponse) -> str:
    """Return "s013pa1:" plus the proposal response's decision digest."""
    return (
        "s013pa1:"
        + _decision_reference_for(
            PROPOSAL_AUTHORITY_DECISION_DOMAIN,
            PROPOSAL_AUTHORITY_DECISION_SCHEMA,
            response,
        )
    )


def projection_decision_reference_for(response: ProjectionAuthorityResponse) -> str:
    """Return "s013px1:" plus the projection response's decision digest."""
    return (
        "s013px1:"
        + _decision_reference_for(
            PROJECTION_AUTHORITY_DECISION_DOMAIN,
            PROJECTION_AUTHORITY_DECISION_SCHEMA,
            response,
        )
    )


class ProposalAuthority:
    """Qualify proposal-provider responses against their requests.

    Delegate the authority decision to the injected provider. qualify
    checks the database alias, an open atomic block, exact response type,
    echoed-field equality and canonical authority reference, then derives
    a decision reference. It does not independently decide permission.
    Explicit wrapper refusals raise ProposalAuthorityNotAuthorised;
    reference validation, provider and digest errors can propagate.
    """
    def __init__(
        self,
        *,
        provider: ProposalAuthorityProvider,
        database_alias: str = "default",
    ) -> None:
        """Keep the provider and the database alias.

        The provider must have a callable authorise (TypeError otherwise);
        database_alias must be a non-empty exact str (ValueError).
        """
        if provider is None or not callable(getattr(provider, "authorise", None)):
            raise TypeError("provider must implement authorise")
        if type(database_alias) is not str or not database_alias:
            raise ValueError("database_alias is required")
        self.provider = provider
        self.database_alias = database_alias

    def qualify(
        self,
        *,
        request: ProposalAuthorityRequest,
        target_reference: str,
    ) -> QualifiedProposalAuthorityDecision:
        """Ask the proposal provider and return a qualified decision.

        Require a matching database alias and an open atomic block before
        calling authorise(request=request). Require an exact
        ProposalAuthorityResponse and equality of its nine echoed fields.
        These failures raise ProposalAuthorityNotAuthorised.

        bounded_reference validates authority_reference at a limit of 255;
        invalid type, empty stripped text, excess length or non-NFC text
        raises ValueError. A valid reference changed by stripping instead
        raises ProposalAuthorityNotAuthorised. Derive the decision reference
        from the response; provider and digest errors propagate.

        Copy target_reference unchanged without checking its format or
        agreement with target_fingerprint. Echo equality compares values
        with != and does not check field types; it does not establish
        authority policy.
        """
        if request.database_alias != self.database_alias:
            raise ProposalAuthorityNotAuthorised("database alias mismatch")
        if not connections[self.database_alias].in_atomic_block:
            raise ProposalAuthorityNotAuthorised(
                "outer atomic transaction is required"
            )
        response = self.provider.authorise(request=request)
        if type(response) is not ProposalAuthorityResponse:
            raise ProposalAuthorityNotAuthorised("authority response is unavailable")
        echoed = (
            "database_alias",
            "actor_pk",
            "actor_identity_id",
            "actor_access_epoch",
            "action",
            "target_fingerprint",
            "request_reference",
            "idempotency_key",
            "evaluated_at",
        )
        if any(getattr(response, name) != getattr(request, name) for name in echoed):
            raise ProposalAuthorityNotAuthorised("authority response mismatch")
        authority_reference = bounded_reference(
            response.authority_reference,
            "authority_reference",
            255,
        )
        if authority_reference != response.authority_reference:
            raise ProposalAuthorityNotAuthorised(
                "authority response is not canonical"
            )
        return QualifiedProposalAuthorityDecision(
            database_alias=response.database_alias,
            actor_pk=response.actor_pk,
            actor_identity_id=response.actor_identity_id,
            actor_access_epoch=response.actor_access_epoch,
            action=response.action,
            target_fingerprint=response.target_fingerprint,
            target_reference=target_reference,
            request_reference=response.request_reference,
            idempotency_key=response.idempotency_key,
            authority_reference=authority_reference,
            evaluated_at=response.evaluated_at,
            decision_reference=proposal_decision_reference_for(response),
        )


class ProjectionAuthority:
    """Qualify projection-provider responses against their requests.

    Delegate the authority decision to the injected provider. qualify
    checks the database alias, an open atomic block, exact response type,
    echoed-field equality and canonical authority reference, then derives
    a decision reference. It does not independently decide permission.
    Explicit wrapper refusals raise ProjectionAuthorityNotAuthorised;
    reference validation, provider and digest errors can propagate.
    """
    def __init__(
        self,
        *,
        provider: ProjectionAuthorityProvider,
        database_alias: str = "default",
    ) -> None:
        """Keep the provider and the database alias.

        The provider must have a callable authorise (TypeError otherwise);
        database_alias must be a non-empty exact str (ValueError).
        """
        if provider is None or not callable(getattr(provider, "authorise", None)):
            raise TypeError("provider must implement authorise")
        if type(database_alias) is not str or not database_alias:
            raise ValueError("database_alias is required")
        self.provider = provider
        self.database_alias = database_alias

    def qualify(
        self,
        *,
        request: ProjectionAuthorityRequest,
        target_reference: str,
    ) -> QualifiedProjectionAuthorityDecision:
        """Ask the projection provider and return a qualified decision.

        Require a matching database alias and an open atomic block before
        calling authorise(request=request). Require an exact
        ProjectionAuthorityResponse and equality of its nine echoed fields.
        These failures raise ProjectionAuthorityNotAuthorised.

        bounded_reference validates authority_reference at a limit of 255;
        invalid type, empty stripped text, excess length or non-NFC text
        raises ValueError. A valid reference changed by stripping instead
        raises ProjectionAuthorityNotAuthorised. Derive the decision reference
        from the response; provider and digest errors propagate.

        Copy target_reference unchanged without checking its format or
        agreement with target_fingerprint. Echo equality compares values
        with != and does not check field types; it does not establish
        authority policy.
        """
        if request.database_alias != self.database_alias:
            raise ProjectionAuthorityNotAuthorised("database alias mismatch")
        if not connections[self.database_alias].in_atomic_block:
            raise ProjectionAuthorityNotAuthorised(
                "outer atomic transaction is required"
            )
        response = self.provider.authorise(request=request)
        if type(response) is not ProjectionAuthorityResponse:
            raise ProjectionAuthorityNotAuthorised(
                "authority response is unavailable"
            )
        echoed = (
            "database_alias",
            "actor_pk",
            "actor_identity_id",
            "actor_access_epoch",
            "action",
            "target_fingerprint",
            "request_reference",
            "idempotency_key",
            "evaluated_at",
        )
        if any(getattr(response, name) != getattr(request, name) for name in echoed):
            raise ProjectionAuthorityNotAuthorised("authority response mismatch")
        authority_reference = bounded_reference(
            response.authority_reference,
            "authority_reference",
            255,
        )
        if authority_reference != response.authority_reference:
            raise ProjectionAuthorityNotAuthorised(
                "authority response is not canonical"
            )
        return QualifiedProjectionAuthorityDecision(
            database_alias=response.database_alias,
            actor_pk=response.actor_pk,
            actor_identity_id=response.actor_identity_id,
            actor_access_epoch=response.actor_access_epoch,
            action=response.action,
            target_fingerprint=response.target_fingerprint,
            target_reference=target_reference,
            request_reference=response.request_reference,
            idempotency_key=response.idempotency_key,
            authority_reference=authority_reference,
            evaluated_at=response.evaluated_at,
            decision_reference=projection_decision_reference_for(response),
        )


__all__ = [
    "ProjectionAuthority",
    "ProjectionAuthorityNotAuthorised",
    "ProjectionAuthorityProvider",
    "ProjectionAuthorityRequest",
    "ProjectionAuthorityResponse",
    "ProposalAuthority",
    "ProposalAuthorityNotAuthorised",
    "ProposalAuthorityProvider",
    "ProposalAuthorityRequest",
    "ProposalAuthorityResponse",
    "QualifiedProjectionAuthorityDecision",
    "QualifiedProposalAuthorityDecision",
    "projection_decision_reference_for",
    "proposal_decision_reference_for",
]