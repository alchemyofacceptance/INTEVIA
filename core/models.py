from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.contrib.auth.models import User

# ---------------------------------------------------------
# Profile
# ---------------------------------------------------------
# Extends Django's built-in User model with INTEVIA-specific
# identity fields. This is the "human identity" anchor for
# everything else in the platform.
# ---------------------------------------------------------
class Profile(models.Model):
    # One-to-one link to Django's User object.
    # This keeps authentication separate from domain identity.
    user = models.OneToOneField(User, on_delete=models.CASCADE)

    # Display name shown in the UI.
    # Rule of 9s → 90 chars for identity fields.
    display_name = models.CharField(max_length=90, blank=True)

    # Timestamp for continuity layer.
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        # Fallback to username if no display name set.
        return self.display_name or self.user.username


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
# ProfileRole (Bridging Table)
# ---------------------------------------------------------
# Many-to-many relationship between Profile and Role.
# This is the "organ system" that allows a Profile to hold
# multiple roles and roles to be assigned to many profiles.
#
# We use an explicit bridging model instead of Django's
# auto-generated M2M so we can:
#   - store metadata (assigned_at)
#   - enforce uniqueness
#   - extend later if needed
# ---------------------------------------------------------
class ProfileRole(models.Model):
    # Link to Profile (the human identity).
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE)

    # Link to Role (the conceptual function).
    role = models.ForeignKey(Role, on_delete=models.CASCADE)

    # Timestamp for continuity layer.
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Prevent duplicate assignments.
        unique_together = ('profile', 'role')

    def __str__(self):
        return f"{self.profile} → {self.role}"


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
        Profile,
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
        Profile,
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
        Profile,
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
        Profile,
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
        Profile,
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
        Profile,
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
        Profile,
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


class EventParticipation(models.Model):
    event = models.ForeignKey(
        Event,
        on_delete=models.PROTECT,
        related_name="participations",
    )
    participant = models.ForeignKey(
        Profile,
        on_delete=models.PROTECT,
        related_name="event_participations",
    )
    attached_by = models.ForeignKey(
        Profile,
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
        if self.pk is not None:
            raise ValidationError("EventParticipation is immutable")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("EventParticipation cannot be deleted")


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
        Profile,
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
