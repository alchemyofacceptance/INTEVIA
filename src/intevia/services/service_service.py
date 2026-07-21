"""Transactional orchestration for the governed Service foundation."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from core.models import (
    Event,
    EventTransition,
    LibraryResourceVersion,
    LibraryServiceAssociation,
    Profile,
    Service,
    ServiceDeliveryEvidenceReference,
    ServiceEventAssociation,
    ServiceEvidenceReference,
    ServiceTransition,
    ServiceVersion,
)
from src.intevia.services.contribution_authority import ContributionAuthority


class ServiceFoundationError(Exception):
    """Base failure for governed Service orchestration."""


class InvalidServiceTransition(ServiceFoundationError):
    """Raised when a Service command is invalid for its current state."""


class GovernedService:
    """Persist authority-gated capability pathways and associations."""

    _TRANSITIONS = {
        Service.State.DRAFT: ("publish_service", Service.State.PUBLISHED),
        Service.State.PUBLISHED: ("retire_service", Service.State.RETIRED),
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
    def _locked(service_id: str) -> Service:
        return Service.objects.select_for_update().get(service_id=service_id)

    @staticmethod
    def _require_current_published(version: ServiceVersion) -> Service:
        service = Service.objects.select_for_update().get(pk=version.service_id)
        if (
            service.state != Service.State.PUBLISHED
            or service.current_version_id != version.pk
        ):
            raise InvalidServiceTransition(
                "associations require the current published Service version"
            )
        return service

    @staticmethod
    def _record_transition(
        *,
        service: Service,
        version: ServiceVersion,
        prior: str,
        new: str,
        command: str,
        actor: Profile,
        authority_reference: str,
        occurred_at: datetime,
    ) -> ServiceTransition:
        previous = service.transitions.order_by("occurred_at", "pk").last()
        prior = prior.value if hasattr(prior, "value") else prior
        new = new.value if hasattr(new, "value") else new
        return ServiceTransition.objects.create(
            service=service,
            version=version,
            from_state=prior,
            to_state=new,
            command=command,
            actor=actor,
            authority_reference=authority_reference,
            occurred_at=occurred_at,
            previous_transition=previous,
            lineage_reference=f"service-transition:{uuid4()}",
        )

    @staticmethod
    def _record_evidence(
        *,
        service: Service,
        version: ServiceVersion,
        transition: ServiceTransition,
        reference: str,
        reference_type: str,
        actor: Profile,
        authority_reference: str,
        occurred_at: datetime,
    ) -> ServiceEvidenceReference:
        return ServiceEvidenceReference.objects.create(
            service=service,
            version=version,
            transition=transition,
            reference=reference,
            reference_type=reference_type,
            supplied_by=actor,
            authority_reference=authority_reference,
            occurred_at=occurred_at,
        )

    @transaction.atomic
    def create_service(
        self,
        *,
        identity: User,
        service_id: str,
        capability_purpose: str,
        domain_intent: str,
        evidence_reference: str,
        description: str = "",
        constraints: str = "",
        occurred_at: datetime | None = None,
    ) -> Service:
        occurred_at = self._at(occurred_at)
        service_id = self._text(service_id, "service_id")
        capability_purpose = self._text(
            capability_purpose,
            "capability_purpose",
        )
        domain_intent = self._text(domain_intent, "domain_intent")
        evidence_reference = self._text(
            evidence_reference,
            "evidence_reference",
        )
        actor, authority_reference = self.authority.evaluate(
            identity=identity,
            action="create_service",
            target=service_id,
            timestamp=occurred_at,
        )
        service = Service.objects.create(
            service_id=service_id,
            created_by=actor,
            created_at=occurred_at,
        )
        version = ServiceVersion.objects.create(
            service=service,
            version_number=1,
            capability_purpose=capability_purpose,
            domain_intent=domain_intent,
            description=self._text(description, "description", required=False),
            constraints=self._text(constraints, "constraints", required=False),
            created_by=actor,
            created_at=occurred_at,
        )
        service.current_version = version
        service.full_clean()
        service.save(update_fields=("current_version", "updated_at"))
        transition = self._record_transition(
            service=service,
            version=version,
            prior="new",
            new=Service.State.DRAFT,
            command="create_service",
            actor=actor,
            authority_reference=authority_reference,
            occurred_at=occurred_at,
        )
        self._record_evidence(
            service=service,
            version=version,
            transition=transition,
            reference=evidence_reference,
            reference_type="creation",
            actor=actor,
            authority_reference=authority_reference,
            occurred_at=occurred_at,
        )
        return service

    @transaction.atomic
    def create_successor_version(
        self,
        *,
        identity: User,
        service_id: str,
        capability_purpose: str,
        domain_intent: str,
        evidence_reference: str,
        description: str = "",
        constraints: str = "",
        occurred_at: datetime | None = None,
    ) -> ServiceVersion:
        occurred_at = self._at(occurred_at)
        capability_purpose = self._text(
            capability_purpose,
            "capability_purpose",
        )
        domain_intent = self._text(domain_intent, "domain_intent")
        evidence_reference = self._text(
            evidence_reference,
            "evidence_reference",
        )
        service = self._locked(service_id)
        if service.state not in (Service.State.DRAFT, Service.State.PUBLISHED):
            raise InvalidServiceTransition(
                f"a successor version is not permitted from {service.state}"
            )
        actor, authority_reference = self.authority.evaluate(
            identity=identity,
            action="create_service_version",
            target=service,
            timestamp=occurred_at,
        )
        predecessor = service.current_version
        version = ServiceVersion.objects.create(
            service=service,
            version_number=predecessor.version_number + 1,
            capability_purpose=capability_purpose,
            domain_intent=domain_intent,
            description=self._text(description, "description", required=False),
            constraints=self._text(constraints, "constraints", required=False),
            predecessor=predecessor,
            created_by=actor,
            created_at=occurred_at,
        )
        prior = service.state
        service.current_version = version
        service.state = Service.State.DRAFT
        service.full_clean()
        service.save(update_fields=("current_version", "state", "updated_at"))
        transition = self._record_transition(
            service=service,
            version=version,
            prior=prior,
            new=Service.State.DRAFT,
            command="create_service_version",
            actor=actor,
            authority_reference=authority_reference,
            occurred_at=occurred_at,
        )
        self._record_evidence(
            service=service,
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
    def transition_service(
        self,
        *,
        identity: User,
        service_id: str,
        command: str,
        evidence_reference: str,
        occurred_at: datetime | None = None,
    ) -> ServiceTransition:
        occurred_at = self._at(occurred_at)
        evidence_reference = self._text(
            evidence_reference,
            "evidence_reference",
        )
        service = self._locked(service_id)
        expected = self._TRANSITIONS.get(service.state)
        if expected is None or expected[0] != command:
            raise InvalidServiceTransition(
                f"{command} is not permitted from {service.state}"
            )
        actor, authority_reference = self.authority.evaluate(
            identity=identity,
            action=command,
            target=service,
            timestamp=occurred_at,
        )
        prior = service.state
        service.state = expected[1]
        if service.state == Service.State.RETIRED:
            service.retired_at = occurred_at
            service.save(update_fields=("state", "retired_at", "updated_at"))
        else:
            service.save(update_fields=("state", "updated_at"))
        transition = self._record_transition(
            service=service,
            version=service.current_version,
            prior=prior,
            new=service.state,
            command=command,
            actor=actor,
            authority_reference=authority_reference,
            occurred_at=occurred_at,
        )
        self._record_evidence(
            service=service,
            version=service.current_version,
            transition=transition,
            reference=evidence_reference,
            reference_type="lifecycle_transition",
            actor=actor,
            authority_reference=authority_reference,
            occurred_at=occurred_at,
        )
        return transition

    @transaction.atomic
    def associate_library_resource(
        self,
        *,
        identity: User,
        service_version: ServiceVersion,
        library_resource_version: LibraryResourceVersion,
        evidence_reference: str,
        occurred_at: datetime | None = None,
    ) -> LibraryServiceAssociation:
        occurred_at = self._at(occurred_at)
        evidence_reference = self._text(
            evidence_reference,
            "evidence_reference",
        )
        if not isinstance(service_version, ServiceVersion):
            raise ValidationError("service_version must be a ServiceVersion")
        if not isinstance(library_resource_version, LibraryResourceVersion):
            raise ValidationError(
                "library_resource_version must be a LibraryResourceVersion"
            )
        self._require_current_published(service_version)
        actor, authority_reference = self.authority.evaluate(
            identity=identity,
            action="associate_library_service",
            target=service_version,
            timestamp=occurred_at,
        )
        return LibraryServiceAssociation.objects.create(
            service_version=service_version,
            library_resource_version=library_resource_version,
            actor=actor,
            authority_reference=authority_reference,
            evidence_reference=evidence_reference,
            occurred_at=occurred_at,
            lineage_reference=f"library-service-association:{uuid4()}",
        )

    @transaction.atomic
    def associate_event(
        self,
        *,
        identity: User,
        service_version: ServiceVersion,
        event: Event,
        evidence_reference: str,
        occurred_at: datetime | None = None,
    ) -> ServiceEventAssociation:
        occurred_at = self._at(occurred_at)
        evidence_reference = self._text(
            evidence_reference,
            "evidence_reference",
        )
        if not isinstance(service_version, ServiceVersion):
            raise ValidationError("service_version must be a ServiceVersion")
        if not isinstance(event, Event):
            raise ValidationError("event must be an Event")
        self._require_current_published(service_version)
        actor, authority_reference = self.authority.evaluate(
            identity=identity,
            action="associate_service_event",
            target=service_version,
            timestamp=occurred_at,
        )
        return ServiceEventAssociation.objects.create(
            service_version=service_version,
            event=event,
            actor=actor,
            authority_reference=authority_reference,
            evidence_reference=evidence_reference,
            occurred_at=occurred_at,
            lineage_reference=f"service-event-association:{uuid4()}",
        )

    @transaction.atomic
    def record_delivery_evidence(
        self,
        *,
        identity: User,
        service_event_association: ServiceEventAssociation,
        completed_event_transition: EventTransition,
        evidence_reference: str,
        occurred_at: datetime | None = None,
    ) -> ServiceDeliveryEvidenceReference:
        occurred_at = self._at(occurred_at)
        evidence_reference = self._text(
            evidence_reference,
            "evidence_reference",
        )
        if not isinstance(service_event_association, ServiceEventAssociation):
            raise ValidationError(
                "service_event_association must be a ServiceEventAssociation"
            )
        if not isinstance(completed_event_transition, EventTransition):
            raise ValidationError(
                "completed_event_transition must be an EventTransition"
            )
        if completed_event_transition.event_id != service_event_association.event_id:
            raise ValidationError(
                "completed transition must belong to the associated Event"
            )
        if completed_event_transition.to_state != Event.State.COMPLETED:
            raise ValidationError("delivery evidence requires a completed Event")
        actor, authority_reference = self.authority.evaluate(
            identity=identity,
            action="record_service_delivery_evidence",
            target=service_event_association,
            timestamp=occurred_at,
        )
        return ServiceDeliveryEvidenceReference.objects.create(
            service_event_association=service_event_association,
            completed_event_transition=completed_event_transition,
            reference=evidence_reference,
            supplied_by=actor,
            authority_reference=authority_reference,
            occurred_at=occurred_at,
        )

    @staticmethod
    def get_service(service_id: str) -> Service:
        return Service.objects.select_related("current_version").get(
            service_id=service_id
        )

    @staticmethod
    def get_version(service_id: str, version_number: int) -> ServiceVersion:
        return ServiceVersion.objects.get(
            service__service_id=service_id,
            version_number=version_number,
        )

    @staticmethod
    def get_lineage(service_id: str) -> QuerySet[ServiceVersion]:
        return ServiceVersion.objects.filter(
            service__service_id=service_id
        ).order_by("version_number")


__all__ = [
    "GovernedService",
    "InvalidServiceTransition",
    "ServiceFoundationError",
]