"""Operational email records, not governed authority or announcement evidence."""

import uuid

from django.db import models
from django.db.models import Q


class AddressRecord(models.Model):
    identity = models.OneToOneField("core.Identity", on_delete=models.PROTECT)
    verified_address = models.EmailField(max_length=254, blank=True)
    verified_at = models.DateTimeField(null=True)
    pending_address = models.EmailField(max_length=254, blank=True)
    challenge_digest = models.CharField(max_length=64, blank=True)
    challenge_id = models.UUIDField(null=True)
    expires_at = models.DateTimeField(null=True)
    last_requested_at = models.DateTimeField(null=True)
    request_day = models.DateField(null=True)
    requests_today = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(verified_address="", verified_at__isnull=True)
                    | (~Q(verified_address="") & Q(verified_at__isnull=False))
                ),
                name="ema_verified_pair",
            ),
        ]


class TemplateVersion(models.Model):
    name = models.CharField(max_length=100)
    version = models.PositiveIntegerField()
    digest = models.CharField(max_length=64)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("name", "version"), name="ema_template_version_unique"
            )
        ]


class Outbox(models.Model):
    class State(models.TextChoices):
        REQUESTED = "REQUESTED"
        QUEUED = "QUEUED"
        PROVIDER_ACCEPTED = "PROVIDER_ACCEPTED"
        FAILED = "FAILED"
        SUPPRESSED = "SUPPRESSED"
        ACCEPTANCE_UNCERTAIN = "ACCEPTANCE_UNCERTAIN"

    intent_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_key = models.CharField(max_length=160, unique=True)
    fingerprint = models.CharField(max_length=64)
    delivery_digest = models.CharField(max_length=64)
    recipient = models.ForeignKey("core.Identity", on_delete=models.PROTECT)
    template = models.ForeignKey(TemplateVersion, on_delete=models.PROTECT)
    category = models.CharField(max_length=12)
    origin_reference = models.CharField(max_length=255)
    organisation_id = models.UUIDField(null=True)
    record_id = models.UUIDField(null=True)
    verification_challenge = models.UUIDField(null=True)
    to_address = models.EmailField(max_length=254)
    sender = models.EmailField(max_length=254)
    subject = models.CharField(max_length=200)
    body = models.TextField()
    state = models.CharField(
        max_length=24, choices=State.choices, default=State.REQUESTED
    )
    created_at = models.DateTimeField(auto_now_add=True)
    ready_at = models.DateTimeField()
    lease_id = models.UUIDField(null=True)
    lease_until = models.DateTimeField(null=True)
    attempt_count = models.PositiveIntegerField(default=0)
    last_code = models.CharField(max_length=48, blank=True)
    terminal_at = models.DateTimeField(null=True)
    payload_erased_at = models.DateTimeField(null=True)

    class Meta:
        indexes = [
            models.Index(fields=("state", "ready_at"), name="ema_ready"),
            models.Index(fields=("lease_until",), name="ema_leases"),
            models.Index(fields=("-created_at",), name="ema_page_created"),
            models.Index(fields=("terminal_at",), name="ema_terminal_cleanup"),
            models.Index(
                fields=("created_at",),
                name="ema_verification_cleanup",
                condition=Q(
                    verification_challenge__isnull=False, payload_erased_at__isnull=True
                ),
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(category__in=("ESSENTIAL", "NOTICE")),
                name="ema_nonmarketing_category",
            ),
            models.CheckConstraint(
                condition=Q(
                    state__in=(
                        "REQUESTED",
                        "QUEUED",
                        "PROVIDER_ACCEPTED",
                        "FAILED",
                        "SUPPRESSED",
                        "ACCEPTANCE_UNCERTAIN",
                    )
                ),
                name="ema_state_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(lease_id__isnull=True, lease_until__isnull=True)
                    | Q(lease_id__isnull=False, lease_until__isnull=False)
                ),
                name="ema_lease_pair",
            ),
        ]


class DeliveryAttempt(models.Model):
    intent = models.ForeignKey(
        Outbox, on_delete=models.CASCADE, related_name="attempts"
    )
    number = models.PositiveIntegerField()
    lease_id = models.UUIDField(unique=True)
    phase = models.CharField(max_length=12, default="CLAIMED")
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True)
    code = models.CharField(max_length=48, blank=True)

    class Meta:
        indexes = [models.Index(fields=("finished_at",), name="ema_attempt_cleanup")]
        constraints = [
            models.UniqueConstraint(
                fields=("intent", "number"), name="ema_attempt_number"
            ),
            models.CheckConstraint(
                condition=Q(phase__in=("CLAIMED", "STARTED", "FINISHED")),
                name="ema_attempt_phase",
            ),
        ]


class CapacityBucket(models.Model):
    day = models.DateField(primary_key=True)
    total_used = models.PositiveIntegerField(default=0)
    notice_used = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(total_used__gte=models.F("notice_used")),
                name="ema_capacity_consistent",
            )
        ]


class LocalCapture(models.Model):
    """Persistent acceptance receipt; retries compare its exact payload digest."""

    intent = models.OneToOneField(Outbox, primary_key=True, on_delete=models.CASCADE)
    payload_digest = models.CharField(max_length=64)
    captured_at = models.DateTimeField(auto_now_add=True)
    recipient = models.EmailField(max_length=254)
    sender = models.EmailField(max_length=254)
    subject = models.CharField(max_length=200)
    body = models.TextField()

    class Meta:
        indexes = [models.Index(fields=("captured_at",), name="ema_capture_cleanup")]
