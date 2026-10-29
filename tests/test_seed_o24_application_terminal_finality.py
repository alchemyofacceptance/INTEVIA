"""O-24 application terminal finality; re-application remains unqualified by this slice.

The Coordinator has no identity history, so its visibility of decision results
(an audience check) is not qualified by this slice; the tests reload stored
rows directly.
"""

import hashlib
import threading
import time
import uuid
from dataclasses import asdict
from datetime import timedelta
from queue import Empty, Queue
from threading import Barrier
from unittest import skipUnless

from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, connection, connections, transaction
from django.test import TransactionTestCase

from core.models import (
    Application,
    ApplicationDecision,
    ApplicationEngagement,
    ApplicationWithdrawal,
    CSAAssignment,
    Identity,
    IdentityTransition,
    LivingOrganism,
)
from src.intevia.services.organism_application_service import (
    ClaimApplicationCommand,
    DecideApplicationCommand,
    OrganismApplicationService,
    SubmitApplicationCommand,
    WithdrawApplicationCommand,
)
from src.intevia.services.organism_authority import (
    S015Authority,
    S015OwnApplicantAuthorityRequest,
    S015OwnApplicantAuthorityResponse,
    S015PostFoundingAuthorityResponse,
)
from tests.s015_0021_support import (
    database_now,
    found_nonroot,
    seed_identity,
    seed_test_gateway,
)


POSTGRESQL_ONLY = skipUnless(
    connection.vendor == "postgresql",
    "O-24 terminal-finality checks require PostgreSQL",
)


class _O24AuthorityProvider:
    def evaluate_organism_authority(self, request):
        fields = asdict(request)
        if isinstance(request, S015OwnApplicantAuthorityRequest):
            return S015OwnApplicantAuthorityResponse(
                **fields,
                authority_reference="o24:test:applicant",
            )
        return S015PostFoundingAuthorityResponse(
            **fields,
            authority_reference="o24:test:coordinator",
        )


@POSTGRESQL_ONLY
class ApplicationTerminalFinalityTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        super().setUp()
        self.tag = uuid.uuid4().hex
        with transaction.atomic(), connection.cursor() as cursor:
            self.ids = found_nonroot(cursor, tag=f"o24-{self.tag}")
            seed_test_gateway(
                cursor,
                self.ids,
                include_governance_configuration=True,
            )
            applicant_id = seed_identity(cursor, f"o24-applicant-{self.tag}")

        self.organism = LivingOrganism.objects.get(pk=self.ids["lo"])
        self.coordinator = Identity.objects.get(pk=self.ids["identity"])
        self.applicant = Identity.objects.get(pk=applicant_id)
        with transaction.atomic():
            provision = IdentityTransition.objects.create(
                identity=self.applicant,
                action=IdentityTransition.Action.PROVISION,
                prior_state=None,
                resulting_state=Identity.AccessState.PENDING,
                authority_reference="o24:test:provision",
                technical_executor="O24_TEST",
                evidence_reference="o24:test:history",
                reason_category=IdentityTransition.ReasonCategory.PROVISIONING,
                correlation_id=uuid.uuid4(),
                occurred_at=database_now(),
            )
            IdentityTransition.objects.create(
                identity=self.applicant,
                action=IdentityTransition.Action.ACTIVATE,
                prior_state=Identity.AccessState.PENDING,
                resulting_state=Identity.AccessState.ACTIVE,
                authority_reference="o24:test:activate",
                technical_executor="O24_TEST",
                evidence_reference="o24:test:history",
                reason_category=IdentityTransition.ReasonCategory.GOVERNED_ACTIVATION,
                correlation_id=uuid.uuid4(),
                occurred_at=database_now(),
                previous_transition=provision,
            )
        self.service = OrganismApplicationService(
            authority=S015Authority(provider=_O24AuthorityProvider()),
            clock=database_now,
        )

    def _keys(self, action):
        token = uuid.uuid4().hex
        return f"o24:{action}:{token}", f"o24:{action}:key:{token}"

    def _authority_decision_reference(self, action):
        digest = hashlib.sha256(
            f"{self.tag}:{action}:{uuid.uuid4()}".encode("utf-8")
        ).hexdigest()
        return "s015d1:" + digest

    def _new_application(self):
        now = database_now()
        request_reference, idempotency_key = self._keys("submit")
        result = self.service.submit_application(SubmitApplicationCommand(
            credential=self.applicant.credential,
            living_organism_id=self.organism.organism_id,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            occurred_at=now,
            effective_at=now,
            answer="O-24 test application",
        ))
        return Application.objects.get(application_uuid=result.application_uuid)

    def _claim(self, application):
        now = database_now()
        request_reference, idempotency_key = self._keys("claim")
        self.service.claim_application(ClaimApplicationCommand(
            credential=self.coordinator.credential,
            application_uuid=application.application_uuid,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            occurred_at=now,
            effective_at=now,
        ))
        return CSAAssignment.objects.get(application=application)

    def _decide(self, application):
        now = database_now()
        request_reference, idempotency_key = self._keys("decide")
        self.service.decide_application(DecideApplicationCommand(
            credential=self.coordinator.credential,
            application_uuid=application.application_uuid,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            occurred_at=now,
            effective_at=now,
            outcome="DECLINE",
        ))
        return ApplicationDecision.objects.get(application=application)

    def _withdraw(self, application):
        now = database_now()
        request_reference, idempotency_key = self._keys("withdraw")
        self.service.withdraw_application(WithdrawApplicationCommand(
            credential=self.applicant.credential,
            application_uuid=application.application_uuid,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            occurred_at=now,
            effective_at=now,
        ))
        return ApplicationWithdrawal.objects.get(application=application)

    def _decision_values(self, application, claim, effective_at, *, reference=None, authority=None):
        return {
            "application_id": application.pk,
            "route": "LO_CSA",
            "deciding_party_id": application.living_organism_id,
            "outcome": "DECLINE",
            "assignment_reference_id": claim.pk,
            "responsible_coordinator_id": self.coordinator.pk,
            "responsible_capacity": "COORDINATOR",
            "qualifying_office_id": claim.assignee_office_id,
            "authority_decision_reference": reference or self._authority_decision_reference(
                "decision"
            ),
            "authority_reference": authority or "o24:test:coordinator",
            "executor_id": self.coordinator.pk,
            "enterer_id": self.coordinator.pk,
            "occurred_at": effective_at,
            "effective_at": effective_at,
            "received_at": max(effective_at, database_now()),
        }

    def _withdrawal_values(self, application, effective_at, reference, authority):
        return {
            "application_id": application.pk,
            "actor_id": self.applicant.pk,
            "enterer_id": self.applicant.pk,
            "authority_decision_reference": reference,
            "authority_reference": authority,
            "actor_access_epoch": self.applicant.access_epoch,
            "occurred_at": effective_at,
            "effective_at": effective_at,
            "received_at": max(effective_at, database_now()),
        }

    @staticmethod
    def _integrity_refusal_message(error):
        cause = error.__cause__
        sqlstate = getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)
        if sqlstate != "23000":
            return None
        diagnostic = getattr(cause, "diag", None)
        return getattr(diagnostic, "message_primary", None)

    def _attempt_guarded_write(self, write, expected_message):
        with transaction.atomic():
            try:
                write()
            except IntegrityError as exc:
                message = self._integrity_refusal_message(exc)
                if message not in {
                    expected_message,
                    "VERIFICATION MUTATION: an unrelated refusal",
                }:
                    raise
                return message
            transaction.set_rollback(True)
            return "admitted"

    def _assert_counts(self, application, *, decisions, withdrawals):
        self.assertEqual(
            ApplicationDecision.objects.filter(application=application).count(),
            decisions,
        )
        self.assertEqual(
            ApplicationWithdrawal.objects.filter(application=application).count(),
            withdrawals,
        )

    def test_decision_then_withdrawal_refuses_at_earlier_and_later_effective_times(self):
        """A committed decision excludes later withdrawals regardless of effective time."""
        application = self._new_application()
        self._claim(application)
        decision = self._decide(application)
        observed = []
        effective_times = (
            decision.effective_at - timedelta(days=1),
            decision.effective_at + timedelta(days=1),
        )
        for attempt, effective_at in enumerate(effective_times):
            values = self._withdrawal_values(
                application,
                effective_at,
                self._authority_decision_reference(f"conflicting-withdrawal-{attempt}"),
                "o24:test:applicant",
            )
            observed.append(
                self._attempt_guarded_write(
                    lambda: ApplicationWithdrawal.objects.create(**values),
                    "S3 terminal exclusion",
                )
            )

        expected = ["S3 terminal exclusion", "S3 terminal exclusion"]
        self._assert_counts(application, decisions=1, withdrawals=0)
        self.assertEqual(
            ApplicationDecision.objects.get(application=application).pk,
            decision.pk,
        )
        self.assertEqual(
            observed,
            expected,
            f"expected refusal outcomes {expected!r}; observed {observed!r}",
        )

    def test_withdrawal_then_decision_and_engagement_refuse_independently(self):
        """A committed withdrawal excludes decisions and engagements regardless of effective time."""
        application = self._new_application()
        claim = self._claim(application)
        withdrawal = self._withdraw(application)
        engagement = ApplicationEngagement.objects.get(application=application)
        observed = []
        expected = []

        for effective_at in (
            withdrawal.effective_at - timedelta(days=1),
            withdrawal.effective_at + timedelta(days=1),
        ):
            values = self._decision_values(application, claim, effective_at)
            observed.append(
                self._attempt_guarded_write(
                    lambda: ApplicationDecision.objects.create(**values),
                    "S3 terminal exclusion",
                )
            )
            expected.append("S3 terminal exclusion")

            engagement_values = {
                "application_id": application.pk,
                "engagement_ordinal": 2,
                "route": "LO_CSA",
                "deciding_party_id": application.living_organism_id,
                "engagement_reference": engagement.engagement_reference,
                "settings_basis": engagement.settings_basis,
                "actor_id": self.coordinator.pk,
                "enterer_id": self.coordinator.pk,
                "occurred_at": effective_at,
                "effective_at": effective_at,
                "received_at": max(effective_at, database_now()),
            }
            observed.append(
                self._attempt_guarded_write(
                    lambda: ApplicationEngagement.objects.create(**engagement_values),
                    "S3 engagement refused",
                )
            )
            expected.append("S3 engagement refused")

        self._assert_counts(application, decisions=0, withdrawals=1)
        self.assertEqual(
            ApplicationWithdrawal.objects.get(application=application).pk,
            withdrawal.pk,
        )
        self.assertEqual(
            observed,
            expected,
            f"expected refusal outcomes {expected!r}; observed {observed!r}",
        )

    def test_second_decision_is_refused_by_application_uniqueness(self):
        """A second decision is refused by the model's uniqueness check and by the application unique key."""
        application = self._new_application()
        claim = self._claim(application)
        first = self._decide(application)
        values = self._decision_values(
            application,
            claim,
            first.effective_at,
        )

        with self.assertRaises(ValidationError) as model_refusal:
            ApplicationDecision.objects.create(**values)
        self.assertIn("application", model_refusal.exception.message_dict)

        try:
            with transaction.atomic():
                ApplicationDecision.objects.bulk_create([ApplicationDecision(**values)])
        except IntegrityError as refused:
            cause = refused.__cause__
            sqlstate = getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)
            self.assertEqual(sqlstate, "23505", str(refused))
            constraint_name = getattr(getattr(cause, "diag", None), "constraint_name", None)
            prefix = (
                f"{ApplicationDecision._meta.db_table}_"
                f"{ApplicationDecision._meta.get_field('application').column}_"
            )
            self.assertIsNotNone(constraint_name, str(refused))
            self.assertTrue(constraint_name.startswith(prefix), str(refused))
        else:
            self.fail("second decision must be refused by the application uniqueness guard")

        self._assert_counts(application, decisions=1, withdrawals=0)
        self.assertEqual(ApplicationDecision.objects.get(application=application).pk, first.pk)

    def _wait_for_contenders_at_application_lock(self, blocker_pid, contender_pids):
        contender_set = set(contender_pids)
        deadline = time.monotonic() + 10
        while True:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT pid, wait_event_type, pg_blocking_pids(pid)
                      FROM pg_catalog.pg_stat_activity
                     WHERE pid = ANY(%s)
                    """,
                    [list(contender_set)],
                )
                activity = {
                    pid: (wait_event_type, set(blockers or ()))
                    for pid, wait_event_type, blockers in cursor.fetchall()
                }

            def reaches_blocker(pid, visited):
                if pid == blocker_pid:
                    return True
                if pid not in contender_set or pid in visited:
                    return False
                state = activity.get(pid)
                if state is None or state[0] != "Lock":
                    return False
                next_visited = visited | {pid}
                return any(
                    reaches_blocker(blocker, next_visited)
                    for blocker in state[1]
                )

            observed = {
                pid for pid in contender_set if reaches_blocker(pid, frozenset())
            }
            if observed == contender_set:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    "both terminal writers must share a wait-chain observation "
                    f"to the application-row lock; observed {sorted(observed)!r}"
                )
            time.sleep(min(0.05, remaining))

    def test_threaded_decision_and_withdrawal_leave_one_terminal_row(self):
        """Concurrent decision and withdrawal writes serialize to one terminal outcome and a guardian refusal."""
        application = self._new_application()
        claim = self._claim(application)
        effective_at = database_now()
        decision_values = self._decision_values(application, claim, effective_at)
        withdrawal_values = self._withdrawal_values(
            application,
            effective_at,
            self._authority_decision_reference("race-withdrawal"),
            "o24:test:applicant",
        )
        ready = Queue()
        outcomes = Queue()
        barrier = Barrier(3)
        started_workers = []
        contender_pids = {}

        def contender(kind, values):
            database = None
            backend_pid = None
            startup_error = None
            operation_result = None
            operation_error = None
            cleanup_error = None
            phase = "startup"
            try:
                close_old_connections()
                database = connections["default"]
                database.ensure_connection()
                with transaction.atomic(using="default"):
                    with database.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '15s'")
                        cursor.execute("SELECT pg_backend_pid()")
                        backend_pid = cursor.fetchone()[0]
                    ready.put((kind, backend_pid))
                    phase = "operation"
                    barrier.wait(timeout=10)
                    model = (
                        ApplicationDecision
                        if kind == "decision"
                        else ApplicationWithdrawal
                    )
                    model.objects.using("default").create(**values)
                operation_result = "committed"
            except Exception as exc:
                if phase == "startup":
                    startup_error = exc
                else:
                    operation_error = exc
            finally:
                if database is not None:
                    try:
                        database.close()
                    except Exception as exc:
                        cleanup_error = exc
                outcomes.put({
                    "kind": kind,
                    "backend_pid": backend_pid,
                    "startup_error": startup_error,
                    "operation_result": operation_result,
                    "operation_error": operation_error,
                    "connection_close_error": cleanup_error,
                })

        primary_error = None
        cleanup_errors = []
        try:
            with transaction.atomic():
                Application.objects.select_for_update().get(pk=application.pk)
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    blocker_pid = cursor.fetchone()[0]

                for kind, values in (
                    ("decision", decision_values),
                    ("withdrawal", withdrawal_values),
                ):
                    worker = threading.Thread(
                        target=contender,
                        args=(kind, values),
                        name=f"o24-{kind}-{self.tag}",
                    )
                    worker.start()
                    started_workers.append((kind, worker))

                for _ in started_workers:
                    kind, pid = ready.get(timeout=10)
                    contender_pids[kind] = pid
                if set(contender_pids) != {"decision", "withdrawal"}:
                    raise RuntimeError("both terminal writer connections must start")
                if len(set(contender_pids.values())) != 2:
                    raise RuntimeError("terminal writers must use separate connections")
                if blocker_pid in contender_pids.values():
                    raise RuntimeError("terminal writers must not reuse the blocker connection")
                barrier.wait(timeout=10)
                self._wait_for_contenders_at_application_lock(
                    blocker_pid,
                    list(contender_pids.values()),
                )
        except Exception as exc:
            primary_error = exc
        finally:
            try:
                barrier.abort()
            except Exception as exc:
                cleanup_errors.append(("barrier abort", exc))

            for _, worker in started_workers:
                worker.join(timeout=3)
            survivors = [
                (kind, worker)
                for kind, worker in started_workers
                if worker.is_alive()
            ]

            def signal_survivors(active_workers, action):
                for kind, _ in active_workers:
                    pid = contender_pids.get(kind)
                    if pid is None:
                        cleanup_errors.append((
                            f"pg_{action}_backend for {kind}",
                            RuntimeError("contender backend PID was not captured"),
                        ))
                        continue
                    try:
                        with connection.cursor() as cursor:
                            cursor.execute(
                                f"SELECT pg_{action}_backend(%s)",
                                [pid],
                            )
                            cursor.fetchone()
                    except Exception as exc:
                        cleanup_errors.append((
                            f"pg_{action}_backend for {kind}",
                            exc,
                        ))

            if survivors:
                signal_survivors(survivors, "cancel")
                for _, worker in survivors:
                    worker.join(timeout=3)
                survivors = [
                    (kind, worker)
                    for kind, worker in started_workers
                    if worker.is_alive()
                ]
            if survivors:
                signal_survivors(survivors, "terminate")
                for _, worker in survivors:
                    worker.join(timeout=3)
                survivors = [
                    (kind, worker)
                    for kind, worker in started_workers
                    if worker.is_alive()
                ]
            if survivors:
                cleanup_errors.append((
                    "bounded worker join",
                    TimeoutError(
                        "worker threads remain alive after backend cancellation "
                        f"and termination: {[kind for kind, _ in survivors]!r}"
                    ),
                ))

        worker_records = {}
        for _ in started_workers:
            try:
                record = outcomes.get(timeout=3)
                worker_records[record["kind"]] = record
            except Empty as exc:
                cleanup_errors.append(("worker final outcome", exc))

        committed = []
        refused = []
        worker_errors = []
        for kind, _ in started_workers:
            record = worker_records.get(kind)
            if record is None:
                continue
            if record["startup_error"] is not None:
                worker_errors.append((f"{kind} startup", record["startup_error"]))
            elif record["operation_error"] is not None:
                error = record["operation_error"]
                refusal_message = self._integrity_refusal_message(error)
                if isinstance(error, IntegrityError) and refusal_message in {
                    "S3 terminal exclusion",
                    "S3 withdrawal requires applicant and no decision",
                }:
                    refused.append((kind, refusal_message))
                else:
                    worker_errors.append((f"{kind} operation", error))
            elif record["operation_result"] == "committed":
                committed.append(kind)
            else:
                worker_errors.append((
                    f"{kind} operation result",
                    RuntimeError("worker published no commit or refusal outcome"),
                ))
            if record["connection_close_error"] is not None:
                if (
                    record["operation_error"] is not None
                    and isinstance(record["operation_error"], IntegrityError)
                    and self._integrity_refusal_message(record["operation_error"])
                    in {
                        "S3 terminal exclusion",
                        "S3 withdrawal requires applicant and no decision",
                    }
                ):
                    worker_errors.append((
                        f"{kind} refusal before connection close",
                        record["operation_error"],
                    ))
                worker_errors.append((
                    f"{kind} connection close",
                    record["connection_close_error"],
                ))

        failures = []
        if primary_error is not None:
            failures.append(("race coordination", primary_error))
        failures.extend(cleanup_errors)
        failures.extend(worker_errors)
        if failures:
            description = "; ".join(
                f"{label}: {type(error).__name__}: {error}"
                for label, error in failures
            )
            raise RuntimeError(
                f"O-24 race coordination or cleanup failed: {description}"
            ) from failures[0][1]

        self.assertEqual(len(committed), 1, worker_records)
        self.assertEqual(len(refused), 1, worker_records)
        self.assertEqual(refused[0][1], "S3 terminal exclusion")

        self.assertEqual(
            ApplicationDecision.objects.filter(application=application).count()
            + ApplicationWithdrawal.objects.filter(application=application).count(),
            1,
        )
        if committed[0] == "decision":
            self._assert_counts(application, decisions=1, withdrawals=0)
        else:
            self._assert_counts(application, decisions=0, withdrawals=1)
