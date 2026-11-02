"""Bounded database worker: short claims, fenced leases, no blind uncertain retry."""

import uuid
from datetime import timedelta

from django.core.mail import EmailMessage
from django.db import connection, transaction
from django.db.models import Case, Exists, OuterRef, Q, Value, When

from core.models import Identity

from .backend import NotAccepted, Suppressed, backend
from .contracts import Origin, number, qualify, templates
from .models import AddressRecord, CapacityBucket, DeliveryAttempt, LocalCapture, Outbox
from .service import VERIFY_TEMPLATE, database_now, is_severed

TERMINAL = (
    Outbox.State.PROVIDER_ACCEPTED,
    Outbox.State.FAILED,
    Outbox.State.SUPPRESSED,
)


def _outside_transaction():
    if connection.in_atomic_block:
        raise RuntimeError(
            "Email worker did not start: run it outside the originating transaction, after commit. Nothing was captured."
        )


def claim():
    """Reserve one attempt; exhausted or obsolete rows do not end the batch."""
    _outside_transaction()
    with transaction.atomic():
        while True:
            now = database_now()
            row = (
                Outbox.objects.select_for_update(skip_locked=True)
                .filter(
                    state__in=(Outbox.State.REQUESTED, Outbox.State.QUEUED),
                    lease_id__isnull=True,
                    ready_at__lte=now,
                )
                .order_by("category", "created_at", "pk")
                .first()
            )
            if row is None:
                return None
            if (
                row.verification_challenge is not None
                and not AddressRecord.objects.filter(
                    identity_id=row.recipient_id,
                    challenge_id=row.verification_challenge,
                    expires_at__gt=now,
                    pending_address=row.to_address,
                )
                .exclude(challenge_digest="")
                .exists()
            ):
                row.state = Outbox.State.SUPPRESSED
                row.last_code = "CHALLENGE_ENDED"
                row.terminal_at = now
                row.save()
                continue
            previous = row.attempts.order_by("number").last()
            resume = previous is not None and previous.code == "CLAIM_RECOVERED"
            maximum = number("INTEVIA_EMAIL_MAX_ATTEMPTS", 3)
            if row.attempt_count >= maximum and not resume:
                row.state = Outbox.State.FAILED
                row.last_code = "ATTEMPTS_EXHAUSTED"
                row.terminal_at = now
                row.save()
                continue
            total = number("INTEVIA_EMAIL_DAILY_CAPACITY", 1000)
            reserve = number("INTEVIA_EMAIL_ESSENTIAL_RESERVE", 100)
            if reserve > total:
                raise ValueError(
                    "Email essential reserve exceeds total capacity; no attempt was claimed."
                )
            bucket, _ = CapacityBucket.objects.get_or_create(day=now.date())
            bucket = CapacityBucket.objects.select_for_update().get(pk=bucket.pk)
            if bucket.total_used >= total or (
                row.category == "NOTICE" and bucket.notice_used >= total - reserve
            ):
                return None
            bucket.total_used += 1
            bucket.notice_used += int(row.category == "NOTICE")
            bucket.save()
            row.state = Outbox.State.QUEUED
            if not resume:
                row.attempt_count += 1
            row.lease_id = uuid.uuid4()
            row.lease_until = now + timedelta(
                seconds=number("INTEVIA_EMAIL_LEASE_SECONDS", 60)
            )
            row.save()
            if resume:
                # The same never-started attempt gets a fresh fenced lease.
                # No quota is refunded; the previous lease can no longer send.
                previous.lease_id = row.lease_id
                previous.phase = "CLAIMED"
                previous.finished_at = None
                previous.code = ""
                previous.save()
            else:
                DeliveryAttempt.objects.create(
                    intent=row, number=row.attempt_count, lease_id=row.lease_id
                )
            return row.intent_id, row.lease_id


def _finish(row, attempt, state, code, now, *, immediate=False):
    row.state = state
    row.last_code = code
    row.lease_id = None
    row.lease_until = None
    row.terminal_at = now if state in TERMINAL else None
    row.ready_at = (
        now
        if immediate
        else now + timedelta(seconds=number("INTEVIA_EMAIL_RETRY_SECONDS", 60))
    )
    row.save()
    attempt.phase = "FINISHED"
    attempt.finished_at = now
    attempt.code = code
    attempt.save()


def _allowed(row, identity, now):
    if (
        identity.access_state == Identity.AccessState.DEACTIVATED
        or is_severed(identity)
        or row.payload_erased_at
    ):
        return False
    address = AddressRecord.objects.filter(identity=identity).first()
    if address is None:
        return False
    if row.template.name == VERIFY_TEMPLATE:
        return (
            row.verification_challenge == address.challenge_id
            and bool(address.challenge_digest)
            and address.expires_at is not None
            and now < address.expires_at
            and row.to_address == address.pending_address
        )
    if not address.verified_address or row.to_address != address.verified_address:
        return False
    spec = templates().get((row.template.name, row.template.version))
    return (
        spec is not None
        and spec.digest() == row.template.digest
        and spec.category == row.category
        and qualify(
            spec,
            Origin(row.origin_reference, row.organisation_id),
            identity,
            {"record_id": str(row.record_id)},
        )
    )


def deliver(intent_id, lease_id, adapter=None):
    _outside_transaction()
    adapter = adapter or backend()
    recipient_id = Outbox.objects.values_list("recipient_id", flat=True).get(
        pk=intent_id
    )
    # Local capture and its STARTED marker share the sending transaction.
    # No marker is written until Identity/intent locks and the lease recheck pass.
    with transaction.atomic():
        identity = Identity.objects.select_for_update().get(pk=recipient_id)
        row = (
            Outbox.objects.select_for_update()
            .select_related("template")
            .get(pk=intent_id)
        )
        now = database_now()
        if (
            row.state != Outbox.State.QUEUED
            or row.lease_id != lease_id
            or row.lease_until is None
            or row.lease_until <= now
        ):
            return False
        attempt = DeliveryAttempt.objects.get(lease_id=lease_id)
        if attempt.phase != "CLAIMED":
            return False
        try:
            allowed = _allowed(row, identity, now)
        except Exception:
            # No send occurred. Do not log exception text, contexts or credentials.
            _finish(row, attempt, Outbox.State.FAILED, "AUDIENCE_CHECK_FAILED", now)
            return False
        if not allowed:
            _finish(
                row,
                attempt,
                Outbox.State.SUPPRESSED,
                "ADDRESS_OR_AUDIENCE_CHANGED",
                now,
            )
            return False
        message = EmailMessage(
            subject=row.subject,
            body=row.body,
            from_email=row.sender,
            to=[row.to_address],
            connection=adapter,
        )
        message.ema_intent_id = row.intent_id
        attempt.phase = "STARTED"
        attempt.save(update_fields=["phase"])
        try:
            with (
                transaction.atomic()
            ):  # Isolate a capture database error from status recording.
                if adapter.send_messages([message]) != 1:
                    raise RuntimeError("backend acceptance was not established")
        except Suppressed:
            state, code = Outbox.State.SUPPRESSED, "BACKEND_SUPPRESSED"
        except NotAccepted:
            state = (
                Outbox.State.QUEUED
                if row.attempt_count < number("INTEVIA_EMAIL_MAX_ATTEMPTS", 3)
                else Outbox.State.FAILED
            )
            code = "NOT_ACCEPTED"
        except Exception:
            state, code = Outbox.State.ACCEPTANCE_UNCERTAIN, "ACCEPTANCE_UNKNOWN"
        else:
            state = Outbox.State.PROVIDER_ACCEPTED
            code = (
                "CAPTURED_LOCAL"
                if adapter.capabilities.local_capture
                else "PROVIDER_ACCEPTED"
            )
        _finish(row, attempt, state, code, database_now())
        return state == Outbox.State.PROVIDER_ACCEPTED


def recover(limit=100, adapter=None):
    """Recover expired claims; reconcile uncertain acceptance without a resend."""
    _outside_transaction()
    adapter = adapter or backend()
    now = database_now()
    if limit <= 0:
        return 0
    ids = (
        Outbox.objects.filter(
            Q(state=Outbox.State.QUEUED, lease_until__lte=now)
            | Q(state=Outbox.State.ACCEPTANCE_UNCERTAIN)
        )
        .order_by(
            Case(When(state=Outbox.State.QUEUED, then=Value(0)), default=Value(1)),
            "created_at",
            "pk",
        )
        .values_list("pk", flat=True)
        .iterator(chunk_size=max(1, limit))
    )
    changed = 0
    for intent_id in ids:
        with transaction.atomic():
            row = (
                Outbox.objects.select_for_update(skip_locked=True)
                .filter(pk=intent_id)
                .first()
            )
            if row is None:
                continue
            now = database_now()
            expired = (
                row.state == Outbox.State.QUEUED
                and row.lease_until
                and row.lease_until <= now
            )
            if not expired and row.state != Outbox.State.ACCEPTANCE_UNCERTAIN:
                continue
            attempt = row.attempts.order_by("number").last()
            if attempt is None:
                continue
            pre_send = bool(expired and attempt.phase == "CLAIMED")
            if pre_send:
                outcome = "ABSENT"  # No backend call was started for this lease.
            else:
                try:
                    outcome = adapter.lookup(row)
                except Exception:
                    outcome = "UNCERTAIN"
                if outcome == "ABSENT" and not (
                    adapter.capabilities.authoritative_absence
                    and adapter.capabilities.local_capture
                ):
                    outcome = "UNCERTAIN"
            if outcome == "ACCEPTED":
                state = Outbox.State.PROVIDER_ACCEPTED
                code = (
                    "CAPTURED_LOCAL"
                    if adapter.capabilities.local_capture
                    else "PROVIDER_ACCEPTED"
                )
            elif outcome == "ABSENT":
                state = (
                    Outbox.State.QUEUED
                    if pre_send
                    or row.attempt_count < number("INTEVIA_EMAIL_MAX_ATTEMPTS", 3)
                    else Outbox.State.FAILED
                )
                code = "CLAIM_RECOVERED" if pre_send else "LEASE_RECOVERED"
            else:
                state, code = Outbox.State.ACCEPTANCE_UNCERTAIN, "ACCEPTANCE_UNKNOWN"
            if state == row.state == Outbox.State.ACCEPTANCE_UNCERTAIN:
                continue
            _finish(row, attempt, state, code, now, immediate=pre_send)
            changed += 1
            if changed >= limit:
                break
    return changed


def run_worker(limit=100):
    _outside_transaction()
    adapter = (
        backend()
    )  # Fail a misconfigured/external backend before claiming anything.
    recover(limit, adapter)
    attempted = 0
    for _ in range(limit):
        work = claim()
        if work is None:
            break
        deliver(*work, adapter=adapter)
        attempted += 1
    return attempted


def housekeeping(limit=100):
    """Redact operational payloads; retain idempotency tombstones and act references."""
    _outside_transaction()
    now = database_now()
    cutoff = now - timedelta(days=number("INTEVIA_EMAIL_RETENTION_DAYS", 180))
    live_challenge = AddressRecord.objects.filter(
        identity_id=OuterRef("recipient_id"),
        challenge_id=OuterRef("verification_challenge"),
        expires_at__gt=now,
    ).exclude(challenge_digest="")
    ids = list(
        Outbox.objects.annotate(live_challenge=Exists(live_challenge))
        .filter(payload_erased_at__isnull=True, lease_id__isnull=True)
        .exclude(state=Outbox.State.ACCEPTANCE_UNCERTAIN)
        .filter(
            Q(state__in=TERMINAL, terminal_at__lte=cutoff)
            | Q(verification_challenge__isnull=False, live_challenge=False)
        )
        .order_by("created_at")
        .values_list("pk", "recipient_id")[:limit]
    )
    erased = 0
    for intent_id, recipient_id in ids:
        with transaction.atomic():
            Identity.objects.select_for_update().get(pk=recipient_id)
            row = Outbox.objects.select_for_update().get(pk=intent_id)
            now = database_now()
            if (
                row.lease_id
                or row.payload_erased_at
                or row.state == Outbox.State.ACCEPTANCE_UNCERTAIN
            ):
                continue
            record = AddressRecord.objects.filter(identity_id=recipient_id).first()
            expired_challenge = row.verification_challenge is not None and (
                record is None
                or row.verification_challenge != record.challenge_id
                or not record.challenge_digest
                or record.expires_at is None
                or now >= record.expires_at
            )
            if not expired_challenge and not (
                row.state in TERMINAL and row.terminal_at <= cutoff
            ):
                continue
            if row.state in {Outbox.State.REQUESTED, Outbox.State.QUEUED}:
                row.state = Outbox.State.SUPPRESSED
                row.last_code = "CHALLENGE_ENDED"
                row.terminal_at = now
            # No governed-record table is updated or deleted by this operation.
            row.to_address = row.sender = row.subject = row.body = ""
            row.payload_erased_at = now
            row.save()
            if row.state in TERMINAL and row.terminal_at <= cutoff:
                LocalCapture.objects.filter(intent=row).delete()
                row.attempts.all().delete()
            else:
                # Erase the verification capability promptly while retaining
                # acceptance metadata for recovery and operational retention.
                LocalCapture.objects.filter(intent=row).update(
                    recipient="", sender="", subject="", body=""
                )
            if (
                record
                and record.challenge_id == row.verification_challenge
                and record.expires_at is not None
                and now >= record.expires_at
            ):
                record.challenge_id = None
                record.challenge_digest = record.pending_address = ""
                record.expires_at = None
                record.save()
            erased += 1
    # Short-lived challenge payloads may already be erased; their attempts still age out.
    old_attempts = list(
        DeliveryAttempt.objects.filter(
            intent__state__in=TERMINAL, finished_at__lte=cutoff
        )
        .order_by("pk")
        .values_list("pk", flat=True)[:limit]
    )
    DeliveryAttempt.objects.filter(pk__in=old_attempts).delete()
    old_captures = list(
        LocalCapture.objects.filter(intent__state__in=TERMINAL, captured_at__lte=cutoff)
        .order_by("pk")
        .values_list("pk", flat=True)[:limit]
    )
    LocalCapture.objects.filter(pk__in=old_captures).delete()
    # Counters contain no addresses or evidence; recent quota cannot be reset.
    CapacityBucket.objects.filter(day__lt=cutoff.date()).delete()
    return erased
