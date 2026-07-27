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
    pass


class ProjectionAuthorityNotAuthorised(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class ProposalAuthorityRequest:
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
    def authorise(
        self,
        *,
        request: ProposalAuthorityRequest,
    ) -> ProposalAuthorityResponse | None: ...


@dataclass(frozen=True, slots=True)
class ProjectionAuthorityRequest:
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
    def authorise(
        self,
        *,
        request: ProjectionAuthorityRequest,
    ) -> ProjectionAuthorityResponse | None: ...


def _decision_reference_for(domain: bytes, schema: str, response: object) -> str:
    values = asdict(response)
    action = values["action"]
    values["action"] = action.value
    values["actor_identity_id"] = str(values["actor_identity_id"])
    values["evaluated_at"] = canonical_timestamp(values["evaluated_at"])
    values["schema"] = schema
    return hashlib.sha256(domain + canonical_json_bytes(values)).hexdigest()


def proposal_decision_reference_for(response: ProposalAuthorityResponse) -> str:
    return (
        "s013pa1:"
        + _decision_reference_for(
            PROPOSAL_AUTHORITY_DECISION_DOMAIN,
            PROPOSAL_AUTHORITY_DECISION_SCHEMA,
            response,
        )
    )


def projection_decision_reference_for(response: ProjectionAuthorityResponse) -> str:
    return (
        "s013px1:"
        + _decision_reference_for(
            PROJECTION_AUTHORITY_DECISION_DOMAIN,
            PROJECTION_AUTHORITY_DECISION_SCHEMA,
            response,
        )
    )


class ProposalAuthority:
    def __init__(
        self,
        *,
        provider: ProposalAuthorityProvider,
        database_alias: str = "default",
    ) -> None:
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
    def __init__(
        self,
        *,
        provider: ProjectionAuthorityProvider,
        database_alias: str = "default",
    ) -> None:
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