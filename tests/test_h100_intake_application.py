"""H100 C2a public application-intake boundary witnesses."""

from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from queue import Queue
from threading import Event, get_ident
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import User
from django.db import IntegrityError, OperationalError, connection, transaction
from django.db.models import F
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext

from core.models import (
    Application,
    ApplicationLineage,
    ApplicationLineageEvent,
    AuthorityDecision,
    CommandIntake,
    Identity,
    IdentityTransition,
    LivingOrganismEvent,
    OrganismCommandReceipt,
)
from core.organism_contract import OrganismAction
from src.intevia.services import organism_application_service as application_service_module
from src.intevia.services.command_intake import (
    ACTIVE_ENTRIES,
    ENTRY_ACTIONS,
    FINGERPRINT_SCHEMA,
    IntakeEntry,
    application_shape,
    intake_fingerprint,
    run_activation_step,
)
from src.intevia.services.organism_application_service import (
    _COMMAND_DOMAIN,
    ClaimApplicationCommand,
    DecideApplicationCommand,
    OrganismApplicationService,
    ReassignApplicationCommand,
    S015ApplicationAuthorityUnavailable,
    SubmitApplicationCommand,
    WithdrawApplicationCommand,
)
from src.intevia.services.organism_authority import (
    S015Authority,
    S015OwnApplicantAuthorityRequest,
    S015OwnApplicantAuthorityResponse,
    S015PostFoundingAuthorityResponse,
)
from src.intevia.services.organism_command_support import (
    _command_payload,
    ReplayViewer,
    S015CommandAtomicityRequired,
    S015CommandContended,
    S015CommandReplayMismatch,
    command_mutex_key,
)
from tests.s015_0021_support import database_now, fresh, seed_identity
from tests.s015_concurrency_support import backend_pid, cleanup_workers, start_worker
from tests.s015_concurrency_support import wait_until_locked
from tests.sb2_route_support import birth, prepare_route, route_test_now


T0 = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)


class _Provider:
    """A fixture authority provider for the public application paths."""

    def evaluate_organism_authority(self, request):
        fields = asdict(request)
        if isinstance(request, S015OwnApplicantAuthorityRequest):
            return S015OwnApplicantAuthorityResponse(
                **fields,
                authority_reference="h100:test:applicant",
            )
        return S015PostFoundingAuthorityResponse(
            **fields,
            authority_reference="h100:test:coordinator",
        )


def _commands(credential: User, *, tag: str):
    application_uuid = uuid4()
    return {
        IntakeEntry.SUBMIT_APPLICATION: SubmitApplicationCommand(
            credential=credential,
            living_organism_id=uuid4(),
            request_reference=f"h100:submit:{tag}",
            idempotency_key=f"h100:submit:key:{tag}",
            occurred_at=T0,
            effective_at=T0,
            answer="A valid fixture answer",
        ),
        IntakeEntry.WITHDRAW_APPLICATION: WithdrawApplicationCommand(
            credential=credential,
            application_uuid=application_uuid,
            request_reference=f"h100:withdraw:{tag}",
            idempotency_key=f"h100:withdraw:key:{tag}",
            occurred_at=T0,
            effective_at=T0,
        ),
        IntakeEntry.CLAIM_APPLICATION: ClaimApplicationCommand(
            credential=credential,
            application_uuid=application_uuid,
            request_reference=f"h100:claim:{tag}",
            idempotency_key=f"h100:claim:key:{tag}",
            occurred_at=T0,
            effective_at=T0,
        ),
        IntakeEntry.REASSIGN_APPLICATION: ReassignApplicationCommand(
            credential=credential,
            application_uuid=application_uuid,
            request_reference=f"h100:reassign:{tag}",
            idempotency_key=f"h100:reassign:key:{tag}",
            occurred_at=T0,
            effective_at=T0,
        ),
        IntakeEntry.DECIDE_APPLICATION: DecideApplicationCommand(
            credential=credential,
            application_uuid=application_uuid,
            request_reference=f"h100:decide:{tag}",
            idempotency_key=f"h100:decide:key:{tag}",
            occurred_at=T0,
            effective_at=T0,
            outcome="DECLINE",
        ),
    }


@skipUnless(connection.vendor == "postgresql", "H100 intake witnesses require PostgreSQL")
class IntakeAdmissionStateTests(TransactionTestCase):
    """Prove active public entries refuse unsupported transactions before SQL."""

    def setUp(self):
        self.service = OrganismApplicationService(
            authority=S015Authority(provider=_Provider()),
            clock=database_now,
        )
        self.commands = _commands(
            User(username="h100-admission-state"),
            tag=f"{fresh()}",
        )

    def _call(self, entry, command):
        methods = {
            IntakeEntry.SUBMIT_APPLICATION: self.service.submit_application,
            IntakeEntry.WITHDRAW_APPLICATION: self.service.withdraw_application,
            IntakeEntry.CLAIM_APPLICATION: self.service.claim_application,
            IntakeEntry.REASSIGN_APPLICATION: self.service.reassign_application,
            IntakeEntry.DECIDE_APPLICATION: self.service.decide_application,
        }
        return methods[entry](command)

    def test_application_entries_refuse_atomic_and_manual_modes_without_sql(self):
        self.assertEqual(len(ACTIVE_ENTRIES), 10)
        for entry, command in self.commands.items():
            with self.subTest(entry=entry):
                with transaction.atomic():
                    with CaptureQueriesContext(connection) as queries:
                        with self.assertRaises(S015CommandAtomicityRequired):
                            self._call(entry, command)
                    self.assertEqual(len(queries), 0)
                    self.assertTrue(connection.in_atomic_block)
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT 1")
                        self.assertEqual(cursor.fetchone()[0], 1)

                connection.set_autocommit(False)
                try:
                    with CaptureQueriesContext(connection) as queries:
                        with self.assertRaises(S015CommandAtomicityRequired):
                            self._call(entry, command)
                    self.assertEqual(len(queries), 0)
                finally:
                    connection.rollback()
                    connection.set_autocommit(True)

    def test_client_received_at_is_refused_before_any_query(self):
        before = CommandIntake.objects.count()
        for entry, command in self.commands.items():
            supplied = replace(command, received_at=T0)
            with self.subTest(entry=entry), CaptureQueriesContext(connection) as queries:
                with self.assertRaisesRegex(
                    ValueError, "received_at is assigned by INTEVIA"
                ):
                    self._call(entry, supplied)
                self.assertEqual(len(queries), 0)
        self.assertEqual(CommandIntake.objects.count(), before)

    def test_shape_rules_refuse_malformed_keys_uuids_timestamps_and_outcomes_before_sql(self):
        naive_time = datetime(2026, 9, 29, 12)
        cases = []
        for entry, command in self.commands.items():
            cases.extend(
                (
                    (entry, "request_reference", replace(
                        command,
                        request_reference=f" {command.request_reference}",
                    ), ValueError, "request_reference is not canonical"),
                    (entry, "idempotency_key", replace(
                        command,
                        idempotency_key=f" {command.idempotency_key}",
                    ), ValueError, "idempotency_key is not canonical"),
                    (
                        entry,
                        "timestamp",
                        replace(command, occurred_at=naive_time),
                        TypeError,
                        "occurred_at must be timezone-aware",
                    ),
                )
            )
            cases.append((
                entry, "effective_at", replace(command, effective_at=naive_time),
                TypeError, "effective_at must be timezone-aware",
            ))
            if entry is IntakeEntry.SUBMIT_APPLICATION:
                cases.append((
                    entry,
                    "organism_uuid",
                    replace(command, living_organism_id="not-a-uuid"),
                    TypeError,
                    "living_organism_id must be a UUID",
                ))
                cases.append((
                    entry, "predecessor_reference",
                    replace(command, predecessor_reference="not-a-uuid"),
                    TypeError, "predecessor_reference must be a UUID",
                ))
            else:
                cases.append((
                    entry,
                    "application_uuid",
                    replace(command, application_uuid="not-a-uuid"),
                    TypeError,
                    "application_uuid must be a UUID",
                ))
            if entry is IntakeEntry.DECIDE_APPLICATION:
                cases.append((
                    entry,
                    "outcome",
                    replace(command, outcome="REJECT"),
                    ValueError,
                    "outcome must be ACCEPT or DECLINE",
                ))

        before = CommandIntake.objects.count()
        for entry, rule, malformed, expected_error, message in cases:
            with self.subTest(entry=entry, rule=rule):
                with CaptureQueriesContext(connection) as queries:
                    with self.assertRaisesRegex(expected_error, message):
                        self._call(entry, malformed)
                self.assertEqual(len(queries), 0)
        self.assertEqual(CommandIntake.objects.count(), before)

    def test_pending_rollback_refuses_before_any_query(self):
        command = self.commands[IntakeEntry.SUBMIT_APPLICATION]
        db = transaction.get_connection()
        self.assertFalse(db.in_atomic_block)
        self.assertTrue(db.get_autocommit())
        self.assertFalse(db.needs_rollback)
        # Isolate this defensive branch; real atomic refusal is tested separately.
        with patch.object(db, "needs_rollback", True):
            with CaptureQueriesContext(connection) as queries:
                with self.assertRaisesMessage(
                    S015CommandAtomicityRequired,
                    "command intake cannot use a connection with a pending rollback",
                ):
                    self.service.submit_application(command)
            self.assertEqual(len(queries), 0)
        self.assertFalse(db.needs_rollback)


@skipUnless(connection.vendor == "postgresql", "H100 intake witnesses require PostgreSQL")
class IntakeAdmissionDjangoTestCase(TestCase):
    def setUp(self):
        self.service = OrganismApplicationService(
            authority=S015Authority(provider=_Provider()),
            clock=database_now,
        )
        self.command = _commands(
            User(username="h100-testcase-admission"),
            tag=f"{fresh()}",
        )[IntakeEntry.SUBMIT_APPLICATION]

    def test_framework_atomic_refuses_public_call_before_any_query(self):
        self.assertTrue(connection.in_atomic_block)
        with CaptureQueriesContext(connection) as queries:
            with self.assertRaises(S015CommandAtomicityRequired):
                self.service.submit_application(self.command)
        self.assertEqual(len(queries), 0)


@skipUnless(connection.vendor == "postgresql", "H100 intake witnesses require PostgreSQL")
class ApplicationIntakeFlowTests(TransactionTestCase):
    """Exercise admission, execution, retries and activation using real fixtures."""

    def setUp(self):
        self.tag = f"h100-application-{fresh()}"
        self.route = prepare_route(tag=self.tag)
        birth(self.route)
        self.organism_uuid = self.route.organism.organism_id
        self.now = route_test_now(self.route)
        self.applicant = self._new_applicant("one")
        self.second_applicant = self._new_applicant("two")
        self.service = OrganismApplicationService(
            authority=S015Authority(provider=_Provider()),
            clock=database_now,
        )

    def _new_applicant(self, suffix):
        with transaction.atomic(), connection.cursor() as cursor:
            identity_pk = seed_identity(cursor, f"h100-applicant-{suffix}-{self.tag}")
        identity = Identity.objects.select_related("credential").get(pk=identity_pk)
        provision = IdentityTransition.objects.create(
            identity=identity,
            action=IdentityTransition.Action.PROVISION,
            prior_state=None,
            resulting_state="pending",
            authority_reference="h100:test:provision",
            technical_executor="H100_TEST",
            evidence_reference="h100:test:history",
            reason_category=IdentityTransition.ReasonCategory.PROVISIONING,
            correlation_id=uuid4(),
            occurred_at=self.now,
        )
        IdentityTransition.objects.create(
            identity=identity,
            action=IdentityTransition.Action.ACTIVATE,
            prior_state="pending",
            resulting_state="active",
            authority_reference="h100:test:activate",
            technical_executor="H100_TEST",
            evidence_reference="h100:test:history",
            reason_category=IdentityTransition.ReasonCategory.ACCESS_REVIEW,
            correlation_id=uuid4(),
            occurred_at=self.now,
            previous_transition=provision,
        )
        return identity

    def _submit(self, applicant, suffix):
        return SubmitApplicationCommand(
            credential=applicant.credential,
            living_organism_id=self.organism_uuid,
            request_reference=f"h100:submit:{suffix}:{self.tag}",
            idempotency_key=f"h100:submit:key:{suffix}:{self.tag}",
            occurred_at=self.now,
            effective_at=self.now,
            answer=f"Application answer {suffix}",
        )

    def _coordinator_command(self, command_type, application_uuid, suffix, **extra):
        return command_type(
            credential=self.route.founder.credential,
            application_uuid=application_uuid,
            request_reference=f"h100:{suffix}:{self.tag}",
            idempotency_key=f"h100:{suffix}:key:{self.tag}",
            occurred_at=self.now,
            effective_at=self.now,
            **extra,
        )

    def _counts(self):
        return {
            model.__name__: model.objects.count()
            for model in (
                CommandIntake,
                Application,
                ApplicationLineage,
                ApplicationLineageEvent,
                AuthorityDecision,
                OrganismCommandReceipt,
            )
        }

    @staticmethod
    def _public_row_counts():
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT c.relname FROM pg_catalog.pg_class c "
                "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') ORDER BY c.relname"
            )
            tables = [row[0] for row in cursor.fetchall()]
            counts = {}
            for table in tables:
                cursor.execute(
                    f"SELECT count(*) FROM public.{connection.ops.quote_name(table)}"
                )
                counts[table] = cursor.fetchone()[0]
        return counts

    @staticmethod
    def _changed_public_rows(before, after):
        return {
            table: after[table] - count
            for table, count in before.items()
            if after[table] != count
        }

    def test_c9_all_five_entries_are_admitted_and_accounted(self):
        intake_count_before = CommandIntake.objects.count()
        receipt_count_before = OrganismCommandReceipt.objects.count()
        decision_count_before = AuthorityDecision.objects.count()
        submit_one = self._submit(self.applicant, "c9-one")
        first_result = self.service.submit_application(submit_one)
        first_application = Application.objects.get(
            application_uuid=first_result.application_uuid
        )
        withdraw = WithdrawApplicationCommand(
            credential=self.applicant.credential,
            application_uuid=first_application.application_uuid,
            request_reference=f"h100:withdraw:{self.tag}",
            idempotency_key=f"h100:withdraw:key:{self.tag}",
            occurred_at=self.now,
            effective_at=self.now,
        )
        self.service.withdraw_application(withdraw)

        submit_two = self._submit(self.second_applicant, "c9-two")
        second_result = self.service.submit_application(submit_two)
        claim = self._coordinator_command(
            ClaimApplicationCommand,
            second_result.application_uuid,
            "claim",
        )
        claim_t0 = database_now()
        self.service.claim_application(claim)
        claim_t1 = database_now()
        reassign = self._coordinator_command(
            ReassignApplicationCommand,
            second_result.application_uuid,
            "reassign",
        )
        self.service.reassign_application(reassign)
        decide = self._coordinator_command(
            DecideApplicationCommand,
            second_result.application_uuid,
            "decide",
            outcome="DECLINE",
        )
        self.service.decide_application(decide)

        commands = (submit_one, withdraw, submit_two, claim, reassign, decide)
        intakes = list(
            CommandIntake.objects.filter(
                idempotency_key__in=[command.idempotency_key for command in commands]
            )
        )
        receipts = list(
            OrganismCommandReceipt.objects.filter(
                idempotency_key__in=[command.idempotency_key for command in commands]
            )
        )
        decisions = list(
            AuthorityDecision.objects.filter(
                idempotency_key__in=[command.idempotency_key for command in commands]
            )
        )
        self.assertTrue(intakes)
        self.assertTrue(receipts)
        self.assertTrue(decisions)
        self.assertEqual(len(intakes), len(commands))
        self.assertEqual(len(receipts), len(commands))
        self.assertEqual(len(decisions), len(commands))
        self.assertEqual(CommandIntake.objects.count(), intake_count_before + len(commands))
        self.assertEqual(
            OrganismCommandReceipt.objects.count(),
            receipt_count_before + len(commands),
        )
        self.assertEqual(
            AuthorityDecision.objects.count(),
            decision_count_before + len(commands),
        )
        self.assertEqual(
            {row.entry for row in intakes},
            {
                entry.value
                for entry in (
                    IntakeEntry.SUBMIT_APPLICATION,
                    IntakeEntry.WITHDRAW_APPLICATION,
                    IntakeEntry.CLAIM_APPLICATION,
                    IntakeEntry.REASSIGN_APPLICATION,
                    IntakeEntry.DECIDE_APPLICATION,
                )
            },
        )
        by_key = {row.idempotency_key: row for row in intakes}
        claim_intake = by_key[claim.idempotency_key]
        claim_shape = application_shape(IntakeEntry.CLAIM_APPLICATION, claim)
        claim_action = ENTRY_ACTIONS[IntakeEntry.CLAIM_APPLICATION]
        if claim_action is None:
            self.fail("claim intake has no action")
        claim_fingerprint = intake_fingerprint(
            entry=IntakeEntry.CLAIM_APPLICATION,
            action=claim_action,
            actor_identity_id=self.route.founder.identity_id,
            actor_access_epoch=self.route.founder.access_epoch,
            request_reference=claim_shape.request_reference,
            idempotency_key=claim_shape.idempotency_key,
            target=claim_shape.target,
            command=claim_shape.command,
            omitted=claim_shape.omitted,
        )
        claim_receipt = next(
            row for row in receipts if row.idempotency_key == claim.idempotency_key
        )
        claim_receipt_fingerprint = _command_payload(
            domain=_COMMAND_DOMAIN,
            schema="s015-application-v1",
            action=OrganismAction.CLAIM_APPLICATION,
            actor_identity_id=self.route.founder.identity_id,
            actor_access_epoch=self.route.founder.access_epoch,
            request_reference=claim.request_reference,
            idempotency_key=claim.idempotency_key,
            occurred_at=claim.occurred_at,
            target={
                "action": OrganismAction.CLAIM_APPLICATION.value,
                "application": claim.application_uuid,
                "organism": self.organism_uuid,
            },
            command={
                "effective_at": claim.effective_at,
                "received_at": claim_intake.received_at,
                "outcome": None,
            },
        )
        self.assertLessEqual(claim_t0, claim_intake.received_at)
        self.assertLessEqual(claim_intake.received_at, claim_t1)
        self.assertEqual(claim_intake.request_fingerprint, claim_fingerprint)
        self.assertEqual(claim_receipt.payload_fingerprint, claim_receipt_fingerprint)
        for decision in decisions:
            with self.subTest(key=decision.idempotency_key):
                self.assertEqual(
                    decision.evaluated_at,
                    by_key[decision.idempotency_key].received_at,
                )
        self.assertEqual(
            run_activation_step(
                "default",
                (
                    IntakeEntry.SUBMIT_APPLICATION,
                    IntakeEntry.WITHDRAW_APPLICATION,
                    IntakeEntry.CLAIM_APPLICATION,
                    IntakeEntry.REASSIGN_APPLICATION,
                    IntakeEntry.DECIDE_APPLICATION,
                ),
            ),
            [],
        )

    def test_rollback_keeps_intake_time_and_retry_completes_once(self):
        command = self._submit(self.applicant, "rollback")
        before = self._counts()
        public_before = self._public_row_counts()
        observed_message = []

        def fail_before_receipt(**kwargs):
            intake = CommandIntake.objects.get(
                actor_id=self.applicant.pk,
                idempotency_key=kwargs["idempotency_key"],
            )
            observed_message.append(
                f"received record was kept at {intake.received_at.isoformat()}"
            )
            raise S015ApplicationAuthorityUnavailable(observed_message[-1])

        with patch.object(self.service, "_finish", side_effect=fail_before_receipt):
            with self.assertRaises(S015ApplicationAuthorityUnavailable) as raised:
                self.service.submit_application(command)
        self.assertIn("received record was kept at", str(raised.exception))
        intake = CommandIntake.objects.get(
            actor_id=self.applicant.pk,
            idempotency_key=command.idempotency_key,
        )
        after_failure = self._counts()
        public_after_failure = self._public_row_counts()
        self.assertEqual(
            self._changed_public_rows(public_before, public_after_failure),
            {"core_commandintake": 1},
        )
        self.assertEqual(after_failure["CommandIntake"], before["CommandIntake"] + 1)
        for table in (
            "Application",
            "ApplicationLineage",
            "ApplicationLineageEvent",
            "AuthorityDecision",
            "OrganismCommandReceipt",
        ):
            self.assertEqual(after_failure[table], before[table], table)
        changed = replace(command, answer="Changed after intake")
        with self.assertRaises(S015CommandReplayMismatch):
            self.service.submit_application(changed)
        self.assertEqual(self._counts(), after_failure)

        result = self.service.submit_application(command)
        after_retry = self._counts()
        public_after_retry = self._public_row_counts()
        self.assertEqual(
            self._changed_public_rows(public_after_failure, public_after_retry),
            {
                "core_application": 1,
                "core_applicationlineage": 1,
                "core_applicationlineageevent": 1,
                "core_authoritydecision": 1,
                "core_organismcommandreceipt": 1,
            },
        )
        self.assertEqual(after_retry["CommandIntake"], after_failure["CommandIntake"])
        for table in (
            "Application",
            "ApplicationLineage",
            "ApplicationLineageEvent",
            "AuthorityDecision",
            "OrganismCommandReceipt",
        ):
            self.assertEqual(after_retry[table], after_failure[table] + 1, table)
        self.assertEqual(
            Application.objects.get(application_uuid=result.application_uuid).received_at,
            intake.received_at,
        )
        decision = AuthorityDecision.objects.get(
            actor_id=self.applicant.pk,
            action=OrganismAction.SUBMIT_APPLICATION.value,
            idempotency_key=command.idempotency_key,
        )
        self.assertEqual(decision.evaluated_at, intake.received_at)
        self.assertEqual(
            CommandIntake.objects.get(pk=intake.pk).received_at,
            intake.received_at,
        )
        after_retry = self._counts()
        with self.assertRaises(S015CommandReplayMismatch):
            self.service.submit_application(changed)
        self.assertEqual(self._counts(), after_retry)
        self.assertEqual(self._public_row_counts(), public_after_retry)
        actor = Identity.objects.get(pk=self.applicant.pk)
        with transaction.atomic(), self.assertRaises(S015CommandReplayMismatch):
            self.service._receipt_or_none(
                actor=actor,
                action=OrganismAction.SUBMIT_APPLICATION,
                request_reference=command.request_reference,
                idempotency_key=command.idempotency_key,
                fingerprint="0" * 64,
                viewer=ReplayViewer(actor.pk, actor.identity_id, database_now()),
            )
        self.assertEqual(self._counts(), after_retry)
        self.assertEqual(self._public_row_counts(), public_after_retry)

    def test_transaction_exit_failure_after_receipt_keeps_only_intake_and_retry_uses_same_time(self):
        command = self._submit(self.applicant, "deferred-commit-failure")
        before = self._public_row_counts()
        original_finish = self.service._finish
        receipt_written = []

        def fail_at_commit(**kwargs):
            result = original_finish(**kwargs)
            receipt_written.append(
                OrganismCommandReceipt.objects.filter(
                    actor=kwargs["actor"],
                    action=kwargs["action"].value,
                    idempotency_key=kwargs["idempotency_key"],
                ).exists()
            )
            with connection.cursor() as cursor:
                cursor.execute(
                    "CREATE TEMP TABLE h100_deferred_parent "
                    "(id integer PRIMARY KEY) ON COMMIT DROP"
                )
                cursor.execute(
                    "CREATE TEMP TABLE h100_deferred_child ("
                    "parent_id integer NOT NULL, "
                    "CONSTRAINT h100_intake_deferred_failure_fk "
                    "FOREIGN KEY (parent_id) REFERENCES h100_deferred_parent(id) "
                    "DEFERRABLE INITIALLY DEFERRED) ON COMMIT DROP"
                )
                cursor.execute(
                    "INSERT INTO h100_deferred_child (parent_id) VALUES (1)"
                )
            return result

        with patch.object(self.service, "_finish", side_effect=fail_at_commit):
            with self.assertRaises(IntegrityError) as raised:
                self.service.submit_application(command)

        self.assertEqual(receipt_written, [True])
        cause = raised.exception.__cause__
        self.assertEqual(getattr(cause, "sqlstate", None), "23503")
        self.assertEqual(
            getattr(getattr(cause, "diag", None), "constraint_name", None),
            "h100_intake_deferred_failure_fk",
        )
        after_failure = self._public_row_counts()
        self.assertEqual(
            self._changed_public_rows(before, after_failure),
            {"core_commandintake": 1},
        )
        intake = CommandIntake.objects.get(
            actor_id=self.applicant.pk,
            idempotency_key=command.idempotency_key,
        )

        result = self.service.submit_application(command)
        after_retry = self._public_row_counts()
        self.assertEqual(
            self._changed_public_rows(after_failure, after_retry),
            {
                "core_application": 1,
                "core_applicationlineage": 1,
                "core_applicationlineageevent": 1,
                "core_authoritydecision": 1,
                "core_organismcommandreceipt": 1,
            },
        )
        application = Application.objects.get(application_uuid=result.application_uuid)
        decision = AuthorityDecision.objects.get(
            actor_id=self.applicant.pk,
            action=OrganismAction.SUBMIT_APPLICATION.value,
            idempotency_key=command.idempotency_key,
        )
        self.assertEqual(application.received_at, intake.received_at)
        self.assertEqual(decision.evaluated_at, intake.received_at)
        self.assertEqual(
            CommandIntake.objects.get(pk=intake.pk).received_at,
            intake.received_at,
        )
        with self.assertRaises(S015CommandReplayMismatch):
            self.service.submit_application(replace(command, answer="Changed on replay"))
        self.assertEqual(self._public_row_counts(), after_retry)

    def test_uncertain_intake_and_command_responses_replay_without_duplicate_writes(self):
        uncertain_intake = self._submit(self.applicant, "uncertain-intake")
        before = self._counts()
        public_before = self._public_row_counts()
        with patch.object(
            self.service,
            "_execute_submit_application",
            side_effect=S015ApplicationAuthorityUnavailable(
                "response was lost after intake committed"
            ),
        ):
            with self.assertRaises(S015ApplicationAuthorityUnavailable):
                self.service.submit_application(uncertain_intake)
        first_binding = CommandIntake.objects.get(
            actor_id=self.applicant.pk,
            idempotency_key=uncertain_intake.idempotency_key,
        )
        after_lost_admission_response = self._counts()
        public_after_lost_response = self._public_row_counts()
        self.assertEqual(
            self._changed_public_rows(public_before, public_after_lost_response),
            {"core_commandintake": 1},
        )
        self.assertEqual(
            after_lost_admission_response["CommandIntake"],
            before["CommandIntake"] + 1,
        )
        for table in (
            "Application",
            "ApplicationLineage",
            "ApplicationLineageEvent",
            "AuthorityDecision",
            "OrganismCommandReceipt",
        ):
            self.assertEqual(
                after_lost_admission_response[table],
                before[table],
                table,
            )
        first_result = self.service.submit_application(uncertain_intake)
        public_after_first_result = self._public_row_counts()
        self.assertEqual(
            self._changed_public_rows(public_after_lost_response, public_after_first_result),
            {
                "core_application": 1,
                "core_applicationlineage": 1,
                "core_applicationlineageevent": 1,
                "core_authoritydecision": 1,
                "core_organismcommandreceipt": 1,
            },
        )
        self.assertEqual(
            Application.objects.get(
                application_uuid=first_result.application_uuid
            ).received_at,
            first_binding.received_at,
        )

        uncertain_command = self._submit(self.second_applicant, "uncertain-command")
        public_before_command_commit = self._public_row_counts()
        original = self.service._execute_submit_application
        discarded = []

        def commit_then_discard(command, bound):
            result = original(command, bound)
            discarded.append(result)
            raise S015ApplicationAuthorityUnavailable(
                "response was lost after command commit"
            )

        with patch.object(
            self.service,
            "_execute_submit_application",
            side_effect=commit_then_discard,
        ):
            with self.assertRaises(S015ApplicationAuthorityUnavailable):
                self.service.submit_application(uncertain_command)
        after_commit = self._counts()
        public_after_commit = self._public_row_counts()
        self.assertEqual(
            self._changed_public_rows(public_before_command_commit, public_after_commit),
            {
                "core_commandintake": 1,
                "core_application": 1,
                "core_applicationlineage": 1,
                "core_applicationlineageevent": 1,
                "core_authoritydecision": 1,
                "core_organismcommandreceipt": 1,
            },
        )
        replay = self.service.submit_application(uncertain_command)
        self.assertEqual(replay, discarded[0])
        self.assertEqual(self._counts(), after_commit)
        self.assertEqual(self._public_row_counts(), public_after_commit)

    def test_intake_decision_and_replay_view_use_distinct_times(self):
        command = self._submit(self.applicant, "separate-times")
        viewer_times = []
        original_serve_replay = application_service_module.serve_replay

        def capture_viewer_time(result, *, viewer, result_type):
            viewer_times.append(viewer.now)
            return original_serve_replay(
                result,
                viewer=viewer,
                result_type=result_type,
            )

        with patch.object(
            application_service_module,
            "serve_replay",
            side_effect=capture_viewer_time,
        ):
            first_result = self.service.submit_application(command)
            replay = self.service.submit_application(command)

        intake = CommandIntake.objects.get(
            actor_id=self.applicant.pk,
            idempotency_key=command.idempotency_key,
        )
        application = Application.objects.get(
            application_uuid=first_result.application_uuid,
        )
        lineage = ApplicationLineage.objects.get(application=application)
        lineage_event = ApplicationLineageEvent.objects.get(application_lineage=lineage)
        decision = AuthorityDecision.objects.get(
            actor_id=self.applicant.pk,
            action=OrganismAction.SUBMIT_APPLICATION.value,
            idempotency_key=command.idempotency_key,
        )
        self.assertEqual(replay, first_result)
        self.assertEqual(application.received_at, intake.received_at)
        self.assertEqual(lineage_event.received_at, intake.received_at)
        self.assertEqual(decision.evaluated_at, intake.received_at)
        self.assertGreaterEqual(application.recorded_at, intake.received_at)
        self.assertGreaterEqual(lineage_event.recorded_at, intake.received_at)
        self.assertGreaterEqual(decision.recorded_at, intake.received_at)
        self.assertEqual(len(viewer_times), 2)
        self.assertTrue(all(view_time > intake.received_at for view_time in viewer_times))
        self.assertGreaterEqual(viewer_times[1], viewer_times[0])

    def test_committed_intake_is_visible_while_executor_is_open(self):
        command = self._submit(self.applicant, "visible")
        before = self._public_row_counts()
        entered = Event()
        release = Event()
        worker_result = {}
        actual_append = application_service_module._append_application_event

        def append_then_pause(*args, **kwargs):
            result = actual_append(*args, **kwargs)
            entered.set()
            if not release.wait(timeout=15):
                raise AssertionError("executor visibility hook timed out")
            return result

        worker = None
        try:
            with patch.object(
                application_service_module,
                "_append_application_event",
                side_effect=append_then_pause,
            ):
                worker = start_worker(
                    lambda: worker_result.setdefault(
                        "result",
                        self.service.submit_application(command),
                    )
                )
                self.assertTrue(entered.wait(timeout=15))
                during = self._public_row_counts()
                self.assertEqual(
                    self._changed_public_rows(before, during),
                    {"core_commandintake": 1},
                )
                intake = CommandIntake.objects.get(
                    actor_id=self.applicant.pk,
                    idempotency_key=command.idempotency_key,
                )
                self.assertIsNotNone(intake.received_at)
                self.assertFalse(
                    Application.objects.filter(
                        applicant=self.applicant,
                        living_organism=self.route.organism,
                    ).exists()
                )
        finally:
            release.set()
            if worker is not None:
                worker.thread.join(timeout=30)
                if worker.thread.is_alive():
                    cleanup_workers([worker])
        self.assertIsNotNone(worker)
        self.assertEqual(worker.result(timeout=5), "ok")
        self.assertIn("result", worker_result)
        self.assertEqual(
            Application.objects.filter(
                applicant=self.applicant,
                living_organism=self.route.organism,
            ).count(),
            1,
        )
        self.assertEqual(
            self._changed_public_rows(before, self._public_row_counts()),
            {
                "core_commandintake": 1,
                "core_application": 1,
                "core_applicationlineage": 1,
                "core_applicationlineageevent": 1,
                "core_authoritydecision": 1,
                "core_organismcommandreceipt": 1,
            },
        )

    def test_manual_transaction_keeps_caller_write_and_public_refusal_does_not_commit_it(self):
        command = self._submit(self.applicant, "manual-state")
        starting_epoch = Identity.objects.get(pk=self.applicant.pk).access_epoch
        intake_count = CommandIntake.objects.count()
        observed = Queue()
        connection.set_autocommit(False)
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE core_identity SET access_epoch = access_epoch + 1 WHERE id = %s",
                    [self.applicant.pk],
                )
            with CaptureQueriesContext(connection) as queries:
                with self.assertRaises(S015CommandAtomicityRequired):
                    self.service.submit_application(command)
            self.assertEqual(len(queries), 0)
            with connection.cursor() as cursor:
                cursor.execute("SELECT access_epoch FROM core_identity WHERE id = %s", [self.applicant.pk])
                self.assertEqual(cursor.fetchone()[0], starting_epoch + 1)

            observer = start_worker(
                lambda: observed.put(
                    Identity.objects.values_list("access_epoch", flat=True).get(
                        pk=self.applicant.pk
                    )
                )
            )
            observer.thread.join(timeout=15)
            if observer.thread.is_alive():
                cleanup_workers([observer])
                raise AssertionError("manual-transaction observer did not finish")
            self.assertEqual(observer.result(timeout=5), "ok")
            self.assertEqual(observed.get(timeout=5), starting_epoch)
            self.assertEqual(CommandIntake.objects.count(), intake_count)
            connection.commit()
        finally:
            if not connection.get_autocommit():
                connection.rollback()
                connection.set_autocommit(True)

        committed = Queue()
        observer = start_worker(
            lambda: committed.put(
                Identity.objects.values_list("access_epoch", flat=True).get(
                    pk=self.applicant.pk
                )
            )
        )
        observer.thread.join(timeout=15)
        if observer.thread.is_alive():
            cleanup_workers([observer])
            raise AssertionError("committed-state observer did not finish")
        self.assertEqual(observer.result(timeout=5), "ok")
        self.assertEqual(committed.get(timeout=5), starting_epoch + 1)
        self.assertEqual(CommandIntake.objects.count(), intake_count)

    def test_overlapping_replay_serializes_at_the_actor_lock(self):
        command = self._submit(self.applicant, "overlap-actor")
        first_result = self.service.submit_application(command)
        entered = Event()
        release = Event()
        first_ident = []
        first_result_holder = {}
        second_result_holder = {}
        acquire_mutex = application_service_module.acquire_organism_command_mutex

        def pause_after_mutex(*args, **kwargs):
            acquired = acquire_mutex(*args, **kwargs)
            if not first_ident:
                first_ident.append(get_ident())
            if get_ident() == first_ident[0]:
                entered.set()
                if not release.wait(timeout=15):
                    raise AssertionError("actor-lock overlap hook timed out")
            return acquired

        first = None
        second = None
        try:
            with patch.object(
                application_service_module,
                "acquire_organism_command_mutex",
                side_effect=pause_after_mutex,
            ):
                first = start_worker(
                    lambda: first_result_holder.setdefault(
                        "result",
                        self.service.submit_application(command),
                    )
                )
                self.assertTrue(entered.wait(timeout=15))

                def replay_second():
                    with connection.cursor() as cursor:
                        cursor.execute("SET lock_timeout = '10s'")
                    second_result_holder["result"] = self.service.submit_application(command)

                second = start_worker(replay_second)
                with connection.cursor() as cursor:
                    wait_until_locked(
                        cursor,
                        second.pid(),
                        first.pid(),
                        relations={"core_identity"},
                        timeout=10,
                    )
        finally:
            release.set()
            for worker in (first, second):
                if worker is not None:
                    worker.thread.join(timeout=30)
                    if worker.thread.is_alive():
                        cleanup_workers([worker])
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(first.result(timeout=5), "ok")
        self.assertEqual(second.result(timeout=5), "ok")
        self.assertEqual(first_result_holder["result"], first_result)
        self.assertEqual(second_result_holder["result"], first_result)
        self.assertEqual(
            CommandIntake.objects.filter(
                actor_id=self.applicant.pk,
                idempotency_key=command.idempotency_key,
            ).count(),
            1,
        )
        self.assertEqual(
            OrganismCommandReceipt.objects.filter(
                actor_id=self.applicant.pk,
                idempotency_key=command.idempotency_key,
            ).count(),
            1,
        )

    def test_fresh_identical_submissions_overlap_at_the_actor_lock(self):
        command = self._submit(self.applicant, "fresh-overlap")
        before = self._public_row_counts()
        entered = Event()
        release = Event()
        first_ident = []
        results = {}
        acquire_mutex = application_service_module.acquire_organism_command_mutex

        def pause_after_mutex(*args, **kwargs):
            acquired = acquire_mutex(*args, **kwargs)
            if not first_ident:
                first_ident.append(get_ident())
            if get_ident() == first_ident[0]:
                entered.set()
                if not release.wait(timeout=15):
                    raise AssertionError("fresh-overlap hook timed out")
            return acquired

        first = None
        second = None
        try:
            with patch.object(
                application_service_module,
                "acquire_organism_command_mutex",
                side_effect=pause_after_mutex,
            ):
                first = start_worker(
                    lambda: results.setdefault(
                        "first",
                        self.service.submit_application(command),
                    )
                )
                self.assertTrue(entered.wait(timeout=15))

                def submit_second():
                    with connection.cursor() as cursor:
                        cursor.execute("SET lock_timeout = '10s'")
                    results["second"] = self.service.submit_application(command)

                second = start_worker(submit_second)
                with connection.cursor() as cursor:
                    wait_until_locked(
                        cursor,
                        second.pid(),
                        first.pid(),
                        relations={"core_identity"},
                        timeout=10,
                    )
        finally:
            release.set()
            for worker in (first, second):
                if worker is not None:
                    worker.thread.join(timeout=30)
                    if worker.thread.is_alive():
                        cleanup_workers([worker])
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(first.result(timeout=5), "ok")
        self.assertEqual(second.result(timeout=5), "ok")
        self.assertEqual(results["first"], results["second"])
        after = self._public_row_counts()
        self.assertEqual(
            self._changed_public_rows(before, after),
            {
                "core_commandintake": 1,
                "core_application": 1,
                "core_applicationlineage": 1,
                "core_applicationlineageevent": 1,
                "core_authoritydecision": 1,
                "core_organismcommandreceipt": 1,
            },
        )
        result = results["first"]
        self.assertEqual(
            Application.objects.filter(application_uuid=result.application_uuid).count(),
            1,
        )
        self.assertEqual(
            CommandIntake.objects.filter(
                actor_id=self.applicant.pk,
                idempotency_key=command.idempotency_key,
            ).count(),
            1,
        )
        self.assertEqual(
            OrganismCommandReceipt.objects.filter(
                actor_id=self.applicant.pk,
                idempotency_key=command.idempotency_key,
            ).count(),
            1,
        )

    def test_public_mutex_timeout_keeps_intake_and_same_time_retry_completes(self):
        command = self._submit(self.applicant, "mutex-timeout")
        before = self._public_row_counts()
        mutex_key = command_mutex_key(
            actor_identity_id=self.applicant.identity_id,
            action=OrganismAction.SUBMIT_APPLICATION,
            idempotency_key=command.idempotency_key,
        )
        holder_pid = backend_pid(connection)
        worker = None
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [mutex_key])

                def submit_with_timeout():
                    with connection.cursor() as worker_cursor:
                        worker_cursor.execute("SET lock_timeout = '2s'")
                    return self.service.submit_application(command)

                worker = start_worker(submit_with_timeout)
                wait_until_locked(
                    cursor,
                    worker.pid(),
                    holder_pid,
                    locktype="advisory",
                    timeout=10,
                )
                worker.thread.join(timeout=15)
                self.assertFalse(worker.thread.is_alive())
                contended = worker.result(timeout=5)
                self.assertIsInstance(contended, S015CommandContended)
                self.assertEqual(contended.diagnostic_sqlstate, "55P03")
                intake = CommandIntake.objects.get(
                    actor_id=self.applicant.pk,
                    idempotency_key=command.idempotency_key,
                )
                after_failure = self._public_row_counts()
                self.assertEqual(
                    self._changed_public_rows(before, after_failure),
                    {"core_commandintake": 1},
                )
        finally:
            if worker is not None:
                worker.thread.join(timeout=30)
                if worker.thread.is_alive():
                    cleanup_workers([worker])

        result = self.service.submit_application(command)
        after_retry = self._public_row_counts()
        self.assertEqual(
            self._changed_public_rows(after_failure, after_retry),
            {
                "core_application": 1,
                "core_applicationlineage": 1,
                "core_applicationlineageevent": 1,
                "core_authoritydecision": 1,
                "core_organismcommandreceipt": 1,
            },
        )
        self.assertEqual(
            CommandIntake.objects.get(pk=intake.pk).received_at,
            intake.received_at,
        )
        self.assertEqual(
            Application.objects.get(application_uuid=result.application_uuid).received_at,
            intake.received_at,
        )

    def test_public_statement_timeout_is_technical_and_retry_completes(self):
        command = self._submit(self.applicant, "statement-timeout")
        before = self._public_row_counts()
        acquire_mutex = application_service_module.acquire_organism_command_mutex

        def timeout_after_mutex(*args, **kwargs):
            result = acquire_mutex(*args, **kwargs)
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL statement_timeout = '25ms'")
                cursor.execute("SELECT pg_sleep(0.2)")
            return result

        with patch.object(
            application_service_module,
            "acquire_organism_command_mutex",
            side_effect=timeout_after_mutex,
        ):
            with self.assertRaises(OperationalError) as raised:
                self.service.submit_application(command)
        self.assertEqual(
            getattr(raised.exception.__cause__, "sqlstate", None),
            "57014",
        )
        after_failure = self._public_row_counts()
        self.assertEqual(
            self._changed_public_rows(before, after_failure),
            {"core_commandintake": 1},
        )
        intake = CommandIntake.objects.get(
            actor_id=self.applicant.pk,
            idempotency_key=command.idempotency_key,
        )

        result = self.service.submit_application(command)
        after_retry = self._public_row_counts()
        self.assertEqual(
            self._changed_public_rows(after_failure, after_retry),
            {
                "core_application": 1,
                "core_applicationlineage": 1,
                "core_applicationlineageevent": 1,
                "core_authoritydecision": 1,
                "core_organismcommandreceipt": 1,
            },
        )
        self.assertEqual(
            Application.objects.get(application_uuid=result.application_uuid).received_at,
            intake.received_at,
        )

    def test_public_intake_time_uses_database_clock_under_host_clock_skew(self):
        for offset, suffix in (
            (timedelta(hours=1), "host-ahead"),
            (-timedelta(hours=1), "host-behind"),
        ):
            with self.subTest(suffix=suffix):
                before = database_now()
                skewed_host_time = before + offset
                applicant = self._new_applicant(f"host-skew-{suffix}")
                service = OrganismApplicationService(
                    authority=S015Authority(provider=_Provider()),
                    clock=lambda skewed_host_time=skewed_host_time: skewed_host_time,
                )
                command = self._submit(applicant, suffix)
                result = service.submit_application(command)
                after = database_now()
                intake = CommandIntake.objects.get(
                    actor_id=applicant.pk,
                    idempotency_key=command.idempotency_key,
                )
                application = Application.objects.get(
                    application_uuid=result.application_uuid,
                )
                decision = AuthorityDecision.objects.get(
                    actor_id=applicant.pk,
                    action=OrganismAction.SUBMIT_APPLICATION.value,
                    idempotency_key=command.idempotency_key,
                )
                self.assertLessEqual(before, intake.received_at)
                self.assertLessEqual(intake.received_at, after)
                self.assertEqual(application.received_at, intake.received_at)
                self.assertEqual(decision.evaluated_at, intake.received_at)
                self.assertGreaterEqual(abs(skewed_host_time - intake.received_at), timedelta(minutes=59))

    def test_direct_mutex_holder_blocks_public_replay_independently_of_actor_lock(self):
        command = self._submit(self.applicant, "overlap-mutex")
        expected = self.service.submit_application(command)
        mutex_key = command_mutex_key(
            actor_identity_id=self.applicant.identity_id,
            action=OrganismAction.SUBMIT_APPLICATION,
            idempotency_key=command.idempotency_key,
        )
        entered = Event()
        release = Event()
        second_result = {}

        def hold_mutex_only():
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [mutex_key])
                entered.set()
                if not release.wait(timeout=15):
                    raise AssertionError("direct mutex holder timed out")

        holder = None
        waiter = None
        try:
            holder = start_worker(hold_mutex_only)
            self.assertTrue(entered.wait(timeout=15))

            def replay_with_timeout():
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '10s'")
                second_result["result"] = self.service.submit_application(command)

            waiter = start_worker(replay_with_timeout)
            with connection.cursor() as cursor:
                wait_until_locked(
                    cursor,
                    waiter.pid(),
                    holder.pid(),
                    locktype="advisory",
                    timeout=10,
                )
        finally:
            release.set()
            for worker in (holder, waiter):
                if worker is not None:
                    worker.thread.join(timeout=30)
                    if worker.thread.is_alive():
                        cleanup_workers([worker])
        self.assertIsNotNone(holder)
        self.assertIsNotNone(waiter)
        self.assertEqual(holder.result(timeout=5), "ok")
        self.assertEqual(waiter.result(timeout=5), "ok")
        self.assertEqual(second_result["result"], expected)

    def test_access_epoch_change_after_admission_does_not_execute_or_inherit(self):
        command = self._submit(self.applicant, "epoch-old")
        before = self._counts()
        public_before = self._public_row_counts()
        original = self.service._execute_submit_application

        def change_epoch_then_execute(bound_command, bound):
            worker = start_worker(
                lambda: Identity.objects.filter(pk=self.applicant.pk).update(
                    access_epoch=F("access_epoch") + 1
                )
            )
            worker.thread.join(timeout=15)
            if worker.thread.is_alive():
                cleanup_workers([worker])
                raise AssertionError("access-epoch update worker did not finish")
            self.assertEqual(worker.result(timeout=5), "ok")
            return original(bound_command, bound)

        with patch.object(
            self.service,
            "_execute_submit_application",
            side_effect=change_epoch_then_execute,
        ):
            with self.assertRaises(S015ApplicationAuthorityUnavailable):
                self.service.submit_application(command)

        old_intake = CommandIntake.objects.get(
            actor_id=self.applicant.pk,
            idempotency_key=command.idempotency_key,
        )
        after_epoch_failure = self._counts()
        public_after_failure = self._public_row_counts()
        self.assertEqual(
            self._changed_public_rows(public_before, public_after_failure),
            {"core_commandintake": 1},
        )
        self.assertEqual(
            after_epoch_failure["CommandIntake"],
            before["CommandIntake"] + 1,
        )
        for table in (
            "Application",
            "ApplicationLineage",
            "ApplicationLineageEvent",
            "AuthorityDecision",
            "OrganismCommandReceipt",
        ):
            self.assertEqual(after_epoch_failure[table], before[table], table)

        with self.assertRaises(S015CommandReplayMismatch):
            self.service.submit_application(command)
        self.assertEqual(self._public_row_counts(), public_after_failure)
        self.assertEqual(
            CommandIntake.objects.filter(
                actor_id=self.applicant.pk,
                idempotency_key=command.idempotency_key,
            ).count(),
            1,
        )
        new_command = self._submit(self.applicant, "epoch-new")
        result = self.service.submit_application(new_command)
        self.assertEqual(
            self._changed_public_rows(public_after_failure, self._public_row_counts()),
            {
                "core_commandintake": 1,
                "core_application": 1,
                "core_applicationlineage": 1,
                "core_applicationlineageevent": 1,
                "core_authoritydecision": 1,
                "core_organismcommandreceipt": 1,
            },
        )
        self.assertTrue(
            CommandIntake.objects.filter(
                actor_id=self.applicant.pk,
                idempotency_key=new_command.idempotency_key,
            ).exists()
        )
        self.assertTrue(
            Application.objects.filter(application_uuid=result.application_uuid).exists()
        )
        self.assertNotEqual(
            old_intake.actor_access_epoch,
            Identity.objects.get(pk=self.applicant.pk).access_epoch,
        )


@skipUnless(connection.vendor == "postgresql", "H100 intake witnesses require PostgreSQL")
class OldReceivedBindingAdmissionTests(TransactionTestCase):
    ISOLATION_FULL_CAPTURE = True

    def setUp(self):
        self.tag = f"h100-old-binding-{fresh()}"
        self.route = prepare_route(tag=self.tag)
        birth(self.route)
        self.now = route_test_now(self.route)
        with transaction.atomic(), connection.cursor() as cursor:
            identity_pk = seed_identity(cursor, f"h100-old-binding-applicant-{self.tag}")
        self.applicant = Identity.objects.select_related("credential").get(pk=identity_pk)
        provision = IdentityTransition.objects.create(
            identity=self.applicant,
            action=IdentityTransition.Action.PROVISION,
            prior_state=None,
            resulting_state="pending",
            authority_reference="h100:test:provision",
            technical_executor="H100_TEST",
            evidence_reference="h100:test:history",
            reason_category=IdentityTransition.ReasonCategory.PROVISIONING,
            correlation_id=uuid4(),
            occurred_at=self.now,
        )
        IdentityTransition.objects.create(
            identity=self.applicant,
            action=IdentityTransition.Action.ACTIVATE,
            prior_state="pending",
            resulting_state="active",
            authority_reference="h100:test:activate",
            technical_executor="H100_TEST",
            evidence_reference="h100:test:history",
            reason_category=IdentityTransition.ReasonCategory.ACCESS_REVIEW,
            correlation_id=uuid4(),
            occurred_at=self.now,
            previous_transition=provision,
        )
        self.service = OrganismApplicationService(
            authority=S015Authority(provider=_Provider()),
            clock=database_now,
        )

    def test_public_execution_and_replay_preserve_an_old_binding_time(self):
        self.assertTrue(connection.get_autocommit())
        latest_event = LivingOrganismEvent.objects.filter(
            living_organism=self.route.organism,
        ).order_by("-sequence").first()
        if latest_event is None:
            self.fail("the prepared route has no recorded event")
        # All route and applicant prerequisites are committed before this cut.
        old_received_at = database_now()
        self.assertLessEqual(self.now, old_received_at)
        self.assertLessEqual(latest_event.recorded_at, old_received_at)
        self.assertTrue(IdentityTransition.objects.filter(
            identity=self.applicant,
            action=IdentityTransition.Action.ACTIVATE,
            occurred_at__lte=old_received_at,
        ).exists())
        command = SubmitApplicationCommand(
            credential=self.applicant.credential,
            living_organism_id=self.route.organism.organism_id,
            request_reference=f"h100:old-binding:{self.tag}",
            idempotency_key=f"h100:old-binding:key:{self.tag}",
            occurred_at=old_received_at,
            effective_at=old_received_at,
            answer="An old-time fixture answer",
        )
        entry = IntakeEntry.SUBMIT_APPLICATION
        shape = application_shape(entry, command)
        action = ENTRY_ACTIONS[entry]
        if action is None:
            self.fail("submission intake has no action")
        fingerprint = intake_fingerprint(
            entry=entry,
            action=action,
            actor_identity_id=self.applicant.identity_id,
            actor_access_epoch=self.applicant.access_epoch,
            request_reference=shape.request_reference,
            idempotency_key=shape.idempotency_key,
            target=shape.target,
            command=shape.command,
            omitted=shape.omitted,
        )

        trigger_disabled = False
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "ALTER TABLE public.core_commandintake "
                    "DISABLE TRIGGER s015_h100_command_intake_assign_time"
                )
                trigger_disabled = True
            CommandIntake.objects.create(
                actor=self.applicant,
                actor_identity_id=self.applicant.identity_id,
                actor_access_epoch=self.applicant.access_epoch,
                entry=entry.value,
                action=action,
                idempotency_key=shape.idempotency_key,
                request_reference=shape.request_reference,
                request_fingerprint=fingerprint,
                fingerprint_schema=FINGERPRINT_SCHEMA,
                target_organism_id=shape.target_organism_id,
                received_at=old_received_at,
            )
        finally:
            if trigger_disabled:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "ALTER TABLE public.core_commandintake "
                        "ENABLE TRIGGER s015_h100_command_intake_assign_time"
                    )

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT tgenabled FROM pg_catalog.pg_trigger "
                "WHERE tgrelid = 'public.core_commandintake'::regclass "
                "AND tgname = 's015_h100_command_intake_assign_time'"
            )
            self.assertEqual(cursor.fetchone()[0], "O")

        binding_before = CommandIntake.objects.get(
            actor_id=self.applicant.pk,
            entry=entry.value,
            action=action,
            idempotency_key=command.idempotency_key,
        )
        binding_values = (binding_before.pk, binding_before.received_at,
                          binding_before.request_fingerprint)
        self.assertLess(old_received_at, database_now())
        first = self.service.submit_application(command)
        after_first = ApplicationIntakeFlowTests._public_row_counts()
        replay = self.service.submit_application(command)
        self.assertEqual(
            ApplicationIntakeFlowTests._public_row_counts(), after_first,
        )
        binding_before.refresh_from_db()
        self.assertEqual(
            (binding_before.pk, binding_before.received_at,
             binding_before.request_fingerprint),
            binding_values,
        )
        self.assertEqual(binding_before.request_fingerprint, fingerprint)
        intake = CommandIntake.objects.get(
            actor_id=self.applicant.pk,
            idempotency_key=command.idempotency_key,
        )
        application = Application.objects.get(application_uuid=first.application_uuid)
        decision = AuthorityDecision.objects.get(
            actor_id=self.applicant.pk,
            action=OrganismAction.SUBMIT_APPLICATION.value,
            idempotency_key=command.idempotency_key,
        )
        self.assertEqual(first, replay)
        self.assertEqual(intake.received_at, old_received_at)
        self.assertEqual(application.received_at, old_received_at)
        self.assertEqual(decision.evaluated_at, old_received_at)
        self.assertLess(old_received_at, database_now())
