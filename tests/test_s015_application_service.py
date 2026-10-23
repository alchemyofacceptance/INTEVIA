"""S3 round 2a: applicant-only commands on the disposable PostgreSQL route."""

import threading
import time
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from queue import Queue
from threading import Barrier, Event
from unittest import skipUnless
from uuid import UUID, uuid4

from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase

from core.models import (
    Application, ApplicationLineage, ApplicationLineageEvent, ApplicationWithdrawal, AuthorityDecision,
    CommandIntake, Identity, IdentityTransition,
    OrganismCommandReceipt, OrganismMembership,
)
from core.organism_contract import OrganismAction
from tests.s015_0021_support import T0, append_event, empty_fp, fresh, refresh, seed_identity, u
from tests.sb2_route_support import birth, prepare_route, route_fixture_state, route_test_now
from src.intevia.services.organism_application_service import (
    OrganismApplicationService, S015ApplicationRefused, SubmitApplicationCommand,
    WithdrawApplicationCommand,
)
from src.intevia.services.organism_authority import (
    S015Authority, S015AuthorityDenied, S015AuthorityRefusalCode,
    S015OwnApplicantAuthorityRefusal, S015OwnApplicantAuthorityResponse,
)
from src.intevia.services.organism_command_support import S015CommandReplayMismatch
from src.intevia.services.organism_membership_service import S015OperationNotYetImplemented


NOW = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)

_support_append_event = append_event
_support_refresh = refresh


def append_event(*args, **kwargs):
    kwargs.setdefault("rec", NOW)
    return _support_append_event(*args, **kwargs)


def refresh(cur, table, aggregate_id, state_at=None):
    return _support_refresh(
        cur, table, aggregate_id, NOW if state_at is None else state_at
    )
POSTGRESQL_ONLY = skipUnless(connection.vendor == "postgresql", "S3 applicant commands require PostgreSQL")


class _ApplicantProvider:
    """TEST-ONLY provider substitute; production tests use ProductionApplicationAuthorityProvider."""

    def __init__(self, *, denied=False):
        self.denied = denied
        self.requests = []

    def evaluate_organism_authority(self, request):
        self.requests.append(request)
        if self.denied:
            return S015OwnApplicantAuthorityRefusal(
                **asdict(request), refusal_code=S015AuthorityRefusalCode.DENIED,
            )
        return S015OwnApplicantAuthorityResponse(**asdict(request), authority_reference="s3:test:own-applicant")


@POSTGRESQL_ONLY
class S015ApplicantCommandTests(TransactionTestCase):
    def setUp(self):
        self.tag = f"application-{fresh()}"
        self.route = prepare_route(tag=self.tag)
        birth(self.route)
        self.ids, self.gateway = route_fixture_state(self.route)
        self.route_times = self.ids["recorded_times"]
        global T0, NOW
        T0 = self.route_times.founding_recorded_at
        NOW = route_test_now(self.route)
        with transaction.atomic(), connection.cursor() as cursor:
            self.applicant_pk = seed_identity(cursor, f"app-{self.tag}")
        self.organism_uuid = self.ids["organism_uuid"]
        self.applicant = Identity.objects.select_related("credential").get(pk=self.applicant_pk)
        self.provider = _ApplicantProvider()

    def service(self, provider=None):
        return OrganismApplicationService(
            authority=S015Authority(provider=provider or self.provider), clock=lambda: NOW,
        )

    def history(self, *, provision=True, active=True, identity=None, activation_at=None):
        subject = identity or self.applicant
        prior = None
        if provision:
            prior = IdentityTransition.objects.create(
                identity=subject, action=IdentityTransition.Action.PROVISION,
                prior_state=None, resulting_state="pending", authority_reference="s3:test:provision",
                technical_executor="S3_TEST", evidence_reference="s3:test:history",
                reason_category=IdentityTransition.ReasonCategory.PROVISIONING,
                correlation_id=uuid4(), occurred_at=T0,
            )
        IdentityTransition.objects.create(
            identity=subject, action=IdentityTransition.Action.ACTIVATE if active else IdentityTransition.Action.RESTRICT,
            prior_state="pending" if provision else "active", resulting_state="active" if active else "restricted",
            authority_reference="s3:test:access", technical_executor="S3_TEST",
            evidence_reference="s3:test:history", reason_category=IdentityTransition.ReasonCategory.ACCESS_REVIEW,
            correlation_id=uuid4(), occurred_at=activation_at or NOW, previous_transition=prior,
        )

    def submit(self, *, actor=None, organism_uuid=None, predecessor=None, request=None, key=None, effective_at=None):
        effective_at = NOW if effective_at is None else effective_at
        return SubmitApplicationCommand(
            credential=(actor or self.applicant).credential,
            living_organism_id=organism_uuid or self.organism_uuid,
            request_reference=request or f"s3:submit:{self.tag}", idempotency_key=key or f"s3:key:{self.tag}",
            occurred_at=effective_at, effective_at=effective_at,
            predecessor_reference=predecessor,
            answer="Test-only applicant answer",
        )

    def withdraw(self, application, *, actor=None, request=None, key=None):
        return WithdrawApplicationCommand(
            credential=(actor or self.applicant).credential, application_uuid=application.application_uuid,
            request_reference=request or f"s3:withdraw:{self.tag}", idempotency_key=key or f"s3:withdraw:key:{self.tag}",
            occurred_at=NOW, effective_at=NOW,
        )

    def submitted(self):
        self.history()
        result = self.service().submit_application(self.submit())
        return result, Application.objects.get(application_uuid=result.application_uuid)

    def test_pending_and_withdrawn_applications_leave_chain(self):
        submission, application = self.submitted()
        lineage = ApplicationLineage.objects.get(application=application)
        first = lineage.head_event
        self.assertEqual((first.sequence, first.action, first.resulting_state, first.actor_id),
                         (1, "SUBMIT_APPLICATION", "SUBMITTED", self.applicant.pk))
        self.assertEqual(first.authority_decision_reference, submission.authority_decision_reference)
        self.assertEqual(first.evidence_reference, f"application:{application.application_uuid}")
        with connection.cursor() as cursor:
            cursor.execute("SELECT public.s015_fold_effective_state(%s, %s, %s, clock_timestamp())",
                           ["core_applicationlineage", lineage.pk, NOW])
            self.assertEqual(cursor.fetchone()[0], "SUBMITTED")
        withdrawal = self.service().withdraw_application(self.withdraw(application))
        lineage.refresh_from_db()
        last = lineage.head_event
        self.assertEqual((last.sequence, last.action, last.prior_state, last.resulting_state,
                          last.predecessor_id, last.actor_id),
                         (2, "WITHDRAW_APPLICATION", "SUBMITTED", "WITHDRAWN", first.pk, self.applicant.pk))
        self.assertEqual(last.evidence_reference, f"application-withdrawal:{application.application_uuid}")
        self.assertEqual(last.authority_decision_reference, withdrawal.authority_decision_reference)
        self.assertEqual(ApplicationLineageEvent.objects.filter(application_lineage=lineage).count(), 2)
        with connection.cursor() as cursor:
            cursor.execute("SELECT public.s015_fold_effective_state(%s, %s, %s, clock_timestamp())",
                           ["core_applicationlineage", lineage.pk, NOW])
            self.assertEqual(cursor.fetchone()[0], "WITHDRAWN")

    def membership(self, state):
        with transaction.atomic(), connection.cursor() as cursor:
            membership_uuid = u(f"member:app:{self.tag}")
            cursor.execute(
                "INSERT INTO core_organismmembership (membership_uuid, state, created_at, current_state_effective_at, "
                "state_known_at, state_source_event_set_fingerprint, identity_id, living_organism_id) "
                "VALUES (%s, %s, now(), %s, %s, %s, %s, %s) RETURNING id",
                (membership_uuid, state, T0, NOW, empty_fp(cursor, "core_organismmembership", membership_uuid),
                 self.applicant.pk, self.ids["lo"]),
            )
            membership_id = cursor.fetchone()[0]
            append_event(cursor, "core_organismmembershiptransition", membership_id, 1, "JOIN", None, state,
                         self.applicant.pk, self.ids["basis"], f"member-{self.tag}",
                         eff=T0, occ=T0, rec=NOW, extra={"reason_class": "TEST"})
            refresh(cursor, "core_organismmembership", membership_id, T0)
        return OrganismMembership.objects.get(pk=membership_id)

    def test_submit_writes_applicant_record_and_receipt(self):
        result, application = self.submitted()
        self.assertEqual(result.action, OrganismAction.SUBMIT_APPLICATION)
        self.assertEqual((application.applicant_id, application.actor_id, application.enterer_id),
                         (self.applicant.pk,) * 3)
        self.assertEqual(result.recorded_at, application.recorded_at)
        payload = OrganismCommandReceipt.objects.get(actor=self.applicant).result_payload
        self.assertNotIn("application_id", payload)
        self.assertEqual(payload["application_uuid"], str(application.application_uuid))
        self.assertIsNone(self.provider.requests[0].application_pk)

    def test_denial_precedes_missing_provision_and_membership_state(self):
        self.membership("ACTIVE")
        with self.assertRaises(S015AuthorityDenied):
            self.service(_ApplicantProvider(denied=True)).submit_application(self.submit())
        self.assertFalse(Application.objects.exists())
        self.assertFalse(OrganismCommandReceipt.objects.filter(actor=self.applicant).exists())

    def test_missing_provision_refuses_without_governed_writes(self):
        self.history(provision=False)
        command = self.submit()
        intake_count = CommandIntake.objects.count()
        with self.assertRaises(S015ApplicationRefused):
            self.service().submit_application(command)
        self.assertFalse(Application.objects.exists())
        self.assertEqual(CommandIntake.objects.count(), intake_count + 1)
        self.assertEqual(
            CommandIntake.objects.filter(idempotency_key=command.idempotency_key).count(),
            1,
        )

    def test_account_not_active_at_record_time_refused(self):
        self.history(active=False)
        with self.assertRaises(S015ApplicationRefused):
            self.service().submit_application(self.submit())
        self.assertFalse(OrganismCommandReceipt.objects.filter(actor=self.applicant).exists())

    def test_clock_before_activation_does_not_refuse_eligible_submission(self):
        activation_at = self.route_times.latest_recorded_at
        self.history(activation_at=activation_at)
        service = OrganismApplicationService(
            authority=S015Authority(provider=self.provider),
            clock=lambda: activation_at - timedelta(minutes=30),
        )
        result = service.submit_application(self.submit())
        application = Application.objects.get(application_uuid=result.application_uuid)
        self.assertEqual(result.recorded_at, application.recorded_at)
        self.assertEqual(application.applicant_id, self.applicant.pk)

    def test_current_member_is_refused(self):
        self.history()
        self.membership("ACTIVE")
        with self.assertRaises(S015ApplicationRefused):
            self.service().submit_application(self.submit())

    def test_probationary_member_is_refused(self):
        self.history()
        self.membership("PROBATIONARY")
        with self.assertRaises(S015ApplicationRefused):
            self.service().submit_application(self.submit())

    def test_ended_member_reports_il_1(self):
        self.history()
        self.membership("ENDED")
        with self.assertRaises(S015OperationNotYetImplemented) as error:
            self.service().submit_application(self.submit())
        self.assertEqual(error.exception.limit_id, "IL-1")

    def test_proposed_member_may_apply(self):
        self.history()
        self.membership("PROPOSED")
        self.assertIsInstance(self.service().submit_application(self.submit()).application_uuid, UUID)

    def test_one_open_application_refuses_sequential_submission(self):
        first, _ = self.submitted()
        with self.assertRaises(S015ApplicationRefused):
            self.service().submit_application(self.submit(request="s3:submit:again", key="s3:key:again"))
        self.assertEqual(
            list(Application.objects.values_list("application_uuid", flat=True)),
            [first.application_uuid],
        )

    def test_one_open_application_serialises_two_connections(self):
        self.history()
        barrier = Barrier(2)
        outcomes: Queue[object] = Queue()

        def worker(key):
            close_old_connections()
            try:
                actor = Identity.objects.select_related("credential").get(pk=self.applicant.pk)
                barrier.wait(timeout=10)
                result = self.service(_ApplicantProvider()).submit_application(
                    self.submit(actor=actor, request=f"s3:concurrent:{key}", key=f"s3:concurrent:{key}"),
                )
                outcomes.put(result)
            except Exception as exc:
                outcomes.put(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker, args=(index,), daemon=True) for index in (1, 2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
            self.assertFalse(thread.is_alive())
        observed = [outcomes.get_nowait(), outcomes.get_nowait()]
        self.assertEqual(sum(type(result) is S015ApplicationRefused for result in observed), 1)
        self.assertEqual(sum(not isinstance(result, Exception) for result in observed), 1)
        self.assertEqual(Application.objects.count(), 1)

    def test_submission_waits_for_owning_organism_before_application_insert(self):
        self.history()
        started = Event()
        backend: Queue[int] = Queue()
        outcomes: Queue[object] = Queue()

        def worker():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    backend.put(cursor.fetchone()[0])
                started.set()
                outcomes.put(self.service(_ApplicantProvider()).submit_application(self.submit()))
            except Exception as exc:
                outcomes.put(exc)
            finally:
                connection.close()

        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SELECT public.s015_lock_organisms(ARRAY[%s]::uuid[])", [self.organism_uuid])
            cursor.execute("SELECT pg_backend_pid()")
            holder_pid = cursor.fetchone()[0]
            thread = threading.Thread(target=worker, daemon=True)
            thread.start()
            self.assertTrue(started.wait(timeout=10))
            worker_pid = backend.get(timeout=10)
            deadline = time.monotonic() + 10
            blocked = False
            while time.monotonic() < deadline:
                cursor.execute("SELECT %s = ANY(pg_blocking_pids(%s))", [holder_pid, worker_pid])
                if cursor.fetchone()[0]:
                    blocked = True
                    break
                Event().wait(timeout=0.01)
            self.assertTrue(blocked, "submission should wait at the owning-organism lock")
            self.assertFalse(Application.objects.filter(applicant=self.applicant).exists())
        thread.join(timeout=30)
        self.assertFalse(thread.is_alive())
        outcome = outcomes.get_nowait()
        self.assertNotIsInstance(outcome, Exception)
        self.assertEqual(Application.objects.filter(applicant=self.applicant).count(), 1)

    def test_withdrawal_by_another_actor_fails_at_authority(self):
        _, application = self.submitted()
        with transaction.atomic(), connection.cursor() as cursor:
            other_pk = seed_identity(cursor, f"other-{self.tag}")
        other = Identity.objects.select_related("credential").get(pk=other_pk)
        with self.assertRaises(S015AuthorityDenied):
            self.service().withdraw_application(self.withdraw(application, actor=other))
        self.assertFalse(ApplicationWithdrawal.objects.exists())
        self.assertFalse(OrganismCommandReceipt.objects.filter(action=OrganismAction.WITHDRAW_APPLICATION.value).exists())

    def test_applicant_may_withdraw_waiting_application(self):
        submission, application = self.submitted()
        result = self.service().withdraw_application(self.withdraw(application))
        self.assertEqual(result.action, OrganismAction.WITHDRAW_APPLICATION)
        withdrawal = ApplicationWithdrawal.objects.get(application=application)
        self.assertEqual((withdrawal.actor_id, withdrawal.enterer_id), (self.applicant.pk, self.applicant.pk))
        self.assertEqual(result.recorded_at, withdrawal.recorded_at)
        self.assertEqual((application.authority_decision_reference, application.authority_reference,
                  application.actor_access_epoch),
                 (submission.authority_decision_reference, submission.authority_reference,
                  self.applicant.access_epoch))
        self.assertEqual((withdrawal.authority_decision_reference, withdrawal.authority_reference,
                  withdrawal.actor_access_epoch),
                 (result.authority_decision_reference, result.authority_reference,
                  self.applicant.access_epoch))
        self.assertNotEqual(withdrawal.authority_decision_reference, application.authority_decision_reference)

    def test_reapplication_after_eligible_withdrawal(self):
        _, previous = self.submitted()
        self.service().withdraw_application(self.withdraw(previous))
        result = self.service().submit_application(self.submit(
            predecessor=previous.application_uuid, request="s3:reapply", key="s3:reapply",
        ))
        self.assertEqual(
            Application.objects.get(application_uuid=result.application_uuid).predecessor_reference_id,
            previous.pk,
        )

    def test_predecessor_not_withdrawn_refused(self):
        self.history()
        previous = Application.objects.create(
            applicant=self.applicant, living_organism_id=self.ids["lo"], actor=self.applicant, enterer=self.applicant,
            settings_version_id=self.gateway["core_loapplicationsettingsversion"],
            question_version_id=self.gateway["core_loapplicationquestionversion"], answer="Test-only applicant answer",
            authority_decision_reference="s015d1:" + "a" * 64,
            authority_reference="s3:test:fixture", actor_access_epoch=self.applicant.access_epoch,
            occurred_at=T0, effective_at=T0, received_at=T0,
        )
        with self.assertRaises(S015ApplicationRefused):
            self.service().submit_application(self.submit(predecessor=previous.application_uuid))

    def test_predecessor_of_another_applicant_refused(self):
        self.history()
        with transaction.atomic(), connection.cursor() as cursor:
            other_pk = seed_identity(cursor, f"other-{self.tag}")
        other = Identity.objects.get(pk=other_pk)
        previous = Application.objects.create(
            applicant=other, living_organism_id=self.ids["lo"], actor=other, enterer=other,
            settings_version_id=self.gateway["core_loapplicationsettingsversion"],
            question_version_id=self.gateway["core_loapplicationquestionversion"], answer="Test-only applicant answer",
            authority_decision_reference="s015d1:" + "a" * 64,
            authority_reference="s3:test:fixture", actor_access_epoch=other.access_epoch,
            occurred_at=T0, effective_at=T0, received_at=T0,
        )
        with self.assertRaises(S015ApplicationRefused):
            self.service().submit_application(self.submit(predecessor=previous.application_uuid))

    def test_predecessor_of_another_organism_refused(self):
        self.history()
        other = prepare_route(tag=f"other-lo-{self.tag}")
        other_ids, other_gateway = route_fixture_state(other)
        other_at = route_test_now(other)
        previous = Application.objects.create(
            applicant=self.applicant, living_organism_id=other_ids["lo"], actor=self.applicant, enterer=self.applicant,
            settings_version_id=other_gateway["core_loapplicationsettingsversion"],
            question_version_id=other_gateway["core_loapplicationquestionversion"], answer="Test-only applicant answer",
            authority_decision_reference="s015d1:" + "a" * 64,
            authority_reference="s3:test:fixture", actor_access_epoch=self.applicant.access_epoch,
            occurred_at=other_at, effective_at=other_at, received_at=other_at,
        )
        with self.assertRaises(S015ApplicationRefused):
            self.service().submit_application(self.submit(predecessor=previous.application_uuid))

    def test_replay_returns_original_and_changed_request_mismatches(self):
        self.history()
        command = self.submit()
        before = (
            Application.objects.count(),
            ApplicationLineageEvent.objects.count(),
            OrganismCommandReceipt.objects.count(),
            AuthorityDecision.objects.count(),
        )
        first = self.service().submit_application(command)
        application = Application.objects.get(application_uuid=first.application_uuid)
        receipt = OrganismCommandReceipt.objects.get(idempotency_key=command.idempotency_key)
        decision = AuthorityDecision.objects.get(decision_reference=first.authority_decision_reference)
        self.assertEqual(receipt.actor_id, self.applicant.pk)
        self.assertEqual(receipt.authority_decision_reference, first.authority_decision_reference)
        self.assertEqual(decision.outcome, "QUALIFIED")
        self.assertEqual(
            ApplicationLineageEvent.objects.filter(application_lineage__application=application).count(),
            1,
        )
        after_first = (
            Application.objects.count(),
            ApplicationLineageEvent.objects.count(),
            OrganismCommandReceipt.objects.count(),
            AuthorityDecision.objects.count(),
        )
        self.assertEqual(after_first, tuple(count + 1 for count in before))
        self.assertEqual(self.service().submit_application(command), first)
        with self.assertRaises(S015CommandReplayMismatch):
            self.service().submit_application(replace(command, request_reference="s3:changed"))
        self.assertEqual(
            (
                Application.objects.count(),
                ApplicationLineageEvent.objects.count(),
                OrganismCommandReceipt.objects.count(),
                AuthorityDecision.objects.count(),
            ),
            after_first,
        )

    def test_withdrawal_replay_returns_original(self):
        _, application = self.submitted()
        first = self.service().withdraw_application(self.withdraw(application))
        self.assertEqual(self.service().withdraw_application(self.withdraw(application)), first)
        self.assertEqual(ApplicationWithdrawal.objects.count(), 1)

    def test_withdrawal_after_a_recorded_decision_refused(self):
        _, application = self.submitted()
        with transaction.atomic(), connection.cursor() as cursor:
            claim_uuid = str(uuid4())
            cursor.execute(
                "INSERT INTO core_csaassignment (assignment_uuid, application_id, assignee_membership_id, assignee_office_id, "
                "actor_id, enterer_id, completion_due_at, occurred_at, effective_at, received_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (claim_uuid, application.pk, self.ids["membership"], self.ids["assignments"]["COORDINATOR"],
                 self.ids["identity"], self.ids["identity"], NOW + timedelta(days=27), NOW, NOW, NOW),
            )
            claim_id = cursor.fetchone()[0]
            cursor.execute(
                "INSERT INTO core_applicationengagement (application_id, engagement_ordinal, route, deciding_party_id, "
                "engagement_reference, settings_basis, actor_id, enterer_id, occurred_at, effective_at, received_at) "
                "VALUES (%s, 1, 'LO_CSA', %s, %s, %s, %s, %s, %s, %s, %s)",
                (application.pk, self.ids["lo"], claim_uuid, self.gateway["settings_basis"],
                 self.ids["identity"], self.ids["identity"], NOW, NOW, NOW),
            )
            cursor.execute(
                "INSERT INTO core_applicationdecision (decision_uuid, application_id, route, deciding_party_id, outcome, "
                "assignment_reference_id, responsible_coordinator_id, responsible_capacity, qualifying_office_id, "
                "authority_decision_reference, executor_id, enterer_id, occurred_at, effective_at, received_at) "
                "VALUES (%s, %s, 'LO_CSA', %s, 'DECLINE', %s, %s, 'COORDINATOR', %s, %s, %s, %s, %s, %s, %s)",
                (str(uuid4()), application.pk, self.ids["lo"], claim_id, self.ids["identity"],
                 self.ids["assignments"]["COORDINATOR"], "s015d1:" + "a" * 64,
                 self.ids["identity"], self.ids["identity"], NOW, NOW, NOW),
            )
        with self.assertRaises(S015ApplicationRefused):
            self.service().withdraw_application(self.withdraw(application))
        self.assertFalse(ApplicationWithdrawal.objects.exists())