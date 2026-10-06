from __future__ import annotations

from datetime import datetime
from uuid import UUID, NAMESPACE_URL, uuid5

from core.organism_contract import OrganismAction
from src.intevia.services.organism_authority import (
    S015PreFoundingAuthorityRequest,
    authority_target_fingerprint,
)


def founding_acceptance_uuid(proposed_organism_uuid: UUID) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"urn:intevia:platform-terms-acceptance:{proposed_organism_uuid}",
    )


def founding_authority_request(
    *,
    database_alias: str,
    actor_pk: int,
    actor_identity_id: UUID,
    actor_access_epoch: int,
    proposed_organism_uuid: UUID,
    accepted_terms_version_uuid: UUID | None,
    request_reference: str,
    idempotency_key: str,
    effective_at: datetime,
    received_at: datetime,
) -> S015PreFoundingAuthorityRequest:
    rule_reference = (
        None
        if accepted_terms_version_uuid is None
        else f"platform-terms-version:{accepted_terms_version_uuid}#founding"
    )
    target: dict[str, object] = {
        "action": OrganismAction.FOUND_LIVING_ORGANISM.value,
        "founder_identity_uuid": actor_identity_id,
        "proposed_organism_uuid": proposed_organism_uuid,
        "founding_rule_reference": rule_reference,
        "instrument_references": [],
    }
    return S015PreFoundingAuthorityRequest(
        database_alias=database_alias,
        actor_pk=actor_pk,
        actor_identity_id=actor_identity_id,
        actor_access_epoch=actor_access_epoch,
        action=OrganismAction.FOUND_LIVING_ORGANISM,
        command_subtype=OrganismAction.FOUND_LIVING_ORGANISM.value,
        target_fingerprint=authority_target_fingerprint(target),
        request_reference=request_reference,
        idempotency_key=idempotency_key,
        evaluated_at=received_at,
        effective_at=effective_at,
        target=target,
        living_organism_id=proposed_organism_uuid,
    )


__all__ = ["founding_acceptance_uuid", "founding_authority_request"]
