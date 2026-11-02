"""Django email seam: local capture only in EM-a. No SMTP or provider SDK."""

from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import get_connection
from django.core.mail.backends.base import BaseEmailBackend

from .contracts import digest
from .models import LocalCapture, Outbox


@dataclass(frozen=True)
class Capabilities:
    name: str
    local_capture: bool
    idempotency: bool
    idempotency_seconds: int | None  # None means durable, not an unspecified promise.
    suppression: bool
    inbound_parse: bool
    authoritative_absence: bool


class NotAccepted(Exception):
    """Adapter guarantees no acceptance; safe for a bounded retry."""


class Suppressed(Exception):
    """Adapter deliberately refuses this recipient; no retry."""


def message_digest(message):
    return digest(
        {
            "to": message.to,
            "from": message.from_email,
            "subject": message.subject,
            "body": message.body,
        }
    )


class CaptureBackend(BaseEmailBackend):
    capabilities = Capabilities("local_capture", True, True, None, False, False, True)

    def send_messages(self, email_messages):
        count = 0
        for message in email_messages:
            intent_id = getattr(message, "ema_intent_id", None)
            if intent_id is None or not Outbox.objects.filter(pk=intent_id).exists():
                raise NotAccepted("capture_shape")
            if (
                len(message.to) != 1
                or message.cc
                or message.bcc
                or message.attachments
                or message.reply_to
                or message.extra_headers
            ):
                raise NotAccepted("capture_shape")
            capture, created = LocalCapture.objects.get_or_create(
                intent_id=message.ema_intent_id,
                defaults={
                    "payload_digest": message_digest(message),
                    "recipient": message.to[0],
                    "sender": message.from_email,
                    "subject": message.subject,
                    "body": message.body,
                },
            )
            if capture.payload_digest != message_digest(message):
                raise NotAccepted("capture_key_conflict")
            count += 1  # Already captured is a successful idempotent replay.
        return count

    def lookup(self, intent):
        capture = LocalCapture.objects.filter(intent=intent).first()
        if capture is None:
            return "UNCERTAIN" if intent.payload_erased_at else "ABSENT"
        return (
            "ACCEPTED"
            if capture.payload_digest == intent.delivery_digest
            else "UNCERTAIN"
        )


def backend():
    instance = get_connection(fail_silently=False)
    caps = getattr(instance, "capabilities", None)
    if not isinstance(caps, Capabilities) or not callable(
        getattr(instance, "lookup", None)
    ):
        raise ImproperlyConfigured(
            "Email backend must declare EM-a capabilities and acceptance lookup."
        )
    # Deliberate delivery gate: opening it belongs to the first-deployment review.
    if (
        not getattr(settings, "INTEVIA_EMAIL_ALLOW_EXTERNAL", False)
        and not caps.local_capture
    ):
        raise ImproperlyConfigured(
            "External email is disabled: this unit permits local capture only."
        )
    return instance
