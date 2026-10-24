"""SB-G commands for establishing an LO's GOVERNANCE arrangements and seats.

An LO supplies its own arrangement, creates a dormant GOVERNANCE Circle,
and records seat requests and admission decisions. SB-G does not activate
GOVERNANCE or transfer governing authority from the LO's Coordinators
(SB-G build commission v1.0, section 0, HD-1 A and HD-2 A).

Adoption, Circle creation, seat requests and admission decisions each use
an authority_atomic scope and a receipt. Compound seating calls compose
the request and decision stages without an enclosing transaction here.

Receipt replay requires a matching request reference and payload fingerprint
and is served through serve_replay. Where database IntegrityError refusals
are translated, the primary message must exactly match a code in that
command's approved mapping; unmatched IntegrityError exceptions are re-raised.

Sources: SB-G build commission v1.0, sections 0 and 6; HD-3 amendment,
10 October 2026; SB-G authority and controlled-refusal ruling, same date.
"""
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
    IntakeEntry,
    LivingOrganism,
    LivingOrganismEvent,
    OrganismMembership,
    OrganismCommandReceipt,
)
from core.organism_contract import OrganismAction
from src.intevia.services.command_intake import BoundRequest, ChildEntry
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
    _intake_tail,
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
_TAIL_NOT_WRITTEN = (
    "INTEVIA kept a record that this request was received at {received_at}; "
    "sent again unchanged, it is answered as of that time."
)
_TAIL_REFUSAL_RECORDED = (
    "INTEVIA recorded this refusal and that the request was received at {received_at}; "
    "nothing else was saved."
)
_SEAT_ID = re.compile(r"[a-z0-9_-]{1,64}\Z")
_ROUTE_BODIES = {
    "ELECTION": {"body:LO_MEMBERSHIP"},
    "COOPTION": {"body:GOVERNANCE"},
    "APPOINTMENT": {"office:LO_COORDINATOR"},
    "EX_OFFICIO": set(),
}
_AUTHORED_REFUSALS = {
    S015AuthorityRefusalCode.E_NOT_APPLICANT: (
        "Nobody was seated and nothing changed. Before the LO is born, a person is seated only on "
        "their own request, through the founding route, and this request was made by someone else. "
        "Once the LO is born, the LO Coordinators can decide it, where the arrangement lets them "
        "appoint to that seat."
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
    """Return the applicant Identity's identity_id UUID as a string.

    Used as applicant_identity_uuid in the admission decision's authority
    target. Reads applicant_membership.identity.identity_id, not the
    membership's identity_id foreign-key value.
    """
    return str(applicant_membership.identity.identity_id)


def _governance_self_decision(
    applicant_membership: OrganismMembership,
    actor: Identity,
) -> bool:
    """Return True when the applicant's membership belongs to the deciding actor.

    Recorded on the admission decision as self_decision (SB-G build commission
    v1.0, section 5.4: true iff the decider is the person).
    """
    return applicant_membership.identity_id == actor.pk


def _authored_refusal_message(
    code: S015AuthorityRefusalCode,
    detail: str | None,
    *,
    act: str,
    organism_state: str | None = None,
    opening_end: str = " and nothing changed.",
    tail: str = "",
) -> str | None:
    """Return an authored refusal explanation, or None if none is selected.

    Some branches use act as the opening clause; others have a fixed opening.
    detail supplies code-specific context, with fallback wording where defined.
    For SBG_LO_NOT_ACTIVE, organism_state must be DORMANT or CLOSED to select
    the corresponding explanation.

    After the explicit branches, look up E_NOT_APPLICANT in
    _AUTHORED_REFUSALS (the founding decision on another person's seat
    request).

    SB-G build commission v1.0, section 10.2, requires refusal explanations to
    state what happened and that nothing changed, why, what can be done, and
    how to avoid it. Under the request intake, an explanation raised after
    admission does not say that nothing changed: it states the route's actual
    persisted effects, including the intake's record of the request's receipt,
    through a tail the caller supplies. This helper selects text; it does not
    validate that the text satisfies that form. opening_end replaces
    " and nothing changed." in every branch that has it, and a non-empty tail
    is appended after one space; the defaults give the text unchanged.
    """
    def with_tail(message: str) -> str:
        return f"{message} {tail}" if tail else message

    if code is S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE:
        condition = detail or "the arrangement does not meet its requirements"
        return with_tail(
            f"{act}{opening_end} The arrangement is incomplete: {condition}. "
            "Adopt a corrected version, then try again. Every seat needs an admission route "
            "and a deciding body that matches it."
        )
    if code is S015AuthorityRefusalCode.SBG_ARRANGEMENT_FIELD_UNSUPPORTED:
        condition = detail or "a field this release does not record"
        return with_tail(
            f"No arrangement was adopted{opening_end} The arrangement includes {condition}, "
            "which this release cannot yet act on. Leave it out for now; it can be added by a later "
            "version once the release that acts on it is delivered."
        )
    if code is S015AuthorityRefusalCode.SBG_ELIGIBILITY_UNSUPPORTED:
        seat_id = detail or "the requested seat"
        return with_tail(
            f"No arrangement was adopted{opening_end} Seat {seat_id} names an eligibility "
            "rule this release cannot check; it can check only \"an active member of the LO\". Use "
            "that rule for now; other eligibility rules arrive with the slices that can check them."
        )
    if code is S015AuthorityRefusalCode.SBG_VERSION_DROPS_HELD_SEAT:
        seat_id = detail or "the requested seat"
        return with_tail(
            f"No arrangement was adopted{opening_end} The new version leaves out seat {seat_id}, "
            "which someone currently holds. Keep that seat in the new version; a held seat cannot be "
            "removed by a new version in this release."
        )
    if code is S015AuthorityRefusalCode.SBG_GOVERNANCE_CIRCLE_MISSING:
        return with_tail(
            f"{act}{opening_end} GOVERNANCE is on for this LO, so it needs its GOVERNANCE "
            "Circle first. Create the LO's GOVERNANCE Circle, then try again. Creating the Circle "
            "early in formation avoids this."
        )
    if code is S015AuthorityRefusalCode.SBG_ARRANGEMENT_MISSING:
        return with_tail(
            f"{act}{opening_end} GOVERNANCE needs an adopted arrangement saying which seats "
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
        return with_tail(
            f"{act}{opening_end} Nobody holds a GOVERNANCE seat yet, and no seat in the "
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
        return with_tail(
            f"{act}{opening_end} This LO is {organism_state.lower()}, so it can take no new "
            f"act of this kind. {remedy} Nothing about the LO's records changed."
        )
    if code is S015AuthorityRefusalCode.SBG_SEAT_ABSENT:
        seat_id = detail or "the requested seat"
        return with_tail(
            f"{act}{opening_end} The arrangement in effect has no seat {seat_id}. "
            "Choose a seat the arrangement in effect names. Check the seats in the current version "
            "before requesting one."
        )
    if code is S015AuthorityRefusalCode.SBG_SEAT_OCCUPIED:
        seat_id = detail or "the requested seat"
        return with_tail(
            f"Nobody was seated{opening_end} Seat {seat_id} is already held at that time, and "
            "each seat holds one person. Choose a vacant seat, or wait until this seat becomes vacant. "
            "A seat becomes vacant when its holder's seat ends."
        )
    if code is S015AuthorityRefusalCode.SBG_ALREADY_SEATED:
        return with_tail(
            f"Nobody was seated{opening_end} This person already holds a seat in this GOVERNANCE "
            "Circle, and a person holds at most one. No further action is needed for this person."
        )
    if code is S015AuthorityRefusalCode.SBG_MEMBER_NOT_ELIGIBLE:
        condition = detail or "is not an active member at that time"
        return with_tail(
            f"Nobody was seated{opening_end} A GOVERNANCE seat needs an active member of this LO "
            f"at that time, and this person {condition}. Seat someone who is an active member of this LO."
        )
    if code is S015AuthorityRefusalCode.SBG_SEAT_ROUTE_UNAVAILABLE:
        seat_id = "the requested seat"
        route = detail or "the founding route, which ends when the LO is born"
        if ":" in route:
            seat_id, route = route.split(":", 1)
        return with_tail(
            f"Nobody was seated{opening_end} Seat {seat_id} is filled by {route}. Use a seat "
            "the LO Coordinators appoint, or wait for that route."
        )
    if code is S015AuthorityRefusalCode.SBG_SEATING_WHILE_DISABLED:
        return with_tail(
            f"Nobody was seated{opening_end} GOVERNANCE is switched off for this LO, so no one "
            "can be seated. Switch GOVERNANCE on through the LO's settings first, if the LO chooses to."
        )
    if code is S015AuthorityRefusalCode.SBG_SECOND_GOVERNANCE_CIRCLE:
        return with_tail(
            f"No Circle was created{opening_end} This LO already has its GOVERNANCE Circle, and "
            "an LO has exactly one. Use the existing Circle; switching GOVERNANCE back on uses the same one."
        )
    return _AUTHORED_REFUSALS.get(code)


def _controlled_integrity_refusal(
    exc: IntegrityError,
    allowed_codes: frozenset[S015AuthorityRefusalCode],
    *,
    detail: str | None,
    act: str,
    opening_end: str = " and nothing changed.",
    tail: str = "",
) -> SBGGovernanceRefused | None:
    """Build a controlled database refusal exception, or return None.

    Read message_primary from exc.__cause__.diag. Return SBGGovernanceRefused
    only when that message exactly matches the value of an allowed code
    and an authored explanation is available.

    For SBG_MEMBER_NOT_ELIGIBLE, discard the supplied detail and use the
    default wording. Return None otherwise; this file's callers then
    re-raise the original IntegrityError. This helper does not raise it.

    Source: SB-G authority and controlled-refusal ruling,
    10 October 2026, point 4.
    """
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
    message = _authored_refusal_message(
        code,
        message_detail,
        act=act,
        opening_end=opening_end,
        tail=tail,
    )
    if message is None:
        return None
    return SBGGovernanceRefused(code, message)


def _active_governance_circle_seating_refusal(seat_id: str) -> str:
    """Build the explanation referring to a previously active GOVERNANCE Circle.

    The text says that LO-Coordinator appointment is available in this release
    only while the GOVERNANCE Circle has never been active, and directs later
    admission to the Circle's own GOVERNANCE route.

    Selected here when authority qualification denies a
    GOVERNANCE_CONSTITUTIONAL decision with SBG_SEAT_ROUTE_UNAVAILABLE.
    This helper formats the explanation; it does not inspect activation
    history or determine authority.

    Related requirement: SB-G build commission v1.0, section 0, LD-5.
    """
    return (
        f"Nobody was seated and nothing changed. Seat {seat_id} is filled by appointment by the LO "
        "Coordinators, which this release can use only while the LO's GOVERNANCE Circle has never "
        "been active, and this Circle has been active before. A Circle that has been active admits "
        "new participants through its own GOVERNANCE route, which arrives with a later release. "
        "The LO's Coordinators continue to govern until then."
    )


@dataclass(frozen=True, slots=True)
class AdoptGoverningInstrumentCommand:
    """Request to adopt a version of the LO's GOVERNANCE arrangement.

    The arrangement is the LO's own; no default exists in code (HD-2 A).
    Permission row: ADOPT_GOVERNING_INSTRUMENT/GOVERNANCE_CONSTITUTION.
    """
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
    """Request to create the LO's one GOVERNANCE Circle, dormant.

    Permission row: CREATE_DORMANT_CIRCLE.
    """
    credential: User
    living_organism_uuid: UUID
    request_reference: str
    idempotency_key: str
    effective_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SeatFoundingGovernanceCircleCommand:
    """Request the compound founding-seating operation for GOVERNANCE.

    Intended for formation before Birthing (SB-G build commission v1.0,
    section 6). Records the person's own request first, then attempts an
    ACCEPT decision through CIRCLE_FOUNDING, with separate authorisations.

    A successful request stage can remain recorded if admission is refused.
    """
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
    """A member's own request and consent for a GOVERNANCE seat.

    Permission row: REQUEST_GOVERNANCE_SEAT (HD-3 amendment, 10 Oct 2026).
    It records consent only: it neither admits nor reserves the seat.
    """
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
    """Request an ACCEPT or DECLINE decision on a GOVERNANCE seat request.

    Permission row: ADMIT_CIRCLE_PARTICIPANT. With request_uuid, decides that
    request. If route is omitted, selects CIRCLE_FOUNDING or
    GOVERNANCE_CONSTITUTIONAL from the Birthing record visible at the
    decision's effective_at and received_at.

    Without request_uuid, first records the caller's own request, then attempts
    the decision through GOVERNANCE_CONSTITUTIONAL. This branch does not use
    the supplied route. The two stages retain separate authorisations and
    receipts; a successful request can remain recorded if admission is refused.
    """
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
    """Result of an arrangement adoption or a GOVERNANCE Circle creation.

    record_uuid is the rule for an adoption and the Circle for a creation;
    event_uuid and event_sequence are the LO event of the adoption, or the
    Circle's state event (sequence 1) of the creation. Carries the event's
    lineage reference and the act's authority and authority-decision references.

    receipt_payload() returns the stored receipt form. replay_field_audiences()
    returns an empty mapping for a result of exactly this class and raises
    TypeError otherwise; serve_replay in organism_command_support defines how
    the mapping is used.
    """
    living_organism_uuid: UUID
    record_uuid: UUID
    event_uuid: UUID
    event_sequence: int
    lineage_reference: str
    authority_reference: str
    authority_decision_reference: str

    def receipt_payload(self) -> dict[str, str | int]:
        """Return the receipt form: UUIDs as strings, the other fields as they
        are."""
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
        """Return an empty mapping for a result of exactly this class; raise
        TypeError otherwise."""
        if type(result) is not cls:
            raise TypeError("governance replay result has the wrong type")
        return {}


@dataclass(frozen=True, slots=True)
class GovernanceSeatingResult:
    """Result returned by the compound founding-seating operation.

    Carries the LO, request, admission-decision and participation UUIDs,
    with the decision stage's lineage, authority and authority-decision
    references.

    receipt_payload() returns these fields as a dictionary, serialising UUIDs
    as strings. The founding wrapper does not store a separate receipt for
    this combined result.

    replay_field_audiences() returns an empty mapping when the result's type
    is exactly cls and raises TypeError otherwise.
    """
    living_organism_uuid: UUID
    request_uuid: UUID
    decision_uuid: UUID
    participation_uuid: UUID
    lineage_reference: str
    authority_reference: str
    authority_decision_reference: str

    def receipt_payload(self) -> dict[str, str]:
        """Return these fields as a dictionary, UUIDs as strings."""
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
        """Return an empty mapping for a result of exactly this class; raise
        TypeError otherwise."""
        if type(result) is not cls:
            raise TypeError("governance seating replay result has the wrong type")
        return {}


@dataclass(frozen=True, slots=True)
class GovernanceSeatRequestResult:
    """Result of a recorded GOVERNANCE seat request.

    Carries the LO, the request and the request's lineage reference; it has no
    authority-reference fields. receipt_payload() and replay_field_audiences()
    behave as for GovernanceActResult.
    """
    living_organism_uuid: UUID
    request_uuid: UUID
    lineage_reference: str

    def receipt_payload(self) -> dict[str, str]:
        """Return the LO and request UUIDs as strings and the lineage
        reference."""
        return {
            "living_organism_uuid": str(self.living_organism_uuid),
            "request_uuid": str(self.request_uuid),
            "lineage_reference": self.lineage_reference,
        }

    @classmethod
    def replay_field_audiences(cls, result: GovernanceSeatRequestResult) -> dict[str, str]:
        """Return an empty mapping for a result of exactly this class; raise
        TypeError otherwise."""
        if type(result) is not cls:
            raise TypeError("governance seat request replay result has the wrong type")
        return {}


@dataclass(frozen=True, slots=True)
class GovernanceSeatDecisionResult:
    """Result of a GOVERNANCE seat decision.

    Carries the request, the admission decision, the participation (None
    unless the outcome is ACCEPT), and the lineage, authority and
    authority-decision references. receipt_payload() stores a missing
    participation as None. replay_field_audiences() behaves as for
    GovernanceActResult.
    """
    living_organism_uuid: UUID
    request_uuid: UUID
    decision_uuid: UUID
    participation_uuid: UUID | None
    lineage_reference: str
    authority_reference: str
    authority_decision_reference: str

    def receipt_payload(self) -> dict[str, str | None]:
        """Return the receipt form: UUIDs as strings, a missing participation
        as None, and the references as they are."""
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
        """Return an empty mapping for a result of exactly this class; raise
        TypeError otherwise."""
        if type(result) is not cls:
            raise TypeError("governance seat decision replay result has the wrong type")
        return {}


class SBGGovernanceRefused(Exception):
    """An SB-G refusal this service explains with authored text.

    Carries refusal_code and the explanation as its message. request_uuid is
    set where the raising path supplies one: the seat-decision paths set it
    when a request was recorded before the refusal.
    """
    def __init__(
        self,
        refusal_code: S015AuthorityRefusalCode,
        message: str,
        *,
        request_uuid: UUID | None = None,
    ) -> None:
        """Set the message, refusal_code and request_uuid (None unless
        given)."""
        super().__init__(message)
        self.refusal_code = refusal_code
        self.request_uuid = request_uuid


def _compound_stage_identifiers(
    organism_uuid: UUID,
    interaction_key: str,
) -> tuple[str, str, str, str]:
    """Derive the stage keys and request references for a compound seating.

    Returns the request stage's idempotency key and request reference, then
    the admission stage's: each is a prefix (sbg:req:, sbg:reqref:, sbg:admit:,
    sbg:admitref:) followed by the SHA-256 of the canonical JSON of the LO,
    the interaction key and that stage's action. The same LO and interaction
    key always give the same four values.
    """
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


@dataclass(frozen=True, slots=True)
class _CompoundRecipe:
    """Describe one private compound seating route and its result conversion."""

    expected_entry: IntakeEntry
    stage1: Callable[
        [SeatFoundingGovernanceCircleCommand | DecideGovernanceSeatCommand, str, str],
        RequestGovernanceSeatCommand,
    ]
    stage2_outcome: Callable[
        [SeatFoundingGovernanceCircleCommand | DecideGovernanceSeatCommand],
        str,
    ]
    stage2_route: str
    post_check: Callable[[GovernanceSeatDecisionResult], None] | None
    result_from: Callable[
        [GovernanceSeatDecisionResult],
        GovernanceSeatingResult | GovernanceSeatDecisionResult,
    ]


def _build_compound_request(
    command: SeatFoundingGovernanceCircleCommand | DecideGovernanceSeatCommand,
    request_reference: str,
    idempotency_key: str,
) -> RequestGovernanceSeatCommand:
    """Build the common request stage from a compound command."""
    return RequestGovernanceSeatCommand(
        credential=command.credential,
        living_organism_uuid=command.living_organism_uuid,
        circle_uuid=command.circle_uuid,
        seat_rule_uuid=command.seat_rule_uuid,
        seat_id=command.seat_id,
        request_reference=request_reference,
        idempotency_key=idempotency_key,
        effective_at=command.effective_at,
    )


def _compound_decision_outcome(
    command: SeatFoundingGovernanceCircleCommand | DecideGovernanceSeatCommand,
) -> str:
    """Return the adopted outcome for the self-decision compound recipe."""
    if type(command) is not DecideGovernanceSeatCommand:
        raise TypeError("compound decision recipe requires DecideGovernanceSeatCommand")
    return command.outcome


def _founding_decision_outcome(
    command: SeatFoundingGovernanceCircleCommand | DecideGovernanceSeatCommand,
) -> str:
    """Return ACCEPT for the founding-seating compound recipe."""
    del command
    return "ACCEPT"


def _founding_participation_check(result: GovernanceSeatDecisionResult) -> None:
    """Require the founding acceptance to return its participation identity."""
    if result.participation_uuid is None:
        raise RuntimeError("founding acceptance returned no participation")


def _founding_seating_result(
    result: GovernanceSeatDecisionResult,
) -> GovernanceSeatingResult:
    """Convert the decision result to the founding route's established result type."""
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


def _identity_decision_result(
    result: GovernanceSeatDecisionResult,
) -> GovernanceSeatDecisionResult:
    """Keep the decision result type for the compound decision recipe."""
    return result


_RECIPE_DECIDE_COMPOUND = _CompoundRecipe(
    expected_entry=IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND,
    stage1=_build_compound_request,
    stage2_outcome=_compound_decision_outcome,
    stage2_route="GOVERNANCE_CONSTITUTIONAL",
    post_check=None,
    result_from=_identity_decision_result,
)
_RECIPE_SEAT_FOUNDING = _CompoundRecipe(
    expected_entry=IntakeEntry.SEAT_GOVERNANCE_CIRCLE,
    stage1=_build_compound_request,
    stage2_outcome=_founding_decision_outcome,
    stage2_route="CIRCLE_FOUNDING",
    post_check=_founding_participation_check,
    result_from=_founding_seating_result,
)


class SBGGovernanceUnavailable(Exception):
    """Signal that required governance context is unavailable.

    Raised directly here when the LO's folded state is not established.
    Also supplied as the unavailable exception class to imported actor
    and membership-context locking helpers; their raising conditions
    are defined in organism_command_support.

    In the seat-request path, an exception with the exact message
    "actor active membership is required" is translated into
    SBG_MEMBER_NOT_ELIGIBLE; other such exceptions are re-raised.
    """
    pass


class OrganismGovernanceService:
    """Governed service for SB-G arrangements, Circle creation and seating.

    Requires an injected clock. Adoption and Circle creation take received_at
    from the durable intake; seat routes read it from the clock until
    PKT-CLK-3's chunk CLK3-3. When a route serves a result through ReplayViewer,
    it reads the injected clock for the present-time access check.

    The founding-seating method and the decision method's no-request branch
    compose separately authorised request and admission-decision stages
    (HD-3 amendment, 10 October 2026).
    """
    def __init__(
        self,
        *,
        clock: Callable[[], datetime],
        authority: S015Authority | None = None,
        database_alias: str = "default",
    ) -> None:
        """Check and keep the clock, authority and database alias.

        clock must be callable, else TypeError; database_alias a non-empty
        str, else ValueError. With no authority, build an S015Authority over
        ProductionApplicationAuthorityProvider on database_alias. A supplied
        or built authority must be an S015Authority on the same alias, else
        TypeError.
        """
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
        """Adopt a supplied version of the LO's GOVERNANCE arrangement.

        Permission row: ADOPT_GOVERNING_INSTRUMENT/GOVERNANCE_CONSTITUTION.
        Writes the rule version, LO adoption event and receipt within the
        command's authority_atomic scope. Admits the request through the
        durable intake first (shape, actor, binding); the act then runs as of
        the intake's received_at.

        For an omitted recorded seat participation, raises
        SBG_VERSION_DROPS_HELD_SEAT when no ending is known or its effective_at
        is later than the new version's effective_from
        (SB-G build commission v1.0, section 9).

        Database guard refusals translated on an exact primary-message match:
        SBG_ARRANGEMENT_INCOMPLETE, SBG_ARRANGEMENT_FIELD_UNSUPPORTED and
        SBG_ELIGIBILITY_UNSUPPORTED. Other IntegrityError exceptions propagate.
        Malformed inputs may raise TypeError or ValueError.
        """
        from src.intevia.services.command_intake import (
            ACTIVE_ENTRIES,
            IntakeAdmissionTechnicalFailure,
            _require_admission_state,
            _resolve_actor,
            admit_request,
            intake_shape,
        )

        entry = IntakeEntry.ADOPT_GOVERNING_INSTRUMENT
        _require_admission_state(self._alias, entry)
        if entry not in ACTIVE_ENTRIES:
            raise IntakeAdmissionTechnicalFailure("ADOPT_GOVERNING_INSTRUMENT is not active")
        shape = intake_shape(entry, command)
        actor_ref = _resolve_actor(self._alias, command.credential)
        bound = admit_request(self._alias, entry, actor_ref, shape)
        return self._execute_adopt_governing_instrument(command, bound)

    def _execute_adopt_governing_instrument(
        self,
        command: AdoptGoverningInstrumentCommand,
        bound: BoundRequest,
    ) -> GovernanceActResult:
        """Execute admitted adoption using the bound request (D §5.4).

        Canonicalizes identifiers, validates the version, arrangement body and
        effective time, then invokes the adoption transaction with the bound
        receipt time. Translates only IntegrityErrors whose primary message
        matches an adoption guard refusal; unrelated database errors propagate.
        """
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
                bound=bound,
            )
        except IntegrityError as exc:
            refusal = _controlled_integrity_refusal(
                exc,
                _ADOPTION_GUARD_REFUSAL_CODES,
                detail=None,
                act="No arrangement was adopted",
                opening_end="; no governed record was written.",
                tail=_intake_tail(_TAIL_NOT_WRITTEN, bound.received_at),
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
        """Create a dormant GOVERNANCE Circle for the LO.

        Permission row: CREATE_DORMANT_CIRCLE. After authority qualification,
        writes the Circle, its sequence-1 CREATE_DORMANT event and a receipt
        within the command's authority_atomic scope.

        The commission requires one GOVERNANCE Circle per LO, reused when
        GOVERNANCE is re-enabled (SB-G build commission v1.0, section 6).
        If authority qualification denies with SBG_SECOND_GOVERNANCE_CIRCLE,
        this service supplies the authored explanation.
        Admits the request through the durable intake first (shape, actor,
        binding); the act then runs as of the intake's received_at.
        """
        from src.intevia.services.command_intake import (
            ACTIVE_ENTRIES,
            IntakeAdmissionTechnicalFailure,
            _require_admission_state,
            _resolve_actor,
            admit_request,
            intake_shape,
        )

        entry = IntakeEntry.CREATE_GOVERNANCE_CIRCLE
        _require_admission_state(self._alias, entry)
        if entry not in ACTIVE_ENTRIES:
            raise IntakeAdmissionTechnicalFailure("CREATE_GOVERNANCE_CIRCLE is not active")
        shape = intake_shape(entry, command)
        actor_ref = _resolve_actor(self._alias, command.credential)
        bound = admit_request(self._alias, entry, actor_ref, shape)
        return self._execute_create_governance_circle(command, bound)

    def _execute_create_governance_circle(
        self,
        command: CreateGovernanceCircleCommand,
        bound: BoundRequest,
    ) -> GovernanceActResult:
        """Execute admitted Circle creation using the bound request (D §5.4).

        Canonicalizes the target LO identifier and invokes the act transaction
        with the bound receipt time. The transaction derives a stable Circle
        identifier, qualifies authority, writes the dormant Circle and event,
        and stores a receipt; a denied act preserves the refusal decision.
        """
        organism_uuid = _canonical_uuid(command.living_organism_uuid, "living_organism_uuid")
        result = self._execute(
            command=command,
            action=OrganismAction.CREATE_DORMANT_CIRCLE,
            subtype=_CIRCLE_SUBTYPE,
            organism_uuid=organism_uuid,
            payload={"kind": "GOVERNANCE"},
            bound=bound,
        )
        if not isinstance(result, GovernanceActResult):
            raise RuntimeError("Circle creation returned a seating result")
        return result

    def seat_governance_circle(
        self,
        command: SeatFoundingGovernanceCircleCommand,
    ) -> GovernanceSeatingResult:
        """Attempt founding GOVERNANCE seating through request and admission stages.

        Uses REQUEST_GOVERNANCE_SEAT followed by ADMIT_CIRCLE_PARTICIPANT with
        route CIRCLE_FOUNDING and outcome ACCEPT. Each stage has its own
        authorisation, receipt, derived key and request reference.

        The stages are called separately; a successful request can remain recorded
        if admission is refused (controlled-refusal ruling, 10 October 2026).

        Derived identifiers are stable for the LO and interaction key. Replay
        also requires matching stage fingerprints, including effective_at;
        reusing the interaction key alone does not guarantee replay.

        This is the founding route specified for formation before Birthing
        (SB-G build commission v1.0, section 6).
        """
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
        """Record a member's own GOVERNANCE seat request and consent.

        Permission row: REQUEST_GOVERNANCE_SEAT
        (HD-3 amendment, 10 October 2026). The request neither admits the member
        nor reserves the seat; admission requires a separate authorised decision.

        Passes the injected clock's received_at to authority qualification.
        Local membership validation folds state at effective_at using
        received_at as the knowledge bound.

        Database guard refusals translated on an exact primary-message match:
        SBG_MEMBER_NOT_ELIGIBLE and SBG_SEAT_ABSENT.
        Other IntegrityError exceptions propagate.
        """
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
        """Record a member's own GOVERNANCE seat request in one authority_atomic scope.

        Before the scope: validates the command (canonical UUIDs, the seat_id
        pattern, the keys, and an effective_at no later than the clock's
        received_at, defaulting to received_at), derives request_uuid from the LO
        and idempotency key, and fingerprints the request
        (schema sbg-governance-seat-request-v1).

        Within the scope, in this order: locks the actor and takes the per-actor
        command mutex; serves a matching receipt through serve_replay, raising
        ValueError for a mismatched one; locks the registered LO and its row
        (LookupError if absent); locks the actor's membership context, refusing
        SBG_MEMBER_NOT_ELIGIBLE when that helper reports that active membership is
        required; validates the seat through _validate_constitutional_seating in
        request mode (no enabled, occupancy, Birthing or route requirement); asks
        the authority to qualify REQUEST_GOVERNANCE_SEAT, rewording a denial where
        an authored text exists; then writes the CircleJoinRequest (initiated by the
        applicant, consent_reference set to request_reference) and a receipt citing
        the authority decision.
        """
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
        return self._request_governance_seat_at(
            command,
            organism_uuid=organism_uuid,
            circle_uuid=circle_uuid,
            rule_uuid=rule_uuid,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            received_at=received_at,
            effective_at=effective_at,
            verify_after_actor_lock=None,
        )

    def _request_governance_seat_at(
        self,
        command: RequestGovernanceSeatCommand,
        *,
        organism_uuid: UUID,
        circle_uuid: UUID,
        rule_uuid: UUID,
        request_reference: str,
        idempotency_key: str,
        received_at: datetime,
        effective_at: datetime,
        verify_after_actor_lock: Callable[[Identity], None] | None,
    ) -> GovernanceSeatRequestResult:
        """Run the shared request-stage body at an explicit time.

        The public clock route passes no verifier; its existing route remains
        unchanged until PKT-CLK-3. Bound stage executors supply a verifier and
        their governed receive time without consulting the clock.
        """

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
            if verify_after_actor_lock is not None:
                verify_after_actor_lock(actor)
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

    def _execute_request_governance_seat(
        self,
        command: RequestGovernanceSeatCommand,
        bound: BoundRequest,
    ) -> GovernanceSeatRequestResult:
        """Execute a verified request stage at its bound receive time.

        This private executor serves REQUEST_GOVERNANCE_SEAT and REQUEST_STAGE;
        the public clock route remains unchanged until PKT-CLK-3.
        """
        organism_uuid = _canonical_uuid(command.living_organism_uuid, "living_organism_uuid")
        circle_uuid = _canonical_uuid(command.circle_uuid, "circle_uuid")
        rule_uuid = _canonical_uuid(command.seat_rule_uuid, "seat_rule_uuid")
        if type(command.seat_id) is not str or _SEAT_ID.fullmatch(command.seat_id) is None:
            raise ValueError("seat_id is invalid")
        request_reference = _canonical_key(command.request_reference, "request_reference", 128)
        idempotency_key = _canonical_key(command.idempotency_key, "idempotency_key", 120)
        received_at = bound.received_at
        effective_at = (
            received_at if command.effective_at is None
            else _timestamp(command.effective_at, "effective_at")
        )
        if effective_at > received_at:
            raise ValueError("effective_at must not be later than received_at")
        try:
            return self._request_governance_seat_at(
                command,
                organism_uuid=organism_uuid,
                circle_uuid=circle_uuid,
                rule_uuid=rule_uuid,
                request_reference=request_reference,
                idempotency_key=idempotency_key,
                received_at=received_at,
                effective_at=effective_at,
                verify_after_actor_lock=lambda actor: bound.verify(
                    command,
                    actor,
                    frozenset(
                        {
                            IntakeEntry.REQUEST_GOVERNANCE_SEAT,
                            ChildEntry.REQUEST_STAGE,
                        }
                    ),
                    self._alias,
                ),
            )
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

    def _request_open_sentence(
        self,
        seat_id: str,
        *,
        stage_context: ChildEntry | None = None,
    ) -> str:
        """Return the sentence saying the caller's request for seat_id is recorded
        and remains open.

        The sentence is fixed text; this method does not check the request's status.
        The optional stage context identifies the chunk-3 hand-off and does not
        alter the public route's wording. Any stage context other than
        ADMIT_STAGE raises ValueError.
        """
        if stage_context is not None and stage_context is not ChildEntry.ADMIT_STAGE:
            raise ValueError("open-request sentence requires the admission stage")
        return f"Your own request for seat {seat_id} is recorded and remains open."

    def _request_open_context(self, request_uuid: UUID) -> tuple[str, str] | None:
        """Return (seat_id, the open-request sentence) for request_uuid, or None.

        Returns None when no CircleJoinRequest with request_uuid is recorded. It
        checks only that the request exists, not whether it has been decided.
        """
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
        """Record an ACCEPT or DECLINE decision on a GOVERNANCE seat request.

        Permission row: ADMIT_CIRCLE_PARTICIPANT. The decision stage writes
        the admission decision, participation on ACCEPT, and its receipt
        within one authority_atomic scope.

        Without request_uuid, first records the caller's own request in a
        separate stage, then attempts a GOVERNANCE_CONSTITUTIONAL decision.
        A successful request stage can survive refusal of the decision stage
        (HD-3 amendment and controlled-refusal ruling, 10 October 2026).

        Database admission-guard refusals translated on an exact primary-message
        match: SBG_MEMBER_NOT_ELIGIBLE, SBG_SEAT_ABSENT, SBG_SEAT_OCCUPIED and
        SBG_ALREADY_SEATED. Unmatched IntegrityError exceptions propagate.

        Handled admission refusals with available request context include text
        saying the request remains open and carry request_uuid on the exception.
        """
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
        """Record an ACCEPT or DECLINE decision on a GOVERNANCE seat request.

        Without request_uuid, requires circle_uuid, seat_rule_uuid and a valid
        seat_id. Uses _compound_stage_identifiers to record the caller's own
        request through request_governance_seat, then recursively decides that
        request through GOVERNANCE_CONSTITUTIONAL. The two stages have separate
        authority_atomic scopes. Handled decision-stage refusals carry the
        request UUID and the open-request sentence.

        With request_uuid, rejects supplied circle_uuid, seat_rule_uuid or
        seat_id. Validates outcome, route and times, then fingerprints the
        decision using schema sbg-governance-seat-decision-v1.

        Within authority_atomic, calls the actor lock helper, takes the command
        mutex and checks for a receipt. A matching receipt is reconstructed and
        passed to serve_replay; a reference or fingerprint mismatch raises
        ValueError. Otherwise, calls the registered-LO lock helper, locks the LO
        row, obtains the actor's membership context and locks the request.

        Refuses SBG_MEMBER_NOT_ELIGIBLE if the request is missing, belongs to
        another LO, was not applicant-initiated, or takes effect after the
        decision.

        Uses the supplied route, or selects GOVERNANCE_CONSTITUTIONAL when a
        BIRTHING event is effective by effective_at and both received and
        recorded by received_at; otherwise selects CIRCLE_FOUNDING.

        For CIRCLE_FOUNDING before Birthing, requires the applicant's identity
        to match the actor, refusing a mismatch as SBGGovernanceRefused with
        E_NOT_APPLICANT and its authored message before anything is written,
        then calls _validate_seating, including its occupancy checks for
        either outcome.

        For GOVERNANCE_CONSTITUTIONAL after Birthing, calls
        _validate_constitutional_seating with governance required to be enabled
        and occupancy checked only for ACCEPT.

        Calls authority qualification after these branch checks. Rewords
        SBG_SEAT_ROUTE_UNAVAILABLE denials. If qualification succeeds without a
        validated seating context, raises RuntimeError.

        Writes the admission decision with the LO as institutional actor in
        TRANSPORT mode, the actor as deciding and entering identity,
        self_decision from the applicant/actor identity comparison, and
        settings_basis rule:<uuid>@<version>#<seat>. Creates a participation only
        for ACCEPT, then records a receipt citing the authority decision and
        returns GovernanceSeatDecisionResult.
        """
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
        return self._decide_governance_seat_at(
            command,
            organism_uuid=organism_uuid,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            request_uuid=request_uuid,
            received_at=received_at,
            effective_at=effective_at,
            verify_after_actor_lock=None,
        )

    def _execute_decide_governance_seat(
        self,
        command: DecideGovernanceSeatCommand,
        bound: BoundRequest,
    ) -> GovernanceSeatDecisionResult:
        """Execute a verified decision stage at its bound receive time.

        This private executor serves DECIDE_GOVERNANCE_SEAT and ADMIT_STAGE;
        the public clock route remains unchanged until PKT-CLK-3.
        """
        if command.request_uuid is None:
            raise ValueError("an existing request is required for the decision stage")
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
        received_at = bound.received_at
        effective_at = (
            received_at if command.effective_at is None
            else _timestamp(command.effective_at, "effective_at")
        )
        if effective_at > received_at:
            raise ValueError("effective_at must not be later than received_at")
        return self._decide_governance_seat_at(
            command,
            organism_uuid=organism_uuid,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            request_uuid=request_uuid,
            received_at=received_at,
            effective_at=effective_at,
            verify_after_actor_lock=lambda actor: bound.verify(
                command,
                actor,
                frozenset(
                    {
                        IntakeEntry.DECIDE_GOVERNANCE_SEAT,
                        ChildEntry.ADMIT_STAGE,
                    }
                ),
                self._alias,
            ),
        )

    def _orchestrate_compound_seating(
        self,
        command: SeatFoundingGovernanceCircleCommand | DecideGovernanceSeatCommand,
        bound: BoundRequest,
        recipe: _CompoundRecipe,
    ) -> GovernanceSeatingResult | GovernanceSeatDecisionResult:
        """Execute two verified stages without enclosing their transactions.

        The parent is verified once, and each child is independently verified
        by its stage executor. The public clock routes remain unchanged until
        PKT-CLK-3 redirects them.
        """
        from src.intevia.services.command_intake import _require_not_in_atomic_block

        _require_not_in_atomic_block(self._alias)
        parent = bound.verify_content(command, recipe.expected_entry)
        organism_uuid = _canonical_uuid(command.living_organism_uuid, "living_organism_uuid")
        interaction_key = _canonical_key(command.idempotency_key, "idempotency_key", 120)
        request_key, request_reference, admission_key, admission_reference = (
            _compound_stage_identifiers(organism_uuid, interaction_key)
        )
        request_command = recipe.stage1(command, request_reference, request_key)
        request_bound = parent.child(
            ChildEntry.REQUEST_STAGE,
            command=request_command,
        )
        request_result = self._execute_request_governance_seat(
            request_command,
            request_bound,
        )
        decision_command = DecideGovernanceSeatCommand(
            credential=command.credential,
            living_organism_uuid=organism_uuid,
            request_reference=admission_reference,
            idempotency_key=admission_key,
            outcome=recipe.stage2_outcome(command),
            route=recipe.stage2_route,
            request_uuid=request_result.request_uuid,
            effective_at=command.effective_at,
        )
        stage_context = ChildEntry.ADMIT_STAGE
        decision_bound = parent.child(
            stage_context,
            command=decision_command,
        )
        try:
            decision_result = self._execute_decide_governance_seat(
                decision_command,
                decision_bound,
            )
        except SBGGovernanceUnavailable as exc:
            open_request = self._request_open_sentence(
                command.seat_id,
                stage_context=stage_context,
            )
            raised = SBGGovernanceUnavailable(f"{exc} {open_request}")
            setattr(raised, "request_uuid", request_result.request_uuid)
            setattr(raised, "stage_context", stage_context)
            raise raised from exc
        except SBGGovernanceRefused as exc:
            open_request = self._request_open_sentence(
                command.seat_id,
                stage_context=stage_context,
            )
            raised = SBGGovernanceRefused(
                exc.refusal_code,
                f"{exc} {open_request}",
                request_uuid=request_result.request_uuid,
            )
            setattr(raised, "stage_context", stage_context)
            raise raised from exc
        except S015AuthorityDenied as exc:
            open_request = self._request_open_sentence(
                command.seat_id,
                stage_context=stage_context,
            )
            raised = S015AuthorityDenied(
                f"{exc} {open_request}",
                refusal_code=exc.refusal_code,
                disclosed_refusal_code=exc.disclosed_refusal_code,
                authority_decision_reference=exc.authority_decision_reference,
                request_uuid=request_result.request_uuid,
            )
            setattr(raised, "stage_context", stage_context)
            raise raised from exc
        except IntegrityError as exc:
            refusal = _controlled_integrity_refusal(
                exc,
                _ADMISSION_GUARD_REFUSAL_CODES,
                detail=command.seat_id,
                act="Nobody was seated",
            )
            if refusal is None:
                setattr(exc, "stage_context", stage_context)
                raise
            open_request = self._request_open_sentence(
                command.seat_id,
                stage_context=stage_context,
            )
            raised = SBGGovernanceRefused(
                refusal.refusal_code,
                f"{refusal} {open_request}",
                request_uuid=request_result.request_uuid,
            )
            setattr(raised, "stage_context", stage_context)
            raise raised from exc
        except Exception as exc:
            setattr(exc, "stage_context", stage_context)
            raise
        if recipe.post_check is not None:
            recipe.post_check(decision_result)
        return recipe.result_from(decision_result)

    def _decide_governance_seat_at(
        self,
        command: DecideGovernanceSeatCommand,
        *,
        organism_uuid: UUID,
        request_reference: str,
        idempotency_key: str,
        request_uuid: UUID,
        received_at: datetime,
        effective_at: datetime,
        verify_after_actor_lock: Callable[[Identity], None] | None,
    ) -> GovernanceSeatDecisionResult:
        """Run the shared decision-stage body at an explicit time.

        The public clock route supplies no verifier and retains its existing
        compound branch. Bound stage executors supply the verified request time;
        replay viewers still use their independent present-time clock read.
        """
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
            if verify_after_actor_lock is not None:
                verify_after_actor_lock(actor)
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
                        S015AuthorityRefusalCode.E_NOT_APPLICANT,
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
        bound: BoundRequest,
    ) -> GovernanceActResult:
        """Run an adoption or a GOVERNANCE Circle creation in one authority_atomic scope.

        Before the scope, shape validation and admission are performed by the
        public caller; the seat routes acquire the same intake boundary when
        CLK3-3 activates them. For Circle creation, derives circle_uuid from
        the LO and idempotency key; fingerprints the act (schema
        sbg-governance-act-v1).

        Within the scope, locks the actor, verifies the bound request and takes
        the command mutex; returns a matching replay; locks the registered LO
        and actor membership context; builds the authority target, including
        the adoption source at the bound time; then qualifies the act and
        rewords a denial when an authored explanation exists. After
        qualification it validates adoption, writes the adoption or Circle,
        and stores a receipt citing the authority decision and result lineage.
        Raises ValueError for any other action.
        """
        request_reference = _canonical_key(command.request_reference, "request_reference", 128)
        idempotency_key = _canonical_key(command.idempotency_key, "idempotency_key", 120)
        received_at = bound.received_at
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
        with authority_atomic(self._alias, received_at=bound.received_at):
            actor = _lock_actor(
                database_alias=self._alias,
                credential=command.credential,
                unavailable=SBGGovernanceUnavailable,
            )
            entry = {
                OrganismAction.ADOPT_GOVERNING_INSTRUMENT: (
                    IntakeEntry.ADOPT_GOVERNING_INSTRUMENT
                ),
                OrganismAction.CREATE_DORMANT_CIRCLE: (
                    IntakeEntry.CREATE_GOVERNANCE_CIRCLE
                ),
            }.get(action)
            if entry is None:
                raise ValueError(f"unsupported governance act: {action.value}")
            bound.verify(command, actor, entry, self._alias)
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
                    opening_end=(
                        "."
                        if action
                        in {
                            OrganismAction.ADOPT_GOVERNING_INSTRUMENT,
                            OrganismAction.CREATE_DORMANT_CIRCLE,
                        }
                        else " and nothing changed."
                    ),
                    tail=(
                        _intake_tail(_TAIL_REFUSAL_RECORDED, received_at)
                        if action
                        in {
                            OrganismAction.ADOPT_GOVERNING_INSTRUMENT,
                            OrganismAction.CREATE_DORMANT_CIRCLE,
                        }
                        else ""
                    ),
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
                    opening_end="; no governed record was written.",
                    tail=_intake_tail(_TAIL_NOT_WRITTEN, received_at),
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
        """Serve an adoption or Circle-creation result from its receipt.

        Look up the actor's receipt for the action and idempotency key using
        select_for_update; return None when no receipt is found.

        Raise ValueError if the request reference or payload fingerprint differs.
        Rebuild GovernanceActResult from the stored payload, converting
        KeyError, TypeError or ValueError during reconstruction into ValueError
        with the receipt-mismatch message. Other exceptions are not converted here.

        Return the rebuilt result through serve_replay.
        """
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
            # This clock read supplies the present-time access check when serving the result; it does not replace the bound request's receipt time.
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
        """Return the arrangement source for an act.

        LO_PRESCRIPTION when a BIRTHING event of the LO has effective_at at or
        before effective_at and recorded_at at or before the recorded_at bound;
        otherwise FOUNDING_BODY_PRESCRIPTION (SB-G build commission v1.0,
        section 0, HD-2 A).
        """
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
        """Return the LO's folded state at (effective_at, recorded_at), or None.

        Calls the database function s015_fold_effective_state for
        core_livingorganism; raises RuntimeError if it returns no row. The fold's
        rules are defined by that function, not here.
        """
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
        """Validate seating conditions used by the CIRCLE_FOUNDING branch.

        Returns (circle, rule). Passes "Nobody was seated" as act to refusals.

        Refuses SBG_MEMBER_NOT_ELIGIBLE when membership is missing, belongs to
        another LO, or does not fold to ACTIVE at effective_at with received_at
        as the knowledge bound. Refuses SBG_LO_NOT_ACTIVE when the LO folds to
        DORMANT or CLOSED. If no BIRTHING event is effective by effective_at and
        both received and recorded by received_at, requires an effective
        configuration with governance_enabled True.

        Locks the LO's GOVERNANCE Circle. Refuses SBG_GOVERNANCE_CIRCLE_MISSING
        if it is absent, differs from the payload's Circle, or has no
        CREATE_DORMANT event effective by effective_at and recorded by
        received_at.

        Considers arrangement versions recorded by received_at. Refuses
        SBG_ARRANGEMENT_MISSING if none exist. Selects the requested rule's
        latest effective version at effective_at; refuses SBG_SEAT_ABSENT if
        that version or its requested seat is absent.

        Refuses SBG_ARRANGEMENT_INCOMPLETE for an invalid seat list or an
        unparseable seat start date. A timezone-naive start date, or one later
        than effective_at, produces SBG_SEAT_ABSENT. Also refuses
        SBG_SEAT_ABSENT if a version of the same rule effective after the act,
        but recorded by received_at, lacks a valid seat list containing the seat.

        Checks participations and their endings recorded by received_at.
        Refuses SBG_SEAT_OCCUPIED for the same rule and seat, or
        SBG_ALREADY_SEATED for the same person in this Circle, when a matching
        participation has no ending or its selected ending is after effective_at.

        Raises SBGGovernanceUnavailable if the LO's folded state is None, and
        RuntimeError if the membership fold returns no row. Does not check the
        seat's eligibility value or constitutional admission route, and does
        not itself refuse founding-route use after Birthing.
        """
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
        """Validate a seat for a request or GOVERNANCE_CONSTITUTIONAL decision.

        Returns (circle, rule). Applies the membership, LO-state, Circle,
        arrangement, seat, start-date and future-effective-version checks
        described by _validate_seating. Additionally refuses
        SBG_ELIGIBILITY_UNSUPPORTED unless the seat's eligibility is LO_MEMBER.

        Passes act, defaulting to "Nobody was seated", to _refuse; the authored
        message helper determines whether each refusal's text uses that value.

        When require_birthing is true, refuses SBG_SEAT_ROUTE_UNAVAILABLE
        unless a BIRTHING event is effective by effective_at and both received
        and recorded by received_at. When require_enabled is true, requires an
        effective configuration with governance_enabled True.

        When require_supported_route is true, refuses
        SBG_ARRANGEMENT_INCOMPLETE for an unknown admission route or a deciding
        body that does not match it. Of the valid combinations, permits only
        APPOINTMENT by office:LO_COORDINATOR; the others produce
        SBG_SEAT_ROUTE_UNAVAILABLE.

        When check_occupancy is true, applies the SBG_SEAT_OCCUPIED and
        SBG_ALREADY_SEATED checks described by _validate_seating.

        The seat-request caller sets all four flags to False. This helper does
        not check whether the GOVERNANCE Circle has ever been activated.
        """
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
        opening_end: str = " and nothing changed.",
        tail: str = "",
    ) -> None:
        """Validate an arrangement adoption before it is written.

        Refuses with SBG_ARRANGEMENT_INCOMPLETE when effective_from is earlier than
        the act's effective_at, or when the version does not continue the LO's
        GOVERNANCE_CONSTITUTION sequence (version 1 for the first; otherwise the
        latest version recorded by received_at plus one, under the same rule_uuid).
        Then validates the arrangement body and, when an earlier version exists,
        refuses a version that leaves out a held seat (_refuse_if_held_seat_dropped).
        """
        rule_uuid = UUID(payload["rule_uuid"])
        version = payload["version"]
        body = payload["body"]
        effective_from = payload["effective_from"]
        if effective_from < effective_at:
            self._refuse(
                S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
                "the arrangement cannot take effect before it is adopted",
                opening_end=opening_end,
                tail=tail,
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
                opening_end=opening_end,
                tail=tail,
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
                opening_end=opening_end,
                tail=tail,
            )
        self._validate_arrangement_body(
            body,
            opening_end=opening_end,
            tail=tail,
        )
        if current is not None:
            self._refuse_if_held_seat_dropped(
                organism=organism,
                current_rule=current,
                next_body=body,
                effective_from=effective_from,
                received_at=received_at,
                opening_end=opening_end,
                tail=tail,
            )

    def _validate_arrangement_body(
        self,
        body: dict[str, object],
        *,
        opening_end: str = " and nothing changed.",
        tail: str = "",
    ) -> None:
        """Validate a GOVERNANCE_CONSTITUTION body at adoption.

        Requires a dict containing exactly seats, officer_roles and
        election_schedule. officer_roles must be an empty list and
        election_schedule must be None. seats must be a nonempty list of dicts
        with unique seat identifiers.

        Each seat must contain exactly seat_id, eligibility, admission_route,
        deciding_body, term, vacancy_rule and effective_from. Requires a
        seat_id matching [a-z0-9_-]{1,64}, eligibility LO_MEMBER, term None and
        vacancy_rule BY_SEAT_ROUTE.

        Requires a string admission route and matching string deciding body:
        ELECTION uses body:LO_MEMBERSHIP; COOPTION uses body:GOVERNANCE;
        APPOINTMENT uses office:LO_COORDINATOR or external: followed by 1 to
        120 characters; EX_OFFICIO uses office: followed by a canonical UUID.

        Requires effective_from to match the timestamp format checked here,
        with a YYYY-MM-DDT prefix and Z or a signed HH:MM offset, and to parse
        as a timezone-aware datetime. Does not compare it with the act time.

        Missing fields, malformed structures, duplicate or invalid seat
        identifiers, invalid route/body combinations and invalid timestamps
        produce SBG_ARRANGEMENT_INCOMPLETE. Extra fields, nonempty or non-list
        officer_roles, a non-None election_schedule or term, and a vacancy_rule
        other than BY_SEAT_ROUTE produce SBG_ARRANGEMENT_FIELD_UNSUPPORTED.
        Eligibility other than the string LO_MEMBER produces
        SBG_ELIGIBILITY_UNSUPPORTED.

        Accepting a route/body combination here does not establish that its
        admission route is available in this release.

        Source: SB-G build commission v1.0, section 5.2.1, as amended by
        amendment 2; D §5.4.
        """
        def refuse(code: S015AuthorityRefusalCode, detail: str | None) -> NoReturn:
            self._refuse(
                code,
                detail,
                opening_end=opening_end,
                tail=tail,
            )

        required_fields = {"seats", "officer_roles", "election_schedule"}
        if type(body) is not dict or not required_fields.issubset(body):
            refuse(
                S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
                "it has no seats"
                if type(body) is not dict or "seats" not in body
                else "a required arrangement field is missing",
            )
        if set(body) - required_fields:
            refuse(
                S015AuthorityRefusalCode.SBG_ARRANGEMENT_FIELD_UNSUPPORTED,
                "a field this release does not record",
            )
        if (
            type(body["officer_roles"]) is not list
            or body["officer_roles"]
            or body["election_schedule"] is not None
        ):
            refuse(
                S015AuthorityRefusalCode.SBG_ARRANGEMENT_FIELD_UNSUPPORTED,
                "officer roles"
                if type(body["officer_roles"]) is not list or body["officer_roles"]
                else "an election schedule",
            )
        seats = body["seats"]
        if type(seats) is not list or not seats:
            refuse(
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
            refuse(
                S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
                f"seat {duplicate} is duplicated",
            )
        for seat in seats:
            if type(seat) is not dict:
                refuse(
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
                refuse(
                    S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
                    "a seat is incomplete",
                )
            if set(seat) - required_seat_fields:
                refuse(
                    S015AuthorityRefusalCode.SBG_ARRANGEMENT_FIELD_UNSUPPORTED,
                    "a field this release does not record",
                )
            if seat.get("term") is not None:
                refuse(
                    S015AuthorityRefusalCode.SBG_ARRANGEMENT_FIELD_UNSUPPORTED,
                    f"a term for seat {seat.get('seat_id')}",
                )
            if seat.get("vacancy_rule") != "BY_SEAT_ROUTE":
                refuse(
                    S015AuthorityRefusalCode.SBG_ARRANGEMENT_FIELD_UNSUPPORTED,
                    "a vacancy rule other than filling the seat by its own route",
                )
            seat_id = seat.get("seat_id")
            route = seat.get("admission_route")
            deciding_body = seat.get("deciding_body")
            if type(seat_id) is not str or _SEAT_ID.fullmatch(seat_id) is None:
                refuse(
                    S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
                    "a seat has no valid seat identifier",
                )
            if type(seat.get("eligibility")) is not str or seat["eligibility"] != "LO_MEMBER":
                refuse(
                    S015AuthorityRefusalCode.SBG_ELIGIBILITY_UNSUPPORTED,
                    seat_id,
                )
            if type(route) is not str or type(deciding_body) is not str:
                refuse(
                    S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
                    "seat route and deciding body must be strings",
                )
            if route not in _ROUTE_BODIES:
                refuse(
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
                refuse(
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
                refuse(
                    S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
                    f"seat {seat_id} has no valid start date",
                )
            try:
                seat_effective_from = datetime.fromisoformat(
                    effective_from.replace("Z", "+00:00")
                )
            except ValueError:
                refuse(
                    S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
                    f"seat {seat_id} has no valid start date",
                )
            if seat_effective_from.tzinfo is None or seat_effective_from.utcoffset() is None:
                refuse(
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
        opening_end: str = " and nothing changed.",
        tail: str = "",
    ) -> None:
        """Refuse a new arrangement version that leaves out a seat still held.

        For each participation recorded by received_at in the LO's GOVERNANCE
        Circles under the current rule whose seat_id the next version omits:
        refuses with SBG_VERSION_DROPS_HELD_SEAT when no ending is recorded by
        received_at, or when the latest recorded ending's effective_at is later
        than the new version's effective_from (SB-G build commission v1.0,
        section 9).
        """
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
                    opening_end=opening_end,
                    tail=tail,
                )

    @staticmethod
    def _refuse(
        code: S015AuthorityRefusalCode,
        detail: str | None,
        *,
        act: str = "No arrangement was adopted",
        organism_state: str | None = None,
        opening_end: str = " and nothing changed.",
        tail: str = "",
    ) -> NoReturn:
        """Raise SBGGovernanceRefused using the selected authored explanation.

        Pass code, detail, act and organism_state to _authored_refusal_message.
        act defaults to "No arrangement was adopted".

        Raise RuntimeError if the message builder returns None for the supplied
        code and context; otherwise raise SBGGovernanceRefused with that message.
        """
        message = _authored_refusal_message(
            code,
            detail,
            act=act,
            organism_state=organism_state,
            opening_end=opening_end,
            tail=tail,
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
        """Write an adopted arrangement version and its LO event; return the result.

        Creates the GOVERNANCE_CONSTITUTION rule version (source from _source_at,
        entered by the actor, authority_reference naming the new LO event), then
        the LO event ADOPT_GOVERNING_INSTRUMENT with evidence rule:<uuid>@<version>
        and the authority decision's reference.
        """
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
        """Create a dormant GOVERNANCE Circle and its sequence-1 state event.

        Creates the Circle with no head event and founding_reference set to
        the authority decision's reference. Inserts a CREATE_DORMANT event
        with resulting state DORMANT, the LO as institutional actor in
        TRANSPORT mode, and the supplied actor Identity as enterer.

        The event carries evidence living-organism:<organism_uuid>, the
        authority-decision reference, the request reference, idempotency key,
        payload fingerprint and a lineage reference from _lineage.

        Calls s015_refresh_governed_cache for the Circle, then reads the
        inserted event and returns GovernanceActResult. Cache-refresh
        behaviour is defined by that database function.

        Source: SB-G build commission v1.0, section 6, Circle creation.
        """
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
        """Insert the next same-state event on the LO's event chain.

        Selects the LO event with the highest sequence using select_for_update.
        Raises RuntimeError if no predecessor is found, or if the subsequent
        commitment lookup finds no row or a missing l1 commitment.

        Inserts the next sequence with the predecessor's id, sequence and
        l1 commitment. Uses the predecessor's resulting state as both prior
        and resulting state. Records the LO as institutional actor in
        TRANSPORT mode and the supplied actor Identity as enterer.

        Carries the supplied evidence and authority-decision references,
        request reference, idempotency key and payload fingerprint, together
        with a lineage reference from _lineage.

        Calls s015_refresh_governed_cache for the LO and returns the inserted
        event's id and sequence. Cache-refresh behaviour is defined by that
        database function.

        Used for the same-state adoption event specified by the SB-G build
        commission v1.0, section 6.
        """
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
    """Return body unchanged after an exact-dict and encoding check.

    Require type(body) is dict; raise TypeError otherwise.
    Call canonical_json_bytes to check encoding, allowing its errors
    to propagate. Arrangement-field validation is performed elsewhere.
    """
    if type(body) is not dict:
        raise TypeError("body must be an object")
    canonical_json_bytes(body)
    return body


def _authority_payload(
    action: OrganismAction,
    payload: dict[str, object],
) -> dict[str, object]:
    """Return the action-specific fields of an SB-G authority target.

    Supports REQUEST_GOVERNANCE_SEAT, ADOPT_GOVERNING_INSTRUMENT,
    CREATE_DORMANT_CIRCLE and ADMIT_CIRCLE_PARTICIPANT.
    Raises ValueError for an unsupported action.

    For adoption, return an empty source placeholder and canonicalise
    effective_from. In the adoption execution path, _execute replaces
    the placeholder using _source_at before fingerprinting the target
    and requesting authority qualification.

    For admission, include constitutional_route, deciding_party and
    outcome only for GOVERNANCE_CONSTITUTIONAL.
    """
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
    """Return an authority target: the action, the LO, and the action's fields
    from _authority_payload.
    """
    return {
        "action": action.value,
        "organism_uuid": organism_uuid,
        **_authority_payload(action, payload),
    }


def _timestamp(value: object, name: str) -> datetime:
    """Return value unchanged if it is an exact, timezone-aware datetime.

    Require type(value) is datetime, a non-None tzinfo and a non-None
    utcoffset(). Raise TypeError naming the supplied name when one of
    these checks fails.
    """
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise TypeError(f"{name} must be timezone-aware")
    return value


def _canonical_uuid_string(value: str) -> bool:
    """Return whether value equals its canonical UUID string representation.

    Evaluate str(UUID(value)) == value. Return False if evaluation raises
    ValueError or TypeError; other exceptions propagate.

    Intended for string input; this helper does not first enforce a
    string type.
    """
    try:
        return str(UUID(value)) == value
    except (TypeError, ValueError):
        return False


def _lineage(action: str, event_uuid: UUID, subject_uuid: UUID, fingerprint: str) -> str:
    """Return a lineage reference: "s015l1:" followed by the SHA-256 of the
    canonical JSON of the action, the event UUID, the payload fingerprint and
    the subject UUID.
    """
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
