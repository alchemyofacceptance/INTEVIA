"""Entitlement reads and the entitlement commands for a Living Organism.

The reads answer what an organism is entitled to at a time: whether a
capability is effective (entitlement_effective), the limit for a counted
dimension (entitlement_limit), whether current usage plus an increment
stays within it (check_allowance), the effective package with its
measurements (entitlement_summary), and capability availability
(capability_status). A read whose time is omitted uses the database's
current time (database_now), not the host's. The SQL functions
s015_entitlement_effective and s015_entitlement_limit decide
effectiveness and limits; the Python reads call them and check what they
return.

The commands (OrganismEntitlementService) record an organism's
entitlement election at Birthing and its opt-in to beta access; requests
to extend access, or to grant it by hand, are refused with authored
messages.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
import hashlib
from typing import Literal, Protocol
from uuid import UUID, uuid4

from django.contrib.auth.models import User
from django.db import DatabaseError, IntegrityError, connections
from django.db.models import Q

from core.models import (
    GovernedRule,
    Identity,
    LivingOrganism,
    LivingOrganismEvent,
    LOApplicationSettingsVersion,
    OrganismMembership,
    OrganismEntitlement,
    OrganismEntitlementEvent,
    OrganismEntitlementPolicyText,
    SE_ENTITLEMENT_CAPABILITY_CODES,
    SE_ENTITLEMENT_DIMENSIONS,
)
from core.organism_contract import OrganismAction
from src.intevia.services.command_intake import BoundRequest, IntakeEntry
from src.intevia.services.organism_application_authority import _effective_version
from src.intevia.services.organism_authority import (
    S015Authority,
    S015AuthorityDenied,
    S015AuthorityRefusalCode,
    S015PostFoundingAuthorityRequest,
    QualifiedS015AuthorityDecision,
    authority_target_fingerprint,
    canonical_json_bytes,
)
from src.intevia.services.organism_command_support import (
    ReplayViewer,
    S015CommandReceiptMalformed,
    _OrganismCommandService,
    _canonical_key,
    _canonical_uuid,
    _command_payload,
    _intake_tail,
    _lock_actor,
    _lock_actor_membership_context,
    _lock_registered_organism,
    _resulting_temporal_basis,
    _validate_timestamp,
    acquire_organism_command_mutex,
    authority_atomic,
)
from src.intevia.services.clock import database_now


_ELECTION_PREFIX = "application-settings-version:"
_ELECTION_SUBTYPE = OrganismAction.EFFECT_ENTITLEMENT_ELECTION.value
_ELECTION_COMMAND_DOMAIN = (
    b"INTEVIA:S015:EFFECT_ENTITLEMENT_ELECTION:COMMAND:v1\x00"
)
_ELECTION_EVENT_DOMAIN = (
    b"INTEVIA:S015:EFFECT_ENTITLEMENT_ELECTION:EVENT:v1\x00"
)
_BETA_CAPABILITY_CODES = frozenset(
    {
        "APPLICATION_CIRCLE",
        "FINANCE_CIRCLE",
        "OPERATIONS_CIRCLE",
        "UNLIMITED_CIRCLES",
        "PROBATION",
        "MENTORSHIP",
        "CUSTOM_ROLES",
        "GOVERNANCE_SUSPENSION",
        "PREMIUM_SEALING",
        "OPTIONAL_TRANSPARENCY",
        "CONFIGURABLE_RECOVERY_WINDOWS",
        "CIRCLE_SCOPED_EXPORT",
        "SEALED_EXPORT",
    }
)
_ENTITLEMENT_REFUSAL_MESSAGES = {
    S015AuthorityRefusalCode.SE_GRANT_NOT_BY_HAND: (
        "Beta access is granted automatically when an organism opts in. "
        "It cannot be granted manually. Nothing has changed."
    ),
    S015AuthorityRefusalCode.SE_EXTENSION_AUTHORITY: (
        "Extending beta access requires approval by a Coordinator of INTEVIA's "
        "Relationship Circle under its recorded policy. This request does not meet "
        "those requirements. Nothing has changed."
    ),
    S015AuthorityRefusalCode.SE_SELF_EXTENSION: (
        "An organism cannot extend its own beta access. Nothing has changed."
    ),
    S015AuthorityRefusalCode.SE_EXTENSION_ROUTE_UNAVAILABLE: (
        "Extensions to beta access are not yet available in this release. "
        "Nothing has changed."
    ),
    S015AuthorityRefusalCode.SE_ALREADY_BETA: (
        "Your organism already has beta access. Nothing has changed."
    ),
    S015AuthorityRefusalCode.SE_OPT_IN_NEEDS_CQ: (
        "Under your organism's rules, opting into beta access after Birthing requires "
        "collective approval through CQ. That process is not yet available for your "
        "organism. Nothing has changed."
    ),
    S015AuthorityRefusalCode.SE_OFFER_CLOSED: (
        "The beta offer is no longer open to your organism. Nothing has changed. "
        "The free offering continues, and subscriptions will be offered when they "
        "become available."
    ),
    S015AuthorityRefusalCode.SE_ALLOWANCE_EXCEEDED: (
        "Your organism's current allowance is {limit}. This action would bring the "
        "total to {proposed_total}. Nothing has changed."
    ),
}
_USAGE_UNCHECKABLE_MESSAGE = (
    "We could not check your organism's usage of {dimension} against its allowance "
    "of {limit}, so this action was not completed. Nothing has changed. Please try "
    "again later."
)
_DIMENSION_PRESENTATION = {
    "circles": (
        "Circles",
        "Circles",
        "Circles not CLOSED",
    ),
    "organism_learners": (
        "Learners",
        "Learners",
        "Learners with an active or probationary membership",
    ),
    "document_footprint": (
        "document footprint",
        "B",
        "Exact stored document bytes for the Living Organism",
    ),
}


class EntitlementAvailabilitySource(Protocol):
    """Interface for reporting an organism's paid-availability transition.

    activate_beta consults this source after its already-has-beta check.
    A non-empty exact-str reference causes SE_OFFER_CLOSED; a malformed
    non-None result causes S015EntitlementTechnicalFailure. None allows the
    remaining opt-in checks to continue. The Protocol declares the source
    contract; it does not implement the transition lookup.
    """
    def paid_availability_transition_reference(
        self,
        living_organism_uuid: UUID,
        effective_at: datetime,
        known_at: datetime,
    ) -> str | None:
        """Report a qualifying transition reference, or None.

        The source contract distinguishes the transition's effective-time
        cutoff, effective_at, from its knowledge-time cutoff, known_at. Return
        a non-empty str reference for a transition reported effective and known
        by those coordinates, or None when the source reports none. This is
        not an authority decision or a guarantee that an opt-in will succeed.
        The Protocol supplies no lookup implementation. activate_beta passes
        its received_at as both coordinates.
        """
        ...


class ProductionEntitlementAvailabilitySource:
    """The production paid-availability source, read from entitlements.

    It treats a PAID entitlement of the organism as its paid-availability
    transition.
    """
    def __init__(self, *, database_alias: str = "default") -> None:
        """Bind the source to a database alias.

        An empty or non-string alias raises ValueError.
        """
        if type(database_alias) is not str or not database_alias:
            raise ValueError("database_alias is required")
        self._alias = database_alias

    def paid_availability_transition_reference(
        self,
        living_organism_uuid: UUID,
        effective_at: datetime,
        known_at: datetime,
    ) -> str | None:
        """Return a reference if the organism has a PAID entitlement, else None.

        Considers the organism's PAID entitlements that took effect by
        effective_at and were recorded by known_at, whether or not they have
        since ended. The latest, by effective_at, recorded_at and id, gives the
        reference "paid-availability-transition:<entitlement UUID>".
        """
        entitlement = (
            OrganismEntitlement.objects.using(self._alias)
            .filter(
                living_organism__organism_id=living_organism_uuid,
                source="PAID",
                effective_at__lte=effective_at,
                recorded_at__lte=known_at,
            )
            .order_by("-effective_at", "-recorded_at", "-pk")
            .first()
        )
        if entitlement is None:
            return None
        return f"paid-availability-transition:{entitlement.entitlement_uuid}"


@dataclass(frozen=True, slots=True)
class EntitlementElectionCommand:
    """A request to opt an organism into beta access after Birthing.

    Carries the requesting credential, the organism, the request reference
    and idempotency key, when the request occurred, and the policy version
    and SHA-256 of the policy text accepted. It names no choice: the opt-in
    is always COMPLIMENTARY_BETA. The Birthing election arrives on the
    Birthing command instead.
    """
    credential: User
    living_organism_uuid: UUID
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    policy_version: int
    policy_text_sha256: str


@dataclass(frozen=True, slots=True)
class EntitlementExtensionCommand:
    """A request to extend an entitlement, with its references and reason.

    extend_entitlement refuses every such request in this release.
    """
    credential: User
    entitlement_uuid: UUID
    request_reference: str
    idempotency_key: str
    reason: str


@dataclass(frozen=True, slots=True)
class EntitlementElectionResult:
    """Receipt result shape used by the completed beta-opt-in path.

    Carries the organism UUID, offering choice, accepted policy version,
    optional entitlement UUID, election-event UUID and sequence, lineage
    reference, and authority and decision references. The current
    activate_beta producer supplies the composed grant's entitlement UUID.
    The type and serializer also permit None in that field, but the receipt
    decoder raises S015CommandReceiptMalformed for it; None alone does not
    establish that no grant was composed. This frozen data carrier does not
    validate the field values or prove their consistency with stored events
    or entitlements.
    """
    living_organism_uuid: UUID
    offering_choice: str
    policy_version: int
    entitlement_uuid: UUID | None
    event_uuid: UUID
    event_sequence: int
    lineage_reference: str
    authority_reference: str
    authority_decision_reference: str

    def receipt_payload(self) -> dict[str, str | int | None]:
        """Return the result as its receipt payload.

        UUIDs become strings (entitlement_uuid stays None when absent); the
        other fields are kept as they are.
        """
        return {
            "living_organism_uuid": str(self.living_organism_uuid),
            "offering_choice": self.offering_choice,
            "policy_version": self.policy_version,
            "entitlement_uuid": (
                None if self.entitlement_uuid is None else str(self.entitlement_uuid)
            ),
            "event_uuid": str(self.event_uuid),
            "event_sequence": self.event_sequence,
            "lineage_reference": self.lineage_reference,
            "authority_reference": self.authority_reference,
            "authority_decision_reference": self.authority_decision_reference,
        }

    @classmethod
    def replay_field_audiences(
        cls,
        result: EntitlementElectionResult,
    ) -> dict[str, str]:
        """Return an empty mapping of type-specific replay field audiences.

        Require type(result) to be exactly cls, otherwise raise TypeError.
        No field-level audience restriction is added by this result type.
        Viewer authorisation and any general field-withholding rules belong
        to the replay-serving layer and are not implemented by this method.
        """
        if type(result) is not cls:
            raise TypeError("entitlement election replay result has the wrong type")
        return {}


@dataclass(frozen=True, slots=True)
class AllowanceCheckResult:
    """Outcome of check_allowance: a read-only decision and its message.

    status is "within", "exceeds" or "cannot_check". dimension and increment
    echo the request. limit_kind and limit are the effective limit for the
    dimension; limit is None unless the kind is LIMIT. When the kind is
    LIMIT and usage could be measured, measured_usage is the authoritative
    usage and proposed_total is measured_usage plus increment; otherwise
    both are None. unit is the dimension's display unit. code and message
    are set only for "exceeds" (SE_ALLOWANCE_EXCEEDED) and "cannot_check"
    (SE_USAGE_UNCHECKABLE); technical_failure is True only for
    "cannot_check".
    """

    status: Literal["within", "exceeds", "cannot_check"]
    dimension: str
    increment: int
    proposed_total: int | None
    limit_kind: Literal["LIMIT", "UNLIMITED", "NOT_SET"]
    limit: int | None
    measured_usage: int | None
    unit: str
    code: S015AuthorityRefusalCode | None = None
    message: str | None = None
    technical_failure: bool = False


@dataclass(frozen=True, slots=True)
class _PreparedBirthingElection:
    """What prepare_birthing_election hands to complete_birthing_election.

    The actor, the organism, the election's settings version, the policy
    text, the authority decision, the offering choice, the request reference
    and idempotency key, the payload fingerprint, and the occurred,
    effective and received times.
    """
    actor: Identity
    organism: LivingOrganism
    version: LOApplicationSettingsVersion
    policy: OrganismEntitlementPolicyText
    decision: QualifiedS015AuthorityDecision
    offering_choice: str
    request_reference: str
    idempotency_key: str
    payload_fingerprint: str
    occurred_at: datetime
    effective_at: datetime
    received_at: datetime


def _election_result_from_payload(payload: object) -> EntitlementElectionResult:
    """Decode a stored beta opt-in receipt, refusing a malformed record.

    Require payload to have type exactly dict with exactly the nine result
    keys. Require every value except policy_version and event_sequence to
    have type exactly str; those two to have type exactly int and be at
    least 1; and offering_choice to be COMPLIMENTARY_BETA. Convert
    living_organism_uuid, entitlement_uuid and event_uuid with UUID and
    require each to be in canonical string form, so a None entitlement UUID
    is refused. The reference fields are checked for type only.

    Each failure raises S015CommandReceiptMalformed, chained to the
    TypeError or ValueError that detected it: a fault in the stored record,
    not evidence of a different request. This helper does not verify
    referenced database records or establish that a replay request matches
    its receipt.
    """
    if type(payload) is not dict:
        raise S015CommandReceiptMalformed() from TypeError("receipt payload must be a dict")
    try:
        # RC-1 commission v1.3, item 9(d): Stage 0 found no producer of a null entitlement UUID.
        expected_keys = {
            "living_organism_uuid",
            "offering_choice",
            "policy_version",
            "entitlement_uuid",
            "event_uuid",
            "event_sequence",
            "lineage_reference",
            "authority_reference",
            "authority_decision_reference",
        }
        if set(payload) != expected_keys:
            raise ValueError("receipt fields do not match the entitlement result contract")
        for key in (
            "living_organism_uuid",
            "offering_choice",
            "event_uuid",
            "lineage_reference",
            "authority_reference",
            "authority_decision_reference",
            "entitlement_uuid",
        ):
            if type(payload[key]) is not str:
                raise TypeError(f"{key} must be a string")
        if type(payload["policy_version"]) is not int:
            raise TypeError("policy_version must be an integer")
        if type(payload["event_sequence"]) is not int:
            raise TypeError("event_sequence must be an integer")
        if payload["offering_choice"] != "COMPLIMENTARY_BETA":
            raise ValueError("offering_choice is outside the entitlement result contract")
        if payload["policy_version"] < 1 or payload["event_sequence"] < 1:
            raise ValueError("policy_version and event_sequence must be positive")

        living_organism_uuid = UUID(payload["living_organism_uuid"])
        entitlement_uuid = UUID(payload["entitlement_uuid"])
        event_uuid = UUID(payload["event_uuid"])
        if (
            str(living_organism_uuid) != payload["living_organism_uuid"]
            or str(entitlement_uuid) != payload["entitlement_uuid"]
            or str(event_uuid) != payload["event_uuid"]
        ):
            raise ValueError("receipt UUIDs must use canonical strings")
        return EntitlementElectionResult(
            living_organism_uuid=living_organism_uuid,
            offering_choice=payload["offering_choice"],
            policy_version=payload["policy_version"],
            entitlement_uuid=entitlement_uuid,
            event_uuid=event_uuid,
            event_sequence=payload["event_sequence"],
            lineage_reference=payload["lineage_reference"],
            authority_reference=payload["authority_reference"],
            authority_decision_reference=payload["authority_decision_reference"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise S015CommandReceiptMalformed() from exc


class S015EntitlementUnavailable(Exception):
    """Raised when something an entitlement command needs is not in place.

    For example: the Living Organism is not found, the actor or its
    membership context cannot be established, no FREE election is recorded
    before a beta opt-in, or the organism's effective application settings
    are unavailable.
    """
    pass


class S015EntitlementTechnicalFailure(Exception):
    """Raised when a checked entitlement result cannot be established safely.

    Examples include a missing or invalid database-function result, usage
    that cannot be established from the returned authoritative counts, a
    missing effective free-tier rule in entitlement_summary, and an
    activate_beta call made inside an existing transaction. Database errors
    are not uniformly wrapped in this exception; callers handle them as
    specified by the individual read or command.
    """
    pass


class S015EntitlementRefused(Exception):
    """A governed refusal of an entitlement command.

    Carries refusal_code and, as its message, the authored text for that code
    from _ENTITLEMENT_REFUSAL_MESSAGES; it can be raised only for a code that
    has an authored message.
    """
    def __init__(
        self,
        refusal_code: S015AuthorityRefusalCode,
        *,
        received_at: datetime | None = None,
    ) -> None:
        """Use the authored message for refusal_code and keep the code.

        With received_at, the receipt time of an admitted request, a code
        restated for the intake (SE_ALREADY_BETA, SE_OFFER_CLOSED; Stop 3
        R-ENT-04, -05) takes its restated text naming that time; every
        other call keeps the authored text. A code with no authored
        message raises KeyError.
        """
        message = _ENTITLEMENT_REFUSAL_MESSAGES[refusal_code]
        template = (
            None if received_at is None else _ADMITTED_REFUSAL_TEMPLATES.get(refusal_code)
        )
        super().__init__(message if template is None else _intake_tail(template, received_at))
        self.refusal_code = refusal_code


@dataclass(frozen=True, slots=True)
class MeasurementResult:
    """The five tallies of one authoritative usage fold.

    counted, absent_state, not_yet_known, unreadable and anomalous count
    anchors (distinct identities, for organism_learners) as _measure_usage
    classifies them. _decide_measurement turns them into usage or a
    refusal.
    """

    counted: int
    absent_state: int
    not_yet_known: int
    unreadable: int
    anomalous: int


def _decide_measurement(
    result: MeasurementResult,
    mode: Literal["check", "summary"],
) -> int:
    """Return counted usage, or refuse a history whose meaning is not established.

    Raise ValueError for a mode other than "check" or "summary". In both
    modes raise S015EntitlementTechnicalFailure if any anchor is unreadable
    or anomalous; in "check" mode, also if any is not yet known. Otherwise
    return result.counted. In "summary" mode this is the count within the
    knowledge view used. This helper returns only that count and leaves the
    input MeasurementResult unchanged; the caller can report its
    not_yet_known tally separately.
    """
    if mode not in {"check", "summary"}:
        raise ValueError("measurement mode is not registered")
    if result.unreadable > 0 or result.anomalous > 0:
        raise S015EntitlementTechnicalFailure(
            "authoritative usage history could not be established"
        )
    # HQ-R1, DOC15-R3, and decision 12.3 keep state-time absence distinct from a
    # history outside the requested knowledge view.
    if mode == "check" and result.not_yet_known > 0:
        raise S015EntitlementTechnicalFailure(
            "authoritative usage is not yet known at the check's current knowledge time"
        )
    return result.counted


def _is_constraint_violation(
    error: IntegrityError,
    *,
    sqlstate: str,
    constraint_name: str,
) -> bool:
    """Report whether an IntegrityError was caused by one named constraint.

    Walk the error's __cause__ chain and return True when a cause carries
    the given SQLSTATE (as sqlstate or pgcode) and a diagnostic naming the
    given constraint; otherwise return False. Message text is not inspected.
    """
    cause = error.__cause__
    while cause is not None:
        state = getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)
        diagnostic = getattr(cause, "diag", None)
        if (
            state == sqlstate
            and getattr(diagnostic, "constraint_name", None) == constraint_name
        ):
            return True
        cause = cause.__cause__
    return False


_ENTITLEMENT_GUARD_FAILURE_TEXT = (
    "Beta access could not be activated because the system could not confirm "
    "the required record of the grant. This attempt made no changes. This is a "
    "system recording fault, not a refusal of your organisation’s eligibility "
    "for beta access. The fault needs to be checked before you retry."
)
# Restated texts of the admitted beta opt-in (Stop 3 R-ENT-04, -05, -06, -08;
# PKT-CLK-3 CLK3-2). {received_at} is the intake's receipt time, rendered by
# _intake_tail; the authored texts above stay for every other caller.
_TAIL_NOT_WRITTEN = (
    "INTEVIA kept a record that this request was received at {received_at}; sent again "
    "unchanged, it is answered as of that time."
)
_TAIL_REFUSAL_RECORDED = (
    "INTEVIA recorded this refusal and that the request was received at {received_at}; "
    "nothing else was saved."
)
_ADMITTED_REFUSAL_TEMPLATES = {
    S015AuthorityRefusalCode.SE_ALREADY_BETA: (
        "Your organism already has beta access; no governed record was written. "
        + _TAIL_NOT_WRITTEN
    ),
    S015AuthorityRefusalCode.SE_OFFER_CLOSED: (
        "The beta offer is no longer open to your organism; no governed record was "
        "written. The free offering continues, and subscriptions will be offered when "
        "they become available. " + _TAIL_NOT_WRITTEN
    ),
}
_OPT_IN_NEEDS_CQ_ADMITTED_TEMPLATE = (
    "Under your organism's rules, opting into beta access after Birthing requires "
    "collective approval through CQ. That process is not yet available for your "
    "organism. " + _TAIL_REFUSAL_RECORDED
)
_ENTITLEMENT_GUARD_FAILURE_ADMITTED_TEMPLATE = (
    "Beta access could not be activated because the system could not confirm "
    "the required record of the grant. This attempt wrote no governed record; "
    "INTEVIA kept a record that this request was received at {received_at}, and the "
    "same request can be sent again once the fault is checked; it is judged as of that "
    "time. This is a system recording fault, not a refusal of your organisation’s "
    "eligibility for beta access."
)


def _is_entitlement_granted_guard_failure(error: IntegrityError) -> bool:
    """Match only the guard SQLSTATE and primary message on the same cause."""
    cause = error.__cause__
    while cause is not None:
        state = getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)
        diagnostic = getattr(cause, "diag", None)
        if (
            state == "23000"
            and getattr(diagnostic, "message_primary", None)
            == "SE_ENTITLEMENT_WITHOUT_GRANT"
        ):
            return True
        cause = cause.__cause__
    return False


@contextmanager
def _entitlement_guard_atomic(
    alias: str,
    *,
    received_at: datetime | None = None,
) -> Iterator[None]:
    """Translate the identified deferred guard failure after rollback.

    With received_at (the bound request's receipt time), the text is the
    restated recording-fault text naming that time (Stop 3 R-ENT-08); the
    command's writes are rolled back and the intake record stands.
    """
    try:
        with authority_atomic(alias, received_at=received_at):
            yield
    except IntegrityError as exc:
        if (
            not connections[alias].in_atomic_block
            and _is_entitlement_granted_guard_failure(exc)
        ):
            raise S015EntitlementTechnicalFailure(
                _ENTITLEMENT_GUARD_FAILURE_TEXT
                if received_at is None
                else _intake_tail(_ENTITLEMENT_GUARD_FAILURE_ADMITTED_TEMPLATE, received_at)
            ) from exc
        raise


def entitlement_effective(
    living_organism_uuid: UUID,
    capability: str,
    *,
    at: datetime | None = None,
    database_alias: str = "default",
) -> bool:
    """Return whether any qualifying entitlement row supplies a capability.

    Calls s015_entitlement_effective for the organism and capability at
    `at`. A row qualifies when it contains the capability and effective_at
    is at or before `at`, and either its end is absent, `at` is before its
    end, or its source is PAID and `at` is strictly before its end plus
    seven days. The end of that grace interval is excluded. No recorded-time
    cutoff or latest-row selection is applied to entitlement rows.

    When `at` is omitted, use database_now on database_alias. An empty
    capability or one whose type is not exactly str raises ValueError. The
    capability is not checked against the registered-code list here. The
    SQL existence test returns False if no matching organism/entitlement
    row supplies it. A missing result row or non-boolean returned value
    raises S015EntitlementTechnicalFailure; database errors propagate.
    """
    organism_uuid = _canonical_uuid(living_organism_uuid, "living_organism_uuid")
    if type(capability) is not str or not capability:
        raise ValueError("capability is required")
    effective_at = _validate_timestamp(
        database_now(database_alias) if at is None else at,
        "at",
    )
    with connections[database_alias].cursor() as cursor:
        cursor.execute(
            "SELECT public.s015_entitlement_effective(%s, %s, %s)",
            [organism_uuid, capability, effective_at],
        )
        row = cursor.fetchone()
    if row is None or type(row[0]) is not bool:
        raise S015EntitlementTechnicalFailure(
            "entitlement effectiveness could not be established"
        )
    return row[0]


def entitlement_limit(
    living_organism_uuid: UUID,
    dimension: str,
    *,
    at: datetime | None = None,
    database_alias: str = "default",
) -> tuple[str, int | None]:
    """Return the effective dimension limit as (kind, value).

    Calls s015_entitlement_limit for the organism at `at`. SQL selects the
    latest entitlement row in effect, ordered by effective_at, recorded_at
    and id descending. A PAID row qualifies until, but not including, its
    end plus seven days. There is no recorded-time cutoff on entitlement
    rows; recorded_at is used to break selection ties.

    Only when no entitlement row qualifies, use the latest PLATFORM
    FREE_TIER rule effective by `at` and recorded no later than the start
    of the evaluating SQL statement, ordered by effective_from,
    recorded_at, version and id descending. A missing dimension in a
    selected entitlement does not cause fallback to the free-tier rule.

    The result is LIMIT with a non-negative integer, or UNLIMITED or
    NOT_SET with None. When `at` is omitted, use database_now on
    database_alias. SQL errors, including an unregistered dimension,
    unknown organism, unavailable limit object, invalid kind or failing
    integer conversion, propagate as database errors. The Python wrapper
    raises S015EntitlementTechnicalFailure for no result row, an unexpected
    kind, a LIMIT value that is not a non-negative int, or a non-None value
    for an unbounded kind.
    """
    organism_uuid = _canonical_uuid(living_organism_uuid, "living_organism_uuid")
    effective_at = _validate_timestamp(
        database_now(database_alias) if at is None else at,
        "at",
    )
    with connections[database_alias].cursor() as cursor:
        cursor.execute(
            "SELECT kind, value FROM public.s015_entitlement_limit(%s, %s, %s)",
            [organism_uuid, dimension, effective_at],
        )
        row = cursor.fetchone()
    if row is None or row[0] not in {"LIMIT", "UNLIMITED", "NOT_SET"}:
        raise S015EntitlementTechnicalFailure(
            "entitlement limit could not be established"
        )
    if row[0] == "LIMIT":
        if type(row[1]) is not int or row[1] < 0:
            raise S015EntitlementTechnicalFailure(
                "entitlement limit returned an invalid integer"
            )
        return row[0], row[1]
    if row[1] is not None:
        raise S015EntitlementTechnicalFailure(
            "unbounded entitlement limit returned a value"
        )
    return row[0], None


def _format_dimension_quantity(dimension: str, value: int) -> str:
    """Render a quantity of a dimension for authored text.

    document_footprint is shown in decimal units (GB, MB, KB) rounded half up
    to one decimal place, with trailing zeros removed, or as "<n> B" below
    1,000. Other dimensions show the count with the dimension's unit,
    singular for exactly 1 and plural otherwise (for example "1 Circle",
    "9 Circles").
    """
    if dimension == "document_footprint":
        units = (
            (1_000_000_000, "GB"),
            (1_000_000, "MB"),
            (1_000, "KB"),
        )
        for factor, unit in units:
            if value >= factor:
                amount = (Decimal(value) / Decimal(factor)).quantize(
                    Decimal("0.1"),
                    rounding=ROUND_HALF_UP,
                )
                rendered = format(amount.normalize(), "f")
                return f"{rendered} {unit}"
        return f"{value} B"
    unit = _DIMENSION_PRESENTATION[dimension][1][:-1]
    if value != 1:
        unit += "s"
    return f"{value} {unit}"


def _measure_usage(
    living_organism_uuid: UUID,
    dimension: str,
    state_at: datetime,
    database_alias: str,
    *,
    known_at: datetime | None,
) -> MeasurementResult:
    """Fold a dimension's anchor records and classify their histories.

    For circles each Circle of the organism is an anchor, counted when its
    folded state is not CLOSED. For organism_learners each membership is an
    anchor, counted when its folded state is ACTIVE or PROBATIONARY, and
    each tally counts distinct identities within that category. An anchor's
    state is folded from its events eligible at state time state_at and
    knowledge time known_at; a known_at of None uses the evaluating
    statement's timestamp. The anchor query itself has no temporal filter.

    An anchor is unreadable when it has no eligible events across all time.
    It is absent in state time when it has eligible events across all time
    but none eligible by state_at. It is not yet known when it has events
    eligible by state_at but none eligible in the requested knowledge view.
    It is anomalous when it has events eligible in that knowledge view but
    they fold to NULL. Return the counted, absent_state, not_yet_known,
    unreadable and anomalous tallies. A readable state outside the counted
    states contributes to none of these tallies.

    Raise S015EntitlementTechnicalFailure when the aggregate row is missing
    or malformed, or when any anchor is unreadable or anomalous; database
    errors from the query propagate. document_footprint always raises
    S015EntitlementTechnicalFailure with the explanation that authoritative
    byte records are required; this helper implements no byte measurement.
    An unregistered dimension raises ValueError.
    """
    if dimension == "circles":
        sql = """
            SELECT
                COUNT(*) FILTER (WHERE folded.n_all = 0),
                COUNT(*) FILTER (WHERE folded.n_all > 0 AND folded.n_state = 0),
                COUNT(*) FILTER (WHERE folded.n_state > 0 AND folded.n_known = 0),
                COUNT(*) FILTER (WHERE folded.n_known > 0 AND folded.state IS NULL),
                COUNT(*) FILTER (WHERE folded.state <> 'CLOSED')
            FROM (
                SELECT
                    events.ids,
                    cardinality(events.ids) AS n_known,
                    public.s015_reduce_projection('core_circle', events.ids) AS state,
                    cardinality(public.s015_eligible_event_ids(
                        'core_circle', circle.id, %(state_at)s, 'infinity'::timestamptz
                    )) AS n_state,
                    cardinality(public.s015_eligible_event_ids(
                        'core_circle', circle.id,
                        'infinity'::timestamptz, 'infinity'::timestamptz
                    )) AS n_all
                FROM public.core_circle AS circle
                JOIN public.core_livingorganism AS organism
                  ON organism.id = circle.parent_organism_id
                CROSS JOIN LATERAL (
                    SELECT public.s015_eligible_event_ids(
                        'core_circle', circle.id, %(state_at)s,
                        COALESCE(%(known_at)s, statement_timestamp())
                    ) AS ids
                ) AS events
                WHERE organism.organism_id = %(living_organism_uuid)s
            ) AS folded
        """
    elif dimension == "organism_learners":
        sql = """
            SELECT
                COUNT(DISTINCT folded.identity_id) FILTER (WHERE folded.n_all = 0),
                COUNT(DISTINCT folded.identity_id) FILTER (
                    WHERE folded.n_all > 0 AND folded.n_state = 0
                ),
                COUNT(DISTINCT folded.identity_id) FILTER (
                    WHERE folded.n_state > 0 AND folded.n_known = 0
                ),
                COUNT(DISTINCT folded.identity_id) FILTER (
                    WHERE folded.n_known > 0 AND folded.state IS NULL
                ),
                COUNT(DISTINCT folded.identity_id) FILTER (
                    WHERE folded.state IN ('ACTIVE', 'PROBATIONARY')
                )
            FROM (
                SELECT
                    membership.identity_id,
                    events.ids,
                    cardinality(events.ids) AS n_known,
                    public.s015_reduce_projection(
                        'core_organismmembership', events.ids
                    ) AS state,
                    cardinality(public.s015_eligible_event_ids(
                        'core_organismmembership', membership.id,
                        %(state_at)s, 'infinity'::timestamptz
                    )) AS n_state,
                    cardinality(public.s015_eligible_event_ids(
                        'core_organismmembership', membership.id,
                        'infinity'::timestamptz, 'infinity'::timestamptz
                    )) AS n_all
                FROM public.core_organismmembership AS membership
                JOIN public.core_livingorganism AS organism
                  ON organism.id = membership.living_organism_id
                CROSS JOIN LATERAL (
                    SELECT public.s015_eligible_event_ids(
                        'core_organismmembership', membership.id, %(state_at)s,
                        COALESCE(%(known_at)s, statement_timestamp())
                    ) AS ids
                ) AS events
                WHERE organism.organism_id = %(living_organism_uuid)s
            ) AS folded
        """
    elif dimension == "document_footprint":
        raise S015EntitlementTechnicalFailure(
            "document footprint cannot be measured without authoritative byte records"
        )
    else:
        raise ValueError("dimension is not registered")

    with connections[database_alias].cursor() as cursor:
        cursor.execute(
            sql,
            {
                "state_at": state_at,
                "known_at": known_at,
                "living_organism_uuid": living_organism_uuid,
            },
        )
        row = cursor.fetchone()
    if (
        row is None
        or len(row) != 5
        or any(type(value) is not int or value < 0 for value in row)
    ):
        raise S015EntitlementTechnicalFailure(
            f"authoritative {dimension} usage could not be established"
        )
    measurement = MeasurementResult(
        counted=row[4],
        absent_state=row[1],
        not_yet_known=row[2],
        unreadable=row[0],
        anomalous=row[3],
    )
    if measurement.unreadable > 0 or measurement.anomalous > 0:
        raise S015EntitlementTechnicalFailure(
            f"authoritative {dimension} usage history could not be established"
        )
    return measurement


def check_allowance(
    living_organism_uuid: UUID,
    dimension: str,
    increment: int,
    at: datetime,
    *,
    database_alias: str = "default",
) -> AllowanceCheckResult:
    """Decide whether authoritative usage at at plus an increment fits the limit.

    Read-only: it writes nothing and does not use dashboard state. The caller
    must supply the request's original bound received_at as at; this helper
    validates the timestamp but does not bind the request's receipt time.
    Raise ValueError for an unregistered dimension or an increment whose
    type is not exactly int or whose value is negative. The limit at at
    comes from entitlement_limit; an error reading it is raised, not
    converted. If its kind is UNLIMITED or NOT_SET, the result is "within"
    and usage is not measured.

    If it is LIMIT, authoritative usage at at is measured with the
    evaluating statement's current knowledge. If measurement raises
    DatabaseError or S015EntitlementTechnicalFailure, including an
    unreadable, anomalous or not-yet-known anchor, the result is
    "cannot_check" (SE_USAGE_UNCHECKABLE). Otherwise proposed_total is the
    measured usage plus increment: "exceeds" (SE_ALLOWANCE_EXCEEDED) when
    it is greater than the limit, "within" when it is not. Usage can change
    after the check; this read does not reserve the allowance.
    """
    organism_uuid = _canonical_uuid(living_organism_uuid, "living_organism_uuid")
    effective_at = _validate_timestamp(at, "at")
    if dimension not in SE_ENTITLEMENT_DIMENSIONS:
        raise ValueError("dimension is not registered")
    if type(increment) is not int or increment < 0:
        raise ValueError("increment must be a non-negative integer")

    kind, limit = entitlement_limit(
        organism_uuid,
        dimension,
        at=effective_at,
        database_alias=database_alias,
    )
    unit = _DIMENSION_PRESENTATION[dimension][1]
    if kind != "LIMIT":
        return AllowanceCheckResult(
            status="within",
            dimension=dimension,
            increment=increment,
            proposed_total=None,
            limit_kind=kind,
            limit=None,
            measured_usage=None,
            unit=unit,
        )

    try:
        measurement = _measure_usage(
            organism_uuid,
            dimension,
            effective_at,
            database_alias,
            known_at=None,
        )
        measured_usage = _decide_measurement(measurement, "check")
    except (DatabaseError, S015EntitlementTechnicalFailure):
        limit_text = _format_dimension_quantity(dimension, limit)
        dimension_text = _DIMENSION_PRESENTATION[dimension][0]
        return AllowanceCheckResult(
            status="cannot_check",
            dimension=dimension,
            increment=increment,
            proposed_total=None,
            limit_kind=kind,
            limit=limit,
            measured_usage=None,
            unit=unit,
            code=S015AuthorityRefusalCode.SE_USAGE_UNCHECKABLE,
            message=_USAGE_UNCHECKABLE_MESSAGE.format(
                dimension=dimension_text,
                limit=limit_text,
            ),
            technical_failure=True,
        )

    proposed_total = measured_usage + increment
    if proposed_total > limit:
        return AllowanceCheckResult(
            status="exceeds",
            dimension=dimension,
            increment=increment,
            proposed_total=proposed_total,
            limit_kind=kind,
            limit=limit,
            measured_usage=measured_usage,
            unit=unit,
            code=S015AuthorityRefusalCode.SE_ALLOWANCE_EXCEEDED,
            message=_ENTITLEMENT_REFUSAL_MESSAGES[
                S015AuthorityRefusalCode.SE_ALLOWANCE_EXCEEDED
            ].format(
                limit=_format_dimension_quantity(dimension, limit),
                proposed_total=_format_dimension_quantity(
                    dimension,
                    proposed_total,
                ),
            ),
        )

    return AllowanceCheckResult(
        status="within",
        dimension=dimension,
        increment=increment,
        proposed_total=proposed_total,
        limit_kind=kind,
        limit=limit,
        measured_usage=measured_usage,
        unit=unit,
    )


def _measurement_display(
    dimension: str,
    kind: str,
    limit: int | None,
    usage: int | None,
    measurement_reason: str | None,
) -> str:
    """Compose the display line for one dimension in entitlement_summary.

    When usage could not be measured, the line says so, with the allowance,
    "unlimited", or that no allowance is set. Otherwise: "<label>: <usage>
    of <limit> in use." for LIMIT; "<label>: unlimited — <usage> in use."
    for UNLIMITED; and for NOT_SET, the usage, a statement that no limit is
    currently enforced, and what is counted.
    """
    label = _DIMENSION_PRESENTATION[dimension][0]
    if measurement_reason is not None:
        if kind == "LIMIT":
            return (
                f"{label}: usage is currently unknown because it could not be "
                f"measured. Allowance: {_format_dimension_quantity(dimension, limit)}."
            )
        if kind == "UNLIMITED":
            return (
                f"{label}: unlimited. Usage is currently unknown because it could "
                "not be measured."
            )
        return (
            f"{label}: usage is currently unknown because it could not be measured. "
            "No allowance has been set; no limit is currently enforced for this measure."
        )

    usage_text = _format_dimension_quantity(dimension, usage)
    if kind == "NOT_SET":
        return (
            f"{label}: {usage_text} in use. No allowance has been set; no limit is "
            f"currently enforced for this measure. Counted as "
            f"{_DIMENSION_PRESENTATION[dimension][2]}."
        )
    if kind == "UNLIMITED":
        return f"{label}: unlimited — {usage_text} in use."
    return (
        f"{label}: {usage_text} of "
        f"{_format_dimension_quantity(dimension, limit)} in use."
    )


def entitlement_summary(
    living_organism_uuid: UUID,
    *,
    at: datetime | None = None,
    database_alias: str = "default",
) -> dict[str, object]:
    """Return package details, transition candidates and dimension usage.

    Read database_now once on database_alias, including when `at` is
    explicit; an omitted `at` defaults to that reading. Select the latest
    entitlement row in effect at `at`, ordered by effective_at, recorded_at
    and primary key descending, with no recorded-time cutoff. PAID rows
    remain in effect strictly before their end plus seven days. Use a
    non-empty tier_reference as the package name; otherwise map
    COMPLIMENTARY to BETA_COMPLIMENTARY_ACCESS and TRIAL to
    TRANSITION_TRIAL, or use the source itself.

    With no qualifying entitlement, report FREE_TIER from the latest
    PLATFORM FREE_TIER rule effective by `at` and recorded by the initial
    database-time reading, ordered by effective_from, recorded_at,
    version and primary key descending. Raise
    S015EntitlementTechnicalFailure if that rule is absent.

    For a selected entitlement, extensions lists its EXTENDED events
    recorded by `at`, in sequence order; this listing does not additionally
    filter their effective_at. next_transition is the earliest collected
    time after `at`: the selected entitlement's end (plus seven days for
    PAID), a future entitlement row's effective_at, a selected-entitlement
    event's effective_at when recorded by `at`, or, only when no entitlement
    is selected, a future FREE_TIER rule's effective_from when recorded by
    the initial database-time reading. Future entitlement rows have no
    recorded-time cutoff. If no candidate exists, next_transition is None.
    This is a candidate time, not a guarantee of a changed allowance.

    For every registered dimension, call entitlement_limit at `at` and
    attempt _measure_usage at `at`, even for UNLIMITED or NOT_SET. Each
    limit call uses its own SQL statement-start knowledge cutoff for
    free-tier rules; Circle and membership state folds use `at` as both
    state time and knowledge time. Usage counts what is known by `at`;
    anchors with events eligible at that state time but none eligible in
    that knowledge view are left out of usage and tallied as not_yet_known.
    A later event not yet known does not place an anchor in that category
    when an earlier eligible history is known; usage then follows the
    fold of the known history. A DatabaseError or
    S015EntitlementTechnicalFailure from measurement, including an
    unreadable or anomalous anchor, produces None usage, None
    not_yet_known and measurement_reason_code UNMEASURABLE. Errors reading
    the limit propagate. Include the unit, measurement basis and display
    line; the display line does not mention not_yet_known.

    These queries do not establish a shared snapshot or one knowledge
    cutoff for the whole result. The returned effective_at is the chosen
    package's start, not necessarily the requested `at`; effective_until
    is its stored end, before any PAID grace adjustment. Times are rendered
    as ISO strings in the result. This function writes nothing.
    """
    organism_uuid = _canonical_uuid(living_organism_uuid, "living_organism_uuid")
    now = database_now(database_alias)
    effective_at = _validate_timestamp(
        now if at is None else at,
        "at",
    )
    all_entitlements = OrganismEntitlement.objects.using(database_alias).filter(
        living_organism__organism_id=organism_uuid,
    )
    entitlements = all_entitlements.filter(
        effective_at__lte=effective_at,
    ).filter(
        Q(effective_until__isnull=True)
        | Q(effective_until__gt=effective_at)
        | Q(
            source="PAID",
            effective_until__gt=effective_at - timedelta(days=7),
        )
    ).order_by("-effective_at", "-recorded_at", "-pk")
    current = entitlements.first()
    future_rules = []

    if current is None:
        current_rule = (
            GovernedRule.objects.using(database_alias)
            .filter(
                scope_kind="PLATFORM",
                scope_organism__isnull=True,
                scope_circle__isnull=True,
                code="FREE_TIER",
                effective_from__lte=effective_at,
                recorded_at__lte=now,
            )
            .order_by(
                "-effective_from",
                "-recorded_at",
                "-version",
                "-pk",
            )
            .first()
        )
        if current_rule is None:
            raise S015EntitlementTechnicalFailure(
                "effective free-tier rule could not be established"
            )
        package = "FREE_TIER"
        basis = "FREE_TIER"
        source = "PLATFORM_RULE"
        effective_from = current_rule.effective_from
        effective_until = None
        extension_rows = []
        future_rules = GovernedRule.objects.using(database_alias).filter(
            scope_kind="PLATFORM",
            scope_organism__isnull=True,
            scope_circle__isnull=True,
            code="FREE_TIER",
            effective_from__gt=effective_at,
            recorded_at__lte=now,
        ).values_list("effective_from", flat=True)
    else:
        package = current.tier_reference or {
            "COMPLIMENTARY": "BETA_COMPLIMENTARY_ACCESS",
            "TRIAL": "TRANSITION_TRIAL",
        }.get(current.source, current.source)
        basis = current.basis
        source = current.source
        effective_from = current.effective_at
        effective_until = current.effective_until
        extension_rows = list(
            OrganismEntitlementEvent.objects.using(database_alias)
            .filter(
                entitlement=current,
                event_type="EXTENDED",
                recorded_at__lte=effective_at,
            )
            .order_by("sequence")
            .values("sequence", "effective_at", "received_at", "reason")
        )

    transitions = []
    if effective_until is not None:
        end_transition = (
            effective_until + timedelta(days=7)
            if source == "PAID"
            else effective_until
        )
        if end_transition > effective_at:
            transitions.append(end_transition)
    transitions.extend(
        all_entitlements.filter(effective_at__gt=effective_at).values_list(
            "effective_at",
            flat=True,
        )
    )
    transitions.extend(future_rules)
    if current is not None:
        transitions.extend(
            OrganismEntitlementEvent.objects.using(database_alias)
            .filter(
                entitlement=current,
                effective_at__gt=effective_at,
                recorded_at__lte=effective_at,
            )
            .values_list("effective_at", flat=True)
        )
    next_transition = min(transitions) if transitions else None

    dimensions = {}
    for dimension in SE_ENTITLEMENT_DIMENSIONS:
        kind, limit = entitlement_limit(
            organism_uuid,
            dimension,
            at=effective_at,
            database_alias=database_alias,
        )
        measurement_reason = None
        not_yet_known = None
        try:
            measurement = _measure_usage(
                organism_uuid,
                dimension,
                effective_at,
                database_alias,
                known_at=effective_at,
            )
            usage = _decide_measurement(measurement, "summary")
            not_yet_known = measurement.not_yet_known
        except (DatabaseError, S015EntitlementTechnicalFailure):
            usage = None
            measurement_reason = "UNMEASURABLE"
        dimensions[dimension] = {
            "kind": kind,
            "limit": limit,
            "measured_usage": usage,
            "not_yet_known": not_yet_known,
            "unit": _DIMENSION_PRESENTATION[dimension][1],
            "measurement_basis": _DIMENSION_PRESENTATION[dimension][2],
            "measurement_reason_code": measurement_reason,
            "display_line": _measurement_display(
                dimension,
                kind,
                limit,
                usage,
                measurement_reason,
            ),
        }

    return {
        "living_organism_uuid": str(organism_uuid),
        "package": package,
        "basis": basis,
        "source": source,
        "effective_at": effective_from.isoformat(),
        "effective_until": (
            None if effective_until is None else effective_until.isoformat()
        ),
        "extensions": [
            {
                **extension,
                "effective_at": extension["effective_at"].isoformat(),
                "received_at": extension["received_at"].isoformat(),
            }
            for extension in extension_rows
        ],
        "next_transition": (
            None if next_transition is None else next_transition.isoformat()
        ),
        "dimensions": dimensions,
    }


def capability_status(
    living_organism_uuid: UUID,
    *,
    at: datetime | None = None,
    database_alias: str = "default",
) -> dict[str, dict[str, bool | str | None]]:
    """Report whether the entitlement makes each capability available.

    "available" is entitlement_effective at `at` (the database's current
    time when omitted). "enabled" and "in_use" are None, with reason code
    NOT_YET_DELIVERED: availability is reported apart from whether a
    capability is switched on or used, which this read does not determine.
    """
    organism_uuid = _canonical_uuid(living_organism_uuid, "living_organism_uuid")
    effective_at = _validate_timestamp(
        database_now(database_alias) if at is None else at,
        "at",
    )
    return {
        capability: {
            "available": entitlement_effective(
                organism_uuid,
                capability,
                at=effective_at,
                database_alias=database_alias,
            ),
            "enabled": None,
            "in_use": None,
            "enabled_reason_code": "NOT_YET_DELIVERED",
            "in_use_reason_code": "NOT_YET_DELIVERED",
        }
        for capability in SE_ENTITLEMENT_CAPABILITY_CODES
    }


class OrganismEntitlementService(_OrganismCommandService):
    """Service for EFFECT_ENTITLEMENT_ELECTION and beta-access refusals.

    The Birthing integration uses prepare_birthing_election before its
    Birthing event and complete_birthing_election after it, within the
    caller's transaction. These helpers do not open their own transaction.
    activate_beta handles a later FREE-to-COMPLIMENTARY_BETA election
    inside authority_atomic and refuses an existing atomic block.
    The beta choice composes a grant under BETA_COMPLIMENTARY_ACCESS.

    refuse_person_grant always raises an authored refusal. A correctly
    typed extension command is refused because that route is unavailable;
    an incorrectly typed one raises TypeError. Missing-context errors
    depend on the path: for example, absent effective application settings
    raise S015EntitlementTechnicalFailure during Birthing preparation and
    S015EntitlementUnavailable during later opt-in.
    """
    _action = OrganismAction.EFFECT_ENTITLEMENT_ELECTION
    _not_found = S015EntitlementUnavailable
    _authority_unavailable = S015EntitlementUnavailable

    def __init__(
        self,
        *,
        authority: S015Authority,
        clock,
        database_alias: str = "default",
        availability_source: EntitlementAvailabilitySource | None = None,
    ) -> None:
        """Bind authority, clock and database alias through the command base.

        Use the supplied availability_source when it is truthy; otherwise
        construct ProductionEntitlementAvailabilitySource on database_alias.
        """
        super().__init__(
            authority=authority,
            clock=clock,
            database_alias=database_alias,
        )
        self._availability_source = availability_source or (
            ProductionEntitlementAvailabilitySource(database_alias=database_alias)
        )

    def _replay_result(self, payload: object) -> EntitlementElectionResult:
        """Rebuild the stored result of a completed opt-in for replay."""
        return _election_result_from_payload(payload)

    def _policy_record(
        self,
        *,
        policy_version: int,
        policy_text_sha256: str,
    ) -> OrganismEntitlementPolicyText:
        """Return the recorded policy text for a version, checked by digest.

        Raises ValueError unless policy_version is a positive integer and
        policy_text_sha256 is 64 lowercase hexadecimal characters. Raises
        S015EntitlementTechnicalFailure when no text is recorded for that
        version, or when the stored text's own SHA-256 or its recorded digest
        differs from the digest given.
        """
        if type(policy_version) is not int or policy_version <= 0:
            raise ValueError("policy_version must be a positive integer")
        if (
            type(policy_text_sha256) is not str
            or len(policy_text_sha256) != 64
            or any(character not in "0123456789abcdef" for character in policy_text_sha256)
        ):
            raise ValueError("policy_text_sha256 must be a lowercase SHA-256 digest")
        policy = (
            OrganismEntitlementPolicyText.objects.using(self._alias)
            .filter(policy_version=policy_version)
            .first()
        )
        if policy is None:
            raise S015EntitlementTechnicalFailure(
                "the accepted entitlement policy text is not recorded"
            )
        actual_digest = hashlib.sha256(policy.accepted_text.encode("utf-8")).hexdigest()
        if (
            policy.text_sha256 != actual_digest
            or policy.text_sha256 != policy_text_sha256
        ):
            raise S015EntitlementTechnicalFailure(
                "the accepted entitlement policy text digest does not match"
            )
        return policy

    def _effective_election(
        self,
        organism: LivingOrganism,
        effective_at: datetime,
        received_at: datetime,
    ) -> LOApplicationSettingsVersion | None:
        """Return the organism's effective election settings version, or None.

        Takes the organism's latest EFFECT_ENTITLEMENT_ELECTION event
        effective by effective_at and received and recorded by received_at
        (latest by effective_at, occurred_at, received_at, recorded_at,
        sequence, then event UUID), and returns the election settings version
        its evidence reference names. Raises S015EntitlementTechnicalFailure
        when that reference is malformed or names no election version of this
        organism.
        """
        reference = (
            LivingOrganismEvent.objects.using(self._alias)
            .filter(
                living_organism=organism,
                action=OrganismAction.EFFECT_ENTITLEMENT_ELECTION.value,
                effective_at__lte=effective_at,
                received_at__lte=received_at,
                recorded_at__lte=received_at,
            )
            .order_by(
                "-effective_at",
                "-occurred_at",
                "-received_at",
                "-recorded_at",
                "-sequence",
                "-event_uuid",
            )
            .values_list("evidence_reference", flat=True)
            .first()
        )
        if reference is None:
            return None
        if not reference.startswith(_ELECTION_PREFIX):
            raise S015EntitlementTechnicalFailure(
                "the effective entitlement election reference is malformed"
            )
        try:
            version_uuid = UUID(reference[len(_ELECTION_PREFIX):])
        except ValueError as exc:
            raise S015EntitlementTechnicalFailure(
                "the effective entitlement election reference is malformed"
            ) from exc
        version = (
            LOApplicationSettingsVersion.objects.using(self._alias)
            .filter(
                version_uuid=version_uuid,
                living_organism=organism,
                entitlement_choice__isnull=False,
                entitlement_election_route__isnull=False,
            )
            .first()
        )
        if version is None:
            raise S015EntitlementTechnicalFailure(
                "the effective entitlement election version is missing"
            )
        return version

    def _beta_grant_collision(
        self,
        organism: LivingOrganism,
        received_at: datetime,
    ) -> OrganismEntitlement | None:
        """Return the first row that makes this opt-in "already has beta", or None.

        Design section 6.3 item 2, with the Human Governor's ruling of 17 Oct 2026
        (HG_DECISION_K2_REISSUE_A1_A7_AND_EXTENSION_20261017): every BETA_OPT_IN row
        of the organism collides, with no effective- or recorded-time filter (the
        unique constraint's scope); an AUTHORISED_EXTENSION collides when its
        effective interval contains received_at, using the rows visible to this
        statement, with no recorded_at cutoff.
        """
        return (
            OrganismEntitlement.objects.using(self._alias)
            .filter(living_organism=organism)
            .filter(
                Q(basis="BETA_OPT_IN")
                | (
                    Q(basis="AUTHORISED_EXTENSION")
                    & Q(effective_at__lte=received_at)
                    & (
                        Q(effective_until__isnull=True)
                        | Q(effective_until__gt=received_at)
                    )
                )
            )
            .order_by("pk")
            .first()
        )

    def _new_settings_version(
        self,
        *,
        actor: Identity,
        organism: LivingOrganism,
        previous_election: LOApplicationSettingsVersion | None,
        policy: OrganismEntitlementPolicyText,
        authority_source: str,
        authority_version: str,
        offering_choice: str,
        election_route: str,
        occurred_at: datetime,
        effective_at: datetime,
        received_at: datetime,
    ) -> LOApplicationSettingsVersion:
        """Create a settings-chain version with election-specific payload.

        Record the offering choice, election route, accepted policy version
        and text digest, and authority source/version. Also record the shared
        organism, actor and enterer, occurred/effective/received timestamps,
        and supersedes link to previous_election when supplied. The acting
        Identity is stored in both actor and enterer on this version row.

        No application-settings-specific values are supplied by this helper;
        those belong to their own action kind under SE-GATE-01 and commission
        section 4.1. Callers supply BIRTHING or POST_BIRTHING as the election
        route. The helper passes its arguments to create; it does not itself
        validate every value or qualify authority.
        """
        return LOApplicationSettingsVersion.objects.using(self._alias).create(
            living_organism=organism,
            actor=actor,
            enterer=actor,
            supersedes=previous_election,
            occurred_at=occurred_at,
            effective_at=effective_at,
            received_at=received_at,
            entitlement_choice=offering_choice,
            entitlement_election_route=election_route,
            entitlement_policy_version=policy.policy_version,
            entitlement_policy_text_sha256=policy.text_sha256,
            entitlement_authority_source=authority_source,
            entitlement_authority_version=authority_version,
        )

    def _authority_provenance(self) -> tuple[str, str]:
        """Return the provider identity and version used in the election record.

        Use provider_identity when its type is exactly str; otherwise use the
        provider class's module-qualified name. Use UNSPECIFIED only when the
        provider_version attribute is absent. A present None, empty string or
        value whose type is not exactly str is an invalid version.

        Raise S015EntitlementTechnicalFailure for an invalid version, an empty
        identity, an identity longer than 255 characters, or a version longer
        than 32 characters. An empty string identity is rejected, not replaced
        by the class-name fallback.
        """
        provider = self._authority.provider
        authority_source = getattr(provider, "provider_identity", None)
        if type(authority_source) is not str:
            authority_source = f"{type(provider).__module__}.{type(provider).__qualname__}"
        authority_version = getattr(provider, "provider_version", "UNSPECIFIED")
        if type(authority_version) is not str or not authority_version:
            raise S015EntitlementTechnicalFailure(
                "the entitlement authority provider version is invalid"
            )
        if (
            not authority_source
            or len(authority_source) > 255
            or len(authority_version) > 32
        ):
            raise S015EntitlementTechnicalFailure(
                "the entitlement authority provenance exceeds its recorded shape"
            )
        return authority_source, authority_version

    def prepare_birthing_election(
        self,
        *,
        command,
        actor: Identity,
        organism: LivingOrganism,
        role_refs: tuple[str, ...],
        snapshot_uuid: UUID,
        request_reference: str,
        idempotency_key: str,
        actor_membership: OrganismMembership,
        occurred_at: datetime,
        effective_at: datetime,
        received_at: datetime,
    ) -> _PreparedBirthingElection:
        """Prepare and record an election version before the Birthing event.

        The caller owns the transaction spanning this preparation, Birthing
        and complete_birthing_election; this method opens no transaction.
        Require FREE or COMPLIMENTARY_BETA, otherwise raise ValueError. Check
        the accepted policy through _policy_record, with that helper's input
        and technical errors. Missing effective application settings or an
        already-effective election raises S015EntitlementTechnicalFailure.

        Create the BIRTHING election version, compute the command fingerprint,
        and qualify an EFFECT_ENTITLEMENT_ELECTION request naming the snapshot
        and version, evaluated at received_at with the supplied effective_at.
        On S015AuthorityDenied, call delete on the new version and re-raise
        the denial if cleanup succeeds. Transactional rollback remains the
        caller's responsibility; cleanup errors are not separately translated
        here. Return the prepared actor, organism, version, policy, decision,
        choice, request references, fingerprint and timestamps. This method
        appends no event and composes no grant, but it does write the version.
        """
        if command.offering_choice not in {"FREE", "COMPLIMENTARY_BETA"}:
            raise ValueError("offering_choice must be FREE or COMPLIMENTARY_BETA")
        policy = self._policy_record(
            policy_version=command.policy_version,
            policy_text_sha256=command.policy_text_sha256,
        )
        current_settings = _effective_version(
            self._alias,
            organism,
            OrganismAction.EFFECT_APPLICATION_SETTINGS,
            effective_at,
            received_at,
        )
        if current_settings is None:
            raise S015EntitlementTechnicalFailure(
                "the effective application settings required for the election are missing"
            )
        previous_election = self._effective_election(
            organism,
            effective_at,
            received_at,
        )
        if previous_election is not None:
            raise S015EntitlementTechnicalFailure(
                "an entitlement election already exists before Birthing"
            )
        authority_source,         authority_version = self._authority_provenance()
        version = self._new_settings_version(
            actor=actor,
            organism=organism,
            previous_election=None,
            policy=policy,
            authority_source=authority_source,
            authority_version=authority_version,
            offering_choice=command.offering_choice,
            election_route="BIRTHING",
            occurred_at=occurred_at,
            effective_at=effective_at,
            received_at=received_at,
        )
        payload_fingerprint = _command_payload(
            domain=_ELECTION_COMMAND_DOMAIN,
            schema="s015-entitlement-election-birthing-v1",
            action=OrganismAction.EFFECT_ENTITLEMENT_ELECTION,
            actor_identity_id=actor.identity_id,
            actor_access_epoch=actor.access_epoch,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
            target={
                "action": _ELECTION_SUBTYPE,
                "organism_uuid": organism.organism_id,
            },
            command={
                "effective_at": effective_at,
                "offering_choice": command.offering_choice,
                "policy_text_sha256": policy.text_sha256,
                "policy_version": policy.policy_version,
                "received_at": received_at,
            },
        )
        target = {
            "action": _ELECTION_SUBTYPE,
            "organism_uuid": organism.organism_id,
            "snapshot_uuid": str(snapshot_uuid),
        }
        request = S015PostFoundingAuthorityRequest(
            database_alias=self._alias,
            actor_pk=actor.pk,
            actor_identity_id=actor.identity_id,
            actor_access_epoch=actor.access_epoch,
            action=OrganismAction.EFFECT_ENTITLEMENT_ELECTION,
            command_subtype=_ELECTION_SUBTYPE,
            target_fingerprint=authority_target_fingerprint(target),
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            evaluated_at=received_at,
            active_membership_head_reference=(
                actor_membership.head_transition.lineage_reference
            ),
            contextual_role_head_references=role_refs,
            effective_at=effective_at,
            target=target,
            living_organism_id=organism.organism_id,
            version_uuid=version.version_uuid,
            version_kind="EFFECT",
        )
        try:
            decision = self._authority.qualify(request)
        except S015AuthorityDenied:
            version.delete(using=self._alias)
            raise
        return _PreparedBirthingElection(
            actor=actor,
            organism=organism,
            version=version,
            policy=policy,
            decision=decision,
            offering_choice=command.offering_choice,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            payload_fingerprint=payload_fingerprint,
            occurred_at=occurred_at,
            effective_at=effective_at,
            received_at=received_at,
        )

    def complete_birthing_election(
        self,
        prepared: _PreparedBirthingElection,
        *,
        birthing_event_uuid: UUID,
    ) -> tuple[LivingOrganismEvent, OrganismEntitlement | None]:
        """Complete a prepared Birthing election once its event exists.

        Appends the election event to the organism's chain and, for
        COMPLIMENTARY_BETA, composes the beta grant, recorded as composed from
        the Birthing event. Returns the event with the grant, or with None for
        FREE.
        """
        event = self._append_election_event(
            actor=prepared.actor,
            organism=prepared.organism,
            version=prepared.version,
            decision=prepared.decision,
            request_reference=prepared.request_reference,
            idempotency_key=prepared.idempotency_key,
            payload_fingerprint=prepared.payload_fingerprint,
            occurred_at=prepared.occurred_at,
            effective_at=prepared.effective_at,
            received_at=prepared.received_at,
        )
        if prepared.offering_choice == "FREE":
            return event, None
        entitlement = self._compose_beta_grant(
            actor=prepared.actor,
            organism=prepared.organism,
            policy=prepared.policy,
            effective_at=prepared.effective_at,
            received_at=prepared.received_at,
            composed_from_event_uuid=birthing_event_uuid,
        )
        return event, entitlement

    def activate_beta(self, command: EntitlementElectionCommand) -> EntitlementElectionResult:
        """Admit a beta election and run it at its bound time (D §5.1).

        Refuse, before any statement, a call made inside an atomic block,
        in manual transaction mode or on a connection with a pending
        rollback; otherwise admit the request through the intake (one
        committed row: key, content fingerprint, server-assigned
        received_at), then enter authority_atomic with the bound request
        in _execute_activate_beta (Stop 3 R-ENT-10).
        """
        if connections[self._alias].in_atomic_block:
            raise S015EntitlementTechnicalFailure(
                "entitlement opt-in cannot run inside an existing transaction"
            )
        from src.intevia.services.command_intake import (
            ACTIVE_ENTRIES,
            IntakeAdmissionTechnicalFailure,
            _require_admission_state,
            _resolve_actor,
            admit_request,
            intake_shape,
        )

        entry = IntakeEntry.ACTIVATE_BETA
        _require_admission_state(self._alias, entry)
        if entry not in ACTIVE_ENTRIES:
            raise IntakeAdmissionTechnicalFailure("ACTIVATE_BETA is not active")
        shape = intake_shape(entry, command)
        actor_ref = _resolve_actor(self._alias, command.credential)
        bound = admit_request(self._alias, entry, actor_ref, shape)
        return self._execute_activate_beta(command, bound)

    def _execute_activate_beta(
        self,
        command: EntitlementElectionCommand,
        bound: BoundRequest,
    ) -> EntitlementElectionResult:
        """Execute a bound beta opt-in (D §5.3).

        activate_beta has admitted the request; enter authority_atomic with
        the bound request. Lock the actor, verify the bound request, validate
        the request fields, take received_at from the binding (on a retry, the
        original receipt time), and validate the accepted policy. Set
        effective_at to that received_at, not to the command's occurred_at.

        Build the fingerprint and acquire the actor/action/idempotency mutex.
        Ask the base replay path for a matching completed result, with a
        ReplayViewer using another service-clock reading. Return a non-None
        replay result before the new-election path. Validation and policy
        lookup precede this replay check; replay eligibility, access and
        mismatch handling belong to the base service.

        For a new election, lock the organism and establish membership context.
        Check first whether _beta_grant_collision finds an existing grant. Every
        BETA_OPT_IN row for the organism collides, without effective- or
        recorded-time filters. An AUTHORISED_EXTENSION collides when its start
        is at or before the original received_at and its end is absent or
        strictly after that time, using rows visible to the collision-check
        statement with no recorded_at cutoff. A collision raises
        S015EntitlementRefused(SE_ALREADY_BETA). Then query paid availability: a
        non-empty exact-str reference causes SE_OFFER_CLOSED; any other non-None
        result causes S015EntitlementTechnicalFailure. Require an effective FREE
        election and effective application settings, otherwise raise
        S015EntitlementUnavailable.

        Create a POST_BIRTHING election version and qualify authority at
        received_at, with effective_at also equal to received_at. For this
        action only, the authority provider recognises the request's own new
        version by its bound identity (this request's version_uuid, this
        organism), not by its record time; every other qualification check
        still applies (PKT-SE-K4). On S015AuthorityDenied, call delete on the
        version. If cleanup succeeds, translate C_ROUTE_IS_CQ or
        SE_OPT_IN_NEEDS_CQ to a S015AuthorityDenied carrying the authored
        SE_OPT_IN_NEEDS_CQ text and code, preserving the original
        disclosed_refusal_code and authority_decision_reference; re-raise other
        authority denials unchanged.

        On approval, append the election event, compose the beta grant from
        that event's UUID, build the result and call _finalise for the receipt.
        If the grant insert raises IntegrityError with SQLSTATE 23505 and
        constraint se_entitlement_beta_one_per_lo_uniq, _compose_beta_grant
        raises S015EntitlementRefused(SE_ALREADY_BETA); other integrity errors
        propagate unchanged.
        This new-election path is within the same authority_atomic block.
        If the database's commit-time guard refuses the transaction because
        the new entitlement row has no first GRANTED event
        (SE_ENTITLEMENT_WITHOUT_GRANT, SQLSTATE 23000), the command's
        transaction is rolled back — the intake record of the request stands —
        and S015EntitlementTechnicalFailure is raised with the authored
        recording-fault text (PKT-SE-K3). Other commit-time
        IntegrityErrors propagate unchanged. Existing PKT-LOCK-1 contention
        translation remains in force for its named OperationalError SQLSTATEs.
        """
        with _entitlement_guard_atomic(self._alias, received_at=bound.received_at):
            actor = _lock_actor(
                database_alias=self._alias,
                credential=command.credential,
                unavailable=S015EntitlementUnavailable,
            )
            bound.verify(command, actor, IntakeEntry.ACTIVATE_BETA, self._alias)
            organism_uuid = _canonical_uuid(
                command.living_organism_uuid,
                "living_organism_uuid",
            )
            request_reference = _canonical_key(
                command.request_reference,
                "request_reference",
                128,
            )
            idempotency_key = _canonical_key(
                command.idempotency_key,
                "idempotency_key",
                120,
            )
            occurred_at = _validate_timestamp(command.occurred_at, "occurred_at")
            received_at = _validate_timestamp(bound.received_at, "received_at")
            effective_at = received_at
            policy = self._policy_record(
                policy_version=command.policy_version,
                policy_text_sha256=command.policy_text_sha256,
            )
            fingerprint = _command_payload(
                domain=_ELECTION_COMMAND_DOMAIN,
                schema="s015-entitlement-election-v1",
                action=self._action,
                actor_identity_id=actor.identity_id,
                actor_access_epoch=actor.access_epoch,
                request_reference=request_reference,
                idempotency_key=idempotency_key,
                occurred_at=occurred_at,
                target={
                    "action": self._action.value,
                    "organism_uuid": organism_uuid,
                },
                command={
                    "policy_text_sha256": policy.text_sha256,
                    "policy_version": policy.policy_version,
                    "offering_choice": "COMPLIMENTARY_BETA",
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
                organism = (
                    LivingOrganism.objects.using(self._alias)
                    .select_for_update()
                    .get(organism_id=organism_uuid)
                )
            except LivingOrganism.DoesNotExist as exc:
                raise S015EntitlementUnavailable("living organism not found") from exc
            membership, role_refs = _lock_actor_membership_context(
                database_alias=self._alias,
                actor_identity_id=actor.identity_id,
                living_organism_id=organism_uuid,
                unavailable=S015EntitlementUnavailable,
            )
            if self._beta_grant_collision(organism, received_at) is not None:
                raise S015EntitlementRefused(
                    S015AuthorityRefusalCode.SE_ALREADY_BETA,
                    received_at=bound.received_at,
                )
            paid_transition = self._availability_source.paid_availability_transition_reference(
                organism_uuid,
                effective_at,
                received_at,
            )
            if paid_transition is not None:
                if type(paid_transition) is not str or not paid_transition:
                    raise S015EntitlementTechnicalFailure(
                        "the paid-availability source returned an invalid reference"
                    )
                raise S015EntitlementRefused(
                    S015AuthorityRefusalCode.SE_OFFER_CLOSED,
                    received_at=bound.received_at,
                )
            previous_election = self._effective_election(
                organism,
                effective_at,
                received_at,
            )
            if previous_election is None or previous_election.entitlement_choice != "FREE":
                raise S015EntitlementUnavailable(
                    "a recorded FREE election is required before beta opt-in"
                )
            current_settings = _effective_version(
                self._alias,
                organism,
                OrganismAction.EFFECT_APPLICATION_SETTINGS,
                effective_at,
                received_at,
            )
            if current_settings is None:
                raise S015EntitlementUnavailable(
                    "effective application settings are unavailable"
                )
            authority_source,             authority_version = self._authority_provenance()
            version = self._new_settings_version(
                actor=actor,
                organism=organism,
                previous_election=previous_election,
                policy=policy,
                authority_source=authority_source,
                authority_version=authority_version,
                offering_choice="COMPLIMENTARY_BETA",
                election_route="POST_BIRTHING",
                occurred_at=occurred_at,
                effective_at=effective_at,
                received_at=received_at,
            )
            evaluated_at = received_at
            target = {
                "action": self._action.value,
                "organism_uuid": organism_uuid,
                "version_uuid": version.version_uuid,
            }
            request = S015PostFoundingAuthorityRequest(
                database_alias=self._alias,
                actor_pk=actor.pk,
                actor_identity_id=actor.identity_id,
                actor_access_epoch=actor.access_epoch,
                action=self._action,
                command_subtype=_ELECTION_SUBTYPE,
                target_fingerprint=authority_target_fingerprint(target),
                request_reference=request_reference,
                idempotency_key=idempotency_key,
                evaluated_at=evaluated_at,
                active_membership_head_reference=membership.head_transition.lineage_reference,
                contextual_role_head_references=role_refs,
                effective_at=effective_at,
                target=target,
                living_organism_id=organism_uuid,
                version_uuid=version.version_uuid,
                version_kind="EFFECT",
            )
            try:
                decision = self._authority.qualify(request)
            except S015AuthorityDenied as exc:
                version.delete(using=self._alias)
                if exc.refusal_code in {
                    S015AuthorityRefusalCode.C_ROUTE_IS_CQ,
                    S015AuthorityRefusalCode.SE_OPT_IN_NEEDS_CQ,
                }:
                    raise S015AuthorityDenied(
                        _intake_tail(_OPT_IN_NEEDS_CQ_ADMITTED_TEMPLATE, bound.received_at),
                        refusal_code=S015AuthorityRefusalCode.SE_OPT_IN_NEEDS_CQ,
                        disclosed_refusal_code=exc.disclosed_refusal_code,
                        authority_decision_reference=exc.authority_decision_reference,
                    ) from exc
                raise
            event = self._append_election_event(
                actor=actor,
                organism=organism,
                version=version,
                decision=decision,
                request_reference=request_reference,
                idempotency_key=idempotency_key,
                payload_fingerprint=fingerprint,
                occurred_at=occurred_at,
                effective_at=effective_at,
                received_at=received_at,
            )
            entitlement = self._compose_beta_grant(
                actor=actor,
                organism=organism,
                policy=policy,
                effective_at=effective_at,
                received_at=received_at,
                composed_from_event_uuid=event.event_uuid,
            )
            result = EntitlementElectionResult(
                living_organism_uuid=organism_uuid,
                offering_choice="COMPLIMENTARY_BETA",
                policy_version=policy.policy_version,
                entitlement_uuid=entitlement.entitlement_uuid,
                event_uuid=event.event_uuid,
                event_sequence=event.sequence,
                lineage_reference=event.lineage_reference,
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

    def refuse_person_grant(self) -> None:
        """Refuse a grant entered by a person.

        Beta access is granted only by composition when an organism opts in.
        Always raises S015EntitlementRefused with SE_GRANT_NOT_BY_HAND.
        """
        raise S015EntitlementRefused(S015AuthorityRefusalCode.SE_GRANT_NOT_BY_HAND)

    def extend_entitlement(
        self,
        command: EntitlementExtensionCommand,
    ) -> None:
        """Refuse an extension because its command route is unavailable.

        Raise TypeError unless type(command) is exactly
        EntitlementExtensionCommand; subclasses are also rejected. Otherwise
        raise S015EntitlementRefused(SE_EXTENSION_ROUTE_UNAVAILABLE).
        This method records nothing.
        """
        if type(command) is not EntitlementExtensionCommand:
            raise TypeError("command must be an EntitlementExtensionCommand")
        raise S015EntitlementRefused(
            S015AuthorityRefusalCode.SE_EXTENSION_ROUTE_UNAVAILABLE
        )

    def _append_election_event(
        self,
        *,
        actor: Identity,
        organism: LivingOrganism,
        version: LOApplicationSettingsVersion,
        decision,
        request_reference: str,
        idempotency_key: str,
        payload_fingerprint: str,
        occurred_at: datetime,
        effective_at: datetime,
        received_at: datetime,
    ) -> LivingOrganismEvent:
        """Append an entitlement-election event in the caller's transaction.

        Lock the organism and its head event. A null head_event_id raises
        S015EntitlementTechnicalFailure, as does a predecessor-commitment
        query returning no row or a NULL commitment. Other ORM and database
        errors are not uniformly converted by this helper.

        Insert EFFECT_ENTITLEMENT_ELECTION as the head's successor, with both
        prior_state and resulting_state equal to the predecessor's resulting
        state. Attribute the act to the Living Organism in STANDING capacity:
        actor_id is None, actor_state is LIVING_ORGANISM, and the supplied
        Identity is the enterer with entry_mode TRANSPORT. Store the organism's
        founding_authority_basis_id separately from the qualified decision's
        authority_decision_reference. This helper records that decision; it
        does not itself qualify authority. The founding basis anchors the
        event to the organism's root authority basis; it does not record that
        the founder's authority persists. The operative authority is the
        qualified decision.

        Include the election-version evidence reference, request references,
        fingerprint, generated lineage reference, temporal basis, supplied
        timestamps, predecessor commitment and computed parts commitment.
        Refresh the organism's governed cache using effective_at and a fresh
        database clock_timestamp() argument, then return the inserted event.
        This helper does not open or commit its own transaction.
        """
        organism = (
            LivingOrganism.objects.using(self._alias)
            .select_for_update()
            .get(pk=organism.pk)
        )
        if organism.head_event_id is None:
            raise S015EntitlementTechnicalFailure(
                "entitlement election requires a current LO chain head"
            )
        predecessor = (
            LivingOrganismEvent.objects.using(self._alias)
            .select_for_update()
            .get(pk=organism.head_event_id)
        )
        event_uuid = uuid4()
        temporal_kind, temporal_reference = _resulting_temporal_basis(
            payload_fingerprint=payload_fingerprint,
            effective_at=effective_at,
            received_at=received_at,
        )
        lineage_reference = "s015l1:" + hashlib.sha256(
            _ELECTION_EVENT_DOMAIN
            + canonical_json_bytes(
                {
                    "event": event_uuid,
                    "organism": organism.organism_id,
                    "fingerprint": payload_fingerprint,
                }
            )
        ).hexdigest()
        with connections[self._alias].cursor() as cursor:
            cursor.execute(
                "SELECT l1_commitment FROM public.core_livingorganismevent WHERE id = %s",
                [predecessor.pk],
            )
            predecessor_commitment = cursor.fetchone()
            if predecessor_commitment is None or predecessor_commitment[0] is None:
                raise S015EntitlementTechnicalFailure(
                    "entitlement election could not read the predecessor commitment"
                )
            cursor.execute(
                "SELECT public.s015_0022_digest('s015p1', "
                "public.s015_0022_parts_preimage(%s, %s::uuid))",
                ["core_livingorganismevent", event_uuid],
            )
            parts_commitment = cursor.fetchone()[0]
            event_columns = {
                "event_uuid": event_uuid,
                "living_organism_id": organism.pk,
                "sequence": predecessor.sequence + 1,
                "predecessor_id": predecessor.pk,
                "predecessor_sequence": predecessor.sequence,
                "action": _ELECTION_SUBTYPE,
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
                "evidence_reference": _ELECTION_PREFIX + str(version.version_uuid),
                "request_reference": request_reference,
                "idempotency_key": idempotency_key,
                "payload_fingerprint": payload_fingerprint,
                "lineage_reference": lineage_reference,
                "temporal_basis_kind": temporal_kind,
                "temporal_basis_reference": temporal_reference,
                "occurred_at": occurred_at,
                "effective_at": effective_at,
                "received_at": received_at,
                "predecessor_l1_commitment": predecessor_commitment[0],
                "parts_commitment": parts_commitment,
            }
            cursor.execute(
                f"INSERT INTO public.core_livingorganismevent "
                f"({', '.join(event_columns)}) "
                f"VALUES ({', '.join(['%s'] * len(event_columns))}) RETURNING id",
                list(event_columns.values()),
            )
            event_id = cursor.fetchone()[0]
            cursor.execute(
                "SELECT public.s015_refresh_governed_cache(%s, %s, %s, clock_timestamp())",
                ["core_livingorganism", organism.pk, effective_at],
            )
        return LivingOrganismEvent.objects.using(self._alias).get(pk=event_id)

    def _compose_beta_grant(
        self,
        *,
        actor: Identity,
        organism: LivingOrganism,
        policy: OrganismEntitlementPolicyText,
        effective_at: datetime,
        received_at: datetime,
        composed_from_event_uuid: UUID,
    ) -> OrganismEntitlement:
        """Record a beta grant and its composed GRANTED event.

        Select the PLATFORM BETA_COMPLIMENTARY_ACCESS rule effective by
        effective_at and recorded by received_at, ordered by effective_from,
        recorded_at, version and primary key descending. Raise
        S015EntitlementTechnicalFailure if no rule exists or its body is not
        exactly a dict. Require capabilities to be exactly a list of exact-str
        values whose set equals _BETA_CAPABILITY_CODES, and limits to be
        exactly a dict; otherwise raise S015EntitlementTechnicalFailure.
        This Python check does not reject duplicate capability entries or
        validate each dimension's limit structure.

        Create a COMPLIMENTARY entitlement with basis BETA_OPT_IN, the rule's
        capabilities and limits, the accepted policy version/reference,
        effective_at as supplied, and effective_until=None. Record
        ending_condition_reference as the prefix paid-availability-transition:
        followed by the organism UUID. This method does not execute that
        ending condition or create the transition trial. If the insert
        violates the one-beta-grant-per-organism constraint
        (se_entitlement_beta_one_per_lo_uniq, SQLSTATE 23505), raise
        S015EntitlementRefused(SE_ALREADY_BETA); other integrity errors
        propagate unchanged.

        Create its first GRANTED event with entry_mode COMPOSED, the selected
        rule and rule-version reference, and the supplied originating event
        UUID. actor_state is NONE and actor is None; the supplied Identity is
        the enterer. No deciding Identity, authoriser or established composer
        Identity is recorded. occurred_at and effective_at both use the
        supplied effective_at; received_at uses the supplied received_at.
        Return the entitlement. The caller owns the surrounding transaction;
        this helper does not open or commit one itself.
        """
        rule = (
            GovernedRule.objects.using(self._alias)
            .filter(
                scope_kind="PLATFORM",
                scope_organism__isnull=True,
                scope_circle__isnull=True,
                code="BETA_COMPLIMENTARY_ACCESS",
                effective_from__lte=effective_at,
                recorded_at__lte=received_at,
            )
            .order_by("-effective_from", "-recorded_at", "-version", "-pk")
            .first()
        )
        if rule is None or type(rule.body) is not dict:
            raise S015EntitlementTechnicalFailure(
                "the effective BETA_COMPLIMENTARY_ACCESS rule is unavailable"
            )
        capabilities = rule.body.get("capabilities")
        limits = rule.body.get("limits")
        if (
            type(capabilities) is not list
            or any(type(capability) is not str for capability in capabilities)
            or set(capabilities) != _BETA_CAPABILITY_CODES
            or type(limits) is not dict
        ):
            raise S015EntitlementTechnicalFailure(
                "the effective BETA_COMPLIMENTARY_ACCESS rule is malformed"
            )
        try:
            entitlement = OrganismEntitlement.objects.using(self._alias).create(
                living_organism=organism,
                source="COMPLIMENTARY",
                basis="BETA_OPT_IN",
                granting_party_id=None,
                policy_reference=f"policy-text:v{policy.policy_version}",
                policy_version=policy.policy_version,
                capabilities=capabilities,
                limits=limits,
                effective_at=effective_at,
                effective_until=None,
                ending_condition_reference=(
                    f"paid-availability-transition:{organism.organism_id}"
                ),
                tier_reference=None,
                pricing_basis_reference=None,
            )
        except IntegrityError as exc:
            # C4 translates only the named beta-per-organism uniqueness race.
            if _is_constraint_violation(
                exc,
                sqlstate="23505",
                constraint_name="se_entitlement_beta_one_per_lo_uniq",
            ):
                raise S015EntitlementRefused(
                    S015AuthorityRefusalCode.SE_ALREADY_BETA,
                    received_at=received_at,
                ) from exc
            raise
        rule_reference = f"rule:{rule.rule_uuid}@{rule.version}"
        OrganismEntitlementEvent.objects.using(self._alias).create(
            entitlement=entitlement,
            sequence=1,
            predecessor=None,
            event_type="GRANTED",
            actor_state="NONE",
            actor=None,
            actor_access_epoch=None,
            actor_capacity="NA_NO_PARTY",
            entry_mode="COMPOSED",
            enterer_identity=actor,
            composing_rule_state="REFERENCE",
            composing_rule=rule,
            composing_rule_reference=rule_reference,
            composer_state="NOT_ESTABLISHED",
            composer_identity=None,
            composed_from_event_uuid=composed_from_event_uuid,
            deciding_identity=None,
            authoriser_identity=None,
            authoriser_office=None,
            reason=None,
            occurred_at=effective_at,
            effective_at=effective_at,
            received_at=received_at,
        )
        return entitlement
