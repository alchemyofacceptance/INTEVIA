import uuid

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from core.identity import canonical_username_v1

# ---------------------------------------------------------
# Identity
# ---------------------------------------------------------
# Canonical durable Human identity. Django User remains the credential and
# session substrate; organisational belonging and authority remain external.
# ---------------------------------------------------------
class Identity(models.Model):
    class AccessState(models.TextChoices):
        PENDING = "pending", "Pending"
        ACTIVE = "active", "Active"
        RESTRICTED = "restricted", "Restricted"
        DEACTIVATED = "deactivated", "Deactivated"

    identity_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
    )
    credential = models.OneToOneField(
        User,
        on_delete=models.PROTECT,
        related_name="intevia_identity",
    )
    display_name = models.CharField(max_length=90, blank=True)
    canonical_username = models.CharField(max_length=255, unique=True)
    access_state = models.CharField(
        max_length=16,
        choices=AccessState.choices,
        default=AccessState.PENDING,
        db_index=True,
    )
    access_epoch = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    restricted_at = models.DateTimeField(null=True, blank=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(access_epoch__gte=0),
                name="identity_access_epoch_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(
                    access_state__in=(
                        "pending",
                        "active",
                        "restricted",
                        "deactivated",
                    )
                ),
                name="identity_access_state_valid",
            ),
        ]

    def clean(self):
        if self.credential_id is not None:
            expected = canonical_username_v1(self.credential.username)
            if self.canonical_username != expected:
                raise ValidationError(
                    {"canonical_username": "must match credential username"}
                )

    def save(self, *args, **kwargs):
        if self._state.adding and not self.canonical_username:
            self.canonical_username = canonical_username_v1(
                self.credential.username
            )
        if self.pk is not None:
            original = type(self).objects.filter(pk=self.pk).values_list(
                "identity_id", flat=True
            ).first()
            if original is not None and original != self.identity_id:
                raise ValidationError("identity_id is immutable")
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.display_name or "Historical Identity"


# ---------------------------------------------------------
# Role
# ---------------------------------------------------------
# Represents a functional or conceptual role within INTEVIA.
# Examples:
#   - User (the human)
#   - Gee  (meaning layer)
#   - Coe  (implementation layer)
#
# This model is intentionally flexible so the ontology can
# evolve (e.g., Mentor, Reviewer, Observer).
# ---------------------------------------------------------
class Role(models.Model):
    # Name of the role (unique).
    # Rule of 9s → 90 chars.
    name = models.CharField(max_length=90, unique=True)

    # Optional description for clarity.
    # Rule of 9s → 270 chars (3× identity length).
    description = models.CharField(max_length=270, blank=True)

    # Timestamp for continuity layer.
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


# ---------------------------------------------------------
# ProfileRole (Compatibility Bridge)
# ---------------------------------------------------------
# Retained for S001-S006 prerequisite compatibility only. It is not
# membership, organisational authority, or Human Governor designation.
# ---------------------------------------------------------
class ProfileRole(models.Model):
    identity = models.ForeignKey(Identity, on_delete=models.CASCADE)

    # Link to Role (the conceptual function).
    role = models.ForeignKey(Role, on_delete=models.CASCADE)

    # Timestamp for continuity layer.
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Prevent duplicate assignments.
        unique_together = ('identity', 'role')

    def __str__(self):
        return f"{self.identity} → {self.role}"


class IdentityTransition(models.Model):
    class Action(models.TextChoices):
        PROVISION = "provision", "Provision"
        ACTIVATE = "activate", "Activate"
        RESTRICT = "restrict", "Restrict"
        DEACTIVATE = "deactivate", "Deactivate"
        REACTIVATE = "reactivate", "Reactivate"
        REPLACE_CREDENTIAL = "replace_credential", "Replace credential"
        RETIRE_CREDENTIAL = "retire_credential", "Retire credential"
        UPDATE_USERNAME = "update_username", "Update username"
        CORRECT = "correct", "Correct"

    class ReasonCategory(models.TextChoices):
        PROVISIONING = "provisioning", "Provisioning"
        GOVERNED_ACTIVATION = "governed_activation", "Governed activation"
        ACCESS_REVIEW = "access_review", "Access review"
        SECURITY = "security", "Security"
        HUMAN_REQUEST = "human_request", "Human request"
        CREDENTIAL_CHANGE = "credential_change", "Credential change"
        CORRECTION = "correction", "Correction"
        OTHER = "other", "Other"

    identity = models.ForeignKey(
        Identity,
        on_delete=models.PROTECT,
        related_name="access_transitions",
    )
    action = models.CharField(max_length=32, choices=Action.choices)
    prior_state = models.CharField(
        max_length=16,
        choices=Identity.AccessState.choices,
        null=True,
        blank=True,
    )
    resulting_state = models.CharField(
        max_length=16,
        choices=Identity.AccessState.choices,
    )
    requesting_actor = models.ForeignKey(
        Identity,
        on_delete=models.PROTECT,
        related_name="requested_identity_transitions",
        null=True,
        blank=True,
    )
    authority_reference = models.CharField(max_length=255)
    technical_executor = models.CharField(max_length=120)
    evidence_reference = models.CharField(max_length=255)
    reason_category = models.CharField(
        max_length=32,
        choices=ReasonCategory.choices,
    )
    correlation_id = models.UUIDField(unique=True)
    occurred_at = models.DateTimeField()
    previous_transition = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        related_name="next_transition",
        null=True,
        blank=True,
    )
    corrects_transition = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="corrections",
        null=True,
        blank=True,
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(action="provision")
                | Q(prior_state__isnull=False),
                name="identity_transition_prior_required",
            ),
            models.CheckConstraint(
                condition=Q(corrects_transition__isnull=True)
                | Q(action="correct"),
                name="identity_correction_action_required",
            ),
        ]
        indexes = [
            models.Index(
                fields=("identity", "occurred_at"),
                name="identity_transition_time_idx",
            ),
        ]

    def clean(self):
        if (
            self.previous_transition_id is not None
            and self.previous_transition.identity_id != self.identity_id
        ):
            raise ValidationError(
                "previous_transition must belong to the same Identity"
            )
        if (
            self.corrects_transition_id is not None
            and self.corrects_transition.identity_id != self.identity_id
        ):
            raise ValidationError(
                "corrects_transition must belong to the same Identity"
            )

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("IdentityTransition is append-only")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("IdentityTransition cannot be deleted")


class OriginatingMembershipProvisioningRequest(models.Model):
    identity = models.ForeignKey(
        Identity,
        on_delete=models.PROTECT,
        related_name="originating_membership_requests",
    )
    organism_reference = models.CharField(max_length=120)
    contract_version = models.PositiveIntegerField()
    correlation_id = models.UUIDField(unique=True)
    authority_reference = models.CharField(max_length=255)
    evidence_reference = models.CharField(max_length=255)
    requested_at = models.DateTimeField()
    supersedes = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        related_name="superseded_by",
        null=True,
        blank=True,
    )
    superseded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("identity", "organism_reference"),
                condition=Q(superseded_at__isnull=True),
                name="one_active_originating_membership_intent",
            ),
            models.CheckConstraint(
                condition=Q(contract_version__gte=1),
                name="originating_contract_version_positive",
            ),
            models.CheckConstraint(
                condition=Q(supersedes__isnull=True)
                | ~Q(pk=models.F("supersedes_id")),
                name="originating_request_not_self_superseding",
            ),
        ]
        indexes = [
            models.Index(
                fields=("identity", "requested_at"),
                name="originating_request_time_idx",
            ),
        ]


class ProvisioningReconciliationAttempt(models.Model):
    class State(models.TextChoices):
        REQUESTED = "requested", "Requested"
        DISPATCHED = "dispatched", "Dispatched"
        ACKNOWLEDGED = "acknowledged", "Acknowledged"
        FULFILLED = "fulfilled", "Fulfilled"
        HELD = "held", "Held"
        REJECTED = "rejected", "Rejected"
        SUPERSEDED = "superseded", "Superseded"
        CORRECTED = "corrected", "Corrected"

    request = models.ForeignKey(
        OriginatingMembershipProvisioningRequest,
        on_delete=models.PROTECT,
        related_name="reconciliation_attempts",
    )
    state = models.CharField(max_length=16, choices=State.choices)
    authority_reference = models.CharField(max_length=255)
    evidence_reference = models.CharField(max_length=255)
    correlation_id = models.UUIDField(unique=True)
    occurred_at = models.DateTimeField()
    previous_attempt = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        related_name="next_attempt",
        null=True,
        blank=True,
    )
    corrects_attempt = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="corrections",
        null=True,
        blank=True,
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(corrects_attempt__isnull=True)
                | Q(state="corrected"),
                name="provision_correction_state_required",
            ),
            models.CheckConstraint(
                condition=Q(previous_attempt__isnull=True)
                | ~Q(pk=models.F("previous_attempt_id")),
                name="provision_attempt_not_self_previous",
            ),
        ]
        indexes = [
            models.Index(
                fields=("request", "occurred_at"),
                name="provision_attempt_time_idx",
            ),
        ]

    def clean(self):
        if (
            self.previous_attempt_id is not None
            and self.previous_attempt.request_id != self.request_id
        ):
            raise ValidationError(
                "previous_attempt must belong to the same request"
            )
        if (
            self.corrects_attempt_id is not None
            and self.corrects_attempt.request_id != self.request_id
        ):
            raise ValidationError(
                "corrects_attempt must belong to the same request"
            )

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError(
                "ProvisioningReconciliationAttempt is append-only"
            )
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            "ProvisioningReconciliationAttempt cannot be deleted"
        )


class Contribution(models.Model):
    class State(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        UNDER_REVIEW = "under_review", "Under review"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"
        CORRECTION_REQUESTED = "correction_requested", "Correction requested"
        CORRECTION_PENDING_REVIEW = (
            "correction_pending_review",
            "Correction pending review",
        )
        WITHDRAWN = "withdrawn", "Withdrawn"
        ARCHIVED = "archived", "Archived"

    contribution_id = models.CharField(max_length=120, unique=True)
    contributor = models.ForeignKey(
        Identity,
        on_delete=models.PROTECT,
        related_name="contributions",
    )
    state = models.CharField(
        max_length=32,
        choices=State.choices,
        default=State.DRAFT,
        db_index=True,
    )
    current_version = models.ForeignKey(
        "ContributionVersion",
        on_delete=models.PROTECT,
        related_name="current_for_contributions",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    archived_at = models.DateTimeField(null=True, blank=True, db_index=True)

    def clean(self):
        if (
            self.current_version_id is not None
            and self.current_version.contribution_id != self.pk
        ):
            raise ValidationError(
                "current_version must belong to this Contribution"
            )

    def __str__(self):
        return self.contribution_id


class ContributionVersion(models.Model):
    class State(models.TextChoices):
        CURRENT = "current", "Current"
        SUPERSEDED = "superseded", "Superseded"
        RESTRICTED = "restricted", "Restricted"
        ERASED_CONTENT = "erased_content", "Erased content"

    contribution = models.ForeignKey(
        Contribution,
        on_delete=models.PROTECT,
        related_name="versions",
    )
    version_number = models.PositiveIntegerField()
    state = models.CharField(
        max_length=24,
        choices=State.choices,
        default=State.CURRENT,
        db_index=True,
    )
    content = models.TextField(null=True, blank=True)
    attachment_references = models.JSONField(default=list, blank=True)
    supersedes = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        related_name="superseded_by",
        null=True,
        blank=True,
    )
    created_by = models.ForeignKey(
        Identity,
        on_delete=models.PROTECT,
        related_name="created_contribution_versions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    restricted_at = models.DateTimeField(null=True, blank=True)
    erased_at = models.DateTimeField(null=True, blank=True)
    legal_hold = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("contribution", "version_number"),
                name="unique_contribution_version_number",
            ),
        ]
        indexes = [
            models.Index(
                fields=("contribution", "version_number"),
                name="contrib_version_order_idx",
            ),
        ]

    def clean(self):
        if self.supersedes_id is None:
            return
        if self.supersedes_id == self.pk:
            raise ValidationError("a version cannot supersede itself")
        if self.supersedes.contribution_id != self.contribution_id:
            raise ValidationError(
                "supersedes must belong to the same Contribution"
            )

    def __str__(self):
        return f"{self.contribution.contribution_id}:v{self.version_number}"


class ContributionTransition(models.Model):
    contribution = models.ForeignKey(
        Contribution,
        on_delete=models.PROTECT,
        related_name="transitions",
    )
    version = models.ForeignKey(
        ContributionVersion,
        on_delete=models.PROTECT,
        related_name="transitions",
        null=True,
        blank=True,
    )
    from_state = models.CharField(max_length=32)
    to_state = models.CharField(max_length=32)
    command = models.CharField(max_length=40, db_index=True)
    actor = models.ForeignKey(
        Identity,
        on_delete=models.PROTECT,
        related_name="contribution_transitions",
    )
    authority_reference = models.CharField(max_length=255)
    occurred_at = models.DateTimeField()
    previous_transition = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        related_name="next_transition",
        null=True,
        blank=True,
    )
    lineage_reference = models.CharField(max_length=255)

    class Meta:
        indexes = [
            models.Index(
                fields=("contribution", "occurred_at"),
                name="contrib_transition_time_idx",
            ),
            models.Index(
                fields=("version", "occurred_at"),
                name="version_transition_time_idx",
            ),
        ]

    def clean(self):
        if (
            self.version_id is not None
            and self.version.contribution_id != self.contribution_id
        ):
            raise ValidationError(
                "version must belong to the transition Contribution"
            )
        if (
            self.previous_transition_id is not None
            and self.previous_transition.contribution_id
            != self.contribution_id
        ):
            raise ValidationError(
                "previous_transition must belong to the same Contribution"
            )

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("ContributionTransition is append-only")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("ContributionTransition cannot be deleted")


class ContributionDecision(models.Model):
    contribution = models.ForeignKey(
        Contribution,
        on_delete=models.PROTECT,
        related_name="decisions",
    )
    version = models.ForeignKey(
        ContributionVersion,
        on_delete=models.PROTECT,
        related_name="decisions",
    )
    decision_actor = models.ForeignKey(
        Identity,
        on_delete=models.PROTECT,
        related_name="contribution_decisions",
    )
    decision_type = models.CharField(max_length=32, db_index=True)
    authority_reference = models.CharField(max_length=255)
    rationale_reference = models.CharField(max_length=255, null=True, blank=True)
    decided_at = models.DateTimeField(db_index=True)
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("version",),
                condition=Q(active=True),
                name="one_active_decision_per_version",
            ),
        ]

    def clean(self):
        if self.version.contribution_id != self.contribution_id:
            raise ValidationError(
                "version must belong to the decision Contribution"
            )
        if self.decision_actor_id == self.contribution.contributor_id:
            raise ValidationError(
                "a contributor cannot decide their own Contribution"
            )

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("ContributionDecision is immutable")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("ContributionDecision cannot be deleted")


class EvidenceReference(models.Model):
    contribution = models.ForeignKey(
        Contribution,
        on_delete=models.PROTECT,
        related_name="evidence_references",
    )
    version = models.ForeignKey(
        ContributionVersion,
        on_delete=models.PROTECT,
        related_name="evidence_references",
        null=True,
        blank=True,
    )
    decision = models.ForeignKey(
        ContributionDecision,
        on_delete=models.PROTECT,
        related_name="evidence_references",
        null=True,
        blank=True,
    )
    reference = models.CharField(max_length=255)
    reference_type = models.CharField(max_length=40, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    added_by = models.ForeignKey(
        Identity,
        on_delete=models.PROTECT,
        related_name="added_evidence_references",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("contribution", "reference"),
                name="unique_contribution_evidence_reference",
            ),
        ]
        indexes = [
            models.Index(
                fields=("contribution", "created_at"),
                name="contrib_evidence_time_idx",
            ),
        ]

    def clean(self):
        if (
            self.version_id is not None
            and self.version.contribution_id != self.contribution_id
        ):
            raise ValidationError(
                "version must belong to the evidence Contribution"
            )
        if (
            self.decision_id is not None
            and self.decision.contribution_id != self.contribution_id
        ):
            raise ValidationError(
                "decision must belong to the evidence Contribution"
            )


class Event(models.Model):
    class State(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"
        ARCHIVED = "archived", "Archived"

    event_id = models.CharField(max_length=120, unique=True)
    title = models.CharField(max_length=270)
    description = models.TextField(blank=True)
    owner = models.ForeignKey(
        Identity,
        on_delete=models.PROTECT,
        related_name="owned_events",
    )
    state = models.CharField(
        max_length=24,
        choices=State.choices,
        default=State.DRAFT,
        db_index=True,
    )
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField(auto_now=True)
    archived_at = models.DateTimeField(null=True, blank=True, db_index=True)

    def __str__(self):
        return self.event_id


class EventTransition(models.Model):
    event = models.ForeignKey(
        Event,
        on_delete=models.PROTECT,
        related_name="transitions",
    )
    from_state = models.CharField(max_length=24)
    to_state = models.CharField(max_length=24)
    command = models.CharField(max_length=40, db_index=True)
    actor = models.ForeignKey(
        Identity,
        on_delete=models.PROTECT,
        related_name="event_transitions",
    )
    authority_reference = models.CharField(max_length=255)
    rationale_reference = models.CharField(max_length=255, null=True, blank=True)
    occurred_at = models.DateTimeField()
    previous_transition = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        related_name="next_transition",
        null=True,
        blank=True,
    )
    lineage_reference = models.CharField(max_length=255, unique=True)

    class Meta:
        indexes = [
            models.Index(
                fields=("event", "occurred_at"),
                name="event_transition_time_idx",
            ),
        ]

    def clean(self):
        if (
            self.previous_transition_id is not None
            and self.previous_transition.event_id != self.event_id
        ):
            raise ValidationError(
                "previous_transition must belong to the same Event"
            )

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("EventTransition is append-only")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("EventTransition cannot be deleted")


class EventParticipationManager(models.Manager):
    def create(self, **kwargs):
        raise ValidationError(
            "EventParticipation writes are retired; use governed Event registration"
        )


class EventParticipation(models.Model):
    event = models.ForeignKey(
        Event,
        on_delete=models.PROTECT,
        related_name="participations",
    )
    participant = models.ForeignKey(
        Identity,
        on_delete=models.PROTECT,
        related_name="event_participations",
    )
    attached_by = models.ForeignKey(
        Identity,
        on_delete=models.PROTECT,
        related_name="attached_event_participations",
    )
    authority_reference = models.CharField(max_length=255)
    attached_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("event", "participant"),
                name="unique_event_participant",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk is None:
            raise ValidationError(
                "EventParticipation writes are retired; use governed Event registration"
            )
        raise ValidationError("EventParticipation is immutable")

    def delete(self, *args, **kwargs):
        raise ValidationError("EventParticipation cannot be deleted")

    objects = EventParticipationManager()


class EventEvidenceReference(models.Model):
    event = models.ForeignKey(
        Event,
        on_delete=models.PROTECT,
        related_name="evidence_references",
    )
    transition = models.ForeignKey(
        EventTransition,
        on_delete=models.PROTECT,
        related_name="evidence_references",
    )
    reference = models.CharField(max_length=255)
    reference_type = models.CharField(max_length=40, db_index=True)
    supplied_by = models.ForeignKey(
        Identity,
        on_delete=models.PROTECT,
        related_name="supplied_event_evidence_references",
    )
    authority_reference = models.CharField(max_length=255)
    occurred_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("event", "reference"),
                name="unique_event_evidence_reference",
            ),
        ]
        indexes = [
            models.Index(
                fields=("event", "occurred_at"),
                name="event_evidence_time_idx",
            ),
        ]

    def clean(self):
        if self.transition.event_id != self.event_id:
            raise ValidationError("transition must belong to the evidence Event")

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("EventEvidenceReference is immutable")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("EventEvidenceReference cannot be deleted")


class EventRegistration(models.Model):
    """A person-specific Event registration and bounded eligibility receipt.

    The eligibility receipt records what was evaluated at registration time.
    It does not prove that the complete governing policy can be reconstructed.
    """

    class State(models.TextChoices):
        REGISTERED = "registered", "Registered"
        CANCELLED = "cancelled", "Cancelled"

    class Origin(models.TextChoices):
        SELF = "self", "Self"
        THIRD_PARTY = "third_party", "Third party"

    class EligibilityBasisType(models.TextChoices):
        EVENT_POLICY = "event_policy", "Event policy"
        AUTHORITY_RULE = "authority_rule", "Authority rule"
        EVENT_CONFIGURATION = "event_configuration", "Event configuration"
        OTHER = "other", "Other"

    registration_id = models.CharField(max_length=120, unique=True)
    event = models.ForeignKey(
        Event,
        on_delete=models.PROTECT,
        related_name="registrations",
    )
    participant = models.ForeignKey(
        Identity,
        on_delete=models.PROTECT,
        related_name="event_registrations",
    )
    state = models.CharField(
        max_length=24,
        choices=State.choices,
        default=State.REGISTERED,
        db_index=True,
    )
    origin = models.CharField(max_length=24, choices=Origin.choices)
    predecessor = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="successors",
        null=True,
        blank=True,
    )
    event_state_at_registration = models.CharField(max_length=24)
    eligibility_policy_reference = models.CharField(
        max_length=255,
        null=True,
        blank=True,
    )
    eligibility_basis_type = models.CharField(
        max_length=32,
        choices=EligibilityBasisType.choices,
    )
    eligibility_basis_reference = models.CharField(max_length=255)
    eligibility_evaluated_at = models.DateTimeField()
    registered_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("event", "participant"),
                condition=Q(state="registered"),
                name="one_active_event_registration",
            ),
            models.CheckConstraint(
                condition=Q(predecessor__isnull=True)
                | ~Q(pk=models.F("predecessor_id")),
                name="event_registration_not_self_predecessor",
            ),
            models.UniqueConstraint(
                fields=("predecessor",),
                condition=Q(predecessor__isnull=False),
                name="one_event_registration_successor",
            ),
        ]
        indexes = [
            models.Index(
                fields=("event", "participant", "state"),
                name="event_reg_lookup_idx",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("EventRegistration is immutable")
        self.full_clean(validate_constraints=False)
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("EventRegistration cannot be deleted")


class EventRegistrationTransition(models.Model):
    class ActionType(models.TextChoices):
        REGISTER_SELF = "register_self", "Register self"
        REGISTER_THIRD_PARTY = "register_third_party", "Register third party"
        CANCEL = "cancel", "Cancel"
        RE_REGISTER = "re_register", "Re-register"

    class CancellationBasis(models.TextChoices):
        PARTICIPANT_REQUEST = "participant_request", "Participant request"
        ACTOR_DECISION = "actor_decision", "Actor decision"
        EVENT_CHANGE = "event_change", "Event change"
        ADMINISTRATIVE = "administrative", "Administrative"
        OTHER = "other", "Other"

    class BasisSource(models.TextChoices):
        PARTICIPANT_SUPPLIED = "participant_supplied", "Participant supplied"
        ACTOR_RECORDED = "actor_recorded", "Actor recorded"

    registration = models.ForeignKey(
        EventRegistration,
        on_delete=models.PROTECT,
        related_name="transitions",
    )
    from_state = models.CharField(max_length=24)
    to_state = models.CharField(max_length=24)
    action_type = models.CharField(
        max_length=32,
        choices=ActionType.choices,
        db_index=True,
    )
    actor = models.ForeignKey(
        Identity,
        on_delete=models.PROTECT,
        related_name="event_registration_transitions",
    )
    authority_reference = models.CharField(max_length=255)
    authority_event = models.ForeignKey(
        Event,
        on_delete=models.PROTECT,
        related_name="registration_authority_transitions",
    )
    authority_participant = models.ForeignKey(
        Identity,
        on_delete=models.PROTECT,
        related_name="registration_authority_subject_transitions",
    )
    authority_predecessor = models.ForeignKey(
        EventRegistration,
        on_delete=models.PROTECT,
        related_name="successor_authority_transitions",
        null=True,
        blank=True,
    )
    authority_evaluated_at = models.DateTimeField()
    idempotency_key = models.CharField(max_length=120, null=True, blank=True)
    cancellation_basis = models.CharField(
        max_length=32,
        choices=CancellationBasis.choices,
        blank=True,
    )
    basis_source = models.CharField(
        max_length=32,
        choices=BasisSource.choices,
        blank=True,
    )
    occurred_at = models.DateTimeField()
    lineage_reference = models.CharField(max_length=255, unique=True)
    previous_transition = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        related_name="next_transition",
        null=True,
        blank=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("actor", "action_type", "idempotency_key"),
                condition=Q(idempotency_key__isnull=False),
                name="unique_event_reg_idempotency",
            ),
        ]
        indexes = [
            models.Index(
                fields=("registration", "occurred_at"),
                name="event_reg_transition_idx",
            ),
        ]

    def clean(self):
        if self.registration_id is not None:
            if self.authority_event_id != self.registration.event_id:
                raise ValidationError("authority Event must match registration")
            if self.authority_participant_id != self.registration.participant_id:
                raise ValidationError(
                    "authority participant must match registration participant"
                )
            if (
                self.authority_predecessor_id
                != self.registration.predecessor_id
            ):
                raise ValidationError(
                    "authority predecessor must match registration predecessor"
                )
        if (
            self.previous_transition_id is not None
            and self.previous_transition.registration_id != self.registration_id
        ):
            raise ValidationError(
                "previous_transition must belong to the same registration"
            )

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("EventRegistrationTransition is append-only")
        self.full_clean(validate_constraints=False)
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("EventRegistrationTransition cannot be deleted")


class EventRegistrationEvidenceReference(models.Model):
    transition = models.ForeignKey(
        EventRegistrationTransition,
        on_delete=models.PROTECT,
        related_name="evidence_references",
    )
    reference = models.CharField(max_length=255)
    reference_type = models.CharField(max_length=40, db_index=True)
    supplied_by = models.ForeignKey(
        Identity,
        on_delete=models.PROTECT,
        related_name="supplied_event_registration_evidence",
    )
    occurred_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("transition", "reference"),
                name="unique_event_registration_evidence",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError(
                "EventRegistrationEvidenceReference is immutable"
            )
        self.full_clean(validate_constraints=False)
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            "EventRegistrationEvidenceReference cannot be deleted"
        )


class LibraryResource(models.Model):
    class State(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        DEPRECATED = "deprecated", "Deprecated"
        ARCHIVED = "archived", "Archived"

    resource_id = models.CharField(max_length=120, unique=True)
    created_by = models.ForeignKey(
        Identity,
        on_delete=models.PROTECT,
        related_name="created_library_resources",
    )
    state = models.CharField(
        max_length=24,
        choices=State.choices,
        default=State.DRAFT,
        db_index=True,
    )
    current_version = models.ForeignKey(
        "LibraryResourceVersion",
        on_delete=models.PROTECT,
        related_name="current_for_resources",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField(auto_now=True)
    archived_at = models.DateTimeField(null=True, blank=True, db_index=True)

    def clean(self):
        if (
            self.current_version_id is not None
            and self.current_version.resource_id != self.pk
        ):
            raise ValidationError(
                "current_version must belong to this LibraryResource"
            )

    def __str__(self):
        return self.resource_id


class LibraryResourceVersion(models.Model):
    resource = models.ForeignKey(
        LibraryResource,
        on_delete=models.PROTECT,
        related_name="versions",
    )
    version_number = models.PositiveIntegerField()
    content = models.TextField()
    predecessor = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        related_name="successor",
        null=True,
        blank=True,
    )
    created_by = models.ForeignKey(
        Identity,
        on_delete=models.PROTECT,
        related_name="created_library_resource_versions",
    )
    created_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("resource", "version_number"),
                name="unique_library_resource_version",
            ),
        ]

    def clean(self):
        if self.predecessor_id is None:
            if self.version_number != 1:
                raise ValidationError("an initial Library version must be version 1")
            return
        if self.predecessor.resource_id != self.resource_id:
            raise ValidationError(
                "predecessor must belong to the same LibraryResource"
            )
        if self.version_number != self.predecessor.version_number + 1:
            raise ValidationError("Library version lineage must be sequential")

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("LibraryResourceVersion is immutable")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("LibraryResourceVersion cannot be deleted")

    def __str__(self):
        return f"{self.resource.resource_id}:v{self.version_number}"


class LibraryResourceTransition(models.Model):
    resource = models.ForeignKey(
        LibraryResource,
        on_delete=models.PROTECT,
        related_name="transitions",
    )
    version = models.ForeignKey(
        LibraryResourceVersion,
        on_delete=models.PROTECT,
        related_name="transitions",
    )
    from_state = models.CharField(max_length=24)
    to_state = models.CharField(max_length=24)
    command = models.CharField(max_length=48, db_index=True)
    actor = models.ForeignKey(
        Identity,
        on_delete=models.PROTECT,
        related_name="library_resource_transitions",
    )
    authority_reference = models.CharField(max_length=255)
    rationale_reference = models.CharField(max_length=255, null=True, blank=True)
    occurred_at = models.DateTimeField()
    previous_transition = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        related_name="next_transition",
        null=True,
        blank=True,
    )
    lineage_reference = models.CharField(max_length=255, unique=True)

    class Meta:
        indexes = [
            models.Index(
                fields=("resource", "occurred_at"),
                name="library_transition_time_idx",
            ),
        ]

    def clean(self):
        if self.version.resource_id != self.resource_id:
            raise ValidationError(
                "version must belong to the transition LibraryResource"
            )
        if (
            self.previous_transition_id is not None
            and self.previous_transition.resource_id != self.resource_id
        ):
            raise ValidationError(
                "previous_transition must belong to the same LibraryResource"
            )

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("LibraryResourceTransition is append-only")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("LibraryResourceTransition cannot be deleted")


class LibraryResourceEvidenceReference(models.Model):
    resource = models.ForeignKey(
        LibraryResource,
        on_delete=models.PROTECT,
        related_name="evidence_references",
    )
    version = models.ForeignKey(
        LibraryResourceVersion,
        on_delete=models.PROTECT,
        related_name="evidence_references",
    )
    transition = models.ForeignKey(
        LibraryResourceTransition,
        on_delete=models.PROTECT,
        related_name="evidence_references",
    )
    reference = models.CharField(max_length=255)
    reference_type = models.CharField(max_length=40, db_index=True)
    supplied_by = models.ForeignKey(
        Identity,
        on_delete=models.PROTECT,
        related_name="supplied_library_evidence_references",
    )
    authority_reference = models.CharField(max_length=255)
    occurred_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("resource", "reference"),
                name="unique_library_evidence_reference",
            ),
        ]
        indexes = [
            models.Index(
                fields=("resource", "occurred_at"),
                name="library_evidence_time_idx",
            ),
        ]

    def clean(self):
        if self.version.resource_id != self.resource_id:
            raise ValidationError(
                "version must belong to the evidence LibraryResource"
            )
        if self.transition.resource_id != self.resource_id:
            raise ValidationError(
                "transition must belong to the evidence LibraryResource"
            )
        if self.transition.version_id != self.version_id:
            raise ValidationError(
                "transition and evidence must reference the same Library version"
            )

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError(
                "LibraryResourceEvidenceReference is immutable"
            )
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            "LibraryResourceEvidenceReference cannot be deleted"
        )


class Service(models.Model):
    class State(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        RETIRED = "retired", "Retired"

    service_id = models.CharField(max_length=120, unique=True)
    state = models.CharField(
        max_length=24,
        choices=State.choices,
        default=State.DRAFT,
        db_index=True,
    )
    current_version = models.ForeignKey(
        "ServiceVersion",
        on_delete=models.PROTECT,
        related_name="current_for_services",
        null=True,
        blank=True,
    )
    created_by = models.ForeignKey(
        Identity,
        on_delete=models.PROTECT,
        related_name="created_services",
    )
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField(auto_now=True)
    retired_at = models.DateTimeField(null=True, blank=True, db_index=True)

    def clean(self):
        if (
            self.current_version_id is not None
            and self.current_version.service_id != self.pk
        ):
            raise ValidationError("current_version must belong to this Service")

    def __str__(self):
        return self.service_id


class ServiceVersion(models.Model):
    class EnactmentMode(models.TextChoices):
        EVENT = "event", "Event"

    service = models.ForeignKey(
        Service,
        on_delete=models.PROTECT,
        related_name="versions",
    )
    version_number = models.PositiveIntegerField()
    capability_purpose = models.CharField(max_length=270)
    domain_intent = models.TextField()
    description = models.TextField(blank=True)
    constraints = models.TextField(blank=True)
    permitted_enactment_mode = models.CharField(
        max_length=24,
        choices=EnactmentMode.choices,
        default=EnactmentMode.EVENT,
    )
    predecessor = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        related_name="successor",
        null=True,
        blank=True,
    )
    created_by = models.ForeignKey(
        Identity,
        on_delete=models.PROTECT,
        related_name="created_service_versions",
    )
    created_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("service", "version_number"),
                name="unique_service_version",
            ),
        ]

    def clean(self):
        if self.predecessor_id is None:
            if self.version_number != 1:
                raise ValidationError("an initial Service version must be version 1")
            return
        if self.predecessor.service_id != self.service_id:
            raise ValidationError("predecessor must belong to the same Service")
        if self.version_number != self.predecessor.version_number + 1:
            raise ValidationError("Service version lineage must be sequential")

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("ServiceVersion is immutable")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("ServiceVersion cannot be deleted")

    def __str__(self):
        return f"{self.service.service_id}:v{self.version_number}"


class ServiceTransition(models.Model):
    service = models.ForeignKey(
        Service,
        on_delete=models.PROTECT,
        related_name="transitions",
    )
    version = models.ForeignKey(
        ServiceVersion,
        on_delete=models.PROTECT,
        related_name="transitions",
    )
    from_state = models.CharField(max_length=24)
    to_state = models.CharField(max_length=24)
    command = models.CharField(max_length=48, db_index=True)
    actor = models.ForeignKey(
        Identity,
        on_delete=models.PROTECT,
        related_name="service_transitions",
    )
    authority_reference = models.CharField(max_length=255)
    occurred_at = models.DateTimeField()
    previous_transition = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        related_name="next_transition",
        null=True,
        blank=True,
    )
    lineage_reference = models.CharField(max_length=255, unique=True)

    class Meta:
        indexes = [
            models.Index(
                fields=("service", "occurred_at"),
                name="service_transition_time_idx",
            ),
        ]

    def clean(self):
        if self.version.service_id != self.service_id:
            raise ValidationError("version must belong to the transition Service")
        if (
            self.previous_transition_id is not None
            and self.previous_transition.service_id != self.service_id
        ):
            raise ValidationError(
                "previous_transition must belong to the same Service"
            )

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("ServiceTransition is append-only")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("ServiceTransition cannot be deleted")


class ServiceEvidenceReference(models.Model):
    service = models.ForeignKey(
        Service,
        on_delete=models.PROTECT,
        related_name="evidence_references",
    )
    version = models.ForeignKey(
        ServiceVersion,
        on_delete=models.PROTECT,
        related_name="evidence_references",
    )
    transition = models.ForeignKey(
        ServiceTransition,
        on_delete=models.PROTECT,
        related_name="evidence_references",
    )
    reference = models.CharField(max_length=255)
    reference_type = models.CharField(max_length=40, db_index=True)
    supplied_by = models.ForeignKey(
        Identity,
        on_delete=models.PROTECT,
        related_name="supplied_service_evidence_references",
    )
    authority_reference = models.CharField(max_length=255)
    occurred_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("service", "reference"),
                name="unique_service_evidence_reference",
            ),
        ]
        indexes = [
            models.Index(
                fields=("service", "occurred_at"),
                name="service_evidence_time_idx",
            ),
        ]

    def clean(self):
        if self.version.service_id != self.service_id:
            raise ValidationError("version must belong to the evidence Service")
        if self.transition.service_id != self.service_id:
            raise ValidationError("transition must belong to the evidence Service")
        if self.transition.version_id != self.version_id:
            raise ValidationError(
                "transition and evidence must reference the same Service version"
            )

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("ServiceEvidenceReference is immutable")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("ServiceEvidenceReference cannot be deleted")


class LibraryServiceAssociation(models.Model):
    service_version = models.ForeignKey(
        ServiceVersion,
        on_delete=models.PROTECT,
        related_name="library_associations",
    )
    library_resource_version = models.ForeignKey(
        LibraryResourceVersion,
        on_delete=models.PROTECT,
        related_name="service_associations",
    )
    actor = models.ForeignKey(
        Identity,
        on_delete=models.PROTECT,
        related_name="library_service_associations",
    )
    authority_reference = models.CharField(max_length=255)
    evidence_reference = models.CharField(max_length=255)
    occurred_at = models.DateTimeField()
    lineage_reference = models.CharField(max_length=255, unique=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("service_version", "library_resource_version"),
                name="unique_library_service_association",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("LibraryServiceAssociation is immutable")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("LibraryServiceAssociation cannot be deleted")


class ServiceEventAssociation(models.Model):
    service_version = models.ForeignKey(
        ServiceVersion,
        on_delete=models.PROTECT,
        related_name="event_associations",
    )
    event = models.ForeignKey(
        Event,
        on_delete=models.PROTECT,
        related_name="service_associations",
    )
    actor = models.ForeignKey(
        Identity,
        on_delete=models.PROTECT,
        related_name="service_event_associations",
    )
    authority_reference = models.CharField(max_length=255)
    evidence_reference = models.CharField(max_length=255)
    occurred_at = models.DateTimeField()
    lineage_reference = models.CharField(max_length=255, unique=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("service_version", "event"),
                name="unique_service_event_association",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("ServiceEventAssociation is immutable")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("ServiceEventAssociation cannot be deleted")


class ServiceDeliveryEvidenceReference(models.Model):
    service_event_association = models.ForeignKey(
        ServiceEventAssociation,
        on_delete=models.PROTECT,
        related_name="delivery_evidence_references",
    )
    completed_event_transition = models.ForeignKey(
        EventTransition,
        on_delete=models.PROTECT,
        related_name="service_delivery_evidence_references",
    )
    reference = models.CharField(max_length=255)
    supplied_by = models.ForeignKey(
        Identity,
        on_delete=models.PROTECT,
        related_name="supplied_service_delivery_evidence_references",
    )
    authority_reference = models.CharField(max_length=255)
    occurred_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("service_event_association", "reference"),
                name="unique_service_delivery_evidence",
            ),
        ]
        indexes = [
            models.Index(
                fields=("service_event_association", "occurred_at"),
                name="service_delivery_time_idx",
            ),
        ]

    def clean(self):
        if (
            self.completed_event_transition.event_id
            != self.service_event_association.event_id
        ):
            raise ValidationError(
                "completed transition must belong to the associated Event"
            )
        if self.completed_event_transition.to_state != Event.State.COMPLETED:
            raise ValidationError("delivery evidence requires a completed Event")

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("ServiceDeliveryEvidenceReference is immutable")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("ServiceDeliveryEvidenceReference cannot be deleted")


class CareResponse(models.Model):
    class TriggerType(models.TextChoices):
        HUMAN_REQUEST = "human_request", "Human request"
        GOVERNED_SYSTEM_EVENT = "governed_system_event", "Governed system event"

    class ContextSource(models.TextChoices):
        HUMAN_DECLARED = "human_declared", "Human declared"
        EXPLICIT_PREFERENCE = "explicit_preference", "Explicit preference"
        AUTHORISED_ORGANISM = "authorised_organism", "Authorised organism"

    class OutputType(models.TextChoices):
        ORIENTATION_GUIDANCE = "orientation_guidance", "Orientation guidance"
        CLARIFICATION_PROMPT = "clarification_prompt", "Clarification prompt"
        NEXT_STEP_SUGGESTION = "next_step_suggestion", "Next step suggestion"
        BOUNDARY_SIGNAL = "boundary_signal", "Boundary signal"
        HUMAN_DEFERENCE = "human_deference", "Human deference"

    response_id = models.CharField(max_length=120, unique=True)
    trigger_type = models.CharField(max_length=32, choices=TriggerType.choices)
    trigger_reference = models.CharField(max_length=255)
    context_source = models.CharField(max_length=32, choices=ContextSource.choices)
    context_reference = models.CharField(max_length=255)
    output_type = models.CharField(
        max_length=32,
        choices=OutputType.choices,
        db_index=True,
    )
    content = models.TextField()
    selection_basis = models.CharField(max_length=255, blank=True)
    governing_rule_reference = models.CharField(max_length=255, blank=True)
    selected_library_version = models.ForeignKey(
        LibraryResourceVersion,
        on_delete=models.PROTECT,
        related_name="care_responses",
        null=True,
        blank=True,
    )
    human_response_reference = models.CharField(max_length=255, blank=True)
    actor = models.ForeignKey(
        Identity,
        on_delete=models.PROTECT,
        related_name="care_responses",
    )
    authority_reference = models.CharField(max_length=255)
    evidence_reference = models.CharField(max_length=255)
    occurred_at = models.DateTimeField()
    lineage_reference = models.CharField(max_length=255, db_index=True)
    predecessor = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        related_name="successor",
        null=True,
        blank=True,
    )

    class Meta:
        indexes = [
            models.Index(
                fields=("lineage_reference", "occurred_at"),
                name="care_lineage_time_idx",
            ),
        ]

    def clean(self):
        if self.predecessor_id is not None:
            if self.predecessor_id == self.pk:
                raise ValidationError("a CARE response cannot precede itself")
            if self.predecessor.lineage_reference != self.lineage_reference:
                raise ValidationError(
                    "predecessor must belong to the same CARE lineage"
                )
        has_selection_basis = bool(self.selection_basis)
        has_governing_rule = bool(self.governing_rule_reference)
        if has_selection_basis != has_governing_rule:
            raise ValidationError(
                "selection basis and governing rule must be recorded together"
            )
        if self.selected_library_version_id is None:
            return
        resource = LibraryResource.objects.get(
            pk=self.selected_library_version.resource_id
        )
        if (
            resource.state != LibraryResource.State.PUBLISHED
            or resource.current_version_id != self.selected_library_version_id
        ):
            raise ValidationError(
                "CARE may surface only the current published Library version"
            )
        if not has_selection_basis:
            raise ValidationError(
                "a surfaced Library version requires its basis and governing rule"
            )

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("CareResponse is immutable")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("CareResponse cannot be deleted")
