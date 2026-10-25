"""Shared substrate for the S015 organism command services (PKT-B slices S1 and S2).

The helpers S1 and S2 both need, lifted from S1's membership service with their
bodies unchanged: canonical input validation, the organism command mutex, the
command payload fingerprint, the temporal-basis shape, the empty-anchor
fingerprint, actor and actor-context locking, the authority request builder,
and the S1 receipt-replay machinery (S2's own row names its own module).

The baseline's shared substrate `qualify_completed_replay` /
`QualifiedOrganismCommandReplay` lives in
src/intevia/services/organism_membership_service.py and is untouched by this
module (the Reviewer's F1; commission PKT-B S2 §2).
"""
from __future__ import annotations

import hashlib
import json
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import datetime, timezone
from typing import Callable, Literal
from uuid import UUID

from django.contrib.auth.models import User
from django.db import OperationalError, connections, transaction

from core.models import (
    Application,
    ContextualRoleAssignment,
    ContextualRoleAssignmentStateEvent,
    Identity,
    IdentityTransition,
    LOApplicationQuestionVersion,
    LOApplicationSettingsVersion,
    LOGovernanceConfigurationVersion,
    LivingOrganism,
    LivingOrganismEvent,
    OrganismCommandReceipt,
    OrganismMembership,
    OrganismRoleDefinition,
)
from core.organism_contract import OrganismAction
from src.intevia.services.organism_authority import (
    QualifiedS015AuthorityDecision,
    S015Authority,
    S015AuthorityDenied,
    S015PostFoundingAuthorityRequest,
    authority_target_fingerprint,
    canonical_json_bytes,
    canonical_timestamp,
)


_MUTEX_DOMAIN = b"INTEVIA:S015:ORGANISM_COMMAND_MUTEX:v1\x00"


def _intake_tail(template: str, received_at: datetime) -> str:
    """Return template with {received_at} replaced by canonical_timestamp(received_at), the form of the receipt time in the intake's restated texts."""
    return template.replace("{received_at}", canonical_timestamp(received_at))


def _lock_registered_organism(database_alias: str, organism_id) -> None:
    """Take the registered organism lock before any per-organism row locks."""
    with connections[database_alias].cursor() as cursor:
        cursor.execute("SELECT public.s015_lock_organisms(ARRAY[%s]::uuid[])", [organism_id])


class S015CommandSubstrateError(Exception):
    """Base class for this module's command-substrate-specific exceptions."""
    pass


class S015CommandAtomicityRequired(S015CommandSubstrateError):
    """Raised by acquire_organism_command_mutex when it is called outside an
    atomic block.
    """
    pass


class S015CommandMutexUnavailable(S015CommandSubstrateError):
    """Raised by acquire_organism_command_mutex when the database is not
    PostgreSQL.
    """
    pass


class S015CommandReplayMismatch(S015CommandSubstrateError):
    """Raised when a completed receipt does not match the request it is
    replayed for; raised in this module by _OrganismCommandService. Also
    raised by the request intake when the content of a request differs from
    the request already received under the same key.
    """
    pass


class S015CommandReceiptMalformed(S015CommandSubstrateError):
    """Raised when a stored command result fails reconstruction or replay checks.

    Carries the Human Governor's fixed record-fault text (a), distinguishing
    a record fault from evidence that a different request was submitted.
    The receipt decoders and the membership and role replay-chain checks
    raise it. A genuine request mismatch remains S015CommandReplayMismatch.

    On the existing-receipt replay path these checks precede new domain
    work. The shared _finalise path also calls a decoder, after inserting
    the new receipt. The exception itself neither prevents prior writes nor
    performs rollback; transaction handling must uphold the message's
    assurance about this attempt's changes.

    On an admitted active entry, the intake record was committed outside
    the command's transaction and survives its rollback. When _finalise is
    given that binding's received_at and its decoder raises this class,
    the message uses R-FIX-04: no new governed change and the intake record
    kept. Without received_at the original record-fault message is used,
    including on the completed-receipt replay path (R-FIX-03) and callers
    whose entry the intake does not yet serve.
    """
    def __init__(self, *, received_at: datetime | None = None) -> None:
        # RC-1 commission v1.3, item 1 (text (a), as resolved by the Human Governor).
        """Create the refusal with the fixed record-fault text (a).

        received_at, when given, is the bound request's receipt time and
        selects the form _finalise raises: the result could not be read back,
        the attempt made no new governed change and the record of the request's
        receipt was kept. Without it the original text is used, as on the
        replay path.
        """
        self.received_at = received_at
        if received_at is None:
            message = (
                "The earlier record of this request could not be read. This attempt made no new changes. "
                "This is a record fault, not evidence that you submitted a different request; it needs to "
                "be checked before the request is retried."
            )
        else:
            message = (
                "The record of this request's result could not be read back. This attempt made no new "
                "governed change; INTEVIA kept a record that your request was received at "
                f"{canonical_timestamp(received_at)}. This is a record fault, not evidence that you "
                "submitted a different request; it needs to be checked before the same request is sent again."
            )
        super().__init__(message)


class S015CommandContended(S015CommandSubstrateError):
    """Authored outcome for residual database contention (T-LOCK-1).

    contention_translated raises this from an OperationalError whose direct
    cause carries SQLSTATE 40P01 (deadlock detected), 40001 (serialization
    failure) or 55P03 (lock not available), when no atomic block remains
    open on the selected connection. The command wrappers place that
    translation outside their transaction so the named statement or commit
    failure is handled after rollback. On an admitted active entry, the
    intake record committed before that transaction remains.

    The message is the Human Governor's T-LOCK-1 text. With the bound
    request's received_at, it states that no governed record was written
    and that the record of receipt was kept. Without received_at, including
    at the admission boundary and on entries the intake does not yet serve,
    it retains the original text stating that the attempt made no changes.
    diagnostic_sqlstate records the code, and the translator preserves the
    original OperationalError as the cause. Constructing this exception
    does not itself roll back a transaction. Nothing is retried.
    """

    def __init__(
        self,
        diagnostic_sqlstate: Literal["40P01", "40001", "55P03"],
        *,
        received_at: datetime | None = None,
    ) -> None:
        """Set the T-LOCK-1 message and record diagnostic_sqlstate and
        received_at.

        received_at, when given, is the bound request's receipt time and
        selects the post-admission text, which states that the attempt wrote no
        governed record and that the record of the request's receipt was kept.
        Without it the original text is used, as at the admission boundary and
        on entries the intake does not yet serve.
        """
        self.received_at = received_at
        if received_at is None:
            message = (
                "Your request could not be completed because it conflicted with another database operation. "
                "This attempt made no changes. Please wait a moment before trying again. "
                "If this keeps happening, note the time it occurred; it needs to be checked."
            )
        else:
            message = (
                "Your request could not be completed because it conflicted with another database operation. "
                "This attempt wrote no governed record; INTEVIA kept a record that your request was received "
                f"at {canonical_timestamp(received_at)}. Please wait a moment before sending the same request "
                "again; it is judged as of that time. If this keeps happening, note the time it occurred; "
                "it needs to be checked."
            )
        super().__init__(message)
        self.diagnostic_sqlstate = diagnostic_sqlstate


@dataclass(frozen=True, slots=True)
class WithheldField:
    """Placeholder value for a result field withheld during replay.

    Stores the field name and an audience label: APPLICANT, PROCESSING or
    SUBJECT. serve_replay uses the first declared audience when no declared
    audience permits access, and SUBJECT for its independent
    authority-reference withholding rule.
    """
    field: str
    audience: Literal["APPLICANT", "PROCESSING", "SUBJECT"]


@dataclass(frozen=True, slots=True)
class ReplayViewer:
    """The viewer of a replayed result: the Identity's primary key and UUID, and
    the time of viewing (validated as timezone-aware by serve_replay).
    """
    identity_pk: int
    identity_id: UUID
    now: datetime


@dataclass(frozen=True, slots=True)
class ReplayResultType:
    """The exact result class a replayed payload must have, and the database alias
    used to evaluate the viewer's audiences.
    """
    result_class: type
    database_alias: str


def withheld_fields(result: object) -> tuple[WithheldField, ...]:
    """Return a tuple of WithheldField values found in a dataclass instance.

    Inspects the instance's fields in dataclass field order and includes
    values that are instances of WithheldField. Raises TypeError for a
    non-dataclass value or a dataclass class rather than an instance.
    """
    if not is_dataclass(result) or isinstance(result, type):
        raise TypeError("result must be a dataclass instance")
    return tuple(
        value
        for result_field in fields(result)
        if isinstance((value := getattr(result, result_field.name)), WithheldField)
    )


def _result_organism_pk(result: object, database_alias: str) -> int | None:
    """Return the primary key of the LO a replayed result belongs to.

    Uses the first of these attributes the result has, and returns
    that lookup's answer, None included: application_uuid (the
    Application's LO), membership_uuid (the membership's LO),
    assignment_uuid (the LO of the assignment's membership), then
    version_uuid, looked up in the application settings, application
    question and governance configuration version tables in that
    order. Returns None when the result has none of them. Reads only,
    on database_alias.
    """
    if hasattr(result, "application_uuid"):
        return Application.objects.using(database_alias).filter(
            application_uuid=result.application_uuid
        ).values_list("living_organism_id", flat=True).first()
    if hasattr(result, "membership_uuid"):
        return OrganismMembership.objects.using(database_alias).filter(
            membership_uuid=result.membership_uuid
        ).values_list("living_organism_id", flat=True).first()
    if hasattr(result, "assignment_uuid"):
        return ContextualRoleAssignment.objects.using(database_alias).filter(
            assignment_uuid=result.assignment_uuid
        ).values_list("membership__living_organism_id", flat=True).first()
    if hasattr(result, "version_uuid"):
        for model in (
            LOApplicationSettingsVersion,
            LOApplicationQuestionVersion,
            LOGovernanceConfigurationVersion,
        ):
            organism_pk = model.objects.using(database_alias).filter(
                version_uuid=result.version_uuid
            ).values_list("living_organism_id", flat=True).first()
            if organism_pk is not None:
                return organism_pk
    return None


def _subject_identity_id(result: object, database_alias: str):
    """Return the UUID of the Identity a replayed result is about.

    Uses membership_uuid if the result has it (the membership's
    Identity), otherwise assignment_uuid (the Identity of the
    assignment's membership). Returns None when the result has neither
    attribute or no row matches. Reads only, on database_alias.
    """
    if hasattr(result, "membership_uuid"):
        return OrganismMembership.objects.using(database_alias).filter(
            membership_uuid=result.membership_uuid
        ).values_list("identity__identity_id", flat=True).first()
    if hasattr(result, "assignment_uuid"):
        return ContextualRoleAssignment.objects.using(database_alias).filter(
            assignment_uuid=result.assignment_uuid
        ).values_list("membership__identity__identity_id", flat=True).first()
    return None


def _viewer_has_audience(
    result: object,
    audience: Literal["APPLICANT", "PROCESSING", "SUBJECT"],
    viewer: ReplayViewer,
    database_alias: str,
) -> bool:
    """Check the viewer's audience using database_alias.

    SUBJECT compares the resolved subject UUID with
    viewer.identity_id. It does not check the viewer's primary key,
    activity or time. serve_replay validates the viewer before
    calling this helper.

    Otherwise require a resolved LO and an Identity matching both
    viewer identifiers; return False if either is absent. Read the
    Application when application_uuid is present, then call
    _present_application_audiences at viewer.now.

    That helper selects the latest IdentityTransition by occurred_at
    and primary key, restricted to occurred_at no later than
    viewer.now. Require an ACTIVE result. There is no separate
    recorded-time cut. An applicant matches the Application's
    applicant Identity UUID and is excluded from PROCESSING, even
    when also a Coordinator.

    PROCESSING requires a current ACTIVE membership in the LO and an
    ACTIVE LO-scope COORDINATOR assignment. Membership and
    assignment states are read as stored, without a historical fold.
    Read the term from the latest ASSIGN event by sequence, with no
    time cut. That term lookup does not consult later END or
    REASSIGN events. An absent term, a non-FIXED term or a null end
    qualifies; a FIXED term with an end qualifies only before that
    end.

    Return the applicant flag for APPLICANT. Every other audience
    value except SUBJECT uses the processing flag, including an
    unrecognised value. The helper does not validate the vocabulary.
    Reads only; read and term-evaluation errors propagate.
    """
    if audience == "SUBJECT":
        return _subject_identity_id(result, database_alias) == viewer.identity_id

    organism_pk = _result_organism_pk(result, database_alias)
    if organism_pk is None:
        return False
    application = None
    if hasattr(result, "application_uuid"):
        application = Application.objects.using(database_alias).filter(
            application_uuid=result.application_uuid,
        ).first()
    reader = Identity.objects.using(database_alias).filter(
        pk=viewer.identity_pk,
        identity_id=viewer.identity_id,
    ).first()
    if reader is None:
        return False
    from src.intevia.services.organism_application_reads import _present_application_audiences

    identity_active, applicant, processing = _present_application_audiences(
        application=application,
        reader=reader,
        organism_pk=organism_pk,
        at=viewer.now,
        database_alias=database_alias,
    )
    if not identity_active:
        return False
    return applicant if audience == "APPLICANT" else processing


def _has_pk_valued_reference(reference: object) -> bool:
    """Recognise selected primary-key forms in authority facts.

    Require exact type str and prefix "s015a1:". Parse the suffix as
    JSON. Return False for TypeError or ValueError during parsing or
    for a parsed value whose exact type is not dict.

    Return True when top-level "a" is an exact int or decimal
    string. Integers include zero and negative values; booleans do
    not count. Otherwise match "e" as a decimal string, or a string
    with a decimal part before its first colon. An integer-valued
    "e" does not match.

    Decimal means an exact, non-empty str accepted by str.isdecimal,
    including non-ASCII decimal digits. There is no recursive
    search, record lookup or full authority-reference validation.
    Unmatched values return False. Other parser errors propagate.

    serve_replay uses this result to withhold authority_reference
    from every viewer. It does not thereby filter other reference
    fields or replace the unfiltered payload stored in the receipt.
    """
    if type(reference) is not str or not reference.startswith("s015a1:"):
        return False
    try:
        facts = json.loads(reference[len("s015a1:"):])
    except (TypeError, ValueError):
        return False
    if type(facts) is not dict:
        return False

    def is_pk_string(value: object) -> bool:
        """Return whether value is a non-empty str of decimal digits."""
        return type(value) is str and value.isdecimal()

    if type(facts.get("a")) is int or is_pk_string(facts.get("a")):
        return True
    event = facts.get("e")
    return is_pk_string(event) or (
        type(event) is str and ":" in event and is_pk_string(event.split(":", 1)[0])
    )


def serve_replay(payload: object, *, viewer: ReplayViewer, result_type: type):
    """Apply declared audience checks and authority-reference withholding.

    Requires a ReplayViewer, a ReplayResultType and a dataclass payload
    instance whose exact class is result_type.result_class. Requires the
    viewer's identity_pk to be an int, identity_id to be a UUID and now to
    be a timezone-aware datetime; invalid values raise TypeError.

    Obtains field classifications from the result class's
    replay_field_audiences(payload). A string audience is treated as a
    single audience. For each classified field, retains its value if any
    declared audience check succeeds; otherwise replaces it with
    WithheldField using the first declared audience as the label.

    SUBJECT compares the viewer's UUID with the subject resolved through
    membership_uuid or assignment_uuid. APPLICANT and PROCESSING require
    a resolved LO, an Identity matching both viewer identifiers, and an
    active-identity result from _present_application_audiences, evaluated
    at viewer.now on result_type.database_alias.

    Independently replaces authority_reference with a SUBJECT-labelled
    WithheldField whenever _has_pk_valued_reference matches it, regardless
    of the viewer's audiences. This rule also applies when the field
    classification mapping is empty.

    Returns a dataclass replacement when any field is withheld; otherwise
    returns the original payload.
    """
    if not isinstance(viewer, ReplayViewer):
        raise TypeError("viewer must be a ReplayViewer")
    if not isinstance(result_type, ReplayResultType):
        raise TypeError("result_type must be a ReplayResultType")
    if not is_dataclass(payload) or isinstance(payload, type) or type(payload) is not result_type.result_class:
        raise TypeError("payload must match result_type")
    if type(viewer.identity_pk) is not int or type(viewer.identity_id) is not UUID:
        raise TypeError("viewer identity is invalid")
    _validate_timestamp(viewer.now, "viewer.now")
    classifications = result_type.result_class.replay_field_audiences(payload)
    replacements = {}
    for field_name, audiences in classifications.items():
        if type(audiences) is str:
            audiences = (audiences,)
        if not any(
            _viewer_has_audience(payload, audience, viewer, result_type.database_alias)
            for audience in audiences
        ):
            replacements[field_name] = WithheldField(field_name, audiences[0])
    authority_reference = getattr(payload, "authority_reference", None)
    if _has_pk_valued_reference(authority_reference):
        replacements["authority_reference"] = WithheldField("authority_reference", "SUBJECT")
    return replace(payload, **replacements) if replacements else payload


@contextmanager
def authority_atomic(
    database_alias: str,
    *,
    received_at: datetime | None = None,
    stage_phase: dict | None = None,
):
    """Run an atomic block that defers S015AuthorityDenied, inside contention translation.

    Uses transaction.atomic on database_alias. Catches S015AuthorityDenied
    from the body and, if atomic exit succeeds, raises the same exception
    afterwards. The caught denial does not itself trigger rollback of this
    block. All surviving writes in the block remain subject to normal
    transaction or savepoint handling; an enclosing transaction can still
    roll them back.

    The block runs inside contention_translated. An OperationalError with
    SQLSTATE 40P01, 40001 or 55P03, from the body or from the block's exit,
    becomes S015CommandContended when no atomic block remains open on the
    connection afterwards. Inside an enclosing transaction those errors,
    and every other exception, propagate unchanged. Nothing is retried.

    This helper does not write or count AuthorityDecision records, isolate
    a refusal from other writes, or guarantee a successful commit.
    SB-G build commission v1.0, section 10.1, relies on this mechanism
    together with the provider's writes and the caller's transaction scope.
    The contention translation is PKT-LOCK-1's
    (PKT_LOCK_1_BUILD_COMMISSION_v1_0.md, section 2.6).

    received_at, when given, is the bound request's receipt time and is
    passed to contention_translated. The executors of entries the intake
    serves pass it; other callers omit it. With a stage-phase cell, EXIT is
    set immediately before atomic exit and RETURNED after it (D §9.2).
    """
    with contention_translated(database_alias, received_at=received_at):
        refusal = None
        with transaction.atomic(using=database_alias):
            try:
                yield
            except S015AuthorityDenied as exc:
                refusal = exc
            if stage_phase is not None:
                stage_phase["phase"] = "EXIT"
        if stage_phase is not None:
            stage_phase["phase"] = "RETURNED"
        if refusal is not None:
            raise refusal


@contextmanager
def contention_translated(
    database_alias: str,
    *,
    received_at: datetime | None = None,
):
    """Translate named contention after the wrapped transaction block exits.

    Place this wrapper outside the command's transaction block on
    database_alias. It does not open, commit or roll back a transaction.
    The transaction block is responsible for rolling back the named
    statement or commit failure before that failure reaches this wrapper.

    An OperationalError whose direct cause's sqlstate is 40P01, 40001 or
    55P03 is raised as S015CommandContended(sqlstate), with received_at when
    one is given, from the original exception, only when no atomic block
    remains open on database_alias. If an enclosing atomic block remains, or
    the error does not match, the original exception propagates unchanged.
    Nothing is retried.

    Used outside transaction.atomic by authority_atomic and by
    OrganismMembershipService.open_membership. The no-open-atomic-block
    check restricts translation to the outermost case; it is not a separate
    check that rollback occurred.

    Source: PKT_LOCK_1_BUILD_COMMISSION_v1_0.md, section 2.6, with the Human
    Governor's outermost-transaction correction in section 3.2.

    received_at, when given, is the bound request's receipt time;
    S015CommandContended then states that the record of the request's
    receipt was kept.
    """
    try:
        yield
    except OperationalError as exc:
        sqlstate = getattr(exc.__cause__, "sqlstate", None)
        if (
            sqlstate in {"40P01", "40001", "55P03"}
            and not transaction.get_connection(database_alias).in_atomic_block
        ):
            raise S015CommandContended(sqlstate, received_at=received_at) from exc
        raise


def _canonical_key(value: object, name: str, maximum: int) -> str:
    """Return value unchanged if it is already a canonical key.

    value must have exact type str, or TypeError "<name> must be a
    string". It must equal its own NFC form with surrounding
    whitespace stripped, be non-empty and have at most maximum
    characters, or ValueError "<name> is not canonical". It checks;
    it does not normalise.
    """
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    canonical = unicodedata.normalize("NFC", value).strip()
    if canonical != value or not canonical or len(canonical) > maximum:
        raise ValueError(f"{name} is not canonical")
    return canonical


def _canonical_uuid(value: object, name: str) -> UUID:
    """Return value if its exact type is UUID; otherwise raise
    TypeError "<name> must be a UUID". A UUID subclass or a UUID
    string is refused."""
    if type(value) is not UUID:
        raise TypeError(f"{name} must be a UUID")
    return value


def _validate_timestamp(value: object, name: str) -> datetime:
    """Return a timezone-aware datetime converted to UTC.

    value must have exact type datetime and a tzinfo that gives a UTC
    offset, or TypeError "<name> must be timezone-aware". A datetime
    subclass is refused.
    """
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise TypeError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _resulting_temporal_basis(
    *,
    payload_fingerprint: str,
    effective_at: datetime,
    received_at: datetime,
) -> tuple[str | None, str | None]:
    """Return the temporal-basis kind and reference for a recorded act.

    (None, None) when effective_at equals received_at. Otherwise the
    kind is "RETROSPECTIVE" when effective_at is earlier and
    "PROSPECTIVE" when it is later, and the reference is "s015tb1:"
    followed by payload_fingerprint. This is the shape the event
    tables' temporal-basis check requires (migration 0021). Nothing is
    validated here.
    """
    if effective_at == received_at:
        return None, None
    kind = "RETROSPECTIVE" if effective_at < received_at else "PROSPECTIVE"
    return kind, f"s015tb1:{payload_fingerprint}"


def _command_payload(
    *,
    domain: bytes,
    schema: str,
    action: OrganismAction,
    actor_identity_id: UUID,
    actor_access_epoch: int,
    request_reference: str,
    idempotency_key: str,
    occurred_at: datetime,
    target: dict[str, object],
    command: dict[str, object],
) -> str:
    """Return a command's payload fingerprint, a SHA-256 hex digest.

    Builds an object with action (its value), actor_access_epoch,
    actor_identity_id (as a string), command, idempotency_key,
    occurred_at (canonical_timestamp), request_reference, schema and
    target, and hashes domain followed by its canonical_json_bytes.
    The digest has no prefix. Nothing is validated here, except that
    canonical_timestamp raises ValueError unless occurred_at is a
    timezone-aware datetime, and canonical_json_bytes raises for a
    value it cannot render.
    """
    payload = {
        "action": action.value,
        "actor_access_epoch": actor_access_epoch,
        "actor_identity_id": str(actor_identity_id),
        "command": command,
        "idempotency_key": idempotency_key,
        "occurred_at": canonical_timestamp(occurred_at),
        "request_reference": request_reference,
        "schema": schema,
        "target": target,
    }
    return hashlib.sha256(domain + canonical_json_bytes(payload)).hexdigest()


def command_mutex_key(
    *,
    actor_identity_id: UUID,
    action: OrganismAction,
    idempotency_key: str,
) -> int:
    """Return the signed 64-bit advisory-lock key for a command identity.

    Hashes _MUTEX_DOMAIN, actor_identity_id.bytes, a NUL separator,
    action.value encoded as ASCII, another NUL separator, and the
    idempotency key encoded as UTF-8. Returns the first eight SHA-256
    digest bytes interpreted as a signed big-endian integer.

    Requires actor_identity_id to have exact type UUID and action to have
    exact type OrganismAction. The idempotency key must be a nonempty
    string of at most 120 characters, already NFC-normalised and stripped
    of surrounding whitespace. Invalid types raise TypeError; a
    noncanonical idempotency-key string raises ValueError.
    """
    if type(actor_identity_id) is not UUID:
        raise TypeError("actor_identity_id must be a UUID")
    if type(action) is not OrganismAction:
        raise TypeError("action must be an OrganismAction")
    canonical_key = _canonical_key(idempotency_key, "idempotency_key", 120)
    digest = hashlib.sha256(
        _MUTEX_DOMAIN
        + actor_identity_id.bytes
        + b"\x00"
        + action.value.encode("ascii")
        + b"\x00"
        + canonical_key.encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def acquire_organism_command_mutex(
    *,
    database_alias: str,
    actor_identity_id: UUID,
    action: OrganismAction,
    idempotency_key: str,
) -> int:
    """Acquire the actor/action/idempotency-key mutex and return its key.

    Requires a nonempty string database_alias, raising ValueError otherwise.
    Raises S015CommandAtomicityRequired if the selected connection is not
    in an atomic block, then S015CommandMutexUnavailable if its vendor is
    not PostgreSQL.

    Computes command_mutex_key and acquires its transaction-scoped
    PostgreSQL advisory lock with pg_advisory_xact_lock. Input-validation
    errors from command_mutex_key propagate.

    The lock is normally retained until transaction end; rolling back a
    savepoint that acquired it can release that acquisition earlier.
    """
    if type(database_alias) is not str or not database_alias:
        raise ValueError("database_alias is required")
    connection = connections[database_alias]
    if not connection.in_atomic_block:
        raise S015CommandAtomicityRequired("outer atomic transaction required")
    if connection.vendor != "postgresql":
        raise S015CommandMutexUnavailable(
            "S015 advisory mutex requires PostgreSQL"
        )
    lock_key = command_mutex_key(
        actor_identity_id=actor_identity_id,
        action=action,
        idempotency_key=idempotency_key,
    )
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [lock_key])
    return lock_key


def _empty_anchor_fingerprint(
    *,
    database_alias: str,
    anchor_token: str,
    anchor_uuid: UUID,
) -> str:
    """Ask the database for an anchor's empty event-set fingerprint.

    On database_alias, pass anchor_token, anchor_uuid and an empty
    record list to public.s015_canonical_envelope, then pass that
    result to public.s015_digest_envelope and return its value. The
    installed database functions determine the canonical form and
    digest prefix; this helper does not choose or verify them.

    The membership service uses this for a new membership's
    state_source_event_set_fingerprint with anchor token
    "core_organismmembership". Database errors propagate.
    """
    with connections[database_alias].cursor() as cursor:
        cursor.execute(
            "SELECT public.s015_digest_envelope(public.s015_canonical_envelope(%s, %s::uuid, '{}'::text[]))",
            [anchor_token, anchor_uuid],
        )
        return cursor.fetchone()[0]


def _lock_actor(
    *,
    database_alias: str,
    credential: User,
    unavailable: type[Exception],
) -> Identity:
    """Lock the actor's Identity and its credential, check both, return the Identity.

    Locks the Identity holding this credential, and the credential row,
    with SELECT ... FOR NO KEY UPDATE OF both (rule I-1: the actor first,
    never FOR UPDATE). Raises the supplied unavailable exception when no
    Identity has this credential, when its access_state is not ACTIVE, or
    when its credential is not active.

    Callers take this lock before any organism lock. Rules I-2 and I-3 bind
    the caller, not this helper: after an organism lock, no explicit lock
    on another person's Identity or credential; another Identity that is
    genuinely needed is locked before the organism lock, at FOR KEY SHARE.
    The timing rule also binds the command: the actor's standing is judged
    as of received_at and must hold unchanged from this lock until commit.
    This helper checks access_state and the credential's is_active as read
    under its lock; it does not compare them with received_at.

    Sources: PKT_LOCK_1_BUILD_COMMISSION_v1_0.md, section 2.1;
    HG_DECISION_SEK2_F1_LOCK_ORDER_REPAIR_20261015_CAPTURE.txt;
    HG_DECISION_PKT_LOCK_1_DESIGN_ADOPTION_WITH_AMENDMENTS_20261015_CAPTURE.txt.
    """
    try:
        identity = (
            Identity.objects.using(database_alias)
            .select_related("credential")
            .select_for_update(no_key=True, of=("self", "credential"))
            .get(credential=credential)
        )
    except Identity.DoesNotExist as exc:
        raise unavailable("actor Identity not found") from exc
    if identity.access_state != Identity.AccessState.ACTIVE:
        raise unavailable("actor Identity is not active")
    if not identity.credential.is_active:
        raise unavailable("actor credential is not active")
    return identity


def _lock_actor_membership_context(
    *,
    database_alias: str,
    actor_identity_id: UUID,
    living_organism_id: UUID,
    unavailable: type[Exception],
    allow_probationary: bool = False,
) -> tuple[OrganismMembership, tuple[str, ...]]:
    """Lock and return the actor's membership and active role-head references.

    Selects the membership for the actor UUID and LO UUID for update.
    Requires state ACTIVE, or ACTIVE or PROBATIONARY when allow_probationary
    is true, and a non-null head transition. Raises the supplied unavailable
    exception with "actor active membership is required" if the membership
    is absent or fails either requirement.

    Evaluates a select_for_update query for this membership's ACTIVE role
    assignments with non-null head state events, joining those events and
    ordering by assignment primary key. Returns the membership and a tuple
    of the selected head events' lineage references in that order.

    Uses the stored membership and assignment states; it does not perform
    an effective-time state fold.
    """
    try:
        membership = (
            OrganismMembership.objects.using(database_alias)
            .select_for_update(of=("self",))
            .get(identity__identity_id=actor_identity_id, living_organism__organism_id=living_organism_id)
        )
    except OrganismMembership.DoesNotExist as exc:
        raise unavailable(
            "actor active membership is required"
        ) from exc
    allowed_states = {"ACTIVE", "PROBATIONARY"} if allow_probationary else {"ACTIVE"}
    if membership.state not in allowed_states or membership.head_transition_id is None:
        raise unavailable(
            "actor active membership is required"
        )
    role_refs = tuple(
        assignment.head_state_event.lineage_reference
        for assignment in (
            ContextualRoleAssignment.objects.using(database_alias)
            .select_for_update()
            .select_related("head_state_event")
            .filter(membership=membership, state="ACTIVE", head_state_event__isnull=False)
            .order_by("pk")
        )
    )
    return membership, role_refs


def _read_actor_active_membership(
    *,
    database_alias: str,
    actor_identity_pk: int,
    living_organism_pk: int,
    unavailable: type[Exception],
) -> OrganismMembership:
    """Read the actor's ACTIVE membership without a row lock.

    Look up the membership using the actor's Identity primary key
    and the LO's primary key. Return it only when its stored state
    is ACTIVE. Absence or any other state raises the supplied
    unavailable exception with the message "actor active membership
    is required". PROBATIONARY does not qualify.

    This checks stored state. It performs no temporal fold and does
    not supply a locked authority context. It does not require a
    head transition or read role heads. Callers needing those
    guarantees must obtain them separately, as
    _lock_actor_membership_context does.
    """
    membership = OrganismMembership.objects.using(database_alias).filter(
        identity_id=actor_identity_pk,
        living_organism_id=living_organism_pk,
    ).first()
    if membership is None or membership.state != "ACTIVE":
        raise unavailable("actor active membership is required")
    return membership


def _authority_request(
    *,
    database_alias: str,
    actor: Identity,
    actor_membership: OrganismMembership,
    actor_role_head_refs: tuple[str, ...],
    action: OrganismAction,
    request_reference: str,
    idempotency_key: str,
    received_at: datetime,
    target_fingerprint: str,
    effective_at: datetime | None = None,
    target: dict[str, object] | None = None,
    living_organism_id: UUID | None = None,
    assignment_uuid: UUID | None = None,
    outcome: str | None = None,
    version_uuid: UUID | None = None,
    version_kind: str | None = None,
    command_subtype: str | None = None,
) -> S015PostFoundingAuthorityRequest:
    """Build a post-founding request from supplied context.

    Copy the actor's primary key, Identity UUID and access epoch.
    Set evaluated_at to received_at. Use the membership head's
    lineage_reference and the supplied actor_role_head_refs.
    Accessing the head relation may read the database if not cached;
    a missing head is not translated into an authored refusal here.

    Use action.value when command_subtype is None; an explicitly
    supplied subtype passes through, including an empty string. Pass
    the remaining arguments unchanged, including effective_at,
    target, target_fingerprint, LO and assignment identifiers,
    outcome, version_uuid and version_kind.

    This builder neither obtains received_at nor computes a target
    fingerprint. It does not check that the membership belongs to
    the actor and LO, validate role references, take explicit row
    locks, qualify authority or write a decision. The caller
    supplies context; qualification checks the request. Attribute,
    relation-read and construction errors propagate.
    """
    return S015PostFoundingAuthorityRequest(
        database_alias=database_alias,
        actor_pk=actor.pk,
        actor_identity_id=actor.identity_id,
        actor_access_epoch=actor.access_epoch,
        action=action,
        command_subtype=action.value if command_subtype is None else command_subtype,
        target_fingerprint=target_fingerprint,
        request_reference=request_reference,
        idempotency_key=idempotency_key,
        evaluated_at=received_at,
        active_membership_head_reference=actor_membership.head_transition.lineage_reference,
        contextual_role_head_references=actor_role_head_refs,
        effective_at=effective_at,
        target=target,
        living_organism_id=living_organism_id,
        assignment_uuid=assignment_uuid,
        outcome=outcome,
        version_uuid=version_uuid,
        version_kind=version_kind,
    )


class _OrganismCommandService:
    """Replay, authority and finalise, shared by the S1 and S2 command services.

    The subclass declares `_action`, `_not_found` and `_authority_unavailable`,
    and implements `_replay_result` for its own result row shape.
    """

    _action: OrganismAction
    _not_found: type[Exception]
    _authority_unavailable: type[Exception]

    def __init__(
        self,
        *,
        authority: S015Authority,
        clock: Callable[[], datetime],
        database_alias: str = "default",
    ) -> None:
        """Check and store the authority, clock and database alias.

        Require an S015Authority and a callable clock, else TypeError.
        Require database_alias to be a non-empty str matching the
        authority's database_alias, else ValueError.
        """
        if not isinstance(authority, S015Authority):
            raise TypeError("authority must be an S015Authority")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if type(database_alias) is not str or not database_alias:
            raise ValueError("database_alias is required")
        if authority.database_alias != database_alias:
            raise ValueError("authority database_alias mismatch")
        self._authority = authority
        self._clock = clock
        self._alias = database_alias

    def _replay_result(self, payload: object) -> object:
        """Rebuild a result from a receipt's stored payload.

        Each service implements this hook. The base version raises
        NotImplementedError.
        """
        raise NotImplementedError

    def _verify_replay_result(self, result: object) -> None:
        """Hook for a service to check a rebuilt replay result against its chain.

        Called by _attempt_replay after _replay_result and before the result is
        served. This base version accepts every result; the membership and role
        services override it.
        """
        return None

    def _lock_existing_receipt(
        self,
        *,
        actor: Identity,
        request_reference: str,
        payload_fingerprint: str,
        idempotency_key: str,
    ) -> OrganismCommandReceipt | None:
        """Lock and return the actor's receipt for this action and key.

        Select the OrganismCommandReceipt FOR UPDATE using the actor,
        this service's action and idempotency_key. Those three fields
        are unique together. Return None when no receipt matches.

        A differing request_reference or payload_fingerprint raises
        S015CommandReplayMismatch.
        """
        receipt = (
            OrganismCommandReceipt.objects.using(self._alias)
            .select_for_update()
            .filter(
                actor=actor,
                action=self._action.value,
                idempotency_key=idempotency_key,
            )
            .first()
        )
        if receipt is None:
            return None
        if (
            receipt.request_reference != request_reference
            or receipt.payload_fingerprint != payload_fingerprint
        ):
            raise S015CommandReplayMismatch("completed receipt does not match request")
        return receipt

    def _evaluate_authority(
        self,
        *,
        actor: Identity,
        actor_membership: OrganismMembership,
        actor_role_refs: tuple[str, ...],
        request_reference: str,
        idempotency_key: str,
        target_fingerprint: str,
        received_at: datetime,
        effective_at: datetime | None = None,
        target: dict[str, object] | None = None,
        living_organism_id: UUID | None = None,
        assignment_uuid: UUID | None = None,
    ) -> QualifiedS015AuthorityDecision:
        """Qualify this service's action and return the decision.

        Build the request through _authority_request with this service's
        action, received_at as its evaluation time, and the optional
        effective_at, target, living_organism_id and assignment_uuid.
        Return self._authority.qualify(request); its exceptions,
        including S015AuthorityDenied, propagate.

        Pass no outcome, version_uuid, version_kind or command_subtype.
        """
        request = _authority_request(
            database_alias=self._alias,
            actor=actor,
            actor_membership=actor_membership,
            actor_role_head_refs=actor_role_refs,
            action=self._action,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            received_at=received_at,
            target_fingerprint=target_fingerprint,
            effective_at=effective_at,
            target=target,
            living_organism_id=living_organism_id,
            assignment_uuid=assignment_uuid,
        )
        return self._authority.qualify(request)

    def _finalise(
        self,
        *,
        actor: Identity,
        request_reference: str,
        idempotency_key: str,
        payload_fingerprint: str,
        result,
        received_at: datetime | None = None,
    ):
        """Insert a completed command's receipt, then serve its result.

        Obtain result.receipt_payload(), then create the receipt with
        the actor, this service's action, idempotency key, request
        reference, fingerprint, authority-decision reference and lineage
        reference. The payload is stored before viewer withholding.

        Rebuild from the returned receipt object's result_payload; there
        is no fresh database read of that payload here. Create the
        actor's ReplayViewer at self._clock(), then return
        serve_replay's result on this service's database alias. Unlike
        _attempt_replay, do not call _verify_replay_result.

        Payload creation or insertion can fail before a receipt exists.
        Decoder, clock and serving failures can occur after insertion.
        Errors propagate. This method opens no atomic scope and performs
        no explicit commit or rollback. The surrounding transaction or
        autocommit mode determines whether writes survive a later error.
        Returning a served result is not a separate commit check.

        received_at, when given, is the bound request's receipt time. If the
        decoder then raises S015CommandReceiptMalformed, the same class is
        raised again with received_at and with the decoder's original cause, so
        that the message states the record of the request's receipt kept.
        Callers whose entry the intake does not yet serve omit it.
        """
        receipt = OrganismCommandReceipt.objects.using(self._alias).create(
            actor=actor,
            action=self._action.value,
            idempotency_key=idempotency_key,
            request_reference=request_reference,
            payload_fingerprint=payload_fingerprint,
            authority_decision_reference=result.authority_decision_reference,
            lineage_reference=result.lineage_reference,
            result_payload=result.receipt_payload(),
        )
        try:
            replay_result = self._replay_result(receipt.result_payload)
        except S015CommandReceiptMalformed as exc:
            if received_at is None:
                raise
            raise S015CommandReceiptMalformed(received_at=received_at) from exc.__cause__
        viewer = ReplayViewer(actor.pk, actor.identity_id, self._clock())
        return serve_replay(
            replay_result,
            viewer=viewer,
            result_type=ReplayResultType(type(replay_result), self._alias),
        )

    def _attempt_replay(
        self,
        *,
        actor: Identity,
        request_reference: str,
        payload_fingerprint: str,
        idempotency_key: str,
        viewer: ReplayViewer,
    ):
        """Serve the stored result of an already-completed request, if there is one.

        Locks the actor's receipt for this action and idempotency key through
        _lock_existing_receipt. No receipt returns None so the caller can
        continue new work. A different request reference or payload fingerprint
        raises S015CommandReplayMismatch before decoding.

        Otherwise rebuilds the stored result with _replay_result, invokes
        _verify_replay_result, then returns serve_replay's result for the supplied
        viewer, including its withholding rules. Membership and role services
        override the verification hook; the base hook performs no check.

        The concrete decoders and verification hooks raise
        S015CommandReceiptMalformed for the record-validation failures they
        recognise. This method catches no exceptions: database-read failures,
        wrong-type errors and errors from serving the result propagate from
        the operation that raises them.
        """
        receipt = self._lock_existing_receipt(
            actor=actor,
            request_reference=request_reference,
            payload_fingerprint=payload_fingerprint,
            idempotency_key=idempotency_key,
        )
        if receipt is None:
            return None
        replay_result = self._replay_result(receipt.result_payload)
        self._verify_replay_result(replay_result)
        return serve_replay(
            replay_result,
            viewer=viewer,
            result_type=ReplayResultType(type(replay_result), self._alias),
        )


def _founding_appointment_by_record(
    *,
    database_alias: str,
    assignment_id: int,
    living_organism_id: int,
) -> int | None:
    """Recognize the founder's original Coordinator appointment from its bound records alone."""
    with connections[database_alias].cursor() as cursor:
        cursor.execute(
            """
            SELECT assignment.id
            FROM public.core_livingorganism AS organism
            JOIN public.core_organismmembership AS membership
              ON membership.living_organism_id = organism.id
            JOIN public.core_contextualroleassignment AS assignment
              ON assignment.membership_id = membership.id
            JOIN public.core_organismroledefinition AS role
              ON role.id = assignment.role_definition_id
            JOIN public.core_livingorganismevent AS founding
              ON founding.living_organism_id = organism.id
             AND founding.sequence = 1
             AND founding.action = 'FOUND'
            JOIN public.core_contextualroleassignmentstateevent AS founding_assignment
              ON founding_assignment.assignment_id = assignment.id
             AND founding_assignment.sequence = 1
             AND founding_assignment.action = 'ASSIGN'
            WHERE organism.id = %s
              AND assignment.id = %s
              AND organism.founder_identity_id IS NOT NULL
              AND organism.founding_authority_basis_id = founding.authority_basis_id
              AND founding.resulting_state = 'ACTIVE'
              AND founding.actor_state = 'PARTY'
              AND founding.actor_id = organism.founder_identity_id
              AND founding.enterer_identity_id = organism.founder_identity_id
              AND membership.identity_id = organism.founder_identity_id
              AND role.code = 'COORDINATOR'
              AND role.scope = 'LIVING_ORGANISM'
              AND founding_assignment.prior_state IS NULL
              AND founding_assignment.resulting_state = 'ACTIVE'
              AND founding_assignment.actor_state = 'PARTY'
              AND founding_assignment.actor_id = organism.founder_identity_id
              AND founding_assignment.enterer_identity_id = organism.founder_identity_id
              AND founding_assignment.authority_basis_id = founding.authority_basis_id
              AND founding_assignment.evidence_reference = 'founding-batch:' || founding.event_uuid::text
              AND founding_assignment.authority_decision_reference = founding.authority_decision_reference
            LIMIT 1
            """,
            [living_organism_id, assignment_id],
        )
        row = cursor.fetchone()
    return int(row[0]) if row is not None else None


def _founding_provenance_assignment_id(
    *,
    database_alias: str,
    assignment_id: int,
    living_organism_id: int,
) -> int | None:
    """The landed founding-provenance test (the Human Governor's IL-7 ruling, 29 Sep).

    An assignment is of founding provenance when its sequence-1 event was written by
    the LO's own founding transaction: the landed completion guard
    (s015_guard_founding_completion_update, 0021) admits the founder and basis into
    the LO only inside the transaction whose id the LO's insert stamped into
    `founding_insert_xid` (xid8, holding pg_current_xact_id()'s value), and an office
    whose first event carries that same transaction as its writing transaction is of
    that founding. The test reads the office's first EVENT, not its anchor: event rows
    are never updated (s015_refuse_row_mutation), so the sequence-1 event's xmin is
    permanently its inserting transaction, while xmin on the anchor is the transaction
    that wrote the row's current version — every head advance and every cache refresh
    replaces it. The anchor must also be held by the founder's own membership for the
    COORDINATOR role.     Returns the assignment id when either the transaction test or the durable record rule holds,
    else None.

    Limits: PostgreSQL provides no cast from xid to xid8 (its cast catalogue holds
    only xid8 -> xid, explicit), so the comparison runs in the direction that exists —
    32-bit transaction ids. A 32-bit xid repeats after about four billion
    transactions, so an office whose first event was written exactly that many
    transactions after the founding would also match; that would over-protect (it
    withholds an ending) and would never record anything false. And within a single
    transaction the test cannot distinguish the founding office from another office
    the same founding transaction inserted on the founder's membership (the landed
    founding shape writes exactly one), so the reading is the commission's: the
    founder's original founding COORDINATOR assignment is the one whose first event
    the founding transaction wrote.
    """
    with connections[database_alias].cursor() as cursor:
        cursor.execute(
            """
            SELECT a.id
              FROM public.core_contextualroleassignment a
              JOIN public.core_organismmembership m
                ON m.id = a.membership_id
              JOIN public.core_livingorganism lo
                ON lo.id = m.living_organism_id
              JOIN public.core_organismroledefinition r
                ON r.id = a.role_definition_id
             WHERE a.id = %s
               AND m.living_organism_id = %s
               AND r.code = 'COORDINATOR'
               AND lo.founder_identity_id IS NOT NULL
               AND m.identity_id = lo.founder_identity_id
               AND lo.founding_insert_xid IS NOT NULL
               AND r.scope = 'LIVING_ORGANISM'
               AND EXISTS (
                     SELECT 1 FROM public.core_contextualroleassignmentstateevent e
                      WHERE e.assignment_id = a.id
                        AND e.sequence = 1
                        AND e.xmin = lo.founding_insert_xid::xid
                   )
            """,
            [assignment_id, living_organism_id],
        )
        row = cursor.fetchone()
    if row is not None:
        return int(row[0])
    return _founding_appointment_by_record(
        database_alias=database_alias,
        assignment_id=assignment_id,
        living_organism_id=living_organism_id,
    )


__all__ = [
    "QualifiedS015AuthorityDecision",
    "S015CommandAtomicityRequired",
    "S015CommandMutexUnavailable",
    "S015CommandReceiptMalformed",
    "S015CommandReplayMismatch",
    "S015CommandSubstrateError",
    "_OrganismCommandService",
    "_authority_request",
    "_canonical_key",
    "_canonical_uuid",
    "_command_payload",
    "_empty_anchor_fingerprint",
    "_founding_appointment_by_record",
    "_founding_provenance_assignment_id",
    "_lock_actor",
    "_lock_actor_membership_context",
    "_read_actor_active_membership",
    "_resulting_temporal_basis",
    "_validate_timestamp",
    "acquire_organism_command_mutex",
    "authority_target_fingerprint",
    "canonical_json_bytes",
    "canonical_timestamp",
    "command_mutex_key",
]
