"""H100 C2a/C2b bound-request projection and execution witnesses."""

from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import User
from django.db import connection, transaction
from django.test import (
    SimpleTestCase,
    TransactionTestCase,
    override_settings,
)
from django.test.utils import CaptureQueriesContext

from core.models import (
    Application,
    AuthorityDecision,
    CommandIntake,
    Identity,
    IdentityTransition,
    OrganismCommandReceipt,
)
from src.intevia.services import command_intake as command_intake_module
from src.intevia.services.command_intake import (
    ACTIVE_ENTRIES,
    ActorRef,
    BoundRequest,
    IntakeAdmissionTechnicalFailure,
    IntakeEntry,
    application_shape,
    intake_shape,
)
from src.intevia.services.organism_membership_service import (
    OpenMembershipCommand,
    TransitionMembershipCommand,
)
from src.intevia.services.organism_application_service import (
    ClaimApplicationCommand,
    DecideApplicationCommand,
    OrganismApplicationService,
    ReassignApplicationCommand,
    SubmitApplicationCommand,
    WithdrawApplicationCommand,
)
from src.intevia.services.organism_office_consent_service import (
    RecordOfficeConsentCommand,
)
from src.intevia.services.organism_role_service import (
    ProposeAssignmentCommand,
    TransitionAssignmentCommand,
)
from src.intevia.services.organism_authority import (
    S015Authority,
    S015OwnApplicantAuthorityRequest,
    S015OwnApplicantAuthorityResponse,
    S015PostFoundingAuthorityResponse,
)
from tests.s015_0021_support import database_now, fresh, seed_identity
from tests.sb2_route_support import birth, prepare_route, route_test_now


UTC = timezone.utc
T0 = datetime(2026, 9, 29, 12, tzinfo=UTC)


def _command_set(credential: User):
    application_uuid = uuid4()
    return {
        IntakeEntry.SUBMIT_APPLICATION: SubmitApplicationCommand(
            credential=credential,
            living_organism_id=uuid4(),
            request_reference="h100:submit:reference",
            idempotency_key="h100:submit:key",
            occurred_at=T0,
            effective_at=T0,
            predecessor_reference=uuid4(),
            answer="a",
        ),
        IntakeEntry.WITHDRAW_APPLICATION: WithdrawApplicationCommand(
            credential=credential,
            application_uuid=application_uuid,
            request_reference="h100:withdraw:reference",
            idempotency_key="h100:withdraw:key",
            occurred_at=T0,
            effective_at=T0,
        ),
        IntakeEntry.CLAIM_APPLICATION: ClaimApplicationCommand(
            credential=credential,
            application_uuid=application_uuid,
            request_reference="h100:claim:reference",
            idempotency_key="h100:claim:key",
            occurred_at=T0,
            effective_at=T0,
        ),
        IntakeEntry.REASSIGN_APPLICATION: ReassignApplicationCommand(
            credential=credential,
            application_uuid=application_uuid,
            request_reference="h100:reassign:reference",
            idempotency_key="h100:reassign:key",
            occurred_at=T0,
            effective_at=T0,
        ),
        IntakeEntry.DECIDE_APPLICATION: DecideApplicationCommand(
            credential=credential,
            application_uuid=application_uuid,
            request_reference="h100:decide:reference",
            idempotency_key="h100:decide:key",
            occurred_at=T0,
            effective_at=T0,
            outcome="DECLINE",
        ),
    }


def _test_actor() -> ActorRef:
    return ActorRef(pk=91, identity_id=uuid4(), access_epoch=4)


def _fingerprint(entry: IntakeEntry, command: object, actor: ActorRef) -> str:
    return BoundRequest.for_test(
        entry,
        command,
        received_at=T0 + timedelta(seconds=3),
        actor=actor,
    ).request_fingerprint


class ApplicationShapeTests(SimpleTestCase):
    """Exercise pure intake projections without database access."""

    def setUp(self):
        self.credential = User(username="h100-projection")
        self.actor = _test_actor()
        self.commands = _command_set(self.credential)

    def test_active_set_and_exact_type_dispatch(self):
        # H100 chunk 4 (C4 §F3 row 6, CH-17): scoped to this module's five application entries; the
        # global set (the twelve) is asserted once in tests/test_h100_intake_gateway.py.
        self.assertTrue(
            frozenset(
                {
                    IntakeEntry.SUBMIT_APPLICATION,
                    IntakeEntry.WITHDRAW_APPLICATION,
                    IntakeEntry.CLAIM_APPLICATION,
                    IntakeEntry.REASSIGN_APPLICATION,
                    IntakeEntry.DECIDE_APPLICATION,
                }
            )
            <= ACTIVE_ENTRIES
        )
        for entry, command in self.commands.items():
            with self.subTest(entry=entry):
                application_shape(entry, command)
        with self.assertRaises(TypeError):
            application_shape(
                IntakeEntry.CLAIM_APPLICATION,
                self.commands[IntakeEntry.REASSIGN_APPLICATION],
            )
        with self.assertRaises(TypeError):
            application_shape(
                IntakeEntry.REASSIGN_APPLICATION,
                self.commands[IntakeEntry.CLAIM_APPLICATION],
            )
        with self.assertRaises(TypeError):
            application_shape(
                IntakeEntry.CLAIM_APPLICATION,
                self.commands[IntakeEntry.DECIDE_APPLICATION],
            )

    def test_each_projection_matches_the_commissioned_shape(self):
        for entry, command in self.commands.items():
            shape = application_shape(entry, command)
            with self.subTest(entry=entry):
                self.assertEqual(shape.idempotency_key, command.idempotency_key)
                self.assertEqual(shape.request_reference, command.request_reference)
                self.assertEqual(shape.omitted, ())
                self.assertIsNone(shape.target_organism_id)
                if entry is IntakeEntry.SUBMIT_APPLICATION:
                    self.assertEqual(
                        shape.target,
                        {"living_organism_id": command.living_organism_id},
                    )
                    self.assertEqual(
                        shape.command,
                        {
                            "occurred_at": command.occurred_at,
                            "effective_at": command.effective_at,
                            "predecessor_reference": command.predecessor_reference,
                            "answer": command.answer,
                        },
                    )
                else:
                    self.assertEqual(
                        shape.target,
                        {"application_uuid": command.application_uuid},
                    )
                    expected_command: dict[str, object] = {
                        "occurred_at": command.occurred_at,
                        "effective_at": command.effective_at,
                    }
                    if entry is IntakeEntry.DECIDE_APPLICATION:
                        expected_command["outcome"] = command.outcome
                    self.assertEqual(shape.command, expected_command)

    def test_every_material_field_changes_the_fingerprint(self):
        replacements = {
            IntakeEntry.SUBMIT_APPLICATION: {
                "living_organism_id": uuid4(),
                "predecessor_reference": uuid4(),
                "answer": "b",
                "occurred_at": T0 + timedelta(seconds=1),
                "effective_at": T0 + timedelta(seconds=1),
                "request_reference": "h100:submit:reference-2",
                "idempotency_key": "h100:submit:key-2",
            },
            IntakeEntry.WITHDRAW_APPLICATION: {
                "application_uuid": uuid4(),
                "occurred_at": T0 + timedelta(seconds=1),
                "effective_at": T0 + timedelta(seconds=1),
                "request_reference": "h100:withdraw:reference-2",
                "idempotency_key": "h100:withdraw:key-2",
            },
            IntakeEntry.CLAIM_APPLICATION: {
                "application_uuid": uuid4(),
                "occurred_at": T0 + timedelta(seconds=1),
                "effective_at": T0 + timedelta(seconds=1),
                "request_reference": "h100:claim:reference-2",
                "idempotency_key": "h100:claim:key-2",
            },
            IntakeEntry.REASSIGN_APPLICATION: {
                "application_uuid": uuid4(),
                "occurred_at": T0 + timedelta(seconds=1),
                "effective_at": T0 + timedelta(seconds=1),
                "request_reference": "h100:reassign:reference-2",
                "idempotency_key": "h100:reassign:key-2",
            },
            IntakeEntry.DECIDE_APPLICATION: {
                "application_uuid": uuid4(),
                "occurred_at": T0 + timedelta(seconds=1),
                "effective_at": T0 + timedelta(seconds=1),
                "outcome": "ACCEPT",
                "request_reference": "h100:decide:reference-2",
                "idempotency_key": "h100:decide:key-2",
            },
        }
        for entry, fields in replacements.items():
            baseline = self.commands[entry]
            baseline_fingerprint = _fingerprint(entry, baseline, self.actor)
            for field, value in fields.items():
                with self.subTest(entry=entry, field=field):
                    self.assertNotEqual(
                        baseline_fingerprint,
                        _fingerprint(entry, replace(baseline, **{field: value}), self.actor),
                    )

    def test_identical_commands_and_equivalent_timestamps_reproduce_fingerprint(self):
        command = self.commands[IntakeEntry.SUBMIT_APPLICATION]
        self.assertEqual(
            _fingerprint(IntakeEntry.SUBMIT_APPLICATION, command, self.actor),
            _fingerprint(IntakeEntry.SUBMIT_APPLICATION, command, self.actor),
        )
        equivalent = replace(
            command,
            occurred_at=T0.astimezone(timezone(timedelta(hours=5))),
            effective_at=T0.astimezone(timezone(timedelta(hours=5))),
        )
        self.assertEqual(
            _fingerprint(IntakeEntry.SUBMIT_APPLICATION, command, self.actor),
            _fingerprint(IntakeEntry.SUBMIT_APPLICATION, equivalent, self.actor),
        )
        different_credential = replace(command, credential=User(username="another"))
        self.assertEqual(
            _fingerprint(IntakeEntry.SUBMIT_APPLICATION, command, self.actor),
            _fingerprint(IntakeEntry.SUBMIT_APPLICATION, different_credential, self.actor),
        )

    def test_received_at_is_rejected_in_each_projection(self):
        for entry, command in self.commands.items():
            with self.subTest(entry=entry), self.assertRaisesRegex(
                ValueError, "received_at is assigned by INTEVIA"
            ):
                application_shape(entry, replace(command, received_at=T0))


class MembershipRoleConsentShapeTests(SimpleTestCase):
    """Check the five new command projections without database access."""

    def setUp(self):
        self.credential = User(username="h100-membership-role-projection")
        self.actor = _test_actor()
        self.commands = {
            IntakeEntry.OPEN_MEMBERSHIP: OpenMembershipCommand(
                credential=self.credential,
                request_reference="h100:membership-open:reference",
                idempotency_key="h100:membership-open:key",
                occurred_at=T0,
                effective_at=T0,
                living_organism_id=uuid4(),
                member_identity_id=uuid4(),
                reason_class="TEST_ONLY_REASON",
            ),
            IntakeEntry.TRANSITION_MEMBERSHIP: TransitionMembershipCommand(
                credential=self.credential,
                request_reference="h100:membership-transition:reference",
                idempotency_key="h100:membership-transition:key",
                occurred_at=T0,
                effective_at=T0,
                membership_uuid=uuid4(),
                resulting_state="ENDED",
                reason_class="TEST_ONLY_REASON",
            ),
            IntakeEntry.PROPOSE_ASSIGNMENT: ProposeAssignmentCommand(
                credential=self.credential,
                request_reference="h100:assignment-propose:reference",
                idempotency_key="h100:assignment-propose:key",
                occurred_at=T0,
                effective_at=T0,
                membership_uuid=uuid4(),
                role_definition_uuid=uuid4(),
                effective_until_state="NONE",
                effective_until=None,
            ),
            IntakeEntry.TRANSITION_ASSIGNMENT: TransitionAssignmentCommand(
                credential=self.credential,
                request_reference="h100:assignment-transition:reference",
                idempotency_key="h100:assignment-transition:key",
                occurred_at=T0,
                effective_at=T0,
                assignment_uuid=uuid4(),
                action="END",
                resulting_state="ENDED",
            ),
            IntakeEntry.RECORD_OFFICE_CONSENT: RecordOfficeConsentCommand(
                credential=self.credential,
                request_reference="h100:consent:reference",
                idempotency_key="h100:consent:key",
                occurred_at=T0,
                effective_at=T0,
                assignment_uuid=uuid4(),
                proposal_event_uuid=uuid4(),
                action="CONSENT",
            ),
        }

    def test_each_projection_matches_the_commissioned_shape(self):
        expected = {
            IntakeEntry.OPEN_MEMBERSHIP: (
                {"living_organism_id": self.commands[IntakeEntry.OPEN_MEMBERSHIP].living_organism_id,
                 "member_identity_id": self.commands[IntakeEntry.OPEN_MEMBERSHIP].member_identity_id},
                {"occurred_at": T0, "effective_at": T0, "reason_class": "TEST_ONLY_REASON"},
            ),
            IntakeEntry.TRANSITION_MEMBERSHIP: (
                {"membership_uuid": self.commands[IntakeEntry.TRANSITION_MEMBERSHIP].membership_uuid},
                {"occurred_at": T0, "effective_at": T0, "resulting_state": "ENDED",
                 "reason_class": "TEST_ONLY_REASON"},
            ),
            IntakeEntry.PROPOSE_ASSIGNMENT: (
                {"membership_uuid": self.commands[IntakeEntry.PROPOSE_ASSIGNMENT].membership_uuid,
                 "role_definition_uuid": self.commands[IntakeEntry.PROPOSE_ASSIGNMENT].role_definition_uuid},
                {"occurred_at": T0, "effective_at": T0, "effective_until_state": "NONE",
                 "effective_until": None},
            ),
            IntakeEntry.TRANSITION_ASSIGNMENT: (
                {"assignment_uuid": self.commands[IntakeEntry.TRANSITION_ASSIGNMENT].assignment_uuid},
                {"occurred_at": T0, "effective_at": T0, "action": "END",
                 "resulting_state": "ENDED", "effective_until_state": None,
                 "effective_until": None},
            ),
            IntakeEntry.RECORD_OFFICE_CONSENT: (
                {"assignment_uuid": self.commands[IntakeEntry.RECORD_OFFICE_CONSENT].assignment_uuid,
                 "proposal_event_uuid": self.commands[IntakeEntry.RECORD_OFFICE_CONSENT].proposal_event_uuid},
                {"occurred_at": T0, "effective_at": T0, "action": "CONSENT"},
            ),
        }
        for entry, command in self.commands.items():
            shape = intake_shape(entry, command)
            with self.subTest(entry=entry):
                self.assertEqual(shape.idempotency_key, command.idempotency_key)
                self.assertEqual(shape.request_reference, command.request_reference)
                self.assertEqual(shape.target, expected[entry][0])
                self.assertEqual(shape.command, expected[entry][1])
                self.assertEqual(shape.omitted, ())
                self.assertIsNone(shape.target_organism_id)
                self.assertIsNone(command.received_at)

    def test_material_inputs_change_the_fingerprint(self):
        replacements = {
            IntakeEntry.OPEN_MEMBERSHIP: {
                "living_organism_id": uuid4(),
                "member_identity_id": uuid4(),
                "occurred_at": T0 + timedelta(seconds=1),
                "effective_at": T0 + timedelta(seconds=1),
                "reason_class": "TEST_ONLY_REASON_CHANGED",
                "request_reference": "h100:membership-open:reference-2",
                "idempotency_key": "h100:membership-open:key-2",
            },
            IntakeEntry.TRANSITION_MEMBERSHIP: {
                "membership_uuid": uuid4(),
                "occurred_at": T0 + timedelta(seconds=1),
                "effective_at": T0 + timedelta(seconds=1),
                "resulting_state": "PROBATIONARY",
                "reason_class": "TEST_ONLY_REASON_CHANGED",
                "request_reference": "h100:membership-transition:reference-2",
                "idempotency_key": "h100:membership-transition:key-2",
            },
            IntakeEntry.PROPOSE_ASSIGNMENT: {
                "membership_uuid": uuid4(),
                "role_definition_uuid": uuid4(),
                "occurred_at": T0 + timedelta(seconds=1),
                "effective_at": T0 + timedelta(seconds=1),
                "effective_until_state": "NOT_ESTABLISHED",
                "request_reference": "h100:assignment-propose:reference-2",
                "idempotency_key": "h100:assignment-propose:key-2",
            },
            IntakeEntry.TRANSITION_ASSIGNMENT: {
                "assignment_uuid": uuid4(),
                "occurred_at": T0 + timedelta(seconds=1),
                "effective_at": T0 + timedelta(seconds=1),
                "action": "REASSIGN",
                "resulting_state": "ACTIVE",
                "request_reference": "h100:assignment-transition:reference-2",
                "idempotency_key": "h100:assignment-transition:key-2",
            },
            IntakeEntry.RECORD_OFFICE_CONSENT: {
                "assignment_uuid": uuid4(),
                "proposal_event_uuid": uuid4(),
                "occurred_at": T0 + timedelta(seconds=1),
                "effective_at": T0 + timedelta(seconds=1),
                "action": "DECLINE",
                "request_reference": "h100:consent:reference-2",
                "idempotency_key": "h100:consent:key-2",
            },
        }
        for entry, fields in replacements.items():
            command = self.commands[entry]
            fingerprint = _fingerprint(entry, command, self.actor)
            for field, value in fields.items():
                with self.subTest(entry=entry, field=field):
                    self.assertNotEqual(
                        fingerprint,
                        _fingerprint(entry, replace(command, **{field: value}), self.actor),
                    )
        for entry in (IntakeEntry.PROPOSE_ASSIGNMENT, IntakeEntry.TRANSITION_ASSIGNMENT):
            command = self.commands[entry]
            fixed_values = {
                "effective_until_state": "FIXED",
                "effective_until": T0 + timedelta(days=1),
            }
            if entry is IntakeEntry.TRANSITION_ASSIGNMENT:
                fixed_values.update(action="ASSIGN", resulting_state="ACTIVE")
            fixed = replace(
                command,
                **fixed_values,
            )
            self.assertNotEqual(
                _fingerprint(entry, fixed, self.actor),
                _fingerprint(
                    entry,
                    replace(fixed, effective_until=T0 + timedelta(days=2)),
                    self.actor,
                ),
            )
            if entry is IntakeEntry.TRANSITION_ASSIGNMENT:
                non_fixed = replace(
                    fixed,
                    effective_until_state="NONE",
                    effective_until=None,
                )
                changed_state = replace(
                    non_fixed,
                    effective_until_state="NOT_ESTABLISHED",
                )
                self.assertNotEqual(
                    _fingerprint(entry, non_fixed, self.actor),
                    _fingerprint(entry, changed_state, self.actor),
                )

    def test_exact_types_and_client_receive_times_are_refused(self):
        for entry, command in self.commands.items():
            with self.subTest(entry=entry):
                with self.assertRaises(ValueError):
                    intake_shape(entry, replace(command, received_at=T0))
                wrong = next(
                    other for other_entry, other in self.commands.items()
                    if other_entry is not entry
                )
                with self.assertRaises(TypeError):
                    intake_shape(entry, wrong)


class _Provider:
    """A test-only authority provider for the application fixtures."""

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


@skipUnless(connection.vendor == "postgresql", "H100 intake witnesses require PostgreSQL")
class BoundRequestDatabaseTests(TransactionTestCase):
    """Exercise production admissions and executor verification on PostgreSQL."""

    def setUp(self):
        self.tag = f"h100-bound-{fresh()}"
        self.route = prepare_route(tag=self.tag)
        birth(self.route)
        self.organism_uuid = self.route.organism.organism_id
        self.received_before = database_now()
        with transaction.atomic(), connection.cursor() as cursor:
            applicant_pk = seed_identity(cursor, f"h100-applicant-{self.tag}")
        self.applicant = Identity.objects.select_related("credential").get(pk=applicant_pk)
        self._activate_applicant()
        self.service = OrganismApplicationService(
            authority=S015Authority(provider=_Provider()),
            clock=database_now,
        )

    def _activate_applicant(self):
        at = route_test_now(self.route)
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
            occurred_at=at,
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
            occurred_at=at,
            previous_transition=provision,
        )

    def _submission(self, *, key_suffix="first", answer="Bound request witness"):
        now = route_test_now(self.route)
        return SubmitApplicationCommand(
            credential=self.applicant.credential,
            living_organism_id=self.organism_uuid,
            request_reference=f"h100:submit:{key_suffix}:{self.tag}",
            idempotency_key=f"h100:submit:key:{key_suffix}:{self.tag}",
            occurred_at=now,
            effective_at=now,
            answer=answer,
        )

    def _capture_submission(self, command):
        original = self.service._execute_submit_application
        captured = {}

        def capture(bound_command, bound):
            captured["bound"] = bound
            return original(bound_command, bound)

        with patch.object(
            self.service,
            "_execute_submit_application",
            side_effect=capture,
        ):
            result = self.service.submit_application(command)
        return result, captured["bound"]

    def _counts(self):
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

    def test_committed_binding_has_matching_v6_row_and_rejects_mismatch(self):
        command = self._submission()
        _, bound = self._capture_submission(command)
        actor = Identity.objects.get(pk=bound.actor_pk)
        with transaction.atomic():
            bound.verify(
                command,
                actor,
                IntakeEntry.SUBMIT_APPLICATION,
                "default",
            )

        before = self._counts()
        tampered = replace(
            bound,
            target_organism_id=uuid4(),
            _sentinel=command_intake_module._CONSTRUCTOR_SENTINEL,
        )
        with transaction.atomic(), self.assertRaisesRegex(
            IntakeAdmissionTechnicalFailure, "not the request that was admitted"
        ):
            tampered.verify(
                command,
                actor,
                IntakeEntry.SUBMIT_APPLICATION,
                "default",
            )
        self.assertEqual(self._counts(), before)
        self.assertTrue(
            CommandIntake.objects.filter(
                id=bound.intake_id,
                idempotency_key=command.idempotency_key,
                received_at=bound.received_at,
            ).exists()
        )

    def test_reused_binding_rejects_changed_content_key_reference_and_entry(self):
        command = self._submission()
        _, record = self._capture_submission(command)
        before = self._counts()
        changed_commands = (
            replace(command, answer="Changed material answer"),
            replace(command, idempotency_key=f"changed:{command.idempotency_key}"),
            replace(command, request_reference=f"changed:{command.request_reference}"),
        )
        for changed in changed_commands:
            with self.subTest(command=changed), self.assertRaises(
                IntakeAdmissionTechnicalFailure
            ):
                self.service._execute_submit_application(changed, record)

        from src.intevia.services.organism_application_service import WithdrawApplicationCommand

        wrong_entry_command = WithdrawApplicationCommand(
            credential=command.credential,
            application_uuid=uuid4(),
            request_reference=command.request_reference,
            idempotency_key=command.idempotency_key,
            occurred_at=command.occurred_at,
            effective_at=command.effective_at,
        )
        with self.assertRaises(IntakeAdmissionTechnicalFailure):
            self.service._execute_withdraw_application(wrong_entry_command, record)
        self.assertEqual(self._counts(), before)

    def test_actor_mismatch_is_rejected_before_v6_query(self):
        command = self._submission()
        _, bound = self._capture_submission(command)
        actor = Identity.objects.get(pk=bound.actor_pk)
        actor.identity_id = uuid4()
        with transaction.atomic(), CaptureQueriesContext(connection) as queries:
            with self.assertRaises(IntakeAdmissionTechnicalFailure):
                bound.verify(
                    command,
                    actor,
                    IntakeEntry.SUBMIT_APPLICATION,
                    "default",
                )
        self.assertEqual(len(queries), 0)

    def test_test_binding_gate_applies_at_construction_and_use(self):
        command = self._submission(key_suffix="test-gate")
        actor = ActorRef(
            pk=self.applicant.pk,
            identity_id=self.applicant.identity_id,
            access_epoch=self.applicant.access_epoch,
        )
        with override_settings(INTEVIA_TEST_CONTEXT=False):
            with self.assertRaises(IntakeAdmissionTechnicalFailure):
                BoundRequest.for_test(
                    IntakeEntry.SUBMIT_APPLICATION,
                    command,
                    received_at=database_now(),
                    actor=actor,
                )
        test_bound = BoundRequest.for_test(
            IntakeEntry.SUBMIT_APPLICATION,
            command,
            received_at=database_now(),
            actor=actor,
        )
        with override_settings(INTEVIA_TEST_CONTEXT=False):
            with self.assertRaises(IntakeAdmissionTechnicalFailure):
                test_bound.verify_content(command, IntakeEntry.SUBMIT_APPLICATION)

    def test_unexpected_parent_is_rejected_for_each_active_executor(self):
        actor_ref = ActorRef(
            pk=self.applicant.pk,
            identity_id=self.applicant.identity_id,
            access_epoch=self.applicant.access_epoch,
        )
        parent_command = _command_set(self.applicant.credential)[
            IntakeEntry.SUBMIT_APPLICATION
        ]
        parent = BoundRequest.for_test(
            IntakeEntry.SUBMIT_APPLICATION,
            parent_command,
            received_at=database_now(),
            actor=actor_ref,
        )
        commands = _command_set(self.applicant.credential)
        from src.intevia.services.organism_formation_service import (
            FoundLivingOrganismCommand,
        )
        from src.intevia.services.organism_governance_service import (
            AdoptGoverningInstrumentCommand,
            CreateGovernanceCircleCommand,
            OrganismGovernanceService,
        )
        from tests.sbg_route_support import governance_arrangement_body

        founding_command = FoundLivingOrganismCommand(
            credential=self.route.founder.credential,
            name="Bound parent founding",
            purpose="A commissioned purpose for bound-parent verification.",
            vision="A commissioned vision.",
            mission="A commissioned mission.",
            values="Commissioned values.",
            request_reference="h100:bound:founding",
            idempotency_key="h100:bound:founding:key",
            accepted_terms_version_uuid=self.route.terms.terms_version_uuid,
        )
        effective_from = route_test_now(self.route)
        adoption_command = AdoptGoverningInstrumentCommand(
            credential=self.route.founder.credential,
            living_organism_uuid=self.organism_uuid,
            rule_uuid=uuid4(),
            version=1,
            body=governance_arrangement_body(effective_from=effective_from),
            effective_from=effective_from,
            request_reference="h100:bound:adoption",
            idempotency_key="h100:bound:adoption:key",
        )
        circle_command = CreateGovernanceCircleCommand(
            credential=self.route.founder.credential,
            living_organism_uuid=self.organism_uuid,
            request_reference="h100:bound:circle",
            idempotency_key="h100:bound:circle:key",
        )
        commands.update(
            {
                IntakeEntry.FOUND_LIVING_ORGANISM: founding_command,
                IntakeEntry.ADOPT_GOVERNING_INSTRUMENT: adoption_command,
                IntakeEntry.CREATE_GOVERNANCE_CIRCLE: circle_command,
            }
        )
        commands[IntakeEntry.CLAIM_APPLICATION] = replace(
            commands[IntakeEntry.CLAIM_APPLICATION],
            credential=self.route.founder.credential,
        )
        commands[IntakeEntry.REASSIGN_APPLICATION] = replace(
            commands[IntakeEntry.REASSIGN_APPLICATION],
            credential=self.route.founder.credential,
        )
        commands[IntakeEntry.DECIDE_APPLICATION] = replace(
            commands[IntakeEntry.DECIDE_APPLICATION],
            credential=self.route.founder.credential,
        )
        actor_by_entry = {
            IntakeEntry.SUBMIT_APPLICATION: self.applicant,
            IntakeEntry.WITHDRAW_APPLICATION: self.applicant,
            IntakeEntry.CLAIM_APPLICATION: self.route.founder,
            IntakeEntry.REASSIGN_APPLICATION: self.route.founder,
            IntakeEntry.DECIDE_APPLICATION: self.route.founder,
            IntakeEntry.FOUND_LIVING_ORGANISM: self.route.founder,
            IntakeEntry.ADOPT_GOVERNING_INSTRUMENT: self.route.founder,
            IntakeEntry.CREATE_GOVERNANCE_CIRCLE: self.route.founder,
        }
        governance = OrganismGovernanceService(clock=database_now)
        executor_by_entry = {
            IntakeEntry.SUBMIT_APPLICATION: self.service._execute_submit_application,
            IntakeEntry.WITHDRAW_APPLICATION: self.service._execute_withdraw_application,
            IntakeEntry.CLAIM_APPLICATION: self.service._execute_claim_application,
            IntakeEntry.REASSIGN_APPLICATION: self.service._execute_reassign_application,
            IntakeEntry.DECIDE_APPLICATION: self.service._execute_decide_application,
            IntakeEntry.FOUND_LIVING_ORGANISM: self.route.formation_service._execute_found_living_organism,
            IntakeEntry.ADOPT_GOVERNING_INSTRUMENT: governance._execute_adopt_governing_instrument,
            IntakeEntry.CREATE_GOVERNANCE_CIRCLE: governance._execute_create_governance_circle,
        }
        before = self._counts()
        for entry in executor_by_entry:
            command = commands[entry]
            actor = actor_by_entry[entry]
            request = BoundRequest.for_test(
                entry,
                command,
                received_at=database_now(),
                actor=ActorRef(
                    pk=actor.pk,
                    identity_id=actor.identity_id,
                    access_epoch=actor.access_epoch,
                ),
            )
            request = replace(
                request,
                parent=parent,
                _sentinel=command_intake_module._CONSTRUCTOR_SENTINEL,
            )
            with self.subTest(entry=entry), self.assertRaises(
                IntakeAdmissionTechnicalFailure
            ):
                executor_by_entry[entry](command, request)
        self.assertEqual(self._counts(), before)

    def test_public_entry_has_no_bound_request_parameter(self):
        command = self._submission(key_suffix="extra-argument")
        with self.assertRaises(TypeError):
            self.service.submit_application(command, object())
        from src.intevia.services.organism_formation_service import (
            FoundLivingOrganismCommand,
        )
        from src.intevia.services.organism_governance_service import (
            AdoptGoverningInstrumentCommand,
            CreateGovernanceCircleCommand,
            OrganismGovernanceService,
        )
        from tests.sbg_route_support import governance_arrangement_body

        governance = OrganismGovernanceService(clock=database_now)
        effective_at = route_test_now(self.route)
        commands = (
            (
                self.route.formation_service.found_living_organism,
                IntakeEntry.FOUND_LIVING_ORGANISM,
                FoundLivingOrganismCommand(
                    credential=self.route.founder.credential,
                    name="Bound keyword founding",
                    purpose="A purpose for the public bound-keyword check.",
                    request_reference="h100:bound-kw:founding",
                    idempotency_key="h100:bound-kw:founding:key",
                    accepted_terms_version_uuid=self.route.terms.terms_version_uuid,
                ),
            ),
            (
                governance.adopt_governing_instrument,
                IntakeEntry.ADOPT_GOVERNING_INSTRUMENT,
                AdoptGoverningInstrumentCommand(
                    credential=self.route.founder.credential,
                    living_organism_uuid=self.organism_uuid,
                    rule_uuid=uuid4(),
                    version=1,
                    body=governance_arrangement_body(effective_from=effective_at),
                    effective_from=effective_at,
                    request_reference="h100:bound-kw:adoption",
                    idempotency_key="h100:bound-kw:adoption:key",
                ),
            ),
            (
                governance.create_governance_circle,
                IntakeEntry.CREATE_GOVERNANCE_CIRCLE,
                CreateGovernanceCircleCommand(
                    credential=self.route.founder.credential,
                    living_organism_uuid=self.organism_uuid,
                    request_reference="h100:bound-kw:circle",
                    idempotency_key="h100:bound-kw:circle:key",
                ),
            ),
        )
        for public_entry, entry, family_command in commands:
            with self.subTest(method=public_entry.__name__):
                with self.assertRaises(TypeError):
                    public_entry(family_command, bound=object())
                self.assertFalse(
                    CommandIntake.objects.filter(
                        actor_id=self.route.founder.pk,
                        action=entry.value,
                        idempotency_key=family_command.idempotency_key,
                    ).exists()
                )
