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
    """Base for the service-defined profile-effect refusal classes.

    Shared with profile-effect readback. It is not raised directly in
    these paths and does not cover every exception they can emit:
    initializer errors, delegated errors and unhandled database errors
    can propagate outside this hierarchy.
    """
    pass


class ProfileEffectConflict(ProfileEffectCommandError):
    """The request conflicts with the lineage's current or recorded state.

    Raised directly when an expected head, proposal transition or
    disposition does not match; its subclasses name the other cases.
    """
    pass


class ProfileEffectCrossEpochConflict(ProfileEffectConflict):
    """The actor's access epoch differs from the one the request or the
    replayed row carries."""
    pass


class ProfileEffectPayloadConflict(ProfileEffectConflict):
    """A replayed request key whose payload fingerprint differs from the
    stored row's."""
    pass


class ProfileEffectMalformedReplay(ProfileEffectConflict):
    """Stored profile-effect rows that fail re-derivation or a structural
    check (a stored-data fault, not a caller error)."""
    pass


class ProfileEffectValidationError(ProfileEffectCommandError):
    """Invalid input form or submission qualification mismatch.

    Used for UUID, bounded-reference and aware-datetime checks, and for
    a qualification alias mismatch or actor_equals_assignee not being
    exact True. Also used when bounded references in a stored root,
    proposal transition or projection disposition fail validation.
    """
    pass


class ProfileEffectNotFound(ProfileEffectCommandError):
    """The requested profile-effect lineage does not exist."""
    pass


class ProfileEffectActorError(ProfileEffectCommandError):
    """The command actor or readback viewer fails an identity check.

    Used when no Identity is found, its access state or credential is
    inactive, or the actor/viewer does not match the required subject.
    Proposal correction also requires the actor to be the proposer;
    creation checks the qualified actor and subject bindings.
    """
    pass


class ProfileEffectLifecycleError(ProfileEffectCommandError):
    """The lineage's state does not allow the requested transition."""
    pass


def _is_postgresql(alias: str) -> bool:
    """Return True when the alias's configured ENGINE names postgresql.

    Reads the settings only; opens no connection.
    """
    return "postgresql" in connections[alias].settings_dict.get("ENGINE", "")


def _validate_uuid(value: object, name: str) -> UUID:
    """Return value if it is a UUID (subclasses accepted), else raise
    ProfileEffectValidationError naming the field."""
    if not isinstance(value, UUID):
        raise ProfileEffectValidationError(f"{name} must be a UUID")
    return value


def _validate_reference(value: str, name: str, maximum: int) -> str:
    """Return bounded_reference(value, name, maximum), the stripped text.

    Its ValueError is re-raised as ProfileEffectValidationError. A
    padded value is accepted and returned stripped; this helper does not
    refuse the change.
    """
    try:
        return bounded_reference(value, name, maximum)
    except ValueError as exc:
        raise ProfileEffectValidationError(str(exc)) from exc


def _validate_occurred_at(value: datetime) -> datetime:
    """Return a timezone-aware datetime converted to UTC.

    A non-datetime, or one without tzinfo or a UTC offset, raises
    ProfileEffectValidationError. Subclasses of datetime are accepted.
    The services also apply it to their own clock's reading.
    """
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ProfileEffectValidationError("occurred_at must be timezone-aware")
    return value.astimezone(timezone.utc)


def _proposal_command_payload(*, action: ProposalAction, database_alias: str, actor_identity_id: UUID, actor_access_epoch: int, request_reference: str, idempotency_key: str, occurred_at: datetime, qualification: ServiceSubmissionQualificationDTO | None = None, lineage_id: UUID | None = None, expected_head_transition_pk: int | None = None, expected_head_lineage_reference: str | None = None, subject_identity_id: UUID) -> dict[str, object]:
    """Build the proposal payload used for command fingerprinting.

    Include the actor, subject, request identifiers, alias, contract
    constants and canonical occurred_at. A non-None qualification adds
    the five source fields; otherwise include lineage_id and the two
    expected-head fields. This branch does not check the action.

    No command, qualification or lineage consistency is checked here.
    IDs are stringified, so lineage_id None becomes "None". Timestamp
    validation and conversion errors propagate; action needs a value
    attribute. This helper builds the payload but does not hash it.
    """
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
    """Build the payload for a proposal transition's lineage reference.

    Include lineage, sequence, action, actor identity and epoch, command
    fingerprint and canonical occurred_at, with the proposal-lineage
    schema. No lineage, sequence or action consistency is checked.
    Timestamp validation and conversion errors propagate. The caller
    hashes the returned payload.
    """
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
    """Build the projection payload used for command fingerprinting.

    Include action, actor identity and epoch, subject, alias, lineage,
    request identifiers, canonical occurred_at and contract constants.
    Copy the expected proposal key and reference and the two expected
    disposition values, including None, without checking their pairing.

    No command or target consistency is checked. Timestamp validation
    and conversion errors propagate; action needs a value attribute.
    The caller hashes the returned payload.
    """
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
    """Build the payload for a projection disposition's lineage reference.

    Include proposal transition key, sequence, action, actor identity
    and epoch, command fingerprint and canonical occurred_at, with the
    projection-lineage schema. No sequence, action or row consistency
    is checked. Timestamp validation and conversion errors propagate.
    The caller hashes the returned payload.
    """
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
    """A proposal transition's projection state and the disposition row
    that set it, or (UNAUTHORISED, None) when it has none."""
    state: ProjectionState
    disposition: ProfileEffectProjectionDisposition | None


class _ProfileEffectServiceBase:
    """Shared machinery of the profile-effect services.

    Holds the clock and database alias, and provides actor and lineage
    locking, stored-row validation and the rebuilding of stored payloads
    and authority targets used by replay. Used by the three command
    services and by ProfileEffectReadService. It records nothing itself
    and opens no transaction; callers hold one.
    """
    def __init__(self, *, clock: Callable[[], datetime], database_alias: str = "default") -> None:
        """Keep the clock and the database alias.

        clock must be callable (TypeError); it is not called here.
        database_alias must be a non-empty exact str (ValueError); it is not
        resolved here.
        """
        if not callable(clock):
            raise TypeError("clock must be callable")
        if type(database_alias) is not str or not database_alias:
            raise ValueError("database_alias is required")
        self._clock = clock
        self._alias = database_alias

    def _lock_actor(self, credential: User) -> Identity:
        """Lock and return the Identity of a credential (select_for_update).

        No Identity raises ProfileEffectActorError; an inactive Identity or
        credential also raises it (_require_active_identity). The access
        epoch is not checked here.
        """
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
        """Require an ACTIVE access state and an active credential, else
        ProfileEffectActorError naming the label."""
        if identity.access_state != Identity.AccessState.ACTIVE:
            raise ProfileEffectActorError(f"{label} Identity is not active")
        if not identity.credential.is_active:
            raise ProfileEffectActorError(f"{label} credential is not active")

    def _require_epoch(self, identity: Identity, expected_epoch: int) -> None:
        """Raise ProfileEffectCrossEpochConflict unless the identity's
        access epoch equals expected_epoch."""
        if identity.access_epoch != expected_epoch:
            raise ProfileEffectCrossEpochConflict("cross-epoch conflict")

    def _lock_lineage(self, lineage_id: UUID) -> ProfileEffectProposalLineage:
        """Lock and return the proposal lineage with this lineage_id.

        None found raises ProfileEffectNotFound. The lineage is not
        validated, and its subject is not compared with any actor, here.
        """
        try:
            return (
                ProfileEffectProposalLineage.objects.using(self._alias)
                .select_for_update()
                .get(lineage_id=lineage_id)
            )
        except ProfileEffectProposalLineage.DoesNotExist as exc:
            raise ProfileEffectNotFound("Profile effect lineage not found") from exc

    def _require_digest_reference(self, value: str, prefix: str, label: str) -> None:
        """Require an exact str that starts with prefix and continues with a
        64-character lowercase hex digest; else ProfileEffectMalformedReplay
        naming the label. The digest is not recomputed here.
        """
        if type(value) is not str or not value.startswith(prefix):
            raise ProfileEffectMalformedReplay(f"{label} is malformed")
        digest = value[len(prefix) :]
        self._require_hex_digest(digest, label)

    def _require_hex_digest(self, value: str, label: str) -> None:
        """Require an exact str of 64 lowercase hex characters, else
        ProfileEffectMalformedReplay naming the label."""
        if type(value) is not str or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ProfileEffectMalformedReplay(f"{label} is malformed")

    def _validate_root_record(self, lineage: ProfileEffectProposalLineage) -> None:
        """Check a stored lineage root's fixed fields.

        Raises ProfileEffectMalformedReplay when the subject is not the
        proposer; the contract version is not CONTRACT_VERSION; the subject
        relation or effect type is not the fixed value; created_at is after
        updated_at; or the source transition or qualification reference is
        not "s012l1:" or "s012sq1:" plus a hex digest. source_database_alias
        (64) and source_authority_reference (255) must pass
        bounded_reference; a failure there raises
        ProfileEffectValidationError instead. A padded stored value passes,
        as the stripped result is not compared.

        Does not compare the alias with this service's (_validate_root_alias
        does) or recompute any digest.
        """
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
        """Rebuild the 17-key create payload for a fingerprint comparison.

        Use source and subject fields from the lineage, and actor, epoch,
        request identifiers and occurred_at from the transition. Use this
        service's alias, the current command schema and contract version,
        and fixed CREATE_PROPOSAL; use stored relation and effect values.

        The key set matches the qualified proposal builder. Value equality
        requires matching stored source, subject and request values and the
        same constants and alias. No row consistency is checked here.
        Timestamp validation and conversion errors propagate. The caller
        hashes this payload and compares it with the stored fingerprint.
        """
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
        """Rebuild the 15-key correction payload for fingerprint comparison.

        Use the transition's action, actor, epoch, request reference, key
        and occurred_at; the lineage and subject; and the supplied
        predecessor's key and reference as the expected head.
        Use this service's alias, the current schema and contract version,
        and stored relation and effect values.

        The key set matches the unqualified proposal builder. Value equality
        requires the same request values and historical head. This helper
        checks neither predecessor linkage nor row consistency. Timestamp
        validation and conversion errors propagate; hashing and comparison
        belong to the caller.
        """
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
        """Rebuild the 17-key projection payload for fingerprint comparison.

        Use the disposition's action, actor, epoch, request identifiers and
        occurred_at, the lineage and subject, and the proposal transition's
        key and reference. Expected disposition values come from the
        supplied predecessor, or are both None when it is absent.
        Use this service's alias, current schema and contract version, and
        stored relation and effect values.

        The key set matches the live builder. Matching values depend on the
        same request and historical targets; no linkage or row consistency
        is checked here. Timestamp validation and conversion errors
        propagate; hashing and comparison belong to the caller.
        """
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
        """Rebuild the expected historical proposal authority target.

        With no predecessor, use this alias, the stored qualification
        reference and subject to form a create target. Otherwise use the
        same alias, the lineage, subject, predecessor key, reference and
        state, stored transition action, and has_current_survivor=True for a
        correction.

        Unknown state or action values raise ValueError during enum
        conversion on the correction branch. Canonical hashing errors also
        propagate. No linkage, legal edge or authority permission is checked
        here. The caller validates the rows and compares authority evidence;
        this result alone does not prove an authorization.
        """
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
        """Rebuild the expected historical projection authority target.

        Use this alias, lineage and subject, the supplied proposal key and
        reference, and the predecessor disposition's key and reference, or
        None for both. The historical state is UNAUTHORISED without a
        predecessor, otherwise its to_state; action comes from the row.

        Unknown state or action values raise ValueError during enum
        conversion. Canonical hashing errors also propagate. No linkage,
        legal edge or authority permission is checked here. The caller
        validates the rows and compares authority evidence; this reference
        alone does not prove an authorization.
        """
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
        """Re-derive one stored proposal transition, or refuse it.

        Require the supplied lineage and its subject/proposer as actor.
        An initial row must be sequence 1, CREATE_PROPOSAL, from None to
        ACTIVE, at the root's created_at. A successor must increment the
        sequence, start from its predecessor's state and not precede it;
        its ACTIVE predecessor permits SUPERSEDE to ACTIVE or VOID to VOIDED.
        Structural or recomputation mismatches raise
        ProfileEffectMalformedReplay.

        Unknown actions raise ValueError. A CREATE_PROPOSAL successor of
        an ACTIVE predecessor raises KeyError; a non-ACTIVE predecessor
        is refused before that map lookup. Authority evaluation must not
        follow occurred_at. Bounded authority/request/key references can
        raise ProfileEffectValidationError; padding passes that check.
        Check digest forms, then payload, lineage and decision references
        against their reconstructions, in that order. Comparison,
        timestamp and canonical-hashing errors propagate.

        The caller validates the root, predecessor chain and previous link.
        This method does not check the root head or survivor flag, call the
        provider, or establish that the stored decision was actually made.
        """
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
        """Re-derive one stored projection disposition, or refuse it.

        Require the supplied proposal transition and the lineage's subject
        as actor. An initial row must be sequence 1 from UNAUTHORISED; a
        successor must increment sequence, start from its predecessor's
        state and not precede it. The row must not precede its proposal,
        and authority evaluation must not follow occurred_at.

        Then check bounded authority/request/key references and digest
        forms, the legal state edge, and the reconstructed payload, lineage
        and decision references. Structural or recomputation mismatches
        raise ProfileEffectMalformedReplay. Bounded references can raise
        ProfileEffectValidationError; padding passes that individual check.

        Unknown action or from_state values raise ValueError if their enum
        conversion is reached. Invalid initial from_state and invalid
        to_state are refused as ProfileEffectMalformedReplay instead.
        Comparison, timestamp and canonical-hashing errors propagate.

        The caller checks previous_disposition linkage and the proposal
        chain. This method does not validate the supplied predecessor,
        check that the proposal belongs to the supplied lineage, establish
        current-survivor status at recording time, or call the provider.
        """
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
        """Lock and return a lineage's proposal transitions, unvalidated.

        Ordered by sequence then key, with actor and lineage loaded. None
        found raises ProfileEffectMalformedReplay. Callers validate the rows
        afterwards (_validate_locked_proposal_lineage_rows).
        """
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
        """Validate the supplied root and ordered proposal chain.

        First validate the root. Then require each previous_transition_id
        to name the preceding row, or None for the first, and validate each
        row. Link mismatches raise ProfileEffectMalformedReplay; delegated
        validation errors propagate. Finally require the root's head,
        survivor flag and updated_at to match the last row, its ACTIVE
        state and its occurred_at, else ProfileEffectMalformedReplay.

        The caller must supply the complete, ordered, non-empty locked
        list. This method neither queries for omitted rows nor verifies
        locks or sorts the list. An empty list reaches IndexError after
        root validation succeeds. Alias and dispositions are checked
        separately.
        """
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
        """Lock a lineage's proposal transitions, validate them, and return
        them in order. Used by the subject read."""
        rows = self._lock_proposal_lineage_rows_for_replay(lineage)
        self._validate_locked_proposal_lineage_rows(lineage, rows)
        return rows

    def _lock_dispositions_by_transition_for_replay(self, transitions: list[ProfileEffectProposalTransition]) -> dict[int, list[ProfileEffectProjectionDisposition]]:
        """Lock the dispositions of the given transitions, unvalidated.

        Returns a dict from each transition's key to its dispositions,
        ordered by sequence then key (an empty list when it has none), with
        actor and transition lineage loaded. Callers validate them
        afterwards (_validate_locked_dispositions_by_transition).
        """
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
        """Validate the supplied disposition chains for the given transitions.

        For each transition, use its group in the supplied order. Require
        each previous_disposition_id to name the preceding row, or None
        for the first, else ProfileEffectMalformedReplay. Validate each row
        against that transition and transition.lineage; delegated errors
        propagate. Missing groups and empty groups pass; extra groups are
        ignored.

        The caller supplies complete, ordered groups and validated proposal
        transitions. This method does not query for missing dispositions,
        sort, verify locks, validate the proposal chain or establish that
        a proposal was current when each disposition was recorded.
        """
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
        """Lock the transitions' dispositions, validate them, and return
        them grouped by transition key. Used by the subject read."""
        grouped = self._lock_dispositions_by_transition_for_replay(transitions)
        self._validate_locked_dispositions_by_transition(transitions, grouped)
        return grouped

    def _current_disposition_state(self, transition: ProfileEffectProposalTransition, grouped: dict[int, list[ProfileEffectProjectionDisposition]]) -> _CurrentDispositionState:
        """Return the projection state implied by the supplied group.

        A missing or empty group gives UNAUTHORISED and no disposition.
        Otherwise convert the last row's to_state to ProjectionState and
        return it with that row; an unknown state raises ValueError.
        The caller supplies ordered, validated rows. This helper does not
        sort, check linkage or decide whether the proposal is current.
        """
        rows = grouped.get(transition.pk, [])
        if not rows:
            return _CurrentDispositionState(ProjectionState.UNAUTHORISED, None)
        last = rows[-1]
        return _CurrentDispositionState(ProjectionState(last.to_state), last)

    def _validate_root_alias(self, lineage: ProfileEffectProposalLineage) -> None:
        """Raise ProfileEffectMalformedReplay unless the lineage's stored
        source_database_alias equals this service's alias."""
        if lineage.source_database_alias != self._alias:
            raise ProfileEffectMalformedReplay("stored source database alias mismatch")

    def _check_constraint_name(self, exc: IntegrityError) -> str | None:
        """Return the database constraint an IntegrityError names, or None.

        Reads exc.__cause__.diag.constraint_name, which the PostgreSQL
        driver supplies; any missing step gives None.
        """
        cause = getattr(exc, "__cause__", None)
        diag = getattr(cause, "diag", None) if cause else None
        return getattr(diag, "constraint_name", None) if diag else None

    def _validate_proposal_authority_evidence(
        self,
        transition: ProfileEffectProposalTransition,
        actor_identity_id: UUID,
        target_reference: str,
    ) -> None:
        """Compare the stored proposal decision reference with reconstruction.

        Build a ProposalAuthorityResponse from this alias, the row's actor
        key, epoch, action, request reference, key, evaluation time and
        authority reference, plus the supplied identity and target suffix.
        Use everything after the first colon as target_fingerprint; no
        prefix or digest-form check occurs here. A string without a colon
        raises IndexError; unknown action conversion raises ValueError.

        Hash that response and raise ProfileEffectMalformedReplay if the
        stored decision reference differs. Timestamp and canonical-hashing
        errors propagate. The caller supplies validated rows and target.
        A match establishes consistency with those supplied values, not
        that a provider decided, that permission exists now, or that the
        supplied identity and target were independently authenticated.
        No provider is called or re-qualified here.
        """
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
        """Compare the stored projection decision reference with reconstruction.

        Build a ProjectionAuthorityResponse from this alias, the row's
        actor key, epoch, action, request reference, key, evaluation time
        and authority reference, plus the supplied identity and target
        suffix. Use everything after the first colon as target_fingerprint;
        no prefix or digest-form check occurs here. A string without a
        colon raises IndexError; unknown action conversion raises ValueError.

        Hash that response and raise ProfileEffectMalformedReplay if the
        stored decision reference differs. Timestamp and canonical-hashing
        errors propagate. The caller supplies validated rows and target.
        A match establishes consistency with those supplied values, not
        that a provider decided, that permission exists now, or that the
        supplied identity and target were independently authenticated.
        No provider is called or re-qualified here.
        """
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
    """Create a profile-effect proposal from a qualified submission.

    The subject of a submitted SERVICE activity records one neutral
    proposal lineage for it. The service reads the submission's
    qualification, asks the injected ProposalAuthority, and writes the
    lineage root and its initial transition. It interprets nothing
    about the profile.
    """
    def __init__(
        self,
        *,
        authority: ProposalAuthority,
        read_service: ServiceActivityReadService,
        clock: Callable[[], datetime],
        database_alias: str = "default",
    ) -> None:
        """Keep the authority, the read service, the clock and the alias.

        The clock and alias are checked as in the base class. authority
        must be a ProposalAuthority and read_service a
        ServiceActivityReadService (isinstance; TypeError otherwise), and
        both must use the same alias (ValueError).
        """
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
        """Create, or replay, the proposal for a submission, inside one
        atomic block on this alias. See _execute_create."""
        with transaction.atomic(using=self._alias):
            return self._execute_create(command)

    def _execute_create(
        self,
        command: CreateServiceSubmissionProposalCommand,
    ) -> ProfileEffectProposalCommandReceipt:
        """Create or replay a proposal within the caller's transaction.

        Validate the activity UUID, strip bounded request reference (128)
        and key (120), and convert aware occurred_at to UTC; invalid forms
        raise ProfileEffectValidationError. Obtain submission qualification,
        lock the active actor and check its epoch, subject/actor bindings,
        qualification alias and exact-True actor_equals_assignee.

        Check any matching lineage's alias, fingerprint the command, and
        replay a create found by actor, action and key. Otherwise qualify
        the create target using the clock's current reading, then save a
        new root, initial transition and root head through _try_integrity.
        Authority and read-service refusals propagate. A decision alias
        unlike the qualification's raises ProposalAuthorityNotAuthorised.

        Model ValidationError and unhandled database errors propagate.
        Authority evaluation must not follow occurred_at at model save.
        An existing lineage does not itself trigger replay or an early
        second-key refusal; recovery may fail to find a row for that key.
        This method neither replaces the command time nor opens its own
        outer transaction.
        """
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
        """Return a create replay after current authority and history checks.

        Within the caller's transaction, load and lock the row's lineage
        and histories. Missing root lookup errors propagate; an empty
        proposal history raises ProfileEffectMalformedReplay. Subject or
        qualification mismatch, or a noninitial replay row, also raises it.

        Build the historical create target and qualify again at the clock's
        reading. Then compare stored epoch and payload fingerprint, raising
        ProfileEffectCrossEpochConflict or ProfileEffectPayloadConflict,
        before validating alias and the whole loaded history. Delegated
        conversion, authority and validation errors propagate.

        Return a replayed receipt for the stored event, not today's head.
        The caller selects the actor/action/key row and checks actor activity
        and command epoch. This method appends no profile-effect record;
        it does call the provider again.
        """
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
        """Attempt the write and recover selected PostgreSQL constraint errors.

        When the configured ENGINE does not name postgresql, call write_fn
        without this helper's atomic block or recovery. Otherwise call it
        inside atomic on this alias, a savepoint within the normal caller's
        transaction. On IntegrityError, recover only when the diagnostic
        constraint name is in allowed_constraints; re-raise other cases.

        Call replay_fn after leaving the failed inner block. A recognized
        constraint does not guarantee that replay_fn finds a matching row
        or succeeds. Its exceptions propagate, as do ValidationError and
        other unhandled write errors. This helper neither loops nor maps
        all duplicate writes to a service refusal.
        """
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
    """Void or supersede a lineage's current proposal.

    The subject, who is also the proposer, appends one transition to
    the current ACTIVE head after the injected ProposalAuthority agrees:
    VOID ends it (VOIDED), SUPERSEDE keeps it ACTIVE as a new head.
    """
    def __init__(self, *, authority: ProposalAuthority, clock: Callable[[], datetime], database_alias: str = "default") -> None:
        """Keep the authority, clock and alias. authority must be a
        ProposalAuthority (TypeError) on the same alias (ValueError)."""
        super().__init__(clock=clock, database_alias=database_alias)
        if not isinstance(authority, ProposalAuthority):
            raise TypeError("authority must be a ProposalAuthority")
        if authority.database_alias != database_alias:
            raise ValueError("authority database_alias mismatch")
        self._authority = authority

    def void_profile_effect_proposal(self, command: ProfileEffectProposalCorrectionCommand) -> ProfileEffectProposalCommandReceipt:
        """Void the current proposal (VOID_PROPOSAL); see
        _execute_correction."""
        return self._execute_correction(command, ProposalAction.VOID_PROPOSAL)

    def supersede_profile_effect_proposal(self, command: ProfileEffectProposalCorrectionCommand) -> ProfileEffectProposalCommandReceipt:
        """Supersede the current proposal (SUPERSEDE_PROPOSAL); see
        _execute_correction."""
        return self._execute_correction(command, ProposalAction.SUPERSEDE_PROPOSAL)

    def _execute_correction(self, command: ProfileEffectProposalCorrectionCommand, action: ProposalAction) -> ProfileEffectProposalCommandReceipt:
        """Append or replay a proposal correction inside an atomic block.

        Before entering it, validate lineage UUID, strip bounded request
        reference (128) and key (120), and convert aware occurrence to UTC.
        Then lock the active actor, check command epoch, lock the root and
        histories, fingerprint the command, and replay a matching
        actor/action/key row if found.

        For a new request validate alias and histories, require subject and
        proposer, an ACTIVE current survivor, and the expected current head.
        Qualify the correction target at the clock's reading, then save a
        new transition and the root's head, flag and time via _try_integrity.

        Model ValidationError is not converted: save checks predecessor
        and authority time ordering, among other row rules. Authority,
        history, database and recovery lookup errors also propagate.
        The service does not replace occurred_at or compare it with the
        head before qualification. The public methods supply the correction
        action; a fresh write must pass the model and database rules too.
        """
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

            def _winner() -> ProfileEffectProposalCommandReceipt:
                winner_lineage = self._lock_lineage(lineage.lineage_id)
                winner_transitions = self._lock_proposal_lineage_rows_for_replay(
                    winner_lineage
                )
                winner_grouped = self._lock_dispositions_by_transition_for_replay(
                    winner_transitions
                )
                existing_winner = (
                    ProfileEffectProposalTransition.objects.using(self._alias)
                    .select_for_update()
                    .get(
                        actor=actor,
                        action=action.value,
                        idempotency_key=idempotency_key,
                    )
                )
                return self._resolve_correction_replay(
                    existing=existing_winner,
                    action=action,
                    actor=actor,
                    lineage=winner_lineage,
                    transitions=winner_transitions,
                    grouped=winner_grouped,
                    expected_head_transition_pk=command.expected_head_transition_pk,
                    expected_head_lineage_reference=command.expected_head_lineage_reference,
                    request_reference=request_reference,
                    idempotency_key=idempotency_key,
                    payload_fingerprint=fingerprint,
                )

            return self._try_integrity(
                write_fn=_write,
                replay_fn=_winner,
                allowed_constraints=_PROPOSAL_APPEND_RACE_CONSTRAINTS,
            )

    def _resolve_correction_replay(self, *, existing: ProfileEffectProposalTransition, action: ProposalAction, actor: Identity, lineage: ProfileEffectProposalLineage, transitions: list[ProfileEffectProposalTransition], grouped: dict[int, list[ProfileEffectProjectionDisposition]], expected_head_transition_pk: int, expected_head_lineage_reference: str, request_reference: str, idempotency_key: str, payload_fingerprint: str) -> ProfileEffectProposalCommandReceipt:
        """Return a correction replay against its historical predecessor.

        Use the caller's transaction and locked histories. Wrong lineage
        or missing predecessor raises ProfileEffectMalformedReplay.
        Build the historical target; an unknown predecessor state raises
        ValueError. Expected head mismatch raises ProfileEffectConflict.

        Qualify again at the current clock reading, then compare stored
        epoch and fingerprint, raising ProfileEffectCrossEpochConflict or
        ProfileEffectPayloadConflict. Validate alias and both histories,
        then require actor to be subject and proposer (ProfileEffectActorError).
        Delegated conversion, authority and validation errors propagate.

        Return the stored event's replayed receipt, not current head state.
        The caller selects the actor/action/key row and checks activity and
        command epoch. No profile-effect record is appended here; the
        provider is called again.
        """
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
        """Attempt the write and recover selected PostgreSQL constraint errors.

        Use the proposal service's _try_integrity semantics: a recognized
        constraint invokes replay_fn once, without guaranteeing a matching
        row or successful replay. ValidationError, unrecognized database
        errors and recovery errors propagate.
        """
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
    """Authorise, decline or withdraw projection of a current proposal.

    The subject appends one disposition to the current ACTIVE proposal
    transition after the injected ProjectionAuthority agrees. The legal
    edges are in _next_projection_state. A disposition records the
    subject's choice only; nothing here changes a profile.
    """
    def __init__(self, *, authority: ProjectionAuthority, clock: Callable[[], datetime], database_alias: str = "default") -> None:
        """Keep the authority, clock and alias. authority must be a
        ProjectionAuthority (TypeError) on the same alias (ValueError)."""
        super().__init__(clock=clock, database_alias=database_alias)
        if not isinstance(authority, ProjectionAuthority):
            raise TypeError("authority must be a ProjectionAuthority")
        if authority.database_alias != database_alias:
            raise ValueError("authority database_alias mismatch")
        self._authority = authority

    def authorise_profile_effect_projection(self, command: ProfileEffectProjectionDispositionCommand) -> ProfileEffectProjectionCommandReceipt:
        """Authorise projection (AUTHORISE_PROJECTION); see
        _execute_projection."""
        return self._execute_projection(command, ProjectionAction.AUTHORISE_PROJECTION)

    def decline_profile_effect_projection(self, command: ProfileEffectProjectionDispositionCommand) -> ProfileEffectProjectionCommandReceipt:
        """Decline projection (DECLINE_PROJECTION); see _execute_projection."""
        return self._execute_projection(command, ProjectionAction.DECLINE_PROJECTION)

    def withdraw_profile_effect_projection(self, command: ProfileEffectProjectionDispositionCommand) -> ProfileEffectProjectionCommandReceipt:
        """Withdraw projection (WITHDRAW_PROJECTION); see
        _execute_projection."""
        return self._execute_projection(command, ProjectionAction.WITHDRAW_PROJECTION)

    def _execute_projection(self, command: ProfileEffectProjectionDispositionCommand, action: ProjectionAction) -> ProfileEffectProjectionCommandReceipt:
        """Append or replay a projection disposition inside an atomic block.

        Before entering it, validate lineage UUID, strip bounded request
        reference (128) and key (120), and convert aware occurrence to UTC.
        Then lock the active actor, check command epoch, lock the lineage
        (ProfileEffectNotFound if absent) and histories, and fingerprint the
        command. A matching actor/action/key row goes to the replay resolver.

        For a new request validate alias and histories, require the subject,
        an ACTIVE current survivor and the expected proposal/disposition.
        Qualify the target at the clock's reading, then check the legal
        edge; an illegal edge raises ProfileEffectLifecycleError.
        Save the new disposition through _try_integrity.

        Model ValidationError is not converted. Save checks predecessor,
        proposal and authority time ordering as well as other row rules.
        Authority, history, database and recovery lookup errors propagate.
        No profile-effect root head is updated and no current time is
        substituted for the command's occurred_at.
        """
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

            def _winner() -> ProfileEffectProjectionCommandReceipt:
                winner_lineage = self._lock_lineage(lineage.lineage_id)
                winner_transitions = self._lock_proposal_lineage_rows_for_replay(
                    winner_lineage
                )
                winner_grouped = self._lock_dispositions_by_transition_for_replay(
                    winner_transitions
                )
                existing_winner = (
                    ProfileEffectProjectionDisposition.objects.using(self._alias)
                    .select_for_update()
                    .get(
                        actor=actor,
                        action=action.value,
                        idempotency_key=idempotency_key,
                    )
                )
                return self._resolve_projection_replay(
                    existing=existing_winner,
                    action=action,
                    actor=actor,
                    lineage=winner_lineage,
                    transitions=winner_transitions,
                    grouped=winner_grouped,
                    request_reference=request_reference,
                    idempotency_key=idempotency_key,
                    payload_fingerprint=fingerprint,
                    expected_proposal_transition_pk=command.expected_proposal_transition_pk,
                    expected_proposal_lineage_reference=command.expected_proposal_lineage_reference,
                    expected_disposition_pk_or_null=command.expected_disposition_pk_or_null,
                    expected_disposition_lineage_reference_or_null=command.expected_disposition_lineage_reference_or_null,
                )

            return self._try_integrity(
                write_fn=_write,
                replay_fn=_winner,
                allowed_constraints=_PROJECTION_APPEND_RACE_CONSTRAINTS,
            )

    def _resolve_projection_replay(self, *, existing: ProfileEffectProjectionDisposition, action: ProjectionAction, actor: Identity, lineage: ProfileEffectProposalLineage, transitions: list[ProfileEffectProposalTransition], grouped: dict[int, list[ProfileEffectProjectionDisposition]], request_reference: str, idempotency_key: str, payload_fingerprint: str, expected_proposal_transition_pk: int, expected_proposal_lineage_reference: str, expected_disposition_pk_or_null: int | None, expected_disposition_lineage_reference_or_null: str | None) -> ProfileEffectProjectionCommandReceipt:
        """Return a disposition replay against its historical target.

        Use the caller's transaction and locked histories. A row whose
        proposal belongs to another lineage raises ProfileEffectMalformedReplay.
        Build the historical target from its proposal and predecessor. With
        no predecessor (a first disposition) the historical state is
        UNAUTHORISED and nothing is refused; an unknown predecessor state
        raises ValueError. Expected proposal
        or disposition mismatch raises ProfileEffectConflict.

        Qualify again at the current clock reading, then compare stored
        epoch and fingerprint, raising ProfileEffectCrossEpochConflict or
        ProfileEffectPayloadConflict. Validate alias and both histories,
        then require the actor to be the subject (ProfileEffectActorError).
        Delegated conversion, authority and validation errors propagate.

        Return the stored event's replayed receipt, not today's projection
        state. The caller selects the actor/action/key row and checks actor
        activity and command epoch. No profile-effect record is appended
        here; the provider is called again.
        """
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
        """Return the state an action leads to from the current state.

        AUTHORISE leads to AUTHORISED from UNAUTHORISED, DECLINED or
        WITHDRAWN; DECLINE to DECLINED from UNAUTHORISED; WITHDRAW to
        WITHDRAWN from AUTHORISED. Any other pair raises
        ProfileEffectLifecycleError.
        """
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
        """Attempt the write and recover selected PostgreSQL constraint errors.

        Use the proposal service's _try_integrity semantics: a recognized
        constraint invokes replay_fn once, without guaranteeing a matching
        row or successful replay. ValidationError, unrecognized database
        errors and recovery errors propagate.
        """
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