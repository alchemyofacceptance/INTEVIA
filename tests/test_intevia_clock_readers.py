"""Three readers of the INTEVIA Clock, each shown across a move.

The module is also the URL configuration for the session witness: one route that
exists only here, added to the product's own routes, written as the front end
will write an LO-scoped page.

Every fixture is this file's own, or a landed helper. Each test moves the clock
through the owned test route, so CLK-05's code half reads this file and all it
reaches. The amounts named MOVED and SHORT are test data. Every other period
used here is the product's own and is read from the product: the lease from the
row the worker wrote, the session's age limit from core.session.
"""

import io
from datetime import timedelta
from unittest import mock
from uuid import uuid4

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, transaction
from django.http import JsonResponse
from django.test import Client, TransactionTestCase, override_settings
from django.urls import path

from core import models
from core.session import SESSION_ABSOLUTE_AGE_SECONDS
from email_foundation.contracts import Origin, digest
from email_foundation.models import AddressRecord, Outbox
from email_foundation.service import request_message
from email_foundation.worker import claim, recover
from intevia.test_postgresql_backend import clock as owned_route
from intevia.urls import urlpatterns as product_urlpatterns
from src.intevia.services import cq
from src.intevia.services import discussion as disc
from src.intevia.services import timed_events_handoff as handoff
from src.intevia.services.organism_formation_service import (
    FoundLivingOrganismCommand,
    OrganismFormationService,
)
from tests import s015_0021_support as support
from tests import test_law_acts as law

MOVED = timedelta(days=2)  # test data: how far the intake witness moves the clock
SHORT = timedelta(seconds=20)  # test data: how far short of a lease's end the mail witness first stops
TEST_PASSWORD = "governed-test-password"  # the landed fixture's own test value; not a secret


def _lo_page(request, organism):
    """Test entry. It reads through a landed LO-scoped service and confirms on its success path only."""
    try:
        cq.settings(credential=request.user, organism_uuid=organism)
    except disc.DiscussionUnavailable:
        return JsonResponse({"refused": "service"})
    handoff.confirm()
    return JsonResponse({"answered": True})


urlpatterns = [
    path("clock-readers-test/lo/<uuid:organism>/", _lo_page),
    *product_urlpatterns,
]


class _CommandFails(Exception):
    """Raised inside a command's own transaction, so that the command rolls back."""


class ClockReaderTests(TransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, "postgresql", "The Clock's readers are witnessed on PostgreSQL only.")

    # ------------------------------------------------------------------ intake

    def test_a_request_keeps_its_receipt_time_across_a_rollback_a_move_and_a_retry(self):
        """A request received before a move keeps its received_at and fingerprint when retried after it; a new request gets the moved instant."""
        tag = uuid4().hex
        founder = support.register_test_learner(username="clock-reader-" + tag)
        with transaction.atomic(), connection.cursor() as cursor:
            support.seed_fixture_substitute_authority(cursor, "clock-reader-principal-" + tag)
        now = support.database_now()
        terms = support.registration_terms_version_in_force(effective_at=now, received_at=now)
        service = OrganismFormationService(clock=support.database_now)

        def command(label):
            return FoundLivingOrganismCommand(
                credential=founder.credential, name="Clock reader %s %s" % (label, tag),
                purpose="A request received before the clock moves", request_reference="clock-reader:%s:%s" % (label, tag),
                idempotency_key="clock-reader:%s:%s" % (label, tag), accepted_terms_version_uuid=terms.terms_version_uuid)

        def intake(request):
            rows = list(models.CommandIntake.objects.filter(actor=founder, idempotency_key=request.idempotency_key)
                        .values_list("pk", "received_at", "request_fingerprint"))
            self.assertEqual(len(rows), 1, "A logical request must have exactly one intake record.")
            return rows[0]

        def founded():
            return models.OrganismRoleDefinition.objects.filter(enterer_id=founder.pk).count()

        first = command("first")
        write = OrganismFormationService._write_founding_batch

        def write_then_fail(*args, **kwargs):
            write(*args, **kwargs)
            raise _CommandFails  # after the founding's records are written, inside the command's own transaction

        with mock.patch.object(OrganismFormationService, "_write_founding_batch", autospec=True, side_effect=write_then_fail):
            with self.assertRaises(_CommandFails):
                service.found_living_organism(first)
        received = intake(first)
        self.assertEqual(founded(), 0, "The command must have rolled back: nothing may be founded yet.")
        self.assertLessEqual(received[1], support.database_now())

        owned_route.advance(MOVED)
        self.assertGreaterEqual(support.database_now(), received[1] + MOVED,
                                "The clock must show the move before the retry is sent.")

        result = service.found_living_organism(first)
        self.assertEqual(
            intake(first), received,
            "A request INTEVIA received before the clock moved was retried after the move, and its record of receipt "
            "changed. The retry must find the same record, with the original time of receipt and the same fingerprint, "
            "because the request is answered as of the time it was first received.")
        role = models.OrganismRoleDefinition.objects.get(
            living_organism__organism_id=result.living_organism_uuid, code="COORDINATOR")
        self.assertEqual(
            role.received_at, received[1],
            "The retried command ran after the move but must act as of the request's original time of receipt, "
            "not as of the moved clock.")

        second = command("second")
        service.found_living_organism(second)
        later = intake(second)
        self.assertGreaterEqual(
            later[1], received[1] + MOVED,
            "A new request sent after the clock moved must be received at the moved time. Its time of receipt is "
            "earlier than the move allows, so the intake did not read the INTEVIA Clock.")
        self.assertLessEqual(later[1], support.database_now())
        self.assertNotEqual(later[2], received[2])

    # -------------------------------------------------------------------- mail

    @override_settings(
        INTEVIA_TEST_CONTEXT=True,
        EMAIL_BACKEND="email_foundation.backend.CaptureBackend",
        INTEVIA_EMAIL_TEMPLATES=("tests.ema_support.NOTICE", "tests.ema_support.ESSENTIAL"),
    )
    def test_a_lease_ends_for_the_worker_and_for_the_operators_count_at_one_instant(self):
        """The worker's lease, the reaper and the operator's count of stuck mail read one clock: all three turn at the lease's end."""
        person = support.create_test_identity(
            username="clock-mail-" + uuid4().hex, access_state=models.Identity.AccessState.ACTIVE)
        AddressRecord.objects.create(
            identity=person, verified_address="recipient@example.invalid", verified_at=support.database_now())
        marker, organisation = uuid4().hex, uuid4()
        act = models.OrganismCommandReceipt.objects.create(
            actor=person, action="CLOCK_READER_FIXTURE", idempotency_key=marker,
            request_reference="clock-reader-fixture:" + marker, payload_fingerprint=digest(marker),
            authority_decision_reference="s015d1:" + digest(marker), lineage_reference="s015l1:" + digest(marker),
            result_payload={"organisation": str(organisation), "recipient": person.pk, "outcome": "UNCHANGED_TEST_ACT"})
        with transaction.atomic():
            request_message(
                category="NOTICE", recipient=person, template="fixture.notice", version=1,
                context={"record_id": str(act.receipt_uuid)}, origin=Origin(act.lineage_reference, organisation),
                request_key=str(act.receipt_uuid))

        def operators_count():
            out = io.StringIO()
            try:
                call_command("email_outbox", "check", stdout=out)
            except CommandError:
                pass
            line = out.getvalue().strip()
            prefix, suffix = "Email intents requiring operator attention: ", ". No message content printed."
            self.assertTrue(line.startswith(prefix) and line.endswith(suffix), line)
            return int(line[len(prefix):-len(suffix)])

        claimed_at_or_after = support.database_now()
        self.assertIsNotNone(claim(), "The committed notice must be claimable.")
        row = Outbox.objects.get()
        lease_end = row.lease_until
        length = lease_end - claimed_at_or_after
        self.assertGreater(length, SHORT)
        self.assertEqual((operators_count(), recover()), (0, 0), "A lease just given is not stuck and is not reaped.")

        owned_route.advance_to(lease_end - SHORT)
        self.assertEqual(
            (operators_count(), recover()), (0, 0),
            "Just short of the lease's end, neither the operator's count nor the reaper may treat the lease as ended.")
        self.assertLess(support.database_now(), lease_end, "The check just short of the lease's end ran too late to count.")

        owned_route.advance_to(lease_end)
        self.assertGreaterEqual(support.database_now(), lease_end)
        counted = operators_count()
        reaped = recover()
        self.assertEqual(
            (counted, reaped), (1, 1),
            "The clock was moved to the end of a mail worker's lease. The operator's count of stuck mail and the "
            "reaper must both see the lease as ended at that instant (expected one each). A difference means one of "
            "them does not read the INTEVIA Clock.")

        before = support.database_now()
        self.assertIsNotNone(claim(), "A reaped claim must be claimable again.")
        again = Outbox.objects.get().lease_until
        self.assertGreaterEqual(again, lease_end, "A lease given after the move must run from the moved time.")
        self.assertGreater(again, before)

    # ----------------------------------------------------------------- session

    @override_settings(ROOT_URLCONF="tests.test_intevia_clock_readers", INTEVIA_TIMED_EVENTS_WAKE=True)
    def test_a_session_older_than_the_move_is_signed_out_and_wakes_nothing_until_it_signs_in_again(self):
        """After a move of the session's whole age limit, the old session is turned away and wakes no engine; a fresh sign-in wakes it."""
        lo, founder = law.ordinary_birth()
        page = "/clock-readers-test/lo/%s/" % lo.organism_id

        def probes():
            with connection.cursor() as cursor:
                cursor.execute("SELECT coalesce(sum(p.probes),0)::bigint FROM public.core_tepace p WHERE p.organism_id=%s", [lo.pk])
                return cursor.fetchone()[0]

        def sign_in():
            client = Client()
            response = client.post("/login/", {"username": founder.credential.get_username(), "password": TEST_PASSWORD})
            self.assertEqual(response.status_code, 302, "The fixture's sign-in must succeed.")
            return client

        client = sign_in()
        self.assertEqual(probes(), 0)
        self.assertEqual(client.get(page).json(), {"answered": True})
        woken = probes()
        self.assertEqual(woken, 1, "A signed-in, confirmed request in this LO must wake the engine once.")

        owned_route.advance(timedelta(seconds=SESSION_ABSOLUTE_AGE_SECONDS))

        turned_away = client.get(page)
        self.assertEqual(
            (turned_away.status_code, turned_away.headers.get("Location")), (302, "/login/"),
            "The clock was moved by the session's whole age limit. A session started before the move must be turned "
            "away to sign in again; it was served instead, so the session's age was not read from the INTEVIA Clock.")
        self.assertEqual(probes(), woken, "A request turned away by the session's age must not wake the engine.")

        fresh = sign_in()
        self.assertEqual(fresh.get(page).json(), {"answered": True})
        self.assertEqual(probes(), woken + 1, "After signing in again, a confirmed request must wake the engine.")
