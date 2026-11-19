"""Alpha's local email: capture as before, then hand the message to the mail catcher on this computer.

The relay sends to 127.0.0.1 and to nowhere else. Only the Alpha settings select it
(relay design v1.1, Alpha U-D parts P3a and P3b). ``email_foundation/backend.py`` is
unchanged: acceptance is still recorded as a ``LocalCapture`` row in this database, so
the outbox keeps showing CAPTURED_LOCAL.
"""

import smtplib
from email.message import EmailMessage as WireMessage
from email.policy import SMTP
from email.utils import format_datetime

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction

from .backend import Capabilities, CaptureBackend, NotAccepted
from .models import LocalCapture, Outbox
from .service import database_now

LOOPBACK = "127.0.0.1"
DEFAULT_TIMEOUT_SECONDS = 5
# Seven-bit on the wire: the relay asks the catcher for no SMTP extension.
_WIRE = SMTP.clone(cte_type="7bit")


class RelayOutcomeUnknown(RuntimeError):
    """The content was sent and the catcher did not confirm it: it may hold a copy.

    Deliberately not ``NotAccepted``: the worker records ACCEPTANCE_UNCERTAIN and never retries.
    """


def _refusal(why, next_step):
    # Five parts (practice item 164): what happened, why, what changed, what to do next, who can help.
    # No setting's value is ever shown.
    return ImproperlyConfigured(
        f"Local email did not start. {why} No message was claimed or sent. "
        f"{next_step} The Vision Chamber can help with this deployment."
    )


def _content(message, moment):
    """The message as it goes on the wire after the DATA command (RFC 5321 section 4.5.2).

    CRLF line ends, a leading dot doubled on any line that starts with one, then the closing line.
    """
    wire = WireMessage(policy=_WIRE)
    wire["From"] = message.from_email
    wire["To"] = message.to[0]
    wire["Subject"] = message.subject
    wire["Date"] = format_datetime(moment)
    # The intent's own identifier, so a copy in the catcher can be traced to its intent.
    wire["Message-ID"] = f"<{message.ema_intent_id}@alpha.intevia.invalid>"
    wire.set_content(message.body)
    lines = wire.as_bytes().split(b"\r\n")
    data = b"\r\n".join(b"." + line if line.startswith(b".") else line for line in lines)
    if not data.endswith(b"\r\n"):
        data += b"\r\n"
    return data + b".\r\n"


def _expect(reply, *accepted):
    # A reply is (code, text). Any code but the expected one is a failure of that step.
    if reply[0] not in accepted:
        raise smtplib.SMTPResponseException(reply[0], reply[1])


class LoopbackRelayBackend(CaptureBackend):
    # authoritative_absence is False on purpose: after a send has started, a missing capture
    # row is not proof that the catcher holds no copy, so recovery leaves the intent uncertain
    # and never queues it again (email_foundation/worker.py, recover). idempotency is False
    # because the catcher offers no key to replay against; replay safety is the capture row.
    capabilities = Capabilities(
        "loopback_relay",
        local_capture=True,
        idempotency=False,
        idempotency_seconds=None,
        suppression=False,
        inbound_parse=False,
        authoritative_absence=False,
    )

    def __init__(self, fail_silently=False, **kwargs):
        """Refuse a forbidden mail setting here, before any socket and before the worker claims.

        ``backend()`` builds this object first (``run_worker`` calls it before ``claim``),
        so a refusal leaves every intent exactly as it was.
        """
        super().__init__(fail_silently=fail_silently, **kwargs)
        host = getattr(settings, "EMAIL_HOST", None)
        if type(host) is not str or host != LOOPBACK:
            raise _refusal(
                "The mail destination is not this computer's own address, 127.0.0.1.",
                "Set the mail host (EMAIL_HOST) to 127.0.0.1 and run the worker again.",
            )
        if getattr(settings, "EMAIL_HOST_USER", ""):
            raise _refusal(
                "A mail user is set, and the mail catcher on this computer takes none.",
                "Remove the mail user (EMAIL_HOST_USER) and run the worker again.",
            )
        if getattr(settings, "EMAIL_HOST_PASSWORD", ""):
            raise _refusal(
                "A mail password is set, and the mail catcher on this computer takes none.",
                "Remove the mail password (EMAIL_HOST_PASSWORD) and run the worker again.",
            )
        if getattr(settings, "EMAIL_USE_TLS", False):
            raise _refusal(
                "Mail TLS is switched on, and the mail catcher on this computer is reached without it.",
                "Switch mail TLS (EMAIL_USE_TLS) off and run the worker again.",
            )
        if getattr(settings, "EMAIL_USE_SSL", False):
            raise _refusal(
                "Mail SSL is switched on, and the mail catcher on this computer is reached without it.",
                "Switch mail SSL (EMAIL_USE_SSL) off and run the worker again.",
            )
        self._port = getattr(settings, "EMAIL_PORT", None)
        timeout = getattr(settings, "EMAIL_TIMEOUT", None)
        # Always a timeout: the worker holds row locks across the send.
        self._timeout = (
            timeout
            if isinstance(timeout, (int, float)) and not isinstance(timeout, bool) and timeout > 0
            else DEFAULT_TIMEOUT_SECONDS
        )

    def send_messages(self, email_messages):
        count = 0
        for message in email_messages:
            # One savepoint of the relay's own, whoever calls it: if the relay does not end
            # in acceptance, no capture row survives.
            with transaction.atomic():
                intent_id = getattr(message, "ema_intent_id", None)
                if intent_id is not None:
                    # Serialise on the intent's row. An overlapping caller waits here until
                    # this call's capture is committed or gone, so only the call that creates
                    # the capture relays. The worker already holds this lock in the same
                    # transaction (email_foundation/worker.py, deliver).
                    Outbox.objects.select_for_update().filter(pk=intent_id).first()
                already_captured = (
                    intent_id is not None
                    and LocalCapture.objects.filter(intent_id=intent_id).exists()
                )
                # The inherited capture first: its shape checks, the row and the digest
                # comparison. A different payload on the same key is refused here.
                super().send_messages([message])
                if not already_captured:
                    self._relay(message)
                # An existing capture is a replay: accepted, and nothing is relayed again.
            count += 1
        return count

    def _relay(self, message):
        """Hand one message to the catcher at 127.0.0.1, one SMTP step at a time.

        One rule decides the error: has the message's content left this process?
        Before that moment the catcher cannot hold the message, so a named failure is
        definite: ``NotAccepted``, which the worker may retry within its bound. From that
        moment it may hold it, so any failure is unknown: ``RelayOutcomeUnknown``, which the
        worker records as ACCEPTANCE_UNCERTAIN. The boundary is the flag set below, never an
        exception's type. An exception this method does not expect is not caught, and is
        left uncertain too.
        """
        content = _content(message, database_now())
        # An address literal as the greeting name: no host name is looked up.
        link = smtplib.SMTP(local_hostname="[" + LOOPBACK + "]", timeout=self._timeout)
        content_left = False
        try:
            try:
                greeting = link.connect(LOOPBACK, self._port)
            except smtplib.SMTPException:
                raise  # Connected, but no greeting could be read: a refusal, mapped below.
            except OSError as failure:  # Refused, unreachable or timed out.
                raise NotAccepted("relay_unavailable") from failure
            _expect(greeting, 220)
            _expect(link.ehlo(), 250)
            _expect(link.mail(message.from_email), 250)
            _expect(link.rcpt(message.to[0]), 250, 251)
            _expect(link.docmd("DATA"), 354)
            content_left = True  # The boundary: set immediately before the content is written.
            link.send(content)
            _expect(link.getreply(), 250)
        except (smtplib.SMTPException, OSError) as failure:
            if content_left:
                raise RelayOutcomeUnknown(
                    "Local email's outcome is unknown. The message's content was sent and the mail "
                    "catcher did not confirm it. No capture was kept and it will not be sent again."
                ) from failure
            raise NotAccepted("relay_refused") from failure
        else:
            try:
                link.quit()
            except (smtplib.SMTPException, OSError):
                pass  # After 250 the message is accepted; a failed QUIT changes nothing.
        finally:
            link.close()
