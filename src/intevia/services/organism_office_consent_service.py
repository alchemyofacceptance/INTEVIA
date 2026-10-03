"""PKT-B R2 nominee consent for Coordinator office nominations."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
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
from src.intevia.services.organism_authority import S015Authority
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


_ACTION = OrganismAction.RECORD_OFFICE_CONSENT
_COMMAND_DOMAIN = b"INTEVIA:S015:OFFICE_CONSENT_COMMAND:v1\x00"
_COMMAND_SCHEMA = "intevia.s015.office-consent-command.v1"
_TARGET_SCHEMA = "intevia.s015.office-consent-target.v1"
_LINEAGE_DOMAIN = b"INTEVIA:S015:OFFICE_CONSENT_LINEAGE:v1\x00"
_ACTIONS = frozenset({"CONSENT", "DECLINE", "WITHDRAW"})


class S015OfficeConsentCommandError(Exception):
    pass


class S015OfficeConsentAuthorityUnavailable(S015OfficeConsentCommandError):
    pass


class S015OfficeConsentNotFound(S015OfficeConsentCommandError):
    pass


@dataclass(frozen=True, slots=True)
class RecordOfficeConsentCommand:
    credential: User
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    effective_at: datetime
    received_at: datetime
    assignment_uuid: UUID
    proposal_event_uuid: UUID
    action: str


@dataclass(frozen=True, slots=True)
class OfficeConsentCommandResult:
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
        return {}


class OfficeAppointmentConsentService(_OrganismCommandService):
    _action = _ACTION
    _not_found = S015OfficeConsentNotFound
    _authority_unavailable = S015OfficeConsentAuthorityUnavailable

    def record_office_consent(
        self, command: RecordOfficeConsentCommand,
    ) -> OfficeConsentCommandResult:
        if type(command) is not RecordOfficeConsentCommand:
            raise TypeError("command must be a RecordOfficeConsentCommand")
        with authority_atomic(self._alias):
            return self._execute(command)

    def _execute(self, command: RecordOfficeConsentCommand) -> OfficeConsentCommandResult:
        request_reference = _canonical_key(command.request_reference, "request_reference", 128)
        idempotency_key = _canonical_key(command.idempotency_key, "idempotency_key", 120)
        occurred_at = _validate_timestamp(command.occurred_at, "occurred_at")
        effective_at = _validate_timestamp(command.effective_at, "effective_at")
        received_at = _validate_timestamp(command.received_at, "received_at")
        assignment_uuid = _canonical_uuid(command.assignment_uuid, "assignment_uuid")
        proposal_event_uuid = _canonical_uuid(command.proposal_event_uuid, "proposal_event_uuid")
        if type(command.action) is not str or command.action not in _ACTIONS:
            raise ValueError("action must be CONSENT, DECLINE, or WITHDRAW")

        actor = _lock_actor(
            database_alias=self._alias,
            credential=command.credential,
            unavailable=S015OfficeConsentAuthorityUnavailable,
        )
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
            .select_for_update()
            .select_related("membership")
            .get(pk=nomination.pk)
        )
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