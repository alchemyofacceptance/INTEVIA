"""Governed S013 PROFILE_EFFECT command services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from uuid import UUID, uuid4

from django.contrib.auth.models import User
from django.db import IntegrityError, connections, transaction

from core.models import (
    Identity,
    ProfileEffectProjectionDisposition,
    ProfileEffectProposalLineage,
    ProfileEffectProposalTransition,
)
from src.intevia.services.profile_effect_authority import (
    ProjectionAuthority,
    ProjectionAuthorityNotAuthorised,
    ProjectionAuthorityRequest,
    ProjectionAuthorityResponse,
    ProposalAuthority,
    ProposalAuthorityNotAuthorised,
    ProposalAuthorityRequest,
    ProposalAuthorityResponse,
    projection_decision_reference_for,
    proposal_decision_reference_for,
)
from src.intevia.services.profile_effect_contract import (
    CONTRACT_VERSION,
    CreateServiceSubmissionProposalCommand,
    EffectType,
    ProfileEffectProjectionCommandReceipt,
    ProfileEffectProjectionDispositionCommand,
    ProfileEffectProposalCommandReceipt,
    ProfileEffectProposalCorrectionCommand,
    PROJECTION_COMMAND_SCHEMA,
    PROPOSAL_COMMAND_SCHEMA,
    ProjectionAction,
    ProjectionState,
    ProposalAction,
    ProposalState,
    SubjectRelation,
    bounded_reference,
    canonical_timestamp,
    projection_authority_target_payload,
    projection_authority_target_reference,
    projection_command_fingerprint,
    projection_lineage_reference,
    proposal_authority_target_payload,
    proposal_authority_target_reference,
    proposal_command_fingerprint,
    proposal_correction_target_payload,
    proposal_lineage_reference,
)
from src.intevia.services.service_activity_read_service import (
    ServiceActivityReadService,
    ServiceSubmissionQualificationDTO,
)


_CREATE_RACE_CONSTRAINTS = frozenset(
    {"s013_pe_lineage_semantic_uniq", "s013_pe_current_survivor_uniq"}
)
_PROPOSAL_IDEM_CONSTRAINT = "s013_pe_prop_actor_action_idem_uniq"
_PROPOSAL_APPEND_RACE_CONSTRAINTS = frozenset(
    {
        _PROPOSAL_IDEM_CONSTRAINT,
        "s013_pe_prop_sequence_uniq",
        "s013_pe_prop_initial_uniq",
        "s013_pe_prop_successor_uniq",
    }
)
_PROJECTION_IDEM_CONSTRAINT = "s013_pe_proj_actor_action_idem_uniq"
_PROJECTION_APPEND_RACE_CONSTRAINTS = frozenset(
    {
        _PROJECTION_IDEM_CONSTRAINT,
        "s013_pe_proj_sequence_uniq",
        "s013_pe_proj_initial_uniq",
        "s013_pe_proj_successor_uniq",
    }
)


class ProfileEffectCommandError(Exception):
    pass


class ProfileEffectConflict(ProfileEffectCommandError):
    pass


class ProfileEffectCrossEpochConflict(ProfileEffectConflict):
    pass


class ProfileEffectPayloadConflict(ProfileEffectConflict):
    pass


class ProfileEffectMalformedReplay(ProfileEffectConflict):
    pass


class ProfileEffectValidationError(ProfileEffectCommandError):
    pass


class ProfileEffectNotFound(ProfileEffectCommandError):
    pass


class ProfileEffectActorError(ProfileEffectCommandError):
    pass


class ProfileEffectLifecycleError(ProfileEffectCommandError):
    pass


def _is_postgresql(alias: str) -> bool:
    return "postgresql" in connections[alias].settings_dict.get("ENGINE", "")


def _validate_uuid(value: object, name: str) -> UUID:
    if not isinstance(value, UUID):
        raise ProfileEffectValidationError(f"{name} must be a UUID")
    return value


def _validate_reference(value: str, name: str, maximum: int) -> str:
    try:
        return bounded_reference(value, name, maximum)
    except ValueError as exc:
        raise ProfileEffectValidationError(str(exc)) from exc


def _validate_occurred_at(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ProfileEffectValidationError("occurred_at must be timezone-aware")
    return value.astimezone(timezone.utc)


def _proposal_command_payload(*, action: ProposalAction, database_alias: str, actor_identity_id: UUID, actor_access_epoch: int, request_reference: str, idempotency_key: str, occurred_at: datetime, qualification: ServiceSubmissionQualificationDTO | None = None, lineage_id: UUID | None = None, expected_head_transition_pk: int | None = None, expected_head_lineage_reference: str | None = None, subject_identity_id: UUID) -> dict[str, object]:
    payload = {
        "action": action.value,
        "actor_access_epoch": actor_access_epoch,
        "actor_identity_id": str(actor_identity_id),
        "contract_version": CONTRACT_VERSION,
        "database_alias": database_alias,
        "effect_type": EffectType.SERVICE_ACTIVITY_SUBMISSION_TRANSITION_RECORDED.value,
        "idempotency_key": idempotency_key,
        "occurred_at": canonical_timestamp(occurred_at),
        "request_reference": request_reference,
        "schema": "intevia.s013.profile-effect.proposal-command.v1",
        "subject_identity_id": str(subject_identity_id),
        "subject_relation": SubjectRelation.IMMUTABLE_ACTIVITY_ASSIGNEE.value,
    }
    if qualification is not None:
        payload.update(
            {
                "activity_id": str(qualification.activity_id),
                "source_occurred_at": canonical_timestamp(qualification.occurred_at),
                "source_qualification_reference": qualification.qualification_reference,
                "source_transition_lineage_reference": qualification.submit_transition_lineage_reference,
                "source_transition_pk": qualification.submit_transition_pk,
            }
        )
    else:
        payload.update(
            {
                "expected_head_lineage_reference": expected_head_lineage_reference,
                "expected_head_transition_pk": expected_head_transition_pk,
                "lineage_id": str(lineage_id),
            }
        )
    return payload


def _proposal_lineage_payload(*, lineage_id: UUID, sequence: int, action: ProposalAction, actor_identity_id: UUID, actor_access_epoch: int, payload_fingerprint: str, occurred_at: datetime) -> dict[str, object]:
    return {
        "action": action.value,
        "actor_access_epoch": actor_access_epoch,
        "actor_identity_id": str(actor_identity_id),
        "lineage_id": str(lineage_id),
        "occurred_at": canonical_timestamp(occurred_at),
        "payload_fingerprint": payload_fingerprint,
        "schema": "intevia.s013.profile-effect.proposal-lineage.v1",
        "sequence": sequence,
    }


def _projection_command_payload(*, action: ProjectionAction, database_alias: str, lineage_id: UUID, expected_proposal_transition_pk: int, expected_proposal_lineage_reference: str, expected_disposition_pk_or_null: int | None, expected_disposition_lineage_reference_or_null: str | None, actor_identity_id: UUID, actor_access_epoch: int, subject_identity_id: UUID, request_reference: str, idempotency_key: str, occurred_at: datetime) -> dict[str, object]:
    return {
        "action": action.value,
        "actor_access_epoch": actor_access_epoch,
        "actor_identity_id": str(actor_identity_id),
        "contract_version": CONTRACT_VERSION,
        "database_alias": database_alias,
        "effect_type": EffectType.SERVICE_ACTIVITY_SUBMISSION_TRANSITION_RECORDED.value,
        "expected_disposition_lineage_reference_or_null": expected_disposition_lineage_reference_or_null,
        "expected_disposition_pk_or_null": expected_disposition_pk_or_null,
        "expected_proposal_lineage_reference": expected_proposal_lineage_reference,
        "expected_proposal_transition_pk": expected_proposal_transition_pk,
        "idempotency_key": idempotency_key,
        "lineage_id": str(lineage_id),
        "occurred_at": canonical_timestamp(occurred_at),
        "request_reference": request_reference,
        "schema": "intevia.s013.profile-effect.projection-command.v1",
        "subject_identity_id": str(subject_identity_id),
        "subject_relation": SubjectRelation.IMMUTABLE_ACTIVITY_ASSIGNEE.value,
    }


def _projection_lineage_payload(*, proposal_transition_pk: int, sequence: int, action: ProjectionAction, actor_identity_id: UUID, actor_access_epoch: int, payload_fingerprint: str, occurred_at: datetime) -> dict[str, object]:
    return {
        "action": action.value,
        "actor_access_epoch": actor_access_epoch,
        "actor_identity_id": str(actor_identity_id),
        "occurred_at": canonical_timestamp(occurred_at),
        "payload_fingerprint": payload_fingerprint,
        "proposal_transition_pk": proposal_transition_pk,
        "schema": "intevia.s013.profile-effect.projection-lineage.v1",
        "sequence": sequence,
    }


@dataclass(frozen=True, slots=True)
class _CurrentDispositionState:
    state: ProjectionState
    disposition: ProfileEffectProjectionDisposition | None


class _ProfileEffectServiceBase:
    def __init__(self, *, clock: Callable[[], datetime], database_alias: str = "default") -> None:
        if not callable(clock):
            raise TypeError("clock must be callable")
        if type(database_alias) is not str or not database_alias:
            raise ValueError("database_alias is required")
        self._clock = clock
        self._alias = database_alias

    def _lock_actor(self, credential: User) -> Identity:
        try:
            identity = (
                Identity.objects.using(self._alias)
                .select_for_update()
                .select_related("credential")
                .get(credential=credential)
            )
        except Identity.DoesNotExist as exc:
            raise ProfileEffectActorError("actor Identity not found") from exc
        self._require_active_identity(identity, "actor")
        return identity

    def _require_active_identity(self, identity: Identity, label: str) -> None:
        if identity.access_state != Identity.AccessState.ACTIVE:
            raise ProfileEffectActorError(f"{label} Identity is not active")
        if not identity.credential.is_active:
            raise ProfileEffectActorError(f"{label} credential is not active")

    def _require_epoch(self, identity: Identity, expected_epoch: int) -> None:
        if identity.access_epoch != expected_epoch:
            raise ProfileEffectCrossEpochConflict("cross-epoch conflict")

    def _lock_lineage(self, lineage_id: UUID) -> ProfileEffectProposalLineage:
        try:
            return (
                ProfileEffectProposalLineage.objects.using(self._alias)
                .select_for_update()
                .get(lineage_id=lineage_id)
            )
        except ProfileEffectProposalLineage.DoesNotExist as exc:
            raise ProfileEffectNotFound("Profile effect lineage not found") from exc

    def _require_digest_reference(self, value: str, prefix: str, label: str) -> None:
        if type(value) is not str or not value.startswith(prefix):
            raise ProfileEffectMalformedReplay(f"{label} is malformed")
        digest = value[len(prefix) :]
        self._require_hex_digest(digest, label)

    def _require_hex_digest(self, value: str, label: str) -> None:
        if type(value) is not str or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ProfileEffectMalformedReplay(f"{label} is malformed")

    def _validate_root_record(self, lineage: ProfileEffectProposalLineage) -> None:
        if lineage.subject_id != lineage.proposer_id:
            raise ProfileEffectMalformedReplay("profile effect subject/proposer binding mismatch")
        if lineage.contract_version != CONTRACT_VERSION:
            raise ProfileEffectMalformedReplay("profile effect contract version mismatch")
        if lineage.subject_relation != SubjectRelation.IMMUTABLE_ACTIVITY_ASSIGNEE.value:
            raise ProfileEffectMalformedReplay("profile effect subject relation mismatch")
        if lineage.effect_type != EffectType.SERVICE_ACTIVITY_SUBMISSION_TRANSITION_RECORDED.value:
            raise ProfileEffectMalformedReplay("profile effect effect type mismatch")
        if lineage.created_at > lineage.updated_at:
            raise ProfileEffectMalformedReplay("profile effect root timestamps are invalid")
        _validate_reference(lineage.source_database_alias, "source_database_alias", 64)
        _validate_reference(lineage.source_authority_reference, "source_authority_reference", 255)
        self._require_digest_reference(
            lineage.source_transition_lineage_reference,
            "s012l1:",
            "source_transition_lineage_reference",
        )
        self._require_digest_reference(
            lineage.source_qualification_reference,
            "s012sq1:",
            "source_qualification_reference",
        )

    def _stored_create_payload(
        self,
        *,
        lineage: ProfileEffectProposalLineage,
        transition: ProfileEffectProposalTransition,
    ) -> dict[str, object]:
        return {
            "schema": PROPOSAL_COMMAND_SCHEMA,
            "contract_version": CONTRACT_VERSION,
            "database_alias": self._alias,
            "action": ProposalAction.CREATE_PROPOSAL.value,
            "activity_id": str(lineage.source_activity_id),
            "source_transition_pk": lineage.source_transition_pk,
            "source_transition_lineage_reference": lineage.source_transition_lineage_reference,
            "source_qualification_reference": lineage.source_qualification_reference,
            "source_occurred_at": canonical_timestamp(lineage.source_occurred_at),
            "subject_identity_id": str(lineage.subject.identity_id),
            "actor_identity_id": str(transition.actor.identity_id),
            "actor_access_epoch": transition.actor_access_epoch,
            "subject_relation": lineage.subject_relation,
            "effect_type": lineage.effect_type,
            "request_reference": transition.request_reference,
            "idempotency_key": transition.idempotency_key,
            "occurred_at": canonical_timestamp(transition.occurred_at),
        }

    def _stored_correction_payload(
        self,
        *,
        lineage: ProfileEffectProposalLineage,
        transition: ProfileEffectProposalTransition,
        predecessor: ProfileEffectProposalTransition,
    ) -> dict[str, object]:
        return {
            "schema": PROPOSAL_COMMAND_SCHEMA,
            "contract_version": CONTRACT_VERSION,
            "database_alias": self._alias,
            "action": transition.action,
            "lineage_id": str(lineage.lineage_id),
            "expected_head_transition_pk": predecessor.pk,
            "expected_head_lineage_reference": predecessor.lineage_reference,
            "subject_identity_id": str(lineage.subject.identity_id),
            "actor_identity_id": str(transition.actor.identity_id),
            "actor_access_epoch": transition.actor_access_epoch,
            "subject_relation": lineage.subject_relation,
            "effect_type": lineage.effect_type,
            "request_reference": transition.request_reference,
            "idempotency_key": transition.idempotency_key,
            "occurred_at": canonical_timestamp(transition.occurred_at),
        }

    def _stored_projection_payload(
        self,
        *,
        lineage: ProfileEffectProposalLineage,
        transition: ProfileEffectProposalTransition,
        disposition: ProfileEffectProjectionDisposition,
        predecessor: ProfileEffectProjectionDisposition | None,
    ) -> dict[str, object]:
        return {
            "schema": PROJECTION_COMMAND_SCHEMA,
            "contract_version": CONTRACT_VERSION,
            "database_alias": self._alias,
            "action": disposition.action,
            "lineage_id": str(lineage.lineage_id),
            "expected_proposal_transition_pk": transition.pk,
            "expected_proposal_lineage_reference": transition.lineage_reference,
            "expected_disposition_pk_or_null": predecessor.pk if predecessor else None,
            "expected_disposition_lineage_reference_or_null": (
                predecessor.lineage_reference if predecessor else None
            ),
            "subject_identity_id": str(lineage.subject.identity_id),
            "actor_identity_id": str(disposition.actor.identity_id),
            "actor_access_epoch": disposition.actor_access_epoch,
            "subject_relation": lineage.subject_relation,
            "effect_type": lineage.effect_type,
            "request_reference": disposition.request_reference,
            "idempotency_key": disposition.idempotency_key,
            "occurred_at": canonical_timestamp(disposition.occurred_at),
        }

    def _proposal_target_reference_from_stored(
        self,
        *,
        lineage: ProfileEffectProposalLineage,
        transition: ProfileEffectProposalTransition,
        predecessor: ProfileEffectProposalTransition | None,
    ) -> str:
        if predecessor is None:
            return proposal_authority_target_reference(
                proposal_authority_target_payload(
                    database_alias=self._alias,
                    qualification_reference=lineage.source_qualification_reference,
                    subject_identity_id=lineage.subject.identity_id,
                )
            )
        return proposal_authority_target_reference(
            proposal_correction_target_payload(
                database_alias=self._alias,
                lineage_id=lineage.lineage_id,
                subject_identity_id=lineage.subject.identity_id,
                current_proposal_transition_pk=predecessor.pk,
                current_proposal_lineage_reference=predecessor.lineage_reference,
                current_proposal_state=ProposalState(predecessor.to_state),
                has_current_survivor=True,
                action=ProposalAction(transition.action),
            )
        )

    def _projection_target_reference_from_stored(
        self,
        *,
        lineage: ProfileEffectProposalLineage,
        transition: ProfileEffectProposalTransition,
        disposition: ProfileEffectProjectionDisposition,
        predecessor: ProfileEffectProjectionDisposition | None,
    ) -> str:
        historical_state = ProjectionState.UNAUTHORISED if predecessor is None else ProjectionState(predecessor.to_state)
        return projection_authority_target_reference(
            projection_authority_target_payload(
                database_alias=self._alias,
                lineage_id=lineage.lineage_id,
                subject_identity_id=lineage.subject.identity_id,
                current_proposal_transition_pk=transition.pk,
                current_proposal_lineage_reference=transition.lineage_reference,
                current_disposition_pk_or_null=predecessor.pk if predecessor else None,
                current_disposition_lineage_reference_or_null=(
                    predecessor.lineage_reference if predecessor else None
                ),
                current_projection_state=historical_state,
                action=ProjectionAction(disposition.action),
            )
        )

    def _validate_proposal_row(
        self,
        *,
        lineage: ProfileEffectProposalLineage,
        transition: ProfileEffectProposalTransition,
        predecessor: ProfileEffectProposalTransition | None,
    ) -> None:
        if transition.lineage_id != lineage.pk:
            raise ProfileEffectMalformedReplay("proposal transition linked across roots")
        if transition.actor_id != lineage.subject_id or transition.actor_id != lineage.proposer_id:
            raise ProfileEffectMalformedReplay("proposal actor binding mismatch")
        if predecessor is None:
            if transition.sequence != 1:
                raise ProfileEffectMalformedReplay("initial proposal sequence mismatch")
            if ProposalAction(transition.action) is not ProposalAction.CREATE_PROPOSAL:
                raise ProfileEffectMalformedReplay("initial proposal action mismatch")
            if transition.from_state is not None or transition.to_state != ProposalState.ACTIVE.value:
                raise ProfileEffectMalformedReplay("initial proposal edge mismatch")
            if transition.occurred_at != lineage.created_at:
                raise ProfileEffectMalformedReplay("initial proposal timestamp mismatch")
        else:
            if transition.sequence != predecessor.sequence + 1:
                raise ProfileEffectMalformedReplay("proposal transition sequence mismatch")
            if transition.from_state != predecessor.to_state:
                raise ProfileEffectMalformedReplay("proposal transition source state mismatch")
            if transition.occurred_at < predecessor.occurred_at:
                raise ProfileEffectMalformedReplay("proposal transition timestamp ordering mismatch")
            legal_edges = {
                ProposalAction.SUPERSEDE_PROPOSAL: ProposalState.ACTIVE,
                ProposalAction.VOID_PROPOSAL: ProposalState.VOIDED,
            }
            action = ProposalAction(transition.action)
            if predecessor.to_state != ProposalState.ACTIVE.value or transition.to_state != legal_edges[action].value:
                raise ProfileEffectMalformedReplay("proposal transition edge mismatch")
        if transition.authority_evaluated_at > transition.occurred_at:
            raise ProfileEffectMalformedReplay("proposal authority evaluation mismatch")
        _validate_reference(transition.authority_reference, "authority_reference", 255)
        _validate_reference(transition.request_reference, "request_reference", 128)
        _validate_reference(transition.idempotency_key, "idempotency_key", 120)
        self._require_hex_digest(transition.payload_fingerprint, "proposal payload_fingerprint")
        self._require_digest_reference(transition.lineage_reference, "s013pl1:", "proposal lineage_reference")
        self._require_digest_reference(
            transition.authority_decision_reference,
            "s013pa1:",
            "proposal authority_decision_reference",
        )
        payload = self._stored_create_payload(lineage=lineage, transition=transition) if predecessor is None else self._stored_correction_payload(lineage=lineage, transition=transition, predecessor=predecessor)
        expected_fingerprint = proposal_command_fingerprint(payload)
        if transition.payload_fingerprint != expected_fingerprint:
            raise ProfileEffectMalformedReplay("proposal payload fingerprint mismatch")
        expected_lineage_reference = proposal_lineage_reference(
            _proposal_lineage_payload(
                lineage_id=lineage.lineage_id,
                sequence=transition.sequence,
                action=ProposalAction(transition.action),
                actor_identity_id=transition.actor.identity_id,
                actor_access_epoch=transition.actor_access_epoch,
                payload_fingerprint=transition.payload_fingerprint,
                occurred_at=transition.occurred_at,
            )
        )
        if transition.lineage_reference != expected_lineage_reference:
            raise ProfileEffectMalformedReplay("proposal lineage reference mismatch")
        self._validate_proposal_authority_evidence(
            transition,
            transition.actor.identity_id,
            self._proposal_target_reference_from_stored(
                lineage=lineage,
                transition=transition,
                predecessor=predecessor,
            ),
        )

    def _validate_projection_row(
        self,
        *,
        lineage: ProfileEffectProposalLineage,
        transition: ProfileEffectProposalTransition,
        disposition: ProfileEffectProjectionDisposition,
        predecessor: ProfileEffectProjectionDisposition | None,
    ) -> None:
        if disposition.proposal_transition_id != transition.pk:
            raise ProfileEffectMalformedReplay("disposition linked to wrong proposal")
        if disposition.actor_id != lineage.subject_id:
            raise ProfileEffectMalformedReplay("projection actor binding mismatch")
        if predecessor is None:
            if disposition.sequence != 1:
                raise ProfileEffectMalformedReplay("initial projection sequence mismatch")
            if disposition.from_state != ProjectionState.UNAUTHORISED.value:
                raise ProfileEffectMalformedReplay("initial projection source state mismatch")
        else:
            if disposition.sequence != predecessor.sequence + 1:
                raise ProfileEffectMalformedReplay("projection sequence mismatch")
            if disposition.from_state != predecessor.to_state:
                raise ProfileEffectMalformedReplay("projection source state mismatch")
            if disposition.occurred_at < predecessor.occurred_at:
                raise ProfileEffectMalformedReplay("projection timestamp ordering mismatch")
        if disposition.occurred_at < transition.occurred_at:
            raise ProfileEffectMalformedReplay("projection precedes proposal transition")
        if disposition.authority_evaluated_at > disposition.occurred_at:
            raise ProfileEffectMalformedReplay("projection authority evaluation mismatch")
        _validate_reference(disposition.authority_reference, "authority_reference", 255)
        _validate_reference(disposition.request_reference, "request_reference", 128)
        _validate_reference(disposition.idempotency_key, "idempotency_key", 120)
        self._require_hex_digest(disposition.payload_fingerprint, "projection payload_fingerprint")
        self._require_digest_reference(disposition.lineage_reference, "s013xl1:", "projection lineage_reference")
        self._require_digest_reference(
            disposition.authority_decision_reference,
            "s013px1:",
            "projection authority_decision_reference",
        )
        legal_edges = {
            (ProjectionAction.AUTHORISE_PROJECTION, ProjectionState.UNAUTHORISED): ProjectionState.AUTHORISED,
            (ProjectionAction.AUTHORISE_PROJECTION, ProjectionState.DECLINED): ProjectionState.AUTHORISED,
            (ProjectionAction.AUTHORISE_PROJECTION, ProjectionState.WITHDRAWN): ProjectionState.AUTHORISED,
            (ProjectionAction.DECLINE_PROJECTION, ProjectionState.UNAUTHORISED): ProjectionState.DECLINED,
            (ProjectionAction.WITHDRAW_PROJECTION, ProjectionState.AUTHORISED): ProjectionState.WITHDRAWN,
        }
        action = ProjectionAction(disposition.action)
        from_state = ProjectionState(disposition.from_state)
        expected_to_state = legal_edges.get((action, from_state))
        if expected_to_state is None or disposition.to_state != expected_to_state.value:
            raise ProfileEffectMalformedReplay("projection edge mismatch")
        payload = self._stored_projection_payload(
            lineage=lineage,
            transition=transition,
            disposition=disposition,
            predecessor=predecessor,
        )
        expected_fingerprint = projection_command_fingerprint(payload)
        if disposition.payload_fingerprint != expected_fingerprint:
            raise ProfileEffectMalformedReplay("projection payload fingerprint mismatch")
        expected_lineage_reference = projection_lineage_reference(
            _projection_lineage_payload(
                proposal_transition_pk=transition.pk,
                sequence=disposition.sequence,
                action=ProjectionAction(disposition.action),
                actor_identity_id=disposition.actor.identity_id,
                actor_access_epoch=disposition.actor_access_epoch,
                payload_fingerprint=disposition.payload_fingerprint,
                occurred_at=disposition.occurred_at,
            )
        )
        if disposition.lineage_reference != expected_lineage_reference:
            raise ProfileEffectMalformedReplay("projection lineage reference mismatch")
        self._validate_projection_authority_evidence(
            disposition,
            disposition.actor.identity_id,
            self._projection_target_reference_from_stored(
                lineage=lineage,
                transition=transition,
                disposition=disposition,
                predecessor=predecessor,
            ),
        )

    def _lock_proposal_lineage_rows_for_replay(self, lineage: ProfileEffectProposalLineage) -> list[ProfileEffectProposalTransition]:
        rows = list(
            ProfileEffectProposalTransition.objects.using(self._alias)
            .select_for_update()
            .select_related("actor", "lineage")
            .filter(lineage=lineage)
            .order_by("sequence", "pk")
        )
        if not rows:
            raise ProfileEffectMalformedReplay("proposal lineage is missing")
        return rows

    def _validate_locked_proposal_lineage_rows(self, lineage: ProfileEffectProposalLineage, rows: list[ProfileEffectProposalTransition]) -> None:
        self._validate_root_record(lineage)
        previous = None
        for row in rows:
            if row.previous_transition_id != (previous.pk if previous else None):
                raise ProfileEffectMalformedReplay("proposal lineage is branched or gapped")
            self._validate_proposal_row(
                lineage=lineage,
                transition=row,
                predecessor=previous,
            )
            previous = row
        derived_head = rows[-1]
        derived_has_current_survivor = derived_head.to_state == ProposalState.ACTIVE.value
        if lineage.head_proposal_transition_id != derived_head.pk:
            raise ProfileEffectMalformedReplay("stale proposal head")
        if lineage.has_current_survivor != derived_has_current_survivor:
            raise ProfileEffectMalformedReplay("current-survivor flag mismatch")
        if lineage.updated_at != derived_head.occurred_at:
            raise ProfileEffectMalformedReplay("root updated_at mismatch")

    def _lock_proposal_lineage_rows(self, lineage: ProfileEffectProposalLineage) -> list[ProfileEffectProposalTransition]:
        rows = self._lock_proposal_lineage_rows_for_replay(lineage)
        self._validate_locked_proposal_lineage_rows(lineage, rows)
        return rows

    def _lock_dispositions_by_transition_for_replay(self, transitions: list[ProfileEffectProposalTransition]) -> dict[int, list[ProfileEffectProjectionDisposition]]:
        transition_ids = [row.pk for row in transitions]
        rows = list(
            ProfileEffectProjectionDisposition.objects.using(self._alias)
            .select_for_update()
            .select_related("actor", "proposal_transition__lineage")
            .filter(proposal_transition_id__in=transition_ids)
            .order_by("proposal_transition_id", "sequence", "pk")
        )
        grouped: dict[int, list[ProfileEffectProjectionDisposition]] = {row.pk: [] for row in transitions}
        for row in rows:
            grouped.setdefault(row.proposal_transition_id, []).append(row)
        return grouped

    def _validate_locked_dispositions_by_transition(self, transitions: list[ProfileEffectProposalTransition], grouped: dict[int, list[ProfileEffectProjectionDisposition]]) -> None:
        for transition in transitions:
            previous = None
            for row in grouped.get(transition.pk, []):
                if row.previous_disposition_id != (previous.pk if previous else None):
                    raise ProfileEffectMalformedReplay("projection lineage is branched or gapped")
                self._validate_projection_row(
                    lineage=transition.lineage,
                    transition=transition,
                    disposition=row,
                    predecessor=previous,
                )
                previous = row

    def _lock_dispositions_by_transition(self, transitions: list[ProfileEffectProposalTransition]) -> dict[int, list[ProfileEffectProjectionDisposition]]:
        grouped = self._lock_dispositions_by_transition_for_replay(transitions)
        self._validate_locked_dispositions_by_transition(transitions, grouped)
        return grouped

    def _current_disposition_state(self, transition: ProfileEffectProposalTransition, grouped: dict[int, list[ProfileEffectProjectionDisposition]]) -> _CurrentDispositionState:
        rows = grouped.get(transition.pk, [])
        if not rows:
            return _CurrentDispositionState(ProjectionState.UNAUTHORISED, None)
        last = rows[-1]
        return _CurrentDispositionState(ProjectionState(last.to_state), last)

    def _validate_root_alias(self, lineage: ProfileEffectProposalLineage) -> None:
        if lineage.source_database_alias != self._alias:
            raise ProfileEffectMalformedReplay("stored source database alias mismatch")

    def _check_constraint_name(self, exc: IntegrityError) -> str | None:
        cause = getattr(exc, "__cause__", None)
        diag = getattr(cause, "diag", None) if cause else None
        return getattr(diag, "constraint_name", None) if diag else None

    def _validate_proposal_authority_evidence(
        self,
        transition: ProfileEffectProposalTransition,
        actor_identity_id: UUID,
        target_reference: str,
    ) -> None:
        response = ProposalAuthorityResponse(
            database_alias=self._alias,
            actor_pk=transition.actor_id,
            actor_identity_id=actor_identity_id,
            actor_access_epoch=transition.actor_access_epoch,
            action=ProposalAction(transition.action),
            target_fingerprint=target_reference.split(":", 1)[1],
            request_reference=transition.request_reference,
            idempotency_key=transition.idempotency_key,
            evaluated_at=transition.authority_evaluated_at,
            authority_reference=transition.authority_reference,
        )
        expected = proposal_decision_reference_for(response)
        if transition.authority_decision_reference != expected:
            raise ProfileEffectMalformedReplay(
                "stored proposal authority evidence is malformed"
            )

    def _validate_projection_authority_evidence(
        self,
        disposition: ProfileEffectProjectionDisposition,
        actor_identity_id: UUID,
        target_reference: str,
    ) -> None:
        response = ProjectionAuthorityResponse(
            database_alias=self._alias,
            actor_pk=disposition.actor_id,
            actor_identity_id=actor_identity_id,
            actor_access_epoch=disposition.actor_access_epoch,
            action=ProjectionAction(disposition.action),
            target_fingerprint=target_reference.split(":", 1)[1],
            request_reference=disposition.request_reference,
            idempotency_key=disposition.idempotency_key,
            evaluated_at=disposition.authority_evaluated_at,
            authority_reference=disposition.authority_reference,
        )
        expected = projection_decision_reference_for(response)
        if disposition.authority_decision_reference != expected:
            raise ProfileEffectMalformedReplay(
                "stored projection authority evidence is malformed"
            )


class ServiceSubmissionProfileEffectProposalService(_ProfileEffectServiceBase):
    def __init__(
        self,
        *,
        authority: ProposalAuthority,
        read_service: ServiceActivityReadService,
        clock: Callable[[], datetime],
        database_alias: str = "default",
    ) -> None:
        super().__init__(clock=clock, database_alias=database_alias)
        if not isinstance(authority, ProposalAuthority):
            raise TypeError("authority must be a ProposalAuthority")
        if not isinstance(read_service, ServiceActivityReadService):
            raise TypeError("read_service must be a ServiceActivityReadService")
        if authority.database_alias != database_alias:
            raise ValueError("authority database_alias mismatch")
        if read_service._alias != database_alias:
            raise ValueError("read_service database_alias mismatch")
        self._authority = authority
        self._read_service = read_service

    def create_service_submission_proposal(
        self,
        command: CreateServiceSubmissionProposalCommand,
    ) -> ProfileEffectProposalCommandReceipt:
        with transaction.atomic(using=self._alias):
            return self._execute_create(command)

    def _execute_create(
        self,
        command: CreateServiceSubmissionProposalCommand,
    ) -> ProfileEffectProposalCommandReceipt:
        activity_id = _validate_uuid(command.activity_id, "activity_id")
        request_reference = _validate_reference(command.request_reference, "request_reference", 128)
        idempotency_key = _validate_reference(command.idempotency_key, "idempotency_key", 120)
        occurred_at = _validate_occurred_at(command.occurred_at)

        qualification = self._read_service.qualify_submission_occurrence(activity_id=activity_id)
        actor = self._lock_actor(command.credential)
        self._require_epoch(actor, command.actor_access_epoch)
        if actor.pk != qualification.subject_pk:
            raise ProfileEffectActorError("actor must equal the qualified subject")
        if actor.identity_id != qualification.subject_identity_id:
            raise ProfileEffectActorError("actor Identity must equal the qualified subject")
        if qualification.actor_identity_id != actor.identity_id:
            raise ProfileEffectActorError("qualified actor must equal subject")
        if qualification.database_alias != self._alias:
            raise ProfileEffectValidationError("qualification alias mismatch")
        if qualification.actor_equals_assignee is not True:
            raise ProfileEffectValidationError("qualified actor_equals_assignee must be exact True")

        lineage = (
            ProfileEffectProposalLineage.objects.using(self._alias)
            .select_for_update()
            .filter(
                subject_id=actor.pk,
                source_transition_lineage_reference=qualification.submit_transition_lineage_reference,
                contract_version=CONTRACT_VERSION,
            )
            .first()
        )
        if lineage is not None:
            self._validate_root_alias(lineage)
        payload = _proposal_command_payload(
            action=ProposalAction.CREATE_PROPOSAL,
            database_alias=self._alias,
            actor_identity_id=actor.identity_id,
            actor_access_epoch=actor.access_epoch,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
            qualification=qualification,
            subject_identity_id=actor.identity_id,
        )
        fingerprint = proposal_command_fingerprint(payload)
        existing = (
            ProfileEffectProposalTransition.objects.using(self._alias)
            .select_for_update()
            .filter(
                actor=actor,
                action=ProposalAction.CREATE_PROPOSAL.value,
                idempotency_key=idempotency_key,
            )
            .first()
        )
        if existing is not None:
            return self._resolve_create_replay(
                existing=existing,
                qualification=qualification,
                actor=actor,
                request_reference=request_reference,
                idempotency_key=idempotency_key,
                payload_fingerprint=fingerprint,
            )
        target_payload = proposal_authority_target_payload(
            database_alias=self._alias,
            qualification_reference=qualification.qualification_reference,
            subject_identity_id=actor.identity_id,
        )
        target_reference = proposal_authority_target_reference(target_payload)
        decision = self._authority.qualify(
            request=ProposalAuthorityRequest(
                database_alias=self._alias,
                actor_pk=actor.pk,
                actor_identity_id=actor.identity_id,
                actor_access_epoch=actor.access_epoch,
                action=ProposalAction.CREATE_PROPOSAL,
                target_fingerprint=target_reference.split(":", 1)[1],
                request_reference=request_reference,
                idempotency_key=idempotency_key,
                evaluated_at=_validate_occurred_at(self._clock()),
            ),
            target_reference=target_reference,
        )
        if decision.database_alias != qualification.database_alias:
            raise ProposalAuthorityNotAuthorised("authority and qualification alias mismatch")

        def _write() -> ProfileEffectProposalCommandReceipt:
            root = ProfileEffectProposalLineage(
                lineage_id=uuid4(),
                subject=actor,
                proposer=actor,
                source_database_alias=self._alias,
                source_activity_id=qualification.activity_id,
                source_transition_pk=qualification.submit_transition_pk,
                source_transition_sequence=qualification.submit_transition_sequence,
                source_transition_lineage_reference=qualification.submit_transition_lineage_reference,
                source_occurred_at=qualification.occurred_at,
                source_actor_access_epoch=qualification.actor_access_epoch,
                source_authority_reference=qualification.source_authority_reference,
                source_qualification_reference=qualification.qualification_reference,
                subject_relation=SubjectRelation.IMMUTABLE_ACTIVITY_ASSIGNEE.value,
                effect_type=EffectType.SERVICE_ACTIVITY_SUBMISSION_TRANSITION_RECORDED.value,
                contract_version=CONTRACT_VERSION,
                has_current_survivor=True,
                created_at=occurred_at,
                updated_at=occurred_at,
            )
            root.save(using=self._alias)
            proposal_ref = proposal_lineage_reference(
                _proposal_lineage_payload(
                    lineage_id=root.lineage_id,
                    sequence=1,
                    action=ProposalAction.CREATE_PROPOSAL,
                    actor_identity_id=actor.identity_id,
                    actor_access_epoch=actor.access_epoch,
                    payload_fingerprint=fingerprint,
                    occurred_at=occurred_at,
                )
            )
            transition = ProfileEffectProposalTransition(
                lineage=root,
                sequence=1,
                previous_transition=None,
                action=ProposalAction.CREATE_PROPOSAL.value,
                from_state=None,
                to_state=ProposalState.ACTIVE.value,
                actor=actor,
                actor_access_epoch=actor.access_epoch,
                authority_reference=decision.authority_reference,
                authority_decision_reference=decision.decision_reference,
                authority_evaluated_at=decision.evaluated_at,
                request_reference=request_reference,
                idempotency_key=idempotency_key,
                payload_fingerprint=fingerprint,
                occurred_at=occurred_at,
                lineage_reference=proposal_ref,
            )
            transition.save(using=self._alias)
            root.head_proposal_transition = transition
            root.save(using=self._alias)
            return ProfileEffectProposalCommandReceipt(
                database_alias=self._alias,
                lineage_id=root.lineage_id,
                proposal_transition_pk=transition.pk,
                action=ProposalAction.CREATE_PROPOSAL,
                to_state=ProposalState.ACTIVE,
                proposal_lineage_reference=transition.lineage_reference,
                has_current_survivor=True,
                request_reference=request_reference,
                idempotency_key=idempotency_key,
                payload_fingerprint=fingerprint,
                occurred_at=occurred_at,
                replayed=False,
            )

        def _winner() -> ProfileEffectProposalCommandReceipt:
            winner = (
                ProfileEffectProposalLineage.objects.using(self._alias)
                .select_for_update()
                .get(
                    subject_id=actor.pk,
                    source_transition_lineage_reference=qualification.submit_transition_lineage_reference,
                    contract_version=CONTRACT_VERSION,
                )
            )
            existing_winner = (
                ProfileEffectProposalTransition.objects.using(self._alias)
                .select_for_update()
                .get(
                    actor=actor,
                    action=ProposalAction.CREATE_PROPOSAL.value,
                    idempotency_key=idempotency_key,
                    lineage=winner,
                )
            )
            return self._resolve_create_replay(
                existing=existing_winner,
                qualification=qualification,
                actor=actor,
                request_reference=request_reference,
                idempotency_key=idempotency_key,
                payload_fingerprint=fingerprint,
            )

        return self._try_integrity(write_fn=_write, replay_fn=_winner, allowed_constraints=_CREATE_RACE_CONSTRAINTS)

    def _resolve_create_replay(
        self,
        *,
        existing: ProfileEffectProposalTransition,
        qualification: ServiceSubmissionQualificationDTO,
        actor: Identity,
        request_reference: str,
        idempotency_key: str,
        payload_fingerprint: str,
    ) -> ProfileEffectProposalCommandReceipt:
        lineage = (
            ProfileEffectProposalLineage.objects.using(self._alias)
            .select_for_update()
            .get(pk=existing.lineage_id)
        )
        transitions = self._lock_proposal_lineage_rows_for_replay(lineage)
        grouped = self._lock_dispositions_by_transition_for_replay(transitions)
        if lineage.subject_id != qualification.subject_pk:
            raise ProfileEffectMalformedReplay("replayed proposal subject mismatch")
        if lineage.source_qualification_reference != qualification.qualification_reference:
            raise ProfileEffectMalformedReplay("replayed proposal qualification mismatch")
        if existing.sequence != 1 or existing.previous_transition_id is not None:
            raise ProfileEffectMalformedReplay("create replay must target initial proposal")
        target_payload = proposal_authority_target_payload(
            database_alias=self._alias,
            qualification_reference=lineage.source_qualification_reference,
            subject_identity_id=lineage.subject.identity_id,
        )
        target_reference = proposal_authority_target_reference(target_payload)
        request = ProposalAuthorityRequest(
            database_alias=self._alias,
            actor_pk=actor.pk,
            actor_identity_id=actor.identity_id,
            actor_access_epoch=actor.access_epoch,
            action=ProposalAction.CREATE_PROPOSAL,
            target_fingerprint=target_reference.split(":", 1)[1],
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            evaluated_at=_validate_occurred_at(self._clock()),
        )
        self._authority.qualify(request=request, target_reference=target_reference)
        if existing.actor_access_epoch != actor.access_epoch:
            raise ProfileEffectCrossEpochConflict("cross-epoch conflict")
        if existing.payload_fingerprint != payload_fingerprint:
            raise ProfileEffectPayloadConflict("payload conflict")
        self._validate_root_alias(lineage)
        self._validate_locked_proposal_lineage_rows(lineage, transitions)
        self._validate_locked_dispositions_by_transition(transitions, grouped)
        return ProfileEffectProposalCommandReceipt(
            database_alias=self._alias,
            lineage_id=lineage.lineage_id,
            proposal_transition_pk=existing.pk,
            action=ProposalAction(existing.action),
            to_state=ProposalState(existing.to_state),
            proposal_lineage_reference=existing.lineage_reference,
            has_current_survivor=existing.to_state == ProposalState.ACTIVE.value,
            request_reference=existing.request_reference,
            idempotency_key=existing.idempotency_key,
            payload_fingerprint=existing.payload_fingerprint,
            occurred_at=existing.occurred_at,
            replayed=True,
        )

    def _try_integrity(self, *, write_fn, replay_fn, allowed_constraints: frozenset[str]):
        if not _is_postgresql(self._alias):
            return write_fn()
        try:
            with transaction.atomic(using=self._alias):
                return write_fn()
        except IntegrityError as exc:
            constraint_name = self._check_constraint_name(exc)
            if constraint_name in allowed_constraints:
                return replay_fn()
            raise


class ProfileEffectProposalCorrectionService(_ProfileEffectServiceBase):
    def __init__(self, *, authority: ProposalAuthority, clock: Callable[[], datetime], database_alias: str = "default") -> None:
        super().__init__(clock=clock, database_alias=database_alias)
        if not isinstance(authority, ProposalAuthority):
            raise TypeError("authority must be a ProposalAuthority")
        if authority.database_alias != database_alias:
            raise ValueError("authority database_alias mismatch")
        self._authority = authority

    def void_profile_effect_proposal(self, command: ProfileEffectProposalCorrectionCommand) -> ProfileEffectProposalCommandReceipt:
        return self._execute_correction(command, ProposalAction.VOID_PROPOSAL)

    def supersede_profile_effect_proposal(self, command: ProfileEffectProposalCorrectionCommand) -> ProfileEffectProposalCommandReceipt:
        return self._execute_correction(command, ProposalAction.SUPERSEDE_PROPOSAL)

    def _execute_correction(self, command: ProfileEffectProposalCorrectionCommand, action: ProposalAction) -> ProfileEffectProposalCommandReceipt:
        lineage_id = _validate_uuid(command.lineage_id, "lineage_id")
        request_reference = _validate_reference(command.request_reference, "request_reference", 128)
        idempotency_key = _validate_reference(command.idempotency_key, "idempotency_key", 120)
        occurred_at = _validate_occurred_at(command.occurred_at)
        with transaction.atomic(using=self._alias):
            actor = self._lock_actor(command.credential)
            self._require_epoch(actor, command.actor_access_epoch)
            lineage = self._lock_lineage(lineage_id)
            transitions = self._lock_proposal_lineage_rows_for_replay(lineage)
            grouped = self._lock_dispositions_by_transition_for_replay(transitions)
            existing = (
                ProfileEffectProposalTransition.objects.using(self._alias)
                .select_for_update()
                .filter(actor=actor, action=action.value, idempotency_key=idempotency_key)
                .first()
            )
            payload = _proposal_command_payload(
                action=action,
                database_alias=self._alias,
                actor_identity_id=actor.identity_id,
                actor_access_epoch=actor.access_epoch,
                request_reference=request_reference,
                idempotency_key=idempotency_key,
                occurred_at=occurred_at,
                lineage_id=lineage.lineage_id,
                expected_head_transition_pk=command.expected_head_transition_pk,
                expected_head_lineage_reference=command.expected_head_lineage_reference,
                subject_identity_id=lineage.subject.identity_id,
            )
            fingerprint = proposal_command_fingerprint(payload)
            if existing is not None:
                return self._resolve_correction_replay(existing=existing, action=action, actor=actor, lineage=lineage, transitions=transitions, grouped=grouped, expected_head_transition_pk=command.expected_head_transition_pk, expected_head_lineage_reference=command.expected_head_lineage_reference, request_reference=request_reference, idempotency_key=idempotency_key, payload_fingerprint=fingerprint)
            self._validate_root_alias(lineage)
            self._validate_locked_proposal_lineage_rows(lineage, transitions)
            self._validate_locked_dispositions_by_transition(transitions, grouped)
            if actor.pk != lineage.subject_id or actor.pk != lineage.proposer_id:
                raise ProfileEffectActorError("actor must equal subject and proposer")
            current_head = transitions[-1]
            if not lineage.has_current_survivor or current_head.to_state != ProposalState.ACTIVE.value:
                raise ProfileEffectLifecycleError("proposal lineage has no current survivor")
            if current_head.pk != command.expected_head_transition_pk or current_head.lineage_reference != command.expected_head_lineage_reference:
                raise ProfileEffectConflict("expected head does not match current head")
            target_payload = proposal_correction_target_payload(
                database_alias=self._alias,
                lineage_id=lineage.lineage_id,
                subject_identity_id=lineage.subject.identity_id,
                current_proposal_transition_pk=current_head.pk,
                current_proposal_lineage_reference=current_head.lineage_reference,
                current_proposal_state=ProposalState(current_head.to_state),
                has_current_survivor=True,
                action=action,
            )
            target_reference = proposal_authority_target_reference(target_payload)
            decision = self._authority.qualify(
                request=ProposalAuthorityRequest(
                    database_alias=self._alias,
                    actor_pk=actor.pk,
                    actor_identity_id=actor.identity_id,
                    actor_access_epoch=actor.access_epoch,
                    action=action,
                    target_fingerprint=target_reference.split(":", 1)[1],
                    request_reference=request_reference,
                    idempotency_key=idempotency_key,
                    evaluated_at=_validate_occurred_at(self._clock()),
                ),
                target_reference=target_reference,
            )
            next_state = ProposalState.VOIDED if action is ProposalAction.VOID_PROPOSAL else ProposalState.ACTIVE

            def _write() -> ProfileEffectProposalCommandReceipt:
                sequence = current_head.sequence + 1
                lineage_ref = proposal_lineage_reference(
                    _proposal_lineage_payload(
                        lineage_id=lineage.lineage_id,
                        sequence=sequence,
                        action=action,
                        actor_identity_id=actor.identity_id,
                        actor_access_epoch=actor.access_epoch,
                        payload_fingerprint=fingerprint,
                        occurred_at=occurred_at,
                    )
                )
                row = ProfileEffectProposalTransition(
                    lineage=lineage,
                    sequence=sequence,
                    previous_transition=current_head,
                    action=action.value,
                    from_state=current_head.to_state,
                    to_state=next_state.value,
                    actor=actor,
                    actor_access_epoch=actor.access_epoch,
                    authority_reference=decision.authority_reference,
                    authority_decision_reference=decision.decision_reference,
                    authority_evaluated_at=decision.evaluated_at,
                    request_reference=request_reference,
                    idempotency_key=idempotency_key,
                    payload_fingerprint=fingerprint,
                    occurred_at=occurred_at,
                    lineage_reference=lineage_ref,
                )
                row.save(using=self._alias)
                lineage.head_proposal_transition = row
                lineage.has_current_survivor = next_state is ProposalState.ACTIVE
                lineage.updated_at = occurred_at
                lineage.save(using=self._alias)
                return ProfileEffectProposalCommandReceipt(
                    database_alias=self._alias,
                    lineage_id=lineage.lineage_id,
                    proposal_transition_pk=row.pk,
                    action=action,
                    to_state=next_state,
                    proposal_lineage_reference=row.lineage_reference,
                    has_current_survivor=lineage.has_current_survivor,
                    request_reference=request_reference,
                    idempotency_key=idempotency_key,
                    payload_fingerprint=fingerprint,
                    occurred_at=occurred_at,
                    replayed=False,
                )

            return self._try_integrity(write_fn=_write, replay_fn=lambda: self._resolve_correction_replay(existing=(ProfileEffectProposalTransition.objects.using(self._alias).select_for_update().get(actor=actor, action=action.value, idempotency_key=idempotency_key)), action=action, actor=actor, lineage=lineage, expected_head_transition_pk=command.expected_head_transition_pk, expected_head_lineage_reference=command.expected_head_lineage_reference, request_reference=request_reference, idempotency_key=idempotency_key, payload_fingerprint=fingerprint), allowed_constraints=_PROPOSAL_APPEND_RACE_CONSTRAINTS)

    def _resolve_correction_replay(self, *, existing: ProfileEffectProposalTransition, action: ProposalAction, actor: Identity, lineage: ProfileEffectProposalLineage, transitions: list[ProfileEffectProposalTransition], grouped: dict[int, list[ProfileEffectProjectionDisposition]], expected_head_transition_pk: int, expected_head_lineage_reference: str, request_reference: str, idempotency_key: str, payload_fingerprint: str) -> ProfileEffectProposalCommandReceipt:
        if existing.lineage_id != lineage.pk:
            raise ProfileEffectMalformedReplay("replayed proposal does not belong to lineage")
        predecessor = existing.previous_transition
        if predecessor is None:
            raise ProfileEffectMalformedReplay("correction replay predecessor is missing")
        historical_target = proposal_correction_target_payload(
            database_alias=self._alias,
            lineage_id=lineage.lineage_id,
            subject_identity_id=lineage.subject.identity_id,
            current_proposal_transition_pk=predecessor.pk,
            current_proposal_lineage_reference=predecessor.lineage_reference,
            current_proposal_state=ProposalState(predecessor.to_state),
            has_current_survivor=True,
            action=action,
        )
        if predecessor.pk != expected_head_transition_pk or predecessor.lineage_reference != expected_head_lineage_reference:
            raise ProfileEffectConflict("expected head does not match historical target")
        target_reference = proposal_authority_target_reference(historical_target)
        self._authority.qualify(
            request=ProposalAuthorityRequest(
                database_alias=self._alias,
                actor_pk=actor.pk,
                actor_identity_id=actor.identity_id,
                actor_access_epoch=actor.access_epoch,
                action=action,
                target_fingerprint=target_reference.split(":", 1)[1],
                request_reference=request_reference,
                idempotency_key=idempotency_key,
                evaluated_at=_validate_occurred_at(self._clock()),
            ),
            target_reference=target_reference,
        )
        if existing.actor_access_epoch != actor.access_epoch:
            raise ProfileEffectCrossEpochConflict("cross-epoch conflict")
        if existing.payload_fingerprint != payload_fingerprint:
            raise ProfileEffectPayloadConflict("payload conflict")
        self._validate_root_alias(lineage)
        self._validate_locked_proposal_lineage_rows(lineage, transitions)
        self._validate_locked_dispositions_by_transition(transitions, grouped)
        if actor.pk != lineage.subject_id or actor.pk != lineage.proposer_id:
            raise ProfileEffectActorError("actor must equal subject and proposer")
        return ProfileEffectProposalCommandReceipt(
            database_alias=self._alias,
            lineage_id=lineage.lineage_id,
            proposal_transition_pk=existing.pk,
            action=action,
            to_state=ProposalState(existing.to_state),
            proposal_lineage_reference=existing.lineage_reference,
            has_current_survivor=existing.to_state == ProposalState.ACTIVE.value,
            request_reference=existing.request_reference,
            idempotency_key=existing.idempotency_key,
            payload_fingerprint=existing.payload_fingerprint,
            occurred_at=existing.occurred_at,
            replayed=True,
        )

    def _try_integrity(self, *, write_fn, replay_fn, allowed_constraints: frozenset[str]):
        if not _is_postgresql(self._alias):
            return write_fn()
        try:
            with transaction.atomic(using=self._alias):
                return write_fn()
        except IntegrityError as exc:
            constraint_name = self._check_constraint_name(exc)
            if constraint_name in allowed_constraints:
                return replay_fn()
            raise


class ProfileEffectProjectionDispositionService(_ProfileEffectServiceBase):
    def __init__(self, *, authority: ProjectionAuthority, clock: Callable[[], datetime], database_alias: str = "default") -> None:
        super().__init__(clock=clock, database_alias=database_alias)
        if not isinstance(authority, ProjectionAuthority):
            raise TypeError("authority must be a ProjectionAuthority")
        if authority.database_alias != database_alias:
            raise ValueError("authority database_alias mismatch")
        self._authority = authority

    def authorise_profile_effect_projection(self, command: ProfileEffectProjectionDispositionCommand) -> ProfileEffectProjectionCommandReceipt:
        return self._execute_projection(command, ProjectionAction.AUTHORISE_PROJECTION)

    def decline_profile_effect_projection(self, command: ProfileEffectProjectionDispositionCommand) -> ProfileEffectProjectionCommandReceipt:
        return self._execute_projection(command, ProjectionAction.DECLINE_PROJECTION)

    def withdraw_profile_effect_projection(self, command: ProfileEffectProjectionDispositionCommand) -> ProfileEffectProjectionCommandReceipt:
        return self._execute_projection(command, ProjectionAction.WITHDRAW_PROJECTION)

    def _execute_projection(self, command: ProfileEffectProjectionDispositionCommand, action: ProjectionAction) -> ProfileEffectProjectionCommandReceipt:
        lineage_id = _validate_uuid(command.lineage_id, "lineage_id")
        request_reference = _validate_reference(command.request_reference, "request_reference", 128)
        idempotency_key = _validate_reference(command.idempotency_key, "idempotency_key", 120)
        occurred_at = _validate_occurred_at(command.occurred_at)
        with transaction.atomic(using=self._alias):
            actor = self._lock_actor(command.credential)
            self._require_epoch(actor, command.actor_access_epoch)
            lineage = self._lock_lineage(lineage_id)
            transitions = self._lock_proposal_lineage_rows_for_replay(lineage)
            grouped = self._lock_dispositions_by_transition_for_replay(transitions)
            existing = (
                ProfileEffectProjectionDisposition.objects.using(self._alias)
                .select_for_update()
                .filter(actor=actor, action=action.value, idempotency_key=idempotency_key)
                .first()
            )
            payload = _projection_command_payload(
                action=action,
                database_alias=self._alias,
                lineage_id=lineage.lineage_id,
                expected_proposal_transition_pk=command.expected_proposal_transition_pk,
                expected_proposal_lineage_reference=command.expected_proposal_lineage_reference,
                expected_disposition_pk_or_null=command.expected_disposition_pk_or_null,
                expected_disposition_lineage_reference_or_null=command.expected_disposition_lineage_reference_or_null,
                actor_identity_id=actor.identity_id,
                actor_access_epoch=actor.access_epoch,
                subject_identity_id=lineage.subject.identity_id,
                request_reference=request_reference,
                idempotency_key=idempotency_key,
                occurred_at=occurred_at,
            )
            fingerprint = projection_command_fingerprint(payload)
            if existing is not None:
                return self._resolve_projection_replay(existing=existing, action=action, actor=actor, lineage=lineage, transitions=transitions, grouped=grouped, request_reference=request_reference, idempotency_key=idempotency_key, payload_fingerprint=fingerprint, expected_proposal_transition_pk=command.expected_proposal_transition_pk, expected_proposal_lineage_reference=command.expected_proposal_lineage_reference, expected_disposition_pk_or_null=command.expected_disposition_pk_or_null, expected_disposition_lineage_reference_or_null=command.expected_disposition_lineage_reference_or_null)
            self._validate_root_alias(lineage)
            self._validate_locked_proposal_lineage_rows(lineage, transitions)
            self._validate_locked_dispositions_by_transition(transitions, grouped)
            if actor.pk != lineage.subject_id:
                raise ProfileEffectActorError("actor must equal subject")
            if not lineage.has_current_survivor or transitions[-1].to_state != ProposalState.ACTIVE.value:
                raise ProfileEffectLifecycleError("projection requires a current active proposal survivor")
            current_transition = transitions[-1]
            current_disposition = self._current_disposition_state(current_transition, grouped)
            if current_transition.pk != command.expected_proposal_transition_pk or current_transition.lineage_reference != command.expected_proposal_lineage_reference:
                raise ProfileEffectConflict("expected proposal transition does not match current proposal transition")
            if (current_disposition.disposition.pk if current_disposition.disposition else None) != command.expected_disposition_pk_or_null:
                raise ProfileEffectConflict("expected disposition primary key does not match current disposition")
            if (current_disposition.disposition.lineage_reference if current_disposition.disposition else None) != command.expected_disposition_lineage_reference_or_null:
                raise ProfileEffectConflict("expected disposition lineage reference does not match current disposition")
            target_payload = projection_authority_target_payload(
                database_alias=self._alias,
                lineage_id=lineage.lineage_id,
                subject_identity_id=lineage.subject.identity_id,
                current_proposal_transition_pk=current_transition.pk,
                current_proposal_lineage_reference=current_transition.lineage_reference,
                current_disposition_pk_or_null=(current_disposition.disposition.pk if current_disposition.disposition else None),
                current_disposition_lineage_reference_or_null=(current_disposition.disposition.lineage_reference if current_disposition.disposition else None),
                current_projection_state=current_disposition.state,
                action=action,
            )
            target_reference = projection_authority_target_reference(target_payload)
            decision = self._authority.qualify(
                request=ProjectionAuthorityRequest(
                    database_alias=self._alias,
                    actor_pk=actor.pk,
                    actor_identity_id=actor.identity_id,
                    actor_access_epoch=actor.access_epoch,
                    action=action,
                    target_fingerprint=target_reference.split(":", 1)[1],
                    request_reference=request_reference,
                    idempotency_key=idempotency_key,
                    evaluated_at=_validate_occurred_at(self._clock()),
                ),
                target_reference=target_reference,
            )
            next_state = self._next_projection_state(action, current_disposition.state)

            def _write() -> ProfileEffectProjectionCommandReceipt:
                sequence = 1 if current_disposition.disposition is None else current_disposition.disposition.sequence + 1
                lineage_ref = projection_lineage_reference(
                    _projection_lineage_payload(
                        proposal_transition_pk=current_transition.pk,
                        sequence=sequence,
                        action=action,
                        actor_identity_id=actor.identity_id,
                        actor_access_epoch=actor.access_epoch,
                        payload_fingerprint=fingerprint,
                        occurred_at=occurred_at,
                    )
                )
                row = ProfileEffectProjectionDisposition(
                    proposal_transition=current_transition,
                    sequence=sequence,
                    previous_disposition=current_disposition.disposition,
                    action=action.value,
                    from_state=current_disposition.state.value,
                    to_state=next_state.value,
                    actor=actor,
                    actor_access_epoch=actor.access_epoch,
                    authority_reference=decision.authority_reference,
                    authority_decision_reference=decision.decision_reference,
                    authority_evaluated_at=decision.evaluated_at,
                    request_reference=request_reference,
                    idempotency_key=idempotency_key,
                    payload_fingerprint=fingerprint,
                    occurred_at=occurred_at,
                    lineage_reference=lineage_ref,
                )
                row.save(using=self._alias)
                return ProfileEffectProjectionCommandReceipt(
                    database_alias=self._alias,
                    lineage_id=lineage.lineage_id,
                    proposal_transition_pk=current_transition.pk,
                    projection_disposition_pk=row.pk,
                    action=action,
                    to_state=next_state,
                    projection_lineage_reference=row.lineage_reference,
                    request_reference=request_reference,
                    idempotency_key=idempotency_key,
                    payload_fingerprint=fingerprint,
                    occurred_at=occurred_at,
                    replayed=False,
                )

            return self._try_integrity(write_fn=_write, replay_fn=lambda: self._resolve_projection_replay(existing=(ProfileEffectProjectionDisposition.objects.using(self._alias).select_for_update().get(actor=actor, action=action.value, idempotency_key=idempotency_key)), action=action, actor=actor, lineage=lineage, request_reference=request_reference, idempotency_key=idempotency_key, payload_fingerprint=fingerprint, expected_proposal_transition_pk=command.expected_proposal_transition_pk, expected_proposal_lineage_reference=command.expected_proposal_lineage_reference, expected_disposition_pk_or_null=command.expected_disposition_pk_or_null, expected_disposition_lineage_reference_or_null=command.expected_disposition_lineage_reference_or_null), allowed_constraints=_PROJECTION_APPEND_RACE_CONSTRAINTS)

    def _resolve_projection_replay(self, *, existing: ProfileEffectProjectionDisposition, action: ProjectionAction, actor: Identity, lineage: ProfileEffectProposalLineage, transitions: list[ProfileEffectProposalTransition], grouped: dict[int, list[ProfileEffectProjectionDisposition]], request_reference: str, idempotency_key: str, payload_fingerprint: str, expected_proposal_transition_pk: int, expected_proposal_lineage_reference: str, expected_disposition_pk_or_null: int | None, expected_disposition_lineage_reference_or_null: str | None) -> ProfileEffectProjectionCommandReceipt:
        if existing.proposal_transition.lineage_id != lineage.pk:
            raise ProfileEffectMalformedReplay("replayed projection does not belong to lineage")
        predecessor = existing.previous_disposition
        historical_state = ProjectionState.UNAUTHORISED if predecessor is None else ProjectionState(predecessor.to_state)
        historical_target = projection_authority_target_payload(
            database_alias=self._alias,
            lineage_id=lineage.lineage_id,
            subject_identity_id=lineage.subject.identity_id,
            current_proposal_transition_pk=existing.proposal_transition.pk,
            current_proposal_lineage_reference=existing.proposal_transition.lineage_reference,
            current_disposition_pk_or_null=(predecessor.pk if predecessor else None),
            current_disposition_lineage_reference_or_null=(predecessor.lineage_reference if predecessor else None),
            current_projection_state=historical_state,
            action=action,
        )
        if existing.proposal_transition.pk != expected_proposal_transition_pk or existing.proposal_transition.lineage_reference != expected_proposal_lineage_reference:
            raise ProfileEffectConflict("expected proposal transition does not match historical target")
        if (predecessor.pk if predecessor else None) != expected_disposition_pk_or_null:
            raise ProfileEffectConflict("expected disposition primary key does not match historical target")
        if (predecessor.lineage_reference if predecessor else None) != expected_disposition_lineage_reference_or_null:
            raise ProfileEffectConflict("expected disposition lineage reference does not match historical target")
        target_reference = projection_authority_target_reference(historical_target)
        self._authority.qualify(
            request=ProjectionAuthorityRequest(
                database_alias=self._alias,
                actor_pk=actor.pk,
                actor_identity_id=actor.identity_id,
                actor_access_epoch=actor.access_epoch,
                action=action,
                target_fingerprint=target_reference.split(":", 1)[1],
                request_reference=request_reference,
                idempotency_key=idempotency_key,
                evaluated_at=_validate_occurred_at(self._clock()),
            ),
            target_reference=target_reference,
        )
        if existing.actor_access_epoch != actor.access_epoch:
            raise ProfileEffectCrossEpochConflict("cross-epoch conflict")
        if existing.payload_fingerprint != payload_fingerprint:
            raise ProfileEffectPayloadConflict("payload conflict")
        self._validate_root_alias(lineage)
        self._validate_locked_proposal_lineage_rows(lineage, transitions)
        self._validate_locked_dispositions_by_transition(transitions, grouped)
        if actor.pk != lineage.subject_id:
            raise ProfileEffectActorError("actor must equal subject")
        return ProfileEffectProjectionCommandReceipt(
            database_alias=self._alias,
            lineage_id=lineage.lineage_id,
            proposal_transition_pk=existing.proposal_transition_id,
            projection_disposition_pk=existing.pk,
            action=action,
            to_state=ProjectionState(existing.to_state),
            projection_lineage_reference=existing.lineage_reference,
            request_reference=existing.request_reference,
            idempotency_key=existing.idempotency_key,
            payload_fingerprint=existing.payload_fingerprint,
            occurred_at=existing.occurred_at,
            replayed=True,
        )

    def _next_projection_state(self, action: ProjectionAction, current_state: ProjectionState) -> ProjectionState:
        legal_edges = {
            (ProjectionAction.AUTHORISE_PROJECTION, ProjectionState.UNAUTHORISED): ProjectionState.AUTHORISED,
            (ProjectionAction.AUTHORISE_PROJECTION, ProjectionState.DECLINED): ProjectionState.AUTHORISED,
            (ProjectionAction.AUTHORISE_PROJECTION, ProjectionState.WITHDRAWN): ProjectionState.AUTHORISED,
            (ProjectionAction.DECLINE_PROJECTION, ProjectionState.UNAUTHORISED): ProjectionState.DECLINED,
            (ProjectionAction.WITHDRAW_PROJECTION, ProjectionState.AUTHORISED): ProjectionState.WITHDRAWN,
        }
        try:
            return legal_edges[(action, current_state)]
        except KeyError as exc:
            raise ProfileEffectLifecycleError("projection disposition edge is invalid") from exc

    def _try_integrity(self, *, write_fn, replay_fn, allowed_constraints: frozenset[str]):
        if not _is_postgresql(self._alias):
            return write_fn()
        try:
            with transaction.atomic(using=self._alias):
                return write_fn()
        except IntegrityError as exc:
            constraint_name = self._check_constraint_name(exc)
            if constraint_name in allowed_constraints:
                return replay_fn()
            raise


__all__ = [
    "ProfileEffectActorError",
    "ProfileEffectCommandError",
    "ProfileEffectConflict",
    "ProfileEffectCrossEpochConflict",
    "ProfileEffectLifecycleError",
    "ProfileEffectMalformedReplay",
    "ProfileEffectNotFound",
    "ProfileEffectPayloadConflict",
    "ProfileEffectProjectionDispositionService",
    "ProfileEffectProposalCorrectionService",
    "ProfileEffectValidationError",
    "ServiceSubmissionProfileEffectProposalService",
]