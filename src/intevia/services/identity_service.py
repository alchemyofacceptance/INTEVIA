"""Governed CORE Identity lifecycle and credential composition services."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.identity import canonical_username_v1
from core.models import (
    Identity,
    IdentityTransition,
    OriginatingMembershipProvisioningRequest,
    ProvisioningReconciliationAttempt,
)
from core.organism_contract import (
    ORIGINATING_INTEVIA_LO_REFERENCE,
    ORIGINATING_MEMBERSHIP_CONTRACT_VERSION,
)


class IdentityLifecycleError(Exception):
    """Base failure for governed Identity lifecycle operations."""


class InvalidIdentityTransition(IdentityLifecycleError):
    """Raised when an Identity lifecycle transition is not permitted."""


class MembershipFulfilmentRequired(IdentityLifecycleError):
    """Raised when activation lacks accepted originating-LO evidence."""


class ProvisioningConflict(IdentityLifecycleError):
    """Raised when an active provisioning intent already exists."""


class IdentityLifecycleService:
    _ALLOWED = {
        IdentityTransition.Action.ACTIVATE: {
            Identity.AccessState.PENDING,
        },
        IdentityTransition.Action.RESTRICT: {
            Identity.AccessState.ACTIVE,
        },
        IdentityTransition.Action.DEACTIVATE: {
            Identity.AccessState.PENDING,
            Identity.AccessState.ACTIVE,
            Identity.AccessState.RESTRICTED,
        },
        IdentityTransition.Action.REACTIVATE: {
            Identity.AccessState.DEACTIVATED,
        },
    }

    @staticmethod
    def _text(value: str, name: str, *, max_length: int = 255) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(f"{name} is required")
        value = value.strip()
        if len(value) > max_length:
            raise ValidationError(f"{name} is too long")
        return value

    @staticmethod
    def _at(value: datetime | None) -> datetime:
        value = value or timezone.now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValidationError("occurred_at must be timezone-aware")
        return value

    @staticmethod
    def _locked(identity_id: UUID) -> Identity:
        return Identity.objects.select_for_update().select_related(
            "credential"
        ).get(identity_id=identity_id)

    @classmethod
    def _transition(
        cls,
        *,
        identity: Identity,
        action: str,
        prior_state: str | None,
        resulting_state: str,
        requesting_actor: Identity | None,
        authority_reference: str,
        technical_executor: str,
        evidence_reference: str,
        reason_category: str,
        correlation_id: UUID,
        occurred_at: datetime,
    ) -> IdentityTransition:
        previous = identity.access_transitions.order_by(
            "occurred_at", "pk"
        ).last()
        return IdentityTransition.objects.create(
            identity=identity,
            action=action,
            prior_state=prior_state,
            resulting_state=resulting_state,
            requesting_actor=requesting_actor,
            authority_reference=cls._text(
                authority_reference, "authority_reference"
            ),
            technical_executor=cls._text(
                technical_executor,
                "technical_executor",
                max_length=120,
            ),
            evidence_reference=cls._text(
                evidence_reference, "evidence_reference"
            ),
            reason_category=reason_category,
            correlation_id=correlation_id,
            occurred_at=occurred_at,
            previous_transition=previous,
        )

    @staticmethod
    def _fulfilled_request(identity: Identity, evidence_reference: str):
        request = identity.originating_membership_requests.filter(
            organism_reference=ORIGINATING_INTEVIA_LO_REFERENCE,
            contract_version=ORIGINATING_MEMBERSHIP_CONTRACT_VERSION,
            superseded_at__isnull=True,
        ).first()
        if request is None:
            raise MembershipFulfilmentRequired(
                "originating membership provisioning intent is required"
            )
        fulfilled = request.reconciliation_attempts.filter(
            state=ProvisioningReconciliationAttempt.State.FULFILLED,
            evidence_reference=evidence_reference,
        ).exists()
        if not fulfilled:
            raise MembershipFulfilmentRequired(
                "accepted originating membership fulfilment evidence is required"
            )
        return request

    @classmethod
    @transaction.atomic
    def activate(
        cls,
        *,
        identity_id: UUID,
        fulfilment_evidence_reference: str,
        requesting_actor: Identity | None,
        authority_reference: str,
        technical_executor: str,
        correlation_id: UUID,
        occurred_at: datetime | None = None,
    ) -> IdentityTransition:
        occurred_at = cls._at(occurred_at)
        identity = cls._locked(identity_id)
        cls._require(identity, IdentityTransition.Action.ACTIVATE)
        evidence_reference = cls._text(
            fulfilment_evidence_reference,
            "fulfilment_evidence_reference",
        )
        cls._fulfilled_request(identity, evidence_reference)
        prior = identity.access_state
        identity.access_state = Identity.AccessState.ACTIVE
        identity.activated_at = occurred_at
        identity.restricted_at = None
        identity.deactivated_at = None
        identity.credential.is_active = True
        identity.credential.save(update_fields=("is_active",))
        identity.save(
            update_fields=(
                "access_state",
                "activated_at",
                "restricted_at",
                "deactivated_at",
                "updated_at",
            )
        )
        return cls._transition(
            identity=identity,
            action=IdentityTransition.Action.ACTIVATE,
            prior_state=prior,
            resulting_state=identity.access_state,
            requesting_actor=requesting_actor,
            authority_reference=authority_reference,
            technical_executor=technical_executor,
            evidence_reference=evidence_reference,
            reason_category=IdentityTransition.ReasonCategory.GOVERNED_ACTIVATION,
            correlation_id=correlation_id,
            occurred_at=occurred_at,
        )

    @classmethod
    def _require(cls, identity: Identity, action: str) -> None:
        if identity.access_state not in cls._ALLOWED[action]:
            raise InvalidIdentityTransition(
                f"{action} is not permitted from {identity.access_state}"
            )

    @classmethod
    @transaction.atomic
    def change_access(
        cls,
        *,
        identity_id: UUID,
        action: str,
        requesting_actor: Identity | None,
        authority_reference: str,
        technical_executor: str,
        evidence_reference: str,
        reason_category: str,
        correlation_id: UUID,
        occurred_at: datetime | None = None,
    ) -> IdentityTransition:
        if action not in {
            IdentityTransition.Action.RESTRICT,
            IdentityTransition.Action.DEACTIVATE,
            IdentityTransition.Action.REACTIVATE,
        }:
            raise InvalidIdentityTransition("unsupported access transition")
        occurred_at = cls._at(occurred_at)
        identity = cls._locked(identity_id)
        cls._require(identity, action)
        if action == IdentityTransition.Action.REACTIVATE:
            cls._fulfilled_request(identity, evidence_reference)
        prior = identity.access_state
        resulting = {
            IdentityTransition.Action.RESTRICT: Identity.AccessState.RESTRICTED,
            IdentityTransition.Action.DEACTIVATE: Identity.AccessState.DEACTIVATED,
            IdentityTransition.Action.REACTIVATE: Identity.AccessState.ACTIVE,
        }[action]
        identity.access_state = resulting
        identity.access_epoch += 1
        if resulting == Identity.AccessState.RESTRICTED:
            identity.restricted_at = occurred_at
        elif resulting == Identity.AccessState.DEACTIVATED:
            identity.deactivated_at = occurred_at
            identity.credential.is_active = False
            identity.credential.save(update_fields=("is_active",))
        else:
            identity.activated_at = occurred_at
            identity.restricted_at = None
            identity.deactivated_at = None
            identity.credential.is_active = True
            identity.credential.save(update_fields=("is_active",))
        identity.save()
        return cls._transition(
            identity=identity,
            action=action,
            prior_state=prior,
            resulting_state=resulting,
            requesting_actor=requesting_actor,
            authority_reference=authority_reference,
            technical_executor=technical_executor,
            evidence_reference=evidence_reference,
            reason_category=reason_category,
            correlation_id=correlation_id,
            occurred_at=occurred_at,
        )


class OriginatingMembershipProvisioningService:
    @classmethod
    @transaction.atomic
    def request(
        cls,
        *,
        identity: Identity,
        authority_reference: str,
        evidence_reference: str,
        correlation_id: UUID,
        requested_at: datetime | None = None,
        supersedes: OriginatingMembershipProvisioningRequest | None = None,
    ) -> OriginatingMembershipProvisioningRequest:
        requested_at = IdentityLifecycleService._at(requested_at)
        locked = Identity.objects.select_for_update().get(pk=identity.pk)
        existing = locked.originating_membership_requests.filter(
            organism_reference=ORIGINATING_INTEVIA_LO_REFERENCE,
            superseded_at__isnull=True,
        ).first()
        if existing is not None:
            if supersedes is None or existing.pk != supersedes.pk:
                raise ProvisioningConflict(
                    "an active originating membership intent already exists"
                )
            existing.superseded_at = requested_at
            existing.save(update_fields=("superseded_at",))
        request = OriginatingMembershipProvisioningRequest.objects.create(
            identity=locked,
            organism_reference=ORIGINATING_INTEVIA_LO_REFERENCE,
            contract_version=ORIGINATING_MEMBERSHIP_CONTRACT_VERSION,
            correlation_id=correlation_id,
            authority_reference=IdentityLifecycleService._text(
                authority_reference, "authority_reference"
            ),
            evidence_reference=IdentityLifecycleService._text(
                evidence_reference, "evidence_reference"
            ),
            requested_at=requested_at,
            supersedes=existing,
        )
        ProvisioningReconciliationAttempt.objects.create(
            request=request,
            state=ProvisioningReconciliationAttempt.State.REQUESTED,
            authority_reference=request.authority_reference,
            evidence_reference=request.evidence_reference,
            correlation_id=uuid_for_child(correlation_id, "requested"),
            occurred_at=requested_at,
        )
        return request

    @classmethod
    @transaction.atomic
    def reconcile(
        cls,
        *,
        request_id: int,
        state: str,
        authority_reference: str,
        evidence_reference: str,
        correlation_id: UUID,
        occurred_at: datetime | None = None,
        corrects_attempt: ProvisioningReconciliationAttempt | None = None,
    ) -> ProvisioningReconciliationAttempt:
        occurred_at = IdentityLifecycleService._at(occurred_at)
        request = OriginatingMembershipProvisioningRequest.objects.select_for_update().get(
            pk=request_id,
            superseded_at__isnull=True,
        )
        previous = request.reconciliation_attempts.order_by(
            "occurred_at", "pk"
        ).last()
        if state not in ProvisioningReconciliationAttempt.State.values:
            raise ValidationError("invalid provisioning reconciliation state")
        if state == ProvisioningReconciliationAttempt.State.CORRECTED:
            if corrects_attempt is None:
                raise ValidationError("corrects_attempt is required")
        elif corrects_attempt is not None:
            raise ValidationError("only correction attempts may correct")
        return ProvisioningReconciliationAttempt.objects.create(
            request=request,
            state=state,
            authority_reference=IdentityLifecycleService._text(
                authority_reference, "authority_reference"
            ),
            evidence_reference=IdentityLifecycleService._text(
                evidence_reference, "evidence_reference"
            ),
            correlation_id=correlation_id,
            occurred_at=occurred_at,
            previous_attempt=previous,
            corrects_attempt=corrects_attempt,
        )


class CredentialService:
    @classmethod
    @transaction.atomic
    def provision(
        cls,
        *,
        username: str,
        password: str,
        display_name: str,
        human_classification: str,
        authority_reference: str,
        evidence_reference: str,
        correlation_id: UUID,
        technical_executor: str,
        occurred_at: datetime | None = None,
    ) -> tuple[Identity, OriginatingMembershipProvisioningRequest]:
        if human_classification != "HUMAN DEVELOPMENT IDENTITY":
            raise ValidationError("explicit Human classification is required")
        occurred_at = IdentityLifecycleService._at(occurred_at)
        canonical = canonical_username_v1(username)
        credential = User(
            username=username,
            is_active=False,
        )
        validate_password(password, user=credential)
        credential.set_password(password)
        credential.full_clean()
        credential.save()
        identity = Identity.objects.create(
            credential=credential,
            display_name=display_name.strip(),
            canonical_username=canonical,
        )
        request = OriginatingMembershipProvisioningService.request(
            identity=identity,
            authority_reference=authority_reference,
            evidence_reference=evidence_reference,
            correlation_id=correlation_id,
            requested_at=occurred_at,
        )
        IdentityLifecycleService._transition(
            identity=identity,
            action=IdentityTransition.Action.PROVISION,
            prior_state=None,
            resulting_state=Identity.AccessState.PENDING,
            requesting_actor=None,
            authority_reference=authority_reference,
            technical_executor=technical_executor,
            evidence_reference=evidence_reference,
            reason_category=IdentityTransition.ReasonCategory.PROVISIONING,
            correlation_id=uuid_for_child(correlation_id, "identity"),
            occurred_at=occurred_at,
        )
        return identity, request

    @classmethod
    @transaction.atomic
    def update_username(
        cls,
        *,
        identity_id: UUID,
        username: str,
        requesting_actor: Identity | None,
        authority_reference: str,
        evidence_reference: str,
        correlation_id: UUID,
        technical_executor: str,
        occurred_at: datetime | None = None,
    ) -> IdentityTransition:
        occurred_at = IdentityLifecycleService._at(occurred_at)
        identity = IdentityLifecycleService._locked(identity_id)
        canonical = canonical_username_v1(username)
        identity.credential.username = username
        identity.credential.full_clean()
        identity.credential.save(update_fields=("username",))
        identity.canonical_username = canonical
        identity.save(update_fields=("canonical_username", "updated_at"))
        return IdentityLifecycleService._transition(
            identity=identity,
            action=IdentityTransition.Action.UPDATE_USERNAME,
            prior_state=identity.access_state,
            resulting_state=identity.access_state,
            requesting_actor=requesting_actor,
            authority_reference=authority_reference,
            technical_executor=technical_executor,
            evidence_reference=evidence_reference,
            reason_category=IdentityTransition.ReasonCategory.CREDENTIAL_CHANGE,
            correlation_id=correlation_id,
            occurred_at=occurred_at,
        )

    @classmethod
    @transaction.atomic
    def replace(
        cls,
        *,
        identity_id: UUID,
        replacement_username: str,
        replacement_password: str,
        requesting_actor: Identity | None,
        authority_reference: str,
        evidence_reference: str,
        correlation_id: UUID,
        technical_executor: str,
        occurred_at: datetime | None = None,
    ) -> IdentityTransition:
        occurred_at = IdentityLifecycleService._at(occurred_at)
        identity = IdentityLifecycleService._locked(identity_id)
        previous_credential = identity.credential
        replacement = User(
            username=replacement_username,
            is_active=identity.access_state != Identity.AccessState.DEACTIVATED,
        )
        validate_password(replacement_password, user=replacement)
        replacement.set_password(replacement_password)
        replacement.full_clean()
        replacement.save()
        identity.credential = replacement
        identity.canonical_username = canonical_username_v1(
            replacement_username
        )
        identity.access_epoch += 1
        identity.save()
        previous_credential.is_active = False
        previous_credential.set_unusable_password()
        previous_credential.save(update_fields=("is_active", "password"))
        return IdentityLifecycleService._transition(
            identity=identity,
            action=IdentityTransition.Action.REPLACE_CREDENTIAL,
            prior_state=identity.access_state,
            resulting_state=identity.access_state,
            requesting_actor=requesting_actor,
            authority_reference=authority_reference,
            technical_executor=technical_executor,
            evidence_reference=evidence_reference,
            reason_category=IdentityTransition.ReasonCategory.CREDENTIAL_CHANGE,
            correlation_id=correlation_id,
            occurred_at=occurred_at,
        )

    @classmethod
    @transaction.atomic
    def retire(
        cls,
        *,
        identity_id: UUID,
        requesting_actor: Identity | None,
        authority_reference: str,
        evidence_reference: str,
        correlation_id: UUID,
        technical_executor: str,
        occurred_at: datetime | None = None,
    ) -> IdentityTransition:
        occurred_at = IdentityLifecycleService._at(occurred_at)
        identity = IdentityLifecycleService._locked(identity_id)
        prior = identity.access_state
        identity.access_state = Identity.AccessState.DEACTIVATED
        identity.access_epoch += 1
        identity.deactivated_at = occurred_at
        identity.credential.is_active = False
        identity.credential.set_unusable_password()
        identity.credential.save(update_fields=("is_active", "password"))
        identity.save()
        return IdentityLifecycleService._transition(
            identity=identity,
            action=IdentityTransition.Action.RETIRE_CREDENTIAL,
            prior_state=prior,
            resulting_state=identity.access_state,
            requesting_actor=requesting_actor,
            authority_reference=authority_reference,
            technical_executor=technical_executor,
            evidence_reference=evidence_reference,
            reason_category=IdentityTransition.ReasonCategory.CREDENTIAL_CHANGE,
            correlation_id=correlation_id,
            occurred_at=occurred_at,
        )


def uuid_for_child(parent: UUID, label: str) -> UUID:
    from uuid import uuid5

    return uuid5(parent, label)


__all__ = [
    "CredentialService",
    "IdentityLifecycleError",
    "IdentityLifecycleService",
    "InvalidIdentityTransition",
    "MembershipFulfilmentRequired",
    "OriginatingMembershipProvisioningService",
    "ProvisioningConflict",
]