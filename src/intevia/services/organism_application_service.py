"""Application commands for living organisms (PKT-B S3).

OrganismApplicationService provides applicant submission and withdrawal,
Coordinator claim, reassignment and decision commands, gateway commands
to draft and put application settings, questions and governance
configuration into effect, and the application_status read.

Every public command first admits the request through the shared intake
(a committed record of the request's key, content fingerprint and
server-assigned received_at, outside the command's transaction), then
executes it inside authority_atomic, directly or through the gateway
effect implementation. The executors lock the actor before looking up a
receipt by actor, action and idempotency key. A receipt must also match
the request reference and payload fingerprint before its result is
served as a replay. These paths qualify fresh requests through
S015Authority, as of the intake's received_at, before their governed
writes. save_gateway_draft also qualifies authority and writes no
command receipt. A first creation or edit returns the saved version; a
retry of a completed draft request returns a GatewayDraftResult, located
or unresolved, found through its authority decision.

Also defines command and result carriers, receipt decoders, gateway and
Coordinator helpers, exceptions, and the LO name, CARE executor and
discussion welcome collaborator interfaces.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Callable, Protocol
from uuid import UUID, uuid4

from django.contrib.auth.models import User
from django.db import IntegrityError, connections

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
    _intake_tail,
    acquire_organism_command_mutex,
    # Shared helper keeps the 15 Oct T-LOCK-1 translation outside the transaction.
    authority_atomic as _authority_atomic,
    serve_replay,
    WithheldField,
)
from src.intevia.services.organism_membership_service import S015OperationNotYetImplemented
from src.intevia.services.organism_role_service import OrganismRoleService
from src.intevia.services.organism_formation_service import _sbg_readiness_message

if TYPE_CHECKING:
    from core.models import IntakeEntry
    from src.intevia.services.command_intake import BoundRequest, IntakeShape


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
_AUTOMATIC_ACCEPTANCE_CARE_REFUSAL = (
    "These settings were not put into effect: automatic acceptance needs a CARE executor, "
    "which is not configured (S3-IL-CARE-EXEC). Existing settings are unchanged and no governed "
    "record was written. INTEVIA kept a record that this request was received at {received_at}; "
    "sent again unchanged, it is answered as of that time. You can still choose manual processing "
    "by the LO's Coordinators."
)
_AUTOMATIC_ACCEPTANCE_WELCOME_REFUSAL = (
    "These settings were not put into effect: automatic acceptance needs the welcome for new "
    "members, which is not yet available (S3-IL-NOTICE). Existing settings are unchanged and no "
    "governed record was written. INTEVIA kept a record that this request was received at "
    "{received_at}; sent again unchanged, it is answered as of that time. You can still choose "
    "manual processing by the LO's Coordinators."
)


def _governance_enabling_refusal_message(
    *,
    database_alias: str,
    organism: LivingOrganism,
    effective_at: datetime,
    recorded_at: datetime,
    code: S015AuthorityRefusalCode,
    opening_end: str = " and nothing changed.",
    tail: str = "",
) -> str:
    """Return the authored message for a refused GOVERNANCE enablement.

    SBG_IL_GRESD selects the fixed message describing a previously active,
    now dormant GOVERNANCE Circle, its unavailable restoration route and
    continued Coordinator governance. This helper does not establish those
    conditions itself.

    Other codes are passed to _sbg_readiness_message with act set to
    "GOVERNANCE was not switched on" and with the supplied LO, database alias
    and temporal coordinates. The incomplete-arrangement, unsupported-field
    and unsupported-eligibility branches read arrangement diagnostics; the
    field and eligibility messages use their own opening. Diagnostic errors
    propagate. Raises RuntimeError if the delegated helper returns None.

    opening_end and tail are passed to _sbg_readiness_message and applied in
    the same way to the SBG_IL_GRESD message. effect_gateway_version passes
    the class B form on its IntegrityError arm and the class A form on its
    S015AuthorityDenied arm, each with the bound request's received_at.
    """
    if code is S015AuthorityRefusalCode.SBG_IL_GRESD:
        message = (
            f"GOVERNANCE was not switched on{opening_end} This LO's GOVERNANCE Circle "
            "was active before and is now dormant; its next step is the restoration route for "
            "a dormant GOVERNANCE, which is not yet available in this release. This is a limit "
            "of the current build, not a refusal of the LO's choice. The LO's Coordinators "
            "continue to govern until that route is delivered."
        )
        return f"{message} {tail}" if tail else message
    message = _sbg_readiness_message(
        code=code,
        act="GOVERNANCE was not switched on",
        database_alias=database_alias,
        organism=organism,
        effective_at=effective_at,
        recorded_at=recorded_at,
        opening_end=opening_end,
        tail=tail,
    )
    if message is None:
        raise RuntimeError("SB-G enabling refusal has no authored message")
    return message


class S015ApplicationError(Exception):
    """Base class for the application service's errors.

    The receipt decoders raise this class directly when their initial
    dictionary check fails. Errors from missing fields or invalid field
    conversions are not translated to this class.
    """
    pass


class S015ApplicationAuthorityUnavailable(S015ApplicationError):
    """Raised when a party the command needs cannot act.

    Covers: the actor's Identity missing or not active, or its credential
    not active; the actor without the active membership the command
    requires; at submission, a supplied CARE executor whose Identity is not
    active or is the applicant's own; and, when an application is admitted,
    the LO's founding authority basis missing.
    """
    pass


class S015ApplicationRefused(S015ApplicationError):
    """Application-service refusal carrying a message for its caller.

    Used for rejected requests and for the status read's terminal-integrity
    check. refusal_code defaults to None. For recognised GOVERNANCE
    enablement refusals, effect_gateway_version supplies the code from
    either S015AuthorityDenied or a matching IntegrityError diagnostic.
    """
    def __init__(
        self,
        message: str,
        *,
        refusal_code: S015AuthorityRefusalCode | None = None,
    ) -> None:
        """Keep message as the exception text and refusal_code (an
        S015AuthorityRefusalCode or None) as an attribute.
        """
        super().__init__(message)
        self.refusal_code = refusal_code


class S015ApplicationNotFound(S015ApplicationError):
    """Raised when the application a command names does not exist, or, at
    submission, the living organism it names does not exist.
    """
    pass


class LONameSource(Protocol):
    """Protocol for the source of an LO's name, used to word the default
    application question.
    """
    def name_for(self, living_organism: LivingOrganism) -> str:
        """Return the name of living_organism."""
        ...


class FormationLONameSource:
    """LONameSource that reads the name recorded at founding: version 1 of
    the LO's founding content.
    """
    def name_for(self, living_organism: LivingOrganism) -> str:
        """Return the name in living_organism's founding content version 1.

        Reads on the organism's own database alias, falling back to "default".
        Raises S015OperationNotYetImplemented with limit S3-IL-LONAME when the
        lookup returns no name.
        """
        database_alias = living_organism._state.db or "default"
        name = LOFoundingContentVersion.objects.using(database_alias).filter(
            living_organism_id=living_organism.pk,
            version=1,
        ).values_list("name", flat=True).first()
        if name is None:
            raise S015OperationNotYetImplemented("lo_name", "S3-IL-LONAME", "FORMATION")
        return name


class CareExecutor(Protocol):
    """Protocol supplying the Identity that executes CARE actions for an LO.

    On a fresh submission, after the receipt-replay check, a supplied
    executor's Identity is locked and required to be active and distinct
    from the applicant. This check precedes the automatic-acceptance branch
    and also applies when automatic acceptance is off. A matching replay
    returns without consulting the executor.

    If effective settings require automatic acceptance and no executor was
    supplied, submission raises S015OperationNotYetImplemented with limit
    S3-IL-CARE-EXEC. Automatic acceptance records the LO as deciding party
    and the CARE Identity as the decision's executor and enterer.
    """
    def identity_for(self, living_organism: LivingOrganism) -> Identity:
        """Return the Identity that executes CARE actions for living_organism.
        """
        ...


class DiscussionWelcome(Protocol):
    """Protocol for the welcome following an automatic acceptance.

    Submission calls send_for synchronously inside its authority_atomic
    context, after automatic admission and the submission and acceptance
    lineage events, and before its final command receipt. This call is not
    deferred until commit. Exceptions pass through authority_atomic's
    handling; external delivery is not made rollback-safe by the database
    transaction, and the intake record that the request was received,
    committed before that transaction, stands whatever the transaction does.
    """
    def send_for(self, application: Application, decision: ApplicationDecision) -> None:
        """Send the welcome for application's accepted decision."""
        ...


class UnavailableDiscussionWelcome:
    """Default welcome collaborator that reports the welcome is not yet built.

    If a fresh submission reaches the automatic-welcome call with this
    default, its implementation-limit exception aborts the submission and
    rolls back that submission's writes in the service's database
    transaction; the intake record of the request, committed before that
    transaction, is not rolled back. Completing automatic acceptance
    requires a supplied welcome collaborator as well as the CARE executor
    and all earlier submission checks.
    """
    def send_for(self, application: Application, decision: ApplicationDecision) -> None:
        """Raise S015OperationNotYetImplemented for automatic_welcome with
        limit S3-IL-NOTICE.
        """
        raise S015OperationNotYetImplemented("automatic_welcome", "S3-IL-NOTICE", "DISCUSSION")


def default_application_question(living_organism: LivingOrganism, name_source: LONameSource | None = None) -> dict[str, str]:
    """Return the default application question for living_organism.

    Returns "Why do you want to join <name>?" with answer format LONG_ANSWER.
    Uses name_source when truthy, otherwise FormationLONameSource. Requires
    the returned name to have exact type str and contain non-whitespace
    text, or raises S015ApplicationRefused; the accepted name is interpolated
    unchanged. Name-source errors propagate.

    When creating a new question draft, save_gateway_draft uses these
    defaults if question_text or answer_format is absent, preserving fields
    explicitly supplied. It does not use this fallback when updating an
    existing draft or replace explicitly supplied blank or None values.
    """
    name = (name_source or FormationLONameSource()).name_for(living_organism)
    if type(name) is not str or not name.strip():
        raise S015ApplicationRefused("formation LO name is required")
    return {"question_text": f"Why do you want to join {name}?", "answer_format": "LONG_ANSWER"}


@dataclass(frozen=True, slots=True)
class SubmitApplicationCommand:
    """Request to submit an application to a living organism.

    Taken by submit_application. credential is the applicant's User;
    living_organism_id names the LO; answer answers the LO's effective
    application question (default empty). predecessor_reference optionally
    names an earlier application of the applicant's to the same LO. Its
    withdrawal must be effective by effective_at and recorded no later than
    the new application's recorded_at.

    The carrier does not validate. The command requires request_reference
    and idempotency_key to be canonical strings (at most 128 and 120
    characters), the identifiers to be UUIDs and the occurred/effective
    times to be timezone-aware. INTEVIA assigns received_at; a supplied
    value is refused. Material inputs enter the request fingerprint, with
    the credential represented through the actor's Identity and access epoch.
    """
    credential: User
    living_organism_id: UUID
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    effective_at: datetime
    received_at: datetime | None = field(default=None, kw_only=True)
    predecessor_reference: UUID | None = None
    answer: str = ""


@dataclass(frozen=True, slots=True)
class GatewayDraftCommand:
    """Request to save a draft of an LO's application settings, question or
    governance configuration.

    Taken by save_gateway_draft. action selects the relation through
    EFFECT_APPLICATION_SETTINGS, EFFECT_APPLICATION_QUESTION or
    EFFECT_GOVERNANCE_CONFIGURATION; an unsupported action is refused.
    fields must be a dict containing only that relation's draftable keys.
    version_uuid None creates a version; otherwise it selects an existing
    version of the LO, which must still be a draft.

    INTEVIA assigns received_at; a supplied value is refused.

    Edits apply the supplied fields. When editing a governance configuration
    whose governance_enabled is None and that key is omitted, the service
    also fills it from the effective predecessor, defaulting to True if
    there is no predecessor choice. New governance drafts use the same
    fallback for an omitted choice. New question drafts fill omitted
    question_text or answer_format through default_application_question.

    A first creation or edit returns the saved version. A retry of a
    completed request returns a GatewayDraftResult, located (its
    version_uuid served through serve_replay) or unresolved. No command
    receipt is recorded for a draft. The request is bound by the intake
    before it runs, and its completion is established from the authority
    decision: an identical repeated request is served from the draft it
    saved (or told that INTEVIA cannot tell which draft that is) and never
    saves a second draft.
    """
    credential: User
    living_organism_id: UUID
    action: OrganismAction
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    effective_at: datetime
    received_at: datetime | None = field(default=None, kw_only=True)
    fields: dict[str, object]
    version_uuid: UUID | None = None


@dataclass(frozen=True, slots=True)
class EffectGatewayVersionCommand:
    """Request to put a draft version of an LO's application settings,
    question or governance configuration into effect.

    Taken by effect_gateway_version. action names the relation as for
    GatewayDraftCommand; version_uuid names the draft version of that LO.
    Keys, identifiers and times are validated as for
    SubmitApplicationCommand and enter the payload fingerprint; effective_at
    is the effect's effective time. INTEVIA assigns received_at; a supplied
    value is refused.
    """
    credential: User
    living_organism_id: UUID
    action: OrganismAction
    version_uuid: UUID
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    effective_at: datetime
    received_at: datetime | None = field(default=None, kw_only=True)


@dataclass(frozen=True, slots=True)
class WithdrawApplicationCommand:
    """Request by an applicant to withdraw an application.

    Taken by withdraw_application. application_uuid names the application.
    Keys, identifiers and times are validated as for
    SubmitApplicationCommand and enter the payload fingerprint.

    INTEVIA assigns received_at; a supplied value is refused.
    """

    credential: User
    application_uuid: UUID
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    effective_at: datetime
    received_at: datetime | None = field(default=None, kw_only=True)


@dataclass(frozen=True, slots=True)
class ClaimApplicationCommand:
    """Request by a Coordinator to claim an application for review.

    Taken by claim_application, and the base of ReassignApplicationCommand
    and DecideApplicationCommand. credential is the Coordinator's User;
    application_uuid names the application. Keys, identifiers and times are
    validated as for SubmitApplicationCommand and enter the payload
    fingerprint. INTEVIA assigns received_at; a supplied value is refused.
    """
    credential: User
    application_uuid: UUID
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    effective_at: datetime
    received_at: datetime | None = field(default=None, kw_only=True)


@dataclass(frozen=True, slots=True)
class ReassignApplicationCommand(ClaimApplicationCommand):
    """Request by a Coordinator to replace the application's current claim
    with a claim of their own.

    Taken by reassign_application. Same fields as ClaimApplicationCommand.
    """
    pass


@dataclass(frozen=True, slots=True)
class DecideApplicationCommand(ClaimApplicationCommand):
    """Request by the Coordinator holding the current claim to decide an
    application.

    Taken by decide_application. Adds outcome to ClaimApplicationCommand's
    fields: the string ACCEPT or DECLINE, checked before the actor is
    locked (TypeError or ValueError otherwise). outcome enters the payload
    fingerprint.
    """
    outcome: str


@dataclass(frozen=True, slots=True)
class ApplicationCommandResult:
    """Result of an applicant or Coordinator command, stored as its receipt.

    recorded_at is the recorded time of the command's primary record: the
    application, withdrawal, claim, replacement claim or decision.
    record_uuid identifies the claim (claim and reassign) or the decision
    (decide) and is None for submission and withdrawal. completion_due_at
    is set for claims and reassignments, with completion_due_source naming
    its source. lineage_reference is derived from the action, actor,
    application and payload fingerprint.

    Results are returned through serve_replay, fresh or replayed, so
    authority_reference and record_uuid may be WithheldField placeholders
    for the viewer.
    """
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
        """Return the result as a dict for its command receipt: one key per
        field, UUIDs as strings, action by value, times through
        canonical_timestamp, and absent values as None.
        """
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
        """Return the replay audiences of result's fields.

        For a decision result, record_uuid is visible only to the PROCESSING or
        APPLICANT audience. No other field is classified; serve_replay withholds
        authority_reference by its own rule.
        """
        if result.action is OrganismAction.DECIDE_APPLICATION:
            return {"record_uuid": ("PROCESSING", "APPLICANT")}
        return {}


@dataclass(frozen=True, slots=True)
class GatewayEffectCommandResult:
    """Result of effect_gateway_version, stored as its receipt.

    Names the version put into effect, the effect's effective_at, and the
    LO event appended for it: its event_uuid, event_sequence and
    lineage_reference. Returned through serve_replay, fresh or replayed, so
    authority_reference, event_uuid and event_sequence may be WithheldField
    placeholders for the viewer.
    """
    version_uuid: UUID
    action: OrganismAction
    effective_at: datetime
    lineage_reference: str
    authority_reference: str | WithheldField
    authority_decision_reference: str
    event_uuid: UUID | WithheldField
    event_sequence: int | WithheldField

    def receipt_payload(self) -> dict[str, object]:
        """Return the result as a dict for its command receipt: one key per
        field, UUIDs as strings, action by value and effective_at through
        canonical_timestamp.
        """
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
        """Return the replay audiences of result's fields: event_uuid and
        event_sequence are visible only to the PROCESSING audience.
        """
        return {"event_uuid": "PROCESSING", "event_sequence": "PROCESSING"}


@dataclass(frozen=True, slots=True)
class GatewayDraftResult:
    """Result of locating a completed draft request or reporting it unresolved."""

    living_organism_uuid: UUID
    version_uuid: UUID | WithheldField | None
    action: OrganismAction
    effective_at: datetime
    received_at: datetime
    located: bool

    @classmethod
    def replay_field_audiences(cls, result: "GatewayDraftResult") -> dict[str, str]:
        """Return the replay audiences of result's fields: version_uuid is
        visible only to the PROCESSING audience.
        """
        return {"version_uuid": "PROCESSING"}


def gateway_draft_retry_text(result: GatewayDraftResult, *, edit: bool) -> str:
    """Return the response text for a retry of a completed draft request. Selects the unresolved text when result.located is false, the withheld text when result.version_uuid is withheld from the viewer, and otherwise the located text; edit selects the edit wording over the creation wording. The receipt time is result.received_at. result is a completed-request result that has already passed the viewer filter, or the unresolved result; edit is true when the same request named a version_uuid."""
    received_at = canonical_timestamp(result.received_at)
    if not result.located:
        return (
            "Your draft was saved from this request, received at "
            f"{received_at}, but INTEVIA cannot tell which of your drafts it is; "
            "check your drafts. Nothing was written by this attempt."
        )
    if isinstance(result.version_uuid, WithheldField):
        if edit:
            return (
                "This change was already applied to a draft from this request, "
                f"received at {received_at}; it is a draft you cannot currently see. "
                "Nothing was written by this attempt."
            )
        return (
            "A draft was already saved from this request, "
            f"received at {received_at}; it is a draft you cannot currently see. "
            "Nothing was written by this attempt."
        )
    if not isinstance(result.version_uuid, UUID):
        raise ValueError("a located gateway draft result must contain a visible version UUID")
    if edit:
        return (
            "This change was already applied to a draft from this request, "
            f"received at {received_at}. Nothing was written by this attempt."
        )
    return (
        "This draft was already saved from this request, "
        f"received at {received_at}. Nothing was written by this attempt."
    )


def _gateway_result_from_receipt(payload: object) -> GatewayEffectCommandResult:
    """Rebuild a GatewayEffectCommandResult from a stored receipt payload.

    Raises S015ApplicationError unless payload's type is exactly dict.
    Every field is required; a missing key or a value that does not convert
    raises its own error. A Z offset in effective_at is read as UTC.
    """
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
    """Return whether identity's account history makes it eligible to apply.

    Requires a PROVISION transition occurring at or before effective_at,
    and an ACTIVE resulting state on the latest transition occurring at or
    before recorded_at, ordered by occurred_at then primary key.

    Both bounds compare the transition's occurred_at. The parameter named
    recorded_at is a cutoff on that same field; no separate transition
    recorded-time field is consulted. The helper does not validate the
    supplied times or establish the other application-admission conditions.
    """
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
    """Return the gateway version named by an LO's latest qualifying event.

    action selects application settings, application question or governance
    configuration. Selects the LO's event for that action effective by at
    and received and recorded by known_at (default at), in descending order
    of effective_at, occurred_at, received_at, recorded_at, sequence and
    event UUID. Returns None if the selected evidence reference is None,
    including when there is no qualifying event.

    Removes the expected prefix when present, parses the remainder as a UUID
    and looks up that version. It does not require the prefix or add an LO
    filter to the version lookup. For application settings, both entitlement
    election fields must be null. No matching version raises the model's
    DoesNotExist; reference parsing and other lookup errors propagate.

    Uses the supplied time coordinates without validating them or sampling
    a clock.
    """
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
    """Return the settings basis recorded on a Coordinator engagement.

    Reads the database's clock_timestamp() as known_at, then the LO's
    governance configuration and application settings in effect at
    effective_at as known at that time (_effective_gateway_version).
    Raises S015ApplicationRefused "effective governance configuration
    and application settings are required" if either is None.
    Otherwise returns "governance-configuration-version:<uuid>;"
    followed by "application-settings-version:<uuid>". Claim and
    reassignment record it on their LO_CSA engagement.
    """
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
    """Check an applicant's answer against the effective question's format.

    SHORT_ANSWER and LONG_ANSWER need text that is not blank, at most 255
    and 4096 characters once stripped; YES_NO needs YES or NO; MULTIPLE_CHOICE
    needs one of the question's options. SCALE_COMPLEX needs an integer
    written plainly (as str(int) would write it) within the bounds of the
    question's options, which must be at least two values that convert to
    integers. Any other format fails. Raises S015ApplicationRefused for an
    answer that is not a string or does not meet its format.
    """
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
    """Return a gateway version's status at a time: EFFECTIVE, SUPERSEDED or
    DRAFT.

    Reads the LO's events for the version's relation that are effective,
    received and recorded by at. EFFECTIVE when the latest of them (ordered
    as in _effective_gateway_version) names this version; SUPERSEDED when an
    earlier one does; otherwise DRAFT. No PROPOSED status is derived: change
    by CQ is not built. version must be a settings, question or governance
    configuration version; raises ValueError for an entitlement election
    settings version.
    """
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
    """Return whether a gateway version holds every choice needed to put it
    into effect.

    Settings: not an entitlement election, applications_open and
    automatic_acceptance chosen, and processing_responsibility LO or
    APPLICATION_CIRCLE. Question: non-blank question_text, a known
    answer_format, and two to seven options for MULTIPLE_CHOICE or
    SCALE_COMPLEX, none otherwise; option values are not checked here.
    Governance configuration: the four CQ choices made. The GOVERNANCE
    choice is checked separately when the version is put into effect.
    """
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
    """Rebuild an ApplicationCommandResult from a stored receipt payload.

    Raises S015ApplicationError unless payload is a dict (a subclass is
    accepted). record_uuid, completion_due_at and completion_due_source may
    be absent or None; the other fields are required. A missing required
    key or a value that does not convert raises its own error. A Z offset
    in a time is read as UTC.
    """
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
    """Insert one membership transition by raw SQL; return its row id.

    Writes core_organismmembershiptransition with action
    TRANSITION_MEMBERSHIP. sequence is 1 without a predecessor, else
    the predecessor's plus one; the predecessor's id, sequence and
    l1_commitment are carried. lineage_reference is "s015l1:" and the
    SHA-256 of the command domain and the canonical JSON of the new
    event UUID, the membership UUID, the sequence and the fingerprint.
    parts_commitment comes from s015_0022_digest, and the temporal
    basis from _resulting_temporal_basis.

    With no actor the row records actor state LIVING_ORGANISM and no
    access epoch; with one, PARTY and actor_access_epoch. A
    composing_rule_reference makes the entry COMPOSED with the
    enterer as composer; otherwise TRANSPORT. Capacity is STANDING
    and effective_until_state NONE; l1_commitment and l2_commitment
    are inserted as NULL. Checks no order and takes no lock; _admit
    checks the order, and its callers hold the organism lock.
    """
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
    """Take the registered organism lock for organism_id through
    public.s015_lock_organisms.

    Fresh application-service command paths take this lock before their
    gateway, applicant-LO pair, membership-context and application-row
    locks. The actor's Identity lock, and any required CARE or applicant
    Identity lock, precede it. A matching receipt replay can return before
    this lock is taken. The gateway effect path also calls this helper
    again after acquiring the organism lock in _gateway_start.
    """
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
    """Append one event to an Application's lineage by raw SQL.

    Locks the ApplicationLineage row and reads its head event. Raises
    S015OperationNotYetImplemented (out_of_order_application_act,
    IL-3) when (effective_at, occurred_at, received_at) sorts before
    the head's. Otherwise inserts a core_applicationlineageevent:
    sequence 1, or the head's plus one; prior_state the head's
    resulting_state (None for the first event); the head's
    l1_commitment as predecessor commitment; and the LO's founding
    authority basis. lineage_reference, parts_commitment, temporal
    basis, actor state and entry mode are formed as in
    _insert_admission_transition, with the application UUID in place
    of the membership UUID. actor_access_epoch is written as given.
    Returns None.
    """
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
    """What _coordinator_start established for a Coordinator command.

    The locked actor and their locked membership, the locked application,
    the qualified authority decision, the payload fingerprint, the request
    keys and the validated times (UTC). Used by claim, reassign and decide
    and their helpers.
    """
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
    """What _admit needs to admit an accepted applicant.

    The application; the enterer of the admission records (the deciding
    Coordinator, or the CARE Identity for automatic acceptance); the
    authority decision reference recorded on the decision; request keys,
    fingerprint and times;
    and, for automatic acceptance, the settings rule that composed the
    decision (None for a Coordinator's decision). For automatic acceptance
    the keys and fingerprint are the automatic decision's own, and the
    times are the application's.
    """
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
    """Application and gateway commands for living organisms.

    submit_application and withdraw_application are the applicant's;
    claim_application, reassign_application and decide_application
    are the LO's Coordinators'; save_gateway_draft and
    effect_gateway_version shape and put into effect the LO's
    application settings, question and governance configuration,
    also by a Coordinator. application_status reads.

    Each public command admits its request through the durable
    intake, then executes inside authority_atomic with the intake's
    received_at. Authority is evaluated at that retained time. Other
    checks and cache refreshes use the coordinates stated on their
    helpers and executors; not every read is an as-of-receipt read.

    Holds the S015 authority, the clock (used for the replay
    viewer's time and passed to OrganismRoleService), the database
    alias, and the optional LO name source, CARE executor and
    discussion welcome. See the module docstring for the intake and
    replay contract.
    """
    def __init__(
        self, *, authority: S015Authority | None = None, clock: Callable[[], datetime], database_alias: str = "default",
        name_source: LONameSource | None = None,
        care_executor: CareExecutor | None = None, welcome: DiscussionWelcome | None = None,
    ) -> None:
        """Build the service from its collaborators.

        authority defaults to an S015Authority over a
        ProductionApplicationAuthorityProvider on database_alias. The
        authority must be an S015Authority on the same database_alias, or
        TypeError "matching S015 authority is required". clock must be
        callable, or TypeError "clock is required"; it is not called here.
        name_source and care_executor are kept as given (None allowed);
        welcome, when None or falsy, becomes UnavailableDiscussionWelcome.
        """
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
        """Return the replay of a completed request, or None.

        Locks the actor's command receipt for action and idempotency_key
        and returns None when there is none. Raises
        S015CommandReplayMismatch "completed receipt does not match
        request" when its request reference or payload fingerprint
        differs. Otherwise rebuilds the stored result
        (_gateway_result_from_receipt when result_class is
        GatewayEffectCommandResult, else _result_from_receipt) and returns
        it through serve_replay for viewer.
        """
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
        """Qualify an applicant's own command through S015Authority.

        Builds an S015OwnApplicantAuthorityRequest for actor, evaluated at
        received_at, with effective_at, the request keys and
        predecessor_reference, and returns the authority's qualified
        decision. The target holds the action, the LO's organism UUID and
        the actor's Identity UUID, and the application's UUID when one is
        given; target_fingerprint is authority_target_fingerprint of it.
        Refusals raised by the authority propagate. Used by submission and
        withdrawal.
        """
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
        """Write an application command's receipt and serve its result.

        Builds the ApplicationCommandResult for application with
        recorded_at, the authority references and the optional record UUID
        and completion due fields. Its lineage_reference is "s015l1:" and
        the SHA-256 of the command domain and the canonical JSON of the
        action, the actor's Identity UUID, the application UUID and the
        fingerprint. Creates the OrganismCommandReceipt with the keys,
        fingerprint, references and receipt_payload, then returns the
        result through serve_replay for the actor, as viewer at
        self._clock().
        """
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
        """Take a transaction-level advisory lock for this applicant and LO.

        The key is derived from a fixed domain and both identifiers, so
        submissions by one applicant to one LO run one at a time; submission
        takes it after the organism lock and before checking for an open
        application.
        """
        key = int.from_bytes(hashlib.sha256(
            _PAIR_DOMAIN + actor.identity_id.bytes + organism.organism_id.bytes,
        ).digest()[:8], "big", signed=True)
        with connections[self._alias].cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [key])

    def _automatic_accept(self, application: Application, care: Identity, request_reference: str,
                          idempotency_key: str, fingerprint: str) -> tuple[ApplicationDecision, str, str, str, str, str]:
        """Accept a new application automatically under the LO's settings.

        Called by the fresh submission inside its authority_atomic
        block, after the caller has locked the applicant, any supplied
        CARE Identity and the organism. Opens no transaction, takes no
        explicit preparatory lock and performs no separate replay or
        authority qualification; it writes no command receipt.

        The application settings in effect at the application's
        effective_at, as known at its recorded_at, must be the
        application's own settings version, with applications_open and
        automatic_acceptance true, or S015ApplicationRefused with
        "automatic acceptance requires the application's recorded
        effective setting". The rule is
        "application-settings-version:<uuid>".

        The decision fingerprint is the SHA-256 of the command domain
        and the canonical JSON of action LO_AUTOMATIC, the application
        UUID, the rule and the submission's fingerprint. The decision
        reference is "s015d1:" and the SHA-256 of the domain and the
        canonical JSON of that fingerprint and the rule. The request
        reference and the idempotency key are both "s3fu:auto:<decision
        fingerprint>".

        Creates engagement 1 on route LO_AUTOMATIC (no actor; the rule
        as engagement reference and settings basis) and an ACCEPT
        decision. Both name the LO as deciding party and care as
        enterer; care is also the decision's executor. Both take the
        application's occurred, effective and received times. Then
        admits the applicant (_admit) with those keys, fingerprint and
        times and the rule as composing rule. Returns (decision,
        decision fingerprint, decision reference, rule, idempotency key,
        request reference).
        """
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

    def _gateway_start(
        self,
        command,
        *,
        create_head: bool,
        bound: BoundRequest,
        expected_entry: IntakeEntry,
        received_at: datetime,
    ):
        """Run the shared opening of the two gateway commands.

        create_head is True for save_gateway_draft and False for
        effect_gateway_version. command.action must be one of the three
        gateway actions, or ValueError "unknown gateway relation".
        Checks the keys (at most 128 and 120 characters), the occurred,
        effective and received times and the LO UUID; locks the actor;
        verifies the bound request; takes the command mutex; reads the
        LO (an unknown LO raises LivingOrganism.DoesNotExist).

        Effect path: computes the fingerprint (schema "s015-gateway-v1";
        action, LO and version; effective_at, received_at and fields),
        returns a matching receipt's replay, and requires the actor's
        active membership in the LO.

        Both paths then take the organism lock, lock the actor's
        membership context and lock the LO's gateway row, if any.

        Draft path: a QUALIFIED DRAFT authority decision for this actor,
        action, keys and LO, evaluated at received_at, with the same
        version UUID (None matching None), means the request completed.
        With a version UUID, the result takes that UUID from the
        recorded decision; this branch does not load the draft version.
        With none, the LO's versions of that kind by this actor with
        these received, occurred and effective times are looked up;
        anything but exactly one gives an unlocated GatewayDraftResult,
        returned as is. A located result is returned through
        serve_replay. Otherwise the fingerprint is computed as on the
        effect path.

        Both paths then qualify authority (_authority_request at
        received_at, version_kind DRAFT or EFFECT). The draft path
        creates the gateway if there is none; the effect path refuses
        "S3FU gateway closed" without one. gateway_at is the database's
        clock_timestamp(). If the governance configuration in effect at
        gateway_at has cq_active true, inherited portal settings raise
        not-yet-implemented S3-IL-INHERIT, and a CQ required for this
        change (the question flag for a question, the LO settings flag
        otherwise) raises S3-IL-CQ.

        Selects the first stored ACTIVE LO-scope COORDINATOR assignment
        on the actor's membership, then calls assignment_in_effect with
        effective_at. That helper checks stored ACTIVE state and the
        latest ASSIGN term's fixed expiry; it does not check the
        office's start or reconstruct historical state. An absent
        assignment or a false result raises S015ApplicationRefused with
        "effective Coordinator office is required".

        Returns a replay result, a GatewayDraftResult, or (actor,
        organism, decision, fingerprint, request_reference,
        idempotency_key, occurred_at, effective_at, received_at,
        gateway_at).
        """
        action = command.action
        if action not in _VERSION_KINDS:
            raise ValueError("unknown gateway relation")
        request_reference = _canonical_key(command.request_reference, "request_reference", 128)
        idempotency_key = _canonical_key(command.idempotency_key, "idempotency_key", 120)
        occurred_at = _validate_timestamp(command.occurred_at, "occurred_at")
        effective_at = _validate_timestamp(command.effective_at, "effective_at")
        received_at = _validate_timestamp(received_at, "received_at")
        organism_uuid = _canonical_uuid(command.living_organism_id, "living_organism_id")
        actor = _lock_actor(database_alias=self._alias, credential=command.credential,
                            unavailable=S015ApplicationAuthorityUnavailable)
        bound.verify(command, actor, expected_entry, self._alias)
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
            with connections[self._alias].cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, target->>'version_uuid'
                      FROM core_authoritydecision
                     WHERE actor_id = %s
                       AND action = %s
                       AND idempotency_key = %s
                       AND request_reference = %s
                       AND outcome = 'QUALIFIED'
                       AND evaluated_at = %s
                       AND target->>'organism_uuid' = %s
                       AND (target->>'version_uuid') IS NOT DISTINCT FROM %s
                       AND decision_payload->>'version_kind' = 'DRAFT'
                    """,
                    [
                        actor.pk,
                        action.value,
                        idempotency_key,
                        request_reference,
                        received_at,
                        str(organism_uuid),
                        None if command.version_uuid is None else str(command.version_uuid),
                    ],
                )
                completed_decision = cursor.fetchone()
            if completed_decision is not None:
                if command.version_uuid is None:
                    model, _ = _VERSION_KINDS[action]
                    candidates = list(
                        model.objects.using(self._alias)
                        .filter(
                            living_organism_id=organism.pk,
                            actor_id=actor.pk,
                            received_at=received_at,
                            occurred_at=occurred_at,
                            effective_at=effective_at,
                        )
                        .values_list("version_uuid", flat=True)
                    )
                    if len(candidates) != 1:
                        return GatewayDraftResult(
                            living_organism_uuid=organism_uuid,
                            version_uuid=None,
                            action=action,
                            effective_at=effective_at,
                            received_at=received_at,
                            located=False,
                        )
                    version_uuid = candidates[0]
                else:
                    version_uuid = UUID(completed_decision[1])
                result = GatewayDraftResult(
                    living_organism_uuid=organism_uuid,
                    version_uuid=version_uuid,
                    action=action,
                    effective_at=effective_at,
                    received_at=received_at,
                    located=True,
                )
                return serve_replay(
                    result,
                    viewer=ReplayViewer(actor.pk, actor.identity_id, self._clock()),
                    result_type=ReplayResultType(GatewayDraftResult, self._alias),
                )
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
        """Create or edit a draft gateway version for an LO.

        Requires the intake's admission state for SAVE_GATEWAY_DRAFT
        (_require_admission_state) and the entry to be in ACTIVE_ENTRIES,
        or IntakeAdmissionTechnicalFailure "SAVE_GATEWAY_DRAFT is not
        active". Validates the command's intake shape, resolves the actor
        from command.credential and admits the request (admit_request),
        which commits or recovers its binding and received_at before the
        command runs. Returns _execute_save_gateway_draft's result: the
        saved version, or a GatewayDraftResult for a completed request.
        Writes no command receipt.
        """
        from src.intevia.services.command_intake import (
            ACTIVE_ENTRIES,
            IntakeAdmissionTechnicalFailure,
            IntakeEntry,
            _require_admission_state,
            _resolve_actor,
            admit_request,
        )

        entry = IntakeEntry.SAVE_GATEWAY_DRAFT
        _require_admission_state(self._alias, entry)
        if entry not in ACTIVE_ENTRIES:
            raise IntakeAdmissionTechnicalFailure("SAVE_GATEWAY_DRAFT is not active")
        shape = self._validate_shape(entry, command)
        actor_ref = _resolve_actor(self._alias, command.credential)
        bound = admit_request(self._alias, entry, actor_ref, shape)
        return self._execute_save_gateway_draft(command, bound)

    def _execute_save_gateway_draft(self, command: GatewayDraftCommand, bound: BoundRequest):
        """Execute an admitted SAVE_GATEWAY_DRAFT request.

        Inside authority_atomic as of bound.received_at. Opens with
        _gateway_start (create_head True) and returns its
        GatewayDraftResult for a completed request. command.fields must be
        a dict whose keys are allowed for the version's kind, or
        S015ApplicationRefused "invalid gateway draft fields". Settings:
        applications_open, automatic_acceptance and
        processing_responsibility. Question: question_text, answer_format
        and options. Governance configuration: the four CQ choices,
        governance_enabled, coordinator_nomination_policy,
        role_suspension_enabled and suspension_tolls_term.

        With a version_uuid: locks that version of the LO (an unknown one
        raises the model's DoesNotExist); it must be DRAFT at gateway_at,
        or "governed gateway version cannot be edited"; saves the given
        fields on it. Without one: creates a version with the actor as
        actor and enterer, the command's times, the received_at, and as
        supersedes the version in effect at gateway_at. A new question
        missing question_text or answer_format takes
        default_application_question's values for the fields not given.

        For a governance configuration, governance_enabled, when not given
        (and, on an edit, unset on the version), is taken from the version
        in effect at gateway_at, or True when there is none or it is
        unset. Returns the saved version.
        """
        from src.intevia.services.command_intake import IntakeEntry

        with _authority_atomic(self._alias, received_at=bound.received_at):
            start = self._gateway_start(
                command,
                create_head=True,
                bound=bound,
                expected_entry=IntakeEntry.SAVE_GATEWAY_DRAFT,
                received_at=bound.received_at,
            )
            if isinstance(start, GatewayDraftResult):
                return start
            (actor, organism, _, _, _, _, occurred_at, effective_at, received_at,
             gateway_at) = start
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
        """Put a draft gateway version into effect for an LO.

        Requires the intake's admission state for EFFECT_GATEWAY_VERSION
        (_require_admission_state) and the entry to be in ACTIVE_ENTRIES,
        or IntakeAdmissionTechnicalFailure "EFFECT_GATEWAY_VERSION is not
        active". Validates the command's intake shape, resolves the actor
        from command.credential and admits the request (admit_request),
        which commits or recovers its binding and received_at before the
        command runs. Returns _execute_effect_gateway_version's
        GatewayEffectCommandResult, served through serve_replay.
        """
        from src.intevia.services.command_intake import (
            ACTIVE_ENTRIES,
            IntakeAdmissionTechnicalFailure,
            IntakeEntry,
            _require_admission_state,
            _resolve_actor,
            admit_request,
        )

        entry = IntakeEntry.EFFECT_GATEWAY_VERSION
        _require_admission_state(self._alias, entry)
        if entry not in ACTIVE_ENTRIES:
            raise IntakeAdmissionTechnicalFailure("EFFECT_GATEWAY_VERSION is not active")
        shape = self._validate_shape(entry, command)
        actor_ref = _resolve_actor(self._alias, command.credential)
        bound = admit_request(self._alias, entry, actor_ref, shape)
        return self._execute_effect_gateway_version(command, bound)

    def _execute_effect_gateway_version(self, command: EffectGatewayVersionCommand, bound: BoundRequest):
        """Execute an admitted EFFECT_GATEWAY_VERSION request.

        Runs _effect_gateway_version. For EFFECT_GOVERNANCE_CONFIGURATION
        only, two failures carrying one of the seven SB-G enabling codes
        (_SBG_ENABLING_REFUSAL_CODES) become S015ApplicationRefused with
        that refusal_code and a message from
        _governance_enabling_refusal_message, for the LO, the command's
        effective_at and bound.received_at:

        - S015AuthorityDenied with such a refusal_code: the message's
          opening ends "." and its tail says the refusal and the request's
          receipt time were recorded and nothing else was saved.
        - IntegrityError whose cause's diagnostic primary message equals
          such a code's value: the opening ends "; no governed record was
          written." and the tail says the receipt time was kept and an
          unchanged retry is answered as of that time.

        Every other failure propagates unchanged.
        """
        try:
            return self._effect_gateway_version(command, bound=bound)
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
                recorded_at=bound.received_at,
                code=exc.refusal_code,
                opening_end=".",
                tail=_intake_tail(
                    "INTEVIA recorded this refusal and that the request was received at {received_at}; "
                    "nothing else was saved.",
                    bound.received_at,
                ),
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
                recorded_at=bound.received_at,
                code=refusal_code,
                opening_end="; no governed record was written.",
                tail=_intake_tail(
                    "INTEVIA kept a record that this request was received at {received_at}; "
                    "sent again unchanged, it is answered as of that time.",
                    bound.received_at,
                ),
            )
            raise S015ApplicationRefused(
                message,
                refusal_code=refusal_code,
            ) from exc

    def _effect_gateway_version(self, command: EffectGatewayVersionCommand, *, bound: BoundRequest):
        """Put a draft gateway version into effect and record it on the LO.

        Inside authority_atomic as of bound.received_at. Opens with
        _gateway_start (create_head False) and returns its replay for a
        completed request. Locks the named version of the LO (an unknown
        one raises the model's DoesNotExist). It is refused
        (S015ApplicationRefused) unless, at gateway_at, it is DRAFT
        ("gateway version already effective") and complete
        (_gateway_version_complete; "S3FU gateway effect: version
        incomplete"), and, for a governance
        configuration, its governance_enabled is set. Not yet implemented:
        a governance configuration with cq_active true (S3-IL-CQ) and
        settings with processing_responsibility APPLICATION_CIRCLE
        (S3-IL-CIRCLE). Settings with automatic_acceptance true are
        refused when the service has no CARE executor, or only the default
        welcome; both messages cite bound.received_at.

        Takes the organism lock again and locks the LO row. The LO's head
        event must exist and be effective no later than effective_at, or
        "gateway effect requires a current LO chain". Inserts, by raw
        SQL, the next core_livingorganismevent: the gateway action, the
        head's resulting_state as both prior and resulting state, actor
        state LIVING_ORGANISM with the actor as enterer, the LO's founding
        authority basis, and evidence "<kind prefix><version UUID>". Its
        lineage_reference is "s015l1:" and the SHA-256 of the command
        domain and the canonical JSON of the event UUID, the organism UUID
        and the fingerprint. That event is what _effective_gateway_version
        reads. Refreshes the LO's governed cache at effective_at, known at
        the database's clock_timestamp().

        Writes the command receipt and returns the
        GatewayEffectCommandResult through serve_replay for the actor, as
        viewer at self._clock().
        """
        from src.intevia.services.command_intake import IntakeEntry

        with _authority_atomic(self._alias, received_at=bound.received_at):
            start = self._gateway_start(
                command,
                create_head=False,
                bound=bound,
                expected_entry=IntakeEntry.EFFECT_GATEWAY_VERSION,
                received_at=bound.received_at,
            )
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
            if isinstance(version, LOApplicationSettingsVersion) and version.automatic_acceptance is True:
                if self._care_executor is None:
                    raise S015ApplicationRefused(
                        _AUTOMATIC_ACCEPTANCE_CARE_REFUSAL.format(
                            received_at=canonical_timestamp(bound.received_at),
                        )
                    )
                if isinstance(self._welcome, UnavailableDiscussionWelcome):
                    raise S015ApplicationRefused(
                        _AUTOMATIC_ACCEPTANCE_WELCOME_REFUSAL.format(
                            received_at=canonical_timestamp(bound.received_at),
                        )
                    )
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

    def _validate_shape(self, entry: IntakeEntry, command: object) -> IntakeShape:
        """Return command's intake shape for entry.

        The two gateway entries use intake_shape; every other entry uses
        application_shape. Their errors propagate.
        """
        from src.intevia.services.command_intake import (
            IntakeEntry,
            application_shape,
            intake_shape,
        )

        if entry in {IntakeEntry.SAVE_GATEWAY_DRAFT, IntakeEntry.EFFECT_GATEWAY_VERSION}:
            return intake_shape(entry, command)
        return application_shape(entry, command)

    def _coordinator_start(
        self,
        command: ClaimApplicationCommand,
        action: OrganismAction,
        *,
        bound: BoundRequest,
        expected_entry: IntakeEntry,
        received_at: datetime,
        outcome: str | None = None,
    ) -> _CoordinatorContext | ApplicationCommandResult:
        """Run the shared opening of the claim, reassign and decide commands.

        Checks the keys (at most 128 and 120 characters), the occurred and
        effective times and the application UUID; received_at is used as
        passed (its callers validate it). Locks the actor, verifies the
        bound request and takes the command mutex. Reads the application,
        or S015ApplicationNotFound "application not found". The
        fingerprint (schema "s015-application-v1") covers the action, the
        application UUID and the LO's organism UUID, effective_at,
        received_at and outcome. A matching receipt returns its replay;
        otherwise the actor must hold an active membership in the LO.

        Lock order on a fresh request: for DECIDE_APPLICATION, a FOR KEY
        SHARE lock on the applicant's Identity row first; then the
        organism lock, the actor's membership context, and the application
        row.

        For DECIDE_APPLICATION, if the application's engagement with the
        highest ordinal effective by effective_at and recorded by
        received_at is on LO_CSA, its engagement_reference, parsed as a
        UUID, is the assignment passed to the authority (None if it does
        not parse). Qualifies authority (_authority_request, at
        received_at, with outcome). Returns a _CoordinatorContext, or the
        replay result.
        """
        request_reference = _canonical_key(command.request_reference, "request_reference", 128)
        idempotency_key = _canonical_key(command.idempotency_key, "idempotency_key", 120)
        occurred_at = _validate_timestamp(command.occurred_at, "occurred_at")
        effective_at = _validate_timestamp(command.effective_at, "effective_at")
        application_uuid = _canonical_uuid(command.application_uuid, "application_uuid")
        actor = _lock_actor(
            database_alias=self._alias, credential=command.credential,
            unavailable=S015ApplicationAuthorityUnavailable,
        )
        bound.verify(command, actor, expected_entry, self._alias)
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
        """Return the actor's Coordinator office for this LO, required in effect.

        Takes an ACTIVE COORDINATOR role assignment of LIVING_ORGANISM scope on
        the actor's membership and asks OrganismRoleService (with this service's
        authority, clock and database alias) whether it is in effect at the
        command's effective_at. Raises S015ApplicationRefused when there is no
        such assignment or it is not in effect.
        """
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
        """Refuse an application engaged on a route not yet built.

        Raises S015OperationNotYetImplemented with limit S3-IL-CQ when any
        engagement is on LO_CQ or CIRCLE_CQ, and with S3-IL-CIRCLE when any is
        on CIRCLE_CSA.
        """
        if application.engagements.using(self._alias).filter(route__in=("LO_CQ", "CIRCLE_CQ")).exists():
            raise S015OperationNotYetImplemented("csa_application", "S3-IL-CQ", "DISCUSSION")
        if application.engagements.using(self._alias).filter(route="CIRCLE_CSA").exists():
            raise S015OperationNotYetImplemented("circle_application", "S3-IL-CIRCLE", "DISCUSSION")

    def _open_for_coordinator(self, context: _CoordinatorContext) -> None:
        """Refuse a Coordinator command on an application that is not open.

        Applies _csa_route, then raises S015ApplicationRefused when the
        application has a withdrawal or a decision. Their effective and recorded
        times are not considered.
        """
        self._csa_route(context.application)
        if ApplicationWithdrawal.objects.using(self._alias).filter(application=context.application).exists():
            raise S015ApplicationRefused("application already withdrawn")
        if ApplicationDecision.objects.using(self._alias).filter(application=context.application).exists():
            raise S015ApplicationRefused("application already decided")

    def _current_claim(self, context: _CoordinatorContext) -> CSAAssignment:
        # Runs under the application row lock taken in _coordinator_start: every committed claim or ending visible
        # here was admitted before this command's row, whose recorded_at 0026 sets after the same lock. So no
        # recorded_at bound applies, and no service clock enters (G-2-CLOCK is the declared database-clock limit).
        """Return the application's current Coordinator claim at effective_at.

        Called under the application-row lock taken by _coordinator_start.
        Considers claims effective by context.effective_at in descending order
        of effective_at, recorded_at and primary key, returning the first with
        no ending effective by that time. Neither claims nor endings have a
        recorded-time cutoff here, and no service clock is sampled.

        Raises S015ApplicationRefused when no such claim exists.
        """
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
        """Finish a Coordinator command through _finish.

        Passes the context's actor, keys, fingerprint and application, its
        qualified decision's authority references, record_uuid and
        recorded_at. With completion_due_at, the due source is "DEFAULT
        (no settings store)"; without it, both are None.
        """
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
        """Submit an application to join a living organism.

        Requires the intake's admission state for SUBMIT_APPLICATION
        (_require_admission_state) and the entry to be in ACTIVE_ENTRIES,
        or IntakeAdmissionTechnicalFailure "SUBMIT_APPLICATION is not
        active". Validates the command's intake shape, resolves the actor
        from command.credential and admits the request (admit_request),
        which commits or recovers its binding and received_at before the
        command runs. Returns _execute_submit_application's result.
        """
        from src.intevia.services.command_intake import (
            ACTIVE_ENTRIES,
            IntakeAdmissionTechnicalFailure,
            IntakeEntry,
            _require_admission_state,
            _resolve_actor,
            admit_request,
        )

        entry = IntakeEntry.SUBMIT_APPLICATION
        _require_admission_state(self._alias, entry)
        if entry not in ACTIVE_ENTRIES:
            raise IntakeAdmissionTechnicalFailure("SUBMIT_APPLICATION is not active")
        shape = self._validate_shape(entry, command)
        actor_ref = _resolve_actor(self._alias, command.credential)
        bound = admit_request(self._alias, entry, actor_ref, shape)
        return self._execute_submit_application(command, bound)

    def _execute_submit_application(
        self,
        command: SubmitApplicationCommand,
        bound: BoundRequest,
    ) -> ApplicationCommandResult:
        """Execute an admitted SUBMIT_APPLICATION request.

        Inside authority_atomic as of bound.received_at. Checks the
        keys, the times, the LO UUID and any predecessor UUID; locks the
        actor; verifies the bound request; takes the command mutex. An
        unknown LO raises S015ApplicationNotFound "living organism not
        found". The fingerprint (schema "s015-application-v1") covers
        the action, LO and applicant, effective_at, received_at, the
        predecessor and the answer. A matching receipt returns its
        replay.

        A fresh request goes on in this order. A supplied CARE
        executor's Identity is locked and must be active and not the
        applicant's, or S015ApplicationAuthorityUnavailable. The
        organism lock and the applicant-LO pair lock are taken, and the
        gateway row is read FOR SHARE. Authority is qualified
        (_authorise, at received_at). A current (ACTIVE or PROBATIONARY)
        member is refused; an ENDED membership raises
        not-yet-implemented IL-1. Without a gateway, "S3FU gateway
        closed".

        gateway_at is the database's clock_timestamp(). The settings and
        question in effect at gateway_at must exist, with
        applications_open true, and the LO's folded state at
        effective_at, known at gateway_at, must be ACTIVE, or "S3FU
        gateway closed". The answer must fit the question
        (_validate_gateway_answer). Automatic acceptance without a CARE
        executor raises not-yet-implemented S3-IL-CARE-EXEC. An open
        application (no decision, no withdrawal) by this applicant to
        this LO is refused, and a predecessor must be this applicant's
        application to this LO.

        Creates the Application with those versions, the answer and the
        authority references, and reloads it for its recorded_at. The
        predecessor needs a withdrawal effective by effective_at and
        recorded by that recorded_at, and the applicant must be eligible
        (applicant_eligible) at effective_at and that recorded_at, or
        the command is refused. Creates the lineage; under automatic
        acceptance, accepts (_automatic_accept). Appends the SUBMITTED
        event; under automatic acceptance, then the ACCEPTED event (no
        actor; CARE as enterer; the rule as composing rule) and the
        welcome. The welcome runs synchronously inside this transaction,
        before the command receipt; it is not deferred until commit.
        Exceptions follow authority_atomic's handling, and the database
        transaction does not make external delivery rollback-safe.
        Finishes with _finish at the application's recorded_at.
        """
        from src.intevia.services.command_intake import IntakeEntry

        with _authority_atomic(self._alias, received_at=bound.received_at):
            request_reference = _canonical_key(command.request_reference, "request_reference", 128)
            idempotency_key = _canonical_key(command.idempotency_key, "idempotency_key", 120)
            occurred_at = _validate_timestamp(command.occurred_at, "occurred_at")
            effective_at = _validate_timestamp(command.effective_at, "effective_at")
            received_at = _validate_timestamp(bound.received_at, "received_at")
            organism_uuid = _canonical_uuid(command.living_organism_id, "living_organism_id")
            predecessor_uuid = None if command.predecessor_reference is None else _canonical_uuid(
                command.predecessor_reference, "predecessor_reference",
            )
            actor = _lock_actor(
                database_alias=self._alias, credential=command.credential,
                unavailable=S015ApplicationAuthorityUnavailable,
            )
            bound.verify(command, actor, IntakeEntry.SUBMIT_APPLICATION, self._alias)
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
        """Withdraw the applicant's open application.

        Requires the intake's admission state for WITHDRAW_APPLICATION
        (_require_admission_state) and the entry to be in ACTIVE_ENTRIES,
        or IntakeAdmissionTechnicalFailure "WITHDRAW_APPLICATION is not
        active". Validates the command's intake shape, resolves the actor
        from command.credential and admits the request (admit_request),
        which commits or recovers its binding and received_at before the
        command runs. Returns _execute_withdraw_application's result.
        """
        from src.intevia.services.command_intake import (
            ACTIVE_ENTRIES,
            IntakeAdmissionTechnicalFailure,
            IntakeEntry,
            _require_admission_state,
            _resolve_actor,
            admit_request,
        )

        entry = IntakeEntry.WITHDRAW_APPLICATION
        _require_admission_state(self._alias, entry)
        if entry not in ACTIVE_ENTRIES:
            raise IntakeAdmissionTechnicalFailure("WITHDRAW_APPLICATION is not active")
        shape = self._validate_shape(entry, command)
        actor_ref = _resolve_actor(self._alias, command.credential)
        bound = admit_request(self._alias, entry, actor_ref, shape)
        return self._execute_withdraw_application(command, bound)

    def _execute_withdraw_application(
        self,
        command: WithdrawApplicationCommand,
        bound: BoundRequest,
    ) -> ApplicationCommandResult:
        """Execute an admitted WITHDRAW_APPLICATION request.

        Inside authority_atomic as of bound.received_at. Checks the keys,
        the times and the application UUID; locks the actor; verifies the
        bound request; takes the command mutex. An unknown application
        raises S015ApplicationNotFound "application not found". The
        fingerprint (schema "s015-application-v1") covers the action, the
        application and its LO, effective_at and received_at. A matching
        receipt returns its replay.

        A fresh request takes the organism lock, locks the application row
        and qualifies authority (_authorise, with the application, at
        received_at). Refuses an application already decided, then one
        already withdrawn; one with an LO_CQ or CIRCLE_CQ engagement
        raises not-yet-implemented S3-IL-CQ. Creates the
        ApplicationWithdrawal and reloads it for its recorded_at, appends
        the WITHDRAWN event, and finishes with _finish at that
        recorded_at.
        """
        from src.intevia.services.command_intake import IntakeEntry

        with _authority_atomic(self._alias, received_at=bound.received_at):
            request_reference = _canonical_key(command.request_reference, "request_reference", 128)
            idempotency_key = _canonical_key(command.idempotency_key, "idempotency_key", 120)
            occurred_at = _validate_timestamp(command.occurred_at, "occurred_at")
            effective_at = _validate_timestamp(command.effective_at, "effective_at")
            received_at = _validate_timestamp(bound.received_at, "received_at")
            application_uuid = _canonical_uuid(command.application_uuid, "application_uuid")
            actor = _lock_actor(
                database_alias=self._alias, credential=command.credential,
                unavailable=S015ApplicationAuthorityUnavailable,
            )
            bound.verify(command, actor, IntakeEntry.WITHDRAW_APPLICATION, self._alias)
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
        """Claim an open application for the acting Coordinator.

        Requires the intake's admission state for CLAIM_APPLICATION
        (_require_admission_state) and the entry to be in ACTIVE_ENTRIES,
        or IntakeAdmissionTechnicalFailure "CLAIM_APPLICATION is not
        active". Validates the command's intake shape, resolves the actor
        from command.credential and admits the request (admit_request),
        which commits or recovers its binding and received_at before the
        command runs. Returns _execute_claim_application's result.
        """
        from src.intevia.services.command_intake import (
            ACTIVE_ENTRIES,
            IntakeAdmissionTechnicalFailure,
            IntakeEntry,
            _require_admission_state,
            _resolve_actor,
            admit_request,
        )

        entry = IntakeEntry.CLAIM_APPLICATION
        _require_admission_state(self._alias, entry)
        if entry not in ACTIVE_ENTRIES:
            raise IntakeAdmissionTechnicalFailure("CLAIM_APPLICATION is not active")
        shape = self._validate_shape(entry, command)
        actor_ref = _resolve_actor(self._alias, command.credential)
        bound = admit_request(self._alias, entry, actor_ref, shape)
        return self._execute_claim_application(command, bound)

    def _execute_claim_application(
        self,
        command: ClaimApplicationCommand,
        bound: BoundRequest,
    ) -> ApplicationCommandResult:
        """Execute an admitted CLAIM_APPLICATION request.

        Inside authority_atomic as of bound.received_at, which is
        validated here and passed to _coordinator_start. Opens with
        _coordinator_start and returns its replay for a completed
        request. The application must be open (_open_for_coordinator)
        and have no claim at any time, or "application already has a
        claim". _coordinator_office selects a stored ACTIVE LO-scope
        COORDINATOR assignment and checks its latest ASSIGN term's fixed
        expiry at effective_at. It does not reconstruct historical
        office state or check the office's start. No assignment or a
        failed check raises S015ApplicationRefused with "effective
        Coordinator office is required".

        Creates a CSAAssignment of the actor's membership and office,
        due 27 days after effective_at, and reloads it for its
        recorded_at. Creates engagement 1 on route LO_CSA, with the LO
        as deciding party, the claim's UUID as reference and the
        settings basis from _engagement_settings_basis, and appends the
        IN_REVIEW event. Finishes with the claim's UUID, its recorded_at
        and the due time.
        """
        from src.intevia.services.command_intake import IntakeEntry

        with _authority_atomic(self._alias, received_at=bound.received_at):
            context = self._coordinator_start(
                command,
                OrganismAction.CLAIM_APPLICATION,
                bound=bound,
                expected_entry=IntakeEntry.CLAIM_APPLICATION,
                received_at=_validate_timestamp(bound.received_at, "received_at"),
            )
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
        """Take over an application's current claim for the acting
        Coordinator.

        Requires the intake's admission state for REASSIGN_APPLICATION
        (_require_admission_state) and the entry to be in ACTIVE_ENTRIES,
        or IntakeAdmissionTechnicalFailure "REASSIGN_APPLICATION is not
        active". Validates the command's intake shape, resolves the actor
        from command.credential and admits the request (admit_request),
        which commits or recovers its binding and received_at before the
        command runs. Returns _execute_reassign_application's result.
        """
        from src.intevia.services.command_intake import (
            ACTIVE_ENTRIES,
            IntakeAdmissionTechnicalFailure,
            IntakeEntry,
            _require_admission_state,
            _resolve_actor,
            admit_request,
        )

        entry = IntakeEntry.REASSIGN_APPLICATION
        _require_admission_state(self._alias, entry)
        if entry not in ACTIVE_ENTRIES:
            raise IntakeAdmissionTechnicalFailure("REASSIGN_APPLICATION is not active")
        shape = self._validate_shape(entry, command)
        actor_ref = _resolve_actor(self._alias, command.credential)
        bound = admit_request(self._alias, entry, actor_ref, shape)
        return self._execute_reassign_application(command, bound)

    def _execute_reassign_application(
        self,
        command: ReassignApplicationCommand,
        bound: BoundRequest,
    ) -> ApplicationCommandResult:
        """Execute an admitted REASSIGN_APPLICATION request.

        Inside authority_atomic as of bound.received_at, which is
        validated here and passed to _coordinator_start. Opens with
        _coordinator_start and returns its replay for a completed
        request. The application must be open. Its current claim at
        effective_at (_current_claim) must be the reference of its
        highest-ordinal engagement, read with no time bound, or "current
        engagement does not match claim". _coordinator_office selects a
        stored ACTIVE LO-scope COORDINATOR assignment and checks its
        latest ASSIGN term's fixed expiry at effective_at. It does not
        reconstruct historical office state or check the office's start.
        No assignment or a failed check raises S015ApplicationRefused
        with "effective Coordinator office is required".

        Reads the settings basis (_engagement_settings_basis), ends the
        current claim (CSAAssignmentEnd), and creates a replacement
        claim of the actor's membership and office that replaces it, due
        27 days after effective_at; both are reloaded for their
        recorded_at. Creates the next engagement (ordinal plus one) on
        LO_CSA with the replacement's UUID as reference and the ending's
        UUID as basis_reference, and appends an IN_REVIEW event.
        Finishes with the replacement's UUID, its recorded_at and the
        due time.

        This method does not require a different assignee: the existing
        assignee can replace their own claim. The new due time is always
        this command's effective_at plus 27 days.
        """
        from src.intevia.services.command_intake import IntakeEntry

        with _authority_atomic(self._alias, received_at=bound.received_at):
            context = self._coordinator_start(
                command,
                OrganismAction.REASSIGN_APPLICATION,
                bound=bound,
                expected_entry=IntakeEntry.REASSIGN_APPLICATION,
                received_at=_validate_timestamp(bound.received_at, "received_at"),
            )
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
        """Accept or decline an application as its current self-assignee.

        Requires the intake's admission state for DECIDE_APPLICATION
        (_require_admission_state) and the entry to be in ACTIVE_ENTRIES,
        or IntakeAdmissionTechnicalFailure "DECIDE_APPLICATION is not
        active". Validates the command's intake shape, resolves the actor
        from command.credential and admits the request (admit_request),
        which commits or recovers its binding and received_at before the
        command runs. Returns _execute_decide_application's result.
        """
        from src.intevia.services.command_intake import (
            ACTIVE_ENTRIES,
            IntakeAdmissionTechnicalFailure,
            IntakeEntry,
            _require_admission_state,
            _resolve_actor,
            admit_request,
        )

        entry = IntakeEntry.DECIDE_APPLICATION
        _require_admission_state(self._alias, entry)
        if entry not in ACTIVE_ENTRIES:
            raise IntakeAdmissionTechnicalFailure("DECIDE_APPLICATION is not active")
        shape = self._validate_shape(entry, command)
        actor_ref = _resolve_actor(self._alias, command.credential)
        bound = admit_request(self._alias, entry, actor_ref, shape)
        return self._execute_decide_application(command, bound)

    def _execute_decide_application(
        self,
        command: DecideApplicationCommand,
        bound: BoundRequest,
    ) -> ApplicationCommandResult:
        """Execute an admitted DECIDE_APPLICATION request.

        Inside authority_atomic as of bound.received_at, which is
        validated here and passed to _coordinator_start. Before the
        actor is locked, _canonical_key checks command.outcome: a
        non-string raises TypeError "outcome must be a string"; an
        empty, non-canonical or overlong string raises ValueError
        "outcome is not canonical" (maximum 7 characters). A canonical
        value other than ACCEPT or DECLINE raises ValueError "outcome
        must be ACCEPT or DECLINE". Opens with _coordinator_start (the
        outcome in the fingerprint and the authority request) and
        returns its replay for a completed request.

        The application must be open. Only the current self-assignee
        decides: the current claim at effective_at must have been made
        by the actor for the actor's membership ("only the current
        self-assignee may decide"). _coordinator_office selects a stored
        ACTIVE LO-scope COORDINATOR assignment and checks its latest
        ASSIGN term's fixed expiry at effective_at; it does not check
        its start or reconstruct historical office state. That selected
        office must be the claim's office, or "claim's qualifying office
        is not effective". The highest-ordinal engagement effective by
        effective_at must be on LO_CSA and name that claim, or "no
        current CSA engagement". For ACCEPT, an LO whose folded state at
        effective_at, known at the database's clock_timestamp(), is
        RESTRICTED_CONTINUITY raises not-yet-implemented S3-IL-RC.

        Creates the ApplicationDecision on route LO_CSA: the LO as
        deciding party, the claim, the actor as responsible Coordinator,
        executor and enterer, the office and the authority references.
        Reloads it for its recorded_at. ACCEPT admits the applicant
        (_admit) with this command's keys, fingerprint and times.
        Appends an ACCEPTED or DECLINED event with no actor and the
        Coordinator as enterer. Finishes with the decision's UUID and
        recorded_at.
        """
        from src.intevia.services.command_intake import IntakeEntry

        with _authority_atomic(self._alias, received_at=bound.received_at):
            outcome = _canonical_key(command.outcome, "outcome", 7)
            if outcome not in {"ACCEPT", "DECLINE"}:
                raise ValueError("outcome must be ACCEPT or DECLINE")
            context = self._coordinator_start(
                command,
                OrganismAction.DECIDE_APPLICATION,
                bound=bound,
                expected_entry=IntakeEntry.DECIDE_APPLICATION,
                received_at=_validate_timestamp(bound.received_at, "received_at"),
                outcome=outcome,
            )
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
        """Admit an accepted applicant to the LO as an ACTIVE member.

        Runs within the authority_atomic block of the command that
        reaches it (a submission under automatic acceptance, through
        _automatic_accept, or decide_application), after that command
        has taken the organism lock and a lock on the applicant's
        Identity: the submission's actor lock, the applicant being its
        actor, or decide's FOR KEY SHARE lock. Opens no transaction and
        performs no separate authority or replay check; it writes no
        command receipt.

        Reads the applicant's Identity and any membership of theirs in
        the LO. An ENDED membership raises not-yet-implemented IL-1; any
        state other than PROPOSED is refused ("applicant already has a
        current membership"). The LO needs a founding authority basis,
        or S015ApplicationAuthorityUnavailable.

        With no membership, creates one in state PROPOSED (effective at
        the application's effective_at, known at its received_at, with
        an empty anchor fingerprint) and inserts its first transition:
        reason APPLICATION, the applicant as actor with the
        application's access epoch, context's enterer (CARE, or the
        deciding Coordinator), the application's authority decision
        reference and times, evidence "application:<uuid>", and
        context's keys and fingerprint. Then refreshes the membership.
        An existing PROPOSED membership skips this creation and first
        transition.

        The admission must not sort before the membership's head
        transition by (effective_at, occurred_at, received_at), or
        not-yet-implemented IL-3. Inserts the PROPOSED to ACTIVE
        transition: reason ADMISSION, no actor (the LO acts), context's
        enterer, authority decision reference, keys, fingerprint, times
        and composing rule, and evidence "application-decision:<uuid>".
        Records the ApplicationDecisionEffect linking the decision to
        that transition, and refreshes the membership at
        context.effective_at.
        """
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
        """Refresh a membership's governed cache, then reload it.

        Calls s015_refresh_governed_cache for the membership at
        effective_at, known at the database's clock_timestamp(), and
        reloads the instance from the database.
        """
        with connections[self._alias].cursor() as cursor:
            cursor.execute(
                "SELECT public.s015_refresh_governed_cache(%s, %s, %s, clock_timestamp())",
                ["core_organismmembership", membership.pk, effective_at],
            )
        membership.refresh_from_db(using=self._alias)

    def application_status(self, application: Application, state_at: datetime, known_at: datetime) -> str:
        """Return an application's status at state_at as known at known_at.

        Requires timezone-aware datetime values and normalises both to UTC;
        invalid values raise TypeError. This method does not qualify a viewer,
        open a transaction or take explicit row or advisory locks. Its caller
        must establish permission to expose the result.

        Returns NOT_PRESENT unless the application is effective by state_at
        and recorded by known_at. Once it is present, the existence of both a
        decision and a withdrawal raises S015ApplicationRefused, regardless of
        their times. Otherwise returns DECIDED for a decision within those two
        bounds, then WITHDRAWN for a withdrawal within them.

        Selects the engagement with the greatest engagement_ordinal within the
        same bounds, returning WAITING if none exists. A selected LO_AUTOMATIC
        engagement returns PENDING: its automatic decision does not fall within
        those bounds. No LO state fold is applied on that route, so this PENDING
        does not certify that the LO is open. A selected route other than
        LO_AUTOMATIC or LO_CSA raises S015OperationNotYetImplemented: S3-IL-CQ
        when the route ends in CQ, otherwise S3-IL-CIRCLE.

        For LO_CSA, returns UNABLE if the LO state fold at state_at and known_at
        returns CLOSED. It also returns UNABLE if the claim named by that
        engagement is absent within the two bounds, or has an ending within
        them. Otherwise returns PENDING. The row-selection queries impose no
        additional occurred_at or received_at cutoff.
        """
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
        if engagement.route == "LO_AUTOMATIC":
            return "PENDING"
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