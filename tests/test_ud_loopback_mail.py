"""U-D P3: Alpha's local email, the loopback relay to the mail catcher at 127.0.0.1.

UD-03 (T3): each forbidden mail setting refuses before the worker claims (rule page G3).
UD-04a (T4a): committed success, a replay and a conflicting payload, at a loopback sink (G4 a, b, c).
UD-07 (T8): among the shipped settings modules, only the Alpha selects the relay (G5).
UD-04b (T4b): a definite refusal, and an unknown outcome that is never sent again (G4 d, e).
UD-08: no second copy after the sending transaction is lost with the content already at the sink (G4 e).
UD-09: called directly, a failing call keeps no capture, and two overlapping calls relay once (G4 b).

No method reaches any address but 127.0.0.1. T3 and T8 open no socket: a recording
transport stands in for it. T4a and T4b use a small SMTP sink from the standard library,
bound to 127.0.0.1 on a port the system chooses. The expected sentences, recipient,
sender, subject and body are written here by hand; none is read from the module under
test. Fixtures follow EM-a's own pattern (tests/test_em_a.py) and make the intent
through the real request route.
"""

import contextlib
import email
import email.policy
import importlib
import os
import secrets
import socket
import sys
import threading
import uuid
from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import EmailMessage
from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase, override_settings

from core.models import Identity, OrganismCommandReceipt
from email_foundation.backend import NotAccepted, backend
from email_foundation.contracts import Origin, digest
from email_foundation.loopback import LoopbackRelayBackend
from email_foundation.models import AddressRecord, DeliveryAttempt, LocalCapture, Outbox
from email_foundation.service import database_now, request_message
from email_foundation.worker import recover, run_worker
from tests.s015_0021_support import create_test_identity
from tests.test_ud_alpha_settings import environment

RELAY = "email_foundation.loopback.LoopbackRelayBackend"
CAPTURE = "email_foundation.backend.CaptureBackend"
SETTINGS_MODULES = {
    "base": "intevia.settings",
    "peek": "intevia.peek_settings",
    "email-local": "intevia.email_local_settings",
    "Alpha": "intevia.alpha_settings",
}
_ABSENT = object()

RECIPIENT = "recipient@example.invalid"
SENDER = "notice@notice.mail.invalid"
# The fixture notice (tests/ema_support.py, NOTICE), written here by hand.
SUBJECT = "A test record changed"
BODY_TEXT = "Open your authorised test record in INTEVIA."
BODY_LINK = "http://localhost:8000/history/"
# Invented values that occur nowhere in a refusal's fixed words, so a leak is detectable.
MARK = "zq7"

# Each refusal in full, written by hand: what happened, why, what changed, what to do next, who can help.
STARTED = "Local email did not start. "
UNCHANGED = " No message was claimed or sent. "
HELP = " The Vision Chamber can help with this deployment."
DESTINATION_REFUSAL = (
    STARTED
    + "The mail destination is not this computer's own address, 127.0.0.1."
    + UNCHANGED
    + "Set the mail host (EMAIL_HOST) to 127.0.0.1 and run the worker again."
    + HELP
)
USER_REFUSAL = (
    STARTED
    + "A mail user is set, and the mail catcher on this computer takes none."
    + UNCHANGED
    + "Remove the mail user (EMAIL_HOST_USER) and run the worker again."
    + HELP
)
PASSWORD_REFUSAL = (
    STARTED
    + "A mail password is set, and the mail catcher on this computer takes none."
    + UNCHANGED
    + "Remove the mail password (EMAIL_HOST_PASSWORD) and run the worker again."
    + HELP
)
TLS_REFUSAL = (
    STARTED
    + "Mail TLS is switched on, and the mail catcher on this computer is reached without it."
    + UNCHANGED
    + "Switch mail TLS (EMAIL_USE_TLS) off and run the worker again."
    + HELP
)
SSL_REFUSAL = (
    STARTED
    + "Mail SSL is switched on, and the mail catcher on this computer is reached without it."
    + UNCHANGED
    + "Switch mail SSL (EMAIL_USE_SSL) off and run the worker again."
    + HELP
)
# Rule page G3, "refused together": none of these is the literal 127.0.0.1. None is ever dialled.
FORBIDDEN_DESTINATIONS = (
    "::1",
    "localhost",
    "127.0.0.2",
    "::ffff:127.0.0.1",
    "127.0.0.1 ",  # a trailing space
    " 127.0.0.1",  # a leading space
    "",
    "192.168.1.20",  # a LAN host
    "203.0.113.77",  # a public address (a documentation range)
    MARK + "-mail.example.invalid",  # a named host
)


def relay_settings(**changes):
    """Every setting the relay and the worker read, stated in one layer; ``changes`` vary one."""
    values = {
        "INTEVIA_TEST_CONTEXT": True,
        "INTEVIA_EMAIL_TEMPLATES": ("tests.ema_support.NOTICE", "tests.ema_support.ESSENTIAL"),
        "INTEVIA_EMAIL_ALLOW_EXTERNAL": False,
        "INTEVIA_EMAIL_SENDERS": {"ESSENTIAL": "essential@essential.mail.invalid", "NOTICE": SENDER},
        "INTEVIA_EMAIL_BASE_URL": "http://localhost:8000",
        "EMAIL_BACKEND": RELAY,
        "EMAIL_HOST": "127.0.0.1",
        "EMAIL_PORT": 1025,
        "EMAIL_HOST_USER": "",
        "EMAIL_HOST_PASSWORD": "",
        "EMAIL_USE_TLS": False,
        "EMAIL_USE_SSL": False,
        "EMAIL_TIMEOUT": 5,
        "INTEVIA_EMAIL_MAX_ATTEMPTS": 3,
        "INTEVIA_EMAIL_RETRY_SECONDS": 60,
    }
    values.update(changes)
    return values


def fresh_settings(target, env):
    """Import one shipped settings module afresh under exactly ``env``, then put the runner's own back.

    The pattern is ``AlphaSettingsTests.load`` (tests/test_ud_alpha_settings.py), widened to the
    four shipped modules so that no case reads another case's import.
    """
    package = importlib.import_module("intevia")
    names = tuple(SETTINGS_MODULES.values())
    kept_modules = {name: sys.modules.get(name, _ABSENT) for name in names}
    kept_attributes = {
        name.rsplit(".", 1)[1]: getattr(package, name.rsplit(".", 1)[1], _ABSENT) for name in names
    }
    try:
        with mock.patch.dict(os.environ, env, clear=True):
            for name in names:
                sys.modules.pop(name, None)
            return importlib.import_module(target)
    finally:
        for name, module in kept_modules.items():
            if module is _ABSENT:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        for name, value in kept_attributes.items():
            if value is _ABSENT:
                if hasattr(package, name):
                    delattr(package, name)
            else:
                setattr(package, name, value)


class RecordingTransport:
    """Stands in for the socket: it records what it is asked to do and reaches no address."""

    def __init__(self):
        self.dialled = []
        self.carried = []

    @contextlib.contextmanager
    def installed(self):
        transport = self

        def carry(relay, message):
            transport.carried.append(message)

        def dial(address, *args, **kwargs):
            transport.dialled.append(address)
            raise OSError("The recording transport dials nothing.")

        with mock.patch.object(LoopbackRelayBackend, "_relay", carry), mock.patch.object(
            socket, "create_connection", dial
        ):
            yield self


class WorkerStopped(BaseException):
    """A worker stopped in the middle of a send (Ctrl-C, a crash).

    Not an ``Exception``, as ``KeyboardInterrupt`` is not: nothing in the worker catches it,
    so the sending transaction is rolled back whole.
    """


def closed_port():
    """A port on 127.0.0.1 where nothing listens: the system chose it, and it is closed again."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class LoopbackSink:
    """A small SMTP catcher on 127.0.0.1 for one test. Standard library only.

    It is bound, on a port the system chooses, before it signals ready. Every socket step
    has a time limit and the thread's join is bounded. ``behaviour`` is one of: "accept";
    "refuse_recipient" (a rejection before the content, each refused recipient recorded in
    ``refused``); "silent_after_content" (takes the
    content and never answers); "reject_after_content" (takes the content, then answers 550).
    """

    STEP_SECONDS = 5
    HOLD_SECONDS = 8

    def __init__(self, behaviour="accept"):
        self.behaviour = behaviour
        self.received = []  # every content taken: (envelope sender, envelope recipients, bytes)
        self.accepted = []  # those the sink answered with 250
        self.refused = []  # every envelope recipient the sink answered with 550
        self.faults = []
        self.content_taken = threading.Event()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(4)
        self._listener.settimeout(0.1)
        self.port = self._listener.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, name="ud-loopback-sink", daemon=True)
        self._thread.start()
        if not self._ready.wait(self.STEP_SECONDS):
            self.close()
            raise AssertionError("The mail sink did not signal ready within five seconds.")

    def close(self):
        self._stop.set()
        self._listener.close()
        self._thread.join(self.STEP_SECONDS)
        if self._thread.is_alive():
            raise AssertionError("The mail sink's thread did not stop within five seconds.")

    def _serve(self):
        self._ready.set()
        while not self._stop.is_set():
            try:
                connection, _peer = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return  # The listener was closed.
            try:
                with connection:
                    connection.settimeout(self.STEP_SECONDS)
                    self._converse(connection)
            except OSError as fault:
                self.faults.append(repr(fault))

    @staticmethod
    def _address(line):
        return line.split(b"<", 1)[1].split(b">", 1)[0].decode("ascii")

    def _converse(self, connection):
        reader = connection.makefile("rb")
        connection.sendall(b"220 sink.invalid ESMTP\r\n")
        sender, recipients = None, []
        while True:
            line = reader.readline()
            if not line:
                return  # The relay closed the connection.
            verb = line.strip().upper()
            if verb.startswith((b"EHLO", b"HELO")):
                connection.sendall(b"250 sink.invalid\r\n")
            elif verb.startswith(b"MAIL FROM:"):
                sender = self._address(line)
                connection.sendall(b"250 OK\r\n")
            elif verb.startswith(b"RCPT TO:"):
                if self.behaviour == "refuse_recipient":
                    self.refused.append(self._address(line))
                    connection.sendall(b"550 No such user here\r\n")
                else:
                    recipients.append(self._address(line))
                    connection.sendall(b"250 OK\r\n")
            elif verb == b"DATA":
                connection.sendall(b"354 End data with <CR><LF>.<CR><LF>\r\n")
                content = []
                while True:
                    part = reader.readline()
                    if not part:
                        return  # Dropped before the closing line: nothing was taken.
                    if part == b".\r\n":
                        break
                    content.append(part[1:] if part.startswith(b".") else part)
                taken = (sender, recipients, b"".join(content))
                self.received.append(taken)
                self.content_taken.set()
                if self.behaviour == "silent_after_content":
                    self._stop.wait(self.HOLD_SECONDS)
                    return
                if self.behaviour == "reject_after_content":
                    connection.sendall(b"550 Rejected after the content\r\n")
                else:
                    self.accepted.append(taken)
                    connection.sendall(b"250 OK\r\n")
                sender, recipients = None, []
            elif verb == b"QUIT":
                connection.sendall(b"221 Bye\r\n")
                return
            else:
                connection.sendall(b"500 Unrecognised\r\n")


class LoopbackRelayTests(TransactionTestCase):
    # run_worker refuses inside a transaction, so these are TransactionTestCase methods.
    # No class-wide settings override: UD-07 reads the running configuration as it is.

    def claimable_intent(self):
        """One eligible notice intent, made as EM-a's tests make theirs, through the real request route."""
        self.assertEqual(
            connection.vendor,
            "postgresql",
            "The email worker needs PostgreSQL; another database cannot witness these claims.",
        )
        person = create_test_identity(
            username="udmail_" + uuid.uuid4().hex, access_state=Identity.AccessState.ACTIVE
        )
        organisation = uuid.uuid4()
        AddressRecord.objects.create(
            identity=person, verified_address=RECIPIENT, verified_at=database_now()
        )
        marker = uuid.uuid4().hex
        act = OrganismCommandReceipt.objects.create(
            actor=person,
            action="EMA_FIXTURE",
            idempotency_key=marker,
            request_reference="ema-fixture:" + marker,
            payload_fingerprint=digest(marker),
            authority_decision_reference="s015d1:" + digest(marker),
            lineage_reference="s015l1:" + digest(marker),
            result_payload={
                "organisation": str(organisation),
                "recipient": person.pk,
                "outcome": "UNCHANGED_TEST_ACT",
            },
        )
        with transaction.atomic():
            return request_message(
                category="NOTICE",
                recipient=person,
                template="fixture.notice",
                version=1,
                context={"record_id": str(act.receipt_uuid)},
                origin=Origin(act.lineage_reference, organisation),
                request_key=str(act.receipt_uuid),
            )

    def as_recorded(self, intent):
        """What the worker would change on a claim, read from the database."""
        return Outbox.objects.values(
            "state", "attempt_count", "lease_id", "lease_until", "last_code", "terminal_at"
        ).get(pk=intent.pk)

    def sink(self, behaviour="accept"):
        sink = LoopbackSink(behaviour)
        self.addCleanup(sink.close)
        return sink

    def due(self, intent):
        """Make a queued intent ready now, as EM-a's tests do, instead of waiting out the retry interval."""
        Outbox.objects.filter(pk=intent.pk).update(ready_at=database_now() - timedelta(seconds=1))

    def message_for(self, row, body=None):
        """The message the worker builds for an intent (email_foundation/worker.py, deliver)."""
        message = EmailMessage(
            subject=row.subject,
            body=row.body if body is None else body,
            from_email=row.sender,
            to=[row.to_address],
        )
        message.ema_intent_id = row.intent_id
        return message

    def assert_recorded(self, intent, state, code, attempts, why):
        row = Outbox.objects.get(pk=intent.pk)
        self.assertEqual((row.state, row.last_code, row.attempt_count), (state, code, attempts), why)

    def assert_no_capture(self, intent, why):
        self.assertFalse(LocalCapture.objects.filter(intent_id=intent.pk).exists(), why)

    def blocked_by(self, waiter_pid, holder_pid):
        """True once the waiter's session is blocked by the holder's own session; False after about five seconds.

        Both sessions are named by their backend PIDs, so no other session's lock wait can count. Each query has a
        one-second statement timeout.
        """
        pause = threading.Event()
        for _ in range(100):
            waiter = waiter_pid()
            if waiter is not None and holder_pid is not None:
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute("SET LOCAL statement_timeout = '1s'")
                    cursor.execute("SELECT %s = ANY(pg_blocking_pids(%s))", [holder_pid, waiter])
                    if cursor.fetchone()[0]:
                        return True
            pause.wait(0.05)
        return False

    def test_01_forbidden_settings_refuse_before_claim(self):
        """UD-03: one forbidden mail setting at a time stops the worker before it claims; 127.0.0.1 works."""
        with override_settings(**relay_settings()):
            intent = self.claimable_intent()
        before = self.as_recorded(intent)
        self.assertEqual(
            (before["state"], before["attempt_count"]),
            (Outbox.State.REQUESTED, 0),
            "The fixture intent must start unclaimed, with no attempt counted.",
        )

        cases = [
            (f"the mail destination {destination!r}", {"EMAIL_HOST": destination}, DESTINATION_REFUSAL)
            for destination in FORBIDDEN_DESTINATIONS
        ] + [
            ("a mail user", {"EMAIL_HOST_USER": MARK + "_mail_user"}, USER_REFUSAL),
            ("a mail password", {"EMAIL_HOST_PASSWORD": MARK + secrets.token_urlsafe(12)}, PASSWORD_REFUSAL),
            ("mail TLS", {"EMAIL_USE_TLS": True}, TLS_REFUSAL),
            ("mail SSL", {"EMAIL_USE_SSL": True}, SSL_REFUSAL),
        ]
        for what, change, expected in cases:
            transport = RecordingTransport()
            refusal = None
            with override_settings(**relay_settings(**change)), transport.installed():
                try:
                    run_worker()
                except ImproperlyConfigured as raised:
                    refusal = str(raised)
            self.assertIsNotNone(
                refusal, f"With {what} set, the worker must refuse to start before it claims anything."
            )
            self.assertEqual(
                refusal,
                expected,
                f"With {what} set, the refusal must be that setting's own five-part sentence and nothing else.",
            )
            self.assertNotIn(MARK, refusal, f"With {what} set, the refusal must never show a setting's value.")
            self.assertEqual(
                self.as_recorded(intent),
                before,
                f"With {what} set, the intent must be exactly as it was: not claimed, no attempt counted.",
            )
            self.assertFalse(
                DeliveryAttempt.objects.filter(intent_id=intent.pk).exists(),
                f"With {what} set, no delivery attempt may be recorded.",
            )
            self.assertFalse(
                LocalCapture.objects.filter(intent_id=intent.pk).exists(),
                f"With {what} set, nothing may be captured.",
            )
            self.assertEqual(
                (transport.dialled, transport.carried),
                ([], []),
                f"With {what} set, the transport must never be opened.",
            )

        # The working control: the same intent, the literal 127.0.0.1, the external gate closed.
        transport = RecordingTransport()
        with override_settings(**relay_settings()), transport.installed():
            self.assertIs(
                settings.INTEVIA_EMAIL_ALLOW_EXTERNAL,
                False,
                "The working control must run with the external mail gate closed.",
            )
            attempted = run_worker()
        self.assertEqual(
            attempted, 1, "With the mail destination 127.0.0.1, the worker must claim the same intent."
        )
        after = Outbox.objects.get(pk=intent.pk)
        self.assertEqual(
            (after.state, after.last_code, after.attempt_count),
            (Outbox.State.PROVIDER_ACCEPTED, "CAPTURED_LOCAL", 1),
            "With the mail destination 127.0.0.1, the intent must be accepted at its first attempt.",
        )
        capture = LocalCapture.objects.filter(intent_id=intent.pk).first()
        self.assertIsNotNone(capture, "An accepted intent must have its capture row.")
        self.assertEqual(
            capture.payload_digest,
            after.delivery_digest,
            "The capture row must carry the accepted intent's own digest.",
        )
        self.assertEqual(
            [(message.to, message.from_email) for message in transport.carried],
            [([RECIPIENT], SENDER)],
            "The working control must hand exactly one message to the transport, for the intent's recipient.",
        )
        self.assertEqual(
            transport.dialled, [], "The recording transport stands in for the socket: nothing may be dialled."
        )

    def test_02_success_replay_and_conflict_at_a_loopback_sink(self):
        """UD-04a: one accepted intent is one capture and one copy; a replay adds none; a changed payload is refused."""
        sink = self.sink()
        with override_settings(**relay_settings(EMAIL_PORT=sink.port)):
            intent = self.claimable_intent()
            expected_body = BODY_TEXT + "\n\n" + BODY_LINK + str(intent.record_id) + "/"

            # a. Committed success.
            self.assertEqual(run_worker(), 1, "The worker must claim the one eligible intent.")
            self.assert_recorded(
                intent,
                Outbox.State.PROVIDER_ACCEPTED,
                "CAPTURED_LOCAL",
                1,
                "A message the sink accepted must be recorded as accepted, at its first attempt.",
            )
            row = Outbox.objects.get(pk=intent.pk)
            captures = list(LocalCapture.objects.filter(intent_id=intent.pk))
            self.assertEqual(len(captures), 1, "An accepted intent must have exactly one capture row.")
            self.assertEqual(
                captures[0].payload_digest,
                row.delivery_digest,
                "The capture row must carry the accepted intent's own digest.",
            )
            self.assertEqual(
                len(sink.received), 1, "The sink must have been sent exactly one copy of the message."
            )
            self.assertEqual(len(sink.accepted), 1, "The sink must have accepted exactly one copy.")
            envelope_sender, envelope_recipients, content = sink.accepted[0]
            self.assertEqual(
                (envelope_sender, envelope_recipients),
                (SENDER, [RECIPIENT]),
                "The copy must be addressed from the notice sender to the intent's recipient, and to no one else.",
            )
            copy = email.message_from_bytes(content, policy=email.policy.default)
            self.assertEqual(
                (str(copy["From"]), str(copy["To"]), str(copy["Subject"])),
                (SENDER, RECIPIENT, SUBJECT),
                "The copy's sender, recipient and subject must be the intent's.",
            )
            self.assertEqual(
                copy.get_content().replace("\r\n", "\n"),
                expected_body + "\n",
                "The copy's body must be the intent's body, line for line.",
            )

            # b. A replay of the same intent, through the relay itself (an idle worker run is not a replay).
            relay = backend()
            self.assertEqual(
                relay.send_messages([self.message_for(row)]),
                1,
                "A second call with the same payload must be accepted.",
            )
            self.assertEqual(
                len(sink.received), 1, "A replay must relay nothing: there must still be one copy in total."
            )
            self.assertEqual(
                LocalCapture.objects.filter(intent_id=intent.pk).count(),
                1,
                "A replay must leave the one capture row.",
            )

            # c. A different payload on the same key.
            reason = None
            try:
                relay.send_messages([self.message_for(row, body=row.body + "\nA changed line.")])
            except NotAccepted as refused:
                reason = str(refused)
            self.assertEqual(
                reason,
                "capture_key_conflict",
                "A different payload on the same intent must be refused as a conflict, before any relay.",
            )
            self.assertEqual(len(sink.received), 1, "A refused conflict must relay no further copy.")
            unchanged = LocalCapture.objects.get(intent_id=intent.pk)
            self.assertEqual(
                (unchanged.payload_digest, unchanged.body),
                (captures[0].payload_digest, expected_body),
                "A refused conflict must leave the first capture as it was.",
            )
        self.assertEqual(sink.faults, [], "The sink must have met no socket fault of its own.")

    def test_03_only_alpha_selects_the_relay(self):
        """UD-07: base, peek and email-local keep the capture outbox; only the Alpha selects the relay."""
        peek_environment = environment(INTEVIA_POSTGRES_DB="intevia_peek_throwaway_film1")
        for label, env in (
            ("base", environment()),
            ("peek", peek_environment),
            ("email-local", peek_environment),
        ):
            module = fresh_settings(SETTINGS_MODULES[label], env)
            self.assertEqual(
                module.EMAIL_BACKEND,
                CAPTURE,
                f"The {label} settings must keep the capture outbox: only the Alpha may select the relay.",
            )

        alpha = fresh_settings(SETTINGS_MODULES["Alpha"], environment())
        self.assertEqual(alpha.EMAIL_BACKEND, RELAY, "The Alpha settings must select the loopback relay.")
        self.assertEqual(
            (alpha.EMAIL_HOST, alpha.EMAIL_PORT, alpha.EMAIL_TIMEOUT),
            ("127.0.0.1", 1025, 5),
            "The Alpha must send to 127.0.0.1 at the mail catcher's port, with a five-second timeout.",
        )
        for name in ("EMAIL_HOST_USER", "EMAIL_HOST_PASSWORD", "EMAIL_USE_TLS", "EMAIL_USE_SSL"):
            self.assertFalse(
                getattr(alpha, name, False),
                f"The Alpha settings must not set {name}: the relay refuses a mail user, password, TLS and SSL.",
            )
        self.assertIs(
            alpha.INTEVIA_EMAIL_ALLOW_EXTERNAL, False, "The Alpha must leave the external mail gate closed."
        )

        # The running test configuration: its own settings module, then what is in effect now.
        # Under any settings override Django reports no module name, so the environment's is the fallback.
        running = settings.SETTINGS_MODULE or os.environ.get("DJANGO_SETTINGS_MODULE")
        self.assertTrue(running, "The running test configuration must name its settings module.")
        self.assertEqual(
            sys.modules[running].EMAIL_BACKEND,
            CAPTURE,
            "The running test configuration's own settings must keep the capture outbox.",
        )
        self.assertNotEqual(
            settings.EMAIL_BACKEND,
            RELAY,
            "Whatever mail backend the test runner has in effect, it must not be the relay.",
        )

    def test_04_definite_refusal_and_unknown_outcome(self):
        """UD-04b: a definite refusal is retried within its bound; an unknown outcome is never sent again."""
        # d. A definite refusal: nothing listens on the port.
        with override_settings(**relay_settings(EMAIL_PORT=closed_port(), INTEVIA_EMAIL_MAX_ATTEMPTS=2)):
            refused = self.claimable_intent()
            run_worker()
            self.assert_no_capture(refused, "A refused connection must leave no capture row.")
            self.assert_recorded(
                refused,
                Outbox.State.QUEUED,
                "NOT_ACCEPTED",
                1,
                "A refused connection is definite: the intent must be queued again while attempts remain.",
            )
            self.due(refused)
            run_worker()
            self.assert_no_capture(refused, "A second refused connection must leave no capture row.")
            self.assert_recorded(
                refused,
                Outbox.State.FAILED,
                "NOT_ACCEPTED",
                2,
                "At its attempt limit, a definitely refused intent must end as failed.",
            )

        # d. A definite refusal: the sink rejects the recipient, before any content.
        rejecting = self.sink("refuse_recipient")
        with override_settings(**relay_settings(EMAIL_PORT=rejecting.port)):
            rejected = self.claimable_intent()
            run_worker()
            self.assertEqual(
                rejecting.refused,
                [RECIPIENT],
                "The sink must have refused this intent's recipient, or this case witnesses nothing: "
                "a connection that failed earlier would look the same.",
            )
            self.assert_no_capture(rejected, "A rejection before the content must leave no capture row.")
            self.assert_recorded(
                rejected,
                Outbox.State.QUEUED,
                "NOT_ACCEPTED",
                1,
                "A rejection before the content is definite: the intent must be queued again.",
            )
            self.assertEqual(rejecting.received, [], "A rejected recipient must mean no content was sent.")

        # e. An unknown outcome: the sink takes the content and never answers. One-second timeout.
        silent = self.sink("silent_after_content")
        with override_settings(**relay_settings(EMAIL_PORT=silent.port, EMAIL_TIMEOUT=1)):
            unknown = self.claimable_intent()
            run_worker()
            self.assertTrue(
                silent.content_taken.is_set(),
                "The silent sink must have taken the content, or this case witnesses nothing.",
            )
            self.assert_recorded(
                unknown,
                Outbox.State.ACCEPTANCE_UNCERTAIN,
                "ACCEPTANCE_UNKNOWN",
                1,
                "Once the content has been sent, a missing answer must be recorded as an unknown outcome.",
            )
            self.assert_no_capture(unknown, "An unknown outcome must leave no capture row.")
            self.assertEqual(recover(), 0, "Recovery must change nothing about an unknown outcome.")
            self.due(unknown)
            run_worker()
            self.assert_recorded(
                unknown,
                Outbox.State.ACCEPTANCE_UNCERTAIN,
                "ACCEPTANCE_UNKNOWN",
                1,
                "An unknown outcome must stay as it is: recovery and a later worker run must not send it again.",
            )
            self.assertEqual(
                len(silent.received),
                1,
                "The content must have been sent once only: a missing capture row is never a reason to send again.",
            )

        # e. An unknown outcome: the sink takes the content, then answers with a rejection.
        late = self.sink("reject_after_content")
        with override_settings(**relay_settings(EMAIL_PORT=late.port)):
            doubtful = self.claimable_intent()
            run_worker()
            self.assertTrue(
                late.content_taken.is_set(),
                "The sink must have taken the content, or this case witnesses nothing.",
            )
            self.assert_recorded(
                doubtful,
                Outbox.State.ACCEPTANCE_UNCERTAIN,
                "ACCEPTANCE_UNKNOWN",
                1,
                "Any answer but acceptance after the content has been sent must be recorded as an unknown outcome.",
            )
            self.assert_no_capture(doubtful, "An unknown outcome must leave no capture row.")
            self.assertEqual(late.accepted, [], "The rejecting sink must have accepted nothing.")

    def test_05_no_second_copy_after_a_lost_delivery_transaction(self):
        """UD-08: the sink has the content and the sending transaction is lost; nothing sends it again."""
        sink = self.sink()
        real_relay = LoopbackRelayBackend._relay

        def relay_then_stop(relay, message):
            real_relay(relay, message)
            raise WorkerStopped()

        with override_settings(**relay_settings(EMAIL_PORT=sink.port)):
            intent = self.claimable_intent()
            stopped = False
            with mock.patch.object(LoopbackRelayBackend, "_relay", relay_then_stop):
                try:
                    run_worker()
                except WorkerStopped:
                    stopped = True
            self.assertTrue(
                stopped, "The worker must have been stopped in the middle of its send, or this case witnesses nothing."
            )
            self.assertEqual(
                len(sink.accepted), 1, "The sink must hold the content before the sending transaction is lost."
            )
            # What a stopped worker leaves on record: its claim, and nothing of the send.
            self.assertEqual(
                DeliveryAttempt.objects.get(intent_id=intent.pk).phase,
                "CLAIMED",
                "The lost transaction must have taken the STARTED marker with it, as a stopped worker's does.",
            )
            self.assert_recorded(
                intent,
                Outbox.State.QUEUED,
                "",
                1,
                "After the stop, the committed claim must be all that is on record for the intent.",
            )
            self.assert_no_capture(intent, "The lost transaction must have left no capture row.")
            self.assertIsNotNone(
                Outbox.objects.values_list("lease_until", flat=True).get(pk=intent.pk),
                "The stopped worker's claim must still hold its lease.",
            )

            # The lease expires; recovery runs; a later worker runs.
            Outbox.objects.filter(pk=intent.pk).update(lease_until=database_now() - timedelta(seconds=1))
            settled = recover()
            self.due(intent)
            attempted_later = run_worker()
            self.assertEqual(
                len(sink.received),
                1,
                "After a lost sending transaction, recovery and a later worker run must not send a second copy.",
            )
            self.assertEqual(
                (settled, attempted_later),
                (1, 0),
                "Recovery must settle the abandoned claim once, and the later run must find nothing to send.",
            )
            self.assert_recorded(
                intent,
                Outbox.State.ACCEPTANCE_UNCERTAIN,
                "ACCEPTANCE_UNKNOWN",
                1,
                "An expired claim that may already have been sent must be left as an unknown outcome, "
                "never queued for another send.",
            )
            self.assert_no_capture(intent, "An unknown outcome must leave no capture row.")
        self.assertEqual(sink.faults, [], "The sink must have met no socket fault of its own.")

    def test_06_direct_calls_keep_one_capture_and_one_copy(self):
        """UD-09: called directly, a failing call keeps no capture, and two overlapping calls relay once."""
        # A failing call outside any worker transaction: the relay's own savepoint is all there is.
        with override_settings(**relay_settings(EMAIL_PORT=closed_port())):
            failing = self.claimable_intent()
            reason = None
            try:
                backend().send_messages([self.message_for(Outbox.objects.get(pk=failing.pk))])
            except NotAccepted as refused:
                reason = str(refused)
            self.assertEqual(
                reason, "relay_unavailable", "A direct call with nothing listening must be refused as unavailable."
            )
            self.assert_no_capture(
                failing, "A failed direct call must leave no capture row: the relay's own savepoint removes it."
            )

        # Two overlapping calls for one intent: the first is held after the sink has its copy.
        sink = self.sink()
        real_relay = LoopbackRelayBackend._relay
        first_has_relayed = threading.Event()
        release = threading.Event()
        relays, results, faults, pids = [], [], [], {}

        def relay_and_hold(relay, message):
            relays.append(1)
            real_relay(relay, message)
            if len(relays) == 1:
                first_has_relayed.set()
                release.wait(LoopbackSink.HOLD_SECONDS)

        with override_settings(**relay_settings(EMAIL_PORT=sink.port)):
            intent = self.claimable_intent()
            row = Outbox.objects.get(pk=intent.pk)

            def call():
                close_old_connections()
                try:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_backend_pid()")
                        pids[threading.current_thread().name] = cursor.fetchone()[0]
                    results.append(backend().send_messages([self.message_for(row)]))
                except Exception as fault:
                    faults.append(type(fault).__name__)
                finally:
                    connection.close()

            first = threading.Thread(target=call, name="ud-direct-call-1", daemon=True)
            second = threading.Thread(target=call, name="ud-direct-call-2", daemon=True)
            reached = waiting = False
            with mock.patch.object(LoopbackRelayBackend, "_relay", relay_and_hold):
                try:
                    first.start()
                    reached = first_has_relayed.wait(LoopbackSink.STEP_SECONDS)
                    if reached:
                        second.start()
                        waiting = self.blocked_by(lambda: pids.get("ud-direct-call-2"), pids.get("ud-direct-call-1"))
                finally:
                    release.set()
                    for thread in (first, second):
                        if thread.ident is not None:
                            thread.join(10)
            self.assertTrue(reached, "The first call must have handed its copy to the sink within five seconds.")
            self.assertTrue(
                waiting,
                "The second call's session must have been blocked by the first call's session while it was held, "
                "or this case witnesses nothing.",
            )
            self.assertFalse(
                first.is_alive() or second.is_alive(), "Both overlapping calls must finish within ten seconds."
            )
            self.assertEqual(faults, [], "Neither overlapping call may fail.")
            self.assertEqual(results, [1, 1], "Both overlapping calls must be accepted.")
            self.assertEqual(
                LocalCapture.objects.filter(intent_id=intent.pk).count(),
                1,
                "Two overlapping calls for one intent must leave one capture row.",
            )
            self.assertEqual(
                len(sink.received), 1, "Two overlapping calls for one intent must relay one copy only."
            )
        self.assertEqual(sink.faults, [], "The sink must have met no socket fault of its own.")
