"""Transactional orchestration for the governed Contribution lifecycle."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import (
    Contribution,
    ContributionDecision,
    ContributionTransition,
    ContributionVersion,
    EvidenceReference,
)
from src.intevia.services.contribution_authority import ContributionAuthority


class ContributionServiceError(Exception):
    """Base failure for Contribution orchestration."""


class InvalidContributionTransition(ContributionServiceError):
    """Raised when a command is invalid for the current lifecycle state."""


class LegalHoldPreventsErasure(ContributionServiceError):
    """Raised when protected content is under legal hold."""


class ContributionService:
    """Persist authority-gated lifecycle changes and lineage atomically."""

    def __init__(self, *, authority: ContributionAuthority) -> None:
        if not isinstance(authority, ContributionAuthority):
            raise TypeError("authority must be a ContributionAuthority")
        self.authority = authority

    @staticmethod
    def _at(value: datetime | None) -> datetime:
        value = value or timezone.now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValidationError("timestamp must be timezone-aware")
        return value

    @staticmethod
    def _text(value: str, name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(f"{name} is required")
        return value.strip()

    @staticmethod
    def _locked(contribution_id: str) -> Contribution:
        return (
            Contribution.objects.select_for_update()
            .select_related("contributor", "current_version")
            .get(contribution_id=contribution_id)
        )

    @staticmethod
    def _require(contribution: Contribution, *states: str) -> None:
        if contribution.state not in states:
            raise InvalidContributionTransition(
                f"command not permitted from {contribution.state}"
            )

    def _record_transition(
        self,
        *,
        contribution: Contribution,
        version: ContributionVersion,
        prior: str,
        new: str,
        command: str,
        actor,
        authority_reference: str,
        occurred_at: datetime,
    ) -> ContributionTransition:
        previous = contribution.transitions.order_by("occurred_at", "pk").last()
        prior = prior.value if hasattr(prior, "value") else prior
        new = new.value if hasattr(new, "value") else new
        return ContributionTransition.objects.create(
            contribution=contribution,
            version=version,
            from_state=prior,
            to_state=new,
            command=command,
            actor=actor,
            authority_reference=authority_reference,
            occurred_at=occurred_at,
            previous_transition=previous,
            lineage_reference=f"transition:{uuid4()}",
        )

    @staticmethod
    def _record_evidence(
        *,
        contribution: Contribution,
        version: ContributionVersion,
        actor,
        reference: str,
        reference_type: str,
        decision: ContributionDecision | None = None,
    ) -> EvidenceReference:
        return EvidenceReference.objects.create(
            contribution=contribution,
            version=version,
            decision=decision,
            reference=ContributionService._text(reference, "evidence_reference"),
            reference_type=reference_type,
            added_by=actor,
        )

    @transaction.atomic
    def create_contribution(
        self,
        *,
        identity: User,
        contribution_id: str,
        content: str,
        occurred_at: datetime | None = None,
    ) -> Contribution:
        occurred_at = self._at(occurred_at)
        contribution_id = self._text(contribution_id, "contribution_id")
        content = self._text(content, "content")
        actor, authority_reference = self.authority.evaluate(
            identity=identity,
            action="create_contribution",
            target=contribution_id,
            timestamp=occurred_at,
        )
        contribution = Contribution.objects.create(
            contribution_id=contribution_id,
            contributor=actor,
        )
        version = ContributionVersion.objects.create(
            contribution=contribution,
            version_number=1,
            content=content,
            created_by=actor,
        )
        contribution.current_version = version
        contribution.full_clean()
        contribution.save(update_fields=("current_version", "updated_at"))
        self._record_transition(
            contribution=contribution,
            version=version,
            prior="creation",
            new=Contribution.State.DRAFT,
            command="create_contribution",
            actor=actor,
            authority_reference=authority_reference,
            occurred_at=occurred_at,
        )
        return contribution

    def _move(
        self,
        *,
        identity: User,
        contribution_id: str,
        command: str,
        allowed: tuple[str, ...],
        new_state: str,
        occurred_at: datetime | None,
    ) -> ContributionTransition:
        occurred_at = self._at(occurred_at)
        with transaction.atomic():
            contribution = self._locked(contribution_id)
            self._require(contribution, *allowed)
            actor, authority_reference = self.authority.evaluate(
                identity=identity,
                action=command,
                target=contribution,
                timestamp=occurred_at,
            )
            prior = contribution.state
            contribution.state = new_state
            contribution.save(update_fields=("state", "updated_at"))
            return self._record_transition(
                contribution=contribution,
                version=contribution.current_version,
                prior=prior,
                new=new_state,
                command=command,
                actor=actor,
                authority_reference=authority_reference,
                occurred_at=occurred_at,
            )

    def submit_contribution(self, *, identity: User, contribution_id: str, occurred_at=None):
        return self._move(
            identity=identity,
            contribution_id=contribution_id,
            command="submit_contribution",
            allowed=(Contribution.State.DRAFT, Contribution.State.CORRECTION_PENDING_REVIEW),
            new_state=Contribution.State.SUBMITTED,
            occurred_at=occurred_at,
        )

    def begin_review(self, *, identity: User, contribution_id: str, occurred_at=None):
        return self._move(
            identity=identity,
            contribution_id=contribution_id,
            command="begin_review",
            allowed=(Contribution.State.SUBMITTED,),
            new_state=Contribution.State.UNDER_REVIEW,
            occurred_at=occurred_at,
        )

    @transaction.atomic
    def record_human_decision(
        self,
        *,
        identity: User,
        contribution_id: str,
        decision_type: str,
        evidence_reference: str,
        rationale_reference: str | None = None,
        occurred_at: datetime | None = None,
    ) -> ContributionDecision:
        outcomes = {
            "accepted": Contribution.State.ACCEPTED,
            "rejected": Contribution.State.REJECTED,
        }
        if decision_type not in outcomes:
            raise ValidationError("decision_type must be accepted or rejected")
        occurred_at = self._at(occurred_at)
        contribution = self._locked(contribution_id)
        self._require(contribution, Contribution.State.UNDER_REVIEW)
        actor, authority_reference = self.authority.evaluate(
            identity=identity,
            action=f"{decision_type}_contribution",
            target=contribution.current_version,
            timestamp=occurred_at,
        )
        if actor.pk == contribution.contributor_id:
            raise ValidationError("a contributor cannot decide their own Contribution")
        decision = ContributionDecision.objects.create(
            contribution=contribution,
            version=contribution.current_version,
            decision_actor=actor,
            decision_type=decision_type,
            authority_reference=authority_reference,
            rationale_reference=rationale_reference,
            decided_at=occurred_at,
        )
        self._record_evidence(
            contribution=contribution,
            version=contribution.current_version,
            actor=actor,
            reference=evidence_reference,
            reference_type="decision",
            decision=decision,
        )
        prior = contribution.state
        contribution.state = outcomes[decision_type]
        contribution.save(update_fields=("state", "updated_at"))
        self._record_transition(
            contribution=contribution,
            version=contribution.current_version,
            prior=prior,
            new=contribution.state,
            command=f"{decision_type}_contribution",
            actor=actor,
            authority_reference=authority_reference,
            occurred_at=occurred_at,
        )
        return decision

    @transaction.atomic
    def request_correction(
        self, *, identity: User, contribution_id: str, evidence_reference: str, occurred_at=None
    ) -> ContributionTransition:
        occurred_at = self._at(occurred_at)
        contribution = self._locked(contribution_id)
        self._require(contribution, Contribution.State.ACCEPTED, Contribution.State.REJECTED)
        actor, authority_reference = self.authority.evaluate(
            identity=identity,
            action="request_correction",
            target=contribution.current_version,
            timestamp=occurred_at,
        )
        self._record_evidence(
            contribution=contribution,
            version=contribution.current_version,
            actor=actor,
            reference=evidence_reference,
            reference_type="correction_request",
        )
        prior = contribution.state
        contribution.state = Contribution.State.CORRECTION_REQUESTED
        contribution.save(update_fields=("state", "updated_at"))
        return self._record_transition(
            contribution=contribution,
            version=contribution.current_version,
            prior=prior,
            new=contribution.state,
            command="request_correction",
            actor=actor,
            authority_reference=authority_reference,
            occurred_at=occurred_at,
        )

    @transaction.atomic
    def create_correction(
        self, *, identity: User, contribution_id: str, content: str, occurred_at=None
    ) -> ContributionVersion:
        occurred_at = self._at(occurred_at)
        contribution = self._locked(contribution_id)
        self._require(contribution, Contribution.State.CORRECTION_REQUESTED)
        predecessor = ContributionVersion.objects.select_for_update().get(
            pk=contribution.current_version_id
        )
        actor, authority_reference = self.authority.evaluate(
            identity=identity,
            action="create_correction",
            target=predecessor,
            timestamp=occurred_at,
        )
        if actor.pk != contribution.contributor_id:
            raise ValidationError("only the contributor may create a correction")
        successor = ContributionVersion.objects.create(
            contribution=contribution,
            version_number=predecessor.version_number + 1,
            content=self._text(content, "content"),
            supersedes=predecessor,
            created_by=actor,
        )
        predecessor.state = ContributionVersion.State.SUPERSEDED
        predecessor.save(update_fields=("state",))
        prior = contribution.state
        contribution.current_version = successor
        contribution.state = Contribution.State.CORRECTION_PENDING_REVIEW
        contribution.save(update_fields=("current_version", "state", "updated_at"))
        self._record_transition(
            contribution=contribution,
            version=successor,
            prior=prior,
            new=contribution.state,
            command="create_correction",
            actor=actor,
            authority_reference=authority_reference,
            occurred_at=occurred_at,
        )
        return successor

    def withdraw_contribution(self, *, identity: User, contribution_id: str, occurred_at=None):
        return self._move(
            identity=identity,
            contribution_id=contribution_id,
            command="withdraw_contribution",
            allowed=(
                Contribution.State.DRAFT,
                Contribution.State.SUBMITTED,
                Contribution.State.UNDER_REVIEW,
                Contribution.State.CORRECTION_REQUESTED,
                Contribution.State.CORRECTION_PENDING_REVIEW,
            ),
            new_state=Contribution.State.WITHDRAWN,
            occurred_at=occurred_at,
        )

    @transaction.atomic
    def archive_contribution(
        self, *, identity: User, contribution_id: str, evidence_reference: str, occurred_at=None
    ) -> ContributionTransition:
        occurred_at = self._at(occurred_at)
        contribution = self._locked(contribution_id)
        self._require(
            contribution,
            Contribution.State.ACCEPTED,
            Contribution.State.REJECTED,
            Contribution.State.WITHDRAWN,
        )
        actor, authority_reference = self.authority.evaluate(
            identity=identity,
            action="archive_contribution",
            target=contribution,
            timestamp=occurred_at,
        )
        self._record_evidence(
            contribution=contribution,
            version=contribution.current_version,
            actor=actor,
            reference=evidence_reference,
            reference_type="archive",
        )
        prior = contribution.state
        contribution.state = Contribution.State.ARCHIVED
        contribution.archived_at = occurred_at
        contribution.save(update_fields=("state", "archived_at", "updated_at"))
        return self._record_transition(
            contribution=contribution,
            version=contribution.current_version,
            prior=prior,
            new=contribution.state,
            command="archive_contribution",
            actor=actor,
            authority_reference=authority_reference,
            occurred_at=occurred_at,
        )

    @transaction.atomic
    def restrict_content(self, *, identity: User, version_id: int, occurred_at=None):
        occurred_at = self._at(occurred_at)
        version = ContributionVersion.objects.select_for_update().select_related(
            "contribution"
        ).get(pk=version_id)
        actor, authority_reference = self.authority.evaluate(
            identity=identity,
            action="restrict_content",
            target=version,
            timestamp=occurred_at,
        )
        if version.state != ContributionVersion.State.CURRENT:
            raise InvalidContributionTransition("only current content may be restricted")
        prior = version.state
        version.state = ContributionVersion.State.RESTRICTED
        version.restricted_at = occurred_at
        version.save(update_fields=("state", "restricted_at"))
        self._record_transition(
            contribution=version.contribution,
            version=version,
            prior=prior,
            new=version.state,
            command="restrict_content",
            actor=actor,
            authority_reference=authority_reference,
            occurred_at=occurred_at,
        )
        return version

    @transaction.atomic
    def erase_content(self, *, identity: User, version_id: int, occurred_at=None):
        occurred_at = self._at(occurred_at)
        version = ContributionVersion.objects.select_for_update().select_related(
            "contribution"
        ).get(pk=version_id)
        actor, authority_reference = self.authority.evaluate(
            identity=identity,
            action="erase_content",
            target=version,
            timestamp=occurred_at,
        )
        if version.legal_hold:
            raise LegalHoldPreventsErasure("legal hold prevents erasure")
        if version.state not in (
            ContributionVersion.State.CURRENT,
            ContributionVersion.State.RESTRICTED,
        ):
            raise InvalidContributionTransition("content cannot be erased")
        prior = version.state
        version.content = None
        version.attachment_references = []
        version.state = ContributionVersion.State.ERASED_CONTENT
        version.erased_at = occurred_at
        version.save(
            update_fields=("content", "attachment_references", "state", "erased_at")
        )
        self._record_transition(
            contribution=version.contribution,
            version=version,
            prior=prior,
            new=version.state,
            command="erase_content",
            actor=actor,
            authority_reference=authority_reference,
            occurred_at=occurred_at,
        )
        return version


__all__ = [
    "ContributionService",
    "ContributionServiceError",
    "InvalidContributionTransition",
    "LegalHoldPreventsErasure",
]