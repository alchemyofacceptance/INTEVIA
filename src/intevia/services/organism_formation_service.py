"""Founding, phase advance and Birthing of a Living Organism (LO).

Three S015 command services:
- OrganismFormationService founds an LO (FOUND_LIVING_ORGANISM).
- OrganismPhaseAdvanceService reports readiness for, and records,
  the phase advances Conceiving to Germinating and Germinating to
  Birthing (TRANSITION_LIVING_ORGANISM/PHASE_ADVANCE).
- OrganismBirthingService records Birthing
  (TRANSITION_LIVING_ORGANISM/BIRTHING).

Also holds the SB-G readiness message helpers that phase advance,
Birthing and organism_application_service use for GOVERNANCE
readiness refusals.

Founding, phase advance and Birthing admit their requests through the
durable intake and run as of the intake's received_at.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import unicodedata
from typing import Callable
from uuid import UUID, uuid4

from django.conf import settings
from django.contrib.auth.models import User
from django.db import IntegrityError, connections, transaction
from django.utils.text import slugify

from core.models import (
    AuthorityBasis,
    AuthorityPrincipal,
    ContextualRoleAssignment,
    EssentialCoverageCase,
    EssentialCoverageRequirement,
    GovernedRule,
    Identity,
    LivingOrganism,
    LivingOrganismEvent,
    LOApplicationQuestionVersion,
    LOApplicationSettingsVersion,
    LOBirthingSnapshot,
    LOFoundingContentVersion,
    LOGovernanceConfigurationVersion,
    OrganismCommandReceipt,
    OrganismMembership,
    OrganismRoleDefinition,
    PlatformTermsAcceptance,
    PlatformTermsVersion,
)
from core.organism_contract import OrganismAction
from src.intevia.services.organism_application_authority import (
    ProductionApplicationAuthorityProvider,
    _effective_version,
)
from src.intevia.services.organism_authority import (
    SB_BIRTHING_ELECTORATE_EMPTY_REASON,
    S015Authority,
    S015AuthorityDenied,
    S015AuthorityRefusalCode,
    S015PostFoundingAuthorityRequest,
    S015PreFoundingAuthorityRequest,
    QualifiedS015AuthorityDecision,
    authority_target_fingerprint,
    canonical_json_bytes,
    canonical_timestamp,
)
from src.intevia.services.organism_command_support import (
    ReplayResultType,
    ReplayViewer,
    _intake_tail,
    S015CommandReplayMismatch,
    _canonical_uuid,
    _canonical_key,
    _command_payload,
    _empty_anchor_fingerprint,
    _lock_actor,
    _lock_actor_membership_context,
    _lock_registered_organism,
    _resulting_temporal_basis,
    _validate_timestamp,
    acquire_organism_command_mutex,
    authority_atomic,
    serve_replay,
)
from src.intevia.services.command_intake import BoundRequest, IntakeEntry
from src.intevia.services.organism_formation_authority import (
    founding_acceptance_uuid,
    founding_authority_request,
)
from src.intevia.services.organism_membership_service import _insert_membership_transition_row
from src.intevia.services.organism_entitlement_service import (
    OrganismEntitlementService,
    _is_constraint_violation,
    _is_entitlement_granted_guard_failure,
)


_COMMAND_DOMAIN = b"INTEVIA:S015:FOUND_LIVING_ORGANISM:COMMAND:v1\x00"
_LINEAGE_DOMAIN = b"INTEVIA:S015:FOUND_LIVING_ORGANISM:LINEAGE:v1\x00"
_ACTION = OrganismAction.FOUND_LIVING_ORGANISM
_NAME_CONSTRAINT = "core_livingorganism_slug_key"
_FOUNDING_TAIL_NOT_WRITTEN = (
    "No governed record was written; INTEVIA kept a record that this request was received at "
    "{received_at}; sent again unchanged, it is answered as of that time."
)
_TAIL_REFUSAL_RECORDED = (
    "INTEVIA recorded this refusal and that the request was received at {received_at}; "
    "nothing else was saved."
)
# Stop 3 rows of the G1 entries (PKT-CLK-3 CLK3-2): a technical failure after
# admission (R-FORM-13, -14, -24), the snapshot mismatch (R-FORM-25) and the
# Birthing election denial (F3-01). {received_at} is rendered by _intake_tail.
# R-FORM-25 and F3-01 as amended, and F3-02 and R-FORM-26-B, are amendment A3's
# texts as the Human Governor adopted them on 25 Oct 2026.
_TAIL_TECHNICAL_RETRY = (
    "INTEVIA kept a record that this request was received at {received_at}; the same "
    "request can be sent again and is judged as of that time. If it persists, report it."
)
_PHASE_TECHNICAL_FAILURE_TEMPLATE = (
    "PHASE_ADVANCE could not be completed because of a technical problem; no governed "
    "record was written. " + _TAIL_TECHNICAL_RETRY
)
_BIRTHING_TECHNICAL_FAILURE_TEMPLATE = (
    "BIRTHING could not be completed because of a technical problem; no governed record "
    "was written. " + _TAIL_TECHNICAL_RETRY
)
_SNAPSHOT_MISMATCH_TEMPLATE = (
    "The LO was not born, and nothing from this request was saved except a record that it "
    "was received at {received_at}. The LO's settings may have changed after the request was "
    "received, so the constitutional snapshot prepared for it no longer matches them. This is "
    "a record inconsistency, not a decision against the LO. To try again, make a new Birthing "
    "request: sending this same request again is still judged as of when it was first "
    "received. If a new request fails the same way, please report this problem."
)
_ELECTION_DENIED_TEMPLATE = (
    "The LO was not born: the entitlement election proposed with this Birthing was refused "
    "({refusal_code}). Records kept from this attempt: the Birthing's qualification, the "
    "constitutional snapshot prepared for it, the election's refusal, and that this request "
    "was received at {received_at}. No LO event, no election, no grant and no receipt were "
    "recorded; the LO remains unborn. Sending this request again, or making a new Birthing "
    "request, cannot complete this Living Organism's (LO) Birthing. A software problem left "
    "a constitutional snapshot from the unsuccessful attempt, which blocks further attempts. "
    "Please report this problem; this LO's Birthing cannot complete until the problem is "
    "repaired."
)
# F3-02 (class T): a later Birthing of the LO meets the snapshot left by a refused election.
_ORPHAN_SNAPSHOT_TEMPLATE = (
    "The LO was not born. A software problem left a constitutional snapshot from an earlier, "
    "unsuccessful Birthing attempt, which blocks further attempts; this is not a decision "
    "against the LO. Nothing from this request was saved except a record that it was "
    "received at {received_at}. Sending this request again, or making a new Birthing "
    "request, cannot complete this LO's Birthing. Please report this problem; this LO's "
    "Birthing cannot complete until the problem is repaired."
)
# R-FORM-26-B (class B, as adopted 25 Oct 2026): the LO already has a BIRTHING event that this
# request's qualification did not see (completed after receipt, or effective after the request's
# effective time).
_BIRTHING_OVERTAKEN_TEMPLATE = (
    "The LO was not born again: it was born on {born_date}. An LO is born once, so nothing "
    "more is needed. Nothing from this request was saved except a record that it was "
    "received at {received_at}."
)
# The SB-G guard arm (class B, A3 §7.4): R-FORM-31a-f and R-FORM-32a, -32b, -32c, -32e, -32f.
_ROLLED_BACK_OPENING_END = (
    ", and nothing from this request was saved except a record that it was received at "
    "{received_at}. The LO's governance settings changed after the request was received."
)
_TAIL_RESEND_ORIGINAL_TIME = (
    "Sending this same request again is still judged as of when it was first received, "
    "before the change."
)
_BIRTHING_SNAPSHOT_UNIQUE = "core_lobirthingsnapshot_living_organism_id_uniq"
_SBG_PHASE_GUARD_CODES = (
    S015AuthorityRefusalCode.SBG_GOVERNANCE_CIRCLE_MISSING,
    S015AuthorityRefusalCode.SBG_ARRANGEMENT_MISSING,
    S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
    S015AuthorityRefusalCode.SBG_ARRANGEMENT_FIELD_UNSUPPORTED,
    S015AuthorityRefusalCode.SBG_ELIGIBILITY_UNSUPPORTED,
)
_SBG_BIRTHING_GUARD_CODES = _SBG_PHASE_GUARD_CODES + (
    S015AuthorityRefusalCode.SBG_NO_INITIAL_PARTICIPATION_ROUTE,
)
# The corrective step of each SB-G text names a new request for the operation
# (adopted 25 Oct 2026): a resend is judged as of its original receipt time.
_SBG_NEW_REQUEST = {
    "BIRTHING": "make a new Birthing request",
    "PHASE_ADVANCE": "make a new request to advance the phase",
    "ENABLING": "make a new request to switch GOVERNANCE on",
}


def _sbg_guard_refusal_code(
    error: IntegrityError,
    codes: tuple[S015AuthorityRefusalCode, ...],
) -> S015AuthorityRefusalCode | None:
    """Return the SB-G code a write-time guard raised, or None.

    Walk the error's __cause__ chain. A cause matches when it carries
    SQLSTATE 23000 (as sqlstate or pgcode) and a diagnostic whose primary
    message equals the value of one of codes; return that code. The
    message text of the error itself is not inspected (A3 §7.4).
    """
    cause = error.__cause__
    while cause is not None:
        state = getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)
        primary = getattr(getattr(cause, "diag", None), "message_primary", None)
        if state == "23000":
            for code in codes:
                if primary == code.value:
                    return code
        cause = cause.__cause__
    return None
_SBG_ROUTE_BODIES = {
    "ELECTION": {"body:LO_MEMBERSHIP"},
    "COOPTION": {"body:GOVERNANCE"},
    "APPOINTMENT": {"office:LO_COORDINATOR"},
}


def _sbg_arrangement_condition(
    *,
    database_alias: str,
    organism: LivingOrganism,
    effective_at: datetime,
    recorded_at: datetime,
    code: S015AuthorityRefusalCode,
) -> str:
    """Name a condition for an already selected SB-G refusal.

    Read the LO's GOVERNANCE_CONSTITUTION rule effective by
    effective_at and recorded by recorded_at. Choose the latest
    by effective time, recorded time and version, in that order.

    For SBG_ARRANGEMENT_FIELD_UNSUPPORTED, look first for
    unsupported officer roles, then an election schedule,
    then a seat term or a vacancy rule other than BY_SEAT_ROUTE.
    A non-dict body gives a generic phrase. Missing or non-list
    officer_roles counts as unsupported. The seats value is
    iterated without a list check; a null value can raise
    TypeError instead of returning a phrase.

    For SBG_ELIGIBILITY_UNSUPPORTED, find the first dict seat
    whose eligibility is not LO_MEMBER, in a dict body's list
    of seats. Return its seat_id as text, using
    "the requested seat" only when the key is absent.
    Raise RuntimeError if no such seat is found.

    For other codes, check the body, seat list and each seat,
    requiring exact dict and list types. Name the first problem
    with the identifier's string type, admission route,
    deciding body, duplicate identifier or aware start date.
    If every seat passes but none starts by effective_at,
    name that condition; otherwise return a generic phrase.

    This explains a code; it does not decide readiness or
    perform the readiness function's complete validation.
    Database errors and uncaught value errors propagate.
    """
    rule = GovernedRule.objects.using(database_alias).filter(
        scope_kind="LIVING_ORGANISM",
        scope_organism=organism,
        code="GOVERNANCE_CONSTITUTION",
        effective_from__lte=effective_at,
        recorded_at__lte=recorded_at,
    ).order_by("-effective_from", "-recorded_at", "-version").first()
    body = None if rule is None else rule.body
    if code is S015AuthorityRefusalCode.SBG_ARRANGEMENT_FIELD_UNSUPPORTED:
        if type(body) is not dict:
            return "a field this release does not record"
        if type(body.get("officer_roles")) is not list or body.get("officer_roles"):
            return "officer roles"
        if body.get("election_schedule") is not None:
            return "an election schedule"
        for seat in body.get("seats", []):
            if type(seat) is dict:
                seat_id = seat.get("seat_id", "unknown")
                if seat.get("term") is not None:
                    return f"a term for seat {seat_id}"
                if seat.get("vacancy_rule") != "BY_SEAT_ROUTE":
                    return "a vacancy rule other than filling the seat by its own route"
        return "a field this release does not record"
    if code is S015AuthorityRefusalCode.SBG_ELIGIBILITY_UNSUPPORTED:
        if type(body) is dict and type(body.get("seats")) is list:
            for seat in body["seats"]:
                if type(seat) is dict and seat.get("eligibility") != "LO_MEMBER":
                    return str(seat.get("seat_id", "the requested seat"))
        raise RuntimeError("SB-G readiness rejected eligibility without identifying a seat")
    if type(body) is not dict:
        return "the arrangement has no valid body"
    if type(body.get("seats")) is not list:
        return "the arrangement has no valid seat list"
    if not body["seats"]:
        return "it has no seats"
    seen: set[object] = set()
    in_effect = False
    for seat in body["seats"]:
        if type(seat) is not dict:
            return "a seat is incomplete"
        seat_id = seat.get("seat_id")
        route = seat.get("admission_route")
        deciding_body = seat.get("deciding_body")
        if type(seat_id) is not str:
            return "a seat has no valid seat identifier"
        if type(route) is not str or route not in {
            "ELECTION", "COOPTION", "APPOINTMENT", "EX_OFFICIO",
        }:
            return f"seat {seat_id} names no admission route"
        if type(deciding_body) is not str or not deciding_body:
            return f"seat {seat_id} names no deciding body"
        if route in _SBG_ROUTE_BODIES:
            matching_body = deciding_body in _SBG_ROUTE_BODIES[route]
            if route == "APPOINTMENT" and deciding_body.startswith("external:"):
                matching_body = 0 < len(deciding_body[9:]) <= 120
        elif route == "EX_OFFICIO" and deciding_body.startswith("office:"):
            try:
                role_uuid = UUID(deciding_body.removeprefix("office:"))
                matching_body = str(role_uuid) == deciding_body.removeprefix("office:")
            except ValueError:
                matching_body = False
        else:
            matching_body = False
        if not matching_body:
            return f"seat {seat_id}'s deciding body does not match its admission route"
        if seat_id in seen:
            return "it contains duplicate seat identifiers"
        seen.add(seat_id)
        try:
            seat_start = datetime.fromisoformat(seat["effective_from"].replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError, AttributeError):
            return f"seat {seat_id} has no valid start date"
        if seat_start.tzinfo is None or seat_start.utcoffset() is None:
            return f"seat {seat_id} has no valid start date"
        in_effect = in_effect or seat_start <= effective_at
    if not in_effect:
        return "it has no seat in effect at this time"
    return "the arrangement does not satisfy the requirements for a seat in effect at this time"


def _sbg_readiness_message(
    *,
    code: S015AuthorityRefusalCode,
    act: str,
    operation: str,
    database_alias: str,
    organism: LivingOrganism,
    effective_at: datetime,
    recorded_at: datetime,
    opening_end: str = " and nothing changed.",
    tail: str = "",
) -> str | None:
    """Return user-facing text for an SB-G code, or None.

    act supplies the opening clause and opening_end what
    follows it, " and nothing changed." by default, so most
    texts begin with that clause followed by "and nothing
    changed.". tail, when not empty, is appended after one
    space. Cover a missing GOVERNANCE Circle or arrangement,
    an incomplete arrangement, unsupported fields or
    eligibility, and no initial participation route.

    operation names the refused request: "BIRTHING",
    "PHASE_ADVANCE" or "ENABLING" (switching GOVERNANCE on);
    any other value raises ValueError. Each corrective step
    ends with a new request for that operation ("then make a
    new Birthing request"), because a resend is judged as of
    its original receipt time (adopted 25 Oct 2026). For
    ENABLING the missing-Circle reason says that switching
    GOVERNANCE on needs the Circle.

    For incomplete, field and eligibility refusals, obtain
    the condition through _sbg_arrangement_condition using
    effective_at and recorded_at. Its errors propagate.

    The participation-route text offers the pre-Birthing
    next step for BIRTHING; every other operation gets the
    alternative next step. This helper does not
    independently check the lifecycle.

    Every text, the field and eligibility texts included,
    opens with act followed by opening_end.
    """
    if operation not in _SBG_NEW_REQUEST:
        raise ValueError("operation must be BIRTHING, PHASE_ADVANCE or ENABLING")
    new_request = _SBG_NEW_REQUEST[operation]
    opening = f"{act}{opening_end}"

    def with_tail(message: str) -> str:
        return f"{message} {tail}" if tail else message

    if code is S015AuthorityRefusalCode.SBG_GOVERNANCE_CIRCLE_MISSING:
        reason = (
            "Switching GOVERNANCE on needs the LO's GOVERNANCE Circle first."
            if operation == "ENABLING"
            else "GOVERNANCE is on for this LO, so it needs its GOVERNANCE Circle first."
        )
        return with_tail(
            f"{opening} {reason} Create the LO's GOVERNANCE Circle, then {new_request}. "
            "Creating the Circle early in formation avoids this."
        )
    if code is S015AuthorityRefusalCode.SBG_ARRANGEMENT_MISSING:
        return with_tail(
            f"{opening} GOVERNANCE needs an adopted arrangement saying which seats exist, how each "
            "is filled and who decides, and this LO has none in effect. Adopt an arrangement, then "
            f"{new_request}. Adopting it before creating the Circle avoids this."
        )
    if code is S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE:
        condition = _sbg_arrangement_condition(
            database_alias=database_alias,
            organism=organism,
            effective_at=effective_at,
            recorded_at=recorded_at,
            code=code,
        )
        return with_tail(
            f"{opening} The arrangement is incomplete: {condition}. Adopt a corrected version, then "
            f"{new_request}. Every seat needs an admission route and a deciding body that matches it."
        )
    if code is S015AuthorityRefusalCode.SBG_ARRANGEMENT_FIELD_UNSUPPORTED:
        condition = _sbg_arrangement_condition(
            database_alias=database_alias,
            organism=organism,
            effective_at=effective_at,
            recorded_at=recorded_at,
            code=code,
        )
        return with_tail(
            f"{opening} The arrangement includes {condition}, "
            "which this release cannot yet act on. Adopt a version that leaves it out for now, then "
            f"{new_request}; it can be added by a later version once the release that acts on it "
            "is delivered."
        )
    if code is S015AuthorityRefusalCode.SBG_ELIGIBILITY_UNSUPPORTED:
        seat_id = _sbg_arrangement_condition(
            database_alias=database_alias,
            organism=organism,
            effective_at=effective_at,
            recorded_at=recorded_at,
            code=code,
        )
        return with_tail(
            f"{opening} Seat {seat_id} names an eligibility "
            "rule this release cannot check; it can check only \"an active member of the LO\". "
            f"Adopt a version that uses that rule for now, then {new_request}; other eligibility "
            "rules arrive with the slices that can check them."
        )
    if code is S015AuthorityRefusalCode.SBG_NO_INITIAL_PARTICIPATION_ROUTE:
        before_birthing = operation == "BIRTHING"
        next_step = (
            "Take a seat before the LO is born, or adopt a version with a seat the "
            "LO Coordinators appoint. Then make a new Birthing request."
            if before_birthing
            else f"Adopt a version with a seat the LO Coordinators appoint, then {new_request}."
        )
        return with_tail(
            f"{opening} Nobody holds a GOVERNANCE seat yet, and no seat in the arrangement can be "
            "filled in this release: election seats wait for elections, co-option seats wait for an "
            "active GOVERNANCE, and ex officio seats are not yet available. "
            f"{next_step} The arrangement you adopted stays recorded and in effect."
        )
    return None


@dataclass(frozen=True, slots=True)
class FoundLivingOrganismCommand:
    """Inputs to found_living_organism.

    credential is the founder's Django User. name and purpose are
    required; vision, mission and values are optional.
    request_reference and idempotency_key identify the request.
    accepted_terms_version_uuid names the platform-terms version the
    founder accepted, or None. effective_at is when the founding takes
    effect; None means the time the service receives the command.

    There is no received_at field: the durable intake assigns it when the
    request is admitted. found_living_organism validates every field.
    """
    credential: User
    name: str
    purpose: str
    request_reference: str
    idempotency_key: str
    accepted_terms_version_uuid: UUID | None = None
    vision: str | None = None
    mission: str | None = None
    values: str | None = None
    effective_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class FoundLivingOrganismResult:
    """Result of founding an LO.

    Carries the new LO's UUID, the FOUND event's UUID, the receipt's
    lineage reference, and the authority and authority-decision
    references.

    Declares no audience-specific field restrictions. New and replayed
    results are both served through serve_replay, which can still
    withhold authority_reference.
    """
    living_organism_uuid: UUID
    event_uuid: UUID
    lineage_reference: str
    authority_reference: str
    authority_decision_reference: str

    def receipt_payload(self) -> dict[str, str]:
        """Return the receipt form: UUIDs as strings, the other fields as they
        are."""
        return {
            "living_organism_uuid": str(self.living_organism_uuid),
            "event_uuid": str(self.event_uuid),
            "lineage_reference": self.lineage_reference,
            "authority_reference": self.authority_reference,
            "authority_decision_reference": self.authority_decision_reference,
        }

    @classmethod
    def replay_field_audiences(cls, result: FoundLivingOrganismResult) -> dict[str, str]:
        """Return an empty mapping for a result of exactly this class; raise
        TypeError otherwise."""
        if type(result) is not cls:
            raise TypeError("founding replay result has the wrong type")
        return {}


def _result_from_payload(payload: object) -> FoundLivingOrganismResult:
    """Rebuild a founding result from a receipt payload.

    Require a dict of exactly that type, else raise
    S015CommandReplayMismatch. Construct the two UUIDs; missing fields,
    and TypeError or ValueError from construction, become
    S015CommandReplayMismatch. Other exceptions propagate.

    The three references are taken as stored, without checking their
    types or values.
    """
    if type(payload) is not dict:
        raise S015CommandReplayMismatch("completed receipt does not match request")
    try:
        return FoundLivingOrganismResult(
            living_organism_uuid=UUID(payload["living_organism_uuid"]),
            event_uuid=UUID(payload["event_uuid"]),
            lineage_reference=payload["lineage_reference"],
            authority_reference=payload["authority_reference"],
            authority_decision_reference=payload["authority_decision_reference"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise S015CommandReplayMismatch("completed receipt does not match request") from exc


class OrganismFormationService:
    """Found a Living Organism (FOUND_LIVING_ORGANISM).

    A successful founding records its authority decision and, in the
    same transaction:
    - the founder's platform-terms acceptance;
    - the founding authority basis;
    - the LO, version 1 of its founding content and its FOUND event;
    - the founder's ACTIVE membership, with OPEN and ACTIVATE
      transitions;
    - the LO Coordinator role, the founder's ACTIVE assignment to
      it, and its ASSIGN event;
    - the GOVERNANCE coverage requirement, its case and a SATISFIED
      assessment;
    - the receipt.

    This service does not extend _OrganismCommandService.
    found_living_organism admits the request through the durable intake, and
    founding runs as of the intake's received_at; the injected clock gives
    only the replay viewer's present time.
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

    def found_living_organism(
        self,
        command: FoundLivingOrganismCommand,
    ) -> FoundLivingOrganismResult:
        """Admit a founding request and return its served result (D §5.2).

        Raises S015FormationTechnicalFailure inside an atomic block, then
        requires admission state and the active FOUND_LIVING_ORGANISM entry.
        Shape validation and actor resolution precede admission; shape
        refusals such as a missing name or purpose occur before an intake
        row. The admitted request is passed to the executor, which runs
        using its bound received_at. A refusal after admission retains its
        intake record; transaction outcome determines whether authority
        decisions or founding rows persist.
        """
        if connections[self._alias].in_atomic_block:
            raise S015FormationTechnicalFailure(
                "FOUND_LIVING_ORGANISM requires a top-level transaction; an enclosing atomic block "
                "would put founding rows in a savepoint and invalidate founding xmin provenance."
            )
        from src.intevia.services.command_intake import (
            ACTIVE_ENTRIES,
            IntakeAdmissionTechnicalFailure,
            IntakeEntry,
            _require_admission_state,
            _resolve_actor,
            admit_request,
            intake_shape,
        )

        entry = IntakeEntry.FOUND_LIVING_ORGANISM
        _require_admission_state(self._alias, entry)
        if entry not in ACTIVE_ENTRIES:
            raise IntakeAdmissionTechnicalFailure("FOUND_LIVING_ORGANISM is not active")
        shape = intake_shape(entry, command)
        actor_ref = _resolve_actor(self._alias, command.credential)
        bound = admit_request(self._alias, entry, actor_ref, shape)
        return self._execute_found_living_organism(command, bound)

    def _execute_found_living_organism(
        self,
        command: FoundLivingOrganismCommand,
        bound: BoundRequest,
    ) -> FoundLivingOrganismResult:
        """Execute admitted founding as of its bound request (D §5.2).

        Locks the actor, verifies the bound request, validates keys, times
        and command fields, then handles replay, slug availability and
        authority qualification before writing the founding batch. Inactive
        actors, an empty or occupied slug, authored qualification denials
        and a slug-constraint IntegrityError refuse the act.

        S015AuthorityDenied is deferred until the atomic scope exits. If the
        transaction stays committable and its exit succeeds, earlier writes
        commit, including a REFUSED authority decision; a denial alone does
        not prove persistence. A slug IntegrityError from the batch leaves
        the transaction requiring rollback, so the translated denial keeps
        nothing but the separately committed intake row. Errors at
        transaction exit can replace a pending denial or a successful
        result; the outer contention wrapper translates qualifying lock
        errors after exit and performs no retry.
        """
        with authority_atomic(self._alias, received_at=bound.received_at):
            try:
                actor = _lock_actor(
                    database_alias=self._alias,
                    credential=command.credential,
                    unavailable=S015FormationActorUnavailable,
                )
            except S015FormationActorUnavailable as exc:
                inactive_identity = (
                    Identity.objects.using(self._alias)
                    .select_related("credential")
                    .filter(credential=command.credential)
                    .first()
                )
                if inactive_identity is not None and (
                    inactive_identity.access_state != Identity.AccessState.ACTIVE
                    or not inactive_identity.credential.is_active
                ):
                    raise S015AuthorityDenied(
                        "The LO was not founded: your account is not active at the time of this act. "
                        + _intake_tail(_FOUNDING_TAIL_NOT_WRITTEN, bound.received_at),
                        refusal_code=S015AuthorityRefusalCode.E_IDENTITY_INACTIVE,
                        disclosed_refusal_code=S015AuthorityRefusalCode.E_IDENTITY_INACTIVE,
                    ) from exc
                raise
            bound.verify(
                command,
                actor,
                IntakeEntry.FOUND_LIVING_ORGANISM,
                self._alias,
            )
            request_reference = _canonical_key(command.request_reference, "request_reference", 128)
            idempotency_key = _canonical_key(command.idempotency_key, "idempotency_key", 120)
            received_at = self._timestamp(bound.received_at, "received_at")
            effective_at = (
                received_at
                if command.effective_at is None
                else self._timestamp(command.effective_at, "effective_at")
            )
            if effective_at > received_at:
                raise ValueError("effective_at must not be later than received_at")
            name = self._required_text(command.name, "name")
            purpose = self._required_text(command.purpose, "purpose")
            vision = self._optional_text(command.vision, "vision")
            mission = self._optional_text(command.mission, "mission")
            values = self._optional_text(command.values, "values")
            terms_version_uuid = command.accepted_terms_version_uuid
            if terms_version_uuid is not None and type(terms_version_uuid) is not UUID:
                raise TypeError("accepted_terms_version_uuid must be a UUID or None")

            slug = slugify(name)[:90]
            if not slug:
                raise S015AuthorityDenied(
                    "The LO was not founded: a name (and a purpose) is required. "
                    + _intake_tail(_FOUNDING_TAIL_NOT_WRITTEN, bound.received_at),
                    refusal_code=S015AuthorityRefusalCode.LO_NAME_REQUIRED,
                    disclosed_refusal_code=S015AuthorityRefusalCode.LO_NAME_REQUIRED,
                )
            acquire_organism_command_mutex(
                database_alias=self._alias,
                actor_identity_id=actor.identity_id,
                action=_ACTION,
                idempotency_key=idempotency_key,
            )
            fingerprint = self._command_fingerprint(
                actor=actor,
                name=name,
                purpose=purpose,
                vision=vision,
                mission=mission,
                values=values,
                accepted_terms_version_uuid=terms_version_uuid,
                request_reference=request_reference,
                idempotency_key=idempotency_key,
                effective_at=command.effective_at,
            )
            replay = self._attempt_replay(
                actor=actor,
                request_reference=request_reference,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
            )
            if replay is not None:
                return replay
            if LivingOrganism.objects.using(self._alias).filter(slug=slug).exists():
                raise S015AuthorityDenied(
                    "The LO was not founded: that name is already taken by another LO. Choose a different name. "
                    + _intake_tail(_FOUNDING_TAIL_NOT_WRITTEN, bound.received_at),
                    refusal_code=S015AuthorityRefusalCode.LO_NAME_UNAVAILABLE,
                    disclosed_refusal_code=S015AuthorityRefusalCode.LO_NAME_UNAVAILABLE,
                )

            proposed_organism_uuid = uuid4()
            request = founding_authority_request(
                database_alias=self._alias,
                actor_pk=actor.pk,
                actor_identity_id=actor.identity_id,
                actor_access_epoch=actor.access_epoch,
                proposed_organism_uuid=proposed_organism_uuid,
                accepted_terms_version_uuid=terms_version_uuid,
                request_reference=request_reference,
                idempotency_key=idempotency_key,
                effective_at=effective_at,
                received_at=received_at,
            )
            try:
                decision = self._authority.qualify(request)
            except S015AuthorityDenied as exc:
                message = self._authored_founding_refusal_message(
                    exc.refusal_code,
                    received_at=bound.received_at,
                )
                if message is None:
                    raise
                raise S015AuthorityDenied(
                    message,
                    refusal_code=exc.refusal_code,
                    disclosed_refusal_code=exc.disclosed_refusal_code,
                    authority_decision_reference=exc.authority_decision_reference,
                ) from exc
            try:
                return self._write_founding_batch(
                    actor=actor,
                    request=request,
                    decision=decision,
                    name=name,
                    purpose=purpose,
                    vision=vision,
                    mission=mission,
                    values=values,
                    slug=slug,
                    fingerprint=fingerprint,
                    effective_at=effective_at,
                    received_at=received_at,
                )
            except IntegrityError as exc:
                if self._constraint_name(exc) == _NAME_CONSTRAINT:
                    raise S015AuthorityDenied(
                        "The LO was not founded: that name is already taken by another LO. Choose a different name. "
                        + _intake_tail(_FOUNDING_TAIL_NOT_WRITTEN, bound.received_at),
                        refusal_code=S015AuthorityRefusalCode.LO_NAME_UNAVAILABLE,
                        disclosed_refusal_code=S015AuthorityRefusalCode.LO_NAME_UNAVAILABLE,
                    ) from exc
                raise

    def _write_founding_batch(
        self,
        *,
        actor: Identity,
        request: S015PreFoundingAuthorityRequest,
        decision: QualifiedS015AuthorityDecision,
        name: str,
        purpose: str,
        vision: str | None,
        mission: str | None,
        values: str | None,
        slug: str,
        fingerprint: str,
        effective_at: datetime | None,
        received_at: datetime,
    ) -> FoundLivingOrganismResult:
        """Write every row of a qualified founding and return the served result.

        Read the registration and founding-rule references from the
        decision's authority reference, and the platform-terms version from
        the rule. The LO's UUID is the request's; the acceptance UUID comes
        from founding_acceptance_uuid. Refuse with FOUNDING_PRINCIPAL_ABSENT,
        keeping the decision reference, if no founding principal is found;
        nothing has been written by this method at that point.

        Then write, in order:
        - the FOUNDING platform-terms acceptance, accepted at effective_at,
          with its s015r1 commitment;
        - the FOUNDING authority basis: issued by the founder under the
          principal, with the rule as instrument and the acceptance as
          evidence;
        - the LO in FOUNDING_PENDING, then version 1 of its founding content,
          then its FOUND event (to ACTIVE);
        - the founder's ACTIVE membership; the LO's founding basis and founder
          are then set; then the membership's OPEN (to PROPOSED) and ACTIVATE
          (to ACTIVE) transitions, reason FOUNDING;
        - the active LO-scope COORDINATOR role (version 1), the founder's
          ACTIVE assignment to it with no Circle, and its ASSIGN event;
        - the GOVERNANCE coverage requirement (COORDINATOR, at least one
          occupant, from effective_at), its case, and a SATISFIED assessment;
        - the receipt, holding the result's receipt payload.

        The FOUND, ASSIGN and assessment events are each the first on their
        anchor; the membership's transitions are sequence 1 and 2. Each event
        and transition carries the actor as PARTY in STANDING capacity,
        TRANSPORT entry, the basis, the decision reference, the request's
        references and the command fingerprint. The acceptance, the content
        and the three events leave recorded_at to the database, which also
        assigns the content commitment.

        Finally refresh the cached state of the LO, the membership and the
        assignment as of effective_at, and serve the result to the founder at
        the clock's time.
        """
        authority_facts = json.loads(decision.authority_reference[len("s015a1:"):])
        rule_reference = authority_facts["rule"]
        registration_reference = authority_facts["registration"]
        registration_uuid = UUID(registration_reference.removeprefix("learner-registration:"))
        terms_version_uuid = UUID(rule_reference.removeprefix("platform-terms-version:").removesuffix("#founding"))
        organism_uuid = request.living_organism_id
        acceptance_uuid = founding_acceptance_uuid(organism_uuid)
        terms_version = PlatformTermsVersion.objects.using(self._alias).get(
            terms_version_uuid=terms_version_uuid,
        )
        principal = self._founding_principal()
        if principal is None:
            raise S015AuthorityDenied(
                self._founding_principal_absent_message(),
                refusal_code=S015AuthorityRefusalCode.FOUNDING_PRINCIPAL_ABSENT,
                disclosed_refusal_code=S015AuthorityRefusalCode.FOUNDING_PRINCIPAL_ABSENT,
                authority_decision_reference=decision.authority_decision_reference,
            )

        acceptance_reference = f"platform-terms-acceptance:{acceptance_uuid}"
        acceptance_commitment = "s015r1:" + hashlib.sha256(canonical_json_bytes({
            "terms_version_uuid": terms_version_uuid,
            "acceptance_kind": "FOUNDING",
            "subject_reference": f"living-organism:{organism_uuid}",
            "accepted_by_identity_id": actor.identity_id,
            "accepted_by_access_epoch": actor.access_epoch,
            "accepted_at": canonical_timestamp(effective_at),
        })).hexdigest()
        PlatformTermsAcceptance.objects.using(self._alias).create(
            acceptance_uuid=acceptance_uuid,
            terms_version=terms_version,
            acceptance_kind=PlatformTermsAcceptance.Kind.FOUNDING,
            subject_reference=f"living-organism:{organism_uuid}",
            accepted_by_identity=actor,
            accepted_by_access_epoch=actor.access_epoch,
            accepted_at=effective_at,
            received_at=received_at,
            recorded_at=None,
            acceptance_commitment=acceptance_commitment,
        )

        scope_fingerprint = hashlib.sha256(canonical_json_bytes({
            "organism_id": organism_uuid,
            "founder_identity_id": actor.identity_id,
            "founder_access_epoch": actor.access_epoch,
            "registration_uuid": registration_uuid,
        })).hexdigest()
        basis = AuthorityBasis.objects.using(self._alias).create(
            authority_principal=principal,
            derivation_root_principal=principal,
            issuer=actor,
            authority_class="FOUNDING",
            scope_fingerprint=scope_fingerprint,
            instrument_reference=rule_reference,
            evidence_reference=acceptance_reference,
            issued_at=effective_at,
            effective_at=effective_at,
            received_at=received_at,
        )

        content_uuid = uuid4()
        organism = LivingOrganism.objects.using(self._alias).create(
            organism_id=organism_uuid,
            slug=slug,
            state=LivingOrganism.State.FOUNDING_PENDING,
            current_state_effective_at=effective_at,
            state_known_at=received_at,
            state_source_event_set_fingerprint=_empty_anchor_fingerprint(
                database_alias=self._alias,
                anchor_token="core_livingorganism",
                anchor_uuid=organism_uuid,
            ),
            constitutional_spine_reference=f"founding-content:{content_uuid}",
            founding_authority_basis=None,
            founder_identity=None,
            platform_root=False,
        )
        LOFoundingContentVersion.objects.using(self._alias).create(
            content_uuid=content_uuid,
            living_organism=organism,
            version=1,
            supersedes=None,
            name=name,
            purpose=purpose,
            vision=vision,
            mission=mission,
            values=values,
            actor_identity=actor,
            actor_access_epoch=actor.access_epoch,
            authority_decision_reference=decision.authority_decision_reference,
            effective_at=effective_at,
            received_at=received_at,
            recorded_at=None,
            content_commitment=None,
        )

        found_event_uuid = uuid4()
        self._insert_chain_event(
            table="core_livingorganismevent",
            anchor_column="living_organism_id",
            anchor_id=organism.pk,
            event_uuid=found_event_uuid,
            action="FOUND",
            prior_state=None,
            resulting_state="ACTIVE",
            actor=actor,
            actor_access_epoch=actor.access_epoch,
            authority_basis_id=basis.pk,
            authority_decision_reference=decision.authority_decision_reference,
            evidence_reference=f"founding-content:{content_uuid}",
            request_reference=request.request_reference,
            idempotency_key=request.idempotency_key,
            payload_fingerprint=fingerprint,
            effective_at=effective_at,
            received_at=received_at,
        )

        membership_uuid = uuid4()
        membership = OrganismMembership.objects.using(self._alias).create(
            membership_uuid=membership_uuid,
            identity=actor,
            living_organism=organism,
            state=OrganismMembership.State.ACTIVE,
            current_state_effective_at=effective_at,
            state_known_at=received_at,
            state_source_event_set_fingerprint=_empty_anchor_fingerprint(
                database_alias=self._alias,
                anchor_token="core_organismmembership",
                anchor_uuid=membership_uuid,
            ),
        )
        LivingOrganism.objects.using(self._alias).filter(pk=organism.pk).update(
            founding_authority_basis=basis,
            founder_identity=actor,
        )

        batch_reference = f"founding-batch:{found_event_uuid}"
        temporal_kind, temporal_reference = _resulting_temporal_basis(
            payload_fingerprint=fingerprint,
            effective_at=effective_at,
            received_at=received_at,
        )
        transition_common = {
            "database_alias": self._alias,
            "actor_id": actor.pk,
            "actor_state": "PARTY",
            "actor_capacity": "STANDING",
            "enterer_identity_id": actor.pk,
            "entry_mode": "TRANSPORT",
            "composing_rule_state": "NA_TRANSPORT",
            "composing_rule_reference": None,
            "composer_state": "NA_TRANSPORT",
            "composer_identity_id": None,
            "effective_until": None,
            "effective_until_state": "NONE",
            "l1_commitment": None,
            "l2_commitment": None,
            "actor_access_epoch": actor.access_epoch,
            "authority_basis_id": basis.pk,
            "authority_decision_reference": decision.authority_decision_reference,
            "evidence_reference": batch_reference,
            "request_reference": request.request_reference,
            "idempotency_key": request.idempotency_key,
            "payload_fingerprint": fingerprint,
            "temporal_basis_kind": temporal_kind,
            "temporal_basis_reference": temporal_reference,
            "occurred_at": effective_at,
            "effective_at": effective_at,
            "received_at": received_at,
            "reason_class": "FOUNDING",
        }
        open_event_uuid = uuid4()
        _insert_membership_transition_row(
            event_uuid=open_event_uuid,
            membership_id=membership.pk,
            sequence=1,
            predecessor_id=None,
            predecessor_sequence=None,
            action="OPEN",
            prior_state=None,
            resulting_state="PROPOSED",
            lineage_reference=self._lineage_reference("membership-open", open_event_uuid, fingerprint),
            **transition_common,
        )
        activate_event_uuid = uuid4()
        _insert_membership_transition_row(
            event_uuid=activate_event_uuid,
            membership_id=membership.pk,
            sequence=2,
            predecessor_id=self._membership_event_id(open_event_uuid),
            predecessor_sequence=1,
            action="ACTIVATE",
            prior_state="PROPOSED",
            resulting_state="ACTIVE",
            lineage_reference=self._lineage_reference("membership-activate", activate_event_uuid, fingerprint),
            **transition_common,
        )

        role = OrganismRoleDefinition.objects.using(self._alias).create(
            role_uuid=uuid4(),
            living_organism=organism,
            code="COORDINATOR",
            scope=OrganismRoleDefinition.Scope.LIVING_ORGANISM,
            definition_version=1,
            active=True,
        )
        assignment_uuid = uuid4()
        assignment = ContextualRoleAssignment.objects.using(self._alias).create(
            assignment_uuid=assignment_uuid,
            membership=membership,
            role_definition=role,
            circle=None,
            state=ContextualRoleAssignment.State.ACTIVE,
            current_state_effective_at=effective_at,
            state_known_at=received_at,
            state_source_event_set_fingerprint=_empty_anchor_fingerprint(
                database_alias=self._alias,
                anchor_token="core_contextualroleassignment",
                anchor_uuid=assignment_uuid,
            ),
        )
        assignment_event_uuid = uuid4()
        self._insert_chain_event(
            table="core_contextualroleassignmentstateevent",
            anchor_column="assignment_id",
            anchor_id=assignment.pk,
            event_uuid=assignment_event_uuid,
            action="ASSIGN",
            prior_state=None,
            resulting_state="ACTIVE",
            actor=actor,
            actor_access_epoch=actor.access_epoch,
            authority_basis_id=basis.pk,
            authority_decision_reference=decision.authority_decision_reference,
            evidence_reference=batch_reference,
            request_reference=request.request_reference,
            idempotency_key=request.idempotency_key,
            payload_fingerprint=fingerprint,
            effective_at=effective_at,
            received_at=received_at,
        )

        requirement = EssentialCoverageRequirement.objects.using(self._alias).create(
            requirement_uuid=uuid4(),
            roster_version=1,
            responsibility_domain_code="GOVERNANCE",
            qualifying_role_code="COORDINATOR",
            minimum_active_occupants=1,
            scope="LIVING_ORGANISM",
            effective_from=effective_at,
            effective_until=None,
            living_organism=organism,
        )
        coverage_case = EssentialCoverageCase.objects.using(self._alias).create(
            case_uuid=uuid4(),
            living_organism=organism,
        )
        self._insert_chain_event(
            table="core_coverageassessment",
            anchor_column="case_id",
            anchor_id=coverage_case.pk,
            event_uuid=uuid4(),
            action="ASSESS",
            prior_state=None,
            resulting_state="SATISFIED",
            actor=actor,
            actor_access_epoch=actor.access_epoch,
            authority_basis_id=basis.pk,
            authority_decision_reference=decision.authority_decision_reference,
            evidence_reference=batch_reference,
            request_reference=request.request_reference,
            idempotency_key=request.idempotency_key,
            payload_fingerprint=fingerprint,
            effective_at=effective_at,
            received_at=received_at,
            extra={
                "roster_version": requirement.roster_version,
                "evaluated_state_at": effective_at,
                "evaluated_known_at": received_at,
                "determiner_capacity_reference": batch_reference,
            },
        )

        result_lineage = self._lineage_reference("receipt", found_event_uuid, fingerprint)
        result = FoundLivingOrganismResult(
            living_organism_uuid=organism_uuid,
            event_uuid=found_event_uuid,
            lineage_reference=result_lineage,
            authority_reference=decision.authority_reference,
            authority_decision_reference=decision.authority_decision_reference,
        )
        OrganismCommandReceipt.objects.using(self._alias).create(
            actor=actor,
            action=_ACTION.value,
            idempotency_key=request.idempotency_key,
            request_reference=request.request_reference,
            payload_fingerprint=fingerprint,
            authority_decision_reference=decision.authority_decision_reference,
            lineage_reference=result_lineage,
            result_payload=result.receipt_payload(),
        )
        self._refresh("core_livingorganism", organism.pk, effective_at)
        self._refresh("core_organismmembership", membership.pk, effective_at)
        self._refresh("core_contextualroleassignment", assignment.pk, effective_at)
        return serve_replay(
            result,
            # This clock read supplies the present-time access check when serving the result; it does not replace the bound request's receipt time.
            viewer=ReplayViewer(actor.pk, actor.identity_id, self._clock()),
            result_type=ReplayResultType(FoundLivingOrganismResult, self._alias),
        )

    def _attempt_replay(
        self,
        *,
        actor: Identity,
        request_reference: str,
        idempotency_key: str,
        fingerprint: str,
    ) -> FoundLivingOrganismResult | None:
        """Return the served result of a completed founding, or None.

        Lock the receipt for this actor, action and idempotency key; with
        none, return None. A different request reference or fingerprint
        raises S015CommandReplayMismatch. Otherwise rebuild the result from
        the receipt payload and serve it to the actor at the clock's time.
        """
        receipt = (
            OrganismCommandReceipt.objects.using(self._alias)
            .select_for_update()
            .filter(actor=actor, action=_ACTION.value, idempotency_key=idempotency_key)
            .first()
        )
        if receipt is None:
            return None
        if receipt.request_reference != request_reference or receipt.payload_fingerprint != fingerprint:
            raise S015CommandReplayMismatch("completed receipt does not match request")
        return serve_replay(
            _result_from_payload(receipt.result_payload),
            # This clock read supplies the present-time access check when serving the result; it does not replace the bound request's receipt time.
            viewer=ReplayViewer(actor.pk, actor.identity_id, self._clock()),
            result_type=ReplayResultType(FoundLivingOrganismResult, self._alias),
        )

    def _command_fingerprint(
        self,
        *,
        actor: Identity,
        name: str,
        purpose: str,
        vision: str | None,
        mission: str | None,
        values: str | None,
        accepted_terms_version_uuid: UUID | None,
        request_reference: str,
        idempotency_key: str,
        effective_at: datetime,
    ) -> str:
        """Return the founding command's fingerprint.

        SHA-256 hex of _COMMAND_DOMAIN and the canonical JSON of the
        action, the actor's identity UUID and access epoch, the request
        reference, the idempotency key, the name, purpose, vision,
        mission and values, the terms version UUID, and effective_at
        (canonical, or None).

        found_living_organism passes the command's effective_at, not its
        received_at default. Omitting effective_at therefore keeps that
        fingerprint input as None across attempts. The fingerprint
        remains the same only if the other inputs also remain the same.
        received_at is not included.
        """
        payload = {
            "action": _ACTION.value,
            "actor_access_epoch": actor.access_epoch,
            "actor_identity_id": actor.identity_id,
            "effective_at": (
                None if effective_at is None else canonical_timestamp(effective_at)
            ),
            "idempotency_key": idempotency_key,
            "name": name,
            "purpose": purpose,
            "request_reference": request_reference,
            "terms_version_uuid": accepted_terms_version_uuid,
            "values": values,
            "vision": vision,
            "mission": mission,
        }
        return hashlib.sha256(_COMMAND_DOMAIN + canonical_json_bytes(payload)).hexdigest()

    def _insert_chain_event(
        self,
        *,
        table: str,
        anchor_column: str,
        anchor_id: int,
        event_uuid: UUID,
        action: str,
        prior_state: str | None,
        resulting_state: str,
        actor: Identity,
        actor_access_epoch: int,
        authority_basis_id: int,
        authority_decision_reference: str,
        evidence_reference: str,
        request_reference: str,
        idempotency_key: str,
        payload_fingerprint: str,
        effective_at: datetime,
        received_at: datetime,
        extra: dict[str, object] | None = None,
    ) -> int:
        """Insert the first event on a founding chain by raw SQL; return its id.

        The table must be core_livingorganismevent,
        core_contextualroleassignmentstateevent or core_coverageassessment,
        with its own anchor column, else ValueError. core_coverageassessment
        takes its state in result and needs exactly the extra fields
        roster_version, evaluated_state_at, evaluated_known_at and
        determiner_capacity_reference; the other tables take resulting_state
        and no extra fields. A mismatch raises ValueError.

        The row is sequence 1 with no predecessor. The actor is PARTY in
        STANDING capacity and the enterer. Entry is TRANSPORT, with no
        composing rule or composer and no end. occurred_at is effective_at.
        The lineage reference comes from _lineage_reference(action, ...),
        and the temporal basis from _resulting_temporal_basis. recorded_at
        is left to the database. parts_commitment is computed by the
        database's s015_0022_digest before the insert, and
        predecessor_l1_commitment is None.
        """
        allowed = {
            "core_livingorganismevent": ("living_organism_id", "resulting_state", ()),
            "core_contextualroleassignmentstateevent": ("assignment_id", "resulting_state", ()),
            "core_coverageassessment": (
                "case_id",
                "result",
                ("roster_version", "evaluated_state_at", "evaluated_known_at", "determiner_capacity_reference"),
            ),
        }
        if table not in allowed or allowed[table][0] != anchor_column:
            raise ValueError("unsupported founding event table")
        state_column = allowed[table][1]
        extra_columns = allowed[table][2]
        extra = extra or {}
        if set(extra) != set(extra_columns):
            if extra_columns or extra:
                raise ValueError("founding event fields do not match table")
        temporal_kind, temporal_reference = _resulting_temporal_basis(
            payload_fingerprint=payload_fingerprint,
            effective_at=effective_at,
            received_at=received_at,
        )
        columns: dict[str, object] = {
            "event_uuid": event_uuid,
            anchor_column: anchor_id,
            "sequence": 1,
            "predecessor_id": None,
            "predecessor_sequence": None,
            "action": action,
            "prior_state": prior_state,
            state_column: resulting_state,
            "actor_id": actor.pk,
            "actor_state": "PARTY",
            "actor_capacity": "STANDING",
            "enterer_identity_id": actor.pk,
            "entry_mode": "TRANSPORT",
            "composing_rule_state": "NA_TRANSPORT",
            "composing_rule_reference": None,
            "composer_state": "NA_TRANSPORT",
            "composer_identity_id": None,
            "effective_until": None,
            "effective_until_state": "NONE",
            "l2_commitment": None,
            "actor_access_epoch": actor_access_epoch,
            "authority_basis_id": authority_basis_id,
            "authority_decision_reference": authority_decision_reference,
            "evidence_reference": evidence_reference,
            "request_reference": request_reference,
            "idempotency_key": idempotency_key,
            "payload_fingerprint": payload_fingerprint,
            "lineage_reference": self._lineage_reference(action, event_uuid, payload_fingerprint),
            "temporal_basis_kind": temporal_kind,
            "temporal_basis_reference": temporal_reference,
            "occurred_at": effective_at,
            "effective_at": effective_at,
            "received_at": received_at,
            "recorded_at": None,
        }
        columns.update(extra)
        with connections[self._alias].cursor() as cursor:
            cursor.execute(
                "SELECT public.s015_0022_digest('s015p1', "
                "public.s015_0022_parts_preimage(%s, %s::uuid))",
                [table, event_uuid],
            )
            columns["parts_commitment"] = cursor.fetchone()[0]
            columns["predecessor_l1_commitment"] = None
            names = ", ".join(columns)
            placeholders = ", ".join(["%s"] * len(columns))
            cursor.execute(
                f"INSERT INTO public.{table} ({names}) VALUES ({placeholders}) RETURNING id",
                list(columns.values()),
            )
            return cursor.fetchone()[0]

    def _membership_event_id(self, event_uuid: UUID) -> int:
        """Return the id of the membership transition with this event UUID.

        Raise S015FormationTechnicalFailure if none is found.
        """
        with connections[self._alias].cursor() as cursor:
            cursor.execute(
                "SELECT id FROM public.core_organismmembershiptransition WHERE event_uuid = %s",
                [event_uuid],
            )
            row = cursor.fetchone()
        if row is None:
            raise S015FormationTechnicalFailure("membership opening event was not persisted")
        return row[0]

    def _founding_principal(self) -> AuthorityPrincipal | None:
        """Return the founding authority principal, or None.

        Delegates to organism_authority._founding_authority_principal: the
        genesis principal, else, only when fixture substitutes are
        permitted, the fixture principal.
        """
        from src.intevia.services.organism_authority import _founding_authority_principal

        return _founding_authority_principal(self._alias)

    def _founding_principal_absent_message(self) -> str:
        """Return the refusal text for a missing founding principal.

        When a fixture-namespace principal exists but fixture substitutes are
        not permitted, say that the labelled development substitute is not
        permitted here. Otherwise say that the GENESIS principal is not
        registered on this database and how to register it.
        """
        fixture_substitute_exists = AuthorityPrincipal.objects.using(self._alias).filter(
            governed_source_namespace="INTEVIA_FIXTURE_AUTHORITY_V1",
        ).exists()
        if fixture_substitute_exists and not settings.INTEVIA_FIXTURE_SUBSTITUTES_PERMITTED:
            return "the labelled development substitute is not permitted in this environment"
        return (
            "The LO was not founded: the platform's founding authority principal is not registered "
            "on this database (the GENESIS principal, act 1 of the bootstrap). Register it (or, in "
            "development, seed it labelled), then found."
        )

    def _authored_founding_refusal_message(
        self,
        code: S015AuthorityRefusalCode | None,
        *,
        received_at: datetime,
    ) -> str | None:
        """Return the authored founding message for a refusal code, or None.

        FOUNDING_PRINCIPAL_ABSENT takes _founding_principal_absent_message.
        FOUNDING_TERMS_NOT_ACCEPTED, FOUNDING_TERMS_VERSION_SUPERSEDED,
        E_NOT_REGISTERED and E_IDENTITY_INACTIVE have fixed texts. Any other
        code, or None, gives None.
        """
        if code is S015AuthorityRefusalCode.FOUNDING_PRINCIPAL_ABSENT:
            return self._founding_principal_absent_message()
        messages = {
            S015AuthorityRefusalCode.FOUNDING_TERMS_NOT_ACCEPTED: (
                "The LO was not founded. INTEVIA records which platform-terms "
                "version every founder accepts, and no acceptance of the version in force was "
                "received. Read the terms shown and confirm them, then found again."
            ),
            S015AuthorityRefusalCode.FOUNDING_TERMS_VERSION_SUPERSEDED: (
                "The LO was not founded. The terms version you accepted is no "
                "longer the version in force. Reload to see the current terms and confirm them."
            ),
            S015AuthorityRefusalCode.E_NOT_REGISTERED: (
                "The LO was not founded. Founding is a registered Learner's "
                "act, and no governed registration is effective for your account at this time. "
                "(Development: the setup command's account has no registration; provision through "
                "the supported path.)"
            ),
            S015AuthorityRefusalCode.E_IDENTITY_INACTIVE: (
                "The LO was not founded: your account is not active at the time of this act."
            ),
        }
        message = messages.get(code)
        if message is None:
            return None
        return f"{message} {_intake_tail(_TAIL_REFUSAL_RECORDED, received_at)}"

    def _refresh(self, anchor_token: str, anchor_id: int, effective_at: datetime) -> None:
        """Refresh a founding anchor's cached state through the
        database.

        Call s015_refresh_governed_cache with the anchor table and id,
        effective_at as the state time and the database's
        clock_timestamp() as the known time. For founding's LO,
        membership and assignment anchors, the function locks the row
        and sets current_state_effective_at and state_known_at. Its
        returned fingerprint is not used.
        """
        with connections[self._alias].cursor() as cursor:
            cursor.execute(
                "SELECT public.s015_refresh_governed_cache(%s, %s, %s, clock_timestamp())",
                [anchor_token, anchor_id, effective_at],
            )
            cursor.fetchone()

    @staticmethod
    def _lineage_reference(kind: str, event_uuid: UUID, fingerprint: str) -> str:
        """Return "s015l1:" and the SHA-256 hex of _LINEAGE_DOMAIN and the
        canonical JSON of the event UUID, the fingerprint and the kind."""
        payload = canonical_json_bytes({
            "event_uuid": event_uuid,
            "fingerprint": fingerprint,
            "kind": kind,
        })
        return "s015l1:" + hashlib.sha256(_LINEAGE_DOMAIN + payload).hexdigest()

    @staticmethod
    def _constraint_name(exc: IntegrityError) -> str | None:
        """Return the violated constraint's name from the IntegrityError's
        database cause, or None when it has none."""
        cause = exc.__cause__
        diagnostics = getattr(cause, "diag", None)
        return getattr(diagnostics, "constraint_name", None)

    @staticmethod
    def _timestamp(value: object, name: str) -> datetime:
        """Return value unchanged if it is a timezone-aware datetime of
        exactly that type; raise TypeError otherwise. Unlike
        _validate_timestamp, it does not convert to UTC."""
        if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
            raise TypeError(f"{name} must be timezone-aware")
        return value

    @staticmethod
    def _required_text(value: object, name: str) -> str:
        """Check a required founding text and return it unchanged.

        Must be a str, else TypeError. If it is empty or whitespace after
        NFC normalisation, raise S015AuthorityDenied with LO_NAME_REQUIRED,
        for purpose as well as name. Then it must already be NFC, else
        ValueError, and a name may be at most 90 characters, else
        ValueError. Surrounding whitespace is kept.
        """
        if type(value) is not str:
            raise TypeError(f"{name} must be a string")
        normalized = unicodedata.normalize("NFC", value)
        if not normalized.strip():
            raise S015AuthorityDenied(
                "The LO was not founded and nothing was saved: a name (and a purpose) is required.",
                refusal_code=S015AuthorityRefusalCode.LO_NAME_REQUIRED,
                disclosed_refusal_code=S015AuthorityRefusalCode.LO_NAME_REQUIRED,
            )
        if normalized != value:
            raise ValueError(f"{name} must use canonical Unicode")
        if name == "name" and len(value) > 90:
            raise ValueError("name exceeds the supported length")
        return value

    @staticmethod
    def _optional_text(value: object, name: str) -> str | None:
        """Check an optional founding text and return it unchanged.

        None stays None. Otherwise it must be a str, else TypeError, and
        already NFC, else ValueError. Empty strings are accepted, and there
        is no length limit.
        """
        if value is None:
            return None
        if type(value) is not str:
            raise TypeError(f"{name} must be a string or None")
        if unicodedata.normalize("NFC", value) != value:
            raise ValueError(f"{name} must use canonical Unicode")
        return value


class S015FormationActorUnavailable(Exception):
    """The founder's Identity could not be locked as an active actor.

    _lock_actor raises it. found_living_organism turns an inactive
    Identity or credential into an E_IDENTITY_INACTIVE refusal, so it
    propagates only when no Identity holds the credential.
    """
    pass


class S015FormationTechnicalFailure(Exception):
    """Founding cannot proceed for a technical reason.

    Raised when found_living_organism is called inside an atomic block,
    and when a membership's opening transition cannot be found after
    its insert.
    """
    pass


__all__ = [
    "FoundLivingOrganismCommand",
    "FoundLivingOrganismResult",
    "OrganismFormationService",
    "OrganismPhaseAdvanceService",
    "PhaseAdvanceCommand",
    "PhaseAdvanceResult",
    "PhaseReadiness",
    "PhaseReadinessQuery",
    "S015PhaseAdvanceNotFound",
    "S015PhaseAdvanceTechnicalFailure",
    "S015PhaseAdvanceUnavailable",
    "S015FormationActorUnavailable",
    "S015FormationTechnicalFailure",
]

from dataclasses import dataclass
from datetime import datetime
import hashlib
from typing import Callable
from uuid import UUID, uuid4

from django.contrib.auth.models import User
from django.db import connections

from core.models import (
    LivingOrganism,
    LivingOrganismEvent,
    LOFoundingContentVersion,
    LOGovernanceConfigurationVersion,
)
from core.organism_contract import OrganismAction
from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider
from src.intevia.services.organism_authority import (
    S015Authority,
    S015AuthorityDenied,
    S015AuthorityRefusalCode,
    S015PostFoundingAuthorityRequest,
    authority_target_fingerprint,
    canonical_json_bytes,
)
from src.intevia.services.organism_command_support import (
    _OrganismCommandService,
    _canonical_key,
    _canonical_uuid,
    _command_payload,
    _lock_actor,
    _lock_actor_membership_context,
    _lock_registered_organism,
    _resulting_temporal_basis,
    _validate_timestamp,
    ReplayViewer,
    S015CommandReplayMismatch,
    acquire_organism_command_mutex,
    authority_atomic,
)


_PHASE_SUBTYPE = "TRANSITION_LIVING_ORGANISM/PHASE_ADVANCE"
_PHASE_COMMAND_DOMAIN = b"INTEVIA:S015:TRANSITION_LIVING_ORGANISM/PHASE_ADVANCE:COMMAND:v1\x00"
_PHASE_NAMES = {
    "CONCEIVING": "Conceiving",
    "GERMINATING": "Germinating",
    "BIRTHING": "Birthing",
    "BIRTHED": "Birthed",
}
_NEXT_PHASE = {
    "CONCEIVING": "GERMINATING",
    "GERMINATING": "BIRTHING",
    "BIRTHING": "BIRTHED",
}


@dataclass(frozen=True, slots=True)
class PhaseAdvanceCommand:
    """Inputs to advance_phase.

    credential is the actor's Django User. living_organism_uuid names
    the LO; from_phase and to_phase name the advance. request_reference
    and idempotency_key identify the request. effective_at is when the
    advance takes effect; None means the time the service receives the
    command.

    There is no received_at field: the durable intake assigns it when the
    request is admitted. advance_phase checks every field.
    """
    credential: User
    living_organism_uuid: UUID
    from_phase: str
    to_phase: str
    request_reference: str
    idempotency_key: str
    effective_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PhaseReadinessQuery:
    """Inputs to assess_readiness.

    The LO's UUID, the proposed from_phase and to_phase, the effective
    time to judge the LO at, and evaluated_at, the latest received and
    recorded time that counts as known. effective_at must not be later
    than evaluated_at.
    """
    living_organism_uuid: UUID
    from_phase: str
    to_phase: str
    effective_at: datetime
    evaluated_at: datetime


@dataclass(frozen=True, slots=True)
class PhaseReadiness:
    """Result of assess_readiness.

    ready says whether the advance's own conditions hold. current_phase
    is the LO's phase at the query's times. refusal_code is None when
    ready; otherwise PHASE_OUT_OF_ORDER, PHASE_CONDITION_UNMET or the
    code the SB-G readiness function returned. missing_provision names
    what is missing for a PHASE_CONDITION_UNMET, and is None otherwise.
    """
    ready: bool
    current_phase: str
    refusal_code: S015AuthorityRefusalCode | None
    missing_provision: str | None


@dataclass(frozen=True, slots=True)
class PhaseAdvanceResult:
    """Result of a phase advance.

    Carries the LO's UUID, the PHASE_ADVANCE event's UUID and sequence,
    the two phases, the receipt's lineage reference, and the authority
    and authority-decision references.

    Declares no audience-specific field restrictions. New and replayed
    results are both served through serve_replay, which can still
    withhold authority_reference.
    """
    living_organism_uuid: UUID
    event_uuid: UUID
    event_sequence: int
    from_phase: str
    to_phase: str
    lineage_reference: str
    authority_reference: str
    authority_decision_reference: str

    def receipt_payload(self) -> dict[str, str | int]:
        """Return the receipt form: the two UUIDs as strings, the other
        fields as they are.
        """
        return {
            "living_organism_uuid": str(self.living_organism_uuid),
            "event_uuid": str(self.event_uuid),
            "event_sequence": self.event_sequence,
            "from_phase": self.from_phase,
            "to_phase": self.to_phase,
            "lineage_reference": self.lineage_reference,
            "authority_reference": self.authority_reference,
            "authority_decision_reference": self.authority_decision_reference,
        }

    @classmethod
    def replay_field_audiences(cls, result: PhaseAdvanceResult) -> dict[str, str]:
        """Return an empty mapping for a result of exactly this class;
        raise TypeError otherwise.
        """
        if type(result) is not cls:
            raise TypeError("phase-advance replay result has the wrong type")
        return {}


def _phase_result_from_payload(payload: object) -> PhaseAdvanceResult:
    """Rebuild a phase-advance result from a receipt payload.

    Require a dict of exactly that type, else raise
    S015CommandReplayMismatch. Construct the two UUIDs; missing fields,
    and TypeError or ValueError from construction, become
    S015CommandReplayMismatch. Other exceptions propagate.

    The sequence, the phases and the three references are taken as
    stored, without checking their types or values.
    """
    if type(payload) is not dict:
        raise S015CommandReplayMismatch("completed receipt does not match request")
    try:
        return PhaseAdvanceResult(
            living_organism_uuid=UUID(payload["living_organism_uuid"]),
            event_uuid=UUID(payload["event_uuid"]),
            event_sequence=payload["event_sequence"],
            from_phase=payload["from_phase"],
            to_phase=payload["to_phase"],
            lineage_reference=payload["lineage_reference"],
            authority_reference=payload["authority_reference"],
            authority_decision_reference=payload["authority_decision_reference"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise S015CommandReplayMismatch("completed receipt does not match request") from exc


class S015PhaseAdvanceNotFound(Exception):
    """The named LO was absent at the service's own row lookup.

    assess_readiness raises this when no LO has the UUID.
    advance_phase has the same translation after its registered
    organism lock. That earlier lock can itself raise a database
    error for a missing LO, before this translation is reached.
    """
    pass


class S015PhaseAdvanceUnavailable(Exception):
    """The actor's locked identity or membership context is
    unavailable.

    _lock_actor raises this when no Identity holds the credential,
    when the Identity is inactive or when its credential is
    inactive. _lock_actor_membership_context raises it when
    membership is missing, is not ACTIVE or has no head transition.
    An empty set of active role-head references does not itself
    raise this exception.
    """
    pass


class S015PhaseAdvanceTechnicalFailure(Exception):
    """A phase advance could not be completed for a technical reason.

    Raised when advance_phase is called inside an atomic block, when the
    LO has no head event or founding basis, when the head event has no
    l1 commitment, and when no authored message fits a remapped refusal.
    """
    pass


class OrganismPhaseAdvanceService(_OrganismCommandService):
    """Assess and record an LO's phase advances
    (TRANSITION_LIVING_ORGANISM/PHASE_ADVANCE).

    The advances are Conceiving to Germinating and Germinating to
    Birthing. assess_readiness reads whether the advance's own
    conditions hold; advance_phase records it as a PHASE_ADVANCE event
    on the LO's chain, with its authority decision and a receipt. The
    LO's state does not change. Its phase is read from the latest
    PHASE_ADVANCE event's evidence reference, and is CONCEIVING when
    there is none.

    Extends _OrganismCommandService for replay and the receipt.
    advance_phase admits the request through the durable intake, and
    the advance runs as of the intake's received_at; the injected clock
    gives only the replay viewer's present time.
    """
    _action = OrganismAction.TRANSITION_LIVING_ORGANISM
    _not_found = S015PhaseAdvanceNotFound
    _authority_unavailable = S015PhaseAdvanceUnavailable

    def __init__(
        self,
        *,
        clock: Callable[[], datetime],
        authority: S015Authority | None = None,
        database_alias: str = "default",
    ) -> None:
        """Check the clock and alias, build the default authority, and pass
        them to _OrganismCommandService.

        clock must be callable, else TypeError; database_alias a non-empty
        str, else ValueError. With no authority, build an S015Authority over
        ProductionApplicationAuthorityProvider on database_alias. The base
        then requires an S015Authority (TypeError) on the same alias
        (ValueError).
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
        super().__init__(authority=authority, clock=clock, database_alias=database_alias)

    def _replay_result(self, payload: object) -> PhaseAdvanceResult:
        """Rebuild a stored result with _phase_result_from_payload.
        """
        return _phase_result_from_payload(payload)

    def assess_readiness(self, query: PhaseReadinessQuery) -> PhaseReadiness:
        """Report whether the LO could advance from from_phase to to_phase.

        Validate the query: the LO UUID must be a UUID (TypeError); the
        phases canonical keys of at most 24 characters (TypeError or
        ValueError); both times aware (TypeError), with effective_at not
        later than evaluated_at (ValueError). A missing LO raises
        S015PhaseAdvanceNotFound.

        The phase, Birthing and founding-content reads, and the governance
        configuration event, are cut at the query's times: effective by
        effective_at, and received and recorded by evaluated_at where the
        record has those times. The configuration version is then read by
        its UUID.

        Not ready, with PHASE_OUT_OF_ORDER, when the pair is not Conceiving
        to Germinating or Germinating to Birthing, when from_phase is not
        the LO's current phase, or when a Birthing is recorded within the
        cut. To Germinating, not ready unless version 1 of the founding
        content has a non-blank name and purpose. To Birthing, not ready
        without an effective governance configuration or with no GOVERNANCE
        choice. Both refuse with PHASE_CONDITION_UNMET and name the missing
        provision. If GOVERNANCE is on, the database's SB-G readiness
        function decides: any code it returns is the refusal, and an unknown
        code or no row raises RuntimeError.

        It writes nothing and takes no lock itself. It does not check the
        actor, the actor's Coordinator office or the size of the LO's
        electorate, which qualification also checks, so ready does not
        promise that advance_phase will qualify.
        """
        organism_uuid = _canonical_uuid(query.living_organism_uuid, "living_organism_uuid")
        from_phase = _canonical_key(query.from_phase, "from_phase", 24)
        to_phase = _canonical_key(query.to_phase, "to_phase", 24)
        effective_at = _validate_timestamp(query.effective_at, "effective_at")
        evaluated_at = _validate_timestamp(query.evaluated_at, "evaluated_at")
        if effective_at > evaluated_at:
            raise ValueError("effective_at must not be later than evaluated_at")
        try:
            organism = LivingOrganism.objects.using(self._alias).get(organism_id=organism_uuid)
        except LivingOrganism.DoesNotExist as exc:
            raise S015PhaseAdvanceNotFound("living organism not found") from exc

        current_phase = self._phase_at(organism, effective_at, evaluated_at)
        if (
            (from_phase, to_phase) not in {
                ("CONCEIVING", "GERMINATING"),
                ("GERMINATING", "BIRTHING"),
            }
            or from_phase != current_phase
            or self._birthing_recorded(organism, effective_at, evaluated_at)
        ):
            return PhaseReadiness(
                False,
                current_phase,
                S015AuthorityRefusalCode.PHASE_OUT_OF_ORDER,
                None,
            )

        if to_phase == "GERMINATING":
            if not self._founding_content_ready(organism, effective_at, evaluated_at):
                return PhaseReadiness(
                    False,
                    current_phase,
                    S015AuthorityRefusalCode.PHASE_CONDITION_UNMET,
                    "founding content (name and purpose)",
                )
            return PhaseReadiness(True, current_phase, None, None)

        configuration = self._effective_governance_configuration(
            organism,
            effective_at,
            evaluated_at,
        )
        if configuration is None:
            return PhaseReadiness(
                False,
                current_phase,
                S015AuthorityRefusalCode.PHASE_CONDITION_UNMET,
                "an effective governance configuration",
            )
        if configuration.governance_enabled is None:
            return PhaseReadiness(
                False,
                current_phase,
                S015AuthorityRefusalCode.PHASE_CONDITION_UNMET,
                "GOVERNANCE choice",
            )
        if configuration.governance_enabled:
            with connections[self._alias].cursor() as cursor:
                cursor.execute(
                    "SELECT public.sbg_governance_readiness(%s, %s, %s, %s)",
                    [organism.pk, effective_at, evaluated_at, "PHASE_ADVANCE"],
                )
                row = cursor.fetchone()
            if row is None:
                raise RuntimeError("SB-G readiness function returned no row")
            if row[0] is not None:
                try:
                    refusal_code = S015AuthorityRefusalCode(row[0])
                except ValueError as exc:
                    raise RuntimeError("SB-G readiness returned an unknown refusal code") from exc
                return PhaseReadiness(False, current_phase, refusal_code, None)
        return PhaseReadiness(True, current_phase, None, None)

    def advance_phase(self, command: PhaseAdvanceCommand) -> PhaseAdvanceResult:
        """Admit a phase advance and run it at its bound time (D §5.1).

        Refuse a call inside an atomic block with
        S015PhaseAdvanceTechnicalFailure before any statement, then
        require the admission state and the active entry, build the
        intake shape, resolve the actor and admit the request through
        the durable intake (one committed row). The advance then runs in
        _execute_advance_phase as of the intake's received_at.

        The SB-G guard arm (amendment A3 §7.4, class B): outside any
        atomic block, after the command's transaction rolled back, an
        IntegrityError whose cause carries SQLSTATE 23000 and one of the
        five phase-advance SB-G codes as its primary message is raised
        as S015AuthorityDenied with that code, no decision reference and
        the rolled-back text (R-FORM-32a, -32b, -32c, -32e, -32f), the error as its
        cause. The LO is re-read after the rollback. Every other error
        propagates unchanged; nothing is retried.
        """
        if connections[self._alias].in_atomic_block:
            raise S015PhaseAdvanceTechnicalFailure(
                "PHASE_ADVANCE could not be completed because of a technical problem; "
                "nothing was saved. Try again; if it persists, report it."
            )
        from src.intevia.services.command_intake import (
            ACTIVE_ENTRIES,
            IntakeAdmissionTechnicalFailure,
            IntakeEntry,
            _require_admission_state,
            _resolve_actor,
            admit_request,
            intake_shape,
        )

        entry = IntakeEntry.ADVANCE_PHASE
        _require_admission_state(self._alias, entry)
        if entry not in ACTIVE_ENTRIES:
            raise IntakeAdmissionTechnicalFailure("ADVANCE_PHASE is not active")
        shape = intake_shape(entry, command)
        actor_ref = _resolve_actor(self._alias, command.credential)
        bound = admit_request(self._alias, entry, actor_ref, shape)
        try:
            return self._execute_advance_phase(command, bound)
        except IntegrityError as exc:
            code = _sbg_guard_refusal_code(exc, _SBG_PHASE_GUARD_CODES)
            if code is None or connections[self._alias].in_atomic_block:
                raise
            organism = LivingOrganism.objects.using(self._alias).get(
                organism_id=_canonical_uuid(command.living_organism_uuid, "living_organism_uuid"),
            )
            effective_at = (
                bound.received_at
                if command.effective_at is None
                else _validate_timestamp(command.effective_at, "effective_at")
            )
            raise S015AuthorityDenied(
                self._authored_refusal_message(
                    code,
                    organism,
                    _canonical_key(command.to_phase, "to_phase", 24),
                    effective_at,
                    bound.received_at,
                    received_at=bound.received_at,
                    rolled_back=True,
                ),
                refusal_code=code,
                disclosed_refusal_code=code,
                authority_decision_reference=None,
            ) from exc

    def _execute_advance_phase(
        self,
        command: PhaseAdvanceCommand,
        bound: BoundRequest,
    ) -> PhaseAdvanceResult:
        """Execute a bound phase advance and return the served result (D §5.2).

        advance_phase has admitted the request. Inside authority_atomic,
        opened with the bound receipt time, in order:
        - lock the actor's Identity and credential; a missing Identity,
          inactive Identity or inactive credential raises
          S015PhaseAdvanceUnavailable; then verify the bound request
          against the command and the actor;
        - check the LO UUID, the phases (at most 24 characters), the
          request reference (128) and the idempotency key (120). Take
          received_at from the bound request (on a retry, the original
          receipt time) and default effective_at to it.
          Validate aware datetimes, convert them to UTC and require
          effective_at <= received_at;
        - fingerprint the command, acquire the
          actor/action/idempotency-key mutex and serve a matching
          completed receipt. A different request reference or
          fingerprint raises S015CommandReplayMismatch;
        - take the registered-organism lock, which can raise a database
          error for a missing LO. Then lock the LO row; a missing row at
          this later lookup becomes S015PhaseAdvanceNotFound;
        - lock the actor's membership, requiring stored state ACTIVE and
          a head transition, else S015PhaseAdvanceUnavailable. Lock its
          ACTIVE role assignments with head events and collect their
          head references; this tuple may be empty;
        - qualify the post-founding request at received_at. For the
          handled phase, CQ-route and SB-G refusals, raise the denial
          with its authored message, preserving its codes and decision
          reference; other denials propagate unchanged;
        - require the LO's head event and founding basis; lock the head
          event and require its l1 commitment. The explicit
          missing-head, missing-basis and missing-commitment checks
          raise S015PhaseAdvanceTechnicalFailure;
        - insert the next PHASE_ADVANCE event with the head as
          predecessor, and equal prior and resulting LO states. The
          actor state is LIVING_ORGANISM, with no actor Identity or
          access epoch, in STANDING capacity. The person is the enterer
          in TRANSPORT mode. Carry the founding basis, decision and
          request references, fingerprint, and
          "formation-phase:<from>-><to>" evidence;
        - refresh the LO's cache at effective_at with database known
          time, then write the receipt, rebuild the result from its stored
          payload and serve it at a fresh clock reading.

        The fingerprint includes received_at and the resolved
        effective_at; changing the receipt time therefore prevents a
        matching replay. The event's occurred_at equals effective_at; an
        earlier effective time adds a retrospective temporal basis.
        recorded_at is omitted from the insert for the database to
        assign.

        UUID and time type errors raise TypeError; key checks raise
        TypeError or ValueError; effective_at after received_at raises
        ValueError. Other helper and database errors follow their own
        contracts and the outer contention translation.

        S015AuthorityDenied is deferred until the atomic scope exits.
        Earlier writes, including a recorded REFUSED authority decision,
        remain only if the transaction can commit and its exit succeeds.
        A technical failure while building a refusal message is not
        deferred and rolls back this command's transaction; the intake
        record of the request stands. Exit failure
        can prevent a pending result or denial from reaching the caller.
        The outer contention wrapper translates qualifying database lock
        errors after exit; nothing is retried.
        """
        with authority_atomic(self._alias, received_at=bound.received_at):
            actor = _lock_actor(
                database_alias=self._alias,
                credential=command.credential,
                unavailable=S015PhaseAdvanceUnavailable,
            )
            bound.verify(command, actor, IntakeEntry.ADVANCE_PHASE, self._alias)
            organism_uuid = _canonical_uuid(command.living_organism_uuid, "living_organism_uuid")
            from_phase = _canonical_key(command.from_phase, "from_phase", 24)
            to_phase = _canonical_key(command.to_phase, "to_phase", 24)
            request_reference = _canonical_key(command.request_reference, "request_reference", 128)
            idempotency_key = _canonical_key(command.idempotency_key, "idempotency_key", 120)
            received_at = _validate_timestamp(bound.received_at, "received_at")
            effective_at = (
                received_at
                if command.effective_at is None
                else _validate_timestamp(command.effective_at, "effective_at")
            )
            if effective_at > received_at:
                raise ValueError("effective_at must not be later than received_at")

            target = {
                "action": self._action.value,
                "organism_uuid": organism_uuid,
                "from_phase": from_phase,
                "to_phase": to_phase,
            }
            fingerprint = _command_payload(
                domain=_PHASE_COMMAND_DOMAIN,
                schema="s015-phase-advance-v1",
                action=self._action,
                actor_identity_id=actor.identity_id,
                actor_access_epoch=actor.access_epoch,
                request_reference=request_reference,
                idempotency_key=idempotency_key,
                occurred_at=effective_at,
                target=target,
                command={
                    "effective_at": effective_at,
                    "received_at": received_at,
                    "schema": "s015-phase-advance-v1",
                },
            )
            acquire_organism_command_mutex(
                database_alias=self._alias,
                actor_identity_id=actor.identity_id,
                action=self._action,
                idempotency_key=idempotency_key,
            )
            replay = self._attempt_replay(
                actor=actor,
                request_reference=request_reference,
                payload_fingerprint=fingerprint,
                idempotency_key=idempotency_key,
                # This clock read supplies the present-time access check when serving the result; it does not replace the bound request's receipt time.
                viewer=ReplayViewer(actor.pk, actor.identity_id, self._clock()),
            )
            if replay is not None:
                return replay

            _lock_registered_organism(
                database_alias=self._alias,
                organism_id=organism_uuid,
            )
            try:
                organism = LivingOrganism.objects.using(self._alias).select_for_update().get(
                    organism_id=organism_uuid,
                )
            except LivingOrganism.DoesNotExist as exc:
                raise S015PhaseAdvanceNotFound("living organism not found") from exc
            actor_membership, role_refs = _lock_actor_membership_context(
                database_alias=self._alias,
                actor_identity_id=actor.identity_id,
                living_organism_id=organism_uuid,
                unavailable=S015PhaseAdvanceUnavailable,
            )
            request = S015PostFoundingAuthorityRequest(
                database_alias=self._alias,
                actor_pk=actor.pk,
                actor_identity_id=actor.identity_id,
                actor_access_epoch=actor.access_epoch,
                action=self._action,
                command_subtype=_PHASE_SUBTYPE,
                target_fingerprint=authority_target_fingerprint(target),
                request_reference=request_reference,
                idempotency_key=idempotency_key,
                evaluated_at=received_at,
                active_membership_head_reference=actor_membership.head_transition.lineage_reference,
                contextual_role_head_references=role_refs,
                effective_at=effective_at,
                target=target,
                living_organism_id=organism_uuid,
            )
            try:
                decision = self._authority.qualify(request)
            except S015AuthorityDenied as exc:
                if exc.refusal_code in {
                    S015AuthorityRefusalCode.PHASE_OUT_OF_ORDER,
                    S015AuthorityRefusalCode.PHASE_CONDITION_UNMET,
                    S015AuthorityRefusalCode.INV_CQ_ROUTE_REQUIRED,
                    S015AuthorityRefusalCode.SBG_GOVERNANCE_CIRCLE_MISSING,
                    S015AuthorityRefusalCode.SBG_ARRANGEMENT_MISSING,
                    S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
                    S015AuthorityRefusalCode.SBG_ARRANGEMENT_FIELD_UNSUPPORTED,
                    S015AuthorityRefusalCode.SBG_ELIGIBILITY_UNSUPPORTED,
                }:
                    raise S015AuthorityDenied(
                        self._authored_refusal_message(
                            exc.refusal_code,
                            organism,
                            to_phase,
                            effective_at,
                            received_at,
                            received_at=bound.received_at,
                        ),
                        refusal_code=exc.refusal_code,
                        disclosed_refusal_code=exc.disclosed_refusal_code,
                        authority_decision_reference=exc.authority_decision_reference,
                    ) from exc
                raise

            if organism.head_event_id is None or organism.founding_authority_basis_id is None:
                raise S015PhaseAdvanceTechnicalFailure(
                    _intake_tail(_PHASE_TECHNICAL_FAILURE_TEMPLATE, bound.received_at)
                )
            predecessor = LivingOrganismEvent.objects.using(self._alias).select_for_update().get(
                pk=organism.head_event_id,
            )
            event_uuid = uuid4()
            temporal_kind, temporal_reference = _resulting_temporal_basis(
                payload_fingerprint=fingerprint,
                effective_at=effective_at,
                received_at=received_at,
            )
            lineage_reference = "s015l1:" + hashlib.sha256(
                _PHASE_COMMAND_DOMAIN + canonical_json_bytes({
                    "event": event_uuid,
                    "organism": organism_uuid,
                    "fingerprint": fingerprint,
                })
            ).hexdigest()
            with connections[self._alias].cursor() as cursor:
                cursor.execute(
                    "SELECT l1_commitment FROM public.core_livingorganismevent WHERE id = %s",
                    [predecessor.pk],
                )
                predecessor_commitment = cursor.fetchone()
                if predecessor_commitment is None or predecessor_commitment[0] is None:
                    raise S015PhaseAdvanceTechnicalFailure(
                        _intake_tail(_PHASE_TECHNICAL_FAILURE_TEMPLATE, bound.received_at)
                    )
                cursor.execute(
                    "SELECT public.s015_0022_digest('s015p1', "
                    "public.s015_0022_parts_preimage(%s, %s::uuid))",
                    ["core_livingorganismevent", event_uuid],
                )
                parts_commitment = cursor.fetchone()[0]
                columns = {
                    "event_uuid": event_uuid,
                    "living_organism_id": organism.pk,
                    "sequence": predecessor.sequence + 1,
                    "predecessor_id": predecessor.pk,
                    "predecessor_sequence": predecessor.sequence,
                    "action": "PHASE_ADVANCE",
                    "prior_state": predecessor.resulting_state,
                    "resulting_state": predecessor.resulting_state,
                    "actor_id": None,
                    "actor_state": "LIVING_ORGANISM",
                    "actor_capacity": "STANDING",
                    "actor_access_epoch": None,
                    "enterer_identity_id": actor.pk,
                    "entry_mode": "TRANSPORT",
                    "composing_rule_state": "NA_TRANSPORT",
                    "composing_rule_reference": None,
                    "composer_state": "NA_TRANSPORT",
                    "composer_identity_id": None,
                    "effective_until": None,
                    "effective_until_state": "NONE",
                    "authority_basis_id": organism.founding_authority_basis_id,
                    "authority_decision_reference": decision.authority_decision_reference,
                    "evidence_reference": f"formation-phase:{from_phase}->{to_phase}",
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
            event = LivingOrganismEvent.objects.using(self._alias).get(pk=event_id)
            result = PhaseAdvanceResult(
                living_organism_uuid=organism_uuid,
                event_uuid=event.event_uuid,
                event_sequence=event.sequence,
                from_phase=from_phase,
                to_phase=to_phase,
                lineage_reference=lineage_reference,
                authority_reference=decision.authority_reference,
                authority_decision_reference=decision.authority_decision_reference,
            )
            return self._finalise(
                actor=actor,
                request_reference=request_reference,
                idempotency_key=idempotency_key,
                payload_fingerprint=fingerprint,
                result=result,
                received_at=bound.received_at,
            )

    def _phase_at(
        self,
        organism: LivingOrganism,
        effective_at: datetime,
        evaluated_at: datetime,
    ) -> str:
        """Return the LO's phase as of effective_at, known by evaluated_at.

        Take the latest PHASE_ADVANCE event with effective_at, received_at
        and recorded_at within those times, ordered by effective, occurred,
        received and recorded time, then sequence and event UUID. Its phase
        is the part after "->" in its evidence reference. With no such
        event, the phase is CONCEIVING.
        """
        evidence_reference = LivingOrganismEvent.objects.using(self._alias).filter(
            living_organism=organism,
            action="PHASE_ADVANCE",
            effective_at__lte=effective_at,
            received_at__lte=evaluated_at,
            recorded_at__lte=evaluated_at,
        ).order_by(
            "-effective_at", "-occurred_at", "-received_at", "-recorded_at", "-sequence", "-event_uuid",
        ).values_list("evidence_reference", flat=True).first()
        return (
            "CONCEIVING"
            if evidence_reference is None
            else evidence_reference.rsplit(":", 1)[1].split("->", 1)[1]
        )

    def _birthing_recorded(
        self,
        organism: LivingOrganism,
        effective_at: datetime,
        evaluated_at: datetime,
    ) -> bool:
        """Return whether a BIRTHING event exists with effective_at,
        received_at and recorded_at within the given times.
        """
        return LivingOrganismEvent.objects.using(self._alias).filter(
            living_organism=organism,
            action="BIRTHING",
            effective_at__lte=effective_at,
            received_at__lte=evaluated_at,
            recorded_at__lte=evaluated_at,
        ).exists()

    def _founding_content_ready(
        self,
        organism: LivingOrganism,
        effective_at: datetime,
        evaluated_at: datetime,
    ) -> bool:
        """Return whether version 1 of the LO's founding content, effective
        by effective_at and recorded by evaluated_at, has a name and a
        purpose that are not blank.
        """
        content = LOFoundingContentVersion.objects.using(self._alias).filter(
            living_organism=organism,
            version=1,
            effective_at__lte=effective_at,
            recorded_at__lte=evaluated_at,
        ).first()
        return content is not None and bool(content.name.strip()) and bool(content.purpose.strip())

    def _effective_governance_configuration(
        self,
        organism: LivingOrganism,
        effective_at: datetime,
        evaluated_at: datetime,
    ) -> LOGovernanceConfigurationVersion | None:
        """Return the LO's effective governance configuration version, or
        None.

        Take the latest EFFECT_GOVERNANCE_CONFIGURATION event with
        effective_at, received_at and recorded_at within the given times, in
        the same order as _phase_at. Its evidence reference must be
        "governance-configuration-version:<uuid>"; return the LO's version
        with that UUID. Return None when there is no event, the reference
        has another form or a malformed UUID, or no such version exists.
        """
        evidence_reference = LivingOrganismEvent.objects.using(self._alias).filter(
            living_organism=organism,
            action=OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION.value,
            effective_at__lte=effective_at,
            received_at__lte=evaluated_at,
            recorded_at__lte=evaluated_at,
        ).order_by(
            "-effective_at", "-occurred_at", "-received_at", "-recorded_at", "-sequence", "-event_uuid",
        ).values_list("evidence_reference", flat=True).first()
        prefix = "governance-configuration-version:"
        if evidence_reference is None or not evidence_reference.startswith(prefix):
            return None
        try:
            version_uuid = UUID(evidence_reference[len(prefix):])
        except ValueError:
            return None
        return LOGovernanceConfigurationVersion.objects.using(self._alias).filter(
            version_uuid=version_uuid,
            living_organism=organism,
        ).first()

    def _authored_refusal_message(
        self,
        code: S015AuthorityRefusalCode,
        organism: LivingOrganism,
        to_phase: str,
        effective_at: datetime,
        evaluated_at: datetime,
        *,
        received_at: datetime,
        rolled_back: bool = False,
    ) -> str:
        """Return user-facing text for a remapped phase-advance refusal.

        Read the current phase first, at the supplied time cuts.
        PHASE_OUT_OF_ORDER names it and the next phase; the next-phase
        lookup falls back to to_phase. INV_CQ_ROUTE_REQUIRED explains
        the unavailable route for a multi-member electorate. Otherwise
        try _sbg_readiness_message, with the opening ending in a full
        stop, and use any text it supplies.

        If no message was supplied and to_phase is GERMINATING, use
        the fixed founding-content explanation without re-reading that
        content. Otherwise re-read the effective governance
        configuration: report a missing configuration or an unset
        GOVERNANCE choice; if neither applies, raise
        S015PhaseAdvanceTechnicalFailure with the restated technical
        text (Stop 3 R-FORM-14).

        With rolled_back False (the default), every text is the refusal of
        an admitted request, after qualify recorded the REFUSED decision
        (class A). It ends with the intake tail: INTEVIA recorded the
        refusal and that the request was received at received_at, the bound
        request's receipt time (Stop 3 R-FORM-15a to -22; PKT-CLK-3 CLK3-2).

        With rolled_back True (the SB-G guard arm, A3 §7.4, class B), no
        decision was recorded and only the SB-G texts apply: the opening
        says nothing from this request was saved except the receipt record
        and that the LO's governance settings changed after receipt, and
        the tail says a resend is judged as of the original receipt
        (R-FORM-32a, -32b, -32c, -32e, -32f). A code without an SB-G text
        then raises RuntimeError.

        This method is not given the refusal's facts. Phase,
        configuration and delegated diagnostic read failures propagate.
        """
        if rolled_back:
            sbg_message = _sbg_readiness_message(
                code=code,
                act="The phase was not advanced",
                operation="PHASE_ADVANCE",
                database_alias=self._alias,
                organism=organism,
                effective_at=effective_at,
                recorded_at=evaluated_at,
                opening_end=_intake_tail(_ROLLED_BACK_OPENING_END, received_at),
                tail=_TAIL_RESEND_ORIGINAL_TIME,
            )
            if sbg_message is None:
                raise RuntimeError("the SB-G guard arm has no text for this code")
            return sbg_message
        tail = _intake_tail(_TAIL_REFUSAL_RECORDED, received_at)
        current_phase = self._phase_at(organism, effective_at, evaluated_at)
        if code is S015AuthorityRefusalCode.PHASE_OUT_OF_ORDER:
            next_phase = _NEXT_PHASE.get(current_phase, to_phase)
            return (
                f"The phase was not advanced: this LO is in "
                f"{_PHASE_NAMES.get(current_phase, current_phase)}, and phases advance one at a time "
                "(Conceiving → Germinating → Birthing → Birthed). "
                f"Advance to {_PHASE_NAMES.get(next_phase, next_phase)} first. {tail}"
            )
        if code is S015AuthorityRefusalCode.INV_CQ_ROUTE_REQUIRED:
            return (
                "This LO's electorate has more than one member, so this act needs the electorate's "
                "qualified decision, and no CQ route is operative in this release (S3-IL-CQ). "
                "This is a known limit, not a refusal of the act; it lifts when "
                f"DISCUSSION's CQ is delivered. {tail}"
            )
        sbg_message = _sbg_readiness_message(
            code=code,
            act="The phase was not advanced",
            operation="PHASE_ADVANCE",
            database_alias=self._alias,
            organism=organism,
            effective_at=effective_at,
            recorded_at=evaluated_at,
            opening_end=".",
            tail=tail,
        )
        if sbg_message is not None:
            return sbg_message
        if to_phase == "GERMINATING":
            provision = "founding content (name and purpose)"
            return (
                f"The phase was not advanced: moving to "
                f"{_PHASE_NAMES.get(to_phase, to_phase)} needs {provision}, which is not yet "
                f"established. Establish it, then advance. {tail}"
            )
        configuration = self._effective_governance_configuration(
            organism,
            effective_at,
            evaluated_at,
        )
        if configuration is None:
            provision = "an effective governance configuration"
            return (
                f"The phase was not advanced: moving to "
                f"{_PHASE_NAMES.get(to_phase, to_phase)} needs {provision}, which is not yet "
                f"established. Establish it, then advance. {tail}"
            )
        if configuration.governance_enabled is None:
            return (
                "The phase was not advanced. Moving to Birthing needs this LO's "
                "GOVERNANCE choice to be confirmed in its governance configuration, and no choice is "
                "recorded yet: the offered default is not a confirmation. Confirm Yes or No by "
                f"effecting the governance configuration, then advance. {tail}"
            )
        raise S015PhaseAdvanceTechnicalFailure(
            _intake_tail(_PHASE_TECHNICAL_FAILURE_TEMPLATE, received_at)
        )

_BIRTHING_SUBTYPE = "TRANSITION_LIVING_ORGANISM/BIRTHING"
_BIRTHING_COMMAND_DOMAIN = b"INTEVIA:S015:TRANSITION_LIVING_ORGANISM/BIRTHING:COMMAND:v2\x00"


@dataclass(frozen=True, slots=True)
class BirthingCommand:
    """Inputs to birth_living_organism.

    credential is the actor's Django User; living_organism_uuid names
    the LO. request_reference and idempotency_key identify the request.
    offering_choice, policy_version and policy_text_sha256 are the
    Birthing entitlement election: FREE or COMPLIMENTARY_BETA, and the
    policy version and text digest the actor accepted. effective_at is
    when Birthing takes effect; None means the time the service
    receives the command.

    There is no received_at field: the durable intake assigns it when the
    request is admitted.
    """
    credential: User
    living_organism_uuid: UUID
    request_reference: str
    idempotency_key: str
    offering_choice: str
    policy_version: int
    policy_text_sha256: str
    effective_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class BirthingResult:
    """Result of Birthing an LO.

    Carries the LO's UUID, the snapshot's UUID, the BIRTHING event's
    UUID and sequence, the receipt's lineage reference, and the
    authority and authority-decision references. The election fields
    carry the offering choice, policy version and text digest, the
    election event's UUID, the beta entitlement's UUID (None for FREE),
    and the election's own authority and authority-decision references.

    Declares no audience-specific field restrictions. New and replayed
    results are both served through serve_replay, which can still
    withhold authority_reference.
    """
    living_organism_uuid: UUID
    snapshot_uuid: UUID
    event_uuid: UUID
    event_sequence: int
    lineage_reference: str
    authority_reference: str
    authority_decision_reference: str
    offering_choice: str | None = None
    policy_version: int | None = None
    policy_text_sha256: str | None = None
    entitlement_event_uuid: UUID | None = None
    entitlement_uuid: UUID | None = None
    entitlement_authority_reference: str | None = None
    entitlement_authority_decision_reference: str | None = None

    def receipt_payload(self) -> dict[str, str | int | None]:
            """Return the receipt form.

            Convert UUIDs to strings and absent optional UUIDs to None.
            Keep the other fields as they are.
            """
            return {
                "living_organism_uuid": str(self.living_organism_uuid),
                "snapshot_uuid": str(self.snapshot_uuid),
                "event_uuid": str(self.event_uuid),
                "event_sequence": self.event_sequence,
                "lineage_reference": self.lineage_reference,
                "authority_reference": self.authority_reference,
                "authority_decision_reference": self.authority_decision_reference,
                "offering_choice": self.offering_choice,
                "policy_version": self.policy_version,
                "policy_text_sha256": self.policy_text_sha256,
                "entitlement_event_uuid": (
                    None
                    if self.entitlement_event_uuid is None
                    else str(self.entitlement_event_uuid)
                ),
                "entitlement_uuid": (
                    None if self.entitlement_uuid is None else str(self.entitlement_uuid)
                ),
                "entitlement_authority_reference": self.entitlement_authority_reference,
                "entitlement_authority_decision_reference": (
                    self.entitlement_authority_decision_reference
                ),
            }

    @classmethod
    def replay_field_audiences(cls, result: BirthingResult) -> dict[str, str]:
            """Return an empty mapping for this exact result class.

            Raise TypeError for a result of any other class.
            """
            if type(result) is not cls:
                raise TypeError("birthing replay result has the wrong type")
            return {}


def _birthing_result_from_payload(payload: object) -> BirthingResult:
    """Rebuild a Birthing result from a receipt payload.

    Require a dict of exactly that type, else raise
    S015CommandReplayMismatch. Construct the three required UUIDs and,
    when present and not None, the election event and entitlement
    UUIDs; missing required fields, and TypeError or ValueError from
    construction, become S015CommandReplayMismatch. Other exceptions
    propagate.

    The optional election fields default to None when absent. The
    sequence and the other fields are taken as stored, without checking
    their types or values.
    """
    if type(payload) is not dict:
            raise S015CommandReplayMismatch("completed receipt does not match request")
    try:
            return BirthingResult(
                living_organism_uuid=UUID(payload["living_organism_uuid"]),
                snapshot_uuid=UUID(payload["snapshot_uuid"]),
                event_uuid=UUID(payload["event_uuid"]),
                event_sequence=payload["event_sequence"],
                lineage_reference=payload["lineage_reference"],
                authority_reference=payload["authority_reference"],
                authority_decision_reference=payload["authority_decision_reference"],
                offering_choice=payload.get("offering_choice"),
                policy_version=payload.get("policy_version"),
                policy_text_sha256=payload.get("policy_text_sha256"),
                entitlement_event_uuid=(
                    None
                    if payload.get("entitlement_event_uuid") is None
                    else UUID(payload["entitlement_event_uuid"])
                ),
                entitlement_uuid=(
                    None
                    if payload.get("entitlement_uuid") is None
                    else UUID(payload["entitlement_uuid"])
                ),
                entitlement_authority_reference=payload.get(
                    "entitlement_authority_reference"
                ),
                entitlement_authority_decision_reference=payload.get(
                    "entitlement_authority_decision_reference"
                ),
            )
    except (KeyError, TypeError, ValueError) as exc:
            raise S015CommandReplayMismatch("completed receipt does not match request") from exc


class S015BirthingNotFound(Exception):
    """The named LO was absent at the service's own row lookup.

    birth_living_organism raises this after its registered-organism
    lock. That earlier lock can itself raise a database error for a
    missing LO, before this translation is reached.
    """
    pass


class S015BirthingUnavailable(Exception):
    """The actor's locked identity or membership context is
    unavailable.

    _lock_actor raises this when no Identity holds the credential, or
    the Identity or its credential is inactive.
    _lock_actor_membership_context raises it when the membership is
    missing, is not ACTIVE or has no head transition.
    """
    pass


class S015BirthingTechnicalFailure(Exception):
    """Birthing could not be completed for a technical reason or a
    record inconsistency.

    Raised when birth_living_organism is called inside an atomic block;
    when the LO has no head event or founding basis; when a record the
    snapshot needs is missing; when the head event has no l1
    commitment; when the database refuses the snapshot as invalid; and,
    after rollback, for the entitlement grant guard's failure.
    """
    pass


@contextmanager
def _birthing_guard_atomic(
    alias: str,
    *,
    received_at: datetime | None = None,
) -> Iterator[None]:
    """Run authority_atomic, and translate one guard failure after it.

    An IntegrityError that escapes the block is re-raised as
    S015BirthingTechnicalFailure only when no atomic block remains open
    and _is_entitlement_granted_guard_failure identifies the entitlement
    grant guard (SE_ENTITLEMENT_WITHOUT_GRANT). Other IntegrityErrors,
    and any error inside an enclosing transaction, propagate unchanged.
    With received_at (the bound request's receipt time), the text is the
    restated technical text naming that time (Stop 3 R-FORM-24); the
    command's writes are rolled back and the intake record stands.
    S015AuthorityDenied keeps authority_atomic's deferral.
    """
    try:
        with authority_atomic(alias, received_at=received_at):
            yield
    except IntegrityError as exc:
        if (
            not connections[alias].in_atomic_block
            and _is_entitlement_granted_guard_failure(exc)
        ):
            raise S015BirthingTechnicalFailure(
                "BIRTHING could not be completed because of a technical problem; "
                "nothing was saved. Try again; if it persists, report it."
                if received_at is None
                else _intake_tail(_BIRTHING_TECHNICAL_FAILURE_TEMPLATE, received_at)
            ) from exc
        raise


class OrganismBirthingService(_OrganismCommandService):
    """Birth an LO (TRANSITION_LIVING_ORGANISM/BIRTHING).

    Birthing is the LO's move from the Birthing phase to Birthed, once.
    It records the constitutional snapshot, the BIRTHING event on the
    LO's chain and the Birthing entitlement election, with the authority
    decisions and a receipt. The LO's state does not change.

    Extends _OrganismCommandService for replay and the receipt, and
    uses an OrganismEntitlementService on the same authority, clock and
    alias for the election. birth_living_organism admits the request
    through the durable intake, and Birthing runs as of the intake's
    received_at; the injected clock gives only the replay viewer's
    present time.
    """
    _action = OrganismAction.TRANSITION_LIVING_ORGANISM
    _not_found = S015BirthingNotFound
    _authority_unavailable = S015BirthingUnavailable

    def __init__(
            self,
            *,
            clock: Callable[[], datetime],
            authority: S015Authority | None = None,
            database_alias: str = "default",
    ) -> None:
            """Build the command base and the entitlement service.

            Require a callable clock, else TypeError, and a non-empty
            str database_alias, else ValueError. With no authority,
            build S015Authority using the production application
            provider on that alias.

            The command base requires an S015Authority, else TypeError,
            and the same database alias, else ValueError.
            Pass the authority, clock and alias to the base and
            use them for the entitlement service too.
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
            super().__init__(authority=authority, clock=clock, database_alias=database_alias)
            self._entitlement_service = OrganismEntitlementService(
                authority=self._authority,
                clock=clock,
                database_alias=database_alias,
            )

    def _replay_result(self, payload: object) -> BirthingResult:
            """Rebuild the stored Birthing result.

            Delegate to _birthing_result_from_payload.
            """
            return _birthing_result_from_payload(payload)

    def birth_living_organism(self, command: BirthingCommand) -> BirthingResult:
        """Admit a Birthing request and run it at its bound time (D §5.1).

        Refuse a call inside an atomic block with
        S015BirthingTechnicalFailure before any statement, then require
        the admission state and the active entry, build the intake
        shape, resolve the actor and admit the request through the
        durable intake (one committed row). Birthing then runs in
        _execute_birth_living_organism as of the intake's received_at.

        Two arms run outside any atomic block, after the command's
        transaction rolled back (amendment A3 §7.4); each keeps the
        IntegrityError as the cause and retries nothing:
        - SB-G guard arm (class B): a cause carrying SQLSTATE 23000 and
          one of the six Birthing SB-G codes as its primary message
          becomes S015AuthorityDenied with that code, no decision
          reference and the rolled-back text (R-FORM-31a to -31f);
        - snapshot uniqueness (DF-2): a cause carrying SQLSTATE 23505 on
          the one-snapshot-per-LO constraint becomes, when the LO has a
          BIRTHING event (one the qualification did not see: completed
          after receipt, or effective after the request's effective
          time), S015AuthorityDenied(BIRTHING_ALREADY_RECORDED) with no
          decision reference and that event's effective date
          (R-FORM-26-B); otherwise S015BirthingTechnicalFailure, the
          snapshot left by a refused election (F3-02).
        The LO is re-read after the rollback. Every other error
        propagates unchanged.
        """
        if connections[self._alias].in_atomic_block:
            raise S015BirthingTechnicalFailure(
                "BIRTHING could not be completed because of a technical problem; "
                "nothing was saved. Try again; if it persists, report it."
            )
        from src.intevia.services.command_intake import (
            ACTIVE_ENTRIES,
            IntakeAdmissionTechnicalFailure,
            IntakeEntry,
            _require_admission_state,
            _resolve_actor,
            admit_request,
            intake_shape,
        )

        entry = IntakeEntry.BIRTHING
        _require_admission_state(self._alias, entry)
        if entry not in ACTIVE_ENTRIES:
            raise IntakeAdmissionTechnicalFailure("BIRTHING is not active")
        shape = intake_shape(entry, command)
        actor_ref = _resolve_actor(self._alias, command.credential)
        bound = admit_request(self._alias, entry, actor_ref, shape)
        try:
            return self._execute_birth_living_organism(command, bound)
        except IntegrityError as exc:
            if connections[self._alias].in_atomic_block:
                raise
            code = _sbg_guard_refusal_code(exc, _SBG_BIRTHING_GUARD_CODES)
            snapshot_taken = code is None and _is_constraint_violation(
                exc,
                sqlstate="23505",
                constraint_name=_BIRTHING_SNAPSHOT_UNIQUE,
            )
            if code is None and not snapshot_taken:
                raise
            organism = LivingOrganism.objects.using(self._alias).get(
                organism_id=_canonical_uuid(command.living_organism_uuid, "living_organism_uuid"),
            )
            if code is not None:
                effective_at = (
                    bound.received_at
                    if command.effective_at is None
                    else _validate_timestamp(command.effective_at, "effective_at")
                )
                raise S015AuthorityDenied(
                    self._authored_refusal_message(
                        code,
                        organism,
                        effective_at,
                        bound.received_at,
                        rolled_back=True,
                    ),
                    refusal_code=code,
                    disclosed_refusal_code=code,
                    authority_decision_reference=None,
                ) from exc
            birth = LivingOrganismEvent.objects.using(self._alias).filter(
                living_organism=organism,
                action="BIRTHING",
            ).order_by("sequence").first()
            if birth is not None:
                raise S015AuthorityDenied(
                    _intake_tail(
                        _BIRTHING_OVERTAKEN_TEMPLATE.replace(
                            "{born_date}",
                            birth.effective_at.date().isoformat(),
                        ),
                        bound.received_at,
                    ),
                    refusal_code=S015AuthorityRefusalCode.BIRTHING_ALREADY_RECORDED,
                    disclosed_refusal_code=S015AuthorityRefusalCode.BIRTHING_ALREADY_RECORDED,
                    authority_decision_reference=None,
                ) from exc
            raise S015BirthingTechnicalFailure(
                _intake_tail(_ORPHAN_SNAPSHOT_TEMPLATE, bound.received_at)
            ) from exc

    def _execute_birth_living_organism(
        self,
        command: BirthingCommand,
        bound: BoundRequest,
    ) -> BirthingResult:
            """Execute a bound Birthing command (D §5.2).

            birth_living_organism has admitted the request. Inside
            _birthing_guard_atomic, opened with the bound receipt time,
            first lock the actor's Identity and credential. Missing or
            inactive identity or credential raises
            S015BirthingUnavailable. Then verify the bound request
            against the command and the actor.

            Require a UUID and canonical request and idempotency keys,
            limited to 128 and 120 characters. Take received_at from the
            bound request (on a retry, the original receipt time); use
            it when effective_at is omitted. Require
            aware datetime values, converted to UTC. Invalid types
            raise TypeError; invalid keys or a future effective_at
            raise ValueError.

            Fingerprint the actor UUID and access epoch, action, LO,
            request reference, idempotency key, schema, election
            inputs and resolved times. occurred_at is effective_at;
            received_at is included. Acquire the actor/action/key
            mutex and serve a matching completed receipt before
            new work. A mismatched reference or fingerprint raises
            S015CommandReplayMismatch. Serving uses a fresh clock
            reading; a changed receipt time prevents matching replay.

            Take the registered-organism lock, then lock the LO row.
            The first can raise a database error for an absent LO;
            absence at the row lookup raises S015BirthingNotFound.
            Require the head event and founding basis, otherwise
            raise S015BirthingTechnicalFailure. Lock the actor's
            membership and active role heads; require ACTIVE membership
            and its head transition, else S015BirthingUnavailable.
            These locks inspect stored states, not a temporal fold.

            Qualify Birthing for a new snapshot UUID, evaluated at
            received_at with effective_at supplied separately.
            Remap the handled Birthing, CQ, electorate and SB-G denials
            to authored text, keeping both codes and the decision
            reference. Other denials propagate unchanged.

            Lock the head event and collect the snapshot inputs:
            - configuration, settings and question: take the latest
              effect event of each, effective by effective_at,
              received and recorded by received_at; resolve its
              named version by UUID and LO, without another
              version-time filter. A missing, wrongly prefixed or
              non-UUID reference resolves to None, as does a
              settings row with an entitlement choice or route;
            - founding content: version 1, effective by effective_at
              and recorded by received_at;
            - founding event: sequence 1, with no time filter;
            - founder's FOUNDING acceptance for this LO: recorded by
              received_at, latest by recorded time and primary key;
              no accepted-time or effective-time predicate;
            - GOVERNANCE_CONSTITUTION, only when GOVERNANCE is True:
              effective by effective_at and recorded by received_at,
              latest by effective time, recorded time and version.

            Missing configuration, settings, question, content,
            founding event or acceptance raises
            S015BirthingTechnicalFailure. This local check does not
            require a governance rule. The snapshot holds those
            references, the chain range through the locked head,
            empty documents and laws, and the rule reference or None.

            Write the snapshot, leaving its commitment and recorded_at
            to the database. Prepare the entitlement election using
            the same received_at and effective_at. That helper checks
            the choice, policy, settings, prior election and provider
            provenance, writes its version, then qualifies
            EFFECT_ENTITLEMENT_ELECTION for that version and snapshot.
            Invalid choice or policy arguments raise ValueError;
            an unrecorded or mismatched policy text, missing
            settings, an existing election or invalid provenance
            raise S015EntitlementTechnicalFailure. Other helper
            errors propagate according to their own contracts.

            Require the predecessor's l1 commitment, else
            S015BirthingTechnicalFailure. Insert the BIRTHING event
            at the next sequence, with equal prior and resulting state.
            Its actor state is LIVING_ORGANISM, capacity STANDING,
            with no actor Identity; the person is the TRANSPORT enterer.
            Record the founding basis, decision reference and
            "birthing-snapshot:<uuid>" evidence. occurred_at equals
            effective_at; a retrospective temporal basis is supplied
            when it precedes received_at. Leave recorded_at to the
            database. Refresh the cache at effective_at with database
            clock_timestamp() as its known time.

            Only within snapshot creation, election preparation,
            event insertion and cache refresh, an IntegrityError whose
            string contains BIRTHING_SNAPSHOT_INVALID becomes
            S015BirthingTechnicalFailure. Other integrity errors
            propagate. Completion, finalisation and atomic exit are
            outside that handler.

            Delegate election completion to the entitlement service:
            append its event and, for COMPLIMENTARY_BETA, compose the
            grant from the Birthing event; FREE returns no entitlement.
            Write the receipt, rebuild its stored result and serve it
            at a fresh clock reading before the atomic scope exits.

            An election denial reaches authority_atomic only if deletion
            of its new version succeeds. If that denial reaches the
            wrapper and commit succeeds, the earlier snapshot and
            Birthing QUALIFIED decision can remain, together with the
            election REFUSED decision; no Birthing event or receipt
            has yet been written. Cleanup errors replace the denial.
            More generally, authority_atomic defers S015AuthorityDenied
            until successful atomic exit; it does not isolate refusal
            evidence from other writes. That denial is re-raised with
            the text stating those effects and that no Birthing of the
            LO can complete until the defect is repaired (Stop 3 F3-01
            as amended by A3), its codes and decision reference kept. Other
            escaping failures roll back the command's transaction; the
            intake record of the request stands. An exit failure does
            not establish retention.

            After rollback, the grant-guard wrapper translates its
            matched entitlement integrity error. Contention translation
            handles qualifying lock errors outside the atomic block.
            Other failures propagate; nothing is retried here.
            """
            # PKT-SE-K3: translate its deferred grant guard only after rollback.
            with _birthing_guard_atomic(self._alias, received_at=bound.received_at):
                actor = _lock_actor(
                    database_alias=self._alias,
                    credential=command.credential,
                    unavailable=S015BirthingUnavailable,
                )
                bound.verify(command, actor, IntakeEntry.BIRTHING, self._alias)
                organism_uuid = _canonical_uuid(command.living_organism_uuid, "living_organism_uuid")
                request_reference = _canonical_key(command.request_reference, "request_reference", 128)
                idempotency_key = _canonical_key(command.idempotency_key, "idempotency_key", 120)
                received_at = _validate_timestamp(bound.received_at, "received_at")
                effective_at = (
                    received_at
                    if command.effective_at is None
                    else _validate_timestamp(command.effective_at, "effective_at")
                )
                if effective_at > received_at:
                    raise ValueError("effective_at must not be later than received_at")

                command_target = {
                    "action": self._action.value,
                    "organism_uuid": organism_uuid,
                }
                fingerprint = _command_payload(
                    domain=_BIRTHING_COMMAND_DOMAIN,
                    schema="s015-birthing-v2",
                    action=self._action,
                    actor_identity_id=actor.identity_id,
                    actor_access_epoch=actor.access_epoch,
                    request_reference=request_reference,
                    idempotency_key=idempotency_key,
                    occurred_at=effective_at,
                    target=command_target,
                    command={
                        "effective_at": effective_at,
                        "received_at": received_at,
                        "offering_choice": command.offering_choice,
                        "policy_text_sha256": command.policy_text_sha256,
                        "policy_version": command.policy_version,
                        "schema": "s015-birthing-v2",
                    },
                )
                acquire_organism_command_mutex(
                    database_alias=self._alias,
                    actor_identity_id=actor.identity_id,
                    action=self._action,
                    idempotency_key=idempotency_key,
                )
                replay = self._attempt_replay(
                    actor=actor,
                    request_reference=request_reference,
                    payload_fingerprint=fingerprint,
                    idempotency_key=idempotency_key,
                    # This clock read supplies the present-time access check when serving the result; it does not replace the bound request's receipt time.
                    viewer=ReplayViewer(actor.pk, actor.identity_id, self._clock()),
                )
                if replay is not None:
                    return replay

                _lock_registered_organism(
                    database_alias=self._alias,
                    organism_id=organism_uuid,
                )
                try:
                    organism = LivingOrganism.objects.using(self._alias).select_for_update().get(
                        organism_id=organism_uuid,
                    )
                except LivingOrganism.DoesNotExist as exc:
                    raise S015BirthingNotFound("living organism not found") from exc
                if organism.head_event_id is None or organism.founding_authority_basis_id is None:
                    raise S015BirthingTechnicalFailure(
                        _intake_tail(_BIRTHING_TECHNICAL_FAILURE_TEMPLATE, bound.received_at)
                    )

                actor_membership, role_refs = _lock_actor_membership_context(
                    database_alias=self._alias,
                    actor_identity_id=actor.identity_id,
                    living_organism_id=organism_uuid,
                    unavailable=S015BirthingUnavailable,
                )
                snapshot_uuid = uuid4()
                authority_target = {
                    **command_target,
                    "snapshot_uuid": str(snapshot_uuid),
                }
                request = S015PostFoundingAuthorityRequest(
                    database_alias=self._alias,
                    actor_pk=actor.pk,
                    actor_identity_id=actor.identity_id,
                    actor_access_epoch=actor.access_epoch,
                    action=self._action,
                    command_subtype=_BIRTHING_SUBTYPE,
                    target_fingerprint=authority_target_fingerprint(authority_target),
                    request_reference=request_reference,
                    idempotency_key=idempotency_key,
                    evaluated_at=received_at,
                    active_membership_head_reference=actor_membership.head_transition.lineage_reference,
                    contextual_role_head_references=role_refs,
                    effective_at=effective_at,
                    target=authority_target,
                    living_organism_id=organism_uuid,
                )
                try:
                    decision = self._authority.qualify(request)
                except S015AuthorityDenied as exc:
                    if exc.refusal_code in {
                        S015AuthorityRefusalCode.BIRTHING_PREREQUISITE_MISSING,
                        S015AuthorityRefusalCode.BIRTHING_ALREADY_RECORDED,
                        S015AuthorityRefusalCode.INV_CQ_ROUTE_REQUIRED,
                        S015AuthorityRefusalCode.SB_BIRTHING_ELECTORATE_EMPTY,
                        S015AuthorityRefusalCode.SBG_GOVERNANCE_CIRCLE_MISSING,
                        S015AuthorityRefusalCode.SBG_ARRANGEMENT_MISSING,
                        S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
                        S015AuthorityRefusalCode.SBG_ARRANGEMENT_FIELD_UNSUPPORTED,
                        S015AuthorityRefusalCode.SBG_ELIGIBILITY_UNSUPPORTED,
                        S015AuthorityRefusalCode.SBG_NO_INITIAL_PARTICIPATION_ROUTE,
                    }:
                        raise S015AuthorityDenied(
                            self._authored_refusal_message(
                                exc.refusal_code,
                                organism,
                                effective_at,
                                received_at,
                            ),
                            refusal_code=exc.refusal_code,
                            disclosed_refusal_code=exc.disclosed_refusal_code,
                            authority_decision_reference=exc.authority_decision_reference,
                        ) from exc
                    raise

                predecessor = LivingOrganismEvent.objects.using(self._alias).select_for_update().get(
                    pk=organism.head_event_id,
                )
                configuration = _effective_version(
                    self._alias, organism, OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
                    effective_at, received_at,
                )
                settings_version = _effective_version(
                    self._alias, organism, OrganismAction.EFFECT_APPLICATION_SETTINGS,
                    effective_at, received_at,
                )
                question_version = _effective_version(
                    self._alias, organism, OrganismAction.EFFECT_APPLICATION_QUESTION,
                    effective_at, received_at,
                )
                content = LOFoundingContentVersion.objects.using(self._alias).filter(
                    living_organism=organism,
                    version=1,
                    effective_at__lte=effective_at,
                    recorded_at__lte=received_at,
                ).first()
                founding_event = LivingOrganismEvent.objects.using(self._alias).filter(
                    living_organism=organism,
                    sequence=1,
                ).first()
                acceptance = PlatformTermsAcceptance.objects.using(self._alias).filter(
                    acceptance_kind=PlatformTermsAcceptance.Kind.FOUNDING,
                    subject_reference=f"living-organism:{organism.organism_id}",
                    accepted_by_identity_id=organism.founder_identity_id,
                    recorded_at__lte=received_at,
                ).order_by("-recorded_at", "-pk").first()
                governance_rule = None
                if configuration is not None and configuration.governance_enabled is True:
                    governance_rule = GovernedRule.objects.using(self._alias).filter(
                        scope_kind="LIVING_ORGANISM",
                        scope_organism=organism,
                        code="GOVERNANCE_CONSTITUTION",
                        effective_from__lte=effective_at,
                        recorded_at__lte=received_at,
                    ).order_by("-effective_from", "-recorded_at", "-version").first()
                if (
                    configuration is None
                    or settings_version is None
                    or question_version is None
                    or content is None
                    or founding_event is None
                    or acceptance is None
                ):
                    raise S015BirthingTechnicalFailure(
                        _intake_tail(_SNAPSHOT_MISMATCH_TEMPLATE, bound.received_at)
                    )

                body = {
                    "constitutional_documents": [],
                    "laws": [],
                    "settings": {
                        "governance_configuration": f"governance-configuration-version:{configuration.version_uuid}",
                        "application_settings": f"application-settings-version:{settings_version.version_uuid}",
                        "application_question": f"application-question-version:{question_version.version_uuid}",
                    },
                    "founding_content": f"founding-content:{content.content_uuid}@{content.version}",
                    "platform_terms_acceptance": f"platform-terms-acceptance:{acceptance.acceptance_uuid}",
                    "formation_history_reference": {
                        "lo_chain_sequence_range": [1, predecessor.sequence],
                    },
                    "governance_constitution": (
                        None
                        if governance_rule is None
                        else f"rule:{governance_rule.rule_uuid}@{governance_rule.version}"
                    ),
                }
                try:
                    LOBirthingSnapshot.objects.using(self._alias).create(
                        snapshot_uuid=snapshot_uuid,
                        living_organism=organism,
                        body=body,
                        snapshot_commitment=None,
                        effective_at=effective_at,
                        received_at=received_at,
                    )
                    try:
                        prepared_election = self._entitlement_service.prepare_birthing_election(
                            command=command,
                            actor=actor,
                            organism=organism,
                            role_refs=role_refs,
                            snapshot_uuid=snapshot_uuid,
                            request_reference=request_reference,
                            idempotency_key=idempotency_key,
                            actor_membership=actor_membership,
                            occurred_at=effective_at,
                            effective_at=effective_at,
                            received_at=received_at,
                        )
                    except S015AuthorityDenied as exc:
                        # F3-01 (DEC-F3 (i)): the text states the current effects and the
                        # binding; codes and decision reference are kept, and authority_atomic
                        # still defers the denial and commits the block, as before.
                        raise S015AuthorityDenied(
                            _intake_tail(
                                _ELECTION_DENIED_TEMPLATE.replace(
                                    "{refusal_code}",
                                    exc.disclosed_refusal_code.value,
                                ),
                                bound.received_at,
                            ),
                            refusal_code=exc.refusal_code,
                            disclosed_refusal_code=exc.disclosed_refusal_code,
                            authority_decision_reference=exc.authority_decision_reference,
                        ) from exc
                    event_uuid = uuid4()
                    temporal_kind, temporal_reference = _resulting_temporal_basis(
                        payload_fingerprint=fingerprint,
                        effective_at=effective_at,
                        received_at=received_at,
                    )
                    lineage_reference = "s015l1:" + hashlib.sha256(
                        _BIRTHING_COMMAND_DOMAIN + canonical_json_bytes({
                            "event": event_uuid,
                            "organism": organism_uuid,
                            "fingerprint": fingerprint,
                        })
                    ).hexdigest()
                    with connections[self._alias].cursor() as cursor:
                        cursor.execute(
                            "SELECT l1_commitment FROM public.core_livingorganismevent WHERE id = %s",
                            [predecessor.pk],
                        )
                        predecessor_commitment = cursor.fetchone()
                        if predecessor_commitment is None or predecessor_commitment[0] is None:
                            raise S015BirthingTechnicalFailure(
                                _intake_tail(
                                    _BIRTHING_TECHNICAL_FAILURE_TEMPLATE,
                                    bound.received_at,
                                )
                            )
                        cursor.execute(
                            "SELECT public.s015_0022_digest('s015p1', "
                            "public.s015_0022_parts_preimage(%s, %s::uuid))",
                            ["core_livingorganismevent", event_uuid],
                        )
                        parts_commitment = cursor.fetchone()[0]
                        columns = {
                            "event_uuid": event_uuid,
                            "living_organism_id": organism.pk,
                            "sequence": predecessor.sequence + 1,
                            "predecessor_id": predecessor.pk,
                            "predecessor_sequence": predecessor.sequence,
                            "action": "BIRTHING",
                            "prior_state": predecessor.resulting_state,
                            "resulting_state": predecessor.resulting_state,
                            "actor_id": None,
                            "actor_state": "LIVING_ORGANISM",
                            "actor_capacity": "STANDING",
                            "actor_access_epoch": None,
                            "enterer_identity_id": actor.pk,
                            "entry_mode": "TRANSPORT",
                            "composing_rule_state": "NA_TRANSPORT",
                            "composing_rule_reference": None,
                            "composer_state": "NA_TRANSPORT",
                            "composer_identity_id": None,
                            "effective_until": None,
                            "effective_until_state": "NONE",
                            "authority_basis_id": organism.founding_authority_basis_id,
                            "authority_decision_reference": decision.authority_decision_reference,
                            "evidence_reference": f"birthing-snapshot:{snapshot_uuid}",
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
                except IntegrityError as exc:
                    if "BIRTHING_SNAPSHOT_INVALID" not in str(exc):
                        raise
                    raise S015BirthingTechnicalFailure(
                        _intake_tail(_SNAPSHOT_MISMATCH_TEMPLATE, bound.received_at)
                    ) from exc

                event = LivingOrganismEvent.objects.using(self._alias).get(pk=event_id)
                entitlement_event, entitlement = (
                    self._entitlement_service.complete_birthing_election(
                        prepared_election,
                        birthing_event_uuid=event.event_uuid,
                    )
                )
                result = BirthingResult(
                    living_organism_uuid=organism_uuid,
                    snapshot_uuid=snapshot_uuid,
                    event_uuid=event.event_uuid,
                    event_sequence=event.sequence,
                    lineage_reference=lineage_reference,
                    authority_reference=decision.authority_reference,
                    authority_decision_reference=decision.authority_decision_reference,
                    offering_choice=command.offering_choice,
                    policy_version=command.policy_version,
                    policy_text_sha256=command.policy_text_sha256,
                    entitlement_event_uuid=entitlement_event.event_uuid,
                    entitlement_uuid=(
                        None if entitlement is None else entitlement.entitlement_uuid
                    ),
                    entitlement_authority_reference=(
                        prepared_election.decision.authority_reference
                    ),
                    entitlement_authority_decision_reference=(
                        prepared_election.decision.authority_decision_reference
                    ),
                )
                return self._finalise(
                    actor=actor,
                    request_reference=request_reference,
                    idempotency_key=idempotency_key,
                    payload_fingerprint=fingerprint,
                    result=result,
                    received_at=bound.received_at,
                )

    def _authored_refusal_message(
            self,
            code: S015AuthorityRefusalCode,
            organism: LivingOrganism,
            effective_at: datetime,
            received_at: datetime,
            *,
            rolled_back: bool = False,
    ) -> str:
            """Return authored text for a remapped Birthing refusal.

            With rolled_back False (the default), every text is the refusal of
            an admitted request, after qualify recorded the REFUSED decision
            (class A), and ends with the intake tail naming received_at, the
            bound request's receipt time (Stop 3 R-FORM-26 to -30f; PKT-CLK-3
            CLK3-2).

            With rolled_back True (the SB-G guard arm, A3 §7.4, class B), no
            decision was recorded and only the SB-G texts apply: the opening
            says nothing from this request was saved except the receipt
            record and that the LO's governance settings changed after
            receipt, and the tail says a resend is judged as of the original
            receipt (R-FORM-31a to -31f). A code without an SB-G text then
            raises RuntimeError.

            SB_BIRTHING_ELECTORATE_EMPTY opens "The LO was not born." before
            the shared reason.
            BIRTHING_ALREADY_RECORDED selects the latest Birthing event
            within the effective, received and recorded time cuts,
            ordered by effective time, recorded time, sequence and UUID.
            Report its effective date, or "an earlier date" if absent.

            Delegate SB-G codes with act "The LO was not born" and an
            opening ending in a full stop.
            INV_CQ_ROUTE_REQUIRED explains the unavailable CQ route
            for an electorate with more than one member.

            Otherwise derive a missing prerequisite through fresh reads,
            without the refusal's facts. Check the phase first, then
            whether an effective configuration exists, then whether
            settings exist with both choices made. A missing settings
            version also means "both application settings chosen".
            The phase read has effective, received and recorded cuts;
            version reads use _effective_version at the supplied times.

            If those checks pass, say a reviewed question is missing.
            This is the default phrase: no question is queried here,
            and the configuration's GOVERNANCE choice is not checked.
            The helper's read and message-building errors propagate.
            """
            if rolled_back:
                sbg_message = _sbg_readiness_message(
                    code=code,
                    act="The LO was not born",
                    operation="BIRTHING",
                    database_alias=self._alias,
                    organism=organism,
                    effective_at=effective_at,
                    recorded_at=received_at,
                    opening_end=_intake_tail(_ROLLED_BACK_OPENING_END, received_at),
                    tail=_TAIL_RESEND_ORIGINAL_TIME,
                )
                if sbg_message is None:
                    raise RuntimeError("the SB-G guard arm has no text for this code")
                return sbg_message
            tail = _intake_tail(_TAIL_REFUSAL_RECORDED, received_at)
            if code is S015AuthorityRefusalCode.SB_BIRTHING_ELECTORATE_EMPTY:
                return f"The LO was not born. {SB_BIRTHING_ELECTORATE_EMPTY_REASON} {tail}"
            if code is S015AuthorityRefusalCode.BIRTHING_ALREADY_RECORDED:
                event = LivingOrganismEvent.objects.using(self._alias).filter(
                    living_organism=organism,
                    action="BIRTHING",
                    effective_at__lte=effective_at,
                    received_at__lte=received_at,
                    recorded_at__lte=received_at,
                ).order_by("-effective_at", "-recorded_at", "-sequence", "-event_uuid").first()
                born_date = "an earlier date" if event is None else event.effective_at.date().isoformat()
                return (
                    f"The LO was not born again: it was born on {born_date}. "
                    f"An LO is born once. {tail}"
                )
            sbg_message = _sbg_readiness_message(
                code=code,
                act="The LO was not born",
                operation="BIRTHING",
                database_alias=self._alias,
                organism=organism,
                effective_at=effective_at,
                recorded_at=received_at,
                opening_end=".",
                tail=tail,
            )
            if sbg_message is not None:
                return sbg_message
            if code is S015AuthorityRefusalCode.INV_CQ_ROUTE_REQUIRED:
                return (
                    "This LO's electorate has more than one member, so this act needs the electorate's "
                    "qualified decision, and no CQ route is operative in this release (S3-IL-CQ). "
                    "This is a known limit, not a refusal of the act; it lifts when "
                    f"DISCUSSION's CQ is delivered. {tail}"
                )
            missing = "a reviewed application question"
            phase = LivingOrganismEvent.objects.using(self._alias).filter(
                living_organism=organism,
                action="PHASE_ADVANCE",
                effective_at__lte=effective_at,
                received_at__lte=received_at,
                recorded_at__lte=received_at,
            ).order_by(
                "-effective_at", "-occurred_at", "-received_at", "-recorded_at", "-sequence", "-event_uuid",
            ).values_list("evidence_reference", flat=True).first()
            if phase != "formation-phase:GERMINATING->BIRTHING":
                missing = "the phase to read Birthing"
            elif _effective_version(
                self._alias, organism, OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
                effective_at, received_at,
            ) is None:
                missing = "an effective governance configuration"
            else:
                settings_version = _effective_version(
                    self._alias, organism, OrganismAction.EFFECT_APPLICATION_SETTINGS,
                    effective_at, received_at,
                )
                if (
                    settings_version is None
                    or settings_version.applications_open is None
                    or settings_version.automatic_acceptance is None
                ):
                    missing = "both application settings chosen"
            return (
                "The LO was not born. Birthing needs "
                f"{missing}, which is not yet in place. Complete it, then birth the LO. {tail}"
            )
