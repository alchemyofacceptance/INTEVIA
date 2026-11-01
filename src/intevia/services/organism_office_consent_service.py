"""PKT-B R2 / SR C4 nominee consent for LO-scoped office nominations."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from django.contrib.auth.models import User
from django.db import transaction

from core.models import (
    ContextualRoleAssignment,
    ContextualRoleAssignmentStateEvent,
    Identity,
    LivingOrganism,
    OfficeAppointmentConsent,
    OrganismMembership,
)
from core.organism_contract import OrganismAction
from src.intevia.services.organism_authority import S015Authority, S015AuthorityDenied
from src.intevia.services.organism_command_support import (
    ReplayViewer,
    S015CommandAtomicityRequired,
    S015CommandMutexUnavailable,
    S015CommandReplayMismatch,
    S015CommandSubstrateError,
    _OrganismCommandService,
    _canonical_key,
    _canonical_uuid,
    _command_payload,
    _lock_actor,
    _lock_actor_membership_context,
    _validate_timestamp,
    WithheldField,
    authority_atomic,
    acquire_organism_command_mutex,
    authority_target_fingerprint,
    canonical_json_bytes,
    canonical_timestamp,
)

if TYPE_CHECKING:
    from src.intevia.services.command_intake import BoundRequest


_ACTION = OrganismAction.RECORD_OFFICE_CONSENT
_COMMAND_DOMAIN = b"INTEVIA:S015:OFFICE_CONSENT_COMMAND:v1\x00"
_COMMAND_SCHEMA = "intevia.s015.office-consent-command.v1"
_TARGET_SCHEMA = "intevia.s015.office-consent-target.v1"
_LINEAGE_DOMAIN = b"INTEVIA:S015:OFFICE_CONSENT_LINEAGE:v1\x00"
_ACTIONS = frozenset({"CONSENT", "DECLINE", "WITHDRAW"})


class S015OfficeConsentCommandError(Exception):
    """Base class for this module's office-consent command errors."""
    support_class = "C"


class S015OfficeConsentAuthorityUnavailable(S015OfficeConsentCommandError):
    """An authority prerequisite for office consent is unavailable.

    Raised by _lock_actor when the Identity is absent, not ACTIVE,
    or has an inactive credential; by bound.verify when the actor's
    access epoch changed after intake; and by
    _lock_actor_membership_context when no ACTIVE membership with a
    head transition is available in the nomination's LO.

    Initial intake actor-resolution failures instead raise
    ValueError, before these execution checks.
    """
    pass


class S015OfficeConsentNotFound(S015OfficeConsentCommandError):
    """The named office nomination or its proposal event does not exist.

    Raised by _execute when no assignment has the command's
    assignment_uuid, or when that assignment has no sequence-1 PROPOSE
    event with the command's proposal_event_uuid.
    """
    pass


OFFICE_CONSENT_MESSAGES = {
    'SUPERSEDED': "Your consent to this office nomination could not be recorded. The office's definition was updated after you were nominated. Nothing has changed: your nomination remains on record as it was. You can still decline it. To consent, you need a new nomination under the current definition.",
    'RETIRED': 'Your consent to this office nomination could not be recorded. The office was retired after you were nominated. Nothing has changed: your nomination and its history remain on record. You can still decline it. No one can now be appointed to this office.',
    'C_RESERVED_ROOT_OFFICE': '{action} was refused. This is a reserved INTEVIA office, so its consent history cannot be changed through this organisation-office route. No consent, decline or withdrawal has been recorded by this request; your request and this refusal are on record. This office is managed by INTEVIA itself; there is nothing for you to do.',
    'C_OFFICE_CONSENT_ABSENT_OR_WITHDRAWN': "{action} was refused. The nomination's existing consent history does not permit that action. No consent, decline or withdrawal has been recorded by this request; your request and this refusal are on record. Open the nomination and check its consent history. For someone else's nomination, a first response can be consent or decline, and withdrawal requires prior consent. A self-nominee can withdraw without a separate consent.",
}


class S015OfficeConsentSuperseded(S015OfficeConsentCommandError):
    """Transaction class C (the decision rolls back; prior intake remains). SUPPORT class A."""
    code = "SR_SUPERSEDED_DEFINITION"
    support_class = "A"

    def __init__(self, *, retired: bool):
        self.retired = retired
        super().__init__(OFFICE_CONSENT_MESSAGES["RETIRED" if retired else "SUPERSEDED"])


def office_consent_support_details(error, action="Responding to this office nomination"):
    """Keep consent-specific text, then share disclosed-rule and unavailable-action explanations."""
    from src.intevia.services.organism_role_service import office_action_support_details
    if isinstance(error, S015AuthorityDenied):
        code = getattr(error.disclosed_refusal_code, "value", None)
        if error.authority_decision_reference is not None and code in {
            "C_RESERVED_ROOT_OFFICE", "C_OFFICE_CONSENT_ABSENT_OR_WITHDRAWN",
        }:
            return ("B" if code == "C_RESERVED_ROOT_OFFICE" else "A"), OFFICE_CONSENT_MESSAGES[code].format(action=action)
    category = getattr(error, "support_class", "C")
    if not isinstance(error, S015AuthorityDenied) and category != "C":
        return category, str(error)
    return office_action_support_details(error, action)


@dataclass(frozen=True, slots=True)
class RecordOfficeConsentCommand:
    """Inputs to record_office_consent.

    The actor's credential; the request reference and idempotency key; the
    occurred and effective times; the nomination's assignment UUID and its
    proposal event UUID; and the action, CONSENT, DECLINE or WITHDRAW.
    INTEVIA assigns received_at at admission; a supplied value is refused.
    """
    credential: User
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    effective_at: datetime
    received_at: datetime | None = field(default=None, kw_only=True)
    assignment_uuid: UUID
    proposal_event_uuid: UUID
    action: str


@dataclass(frozen=True, slots=True)
class OfficeConsentCommandResult:
    """Result of a recorded office consent.

    Carries the assignment, proposal event and consent UUIDs, the
    action, ordinal, recorded_at read back from the database,
    lineage reference, and authority and authority-decision
    references.

    The result declares no audience-specific field restrictions. The
    shared service passes both new and replayed results through
    serve_replay; that helper owns the serving and withholding
    rules.
    """
    assignment_uuid: UUID
    proposal_event_uuid: UUID
    consent_uuid: UUID
    action: str
    ordinal: int
    recorded_at: datetime
    lineage_reference: str
    authority_reference: str | WithheldField
    authority_decision_reference: str

    def receipt_payload(self) -> dict[str, object]:
        """Return the receipt form: UUIDs as strings, recorded_at as a
        canonical timestamp, and the other fields as they are. The shared
        service stores it before serve_replay applies any withholding."""
        return {
            "action": self.action,
            "assignment_uuid": str(self.assignment_uuid),
            "authority_decision_reference": self.authority_decision_reference,
            "authority_reference": self.authority_reference,
            "consent_uuid": str(self.consent_uuid),
            "lineage_reference": self.lineage_reference,
            "ordinal": self.ordinal,
            "proposal_event_uuid": str(self.proposal_event_uuid),
            "recorded_at": canonical_timestamp(self.recorded_at),
        }

    @classmethod
    def replay_field_audiences(cls, result: "OfficeConsentCommandResult") -> dict[str, str]:
        """Return an empty mapping: no field is restricted to an audience.
        Unlike the governance results, this does not check the result's
        type."""
        return {}


class OfficeAppointmentConsentService(_OrganismCommandService):
    """The command service for a nominee's consent to an LO-scoped office
    nomination (RECORD_OFFICE_CONSENT).

    Declares, for the shared _OrganismCommandService, the action
    RECORD_OFFICE_CONSENT, S015OfficeConsentNotFound as its not-found
    exception and S015OfficeConsentAuthorityUnavailable as its unavailable
    exception, and rebuilds replayed results from receipt payloads. It
    writes OfficeAppointmentConsent rows and receipts; it does not change
    the nominated assignment's state.
    """
    _action = _ACTION
    _not_found = S015OfficeConsentNotFound
    _authority_unavailable = S015OfficeConsentAuthorityUnavailable

    def record_office_consent(
        self, command: RecordOfficeConsentCommand,
    ) -> OfficeConsentCommandResult:
        """Run the governed consent route and attach SUPPORT details to every error."""
        try:
            return self._record_office_consent(command)
        except Exception as exc:
            try:
                from src.intevia.services.organism_role_service import OFFICE_ACTION_NAMES
                exc.support_class, exc.support_message = office_consent_support_details(
                    exc, OFFICE_ACTION_NAMES.get(command.action, "Responding to this office nomination"))
            except Exception:
                pass
            raise

    def _record_office_consent(
        self, command: RecordOfficeConsentCommand,
    ) -> OfficeConsentCommandResult:
        """Admit an office-consent request, then execute it.

        First require PostgreSQL, the default writer alias, no open
        atomic block, autocommit and no pending rollback, in that order.
        A non-PostgreSQL writer raises S015CommandMutexUnavailable; a
        non-default alias raises IntakeAdmissionTechnicalFailure;
        unsupported transaction state raises
        S015CommandAtomicityRequired. An inactive RECORD_OFFICE_CONSENT
        entry raises IntakeAdmissionTechnicalFailure.

        intake_shape requires this command type, canonical keys, aware
        timestamps, UUID fields and a CONSENT, DECLINE or WITHDRAW
        action. It rejects a supplied received_at. Shape errors raise
        TypeError or ValueError. Resolve the actor by credential, with
        ValueError if it cannot identify an Identity, then call
        admit_request.

        Admission commits or recovers a binding with its original
        received_at before _execute_record_office_consent runs in its
        own authority_atomic scope. Admission and execution exceptions
        propagate; a later command failure does not undo that binding.
        """
        from src.intevia.services.command_intake import (
            ACTIVE_ENTRIES,
            IntakeAdmissionTechnicalFailure,
            IntakeEntry,
            _require_admission_state,
            _resolve_actor,
            admit_request,
            intake_shape,
        )

        entry = IntakeEntry.RECORD_OFFICE_CONSENT
        _require_admission_state(self._alias, entry)
        if entry not in ACTIVE_ENTRIES:
            raise IntakeAdmissionTechnicalFailure("RECORD_OFFICE_CONSENT is not active")
        shape = intake_shape(entry, command)
        actor_ref = _resolve_actor(self._alias, command.credential)
        bound = admit_request(self._alias, entry, actor_ref, shape)
        return self._execute_record_office_consent(command, bound)

    def _execute_record_office_consent(
        self, command: RecordOfficeConsentCommand, bound: BoundRequest,
    ) -> OfficeConsentCommandResult:
        """Execute an admitted consent record within authority_atomic."""
        with authority_atomic(self._alias, received_at=bound.received_at):
            return self._execute(command, bound=bound)

    def _execute(
        self, command: RecordOfficeConsentCommand, *, bound: BoundRequest,
    ) -> OfficeConsentCommandResult:
        """Record CONSENT, DECLINE or WITHDRAW on an office nomination.

        Called inside authority_atomic for an admitted request. In
        order:
        - validate canonical keys, UUIDs and aware times, taking
          received_at from the bound request; an invalid action raises
          ValueError;
        - lock the actor's Identity and credential, verify the binding
          and actor epoch, then take the actor/action/idempotency-key
          mutex;
        - read the assignment and its named sequence-1 PROPOSE event,
          without row locks; missing records raise
          S015OfficeConsentNotFound before authority qualification;
        - form the target and command fingerprints, then serve a
          matching completed receipt if present. A receipt-reference or
          fingerprint mismatch raises S015CommandReplayMismatch;
        - for fresh work, lock the LO and the actor's ACTIVE membership
          context, including its active role heads;
        - qualify through S015Authority with effective_at from the
          command and evaluated_at equal to the bound received_at. The
          provider checks nominee identity and the permitted consent
          transition;
        - lock the nomination, re-read the proposal, then insert the
          next consent ordinal for the assignment, with the actor as
          PARTY in STANDING capacity and as enterer.

        Read the consent back and derive its s015l1 lineage reference
        from _LINEAGE_DOMAIN and the canonical fields, including
        recorded_at. _finalise stores the receipt, rebuilds the result
        and serves it. The assignment's state is not changed. The
        injected clock supplies replay-viewer time, not this command's
        received_at.

        Completed replay returns before the LO/membership locks and
        fresh authority qualification; decoding and serving errors
        propagate. A caught S015AuthorityDenied is raised after
        successful atomic exit. Other failures follow the shared
        transaction and contention rules. The earlier committed intake
        binding is outside this block.
        """
        request_reference = _canonical_key(command.request_reference, "request_reference", 128)
        idempotency_key = _canonical_key(command.idempotency_key, "idempotency_key", 120)
        occurred_at = _validate_timestamp(command.occurred_at, "occurred_at")
        effective_at = _validate_timestamp(command.effective_at, "effective_at")
        received_at = _validate_timestamp(bound.received_at, "received_at")
        assignment_uuid = _canonical_uuid(command.assignment_uuid, "assignment_uuid")
        proposal_event_uuid = _canonical_uuid(command.proposal_event_uuid, "proposal_event_uuid")
        if type(command.action) is not str or command.action not in _ACTIONS:
            raise ValueError("action must be CONSENT, DECLINE, or WITHDRAW")

        actor = _lock_actor(
            database_alias=self._alias,
            credential=command.credential,
            unavailable=S015OfficeConsentAuthorityUnavailable,
        )
        from src.intevia.services.command_intake import IntakeEntry

        bound.verify(command, actor, IntakeEntry.RECORD_OFFICE_CONSENT, self._alias)
        acquire_organism_command_mutex(
            database_alias=self._alias,
            actor_identity_id=actor.identity_id,
            action=_ACTION,
            idempotency_key=idempotency_key,
        )
        try:
            nomination = (
                ContextualRoleAssignment.objects.using(self._alias)
                .select_related("membership__living_organism")
                .get(assignment_uuid=assignment_uuid)
            )
        except ContextualRoleAssignment.DoesNotExist as exc:
            raise S015OfficeConsentNotFound("office nomination not found") from exc

        organism = nomination.membership.living_organism
        try:
            proposal = ContextualRoleAssignmentStateEvent.objects.using(self._alias).get(
                event_uuid=proposal_event_uuid,
                assignment=nomination,
                sequence=1,
                action="PROPOSE",
            )
        except ContextualRoleAssignmentStateEvent.DoesNotExist as exc:
            raise S015OfficeConsentNotFound("office nomination proposal not found") from exc

        target: dict[str, object] = {
            "action": _ACTION.value,
            "assignment_uuid": str(assignment_uuid),
            "event_action": command.action,
            "living_organism_id": str(organism.organism_id),
            "membership_uuid": str(nomination.membership.membership_uuid),
            "proposal_event_uuid": str(proposal_event_uuid),
            "schema": _TARGET_SCHEMA,
        }
        target_fingerprint = authority_target_fingerprint(target)
        command_fingerprint = _command_payload(
            domain=_COMMAND_DOMAIN,
            schema=_COMMAND_SCHEMA,
            action=_ACTION,
            actor_identity_id=actor.identity_id,
            actor_access_epoch=actor.access_epoch,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
            target=target,
            command={
                "action": command.action,
                "assignment_uuid": str(assignment_uuid),
                "effective_at": canonical_timestamp(effective_at),
                "proposal_event_uuid": str(proposal_event_uuid),
                "received_at": canonical_timestamp(received_at),
                "schema": _COMMAND_SCHEMA,
            },
        )
        replay = self._attempt_replay(
            actor=actor,
            request_reference=request_reference,
            payload_fingerprint=command_fingerprint,
            idempotency_key=idempotency_key,
            viewer=ReplayViewer(actor.pk, actor.identity_id, self._clock()),
        )
        if replay is not None:
            return replay

        organism = (
            LivingOrganism.objects.using(self._alias)
            .select_for_update()
            .get(pk=nomination.membership.living_organism_id)
        )
        actor_membership, actor_role_refs = _lock_actor_membership_context(
            database_alias=self._alias,
            actor_identity_id=actor.identity_id,
            living_organism_id=organism.organism_id,
            unavailable=S015OfficeConsentAuthorityUnavailable,
        )

        decision = self._evaluate_authority(
            actor=actor,
            actor_membership=actor_membership,
            actor_role_refs=actor_role_refs,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            target_fingerprint=target_fingerprint,
            received_at=received_at,
            effective_at=effective_at,
            target=target,
            living_organism_id=organism.organism_id,
            assignment_uuid=assignment_uuid,
        )

        nomination = (
            ContextualRoleAssignment.objects.using(self._alias)
            # No of=: also lock the joined definition so AMEND serialises with the stale check.
            .select_for_update()
            .select_related("membership", "role_definition")
            .get(pk=nomination.pk)
        )
        role = nomination.role_definition
        if command.action == "CONSENT" and role.code != "COORDINATOR" and not role.active:
            raise S015OfficeConsentSuperseded(retired=role.retired_by_decision_reference is not None)
        proposal = ContextualRoleAssignmentStateEvent.objects.using(self._alias).get(
            pk=proposal.pk,
            assignment=nomination,
            sequence=1,
            action="PROPOSE",
        )
        previous_ordinal = OfficeAppointmentConsent.objects.using(self._alias).filter(
            assignment=nomination,
        ).order_by("-ordinal").values_list("ordinal", flat=True).first() or 0
        consent = OfficeAppointmentConsent.objects.using(self._alias).create(
            assignment=nomination,
            proposal_event=proposal,
            nominee_membership=nomination.membership,
            action=command.action,
            ordinal=previous_ordinal + 1,
            actor=actor,
            actor_state="PARTY",
            actor_capacity="STANDING",
            actor_access_epoch=actor.access_epoch,
            enterer=actor,
            authority_decision_reference=decision.authority_decision_reference,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
            effective_at=effective_at,
            received_at=received_at,
        )
        consent.refresh_from_db(using=self._alias)
        lineage_reference = "s015l1:" + hashlib.sha256(
            _LINEAGE_DOMAIN + canonical_json_bytes({
                "action": consent.action,
                "actor_access_epoch": actor.access_epoch,
                "actor_identity_id": str(actor.identity_id),
                "assignment_uuid": str(assignment_uuid),
                "consent_uuid": str(consent.consent_uuid),
                "effective_at": canonical_timestamp(effective_at),
                "proposal_event_uuid": str(proposal_event_uuid),
                "recorded_at": canonical_timestamp(consent.recorded_at),
                "schema": _COMMAND_SCHEMA,
            })
        ).hexdigest()
        result = OfficeConsentCommandResult(
            assignment_uuid=assignment_uuid,
            proposal_event_uuid=proposal_event_uuid,
            consent_uuid=consent.consent_uuid,
            action=consent.action,
            ordinal=consent.ordinal,
            recorded_at=consent.recorded_at,
            lineage_reference=lineage_reference,
            authority_reference=decision.authority_reference,
            authority_decision_reference=decision.authority_decision_reference,
        )
        return self._finalise(
            actor=actor,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            payload_fingerprint=command_fingerprint,
            result=result,
        )

    def _replay_result(self, payload: object) -> OfficeConsentCommandResult:
        """Rebuild an office-consent result from a receipt payload.

        Require a dict of exactly that type, else raise
        S015CommandReplayMismatch. Construct the three UUIDs and parse
        recorded_at with datetime.fromisoformat. Missing fields and
        TypeError or ValueError from construction become
        S015CommandReplayMismatch; other exceptions propagate.

        Take action, ordinal and references as stored, without
        validating their types or values. This decoder does not require
        canonical timestamp spelling or a timezone, or verify the
        recorded consent.
        """
        if type(payload) is not dict:
            raise S015CommandReplayMismatch("consent receipt payload is malformed")
        try:
            return OfficeConsentCommandResult(
                assignment_uuid=UUID(payload["assignment_uuid"]),
                proposal_event_uuid=UUID(payload["proposal_event_uuid"]),
                consent_uuid=UUID(payload["consent_uuid"]),
                action=payload["action"],
                ordinal=payload["ordinal"],
                recorded_at=datetime.fromisoformat(payload["recorded_at"]),
                lineage_reference=payload["lineage_reference"],
                authority_reference=payload["authority_reference"],
                authority_decision_reference=payload["authority_decision_reference"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise S015CommandReplayMismatch("consent receipt payload is malformed") from exc


__all__ = [
    "OfficeAppointmentConsentService",
    "OfficeConsentCommandResult",
    "RecordOfficeConsentCommand",
    "S015OfficeConsentAuthorityUnavailable",
    "S015OfficeConsentCommandError",
    "S015OfficeConsentNotFound",
]