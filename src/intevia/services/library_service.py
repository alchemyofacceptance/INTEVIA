"""Transactional orchestration for the governed Library lifecycle."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from core.models import (
    LibraryResource,
    LibraryResourceEvidenceReference,
    LibraryResourceTransition,
    LibraryResourceVersion,
    Profile,
)
from src.intevia.services.contribution_authority import ContributionAuthority


class LibraryServiceError(Exception):
    """Base failure for Library orchestration."""


class InvalidLibraryTransition(LibraryServiceError):
    """Raised when a command is invalid for the current lifecycle state."""


class LibraryService:
    """Persist authority-gated Library changes and lineage atomically."""

    _TRANSITIONS = {
        LibraryResource.State.DRAFT: (
            "publish_library_resource",
            LibraryResource.State.PUBLISHED,
        ),
        LibraryResource.State.PUBLISHED: (
            "deprecate_library_resource",
            LibraryResource.State.DEPRECATED,
        ),
        LibraryResource.State.DEPRECATED: (
            "archive_library_resource",
            LibraryResource.State.ARCHIVED,
        ),
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
    def _text(value: str, name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(f"{name} is required")
        return value.strip()

    @staticmethod
    def _locked(resource_id: str) -> LibraryResource:
        return (
            LibraryResource.objects.select_for_update()
            .select_related("current_version")
            .get(resource_id=resource_id)
        )

    @staticmethod
    def _record_transition(
        *,
        resource: LibraryResource,
        version: LibraryResourceVersion,
        prior: str,
        new: str,
        command: str,
        actor: Profile,
        authority_reference: str,
        occurred_at: datetime,
        rationale_reference: str | None = None,
    ) -> LibraryResourceTransition:
        previous = resource.transitions.order_by("occurred_at", "pk").last()
        prior = prior.value if hasattr(prior, "value") else prior
        new = new.value if hasattr(new, "value") else new
        return LibraryResourceTransition.objects.create(
            resource=resource,
            version=version,
            from_state=prior,
            to_state=new,
            command=command,
            actor=actor,
            authority_reference=authority_reference,
            rationale_reference=rationale_reference,
            occurred_at=occurred_at,
            previous_transition=previous,
            lineage_reference=f"library-transition:{uuid4()}",
        )

    @staticmethod
    def _record_evidence(
        *,
        resource: LibraryResource,
        version: LibraryResourceVersion,
        transition: LibraryResourceTransition,
        reference: str,
        reference_type: str,
        actor: Profile,
        authority_reference: str,
        occurred_at: datetime,
    ) -> LibraryResourceEvidenceReference:
        return LibraryResourceEvidenceReference.objects.create(
            resource=resource,
            version=version,
            transition=transition,
            reference=reference,
            reference_type=reference_type,
            supplied_by=actor,
            authority_reference=authority_reference,
            occurred_at=occurred_at,
        )

    @transaction.atomic
    def create_resource(
        self,
        *,
        identity: User,
        resource_id: str,
        content: str,
        evidence_reference: str,
        occurred_at: datetime | None = None,
    ) -> LibraryResource:
        occurred_at = self._at(occurred_at)
        resource_id = self._text(resource_id, "resource_id")
        content = self._text(content, "content")
        evidence_reference = self._text(
            evidence_reference,
            "evidence_reference",
        )
        actor, authority_reference = self.authority.evaluate(
            identity=identity,
            action="create_library_resource",
            target=resource_id,
            timestamp=occurred_at,
        )
        resource = LibraryResource.objects.create(
            resource_id=resource_id,
            created_by=actor,
            created_at=occurred_at,
        )
        version = LibraryResourceVersion.objects.create(
            resource=resource,
            version_number=1,
            content=content,
            created_by=actor,
            created_at=occurred_at,
        )
        resource.current_version = version
        resource.full_clean()
        resource.save(update_fields=("current_version", "updated_at"))
        creation = self._record_transition(
            resource=resource,
            version=version,
            prior="new",
            new=LibraryResource.State.DRAFT,
            command="create_library_resource",
            actor=actor,
            authority_reference=authority_reference,
            occurred_at=occurred_at,
        )
        self._record_evidence(
            resource=resource,
            version=version,
            transition=creation,
            reference=evidence_reference,
            reference_type="creation",
            actor=actor,
            authority_reference=authority_reference,
            occurred_at=occurred_at,
        )
        return resource

    @transaction.atomic
    def create_successor_version(
        self,
        *,
        identity: User,
        resource_id: str,
        content: str,
        evidence_reference: str,
        occurred_at: datetime | None = None,
    ) -> LibraryResourceVersion:
        occurred_at = self._at(occurred_at)
        content = self._text(content, "content")
        evidence_reference = self._text(
            evidence_reference,
            "evidence_reference",
        )
        resource = self._locked(resource_id)
        if resource.state not in (
            LibraryResource.State.DRAFT,
            LibraryResource.State.PUBLISHED,
        ):
            raise InvalidLibraryTransition(
                f"a successor version is not permitted from {resource.state}"
            )
        actor, authority_reference = self.authority.evaluate(
            identity=identity,
            action="create_library_resource_version",
            target=resource,
            timestamp=occurred_at,
        )
        predecessor = resource.current_version
        version = LibraryResourceVersion.objects.create(
            resource=resource,
            version_number=predecessor.version_number + 1,
            content=content,
            predecessor=predecessor,
            created_by=actor,
            created_at=occurred_at,
        )
        prior = resource.state
        resource.current_version = version
        resource.state = LibraryResource.State.DRAFT
        resource.full_clean()
        resource.save(update_fields=("current_version", "state", "updated_at"))
        transition = self._record_transition(
            resource=resource,
            version=version,
            prior=prior,
            new=LibraryResource.State.DRAFT,
            command="create_library_resource_version",
            actor=actor,
            authority_reference=authority_reference,
            occurred_at=occurred_at,
        )
        self._record_evidence(
            resource=resource,
            version=version,
            transition=transition,
            reference=evidence_reference,
            reference_type="version_creation",
            actor=actor,
            authority_reference=authority_reference,
            occurred_at=occurred_at,
        )
        return version

    @transaction.atomic
    def transition_resource(
        self,
        *,
        identity: User,
        resource_id: str,
        command: str,
        evidence_reference: str,
        rationale_reference: str | None = None,
        occurred_at: datetime | None = None,
    ) -> LibraryResourceTransition:
        occurred_at = self._at(occurred_at)
        evidence_reference = self._text(
            evidence_reference,
            "evidence_reference",
        )
        resource = self._locked(resource_id)
        expected = self._TRANSITIONS.get(resource.state)
        if expected is None or expected[0] != command:
            raise InvalidLibraryTransition(
                f"{command} is not permitted from {resource.state}"
            )
        actor, authority_reference = self.authority.evaluate(
            identity=identity,
            action=command,
            target=resource,
            timestamp=occurred_at,
        )
        prior = resource.state
        resource.state = expected[1]
        if resource.state == LibraryResource.State.ARCHIVED:
            resource.archived_at = occurred_at
            resource.save(update_fields=("state", "archived_at", "updated_at"))
        else:
            resource.save(update_fields=("state", "updated_at"))
        transition = self._record_transition(
            resource=resource,
            version=resource.current_version,
            prior=prior,
            new=resource.state,
            command=command,
            actor=actor,
            authority_reference=authority_reference,
            rationale_reference=rationale_reference,
            occurred_at=occurred_at,
        )
        self._record_evidence(
            resource=resource,
            version=resource.current_version,
            transition=transition,
            reference=evidence_reference,
            reference_type="lifecycle_transition",
            actor=actor,
            authority_reference=authority_reference,
            occurred_at=occurred_at,
        )
        return transition

    @staticmethod
    def get_resource(resource_id: str) -> LibraryResource:
        return LibraryResource.objects.select_related("current_version").get(
            resource_id=resource_id
        )

    @staticmethod
    def get_version(
        resource_id: str,
        version_number: int,
    ) -> LibraryResourceVersion:
        return LibraryResourceVersion.objects.get(
            resource__resource_id=resource_id,
            version_number=version_number,
        )

    @staticmethod
    def get_lineage(resource_id: str) -> QuerySet[LibraryResourceVersion]:
        return LibraryResourceVersion.objects.filter(
            resource__resource_id=resource_id
        ).order_by("version_number")

    @staticmethod
    def get_evidence_references(
        resource_id: str,
    ) -> QuerySet[LibraryResourceEvidenceReference]:
        return LibraryResourceEvidenceReference.objects.filter(
            resource__resource_id=resource_id
        ).order_by("occurred_at", "pk")


__all__ = [
    "InvalidLibraryTransition",
    "LibraryService",
    "LibraryServiceError",
]