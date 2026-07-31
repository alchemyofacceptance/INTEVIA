from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Callable
from uuid import UUID

from django.contrib.auth.models import User
from django.db import IntegrityError, connections, transaction

from core.models import Course, CourseVersion, Identity
from src.intevia.services.education_course_authority import (
    EducationCourseAuthority,
    EducationCourseAuthorityDenied,
    EducationCourseAuthorityMalformed,
    EducationCourseAuthorityUnavailable,
    EducationCourseError,
)
from src.intevia.services.education_course_contract import (
    AppendCourseVersionCommand,
    AppendCourseVersionReceipt,
    CreateCourseCommand,
    CreateCourseReceipt,
    EducationCourseAction,
    EducationCourseAuthorityRequest,
    EducationCourseAuthorityResponse,
    authority_decision_reference,
    authority_target,
    command_payload,
    lineage_payload,
    lineage_reference,
    payload_fingerprint,
    target_fingerprint,
)


_CREATE_RACE_CONSTRAINTS = frozenset(
    {
        "s014_course_actor_action_idem_uniq",
        "s014_course_id_uniq",
        "s014_course_version_number_uniq",
        "s014_course_initial_version_uniq",
    }
)
_APPEND_RACE_CONSTRAINTS = frozenset(
    {
        "s014_course_actor_action_idem_uniq",
        "s014_course_version_number_uniq",
        "s014_course_predecessor_uniq",
    }
)


class EducationCourseValidationError(EducationCourseError):
    pass


class EducationCourseActorError(EducationCourseError):
    pass


class EducationCourseNotFound(EducationCourseError):
    pass


class EducationCourseCrossEpochConflict(EducationCourseError):
    pass


class EducationCourseIdempotencyCourseConflict(EducationCourseError):
    pass


class EducationCoursePayloadConflict(EducationCourseError):
    pass


class EducationCourseIdentityConflict(EducationCourseError):
    pass


class EducationCourseStaleHeadConflict(EducationCourseError):
    pass


class EducationCourseLineageError(EducationCourseError):
    pass


def _canonical_text(value: object, name: str, maximum: int) -> str:
    if type(value) is not str:
        raise EducationCourseValidationError(f"{name} must be a string")
    canonical = unicodedata.normalize("NFC", value).strip()
    if not canonical:
        raise EducationCourseValidationError(f"{name} is required")
    if len(canonical) > maximum:
        raise EducationCourseValidationError(f"{name} exceeds {maximum} code points")
    return canonical


def _exact_uuid(value: object, name: str) -> UUID:
    if type(value) is not UUID:
        raise EducationCourseValidationError(f"{name} must be a UUID")
    return value


def _exact_positive_int(value: object, name: str) -> int:
    if type(value) is not int or value < 1:
        raise EducationCourseValidationError(f"{name} must be a positive integer")
    return value


def _aware_utc(value: object, name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise EducationCourseValidationError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


class EducationCourseService:
    def __init__(
        self,
        *,
        authority: EducationCourseAuthority,
        clock: Callable[[], datetime],
        database_alias: str = "default",
    ) -> None:
        if type(authority) is not EducationCourseAuthority:
            raise TypeError("authority must be an EducationCourseAuthority")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if type(database_alias) is not str or not database_alias:
            raise ValueError("database_alias is required")
        if authority.database_alias != database_alias:
            raise ValueError("authority database_alias mismatch")
        self._authority = authority
        self._clock = clock
        self._alias = database_alias

    def _validate_create(self, command: CreateCourseCommand) -> dict[str, object]:
        if type(command) is not CreateCourseCommand:
            raise EducationCourseValidationError("command type is invalid")
        return self._validate_common(command)

    def _validate_append(
        self, command: AppendCourseVersionCommand
    ) -> dict[str, object]:
        if type(command) is not AppendCourseVersionCommand:
            raise EducationCourseValidationError("command type is invalid")
        values = self._validate_common(command)
        values["expected_current_version_pk"] = _exact_positive_int(
            command.expected_current_version_pk, "expected_current_version_pk"
        )
        expected_lineage = _canonical_text(
            command.expected_current_lineage_reference,
            "expected_current_lineage_reference",
            71,
        )
        if re.fullmatch(r"s014l1:[0-9a-f]{64}", expected_lineage) is None:
            raise EducationCourseValidationError(
                "expected_current_lineage_reference is malformed"
            )
        values["expected_current_lineage_reference"] = expected_lineage
        return values

    def _validate_common(self, command: object) -> dict[str, object]:
        credential = getattr(command, "credential", None)
        if not isinstance(credential, User) or credential.pk is None:
            raise EducationCourseValidationError("credential must be a durable User")
        return {
            "credential": credential,
            "course_id": _exact_uuid(getattr(command, "course_id"), "course_id"),
            "course_name": _canonical_text(
                getattr(command, "course_name"), "course_name", 45
            ),
            "course_description": _canonical_text(
                getattr(command, "course_description"), "course_description", 4096
            ),
            "course_learning_objectives": _canonical_text(
                getattr(command, "course_learning_objectives"),
                "course_learning_objectives",
                4096,
            ),
            "definition_basis_reference": _canonical_text(
                getattr(command, "definition_basis_reference"),
                "definition_basis_reference",
                255,
            ),
            "request_reference": _canonical_text(
                getattr(command, "request_reference"), "request_reference", 128
            ),
            "idempotency_key": _canonical_text(
                getattr(command, "idempotency_key"), "idempotency_key", 120
            ),
            "occurred_at": _aware_utc(getattr(command, "occurred_at"), "occurred_at"),
        }

    def _lock_actor(self, credential: User) -> Identity:
        try:
            locked_credential = (
                User.objects.using(self._alias)
                .select_for_update()
                .get(pk=credential.pk)
            )
        except User.DoesNotExist as exc:
            raise EducationCourseActorError("actor credential not found") from exc
        identities = list(
            Identity.objects.using(self._alias)
            .select_for_update()
            .select_related("credential")
            .filter(credential_id=locked_credential.pk)
            .order_by("pk")
        )
        if len(identities) != 1:
            raise EducationCourseActorError("actor Identity is not unique")
        actor = identities[0]
        if (
            not locked_credential.is_active
            or actor.access_state != Identity.AccessState.ACTIVE
            or actor.credential_id != locked_credential.pk
        ):
            raise EducationCourseActorError("actor is not active")
        return actor

    def _discover_course_ids(
        self,
        *,
        actor: Identity,
        action: EducationCourseAction,
        idempotency_key: str,
        requested_course_id: UUID,
    ) -> set[UUID]:
        course_ids = {requested_course_id}
        discovered = (
            CourseVersion.objects.using(self._alias)
            .filter(actor=actor, action=action.value, idempotency_key=idempotency_key)
            .values_list("course__course_id", flat=True)
            .first()
        )
        if discovered is not None:
            course_ids.add(discovered)
        return course_ids

    def _lock_roots_and_versions(
        self, course_ids: set[UUID]
    ) -> tuple[dict[UUID, Course], dict[int, list[CourseVersion]]]:
        roots_by_id: dict[UUID, Course] = {}
        rows_by_root: dict[int, list[CourseVersion]] = {}
        for course_id in sorted(course_ids, key=lambda value: value.bytes):
            root = (
                Course.objects.using(self._alias)
                .select_for_update()
                .filter(course_id=course_id)
                .first()
            )
            if root is not None:
                roots_by_id[root.course_id] = root
        for root in sorted(roots_by_id.values(), key=lambda value: value.course_id.bytes):
            rows_by_root[root.pk] = list(
                CourseVersion.objects.using(self._alias)
                .select_for_update(of=("self",))
                .select_related("actor", "predecessor", "course")
                .filter(course=root)
                .order_by("version_number", "pk")
            )
        return roots_by_id, rows_by_root

    def _current_winner(
        self,
        *,
        actor: Identity,
        action: EducationCourseAction,
        idempotency_key: str,
        rows_by_root: dict[int, list[CourseVersion]],
    ) -> CourseVersion | None:
        winners = [
            row
            for rows in rows_by_root.values()
            for row in rows
            if row.actor_id == actor.pk
            and row.action == action.value
            and row.idempotency_key == idempotency_key
        ]
        if len(winners) > 1:
            raise EducationCourseLineageError("global idempotency winner is not unique")
        return winners[0] if winners else None

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

    def _stored_payload(self, row: CourseVersion) -> dict[str, object]:
        return command_payload(
            database_alias=self._alias,
            action=EducationCourseAction(row.action),
            actor_identity_id=row.actor.identity_id,
            actor_access_epoch=row.actor_access_epoch,
            target=self._stored_target(row),
            course_name=row.course_name,
            course_description=row.course_description,
            course_learning_objectives=row.course_learning_objectives,
            definition_basis_reference=row.definition_basis_reference,
            request_reference=row.request_reference,
            idempotency_key=row.idempotency_key,
            occurred_at=row.occurred_at,
        )

    def _validate_lineage(
        self, root: Course, rows: list[CourseVersion]
    ) -> None:
        if not rows or root.current_version_id is None:
            raise EducationCourseLineageError("Course lineage is incomplete")
        previous = None
        for position, row in enumerate(rows, start=1):
            if row.course_id != root.pk or row.version_number != position:
                raise EducationCourseLineageError("Course lineage is gapped")
            if row.predecessor_id != (previous.pk if previous else None):
                raise EducationCourseLineageError("Course lineage is branched")
            expected_action = (
                EducationCourseAction.CREATE
                if previous is None
                else EducationCourseAction.APPEND_VERSION
            )
            if row.action != expected_action.value:
                raise EducationCourseLineageError("Course lineage action is invalid")
            if previous is not None and row.occurred_at <= previous.occurred_at:
                raise EducationCourseLineageError("Course occurrence order is invalid")
            try:
                stored_fingerprint = payload_fingerprint(self._stored_payload(row))
                stored_target_fingerprint = target_fingerprint(
                    self._stored_target(row)
                )
                response = EducationCourseAuthorityResponse(
                    database_alias=self._alias,
                    actor_pk=row.actor_id,
                    actor_identity_id=row.actor.identity_id,
                    actor_access_epoch=row.actor_access_epoch,
                    action=expected_action,
                    target_fingerprint=stored_target_fingerprint,
                    request_reference=row.request_reference,
                    idempotency_key=row.idempotency_key,
                    evaluated_at=row.authority_evaluated_at,
                    authority_reference=row.authority_reference,
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
                raise EducationCourseLineageError("Course payload fingerprint is invalid")
            if stored_decision_reference != row.authority_decision_reference:
                raise EducationCourseLineageError("Course authority evidence is invalid")
            if row.lineage_reference != expected_lineage:
                raise EducationCourseLineageError("Course lineage reference is invalid")
            previous = row
        first = rows[0]
        if root.created_by_id != first.actor_id or root.created_at != first.occurred_at:
            raise EducationCourseLineageError("Course creation binding is invalid")
        if root.current_version_id != rows[-1].pk:
            raise EducationCourseLineageError("Course current version is not terminal")

    def _authority_request(
        self,
        *,
        actor: Identity,
        action: EducationCourseAction,
        target: dict[str, object],
        request_reference: str,
        idempotency_key: str,
    ) -> EducationCourseAuthorityRequest:
        evaluated_at = _aware_utc(self._clock(), "authority evaluated_at")
        return EducationCourseAuthorityRequest(
            database_alias=self._alias,
            actor_pk=actor.pk,
            actor_identity_id=actor.identity_id,
            actor_access_epoch=actor.access_epoch,
            action=action,
            target_fingerprint=target_fingerprint(target),
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            evaluated_at=evaluated_at,
        )

    def _classify_winner(
        self,
        *,
        winner: CourseVersion | None,
        requested_course_id: UUID,
        actor: Identity,
        requested_fingerprint: str,
        roots: dict[UUID, Course],
        rows_by_root: dict[int, list[CourseVersion]],
    ) -> CourseVersion | None:
        if winner is None:
            return None
        if winner.actor_access_epoch != actor.access_epoch:
            raise EducationCourseCrossEpochConflict("cross-epoch conflict")
        if winner.course.course_id != requested_course_id:
            raise EducationCourseIdempotencyCourseConflict(
                "idempotency key names another Course"
            )
        if winner.payload_fingerprint != requested_fingerprint:
            raise EducationCoursePayloadConflict("payload conflict")
        root = roots[winner.course.course_id]
        self._validate_lineage(root, rows_by_root[root.pk])
        return winner

    def _constraint_name(self, exc: IntegrityError) -> str | None:
        cause = getattr(exc, "__cause__", None)
        diagnostics = getattr(cause, "diag", None) if cause is not None else None
        return (
            getattr(diagnostics, "constraint_name", None)
            if diagnostics is not None
            else None
        )

    def _recover_create_race(
        self,
        *,
        exc: IntegrityError,
        actor: Identity,
        values: dict[str, object],
        fingerprint: str,
    ) -> CreateCourseReceipt:
        constraint_name = self._constraint_name(exc)
        if (
            connections[self._alias].vendor != "postgresql"
            or constraint_name not in _CREATE_RACE_CONSTRAINTS
        ):
            raise exc
        course_ids = self._discover_course_ids(
            actor=actor,
            action=EducationCourseAction.CREATE,
            idempotency_key=values["idempotency_key"],
            requested_course_id=values["course_id"],
        )
        roots, rows = self._lock_roots_and_versions(course_ids)
        winner = self._current_winner(
            actor=actor,
            action=EducationCourseAction.CREATE,
            idempotency_key=values["idempotency_key"],
            rows_by_root=rows,
        )
        winner = self._classify_winner(
            winner=winner,
            requested_course_id=values["course_id"],
            actor=actor,
            requested_fingerprint=fingerprint,
            roots=roots,
            rows_by_root=rows,
        )
        if winner is not None:
            return self._create_receipt(winner, self._alias, True)
        root = roots.get(values["course_id"])
        if root is not None:
            self._validate_lineage(root, rows[root.pk])
            raise EducationCourseIdentityConflict("Course identity conflict")
        raise exc

    def _recover_append_race(
        self,
        *,
        exc: IntegrityError,
        actor: Identity,
        values: dict[str, object],
        fingerprint: str,
    ) -> AppendCourseVersionReceipt:
        constraint_name = self._constraint_name(exc)
        if (
            connections[self._alias].vendor != "postgresql"
            or constraint_name not in _APPEND_RACE_CONSTRAINTS
        ):
            raise exc
        course_ids = self._discover_course_ids(
            actor=actor,
            action=EducationCourseAction.APPEND_VERSION,
            idempotency_key=values["idempotency_key"],
            requested_course_id=values["course_id"],
        )
        roots, rows = self._lock_roots_and_versions(course_ids)
        winner = self._current_winner(
            actor=actor,
            action=EducationCourseAction.APPEND_VERSION,
            idempotency_key=values["idempotency_key"],
            rows_by_root=rows,
        )
        winner = self._classify_winner(
            winner=winner,
            requested_course_id=values["course_id"],
            actor=actor,
            requested_fingerprint=fingerprint,
            roots=roots,
            rows_by_root=rows,
        )
        if winner is not None:
            return self._append_receipt(winner, self._alias, True)
        root = roots.get(values["course_id"])
        if root is None:
            raise EducationCourseNotFound("course not found")
        self._validate_lineage(root, rows[root.pk])
        raise EducationCourseStaleHeadConflict("stale Course head")

    @staticmethod
    def _create_receipt(row: CourseVersion, alias: str, replayed: bool):
        return CreateCourseReceipt(
            alias,
            row.course.course_id,
            row.pk,
            row.version_number,
            row.lineage_reference,
            row.request_reference,
            row.idempotency_key,
            row.payload_fingerprint,
            row.occurred_at,
            replayed,
        )

    @staticmethod
    def _append_receipt(row: CourseVersion, alias: str, replayed: bool):
        if row.predecessor is None:
            raise EducationCourseLineageError("append predecessor is missing")
        return AppendCourseVersionReceipt(
            alias,
            row.course.course_id,
            row.pk,
            row.version_number,
            row.predecessor.pk,
            row.predecessor.lineage_reference,
            row.lineage_reference,
            row.request_reference,
            row.idempotency_key,
            row.payload_fingerprint,
            row.occurred_at,
            replayed,
        )

    def create(self, command: CreateCourseCommand) -> CreateCourseReceipt:
        values = self._validate_create(command)
        return self._execute_create(values)

    def _execute_create(self, values: dict[str, object]) -> CreateCourseReceipt:
        with transaction.atomic(using=self._alias):
            actor = self._lock_actor(values["credential"])
            action = EducationCourseAction.CREATE
            target = authority_target(
                database_alias=self._alias,
                action=action,
                course_id=values["course_id"],
            )
            payload = command_payload(
                database_alias=self._alias,
                action=action,
                actor_identity_id=actor.identity_id,
                actor_access_epoch=actor.access_epoch,
                target=target,
                course_name=values["course_name"],
                course_description=values["course_description"],
                course_learning_objectives=values["course_learning_objectives"],
                definition_basis_reference=values["definition_basis_reference"],
                request_reference=values["request_reference"],
                idempotency_key=values["idempotency_key"],
                occurred_at=values["occurred_at"],
            )
            fingerprint = payload_fingerprint(payload)
            course_ids = self._discover_course_ids(
                actor=actor,
                action=action,
                idempotency_key=values["idempotency_key"],
                requested_course_id=values["course_id"],
            )
            roots, rows = self._lock_roots_and_versions(course_ids)
            winner = self._current_winner(
                actor=actor,
                action=action,
                idempotency_key=values["idempotency_key"],
                rows_by_root=rows,
            )
            decision = self._authority.qualify(
                self._authority_request(
                    actor=actor,
                    action=action,
                    target=target,
                    request_reference=values["request_reference"],
                    idempotency_key=values["idempotency_key"],
                )
            )
            winner = self._classify_winner(
                winner=winner,
                requested_course_id=values["course_id"],
                actor=actor,
                requested_fingerprint=fingerprint,
                roots=roots,
                rows_by_root=rows,
            )
            if winner is not None:
                return self._create_receipt(winner, self._alias, True)
            if values["course_id"] in roots:
                self._validate_lineage(roots[values["course_id"]], rows[roots[values["course_id"]].pk])
                raise EducationCourseIdentityConflict("Course identity conflict")
            lineage = lineage_reference(
                lineage_payload(
                    database_alias=self._alias,
                    course_id=values["course_id"],
                    version_number=1,
                    predecessor_lineage_reference=None,
                    action=action,
                    actor_identity_id=actor.identity_id,
                    actor_access_epoch=actor.access_epoch,
                    authority_decision_reference=decision.authority_decision_reference,
                    request_reference=values["request_reference"],
                    idempotency_key=values["idempotency_key"],
                    payload_fingerprint=fingerprint,
                    occurred_at=values["occurred_at"],
                )
            )
            try:
                with transaction.atomic(using=self._alias):
                    course = Course.objects.using(self._alias).create(
                        course_id=values["course_id"],
                        created_by=actor,
                        created_at=values["occurred_at"],
                    )
                    version = CourseVersion.objects.using(self._alias).create(
                        course=course,
                        version_number=1,
                        predecessor=None,
                        action=action.value,
                        course_name=values["course_name"],
                        course_description=values["course_description"],
                        course_learning_objectives=values["course_learning_objectives"],
                        definition_basis_reference=values["definition_basis_reference"],
                        actor=actor,
                        actor_access_epoch=actor.access_epoch,
                        authority_reference=decision.authority_reference,
                        authority_decision_reference=decision.authority_decision_reference,
                        authority_evaluated_at=decision.evaluated_at,
                        request_reference=values["request_reference"],
                        idempotency_key=values["idempotency_key"],
                        payload_fingerprint=fingerprint,
                        occurred_at=values["occurred_at"],
                        lineage_reference=lineage,
                    )
                    course._advance_current_version(version)
            except IntegrityError as exc:
                return self._recover_create_race(
                    exc=exc,
                    actor=actor,
                    values=values,
                    fingerprint=fingerprint,
                )
            return self._create_receipt(version, self._alias, False)

    def append_version(
        self, command: AppendCourseVersionCommand
    ) -> AppendCourseVersionReceipt:
        values = self._validate_append(command)
        with transaction.atomic(using=self._alias):
            actor = self._lock_actor(values["credential"])
            action = EducationCourseAction.APPEND_VERSION
            target = authority_target(
                database_alias=self._alias,
                action=action,
                course_id=values["course_id"],
                expected_current_version_pk=values["expected_current_version_pk"],
                expected_current_lineage_reference=values[
                    "expected_current_lineage_reference"
                ],
            )
            payload = command_payload(
                database_alias=self._alias,
                action=action,
                actor_identity_id=actor.identity_id,
                actor_access_epoch=actor.access_epoch,
                target=target,
                course_name=values["course_name"],
                course_description=values["course_description"],
                course_learning_objectives=values["course_learning_objectives"],
                definition_basis_reference=values["definition_basis_reference"],
                request_reference=values["request_reference"],
                idempotency_key=values["idempotency_key"],
                occurred_at=values["occurred_at"],
            )
            fingerprint = payload_fingerprint(payload)
            course_ids = self._discover_course_ids(
                actor=actor,
                action=action,
                idempotency_key=values["idempotency_key"],
                requested_course_id=values["course_id"],
            )
            roots, rows = self._lock_roots_and_versions(course_ids)
            winner = self._current_winner(
                actor=actor,
                action=action,
                idempotency_key=values["idempotency_key"],
                rows_by_root=rows,
            )
            decision = self._authority.qualify(
                self._authority_request(
                    actor=actor,
                    action=action,
                    target=target,
                    request_reference=values["request_reference"],
                    idempotency_key=values["idempotency_key"],
                )
            )
            winner = self._classify_winner(
                winner=winner,
                requested_course_id=values["course_id"],
                actor=actor,
                requested_fingerprint=fingerprint,
                roots=roots,
                rows_by_root=rows,
            )
            if winner is not None:
                return self._append_receipt(winner, self._alias, True)
            root = roots.get(values["course_id"])
            if root is None:
                raise EducationCourseNotFound("course not found")
            lineage_rows = rows[root.pk]
            self._validate_lineage(root, lineage_rows)
            current = lineage_rows[-1]
            if (
                current.pk != values["expected_current_version_pk"]
                or current.lineage_reference
                != values["expected_current_lineage_reference"]
            ):
                raise EducationCourseStaleHeadConflict("stale Course head")
            if values["occurred_at"] <= current.occurred_at:
                raise EducationCourseValidationError(
                    "occurred_at must be later than expected current version"
                )
            next_number = current.version_number + 1
            next_lineage = lineage_reference(
                lineage_payload(
                    database_alias=self._alias,
                    course_id=root.course_id,
                    version_number=next_number,
                    predecessor_lineage_reference=current.lineage_reference,
                    action=action,
                    actor_identity_id=actor.identity_id,
                    actor_access_epoch=actor.access_epoch,
                    authority_decision_reference=decision.authority_decision_reference,
                    request_reference=values["request_reference"],
                    idempotency_key=values["idempotency_key"],
                    payload_fingerprint=fingerprint,
                    occurred_at=values["occurred_at"],
                )
            )
            try:
                with transaction.atomic(using=self._alias):
                    version = CourseVersion.objects.using(self._alias).create(
                        course=root,
                        version_number=next_number,
                        predecessor=current,
                        action=action.value,
                        course_name=values["course_name"],
                        course_description=values["course_description"],
                        course_learning_objectives=values["course_learning_objectives"],
                        definition_basis_reference=values["definition_basis_reference"],
                        actor=actor,
                        actor_access_epoch=actor.access_epoch,
                        authority_reference=decision.authority_reference,
                        authority_decision_reference=decision.authority_decision_reference,
                        authority_evaluated_at=decision.evaluated_at,
                        request_reference=values["request_reference"],
                        idempotency_key=values["idempotency_key"],
                        payload_fingerprint=fingerprint,
                        occurred_at=values["occurred_at"],
                        lineage_reference=next_lineage,
                    )
                    root._advance_current_version(version)
            except IntegrityError as exc:
                return self._recover_append_race(
                    exc=exc,
                    actor=actor,
                    values=values,
                    fingerprint=fingerprint,
                )
            return self._append_receipt(version, self._alias, False)


__all__ = [
    "EducationCourseActorError",
    "EducationCourseAuthorityDenied",
    "EducationCourseAuthorityMalformed",
    "EducationCourseAuthorityUnavailable",
    "EducationCourseCrossEpochConflict",
    "EducationCourseError",
    "EducationCourseIdempotencyCourseConflict",
    "EducationCourseIdentityConflict",
    "EducationCourseLineageError",
    "EducationCourseNotFound",
    "EducationCoursePayloadConflict",
    "EducationCourseService",
    "EducationCourseStaleHeadConflict",
    "EducationCourseValidationError",
]