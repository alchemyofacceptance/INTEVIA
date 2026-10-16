"""PKT-B S3 applicant and Coordinator application commands."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Protocol
from uuid import UUID, uuid4

from django.contrib.auth.models import User
from django.db import IntegrityError, connections, transaction

from core.models import (
    Application, ApplicationDecision, ApplicationDecisionEffect, ApplicationEngagement,
    ApplicationLineage, ApplicationLineageEvent,
    ApplicationWithdrawal, CSAAssignment, CSAAssignmentEnd, ContextualRoleAssignment,
    Identity, IdentityTransition, LivingOrganism, LivingOrganismEvent,
    LOApplicationGateway, LOApplicationSettingsVersion, LOApplicationQuestionVersion,
    LOGovernanceConfigurationVersion, LOFoundingContentVersion, OrganismCommandReceipt,
    OrganismMembership, OrganismMembershipTransition,
)
from core.organism_contract import OrganismAction
from src.intevia.services.organism_authority import (
    QualifiedS015AuthorityDecision, S015Authority, S015AuthorityDenied,
    S015AuthorityRefusalCode, S015OwnApplicantAuthorityRequest, authority_target_fingerprint,
    canonical_json_bytes, canonical_timestamp,
)
from src.intevia.services.organism_command_support import (
    ReplayResultType,
    ReplayViewer,
    S015CommandReplayMismatch, _canonical_key, _canonical_uuid, _command_payload,
    _empty_anchor_fingerprint, _lock_actor, _lock_actor_membership_context,
    _read_actor_active_membership, _resulting_temporal_basis, _validate_timestamp, _authority_request,
    acquire_organism_command_mutex,
    # Shared helper keeps the 15 Oct T-LOCK-1 translation outside the transaction.
    authority_atomic as _authority_atomic,
    serve_replay,
    WithheldField,
)
from src.intevia.services.organism_membership_service import S015OperationNotYetImplemented
from src.intevia.services.organism_role_service import OrganismRoleService
from src.intevia.services.organism_formation_service import _sbg_readiness_message


_COMMAND_DOMAIN = b"INTEVIA:S015:APPLICATION_COMMAND:v1\x00"
_PAIR_DOMAIN = b"INTEVIA:S015:OPEN_APPLICATION_PAIR:v1\x00"
_DUE_SOURCE = "DEFAULT (no settings store)"
_SBG_ENABLING_REFUSAL_CODES = {
    S015AuthorityRefusalCode.SBG_GOVERNANCE_CIRCLE_MISSING,
    S015AuthorityRefusalCode.SBG_ARRANGEMENT_MISSING,
    S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
    S015AuthorityRefusalCode.SBG_ARRANGEMENT_FIELD_UNSUPPORTED,
    S015AuthorityRefusalCode.SBG_ELIGIBILITY_UNSUPPORTED,
    S015AuthorityRefusalCode.SBG_NO_INITIAL_PARTICIPATION_ROUTE,
    S015AuthorityRefusalCode.SBG_IL_GRESD,
}


def _governance_enabling_refusal_message(
    *,
    database_alias: str,
    organism: LivingOrganism,
    effective_at: datetime,
    recorded_at: datetime,
    code: S015AuthorityRefusalCode,
) -> str:
    if code is S015AuthorityRefusalCode.SBG_IL_GRESD:
        return (
            "GOVERNANCE was not switched on and nothing changed. This LO's GOVERNANCE Circle "
            "was active before and is now dormant; its next step is the restoration route for "
            "a dormant GOVERNANCE, which is not yet available in this release. This is a limit "
            "of the current build, not a refusal of the LO's choice. The LO's Coordinators "
            "continue to govern until that route is delivered."
        )
    message = _sbg_readiness_message(
        code=code,
        act="GOVERNANCE was not switched on",
        database_alias=database_alias,
        organism=organism,
        effective_at=effective_at,
        recorded_at=recorded_at,
    )
    if message is None:
        raise RuntimeError("SB-G enabling refusal has no authored message")
    return message


class S015ApplicationError(Exception):
    pass


class S015ApplicationAuthorityUnavailable(S015ApplicationError):
    pass


class S015ApplicationRefused(S015ApplicationError):
    def __init__(
        self,
        message: str,
        *,
        refusal_code: S015AuthorityRefusalCode | None = None,
    ) -> None:
        super().__init__(message)
        self.refusal_code = refusal_code


class S015ApplicationNotFound(S015ApplicationError):
    pass


class LONameSource(Protocol):
    def name_for(self, living_organism: LivingOrganism) -> str: ...


class FormationLONameSource:
    def name_for(self, living_organism: LivingOrganism) -> str:
        database_alias = living_organism._state.db or "default"
        name = LOFoundingContentVersion.objects.using(database_alias).filter(
            living_organism_id=living_organism.pk,
            version=1,
        ).values_list("name", flat=True).first()
        if name is None:
            raise S015OperationNotYetImplemented("lo_name", "S3-IL-LONAME", "FORMATION")
        return name


class CareExecutor(Protocol):
    def identity_for(self, living_organism: LivingOrganism) -> Identity: ...


class DiscussionWelcome(Protocol):
    def send_for(self, application: Application, decision: ApplicationDecision) -> None: ...


class UnavailableDiscussionWelcome:
    def send_for(self, application: Application, decision: ApplicationDecision) -> None:
        raise S015OperationNotYetImplemented("automatic_welcome", "S3-IL-NOTICE", "DISCUSSION")


def default_application_question(living_organism: LivingOrganism, name_source: LONameSource | None = None) -> dict[str, str]:
    name = (name_source or FormationLONameSource()).name_for(living_organism)
    if type(name) is not str or not name.strip():
        raise S015ApplicationRefused("formation LO name is required")
    return {"question_text": f"Why do you want to join {name}?", "answer_format": "LONG_ANSWER"}


@dataclass(frozen=True, slots=True)
class SubmitApplicationCommand:
    credential: User
    living_organism_id: UUID
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    effective_at: datetime
    received_at: datetime
    predecessor_reference: UUID | None = None
    answer: str = ""


@dataclass(frozen=True, slots=True)
class GatewayDraftCommand:
    credential: User
    living_organism_id: UUID
    action: OrganismAction
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    effective_at: datetime
    received_at: datetime
    fields: dict[str, object]
    version_uuid: UUID | None = None


@dataclass(frozen=True, slots=True)
class EffectGatewayVersionCommand:
    credential: User
    living_organism_id: UUID
    action: OrganismAction
    version_uuid: UUID
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    effective_at: datetime
    received_at: datetime


@dataclass(frozen=True, slots=True)
class WithdrawApplicationCommand:
    """The receiving surface supplies received_at; the applicant does not choose it."""

    credential: User
    application_uuid: UUID
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    effective_at: datetime
    received_at: datetime


@dataclass(frozen=True, slots=True)
class ClaimApplicationCommand:
    credential: User
    application_uuid: UUID
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    effective_at: datetime
    received_at: datetime


@dataclass(frozen=True, slots=True)
class ReassignApplicationCommand(ClaimApplicationCommand):
    pass


@dataclass(frozen=True, slots=True)
class DecideApplicationCommand(ClaimApplicationCommand):
    outcome: str


@dataclass(frozen=True, slots=True)
class ApplicationCommandResult:
    application_uuid: UUID
    action: OrganismAction
    recorded_at: datetime
    lineage_reference: str
    authority_reference: str | WithheldField
    authority_decision_reference: str
    record_uuid: UUID | None | WithheldField = None
    completion_due_at: datetime | None = None
    completion_due_source: str | None = None

    def receipt_payload(self) -> dict[str, object]:
        return {
            "application_uuid": str(self.application_uuid),
            "action": self.action.value,
            "recorded_at": canonical_timestamp(self.recorded_at),
            "lineage_reference": self.lineage_reference,
            "authority_reference": self.authority_reference,
            "authority_decision_reference": self.authority_decision_reference,
            "record_uuid": None if self.record_uuid is None else str(self.record_uuid),
            "completion_due_at": None if self.completion_due_at is None else canonical_timestamp(self.completion_due_at),
            "completion_due_source": self.completion_due_source,
        }

    @classmethod
    def replay_field_audiences(cls, result: "ApplicationCommandResult") -> dict[str, tuple[str, ...]]:
        if result.action is OrganismAction.DECIDE_APPLICATION:
            return {"record_uuid": ("PROCESSING", "APPLICANT")}
        return {}


@dataclass(frozen=True, slots=True)
class GatewayEffectCommandResult:
    version_uuid: UUID
    action: OrganismAction
    effective_at: datetime
    lineage_reference: str
    authority_reference: str | WithheldField
    authority_decision_reference: str
    event_uuid: UUID | WithheldField
    event_sequence: int | WithheldField

    def receipt_payload(self) -> dict[str, object]:
        return {
            "action": self.action.value,
            "authority_decision_reference": self.authority_decision_reference,
            "authority_reference": self.authority_reference,
            "effective_at": canonical_timestamp(self.effective_at),
            "event_sequence": self.event_sequence,
            "event_uuid": str(self.event_uuid),
            "lineage_reference": self.lineage_reference,
            "version_uuid": str(self.version_uuid),
        }

    @classmethod
    def replay_field_audiences(cls, result: "GatewayEffectCommandResult") -> dict[str, str]:
        return {"event_uuid": "PROCESSING", "event_sequence": "PROCESSING"}


def _gateway_result_from_receipt(payload: object) -> GatewayEffectCommandResult:
    if type(payload) is not dict:
        raise S015ApplicationError("gateway receipt payload is malformed")
    return GatewayEffectCommandResult(
        version_uuid=UUID(payload["version_uuid"]),
        action=OrganismAction(payload["action"]),
        effective_at=datetime.fromisoformat(payload["effective_at"].replace("Z", "+00:00")),
        lineage_reference=payload["lineage_reference"],
        authority_reference=payload["authority_reference"],
        authority_decision_reference=payload["authority_decision_reference"],
        event_uuid=UUID(payload["event_uuid"]),
        event_sequence=int(payload["event_sequence"]),
    )


def applicant_eligible(
    identity: Identity, effective_at: datetime, recorded_at: datetime, *, database_alias: str = "default",
) -> bool:
    """Read account history using its one time per transition, occurred_at, not B-G2's separate recorded time."""
    history = IdentityTransition.objects.using(database_alias).filter(identity=identity)
    if not history.filter(action=IdentityTransition.Action.PROVISION, occurred_at__lte=effective_at).exists():
        return False
    latest = history.filter(occurred_at__lte=recorded_at).order_by("-occurred_at", "-pk").first()
    return latest is not None and latest.resulting_state == Identity.AccessState.ACTIVE


_VERSION_KINDS = {
    OrganismAction.EFFECT_APPLICATION_SETTINGS: (LOApplicationSettingsVersion, "application-settings-version:"),
    OrganismAction.EFFECT_APPLICATION_QUESTION: (LOApplicationQuestionVersion, "application-question-version:"),
    OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION: (LOGovernanceConfigurationVersion, "governance-configuration-version:"),
}


def _effective_gateway_version(
    database_alias: str, organism: LivingOrganism, action: OrganismAction,
    at: datetime, known_at: datetime | None = None,
):
    model, prefix = _VERSION_KINDS[action]
    known_at = at if known_at is None else known_at
    references = LivingOrganismEvent.objects.using(database_alias).filter(
        living_organism=organism, action=action.value,
        effective_at__lte=at, received_at__lte=known_at, recorded_at__lte=known_at,
    ).order_by("-effective_at", "-occurred_at", "-received_at", "-recorded_at", "-sequence", "-event_uuid")
    reference = references.values_list("evidence_reference", flat=True).first()
    if reference is None:
        return None
    versions = model.objects.using(database_alias).filter(
        version_uuid=UUID(reference.removeprefix(prefix)),
    )
    if action is OrganismAction.EFFECT_APPLICATION_SETTINGS:
        versions = versions.filter(
            entitlement_choice__isnull=True,
            entitlement_election_route__isnull=True,
        )
    return versions.get()


def _engagement_settings_basis(
    database_alias: str, organism: LivingOrganism, effective_at: datetime,
) -> str:
    with connections[database_alias].cursor() as cursor:
        cursor.execute("SELECT clock_timestamp()")
        known_at = cursor.fetchone()[0]
    configuration = _effective_gateway_version(
        database_alias, organism, OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
        effective_at, known_at,
    )
    settings = _effective_gateway_version(
        database_alias, organism, OrganismAction.EFFECT_APPLICATION_SETTINGS,
        effective_at, known_at,
    )
    if configuration is None or settings is None:
        raise S015ApplicationRefused("effective governance configuration and application settings are required")
    return (
        f"governance-configuration-version:{configuration.version_uuid};"
        f"application-settings-version:{settings.version_uuid}"
    )


def _validate_gateway_answer(question: LOApplicationQuestionVersion, answer: str) -> None:
    if type(answer) is not str:
        raise S015ApplicationRefused("application answer is invalid")
    kind = question.answer_format
    if kind == "SHORT_ANSWER":
        valid = 0 < len(answer.strip()) <= 255
    elif kind == "LONG_ANSWER":
        valid = 0 < len(answer.strip()) <= 4096
    elif kind == "YES_NO":
        valid = answer in ("YES", "NO")
    elif kind == "MULTIPLE_CHOICE":
        valid = isinstance(question.options, list) and answer in question.options
    elif kind == "SCALE_COMPLEX":
        try:
            scale = int(answer)
            bounds = [int(option) for option in question.options]
            valid = str(scale) == answer and len(bounds) >= 2 and min(bounds) <= scale <= max(bounds)
        except (ValueError, TypeError):
            valid = False
    else:
        valid = False
    if not valid:
        raise S015ApplicationRefused("application answer is invalid for the effective question")


def gateway_version_status(database_alias: str, version, at: datetime) -> str:
    """Derive draft/effective/superseded; PROPOSED is reserved for the unbuilt CQ route."""
    if isinstance(version, LOApplicationSettingsVersion) and (
        version.entitlement_choice is not None
        or version.entitlement_election_route is not None
    ):
        raise ValueError("entitlement election versions are not application gateway versions")
    action = next(action for action, (model, _) in _VERSION_KINDS.items() if isinstance(version, model))
    prefix = _VERSION_KINDS[action][1]
    reference = prefix + str(version.version_uuid)
    events = LivingOrganismEvent.objects.using(database_alias).filter(
        living_organism=version.living_organism, action=action.value,
        effective_at__lte=at, received_at__lte=at, recorded_at__lte=at,
    ).order_by("-effective_at", "-occurred_at", "-received_at", "-recorded_at", "-sequence", "-event_uuid")
    current = events.values_list("evidence_reference", flat=True).first()
    if current == reference:
        return "EFFECTIVE"
    if events.filter(evidence_reference=reference).exists():
        return "SUPERSEDED"
    return "DRAFT"


def _gateway_version_complete(version) -> bool:
    if isinstance(version, LOApplicationSettingsVersion):
        return (version.entitlement_choice is None and version.entitlement_election_route is None
                and version.applications_open is not None and version.automatic_acceptance is not None
                and version.processing_responsibility in ("LO", "APPLICATION_CIRCLE"))
    if isinstance(version, LOApplicationQuestionVersion):
        options = version.options
        return (bool(version.question_text.strip()) and
                version.answer_format in ("SHORT_ANSWER", "LONG_ANSWER", "YES_NO", "MULTIPLE_CHOICE", "SCALE_COMPLEX")
                and (isinstance(options, list) and 2 <= len(options) <= 7
                     if version.answer_format in ("MULTIPLE_CHOICE", "SCALE_COMPLEX") else options in (None, [])))
    return all(getattr(version, field) is not None for field in (
        "cq_active", "inherit_portal_cq_settings", "require_cq_to_change_lo_settings",
        "require_cq_to_change_application_question",
    ))


def _result_from_receipt(payload: object) -> ApplicationCommandResult:
    if not isinstance(payload, dict):
        raise S015ApplicationError("application receipt payload is malformed")
    return ApplicationCommandResult(
        application_uuid=UUID(payload["application_uuid"]),
        action=OrganismAction(payload["action"]),
        recorded_at=datetime.fromisoformat(payload["recorded_at"].replace("Z", "+00:00")),
        lineage_reference=payload["lineage_reference"],
        authority_reference=payload["authority_reference"],
        authority_decision_reference=payload["authority_decision_reference"],
        record_uuid=None if payload.get("record_uuid") is None else UUID(payload["record_uuid"]),
        completion_due_at=None if payload.get("completion_due_at") is None else datetime.fromisoformat(
            payload["completion_due_at"].replace("Z", "+00:00")
        ),
        completion_due_source=payload.get("completion_due_source"),
    )


def _insert_admission_transition(
    *, database_alias: str, membership: OrganismMembership,
    predecessor: OrganismMembershipTransition | None, actor: Identity | None,
    enterer: Identity, basis_id: int, prior_state: str | None, resulting_state: str,
    reason_class: str, evidence_reference: str, authority_decision_reference: str,
    request_reference: str, idempotency_key: str, payload_fingerprint: str,
    occurred_at: datetime, effective_at: datetime, received_at: datetime,
    actor_access_epoch: int | None = None,
    composing_rule_reference: str | None = None,
) -> int:
    event_uuid = uuid4()
    sequence = 1 if predecessor is None else predecessor.sequence + 1
    temporal_kind, temporal_reference = _resulting_temporal_basis(
        payload_fingerprint=payload_fingerprint, effective_at=effective_at, received_at=received_at,
    )
    lineage_reference = "s015l1:" + hashlib.sha256(_COMMAND_DOMAIN + canonical_json_bytes({
        "event": event_uuid, "membership": membership.membership_uuid,
        "sequence": sequence, "fingerprint": payload_fingerprint,
    })).hexdigest()
    with connections[database_alias].cursor() as cursor:
        predecessor_commitment = None
        if predecessor is not None:
            cursor.execute(
                "SELECT l1_commitment FROM public.core_organismmembershiptransition WHERE id = %s",
                [predecessor.pk],
            )
            predecessor_commitment = cursor.fetchone()[0]
        cursor.execute(
            "SELECT public.s015_0022_digest('s015p1', public.s015_0022_parts_preimage(%s, %s::uuid))",
            ["core_organismmembershiptransition", event_uuid],
        )
        parts_commitment = cursor.fetchone()[0]
        cursor.execute("""
            INSERT INTO public.core_organismmembershiptransition (
                event_uuid, membership_id, sequence, predecessor_id, predecessor_sequence, action,
                actor_id, actor_state, actor_capacity, enterer_identity_id, entry_mode,
                composing_rule_state, composing_rule_reference, composer_state, composer_identity_id,
                effective_until, effective_until_state, l1_commitment, predecessor_l1_commitment,
                l2_commitment, parts_commitment, actor_access_epoch, authority_basis_id,
                authority_decision_reference, evidence_reference, request_reference, idempotency_key,
                payload_fingerprint, lineage_reference, temporal_basis_kind, temporal_basis_reference,
                occurred_at, effective_at, received_at, prior_state, resulting_state, reason_class
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            ) RETURNING id
        """, [
            event_uuid, membership.pk, sequence, None if predecessor is None else predecessor.pk,
            None if predecessor is None else predecessor.sequence, OrganismAction.TRANSITION_MEMBERSHIP.value,
            None if actor is None else actor.pk, "LIVING_ORGANISM" if actor is None else "PARTY",
            "STANDING", enterer.pk, "COMPOSED" if composing_rule_reference else "TRANSPORT",
            "REFERENCE" if composing_rule_reference else "NA_TRANSPORT", composing_rule_reference,
            "REFERENCE" if composing_rule_reference else "NA_TRANSPORT",
            enterer.pk if composing_rule_reference else None,
            None, "NONE", None, predecessor_commitment, None, parts_commitment,
            None if actor is None else actor_access_epoch, basis_id,
            authority_decision_reference, evidence_reference, request_reference, idempotency_key,
            payload_fingerprint, lineage_reference, temporal_kind, temporal_reference,
            occurred_at, effective_at, received_at, prior_state, resulting_state, reason_class,
        ])
        return cursor.fetchone()[0]


def _lock_owning_organism(database_alias: str, organism_id) -> None:
    """Take the registered organism lock (s015_lock_organisms). Every S3 path takes it first among its per-LO locks: before the gateway head,
    the pair lock, the membership helper and application rows."""
    with connections[database_alias].cursor() as cursor:
        cursor.execute("SELECT public.s015_lock_organisms(ARRAY[%s]::uuid[])", [organism_id])


def _append_application_event(
    *, database_alias: str, application: Application, action: OrganismAction,
    resulting_state: str, actor: Identity | None, enterer: Identity,
    actor_access_epoch: int | None, authority_decision_reference: str,
    evidence_reference: str, request_reference: str, idempotency_key: str,
    payload_fingerprint: str, occurred_at: datetime, effective_at: datetime,
    received_at: datetime,
    composing_rule_reference: str | None = None,
) -> None:
    lineage = ApplicationLineage.objects.using(database_alias).select_for_update().get(application=application)
    predecessor = ApplicationLineageEvent.objects.using(database_alias).filter(
        pk=lineage.head_event_id,
    ).first()
    if predecessor is not None and (effective_at, occurred_at, received_at) < (
        predecessor.effective_at, predecessor.occurred_at, predecessor.received_at,
    ):
        raise S015OperationNotYetImplemented("out_of_order_application_act", "IL-3", "S5")
    event_uuid = uuid4()
    sequence = 1 if predecessor is None else predecessor.sequence + 1
    temporal_kind, temporal_reference = _resulting_temporal_basis(
        payload_fingerprint=payload_fingerprint, effective_at=effective_at, received_at=received_at,
    )
    lineage_reference = "s015l1:" + hashlib.sha256(_COMMAND_DOMAIN + canonical_json_bytes({
        "event": event_uuid, "application": application.application_uuid,
        "sequence": sequence, "fingerprint": payload_fingerprint,
    })).hexdigest()
    with connections[database_alias].cursor() as cursor:
        predecessor_commitment = None
        if predecessor is not None:
            cursor.execute(
                "SELECT l1_commitment FROM public.core_applicationlineageevent WHERE id = %s",
                [predecessor.pk],
            )
            predecessor_commitment = cursor.fetchone()[0]
        cursor.execute(
            "SELECT public.s015_0022_digest('s015p1', public.s015_0022_parts_preimage(%s, %s::uuid))",
            ["core_applicationlineageevent", event_uuid],
        )
        parts_commitment = cursor.fetchone()[0]
        columns = (
            "event_uuid", "application_lineage_id", "sequence", "predecessor_id",
            "predecessor_sequence", "action", "actor_id", "actor_state", "actor_capacity",
            "enterer_identity_id", "entry_mode", "composing_rule_state", "composing_rule_reference",
            "composer_state", "composer_identity_id", "effective_until", "effective_until_state",
            "l1_commitment", "predecessor_l1_commitment", "l2_commitment", "parts_commitment",
            "actor_access_epoch", "authority_basis_id", "authority_decision_reference",
            "evidence_reference", "request_reference", "idempotency_key", "payload_fingerprint",
            "lineage_reference", "temporal_basis_kind", "temporal_basis_reference", "occurred_at",
            "effective_at", "received_at", "prior_state", "resulting_state",
        )
        values = (
            event_uuid, lineage.pk, sequence, None if predecessor is None else predecessor.pk,
            None if predecessor is None else predecessor.sequence, action.value,
            None if actor is None else actor.pk, "LIVING_ORGANISM" if actor is None else "PARTY",
            "STANDING", enterer.pk, "COMPOSED" if composing_rule_reference else "TRANSPORT",
            "REFERENCE" if composing_rule_reference else "NA_TRANSPORT", composing_rule_reference,
            "REFERENCE" if composing_rule_reference else "NA_TRANSPORT",
            enterer.pk if composing_rule_reference else None,
            None, "NONE", None, predecessor_commitment, None, parts_commitment,
            actor_access_epoch, application.living_organism.founding_authority_basis_id,
            authority_decision_reference, evidence_reference, request_reference, idempotency_key,
            payload_fingerprint, lineage_reference, temporal_kind, temporal_reference,
            occurred_at, effective_at, received_at,
            None if predecessor is None else predecessor.resulting_state, resulting_state,
        )
        cursor.execute(
            f"INSERT INTO public.core_applicationlineageevent ({', '.join(columns)}) "
            f"VALUES ({', '.join(['%s'] * len(columns))})",
            values,
        )


@dataclass(frozen=True, slots=True)
class _CoordinatorContext:
    actor: Identity
    membership: OrganismMembership
    application: Application
    decision: QualifiedS015AuthorityDecision
    fingerprint: str
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    effective_at: datetime
    received_at: datetime


@dataclass(frozen=True, slots=True)
class _AdmissionDetails:
    application: Application
    enterer: Identity
    authority_decision_reference: str
    request_reference: str
    idempotency_key: str
    fingerprint: str
    occurred_at: datetime
    effective_at: datetime
    received_at: datetime
    composing_rule_reference: str | None = None


class OrganismApplicationService:
    def __init__(
        self, *, authority: S015Authority | None = None, clock: Callable[[], datetime], database_alias: str = "default",
        name_source: LONameSource | None = None,
        care_executor: CareExecutor | None = None, welcome: DiscussionWelcome | None = None,
    ) -> None:
        if authority is None:
            from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider

            authority = S015Authority(
                provider=ProductionApplicationAuthorityProvider(database_alias=database_alias),
                database_alias=database_alias,
            )
        if not isinstance(authority, S015Authority) or authority.database_alias != database_alias:
            raise TypeError("matching S015 authority is required")
        if not callable(clock):
            raise TypeError("clock is required")
        self._authority = authority
        self._clock = clock
        self._alias = database_alias
        self._name_source = name_source
        self._care_executor = care_executor
        self._welcome = welcome or UnavailableDiscussionWelcome()

    def _receipt_or_none(
        self, *, actor: Identity, action: OrganismAction, request_reference: str,
        idempotency_key: str, fingerprint: str, viewer: ReplayViewer,
        result_class: type = ApplicationCommandResult,
    ) -> ApplicationCommandResult | GatewayEffectCommandResult | None:
        receipt = OrganismCommandReceipt.objects.using(self._alias).select_for_update().filter(
            actor=actor, action=action.value, idempotency_key=idempotency_key,
        ).first()
        if receipt is None:
            return None
        if receipt.request_reference != request_reference or receipt.payload_fingerprint != fingerprint:
            raise S015CommandReplayMismatch("completed receipt does not match request")
        result = (
            _gateway_result_from_receipt(receipt.result_payload)
            if result_class is GatewayEffectCommandResult
            else _result_from_receipt(receipt.result_payload)
        )
        return serve_replay(
            result,
            viewer=viewer,
            result_type=ReplayResultType(result_class, self._alias),
        )

    def _authorise(
        self, *, actor: Identity, action: OrganismAction, organism: LivingOrganism,
        application: Application | None,
        request_reference: str, idempotency_key: str, received_at: datetime,
        effective_at: datetime, predecessor_reference: UUID | None = None,
    ):
        authority_target: dict[str, object] = {
            "action": action.value,
            "organism_uuid": organism.organism_id,
            "applicant_identity_uuid": actor.identity_id,
        }
        if application is not None:
            authority_target["application_uuid"] = application.application_uuid
        return self._authority.qualify(S015OwnApplicantAuthorityRequest(
            database_alias=self._alias,
            actor_pk=actor.pk,
            actor_identity_id=actor.identity_id,
            actor_credential_pk=actor.credential_id,
            actor_access_epoch=actor.access_epoch,
            action=action,
            command_subtype=action.value,
            target_fingerprint=authority_target_fingerprint(authority_target),
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            evaluated_at=received_at,
            living_organism_pk=organism.pk,
            application_pk=None if application is None else application.pk,
            effective_at=effective_at,
            target=authority_target,
            living_organism_id=organism.organism_id,
            predecessor_reference=predecessor_reference,
        ))

    def _finish(
        self, *, actor: Identity, action: OrganismAction, request_reference: str,
        idempotency_key: str, fingerprint: str, application: Application, recorded_at: datetime,
        authority_reference: str, authority_decision_reference: str,
        record_uuid: UUID | None = None, completion_due_at: datetime | None = None,
        completion_due_source: str | None = None,
    ) -> ApplicationCommandResult:
        lineage_reference = "s015l1:" + hashlib.sha256(
            _COMMAND_DOMAIN + canonical_json_bytes({
                "action": action.value, "actor": actor.identity_id, "application": application.application_uuid,
                "fingerprint": fingerprint,
            })
        ).hexdigest()
        result = ApplicationCommandResult(
            application_uuid=application.application_uuid,
            action=action,
            recorded_at=recorded_at,
            lineage_reference=lineage_reference,
            authority_reference=authority_reference,
            authority_decision_reference=authority_decision_reference,
            record_uuid=record_uuid, completion_due_at=completion_due_at,
            completion_due_source=completion_due_source,
        )
        OrganismCommandReceipt.objects.using(self._alias).create(
            actor=actor, action=action.value, idempotency_key=idempotency_key,
            request_reference=request_reference, payload_fingerprint=fingerprint,
            authority_decision_reference=authority_decision_reference,
            lineage_reference=lineage_reference, result_payload=result.receipt_payload(),
        )
        return serve_replay(
            result,
            viewer=ReplayViewer(actor.pk, actor.identity_id, self._clock()),
            result_type=ReplayResultType(ApplicationCommandResult, self._alias),
        )

    def _pair_lock(self, actor: Identity, organism: LivingOrganism) -> None:
        key = int.from_bytes(hashlib.sha256(
            _PAIR_DOMAIN + actor.identity_id.bytes + organism.organism_id.bytes,
        ).digest()[:8], "big", signed=True)
        with connections[self._alias].cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [key])

    def _automatic_accept(self, application: Application, care: Identity, request_reference: str,
                          idempotency_key: str, fingerprint: str) -> tuple[ApplicationDecision, str, str, str, str, str]:
        settings = _effective_gateway_version(
            self._alias, application.living_organism, OrganismAction.EFFECT_APPLICATION_SETTINGS,
            application.effective_at, application.recorded_at,
        )
        if settings is None or settings.pk != application.settings_version_id or not settings.applications_open or not settings.automatic_acceptance:
            raise S015ApplicationRefused("automatic acceptance requires the application's recorded effective setting")
        rule = f"application-settings-version:{settings.version_uuid}"
        decision_fingerprint = hashlib.sha256(_COMMAND_DOMAIN + canonical_json_bytes({
            "action": "LO_AUTOMATIC", "application": application.application_uuid,
            "rule": rule, "submission": fingerprint,
        })).hexdigest()
        decision_reference = "s015d1:" + hashlib.sha256(
            _COMMAND_DOMAIN + canonical_json_bytes({"automatic": decision_fingerprint, "rule": rule})
        ).hexdigest()
        automatic_key = f"s3fu:auto:{decision_fingerprint}"
        automatic_request = f"s3fu:auto:{decision_fingerprint}"
        ApplicationEngagement.objects.using(self._alias).create(
            application=application, engagement_ordinal=1, route="LO_AUTOMATIC",
            deciding_party=application.living_organism, engagement_reference=rule,
            settings_basis=rule, actor=None, enterer=care,
            occurred_at=application.occurred_at, effective_at=application.effective_at,
            received_at=application.received_at,
        )
        decision = ApplicationDecision.objects.using(self._alias).create(
            application=application, route="LO_AUTOMATIC", deciding_party=application.living_organism,
            outcome="ACCEPT", authority_decision_reference=decision_reference, executor=care, enterer=care,
            occurred_at=application.occurred_at, effective_at=application.effective_at,
            received_at=application.received_at,
        )
        self._admit(_AdmissionDetails(
            application, care, decision_reference, automatic_request, automatic_key,
            decision_fingerprint, application.occurred_at, application.effective_at,
            application.received_at, rule,
        ), decision)
        return decision, decision_fingerprint, decision_reference, rule, automatic_key, automatic_request

    def _gateway_start(self, command, *, create_head: bool):
        action = command.action
        if action not in _VERSION_KINDS:
            raise ValueError("unknown gateway relation")
        request_reference = _canonical_key(command.request_reference, "request_reference", 128)
        idempotency_key = _canonical_key(command.idempotency_key, "idempotency_key", 120)
        occurred_at = _validate_timestamp(command.occurred_at, "occurred_at")
        effective_at = _validate_timestamp(command.effective_at, "effective_at")
        received_at = _validate_timestamp(command.received_at, "received_at")
        organism_uuid = _canonical_uuid(command.living_organism_id, "living_organism_id")
        actor = _lock_actor(database_alias=self._alias, credential=command.credential,
                            unavailable=S015ApplicationAuthorityUnavailable)
        acquire_organism_command_mutex(database_alias=self._alias, actor_identity_id=actor.identity_id,
                                       action=action, idempotency_key=idempotency_key)
        organism = LivingOrganism.objects.using(self._alias).get(organism_id=organism_uuid)
        target = {"action": action.value, "organism": organism_uuid,
                  "version": command.version_uuid}
        authority_target = {
            "action": action.value,
            "organism_uuid": organism_uuid,
            "version_uuid": command.version_uuid,
        }
        if not create_head:
            fingerprint = _command_payload(
                domain=_COMMAND_DOMAIN, schema="s015-gateway-v1", action=action,
                actor_identity_id=actor.identity_id, actor_access_epoch=actor.access_epoch,
                request_reference=request_reference, idempotency_key=idempotency_key,
                occurred_at=occurred_at, target=target,
                command={"effective_at": effective_at, "received_at": received_at,
                         "fields": getattr(command, "fields", None)},
            )
            replay = self._receipt_or_none(
                actor=actor,
                action=action,
                request_reference=request_reference,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                viewer=ReplayViewer(actor.pk, actor.identity_id, self._clock()),
                result_class=GatewayEffectCommandResult,
            )
            if replay is not None:
                return replay
            _read_actor_active_membership(
                database_alias=self._alias,
                actor_identity_pk=actor.pk,
                living_organism_pk=organism.pk,
                unavailable=S015ApplicationAuthorityUnavailable,
            )
        _lock_owning_organism(self._alias, organism.organism_id)
        membership, role_refs = _lock_actor_membership_context(
            database_alias=self._alias, actor_identity_id=actor.identity_id,
            living_organism_id=organism_uuid, unavailable=S015ApplicationAuthorityUnavailable,
        )
        gateway = LOApplicationGateway.objects.using(self._alias).select_for_update().filter(
            living_organism=organism,
        ).first()
        if create_head:
            fingerprint = _command_payload(
                domain=_COMMAND_DOMAIN, schema="s015-gateway-v1", action=action,
                actor_identity_id=actor.identity_id, actor_access_epoch=actor.access_epoch,
                request_reference=request_reference, idempotency_key=idempotency_key,
                occurred_at=occurred_at, target=target,
                command={"effective_at": effective_at, "received_at": received_at,
                         "fields": getattr(command, "fields", None)},
            )
        decision = self._authority.qualify(_authority_request(
            database_alias=self._alias, actor=actor, actor_membership=membership,
            actor_role_head_refs=role_refs, action=action, request_reference=request_reference,
            idempotency_key=idempotency_key, received_at=received_at,
            target_fingerprint=authority_target_fingerprint(authority_target),
            effective_at=effective_at, target=authority_target,
            living_organism_id=organism_uuid, version_uuid=command.version_uuid,
            version_kind="DRAFT" if create_head else "EFFECT",
        ))
        if create_head and gateway is None:
            LOApplicationGateway.objects.using(self._alias).get_or_create(living_organism=organism)
            gateway = LOApplicationGateway.objects.using(self._alias).select_for_update().filter(
                living_organism=organism,
            ).first()
        if gateway is None:
            raise S015ApplicationRefused("S3FU gateway closed")
        with connections[self._alias].cursor() as cursor:
            cursor.execute("SELECT clock_timestamp()")
            gateway_at = cursor.fetchone()[0]
        configuration = _effective_gateway_version(
            self._alias, organism, OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION, gateway_at,
        )
        if configuration is not None and configuration.cq_active is True:
            if configuration.inherit_portal_cq_settings is True:
                raise S015OperationNotYetImplemented("gateway_inherited_configuration", "S3-IL-INHERIT", "PORTAL")
            cq_required = (configuration.require_cq_to_change_application_question if action is OrganismAction.EFFECT_APPLICATION_QUESTION
                           else configuration.require_cq_to_change_lo_settings)
            if cq_required is True:
                raise S015OperationNotYetImplemented("gateway_cq_change", "S3-IL-CQ", "DISCUSSION")
        office = ContextualRoleAssignment.objects.using(self._alias).select_related("role_definition").filter(
            membership=membership, role_definition__code="COORDINATOR",
            role_definition__scope="LIVING_ORGANISM", state="ACTIVE",
        ).first()
        if office is None or not OrganismRoleService(
            authority=self._authority, clock=self._clock, database_alias=self._alias,
        ).assignment_in_effect(office, effective_at):
            raise S015ApplicationRefused("effective Coordinator office is required")
        return (actor, organism, decision, fingerprint, request_reference,
                idempotency_key, occurred_at, effective_at, received_at, gateway_at)

    def save_gateway_draft(self, command: GatewayDraftCommand):
        with _authority_atomic(self._alias):
            (actor, organism, _, _, _, _, occurred_at, effective_at, received_at,
             gateway_at) = self._gateway_start(command, create_head=True)
            model, _ = _VERSION_KINDS[command.action]
            allowed = {
                LOApplicationSettingsVersion: {"applications_open", "automatic_acceptance", "processing_responsibility"},
                LOApplicationQuestionVersion: {"question_text", "answer_format", "options"},
                LOGovernanceConfigurationVersion: {"cq_active", "inherit_portal_cq_settings",
                                                   "require_cq_to_change_lo_settings", "require_cq_to_change_application_question",
                                                   "governance_enabled", "coordinator_nomination_policy", "role_suspension_enabled",
                                                   "suspension_tolls_term"},
            }[model]
            if not isinstance(command.fields, dict) or not command.fields.keys() <= allowed:
                raise S015ApplicationRefused("invalid gateway draft fields")
            if command.version_uuid is not None:
                version = model.objects.using(self._alias).select_for_update().get(
                    version_uuid=command.version_uuid, living_organism=organism,
                )
                if gateway_version_status(self._alias, version, gateway_at) != "DRAFT":
                    raise S015ApplicationRefused("governed gateway version cannot be edited")
                fields = dict(command.fields)
                if (
                    model is LOGovernanceConfigurationVersion
                    and "governance_enabled" not in fields
                    and version.governance_enabled is None
                ):
                    predecessor = _effective_gateway_version(
                        self._alias, organism, command.action, gateway_at,
                    )
                    fields["governance_enabled"] = (
                        True
                        if predecessor is None or predecessor.governance_enabled is None
                        else predecessor.governance_enabled
                    )
                for field, value in fields.items():
                    setattr(version, field, value)
                version.save(using=self._alias, update_fields=list(fields))
            else:
                predecessor = _effective_gateway_version(self._alias, organism, command.action, gateway_at)
                fields = dict(command.fields)
                if model is LOApplicationQuestionVersion and (
                    "question_text" not in fields or "answer_format" not in fields
                ):
                    fields = {**default_application_question(organism, self._name_source), **fields}
                if model is LOGovernanceConfigurationVersion:
                    fields.setdefault(
                        "governance_enabled",
                        True
                        if predecessor is None or predecessor.governance_enabled is None
                        else predecessor.governance_enabled,
                    )
                version = model.objects.using(self._alias).create(
                    living_organism=organism, actor=actor, enterer=actor, supersedes=predecessor,
                    occurred_at=occurred_at, effective_at=effective_at, received_at=received_at,
                    **fields,
                )
            return version

    def effect_gateway_version(self, command: EffectGatewayVersionCommand):
        try:
            return self._effect_gateway_version(command)
        except S015AuthorityDenied as exc:
            if (
                command.action is not OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION
                or exc.refusal_code not in _SBG_ENABLING_REFUSAL_CODES
            ):
                raise
            organism = LivingOrganism.objects.using(self._alias).get(
                organism_id=_canonical_uuid(command.living_organism_id, "living_organism_id"),
            )
            message = _governance_enabling_refusal_message(
                database_alias=self._alias,
                organism=organism,
                effective_at=command.effective_at,
                recorded_at=command.received_at,
                code=exc.refusal_code,
            )
            raise S015ApplicationRefused(
                message,
                refusal_code=exc.refusal_code,
            ) from exc
        except IntegrityError as exc:
            if command.action is not OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION:
                raise
            message_primary = getattr(
                getattr(exc.__cause__, "diag", None),
                "message_primary",
                None,
            )
            refusal_code = next(
                (
                    code for code in _SBG_ENABLING_REFUSAL_CODES
                    if message_primary == code.value
                ),
                None,
            )
            if refusal_code is None:
                raise
            organism = LivingOrganism.objects.using(self._alias).get(
                organism_id=_canonical_uuid(command.living_organism_id, "living_organism_id"),
            )
            message = _governance_enabling_refusal_message(
                database_alias=self._alias,
                organism=organism,
                effective_at=command.effective_at,
                recorded_at=command.received_at,
                code=refusal_code,
            )
            raise S015ApplicationRefused(
                message,
                refusal_code=refusal_code,
            ) from exc

    def _effect_gateway_version(self, command: EffectGatewayVersionCommand):
        with _authority_atomic(self._alias):
            start = self._gateway_start(command, create_head=False)
            if isinstance(start, GatewayEffectCommandResult):
                return start
            (actor, organism, decision, fingerprint, request_reference, idempotency_key,
             occurred_at, effective_at, received_at, gateway_at) = start
            model, prefix = _VERSION_KINDS[command.action]
            version = model.objects.using(self._alias).select_for_update().get(
                version_uuid=_canonical_uuid(command.version_uuid, "version_uuid"), living_organism=organism,
            )
            if gateway_version_status(self._alias, version, gateway_at) != "DRAFT":
                raise S015ApplicationRefused("gateway version already effective")
            if not _gateway_version_complete(version):
                raise S015ApplicationRefused("S3FU gateway effect: version incomplete")
            if isinstance(version, LOGovernanceConfigurationVersion) and version.governance_enabled is None:
                raise S015ApplicationRefused(
                    "S3FU governance configuration effect: GOVERNANCE choice is not confirmed"
                )
            if isinstance(version, LOGovernanceConfigurationVersion) and version.cq_active is True:
                raise S015OperationNotYetImplemented("cq_configuration", "S3-IL-CQ", "DISCUSSION")
            if isinstance(version, LOApplicationSettingsVersion) and version.processing_responsibility == "APPLICATION_CIRCLE":
                raise S015OperationNotYetImplemented("circle_application_processing", "S3-IL-CIRCLE", "DISCUSSION")
            _lock_owning_organism(self._alias, organism.organism_id)
            organism = LivingOrganism.objects.using(self._alias).select_for_update().get(pk=organism.pk)
            predecessor = LivingOrganismEvent.objects.using(self._alias).filter(pk=organism.head_event_id).first()
            if predecessor is None or effective_at < predecessor.effective_at:
                raise S015ApplicationRefused("gateway effect requires a current LO chain")
            with connections[self._alias].cursor() as cursor:
                cursor.execute("SELECT l1_commitment FROM public.core_livingorganismevent WHERE id = %s", [predecessor.pk])
                predecessor_commitment = cursor.fetchone()[0]
                event_uuid = uuid4()
                cursor.execute(
                    "SELECT public.s015_0022_digest('s015p1', public.s015_0022_parts_preimage(%s, %s::uuid))",
                    ["core_livingorganismevent", event_uuid],
                )
                parts_commitment = cursor.fetchone()[0]
                temporal_kind, temporal_reference = _resulting_temporal_basis(
                    payload_fingerprint=fingerprint, effective_at=effective_at, received_at=received_at,
                )
                lineage_reference = "s015l1:" + hashlib.sha256(_COMMAND_DOMAIN + canonical_json_bytes({
                    "event": event_uuid, "organism": organism.organism_id, "fingerprint": fingerprint,
                })).hexdigest()
                event_columns = {
                    "event_uuid": event_uuid, "living_organism_id": organism.pk,
                    "sequence": predecessor.sequence + 1, "predecessor_id": predecessor.pk,
                    "predecessor_sequence": predecessor.sequence, "action": command.action.value,
                    "prior_state": predecessor.resulting_state, "resulting_state": predecessor.resulting_state,
                    "actor_id": None, "actor_state": "LIVING_ORGANISM", "actor_capacity": "STANDING",
                    "actor_access_epoch": None, "enterer_identity_id": actor.pk, "entry_mode": "TRANSPORT",
                    "composing_rule_state": "NA_TRANSPORT", "composing_rule_reference": None,
                    "composer_state": "NA_TRANSPORT", "composer_identity_id": None,
                    "effective_until": None, "effective_until_state": "NONE",
                    "authority_basis_id": organism.founding_authority_basis_id,
                    "authority_decision_reference": decision.authority_decision_reference,
                    "evidence_reference": prefix + str(version.version_uuid),
                    "request_reference": request_reference, "idempotency_key": idempotency_key,
                    "payload_fingerprint": fingerprint, "lineage_reference": lineage_reference,
                    "temporal_basis_kind": temporal_kind, "temporal_basis_reference": temporal_reference,
                    "occurred_at": occurred_at, "effective_at": effective_at, "received_at": received_at,
                    "predecessor_l1_commitment": predecessor_commitment, "parts_commitment": parts_commitment,
                }
                cursor.execute(
                    f"INSERT INTO public.core_livingorganismevent ({', '.join(event_columns)}) "
                    f"VALUES ({', '.join(['%s'] * len(event_columns))}) RETURNING id",
                    list(event_columns.values()),
                )
                event_id = cursor.fetchone()[0]
                cursor.execute("SELECT public.s015_refresh_governed_cache(%s, %s, %s, clock_timestamp())",
                               ["core_livingorganism", organism.pk, effective_at])
            event = LivingOrganismEvent.objects.using(self._alias).get(pk=event_id)
            result = GatewayEffectCommandResult(
                version_uuid=version.version_uuid,
                action=command.action,
                effective_at=effective_at,
                lineage_reference=event.lineage_reference,
                authority_reference=decision.authority_reference,
                authority_decision_reference=decision.authority_decision_reference,
                event_uuid=event.event_uuid,
                event_sequence=event.sequence,
            )
            OrganismCommandReceipt.objects.using(self._alias).create(
                actor=actor,
                action=command.action.value,
                idempotency_key=idempotency_key,
                request_reference=request_reference,
                payload_fingerprint=fingerprint,
                authority_decision_reference=decision.authority_decision_reference,
                lineage_reference=event.lineage_reference,
                result_payload=result.receipt_payload(),
            )
            return serve_replay(
                result,
                viewer=ReplayViewer(actor.pk, actor.identity_id, self._clock()),
                result_type=ReplayResultType(GatewayEffectCommandResult, self._alias),
            )

    def _coordinator_start(
        self, command: ClaimApplicationCommand, action: OrganismAction,
        *, outcome: str | None = None,
    ) -> _CoordinatorContext | ApplicationCommandResult:
        request_reference = _canonical_key(command.request_reference, "request_reference", 128)
        idempotency_key = _canonical_key(command.idempotency_key, "idempotency_key", 120)
        occurred_at = _validate_timestamp(command.occurred_at, "occurred_at")
        effective_at = _validate_timestamp(command.effective_at, "effective_at")
        received_at = _validate_timestamp(command.received_at, "received_at")
        application_uuid = _canonical_uuid(command.application_uuid, "application_uuid")
        actor = _lock_actor(
            database_alias=self._alias, credential=command.credential,
            unavailable=S015ApplicationAuthorityUnavailable,
        )
        acquire_organism_command_mutex(
            database_alias=self._alias, actor_identity_id=actor.identity_id,
            action=action, idempotency_key=idempotency_key,
        )
        try:
            application = Application.objects.using(self._alias).select_related("living_organism").get(
                application_uuid=application_uuid,
            )
        except Application.DoesNotExist as exc:
            raise S015ApplicationNotFound("application not found") from exc
        target = {"action": action.value, "application": application_uuid,
                  "organism": application.living_organism.organism_id}
        authority_target = {
            "action": action.value,
            "application_uuid": application_uuid,
            "organism_uuid": application.living_organism.organism_id,
        }
        fingerprint = _command_payload(
            domain=_COMMAND_DOMAIN, schema="s015-application-v1", action=action,
            actor_identity_id=actor.identity_id, actor_access_epoch=actor.access_epoch,
            request_reference=request_reference, idempotency_key=idempotency_key,
            occurred_at=occurred_at, target=target,
            command={"effective_at": effective_at, "received_at": received_at, "outcome": outcome},
        )
        replay = self._receipt_or_none(
            actor=actor, action=action, request_reference=request_reference,
            idempotency_key=idempotency_key, fingerprint=fingerprint,
            viewer=ReplayViewer(actor.pk, actor.identity_id, self._clock()),
        )
        if replay is not None:
            return replay
        _read_actor_active_membership(
            database_alias=self._alias,
            actor_identity_pk=actor.pk,
            living_organism_pk=application.living_organism_id,
            unavailable=S015ApplicationAuthorityUnavailable,
        )
        if action is OrganismAction.DECIDE_APPLICATION:
            # I-3: take the applicant's FK-compatible KEY SHARE first to preserve lock order.
            # See PKT_LOCK_1_BUILD_COMMISSION_v1_0.md section 2.5.
            with connections[self._alias].cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM public.core_identity WHERE id = %s FOR KEY SHARE", [application.applicant_id],
                )
        _lock_owning_organism(self._alias, application.living_organism.organism_id)
        actor_membership, role_refs = _lock_actor_membership_context(
            database_alias=self._alias, actor_identity_id=actor.identity_id,
            living_organism_id=application.living_organism.organism_id,
            unavailable=S015ApplicationAuthorityUnavailable,
        )
        application = Application.objects.using(self._alias).select_for_update().get(pk=application.pk)
        assignment_uuid = None
        if action is OrganismAction.DECIDE_APPLICATION:
            engagement = ApplicationEngagement.objects.using(self._alias).filter(
                application=application,
                effective_at__lte=effective_at,
                recorded_at__lte=received_at,
            ).order_by("-engagement_ordinal").first()
            if engagement is not None and engagement.route == "LO_CSA":
                try:
                    assignment_uuid = UUID(engagement.engagement_reference)
                except ValueError:
                    assignment_uuid = None
        decision = self._authority.qualify(_authority_request(
            database_alias=self._alias, actor=actor, actor_membership=actor_membership,
            actor_role_head_refs=role_refs, action=action, request_reference=request_reference,
            idempotency_key=idempotency_key, received_at=received_at,
            target_fingerprint=authority_target_fingerprint(authority_target),
            effective_at=effective_at, target=authority_target,
            living_organism_id=application.living_organism.organism_id,
            assignment_uuid=assignment_uuid, outcome=outcome,
        ))
        return _CoordinatorContext(
            actor, actor_membership, application, decision, fingerprint, request_reference,
            idempotency_key, occurred_at, effective_at, received_at,
        )

    def _coordinator_office(self, context: _CoordinatorContext) -> ContextualRoleAssignment:
        office = ContextualRoleAssignment.objects.using(self._alias).select_related("role_definition").filter(
            membership=context.membership, role_definition__code="COORDINATOR",
            role_definition__scope="LIVING_ORGANISM", state="ACTIVE",
        ).first()
        if office is None or not OrganismRoleService(
            authority=self._authority, clock=self._clock, database_alias=self._alias,
        ).assignment_in_effect(office, context.effective_at):
            raise S015ApplicationRefused("effective Coordinator office is required")
        return office

    def _csa_route(self, application: Application) -> None:
        if application.engagements.using(self._alias).filter(route__in=("LO_CQ", "CIRCLE_CQ")).exists():
            raise S015OperationNotYetImplemented("csa_application", "S3-IL-CQ", "DISCUSSION")
        if application.engagements.using(self._alias).filter(route="CIRCLE_CSA").exists():
            raise S015OperationNotYetImplemented("circle_application", "S3-IL-CIRCLE", "DISCUSSION")

    def _open_for_coordinator(self, context: _CoordinatorContext) -> None:
        self._csa_route(context.application)
        if ApplicationWithdrawal.objects.using(self._alias).filter(application=context.application).exists():
            raise S015ApplicationRefused("application already withdrawn")
        if ApplicationDecision.objects.using(self._alias).filter(application=context.application).exists():
            raise S015ApplicationRefused("application already decided")

    def _current_claim(self, context: _CoordinatorContext) -> CSAAssignment:
        # Runs under the application row lock taken in _coordinator_start: every committed claim or ending visible
        # here was admitted before this command's row, whose recorded_at 0026 sets after the same lock. So no
        # recorded_at bound applies, and no service clock enters (G-2-CLOCK is the declared database-clock limit).
        claims = CSAAssignment.objects.using(self._alias).filter(
            application=context.application, effective_at__lte=context.effective_at,
        ).order_by("-effective_at", "-recorded_at", "-pk")
        for claim in claims:
            if not CSAAssignmentEnd.objects.using(self._alias).filter(
                assignment=claim, effective_at__lte=context.effective_at,
            ).exists():
                return claim
        raise S015ApplicationRefused("no current Coordinator claim")

    def _finish_coordinator(
        self, context: _CoordinatorContext, action: OrganismAction, record_uuid: UUID,
        recorded_at: datetime, *, completion_due_at: datetime | None = None,
    ) -> ApplicationCommandResult:
        return self._finish(
            actor=context.actor, action=action, request_reference=context.request_reference,
            idempotency_key=context.idempotency_key, fingerprint=context.fingerprint,
            application=context.application, recorded_at=recorded_at,
            authority_reference=context.decision.authority_reference,
            authority_decision_reference=context.decision.authority_decision_reference,
            record_uuid=record_uuid, completion_due_at=completion_due_at,
            completion_due_source=_DUE_SOURCE if completion_due_at is not None else None,
        )

    def submit_application(self, command: SubmitApplicationCommand) -> ApplicationCommandResult:
        with _authority_atomic(self._alias):
            request_reference = _canonical_key(command.request_reference, "request_reference", 128)
            idempotency_key = _canonical_key(command.idempotency_key, "idempotency_key", 120)
            occurred_at = _validate_timestamp(command.occurred_at, "occurred_at")
            effective_at = _validate_timestamp(command.effective_at, "effective_at")
            received_at = _validate_timestamp(command.received_at, "received_at")
            organism_uuid = _canonical_uuid(command.living_organism_id, "living_organism_id")
            predecessor_uuid = None if command.predecessor_reference is None else _canonical_uuid(
                command.predecessor_reference, "predecessor_reference",
            )
            actor = _lock_actor(
                database_alias=self._alias, credential=command.credential,
                unavailable=S015ApplicationAuthorityUnavailable,
            )
            acquire_organism_command_mutex(
                database_alias=self._alias, actor_identity_id=actor.identity_id,
                action=OrganismAction.SUBMIT_APPLICATION, idempotency_key=idempotency_key,
            )
            try:
                organism = LivingOrganism.objects.using(self._alias).get(organism_id=organism_uuid)
            except LivingOrganism.DoesNotExist as exc:
                raise S015ApplicationNotFound("living organism not found") from exc
            target = {"action": OrganismAction.SUBMIT_APPLICATION.value, "organism": organism_uuid,
                      "applicant": actor.identity_id}
            fingerprint = _command_payload(
                domain=_COMMAND_DOMAIN, schema="s015-application-v1", action=OrganismAction.SUBMIT_APPLICATION,
                actor_identity_id=actor.identity_id, actor_access_epoch=actor.access_epoch,
                request_reference=request_reference, idempotency_key=idempotency_key, occurred_at=occurred_at,
                target=target, command={"effective_at": effective_at, "received_at": received_at,
                                        "predecessor": predecessor_uuid, "answer": command.answer},
            )
            replay = self._receipt_or_none(
                actor=actor, action=OrganismAction.SUBMIT_APPLICATION, request_reference=request_reference,
                idempotency_key=idempotency_key, fingerprint=fingerprint,
                viewer=ReplayViewer(actor.pk, actor.identity_id, self._clock()),
            )
            if replay is not None:
                return replay
            care = None
            if self._care_executor is not None:
                candidate = self._care_executor.identity_for(organism)
                care = Identity.objects.using(self._alias).select_for_update(no_key=True).get(pk=candidate.pk)
                if care.pk == actor.pk or care.access_state != Identity.AccessState.ACTIVE:
                    raise S015ApplicationAuthorityUnavailable("distinct active CARE executor identity required")
            _lock_owning_organism(self._alias, organism.organism_id)
            self._pair_lock(actor, organism)
            with connections[self._alias].cursor() as cursor:
                cursor.execute(
                    "SELECT id FROM public.core_loapplicationgateway WHERE living_organism_id = %s FOR SHARE",
                    [organism.pk],
                )
                gateway_exists = cursor.fetchone() is not None
            decision = self._authorise(
                actor=actor, action=OrganismAction.SUBMIT_APPLICATION, organism=organism, application=None,
                request_reference=request_reference, idempotency_key=idempotency_key,
                received_at=received_at,
                effective_at=effective_at, predecessor_reference=predecessor_uuid,
            )
            membership = OrganismMembership.objects.using(self._alias).filter(
                identity=actor, living_organism=organism,
            ).first()
            if membership is not None:
                if membership.state in {"ACTIVE", "PROBATIONARY"}:
                    raise S015ApplicationRefused("applicant is already a current member")
                if membership.state == "ENDED":
                    raise S015OperationNotYetImplemented("rejoin_by_application", "IL-1", "S4")
            if not gateway_exists:
                raise S015ApplicationRefused("S3FU gateway closed")
            with connections[self._alias].cursor() as cursor:
                cursor.execute("SELECT clock_timestamp()")
                gateway_at = cursor.fetchone()[0]
            settings = _effective_gateway_version(
                self._alias, organism, OrganismAction.EFFECT_APPLICATION_SETTINGS, gateway_at,
            )
            question = _effective_gateway_version(
                self._alias, organism, OrganismAction.EFFECT_APPLICATION_QUESTION, gateway_at,
            )
            if settings is None or question is None or settings.applications_open is not True:
                raise S015ApplicationRefused("S3FU gateway closed")
            with connections[self._alias].cursor() as cursor:
                cursor.execute(
                    "SELECT public.s015_fold_effective_state(%s, %s, %s, %s)",
                    ["core_livingorganism", organism.pk, effective_at, gateway_at],
                )
                if cursor.fetchone()[0] != "ACTIVE":
                    raise S015ApplicationRefused("S3FU gateway closed")
            _validate_gateway_answer(question, command.answer)
            if settings.automatic_acceptance is True and care is None:
                raise S015OperationNotYetImplemented("automatic_acceptance", "S3-IL-CARE-EXEC", "CARE")
            open_application = Application.objects.using(self._alias).filter(
                applicant=actor, living_organism=organism, decision__isnull=True, withdrawal__isnull=True,
            ).exists()
            if open_application:
                raise S015ApplicationRefused("an application is already open")
            predecessor = None
            if predecessor_uuid is not None:
                predecessor = Application.objects.using(self._alias).filter(
                    application_uuid=predecessor_uuid, applicant=actor, living_organism=organism,
                ).first()
                if predecessor is None:
                    raise S015ApplicationRefused("predecessor is not an eligible withdrawn application")
            application = Application.objects.using(self._alias).create(
                applicant=actor, living_organism=organism, predecessor_reference=predecessor,
                settings_version=settings, question_version=question, answer=command.answer,
                actor=actor, enterer=actor, occurred_at=occurred_at, effective_at=effective_at,
                received_at=received_at, authority_decision_reference=decision.authority_decision_reference,
                authority_reference=decision.authority_reference, actor_access_epoch=actor.access_epoch,
            )
            application.refresh_from_db(using=self._alias)
            if predecessor is not None and not ApplicationWithdrawal.objects.using(self._alias).filter(
                application=predecessor, effective_at__lte=effective_at,
                recorded_at__lte=application.recorded_at,
            ).exists():
                raise S015ApplicationRefused("predecessor is not an eligible withdrawn application")
            if not applicant_eligible(actor, effective_at, application.recorded_at, database_alias=self._alias):
                raise S015ApplicationRefused("applicant account is not eligible at record time")
            ApplicationLineage.objects.using(self._alias).create(application=application)
            automatic = None
            if settings.automatic_acceptance is True:
                automatic = self._automatic_accept(application, care, request_reference, idempotency_key, fingerprint)
            _append_application_event(
                database_alias=self._alias, application=application,
                action=OrganismAction.SUBMIT_APPLICATION, resulting_state="SUBMITTED",
                actor=actor, enterer=actor, actor_access_epoch=actor.access_epoch,
                authority_decision_reference=decision.authority_decision_reference,
                evidence_reference=f"application:{application.application_uuid}",
                request_reference=request_reference, idempotency_key=idempotency_key,
                payload_fingerprint=fingerprint, occurred_at=occurred_at,
                effective_at=effective_at, received_at=received_at,
            )
            if automatic is not None:
                accepted, accepted_fingerprint, accepted_reference, rule, automatic_key, automatic_request = automatic
                _append_application_event(
                    database_alias=self._alias, application=application, action=OrganismAction.DECIDE_APPLICATION,
                    resulting_state="ACCEPTED", actor=None, enterer=care, actor_access_epoch=None,
                    authority_decision_reference=accepted_reference,
                    evidence_reference=f"application-decision:{accepted.decision_uuid}",
                    request_reference=automatic_request, idempotency_key=automatic_key,
                    payload_fingerprint=accepted_fingerprint, occurred_at=application.occurred_at,
                    effective_at=application.effective_at, received_at=application.received_at,
                    composing_rule_reference=rule,
                )
                self._welcome.send_for(application, accepted)
            return self._finish(
                actor=actor, action=OrganismAction.SUBMIT_APPLICATION, request_reference=request_reference,
                idempotency_key=idempotency_key, fingerprint=fingerprint, application=application,
                recorded_at=application.recorded_at, authority_reference=decision.authority_reference,
                authority_decision_reference=decision.authority_decision_reference,
            )

    def withdraw_application(self, command: WithdrawApplicationCommand) -> ApplicationCommandResult:
        with _authority_atomic(self._alias):
            request_reference = _canonical_key(command.request_reference, "request_reference", 128)
            idempotency_key = _canonical_key(command.idempotency_key, "idempotency_key", 120)
            occurred_at = _validate_timestamp(command.occurred_at, "occurred_at")
            effective_at = _validate_timestamp(command.effective_at, "effective_at")
            received_at = _validate_timestamp(command.received_at, "received_at")
            application_uuid = _canonical_uuid(command.application_uuid, "application_uuid")
            actor = _lock_actor(
                database_alias=self._alias, credential=command.credential,
                unavailable=S015ApplicationAuthorityUnavailable,
            )
            acquire_organism_command_mutex(
                database_alias=self._alias, actor_identity_id=actor.identity_id,
                action=OrganismAction.WITHDRAW_APPLICATION, idempotency_key=idempotency_key,
            )
            try:
                application = Application.objects.using(self._alias).select_related("living_organism").get(
                    application_uuid=application_uuid,
                )
            except Application.DoesNotExist as exc:
                raise S015ApplicationNotFound("application not found") from exc
            target = {"action": OrganismAction.WITHDRAW_APPLICATION.value,
                      "application": application_uuid, "organism": application.living_organism.organism_id}
            fingerprint = _command_payload(
                domain=_COMMAND_DOMAIN, schema="s015-application-v1", action=OrganismAction.WITHDRAW_APPLICATION,
                actor_identity_id=actor.identity_id, actor_access_epoch=actor.access_epoch,
                request_reference=request_reference, idempotency_key=idempotency_key, occurred_at=occurred_at,
                target=target, command={"effective_at": effective_at, "received_at": received_at},
            )
            replay = self._receipt_or_none(
                actor=actor, action=OrganismAction.WITHDRAW_APPLICATION, request_reference=request_reference,
                idempotency_key=idempotency_key, fingerprint=fingerprint,
                viewer=ReplayViewer(actor.pk, actor.identity_id, self._clock()),
            )
            if replay is not None:
                return replay
            _lock_owning_organism(self._alias, application.living_organism.organism_id)
            application = Application.objects.using(self._alias).select_for_update().select_related(
                "living_organism",
            ).get(pk=application.pk)
            decision = self._authorise(
                actor=actor, action=OrganismAction.WITHDRAW_APPLICATION,
                organism=application.living_organism, application=application,
                request_reference=request_reference, idempotency_key=idempotency_key,
                received_at=received_at, effective_at=effective_at,
            )
            if ApplicationDecision.objects.using(self._alias).filter(application=application).exists():
                raise S015ApplicationRefused("application already decided")
            if ApplicationWithdrawal.objects.using(self._alias).filter(application=application).exists():
                raise S015ApplicationRefused("application already withdrawn")
            if application.engagements.using(self._alias).filter(route__in=["LO_CQ", "CIRCLE_CQ"]).exists():
                raise S015OperationNotYetImplemented("withdraw_cq_application", "S3-IL-CQ", "DISCUSSION")
            withdrawal = ApplicationWithdrawal.objects.using(self._alias).create(
                application=application, actor=actor, enterer=actor,
                occurred_at=occurred_at, effective_at=effective_at, received_at=received_at,
                authority_decision_reference=decision.authority_decision_reference,
                authority_reference=decision.authority_reference, actor_access_epoch=actor.access_epoch,
            )
            withdrawal.refresh_from_db(using=self._alias)
            _append_application_event(
                database_alias=self._alias, application=application,
                action=OrganismAction.WITHDRAW_APPLICATION, resulting_state="WITHDRAWN",
                actor=actor, enterer=actor, actor_access_epoch=actor.access_epoch,
                authority_decision_reference=decision.authority_decision_reference,
                evidence_reference=f"application-withdrawal:{application.application_uuid}",
                request_reference=request_reference, idempotency_key=idempotency_key,
                payload_fingerprint=fingerprint, occurred_at=occurred_at,
                effective_at=effective_at, received_at=received_at,
            )
            return self._finish(
                actor=actor, action=OrganismAction.WITHDRAW_APPLICATION, request_reference=request_reference,
                idempotency_key=idempotency_key, fingerprint=fingerprint, application=application,
                recorded_at=withdrawal.recorded_at, authority_reference=decision.authority_reference,
                authority_decision_reference=decision.authority_decision_reference,
            )

    def claim_application(self, command: ClaimApplicationCommand) -> ApplicationCommandResult:
        with _authority_atomic(self._alias):
            context = self._coordinator_start(command, OrganismAction.CLAIM_APPLICATION)
            if isinstance(context, ApplicationCommandResult):
                return context
            self._open_for_coordinator(context)
            if CSAAssignment.objects.using(self._alias).filter(application=context.application).exists():
                raise S015ApplicationRefused("application already has a claim")
            office = self._coordinator_office(context)
            due_at = context.effective_at + timedelta(days=27)
            claim = CSAAssignment.objects.using(self._alias).create(
                application=context.application, assignee_membership=context.membership,
                assignee_office=office, actor=context.actor, enterer=context.actor,
                completion_due_at=due_at, occurred_at=context.occurred_at,
                effective_at=context.effective_at, received_at=context.received_at,
            )
            claim.refresh_from_db(using=self._alias)
            settings_basis = _engagement_settings_basis(
                self._alias, context.application.living_organism, context.effective_at,
            )
            ApplicationEngagement.objects.using(self._alias).create(
                application=context.application, engagement_ordinal=1, route="LO_CSA",
                deciding_party_id=context.application.living_organism_id,
                engagement_reference=str(claim.assignment_uuid), settings_basis=settings_basis,
                actor=context.actor, enterer=context.actor, occurred_at=context.occurred_at,
                effective_at=context.effective_at, received_at=context.received_at,
            )
            _append_application_event(
                database_alias=self._alias, application=context.application,
                action=OrganismAction.CLAIM_APPLICATION, resulting_state="IN_REVIEW",
                actor=context.actor, enterer=context.actor, actor_access_epoch=context.actor.access_epoch,
                authority_decision_reference=context.decision.authority_decision_reference,
                evidence_reference=f"csa-assignment:{claim.assignment_uuid}",
                request_reference=context.request_reference, idempotency_key=context.idempotency_key,
                payload_fingerprint=context.fingerprint, occurred_at=context.occurred_at,
                effective_at=context.effective_at, received_at=context.received_at,
            )
            return self._finish_coordinator(
                context, OrganismAction.CLAIM_APPLICATION, claim.assignment_uuid, claim.recorded_at,
                completion_due_at=due_at,
            )

    def reassign_application(self, command: ReassignApplicationCommand) -> ApplicationCommandResult:
        with _authority_atomic(self._alias):
            context = self._coordinator_start(command, OrganismAction.REASSIGN_APPLICATION)
            if isinstance(context, ApplicationCommandResult):
                return context
            self._open_for_coordinator(context)
            previous = self._current_claim(context)
            engagement = ApplicationEngagement.objects.using(self._alias).filter(
                application=context.application,
            ).order_by("-engagement_ordinal").first()
            if engagement is None or engagement.engagement_reference != str(previous.assignment_uuid):
                raise S015ApplicationRefused("current engagement does not match claim")
            office = self._coordinator_office(context)
            settings_basis = _engagement_settings_basis(
                self._alias, context.application.living_organism, context.effective_at,
            )
            ending = CSAAssignmentEnd.objects.using(self._alias).create(
                assignment=previous, actor=context.actor, enterer=context.actor,
                occurred_at=context.occurred_at, effective_at=context.effective_at,
                received_at=context.received_at,
            )
            ending.refresh_from_db(using=self._alias)
            due_at = context.effective_at + timedelta(days=27)
            replacement = CSAAssignment.objects.using(self._alias).create(
                application=context.application, replaces=previous,
                assignee_membership=context.membership, assignee_office=office,
                actor=context.actor, enterer=context.actor, completion_due_at=due_at,
                occurred_at=context.occurred_at, effective_at=context.effective_at,
                received_at=context.received_at,
            )
            replacement.refresh_from_db(using=self._alias)
            ApplicationEngagement.objects.using(self._alias).create(
                application=context.application, engagement_ordinal=engagement.engagement_ordinal + 1,
                route="LO_CSA", deciding_party_id=context.application.living_organism_id,
                engagement_reference=str(replacement.assignment_uuid), settings_basis=settings_basis,
                basis_reference=str(ending.ending_uuid), actor=context.actor, enterer=context.actor,
                occurred_at=context.occurred_at, effective_at=context.effective_at,
                received_at=context.received_at,
            )
            _append_application_event(
                database_alias=self._alias, application=context.application,
                action=OrganismAction.REASSIGN_APPLICATION, resulting_state="IN_REVIEW",
                actor=context.actor, enterer=context.actor, actor_access_epoch=context.actor.access_epoch,
                authority_decision_reference=context.decision.authority_decision_reference,
                evidence_reference=f"csa-assignment:{replacement.assignment_uuid}",
                request_reference=context.request_reference, idempotency_key=context.idempotency_key,
                payload_fingerprint=context.fingerprint, occurred_at=context.occurred_at,
                effective_at=context.effective_at, received_at=context.received_at,
            )
            return self._finish_coordinator(
                context, OrganismAction.REASSIGN_APPLICATION, replacement.assignment_uuid,
                replacement.recorded_at, completion_due_at=due_at,
            )

    def decide_application(self, command: DecideApplicationCommand) -> ApplicationCommandResult:
        with _authority_atomic(self._alias):
            outcome = _canonical_key(command.outcome, "outcome", 7)
            if outcome not in {"ACCEPT", "DECLINE"}:
                raise ValueError("outcome must be ACCEPT or DECLINE")
            context = self._coordinator_start(command, OrganismAction.DECIDE_APPLICATION, outcome=outcome)
            if isinstance(context, ApplicationCommandResult):
                return context
            self._open_for_coordinator(context)
            claim = self._current_claim(context)
            if claim.actor_id != context.actor.pk or claim.assignee_membership_id != context.membership.pk:
                raise S015ApplicationRefused("only the current self-assignee may decide")
            office = self._coordinator_office(context)
            if claim.assignee_office_id != office.pk:
                raise S015ApplicationRefused("claim's qualifying office is not effective")
            engagement = ApplicationEngagement.objects.using(self._alias).filter(
                application=context.application, effective_at__lte=context.effective_at,
            ).order_by("-engagement_ordinal").first()
            if engagement is None or engagement.route != "LO_CSA" or engagement.engagement_reference != str(claim.assignment_uuid):
                raise S015ApplicationRefused("no current CSA engagement")
            if outcome == "ACCEPT":
                with connections[self._alias].cursor() as cursor:
                    cursor.execute(
                        "SELECT public.s015_fold_effective_state(%s, %s, %s, clock_timestamp())",
                        ["core_livingorganism", context.application.living_organism_id, context.effective_at],
                    )
                    if cursor.fetchone()[0] == "RESTRICTED_CONTINUITY":
                        raise S015OperationNotYetImplemented("restricted_continuity_admission", "S3-IL-RC", "S4")
            decision = ApplicationDecision.objects.using(self._alias).create(
                application=context.application, route="LO_CSA",
                deciding_party_id=context.application.living_organism_id, outcome=outcome,
                assignment_reference=claim, responsible_coordinator=context.actor,
                responsible_capacity="COORDINATOR", qualifying_office=office,
                authority_decision_reference=context.decision.authority_decision_reference,
                authority_reference=context.decision.authority_reference,
                executor=context.actor, enterer=context.actor, occurred_at=context.occurred_at,
                effective_at=context.effective_at, received_at=context.received_at,
            )
            decision.refresh_from_db(using=self._alias)
            if outcome == "ACCEPT":
                self._admit(_AdmissionDetails(
                    context.application, context.actor, context.decision.authority_decision_reference,
                    context.request_reference, context.idempotency_key, context.fingerprint,
                    context.occurred_at, context.effective_at, context.received_at,
                ), decision)
            _append_application_event(
                database_alias=self._alias, application=context.application,
                action=OrganismAction.DECIDE_APPLICATION,
                resulting_state="ACCEPTED" if outcome == "ACCEPT" else "DECLINED",
                actor=None, enterer=context.actor, actor_access_epoch=None,
                authority_decision_reference=context.decision.authority_decision_reference,
                evidence_reference=f"application-decision:{decision.decision_uuid}",
                request_reference=context.request_reference, idempotency_key=context.idempotency_key,
                payload_fingerprint=context.fingerprint, occurred_at=context.occurred_at,
                effective_at=context.effective_at, received_at=context.received_at,
            )
            return self._finish_coordinator(
                context, OrganismAction.DECIDE_APPLICATION, decision.decision_uuid, decision.recorded_at,
            )

    def _admit(self, context: _AdmissionDetails, decision: ApplicationDecision) -> None:
        application = context.application
        applicant = Identity.objects.using(self._alias).get(pk=application.applicant_id)
        membership = OrganismMembership.objects.using(self._alias).filter(
            identity=applicant, living_organism_id=application.living_organism_id,
        ).first()
        if membership is not None and membership.state == "ENDED":
            raise S015OperationNotYetImplemented("rejoin_by_application", "IL-1", "S4")
        if membership is not None and membership.state != "PROPOSED":
            raise S015ApplicationRefused("applicant already has a current membership")
        basis_id = application.living_organism.founding_authority_basis_id
        if basis_id is None:
            raise S015ApplicationAuthorityUnavailable("LO founding authority basis is required")
        if membership is None:
            membership_uuid = uuid4()
            membership = OrganismMembership.objects.using(self._alias).create(
                membership_uuid=membership_uuid, identity=applicant,
                living_organism_id=application.living_organism_id, state="PROPOSED",
                current_state_effective_at=application.effective_at, state_known_at=application.received_at,
                state_source_event_set_fingerprint=_empty_anchor_fingerprint(
                    database_alias=self._alias, anchor_token="core_organismmembership",
                    anchor_uuid=membership_uuid,
                ),
            )
            _insert_admission_transition(
                database_alias=self._alias, membership=membership, predecessor=None, actor=applicant,
                enterer=context.enterer, basis_id=basis_id, prior_state=None, resulting_state="PROPOSED",
                reason_class="APPLICATION", evidence_reference=f"application:{application.application_uuid}",
                authority_decision_reference=application.authority_decision_reference,
                request_reference=context.request_reference, idempotency_key=context.idempotency_key,
                payload_fingerprint=context.fingerprint, occurred_at=application.occurred_at,
                effective_at=application.effective_at, received_at=application.received_at,
                actor_access_epoch=application.actor_access_epoch,
            )
            self._refresh_membership(membership, application.effective_at)
        predecessor = OrganismMembershipTransition.objects.using(self._alias).get(
            pk=membership.head_transition_id,
        )
        if (context.effective_at, context.occurred_at, context.received_at) < (
            predecessor.effective_at, predecessor.occurred_at, predecessor.received_at,
        ):
            raise S015OperationNotYetImplemented("out_of_order_admission", "IL-3", "S5")
        transition_id = _insert_admission_transition(
            database_alias=self._alias, membership=membership, predecessor=predecessor,
            actor=None, enterer=context.enterer, basis_id=basis_id,
            prior_state="PROPOSED", resulting_state="ACTIVE", reason_class="ADMISSION",
            evidence_reference=f"application-decision:{decision.decision_uuid}",
            authority_decision_reference=context.authority_decision_reference,
            request_reference=context.request_reference, idempotency_key=context.idempotency_key,
            payload_fingerprint=context.fingerprint, occurred_at=context.occurred_at,
            effective_at=context.effective_at, received_at=context.received_at,
            composing_rule_reference=context.composing_rule_reference,
        )
        ApplicationDecisionEffect.objects.using(self._alias).create(
            decision=decision, transition_id=transition_id,
            occurred_at=context.occurred_at, effective_at=context.effective_at,
            received_at=context.received_at,
        )
        self._refresh_membership(membership, context.effective_at)

    def _refresh_membership(self, membership: OrganismMembership, effective_at: datetime) -> None:
        with connections[self._alias].cursor() as cursor:
            cursor.execute(
                "SELECT public.s015_refresh_governed_cache(%s, %s, %s, clock_timestamp())",
                ["core_organismmembership", membership.pk, effective_at],
            )
        membership.refresh_from_db(using=self._alias)

    def application_status(self, application: Application, state_at: datetime, known_at: datetime) -> str:
        state_at = _validate_timestamp(state_at, "state_at")
        known_at = _validate_timestamp(known_at, "known_at")
        if not Application.objects.using(self._alias).filter(
            pk=application.pk, effective_at__lte=state_at, recorded_at__lte=known_at,
        ).exists():
            return "NOT_PRESENT"
        decisions = ApplicationDecision.objects.using(self._alias).filter(application=application)
        withdrawals = ApplicationWithdrawal.objects.using(self._alias).filter(application=application)
        if decisions.exists() and withdrawals.exists():
            raise S015ApplicationRefused("application terminal integrity failure")
        if decisions.filter(effective_at__lte=state_at, recorded_at__lte=known_at).exists():
            return "DECIDED"
        if withdrawals.filter(effective_at__lte=state_at, recorded_at__lte=known_at).exists():
            return "WITHDRAWN"
        engagement = ApplicationEngagement.objects.using(self._alias).filter(
            application=application, effective_at__lte=state_at, recorded_at__lte=known_at,
        ).order_by("-engagement_ordinal").first()
        if engagement is None:
            return "WAITING"
        if engagement.route != "LO_CSA":
            limit = "S3-IL-CQ" if engagement.route.endswith("CQ") else "S3-IL-CIRCLE"
            raise S015OperationNotYetImplemented("application_status_route", limit, "DISCUSSION")
        with connections[self._alias].cursor() as cursor:
            cursor.execute(
                "SELECT public.s015_fold_effective_state(%s, %s, %s, %s)",
                ["core_livingorganism", application.living_organism_id, state_at, known_at],
            )
            if cursor.fetchone()[0] == "CLOSED":
                return "UNABLE"
        claim = CSAAssignment.objects.using(self._alias).filter(
            application=application, assignment_uuid=engagement.engagement_reference,
            effective_at__lte=state_at, recorded_at__lte=known_at,
        ).first()
        if claim is None or CSAAssignmentEnd.objects.using(self._alias).filter(
            assignment=claim, effective_at__lte=state_at, recorded_at__lte=known_at,
        ).exists():
            return "UNABLE"
        return "PENDING"