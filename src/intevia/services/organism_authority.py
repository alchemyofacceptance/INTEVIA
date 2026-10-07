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


_TARGET_DOMAIN = b"INTEVIA:S015:ORGANISM_AUTHORITY_TARGET:v1\x00"
_DECISION_DOMAIN = b"INTEVIA:S015:ORGANISM_AUTHORITY_DECISION:v1\x00"


class S015AuthorityError(Exception):
    pass


class S015AuthorityUnavailable(S015AuthorityError):
    pass


class S015AuthorityMalformed(S015AuthorityError):
    pass


class S015AuthorityDenied(S015AuthorityError):
    def __init__(
        self,
        message: str = "organism authority denied",
        *,
        refusal_code: "S015AuthorityRefusalCode | None" = None,
        disclosed_refusal_code: "S015AuthorityRefusalCode | None" = None,
        authority_decision_reference: str | None = None,
    ) -> None:
        super().__init__(message)
        self.refusal_code = refusal_code
        self.disclosed_refusal_code = disclosed_refusal_code
        self.authority_decision_reference = authority_decision_reference


class S015AuthorityRefusalCode(str, Enum):
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
    SB_IL_GOVCONST = "SB-IL-GOVCONST"
    INV_CQ_ROUTE_REQUIRED = "INV_CQ_ROUTE_REQUIRED"
    C_GATEWAY_NOT_OPEN = "C_GATEWAY_NOT_OPEN"
    C_ROUTE_IS_CQ = "C_ROUTE_IS_CQ"
    C_CONFIGURATION_NOT_ESTABLISHED = "C_CONFIGURATION_NOT_ESTABLISHED"
    C_CHANGE_SELF_AUTHORISING = "C_CHANGE_SELF_AUTHORISING"
    C_TERMINAL_ROW_STANDS = "C_TERMINAL_ROW_STANDS"
    C_APPLICATION_OPEN = "C_APPLICATION_OPEN"
    C_LO_PAUSED = "C_LO_PAUSED"
    C_LO_CLOSED = "C_LO_CLOSED"
    C_LO_NO_CAPACITY = "C_LO_NO_CAPACITY"
    C_COORDINATOR_RECOVERY_NOT_IMPLEMENTED = "C_COORDINATOR_RECOVERY_NOT_IMPLEMENTED"
    C_REFERENCE_UNREPRESENTABLE = "C_REFERENCE_UNREPRESENTABLE"
    E_NOMINATION_POLICY_NOT_ENTITLED = "E_NOMINATION_POLICY_NOT_ENTITLED"
    C_OFFICE_CONSENT_ABSENT_OR_WITHDRAWN = "C_OFFICE_CONSENT_ABSENT_OR_WITHDRAWN"
    C_MEMBERSHIP_TRANSITION_NOT_IMPLEMENTED = "C_MEMBERSHIP_TRANSITION_NOT_IMPLEMENTED"
    C_MEMBERSHIP_LEAVE_WITH_OFFICE_NOT_IMPLEMENTED = "C_MEMBERSHIP_LEAVE_WITH_OFFICE_NOT_IMPLEMENTED"
    UNRECOGNISED_ACTION = "UNRECOGNISED_ACTION"
    UNRECOGNISED_ROUTE = "UNRECOGNISED_ROUTE"


@dataclass(frozen=True, slots=True)
class S015PreFoundingAuthorityRequest:
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
    def evaluate_organism_authority(
        self, request: S015AuthorityRequest
    ) -> S015AuthorityResult: ...


@dataclass(frozen=True, slots=True)
class QualifiedS015AuthorityDecision:
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
    access_state: str | None
    access_epoch: int


def identity_state_at(
    identity_id,
    *,
    state_at: datetime,
    known_at: datetime,
    database_alias: str = "default",
) -> IdentityStateAt:
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
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    utc_value = value.astimezone(timezone.utc)
    return (
        f"{utc_value.year:04d}-{utc_value.month:02d}-{utc_value.day:02d}"
        f"T{utc_value.hour:02d}:{utc_value.minute:02d}:{utc_value.second:02d}"
        f".{utc_value.microsecond:06d}Z"
    )


def _canonicalise(value: object) -> object:
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
    return json.dumps(
        _canonicalise(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def authority_target_fingerprint(target: dict[str, object]) -> str:
    return hashlib.sha256(_TARGET_DOMAIN + canonical_json_bytes(target)).hexdigest()


def _canonical_reference(value: object, name: str, maximum: int) -> str:
    if type(value) is not str:
        raise S015AuthorityMalformed(f"{name} must be a string")
    canonical = unicodedata.normalize("NFC", value).strip()
    if canonical != value or not canonical or len(canonical) > maximum:
        raise S015AuthorityMalformed(f"{name} is not canonical")
    return canonical


def _validate_request(request: S015AuthorityRequest) -> None:
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
    qualification_planes(request.command_subtype)
    if not request.command_subtype.startswith(request.action.value):
        raise S015AuthorityMalformed("command subtype does not match action")
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
    OrganismAction.TRANSITION_CONTEXTUAL_ROLE,
    OrganismAction.RECORD_OFFICE_CONSENT,
    OrganismAction.TRANSITION_MEMBERSHIP,
})


def _should_record_application_decision(request: S015AuthorityRequest) -> bool:
    return request.action in _APPLICATION_ACTIONS and (
        request.action is not OrganismAction.TRANSITION_MEMBERSHIP or request.target is not None
    )


def _refusal_result(
    request: S015AuthorityRequest,
    refusal_code: S015AuthorityRefusalCode,
    disclosed_refusal_code: S015AuthorityRefusalCode,
) -> S015AuthorityResult:
    refusal_type = _response_types(request)[1]
    fields = asdict(request)
    fields["refusal_code"] = refusal_code
    fields["disclosed_refusal_code"] = disclosed_refusal_code
    fields["authority_reference"] = "s015a1:" + canonical_json_bytes({"x": refusal_code.value}).decode("utf-8")
    return refusal_type(**fields)


def _founding_authority_principal(database_alias: str) -> AuthorityPrincipal | None:
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
    if authority_reference is None or not authority_reference.startswith("s015a1:"):
        return facts
    try:
        content = json.loads(authority_reference[len("s015a1:"):])
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise S015AuthorityMalformed("structured authority reference is invalid") from exc
    if type(content) is not dict:
        raise S015AuthorityMalformed("structured authority reference must be an object")
    phase_advance_reference = (
        request.action is OrganismAction.TRANSITION_LIVING_ORGANISM
        and content.get("kind") == "PHASE_ADVANCE"
    )
    if request.action is OrganismAction.FOUND_LIVING_ORGANISM and content.get("kind") == "FOUNDING":
        rule_reference = content.get("rule")
        if type(rule_reference) is str and rule_reference.startswith("platform-terms-version:"):
            version_reference = rule_reference[len("platform-terms-version:"):].removesuffix("#founding")
            facts.append({
                "relation": "core_platformtermsversion",
                "reference": version_reference,
                "eligible_at": eligible_at,
            })
        registration_reference = content.get("registration")
        if type(registration_reference) is str:
            facts.append({
                "relation": "learner-registration",
                "reference": registration_reference,
                "eligible_at": eligible_at,
            })
        acceptance_reference = content.get("terms")
        if type(acceptance_reference) is str:
            facts.append({
                "relation": "core_platformtermsacceptance",
                "reference": acceptance_reference.removeprefix("platform-terms-acceptance:"),
                "eligible_at": eligible_at,
            })
        if founding_principal is not None:
            facts.append({
                "relation": "core_authorityprincipal",
                "reference": str(founding_principal.principal_uuid),
                "eligible_at": eligible_at,
            })
    if (
        request.action is OrganismAction.TRANSITION_CONTEXTUAL_ROLE
        and (request.target or {}).get("event_action") == "PROPOSE"
        and content.get("k") in {"A", "B", "C"}
        and "g" not in content
    ):
        role_uuid = (request.target or {}).get("role_definition_uuid")
        if role_uuid is not None:
            facts.append({
                "relation": "core_organismroledefinition",
                "reference": str(role_uuid),
                "eligible_at": eligible_at,
            })

    prefix = {
        "b": ("s3_il_birthing_fact_source", None),
        "c": ("core_logovernanceconfigurationversion", "governance-configuration-version:"),
        "d": ("s2b_coordinator_nomination_policy_default", None),
        "g": ("core_organismroledefinition", None),
        "i": ("s2b_coordinator_nomination_entitlement", None),
        "m": ("core_organismmembershiptransition", None),
        "n": ("core_organismmembershiptransition", None),
        "o": ("core_contextualroleassignmentstateevent", None),
        "p": ("core_application", None),
        "e": ("core_applicationengagement", None),
        "q": ("core_loapplicationquestionversion", "application-question-version:"),
        "r": ("core_identitytransition", "identity-transition:"),
        "s": ("core_loapplicationsettingsversion", "application-settings-version:"),
        "v": (_version_relation(request.action), _version_reference_prefix(request.action)),
        "a": (_assignment_or_application_relation(request.action), None),
    }
    if request.action is OrganismAction.RECORD_OFFICE_CONSENT:
        prefix.update({
            "a": ("core_contextualroleassignment", None),
            "g": ("core_organismroledefinition", None),
            "p": ("core_contextualroleassignmentstateevent", None),
            "u": ("core_organismmembership", None),
        })
    elif request.action is OrganismAction.TRANSITION_CONTEXTUAL_ROLE:
        prefix.update({
            "a": ("core_contextualroleassignment", None),
            "g": ("core_organismroledefinition", None),
            "p": ("core_contextualroleassignmentstateevent", None),
        })
    if phase_advance_reference:
        prefix["o"] = ("core_contextualroleassignment", None)
    for key, value in content.items():
        if phase_advance_reference and key in {"kind", "f", "p"}:
            continue
        if request.action is OrganismAction.FOUND_LIVING_ORGANISM and key in {
            "kind", "rule", "registration", "terms",
        }:
            continue
        if key == "k" and request.action is OrganismAction.TRANSITION_CONTEXTUAL_ROLE and (
            request.target or {}
        ).get("event_action") == "PROPOSE":
            facts.append({
                "relation": "s2b_coordinator_nomination_policy",
                "reference": str(value),
                "eligible_at": eligible_at,
            })
            continue
        if key in {"k", "t", "x"}:
            continue
        if key == "u" and request.action is OrganismAction.TRANSITION_CONTEXTUAL_ROLE:
            if type(value) is not str:
                raise S015AuthorityMalformed("structured consent reference is invalid")
            if value.startswith("c:"):
                facts.append({
                    "relation": "core_officeappointmentconsent",
                    "reference": value[2:],
                    "eligible_at": eligible_at,
                })
                continue
            if value.startswith("e:"):
                facts.append({
                    "relation": "core_contextualroleassignmentstateevent",
                    "reference": value[2:],
                    "eligible_at": eligible_at,
                })
                continue
            raise S015AuthorityMalformed("structured consent reference is invalid")
        if key == "w" and request.action is OrganismAction.RECORD_OFFICE_CONSENT:
            if type(value) is not str:
                raise S015AuthorityMalformed("prior consent reference is invalid")
            facts.append({
                "relation": "core_officeappointmentconsent",
                "reference": value,
                "eligible_at": eligible_at,
            })
            continue
        if key not in prefix:
            raise S015AuthorityMalformed("structured authority reference has an unknown fact key")
        if value == "-":
            continue
        relation, reference_prefix = prefix[key]
        if (
            key == "e"
            and type(value) is int
            and request.action in {
                OrganismAction.DECIDE_APPLICATION,
                OrganismAction.REASSIGN_APPLICATION,
            }
        ):
            target = request.target
            application_uuid = target.get("application_uuid") if type(target) is dict else None
            if application_uuid is None:
                raise S015AuthorityMalformed("application engagement reference has no application target")
            reference = f"{application_uuid}:{value}"
        else:
            reference = None if value is None else str(value)
        if reference is not None and reference_prefix is not None and not reference.startswith(reference_prefix):
            reference = reference_prefix + reference
        facts.append({"relation": relation, "reference": reference, "eligible_at": eligible_at})
    return facts


def _version_relation(action: OrganismAction) -> str:
    return {
        OrganismAction.EFFECT_APPLICATION_SETTINGS: "core_loapplicationsettingsversion",
        OrganismAction.EFFECT_APPLICATION_QUESTION: "core_loapplicationquestionversion",
        OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION: "core_logovernanceconfigurationversion",
    }.get(action, "")


def _version_reference_prefix(action: OrganismAction) -> str:
    return {
        OrganismAction.EFFECT_APPLICATION_SETTINGS: "application-settings-version:",
        OrganismAction.EFFECT_APPLICATION_QUESTION: "application-question-version:",
        OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION: "governance-configuration-version:",
    }.get(action, "")


def _assignment_or_application_relation(action: OrganismAction) -> str:
    return "core_csaassignment" if action in {
        OrganismAction.CLAIM_APPLICATION,
        OrganismAction.REASSIGN_APPLICATION,
        OrganismAction.DECIDE_APPLICATION,
    } else "core_application"


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
    def __init__(self, *, provider: S015AuthorityProvider, database_alias: str = "default"):
        method = getattr(provider, "evaluate_organism_authority", None)
        if provider is None or not callable(method):
            raise TypeError("provider must implement evaluate_organism_authority")
        if type(database_alias) is not str or not database_alias:
            raise ValueError("database_alias is required")
        self.provider = provider
        self.database_alias = database_alias

    def qualify(self, request: S015AuthorityRequest) -> QualifiedS015AuthorityDecision:
        try:
            _validate_request(request)
        except (TypeError, ValueError, UnicodeError) as exc:
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
        echoed = tuple(asdict(request))
        if any(getattr(result, field) != getattr(request, field) for field in echoed):
            raise S015AuthorityMalformed("authority response echo mismatch")
        if type(result) is refusal_type:
            if type(result.refusal_code) is not S015AuthorityRefusalCode:
                raise S015AuthorityMalformed("refusal code is invalid")
            decision_payload = asdict(result)
            decision_reference = "s015d1:" + hashlib.sha256(
                _DECISION_DOMAIN + canonical_json_bytes(decision_payload)
            ).hexdigest()
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
            else:
                message = "organism authority denied"
            raise S015AuthorityDenied(
                message,
                refusal_code=result.refusal_code,
                disclosed_refusal_code=disclosed_refusal_code,
                authority_decision_reference=decision_reference,
            )
        authority_reference = _canonical_reference(
            result.authority_reference, "authority_reference", 255
        )
        decision_payload = asdict(result)
        decision_reference = "s015d1:" + hashlib.sha256(
            _DECISION_DOMAIN + canonical_json_bytes(decision_payload)
        ).hexdigest()
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