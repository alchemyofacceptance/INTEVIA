"""PKT-B S3 applicant and Coordinator application commands."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable
from uuid import UUID, uuid4

from django.contrib.auth.models import User
from django.db import connections, transaction

from core.models import (
    Application, ApplicationDecision, ApplicationDecisionEffect, ApplicationEngagement,
    ApplicationLineage, ApplicationLineageEvent,
    ApplicationWithdrawal, CSAAssignment, CSAAssignmentEnd, ContextualRoleAssignment,
    Identity, IdentityTransition, LivingOrganism, OrganismCommandReceipt, OrganismMembership,
    OrganismMembershipTransition,
)
from core.organism_contract import OrganismAction
from src.intevia.services.organism_authority import (
    QualifiedS015AuthorityDecision, S015Authority, S015OwnApplicantAuthorityRequest, authority_target_fingerprint,
    canonical_json_bytes, canonical_timestamp,
)
from src.intevia.services.organism_command_support import (
    S015CommandReplayMismatch, _canonical_key, _canonical_uuid, _command_payload,
    _empty_anchor_fingerprint, _lock_actor, _lock_actor_membership_context,
    _resulting_temporal_basis, _validate_timestamp, _authority_request,
    acquire_organism_command_mutex,
)
from src.intevia.services.organism_membership_service import S015OperationNotYetImplemented
from src.intevia.services.organism_role_service import OrganismRoleService


_COMMAND_DOMAIN = b"INTEVIA:S015:APPLICATION_COMMAND:v1\x00"
_PAIR_DOMAIN = b"INTEVIA:S015:OPEN_APPLICATION_PAIR:v1\x00"
_SETTINGS_BASIS = "DEFAULT (no settings store): cq_active=No, application_sole_participant_delegation=No"
_DUE_SOURCE = "DEFAULT (no settings store)"


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
    application_id: int
    action: OrganismAction
    recorded_at: datetime
    lineage_reference: str
    authority_reference: str
    authority_decision_reference: str
    record_uuid: UUID | None = None
    completion_due_at: datetime | None = None
    completion_due_source: str | None = None

    def receipt_payload(self) -> dict[str, object]:
        return {
            "application_uuid": str(self.application_uuid),
            "application_id": self.application_id,
            "action": self.action.value,
            "recorded_at": canonical_timestamp(self.recorded_at),
            "lineage_reference": self.lineage_reference,
            "authority_reference": self.authority_reference,
            "authority_decision_reference": self.authority_decision_reference,
            "record_uuid": None if self.record_uuid is None else str(self.record_uuid),
            "completion_due_at": None if self.completion_due_at is None else canonical_timestamp(self.completion_due_at),
            "completion_due_source": self.completion_due_source,
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
            "STANDING", enterer.pk, "TRANSPORT",
            "NA_TRANSPORT", None, "NA_TRANSPORT", None,
            None, "NONE", None, predecessor_commitment, None, parts_commitment,
            None if actor is None else actor_access_epoch, basis_id,
            authority_decision_reference, evidence_reference, request_reference, idempotency_key,
            payload_fingerprint, lineage_reference, temporal_kind, temporal_reference,
            occurred_at, effective_at, received_at, prior_state, resulting_state, reason_class,
        ])
        return cursor.fetchone()[0]


def _lock_owning_organism(database_alias: str, organism_id) -> None:
    """Take the owning organism's lock ahead of the application lock (S3-FU F2-6).

    Lock order for every application act: identities, then the owning organism, then the application, then its lineage.
    Appending to the application's lineage takes the organism lock through its trigger; taking it here, first, keeps every
    act in the same order. A second request in the same transaction returns at once.
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
            "STANDING", enterer.pk, "TRANSPORT", "NA_TRANSPORT", None, "NA_TRANSPORT", None,
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
            application_id=application.pk,
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
        return result

    def _pair_lock(self, actor: Identity, organism: LivingOrganism) -> None:
        key = int.from_bytes(hashlib.sha256(
            _PAIR_DOMAIN + actor.identity_id.bytes + organism.organism_id.bytes,
        ).digest()[:8], "big", signed=True)
        with connections[self._alias].cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [key])

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
        if action is OrganismAction.DECIDE_APPLICATION:
            # Lock order for every application act (S3-FU F2-6): identities first -- the actor, then for a decision the
            # applicant's key share (an ACCEPT's membership rows take it through their foreign keys, and the withdrawal holds
            # the applicant FOR UPDATE as its actor lock) -- then the owning organism, then the application, then its lineage.
            # The key share is taken here, before the actor-context lock, so no decision holds an organism-level lock while it
            # waits for the applicant.
            with connections[self._alias].cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM public.core_identity WHERE id = %s FOR KEY SHARE", [application.applicant_id],
                )
        actor_membership, role_refs = _lock_actor_membership_context(
            database_alias=self._alias, actor_identity_id=actor.identity_id,
            living_organism_id=application.living_organism.organism_id,
            unavailable=S015ApplicationAuthorityUnavailable,
        )
        target = {"action": action.value, "application": application_uuid,
                  "organism": application.living_organism.organism_id}
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
        )
        if replay is not None:
            return replay
        decision = self._authority.qualify(_authority_request(
            database_alias=self._alias, actor=actor, actor_membership=actor_membership,
            actor_role_head_refs=role_refs, action=action, request_reference=request_reference,
            idempotency_key=idempotency_key, received_at=received_at,
            target_fingerprint=authority_target_fingerprint(target),
        ))
        _lock_owning_organism(self._alias, application.living_organism.organism_id)
        application = Application.objects.using(self._alias).select_for_update().get(pk=application.pk)
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
                if predecessor is None:
                    raise S015ApplicationRefused("predecessor is not an eligible withdrawn application")
            application = Application.objects.using(self._alias).create(
                applicant=actor, living_organism=organism, predecessor_reference=predecessor,
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
            _lock_owning_organism(self._alias, application.living_organism.organism_id)
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
        with transaction.atomic(using=self._alias):
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
            ApplicationEngagement.objects.using(self._alias).create(
                application=context.application, engagement_ordinal=1, route="LO_CSA",
                deciding_party_id=context.application.living_organism_id,
                engagement_reference=str(claim.assignment_uuid), settings_basis=_SETTINGS_BASIS,
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
        with transaction.atomic(using=self._alias):
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
                engagement_reference=str(replacement.assignment_uuid), settings_basis=_SETTINGS_BASIS,
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
        with transaction.atomic(using=self._alias):
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
                executor=context.actor, enterer=context.actor, occurred_at=context.occurred_at,
                effective_at=context.effective_at, received_at=context.received_at,
            )
            decision.refresh_from_db(using=self._alias)
            if outcome == "ACCEPT":
                self._admit(context, decision)
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

    def _admit(self, context: _CoordinatorContext, decision: ApplicationDecision) -> None:
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
                enterer=context.actor, basis_id=basis_id, prior_state=None, resulting_state="PROPOSED",
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
            actor=None, enterer=context.actor, basis_id=basis_id,
            prior_state="PROPOSED", resulting_state="ACTIVE", reason_class="ADMISSION",
            evidence_reference=f"application-decision:{decision.decision_uuid}",
            authority_decision_reference=context.decision.authority_decision_reference,
            request_reference=context.request_reference, idempotency_key=context.idempotency_key,
            payload_fingerprint=context.fingerprint, occurred_at=context.occurred_at,
            effective_at=context.effective_at, received_at=context.received_at,
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