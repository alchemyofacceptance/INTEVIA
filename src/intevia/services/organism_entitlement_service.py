"""Entitlement election and entitlement command services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
import hashlib
from typing import Literal, Protocol
from uuid import UUID, uuid4

from django.contrib.auth.models import User
from django.db import DatabaseError, connections
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
    S015CommandReplayMismatch,
    _OrganismCommandService,
    _canonical_key,
    _canonical_uuid,
    _command_payload,
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
    def paid_availability_transition_reference(
        self,
        living_organism_uuid: UUID,
        effective_at: datetime,
        known_at: datetime,
    ) -> str | None: ...


class ProductionEntitlementAvailabilitySource:
    def __init__(self, *, database_alias: str = "default") -> None:
        if type(database_alias) is not str or not database_alias:
            raise ValueError("database_alias is required")
        self._alias = database_alias

    def paid_availability_transition_reference(
        self,
        living_organism_uuid: UUID,
        effective_at: datetime,
        known_at: datetime,
    ) -> str | None:
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
    credential: User
    living_organism_uuid: UUID
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    policy_version: int
    policy_text_sha256: str


@dataclass(frozen=True, slots=True)
class EntitlementExtensionCommand:
    credential: User
    entitlement_uuid: UUID
    request_reference: str
    idempotency_key: str
    reason: str


@dataclass(frozen=True, slots=True)
class EntitlementElectionResult:
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
        if type(result) is not cls:
            raise TypeError("entitlement election replay result has the wrong type")
        return {}


@dataclass(frozen=True, slots=True)
class AllowanceCheckResult:
    """Represent a read-only allowance decision and its authored outcome."""

    status: Literal["within", "exceeds", "cannot_check"]
    dimension: str
    proposed_total: int
    limit_kind: Literal["LIMIT", "UNLIMITED", "NOT_SET"]
    limit: int | None
    measured_usage: int | None
    unit: str
    code: S015AuthorityRefusalCode | None = None
    message: str | None = None
    technical_failure: bool = False


@dataclass(frozen=True, slots=True)
class _PreparedBirthingElection:
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
    if type(payload) is not dict:
        raise S015CommandReplayMismatch("completed receipt does not match request")
    try:
        return EntitlementElectionResult(
            living_organism_uuid=UUID(payload["living_organism_uuid"]),
            offering_choice=payload["offering_choice"],
            policy_version=payload["policy_version"],
            entitlement_uuid=(
                None
                if payload["entitlement_uuid"] is None
                else UUID(payload["entitlement_uuid"])
            ),
            event_uuid=UUID(payload["event_uuid"]),
            event_sequence=payload["event_sequence"],
            lineage_reference=payload["lineage_reference"],
            authority_reference=payload["authority_reference"],
            authority_decision_reference=payload["authority_decision_reference"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise S015CommandReplayMismatch(
            "completed receipt does not match request"
        ) from exc


class S015EntitlementUnavailable(Exception):
    pass


class S015EntitlementTechnicalFailure(Exception):
    pass


class S015EntitlementRefused(Exception):
    def __init__(
        self,
        refusal_code: S015AuthorityRefusalCode,
    ) -> None:
        super().__init__(_ENTITLEMENT_REFUSAL_MESSAGES[refusal_code])
        self.refusal_code = refusal_code


def entitlement_effective(
    living_organism_uuid: UUID,
    capability: str,
    *,
    at: datetime | None = None,
    database_alias: str = "default",
) -> bool:
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
    at: datetime,
    database_alias: str,
) -> int:
    if dimension == "circles":
        sql = """
            SELECT
                COUNT(*) FILTER (WHERE folded.state IS NULL),
                COUNT(*) FILTER (WHERE folded.state <> 'CLOSED')
            FROM (
                SELECT public.s015_fold_effective_state(
                    'core_circle', circle.id, %s, %s
                ) AS state
                FROM public.core_circle AS circle
                JOIN public.core_livingorganism AS organism
                  ON organism.id = circle.parent_organism_id
                WHERE organism.organism_id = %s
            ) AS folded
        """
        params = [at, at, living_organism_uuid]
    elif dimension == "organism_learners":
        sql = """
            SELECT
                COUNT(*) FILTER (WHERE folded.state IS NULL),
                COUNT(DISTINCT folded.identity_id) FILTER (
                    WHERE folded.state IN ('ACTIVE', 'PROBATIONARY')
                )
            FROM (
                SELECT
                    membership.identity_id,
                    public.s015_fold_effective_state(
                        'core_organismmembership', membership.id, %s, %s
                    ) AS state
                FROM public.core_organismmembership AS membership
                JOIN public.core_livingorganism AS organism
                  ON organism.id = membership.living_organism_id
                WHERE organism.organism_id = %s
            ) AS folded
        """
        params = [at, at, living_organism_uuid]
    elif dimension == "document_footprint":
        raise S015EntitlementTechnicalFailure(
            "document footprint cannot be measured without authoritative byte records"
        )
    else:
        raise ValueError("dimension is not registered")

    with connections[database_alias].cursor() as cursor:
        cursor.execute(sql, params)
        row = cursor.fetchone()
    if (
        row is None
        or type(row[0]) is not int
        or row[0] != 0
        or type(row[1]) is not int
        or row[1] < 0
    ):
        raise S015EntitlementTechnicalFailure(
            f"authoritative {dimension} usage could not be established"
        )
    return row[1]


def check_allowance(
    living_organism_uuid: UUID,
    dimension: str,
    proposed_total: int,
    at: datetime,
    *,
    database_alias: str = "default",
) -> AllowanceCheckResult:
    """Check a proposed total without writing or consulting dashboard state."""
    organism_uuid = _canonical_uuid(living_organism_uuid, "living_organism_uuid")
    effective_at = _validate_timestamp(at, "at")
    if dimension not in SE_ENTITLEMENT_DIMENSIONS:
        raise ValueError("dimension is not registered")
    if type(proposed_total) is not int or proposed_total < 0:
        raise ValueError("proposed_total must be a non-negative integer")

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
            proposed_total=proposed_total,
            limit_kind=kind,
            limit=None,
            measured_usage=None,
            unit=unit,
        )

    try:
        measured_usage = _measure_usage(
            organism_uuid,
            dimension,
            effective_at,
            database_alias,
        )
    except (DatabaseError, S015EntitlementTechnicalFailure):
        limit_text = _format_dimension_quantity(dimension, limit)
        dimension_text = _DIMENSION_PRESENTATION[dimension][0]
        return AllowanceCheckResult(
            status="cannot_check",
            dimension=dimension,
            proposed_total=proposed_total,
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

    if proposed_total > limit:
        return AllowanceCheckResult(
            status="exceeds",
            dimension=dimension,
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
    """Return the effective package and explicit per-dimension measurements."""
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
        try:
            usage = _measure_usage(
                organism_uuid,
                dimension,
                effective_at,
                database_alias,
            )
        except (DatabaseError, S015EntitlementTechnicalFailure):
            usage = None
            measurement_reason = "UNMEASURABLE"
        dimensions[dimension] = {
            "kind": kind,
            "limit": limit,
            "measured_usage": usage,
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
    """Separate entitlement availability from undelivered owner-slice status."""
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
        super().__init__(
            authority=authority,
            clock=clock,
            database_alias=database_alias,
        )
        self._availability_source = availability_source or (
            ProductionEntitlementAvailabilitySource(database_alias=database_alias)
        )

    def _replay_result(self, payload: object) -> EntitlementElectionResult:
        return _election_result_from_payload(payload)

    def _policy_record(
        self,
        *,
        policy_version: int,
        policy_text_sha256: str,
    ) -> OrganismEntitlementPolicyText:
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

    def _effective_beta_grant(
        self,
        organism: LivingOrganism,
        effective_at: datetime,
        received_at: datetime,
    ) -> OrganismEntitlement | None:
        return (
            OrganismEntitlement.objects.using(self._alias)
            .filter(
                living_organism=organism,
                basis__in=("BETA_OPT_IN", "AUTHORISED_EXTENSION"),
                effective_at__lte=effective_at,
                recorded_at__lte=received_at,
            )
            .filter(
                Q(effective_until__isnull=True)
                | Q(effective_until__gt=effective_at)
            )
            .order_by("-effective_at", "-recorded_at", "-pk")
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
        """Create an election version with only the election-specific payload.

        HG_DECISION_SE_GATE_01_FIX_IN_SE_20261012 and commission §4.1 keep
        application-settings fields on their own action kind.
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
        if connections[self._alias].in_atomic_block:
            raise S015EntitlementTechnicalFailure(
                "entitlement opt-in cannot run inside an existing transaction"
            )
        with authority_atomic(self._alias):
            actor = _lock_actor(
                database_alias=self._alias,
                credential=command.credential,
                unavailable=S015EntitlementUnavailable,
            )
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
            received_at = _validate_timestamp(self._clock(), "received_at")
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
            if self._effective_beta_grant(organism, effective_at, received_at) is not None:
                raise S015EntitlementRefused(S015AuthorityRefusalCode.SE_ALREADY_BETA)
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
                raise S015EntitlementRefused(S015AuthorityRefusalCode.SE_OFFER_CLOSED)
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
            version.refresh_from_db(using=self._alias, fields=["recorded_at"])
            if version.recorded_at is None:
                raise S015EntitlementTechnicalFailure(
                    "the entitlement election version record time is unavailable"
                )
            evaluated_at = max(received_at, version.recorded_at)
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
                        _ENTITLEMENT_REFUSAL_MESSAGES[
                            S015AuthorityRefusalCode.SE_OPT_IN_NEEDS_CQ
                        ],
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
            )

    def refuse_person_grant(self) -> None:
        raise S015EntitlementRefused(S015AuthorityRefusalCode.SE_GRANT_NOT_BY_HAND)

    def extend_entitlement(
        self,
        command: EntitlementExtensionCommand,
    ) -> None:
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
