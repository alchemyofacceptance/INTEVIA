"""Governed CORE Identity lifecycle and credential composition services."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone as datetime_timezone
from uuid import UUID, uuid4

from django.contrib.auth.models import User
from django.contrib.auth.hashers import check_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import Q
from django.utils import timezone

from core.identity import canonical_username_v1
from core.models import (
    Identity,
    IdentityTransition,
    LearnerRegistration,
    OriginatingMembershipProvisioningRequest,
    PlatformTermsAcceptance,
    PlatformTermsVersion,
    ProvisioningReconciliationAttempt,
)
from core.organism_contract import (
    ORIGINATING_INTEVIA_LO_REFERENCE,
    ORIGINATING_MEMBERSHIP_CONTRACT_VERSION,
)
from src.intevia.services.organism_application_authority import (
    fixture_terms_permitted,
)


class IdentityLifecycleError(Exception):
    """Base failure for governed Identity lifecycle operations."""


class InvalidIdentityTransition(IdentityLifecycleError):
    """Raised when an Identity lifecycle transition is not permitted."""


class MembershipFulfilmentRequired(IdentityLifecycleError):
    """Raised when activation lacks accepted originating-LO evidence."""


class ProvisioningConflict(IdentityLifecycleError):
    """Raised when an active provisioning intent already exists."""


class CredentialServiceRefused(IdentityLifecycleError):
    """Raised when a credential operation is refused by policy."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class RegistrationServiceRefused(IdentityLifecycleError):
    """Raised when a registration request is refused by policy."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        reason: str | None = None,
        submitted_version: PlatformTermsVersion | None = None,
        submitted_version_uuid: UUID | None = None,
        current_version: PlatformTermsVersion | None = None,
        effective_at: datetime | None = None,
        received_at: datetime | None = None,
    ):
        self.code = code
        self.message = message
        self.reason = reason
        self.submitted_version = submitted_version
        self.submitted_version_uuid = (
            submitted_version.terms_version_uuid
            if submitted_version is not None
            else submitted_version_uuid
        )
        self.current_version = current_version
        self.effective_at = effective_at
        self.received_at = received_at
        super().__init__(message)


class IdentityLifecycleRefused(IdentityLifecycleError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


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
        received_at: datetime | None = None,
    ) -> IdentityTransition:
        previous = identity.access_transitions.order_by(
            "occurred_at", "pk"
        ).last()
        transition = IdentityTransition.objects.create(
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
            effective_at=occurred_at,
            received_at=(
                received_at if received_at is not None else timezone.now()
            ),
            previous_transition=previous,
        )
        transition.refresh_from_db()
        return transition

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
        proof_password: str | None = None,
    ) -> IdentityTransition:
        if action not in {
            IdentityTransition.Action.RESTRICT,
            IdentityTransition.Action.DEACTIVATE,
            IdentityTransition.Action.REACTIVATE,
        }:
            raise InvalidIdentityTransition("unsupported access transition")
        occurred_at = cls._at(occurred_at)
        identity = cls._locked(identity_id)
        if action == IdentityTransition.Action.REACTIVATE:
            if identity.access_state != Identity.AccessState.DEACTIVATED:
                raise IdentityLifecycleRefused(
                    "REACT_NOT_DEACTIVATED",
                    "Nothing was changed: this account is not deactivated, "
                    "so there is nothing to reactivate. Sign in normally.",
                )
            if not isinstance(proof_password, str) or not proof_password or not check_password(
                proof_password,
                identity.credential.password,
            ):
                raise IdentityLifecycleRefused(
                    "REACT_PROOF_OF_CONTROL_REQUIRED",
                    "Your account was not reactivated and nothing changed. "
                    "INTEVIA restores access only after you prove control of "
                    "the account; the sign-in you provided did not succeed. "
                    "Try again with your account's password. If you no longer "
                    "hold a working sign-in method, assisted recovery is not "
                    "yet available (RB-4 refuses) — nothing can be done "
                    "through INTEVIA at present.",
                )
            registration = LearnerRegistration.objects.filter(
                identity=identity
            ).first()
            if registration is None:
                raise IdentityLifecycleRefused(
                    "E_NOT_REGISTERED",
                    "Your account was not reactivated and nothing changed. "
                    "No governed registration record was found for this identity.",
                )
            authority_reference = (
                f"learner-registration:{registration.registration_uuid}"
            )
            evidence_reference = f"reauthentication:password:{correlation_id}"
            requesting_actor = identity
            technical_executor = "identity-service"
            reason_category = IdentityTransition.ReasonCategory.HUMAN_REQUEST
        else:
            if proof_password is not None:
                raise ValidationError(
                    "proof_password is only accepted for reactivation"
                )
            cls._require(identity, action)
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


class LearnerRegistrationService:
    _USERNAME_CONSTRAINTS = {
        "auth_user_username_key",
        "core_identity_canonical_username_key",
        "core_identityresolution_canonical_username_key",
    }

    @staticmethod
    def _username_conflict(error: IntegrityError) -> bool:
        cause = error.__cause__
        diagnostic = getattr(cause, "diag", None)
        constraint_name = getattr(diagnostic, "constraint_name", None)
        return constraint_name in LearnerRegistrationService._USERNAME_CONSTRAINTS

    @staticmethod
    def _unique_field_error(error: ValidationError, field: str) -> bool:
        field_errors = getattr(error, "error_dict", {}).get(field, ())
        return any(item.code == "unique" for item in field_errors)

    @staticmethod
    def _username_unavailable(username: str, canonical_username: str) -> bool:
        if User.objects.filter(username=username).exists():
            return True
        if Identity.objects.filter(canonical_username=canonical_username).exists():
            return True
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT EXISTS ("
                "SELECT 1 FROM public.core_identityresolution "
                "WHERE canonical_username = %s"
                ")",
                [canonical_username],
            )
            return cursor.fetchone()[0]

    @staticmethod
    def _current_terms_version(
        *,
        terms_version_uuid: UUID | None,
        effective_at: datetime,
        received_at: datetime,
    ) -> PlatformTermsVersion | None:
        requested_version = (
            PlatformTermsVersion.objects.filter(
                terms_version_uuid=terms_version_uuid,
                effective_from__lte=effective_at,
                recorded_at__lte=received_at,
            )
            .filter(
                Q(effective_until__isnull=True)
                | Q(effective_until__gt=effective_at)
            )
            .first()
            if terms_version_uuid is not None
            else None
        )
        current_version = LearnerRegistrationService._latest_terms_version(
            effective_at=effective_at,
            received_at=received_at,
        )
        if (
            requested_version is None
            or current_version is None
            or requested_version.pk != current_version.pk
        ):
            return None
        return requested_version

    @staticmethod
    def _latest_terms_version(
        *,
        effective_at: datetime,
        received_at: datetime,
    ) -> PlatformTermsVersion | None:
        eligible_versions = PlatformTermsVersion.objects.filter(
            effective_from__lte=effective_at,
            recorded_at__lte=received_at,
        ).filter(
            Q(effective_until__isnull=True)
            | Q(effective_until__gt=effective_at)
        )
        superseded_ids = eligible_versions.filter(
            supersedes__isnull=False,
        ).values_list("supersedes_id", flat=True)
        current_version = (
            eligible_versions.exclude(pk__in=superseded_ids)
            .order_by("-effective_from", "-recorded_at", "-pk")
            .first()
        )
        return current_version

    @classmethod
    def _terms_version_refusal(
        cls,
        *,
        reason: str,
        submitted_version: PlatformTermsVersion | None,
        submitted_version_uuid: UUID | None = None,
        current_version: PlatformTermsVersion | None,
        effective_at: datetime,
        received_at: datetime,
    ) -> RegistrationServiceRefused:
        if reason == "SUPERSEDED_AT_RECEIPT":
            if submitted_version is None or current_version is None:
                raise ValueError(
                    "SUPERSEDED_AT_RECEIPT requires submitted and current terms versions"
                )
            message = (
                "Registration was not completed and nothing was recorded: no account "
                "was created and no acceptance was saved. When INTEVIA received your "
                "registration, the platform terms you accepted "
                f"({submitted_version.version_label}) were no longer in force; "
                f"{current_version.version_label} was in force instead. Your "
                "acceptance has not been applied to the newer terms. Please read "
                "the terms now in force and accept them to register."
            )
        elif reason == "NOT_YET_IN_FORCE":
            if submitted_version is None:
                raise ValueError(
                    "NOT_YET_IN_FORCE requires a submitted terms version"
                )
            message = (
                "Registration was not completed and nothing was recorded: no account "
                "was created and no acceptance was saved. When INTEVIA received your "
                f"registration, the platform terms you accepted "
                f"({submitted_version.version_label}) had not yet come into force. "
                "Please read the terms now in force and accept them to register."
            )
        elif reason == "NOT_FOUND_AT_RECEIPT":
            message = (
                "Registration was not completed and nothing was recorded: no account "
                "was created and no acceptance was saved. INTEVIA could not find the "
                "platform terms your registration referred to among the terms known "
                "when it received your registration. Please read the terms now in "
                "force and accept them to register."
            )
        elif reason == "NONE_IN_FORCE":
            message = (
                "Registration was not completed and nothing was recorded: no account "
                "was created and no acceptance was saved. No platform terms were in "
                "force when INTEVIA received your registration, so registration is "
                "not available yet. Please try again later."
            )
        else:
            raise ValueError(f"Unsupported registration terms refusal reason: {reason}")
        return RegistrationServiceRefused(
            "REG_TERMS_VERSION_NOT_IN_FORCE",
            message,
            reason=reason,
            submitted_version=submitted_version,
            submitted_version_uuid=submitted_version_uuid,
            current_version=current_version,
            effective_at=effective_at,
            received_at=received_at,
        )

    @staticmethod
    def _acceptance_commitment(
        *,
        terms_version: PlatformTermsVersion,
        subject_reference: str,
        identity: Identity,
        accepted_at: datetime,
    ) -> str:
        accepted_at_utc = accepted_at.astimezone(datetime_timezone.utc)
        payload = {
            "accepted_at": accepted_at_utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "acceptance_kind": PlatformTermsAcceptance.Kind.REGISTRATION,
            "epoch": identity.access_epoch,
            "identity_id": identity.pk,
            "subject_reference": subject_reference,
            "terms_version_uuid": str(terms_version.terms_version_uuid),
        }
        preimage = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return "s015r1:" + hashlib.sha256(preimage.encode("utf-8")).hexdigest()

    @classmethod
    @transaction.atomic
    def register(
        cls,
        *,
        username: str,
        password: str,
        display_name: str,
        terms_version_uuid: UUID | None,
        terms_accepted: bool,
        correlation_id: UUID,
        entry_mode: str = LearnerRegistration.EntryMode.SELF,
        enterer_executor: str | None = None,
    ) -> LearnerRegistration:
        if entry_mode == LearnerRegistration.EntryMode.SELF:
            if enterer_executor not in (None, "web:register_learner"):
                raise ValidationError(
                    "self-registration executor is fixed to web:register_learner"
                )
            resolved_executor = "web:register_learner"
        elif entry_mode == LearnerRegistration.EntryMode.SUPPORTED:
            resolved_executor = enterer_executor or "management:provision_learner"
            if resolved_executor not in {
                "management:provision_learner",
                "test:registration_fixture",
            }:
                raise ValidationError("unsupported supported-registration executor")
            if (
                resolved_executor == "test:registration_fixture"
                and not fixture_terms_permitted("default")
            ):
                raise RegistrationServiceRefused(
                    "REG_TERMS_TEST_ONLY",
                    "Registration was not completed and no account was created. "
                    "The platform terms set up here are a test-only version, which "
                    "cannot be used to register a real account. Nothing can be done "
                    "from this page; the platform's operators must publish the real "
                    "platform terms first. (Operator: use a disposable development "
                    "database, or publish a real terms version.)",
                )
        else:
            raise ValidationError("invalid registration entry mode")
        try:
            return cls._register(
                username=username,
                password=password,
                display_name=display_name,
                terms_version_uuid=terms_version_uuid,
                terms_accepted=terms_accepted,
                correlation_id=correlation_id,
                entry_mode=entry_mode,
                enterer_executor=resolved_executor,
            )
        except IntegrityError as error:
            if not cls._username_conflict(error):
                raise
            raise RegistrationServiceRefused(
                "REG_USERNAME_UNAVAILABLE",
                "Registration was not completed and no account was created. "
                "The username you chose is already in use. Choose a different "
                "username and submit again.",
            ) from error

    @classmethod
    def _register(
        cls,
        *,
        username: str,
        password: str,
        display_name: str,
        terms_version_uuid: UUID | None,
        terms_accepted: bool,
        correlation_id: UUID,
        entry_mode: str,
        enterer_executor: str,
    ) -> LearnerRegistration:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO public.core_registrationreceipt DEFAULT VALUES "
                "RETURNING id, received_at, transaction_id"
            )
            receipt_id, received_at, _transaction_id = cursor.fetchone()
        effective_at = received_at
        if not terms_accepted:
            raise RegistrationServiceRefused(
                "REG_TERMS_NOT_ACCEPTED",
                "Registration was not completed and no account was created. "
                "INTEVIA records that you accepted the platform terms in force, "
                "and no acceptance was received. Read the terms shown and "
                "confirm your acceptance to continue.",
                effective_at=effective_at,
                received_at=received_at,
            )

        submitted_version = (
            PlatformTermsVersion.objects.filter(
                terms_version_uuid=terms_version_uuid
            ).first()
            if terms_version_uuid is not None
            else None
        )
        known_submitted_version = (
            PlatformTermsVersion.objects.filter(
                terms_version_uuid=terms_version_uuid,
                recorded_at__lte=received_at,
            ).first()
            if terms_version_uuid is not None
            else None
        )
        current_version = cls._latest_terms_version(
            effective_at=effective_at,
            received_at=received_at,
        )
        if known_submitted_version is None:
            reason = (
                "NONE_IN_FORCE"
                if current_version is None and terms_version_uuid is None
                else "NOT_FOUND_AT_RECEIPT"
            )
            raise cls._terms_version_refusal(
                reason=reason,
                submitted_version=submitted_version,
                submitted_version_uuid=terms_version_uuid,
                current_version=current_version,
                effective_at=effective_at,
                received_at=received_at,
            )
        if known_submitted_version.effective_from > effective_at:
            raise cls._terms_version_refusal(
                reason="NOT_YET_IN_FORCE",
                submitted_version=known_submitted_version,
                current_version=current_version,
                effective_at=effective_at,
                received_at=received_at,
            )
        if current_version is None:
            raise cls._terms_version_refusal(
                reason="NONE_IN_FORCE",
                submitted_version=known_submitted_version,
                current_version=None,
                effective_at=effective_at,
                received_at=received_at,
            )
        if current_version.pk != known_submitted_version.pk:
            raise cls._terms_version_refusal(
                reason="SUPERSEDED_AT_RECEIPT",
                submitted_version=known_submitted_version,
                current_version=current_version,
                effective_at=effective_at,
                received_at=received_at,
            )
        terms_version = known_submitted_version
        if terms_version is None:
            raise AssertionError("current terms selection returned no submitted version")
        if terms_version.is_fixture and not fixture_terms_permitted("default"):
            raise RegistrationServiceRefused(
                "REG_TERMS_TEST_ONLY",
                "Registration was not completed and no account was created. "
                "The platform terms set up here are a test-only version, which "
                "cannot be used to register a real account. Nothing can be done "
                "from this page; the platform's operators must publish the real "
                "platform terms first. (Operator: use a disposable development "
                "database, or publish a real terms version.)",
                submitted_version=terms_version,
                current_version=current_version,
                effective_at=effective_at,
                received_at=received_at,
            )

        canonical_username = canonical_username_v1(username)
        if cls._username_unavailable(username, canonical_username):
            raise RegistrationServiceRefused(
                "REG_USERNAME_UNAVAILABLE",
                "Registration was not completed and no account was created. "
                "The username you chose is already in use. Choose a different "
                "username and submit again.",
                submitted_version=terms_version,
                current_version=current_version,
                effective_at=effective_at,
                received_at=received_at,
            )

        credential = User(username=username, is_active=True)
        try:
            validate_password(password, user=credential)
        except ValidationError as error:
            reason = "; ".join(error.messages)
            raise RegistrationServiceRefused(
                "REG_PASSWORD_REJECTED",
                "Registration was not completed and no account was created. "
                "The password did not meet INTEVIA's password requirements: "
                f"{reason}. Choose a password that meets them.",
                submitted_version=terms_version,
                current_version=current_version,
                effective_at=effective_at,
                received_at=received_at,
            ) from error
        credential.set_password(password)
        try:
            credential.full_clean()
        except ValidationError as error:
            if cls._unique_field_error(error, "username"):
                raise RegistrationServiceRefused(
                    "REG_USERNAME_UNAVAILABLE",
                    "Registration was not completed and no account was created. "
                    "The username you chose is already in use. Choose a "
                    "different username and submit again.",
                    submitted_version=terms_version,
                    current_version=current_version,
                    effective_at=effective_at,
                    received_at=received_at,
                ) from error
            raise
        credential.save()

        identity = Identity(
            credential=credential,
            display_name=display_name.strip(),
            canonical_username=canonical_username,
        )
        try:
            identity.save(force_insert=True)
        except ValidationError as error:
            if cls._unique_field_error(error, "canonical_username"):
                raise RegistrationServiceRefused(
                    "REG_USERNAME_UNAVAILABLE",
                    "Registration was not completed and no account was created. "
                    "The username you chose is already in use. Choose a "
                    "different username and submit again.",
                    submitted_version=terms_version,
                    current_version=current_version,
                    effective_at=effective_at,
                    received_at=received_at,
                ) from error
            raise

        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO public.core_identityresolution "
                "(identity_id, display_name, canonical_username, credential_link) "
                "VALUES (%s, %s, %s, %s)",
                (
                    identity.pk,
                    identity.display_name,
                    identity.canonical_username,
                    identity.credential_id,
                ),
            )

        registration_uuid = uuid4()
        acceptance_uuid = uuid4()
        subject_reference = f"learner-registration:{registration_uuid}"
        evidence_reference = f"platform-terms-acceptance:{acceptance_uuid}"
        PlatformTermsAcceptance.objects.create(
            acceptance_uuid=acceptance_uuid,
            terms_version=terms_version,
            acceptance_kind=PlatformTermsAcceptance.Kind.REGISTRATION,
            subject_reference=subject_reference,
            accepted_by_identity=identity,
            accepted_by_access_epoch=identity.access_epoch,
            accepted_at=effective_at,
            received_at=received_at,
            acceptance_commitment=cls._acceptance_commitment(
                terms_version=terms_version,
                subject_reference=subject_reference,
                identity=identity,
                accepted_at=effective_at,
            ),
        )
        registration = LearnerRegistration.objects.create(
            registration_uuid=registration_uuid,
            identity=identity,
            actor_access_epoch=identity.access_epoch,
            entry_mode=entry_mode,
            enterer_executor=enterer_executor,
            authority_reference=(
                f"platform-terms-version:{terms_version.terms_version_uuid}"
                "#registration"
            ),
            evidence_reference=evidence_reference,
            registration_receipt_id=receipt_id,
            effective_at=effective_at,
            received_at=received_at,
        )

        authority_reference = f"learner-registration:{registration_uuid}"
        for action, prior_state, resulting_state, label in (
            (
                IdentityTransition.Action.PROVISION,
                None,
                Identity.AccessState.PENDING,
                "provision",
            ),
            (
                IdentityTransition.Action.ACTIVATE,
                Identity.AccessState.PENDING,
                Identity.AccessState.ACTIVE,
                "activate",
            ),
        ):
            if action == IdentityTransition.Action.ACTIVATE:
                identity.access_state = resulting_state
                identity.activated_at = effective_at
                identity.save(
                    update_fields=("access_state", "activated_at", "updated_at")
                )
            IdentityLifecycleService._transition(
                identity=identity,
                action=action,
                prior_state=prior_state,
                resulting_state=resulting_state,
                requesting_actor=identity,
                authority_reference=authority_reference,
                technical_executor="identity-service",
                evidence_reference=evidence_reference,
                reason_category=IdentityTransition.ReasonCategory.GOVERNED_ACTIVATION,
                correlation_id=uuid_for_child(correlation_id, label),
                occurred_at=effective_at,
                received_at=received_at,
            )
        registration.refresh_from_db()
        return registration


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
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO public.core_identityresolution "
                "(identity_id, display_name, canonical_username, credential_link) "
                "VALUES (%s, %s, %s, %s)",
                (
                    identity.pk,
                    identity.display_name,
                    identity.canonical_username,
                    identity.credential_id,
                ),
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
        raise CredentialServiceRefused(
            "USERNAME_CHANGE_UNAVAILABLE",
            "Your username was not changed. Usernames in INTEVIA are fixed once "
            "registered, because your records rely on them staying the same. "
            "Your display name can still be changed.",
        )

    @classmethod
    @transaction.atomic
    def change_password(
        cls,
        *,
        identity_id: UUID,
        new_password: str,
        requesting_actor: Identity | None,
        session_access_epoch: int,
        correlation_id: UUID,
        occurred_at: datetime | None = None,
    ) -> IdentityTransition:
        occurred_at = IdentityLifecycleService._at(occurred_at)
        identity = IdentityLifecycleService._locked(identity_id)
        if requesting_actor is None or requesting_actor.pk != identity.pk:
            raise CredentialServiceRefused(
                "PASSWORD_CHANGE_UNAUTHORIZED",
                "Only the identity holder can change this account's password.",
            )
        if identity.access_state not in {
            Identity.AccessState.ACTIVE,
            Identity.AccessState.RESTRICTED,
        }:
            raise InvalidIdentityTransition(
                f"password change is not permitted from {identity.access_state}"
            )
        if session_access_epoch != identity.access_epoch:
            raise CredentialServiceRefused(
                "PASSWORD_CHANGE_SESSION_STALE",
                "Your password was not changed and nothing changed. Your session "
                "is out of date because your account's security settings changed; "
                "sign in again, then try again.",
            )
        try:
            validate_password(new_password, user=identity.credential)
        except ValidationError as error:
            raise CredentialServiceRefused(
                "PASSWORD_REJECTED",
                "; ".join(error.messages),
            ) from error

        identity.credential.set_password(new_password)
        identity.credential.save(update_fields=("password",))
        identity.access_epoch += 1
        identity.save(update_fields=("access_epoch", "updated_at"))
        return IdentityLifecycleService._transition(
            identity=identity,
            action=IdentityTransition.Action.REPLACE_CREDENTIAL,
            prior_state=identity.access_state,
            resulting_state=identity.access_state,
            requesting_actor=identity,
            authority_reference="self-service:password-change",
            technical_executor="identity-service",
            evidence_reference=f"password-change:{correlation_id}",
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
        IdentityLifecycleService._locked(identity_id)
        raise CredentialServiceRefused(
            "CREDENTIAL_ACCOUNT_SWAP_UNAVAILABLE",
            "Your sign-in account was not replaced and nothing changed. "
            "Replacing the sign-in account itself is not available yet; you "
            "can change your password instead.",
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
    "CredentialServiceRefused",
    "IdentityLifecycleError",
    "IdentityLifecycleService",
    "InvalidIdentityTransition",
    "MembershipFulfilmentRequired",
    "OriginatingMembershipProvisioningService",
    "ProvisioningConflict",
]