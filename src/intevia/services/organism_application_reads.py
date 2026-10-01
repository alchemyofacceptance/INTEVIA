from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.db import connections

from core.models import (
    Application,
    ContextualRoleAssignment,
    Identity,
    IdentityTransition,
)
from src.intevia.services.organism_application_service import (
    S015ApplicationNotFound,
    S015ApplicationRefused,
)
from src.intevia.services.organism_role_service import OrganismRoleService


@dataclass(frozen=True)
class ActorRef:
    identity_id: int | None
    resolution: str
    display_name: str | None


@dataclass(frozen=True)
class ApplicationView:
    application_uuid: UUID
    living_organism_id: int
    applicant: ActorRef
    predecessor_application_uuid: UUID | None
    settings_version_uuid: UUID
    question_version_uuid: UUID
    question_text: str
    answer_format: str
    answer: str
    effective_at: datetime
    recorded_at: datetime


@dataclass(frozen=True)
class HistoryRow:
    sequence: int
    action: str
    prior_state: str | None
    resulting_state: str
    actor_state: str
    actor: ActorRef
    actor_capacity: str
    entry_mode: str
    enterer: ActorRef
    composer: ActorRef
    authority_decision_reference: str | None
    evidence_reference: str | None
    effective_at: datetime
    recorded_at: datetime


class OrganismApplicationReader:
    def __init__(self, *, authority, clock, database_alias: str = "default"):
        self._authority = authority
        self._clock = clock
        self._alias = database_alias

    def read_application(self, application_uuid: UUID, reader: Identity) -> ApplicationView:
        application = self._application(application_uuid)
        at = self._database_time()
        self._authorise_read(application, reader, at)

        predecessor_uuid = None
        if application.predecessor_reference_id is not None:
            predecessor_uuid = (
                Application.objects.using(self._alias)
                .filter(pk=application.predecessor_reference_id)
                .values_list("application_uuid", flat=True)
                .first()
            )
        question = application.question_version
        settings = application.settings_version
        return ApplicationView(
            application_uuid=application.application_uuid,
            living_organism_id=application.living_organism_id,
            applicant=self._actor_ref(application.applicant_id),
            predecessor_application_uuid=predecessor_uuid,
            settings_version_uuid=settings.version_uuid,
            question_version_uuid=question.version_uuid,
            question_text=question.question_text,
            answer_format=question.answer_format,
            answer=application.answer,
            effective_at=application.effective_at,
            recorded_at=application.recorded_at,
        )

    def read_application_history(
        self, application_uuid: UUID, reader: Identity,
    ) -> tuple[HistoryRow, ...]:
        application = self._application(application_uuid)
        at = self._database_time()
        self._authorise_read(application, reader, at)
        with connections[self._alias].cursor() as cursor:
            cursor.execute(
                "SELECT e.sequence, e.action, e.prior_state, e.resulting_state, e.actor_id, e.actor_state, e.actor_capacity, "
                "e.entry_mode, e.enterer_identity_id, e.composer_identity_id, e.authority_decision_reference, "
                "e.evidence_reference, e.effective_at, e.recorded_at "
                "FROM public.core_applicationlineageevent e "
                "JOIN public.core_applicationlineage l ON l.id = e.application_lineage_id "
                "WHERE l.application_id = %s "
                "ORDER BY e.sequence",
                [application.pk],
            )
            events = cursor.fetchall()
        return tuple(
            HistoryRow(
                sequence=row[0],
                action=row[1],
                prior_state=row[2],
                resulting_state=row[3],
                actor_state=row[5],
                actor=self._actor_ref(row[4]),
                actor_capacity=row[6],
                entry_mode=row[7],
                enterer=self._actor_ref(row[8]),
                composer=self._actor_ref(row[9]),
                authority_decision_reference=row[10],
                evidence_reference=row[11],
                effective_at=row[12],
                recorded_at=row[13],
            )
            for row in events
        )

    def _authorise_read(self, application: Application, reader: Identity, at: datetime) -> None:
        latest_state = (
            IdentityTransition.objects.using(self._alias)
            .filter(identity_id=reader.pk, occurred_at__lte=at)
            .order_by("-occurred_at", "-pk")
            .values_list("resulting_state", flat=True)
            .first()
        )
        if latest_state != Identity.AccessState.ACTIVE:
            raise S015ApplicationRefused("S3FU read refused: reader not active")
        if reader.pk == application.applicant_id:
            return

        offices = (
            ContextualRoleAssignment.objects.using(self._alias)
            .select_related("role_definition")
            .filter(
                membership__identity_id=reader.pk,
                membership__living_organism_id=application.living_organism_id,
                membership__state="ACTIVE",
                role_definition__code="COORDINATOR",
                role_definition__scope="LIVING_ORGANISM",
                state="ACTIVE",
            )
        )
        role_service = OrganismRoleService(
            authority=self._authority,
            clock=self._clock,
            database_alias=self._alias,
        )
        if any(role_service.assignment_in_effect(office, at) for office in offices):
            return
        raise S015ApplicationRefused("S3FU read refused: not in the application's audience")

    def _application(self, application_uuid: UUID) -> Application:
        application = (
            Application.objects.using(self._alias)
            .filter(application_uuid=application_uuid)
            .first()
        )
        if application is None:
            raise S015ApplicationNotFound("application not found")
        return application

    def _database_time(self) -> datetime:
        with connections[self._alias].cursor() as cursor:
            cursor.execute("SELECT clock_timestamp()")
            return cursor.fetchone()[0]

    def _actor_ref(self, identity_id: int | None) -> ActorRef:
        if identity_id is None:
            return ActorRef(None, "NOT_A_PERSON", None)
        with connections[self._alias].cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM public.core_identityresolutionsevered WHERE identity_id = %s",
                [identity_id],
            )
            if cursor.fetchone() is not None:
                return ActorRef(identity_id, "SEVERED", None)
            cursor.execute(
                "SELECT display_name FROM public.core_identityresolution WHERE identity_id = %s",
                [identity_id],
            )
            resolution = cursor.fetchone()
        if resolution is None:
            return ActorRef(identity_id, "NO_RESOLUTION", None)
        return ActorRef(identity_id, "RESOLVED", resolution[0])