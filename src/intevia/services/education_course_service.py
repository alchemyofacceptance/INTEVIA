"""Create a Course definition and append linked versions (S014).

Both writes lock the actor and relevant Course records and ask
the authority to qualify the request. The actor, action and key
locate a prior version. Replay also requires a matching epoch,
Course and payload fingerprint, and a valid stored lineage.

A new version records its authority and advances the Course
head. Only named PostgreSQL uniqueness failures enter race
recovery. An unresolved create race can raise IntegrityError.
"""

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
    """A command value, clock value or append date is invalid.

    Before locking, the writer checks command type, credential
    and field values. Under lock it also checks the service
    clock when forming the authority request. A new append
    must occur strictly later than its current version. The
    read service raises it for a malformed course_id or
    version_number.
    """
    pass


class EducationCourseActorError(EducationCourseError):
    """The requesting user cannot act as one active Identity.

    Raised for a missing user, zero or several Identities, or an
    inactive user or Identity. The read service also raises it
    for a credential that is not a User with a primary key.
    """
    pass


class EducationCourseNotFound(EducationCourseError):
    """No visible Course or version matches the request.

    The service raises it when an append names an absent Course.
    The read service also raises it when the Course is absent or
    was created by another Identity, or when the version number
    is past the Course's last version.
    """
    pass


class EducationCourseCrossEpochConflict(EducationCourseError):
    """A replay was found after the actor's access epoch changed.

    The stored version under this idempotency key was written at
    another access epoch, so it is not returned as a replay.
    """
    pass


class EducationCourseIdempotencyCourseConflict(EducationCourseError):
    """Same actor, action and key, but a different Course."""
    pass


class EducationCoursePayloadConflict(EducationCourseError):
    """The stored and requested payload fingerprints differ.

    The actor, action, key, Course and access epoch match.
    """
    pass


class EducationCourseIdentityConflict(EducationCourseError):
    """Create found an existing Course with no replay."""
    pass


class EducationCourseStaleHeadConflict(EducationCourseError):
    """An append cannot proceed against its expected head.

    The current version ID or lineage reference differs, or a
    recognised append race leaves a valid Course and no replay.
    """
    pass


class EducationCourseLineageError(EducationCourseError):
    """Stored Course versions or their evidence disagree.

    Covers missing, gapped or branched lineage, wrong action or
    occurrence order, and a missing append predecessor. Evidence
    may fail canonicalisation or disagree with the recomputed
    payload fingerprint, decision reference or lineage hash.
    The Course must match its first version's creator and time
    and point to its last version. Multiple stored winners for
    one actor, action and key also raise this error.
    """
    pass


def _canonical_text(value: object, name: str, maximum: int) -> str:
    """Return the NFC-normalised, stripped text.

    Require an exact str, normalise to NFC, then strip both ends.
    Reject an empty result before comparing its code-point count
    with maximum. Failures raise EducationCourseValidationError
    naming the field. Internal whitespace and controls can remain.
    The limit itself is unchecked. Comparing a nonempty result with
    an incompatible limit can raise TypeError.
    """
    if type(value) is not str:
        raise EducationCourseValidationError(f"{name} must be a string")
    canonical = unicodedata.normalize("NFC", value).strip()
    if not canonical:
        raise EducationCourseValidationError(f"{name} is required")
    if len(canonical) > maximum:
        raise EducationCourseValidationError(f"{name} exceeds {maximum} code points")
    return canonical


def _exact_uuid(value: object, name: str) -> UUID:
    """Return value if it is exactly a uuid.UUID.

    A string form or a UUID subclass raises
    EducationCourseValidationError.
    """
    if type(value) is not UUID:
        raise EducationCourseValidationError(f"{name} must be a UUID")
    return value


def _exact_positive_int(value: object, name: str) -> int:
    """Return value if it is exactly an int of at least 1.

    bool, float and str values raise EducationCourseValidationError,
    as do zero and negative numbers.
    """
    if type(value) is not int or value < 1:
        raise EducationCourseValidationError(f"{name} must be a positive integer")
    return value


def _aware_utc(value: object, name: str) -> datetime:
    """Convert an exact datetime with a UTC offset to UTC.

    Raise EducationCourseValidationError for the wrong type, absent
    tzinfo or a None offset. Errors raised by the timezone or UTC
    conversion itself propagate.
    """
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise EducationCourseValidationError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


class EducationCourseService:
    """Create a Course or append a version under authority.

    Each command validates its input before opening its atomic
    block. It locks the actor, discovers Courses for the key,
    locks roots then versions, and looks for a prior result.
    It asks for authority before classifying a single winner.

    A matching replay returns the stored version's receipt.
    A new create writes version one. A new append must match
    the current head and occur later. Each new write stores
    its version and advances the head in the same transaction.
    """
    def __init__(
        self,
        *,
        authority: EducationCourseAuthority,
        clock: Callable[[], datetime],
        database_alias: str = "default",
    ) -> None:
        """Bind an authority, clock and alias. Return None.

        Arguments are keyword-only. First require an exact
        EducationCourseAuthority, then a callable clock.
        Either failure raises TypeError. Next require an exact
        nonempty str alias (default "default"), then equality
        with the authority's alias. Either failure raises
        ValueError. Store the three values without looking up
        the alias or calling the clock. Its result is checked
        when an authority request is prepared.
        """
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
        """Validate a CreateCourseCommand; return its canonical values.

        The command must be exactly a CreateCourseCommand; the fields
        are then checked by _validate_common. Raises
        EducationCourseValidationError.
        """
        if type(command) is not CreateCourseCommand:
            raise EducationCourseValidationError("command type is invalid")
        return self._validate_common(command)

    def _validate_append(
        self, command: AppendCourseVersionCommand
    ) -> dict[str, object]:
        """Return validated append fields in a dict.

        Require an exact AppendCourseVersionCommand, then
        validate its common fields. Require an exact int of
        at least one for expected_current_version_pk. Require
        an exact str for expected_current_lineage_reference.
        Normalise it to NFC, strip, reject empty or over 71
        code points, then require s014l1: followed by exactly
        64 lowercase hex digits. Add both values to the dict.

        Failed checks raise EducationCourseValidationError.
        Errors reading fields or converting the common time
        propagate as in _validate_common. A later locked check
        compares the supplied head with the current head.
        """
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
        """Return the common fields in a dict, in check order.

        Require a User (subclasses allowed) with a non-None
        pk, then an exact UUID for course_id. Check exact str
        fields in order, using NFC and strip, then requiring
        nonempty text within these code-point limits:
        course_name 45, course_description 4096,
        course_learning_objectives 4096,
        definition_basis_reference 255, request_reference 128,
        idempotency_key 120. Finally, occurred_at must be an
        exact datetime with tzinfo and a non-None UTC offset.
        Return it in UTC, with the original User and UUID.

        Failed checks raise EducationCourseValidationError.
        Offset errors such as TypeError or ValueError propagate,
        as does OverflowError on UTC conversion. User existence
        and activity are checked later under lock.
        """
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
        """Lock the User and matching rows. Return the Identity.

        On this alias, lock the User by pk. Translate
        User.DoesNotExist to EducationCourseActorError.
        Lock matching identities in pk order, joining their
        credential. Zero or multiple rows raise the same
        actor error for a non-unique Identity. Then require
        an active User, ACTIVE Identity and matching credential
        ID, in that order. Failure raises the inactive-actor
        error. The access epoch is unchecked here.

        Alias lookup errors escape. The commands supply the
        transaction. Query errors propagate, including
        TransactionManagementError for a PostgreSQL row lock
        attempted in autocommit.
        """
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
        """Return a set containing the requested Course UUID.

        Look up the first version for this actor, action and
        key. Add its Course UUID if found. This lookup takes
        no row lock. The caller locks the discovered Courses.
        """
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
        """Lock Course roots first, then their versions.

        Visit UUIDs in byte order. For each, take the first
        matching root FOR UPDATE and skip absent roots. Visit
        found roots in the same order. Lock only version rows
        in each joined query, ordered by version number then
        pk. Return a pair of dicts: roots by UUID and version
        lists by root pk. These reads do not validate lineage.
        """
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
        """Return the stored version this key already produced, if any.

        Searches the locked rows for one by this actor with the same
        action and idempotency key. Returns None when there is none and
        raises EducationCourseLineageError when there is more than one.
        Rows outside the locked Courses are not searched.
        """
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
        """Rebuild the authority target for the stored action.

        An unknown action raises ValueError. Both targets name
        the schema, contract version, service alias, action and
        Course UUID. For APPEND_VERSION also include the
        predecessor's pk and lineage reference as the expected
        head. A missing predecessor raises
        EducationCourseLineageError.
        """
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
        """Rebuild the command payload of a stored version.

        Use the row's action, its actor's Identity id, its stored
        access epoch, the target from _stored_target, and its stored
        name, description, objectives, basis reference, request
        reference, key and occurred_at. Errors from _stored_target
        propagate. The caller fingerprints the result.
        """
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
        """Validate the supplied Course chain. Return None.

        Require rows and a current-version id. In supplied
        order, require each row to belong to the root and have
        its one-based position as its version number. Require
        the predecessor id to be absent first, then the previous
        pk. Require CREATE first and APPEND_VERSION thereafter,
        with strictly increasing occurrence times.

        Rebuild the payload fingerprint, then the target
        fingerprint used in the stored authority response.
        Derive the decision reference from that response and the
        lineage reference from the previous lineage and the
        row's stored evidence. In that rebuilding step,
        translate TypeError, ValueError and UnicodeError to
        EducationCourseLineageError.

        Compare payload fingerprint, decision reference and
        lineage reference with their stored values, in that
        order. Finally match root creator and creation time to
        the first actor and occurrence, then the current-version
        id to the last pk. Failed checks raise
        EducationCourseLineageError.

        The caller supplies ordered, locked rows. This checks
        evidence agreement without asking the provider or
        rerunning command or model validation. Other errors
        propagate, including a TypeError comparing times before
        rebuilding.
        """
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
        """Build a request for authority qualification.

        Call the clock once. Require an exact datetime with a
        UTC offset or raise EducationCourseValidationError. Use
        its UTC value as evaluated_at. Include service alias,
        actor pk, identity UUID and epoch, action, target
        fingerprint, request reference and key.

        Clock, offset, UTC conversion and fingerprinting errors
        propagate. The caller submits the request to qualify.
        """
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
        """Accept the prior result for this key, or raise a conflict.

        Return None when there is no winner. Otherwise check in order:
        the winner's access epoch is the actor's, else
        EducationCourseCrossEpochConflict; it names the requested
        Course, else EducationCourseIdempotencyCourseConflict; its
        payload fingerprint is the request's, else
        EducationCoursePayloadConflict. Then validate the winner's
        whole Course chain and return the winner.
        """
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
        """Return the driver's constraint name from an IntegrityError.

        Return exc.__cause__.diag.constraint_name when present.
        A missing cause, diagnostic object or name yields None.
        """
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
        """Recover a create whose write savepoint rolled back.

        Read the diagnostic constraint name. Re-raise the
        supplied IntegrityError unless the alias uses PostgreSQL
        and the name is one of these:
        s014_course_actor_action_idem_uniq
        s014_course_id_uniq
        s014_course_version_number_uniq
        s014_course_initial_version_uniq

        Discover the requested UUID and the first Course UUID
        found for this actor, action and key. Lock existing
        roots in UUID byte order, then their ordered versions.
        Multiple winners raise EducationCourseLineageError. For
        a single winner compare epoch, Course and payload,
        raising respectively EducationCourseCrossEpochConflict,
        EducationCourseIdempotencyCourseConflict or
        EducationCoursePayloadConflict. A broken chain raises
        EducationCourseLineageError. Build the receipt from that
        stored version. Return CreateCourseReceipt with
        replayed=True.

        With no winner, validate an existing requested Course's
        chain, then raise EducationCourseIdentityConflict. If
        the root is absent, re-raise the supplied
        IntegrityError. Lineage and database read errors
        propagate. The caller holds the transaction and has
        already qualified this request.
        """
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
        """Recover an append whose write savepoint rolled back.

        Read the diagnostic constraint name. Re-raise the
        supplied IntegrityError unless the alias uses PostgreSQL
        and the name is one of these:
        s014_course_actor_action_idem_uniq
        s014_course_version_number_uniq
        s014_course_predecessor_uniq

        Discover the requested UUID and the first Course UUID
        found for this actor, action and key. Lock existing
        roots in UUID byte order, then their ordered versions.
        Multiple winners raise EducationCourseLineageError. For
        a single winner compare epoch, Course and payload,
        raising respectively EducationCourseCrossEpochConflict,
        EducationCourseIdempotencyCourseConflict or
        EducationCoursePayloadConflict. A broken chain raises
        EducationCourseLineageError. Build the receipt from that
        stored version. Return AppendCourseVersionReceipt with
        replayed=True.

        With no winner, an absent requested root raises
        EducationCourseNotFound. Otherwise validate its chain,
        then raise EducationCourseStaleHeadConflict without
        comparing the expected head again. Lineage and database
        read errors propagate. The caller supplies the
        transaction and has already qualified this request.
        """
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
        """Return a CreateCourseReceipt for a stored version.

        Copy the alias, the Course UUID, the row's pk, version
        number, lineage reference, request reference, key, payload
        fingerprint and occurred_at, and replayed as given. Nothing
        is checked.
        """
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
        """Return an AppendCourseVersionReceipt for a stored version.

        A row with no predecessor raises EducationCourseLineageError.
        Otherwise copy the alias, the Course UUID, the row's pk and
        version number, the predecessor's pk and lineage reference,
        the row's lineage reference, request reference, key, payload
        fingerprint and occurred_at, and replayed as given.
        """
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
        """Create a Course with its first version. Return a receipt.

        Validate before _execute_create opens its atomic block.
        A matching stored create returns replayed=True after
        current authority qualification and lineage checks.
        Validation, authority and execution errors propagate.
        """
        values = self._validate_create(command)
        return self._execute_create(values)

    def _execute_create(self, values: dict[str, object]) -> CreateCourseReceipt:
        """Create a Course and return a CreateCourseReceipt.

        In one atomic block, lock the actor as _lock_actor does
        (EducationCourseActorError). Build the CREATE target,
        payload and payload fingerprint. Discover and lock the
        Courses; more than one prior result for this key raises
        EducationCourseLineageError before qualification.

        Ask qualify for authority on the clock's UTC instant. A
        clock value that is not an exact aware datetime raises
        EducationCourseValidationError. A failed provider call
        raises EducationCourseAuthorityUnavailable, an invalid
        request or response EducationCourseAuthorityMalformed,
        and a valid refusal EducationCourseAuthorityDenied.

        Classify one prior result as _classify_winner does
        (cross-epoch, other-Course, payload and lineage
        conflicts); a match returns its receipt, replayed=True.
        With no winner, an existing requested Course has its
        chain validated, then raises
        EducationCourseIdentityConflict.

        Otherwise derive version one's lineage, then in a nested
        savepoint insert the Course and version one with the
        decision evidence and advance the head. Django
        ValidationError from model saves or head advancement
        propagates. Only IntegrityError from that savepoint goes
        to _recover_create_race, whose receipt or exception is
        returned or raised. A fresh write returns replayed=False.
        """
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
        """Append a version and return its receipt.

        Validate the command as _validate_append does
        (EducationCourseValidationError) before opening the
        atomic block. In it, lock the actor as _lock_actor does
        (EducationCourseActorError). Build the append target,
        naming the expected head pk and lineage, the payload and
        its fingerprint. Discover and lock the Courses; more
        than one prior result for this key raises
        EducationCourseLineageError before qualification.

        Ask qualify for authority on the clock's UTC instant. A
        clock value that is not an exact aware datetime raises
        EducationCourseValidationError. A failed provider call
        raises EducationCourseAuthorityUnavailable, an invalid
        request or response EducationCourseAuthorityMalformed,
        and a valid refusal EducationCourseAuthorityDenied.

        Classify one prior result as _classify_winner does
        (cross-epoch, other-Course, payload and lineage
        conflicts); a match returns its receipt, replayed=True,
        before any head check. With no winner, an absent Course
        raises EducationCourseNotFound. Validate its chain, then
        compare the last pk and lineage with both expected
        values; either mismatch raises
        EducationCourseStaleHeadConflict. An occurrence no later
        than the head's raises EducationCourseValidationError.

        Derive the next number and lineage, then in a nested
        savepoint insert the version with the head as
        predecessor and the decision evidence, and advance the
        head. Django ValidationError from model saves or head
        advancement propagates. Only IntegrityError from that
        savepoint goes to _recover_append_race, whose receipt or
        exception is returned or raised. A fresh write returns
        replayed=False.
        """
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