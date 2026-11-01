"""Organism authority for S015 commands: qualification and decision records.

S015Authority.qualify validates an authority request inside the caller's
atomic block. It handles its own preflight refusals or consults an
injected S015AuthorityProvider; the production provider is in
organism_application_authority.py. It checks the result's exact type
and that each echoed request field compares equal and renders to the
same canonical JSON, derives an s015d1 reference from the result's
canonical bytes, and records an AuthorityDecision for selected actions.
A valid result produces a QualifiedS015AuthorityDecision or
S015AuthorityDenied; malformed requests or results and provider
failures raise the corresponding authority errors.

The module also holds the request, response and refusal carriers,
refusal codes and two fixed refusal messages, canonical JSON and target
fingerprint helpers, and identity_state_at for historical access state
and epoch. It does not commit the caller's transaction or explicitly
acquire the command's coordination locks. Decision writes and injected
provider behaviour are not a guarantee of lock-free execution. A
decision row's durability depends on the surrounding transaction.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol
from uuid import UUID

from django.conf import settings
from django.db import connections
from django.db.models.functions import Coalesce

from core.models import (
    Application,
    AuthorityDecision,
    AuthorityPrincipal,
    Identity,
    IdentityTransition,
)
from core.organism_contract import OrganismAction, qualification_planes
from core.organism_authority_facts import (
    CONSENT_U_RELATIONS,
    FOUNDING_KEYS,
    FactKind,
    FactsSelectionError,
    POLICY_LITERALS,
    REFERENCE_SENTINEL,
    S015_AUTHORITY_FACTS,
    S015_AUTHORITY_REFERENCE_MAX_LENGTH,
    S015_AUTHORITY_REFERENCE_PREFIX,
    S015_AUTHORITY_REFUSAL_KEY,
    founding_value_form,
    propose_role_reference,
    select_shape,
)


_TARGET_DOMAIN = b"INTEVIA:S015:ORGANISM_AUTHORITY_TARGET:v1\x00"
_DECISION_DOMAIN = b"INTEVIA:S015:ORGANISM_AUTHORITY_DECISION:v1\x00"


class S015AuthorityError(Exception):
    """Base class of the organism authority errors in this module."""
    pass


class S015AuthorityUnavailable(S015AuthorityError):
    """The authority provider could not be consulted.

    S015Authority.qualify raises it when the provider's
    evaluate_organism_authority raises an Exception, chained from that
    exception. qualify does not record a decision on that failure path.
    """
    pass


class S015AuthorityMalformed(S015AuthorityError):
    """An authority request, provider result or authority reference is
    invalid.

    Raised in this module for a request that fails validation, a database
    alias that does not match the authority's, a call outside an atomic
    block, a provider result of the wrong type, not synchronous, not
    echoing the request or not canonical, an invalid refusal code or
    authority reference, an unrecognised structured authority fact, a
    provider_version that is not a str, and a decision reference that
    already records different bytes.
    """
    pass


class S015AuthorityDenied(S015AuthorityError):
    """Organism authority refused the act.

    S015Authority.qualify raises it for a provider refusal or its own
    preflight refusal, with refusal_code (the code recorded),
    disclosed_refusal_code (the code a caller may show; the recorded code
    when none is disclosed) and authority_decision_reference (the
    refusal's s015d1 decision reference, whether or not a decision row was
    recorded). Services also raise it for their own refusals, some without
    a decision reference. request_uuid can identify a retained governance
    seat request on a service-raised refusal; qualify leaves it None.
    """
    def __init__(
        self,
        message: str = "organism authority denied",
        *,
        refusal_code: "S015AuthorityRefusalCode | None" = None,
        disclosed_refusal_code: "S015AuthorityRefusalCode | None" = None,
        authority_decision_reference: str | None = None,
        request_uuid: UUID | None = None,
    ) -> None:
        """Initialise the exception message and store the optional refusal details.

        The four detail attributes default to None and are stored without
        validation. message defaults to "organism authority denied" and is
        passed to the base exception constructor.
        """
        super().__init__(message)
        self.refusal_code = refusal_code
        self.disclosed_refusal_code = disclosed_refusal_code
        self.authority_decision_reference = authority_decision_reference
        self.request_uuid = request_uuid


class S015AuthorityRefusalCode(str, Enum):
    """Refusal codes an authority provider, qualify's preflight or a service
    can return or raise.
    
    Each value equals its name except SB_IL_GOVCONST and SB_IL_GOVENABLE,
    whose values are "SB-IL-GOVCONST" and "SB-IL-GOVENABLE". The value is
    what an AuthorityDecision records as refusal_code. A refusal carries
    two codes: the code decided and the code disclosed, which may be the
    less specific DENIED.
    
    For office operations, C_CONFIGURATION_NOT_ESTABLISHED means no eligible
    governance configuration was found. C_LO_PAUSED and C_LO_CLOSED mean the
    Living Organism (LO) is paused or closed at the checked coordinates.
    C_LO_NO_CAPACITY means the recorded lifecycle cannot support this act; it does
    not identify one unique cause. SBG_LO_NOT_ACTIVE means the organisation is not
    active; SBG_SEAT_ROUTE_UNAVAILABLE means the requested governance seating route
    is unavailable at that stage. E_IDENTITY_INACTIVE means the actor's access
    state or credential epoch does not permit the act. Base exceptions may retain
    the generic refusal text; specialised service display helpers supply some, not
    all, contextual messages. These codes neither repair state nor create tickets."""
    DENIED = "DENIED"
    E_IDENTITY_INACTIVE = "E_IDENTITY_INACTIVE"
    E_NOT_APPLICANT = "E_NOT_APPLICANT"
    E_NOT_EFFECTIVE_COORDINATOR = "E_NOT_EFFECTIVE_COORDINATOR"
    E_NOT_ASSIGNEE = "E_NOT_ASSIGNEE"
    E_NOT_REGISTERED = "E_NOT_REGISTERED"
    FOUNDING_TERMS_NOT_ACCEPTED = "FOUNDING_TERMS_NOT_ACCEPTED"
    FOUNDING_TERMS_VERSION_SUPERSEDED = "FOUNDING_TERMS_VERSION_SUPERSEDED"
    FOUNDING_PRINCIPAL_ABSENT = "FOUNDING_PRINCIPAL_ABSENT"
    LO_NAME_REQUIRED = "LO_NAME_REQUIRED"
    LO_NAME_UNAVAILABLE = "LO_NAME_UNAVAILABLE"
    PHASE_OUT_OF_ORDER = "PHASE_OUT_OF_ORDER"
    PHASE_CONDITION_UNMET = "PHASE_CONDITION_UNMET"
    BIRTHING_PREREQUISITE_MISSING = "BIRTHING_PREREQUISITE_MISSING"
    BIRTHING_ALREADY_RECORDED = "BIRTHING_ALREADY_RECORDED"
    SB_IL_GOVCONST = "SB-IL-GOVCONST"
    SB_IL_GOVENABLE = "SB-IL-GOVENABLE"
    SBG_ARRANGEMENT_INCOMPLETE = "SBG_ARRANGEMENT_INCOMPLETE"
    SBG_ARRANGEMENT_FIELD_UNSUPPORTED = "SBG_ARRANGEMENT_FIELD_UNSUPPORTED"
    SBG_ELIGIBILITY_UNSUPPORTED = "SBG_ELIGIBILITY_UNSUPPORTED"
    SBG_VERSION_DROPS_HELD_SEAT = "SBG_VERSION_DROPS_HELD_SEAT"
    SBG_GOVERNANCE_CIRCLE_MISSING = "SBG_GOVERNANCE_CIRCLE_MISSING"
    SBG_ARRANGEMENT_MISSING = "SBG_ARRANGEMENT_MISSING"
    SBG_NO_INITIAL_PARTICIPATION_ROUTE = "SBG_NO_INITIAL_PARTICIPATION_ROUTE"
    SBG_IL_GRESD = "SBG_IL_GRESD"
    SBG_SECOND_GOVERNANCE_CIRCLE = "SBG_SECOND_GOVERNANCE_CIRCLE"
    SBG_LO_NOT_ACTIVE = "SBG_LO_NOT_ACTIVE"
    SBG_SEAT_ABSENT = "SBG_SEAT_ABSENT"
    SBG_SEAT_OCCUPIED = "SBG_SEAT_OCCUPIED"
    SBG_ALREADY_SEATED = "SBG_ALREADY_SEATED"
    SBG_MEMBER_NOT_ELIGIBLE = "SBG_MEMBER_NOT_ELIGIBLE"
    SBG_SEAT_ROUTE_UNAVAILABLE = "SBG_SEAT_ROUTE_UNAVAILABLE"
    SBG_SEATING_WHILE_DISABLED = "SBG_SEATING_WHILE_DISABLED"
    SBG_DEPARTURE_PRECEDES_PARTICIPATION = "SBG_DEPARTURE_PRECEDES_PARTICIPATION"
    INV_CQ_ROUTE_REQUIRED = "INV_CQ_ROUTE_REQUIRED"
    SB_BIRTHING_ELECTORATE_EMPTY = "SB_BIRTHING_ELECTORATE_EMPTY"
    C_GATEWAY_NOT_OPEN = "C_GATEWAY_NOT_OPEN"
    C_ROUTE_IS_CQ = "C_ROUTE_IS_CQ"
    C_RESERVED_ROOT_OFFICE = "C_RESERVED_ROOT_OFFICE"
    C_COORDINATOR_DEFINITION_RESERVED = "C_COORDINATOR_DEFINITION_RESERVED"
    C_DEFINITION_KIND_FIXED = "C_DEFINITION_KIND_FIXED"
    C_BOARD_OFFICE_PERMANENT = "C_BOARD_OFFICE_PERMANENT"
    E_CUSTOM_ROLE_NOT_ENTITLED = "E_CUSTOM_ROLE_NOT_ENTITLED"
    C_APPOINTING_ROUTE_NOT_IMPLEMENTED = "C_APPOINTING_ROUTE_NOT_IMPLEMENTED"
    E_NOMINEE_NOT_ELIGIBLE = "E_NOMINEE_NOT_ELIGIBLE"
    C_NOMINATION_ROUTE_NOT_IMPLEMENTED = "C_NOMINATION_ROUTE_NOT_IMPLEMENTED"
    C_CONFIGURATION_NOT_ESTABLISHED = "C_CONFIGURATION_NOT_ESTABLISHED"
    C_CHANGE_SELF_AUTHORISING = "C_CHANGE_SELF_AUTHORISING"
    C_TERMINAL_ROW_STANDS = "C_TERMINAL_ROW_STANDS"
    C_APPLICATION_OPEN = "C_APPLICATION_OPEN"
    C_LO_PAUSED = "C_LO_PAUSED"
    C_LO_CLOSED = "C_LO_CLOSED"
    C_LO_NO_CAPACITY = "C_LO_NO_CAPACITY"
    C_FOUNDER_PROTECTED = "C_FOUNDER_PROTECTED"
    C_COORDINATOR_RECOVERY_NOT_IMPLEMENTED = "C_COORDINATOR_RECOVERY_NOT_IMPLEMENTED"
    C_REFERENCE_UNREPRESENTABLE = "C_REFERENCE_UNREPRESENTABLE"
    E_NOMINATION_POLICY_NOT_ENTITLED = "E_NOMINATION_POLICY_NOT_ENTITLED"
    C_OFFICE_CONSENT_ABSENT_OR_WITHDRAWN = "C_OFFICE_CONSENT_ABSENT_OR_WITHDRAWN"
    C_MEMBERSHIP_TRANSITION_NOT_IMPLEMENTED = "C_MEMBERSHIP_TRANSITION_NOT_IMPLEMENTED"
    C_MEMBERSHIP_LEAVE_WITH_OFFICE_NOT_IMPLEMENTED = "C_MEMBERSHIP_LEAVE_WITH_OFFICE_NOT_IMPLEMENTED"
    SE_GRANT_NOT_BY_HAND = "SE_GRANT_NOT_BY_HAND"
    SE_EXTENSION_AUTHORITY = "SE_EXTENSION_AUTHORITY"
    SE_SELF_EXTENSION = "SE_SELF_EXTENSION"
    SE_EXTENSION_ROUTE_UNAVAILABLE = "SE_EXTENSION_ROUTE_UNAVAILABLE"
    SE_ALREADY_BETA = "SE_ALREADY_BETA"
    SE_OPT_IN_NEEDS_CQ = "SE_OPT_IN_NEEDS_CQ"
    SE_OFFER_CLOSED = "SE_OFFER_CLOSED"
    SE_ALLOWANCE_EXCEEDED = "SE_ALLOWANCE_EXCEEDED"
    SE_USAGE_UNCHECKABLE = "SE_USAGE_UNCHECKABLE"
    UNRECOGNISED_ACTION = "UNRECOGNISED_ACTION"
    UNRECOGNISED_ROUTE = "UNRECOGNISED_ROUTE"


C_FOUNDER_PROTECTED_MESSAGE = (
    "The office was not ended or reassigned, and nothing changed. Until this LO is born, "
    "the founder's original Coordinator appointment can be ended or reassigned only by the "
    "founder. This is a governed protection of the LO's founding, not a technical fault. "
    "After Birthing it no longer applies: the appointment can then be ended or reassigned "
    "under the LO's ordinary governed rules."
)

# R-FORM-29: the reason is shared; Birthing, once admitted (PKT-CLK-3 CLK3-2), opens it
# with "The LO was not born." and the intake tail. The full message is unchanged.
SB_BIRTHING_ELECTORATE_EMPTY_REASON = (
    "On this sole-founder route, Birthing needs exactly one active member of the LO's "
    "electorate at the moment of Birthing, and this LO has none. This is a governed "
    "requirement of the route, not a technical fault."
)
SB_BIRTHING_ELECTORATE_EMPTY_MESSAGE = (
    "The LO was not born, and nothing changed. " + SB_BIRTHING_ELECTORATE_EMPTY_REASON
)


@dataclass(frozen=True, slots=True)
class S015PreFoundingAuthorityRequest:
    """Authority request for founding a living organism.

    Used before the actor holds any membership in the LO. qualify requires
    effective_at, target and living_organism_id for FOUND_LIVING_ORGANISM,
    and for that action looks up the founding authority principal before
    consulting the provider.

    Fields shared by the three request types: database_alias names the
    connection, which must be the authority's; actor_pk, actor_identity_id
    and actor_access_epoch identify the actor and the access epoch the
    request was bound to; action and command_subtype select the
    qualification planes (the subtype must begin with the action's value);
    target_fingerprint is a 64-character lowercase hex digest, equal to
    authority_target_fingerprint(target) when target is given; a request
    whose result qualify records must carry a non-empty target;
    request_reference and idempotency_key are canonical strings of at most
    128 and 120 characters; evaluated_at is the time authority is judged
    at, timezone-aware; effective_at, when given, is timezone-aware.
    """
    database_alias: str
    actor_pk: int
    actor_identity_id: UUID
    actor_access_epoch: int
    action: OrganismAction
    command_subtype: str
    target_fingerprint: str
    request_reference: str
    idempotency_key: str
    evaluated_at: datetime
    effective_at: datetime | None = None
    target: dict[str, object] | None = None
    living_organism_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class S015PostFoundingAuthorityRequest:
    """Authority request for an act by a member of an existing LO.

    Adds the actor's evidence of standing: active_membership_head_reference
    (the lineage reference of the actor's membership head transition) and
    contextual_role_head_references (the head lineage references of the
    actor's active contextual role assignments), each a canonical string of
    at most 255 characters, the second a tuple. assignment_uuid and
    version_uuid, when given, are UUIDs; outcome is None, "ACCEPT" or
    "DECLINE"; version_kind, when given, is a canonical string of at most
    48 characters.

    The shared fields are as described on S015PreFoundingAuthorityRequest.
    """
    database_alias: str
    actor_pk: int
    actor_identity_id: UUID
    actor_access_epoch: int
    action: OrganismAction
    command_subtype: str
    target_fingerprint: str
    request_reference: str
    idempotency_key: str
    evaluated_at: datetime
    active_membership_head_reference: str
    contextual_role_head_references: tuple[str, ...]
    effective_at: datetime | None = None
    target: dict[str, object] | None = None
    living_organism_id: UUID | None = None
    assignment_uuid: UUID | None = None
    outcome: str | None = None
    version_uuid: UUID | None = None
    version_kind: str | None = None


@dataclass(frozen=True, slots=True)
class S015OwnApplicantAuthorityRequest:
    """Authority request for an applicant's own submission or withdrawal.

    action must be SUBMIT_APPLICATION or WITHDRAW_APPLICATION.
    actor_credential_pk and living_organism_pk are positive integers. A
    submission names no application (application_pk None) and may carry a
    predecessor_reference UUID; a withdrawal names the application by
    application_pk. For a withdrawal qualify first checks that the actor is
    active with an active credential and the bound access epoch, and that
    the application is the actor's in that LO.

    The shared fields are as described on S015PreFoundingAuthorityRequest.
    """
    database_alias: str
    actor_pk: int
    actor_identity_id: UUID
    actor_credential_pk: int
    actor_access_epoch: int
    action: OrganismAction
    command_subtype: str
    target_fingerprint: str
    request_reference: str
    idempotency_key: str
    evaluated_at: datetime
    living_organism_pk: int
    application_pk: int | None
    effective_at: datetime | None = None
    target: dict[str, object] | None = None
    living_organism_id: UUID | None = None
    predecessor_reference: UUID | None = None


@dataclass(frozen=True, slots=True)
class S015PreFoundingAuthorityResponse:
    """Qualified result for a S015PreFoundingAuthorityRequest.

    Echoes every field of the request, which qualify checks, and adds
    authority_reference: the provider's reference for the authority it
    relied on, a canonical string of at most 255 characters.
    """
    database_alias: str
    actor_pk: int
    actor_identity_id: UUID
    actor_access_epoch: int
    action: OrganismAction
    command_subtype: str
    target_fingerprint: str
    request_reference: str
    idempotency_key: str
    evaluated_at: datetime
    authority_reference: str
    effective_at: datetime | None = None
    target: dict[str, object] | None = None
    living_organism_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class S015PostFoundingAuthorityResponse:
    """Qualified result for a S015PostFoundingAuthorityRequest.

    Echoes every field of the request, which qualify checks, and adds
    authority_reference, a canonical string of at most 255 characters.
    """
    database_alias: str
    actor_pk: int
    actor_identity_id: UUID
    actor_access_epoch: int
    action: OrganismAction
    command_subtype: str
    target_fingerprint: str
    request_reference: str
    idempotency_key: str
    evaluated_at: datetime
    active_membership_head_reference: str
    contextual_role_head_references: tuple[str, ...]
    authority_reference: str
    effective_at: datetime | None = None
    target: dict[str, object] | None = None
    living_organism_id: UUID | None = None
    assignment_uuid: UUID | None = None
    outcome: str | None = None
    version_uuid: UUID | None = None
    version_kind: str | None = None


@dataclass(frozen=True, slots=True)
class S015OwnApplicantAuthorityResponse:
    """Qualified result for a S015OwnApplicantAuthorityRequest.

    Echoes every field of the request, which qualify checks, and adds
    authority_reference, a canonical string of at most 255 characters.
    """
    database_alias: str
    actor_pk: int
    actor_identity_id: UUID
    actor_credential_pk: int
    actor_access_epoch: int
    action: OrganismAction
    command_subtype: str
    target_fingerprint: str
    request_reference: str
    idempotency_key: str
    evaluated_at: datetime
    living_organism_pk: int
    application_pk: int | None
    authority_reference: str
    effective_at: datetime | None = None
    target: dict[str, object] | None = None
    living_organism_id: UUID | None = None
    predecessor_reference: UUID | None = None


@dataclass(frozen=True, slots=True)
class S015PreFoundingAuthorityRefusal:
    """Refusal result for a S015PreFoundingAuthorityRequest.

    Echoes every field of the request and adds refusal_code (the code
    decided), disclosed_refusal_code (the code a caller may show; when None
    qualify discloses refusal_code) and an optional authority_reference, a
    canonical string of at most 255 characters when given.
    """
    database_alias: str
    actor_pk: int
    actor_identity_id: UUID
    actor_access_epoch: int
    action: OrganismAction
    command_subtype: str
    target_fingerprint: str
    request_reference: str
    idempotency_key: str
    evaluated_at: datetime
    refusal_code: S015AuthorityRefusalCode
    disclosed_refusal_code: S015AuthorityRefusalCode | None = None
    authority_reference: str | None = None
    effective_at: datetime | None = None
    target: dict[str, object] | None = None
    living_organism_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class S015PostFoundingAuthorityRefusal:
    """Refusal result for a S015PostFoundingAuthorityRequest.

    Echoes every field of the request and adds refusal_code,
    disclosed_refusal_code (refusal_code is disclosed when it is None) and
    an optional authority_reference, a canonical string of at most 255
    characters when given.
    """
    database_alias: str
    actor_pk: int
    actor_identity_id: UUID
    actor_access_epoch: int
    action: OrganismAction
    command_subtype: str
    target_fingerprint: str
    request_reference: str
    idempotency_key: str
    evaluated_at: datetime
    active_membership_head_reference: str
    contextual_role_head_references: tuple[str, ...]
    refusal_code: S015AuthorityRefusalCode
    disclosed_refusal_code: S015AuthorityRefusalCode | None = None
    authority_reference: str | None = None
    effective_at: datetime | None = None
    target: dict[str, object] | None = None
    living_organism_id: UUID | None = None
    assignment_uuid: UUID | None = None
    outcome: str | None = None
    version_uuid: UUID | None = None
    version_kind: str | None = None


@dataclass(frozen=True, slots=True)
class S015OwnApplicantAuthorityRefusal:
    """Refusal result for a S015OwnApplicantAuthorityRequest.

    Echoes every field of the request and adds refusal_code,
    disclosed_refusal_code (refusal_code is disclosed when it is None) and
    an optional authority_reference, a canonical string of at most 255
    characters when given.
    """
    database_alias: str
    actor_pk: int
    actor_identity_id: UUID
    actor_credential_pk: int
    actor_access_epoch: int
    action: OrganismAction
    command_subtype: str
    target_fingerprint: str
    request_reference: str
    idempotency_key: str
    evaluated_at: datetime
    living_organism_pk: int
    application_pk: int | None
    refusal_code: S015AuthorityRefusalCode
    disclosed_refusal_code: S015AuthorityRefusalCode | None = None
    authority_reference: str | None = None
    effective_at: datetime | None = None
    target: dict[str, object] | None = None
    living_organism_id: UUID | None = None
    predecessor_reference: UUID | None = None


S015AuthorityRequest = S015PreFoundingAuthorityRequest | S015PostFoundingAuthorityRequest | S015OwnApplicantAuthorityRequest
S015AuthorityResult = (
    S015PreFoundingAuthorityResponse
    | S015PostFoundingAuthorityResponse
    | S015PreFoundingAuthorityRefusal
    | S015PostFoundingAuthorityRefusal
    | S015OwnApplicantAuthorityResponse
    | S015OwnApplicantAuthorityRefusal
)


class S015AuthorityProvider(Protocol):
    """The interface S015Authority consults for organism authority.

    At construction, S015Authority checks that evaluate_organism_authority
    is callable. When recording a decision, it also reads the provider's
    optional provider_identity and provider_version. provider_identity is
    used when its type is exactly str; otherwise the provider's module and
    qualified class name are used. An absent provider_version defaults to
    "UNSPECIFIED"; a supplied value must have type exactly str or is refused
    as malformed.
    """
    def evaluate_organism_authority(
        self, request: S015AuthorityRequest
    ) -> S015AuthorityResult:
        """Judge the request and return its response or refusal.

        qualify calls it inside the caller's atomic block. The result must be
        returned synchronously and have exactly the request type's corresponding
        response or refusal type; subclasses are refused. Every echoed field
        must compare equal to the corresponding request field and render to
        the same canonical JSON, and every field must be canonical. Otherwise
        qualify raises S015AuthorityMalformed. An Exception raised by the
        provider call becomes S015AuthorityUnavailable, chained from it.
        """
        ...


@dataclass(frozen=True, slots=True)
class QualifiedS015AuthorityDecision:
    """What S015Authority.qualify returns when authority qualifies the act.

    The actor, action, subtype, fingerprint, references and evaluated_at
    are copied from the provider's response. active_membership_head_reference
    and contextual_role_head_references are the response's for a
    post-founding request, and None and () otherwise. authority_reference
    is the response's; authority_decision_reference is the s015d1:
    reference derived from the response's bytes, under which qualify
    records an AuthorityDecision for the actions it records.
    """
    database_alias: str
    actor_pk: int
    actor_identity_id: UUID
    actor_access_epoch: int
    action: OrganismAction
    command_subtype: str
    target_fingerprint: str
    request_reference: str
    idempotency_key: str
    evaluated_at: datetime
    active_membership_head_reference: str | None
    contextual_role_head_references: tuple[str, ...]
    authority_reference: str
    authority_decision_reference: str


@dataclass(frozen=True, slots=True)
class IdentityStateAt:
    """An identity's access state and access epoch as of a state time and a
    known time, as computed by identity_state_at. access_state is None when
    no transition is eligible.
    """
    access_state: str | None
    access_epoch: int


def identity_state_at(
    identity_id,
    *,
    state_at: datetime,
    known_at: datetime,
    database_alias: str = "default",
) -> IdentityStateAt:
    """Return an identity's access state and access epoch at two time cuts.

    A transition is eligible when its effective time is at or before
    state_at and its received and recorded times are at or before known_at.
    Each missing coordinate falls back independently to occurred_at.
    access_state is the resulting state of the latest eligible transition,
    ordered by effective time, recorded time and primary key; it is None
    when none is eligible. access_epoch counts eligible RESTRICT,
    DEACTIVATE, REACTIVATE, REPLACE_CREDENTIAL and RETIRE_CREDENTIAL
    transitions, and is zero when there are none.

    identity_id is the Identity UUID. This helper reads only, using separate
    queries for the latest transition and the epoch count. It acquires no
    explicit row locks and does not establish a transaction or snapshot of
    its own; any consistency protection comes from its calling context.
    """
    eligible = (
        IdentityTransition.objects.using(database_alias)
        .filter(identity__identity_id=identity_id)
        .annotate(
            effective_coordinate=Coalesce("effective_at", "occurred_at"),
            received_coordinate=Coalesce("received_at", "occurred_at"),
            recorded_coordinate=Coalesce("recorded_at", "occurred_at"),
        )
        .filter(
            effective_coordinate__lte=state_at,
            received_coordinate__lte=known_at,
            recorded_coordinate__lte=known_at,
        )
    )
    latest = eligible.order_by(
        "effective_coordinate",
        "recorded_coordinate",
        "pk",
    ).last()
    epoch = eligible.filter(
        action__in=(
            IdentityTransition.Action.RESTRICT,
            IdentityTransition.Action.DEACTIVATE,
            IdentityTransition.Action.REACTIVATE,
            IdentityTransition.Action.REPLACE_CREDENTIAL,
            IdentityTransition.Action.RETIRE_CREDENTIAL,
        )
    ).count()
    return IdentityStateAt(
        access_state=None if latest is None else latest.resulting_state,
        access_epoch=epoch,
    )


def canonical_timestamp(value: datetime) -> str:
    """Return a timezone-aware datetime as a fixed-width UTC string.

    The form is YYYY-MM-DDTHH:MM:SS.ffffffZ, always with six fractional
    digits. Raises ValueError for a naive datetime and for any value whose
    type is not exactly datetime.
    """
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    utc_value = value.astimezone(timezone.utc)
    return (
        f"{utc_value.year:04d}-{utc_value.month:02d}-{utc_value.day:02d}"
        f"T{utc_value.hour:02d}:{utc_value.minute:02d}:{utc_value.second:02d}"
        f".{utc_value.microsecond:06d}Z"
    )


def _canonicalise(value: object) -> object:
    """Convert a value to the JSON-ready form used for canonical bytes.

    None, bool and int pass unchanged. Strings must already be NFC
    normalised and are not stripped. A UUID becomes its string; a datetime
    is converted by canonical_timestamp; an Enum's value is converted
    recursively. Lists and tuples become lists. Dictionaries retain their
    keys and recursively convert their values; keys must have type exactly
    str and already be NFC normalised.

    Types are matched exactly except for Enum members. Non-NFC strings,
    invalid dictionary keys and naive datetimes raise ValueError.
    Unsupported value types, including floats, raise TypeError. Nested
    values follow the same rules.
    """
    if value is None or type(value) in {bool, int}:
        return value
    if type(value) is str:
        if unicodedata.normalize("NFC", value) != value:
            raise ValueError("strings must be NFC-normalized")
        return value
    if type(value) is UUID:
        return str(value)
    if type(value) is datetime:
        return canonical_timestamp(value)
    if isinstance(value, Enum):
        return _canonicalise(value.value)
    if type(value) in {list, tuple}:
        return [_canonicalise(item) for item in value]
    if type(value) is dict:
        result: dict[str, object] = {}
        for key, item in value.items():
            if type(key) is not str or unicodedata.normalize("NFC", key) != key:
                raise ValueError("object keys must be NFC strings")
            result[key] = _canonicalise(item)
        return result
    raise TypeError("unsupported canonical primitive")


def canonical_json_bytes(value: object) -> bytes:
    """Return a value's canonical JSON as UTF-8 bytes.

    The value is converted by _canonicalise, then serialised with sorted
    keys, no separator whitespace and non-ASCII characters kept. Whitespace
    inside string values is preserved. These bytes feed the target
    fingerprint, the s015d1 decision reference, the recorded decision
    payload and target, and the refusal authority reference.
    """
    return json.dumps(
        _canonicalise(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def authority_target_fingerprint(target: dict[str, object]) -> str:
    """Return the SHA-256 hex digest of a target's canonical bytes under
    the organism authority target domain.

    _validate_request compares it with a request's target_fingerprint when
    the request carries a target.
    """
    return hashlib.sha256(_TARGET_DOMAIN + canonical_json_bytes(target)).hexdigest()


def _canonical_reference(value: object, name: str, maximum: int) -> str:
    """Return value if it is a canonical reference, else raise
    S015AuthorityMalformed.

    Canonical means a str, already NFC normalised with no surrounding
    whitespace, non-empty and at most maximum characters. name is used in
    the error message.
    """
    if type(value) is not str:
        raise S015AuthorityMalformed(f"{name} must be a string")
    canonical = unicodedata.normalize("NFC", value).strip()
    if canonical != value or not canonical or len(canonical) > maximum:
        raise S015AuthorityMalformed(f"{name} is not canonical")
    return canonical


def _decision_reference(result: S015AuthorityResult) -> str:
    """Return the s015d1 decision reference of a provider result.

    The reference is "s015d1:" followed by the SHA-256 hex digest of the
    decision domain and the canonical JSON bytes of asdict(result). When
    those bytes cannot be derived, raise
    S015AuthorityMalformed("authority result is not canonical"), chained
    from the TypeError, ValueError or UnicodeError. qualify uses it for
    refusals and responses after their echoed fields have been checked,
    so only a result-only field can fail here.
    """
    try:
        payload_bytes = canonical_json_bytes(asdict(result))
    except (TypeError, ValueError, UnicodeError) as exc:
        raise S015AuthorityMalformed("authority result is not canonical") from exc
    return "s015d1:" + hashlib.sha256(_DECISION_DOMAIN + payload_bytes).hexdigest()


def _validate_echo(result: S015AuthorityResult, request: S015AuthorityRequest) -> None:
    """Check that result echoes every field of request; return None.

    First, every field of the request's type must compare equal between
    result and request; any difference raises
    S015AuthorityMalformed("authority response echo mismatch"). Then each
    pair must render to the same canonical JSON bytes, so a value that
    compares equal but renders differently (a bool for an int) raises the
    same error, while a plain string for an Enum member or the same
    instant in another timezone passes. A request value that
    canonical_json_bytes cannot render raises
    S015AuthorityMalformed("authority request is malformed"), and a result
    value that it cannot render raises
    S015AuthorityMalformed("authority response echo is not canonical"),
    each chained from the TypeError, ValueError or UnicodeError. qualify
    calls this after the result's type check, for responses and refusals
    alike.
    """
    echoed = tuple(asdict(request))
    if any(getattr(result, field) != getattr(request, field) for field in echoed):
        raise S015AuthorityMalformed("authority response echo mismatch")
    for field in echoed:
        try:
            request_rendering = canonical_json_bytes(getattr(request, field))
        except (TypeError, ValueError, UnicodeError) as exc:
            raise S015AuthorityMalformed("authority request is malformed") from exc
        try:
            result_rendering = canonical_json_bytes(getattr(result, field))
        except (TypeError, ValueError, UnicodeError) as exc:
            raise S015AuthorityMalformed("authority response echo is not canonical") from exc
        if result_rendering != request_rendering:
            raise S015AuthorityMalformed("authority response echo mismatch")


def _validate_request(request: S015AuthorityRequest) -> None:
    """Validate an authority request's shape and canonical fields; return None.

    Checks, in order: the request has exactly one of the three request
    types; database_alias is a non-empty str; actor_pk is a positive int,
    actor_identity_id a UUID and actor_access_epoch a non-negative int;
    action is an OrganismAction; command_subtype is a str that has
    qualification planes and begins with the action's value;
    target_fingerprint is a str of 64 lowercase hex characters;
    request_reference and idempotency_key are canonical and within their
    128- and 120-character bounds; evaluated_at and any effective_at are
    timezone-aware; a supplied target is a dict whose fingerprint matches;
    and a supplied living_organism_id is a UUID.

    A pre-founding request for FOUND_LIVING_ORGANISM must carry effective_at,
    target and living_organism_id. A request whose result
    _should_record_application_decision selects must carry a non-empty
    target. Post-founding and own-applicant fields are checked as their
    carriers describe, outcome being a str when given.
    This helper reads no database state and does not establish whether the
    chosen request class matches the organism's existence or the actor's
    standing.

    Explicit validation refusals raise S015AuthorityMalformed. Called
    validators and operations can also raise TypeError or ValueError,
    including QualificationApplicabilityUnknown for an unknown subtype.
    qualify translates those validation failures to S015AuthorityMalformed;
    this helper does not perform that translation itself.
    """
    if type(request) not in {
        S015PreFoundingAuthorityRequest,
        S015PostFoundingAuthorityRequest,
        S015OwnApplicantAuthorityRequest,
    }:
        raise S015AuthorityMalformed("authority request type is invalid")
    if type(request.database_alias) is not str or not request.database_alias:
        raise S015AuthorityMalformed("database_alias is invalid")
    if type(request.actor_pk) is not int or request.actor_pk < 1:
        raise S015AuthorityMalformed("actor_pk is invalid")
    if type(request.actor_identity_id) is not UUID:
        raise S015AuthorityMalformed("actor_identity_id is invalid")
    if type(request.actor_access_epoch) is not int or request.actor_access_epoch < 0:
        raise S015AuthorityMalformed("actor_access_epoch is invalid")
    if type(request.action) is not OrganismAction:
        raise S015AuthorityMalformed("action is invalid")
    if type(request.command_subtype) is not str:
        raise S015AuthorityMalformed("command_subtype is invalid")
    qualification_planes(request.command_subtype)
    if not request.command_subtype.startswith(request.action.value):
        raise S015AuthorityMalformed("command subtype does not match action")
    if type(request.target_fingerprint) is not str:
        raise S015AuthorityMalformed("target_fingerprint is invalid")
    if re.fullmatch(r"[0-9a-f]{64}", request.target_fingerprint or "") is None:
        raise S015AuthorityMalformed("target_fingerprint is invalid")
    _canonical_reference(request.request_reference, "request_reference", 128)
    _canonical_reference(request.idempotency_key, "idempotency_key", 120)
    canonical_timestamp(request.evaluated_at)
    if request.effective_at is not None:
        canonical_timestamp(request.effective_at)
    if request.target is not None:
        if type(request.target) is not dict:
            raise S015AuthorityMalformed("plain target must be an object")
        if authority_target_fingerprint(request.target) != request.target_fingerprint:
            raise S015AuthorityMalformed("plain target fingerprint mismatch")
    if request.living_organism_id is not None and type(request.living_organism_id) is not UUID:
        raise S015AuthorityMalformed("living_organism_id is invalid")
    if (
        type(request) is S015PreFoundingAuthorityRequest
        and request.action is OrganismAction.FOUND_LIVING_ORGANISM
        and (
            request.effective_at is None
            or request.target is None
            or request.living_organism_id is None
        )
    ):
        raise S015AuthorityMalformed("founding request requires effective_at, target, and living_organism_id")
    if not request.target and _should_record_application_decision(request):
        raise S015AuthorityMalformed("recorded request requires a non-empty plain target")
    if type(request) is S015PostFoundingAuthorityRequest:
        _canonical_reference(
            request.active_membership_head_reference,
            "active_membership_head_reference",
            255,
        )
        if type(request.contextual_role_head_references) is not tuple:
            raise S015AuthorityMalformed("contextual role heads must be a tuple")
        for reference in request.contextual_role_head_references:
            _canonical_reference(reference, "contextual_role_head_reference", 255)
        for field_name in ("assignment_uuid", "version_uuid"):
            value = getattr(request, field_name)
            if value is not None and type(value) is not UUID:
                raise S015AuthorityMalformed(f"{field_name} is invalid")
        if request.outcome is not None and type(request.outcome) is not str:
            raise S015AuthorityMalformed("outcome is invalid")
        if request.outcome is not None and request.outcome not in {"ACCEPT", "DECLINE"}:
            raise S015AuthorityMalformed("outcome is invalid")
        if request.version_kind is not None:
            _canonical_reference(request.version_kind, "version_kind", 48)
    if type(request) is S015OwnApplicantAuthorityRequest:
        if request.action not in {OrganismAction.SUBMIT_APPLICATION, OrganismAction.WITHDRAW_APPLICATION}:
            raise S015AuthorityMalformed("own-applicant action is invalid")
        if type(request.actor_credential_pk) is not int or request.actor_credential_pk < 1:
            raise S015AuthorityMalformed("actor credential is invalid")
        if type(request.living_organism_pk) is not int or request.living_organism_pk < 1:
            raise S015AuthorityMalformed("living organism is invalid")
        if request.action is OrganismAction.SUBMIT_APPLICATION:
            if request.application_pk is not None:
                raise S015AuthorityMalformed("submission cannot name an application")
            if request.predecessor_reference is not None and type(request.predecessor_reference) is not UUID:
                raise S015AuthorityMalformed("predecessor_reference is invalid")
        elif type(request.application_pk) is not int or request.application_pk < 1:
            raise S015AuthorityMalformed("withdrawal requires an application")


def _response_types(request: S015AuthorityRequest) -> tuple[type[object], type[object]]:
    """Return the (response type, refusal type) pair for a request's type.

    Any request that is not pre-founding or own-applicant gets the
    post-founding pair; qualify calls this only after _validate_request has
    checked the request's type.
    """
    if type(request) is S015PreFoundingAuthorityRequest:
        return S015PreFoundingAuthorityResponse, S015PreFoundingAuthorityRefusal
    if type(request) is S015OwnApplicantAuthorityRequest:
        return S015OwnApplicantAuthorityResponse, S015OwnApplicantAuthorityRefusal
    return S015PostFoundingAuthorityResponse, S015PostFoundingAuthorityRefusal


_APPLICATION_ACTIONS = frozenset({
    OrganismAction.FOUND_LIVING_ORGANISM,
    OrganismAction.TRANSITION_LIVING_ORGANISM,
    OrganismAction.SUBMIT_APPLICATION,
    OrganismAction.WITHDRAW_APPLICATION,
    OrganismAction.CLAIM_APPLICATION,
    OrganismAction.REASSIGN_APPLICATION,
    OrganismAction.DECIDE_APPLICATION,
    OrganismAction.EFFECT_APPLICATION_SETTINGS,
    OrganismAction.EFFECT_APPLICATION_QUESTION,
    OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
    OrganismAction.EFFECT_ENTITLEMENT_ELECTION,
    OrganismAction.RECORD_ENTITLEMENT_GRANT,
    OrganismAction.TRANSITION_CONTEXTUAL_ROLE,
    OrganismAction.RECORD_OFFICE_CONSENT,
    OrganismAction.RECORD_ROLE_DEFINITION,
    OrganismAction.TRANSITION_MEMBERSHIP,
    OrganismAction.ADOPT_GOVERNING_INSTRUMENT,
    OrganismAction.CREATE_DORMANT_CIRCLE,
    OrganismAction.ADMIT_CIRCLE_PARTICIPANT,
    OrganismAction.REQUEST_GOVERNANCE_SEAT,
})


def _should_record_application_decision(request: S015AuthorityRequest) -> bool:
    """Select whether qualify records a request's authority result.

    True for actions in _APPLICATION_ACTIONS: application, gateway and
    entitlement acts, founding and Living Organism transitions, contextual
    role transitions, office consent, membership transitions and governance
    acts. A TRANSITION_MEMBERSHIP request with no target is excluded; the
    membership-opening path uses that form. Other actions are also
    excluded.

    This predicate applies to both qualified and refused results. False
    suppresses AuthorityDecision recording by qualify; it does not grant
    authority or bypass request and result validation. _validate_request
    also consults it: a selected request must carry a non-empty target.

    _APPLICATION_ACTIONS is kept as a literal. The facts vocabulary,
    core.organism_authority_facts.S015_AUTHORITY_FACTS, declares a row for
    exactly these actions; tests/test_s015_authority_facts_schema.py holds
    both to the same frozen set.
    """
    return request.action in _APPLICATION_ACTIONS and (
        request.action is not OrganismAction.TRANSITION_MEMBERSHIP or request.target is not None
    )


def _refusal_result(
    request: S015AuthorityRequest,
    refusal_code: S015AuthorityRefusalCode,
    disclosed_refusal_code: S015AuthorityRefusalCode,
) -> S015AuthorityResult:
    """Build qualify's own refusal for a request it refuses before
    consulting the provider.

    The result has the request type's refusal type, echoes every request
    field, carries refusal_code and disclosed_refusal_code as given, and has
    authority_reference "s015a1:" followed by the canonical JSON
    {"x": <refusal code value>}: the x-only refusal shape every action's
    vocabulary admits, built here by construction and not validated
    against the table. Raises nothing of its own.
    """
    refusal_type = _response_types(request)[1]
    fields = asdict(request)
    fields["refusal_code"] = refusal_code
    fields["disclosed_refusal_code"] = disclosed_refusal_code
    fields["authority_reference"] = S015_AUTHORITY_REFERENCE_PREFIX + canonical_json_bytes(
        {S015_AUTHORITY_REFUSAL_KEY: refusal_code.value},
    ).decode("utf-8")
    return refusal_type(**fields)


def _founding_authority_principal(database_alias: str) -> AuthorityPrincipal | None:
    """Return the AuthorityPrincipal a founding request rests on, or None.

    The first principal by primary key in the reserved genesis namespace
    INTEVIA_RESERVED_GENESIS_AUTHORITY_V1. When there is none and settings
    INTEVIA_FIXTURE_SUBSTITUTES_PERMITTED is true, the first in the fixture
    namespace INTEVIA_FIXTURE_AUTHORITY_V1; otherwise None, which qualify
    turns into a FOUNDING_PRINCIPAL_ABSENT refusal. Reads only.
    """
    manager = AuthorityPrincipal.objects.using(database_alias)
    principal = manager.filter(
        governed_source_namespace="INTEVIA_RESERVED_GENESIS_AUTHORITY_V1",
    ).order_by("pk").first()
    if principal is not None:
        return principal
    if not settings.INTEVIA_FIXTURE_SUBSTITUTES_PERMITTED:
        return None
    return manager.filter(
        governed_source_namespace="INTEVIA_FIXTURE_AUTHORITY_V1",
    ).order_by("pk").first()


def _facts_read(
    request: S015AuthorityRequest,
    authority_reference: str | None,
    founding_principal: AuthorityPrincipal | None = None,
) -> list[dict[str, object]]:
    """Build the request context and reported authority facts for a decision row.

    Each fact contains relation, reference and eligible_at. eligible_at
    holds canonical effective_at (the request's effective_at, falling back
    to evaluated_at) and evaluated_at. The actor's Identity is always
    included, and the Living Organism is included when its UUID is supplied.
    These coordinates label the facts; this helper does not query the
    referenced rows or verify their existence or temporal eligibility.

    For None or an unstructured authority reference, those are the only
    facts, except EFFECT_ENTITLEMENT_ELECTION requires a structured s015a1
    reference. A structured reference must parse as a JSON object. Its keys
    are read against the declared vocabulary of the request's action,
    core.organism_authority_facts.S015_AUTHORITY_FACTS (version
    S015_AUTHORITY_FACTS_SCHEMA): a reference carrying x is a refusal and
    selects the action's refusal shape for the request's form; any other
    reference must match exactly one qualified entry of that form by its
    discriminator keys, whose values are descriptive and add no fact. The
    key set is then checked: an exact shape must carry exactly the declared
    keys, and every shape refuses a key it does not declare. Outside the
    exact shapes no key is required of a reference here; the producer side
    requires keys.

    Facts are appended in this order: the identity and organism facts; for a
    FOUNDING reference, the founding block (platform terms version from
    rule, learner registration from registration, platform terms acceptance
    from terms, then the founding principal when one is supplied), each
    value in its exact prefixed form; for a PROPOSE whose k is in
    POLICY_LITERALS, the role definition named by the request target's
    role_definition_uuid; then one fact per remaining key
    in the reference's own key order. A REF, REF_OR_NULL or ENGAGEMENT
    value "-" adds no fact, a None value is a null reference only where the
    key is declared REF_OR_NULL, an int ENGAGEMENT becomes
    '<application_uuid>:<ordinal>' from the request target, a CONSENT_U
    value names its relation by its c: or e: prefix, and a declared
    reference prefix is added to a non-null reference only when absent.
    TEXT and LITERAL keys, including x, add no fact.

    Invalid structured JSON, a non-object, an undeclared action, an
    unresolved or ambiguous discriminator set, an unknown key, a shape
    violation, a null value, a mistyped value of a key whose declared kind
    this parser validates (REF, REF_OR_NULL, ENGAGEMENT, POLICY_K,
    CONSENT_U, CONSENT_W and the founding kinds; a TEXT or LITERAL value is
    skipped unread, and a policy letter outside its literal set is read as
    given, since strict literal enforcement is the producer's), a founding
    value without its exact form, a PROPOSE target without a role
    definition, a malformed consent or prior-consent reference, and an
    integer engagement without an application target raise
    S015AuthorityMalformed. The function reads no database state.
    """
    effective_at = getattr(request, "effective_at", None) or request.evaluated_at
    eligible_at = {
        "effective_at": canonical_timestamp(effective_at),
        "evaluated_at": canonical_timestamp(request.evaluated_at),
    }
    facts: list[dict[str, object]] = [{
        "relation": "core_identity",
        "reference": str(request.actor_identity_id),
        "eligible_at": eligible_at,
    }]
    living_organism_id = getattr(request, "living_organism_id", None)
    if living_organism_id is not None:
        facts.append({
            "relation": "core_livingorganism",
            "reference": str(living_organism_id),
            "eligible_at": eligible_at,
        })
    if authority_reference is None or not authority_reference.startswith(S015_AUTHORITY_REFERENCE_PREFIX):
        if request.action is OrganismAction.EFFECT_ENTITLEMENT_ELECTION:
            raise S015AuthorityMalformed("structured authority reference is invalid")
        return facts
    try:
        content = json.loads(authority_reference[len(S015_AUTHORITY_REFERENCE_PREFIX):])
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise S015AuthorityMalformed("structured authority reference is invalid") from exc
    if type(content) is not dict:
        raise S015AuthorityMalformed("structured authority reference must be an object")
    vocabulary = S015_AUTHORITY_FACTS.get(request.action)
    if vocabulary is None:
        raise S015AuthorityMalformed("no authority facts vocabulary is declared for this action")
    try:
        shape = select_shape(vocabulary, request, content)
    except FactsSelectionError as exc:
        raise S015AuthorityMalformed(str(exc)) from exc
    present = frozenset(content)
    if shape.exact_set is not None and present != shape.exact_set:
        raise S015AuthorityMalformed("structured authority reference has an unknown fact key")
    admissible = frozenset(shape.keys) | shape.discriminator_keys | {S015_AUTHORITY_REFUSAL_KEY}
    if present - admissible:
        raise S015AuthorityMalformed("structured authority reference has an unknown fact key")
    handled: set[str] = set(shape.discriminator_keys) | {S015_AUTHORITY_REFUSAL_KEY}
    if shape.founding_block:
        for key in ("rule", "registration", "terms"):
            if key in content:
                spec = shape.keys[key]
                reference = founding_value_form(spec.kind, content[key])
                if reference is None:
                    raise S015AuthorityMalformed("structured authority reference has an invalid fact value")
                facts.append({"relation": spec.relation, "reference": reference, "eligible_at": eligible_at})
        if founding_principal is not None:
            facts.append({
                "relation": "core_authorityprincipal",
                "reference": str(founding_principal.principal_uuid),
                "eligible_at": eligible_at,
            })
        handled |= FOUNDING_KEYS
    if shape.propose_role and type(content.get("k")) is str and content.get("k") in POLICY_LITERALS:
        role_reference = propose_role_reference(request)
        if role_reference is None:
            raise S015AuthorityMalformed("structured authority reference has an invalid fact value")
        facts.append({
            "relation": "core_organismroledefinition",
            "reference": role_reference,
            "eligible_at": eligible_at,
        })
    for key, value in content.items():
        if key in handled:
            continue
        spec = shape.keys[key]
        kind = spec.kind
        if kind is FactKind.PROVISION:
            definition = content.get("g")
            if value != "AR" or type(definition) is not str or definition == REFERENCE_SENTINEL:
                raise S015AuthorityMalformed("alternative route requires AR and definition g")
            facts.append({"relation": spec.relation, "reference": definition + ":ALTERNATIVE_ROUTE", "eligible_at": eligible_at})
            continue
        if key == "z" and spec.relation == "s015_entitlement_effective" and value != "CUSTOM_ROLES":
            raise S015AuthorityMalformed("custom entitlement fact is invalid")
        if kind in {FactKind.TEXT, FactKind.LITERAL}:
            continue
        if kind is FactKind.CONSENT_U:
            if type(value) is not str or value[:2] not in CONSENT_U_RELATIONS:
                raise S015AuthorityMalformed("structured consent reference is invalid")
            facts.append({
                "relation": CONSENT_U_RELATIONS[value[:2]],
                "reference": value[2:],
                "eligible_at": eligible_at,
            })
            continue
        if kind is FactKind.CONSENT_W:
            if type(value) is not str:
                raise S015AuthorityMalformed("prior consent reference is invalid")
            facts.append({"relation": spec.relation, "reference": value, "eligible_at": eligible_at})
            continue
        if kind is FactKind.POLICY_K:
            if type(value) is not str:
                raise S015AuthorityMalformed("structured authority reference has an invalid fact value")
            facts.append({"relation": spec.relation, "reference": value, "eligible_at": eligible_at})
            continue
        if value == REFERENCE_SENTINEL:
            continue
        if value is None:
            if kind is not FactKind.REF_OR_NULL:
                raise S015AuthorityMalformed("structured authority reference has a null fact value")
            facts.append({"relation": spec.relation, "reference": None, "eligible_at": eligible_at})
            continue
        if kind is FactKind.ENGAGEMENT and type(value) is int:
            target = request.target
            application_uuid = target.get("application_uuid") if type(target) is dict else None
            if application_uuid is None:
                raise S015AuthorityMalformed("application engagement reference has no application target")
            reference = f"{application_uuid}:{value}"
        elif type(value) is str:
            reference = value
        else:
            raise S015AuthorityMalformed("structured authority reference has an invalid fact value")
        if spec.prefix is not None and not reference.startswith(spec.prefix):
            reference = spec.prefix + reference
        facts.append({"relation": spec.relation, "reference": reference, "eligible_at": eligible_at})
    return facts


def _record_application_decision(
    *,
    provider: S015AuthorityProvider,
    request: S015AuthorityRequest,
    response: S015AuthorityResult,
    decision_reference: str,
    outcome: str,
    refusal_code: S015AuthorityRefusalCode | None,
    founding_principal: AuthorityPrincipal | None = None,
) -> None:
    """Create or reuse an AuthorityDecision for a qualification or refusal.

    Before the database lookup, canonicalise the response and target,
    derive their JSON, build the reported facts, and resolve the provider's
    identity and version as described by S015AuthorityProvider. The facts
    are read by _facts_read against the declared vocabulary
    (core.organism_authority_facts); invalid canonical data propagates its
    error, an invalid reference and an invalid provider version raise
    S015AuthorityMalformed.

    A new row carries the response bytes and JSON, request kind, action,
    subtype, actor and any applicant credential, target bytes and JSON
    ({} for no target, a case _validate_request refuses for every
    request qualify records), target fingerprint, effective_at (falling
    back to evaluated_at), evaluated_at, authority reference, facts,
    outcome, refusal code value or None, provider metadata and request
    identifiers. recorded_at is supplied as None for the database to
    assign.

    Migration 0034's insert guard checks that the supplied byte fields
    decode to their accompanying JSON, and that the target fingerprint and
    decision reference recompute from those bytes. It does not recreate
    the canonical bytes from the JSON. Its triggers prohibit update,
    delete and truncate of decision rows.

    get_or_create looks up decision_reference. If a row exists, only its
    decision payload bytes are compared: different bytes raise
    S015AuthorityMalformed, while equal bytes reuse the row without
    refreshing its other columns. qualify invokes this helper inside the
    caller's atomic block; the helper does not commit that transaction.
    New rows survive only if the surrounding transaction commits.
    """
    response_bytes = canonical_json_bytes(asdict(response))
    response_payload = json.loads(response_bytes.decode("utf-8"))
    target_bytes = canonical_json_bytes(getattr(request, "target", None) or {})
    target_payload = json.loads(target_bytes.decode("utf-8"))
    authority_reference = getattr(response, "authority_reference", None)
    facts_read = _facts_read(request, authority_reference, founding_principal)
    provider_identity = getattr(provider, "provider_identity", None)
    if type(provider_identity) is not str:
        provider_identity = f"{type(provider).__module__}.{type(provider).__qualname__}"
    provider_version = getattr(provider, "provider_version", "UNSPECIFIED")
    if type(provider_version) is not str:
        raise S015AuthorityMalformed("provider_version is invalid")
    effective_at = getattr(request, "effective_at", None) or request.evaluated_at
    credential_pk = getattr(request, "actor_credential_pk", None)
    defaults = {
            "decision_payload": response_payload,
            "decision_payload_bytes": response_bytes,
            "request_kind": type(request).__name__,
            "action": request.action.value,
            "command_subtype": request.command_subtype,
            "actor_id": request.actor_pk,
            "actor_identity_id": request.actor_identity_id,
            "actor_access_epoch": request.actor_access_epoch,
            "actor_credential_pk": credential_pk,
            "target": target_payload,
            "target_bytes": target_bytes,
            "target_fingerprint": request.target_fingerprint,
            "effective_at": effective_at,
            "evaluated_at": request.evaluated_at,
            "authority_reference": authority_reference,
            "facts_read": facts_read,
            "outcome": outcome,
            "refusal_code": None if refusal_code is None else refusal_code.value,
            "provider_identity": provider_identity,
            "provider_version": provider_version,
            "request_reference": request.request_reference,
            "idempotency_key": request.idempotency_key,
            "recorded_at": None,
        }
    record, created = AuthorityDecision.objects.using(request.database_alias).get_or_create(
        decision_reference=decision_reference,
        defaults=defaults,
    )
    if not created and record.decision_payload_bytes != response_bytes:
        raise S015AuthorityMalformed("authority decision reference collision")


class S015Authority:
    """Qualifies organism commands through an injected authority provider,
    on one database alias.

    Services hold one and call qualify inside their command transaction.
    """
    def __init__(self, *, provider: S015AuthorityProvider, database_alias: str = "default"):
        """Bind the provider and the database alias.

        Raises TypeError unless the provider has a callable
        evaluate_organism_authority, and ValueError unless database_alias is a
        non-empty str.
        """
        method = getattr(provider, "evaluate_organism_authority", None)
        if provider is None or not callable(method):
            raise TypeError("provider must implement evaluate_organism_authority")
        if type(database_alias) is not str or not database_alias:
            raise ValueError("database_alias is required")
        self.provider = provider
        self.database_alias = database_alias

    def qualify(self, request: S015AuthorityRequest) -> QualifiedS015AuthorityDecision:
        """Judge a request's authority; return the qualified decision or raise.

        Validate the request first, translating TypeError, ValueError,
        UnicodeError, OverflowError and RecursionError from validation to
        S015AuthorityMalformed. The database alias must match this
        authority's and the connection must already be inside an atomic
        block; this check does not require an outermost block.

        Preflight bypasses the provider for a pre-founding FOUND_LIVING_ORGANISM
        request with no founding principal (FOUNDING_PRINCIPAL_ABSENT), or an
        own-applicant withdrawal whose current identity, active credential and
        bound access epoch do not match (E_IDENTITY_INACTIVE), or whose
        application does not belong to that actor in that LO (E_NOT_APPLICANT,
        disclosed as DENIED). If both withdrawal checks fail, the application
        refusal replaces the identity refusal. Those checks use current rows,
        not historical state at evaluated_at.

        Otherwise call the provider, translating an Exception it raises to
        S015AuthorityUnavailable with the original exception chained. Reject
        awaitable results as malformed, closing coroutine results. Require the
        exact corresponding response or refusal type and check every echoed
        request field for equality and for the same canonical JSON; a
        mismatch, or a field that cannot be rendered canonically, raises
        S015AuthorityMalformed.

        For a refusal, require the exact S015AuthorityRefusalCode type and,
        when authority_reference is given, a canonical one of at most 255
        characters; derive the s015d1 reference from its canonical bytes,
        raising S015AuthorityMalformed for a result that is not canonical;
        then record a REFUSED decision when selected by
        _should_record_application_decision. Resolve
        the disclosed code using disclosed_refusal_code or refusal_code, so
        false-valued disclosures fall back to the refusal code. Only then check
        that the resolved code has exactly S015AuthorityRefusalCode type;
        otherwise raise S015AuthorityMalformed. Thus that error can occur after
        the recording call, and this function does not itself roll back writes.

        Raise S015AuthorityDenied with the refusal code, resolved disclosed code
        and decision reference. Use the founder-protection message for
        C_FOUNDER_PROTECTED, the fixed not-yet-implemented messages for
        C_COORDINATOR_RECOVERY_NOT_IMPLEMENTED,
        C_MEMBERSHIP_TRANSITION_NOT_IMPLEMENTED and
        C_MEMBERSHIP_LEAVE_WITH_OFFICE_NOT_IMPLEMENTED, and 'organism authority
        denied' otherwise. The denial is raised inside the caller's atomic
        block. authority_atomic can defer it until that helper's block exits
        successfully, but a nested block is not a durable commit and enclosing
        transactions can still roll back the row.

        For a qualification, require a canonical authority_reference of at
        most 255 characters, derive the s015d1 reference from the response's
        canonical bytes, raising S015AuthorityMalformed for a response that is
        not canonical, record a QUALIFIED decision when selected, and return
        QualifiedS015AuthorityDecision. A decision reference alone does not
        prove that a decision row was recorded or committed.

        qualify does not commit or explicitly acquire the command's coordination
        locks. It does not guarantee lock-free provider calls or database writes.
        Errors outside the specified translation sites propagate to the caller.
        """
        try:
            _validate_request(request)
        except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError) as exc:
            if isinstance(exc, S015AuthorityMalformed):
                raise
            raise S015AuthorityMalformed("authority request is malformed") from exc
        if request.database_alias != self.database_alias:
            raise S015AuthorityMalformed("database alias mismatch")
        if not connections[self.database_alias].in_atomic_block:
            raise S015AuthorityMalformed("outer atomic transaction required")
        preflight_refusal = None
        founding_principal = None
        if (
            type(request) is S015PreFoundingAuthorityRequest
            and request.action is OrganismAction.FOUND_LIVING_ORGANISM
        ):
            founding_principal = _founding_authority_principal(self.database_alias)
            if founding_principal is None:
                preflight_refusal = _refusal_result(
                    request,
                    S015AuthorityRefusalCode.FOUNDING_PRINCIPAL_ABSENT,
                    S015AuthorityRefusalCode.FOUNDING_PRINCIPAL_ABSENT,
                )
        if type(request) is S015OwnApplicantAuthorityRequest:
            if request.action is OrganismAction.SUBMIT_APPLICATION:
                pass
            elif not Identity.objects.using(self.database_alias).filter(
                pk=request.actor_pk,
                identity_id=request.actor_identity_id,
                credential_id=request.actor_credential_pk,
                access_epoch=request.actor_access_epoch,
                access_state=Identity.AccessState.ACTIVE,
                credential__is_active=True,
            ).exists():
                preflight_refusal = _refusal_result(
                    request,
                    S015AuthorityRefusalCode.E_IDENTITY_INACTIVE,
                    S015AuthorityRefusalCode.E_IDENTITY_INACTIVE,
                )
            if request.application_pk is not None and not Application.objects.using(self.database_alias).filter(
                pk=request.application_pk,
                applicant_id=request.actor_pk,
                living_organism_id=request.living_organism_pk,
            ).exists():
                preflight_refusal = _refusal_result(
                    request,
                    S015AuthorityRefusalCode.E_NOT_APPLICANT,
                    S015AuthorityRefusalCode.DENIED,
                )
        if preflight_refusal is not None:
            result = preflight_refusal
        else:
            try:
                result = self.provider.evaluate_organism_authority(request)
            except Exception as exc:
                raise S015AuthorityUnavailable("organism authority unavailable") from exc
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise S015AuthorityMalformed("authority response must be synchronous")
        response_type, refusal_type = _response_types(request)
        if type(result) not in {response_type, refusal_type}:
            raise S015AuthorityMalformed("authority response type is invalid")
        _validate_echo(result, request)
        if type(result) is refusal_type:
            if type(result.refusal_code) is not S015AuthorityRefusalCode:
                raise S015AuthorityMalformed("refusal code is invalid")
            if result.authority_reference is not None:
                _canonical_reference(result.authority_reference, "authority_reference", S015_AUTHORITY_REFERENCE_MAX_LENGTH)
            decision_reference = _decision_reference(result)
            if _should_record_application_decision(request):
                _record_application_decision(
                    provider=self.provider,
                    request=request,
                    response=result,
                    decision_reference=decision_reference,
                    outcome="REFUSED",
                    refusal_code=result.refusal_code,
                    founding_principal=founding_principal,
                )
            disclosed_refusal_code = (
                result.disclosed_refusal_code or result.refusal_code
            )
            if type(disclosed_refusal_code) is not S015AuthorityRefusalCode:
                raise S015AuthorityMalformed("disclosed refusal code is invalid")
            if disclosed_refusal_code is S015AuthorityRefusalCode.C_COORDINATOR_RECOVERY_NOT_IMPLEMENTED:
                message = "the required recovery route is not yet implemented"
            elif disclosed_refusal_code is S015AuthorityRefusalCode.C_MEMBERSHIP_TRANSITION_NOT_IMPLEMENTED:
                message = "membership transition is not yet implemented"
            elif disclosed_refusal_code is S015AuthorityRefusalCode.C_MEMBERSHIP_LEAVE_WITH_OFFICE_NOT_IMPLEMENTED:
                message = "leaving while holding an office is not yet implemented (IL-2)"
            elif disclosed_refusal_code is S015AuthorityRefusalCode.C_FOUNDER_PROTECTED:
                message = C_FOUNDER_PROTECTED_MESSAGE
            else:
                message = "organism authority denied"
            raise S015AuthorityDenied(
                message,
                refusal_code=result.refusal_code,
                disclosed_refusal_code=disclosed_refusal_code,
                authority_decision_reference=decision_reference,
            )
        authority_reference = _canonical_reference(
            result.authority_reference, "authority_reference", S015_AUTHORITY_REFERENCE_MAX_LENGTH
        )
        decision_reference = _decision_reference(result)
        if _should_record_application_decision(request):
            _record_application_decision(
                provider=self.provider,
                request=request,
                response=result,
                decision_reference=decision_reference,
                outcome="QUALIFIED",
                refusal_code=None,
                founding_principal=founding_principal,
            )
        post_founding = type(request) is S015PostFoundingAuthorityRequest
        return QualifiedS015AuthorityDecision(
            database_alias=result.database_alias,
            actor_pk=result.actor_pk,
            actor_identity_id=result.actor_identity_id,
            actor_access_epoch=result.actor_access_epoch,
            action=result.action,
            command_subtype=result.command_subtype,
            target_fingerprint=result.target_fingerprint,
            request_reference=result.request_reference,
            idempotency_key=result.idempotency_key,
            evaluated_at=result.evaluated_at,
            active_membership_head_reference=(
                result.active_membership_head_reference if post_founding else None
            ),
            contextual_role_head_references=(
                result.contextual_role_head_references if post_founding else ()
            ),
            authority_reference=authority_reference,
            authority_decision_reference=decision_reference,
        )


__all__ = [
    "C_FOUNDER_PROTECTED_MESSAGE",
    "SB_BIRTHING_ELECTORATE_EMPTY_MESSAGE",
    "SB_BIRTHING_ELECTORATE_EMPTY_REASON",
    "QualifiedS015AuthorityDecision",
    "S015Authority",
    "S015AuthorityDenied",
    "S015AuthorityError",
    "S015AuthorityMalformed",
    "S015AuthorityProvider",
    "S015AuthorityRefusalCode",
    "S015AuthorityUnavailable",
    "S015PostFoundingAuthorityRefusal",
    "S015PostFoundingAuthorityRequest",
    "S015PostFoundingAuthorityResponse",
    "S015OwnApplicantAuthorityRefusal",
    "S015OwnApplicantAuthorityRequest",
    "S015OwnApplicantAuthorityResponse",
    "S015PreFoundingAuthorityRefusal",
    "S015PreFoundingAuthorityRequest",
    "S015PreFoundingAuthorityResponse",
    "authority_target_fingerprint",
    "canonical_json_bytes",
    "canonical_timestamp",
]