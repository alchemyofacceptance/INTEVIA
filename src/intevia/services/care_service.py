"""Transactional orchestration for bounded CARE responses."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import (
    CareResponse,
    LibraryResource,
    LibraryResourceVersion,
    Identity,
)
from src.intevia.services.contribution_authority import ContributionAuthority


@dataclass(frozen=True, slots=True)
class TransientCareResponse:
    trigger_type: str
    trigger_reference: str
    context_source: str
    context_reference: str
    output_type: str
    content: str
    actor: Identity
    authority_reference: str


class CareService:
    """Produce non-binding assistance without owning authority or domain state."""

    _TRANSIENT_OUTPUTS = {
        CareResponse.OutputType.ORIENTATION_GUIDANCE,
        CareResponse.OutputType.CLARIFICATION_PROMPT,
    }

    def __init__(self, *, authority: ContributionAuthority) -> None:
        if not isinstance(authority, ContributionAuthority):
            raise TypeError("authority must preserve the existing capability contract")
        self.authority = authority

    @staticmethod
    def _at(value: datetime | None) -> datetime:
        value = value or timezone.now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValidationError("timestamp must be timezone-aware")
        return value

    @staticmethod
    def _text(value: str, name: str, *, required: bool = True) -> str:
        if not isinstance(value, str):
            raise ValidationError(f"{name} must be text")
        value = value.strip()
        if required and not value:
            raise ValidationError(f"{name} is required")
        return value

    @staticmethod
    def _choice(value: str, choices, name: str) -> str:
        if value not in choices.values:
            raise ValidationError(f"{name} is not governed")
        return value

    def render_transient_response(
        self,
        *,
        identity: User,
        trigger_type: str,
        trigger_reference: str,
        context_source: str,
        context_reference: str,
        output_type: str,
        content: str,
        occurred_at: datetime | None = None,
    ) -> TransientCareResponse:
        occurred_at = self._at(occurred_at)
        trigger_type = self._choice(
            trigger_type,
            CareResponse.TriggerType,
            "trigger_type",
        )
        context_source = self._choice(
            context_source,
            CareResponse.ContextSource,
            "context_source",
        )
        output_type = self._choice(
            output_type,
            CareResponse.OutputType,
            "output_type",
        )
        if output_type not in self._TRANSIENT_OUTPUTS:
            raise ValidationError("consequential CARE output requires persistence")
        trigger_reference = self._text(trigger_reference, "trigger_reference")
        context_reference = self._text(context_reference, "context_reference")
        content = self._text(content, "content")
        actor, authority_reference = self.authority.evaluate(
            identity=identity,
            action="render_transient_care_response",
            target=trigger_reference,
            timestamp=occurred_at,
        )
        return TransientCareResponse(
            trigger_type=trigger_type,
            trigger_reference=trigger_reference,
            context_source=context_source,
            context_reference=context_reference,
            output_type=output_type,
            content=content,
            actor=actor,
            authority_reference=authority_reference,
        )

    @transaction.atomic
    def record_consequential_response(
        self,
        *,
        identity: User,
        response_id: str,
        trigger_type: str,
        trigger_reference: str,
        context_source: str,
        context_reference: str,
        output_type: str,
        content: str,
        evidence_reference: str,
        lineage_reference: str,
        selection_basis: str = "",
        governing_rule_reference: str = "",
        selected_library_version: LibraryResourceVersion | None = None,
        human_response_reference: str = "",
        predecessor: CareResponse | None = None,
        occurred_at: datetime | None = None,
    ) -> CareResponse:
        occurred_at = self._at(occurred_at)
        response_id = self._text(response_id, "response_id")
        trigger_type = self._choice(
            trigger_type,
            CareResponse.TriggerType,
            "trigger_type",
        )
        context_source = self._choice(
            context_source,
            CareResponse.ContextSource,
            "context_source",
        )
        output_type = self._choice(
            output_type,
            CareResponse.OutputType,
            "output_type",
        )
        trigger_reference = self._text(trigger_reference, "trigger_reference")
        context_reference = self._text(context_reference, "context_reference")
        content = self._text(content, "content")
        evidence_reference = self._text(
            evidence_reference,
            "evidence_reference",
        )
        lineage_reference = self._text(lineage_reference, "lineage_reference")
        selection_basis = self._text(
            selection_basis,
            "selection_basis",
            required=False,
        )
        governing_rule_reference = self._text(
            governing_rule_reference,
            "governing_rule_reference",
            required=False,
        )
        human_response_reference = self._text(
            human_response_reference,
            "human_response_reference",
            required=False,
        )
        if predecessor is not None:
            if not isinstance(predecessor, CareResponse):
                raise ValidationError("predecessor must be a CareResponse")
            predecessor = CareResponse.objects.select_for_update().get(
                pk=predecessor.pk
            )
            if hasattr(predecessor, "successor"):
                raise ValidationError("a CARE response may have only one successor")
        if selected_library_version is not None:
            if not isinstance(selected_library_version, LibraryResourceVersion):
                raise ValidationError(
                    "selected_library_version must be a LibraryResourceVersion"
                )
            selected_library_version = (
                LibraryResourceVersion.objects.select_for_update().get(
                    pk=selected_library_version.pk
                )
            )
            resource = LibraryResource.objects.select_for_update().get(
                pk=selected_library_version.resource_id
            )
            if (
                resource.state != LibraryResource.State.PUBLISHED
                or resource.current_version_id != selected_library_version.pk
            ):
                raise ValidationError(
                    "CARE may surface only the current published Library version"
                )
        actor, authority_reference = self.authority.evaluate(
            identity=identity,
            action="record_care_response",
            target=response_id,
            timestamp=occurred_at,
        )
        return CareResponse.objects.create(
            response_id=response_id,
            trigger_type=trigger_type,
            trigger_reference=trigger_reference,
            context_source=context_source,
            context_reference=context_reference,
            output_type=output_type,
            content=content,
            selection_basis=selection_basis,
            governing_rule_reference=governing_rule_reference,
            selected_library_version=selected_library_version,
            human_response_reference=human_response_reference,
            actor=actor,
            authority_reference=authority_reference,
            evidence_reference=evidence_reference,
            occurred_at=occurred_at,
            lineage_reference=lineage_reference,
            predecessor=predecessor,
        )


__all__ = ["CareService", "TransientCareResponse"]
