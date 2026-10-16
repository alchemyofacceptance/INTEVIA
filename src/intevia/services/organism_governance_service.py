from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, NoReturn
from uuid import UUID, uuid4, uuid5

from django.contrib.auth.models import User
from django.db import IntegrityError, connections, transaction

from core.models import (
    Circle,
    CircleAdmissionDecision,
    CircleJoinRequest,
    CircleParticipation,
    CircleParticipationEnd,
    CircleStateEvent,
    GovernedRule,
    Identity,
    LivingOrganism,
    LivingOrganismEvent,
    OrganismMembership,
    OrganismCommandReceipt,
)
from core.organism_contract import OrganismAction
from src.intevia.services.organism_application_authority import (
    ProductionApplicationAuthorityProvider,
    _effective_version,
)
from src.intevia.services.organism_authority import (
    S015Authority,
    S015AuthorityDenied,
    S015AuthorityRefusalCode,
    QualifiedS015AuthorityDecision,
    authority_target_fingerprint,
    canonical_json_bytes,
    canonical_timestamp,
)
from src.intevia.services.organism_command_support import (
    ReplayResultType,
    ReplayViewer,
    _authority_request,
    _canonical_key,
    _canonical_uuid,
    _empty_anchor_fingerprint,
    _lock_actor,
    _lock_actor_membership_context,
    _lock_registered_organism,
    _resulting_temporal_basis,
    acquire_organism_command_mutex,
    authority_atomic,
    serve_replay,
)


_RULE_SUBTYPE = "ADOPT_GOVERNING_INSTRUMENT/GOVERNANCE_CONSTITUTION"
_CIRCLE_SUBTYPE = OrganismAction.CREATE_DORMANT_CIRCLE.value
_FOUNDING_SEATING_SUBTYPE = OrganismAction.ADMIT_CIRCLE_PARTICIPANT.value
_SEAT_REQUEST_SUBTYPE = OrganismAction.REQUEST_GOVERNANCE_SEAT.value
_SEAT_ID = re.compile(r"[a-z0-9_-]{1,64}\Z")
_ROUTE_BODIES = {
    "ELECTION": {"body:LO_MEMBERSHIP"},
    "COOPTION": {"body:GOVERNANCE"},
    "APPOINTMENT": {"office:LO_COORDINATOR"},
    "EX_OFFICIO": set(),
}
_AUTHORED_REFUSALS = {
    S015AuthorityRefusalCode.SBG_SECOND_GOVERNANCE_CIRCLE: (
        "No Circle was created and nothing changed. This LO already has its GOVERNANCE Circle, and "
        "an LO has exactly one. Use the existing Circle; switching GOVERNANCE back on uses the same one."
    ),
}
_ADOPTION_GUARD_REFUSAL_CODES = frozenset({
    S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
    S015AuthorityRefusalCode.SBG_ARRANGEMENT_FIELD_UNSUPPORTED,
    S015AuthorityRefusalCode.SBG_ELIGIBILITY_UNSUPPORTED,
})
_REQUEST_GUARD_REFUSAL_CODES = frozenset({
    S015AuthorityRefusalCode.SBG_MEMBER_NOT_ELIGIBLE,
    S015AuthorityRefusalCode.SBG_SEAT_ABSENT,
})
_ADMISSION_GUARD_REFUSAL_CODES = frozenset({
    S015AuthorityRefusalCode.SBG_MEMBER_NOT_ELIGIBLE,
    S015AuthorityRefusalCode.SBG_SEAT_ABSENT,
    S015AuthorityRefusalCode.SBG_SEAT_OCCUPIED,
    S015AuthorityRefusalCode.SBG_ALREADY_SEATED,
})


def _governance_applicant_identity_uuid(
    applicant_membership: OrganismMembership,
) -> str:
    return str(applicant_membership.identity.identity_id)


def _governance_self_decision(
    applicant_membership: OrganismMembership,
    actor: Identity,
) -> bool:
    return applicant_membership.identity_id == actor.pk


def _authored_refusal_message(
    code: S015AuthorityRefusalCode,
    detail: str | None,
    *,
    act: str,
    organism_state: str | None = None,
) -> str | None:
    if code is S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE:
        condition = detail or "the arrangement does not meet its requirements"
        return (
            f"{act} and nothing changed. The arrangement is incomplete: {condition}. "
            "Adopt a corrected version, then try again. Every seat needs an admission route "
            "and a deciding body that matches it."
        )
    if code is S015AuthorityRefusalCode.SBG_ARRANGEMENT_FIELD_UNSUPPORTED:
        condition = detail or "a field this release does not record"
        return (
            f"No arrangement was adopted and nothing changed. The arrangement includes {condition}, "
            "which this release cannot yet act on. Leave it out for now; it can be added by a later "
            "version once the release that acts on it is delivered."
        )
    if code is S015AuthorityRefusalCode.SBG_ELIGIBILITY_UNSUPPORTED:
        seat_id = detail or "the requested seat"
        return (
            f"No arrangement was adopted and nothing changed. Seat {seat_id} names an eligibility "
            "rule this release cannot check; it can check only \"an active member of the LO\". Use "
            "that rule for now; other eligibility rules arrive with the slices that can check them."
        )
    if code is S015AuthorityRefusalCode.SBG_VERSION_DROPS_HELD_SEAT:
        seat_id = detail or "the requested seat"
        return (
            f"No arrangement was adopted and nothing changed. The new version leaves out seat {seat_id}, "
            "which someone currently holds. Keep that seat in the new version; a held seat cannot be "
            "removed by a new version in this release."
        )
    if code is S015AuthorityRefusalCode.SBG_GOVERNANCE_CIRCLE_MISSING:
        return (
            f"{act} and nothing changed. GOVERNANCE is on for this LO, so it needs its GOVERNANCE "
            "Circle first. Create the LO's GOVERNANCE Circle, then try again. Creating the Circle "
            "early in formation avoids this."
        )
    if code is S015AuthorityRefusalCode.SBG_ARRANGEMENT_MISSING:
        return (
            f"{act} and nothing changed. GOVERNANCE needs an adopted arrangement saying which seats "
            "exist, how each is filled and who decides, and this LO has none in effect. Adopt an "
            "arrangement, then try again. Adopting it before creating the Circle avoids this."
        )
    if code is S015AuthorityRefusalCode.SBG_NO_INITIAL_PARTICIPATION_ROUTE:
        timing = (
            "Take a seat before the LO is born, or adopt a version with a seat the "
            "LO Coordinators appoint."
            if act == "The LO was not born"
            else "Adopt a version with a seat the LO Coordinators appoint, then try again."
        )
        return (
            f"{act} and nothing changed. Nobody holds a GOVERNANCE seat yet, and no seat in the "
            "arrangement can be filled in this release: election seats wait for elections, co-option "
            "seats wait for an active GOVERNANCE, and ex officio seats are not yet available. "
            f"{timing} The arrangement you adopted stays recorded and in effect."
        )
    if code is S015AuthorityRefusalCode.SBG_LO_NOT_ACTIVE and organism_state in {"DORMANT", "CLOSED"}:
        remedy = (
            "Once this LO is restored, try again."
            if organism_state == "DORMANT"
            else "A closed LO is not reopened."
        )
        return (
            f"{act} and nothing changed. This LO is {organism_state.lower()}, so it can take no new "
            f"act of this kind. {remedy} Nothing about the LO's records changed."
        )
    if code is S015AuthorityRefusalCode.SBG_SEAT_ABSENT:
        seat_id = detail or "the requested seat"
        return (
            f"{act} and nothing changed. The arrangement in effect has no seat {seat_id}. "
            "Choose a seat the arrangement in effect names. Check the seats in the current version "
            "before requesting one."
        )
    if code is S015AuthorityRefusalCode.SBG_SEAT_OCCUPIED:
        seat_id = detail or "the requested seat"
        return (
            f"Nobody was seated and nothing changed. Seat {seat_id} is already held at that time, and "
            "each seat holds one person. Choose a vacant seat, or wait until this seat becomes vacant. "
            "A seat becomes vacant when its holder's seat ends."
        )
    if code is S015AuthorityRefusalCode.SBG_ALREADY_SEATED:
        return (
            "Nobody was seated and nothing changed. This person already holds a seat in this GOVERNANCE "
            "Circle, and a person holds at most one. No further action is needed for this person."
        )
    if code is S015AuthorityRefusalCode.SBG_MEMBER_NOT_ELIGIBLE:
        condition = detail or "is not an active member at that time"
        return (
            "Nobody was seated and nothing changed. A GOVERNANCE seat needs an active member of this LO "
            f"at that time, and this person {condition}. Seat someone who is an active member of this LO."
        )
    if code is S015AuthorityRefusalCode.SBG_SEAT_ROUTE_UNAVAILABLE:
        seat_id = "the requested seat"
        route = detail or "the founding route, which ends when the LO is born"
        if ":" in route:
            seat_id, route = route.split(":", 1)
        return (
            f"Nobody was seated and nothing changed. Seat {seat_id} is filled by {route}. Use a seat "
            "the LO Coordinators appoint, or wait for that route."
        )
    if code is S015AuthorityRefusalCode.SBG_SEATING_WHILE_DISABLED:
        return (
            "Nobody was seated and nothing changed. GOVERNANCE is switched off for this LO, so no one "
            "can be seated. Switch GOVERNANCE on through the LO's settings first, if the LO chooses to."
        )
    return _AUTHORED_REFUSALS.get(code)


def _controlled_integrity_refusal(
    exc: IntegrityError,
    allowed_codes: frozenset[S015AuthorityRefusalCode],
    *,
    detail: str | None,
    act: str,
) -> SBGGovernanceRefused | None:
    message_primary = getattr(
        getattr(exc.__cause__, "diag", None),
        "message_primary",
        None,
    )
    code = next(
        (candidate for candidate in allowed_codes if message_primary == candidate.value),
        None,
    )
    if code is None:
        return None
    message_detail = (
        None
        if code is S015AuthorityRefusalCode.SBG_MEMBER_NOT_ELIGIBLE
        else detail
    )
    message = _authored_refusal_message(code, message_detail, act=act)
    if message is None:
        return None
    return SBGGovernanceRefused(code, message)


def _active_governance_circle_seating_refusal(seat_id: str) -> str:
    return (
        f"Nobody was seated and nothing changed. Seat {seat_id} is filled by appointment by the LO "
        "Coordinators, which this release can use only while the LO's GOVERNANCE Circle has never "
        "been active, and this Circle has been active before. A Circle that has been active admits "
        "new participants through its own GOVERNANCE route, which arrives with a later release. "
        "The LO's Coordinators continue to govern until then."
    )


@dataclass(frozen=True, slots=True)
class AdoptGoverningInstrumentCommand:
    credential: User
    living_organism_uuid: UUID
    rule_uuid: UUID
    version: int
    body: dict[str, object]
    effective_from: datetime
    request_reference: str
    idempotency_key: str
    effective_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class CreateGovernanceCircleCommand:
    credential: User
    living_organism_uuid: UUID
    request_reference: str
    idempotency_key: str
    effective_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SeatFoundingGovernanceCircleCommand:
    credential: User
    living_organism_uuid: UUID
    circle_uuid: UUID
    seat_rule_uuid: UUID
    seat_id: str
    request_reference: str
    idempotency_key: str
    effective_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RequestGovernanceSeatCommand:
    credential: User
    living_organism_uuid: UUID
    circle_uuid: UUID
    seat_rule_uuid: UUID
    seat_id: str
    request_reference: str
    idempotency_key: str
    effective_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class DecideGovernanceSeatCommand:
    credential: User
    living_organism_uuid: UUID
    request_reference: str
    idempotency_key: str
    outcome: str
    route: str | None = None
    request_uuid: UUID | None = None
    circle_uuid: UUID | None = None
    seat_rule_uuid: UUID | None = None
    seat_id: str | None = None
    effective_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class GovernanceActResult:
    living_organism_uuid: UUID
    record_uuid: UUID
    event_uuid: UUID
    event_sequence: int
    lineage_reference: str
    authority_reference: str
    authority_decision_reference: str

    def receipt_payload(self) -> dict[str, str | int]:
        return {
            "living_organism_uuid": str(self.living_organism_uuid),
            "record_uuid": str(self.record_uuid),
            "event_uuid": str(self.event_uuid),
            "event_sequence": self.event_sequence,
            "lineage_reference": self.lineage_reference,
            "authority_reference": self.authority_reference,
            "authority_decision_reference": self.authority_decision_reference,
        }

    @classmethod
    def replay_field_audiences(cls, result: GovernanceActResult) -> dict[str, str]:
        if type(result) is not cls:
            raise TypeError("governance replay result has the wrong type")
        return {}


@dataclass(frozen=True, slots=True)
class GovernanceSeatingResult:
    living_organism_uuid: UUID
    request_uuid: UUID
    decision_uuid: UUID
    participation_uuid: UUID
    lineage_reference: str
    authority_reference: str
    authority_decision_reference: str

    def receipt_payload(self) -> dict[str, str]:
        return {
            "living_organism_uuid": str(self.living_organism_uuid),
            "request_uuid": str(self.request_uuid),
            "decision_uuid": str(self.decision_uuid),
            "participation_uuid": str(self.participation_uuid),
            "lineage_reference": self.lineage_reference,
            "authority_reference": self.authority_reference,
            "authority_decision_reference": self.authority_decision_reference,
        }

    @classmethod
    def replay_field_audiences(cls, result: GovernanceSeatingResult) -> dict[str, str]:
        if type(result) is not cls:
            raise TypeError("governance seating replay result has the wrong type")
        return {}


@dataclass(frozen=True, slots=True)
class GovernanceSeatRequestResult:
    living_organism_uuid: UUID
    request_uuid: UUID
    lineage_reference: str

    def receipt_payload(self) -> dict[str, str]:
        return {
            "living_organism_uuid": str(self.living_organism_uuid),
            "request_uuid": str(self.request_uuid),
            "lineage_reference": self.lineage_reference,
        }

    @classmethod
    def replay_field_audiences(cls, result: GovernanceSeatRequestResult) -> dict[str, str]:
        if type(result) is not cls:
            raise TypeError("governance seat request replay result has the wrong type")
        return {}


@dataclass(frozen=True, slots=True)
class GovernanceSeatDecisionResult:
    living_organism_uuid: UUID
    request_uuid: UUID
    decision_uuid: UUID
    participation_uuid: UUID | None
    lineage_reference: str
    authority_reference: str
    authority_decision_reference: str

    def receipt_payload(self) -> dict[str, str | None]:
        return {
            "living_organism_uuid": str(self.living_organism_uuid),
            "request_uuid": str(self.request_uuid),
            "decision_uuid": str(self.decision_uuid),
            "participation_uuid": (
                None if self.participation_uuid is None else str(self.participation_uuid)
            ),
            "lineage_reference": self.lineage_reference,
            "authority_reference": self.authority_reference,
            "authority_decision_reference": self.authority_decision_reference,
        }

    @classmethod
    def replay_field_audiences(cls, result: GovernanceSeatDecisionResult) -> dict[str, str]:
        if type(result) is not cls:
            raise TypeError("governance seat decision replay result has the wrong type")
        return {}


class SBGGovernanceRefused(Exception):
    def __init__(
        self,
        refusal_code: S015AuthorityRefusalCode,
        message: str,
        *,
        request_uuid: UUID | None = None,
    ) -> None:
        super().__init__(message)
        self.refusal_code = refusal_code
        self.request_uuid = request_uuid


def _compound_stage_identifiers(
    organism_uuid: UUID,
    interaction_key: str,
) -> tuple[str, str, str, str]:
    values: list[str] = []
    for action, key_prefix, reference_prefix in (
        (
            OrganismAction.REQUEST_GOVERNANCE_SEAT,
            "sbg:req:",
            "sbg:reqref:",
        ),
        (
            OrganismAction.ADMIT_CIRCLE_PARTICIPANT,
            "sbg:admit:",
            "sbg:admitref:",
        ),
    ):
        digest = hashlib.sha256(canonical_json_bytes({
            "organism_uuid": organism_uuid,
            "interaction_key": interaction_key,
            "action": action.value,
        })).hexdigest()
        values.extend((key_prefix + digest, reference_prefix + digest))
    return values[0], values[1], values[2], values[3]


class SBGGovernanceUnavailable(Exception):
    pass


class OrganismGovernanceService:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime],
        authority: S015Authority | None = None,
        database_alias: str = "default",
    ) -> None:
        if not callable(clock):
            raise TypeError("clock must be callable")
        if type(database_alias) is not str or not database_alias:
            raise ValueError("database_alias is required")
        if authority is None:
            authority = S015Authority(
                provider=ProductionApplicationAuthorityProvider(database_alias=database_alias),
                database_alias=database_alias,
            )
        if not isinstance(authority, S015Authority) or authority.database_alias != database_alias:
            raise TypeError("matching S015 authority is required")
        self._clock = clock
        self._authority = authority
        self._alias = database_alias

    def adopt_governing_instrument(
        self,
        command: AdoptGoverningInstrumentCommand,
    ) -> GovernanceActResult:
        organism_uuid = _canonical_uuid(command.living_organism_uuid, "living_organism_uuid")
        rule_uuid = _canonical_uuid(command.rule_uuid, "rule_uuid")
        if type(command.version) is not int or command.version < 1:
            raise ValueError("version must be a positive integer")
        body = _validate_body(command.body)
        effective_from = _timestamp(command.effective_from, "effective_from")
        try:
            result = self._execute(
                command=command,
                action=OrganismAction.ADOPT_GOVERNING_INSTRUMENT,
                subtype=_RULE_SUBTYPE,
                organism_uuid=organism_uuid,
                payload={
                    "rule_uuid": str(rule_uuid),
                    "version": command.version,
                    "body": body,
                    "effective_from": effective_from,
                },
            )
        except IntegrityError as exc:
            refusal = _controlled_integrity_refusal(
                exc,
                _ADOPTION_GUARD_REFUSAL_CODES,
                detail=None,
                act="No arrangement was adopted",
            )
            if refusal is None:
                raise
            raise refusal from exc
        if not isinstance(result, GovernanceActResult):
            raise RuntimeError("adoption returned a seating result")
        return result

    def create_governance_circle(
        self,
        command: CreateGovernanceCircleCommand,
    ) -> GovernanceActResult:
        organism_uuid = _canonical_uuid(command.living_organism_uuid, "living_organism_uuid")
        result = self._execute(
            command=command,
            action=OrganismAction.CREATE_DORMANT_CIRCLE,
            subtype=_CIRCLE_SUBTYPE,
            organism_uuid=organism_uuid,
            payload={"kind": "GOVERNANCE"},
        )
        if not isinstance(result, GovernanceActResult):
            raise RuntimeError("Circle creation returned a seating result")
        return result

    def seat_governance_circle(
        self,
        command: SeatFoundingGovernanceCircleCommand,
    ) -> GovernanceSeatingResult:
        organism_uuid = _canonical_uuid(command.living_organism_uuid, "living_organism_uuid")
        circle_uuid = _canonical_uuid(command.circle_uuid, "circle_uuid")
        rule_uuid = _canonical_uuid(command.seat_rule_uuid, "seat_rule_uuid")
        if type(command.seat_id) is not str or _SEAT_ID.fullmatch(command.seat_id) is None:
            raise ValueError("seat_id is invalid")
        idempotency_key = _canonical_key(
            command.idempotency_key,
            "idempotency_key",
            120,
        )
        request_key, request_reference, admission_key, admission_reference = (
            _compound_stage_identifiers(organism_uuid, idempotency_key)
        )
        request_result = self.request_governance_seat(RequestGovernanceSeatCommand(
            credential=command.credential,
            living_organism_uuid=organism_uuid,
            circle_uuid=circle_uuid,
            seat_rule_uuid=rule_uuid,
            seat_id=command.seat_id,
            request_reference=request_reference,
            idempotency_key=request_key,
            effective_at=command.effective_at,
        ))
        result = self.decide_governance_seat(DecideGovernanceSeatCommand(
            credential=command.credential,
            living_organism_uuid=organism_uuid,
            request_reference=admission_reference,
            idempotency_key=admission_key,
            outcome="ACCEPT",
            route="CIRCLE_FOUNDING",
            request_uuid=request_result.request_uuid,
            effective_at=command.effective_at,
        ))
        if result.participation_uuid is None:
            raise RuntimeError("founding acceptance returned no participation")
        return GovernanceSeatingResult(
            living_organism_uuid=result.living_organism_uuid,
            request_uuid=result.request_uuid,
            decision_uuid=result.decision_uuid,
            participation_uuid=result.participation_uuid,
            lineage_reference=result.lineage_reference,
            authority_reference=result.authority_reference,
            authority_decision_reference=result.authority_decision_reference,
        )

    def request_governance_seat(
        self,
        command: RequestGovernanceSeatCommand,
    ) -> GovernanceSeatRequestResult:
        try:
            return self._request_governance_seat(command)
        except IntegrityError as exc:
            refusal = _controlled_integrity_refusal(
                exc,
                _REQUEST_GUARD_REFUSAL_CODES,
                detail=command.seat_id,
                act=f"Your own request for seat {command.seat_id} was not recorded",
            )
            if refusal is None:
                raise
            raise refusal from exc

    def _request_governance_seat(
        self,
        command: RequestGovernanceSeatCommand,
    ) -> GovernanceSeatRequestResult:
        organism_uuid = _canonical_uuid(command.living_organism_uuid, "living_organism_uuid")
        circle_uuid = _canonical_uuid(command.circle_uuid, "circle_uuid")
        rule_uuid = _canonical_uuid(command.seat_rule_uuid, "seat_rule_uuid")
        if type(command.seat_id) is not str or _SEAT_ID.fullmatch(command.seat_id) is None:
            raise ValueError("seat_id is invalid")
        request_reference = _canonical_key(command.request_reference, "request_reference", 128)
        idempotency_key = _canonical_key(command.idempotency_key, "idempotency_key", 120)
        received_at = _timestamp(self._clock(), "received_at")
        effective_at = (
            received_at if command.effective_at is None
            else _timestamp(command.effective_at, "effective_at")
        )
        if effective_at > received_at:
            raise ValueError("effective_at must not be later than received_at")
        action = OrganismAction.REQUEST_GOVERNANCE_SEAT
        request_uuid = uuid5(organism_uuid, f"{action.value}:{idempotency_key}")
        payload = {
            "circle_uuid": str(circle_uuid),
            "seat_rule_uuid": str(rule_uuid),
            "seat_id": command.seat_id,
        }
        fingerprint = hashlib.sha256(canonical_json_bytes({
            "action": action.value,
            "effective_at": effective_at,
            "idempotency_key": idempotency_key,
            "organism_uuid": organism_uuid,
            "payload": payload,
            "request_reference": request_reference,
            "schema": "sbg-governance-seat-request-v1",
        })).hexdigest()
        with authority_atomic(self._alias):
            actor = _lock_actor(
                database_alias=self._alias,
                credential=command.credential,
                unavailable=SBGGovernanceUnavailable,
            )
            acquire_organism_command_mutex(
                database_alias=self._alias,
                actor_identity_id=actor.identity_id,
                action=action,
                idempotency_key=idempotency_key,
            )
            receipt = (
                OrganismCommandReceipt.objects.using(self._alias)
                .select_for_update()
                .filter(actor=actor, action=action.value, idempotency_key=idempotency_key)
                .first()
            )
            if receipt is not None:
                if (
                    receipt.request_reference != request_reference
                    or receipt.payload_fingerprint != fingerprint
                ):
                    raise ValueError("completed receipt does not match request")
                result = GovernanceSeatRequestResult(
                    living_organism_uuid=UUID(receipt.result_payload["living_organism_uuid"]),
                    request_uuid=UUID(receipt.result_payload["request_uuid"]),
                    lineage_reference=receipt.result_payload["lineage_reference"],
                )
                return serve_replay(
                    result,
                    viewer=ReplayViewer(actor.pk, actor.identity_id, self._clock()),
                    result_type=ReplayResultType(GovernanceSeatRequestResult, self._alias),
                )
            _lock_registered_organism(
                database_alias=self._alias,
                organism_id=organism_uuid,
            )
            organism = (
                LivingOrganism.objects.using(self._alias)
                .select_for_update()
                .filter(organism_id=organism_uuid)
                .first()
            )
            if organism is None:
                raise LookupError("living organism not found")
            try:
                membership, role_refs = _lock_actor_membership_context(
                    database_alias=self._alias,
                    actor_identity_id=actor.identity_id,
                    living_organism_id=organism_uuid,
                    unavailable=SBGGovernanceUnavailable,
                )
            except SBGGovernanceUnavailable as exc:
                if str(exc) != "actor active membership is required":
                    raise
                self._refuse(
                    S015AuthorityRefusalCode.SBG_MEMBER_NOT_ELIGIBLE,
                    "is not an active member at that time",
                )
            circle, rule = self._validate_constitutional_seating(
                organism=organism,
                membership=membership,
                payload=payload,
                effective_at=effective_at,
                received_at=received_at,
                require_enabled=False,
                check_occupancy=False,
                require_birthing=False,
                require_supported_route=False,
                act=f"Your own request for seat {command.seat_id} was not recorded",
            )
            target = _authority_target(
                action,
                organism_uuid,
                {
                    **payload,
                    "applicant_identity_uuid": str(actor.identity_id),
                },
            )
            request = _authority_request(
                database_alias=self._alias,
                actor=actor,
                actor_membership=membership,
                actor_role_head_refs=role_refs,
                action=action,
                command_subtype=action.value,
                request_reference=request_reference,
                idempotency_key=idempotency_key,
                received_at=received_at,
                target_fingerprint=authority_target_fingerprint(target),
                effective_at=effective_at,
                target=target,
                living_organism_id=organism_uuid,
            )
            try:
                decision = self._authority.qualify(request)
            except S015AuthorityDenied as exc:
                message = _authored_refusal_message(
                    exc.refusal_code,
                    None,
                    act=f"Your own request for seat {command.seat_id} was not recorded",
                )
                if message is None:
                    raise
                raise S015AuthorityDenied(
                    message,
                    refusal_code=exc.refusal_code,
                    disclosed_refusal_code=exc.disclosed_refusal_code,
                    authority_decision_reference=exc.authority_decision_reference,
                ) from exc
            join_request = CircleJoinRequest.objects.using(self._alias).create(
                request_uuid=request_uuid,
                circle=circle,
                membership=membership,
                seat_rule_uuid=rule.rule_uuid,
                seat_id=command.seat_id,
                initiated_by="APPLICANT",
                consent_reference=request_reference,
                enterer_identity=actor,
                occurred_at=effective_at,
                effective_at=effective_at,
                received_at=received_at,
            )
            lineage_reference = _lineage(
                action.value,
                join_request.request_uuid,
                join_request.request_uuid,
                fingerprint,
            )
            result = GovernanceSeatRequestResult(
                living_organism_uuid=organism.organism_id,
                request_uuid=join_request.request_uuid,
                lineage_reference=lineage_reference,
            )
            OrganismCommandReceipt.objects.using(self._alias).create(
                actor=actor,
                action=action.value,
                idempotency_key=idempotency_key,
                request_reference=request_reference,
                payload_fingerprint=fingerprint,
                authority_decision_reference=decision.authority_decision_reference,
                lineage_reference=lineage_reference,
                result_payload=result.receipt_payload(),
            )
            return result

    def _request_open_sentence(self, seat_id: str) -> str:
        return f"Your own request for seat {seat_id} is recorded and remains open."

    def _request_open_context(self, request_uuid: UUID) -> tuple[str, str] | None:
        seat_id = (
            CircleJoinRequest.objects.using(self._alias)
            .filter(request_uuid=request_uuid)
            .values_list("seat_id", flat=True)
            .first()
        )
        if seat_id is None:
            return None
        return seat_id, self._request_open_sentence(seat_id)

    def decide_governance_seat(
        self,
        command: DecideGovernanceSeatCommand,
    ) -> GovernanceSeatDecisionResult:
        if command.request_uuid is None:
            return self._decide_governance_seat(command)

        request_uuid = _canonical_uuid(command.request_uuid, "request_uuid")
        try:
            return self._decide_governance_seat(command)
        except IntegrityError as exc:
            request_context = self._request_open_context(request_uuid)
            if request_context is None:
                raise
            seat_id, open_request = request_context
            refusal = _controlled_integrity_refusal(
                exc,
                _ADMISSION_GUARD_REFUSAL_CODES,
                detail=seat_id,
                act="Nobody was seated",
            )
            if refusal is None:
                raise
            raise SBGGovernanceRefused(
                refusal.refusal_code,
                f"{refusal} {open_request}",
                request_uuid=request_uuid,
            ) from exc
        except SBGGovernanceRefused as exc:
            request_context = self._request_open_context(request_uuid)
            if request_context is None:
                raise
            _, open_request = request_context
            raise SBGGovernanceRefused(
                exc.refusal_code,
                f"{exc} {open_request}",
                request_uuid=request_uuid,
            ) from exc
        except S015AuthorityDenied as exc:
            request_context = self._request_open_context(request_uuid)
            if request_context is None:
                raise
            _, open_request = request_context
            raise S015AuthorityDenied(
                f"{exc} {open_request}",
                refusal_code=exc.refusal_code,
                disclosed_refusal_code=exc.disclosed_refusal_code,
                authority_decision_reference=exc.authority_decision_reference,
                request_uuid=request_uuid,
            ) from exc

    def _decide_governance_seat(
        self,
        command: DecideGovernanceSeatCommand,
    ) -> GovernanceSeatDecisionResult:
        if command.request_uuid is None:
            if (
                command.circle_uuid is None
                or command.seat_rule_uuid is None
                or type(command.seat_id) is not str
                or _SEAT_ID.fullmatch(command.seat_id) is None
            ):
                raise ValueError("self-appointment requires a circle and seat")
            organism_uuid = _canonical_uuid(
                command.living_organism_uuid,
                "living_organism_uuid",
            )
            interaction_key = _canonical_key(
                command.idempotency_key,
                "idempotency_key",
                120,
            )
            request_key, request_reference, admission_key, admission_reference = (
                _compound_stage_identifiers(organism_uuid, interaction_key)
            )
            request_result = self.request_governance_seat(RequestGovernanceSeatCommand(
                credential=command.credential,
                living_organism_uuid=organism_uuid,
                circle_uuid=_canonical_uuid(command.circle_uuid, "circle_uuid"),
                seat_rule_uuid=_canonical_uuid(command.seat_rule_uuid, "seat_rule_uuid"),
                seat_id=command.seat_id,
                request_reference=request_reference,
                idempotency_key=request_key,
                effective_at=command.effective_at,
            ))
            try:
                return self._decide_governance_seat(DecideGovernanceSeatCommand(
                    credential=command.credential,
                    living_organism_uuid=organism_uuid,
                    request_reference=admission_reference,
                    idempotency_key=admission_key,
                    outcome=command.outcome,
                    route="GOVERNANCE_CONSTITUTIONAL",
                    request_uuid=request_result.request_uuid,
                    effective_at=command.effective_at,
                ))
            except SBGGovernanceRefused as exc:
                raise SBGGovernanceRefused(
                    exc.refusal_code,
                    f"{exc} {self._request_open_sentence(command.seat_id)}",
                    request_uuid=request_result.request_uuid,
                ) from exc
            except S015AuthorityDenied as exc:
                raise S015AuthorityDenied(
                    f"{exc} {self._request_open_sentence(command.seat_id)}",
                    refusal_code=exc.refusal_code,
                    disclosed_refusal_code=exc.disclosed_refusal_code,
                    authority_decision_reference=exc.authority_decision_reference,
                    request_uuid=request_result.request_uuid,
                ) from exc
            except IntegrityError as exc:
                refusal = _controlled_integrity_refusal(
                    exc,
                    _ADMISSION_GUARD_REFUSAL_CODES,
                    detail=command.seat_id,
                    act="Nobody was seated",
                )
                if refusal is None:
                    raise
                raise SBGGovernanceRefused(
                    refusal.refusal_code,
                    f"{refusal} {self._request_open_sentence(command.seat_id)}",
                    request_uuid=request_result.request_uuid,
                ) from exc

        organism_uuid = _canonical_uuid(command.living_organism_uuid, "living_organism_uuid")
        request_reference = _canonical_key(command.request_reference, "request_reference", 128)
        idempotency_key = _canonical_key(command.idempotency_key, "idempotency_key", 120)
        if type(command.outcome) is not str or command.outcome not in {"ACCEPT", "DECLINE"}:
            raise ValueError("outcome must be ACCEPT or DECLINE")
        if command.route not in {None, "CIRCLE_FOUNDING", "GOVERNANCE_CONSTITUTIONAL"}:
            raise ValueError("route is invalid")
        request_uuid = _canonical_uuid(command.request_uuid, "request_uuid")
        if (
            command.circle_uuid is not None
            or command.seat_rule_uuid is not None
            or command.seat_id is not None
        ):
            raise ValueError("an existing request supplies its own circle and seat")
        received_at = _timestamp(self._clock(), "received_at")
        effective_at = (
            received_at if command.effective_at is None
            else _timestamp(command.effective_at, "effective_at")
        )
        if effective_at > received_at:
            raise ValueError("effective_at must not be later than received_at")
        action = OrganismAction.ADMIT_CIRCLE_PARTICIPANT
        fingerprint = hashlib.sha256(canonical_json_bytes({
            "action": action.value,
            "effective_at": effective_at,
            "idempotency_key": idempotency_key,
            "organism_uuid": organism_uuid,
            "outcome": command.outcome,
            "route": command.route,
            "request_uuid": request_uuid,
            "schema": "sbg-governance-seat-decision-v1",
        })).hexdigest()
        with authority_atomic(self._alias):
            actor = _lock_actor(
                database_alias=self._alias,
                credential=command.credential,
                unavailable=SBGGovernanceUnavailable,
            )
            acquire_organism_command_mutex(
                database_alias=self._alias,
                actor_identity_id=actor.identity_id,
                action=action,
                idempotency_key=idempotency_key,
            )
            receipt = (
                OrganismCommandReceipt.objects.using(self._alias)
                .select_for_update()
                .filter(actor=actor, action=action.value, idempotency_key=idempotency_key)
                .first()
            )
            if receipt is not None:
                if (
                    receipt.request_reference != request_reference
                    or receipt.payload_fingerprint != fingerprint
                ):
                    raise ValueError("completed receipt does not match request")
                payload = receipt.result_payload
                result = GovernanceSeatDecisionResult(
                    living_organism_uuid=UUID(payload["living_organism_uuid"]),
                    request_uuid=UUID(payload["request_uuid"]),
                    decision_uuid=UUID(payload["decision_uuid"]),
                    participation_uuid=(
                        None if payload["participation_uuid"] is None
                        else UUID(payload["participation_uuid"])
                    ),
                    lineage_reference=payload["lineage_reference"],
                    authority_reference=payload["authority_reference"],
                    authority_decision_reference=payload["authority_decision_reference"],
                )
                return serve_replay(
                    result,
                    viewer=ReplayViewer(actor.pk, actor.identity_id, self._clock()),
                    result_type=ReplayResultType(GovernanceSeatDecisionResult, self._alias),
                )
            _lock_registered_organism(
                database_alias=self._alias,
                organism_id=organism_uuid,
            )
            organism = (
                LivingOrganism.objects.using(self._alias)
                .select_for_update()
                .filter(organism_id=organism_uuid)
                .first()
            )
            if organism is None:
                raise LookupError("living organism not found")
            actor_membership, role_refs = _lock_actor_membership_context(
                database_alias=self._alias,
                actor_identity_id=actor.identity_id,
                living_organism_id=organism_uuid,
                unavailable=SBGGovernanceUnavailable,
            )
            join_request = (
                CircleJoinRequest.objects.using(self._alias)
                .select_for_update(of=("self",))
                .select_related("circle", "membership__identity")
                .filter(request_uuid=request_uuid)
                .first()
            )
            if (
                join_request is None
                or join_request.circle.parent_organism_id != organism.pk
                or join_request.initiated_by != "APPLICANT"
                or join_request.effective_at > effective_at
            ):
                self._refuse(
                    S015AuthorityRefusalCode.SBG_MEMBER_NOT_ELIGIBLE,
                    "is not an active member at that time",
                    act="Nobody was seated",
                )
            applicant_membership = join_request.membership
            payload = {
                "circle_uuid": str(join_request.circle.circle_uuid),
                "seat_rule_uuid": str(join_request.seat_rule_uuid),
                "seat_id": join_request.seat_id,
            }
            has_birthing = LivingOrganismEvent.objects.using(self._alias).filter(
                living_organism=organism,
                action="BIRTHING",
                effective_at__lte=effective_at,
                received_at__lte=received_at,
                recorded_at__lte=received_at,
            ).exists()
            route = command.route or (
                "GOVERNANCE_CONSTITUTIONAL"
                if has_birthing
                else "CIRCLE_FOUNDING"
            )
            seating_context = None
            if route == "CIRCLE_FOUNDING" and not has_birthing:
                if applicant_membership.identity_id != actor.pk:
                    self._refuse(
                        S015AuthorityRefusalCode.DENIED,
                        None,
                        act="Nobody was seated",
                    )
                seating_context = self._validate_seating(
                    organism=organism,
                    actor=actor,
                    membership=actor_membership,
                    payload=payload,
                    effective_at=effective_at,
                    received_at=received_at,
                )
            elif route == "GOVERNANCE_CONSTITUTIONAL" and has_birthing:
                seating_context = self._validate_constitutional_seating(
                    organism=organism,
                    membership=applicant_membership,
                    payload=payload,
                    effective_at=effective_at,
                    received_at=received_at,
                    require_enabled=True,
                    check_occupancy=command.outcome == "ACCEPT",
                )
            target = _authority_target(
                action,
                organism_uuid,
                {
                    **payload,
                    "kind": "GOVERNANCE",
                    "route": route,
                    "request_uuid": str(request_uuid),
                    "applicant_identity_uuid": _governance_applicant_identity_uuid(
                        applicant_membership,
                    ),
                    **(
                        {
                            "constitutional_route": "APPOINTMENT",
                            "deciding_party": "office:LO_COORDINATOR",
                            "outcome": command.outcome,
                        }
                        if route == "GOVERNANCE_CONSTITUTIONAL"
                        else {}
                    ),
                },
            )
            request = _authority_request(
                database_alias=self._alias,
                actor=actor,
                actor_membership=actor_membership,
                actor_role_head_refs=role_refs,
                action=action,
                command_subtype=action.value,
                request_reference=request_reference,
                idempotency_key=idempotency_key,
                received_at=received_at,
                target_fingerprint=authority_target_fingerprint(target),
                effective_at=effective_at,
                target=target,
                living_organism_id=organism_uuid,
            )
            try:
                decision = self._authority.qualify(request)
            except S015AuthorityDenied as exc:
                if exc.refusal_code is not S015AuthorityRefusalCode.SBG_SEAT_ROUTE_UNAVAILABLE:
                    raise
                refusal_message = (
                    _authored_refusal_message(
                        exc.refusal_code,
                        f"{payload['seat_id']}:the founding route, which ends when the LO is born",
                        act="Nobody was seated",
                    )
                    if route == "CIRCLE_FOUNDING"
                    else _active_governance_circle_seating_refusal(payload["seat_id"])
                )
                raise S015AuthorityDenied(
                    refusal_message or str(exc),
                    refusal_code=exc.refusal_code,
                    disclosed_refusal_code=exc.disclosed_refusal_code,
                    authority_decision_reference=exc.authority_decision_reference,
                ) from exc
            if seating_context is None:
                raise RuntimeError("the selected governance seating route was not validated")
            circle, rule = seating_context
            self_decision = _governance_self_decision(applicant_membership, actor)
            admission = CircleAdmissionDecision.objects.using(self._alias).create(
                request=join_request,
                route=route,
                constitutional_route=(
                    "APPOINTMENT"
                    if route == "GOVERNANCE_CONSTITUTIONAL"
                    else "NOT_APPLICABLE"
                ),
                deciding_party=(
                    "office:LO_COORDINATOR"
                    if route == "GOVERNANCE_CONSTITUTIONAL"
                    else "FOUNDING_BODY"
                ),
                deciding_identity=actor,
                self_decision=self_decision,
                outcome=command.outcome,
                authority_decision_reference=decision.authority_decision_reference,
                settings_basis=f"rule:{rule.rule_uuid}@{rule.version}#{payload['seat_id']}",
                seat_rule_uuid=rule.rule_uuid,
                seat_id=payload["seat_id"],
                actor_state="LIVING_ORGANISM",
                actor=None,
                actor_access_epoch=None,
                actor_capacity="STANDING",
                entry_mode="TRANSPORT",
                enterer_identity=actor,
                composing_rule_state="NA_TRANSPORT",
                composing_rule_reference=None,
                composer_state="NA_TRANSPORT",
                composer_identity=None,
                occurred_at=effective_at,
                effective_at=effective_at,
                received_at=received_at,
            )
            participation = None
            if command.outcome == "ACCEPT":
                participation = CircleParticipation.objects.using(self._alias).create(
                    circle=circle,
                    membership=applicant_membership,
                    seat_rule_uuid=rule.rule_uuid,
                    seat_id=payload["seat_id"],
                    decision=admission,
                    actor_state="LIVING_ORGANISM",
                    actor=None,
                    actor_access_epoch=None,
                    actor_capacity="STANDING",
                    entry_mode="TRANSPORT",
                    enterer_identity=actor,
                    composing_rule_state="NA_TRANSPORT",
                    composing_rule_reference=None,
                    composer_state="NA_TRANSPORT",
                    composer_identity=None,
                    occurred_at=effective_at,
                    effective_at=effective_at,
                    received_at=received_at,
                )
            lineage_reference = _lineage(
                action.value,
                admission.decision_uuid,
                (
                    join_request.request_uuid
                    if participation is None
                    else participation.participation_uuid
                ),
                fingerprint,
            )
            result = GovernanceSeatDecisionResult(
                living_organism_uuid=organism.organism_id,
                request_uuid=join_request.request_uuid,
                decision_uuid=admission.decision_uuid,
                participation_uuid=(
                    None if participation is None else participation.participation_uuid
                ),
                lineage_reference=lineage_reference,
                authority_reference=decision.authority_reference,
                authority_decision_reference=decision.authority_decision_reference,
            )
            OrganismCommandReceipt.objects.using(self._alias).create(
                actor=actor,
                action=action.value,
                idempotency_key=idempotency_key,
                request_reference=request_reference,
                payload_fingerprint=fingerprint,
                authority_decision_reference=decision.authority_decision_reference,
                lineage_reference=lineage_reference,
                result_payload=result.receipt_payload(),
            )
            return result

    def _execute(
        self,
        *,
        command: (
            AdoptGoverningInstrumentCommand
            | CreateGovernanceCircleCommand
        ),
        action: OrganismAction,
        subtype: str,
        organism_uuid: UUID,
        payload: dict[str, object],
    ) -> GovernanceActResult:
        request_reference = _canonical_key(command.request_reference, "request_reference", 128)
        idempotency_key = _canonical_key(command.idempotency_key, "idempotency_key", 120)
        received_at = _timestamp(self._clock(), "received_at")
        effective_at = (
            received_at if command.effective_at is None
            else _timestamp(command.effective_at, "effective_at")
        )
        if effective_at > received_at:
            raise ValueError("effective_at must not be later than received_at")
        if action is OrganismAction.CREATE_DORMANT_CIRCLE:
            payload["circle_uuid"] = str(
                uuid5(organism_uuid, f"{action.value}:{idempotency_key}")
            )
        fingerprint = hashlib.sha256(canonical_json_bytes({
            "action": action.value,
            "effective_at": effective_at,
            "idempotency_key": idempotency_key,
            "organism_uuid": organism_uuid,
            "payload": payload,
            "request_reference": request_reference,
            "schema": "sbg-governance-act-v1",
        })).hexdigest()
        with authority_atomic(self._alias):
            actor = _lock_actor(
                database_alias=self._alias,
                credential=command.credential,
                unavailable=SBGGovernanceUnavailable,
            )
            acquire_organism_command_mutex(
                database_alias=self._alias,
                actor_identity_id=actor.identity_id,
                action=action,
                idempotency_key=idempotency_key,
            )
            replay = self._replay(
                actor=actor,
                action=action,
                request_reference=request_reference,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
            )
            if replay is not None:
                return replay
            _lock_registered_organism(
                database_alias=self._alias,
                organism_id=organism_uuid,
            )
            organism = (
                LivingOrganism.objects.using(self._alias)
                .select_for_update()
                .filter(organism_id=organism_uuid)
                .first()
            )
            if organism is None:
                raise LookupError("living organism not found")
            actor_membership, role_refs = _lock_actor_membership_context(
                database_alias=self._alias,
                actor_identity_id=actor.identity_id,
                living_organism_id=organism_uuid,
                unavailable=SBGGovernanceUnavailable,
            )
            target = _authority_target(action, organism_uuid, payload)
            if action is OrganismAction.ADOPT_GOVERNING_INSTRUMENT:
                source = self._source_at(organism, effective_at, received_at)
                target["source"] = source
            request = _authority_request(
                database_alias=self._alias,
                actor=actor,
                actor_membership=actor_membership,
                actor_role_head_refs=role_refs,
                action=action,
                command_subtype=subtype,
                request_reference=request_reference,
                idempotency_key=idempotency_key,
                received_at=received_at,
                target_fingerprint=authority_target_fingerprint(target),
                effective_at=effective_at,
                target=target,
                living_organism_id=organism_uuid,
            )
            try:
                decision = self._authority.qualify(request)
            except S015AuthorityDenied as exc:
                act = (
                    "No arrangement was adopted"
                    if action is OrganismAction.ADOPT_GOVERNING_INSTRUMENT
                    else "No Circle was created"
                )
                organism_state = (
                    self._organism_state(organism, effective_at, received_at)
                    if exc.refusal_code is S015AuthorityRefusalCode.SBG_LO_NOT_ACTIVE
                    else None
                )
                message = _authored_refusal_message(
                    exc.refusal_code,
                    None,
                    act=act,
                    organism_state=organism_state,
                )
                if message is None:
                    raise
                raise S015AuthorityDenied(
                    message,
                    refusal_code=exc.refusal_code,
                    disclosed_refusal_code=exc.disclosed_refusal_code,
                    authority_decision_reference=exc.authority_decision_reference,
                ) from exc
            if action is OrganismAction.ADOPT_GOVERNING_INSTRUMENT:
                self._validate_adoption(
                    organism=organism,
                    actor=actor,
                    payload=payload,
                    effective_at=effective_at,
                    received_at=received_at,
                )
                result = self._write_adoption(
                    organism=organism,
                    actor=actor,
                    payload=payload,
                    decision=decision,
                    fingerprint=fingerprint,
                    request_reference=request_reference,
                    idempotency_key=idempotency_key,
                    effective_at=effective_at,
                    received_at=received_at,
                )
            elif action is OrganismAction.CREATE_DORMANT_CIRCLE:
                result = self._write_circle(
                    organism=organism,
                    actor=actor,
                    circle_uuid=UUID(payload["circle_uuid"]),
                    decision=decision,
                    fingerprint=fingerprint,
                    request_reference=request_reference,
                    idempotency_key=idempotency_key,
                    effective_at=effective_at,
                    received_at=received_at,
                )
            else:
                raise ValueError(f"unsupported governance act: {action.value}")
            OrganismCommandReceipt.objects.using(self._alias).create(
                actor=actor,
                action=action.value,
                idempotency_key=idempotency_key,
                request_reference=request_reference,
                payload_fingerprint=fingerprint,
                authority_decision_reference=decision.authority_decision_reference,
                lineage_reference=result.lineage_reference,
                result_payload=result.receipt_payload(),
            )
            return result

    def _replay(
        self,
        *,
        actor: Identity,
        action: OrganismAction,
        request_reference: str,
        idempotency_key: str,
        fingerprint: str,
    ) -> GovernanceActResult | None:
        receipt = (
            OrganismCommandReceipt.objects.using(self._alias)
            .select_for_update()
            .filter(actor=actor, action=action.value, idempotency_key=idempotency_key)
            .first()
        )
        if receipt is None:
            return None
        if receipt.request_reference != request_reference or receipt.payload_fingerprint != fingerprint:
            raise ValueError("completed receipt does not match request")
        payload = receipt.result_payload
        try:
            result = GovernanceActResult(
                living_organism_uuid=UUID(payload["living_organism_uuid"]),
                record_uuid=UUID(payload["record_uuid"]),
                event_uuid=UUID(payload["event_uuid"]),
                event_sequence=payload["event_sequence"],
                lineage_reference=payload["lineage_reference"],
                authority_reference=payload["authority_reference"],
                authority_decision_reference=payload["authority_decision_reference"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("completed receipt does not match request") from exc
        replay_result = serve_replay(
            result,
            viewer=ReplayViewer(actor.pk, actor.identity_id, self._clock()),
            result_type=ReplayResultType(type(result), self._alias),
        )
        return replay_result

    def _source_at(
        self,
        organism: LivingOrganism,
        effective_at: datetime,
        recorded_at: datetime,
    ) -> str:
        born = LivingOrganismEvent.objects.using(self._alias).filter(
            living_organism=organism,
            action="BIRTHING",
            effective_at__lte=effective_at,
            recorded_at__lte=recorded_at,
        ).exists()
        return "LO_PRESCRIPTION" if born else "FOUNDING_BODY_PRESCRIPTION"

    def _organism_state(
        self,
        organism: LivingOrganism,
        effective_at: datetime,
        recorded_at: datetime,
    ) -> str | None:
        with connections[self._alias].cursor() as cursor:
            cursor.execute(
                "SELECT public.s015_fold_effective_state("
                "'core_livingorganism', %s, %s, %s)",
                [organism.pk, effective_at, recorded_at],
            )
            row = cursor.fetchone()
        if row is None:
            raise RuntimeError("living-organism state fold returned no row")
        return row[0]

    def _validate_seating(
        self,
        *,
        organism: LivingOrganism,
        actor: Identity,
        membership: OrganismMembership,
        payload: dict[str, object],
        effective_at: datetime,
        received_at: datetime,
    ) -> tuple[Circle, GovernedRule]:
        act = "Nobody was seated"
        if membership is None:
            self._refuse(
                S015AuthorityRefusalCode.SBG_MEMBER_NOT_ELIGIBLE,
                "is not an active member at that time",
                act=act,
            )
        organism_state = self._organism_state(organism, effective_at, received_at)
        if organism_state in {"DORMANT", "CLOSED"}:
            self._refuse(
                S015AuthorityRefusalCode.SBG_LO_NOT_ACTIVE,
                organism_state,
                act=act,
                organism_state=organism_state,
            )
        if organism_state is None:
            raise SBGGovernanceUnavailable("living-organism state is not established")
        birthing_recorded = LivingOrganismEvent.objects.using(self._alias).filter(
            living_organism=organism,
            action="BIRTHING",
            effective_at__lte=effective_at,
            received_at__lte=received_at,
            recorded_at__lte=received_at,
        ).exists()
        if not birthing_recorded:
            configuration = _effective_version(
                self._alias,
                organism,
                OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
                effective_at,
                received_at,
            )
            if configuration is None or configuration.governance_enabled is not True:
                self._refuse(
                    S015AuthorityRefusalCode.SBG_SEATING_WHILE_DISABLED,
                    None,
                    act=act,
                )
        circle = (
            Circle.objects.using(self._alias)
            .select_for_update()
            .filter(parent_organism=organism, kind="GOVERNANCE")
            .first()
        )
        if circle is None or str(circle.circle_uuid) != payload["circle_uuid"]:
            self._refuse(
                S015AuthorityRefusalCode.SBG_GOVERNANCE_CIRCLE_MISSING,
                None,
                act=act,
            )
        if not CircleStateEvent.objects.using(self._alias).filter(
            circle=circle,
            action="CREATE_DORMANT",
            effective_at__lte=effective_at,
            recorded_at__lte=received_at,
        ).exists():
            self._refuse(
                S015AuthorityRefusalCode.SBG_GOVERNANCE_CIRCLE_MISSING,
                None,
                act=act,
            )

        if membership.living_organism_id != organism.pk:
            self._refuse(
                S015AuthorityRefusalCode.SBG_MEMBER_NOT_ELIGIBLE,
                "is a member of another LO",
                act=act,
            )
        with connections[self._alias].cursor() as cursor:
            cursor.execute(
                "SELECT public.s015_fold_effective_state("
                "'core_organismmembership', %s, %s, %s)",
                [membership.pk, effective_at, received_at],
            )
            row = cursor.fetchone()
        if row is None:
            raise RuntimeError("membership state fold returned no row")
        if row[0] != "ACTIVE":
            self._refuse(
                S015AuthorityRefusalCode.SBG_MEMBER_NOT_ELIGIBLE,
                "is not an active member at that time",
                act=act,
            )

        arrangement_versions = GovernedRule.objects.using(self._alias).filter(
            scope_kind="LIVING_ORGANISM",
            scope_organism=organism,
            code="GOVERNANCE_CONSTITUTION",
            recorded_at__lte=received_at,
        )
        if not arrangement_versions.exists():
            self._refuse(
                S015AuthorityRefusalCode.SBG_ARRANGEMENT_MISSING,
                None,
                act=act,
            )
        rule = arrangement_versions.filter(
            rule_uuid=payload["seat_rule_uuid"],
            effective_from__lte=effective_at,
        ).order_by("-effective_from", "-recorded_at", "-version").first()
        seat_id = payload["seat_id"]
        if rule is None:
            self._refuse(S015AuthorityRefusalCode.SBG_SEAT_ABSENT, seat_id, act=act)
        body = rule.body
        seats = body.get("seats") if type(body) is dict else None
        if type(seats) is not list:
            self._refuse(
                S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
                "the arrangement does not contain a valid seat list",
                act=act,
            )
        seat = next(
            (
                item for item in seats
                if type(item) is dict and item.get("seat_id") == seat_id
            ),
            None,
        )
        if seat is None:
            self._refuse(S015AuthorityRefusalCode.SBG_SEAT_ABSENT, seat_id, act=act)
        try:
            seat_effective_from = datetime.fromisoformat(
                seat["effective_from"].replace("Z", "+00:00")
            )
        except (KeyError, TypeError, ValueError, AttributeError):
            self._refuse(
                S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
                f"seat {seat_id} has no valid start date",
                act=act,
            )
        if (
            seat_effective_from.tzinfo is None
            or seat_effective_from.utcoffset() is None
            or seat_effective_from > effective_at
        ):
            self._refuse(S015AuthorityRefusalCode.SBG_SEAT_ABSENT, seat_id, act=act)

        for future_rule in arrangement_versions.filter(
            rule_uuid=rule.rule_uuid,
            effective_from__gt=effective_at,
        ):
            future_body = future_rule.body
            future_seats = future_body.get("seats") if type(future_body) is dict else None
            if type(future_seats) is not list or not any(
                type(item) is dict and item.get("seat_id") == seat_id
                for item in future_seats
            ):
                self._refuse(S015AuthorityRefusalCode.SBG_SEAT_ABSENT, seat_id, act=act)

        for participation in CircleParticipation.objects.using(self._alias).filter(
            circle=circle,
            seat_rule_uuid=rule.rule_uuid,
            seat_id=seat_id,
            recorded_at__lte=received_at,
        ):
            ending = CircleParticipationEnd.objects.using(self._alias).filter(
                participation=participation,
                recorded_at__lte=received_at,
            ).first()
            if ending is None or ending.effective_at > effective_at:
                self._refuse(S015AuthorityRefusalCode.SBG_SEAT_OCCUPIED, seat_id, act=act)

        for participation in CircleParticipation.objects.using(self._alias).filter(
            circle=circle,
            membership__identity_id=membership.identity_id,
            recorded_at__lte=received_at,
        ):
            ending = CircleParticipationEnd.objects.using(self._alias).filter(
                participation=participation,
                recorded_at__lte=received_at,
            ).first()
            if ending is None or ending.effective_at > effective_at:
                self._refuse(S015AuthorityRefusalCode.SBG_ALREADY_SEATED, None, act=act)
        return circle, rule

    def _validate_constitutional_seating(
        self,
        *,
        organism: LivingOrganism,
        membership: OrganismMembership,
        payload: dict[str, object],
        effective_at: datetime,
        received_at: datetime,
        require_enabled: bool,
        check_occupancy: bool,
        require_birthing: bool = True,
        require_supported_route: bool = True,
        act: str = "Nobody was seated",
    ) -> tuple[Circle, GovernedRule]:
        if membership is None:
            self._refuse(
                S015AuthorityRefusalCode.SBG_MEMBER_NOT_ELIGIBLE,
                "is not an active member at that time",
                act=act,
            )
        organism_state = self._organism_state(organism, effective_at, received_at)
        if organism_state in {"DORMANT", "CLOSED"}:
            self._refuse(
                S015AuthorityRefusalCode.SBG_LO_NOT_ACTIVE,
                organism_state,
                act=act,
                organism_state=organism_state,
            )
        if organism_state is None:
            raise SBGGovernanceUnavailable("living-organism state is not established")
        if require_birthing and not LivingOrganismEvent.objects.using(self._alias).filter(
            living_organism=organism,
            action="BIRTHING",
            effective_at__lte=effective_at,
            received_at__lte=received_at,
            recorded_at__lte=received_at,
        ).exists():
            self._refuse(
                S015AuthorityRefusalCode.SBG_SEAT_ROUTE_UNAVAILABLE,
                f"{payload['seat_id']}:the founding route, which ends when the LO is born",
                act=act,
            )
        if require_enabled:
            configuration = _effective_version(
                self._alias,
                organism,
                OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
                effective_at,
                received_at,
            )
            if configuration is None or configuration.governance_enabled is not True:
                self._refuse(
                    S015AuthorityRefusalCode.SBG_SEATING_WHILE_DISABLED,
                    None,
                    act=act,
                )
        circle = (
            Circle.objects.using(self._alias)
            .select_for_update()
            .filter(parent_organism=organism, kind="GOVERNANCE")
            .first()
        )
        if circle is None or str(circle.circle_uuid) != payload["circle_uuid"]:
            self._refuse(
                S015AuthorityRefusalCode.SBG_GOVERNANCE_CIRCLE_MISSING,
                None,
                act=act,
            )
        if not CircleStateEvent.objects.using(self._alias).filter(
            circle=circle,
            action="CREATE_DORMANT",
            effective_at__lte=effective_at,
            recorded_at__lte=received_at,
        ).exists():
            self._refuse(
                S015AuthorityRefusalCode.SBG_GOVERNANCE_CIRCLE_MISSING,
                None,
                act=act,
            )
        if membership.living_organism_id != organism.pk:
            self._refuse(
                S015AuthorityRefusalCode.SBG_MEMBER_NOT_ELIGIBLE,
                "is a member of another LO",
                act=act,
            )
        with connections[self._alias].cursor() as cursor:
            cursor.execute(
                "SELECT public.s015_fold_effective_state("
                "'core_organismmembership', %s, %s, %s)",
                [membership.pk, effective_at, received_at],
            )
            row = cursor.fetchone()
        if row is None:
            raise RuntimeError("membership state fold returned no row")
        if row[0] != "ACTIVE":
            self._refuse(
                S015AuthorityRefusalCode.SBG_MEMBER_NOT_ELIGIBLE,
                "is not an active member at that time",
                act=act,
            )
        arrangement_versions = GovernedRule.objects.using(self._alias).filter(
            scope_kind="LIVING_ORGANISM",
            scope_organism=organism,
            code="GOVERNANCE_CONSTITUTION",
            recorded_at__lte=received_at,
        )
        if not arrangement_versions.exists():
            self._refuse(
                S015AuthorityRefusalCode.SBG_ARRANGEMENT_MISSING,
                None,
                act=act,
            )
        rule = arrangement_versions.filter(
            rule_uuid=payload["seat_rule_uuid"],
            effective_from__lte=effective_at,
        ).order_by("-effective_from", "-recorded_at", "-version").first()
        seat_id = payload["seat_id"]
        if rule is None:
            self._refuse(S015AuthorityRefusalCode.SBG_SEAT_ABSENT, seat_id, act=act)
        body = rule.body
        seats = body.get("seats") if type(body) is dict else None
        if type(seats) is not list:
            self._refuse(
                S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
                "the arrangement does not contain a valid seat list",
                act=act,
            )
        seat = next(
            (
                item for item in seats
                if type(item) is dict and item.get("seat_id") == seat_id
            ),
            None,
        )
        if seat is None:
            self._refuse(S015AuthorityRefusalCode.SBG_SEAT_ABSENT, seat_id, act=act)
        if seat.get("eligibility") != "LO_MEMBER":
            self._refuse(
                S015AuthorityRefusalCode.SBG_ELIGIBILITY_UNSUPPORTED,
                seat_id,
                act=act,
            )
        if require_supported_route:
            admission_route = seat.get("admission_route")
            deciding_body = seat.get("deciding_body")
            if type(admission_route) is not str or admission_route not in _ROUTE_BODIES:
                self._refuse(
                    S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
                    f"seat {seat_id} names no admission route",
                    act=act,
                )
            if admission_route == "APPOINTMENT":
                valid_deciding_body = deciding_body == "office:LO_COORDINATOR" or (
                    type(deciding_body) is str
                    and deciding_body.startswith("external:")
                    and 0 < len(deciding_body.removeprefix("external:")) <= 120
                )
            elif admission_route == "EX_OFFICIO":
                try:
                    role_uuid = UUID(deciding_body.removeprefix("office:"))
                    valid_deciding_body = (
                        type(deciding_body) is str
                        and deciding_body.startswith("office:")
                        and str(role_uuid) == deciding_body.removeprefix("office:")
                    )
                except (AttributeError, TypeError, ValueError):
                    valid_deciding_body = False
            else:
                valid_deciding_body = deciding_body in _ROUTE_BODIES[admission_route]
            if not valid_deciding_body:
                self._refuse(
                    S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
                    f"seat {seat_id}'s deciding body does not match its admission route",
                    act=act,
                )
            if admission_route != "APPOINTMENT" or deciding_body != "office:LO_COORDINATOR":
                unavailable_routes = {
                    "ELECTION": "election, which waits for elections to be delivered",
                    "COOPTION": "co-option, which waits for an active GOVERNANCE",
                    "EX_OFFICIO": "ex officio attachment, which is not yet available",
                }
                route_detail = unavailable_routes.get(
                    admission_route,
                    "a body outside INTEVIA, whose decisions are not yet recorded here",
                )
                self._refuse(
                    S015AuthorityRefusalCode.SBG_SEAT_ROUTE_UNAVAILABLE,
                    f"{seat_id}:{route_detail}",
                    act=act,
                )
        try:
            seat_effective_from = datetime.fromisoformat(
                seat["effective_from"].replace("Z", "+00:00")
            )
        except (KeyError, TypeError, ValueError, AttributeError):
            self._refuse(
                S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
                f"seat {seat_id} has no valid start date",
                act=act,
            )
        if (
            seat_effective_from.tzinfo is None
            or seat_effective_from.utcoffset() is None
            or seat_effective_from > effective_at
        ):
            self._refuse(S015AuthorityRefusalCode.SBG_SEAT_ABSENT, seat_id, act=act)
        for future_rule in arrangement_versions.filter(
            rule_uuid=rule.rule_uuid,
            effective_from__gt=effective_at,
        ):
            future_body = future_rule.body
            future_seats = future_body.get("seats") if type(future_body) is dict else None
            if type(future_seats) is not list or not any(
                type(item) is dict and item.get("seat_id") == seat_id
                for item in future_seats
            ):
                self._refuse(S015AuthorityRefusalCode.SBG_SEAT_ABSENT, seat_id, act=act)
        if check_occupancy:
            for participation in CircleParticipation.objects.using(self._alias).filter(
                circle=circle,
                seat_rule_uuid=rule.rule_uuid,
                seat_id=seat_id,
                recorded_at__lte=received_at,
            ):
                ending = CircleParticipationEnd.objects.using(self._alias).filter(
                    participation=participation,
                    recorded_at__lte=received_at,
                ).first()
                if ending is None or ending.effective_at > effective_at:
                    self._refuse(
                        S015AuthorityRefusalCode.SBG_SEAT_OCCUPIED,
                        seat_id,
                        act=act,
                    )
            for participation in CircleParticipation.objects.using(self._alias).filter(
                circle=circle,
                membership__identity_id=membership.identity_id,
                recorded_at__lte=received_at,
            ):
                ending = CircleParticipationEnd.objects.using(self._alias).filter(
                    participation=participation,
                    recorded_at__lte=received_at,
                ).first()
                if ending is None or ending.effective_at > effective_at:
                    self._refuse(
                        S015AuthorityRefusalCode.SBG_ALREADY_SEATED,
                        None,
                        act=act,
                    )
        return circle, rule

    def _validate_adoption(
        self,
        *,
        organism: LivingOrganism,
        actor: Identity,
        payload: dict[str, object],
        effective_at: datetime,
        received_at: datetime,
    ) -> None:
        rule_uuid = UUID(payload["rule_uuid"])
        version = payload["version"]
        body = payload["body"]
        effective_from = payload["effective_from"]
        if effective_from < effective_at:
            self._refuse(
                S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
                "the arrangement cannot take effect before it is adopted",
            )
        current = GovernedRule.objects.using(self._alias).filter(
            scope_kind="LIVING_ORGANISM",
            scope_organism=organism,
            code="GOVERNANCE_CONSTITUTION",
            recorded_at__lte=received_at,
        ).order_by("-version", "-recorded_at", "-pk").first()
        expected_version = 1 if current is None else current.version + 1
        if version != expected_version or (current is not None and current.rule_uuid != rule_uuid):
            self._refuse(
                S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
                "the arrangement does not continue this LO's existing version sequence",
            )
        if current is None and GovernedRule.objects.using(self._alias).filter(
            scope_kind="LIVING_ORGANISM",
            scope_organism=organism,
            code="GOVERNANCE_CONSTITUTION",
            recorded_at__lte=received_at,
        ).exists():
            self._refuse(
                S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
                "this LO already has a GOVERNANCE arrangement",
            )
        self._validate_arrangement_body(body)
        if current is not None:
            self._refuse_if_held_seat_dropped(
                organism=organism,
                current_rule=current,
                next_body=body,
                effective_from=effective_from,
                received_at=received_at,
            )

    def _validate_arrangement_body(
        self,
        body: dict[str, object],
    ) -> None:
        required_fields = {"seats", "officer_roles", "election_schedule"}
        if type(body) is not dict or not required_fields.issubset(body):
            self._refuse(
                S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
                "it has no seats"
                if type(body) is not dict or "seats" not in body
                else "a required arrangement field is missing",
            )
        if set(body) - required_fields:
            self._refuse(
                S015AuthorityRefusalCode.SBG_ARRANGEMENT_FIELD_UNSUPPORTED,
                "a field this release does not record",
            )
        if (
            type(body["officer_roles"]) is not list
            or body["officer_roles"]
            or body["election_schedule"] is not None
        ):
            self._refuse(
                S015AuthorityRefusalCode.SBG_ARRANGEMENT_FIELD_UNSUPPORTED,
                "officer roles"
                if type(body["officer_roles"]) is not list or body["officer_roles"]
                else "an election schedule",
            )
        seats = body["seats"]
        if type(seats) is not list or not seats:
            self._refuse(
                S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
                "it has no seats",
            )
        seat_ids = []
        for seat in seats:
            if type(seat) is dict:
                seat_id = seat.get("seat_id")
                if type(seat_id) is str:
                    seat_ids.append(seat_id)
        if len(seat_ids) != len(set(seat_ids)):
            duplicate = next(seat_id for seat_id in seat_ids if seat_ids.count(seat_id) > 1)
            self._refuse(
                S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
                f"seat {duplicate} is duplicated",
            )
        for seat in seats:
            if type(seat) is not dict:
                self._refuse(
                    S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
                    "a seat is incomplete",
                )
            required_seat_fields = {
                "seat_id",
                "eligibility",
                "admission_route",
                "deciding_body",
                "term",
                "vacancy_rule",
                "effective_from",
            }
            if not required_seat_fields.issubset(seat):
                self._refuse(
                    S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
                    "a seat is incomplete",
                )
            if set(seat) - required_seat_fields:
                self._refuse(
                    S015AuthorityRefusalCode.SBG_ARRANGEMENT_FIELD_UNSUPPORTED,
                    "a field this release does not record",
                )
            if seat.get("term") is not None:
                self._refuse(
                    S015AuthorityRefusalCode.SBG_ARRANGEMENT_FIELD_UNSUPPORTED,
                    f"a term for seat {seat.get('seat_id')}",
                )
            if seat.get("vacancy_rule") != "BY_SEAT_ROUTE":
                self._refuse(
                    S015AuthorityRefusalCode.SBG_ARRANGEMENT_FIELD_UNSUPPORTED,
                    "a vacancy rule other than filling the seat by its own route",
                )
            seat_id = seat.get("seat_id")
            route = seat.get("admission_route")
            deciding_body = seat.get("deciding_body")
            if type(seat_id) is not str or _SEAT_ID.fullmatch(seat_id) is None:
                self._refuse(
                    S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
                    "a seat has no valid seat identifier",
                )
            if type(seat.get("eligibility")) is not str or seat["eligibility"] != "LO_MEMBER":
                self._refuse(
                    S015AuthorityRefusalCode.SBG_ELIGIBILITY_UNSUPPORTED,
                    seat_id,
                )
            if type(route) is not str or type(deciding_body) is not str:
                self._refuse(
                    S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
                    "seat route and deciding body must be strings",
                )
            if route not in _ROUTE_BODIES:
                self._refuse(
                    S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
                    f"seat {seat_id} names no admission route",
                )
            if route == "APPOINTMENT":
                valid_body = (
                    deciding_body == "office:LO_COORDINATOR"
                    or (deciding_body.startswith("external:") and 0 < len(deciding_body[9:]) <= 120)
                )
            elif route == "EX_OFFICIO":
                valid_body = (
                    deciding_body.startswith("office:")
                    and _canonical_uuid_string(deciding_body.removeprefix("office:"))
                )
            else:
                valid_body = deciding_body in _ROUTE_BODIES[route]
            if not valid_body:
                condition = (
                    f"seat {seat_id} names no deciding body"
                    if deciding_body is None
                    else f"seat {seat_id}'s deciding body does not match its admission route"
                )
                self._refuse(
                    S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
                    condition,
                )
            effective_from = seat["effective_from"]
            if (
                type(effective_from) is not str
                or re.fullmatch(
                    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T.*(Z|[+-][0-9]{2}:[0-9]{2})$",
                    effective_from,
                ) is None
            ):
                self._refuse(
                    S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
                    f"seat {seat_id} has no valid start date",
                )
            try:
                seat_effective_from = datetime.fromisoformat(
                    effective_from.replace("Z", "+00:00")
                )
            except ValueError:
                self._refuse(
                    S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
                    f"seat {seat_id} has no valid start date",
                )
            if seat_effective_from.tzinfo is None or seat_effective_from.utcoffset() is None:
                self._refuse(
                    S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
                    f"seat {seat_id} has no valid start date",
                )

    def _refuse_if_held_seat_dropped(
        self,
        *,
        organism: LivingOrganism,
        current_rule: GovernedRule,
        next_body: dict[str, object],
        effective_from: datetime,
        received_at: datetime,
    ) -> None:
        circles = Circle.objects.using(self._alias).filter(
            parent_organism=organism,
            kind="GOVERNANCE",
        )
        next_seats = {seat["seat_id"] for seat in next_body["seats"]}
        for participation in CircleParticipation.objects.using(self._alias).filter(
            circle__in=circles,
            seat_rule_uuid=current_rule.rule_uuid,
            recorded_at__lte=received_at,
        ):
            if participation.seat_id in next_seats:
                continue
            ending = CircleParticipationEnd.objects.using(self._alias).filter(
                participation=participation,
                recorded_at__lte=received_at,
            ).order_by("-recorded_at", "-pk").first()
            if ending is None or ending.effective_at > effective_from:
                self._refuse(
                    S015AuthorityRefusalCode.SBG_VERSION_DROPS_HELD_SEAT,
                    participation.seat_id,
                )

    @staticmethod
    def _refuse(
        code: S015AuthorityRefusalCode,
        detail: str | None,
        *,
        act: str = "No arrangement was adopted",
        organism_state: str | None = None,
    ) -> NoReturn:
        message = _authored_refusal_message(
            code,
            detail,
            act=act,
            organism_state=organism_state,
        )
        if message is None:
            raise RuntimeError(f"no authored message for governance refusal {code.value}")
        raise SBGGovernanceRefused(code, message)

    def _write_adoption(
        self,
        *,
        organism: LivingOrganism,
        actor: Identity,
        payload: dict[str, object],
        decision,
        fingerprint: str,
        request_reference: str,
        idempotency_key: str,
        effective_at: datetime,
        received_at: datetime,
    ) -> GovernanceActResult:
        rule_uuid = UUID(payload["rule_uuid"])
        event_uuid = uuid4()
        rule = GovernedRule.objects.using(self._alias).create(
            rule_uuid=rule_uuid,
            version=payload["version"],
            scope_kind="LIVING_ORGANISM",
            scope_organism=organism,
            scope_circle=None,
            code="GOVERNANCE_CONSTITUTION",
            body=payload["body"],
            authority_reference=f"lo-event:{event_uuid}",
            source=self._source_at(organism, effective_at, received_at),
            effective_from=payload["effective_from"],
            decided_at=effective_at,
            observations=None,
            enterer_identity=actor,
            text_commitment=None,
            recorded_at=None,
        )
        event_id, sequence = self._insert_living_event(
            organism=organism,
            actor=actor,
            action="ADOPT_GOVERNING_INSTRUMENT",
            event_uuid=event_uuid,
            evidence_reference=f"rule:{rule.rule_uuid}@{rule.version}",
            decision_reference=decision.authority_decision_reference,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            effective_at=effective_at,
            received_at=received_at,
        )
        event = LivingOrganismEvent.objects.using(self._alias).get(pk=event_id)
        return GovernanceActResult(
            living_organism_uuid=organism.organism_id,
            record_uuid=rule.rule_uuid,
            event_uuid=event.event_uuid,
            event_sequence=sequence,
            lineage_reference=event.lineage_reference,
            authority_reference=decision.authority_reference,
            authority_decision_reference=decision.authority_decision_reference,
        )

    def _write_circle(
        self,
        *,
        organism: LivingOrganism,
        actor: Identity,
        circle_uuid: UUID,
        decision: QualifiedS015AuthorityDecision,
        fingerprint: str,
        request_reference: str,
        idempotency_key: str,
        effective_at: datetime,
        received_at: datetime,
    ) -> GovernanceActResult:
        circle = Circle.objects.using(self._alias).create(
            circle_uuid=circle_uuid,
            parent_organism=organism,
            kind="GOVERNANCE",
            state=Circle.State.DORMANT,
            current_state_effective_at=effective_at,
            state_known_at=received_at,
            state_source_event_set_fingerprint=_empty_anchor_fingerprint(
                database_alias=self._alias,
                anchor_token="core_circle",
                anchor_uuid=circle_uuid,
            ),
            founding_reference=decision.authority_decision_reference,
            head_event=None,
        )
        event_uuid = uuid4()
        temporal_kind, temporal_reference = _resulting_temporal_basis(
            payload_fingerprint=fingerprint,
            effective_at=effective_at,
            received_at=received_at,
        )
        lineage_reference = _lineage("CREATE_DORMANT_CIRCLE", event_uuid, circle_uuid, fingerprint)
        with connections[self._alias].cursor() as cursor:
            cursor.execute(
                "SELECT public.s015_0022_digest('s015p1', "
                "public.s015_0022_parts_preimage(%s, %s::uuid))",
                ["core_circlestateevent", event_uuid],
            )
            parts_commitment = cursor.fetchone()[0]
            columns = {
                "event_uuid": event_uuid,
                "circle_id": circle.pk,
                "sequence": 1,
                "predecessor_id": None,
                "predecessor_sequence": None,
                "action": "CREATE_DORMANT",
                "prior_state": None,
                "resulting_state": "DORMANT",
                "actor_id": None,
                "actor_state": "LIVING_ORGANISM",
                "actor_access_epoch": None,
                "actor_capacity": "STANDING",
                "entry_mode": "TRANSPORT",
                "enterer_identity_id": actor.pk,
                "composing_rule_state": "NA_TRANSPORT",
                "composing_rule_reference": None,
                "composer_state": "NA_TRANSPORT",
                "composer_identity_id": None,
                "effective_until": None,
                "effective_until_state": "NONE",
                "authority_basis_id": organism.founding_authority_basis_id,
                "authority_decision_reference": decision.authority_decision_reference,
                "evidence_reference": f"living-organism:{organism.organism_id}",
                "request_reference": request_reference,
                "idempotency_key": idempotency_key,
                "payload_fingerprint": fingerprint,
                "lineage_reference": lineage_reference,
                "temporal_basis_kind": temporal_kind,
                "temporal_basis_reference": temporal_reference,
                "occurred_at": effective_at,
                "effective_at": effective_at,
                "received_at": received_at,
                "recorded_at": None,
                "predecessor_l1_commitment": None,
                "l2_commitment": None,
                "parts_commitment": parts_commitment,
            }
            cursor.execute(
                f"INSERT INTO public.core_circlestateevent ({', '.join(columns)}) "
                f"VALUES ({', '.join(['%s'] * len(columns))}) RETURNING id",
                list(columns.values()),
            )
            event_id = cursor.fetchone()[0]
            cursor.execute(
                "SELECT public.s015_refresh_governed_cache(%s, %s, %s, clock_timestamp())",
                ["core_circle", circle.pk, effective_at],
            )
            cursor.fetchone()
        from core.models import CircleStateEvent

        event = CircleStateEvent.objects.using(self._alias).get(pk=event_id)
        return GovernanceActResult(
            living_organism_uuid=organism.organism_id,
            record_uuid=circle.circle_uuid,
            event_uuid=event.event_uuid,
            event_sequence=1,
            lineage_reference=event.lineage_reference,
            authority_reference=decision.authority_reference,
            authority_decision_reference=decision.authority_decision_reference,
        )

    def _insert_living_event(
        self,
        *,
        organism: LivingOrganism,
        actor: Identity,
        action: str,
        event_uuid: UUID,
        evidence_reference: str,
        decision_reference: str,
        request_reference: str,
        idempotency_key: str,
        fingerprint: str,
        effective_at: datetime,
        received_at: datetime,
    ) -> tuple[int, int]:
        predecessor = (
            LivingOrganismEvent.objects.using(self._alias)
            .select_for_update()
            .filter(living_organism=organism)
            .order_by("-sequence")
            .first()
        )
        if predecessor is None:
            raise RuntimeError("living organism event chain is unavailable")
        sequence = predecessor.sequence + 1
        temporal_kind, temporal_reference = _resulting_temporal_basis(
            payload_fingerprint=fingerprint,
            effective_at=effective_at,
            received_at=received_at,
        )
        lineage_reference = _lineage(action, event_uuid, organism.organism_id, fingerprint)
        with connections[self._alias].cursor() as cursor:
            cursor.execute(
                "SELECT l1_commitment FROM public.core_livingorganismevent WHERE id = %s",
                [predecessor.pk],
            )
            predecessor_commitment = cursor.fetchone()
            if predecessor_commitment is None or predecessor_commitment[0] is None:
                raise RuntimeError("living organism event chain is unavailable")
            cursor.execute(
                "SELECT public.s015_0022_digest('s015p1', "
                "public.s015_0022_parts_preimage(%s, %s::uuid))",
                ["core_livingorganismevent", event_uuid],
            )
            parts_commitment = cursor.fetchone()[0]
            columns = {
                "event_uuid": event_uuid,
                "living_organism_id": organism.pk,
                "sequence": sequence,
                "predecessor_id": predecessor.pk,
                "predecessor_sequence": predecessor.sequence,
                "action": action,
                "prior_state": predecessor.resulting_state,
                "resulting_state": predecessor.resulting_state,
                "actor_id": None,
                "actor_state": "LIVING_ORGANISM",
                "actor_access_epoch": None,
                "actor_capacity": "STANDING",
                "entry_mode": "TRANSPORT",
                "enterer_identity_id": actor.pk,
                "composing_rule_state": "NA_TRANSPORT",
                "composing_rule_reference": None,
                "composer_state": "NA_TRANSPORT",
                "composer_identity_id": None,
                "effective_until": None,
                "effective_until_state": "NONE",
                "effective_until": None,
                "effective_until_state": "NONE",
                "authority_basis_id": organism.founding_authority_basis_id,
                "authority_decision_reference": decision_reference,
                "evidence_reference": evidence_reference,
                "request_reference": request_reference,
                "idempotency_key": idempotency_key,
                "payload_fingerprint": fingerprint,
                "lineage_reference": lineage_reference,
                "temporal_basis_kind": temporal_kind,
                "temporal_basis_reference": temporal_reference,
                "occurred_at": effective_at,
                "effective_at": effective_at,
                "received_at": received_at,
                "predecessor_l1_commitment": predecessor_commitment[0],
                "parts_commitment": parts_commitment,
            }
            cursor.execute(
                f"INSERT INTO public.core_livingorganismevent ({', '.join(columns)}) "
                f"VALUES ({', '.join(['%s'] * len(columns))}) RETURNING id",
                list(columns.values()),
            )
            event_id = cursor.fetchone()[0]
            cursor.execute(
                "SELECT public.s015_refresh_governed_cache(%s, %s, %s, clock_timestamp())",
                ["core_livingorganism", organism.pk, effective_at],
            )
            cursor.fetchone()
        return event_id, sequence


def _validate_body(body: object) -> dict[str, object]:
    if type(body) is not dict:
        raise TypeError("body must be an object")
    canonical_json_bytes(body)
    return body


def _authority_payload(
    action: OrganismAction,
    payload: dict[str, object],
) -> dict[str, object]:
    if action is OrganismAction.REQUEST_GOVERNANCE_SEAT:
        return {
            "circle_uuid": payload["circle_uuid"],
            "kind": "GOVERNANCE",
            "seat_rule_uuid": payload["seat_rule_uuid"],
            "seat_id": payload["seat_id"],
            "applicant_identity_uuid": payload["applicant_identity_uuid"],
        }
    if action is OrganismAction.ADOPT_GOVERNING_INSTRUMENT:
        return {
            "rule_uuid": payload["rule_uuid"],
            "version": payload["version"],
            "source": "",
            "body": payload["body"],
            "effective_from": canonical_timestamp(payload["effective_from"]),
        }
    if action is OrganismAction.CREATE_DORMANT_CIRCLE:
        return {
            "circle_uuid": payload["circle_uuid"],
            "kind": payload["kind"],
        }
    if action is OrganismAction.ADMIT_CIRCLE_PARTICIPANT:
        result = {
            "circle_uuid": payload["circle_uuid"],
            "kind": payload["kind"],
            "seat_rule_uuid": payload["seat_rule_uuid"],
            "seat_id": payload["seat_id"],
            "route": payload["route"],
            "request_uuid": payload["request_uuid"],
            "applicant_identity_uuid": payload["applicant_identity_uuid"],
        }
        if payload["route"] == "GOVERNANCE_CONSTITUTIONAL":
            result.update({
                "constitutional_route": payload["constitutional_route"],
                "deciding_party": payload["deciding_party"],
                "outcome": payload["outcome"],
            })
        return result
    raise ValueError(f"unsupported governance authority action: {action.value}")


def _authority_target(
    action: OrganismAction,
    organism_uuid: UUID,
    payload: dict[str, object],
) -> dict[str, object]:
    return {
        "action": action.value,
        "organism_uuid": organism_uuid,
        **_authority_payload(action, payload),
    }


def _timestamp(value: object, name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise TypeError(f"{name} must be timezone-aware")
    return value


def _canonical_uuid_string(value: str) -> bool:
    try:
        return str(UUID(value)) == value
    except (TypeError, ValueError):
        return False


def _lineage(action: str, event_uuid: UUID, subject_uuid: UUID, fingerprint: str) -> str:
    return "s015l1:" + hashlib.sha256(canonical_json_bytes({
        "action": action,
        "event_uuid": event_uuid,
        "fingerprint": fingerprint,
        "subject_uuid": subject_uuid,
    })).hexdigest()


__all__ = [
    "AdoptGoverningInstrumentCommand",
    "CreateGovernanceCircleCommand",
    "GovernanceActResult",
    "OrganismGovernanceService",
    "SBGGovernanceRefused",
]
