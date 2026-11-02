"""Transactional notice intent and inbox verification; no authority is conferred."""

import hmac
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import connection, transaction

from core.models import Identity

from .contracts import (
    MailRefused,
    Origin,
    address,
    base_url,
    context_path,
    digest,
    number,
    qualify,
    templates,
)
from .models import AddressRecord, Outbox, TemplateVersion

VERIFY_TEMPLATE = "ema.verify"
VERIFY_VERSION = 1
VERIFY_SUBJECT = "Verify your INTEVIA inbox"
VERIFY_TEXT = "Use this code with your INTEVIA verification request. It proves inbox control only."
VERIFY_DIGEST = digest({"subject": VERIFY_SUBJECT, "text": VERIFY_TEXT, "version": 1})


def database_now():
    if connection.vendor != "postgresql":
        raise MailRefused(
            "Email work did not start: PostgreSQL is required. No email was queued or captured; use the configured PostgreSQL environment."
        )
    with connection.cursor() as cursor:
        cursor.execute("SELECT clock_timestamp()")
        return cursor.fetchone()[0]


def is_severed(identity):
    """Read the existing no-reattachment ledger; introduce no parallel model."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM public.core_identityresolutionsevered WHERE identity_id = %s",
            [identity.pk],
        )
        return cursor.fetchone() is not None


def _identity(credential):
    # Called by trusted authenticated command intake, never from a submitted Identity ID.
    identity = Identity.objects.select_for_update().get(credential_id=credential.pk)
    if is_severed(identity):
        raise MailRefused(
            "Inbox verification was refused: this identity reference has been severed. No address changed; this reference cannot be restored or used for email."
        )
    if identity.access_state == Identity.AccessState.DEACTIVATED:
        raise MailRefused(
            "Inbox verification was refused: this Identity is deactivated. No address changed; use the existing Identity recovery process."
        )
    return identity


def _template_version(name, version, fingerprint):
    row, _ = TemplateVersion.objects.get_or_create(
        name=name, version=version, defaults={"digest": fingerprint}
    )
    if row.digest != fingerprint:
        raise MailRefused(
            "Email was not requested: this template version has changed. No email was queued; the package owner must issue a new reviewed version."
        )
    return row


def _enqueue(*, request_key, fingerprint, **values):
    if not isinstance(request_key, str) or not 1 <= len(request_key) <= 160:
        raise MailRefused(
            "Email was not requested: its request reference is missing or too long. No email was queued; the calling package must supply its stable request key."
        )
    values["delivery_digest"] = digest(
        {
            "to": [values["to_address"]],
            "from": values["sender"],
            "subject": values["subject"],
            "body": values["body"],
        }
    )
    row, _ = Outbox.objects.get_or_create(
        request_key=request_key, defaults={"fingerprint": fingerprint, **values}
    )
    if row.fingerprint != fingerprint:
        raise MailRefused(
            "Email was not requested: this request key already describes a different message. The original intent is unchanged; reuse its original request or supply a new key for a new act."
        )
    return row


def request_message(
    *, category, recipient, template, version, context, origin, request_key
):
    """Call inside the originating act's atomic block, after its persisted evidence.

    The caller catches MailRefused per recipient inside its outer act transaction,
    with a recipient savepoint, and records the notice refusal separately. An
    uncaught refusal would roll back the outer act: it is not a supported caller.
    Database commit failure remains a failure of the combined transaction.
    Approved package adapters establish authority/audience from stored records;
    there are no package templates in EM-a and no broadcast entry point.
    """
    if category not in {"ESSENTIAL", "NOTICE"}:
        raise MailRefused(
            "Email was not requested: marketing or an unknown category is unavailable in v1.0. No intent was created and the originating act is unchanged; use an approved service-notice template."
        )
    if not connection.in_atomic_block:
        raise MailRefused(
            "Email was not requested: it must be recorded inside its originating act's transaction. No intent was created; the calling package must use its atomic command route."
        )
    spec = templates().get((template, version))
    if spec is None or spec.category != category:
        raise MailRefused(
            "Email was not requested: this template and category are not approved. No intent was created; the package owner must provide the reviewed notice adapter."
        )
    if (
        not isinstance(origin, Origin)
        or not isinstance(origin.reference, str)
        or not 1 <= len(origin.reference) <= 255
    ):
        raise MailRefused(
            "Email was not requested: the originating act reference is missing. No intent was created; the calling package must supply its recorded act."
        )
    if origin.organisation_id is not None and not isinstance(
        origin.organisation_id, uuid.UUID
    ):
        raise MailRefused(
            "Email was not requested: the organisation reference must be a UUID or absent. No intent was created; the calling package must supply its recorded organisation identifier."
        )
    path = context_path(context, spec)
    identity = Identity.objects.select_for_update().get(pk=recipient.pk)
    if is_severed(identity):
        raise MailRefused(
            "Email was not requested: this identity reference has been severed. No intent was created; omit this recipient because this reference cannot be restored."
        )
    if identity.access_state == Identity.AccessState.DEACTIVATED or not qualify(
        spec, origin, identity, context
    ):
        raise MailRefused(
            "Email was not requested: the recorded act does not authorise this recipient in this organisation. No intent was created; use the act's authorised audience."
        )
    record = AddressRecord.objects.filter(identity=identity).first()
    if record is None or not record.verified_address:
        raise MailRefused(
            "Email was not requested: this Identity has no verified inbox. The originating act is unchanged and no intent was created; verify an inbox before requesting the notice."
        )
    sender = address(settings.INTEVIA_EMAIL_SENDERS[category])
    recipient_address = record.verified_address
    payload = {
        "recipient_id": identity.pk,
        "template_name": template,
        "version": version,
        "template_digest": spec.digest(),
        "category": category,
        "origin": origin.reference,
        "organisation": origin.organisation_id,
        "record": context["record_id"],
        "to": recipient_address,
        "sender": sender,
        "subject": spec.subject,
        "body": spec.text + "\n\n" + base_url() + path,
    }
    template_row = _template_version(template, version, spec.digest())
    return _enqueue(
        request_key=request_key,
        fingerprint=digest(payload),
        recipient=identity,
        template=template_row,
        category=category,
        origin_reference=origin.reference,
        organisation_id=origin.organisation_id,
        record_id=context["record_id"],
        to_address=recipient_address,
        sender=sender,
        subject=spec.subject,
        body=payload["body"],
        ready_at=database_now(),
    )


def _suppress_verification(identity, now):
    Outbox.objects.filter(
        recipient=identity,
        verification_challenge__isnull=False,
        state__in=(Outbox.State.REQUESTED, Outbox.State.QUEUED),
        lease_id__isnull=True,
    ).update(
        state=Outbox.State.SUPPRESSED,
        last_code="CHALLENGE_ENDED",
        terminal_at=now,
    )


@transaction.atomic
def request_verification(*, credential, proposed_address, request_id):
    """One address challenge per request; own-credential command intake required.

    Returns intent metadata, never a plaintext token. Local operator capture is
    the only inbox in this unit. No Identity access-state or authority changes.
    """
    identity = _identity(credential)
    proposed_address = address(proposed_address)
    try:
        request_uuid = uuid.UUID(str(request_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise MailRefused(
            "Inbox verification was not requested: the request reference is not a valid UUID. Nothing changed; the calling interface must supply a new request identifier."
        ) from exc
    request_key = f"ema.verify:{identity.identity_id}:{request_uuid}"
    fingerprint = digest(
        {"identity": identity.pk, "address": proposed_address, "request": request_key}
    )
    existing = Outbox.objects.filter(request_key=request_key).first()
    if existing:
        if existing.fingerprint != fingerprint:
            raise MailRefused(
                "Inbox verification was not requested: this request key already names another address. Nothing changed; use a new request for a new address."
            )
        return existing
    now = database_now()
    record, _ = AddressRecord.objects.get_or_create(identity=identity)
    limit = number("INTEVIA_EMAIL_VERIFICATIONS_PER_DAY", 5)
    interval = number("INTEVIA_EMAIL_RESEND_SECONDS", 60)
    if (
        record.last_requested_at
        and now < record.last_requested_at + timedelta(seconds=interval)
    ) or (record.request_day == now.date() and record.requests_today >= limit):
        raise MailRefused(
            "Inbox verification was not requested: the resend limit has been reached. The existing challenge is unchanged; wait for the resend interval or next UTC day before requesting again."
        )
    _suppress_verification(identity, now)
    token = secrets.token_urlsafe(32)
    challenge = uuid.uuid4()
    record.pending_address = proposed_address
    record.challenge_digest = digest(token)
    record.challenge_id = challenge
    record.expires_at = now + timedelta(
        seconds=number("INTEVIA_EMAIL_CHALLENGE_SECONDS", 1800)
    )
    record.requests_today = (
        record.requests_today + 1 if record.request_day == now.date() else 1
    )
    record.request_day = now.date()
    record.last_requested_at = now
    record.save()
    template_row = _template_version(VERIFY_TEMPLATE, VERIFY_VERSION, VERIFY_DIGEST)
    return _enqueue(
        request_key=request_key,
        fingerprint=fingerprint,
        recipient=identity,
        template=template_row,
        category="ESSENTIAL",
        origin_reference=f"email-verification:{challenge}",
        verification_challenge=challenge,
        to_address=proposed_address,
        sender=address(settings.INTEVIA_EMAIL_SENDERS["ESSENTIAL"]),
        subject=VERIFY_SUBJECT,
        body=f"{VERIFY_TEXT}\n\nRequest: {challenge}\nCode: {token}\nExpires: {record.expires_at.isoformat()}\nIf you did not request this, ignore it.",
        ready_at=now,
    )


@transaction.atomic
def verify_address(*, credential, challenge_id, token):
    identity = _identity(credential)
    record = AddressRecord.objects.filter(identity=identity).first()
    now = database_now()
    if (
        record is None
        or not record.challenge_digest
        or str(record.challenge_id) != str(challenge_id)
        or record.expires_at is None
        or now >= record.expires_at
        or not isinstance(token, str)
        or len(token) > 128
        or not hmac.compare_digest(record.challenge_digest, digest(token))
    ):
        raise MailRefused(
            "Inbox verification failed: the code is invalid, expired or already used. No address or authority changed; request a new code if needed."
        )
    _suppress_verification(identity, now)
    record.verified_address = record.pending_address
    record.verified_at = now
    record.pending_address = ""
    record.challenge_digest = ""
    record.challenge_id = None
    record.expires_at = None
    record.save()
    return record
