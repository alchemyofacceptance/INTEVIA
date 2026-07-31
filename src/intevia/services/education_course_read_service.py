from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable
from uuid import UUID

from django.contrib.auth.models import User
from django.db import transaction

from core.models import Course, CourseVersion, Identity
from src.intevia.services.education_course_contract import (
    EducationCourseAction,
    EducationCourseAuthorityResponse,
    authority_decision_reference,
    authority_target,
    command_payload,
    lineage_payload,
    lineage_reference,
    payload_fingerprint,
    target_fingerprint,
)
from src.intevia.services.education_course_service import (
    EducationCourseActorError,
    EducationCourseLineageError,
    EducationCourseNotFound,
    EducationCourseValidationError,
)


NEUTRAL_MESSAGE = (
    "This Course is a draft definition. It is not submitted, qualified, "
    "published, available, scheduled, delivered, completed, assessed, or "
    "certified."
)


@dataclass(frozen=True, slots=True)
class CourseCurrentDefinitionDTO:
    course_id: UUID
    version_pk: int
    version_number: int
    predecessor_lineage_reference: str | None
    course_name: str
    course_description: str
    course_learning_objectives: str
    definition_basis_reference: str
    actor_identity_id: UUID
    occurred_at: datetime
    lineage_reference: str
    neutral_message: str


@dataclass(frozen=True, slots=True)
class CourseExactVersionDTO:
    course_id: UUID
    version_pk: int
    version_number: int
    predecessor_lineage_reference: str | None
    is_current: bool
    course_name: str
    course_description: str
    course_learning_objectives: str
    definition_basis_reference: str
    actor_identity_id: UUID
    occurred_at: datetime
    lineage_reference: str
    neutral_message: str


@dataclass(frozen=True, slots=True)
class CourseLineageEntryDTO:
    version_pk: int
    version_number: int
    predecessor_lineage_reference: str | None
    is_current: bool
    course_name: str
    course_description: str
    course_learning_objectives: str
    definition_basis_reference: str
    actor_identity_id: UUID
    occurred_at: datetime
    lineage_reference: str


@dataclass(frozen=True, slots=True)
class CourseLineageDTO:
    course_id: UUID
    current_version_number: int
    versions: tuple[CourseLineageEntryDTO, ...]
    neutral_message: str


class EducationCourseReadService:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime],
        database_alias: str = "default",
    ) -> None:
        if not callable(clock):
            raise TypeError("clock must be callable")
        if type(database_alias) is not str or not database_alias:
            raise ValueError("database_alias is required")
        self._clock = clock
        self._alias = database_alias

    @staticmethod
    def _course_id(value: object) -> UUID:
        if type(value) is not UUID:
            raise EducationCourseValidationError("course_id must be a UUID")
        return value

    @staticmethod
    def _version_number(value: object) -> int:
        if type(value) is not int or value < 1:
            raise EducationCourseValidationError(
                "version_number must be a positive integer"
            )
        return value

    def _lock_viewer(self, credential: User) -> Identity:
        if not isinstance(credential, User) or credential.pk is None:
            raise EducationCourseActorError("viewer credential is invalid")
        try:
            locked_credential = (
                User.objects.using(self._alias)
                .select_for_update()
                .get(pk=credential.pk)
            )
        except User.DoesNotExist as exc:
            raise EducationCourseActorError("viewer credential not found") from exc
        identities = list(
            Identity.objects.using(self._alias)
            .select_for_update()
            .select_related("credential")
            .filter(credential_id=locked_credential.pk)
            .order_by("pk")
        )
        if len(identities) != 1:
            raise EducationCourseActorError("viewer Identity is not unique")
        viewer = identities[0]
        if (
            not locked_credential.is_active
            or viewer.access_state != Identity.AccessState.ACTIVE
            or viewer.credential_id != locked_credential.pk
        ):
            raise EducationCourseActorError("viewer is not active")
        return viewer

    def _lock_visible(
        self, credential: User, course_id: UUID
    ) -> tuple[Course, list[CourseVersion]]:
        viewer = self._lock_viewer(credential)
        root = (
            Course.objects.using(self._alias)
            .select_for_update()
            .filter(course_id=course_id)
            .first()
        )
        if root is None or root.created_by_id != viewer.pk:
            raise EducationCourseNotFound("course not found")
        rows = list(
            CourseVersion.objects.using(self._alias)
            .select_for_update(of=("self",))
            .select_related("actor", "predecessor", "course")
            .filter(course=root)
            .order_by("version_number", "pk")
        )
        self._validate_lineage(root, rows)
        return root, rows

    def _stored_target(self, row: CourseVersion) -> dict[str, object]:
        action = EducationCourseAction(row.action)
        if action is EducationCourseAction.CREATE:
            return authority_target(
                database_alias=self._alias,
                action=action,
                course_id=row.course.course_id,
            )
        if row.predecessor is None:
            raise EducationCourseLineageError("append predecessor is missing")
        return authority_target(
            database_alias=self._alias,
            action=action,
            course_id=row.course.course_id,
            expected_current_version_pk=row.predecessor.pk,
            expected_current_lineage_reference=row.predecessor.lineage_reference,
        )

    def _validate_lineage(
        self, root: Course, rows: list[CourseVersion]
    ) -> None:
        if not rows or root.current_version_id is None:
            raise EducationCourseLineageError("Course lineage is incomplete")
        previous = None
        for position, row in enumerate(rows, start=1):
            expected_action = (
                EducationCourseAction.CREATE
                if previous is None
                else EducationCourseAction.APPEND_VERSION
            )
            if (
                row.course_id != root.pk
                or row.version_number != position
                or row.predecessor_id != (previous.pk if previous else None)
                or row.action != expected_action.value
                or (previous is not None and row.occurred_at <= previous.occurred_at)
            ):
                raise EducationCourseLineageError("Course lineage is malformed")
            target = self._stored_target(row)
            payload = command_payload(
                database_alias=self._alias,
                action=expected_action,
                actor_identity_id=row.actor.identity_id,
                actor_access_epoch=row.actor_access_epoch,
                target=target,
                course_name=row.course_name,
                course_description=row.course_description,
                course_learning_objectives=row.course_learning_objectives,
                definition_basis_reference=row.definition_basis_reference,
                request_reference=row.request_reference,
                idempotency_key=row.idempotency_key,
                occurred_at=row.occurred_at,
            )
            try:
                stored_fingerprint = payload_fingerprint(payload)
                stored_target_fingerprint = target_fingerprint(target)
                response = EducationCourseAuthorityResponse(
                    self._alias,
                    row.actor_id,
                    row.actor.identity_id,
                    row.actor_access_epoch,
                    expected_action,
                    stored_target_fingerprint,
                    row.request_reference,
                    row.idempotency_key,
                    row.authority_evaluated_at,
                    row.authority_reference,
                )
                stored_decision_reference = authority_decision_reference(response)
                expected_lineage = lineage_reference(
                    lineage_payload(
                        database_alias=self._alias,
                        course_id=root.course_id,
                        version_number=row.version_number,
                        predecessor_lineage_reference=(
                            previous.lineage_reference if previous else None
                        ),
                        action=expected_action,
                        actor_identity_id=row.actor.identity_id,
                        actor_access_epoch=row.actor_access_epoch,
                        authority_decision_reference=row.authority_decision_reference,
                        request_reference=row.request_reference,
                        idempotency_key=row.idempotency_key,
                        payload_fingerprint=row.payload_fingerprint,
                        occurred_at=row.occurred_at,
                    )
                )
            except (TypeError, ValueError, UnicodeError) as exc:
                raise EducationCourseLineageError(
                    "Course stored evidence is not canonical"
                ) from exc
            if stored_fingerprint != row.payload_fingerprint:
                raise EducationCourseLineageError("Course payload is malformed")
            if stored_decision_reference != row.authority_decision_reference:
                raise EducationCourseLineageError("Course authority evidence is malformed")
            if expected_lineage != row.lineage_reference:
                raise EducationCourseLineageError("Course lineage reference is malformed")
            previous = row
        if (
            root.created_by_id != rows[0].actor_id
            or root.created_at != rows[0].occurred_at
            or root.current_version_id != rows[-1].pk
        ):
            raise EducationCourseLineageError("Course root binding is malformed")

    @staticmethod
    def _predecessor_reference(row: CourseVersion) -> str | None:
        return row.predecessor.lineage_reference if row.predecessor else None

    def get_current_course(
        self, *, credential: User, course_id: UUID
    ) -> CourseCurrentDefinitionDTO:
        course_id = self._course_id(course_id)
        with transaction.atomic(using=self._alias):
            root, rows = self._lock_visible(credential, course_id)
            row = rows[-1]
            return CourseCurrentDefinitionDTO(
                root.course_id,
                row.pk,
                row.version_number,
                self._predecessor_reference(row),
                row.course_name,
                row.course_description,
                row.course_learning_objectives,
                row.definition_basis_reference,
                row.actor.identity_id,
                row.occurred_at,
                row.lineage_reference,
                NEUTRAL_MESSAGE,
            )

    def get_course_version(
        self, *, credential: User, course_id: UUID, version_number: int
    ) -> CourseExactVersionDTO:
        course_id = self._course_id(course_id)
        version_number = self._version_number(version_number)
        with transaction.atomic(using=self._alias):
            root, rows = self._lock_visible(credential, course_id)
            if version_number > len(rows):
                raise EducationCourseNotFound("course not found")
            row = rows[version_number - 1]
            return CourseExactVersionDTO(
                root.course_id,
                row.pk,
                row.version_number,
                self._predecessor_reference(row),
                row.pk == root.current_version_id,
                row.course_name,
                row.course_description,
                row.course_learning_objectives,
                row.definition_basis_reference,
                row.actor.identity_id,
                row.occurred_at,
                row.lineage_reference,
                NEUTRAL_MESSAGE,
            )

    def get_course_lineage(
        self, *, credential: User, course_id: UUID
    ) -> CourseLineageDTO:
        course_id = self._course_id(course_id)
        with transaction.atomic(using=self._alias):
            root, rows = self._lock_visible(credential, course_id)
            versions = tuple(
                CourseLineageEntryDTO(
                    row.pk,
                    row.version_number,
                    self._predecessor_reference(row),
                    row.pk == root.current_version_id,
                    row.course_name,
                    row.course_description,
                    row.course_learning_objectives,
                    row.definition_basis_reference,
                    row.actor.identity_id,
                    row.occurred_at,
                    row.lineage_reference,
                )
                for row in rows
            )
            return CourseLineageDTO(
                root.course_id, rows[-1].version_number, versions, NEUTRAL_MESSAGE
            )


__all__ = [
    "CourseCurrentDefinitionDTO",
    "CourseExactVersionDTO",
    "CourseLineageDTO",
    "CourseLineageEntryDTO",
    "EducationCourseReadService",
    "NEUTRAL_MESSAGE",
]