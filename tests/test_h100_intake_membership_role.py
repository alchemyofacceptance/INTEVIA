"""H100 C2b membership, role and consent intake witnesses."""

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from django.contrib.auth.models import User
from django.db import connection, transaction
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext

from core.models import AuthorityDecision, CommandIntake, Identity, IntakeEntry, OrganismCommandReceipt
from src.intevia.services.command_intake import (
    ACTIVE_ENTRIES,
    ACTIVATION_GROUPS,
    ActorRef,
    BoundRequest,
    IntakeAdmissionTechnicalFailure,
    S015CommandAtomicityRequired,
    S015CommandReplayMismatch,
    _resolve_actor,
    admit_request,
    intake_shape,
)
from src.intevia.services import command_intake as command_intake_module
from src.intevia.services.organism_application_service import SubmitApplicationCommand
from src.intevia.services.organism_authority import S015Authority
from src.intevia.services.organism_membership_service import (
    OrganismMembershipService,
    OpenMembershipCommand,
    S015MembershipAuthorityUnavailable,
    TransitionMembershipCommand,
)
from src.intevia.services.organism_office_consent_service import (
    OfficeAppointmentConsentService,
    RecordOfficeConsentCommand,
    S015OfficeConsentAuthorityUnavailable,
)
from src.intevia.services.organism_role_service import (
    OrganismRoleService,
    ProposeAssignmentCommand,
    S015AssignmentAuthorityUnavailable,
    TransitionAssignmentCommand,
)


UTC = timezone.utc
T0 = datetime(2026, 9, 29, 12, tzinfo=UTC)


def _commands(credential: User) -> dict[IntakeEntry, object]:
    return {
        IntakeEntry.OPEN_MEMBERSHIP: OpenMembershipCommand(
            credential=credential,
            request_reference="h100:c2b:membership-open",
            idempotency_key="h100:c2b:membership-open",
            occurred_at=T0,
            effective_at=T0,
            living_organism_id=uuid4(),
            member_identity_id=uuid4(),
            reason_class="TEST_ONLY_REASON",
        ),
        IntakeEntry.TRANSITION_MEMBERSHIP: TransitionMembershipCommand(
            credential=credential,
            request_reference="h100:c2b:membership-transition",
            idempotency_key="h100:c2b:membership-transition",
            occurred_at=T0,
            effective_at=T0,
            membership_uuid=uuid4(),
            resulting_state="ENDED",
            reason_class="TEST_ONLY_REASON",
        ),
        IntakeEntry.PROPOSE_ASSIGNMENT: ProposeAssignmentCommand(
            credential=credential,
            request_reference="h100:c2b:assignment-propose",
            idempotency_key="h100:c2b:assignment-propose",
            occurred_at=T0,
            effective_at=T0,
            membership_uuid=uuid4(),
            role_definition_uuid=uuid4(),
            effective_until_state="NONE",
            effective_until=None,
        ),
        IntakeEntry.TRANSITION_ASSIGNMENT: TransitionAssignmentCommand(
            credential=credential,
            request_reference="h100:c2b:assignment-transition",
            idempotency_key="h100:c2b:assignment-transition",
            occurred_at=T0,
            effective_at=T0,
            assignment_uuid=uuid4(),
            action="END",
            resulting_state="ENDED",
        ),
        IntakeEntry.RECORD_OFFICE_CONSENT: RecordOfficeConsentCommand(
            credential=credential,
            request_reference="h100:c2b:consent",
            idempotency_key="h100:c2b:consent",
            occurred_at=T0,
            effective_at=T0,
            assignment_uuid=uuid4(),
            proposal_event_uuid=uuid4(),
            action="CONSENT",
        ),
    }


def _public_table_counts() -> dict[str, int]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT c.relname FROM pg_catalog.pg_class c "
            "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') "
            "ORDER BY c.relname"
        )
        tables = [row[0] for row in cursor.fetchall()]
        counts = {}
        for table in tables:
            cursor.execute(
                f"SELECT count(*) FROM public.{connection.ops.quote_name(table)}"
            )
            counts[table] = cursor.fetchone()[0]
    return counts


def _public_table_delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    return {
        table: after[table] - count
        for table, count in before.items()
        if after[table] != count
    }


class MembershipRoleIntakeBoundaryTests(TransactionTestCase):
    def setUp(self):
        self.credential = User(pk=71, username="h100-c2b-boundary")
        self.commands = _commands(self.credential)
        from tests.s015_0021_support import database_now

        self.provider = Mock()
        authority = S015Authority(provider=self.provider)
        self.services = {
            IntakeEntry.OPEN_MEMBERSHIP: OrganismMembershipService(authority=authority, clock=database_now),
            IntakeEntry.TRANSITION_MEMBERSHIP: OrganismMembershipService(authority=authority, clock=database_now),
            IntakeEntry.PROPOSE_ASSIGNMENT: OrganismRoleService(authority=authority, clock=database_now),
            IntakeEntry.TRANSITION_ASSIGNMENT: OrganismRoleService(authority=authority, clock=database_now),
            IntakeEntry.RECORD_OFFICE_CONSENT: OfficeAppointmentConsentService(
                authority=authority,
                clock=database_now,
            ),
        }
        self.methods = {
            IntakeEntry.OPEN_MEMBERSHIP: "open_membership",
            IntakeEntry.TRANSITION_MEMBERSHIP: "transition_membership",
            IntakeEntry.PROPOSE_ASSIGNMENT: "propose_assignment",
            IntakeEntry.TRANSITION_ASSIGNMENT: "transition_assignment",
            IntakeEntry.RECORD_OFFICE_CONSENT: "record_office_consent",
        }

    def test_activation_adds_membership_and_role_groups_and_consent(self):
        self.assertEqual(
            ACTIVE_ENTRIES,
            frozenset(
                {
                    IntakeEntry.SUBMIT_APPLICATION,
                    IntakeEntry.WITHDRAW_APPLICATION,
                    IntakeEntry.CLAIM_APPLICATION,
                    IntakeEntry.REASSIGN_APPLICATION,
                    IntakeEntry.DECIDE_APPLICATION,
                    *self.commands,
                }
            ),
        )
        self.assertEqual(
            ACTIVATION_GROUPS["G4"],
            frozenset(
                {
                    IntakeEntry.OPEN_MEMBERSHIP,
                    IntakeEntry.TRANSITION_MEMBERSHIP,
                }
            ),
        )
        self.assertEqual(
            ACTIVATION_GROUPS["G5"],
            frozenset(
                {
                    IntakeEntry.PROPOSE_ASSIGNMENT,
                    IntakeEntry.TRANSITION_ASSIGNMENT,
                }
            ),
        )
        self.assertNotIn(IntakeEntry.RECORD_OFFICE_CONSENT, ACTIVATION_GROUPS["G4"])
        self.assertNotIn(IntakeEntry.RECORD_OFFICE_CONSENT, ACTIVATION_GROUPS["G5"])

    def test_ch9_activation_expands_membership_role_and_consent_namespaces(self):
        from tests.s015_0021_support import database_now, register_test_learner

        learner = register_test_learner(
            username=f"h100-c2b-ch9-{uuid4().hex}",
            display_name="H100 C2b CH-9",
        )
        actor = _resolve_actor("default", learner.credential)
        tag = uuid4().hex
        bound_entry = IntakeEntry.OPEN_MEMBERSHIP
        bound_command = replace(
            _commands(learner.credential)[bound_entry],
            request_reference=f"h100-c2b-ch9-bound:{tag}",
            idempotency_key=f"h100-c2b-ch9-bound:{tag}",
        )

        self.assertEqual(
            command_intake_module._activation_entries((IntakeEntry.OPEN_MEMBERSHIP,)),
            frozenset(
                {
                    IntakeEntry.OPEN_MEMBERSHIP,
                    IntakeEntry.TRANSITION_MEMBERSHIP,
                }
            ),
        )
        self.assertEqual(
            command_intake_module._activation_entries(
                (IntakeEntry.PROPOSE_ASSIGNMENT,)
            ),
            frozenset(
                {
                    IntakeEntry.PROPOSE_ASSIGNMENT,
                    IntakeEntry.TRANSITION_ASSIGNMENT,
                }
            ),
        )
        self.assertEqual(
            command_intake_module._activation_entries(
                (IntakeEntry.RECORD_OFFICE_CONSENT,)
            ),
            frozenset({IntakeEntry.RECORD_OFFICE_CONSENT}),
        )

        admit_request(
            "default",
            bound_entry,
            actor,
            intake_shape(bound_entry, bound_command),
        )

        def seed_receipt(action, key):
            receipt_uuid = uuid4()
            receipt = OrganismCommandReceipt(
                receipt_uuid=receipt_uuid,
                actor_id=actor.pk,
                action=action,
                idempotency_key=key,
                request_reference=f"request:{receipt_uuid.hex}",
                payload_fingerprint="b" * 64,
                authority_decision_reference="s015d1:" + "c" * 64,
                lineage_reference="s015l1:"
                + hashlib.sha256(
                    f"h100-c2b-ch9:{receipt_uuid}".encode("ascii")
                ).hexdigest(),
                result_payload={"fixture": True},
                completed_at=database_now(),
            )
            receipt.full_clean()
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO public.core_organismcommandreceipt (
                        receipt_uuid, actor_id, action, idempotency_key,
                        request_reference, payload_fingerprint,
                        authority_decision_reference, lineage_reference,
                        result_payload, completed_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                    """,
                    [
                        receipt.receipt_uuid,
                        receipt.actor_id,
                        receipt.action,
                        receipt.idempotency_key,
                        receipt.request_reference,
                        receipt.payload_fingerprint,
                        receipt.authority_decision_reference,
                        receipt.lineage_reference,
                        json.dumps(receipt.result_payload),
                        receipt.completed_at,
                    ],
                )

        bound_action = command_intake_module.ENTRY_ACTIONS[bound_entry]
        seed_receipt(bound_action, bound_command.idempotency_key)
        unbound_specs = (
            (IntakeEntry.OPEN_MEMBERSHIP, f"h100-c2b-ch9-open:{tag}"),
            (IntakeEntry.PROPOSE_ASSIGNMENT, f"h100-c2b-ch9-propose:{tag}"),
            (IntakeEntry.RECORD_OFFICE_CONSENT, f"h100-c2b-ch9-consent:{tag}"),
        )
        for entry, key in unbound_specs:
            action = command_intake_module.ENTRY_ACTIONS[entry]
            self.assertFalse(
                CommandIntake.objects.filter(
                    actor_id=actor.pk,
                    action=action,
                    idempotency_key=key,
                ).exists()
            )
            seed_receipt(action, key)

        entries = (
            IntakeEntry.OPEN_MEMBERSHIP,
            IntakeEntry.TRANSITION_MEMBERSHIP,
            IntakeEntry.PROPOSE_ASSIGNMENT,
            IntakeEntry.TRANSITION_ASSIGNMENT,
            IntakeEntry.RECORD_OFFICE_CONSENT,
        )
        records = command_intake_module.run_activation_step("default", entries)
        observed = {
            (record.kind, record.action, record.idempotency_key, record.actor_id)
            for record in records
        }
        expected = {
            ("receipt", command_intake_module.ENTRY_ACTIONS[entry], key, actor.pk)
            for entry, key in unbound_specs
        }
        self.assertEqual(observed, expected)
        self.assertNotIn(
            (
                "receipt",
                bound_action,
                bound_command.idempotency_key,
                actor.pk,
            ),
            observed,
        )

    def test_each_public_entry_refuses_an_enclosing_transaction_before_sql(self):
        for entry, command in self.commands.items():
            with self.subTest(entry=entry), transaction.atomic():
                with CaptureQueriesContext(connection) as queries:
                    with self.assertRaises(S015CommandAtomicityRequired) as raised:
                        getattr(self.services[entry], self.methods[entry])(command)
                self.assertIsNone(
                    getattr(raised.exception, "refusal_code", None),
                    entry,
                )
                self.assertEqual(len(queries), 0)

    def test_each_public_entry_refuses_manual_transaction_before_sql(self):
        connection.set_autocommit(False)
        try:
            for entry, command in self.commands.items():
                with self.subTest(entry=entry), CaptureQueriesContext(connection) as queries:
                    with self.assertRaises(S015CommandAtomicityRequired) as raised:
                        getattr(self.services[entry], self.methods[entry])(command)
                self.assertIsNone(
                    getattr(raised.exception, "refusal_code", None),
                    entry,
                )
                self.assertEqual(len(queries), 0)
                self.assertFalse(connection.in_atomic_block)
                self.assertFalse(connection.get_autocommit())
        finally:
            connection.rollback()
            connection.set_autocommit(True)

    def test_access_epoch_refusal_uses_the_command_family(self):
        expected = {
            IntakeEntry.OPEN_MEMBERSHIP: S015MembershipAuthorityUnavailable,
            IntakeEntry.TRANSITION_MEMBERSHIP: S015MembershipAuthorityUnavailable,
            IntakeEntry.PROPOSE_ASSIGNMENT: S015AssignmentAuthorityUnavailable,
            IntakeEntry.TRANSITION_ASSIGNMENT: S015AssignmentAuthorityUnavailable,
            IntakeEntry.RECORD_OFFICE_CONSENT: S015OfficeConsentAuthorityUnavailable,
        }
        actor = ActorRef(pk=71, identity_id=uuid4(), access_epoch=3)
        for entry, command in self.commands.items():
            bound = BoundRequest.for_test(
                entry,
                command,
                received_at=T0 + timedelta(seconds=1),
                actor=actor,
            )
            changed_actor = SimpleNamespace(
                pk=actor.pk,
                identity_id=actor.identity_id,
                access_epoch=actor.access_epoch + 1,
            )
            with self.subTest(entry=entry), CaptureQueriesContext(connection) as queries:
                with self.assertRaises(expected[entry]) as raised:
                    bound.verify(command, changed_actor, entry, "default")
            self.assertIs(type(raised.exception), expected[entry])
            self.assertEqual(len(queries), 0)
            self.assertIsNone(bound.intake_id)
            self.assertEqual(bound.received_at, T0 + timedelta(seconds=1))

    def test_test_bindings_reject_parent_for_each_family(self):
        actor = ActorRef(pk=71, identity_id=uuid4(), access_epoch=3)
        application_command = SubmitApplicationCommand(
            credential=self.credential,
            living_organism_id=uuid4(),
            request_reference="h100:c2b:parent",
            idempotency_key="h100:c2b:parent",
            occurred_at=T0,
            effective_at=T0,
            answer="test",
        )
        parent = BoundRequest.for_test(
            IntakeEntry.SUBMIT_APPLICATION,
            application_command,
            received_at=T0 + timedelta(seconds=1),
            actor=actor,
        )
        matching_actor = SimpleNamespace(
            pk=actor.pk,
            identity_id=actor.identity_id,
            access_epoch=actor.access_epoch,
        )
        for entry, command in self.commands.items():
            request = BoundRequest.for_test(
                entry,
                command,
                received_at=T0 + timedelta(seconds=1),
                actor=actor,
            )
            request_with_parent = replace(
                request,
                parent=parent,
                _sentinel=command_intake_module._CONSTRUCTOR_SENTINEL,
            )
            with self.subTest(entry=entry), self.assertRaises(
                IntakeAdmissionTechnicalFailure
            ):
                request_with_parent.verify(command, matching_actor, entry, "default")

    def test_test_binding_gate_applies_to_each_family(self):
        actor = ActorRef(pk=71, identity_id=uuid4(), access_epoch=3)
        with override_settings(INTEVIA_TEST_CONTEXT=False):
            for entry, command in self.commands.items():
                with self.subTest(entry=entry), self.assertRaises(
                    IntakeAdmissionTechnicalFailure
                ):
                    BoundRequest.for_test(
                        entry,
                        command,
                        received_at=T0 + timedelta(seconds=1),
                        actor=actor,
                    )

    def test_epoch_refusal_preserves_each_committed_binding_without_qualification(self):
        """Verifier-level witness; this does not establish V5 executor ordering."""
        from tests.s015_0021_support import seed_identity

        with transaction.atomic(), connection.cursor() as cursor:
            identity_pk = seed_identity(cursor, "h100-c2b-epoch-dispatch")
        identity = Identity.objects.select_related("credential").get(pk=identity_pk)
        commands = _commands(identity.credential)
        actor = _resolve_actor("default", identity.credential)
        decisions_before = AuthorityDecision.objects.count()
        receipts_before = OrganismCommandReceipt.objects.count()
        for entry, command in commands.items():
            bound = admit_request("default", entry, actor, intake_shape(entry, command))
            binding_before = CommandIntake.objects.filter(pk=bound.intake_id).values().get()
            bound.verify(command, identity, entry, "default")
            changed_actor = SimpleNamespace(
                pk=actor.pk,
                identity_id=actor.identity_id,
                access_epoch=actor.access_epoch + 1,
            )
            with self.subTest(entry=entry):
                with self.assertRaises(
                    {
                        IntakeEntry.OPEN_MEMBERSHIP: S015MembershipAuthorityUnavailable,
                        IntakeEntry.TRANSITION_MEMBERSHIP: S015MembershipAuthorityUnavailable,
                        IntakeEntry.PROPOSE_ASSIGNMENT: S015AssignmentAuthorityUnavailable,
                        IntakeEntry.TRANSITION_ASSIGNMENT: S015AssignmentAuthorityUnavailable,
                        IntakeEntry.RECORD_OFFICE_CONSENT: S015OfficeConsentAuthorityUnavailable,
                    }[entry]
                ):
                    bound.verify(command, changed_actor, entry, "default")
                self.assertEqual(
                    CommandIntake.objects.filter(pk=bound.intake_id).values().get(),
                    binding_before,
                )
        self.assertEqual(AuthorityDecision.objects.count(), decisions_before)
        self.assertEqual(OrganismCommandReceipt.objects.count(), receipts_before)
        self.assertEqual(self.provider.mock_calls, [])

    def test_public_shape_refusal_does_not_create_an_intake(self):
        for entry, command in self.commands.items():
            invalid = replace(command, received_at=T0 + timedelta(seconds=1))
            service = self.services[entry]
            method = getattr(service, self.methods[entry])
            with self.subTest(entry=entry), CaptureQueriesContext(connection) as queries:
                with self.assertRaisesRegex(ValueError, "received_at is assigned"):
                    method(invalid)
            self.assertEqual(len(queries), 0)

    def test_consent_exact_type_refusal_keeps_base_message_before_admission(self):
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class DerivedConsentCommand(RecordOfficeConsentCommand):
            pass

        command = self.commands[IntakeEntry.RECORD_OFFICE_CONSENT]
        invalid = DerivedConsentCommand(
            credential=command.credential,
            request_reference=command.request_reference,
            idempotency_key=command.idempotency_key,
            occurred_at=command.occurred_at,
            effective_at=command.effective_at,
            assignment_uuid=command.assignment_uuid,
            proposal_event_uuid=command.proposal_event_uuid,
            action=command.action,
        )
        with CaptureQueriesContext(connection) as queries:
            with self.assertRaises(TypeError) as raised:
                self.services[IntakeEntry.RECORD_OFFICE_CONSENT].record_office_consent(
                    invalid
                )
        self.assertEqual(
            str(raised.exception),
            "command must be a RecordOfficeConsentCommand",
        )
        self.assertEqual(len(queries), 0)


class MembershipRoleApplicationOrderingTests(TransactionTestCase):
    def test_iw_f6_withdrawal_admitted_first_loses_to_claim_locked_first(self):
        from threading import Event
        from unittest.mock import patch

        from core.models import (
            Application,
            ApplicationDecision,
            ApplicationEngagement,
            ApplicationLineage,
            ApplicationLineageEvent,
            ApplicationWithdrawal,
            AuthorityDecision,
            CSAAssignment,
            LOApplicationSettingsVersion,
            OrganismCommandReceipt,
        )
        from core.organism_contract import OrganismAction
        from src.intevia.services import (
            organism_application_service as application_service_module,
        )
        from src.intevia.services.organism_application_service import (
            ClaimApplicationCommand,
            OrganismApplicationService,
            S015OperationNotYetImplemented,
            SubmitApplicationCommand,
            WithdrawApplicationCommand,
        )
        from src.intevia.services.organism_application_authority import (
            ProductionApplicationAuthorityProvider,
        )
        from tests.s015_0021_support import database_now, fresh, register_test_learner
        from tests.s015_concurrency_support import cleanup_workers, start_worker
        from tests.sb2_route_support import birth, prepare_route

        if connection.vendor != "postgresql":
            self.skipTest("IW-F6 requires PostgreSQL")
        tag = f"h100-iw-f6-{fresh()}"
        route = prepare_route(tag=tag)
        birth(route)
        settings = LOApplicationSettingsVersion.objects.get(
            living_organism=route.organism,
            entitlement_choice__isnull=True,
            entitlement_election_route__isnull=True,
        )
        self.assertEqual(settings.processing_responsibility, "LO")
        self.assertFalse(settings.automatic_acceptance)
        service = OrganismApplicationService(
            authority=S015Authority(provider=ProductionApplicationAuthorityProvider()),
            clock=database_now,
        )

        def new_applicant(suffix):
            return register_test_learner(
                username=f"{tag}-{suffix}",
                display_name=f"IW-F6 {suffix}",
            )

        def public_row_counts():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT c.relname FROM pg_catalog.pg_class c "
                    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') "
                    "ORDER BY c.relname"
                )
                tables = [row[0] for row in cursor.fetchall()]
                counts = {}
                for table in tables:
                    cursor.execute(
                        f"SELECT count(*) FROM public.{connection.ops.quote_name(table)}"
                    )
                    counts[table] = cursor.fetchone()[0]
            return counts

        def changed_rows(before, after):
            return {
                table: after[table] - count
                for table, count in before.items()
                if after[table] != count
            }

        def submit(applicant, suffix):
            submitted_at = database_now()
            return service.submit_application(SubmitApplicationCommand(
                credential=applicant.credential,
                living_organism_id=route.organism.organism_id,
                request_reference=f"{tag}:submit:{suffix}",
                idempotency_key=f"{tag}:submit:key:{suffix}",
                occurred_at=submitted_at,
                effective_at=submitted_at,
                answer=f"IW-F6 application {suffix}",
            ))

        applicant = new_applicant("applicant-a")
        control_applicant = new_applicant("applicant-control")
        submission = submit(applicant, "a")
        application = Application.objects.get(application_uuid=submission.application_uuid)
        submission_intake = CommandIntake.objects.get(
            actor_id=applicant.pk,
            idempotency_key=f"{tag}:submit:key:a",
        )
        self.assertFalse(ApplicationDecision.objects.filter(application=application).exists())
        self.assertFalse(ApplicationWithdrawal.objects.filter(application=application).exists())
        self.assertFalse(CSAAssignment.objects.filter(application=application).exists())
        self.assertFalse(
            ApplicationEngagement.objects.filter(
                application=application,
                route__in=("LO_CQ", "CIRCLE_CQ"),
            ).exists()
        )
        shared_at = database_now()
        self.assertGreaterEqual(shared_at, submission_intake.received_at)
        before = public_row_counts()

        withdrawal = WithdrawApplicationCommand(
            credential=applicant.credential,
            application_uuid=application.application_uuid,
            request_reference=f"{tag}:withdraw:a",
            idempotency_key=f"{tag}:withdraw:key:a",
            occurred_at=shared_at,
            effective_at=shared_at,
        )
        claim = ClaimApplicationCommand(
            credential=route.founder.credential,
            application_uuid=application.application_uuid,
            request_reference=f"{tag}:claim:a",
            idempotency_key=f"{tag}:claim:key:a",
            occurred_at=shared_at,
            effective_at=shared_at,
        )
        admitted = Event()
        release_withdrawal = Event()
        withdrawal_received_at = []
        withdrawal_transaction_state = []
        actual_executor = service._execute_withdraw_application
        actual_append = application_service_module._append_application_event
        withdrawal_append_calls = []

        def pause_after_admission(command, bound):
            if command.idempotency_key == withdrawal.idempotency_key:
                withdrawal_received_at.append(bound.received_at)
                withdrawal_transaction_state.append(
                    (connection.in_atomic_block, connection.get_autocommit())
                )
                admitted.set()
                if not release_withdrawal.wait(timeout=20):
                    raise AssertionError("IW-F6 withdrawal release timed out")
            return actual_executor(command, bound)

        def observe_real_append(*args, **kwargs):
            if kwargs.get("idempotency_key") == withdrawal.idempotency_key:
                withdrawal_append_calls.append(kwargs)
            return actual_append(*args, **kwargs)

        worker = None
        claim_result = None
        withdrawal_outcome = None
        try:
            with patch.object(
                service,
                "_execute_withdraw_application",
                side_effect=pause_after_admission,
            ), patch.object(
                application_service_module,
                "_append_application_event",
                side_effect=observe_real_append,
            ):
                worker = start_worker(lambda: service.withdraw_application(withdrawal))
                self.assertTrue(admitted.wait(timeout=15), "withdrawal was not admitted")
                self.assertEqual(len(withdrawal_received_at), 1)
                self.assertEqual(withdrawal_transaction_state[0], (False, True))
                first_intake_at = withdrawal_received_at[0]
                admitted_intake = CommandIntake.objects.get(
                    actor_id=applicant.pk,
                    idempotency_key=withdrawal.idempotency_key,
                )
                self.assertEqual(admitted_intake.received_at, first_intake_at)
                during_a = public_row_counts()
                self.assertEqual(
                    changed_rows(before, during_a),
                    {CommandIntake._meta.db_table: 1},
                )

                claim_result = service.claim_application(claim)
                claim_intake = CommandIntake.objects.get(
                    actor_id=route.founder.pk,
                    idempotency_key=claim.idempotency_key,
                )
                self.assertGreater(claim_intake.received_at, first_intake_at)
                self.assertEqual(claim_result.action, OrganismAction.CLAIM_APPLICATION)
                lineage = ApplicationLineage.objects.get(application=application)
                head = ApplicationLineageEvent.objects.get(pk=lineage.head_event_id)
                claim_lineage_identity = (lineage.pk, lineage.lineage_uuid)
                claim_head_identity = (head.pk, head.event_uuid)
                withdrawal_intake_snapshot = CommandIntake.objects.filter(
                    actor_id=applicant.pk,
                    idempotency_key=withdrawal.idempotency_key,
                ).values().get()
                self.assertEqual(head.action, OrganismAction.CLAIM_APPLICATION.value)
                self.assertEqual(head.received_at, claim_intake.received_at)
                self.assertEqual(head.effective_at, shared_at)
                self.assertEqual(head.occurred_at, shared_at)
                release_withdrawal.set()
                worker.thread.join(timeout=30)
                self.assertFalse(
                    worker.thread.is_alive(),
                    "IW-F6 withdrawal did not finish",
                )

                def assert_claim_winner_persisted():
                    lineage.refresh_from_db()
                    self.assertEqual(
                        (lineage.pk, lineage.lineage_uuid),
                        claim_lineage_identity,
                    )
                    self.assertEqual(lineage.head_event_id, claim_head_identity[0])
                    current_head = ApplicationLineageEvent.objects.get(
                        pk=lineage.head_event_id
                    )
                    self.assertEqual(
                        (current_head.pk, current_head.event_uuid),
                        claim_head_identity,
                    )
                    self.assertEqual(
                        current_head.action,
                        OrganismAction.CLAIM_APPLICATION.value,
                    )
                    self.assertEqual(current_head.resulting_state, "IN_REVIEW")

                    claim_action = OrganismAction.CLAIM_APPLICATION.value
                    decision = AuthorityDecision.objects.get(
                        actor_id=route.founder.pk,
                        action=claim_action,
                        idempotency_key=claim.idempotency_key,
                    )
                    self.assertEqual(decision.outcome, "QUALIFIED")
                    receipt = OrganismCommandReceipt.objects.get(
                        actor_id=route.founder.pk,
                        action=claim_action,
                        idempotency_key=claim.idempotency_key,
                    )
                    self.assertEqual(decision.actor_id, route.founder.pk)
                    self.assertEqual(decision.action, claim_action)
                    self.assertEqual(
                        decision.idempotency_key,
                        claim.idempotency_key,
                    )
                    self.assertEqual(receipt.actor_id, route.founder.pk)
                    self.assertEqual(receipt.action, claim_action)
                    self.assertEqual(
                        receipt.idempotency_key,
                        claim.idempotency_key,
                    )

                    withdrawal_action = OrganismAction.WITHDRAW_APPLICATION.value
                    self.assertFalse(
                        AuthorityDecision.objects.filter(
                            actor_id=applicant.pk,
                            action=withdrawal_action,
                            idempotency_key=withdrawal.idempotency_key,
                        ).exists()
                    )
                    self.assertFalse(
                        OrganismCommandReceipt.objects.filter(
                            actor_id=applicant.pk,
                            action=withdrawal_action,
                            idempotency_key=withdrawal.idempotency_key,
                        ).exists()
                    )
                    assignment = CSAAssignment.objects.get(application=application)
                    engagement = ApplicationEngagement.objects.get(
                        application=application
                    )
                    self.assertEqual(engagement.route, "LO_CSA")
                    self.assertEqual(
                        engagement.engagement_reference,
                        str(assignment.assignment_uuid),
                    )
                    self.assertEqual(
                        CommandIntake.objects.filter(
                            actor_id=applicant.pk,
                            idempotency_key=withdrawal.idempotency_key,
                        ).values().get(),
                        withdrawal_intake_snapshot,
                    )

        finally:
            release_withdrawal.set()
            if worker is not None:
                worker.thread.join(timeout=30)
                if worker.thread.is_alive():
                    cleanup_workers([worker])

        self.assertIsNotNone(worker)
        withdrawal_outcome = worker.result(timeout=5)
        self.assertIsInstance(withdrawal_outcome, S015OperationNotYetImplemented)
        self.assertEqual(
            (
                withdrawal_outcome.operation,
                withdrawal_outcome.limit_id,
                withdrawal_outcome.owning_slice,
            ),
            ("out_of_order_application_act", "IL-3", "S5"),
        )
        self.assertEqual(len(withdrawal_append_calls), 1)
        assert_claim_winner_persisted()
        after = public_row_counts()
        self.assertEqual(
            changed_rows(before, after),
            {
                CommandIntake._meta.db_table: 2,
                CSAAssignment._meta.db_table: 1,
                ApplicationEngagement._meta.db_table: 1,
                ApplicationLineageEvent._meta.db_table: 1,
                AuthorityDecision._meta.db_table: 1,
                OrganismCommandReceipt._meta.db_table: 1,
            },
        )
        self.assertFalse(
            ApplicationWithdrawal.objects.filter(application=application).exists()
        )
        replay_before = public_row_counts()
        with self.assertRaises(S015OperationNotYetImplemented) as retry_raised:
            service.withdraw_application(withdrawal)
        self.assertEqual(
            (
                retry_raised.exception.operation,
                retry_raised.exception.limit_id,
                retry_raised.exception.owning_slice,
            ),
            (
                withdrawal_outcome.operation,
                withdrawal_outcome.limit_id,
                withdrawal_outcome.owning_slice,
            ),
        )
        self.assertEqual(public_row_counts(), replay_before)
        assert_claim_winner_persisted()

        control_submission = submit(control_applicant, "ordered-control")
        control_application = Application.objects.get(
            application_uuid=control_submission.application_uuid,
        )
        control_claim_time = database_now()
        service.claim_application(ClaimApplicationCommand(
            credential=route.founder.credential,
            application_uuid=control_application.application_uuid,
            request_reference=f"{tag}:claim:ordered-control",
            idempotency_key=f"{tag}:claim:key:ordered-control",
            occurred_at=control_claim_time,
            effective_at=control_claim_time,
        ))
        control_withdrawal_time = database_now()
        service.withdraw_application(WithdrawApplicationCommand(
            credential=control_applicant.credential,
            application_uuid=control_application.application_uuid,
            request_reference=f"{tag}:withdraw:ordered-control",
            idempotency_key=f"{tag}:withdraw:key:ordered-control",
            occurred_at=control_withdrawal_time,
            effective_at=control_withdrawal_time,
        ))
        self.assertTrue(
            ApplicationWithdrawal.objects.filter(
                application=control_application,
            ).exists()
        )


class MembershipRoleIntakeDurabilityTests(TransactionTestCase):
    def _route(self, label):
        from tests.sb2_route_support import birth, prepare_route

        route = prepare_route(tag=f"h100-c2b-{label}-{uuid4().hex}")
        birth(route)
        return route

    def _authority(self):
        from tests.test_s015_role_command import _ExactProvider

        return S015Authority(
            provider=_ExactProvider(),
            database_alias="default",
        )

    def _active_member(self, route, label):
        from core.models import OrganismMembership
        from tests.sb2_route_support import add_active_elector

        member = add_active_elector(route, tag=label)
        membership = OrganismMembership.objects.get(
            living_organism=route.organism,
            identity=member,
        )
        return member, membership

    def _proposal(self, route, label, authority):
        from core.models import OrganismMembership
        from src.intevia.services.organism_role_service import (
            OrganismRoleService,
            ProposeAssignmentCommand,
        )
        from tests.s015_0021_support import database_now
        from tests.sb2_route_support import add_active_elector, route_fixture_state

        ids, _ = route_fixture_state(route)
        nominee = add_active_elector(route, tag="nominee")
        membership = OrganismMembership.objects.get(
            living_organism=route.organism,
            identity=nominee,
        )
        at = database_now()
        command = ProposeAssignmentCommand(
            credential=route.founder.credential,
            request_reference=f"{label}:proposal",
            idempotency_key=f"{label}:proposal",
            occurred_at=at,
            effective_at=at,
            membership_uuid=membership.membership_uuid,
            role_definition_uuid=ids["coordinator_role_uuid"],
            effective_until_state="NONE",
            effective_until=None,
        )
        service = OrganismRoleService(
            authority=authority,
            clock=database_now,
            database_alias="default",
        )
        return service, command, nominee

    def _assert_success_delta(
        self,
        *,
        service,
        method,
        entry,
        command,
        expected_delta,
        expected_decision,
        expected_receipt_fingerprint,
    ):
        from tests.s015_0021_support import database_now

        actor = Identity.objects.get(credential=command.credential)
        t0 = database_now()
        before = _public_table_counts()
        result = getattr(service, method)(command)
        t1 = database_now()
        after = _public_table_counts()
        self.assertEqual(_public_table_delta(before, after), expected_delta)

        intake = CommandIntake.objects.get(
            actor_id=actor.pk,
            idempotency_key=command.idempotency_key,
        )
        self.assertLessEqual(t0, intake.received_at)
        self.assertLessEqual(intake.received_at, t1)
        shape = intake_shape(entry, command)
        self.assertEqual(
            intake.request_fingerprint,
            command_intake_module.intake_fingerprint(
                entry=entry,
                action=command_intake_module.ENTRY_ACTIONS[entry],
                actor_identity_id=actor.identity_id,
                actor_access_epoch=actor.access_epoch,
                request_reference=shape.request_reference,
                idempotency_key=shape.idempotency_key,
                target=shape.target,
                command=shape.command,
                omitted=shape.omitted,
            ),
        )
        receipt = OrganismCommandReceipt.objects.get(
            actor_id=actor.pk,
            idempotency_key=command.idempotency_key,
        )
        self.assertEqual(
            OrganismCommandReceipt.objects.filter(
                actor_id=actor.pk,
                idempotency_key=command.idempotency_key,
            ).count(),
            1,
        )
        self.assertEqual(
            receipt.payload_fingerprint,
            expected_receipt_fingerprint(
                command,
                actor,
                intake.received_at,
            ),
        )
        decisions = AuthorityDecision.objects.filter(
            actor_id=actor.pk,
            idempotency_key=command.idempotency_key,
        )
        if expected_decision:
            self.assertEqual(decisions.count(), 1)
            self.assertEqual(decisions.get().evaluated_at, intake.received_at)
        else:
            self.assertEqual(decisions.count(), 0)
        return result

    def _assert_iw04a_iw07(
        self,
        *,
        family,
        service,
        method,
        command,
        failure_type,
        changed_command,
        expected_delta,
        expected_decision,
    ):
        actor = Identity.objects.get(credential=command.credential)
        before = _public_table_counts()
        observed_received_at = []

        def fail_before_finalise(*args, **kwargs):
            intake = CommandIntake.objects.get(
                actor_id=actor.pk,
                idempotency_key=command.idempotency_key,
            )
            observed_received_at.append(intake.received_at)
            raise failure_type(
                "received record was kept at "
                f"{intake.received_at.isoformat()}"
            )

        with patch.object(service, "_finalise", side_effect=fail_before_finalise):
            with self.assertRaises(failure_type) as raised:
                getattr(service, method)(command)
        self.assertIn("received record was kept at", str(raised.exception))
        self.assertEqual(len(observed_received_at), 1)

        after_failure = _public_table_counts()
        self.assertEqual(
            _public_table_delta(before, after_failure),
            {CommandIntake._meta.db_table: 1},
            family,
        )
        binding_before = CommandIntake.objects.filter(
            actor_id=actor.pk,
            idempotency_key=command.idempotency_key,
        ).values().get()
        self.assertEqual(binding_before["received_at"], observed_received_at[0])
        self.assertEqual(
            OrganismCommandReceipt.objects.filter(
                actor_id=actor.pk,
                idempotency_key=command.idempotency_key,
            ).count(),
            0,
        )
        self.assertEqual(
            AuthorityDecision.objects.filter(
                actor_id=actor.pk,
                idempotency_key=command.idempotency_key,
            ).count(),
            0,
        )

        changed = changed_command(command)
        before_mismatch = _public_table_counts()
        executor_name = {
            "membership": "_execute_open_membership",
            "role": "_execute_propose_assignment",
            "consent": "_execute_record_office_consent",
        }[family]
        original_executor = getattr(service, executor_name)
        with patch.object(
            service,
            executor_name,
            wraps=original_executor,
        ) as executor_spy:
            with self.assertRaises(S015CommandReplayMismatch):
                getattr(service, method)(changed)
            executor_spy.assert_not_called()
        self.assertEqual(_public_table_counts(), before_mismatch, family)
        self.assertEqual(
            CommandIntake.objects.filter(
                actor_id=actor.pk,
                idempotency_key=command.idempotency_key,
            ).values().get(),
            binding_before,
            family,
        )

        result = getattr(service, method)(command)
        after_retry = _public_table_counts()
        self.assertEqual(_public_table_delta(before, after_retry), expected_delta, family)
        intake = CommandIntake.objects.get(
            actor_id=actor.pk,
            idempotency_key=command.idempotency_key,
        )
        self.assertEqual(intake.received_at, observed_received_at[0], family)
        self.assertEqual(
            CommandIntake.objects.filter(
                actor_id=actor.pk,
                idempotency_key=command.idempotency_key,
            ).count(),
            1,
        )
        self.assertEqual(
            OrganismCommandReceipt.objects.filter(
                actor_id=actor.pk,
                idempotency_key=command.idempotency_key,
            ).count(),
            1,
        )
        decisions = AuthorityDecision.objects.filter(
            actor_id=actor.pk,
            idempotency_key=command.idempotency_key,
        )
        if expected_decision:
            self.assertEqual(decisions.count(), 1, family)
            self.assertEqual(decisions.get().evaluated_at, intake.received_at, family)
        else:
            self.assertEqual(decisions.count(), 0, family)
        return result

    def test_c9_all_five_2b_entries_are_admitted_and_accounted(self):
        from core.models import ContextualRoleAssignmentStateEvent
        from tests.s015_0021_support import database_now, register_test_learner

        route = self._route("c9-witness")
        authority = self._authority()
        membership_service = OrganismMembershipService(
            authority=authority,
            clock=database_now,
            database_alias="default",
        )
        applicant = register_test_learner(
            username=f"{route.tag}-open-member",
            display_name="C9 applicant",
        )
        opened_at = database_now()
        open_command = OpenMembershipCommand(
            credential=route.founder.credential,
            request_reference=f"{route.tag}:open",
            idempotency_key=f"{route.tag}:open",
            occurred_at=opened_at,
            effective_at=opened_at,
            living_organism_id=route.organism.organism_id,
            member_identity_id=applicant.identity_id,
            reason_class="TEST_ONLY_REASON",
        )
        membership_service.open_membership(open_command)

        member, membership = self._active_member(route, "departure")
        departure_at = database_now()
        departure_command = TransitionMembershipCommand(
            credential=member.credential,
            request_reference=f"{route.tag}:membership-end",
            idempotency_key=f"{route.tag}:membership-end",
            occurred_at=departure_at,
            effective_at=departure_at,
            membership_uuid=membership.membership_uuid,
            resulting_state="ENDED",
            reason_class="TEST_ONLY_REASON",
        )
        membership_service.transition_membership(departure_command)

        role_service, proposal_command, nominee = self._proposal(
            route,
            f"{route.tag}:c9",
            authority,
        )
        proposal = role_service.propose_assignment(proposal_command)
        proposal_event = ContextualRoleAssignmentStateEvent.objects.get(
            assignment__assignment_uuid=proposal.assignment_uuid,
            sequence=1,
            action="PROPOSE",
        )

        consent_service = OfficeAppointmentConsentService(
            authority=authority,
            clock=database_now,
            database_alias="default",
        )
        consent_at = database_now()
        consent_command = RecordOfficeConsentCommand(
            credential=nominee.credential,
            request_reference=f"{route.tag}:consent",
            idempotency_key=f"{route.tag}:consent",
            occurred_at=consent_at,
            effective_at=proposal_command.effective_at,
            assignment_uuid=proposal.assignment_uuid,
            proposal_event_uuid=proposal_event.event_uuid,
            action="CONSENT",
        )
        consent_service.record_office_consent(consent_command)

        assignment_at = database_now()
        assignment_command = TransitionAssignmentCommand(
            credential=route.founder.credential,
            request_reference=f"{route.tag}:assign",
            idempotency_key=f"{route.tag}:assign",
            occurred_at=assignment_at,
            effective_at=proposal_command.effective_at,
            assignment_uuid=proposal.assignment_uuid,
            action="ASSIGN",
            resulting_state="ACTIVE",
            effective_until_state="NONE",
            effective_until=None,
        )
        role_service.transition_assignment(assignment_command)

        commands = (
            (IntakeEntry.OPEN_MEMBERSHIP, open_command),
            (IntakeEntry.TRANSITION_MEMBERSHIP, departure_command),
            (IntakeEntry.PROPOSE_ASSIGNMENT, proposal_command),
            (IntakeEntry.RECORD_OFFICE_CONSENT, consent_command),
            (IntakeEntry.TRANSITION_ASSIGNMENT, assignment_command),
        )
        keys = [command.idempotency_key for _, command in commands]
        intakes = list(CommandIntake.objects.filter(idempotency_key__in=keys))
        receipts = list(
            OrganismCommandReceipt.objects.filter(idempotency_key__in=keys)
        )
        decisions = list(AuthorityDecision.objects.filter(idempotency_key__in=keys))
        self.assertTrue(intakes)
        self.assertTrue(receipts)
        self.assertTrue(decisions)
        self.assertEqual(len(intakes), len(commands))
        self.assertEqual(len(receipts), len(commands))
        self.assertEqual(len(decisions), len(commands) - 1)

        intakes_by_key = {row.idempotency_key: row for row in intakes}
        receipts_by_key = {row.idempotency_key: row for row in receipts}
        decisions_by_key = {row.idempotency_key: row for row in decisions}
        self.assertEqual(set(intakes_by_key), set(keys))
        self.assertEqual(set(receipts_by_key), set(keys))
        self.assertEqual(
            set(decisions_by_key),
            {
                command.idempotency_key
                for entry, command in commands
                if entry is not IntakeEntry.OPEN_MEMBERSHIP
            },
        )
        for entry, command in commands:
            with self.subTest(entry=entry):
                actor = Identity.objects.get(credential=command.credential)
                action = command_intake_module.ENTRY_ACTIONS[entry]
                intake = intakes_by_key[command.idempotency_key]
                receipt = receipts_by_key[command.idempotency_key]
                self.assertEqual(
                    CommandIntake.objects.filter(
                        idempotency_key=command.idempotency_key
                    ).count(),
                    1,
                )
                self.assertEqual(
                    (
                        intake.entry,
                        intake.action,
                        intake.idempotency_key,
                        intake.actor_id,
                    ),
                    (entry.value, action, command.idempotency_key, actor.pk),
                )
                self.assertEqual(
                    (receipt.action, receipt.idempotency_key, receipt.actor_id),
                    (action, command.idempotency_key, actor.pk),
                )
                if entry is IntakeEntry.OPEN_MEMBERSHIP:
                    self.assertEqual(
                        AuthorityDecision.objects.filter(
                            idempotency_key=command.idempotency_key
                        ).count(),
                        0,
                    )
                else:
                    decision = decisions_by_key[command.idempotency_key]
                    self.assertEqual(
                        (
                            decision.action,
                            decision.idempotency_key,
                            decision.actor_id,
                            decision.evaluated_at,
                        ),
                        (
                            action,
                            command.idempotency_key,
                            actor.pk,
                            intake.received_at,
                        ),
                    )

        self.assertEqual(
            command_intake_module.run_activation_step(
                "default",
                (
                    IntakeEntry.OPEN_MEMBERSHIP,
                    IntakeEntry.TRANSITION_MEMBERSHIP,
                    IntakeEntry.PROPOSE_ASSIGNMENT,
                    IntakeEntry.TRANSITION_ASSIGNMENT,
                    IntakeEntry.RECORD_OFFICE_CONSENT,
                ),
            ),
            [],
        )

    def test_iw01_exact_deltas_for_all_five_entries(self):
        from core.models import (
            ContextualRoleAssignment,
            ContextualRoleAssignmentStateEvent,
            OrganismMembership,
        )
        from core.organism_contract import OrganismAction
        from src.intevia.services import (
            organism_office_consent_service as consent_service_module,
        )
        from src.intevia.services.organism_command_support import canonical_timestamp
        from src.intevia.services.organism_membership_service import (
            OpenMembershipCommand,
            OrganismMembershipService,
            TransitionMembershipCommand,
        )
        from src.intevia.services.organism_office_consent_service import (
            OfficeAppointmentConsentService,
            RecordOfficeConsentCommand,
        )
        from src.intevia.services.organism_role_service import (
            OrganismRoleService,
            TransitionAssignmentCommand,
        )
        from tests.s015_0021_support import database_now, register_test_learner

        def expected_service_fingerprint(
            service,
            command,
            actor,
            received_at,
            target,
            command_payload,
        ):
            return service._command_fingerprint(
                actor=actor,
                request_reference=command.request_reference,
                idempotency_key=command.idempotency_key,
                occurred_at=command.occurred_at,
                target=target,
                command=command_payload,
            )

        route = self._route("iw01")
        authority = self._authority()
        service = OrganismMembershipService(
            authority=authority,
            clock=database_now,
            database_alias="default",
        )
        applicant = register_test_learner(
            username=f"{route.tag}-open-member",
            display_name=f"IW-01 {route.tag}",
        )
        opened_at = database_now()
        open_command = OpenMembershipCommand(
            credential=route.founder.credential,
            request_reference=f"{route.tag}:open",
            idempotency_key=f"{route.tag}:open",
            occurred_at=opened_at,
            effective_at=opened_at,
            living_organism_id=route.organism.organism_id,
            member_identity_id=applicant.identity_id,
            reason_class="TEST_ONLY_REASON",
        )

        def expected_open_fingerprint(command, actor, received_at):
            return expected_service_fingerprint(
                service,
                command,
                actor,
                received_at,
                target={
                    "living_organism_id": str(command.living_organism_id),
                    "member_identity_id": str(command.member_identity_id),
                    "reason_class": command.reason_class,
                    "resulting_state": "PROPOSED",
                    "schema": "intevia.s015.organism-membership-target.v1",
                },
                command_payload={
                    "living_organism_id": str(command.living_organism_id),
                    "member_identity_id": str(command.member_identity_id),
                    "reason_class": command.reason_class,
                    "resulting_state": "PROPOSED",
                    "effective_at": canonical_timestamp(command.effective_at),
                    "received_at": canonical_timestamp(received_at),
                    "schema": "intevia.s015.organism-membership-command.v1",
                },
            )

        self._assert_success_delta(
            service=service,
            method="open_membership",
            entry=IntakeEntry.OPEN_MEMBERSHIP,
            command=open_command,
            expected_delta={
                "core_commandintake": 1,
                "core_organismmembership": 1,
                "core_organismmembershiptransition": 1,
                "core_organismcommandreceipt": 1,
            },
            expected_decision=False,
            expected_receipt_fingerprint=expected_open_fingerprint,
        )

        member, membership = self._active_member(route, "departure")
        departure_at = database_now()
        departure = TransitionMembershipCommand(
            credential=member.credential,
            request_reference=f"{route.tag}:membership-end",
            idempotency_key=f"{route.tag}:membership-end",
            occurred_at=departure_at,
            effective_at=departure_at,
            membership_uuid=membership.membership_uuid,
            resulting_state="ENDED",
            reason_class="TEST_ONLY_REASON",
        )

        def expected_membership_transition_fingerprint(command, actor, received_at):
            target_membership = OrganismMembership.objects.select_related(
                "living_organism"
            ).get(membership_uuid=command.membership_uuid)
            return expected_service_fingerprint(
                service,
                command,
                actor,
                received_at,
                target={
                    "living_organism_id": str(
                        target_membership.living_organism.organism_id
                    ),
                    "membership_uuid": str(command.membership_uuid),
                    "reason_class": command.reason_class,
                    "resulting_state": command.resulting_state,
                    "schema": "intevia.s015.organism-membership-target.v1",
                },
                command_payload={
                    "membership_uuid": str(command.membership_uuid),
                    "reason_class": command.reason_class,
                    "resulting_state": command.resulting_state,
                    "effective_at": canonical_timestamp(command.effective_at),
                    "received_at": canonical_timestamp(received_at),
                    "schema": "intevia.s015.organism-membership-command.v1",
                },
            )

        self._assert_success_delta(
            service=service,
            method="transition_membership",
            entry=IntakeEntry.TRANSITION_MEMBERSHIP,
            command=departure,
            expected_delta={
                "core_commandintake": 1,
                "core_organismmembershiptransition": 1,
                "core_authoritydecision": 1,
                "core_organismcommandreceipt": 1,
            },
            expected_decision=True,
            expected_receipt_fingerprint=expected_membership_transition_fingerprint,
        )

        role_service, proposal_command, nominee = self._proposal(
            route,
            f"{route.tag}:iw01",
            authority,
        )

        def expected_proposal_fingerprint(command, actor, received_at):
            target_membership = OrganismMembership.objects.select_related(
                "living_organism"
            ).get(membership_uuid=command.membership_uuid)
            return expected_service_fingerprint(
                role_service,
                command,
                actor,
                received_at,
                target={
                    "action": OrganismAction.TRANSITION_CONTEXTUAL_ROLE.value,
                    "circle": None,
                    "event_action": "PROPOSE",
                    "effective_until": None,
                    "effective_until_state": command.effective_until_state,
                    "living_organism_id": str(
                        target_membership.living_organism.organism_id
                    ),
                    "membership_uuid": str(command.membership_uuid),
                    "resulting_state": "PROPOSED",
                    "role_definition_uuid": str(command.role_definition_uuid),
                    "schema": "intevia.s015.organism-role-target.v1",
                    "suspension_tolls_term": "NO",
                    "suspension_tolls_term_source": "DEFAULT (no settings store)",
                },
                command_payload={
                    "effective_at": canonical_timestamp(command.effective_at),
                    "effective_until": None,
                    "effective_until_state": command.effective_until_state,
                    "membership_uuid": str(command.membership_uuid),
                    "received_at": canonical_timestamp(received_at),
                    "resulting_state": "PROPOSED",
                    "role_definition_uuid": str(command.role_definition_uuid),
                    "schema": "intevia.s015.organism-role-command.v1",
                    "suspension_tolls_term": "NO",
                    "suspension_tolls_term_source": "DEFAULT (no settings store)",
                },
            )

        proposal = self._assert_success_delta(
            service=role_service,
            method="propose_assignment",
            entry=IntakeEntry.PROPOSE_ASSIGNMENT,
            command=proposal_command,
            expected_delta={
                "core_commandintake": 1,
                "core_contextualroleassignment": 1,
                "core_contextualroleassignmentstateevent": 1,
                "core_authoritydecision": 1,
                "core_organismcommandreceipt": 1,
            },
            expected_decision=True,
            expected_receipt_fingerprint=expected_proposal_fingerprint,
        )
        proposal_event = ContextualRoleAssignmentStateEvent.objects.get(
            assignment__assignment_uuid=proposal.assignment_uuid,
            sequence=1,
            action="PROPOSE",
        )

        consent_at = database_now()
        consent_command = RecordOfficeConsentCommand(
            credential=nominee.credential,
            request_reference=f"{route.tag}:consent",
            idempotency_key=f"{route.tag}:consent",
            occurred_at=consent_at,
            effective_at=proposal_command.effective_at,
            assignment_uuid=proposal.assignment_uuid,
            proposal_event_uuid=proposal_event.event_uuid,
            action="CONSENT",
        )
        consent_service = OfficeAppointmentConsentService(
            authority=authority,
            clock=database_now,
            database_alias="default",
        )

        def expected_consent_fingerprint(command, actor, received_at):
            assignment = ContextualRoleAssignment.objects.select_related(
                "membership__living_organism"
            ).get(assignment_uuid=command.assignment_uuid)
            target = {
                "action": OrganismAction.RECORD_OFFICE_CONSENT.value,
                "assignment_uuid": str(command.assignment_uuid),
                "event_action": command.action,
                "living_organism_id": str(
                    assignment.membership.living_organism.organism_id
                ),
                "membership_uuid": str(assignment.membership.membership_uuid),
                "proposal_event_uuid": str(command.proposal_event_uuid),
                "schema": "intevia.s015.office-consent-target.v1",
            }
            command_payload = {
                "action": command.action,
                "assignment_uuid": str(command.assignment_uuid),
                "effective_at": canonical_timestamp(command.effective_at),
                "proposal_event_uuid": str(command.proposal_event_uuid),
                "received_at": canonical_timestamp(received_at),
                "schema": "intevia.s015.office-consent-command.v1",
            }
            return consent_service_module._command_payload(
                domain=consent_service_module._COMMAND_DOMAIN,
                schema=consent_service_module._COMMAND_SCHEMA,
                action=OrganismAction.RECORD_OFFICE_CONSENT,
                actor_identity_id=actor.identity_id,
                actor_access_epoch=actor.access_epoch,
                request_reference=command.request_reference,
                idempotency_key=command.idempotency_key,
                occurred_at=command.occurred_at,
                target=target,
                command=command_payload,
            )

        self._assert_success_delta(
            service=consent_service,
            method="record_office_consent",
            entry=IntakeEntry.RECORD_OFFICE_CONSENT,
            command=consent_command,
            expected_delta={
                "core_commandintake": 1,
                "core_officeappointmentconsent": 1,
                "core_authoritydecision": 1,
                "core_organismcommandreceipt": 1,
            },
            expected_decision=True,
            expected_receipt_fingerprint=expected_consent_fingerprint,
        )

        assign_at = database_now()
        assign_command = TransitionAssignmentCommand(
            credential=route.founder.credential,
            request_reference=f"{route.tag}:assign",
            idempotency_key=f"{route.tag}:assign",
            occurred_at=assign_at,
            effective_at=proposal_command.effective_at,
            assignment_uuid=proposal.assignment_uuid,
            action="ASSIGN",
            resulting_state="ACTIVE",
            effective_until_state="NONE",
            effective_until=None,
        )

        def expected_assignment_transition_fingerprint(
            command,
            actor,
            received_at,
        ):
            assignment = ContextualRoleAssignment.objects.select_related(
                "membership__living_organism"
            ).get(assignment_uuid=command.assignment_uuid)
            target = {
                "action": OrganismAction.TRANSITION_CONTEXTUAL_ROLE.value,
                "assignment_uuid": str(command.assignment_uuid),
                "event_action": command.action,
                "living_organism_id": str(
                    assignment.membership.living_organism.organism_id
                ),
                "resulting_state": command.resulting_state,
                "schema": "intevia.s015.organism-role-target.v1",
                "effective_until": None,
                "effective_until_state": command.effective_until_state,
                "suspension_tolls_term": "NO",
                "suspension_tolls_term_source": "DEFAULT (no settings store)",
            }
            command_payload = {
                "action": command.action,
                "assignment_uuid": str(command.assignment_uuid),
                "effective_at": canonical_timestamp(command.effective_at),
                "received_at": canonical_timestamp(received_at),
                "resulting_state": command.resulting_state,
                "schema": "intevia.s015.organism-role-command.v1",
                "effective_until": None,
                "effective_until_state": command.effective_until_state,
                "suspension_tolls_term": "NO",
                "suspension_tolls_term_source": "DEFAULT (no settings store)",
            }
            return expected_service_fingerprint(
                role_service,
                command,
                actor,
                received_at,
                target,
                command_payload,
            )

        self._assert_success_delta(
            service=role_service,
            method="transition_assignment",
            entry=IntakeEntry.TRANSITION_ASSIGNMENT,
            command=assign_command,
            expected_delta={
                "core_commandintake": 1,
                "core_contextualroleassignmentstateevent": 1,
                "core_authoritydecision": 1,
                "core_organismcommandreceipt": 1,
            },
            expected_decision=True,
            expected_receipt_fingerprint=expected_assignment_transition_fingerprint,
        )

    def test_iw04a_and_iw07_rollback_retry_and_changed_answer_per_family(self):
        from dataclasses import replace as dataclass_replace

        from src.intevia.services.organism_membership_service import (
            OpenMembershipCommand,
            OrganismMembershipService,
        )
        from src.intevia.services.organism_office_consent_service import (
            OfficeAppointmentConsentService,
            RecordOfficeConsentCommand,
        )
        from src.intevia.services.organism_role_service import OrganismRoleService
        from tests.s015_0021_support import database_now, register_test_learner
        route = self._route("iw04a-iw07")
        authority = self._authority()
        membership_service = OrganismMembershipService(
            authority=authority,
            clock=database_now,
            database_alias="default",
        )
        target = register_test_learner(
            username=f"{route.tag}-rollback-target",
            display_name=f"IW-04a {route.tag}",
        )
        at = database_now()
        open_command = OpenMembershipCommand(
            credential=route.founder.credential,
            request_reference=f"{route.tag}:open",
            idempotency_key=f"{route.tag}:open",
            occurred_at=at,
            effective_at=at,
            living_organism_id=route.organism.organism_id,
            member_identity_id=target.identity_id,
            reason_class="TEST_ONLY_REASON",
        )
        role_service, proposal_command, nominee = self._proposal(
            route,
            f"{route.tag}:role",
            authority,
        )
        consent_service = OfficeAppointmentConsentService(
            authority=authority,
            clock=database_now,
            database_alias="default",
        )

        families = (
            (
                "membership",
                membership_service,
                "open_membership",
                open_command,
                S015MembershipAuthorityUnavailable,
                lambda command: dataclass_replace(
                    command,
                    reason_class="TEST_ONLY_CHANGED_REASON",
                ),
                {
                    "core_commandintake": 1,
                    "core_organismmembership": 1,
                    "core_organismmembershiptransition": 1,
                    "core_organismcommandreceipt": 1,
                },
                False,
            ),
            (
                "role",
                role_service,
                "propose_assignment",
                proposal_command,
                S015AssignmentAuthorityUnavailable,
                lambda command: dataclass_replace(
                    command,
                    occurred_at=command.occurred_at + timedelta(seconds=1),
                ),
                {
                    "core_commandintake": 1,
                    "core_contextualroleassignment": 1,
                    "core_contextualroleassignmentstateevent": 1,
                    "core_authoritydecision": 1,
                    "core_organismcommandreceipt": 1,
                },
                True,
            ),
        )
        proposal_result = None
        for (
            family,
            service,
            method,
            command,
            failure_type,
            changed_command,
            expected_delta,
            expected_decision,
        ) in families:
            with self.subTest(family=family):
                result = self._assert_iw04a_iw07(
                    family=family,
                    service=service,
                    method=method,
                    command=command,
                    failure_type=failure_type,
                    changed_command=changed_command,
                    expected_delta=expected_delta,
                    expected_decision=expected_decision,
                )
                if family == "role":
                    proposal_result = result

        if proposal_result is None:
            self.fail("IW-04a role retry did not return its proposal")
        from core.models import ContextualRoleAssignmentStateEvent

        proposal_event = ContextualRoleAssignmentStateEvent.objects.get(
            assignment__assignment_uuid=proposal_result.assignment_uuid,
            sequence=1,
            action="PROPOSE",
        )
        consent_at = database_now()
        consent_command = RecordOfficeConsentCommand(
            credential=nominee.credential,
            request_reference=f"{route.tag}:consent",
            idempotency_key=f"{route.tag}:consent",
            occurred_at=consent_at,
            effective_at=proposal_command.effective_at,
            assignment_uuid=proposal_result.assignment_uuid,
            proposal_event_uuid=proposal_event.event_uuid,
            action="CONSENT",
        )
        self._assert_iw04a_iw07(
            family="consent",
            service=consent_service,
            method="record_office_consent",
            command=consent_command,
            failure_type=S015OfficeConsentAuthorityUnavailable,
            changed_command=lambda command: dataclass_replace(
                command,
                action="DECLINE",
            ),
            expected_delta={
                "core_commandintake": 1,
                "core_officeappointmentconsent": 1,
                "core_authoritydecision": 1,
                "core_organismcommandreceipt": 1,
            },
            expected_decision=True,
        )

    def test_iw15_manual_transaction_visibility_for_each_family(self):
        from queue import Queue

        from tests.s015_0021_support import database_now
        from tests.s015_concurrency_support import cleanup_workers, start_worker

        credential = User(pk=771, username="h100-c2b-iw15-unpersisted")
        commands = _commands(credential)
        authority = S015Authority(provider=Mock(), database_alias="default")
        services = (
            (
                "membership",
                OrganismMembershipService(authority=authority, clock=database_now),
                "open_membership",
                commands[IntakeEntry.OPEN_MEMBERSHIP],
            ),
            (
                "role",
                OrganismRoleService(authority=authority, clock=database_now),
                "propose_assignment",
                commands[IntakeEntry.PROPOSE_ASSIGNMENT],
            ),
            (
                "consent",
                OfficeAppointmentConsentService(authority=authority, clock=database_now),
                "record_office_consent",
                commands[IntakeEntry.RECORD_OFFICE_CONSENT],
            ),
        )
        username = f"h100-c2b-iw15-{uuid4().hex}"

        def read_from_second_connection():
            visible = Queue()
            worker = start_worker(
                lambda: visible.put(User.objects.filter(username=username).exists())
            )
            try:
                self.assertEqual(worker.result(timeout=10), "ok")
                worker.thread.join(timeout=10)
                self.assertFalse(worker.thread.is_alive())
                return visible.get(timeout=5)
            finally:
                if worker.thread.is_alive():
                    cleanup_workers([worker])

        connection.set_autocommit(False)
        try:
            sentinel = User.objects.create(username=username)
            for family, service, method, command in services:
                with self.subTest(family=family), CaptureQueriesContext(connection) as queries:
                    with self.assertRaises(S015CommandAtomicityRequired) as raised:
                        getattr(service, method)(command)
                self.assertIsNone(
                    getattr(raised.exception, "refusal_code", None),
                    family,
                )
                self.assertEqual(len(queries), 0, family)
                self.assertFalse(connection.in_atomic_block, family)
                self.assertFalse(connection.get_autocommit(), family)
                self.assertTrue(User.objects.filter(pk=sentinel.pk).exists(), family)
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                self.assertEqual(cursor.fetchone(), (1,))
            self.assertFalse(read_from_second_connection())
            connection.commit()
            self.assertTrue(read_from_second_connection())
        finally:
            if not connection.get_autocommit():
                connection.rollback()
                connection.set_autocommit(True)

    def test_iw25p_transition_membership_real_and_injected_recheck(self):
        from queue import Queue
        from threading import Event, get_ident

        from core.models import OrganismMembership
        from src.intevia.services.organism_membership_service import (
            OrganismMembershipService,
            TransitionMembershipCommand,
        )
        from tests.s015_0021_support import database_now
        from tests.s015_concurrency_support import cleanup_workers, start_worker

        def run_branch(label, inject_unaccounted):
            route = self._route(f"iw25p-{label}")
            member, membership = self._active_member(route, "member")
            service = OrganismMembershipService(
                authority=self._authority(),
                clock=database_now,
                database_alias="default",
            )
            at = database_now()
            command = TransitionMembershipCommand(
                credential=member.credential,
                request_reference=f"{route.tag}:end",
                idempotency_key=f"{route.tag}:end",
                occurred_at=at,
                effective_at=at,
                membership_uuid=membership.membership_uuid,
                resulting_state="ENDED",
                reason_class="TEST_ONLY_REASON",
            )
            actor = Identity.objects.get(credential=member.credential)
            before = _public_table_counts()
            c2_paused = Event()
            release_c2 = Event()
            c2_thread_id = []
            c2_rows = []
            c2_history_results = []
            injected = []
            c2_results = Queue()
            original_refuse = command_intake_module._refuse_unexpected_history
            original_existing = command_intake_module._existing_row
            original_accounts_for = command_intake_module.accounts_for

            def pause_after_step_zero(*args, **kwargs):
                if c2_thread_id and get_ident() == c2_thread_id[0]:
                    c2_paused.set()
                    if not release_c2.wait(timeout=20):
                        raise TimeoutError("IW-25P C2 release timed out")
                result = original_refuse(*args, **kwargs)
                if c2_thread_id and get_ident() == c2_thread_id[0]:
                    c2_history_results.append(result)
                return result

            def observe_existing(*args, **kwargs):
                row = original_existing(*args, **kwargs)
                if c2_thread_id and get_ident() == c2_thread_id[0]:
                    c2_rows.append(row)
                return row

            def observe_accounts_for(binding, record):
                if (
                    inject_unaccounted
                    and c2_thread_id
                    and get_ident() == c2_thread_id[0]
                    and not injected
                ):
                    injected.append((record.kind, record.id))
                    return False
                return original_accounts_for(binding, record)

            def c2_call():
                c2_thread_id.append(get_ident())
                c2_results.put(service.transition_membership(command))

            worker = None
            try:
                with patch.object(
                    command_intake_module,
                    "_refuse_unexpected_history",
                    side_effect=pause_after_step_zero,
                ), patch.object(
                    command_intake_module,
                    "_existing_row",
                    side_effect=observe_existing,
                ), patch.object(
                    command_intake_module,
                    "accounts_for",
                    side_effect=observe_accounts_for,
                ):
                    worker = start_worker(c2_call)
                    self.assertTrue(c2_paused.wait(timeout=15))
                    self.assertFalse(
                        CommandIntake.objects.filter(
                            actor_id=actor.pk,
                            idempotency_key=command.idempotency_key,
                        ).exists()
                    )

                    c1_result = service.transition_membership(command)
                    after_c1 = _public_table_counts()
                    self.assertEqual(
                        _public_table_delta(before, after_c1),
                        {
                            "core_commandintake": 1,
                            "core_organismmembershiptransition": 1,
                            "core_authoritydecision": 1,
                            "core_organismcommandreceipt": 1,
                        },
                        label,
                    )
                    release_c2.set()
                    self.assertEqual(worker.result(timeout=30), "ok")
                    worker.thread.join(timeout=10)
                    self.assertFalse(worker.thread.is_alive())
                    c2_result = c2_results.get(timeout=5)

                self.assertEqual(c2_result, c1_result, label)
                self.assertEqual(_public_table_counts(), after_c1, label)
                self.assertEqual(
                    CommandIntake.objects.filter(
                        actor_id=actor.pk,
                        idempotency_key=command.idempotency_key,
                    ).count(),
                    1,
                )
                self.assertEqual(
                    OrganismCommandReceipt.objects.filter(
                        actor_id=actor.pk,
                        idempotency_key=command.idempotency_key,
                    ).count(),
                    1,
                )
                self.assertEqual(
                    AuthorityDecision.objects.filter(
                        actor_id=actor.pk,
                        idempotency_key=command.idempotency_key,
                    ).count(),
                    1,
                )
                self.assertEqual(len(c2_rows), 2)
                self.assertIsNone(c2_rows[0])
                self.assertEqual(len(c2_history_results), 1)
                if inject_unaccounted:
                    self.assertEqual(len(injected), 1)
                    self.assertIsNotNone(c2_history_results[0])
                else:
                    self.assertEqual(injected, [])
                    self.assertIsNone(c2_history_results[0])
                self.assertIsNotNone(c2_rows[1])
            finally:
                release_c2.set()
                if worker is not None and worker.thread.is_alive():
                    cleanup_workers([worker])

        with self.subTest(branch="IW-25P(e)"):
            run_branch("real", inject_unaccounted=False)
        with self.subTest(branch="IW-25P(e-prime)"):
            run_branch("injected", inject_unaccounted=True)
