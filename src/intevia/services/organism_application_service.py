"""PKT-B S3 applicant commands; Coordinator acts are reserved for round 2b."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Callable
from uuid import UUID, uuid4

from django.contrib.auth.models import User
from django.db import connections, transaction

from core.models import (
    Application, ApplicationDecision, ApplicationWithdrawal, Identity, IdentityTransition,
    LivingOrganism, OrganismCommandReceipt, OrganismMembership,
)
from core.organism_contract import OrganismAction
from src.intevia.services.organism_authority import (
    S015Authority, S015OwnApplicantAuthorityRequest, authority_target_fingerprint,
    canonical_json_bytes, canonical_timestamp,
)
from src.intevia.services.organism_command_support import (
    S015CommandReplayMismatch, _canonical_key, _canonical_uuid, _command_payload,
    _lock_actor, _validate_timestamp, acquire_organism_command_mutex,
)
from src.intevia.services.organism_membership_service import S015OperationNotYetImplemented


_COMMAND_DOMAIN = b"INTEVIA:S015:APPLICATION_COMMAND:v1\x00"
_PAIR_DOMAIN = b"INTEVIA:S015:OPEN_APPLICATION_PAIR:v1\x00"


class S015ApplicationError(Exception):
    pass


class S015ApplicationAuthorityUnavailable(S015ApplicationError):
    pass


class S015ApplicationRefused(S015ApplicationError):
    pass


class S015ApplicationNotFound(S015ApplicationError):
    pass


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
class ApplicationCommandResult:
    application_uuid: UUID
    application_id: int
    action: OrganismAction
    recorded_at: datetime
    lineage_reference: str
    authority_reference: str
    authority_decision_reference: str

    def receipt_payload(self) -> dict[str, object]:
        return {
            "application_uuid": str(self.application_uuid),
            "application_id": self.application_id,
            "action": self.action.value,
            "recorded_at": canonical_timestamp(self.recorded_at),
            "lineage_reference": self.lineage_reference,
            "authority_reference": self.authority_reference,
            "authority_decision_reference": self.authority_decision_reference,
        }


def applicant_eligible(
    identity: Identity, effective_at: datetime, recorded_at: datetime, *, database_alias: str = "default",
) -> bool:
    """Read account history using its one time per transition, occurred_at, not B-G2's separate recorded time."""
    history = IdentityTransition.objects.using(database_alias).filter(identity=identity)
    if not history.filter(action=IdentityTransition.Action.PROVISION, occurred_at__lte=effective_at).exists():
        return False
    latest = history.filter(occurred_at__lte=recorded_at).order_by("-occurred_at", "-pk").first()
    return latest is not None and latest.resulting_state == Identity.AccessState.ACTIVE


def _result_from_receipt(payload: object) -> ApplicationCommandResult:
    if not isinstance(payload, dict):
        raise S015ApplicationError("application receipt payload is malformed")
    return ApplicationCommandResult(
        application_uuid=UUID(payload["application_uuid"]),
        application_id=int(payload["application_id"]),
        action=OrganismAction(payload["action"]),
        recorded_at=datetime.fromisoformat(payload["recorded_at"].replace("Z", "+00:00")),
        lineage_reference=payload["lineage_reference"],
        authority_reference=payload["authority_reference"],
        authority_decision_reference=payload["authority_decision_reference"],
    )


class OrganismApplicationService:
    def __init__(
        self, *, authority: S015Authority, clock: Callable[[], datetime], database_alias: str = "default",
    ) -> None:
        if not isinstance(authority, S015Authority) or authority.database_alias != database_alias:
            raise TypeError("matching S015 authority is required")
        if not callable(clock):
            raise TypeError("clock is required")
        self._authority = authority
        self._clock = clock
        self._alias = database_alias

    def _receipt_or_none(
        self, *, actor: Identity, action: OrganismAction, request_reference: str,
        idempotency_key: str, fingerprint: str,
    ) -> ApplicationCommandResult | None:
        receipt = OrganismCommandReceipt.objects.using(self._alias).select_for_update().filter(
            actor=actor, action=action.value, idempotency_key=idempotency_key,
        ).first()
        if receipt is None:
            return None
        if receipt.request_reference != request_reference or receipt.payload_fingerprint != fingerprint:
            raise S015CommandReplayMismatch("completed receipt does not match request")
        return _result_from_receipt(receipt.result_payload)

    def _authorise(
        self, *, actor: Identity, action: OrganismAction, organism: LivingOrganism,
        application: Application | None, target: dict[str, object],
        request_reference: str, idempotency_key: str, received_at: datetime,
    ):
        return self._authority.qualify(S015OwnApplicantAuthorityRequest(
            database_alias=self._alias,
            actor_pk=actor.pk,
            actor_identity_id=actor.identity_id,
            actor_credential_pk=actor.credential_id,
            actor_access_epoch=actor.access_epoch,
            action=action,
            command_subtype=action.value,
            target_fingerprint=authority_target_fingerprint(target),
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            evaluated_at=received_at,
            living_organism_pk=organism.pk,
            application_pk=None if application is None else application.pk,
        ))

    def _finish(
        self, *, actor: Identity, action: OrganismAction, request_reference: str,
        idempotency_key: str, fingerprint: str, application: Application, recorded_at: datetime,
        authority_reference: str, authority_decision_reference: str,
    ) -> ApplicationCommandResult:
        lineage_reference = "s015l1:" + hashlib.sha256(
            _COMMAND_DOMAIN + canonical_json_bytes({
                "action": action.value, "actor": actor.identity_id, "application": application.application_uuid,
                "fingerprint": fingerprint,
            })
        ).hexdigest()
        result = ApplicationCommandResult(
            application_uuid=application.application_uuid,
            application_id=application.pk,
            action=action,
            recorded_at=recorded_at,
            lineage_reference=lineage_reference,
            authority_reference=authority_reference,
            authority_decision_reference=authority_decision_reference,
        )
        OrganismCommandReceipt.objects.using(self._alias).create(
            actor=actor, action=action.value, idempotency_key=idempotency_key,
            request_reference=request_reference, payload_fingerprint=fingerprint,
            authority_decision_reference=authority_decision_reference,
            lineage_reference=lineage_reference, result_payload=result.receipt_payload(),
        )
        return result

    def _pair_lock(self, actor: Identity, organism: LivingOrganism) -> None:
        key = int.from_bytes(hashlib.sha256(
            _PAIR_DOMAIN + actor.identity_id.bytes + organism.organism_id.bytes,
        ).digest()[:8], "big", signed=True)
        with connections[self._alias].cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [key])

    def submit_application(self, command: SubmitApplicationCommand) -> ApplicationCommandResult:
        with transaction.atomic(using=self._alias):
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
                                        "predecessor": predecessor_uuid},
            )
            replay = self._receipt_or_none(
                actor=actor, action=OrganismAction.SUBMIT_APPLICATION, request_reference=request_reference,
                idempotency_key=idempotency_key, fingerprint=fingerprint,
            )
            if replay is not None:
                return replay
            decision = self._authorise(
                actor=actor, action=OrganismAction.SUBMIT_APPLICATION, organism=organism, application=None,
                target=target, request_reference=request_reference, idempotency_key=idempotency_key,
                received_at=received_at,
            )
            record_time = _validate_timestamp(self._clock(), "record_time")
            if not applicant_eligible(actor, effective_at, record_time, database_alias=self._alias):
                raise S015ApplicationRefused("applicant account is not eligible")
            membership = OrganismMembership.objects.using(self._alias).filter(
                identity=actor, living_organism=organism,
            ).first()
            if membership is not None:
                if membership.state in {"ACTIVE", "PROBATIONARY"}:
                    raise S015ApplicationRefused("applicant is already a current member")
                if membership.state == "ENDED":
                    raise S015OperationNotYetImplemented("rejoin_by_application", "IL-1", "S4")
            self._pair_lock(actor, organism)
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
                if predecessor is None or not ApplicationWithdrawal.objects.using(self._alias).filter(
                    application=predecessor, effective_at__lte=effective_at, recorded_at__lte=record_time,
                ).exists():
                    raise S015ApplicationRefused("predecessor is not an eligible withdrawn application")
            application = Application.objects.using(self._alias).create(
                applicant=actor, living_organism=organism, predecessor_reference=predecessor,
                actor=actor, enterer=actor, occurred_at=occurred_at, effective_at=effective_at,
                received_at=received_at,
            )
            application.refresh_from_db(using=self._alias)
            if not applicant_eligible(actor, effective_at, application.recorded_at, database_alias=self._alias):
                raise S015ApplicationRefused("applicant account is not eligible at record time")
            return self._finish(
                actor=actor, action=OrganismAction.SUBMIT_APPLICATION, request_reference=request_reference,
                idempotency_key=idempotency_key, fingerprint=fingerprint, application=application,
                recorded_at=application.recorded_at, authority_reference=decision.authority_reference,
                authority_decision_reference=decision.authority_decision_reference,
            )

    def withdraw_application(self, command: WithdrawApplicationCommand) -> ApplicationCommandResult:
        with transaction.atomic(using=self._alias):
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
            )
            if replay is not None:
                return replay
            decision = self._authorise(
                actor=actor, action=OrganismAction.WITHDRAW_APPLICATION,
                organism=application.living_organism, application=application,
                target=target, request_reference=request_reference, idempotency_key=idempotency_key,
                received_at=received_at,
            )
            if ApplicationDecision.objects.using(self._alias).filter(application=application).exists():
                raise S015ApplicationRefused("application already decided")
            if ApplicationWithdrawal.objects.using(self._alias).filter(application=application).exists():
                raise S015ApplicationRefused("application already withdrawn")
            if application.engagements.filter(route__in=["LO_CQ", "CIRCLE_CQ"]).exists():
                raise S015OperationNotYetImplemented("withdraw_cq_application", "S3-IL-CQ", "DISCUSSION")
            withdrawal = ApplicationWithdrawal.objects.using(self._alias).create(
                application=application, actor=actor, enterer=actor,
                occurred_at=occurred_at, effective_at=effective_at, received_at=received_at,
            )
            withdrawal.refresh_from_db(using=self._alias)
            return self._finish(
                actor=actor, action=OrganismAction.WITHDRAW_APPLICATION, request_reference=request_reference,
                idempotency_key=idempotency_key, fingerprint=fingerprint, application=application,
                recorded_at=withdrawal.recorded_at, authority_reference=decision.authority_reference,
                authority_decision_reference=decision.authority_decision_reference,
            )