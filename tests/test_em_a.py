"""EM-a's ten obligation witnesses. PostgreSQL 17 rehearsal/admission still required.

Fixtures use the existing disposable identity helper. No network, new provider,
actual notice, new product authority or production administrator is introduced.
"""

import threading
import uuid
from datetime import timedelta
from queue import Queue
from unittest.mock import patch

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.mail import EmailMessage
from django.db import (
    DatabaseError,
    IntegrityError,
    close_old_connections,
    connection,
    transaction,
)
from django.test import Client, TransactionTestCase, override_settings

from core.models import Identity, OrganismCommandReceipt
from core.session import (
    SESSION_ACCESS_EPOCH_KEY,
    SESSION_IDENTITY_KEY,
    SESSION_STARTED_AT_KEY,
)
from email_foundation.backend import NotAccepted, backend
from email_foundation.contracts import MailRefused, Origin, digest
from email_foundation.models import (
    AddressRecord,
    DeliveryAttempt,
    LocalCapture,
    Outbox,
    TemplateVersion,
)
from email_foundation.service import (
    database_now,
    request_message,
    request_verification,
    verify_address,
)
from email_foundation.worker import claim, deliver, housekeeping, recover, run_worker
from tests.ema_support import OutageBackend, UncertainBackend
from tests.s015_0021_support import create_test_identity


@override_settings(
    INTEVIA_TEST_CONTEXT=True,
    EMAIL_BACKEND="email_foundation.backend.CaptureBackend",
    INTEVIA_EMAIL_TEMPLATES=("tests.ema_support.NOTICE", "tests.ema_support.ESSENTIAL"),
)
class EmailFoundationTests(TransactionTestCase):
    def setUp(self):
        self.assertEqual(
            connection.vendor,
            "postgresql",
            "EM-a requires the PostgreSQL rehearsal substrate; another database cannot qualify these witnesses.",
        )
        self.person = create_test_identity(
            username="ema_" + uuid.uuid4().hex, access_state=Identity.AccessState.ACTIVE
        )
        self.other = create_test_identity(
            username="ema_" + uuid.uuid4().hex, access_state=Identity.AccessState.ACTIVE
        )
        self.org = uuid.uuid4()
        AddressRecord.objects.create(
            identity=self.person,
            verified_address="recipient@example.invalid",
            verified_at=database_now(),
        )

    def act(self, recipient=None, organisation=None, recipients=None):
        marker = uuid.uuid4().hex
        return OrganismCommandReceipt.objects.create(
            actor=self.person,
            action="EMA_FIXTURE",
            idempotency_key=marker,
            request_reference="ema-fixture:" + marker,
            payload_fingerprint=digest(marker),
            authority_decision_reference="s015d1:" + digest(marker),
            lineage_reference="s015l1:" + digest(marker),
            result_payload={
                "organisation": str(organisation or self.org),
                **(
                    {"recipients": recipients}
                    if recipients is not None
                    else {"recipient": (recipient or self.person).pk}
                ),
                "outcome": "UNCHANGED_TEST_ACT",
            },
        )

    def enqueue(
        self,
        act=None,
        category="NOTICE",
        recipient=None,
        organisation=None,
        key=None,
        context=None,
    ):
        act = act or self.act(recipient, organisation)
        with transaction.atomic():
            return request_message(
                category=category,
                recipient=recipient or self.person,
                template="fixture.essential"
                if category == "ESSENTIAL"
                else "fixture.notice",
                version=1,
                context=context or {"record_id": str(act.receipt_uuid)},
                origin=Origin(act.lineage_reference, organisation or self.org),
                request_key=key or str(act.receipt_uuid),
            )

    def assert_refused(self, operation, why):
        try:
            operation()
        except MailRefused:
            return
        self.fail(why)

    def due(self, row):
        Outbox.objects.filter(pk=row.pk).update(
            ready_at=database_now() - timedelta(seconds=1)
        )

    def code(self, intent):
        # Never print the generated verification capability, including on failure.
        return intent.body.split("Code: ", 1)[1].split("\n", 1)[0]

    def test_01_atomic_intent_and_one_request(self):
        """EMA-01: act/intent atomicity and replay hold; per-recipient refusal does not undo the act."""
        act_id = intent_id = None
        try:
            with transaction.atomic():
                act = self.act()
                act_id = act.pk
                row = self.enqueue(act)
                intent_id = row.pk
                self.assert_refused_worker()
                raise RuntimeError("fixture rollback")
        except RuntimeError as exc:
            self.assertEqual(
                str(exc),
                "fixture rollback",
                "The atomicity witness must reach its deliberate rollback, not fail in setup.",
            )
        self.assertFalse(
            OrganismCommandReceipt.objects.filter(pk=act_id).exists(),
            "The rolled-back fixture act must be absent.",
        )
        self.assertFalse(
            Outbox.objects.filter(pk=intent_id).exists(),
            "A rolled-back act must not leave an email intent.",
        )
        act = self.act()
        row = self.enqueue(act)
        again = self.enqueue(act)
        self.assertEqual(
            row.pk,
            again.pk,
            "Replaying the same act/request must return the original email intent.",
        )
        self.assertEqual(
            Outbox.objects.filter(request_key=str(act.receipt_uuid)).count(),
            1,
            "The replay must leave exactly one intent for this request.",
        )
        try:
            with transaction.atomic():
                values = {
                    field.attname: getattr(row, field.attname)
                    for field in Outbox._meta.concrete_fields
                    if not field.primary_key
                }
                Outbox.objects.create(**values)
        except IntegrityError:
            pass
        else:
            self.fail(
                "The database must refuse a second outbox intent with the same request key."
            )
        run_worker()
        self.assertEqual(
            LocalCapture.objects.filter(intent=row).count(),
            1,
            "The committed intent must produce one local capture after commit.",
        )

        # Each recipient has a savepoint; catch outside it but inside the act.
        refusals = []
        with transaction.atomic():
            batch_act = self.act(recipients=[self.other.pk, self.person.pk])
            for recipient in (self.other, self.person):
                try:
                    with transaction.atomic():
                        self.enqueue(
                            batch_act,
                            recipient=recipient,
                            key=f"{batch_act.receipt_uuid}:{recipient.pk}",
                        )
                except MailRefused:
                    refusals.append(recipient.pk)
        self.assertEqual(
            refusals,
            [self.other.pk],
            "Only the unverified recipient must be refused; later recipients must still be processed.",
        )
        self.assertTrue(
            OrganismCommandReceipt.objects.filter(pk=batch_act.pk).exists(),
            "A handled notice refusal must not roll back the caller's recorded act.",
        )
        self.assertTrue(
            Outbox.objects.filter(
                request_key=f"{batch_act.receipt_uuid}:{self.person.pk}"
            ).exists(),
            "The verified recipient's intent must commit with the act despite another refusal.",
        )

    def assert_refused_worker(self):
        try:
            run_worker()
        except RuntimeError:
            return
        self.fail(
            "An email worker called before the originating transaction commits must refuse without capture."
        )

    @override_settings(INTEVIA_EMAIL_MAX_ATTEMPTS=2)
    def test_02_outage_bounded_and_uncertain_not_retried(self):
        """EMA-02: definite outage exhausts its bound; uncertainty never becomes a blind resend."""
        act = self.act()
        original = act.result_payload.copy()
        row = self.enqueue(act)
        with patch("email_foundation.worker.backend", return_value=OutageBackend()):
            run_worker()
            self.due(row)
            run_worker()
            self.due(row)
            run_worker()
        row.refresh_from_db()
        act.refresh_from_db()
        self.assertEqual(
            row.state,
            Outbox.State.FAILED,
            "Two definitely unaccepted attempts must end in FAILED, not unbounded retries.",
        )
        self.assertEqual(
            row.attempt_count,
            2,
            "The failed notice must consume no more than its configured two attempts.",
        )
        self.assertEqual(
            act.result_payload,
            original,
            "An email outage must leave the originating act's saved outcome unchanged.",
        )
        uncertain = self.enqueue()
        adapter = UncertainBackend()
        with patch("email_foundation.worker.backend", return_value=adapter):
            run_worker()
            self.due(uncertain)
            run_worker()
        uncertain.refresh_from_db()
        self.assertEqual(
            uncertain.state,
            Outbox.State.ACCEPTANCE_UNCERTAIN,
            "A possible acceptance must remain explicitly uncertain.",
        )
        self.assertEqual(
            uncertain.attempt_count,
            1,
            "Uncertain acceptance must not trigger a second send.",
        )
        self.assertFalse(
            LocalCapture.objects.filter(intent=uncertain).exists(),
            "The outage fixture must not fabricate a capture receipt.",
        )
        original_finish = uncertain.attempts.get().finished_at
        self.assertIsNone(
            uncertain.terminal_at, "Uncertain acceptance is not a terminal outcome."
        )
        pending = self.enqueue()
        claim()
        Outbox.objects.filter(pk=pending.pk).update(
            lease_until=database_now() - timedelta(seconds=1)
        )
        self.assertEqual(
            recover(limit=1, adapter=adapter),
            1,
            "An older unresolved row must not starve an expired pre-send lease.",
        )
        self.assertEqual(
            recover(limit=1, adapter=adapter),
            0,
            "Rechecking unchanged uncertainty must not report a changed outcome.",
        )
        self.assertEqual(
            uncertain.attempts.get().finished_at,
            original_finish,
            "Unchanged uncertainty must preserve the attempt's original completion timestamp.",
        )

    @override_settings(INTEVIA_EMAIL_RETRY_SECONDS=60, INTEVIA_EMAIL_MAX_ATTEMPTS=1)
    def test_03_expired_claim_fenced_and_workers_overlap(self):
        """EMA-03: final-slot pre-send recovery is immediate, stale leases refuse, and overlap captures once."""
        row = self.enqueue()
        old = claim()
        self.assertIsNotNone(
            old,
            "The committed notice must be claimable before the simulated worker stop.",
        )
        Outbox.objects.filter(pk=row.pk).update(
            lease_until=database_now() - timedelta(seconds=1)
        )
        recover()
        fresh = claim()
        self.assertIsNotNone(
            fresh, "Recovery must make an abandoned pre-send claim eligible again."
        )
        self.assertFalse(
            deliver(*old),
            "A worker holding the expired lease must not send after another lease was issued.",
        )
        self.assertTrue(
            deliver(*fresh), "The fresh lease must capture the recovered notice."
        )
        late = self.enqueue()
        late_work = claim()
        late.refresh_from_db()
        with patch(
            "email_foundation.worker.database_now",
            return_value=late.lease_until + timedelta(seconds=1),
        ):
            self.assertFalse(
                deliver(*late_work), "An expired lease must return without capture."
            )
        self.assertEqual(
            DeliveryAttempt.objects.get(lease_id=late_work[1]).phase,
            "CLAIMED",
            "A return before sending must not leave a STARTED attempt.",
        )
        Outbox.objects.filter(pk=late.pk).update(
            lease_until=database_now() - timedelta(seconds=1)
        )
        self.assertEqual(
            recover(limit=1), 1, "The never-started final-slot claim must recover."
        )
        self.assertTrue(
            deliver(*claim()), "Recovered final-slot claim must still be sendable."
        )

        overlap = self.enqueue()
        barrier = threading.Barrier(2)
        results = Queue()

        def work():
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                run_worker(limit=1)
                results.put("ok")
            except Exception as exc:
                results.put(type(exc).__name__)
            finally:
                connection.close()

        threads = [threading.Thread(target=work) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertTrue(
            all(not t.is_alive() for t in threads),
            "Overlapping email workers must finish without a lock hang.",
        )
        outcomes = [results.get(timeout=1) for _ in threads]
        self.assertEqual(
            outcomes,
            ["ok", "ok"],
            "Both workers must finish without setup/execution errors; an error is not a no-double-send proof.",
        )
        self.assertEqual(
            LocalCapture.objects.filter(intent=overlap).count(),
            1,
            "Overlapping workers must persist one capture for the same intent.",
        )
        self.assertEqual(
            DeliveryAttempt.objects.filter(intent=overlap).count(),
            1,
            "Only one worker may begin the overlapping intent's attempt.",
        )
        # Simulate a committed capture whose final outcome was lost before recording.
        overlap.refresh_from_db()
        Outbox.objects.filter(pk=overlap.pk).update(
            state=Outbox.State.ACCEPTANCE_UNCERTAIN
        )
        recover()
        overlap.refresh_from_db()
        self.assertEqual(
            overlap.state,
            Outbox.State.PROVIDER_ACCEPTED,
            "Recovery must reconcile the durable local receipt without another send.",
        )
        self.assertEqual(
            overlap.attempt_count,
            1,
            "Reconciliation must not create a replacement delivery attempt.",
        )

    @override_settings(
        INTEVIA_EMAIL_DAILY_CAPACITY=3, INTEVIA_EMAIL_ESSENTIAL_RESERVE=1
    )
    def test_04_reserved_capacity_and_marketing_refusal(self):
        """EMA-04: notice saturation leaves the reserved essential slot and marketing creates no intent."""
        notices = [self.enqueue() for _ in range(3)]
        run_worker()
        self.assertEqual(
            LocalCapture.objects.filter(intent__in=notices).count(),
            2,
            "A NOTICE backlog must stop before consuming the reserved ESSENTIAL slot.",
        )
        essential = self.enqueue(category="ESSENTIAL")
        run_worker()
        self.assertTrue(
            LocalCapture.objects.filter(intent=essential).exists(),
            "ESSENTIAL must still capture after NOTICE reaches its nonreserved allowance.",
        )
        before = Outbox.objects.count()
        self.assert_refused(
            lambda: self.enqueue(category="MARKETING"),
            "MARKETING must refuse before creating any email intent.",
        )
        self.assertEqual(
            Outbox.objects.count(),
            before,
            "A marketing refusal must not leave an outbox row.",
        )
        try:
            with transaction.atomic():
                Outbox.objects.filter(pk=essential.pk).update(category="MARKETING")
        except IntegrityError:
            pass
        else:
            self.fail(
                "The database must also refuse changing an intent into MARKETING."
            )

    def test_05_verification_expiry_single_use_and_resend(self):
        """EMA-05: inbox proof expires, is single-use, replacement-invalidated and capped at five per UTC day; it confers no authority."""
        pending = create_test_identity(
            username="ema_pending_" + uuid.uuid4().hex,
            access_state=Identity.AccessState.PENDING,
        )
        row = request_verification(
            credential=pending.credential,
            proposed_address="pending@example.invalid",
            request_id=uuid.uuid4(),
        )
        self.assertEqual(
            row.category,
            "ESSENTIAL",
            "A verification challenge for an unverified inbox must be ESSENTIAL.",
        )
        run_worker()
        self.assertTrue(
            LocalCapture.objects.filter(intent=row).exists(),
            "An unverified pending Identity must receive its local verification capture.",
        )
        self.assert_refused(
            lambda: request_verification(
                credential=pending.credential,
                proposed_address="pending@example.invalid",
                request_id=uuid.uuid4(),
            ),
            "An immediate resend must be refused without replacing the current challenge.",
        )
        token = self.code(row)
        verify_address(
            credential=pending.credential,
            challenge_id=row.verification_challenge,
            token=token,
        )
        self.assert_refused(
            lambda: verify_address(
                credential=pending.credential,
                challenge_id=row.verification_challenge,
                token=token,
            ),
            "A consumed inbox challenge must not verify twice.",
        )
        pending.refresh_from_db()
        self.assertEqual(
            pending.access_state,
            Identity.AccessState.PENDING,
            "Verifying an inbox must not activate the Identity or confer authority.",
        )
        expired = request_verification(
            credential=self.other.credential,
            proposed_address="other@example.invalid",
            request_id=uuid.uuid4(),
        )
        AddressRecord.objects.filter(identity=self.other).update(
            expires_at=database_now() - timedelta(seconds=1)
        )
        self.assert_refused(
            lambda: verify_address(
                credential=self.other.credential,
                challenge_id=expired.verification_challenge,
                token=self.code(expired),
            ),
            "An expired challenge must leave the address unverified.",
        )
        housekeeping()
        expired.refresh_from_db()
        proof = LocalCapture.objects.get(intent=row)
        self.assertEqual(
            proof.body,
            "",
            "Consuming verification must allow prompt erasure of the captured capability text.",
        )
        self.assertEqual(
            backend().lookup(Outbox.objects.get(pk=row.pk)),
            "ACCEPTED",
            "Erasing a verification capability must retain its local acceptance proof.",
        )
        self.assertEqual(
            expired.body,
            "",
            "Expired verification capability text must be erased by short-lived challenge housekeeping.",
        )
        self.assertFalse(
            AddressRecord.objects.get(identity=self.other).challenge_digest,
            "Expired challenge housekeeping must erase its stored digest.",
        )
        replacement = None
        for ordinal in range(2, 6):
            AddressRecord.objects.filter(identity=pending).update(
                last_requested_at=database_now() - timedelta(seconds=61)
            )
            previous = replacement
            replacement = request_verification(
                credential=pending.credential,
                proposed_address="next@example.invalid",
                request_id=uuid.uuid4(),
            )
            if previous is not None:
                self.assert_refused(
                    lambda: verify_address(
                        credential=pending.credential,
                        challenge_id=previous.verification_challenge,
                        token=self.code(previous),
                    ),
                    "A newer challenge must invalidate the previous unconsumed code.",
                )
                previous.refresh_from_db()
                self.assertEqual(
                    previous.state,
                    Outbox.State.SUPPRESSED,
                    "A superseded unleased challenge must be suppressed before capacity is charged.",
                )
        record = AddressRecord.objects.get(identity=pending)
        self.assertEqual(
            record.requests_today,
            5,
            "Exactly five distinct challenges must consume the daily allowance.",
        )
        AddressRecord.objects.filter(identity=pending).update(
            last_requested_at=database_now() - timedelta(seconds=61)
        )
        self.assert_refused(
            lambda: request_verification(
                credential=pending.credential,
                proposed_address="sixth@example.invalid",
                request_id=uuid.uuid4(),
            ),
            "A sixth challenge in the same UTC day must refuse even after the resend interval.",
        )
        self.assertEqual(
            AddressRecord.objects.get(identity=pending).challenge_id,
            replacement.verification_challenge,
            "The daily-cap refusal must preserve the fifth challenge.",
        )

    def test_06_audience_and_content_boundaries(self):
        """EMA-06: wrong scope, malformed organisation, unverified or severed recipients and unapproved broadcast refuse."""
        act = self.act()
        before = Outbox.objects.count()
        self.assert_refused(
            lambda: self.enqueue(act, recipient=self.other),
            "An act for one Identity must not create email for another.",
        )
        self.assert_refused(
            lambda: self.enqueue(act, organisation=uuid.uuid4()),
            "An act in one organisation must not become another organisation's email audience.",
        )
        other_act = self.act(recipient=self.other)
        self.assert_refused(
            lambda: self.enqueue(other_act, recipient=self.other),
            "A nonverification notice must refuse an unverified inbox.",
        )
        self.assert_refused(
            lambda: self.enqueue(
                act,
                context={"record_id": str(act.receipt_uuid), "other_name": "private"},
            ),
            "Arbitrary personal content must not enter email context.",
        )
        with transaction.atomic():
            self.assert_refused(
                lambda: request_message(
                    category="NOTICE",
                    recipient=self.person,
                    template="broadcast",
                    version=1,
                    context={"record_id": str(act.receipt_uuid)},
                    origin=Origin(act.lineage_reference, self.org),
                    request_key="broadcast-fixture",
                ),
                "No broadcast adapter is commissioned in EM-a; an unapproved request must create no intent.",
            )
        self.assertEqual(
            Outbox.objects.count(),
            before,
            "Boundary refusals must leave no email intents.",
        )
        row = self.enqueue(act)
        run_worker()
        captured = LocalCapture.objects.get(intent=row)
        self.assertEqual(
            captured.recipient,
            "recipient@example.invalid",
            "The capture must have exactly the authorised inbox.",
        )
        self.assertNotIn(
            str(self.other.identity_id),
            captured.body,
            "The body must not contain another Identity's data.",
        )
        self.assertIn(
            "http://localhost:8000/history/",
            captured.body,
            "A service notice must link to its record rather than copying sensitive record content.",
        )
        self.assert_refused(
            lambda: self.enqueue(act, organisation="not-a-uuid"),
            "A malformed organisation reference must produce MailRefused before database insertion.",
        )
        queued = self.enqueue()
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM public.core_identityresolution WHERE identity_id = %s",
                [self.person.pk],
            )
        self.assert_refused(
            lambda: self.enqueue(),
            "A severed reference must refuse new notices even while its legacy Identity copy remains.",
        )
        self.assert_refused(
            lambda: request_verification(
                credential=self.person.credential,
                proposed_address="severed@example.invalid",
                request_id=uuid.uuid4(),
            ),
            "A severed reference must not request new verification.",
        )
        run_worker()
        queued.refresh_from_db()
        self.assertEqual(
            queued.state,
            Outbox.State.SUPPRESSED,
            "An already queued notice must refuse the recipient after severance.",
        )
        self.assertFalse(
            LocalCapture.objects.filter(intent=queued).exists(),
            "No local capture may be created for a severed recipient.",
        )

    def test_07_recheck_address_before_capture(self):
        """EMA-07: a queued notice is suppressed if its verified address changed before the worker acts."""
        row = self.enqueue()
        change = request_verification(
            credential=self.person.credential,
            proposed_address="replacement@example.invalid",
            request_id=uuid.uuid4(),
        )
        verify_address(
            credential=self.person.credential,
            challenge_id=change.verification_challenge,
            token=self.code(change),
        )
        run_worker()
        row.refresh_from_db()
        self.assertEqual(
            row.state,
            Outbox.State.SUPPRESSED,
            "The worker must suppress a notice addressed to the superseded inbox.",
        )
        self.assertFalse(
            LocalCapture.objects.filter(intent=row).exists(),
            "No capture may target the superseded verified address.",
        )

    def test_08_housekeeping_preserves_governed_evidence_and_dedupe(self):
        """EMA-08: operational erasure preserves the act and the one-request tombstone."""
        act = self.act()
        original = act.result_payload.copy()
        row = self.enqueue(act)
        run_worker()
        Outbox.objects.filter(pk=row.pk).update(
            terminal_at=database_now() - timedelta(days=181)
        )
        active = request_verification(
            credential=self.other.credential,
            proposed_address="active@example.invalid",
            request_id=uuid.uuid4(),
        )
        Outbox.objects.filter(pk=active.pk).update(
            created_at=row.created_at - timedelta(seconds=1)
        )
        uncertain_verification = request_verification(
            credential=self.person.credential,
            proposed_address="uncertain@example.invalid",
            request_id=uuid.uuid4(),
        )
        # Persist the exact uncertain state, not an invented acceptance receipt.
        Outbox.objects.filter(pk=uncertain_verification.pk).update(
            state=Outbox.State.ACCEPTANCE_UNCERTAIN, terminal_at=None
        )
        AddressRecord.objects.filter(identity=self.person).update(
            expires_at=database_now() - timedelta(seconds=1)
        )
        housekeeping(limit=1)
        uncertain_verification.refresh_from_db()
        self.assertIsNone(
            uncertain_verification.payload_erased_at,
            "Expired verification must not erase an unresolved acceptance payload.",
        )
        self.assertTrue(
            bool(uncertain_verification.body),
            "The unresolved payload must remain available for reconciliation.",
        )
        row.refresh_from_db()
        act.refresh_from_db()
        self.assertEqual(
            row.body,
            "",
            "Terminal operational message payload must be erased after the retention window.",
        )
        self.assertFalse(
            LocalCapture.objects.filter(intent=row).exists(),
            "Retention must remove the duplicate capture payload too.",
        )
        self.assertFalse(
            DeliveryAttempt.objects.filter(intent=row).exists(),
            "Old terminal attempt records must be removed by housekeeping.",
        )
        self.assertEqual(
            act.result_payload,
            original,
            "Outbox housekeeping must leave the governed act's evidence untouched.",
        )
        replay = self.enqueue(act)
        self.assertEqual(
            replay.pk,
            row.pk,
            "A request replay after payload erasure must return its retained intent, not send again.",
        )
        run_worker()
        self.assertFalse(
            LocalCapture.objects.filter(intent=row).exists(),
            "A retained terminal tombstone must not be recaptured on replay.",
        )

    def test_09_operator_view_local_only_and_read_only(self):
        """EMA-09: only an explicitly named active operator sees local captures; production and writes refuse."""
        import logging
        import time

        request_logger = logging.getLogger("django.request")
        self.addCleanup(request_logger.setLevel, request_logger.level)
        request_logger.setLevel(logging.ERROR)

        row = self.enqueue()
        client = Client()
        client.force_login(self.person.credential)
        session = client.session
        session[SESSION_IDENTITY_KEY] = str(self.person.identity_id)
        session[SESSION_ACCESS_EPOCH_KEY] = self.person.access_epoch
        session[SESSION_STARTED_AT_KEY] = int(time.time())
        session.save()
        url = "/development/email-outbox/"
        with override_settings(
            ROOT_URLCONF="tests.ema_support",
            SETTINGS_MODULE="intevia.email_local_settings",
            DEBUG=True,
            INTEVIA_EMAIL_OUTBOX_PAGE=True,
            INTEVIA_EMAIL_OPERATOR_IDENTITIES=(str(self.person.identity_id),),
        ):
            response = client.get(url)
            self.assertEqual(
                response.status_code,
                200,
                "The named active local operator must be able to inspect capture intent.",
            )
            self.assertIn(
                b"CAPTURED_LOCAL",
                response.content,
                "The view must explain capture does not mean sent, delivered or read.",
            )
            self.assertIn(
                "no-store",
                response["Cache-Control"],
                "Captured addresses and challenge text must never be cacheable.",
            )
            self.assertEqual(
                client.post(url).status_code,
                405,
                "The operator outbox must not offer a write or resend action.",
            )
            with override_settings(INTEVIA_EMAIL_OPERATOR_IDENTITIES=()):
                self.assertEqual(
                    client.get(url).status_code,
                    404,
                    "A signed-in user without the explicit operator designation must see no outbox.",
                )
            with override_settings(SETTINGS_MODULE="intevia.settings"):
                self.assertEqual(
                    client.get(url).status_code,
                    404,
                    "Even an enabled flag and operator session must not expose the page under production settings.",
                )
        row.refresh_from_db()
        self.assertEqual(
            row.state,
            Outbox.State.REQUESTED,
            "Viewing or posting to the read-only page must not send or change the intent.",
        )

    def test_10_template_immutability_and_provider_seam(self):
        """EMA-10: template guards survive qualified test flush; undeclared mail/backends and populated reversal refuse."""
        row = self.enqueue()
        try:
            with transaction.atomic():
                TemplateVersion.objects.filter(pk=row.template_id).update(
                    digest="0" * 64
                )
        except DatabaseError as exc:
            self.assertIn(
                "EM-a template versions are immutable",
                str(exc),
                "The template update must reach the named immutable-version guard, not an unrelated database error.",
            )
        else:
            self.fail(
                "A used template version must reject database mutation; issue a fresh version instead."
            )
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(
                    "TRUNCATE TABLE email_foundation_templateversion CASCADE"
                )
        except DatabaseError as exc:
            self.assertIn(
                "EM-a template versions are immutable",
                str(exc),
                "Bulk erasure must reach the template-version guard, not remove the version anchors.",
            )
        else:
            self.fail(
                "Truncating version anchors must refuse even through the disposable owner test route."
            )
        from django.db.migrations.executor import MigrationExecutor

        try:
            MigrationExecutor(connection).migrate([("email_foundation", None)])
        except RuntimeError as exc:
            self.assertIn(
                "EM-a reverse refused",
                str(exc),
                "Populated reversal must reach EM-a's explicit preservation refusal.",
            )
        else:
            self.fail(
                "Reversing the email migration with an existing intent must refuse before removing tables."
            )
        self.assertTrue(
            Outbox.objects.filter(pk=row.pk).exists(),
            "A refused migration reversal must retain the existing intent.",
        )
        self.assertTrue(
            backend().capabilities.local_capture,
            "EM-a's configured backend must identify acceptance as local capture.",
        )
        self.assertFalse(
            backend().capabilities.suppression,
            "Capture must not pretend to prove provider suppression integration.",
        )
        self.assertFalse(
            backend().capabilities.inbound_parse,
            "Capture must not pretend to implement inbound processing.",
        )
        with override_settings(
            EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend"
        ):
            try:
                run_worker()
            except ImproperlyConfigured:
                pass
            else:
                self.fail(
                    "A generic SMTP backend without declared capabilities must refuse before claim or send."
                )
        self.assertEqual(
            DeliveryAttempt.objects.filter(intent=row).count(),
            0,
            "Invalid provider configuration must not claim the queued intent.",
        )
        self.assertFalse(
            settings.INTEVIA_EMAIL_ALLOW_EXTERNAL,
            "External sending must remain disabled in this unit.",
        )

        message = EmailMessage(
            subject="fixture", body="fixture", to=["fixture@example.invalid"]
        )
        for intent in (None, uuid.uuid4()):
            if intent is not None:
                message.ema_intent_id = intent
            try:
                backend().send_messages([message])
            except NotAccepted:
                pass
            else:
                self.fail(
                    "Mail without a known EM-a intent must be refused as NotAccepted."
                )
        # Exercise the real qualified disposable reset, not a blanket trigger bypass.
        call_command("flush", interactive=False, verbosity=0, inhibit_post_migrate=True)
        self.assertFalse(
            TemplateVersion.objects.exists(),
            "Qualified test teardown must flush template rows without breaking the suite.",
        )
        template = TemplateVersion.objects.create(
            name="post-flush", version=1, digest="1" * 64
        )
        try:
            with transaction.atomic():
                TemplateVersion.objects.filter(pk=template.pk).update(digest="2" * 64)
        except DatabaseError as exc:
            self.assertIn(
                "EM-a template versions are immutable",
                str(exc),
                "The flush route must restore the template guard before returning.",
            )
        else:
            self.fail("The test reset must not leave template protection disabled.")
