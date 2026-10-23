"""S3-FU Round 3 gateway, change and SQL-guard witnesses on disposable PostgreSQL."""

import importlib
import hashlib
import json
import threading
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from unittest import skipUnless
from uuid import UUID, uuid4

from django.db import IntegrityError, connection, transaction
from django.test import TransactionTestCase

from core.forms import LOApplicationQuestionDraftForm, LOApplicationSettingsDraftForm
from core.models import (
    Application, ApplicationDecision, ApplicationDecisionEffect, ApplicationEngagement, AuthorityDecision,
    CommandIntake,
    ApplicationLineage, ApplicationLineageEvent, ContextualRoleAssignment,
    ContextualRoleAssignmentStateEvent, Identity, IdentityTransition,
    IntakeEntry, LivingOrganism, LivingOrganismEvent, OrganismCommandReceipt, OrganismMembership,
    OrganismMembershipTransition, LOFoundingContentVersion,
    LOApplicationQuestionVersion, LOApplicationSettingsVersion, LOGovernanceConfigurationVersion,
)
from core.organism_contract import OrganismAction
from src.intevia.services.organism_application_service import (
    EffectGatewayVersionCommand, FormationLONameSource, GatewayDraftCommand,
    OrganismApplicationService, S015ApplicationRefused, SubmitApplicationCommand, _PAIR_DOMAIN,
    S015ApplicationAuthorityUnavailable,
    _effective_gateway_version, _validate_gateway_answer,
    default_application_question, gateway_version_status,
)
from src.intevia.services.command_intake import ActorRef, BoundRequest, ENTRY_ACTIONS
from src.intevia.services.organism_authority import (
    S015Authority, S015OwnApplicantAuthorityRequest, S015OwnApplicantAuthorityResponse,
    S015PostFoundingAuthorityResponse, canonical_timestamp,
)
from src.intevia.services.organism_command_support import S015CommandReplayMismatch, WithheldField, withheld_fields
from src.intevia.services.organism_membership_service import (
    OrganismMembershipService, OpenMembershipCommand, S015OperationNotYetImplemented,
)
from src.intevia.services.organism_role_service import (
    OrganismRoleService, ProposeAssignmentCommand, TransitionAssignmentCommand,
)
from tests.s015_0021_support import (
    T0,
    append_event,
    database_now,
    empty_fp,
    found_nonroot,
    fresh,
    refresh,
    seed_identity,
    u,
)
from tests.sb2_route_support import (
    birth,
    prepare_route,
    route_fixture_state,
    route_recorded_times,
    route_test_now,
)
from tests.s015_0021_support import seed_test_gateway
from tests.test_s015_application_authority import TestFixtureBirthingFactSource
from tests.s015_concurrency_support import (
    ORGANISM_LOCK_RELATIONS,
    backend_pid,
    cleanup_workers,
    exact_wait,
    start_worker,
    wait_for_blocker_set,
    wait_until_locked,
)
from tests.test_s015_role_command import (
    _run_with_external_locks,
    _register_no_other_sessions_cleanup,
    _set_local_concurrency_timeouts,
)
from tests.test_s015_0024_commitment_preimages import digest, l1_preimage, parts_preimage


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
POSTGRES = skipUnless(connection.vendor == "postgresql", "S3 gateway requires PostgreSQL")


def _assert_single_intake(test, entry, command):
    """Assert exactly one intake row for the command, with its entry, action and intake-projection fingerprint (CH-17)."""
    from core.models import CommandIntake, Identity
    from src.intevia.services import command_intake as command_intake_module

    actor = Identity.objects.get(credential=command.credential)
    shape = command_intake_module.intake_shape(entry, command)
    action = command_intake_module.ENTRY_ACTIONS[entry]
    if action is None:
        # H100 chunk 4 (C4 §F3 row 5): the two G3 entries take their action from the shape (CI:396-397).
        action = shape.action
    rows = CommandIntake.objects.filter(
        actor=actor,
        action=action,
        idempotency_key=command.idempotency_key,
    )
    test.assertEqual(rows.count(), 1)
    intake = rows.get()
    test.assertEqual(intake.entry, entry.value)
    test.assertEqual(
        intake.request_fingerprint,
        command_intake_module.intake_fingerprint(
            entry=entry,
            action=action,
            actor_identity_id=actor.identity_id,
            actor_access_epoch=actor.access_epoch,
            request_reference=shape.request_reference,
            idempotency_key=shape.idempotency_key,
            target=shape.target,
            command=shape.command,
            omitted=shape.omitted,
        ),
    )


class _CoordinatorProvider:
    """TEST-ONLY permissive provider for service and lock witnesses."""

    def __init__(self):
        self.requests = []

    def evaluate_organism_authority(self, request):
        self.requests.append(request)
        if isinstance(request, S015OwnApplicantAuthorityRequest):
            return S015OwnApplicantAuthorityResponse(**asdict(request), authority_reference="s3:test:applicant")
        return S015PostFoundingAuthorityResponse(**asdict(request), authority_reference="s3:test:gateway")


class _FixtureBirthingCoordinatorProvider(_CoordinatorProvider):
    def __init__(self, birthing_source):
        super().__init__()
        self.birthing_source = birthing_source
        self.birthing_references = []

    def evaluate_organism_authority(self, request):
        if request.action is OrganismAction.SUBMIT_APPLICATION:
            reference = self.birthing_source.reference_at(
                request.living_organism_id,
                request.effective_at,
                request.evaluated_at,
            )
            if not reference:
                raise RuntimeError("The controlled Birthing source did not label this LO")
            self.birthing_references.append(reference)
        return super().evaluate_organism_authority(request)


class _PausingPostFoundingProvider:
    """TEST DEVICE (LOI Option B): answers post-founding authority exactly, but first signals `entered` and waits for `release`.
    It freezes an S1/S2 command INSIDE AUTHORITY - after the command holds the LO row, before its insert trigger enters the register.
    It bypasses no lock and changes no command semantics. Never used outside these two witnesses."""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def evaluate_organism_authority(self, request):
        self.entered.set()
        assert self.release.wait(timeout=30)
        return S015PostFoundingAuthorityResponse(
            **asdict(request), authority_reference="s3:test:paused",
        )


class _TestFixtureLONameSource:
    def __init__(self, fixture_name="TEST-FIXTURE-NAME: Riverside Commons"):
        self.fixture_name = fixture_name

    def name_for(self, living_organism):
        return self.fixture_name


class FakeCareExecutor:
    """TEST SUBSTITUTE — stands in for the CARE principal (S3-IL-CARE-EXEC); never a production source."""

    def __init__(self, identity):
        self.identity = identity

    def identity_for(self, living_organism):
        return self.identity


class _TestDiscussionWelcome:
    """Test-only substitute for the unavailable DISCUSSION welcome (S3-IL-NOTICE)."""

    def __init__(self):
        self.calls = []

    def send_for(self, application, decision):
        self.calls.append((application.pk, decision.pk))


@POSTGRES
class ApplicationGatewayTests(TransactionTestCase):
    def setUp(self):
        global T0, NOW
        _register_no_other_sessions_cleanup(self)
        self.tag = f"s3-gateway-{fresh()}"
        if self._testMethodName == "test_four_closed_states_and_version_evidence_survives_a_change":
            with transaction.atomic(), connection.cursor() as cursor:
                self.ids = found_nonroot(cursor, tag=f"gateway-closed-states-{self.tag}")
                cursor.execute(
                    "SELECT recorded_at FROM core_livingorganismevent WHERE id = %s",
                    [self.ids["events"]["core_livingorganismevent"]],
                )
                T0 = cursor.fetchone()[0]
                cursor.execute("SELECT clock_timestamp()")
                fixture_at = cursor.fetchone()[0]
                self.gateway = seed_test_gateway(
                    cursor,
                    self.ids,
                    at=fixture_at,
                    include_governance_configuration=True,
                    include_settings=False,
                    include_question=False,
                )
            with connection.cursor() as cursor:
                cursor.execute("SELECT clock_timestamp()")
                NOW = cursor.fetchone()[0]
            self.route = None
            self.route_times = None
            self.organism = LivingOrganism.objects.get(pk=self.ids["lo"])
            self.actor = Identity.objects.select_related("credential").get(pk=self.ids["identity"])
            self.fixture_birthing_source = TestFixtureBirthingFactSource(self.organism.organism_id)
            self.provider = _FixtureBirthingCoordinatorProvider(self.fixture_birthing_source)
            self.service = OrganismApplicationService(
                authority=S015Authority(provider=self.provider),
                clock=lambda: NOW,
            )
            return
        if self._testMethodName == "test_name_dependency_default_draft_and_unselected_settings":
            with transaction.atomic(), connection.cursor() as cursor:
                self.no_name_ids = found_nonroot(cursor, tag=f"no-name-{self.tag}")
        first_governance_configuration = (
            self._testMethodName == "test_governance_choice_defaults_copies_and_remains_effective"
        )
        self.route = prepare_route(
            tag=self.tag,
            governance_enabled=None if first_governance_configuration else False,
            include_settings=not first_governance_configuration,
            include_question=not first_governance_configuration,
            to_birthing_phase=not first_governance_configuration,
        )
        if first_governance_configuration:
            self.route_times = route_recorded_times(self.route)
            self.organism = self.route.organism
        else:
            birth(self.route)
            self.ids, self.gateway = route_fixture_state(self.route)
            self.route_times = self.ids["recorded_times"]
            self.organism = LivingOrganism.objects.get(pk=self.ids["lo"])
        T0 = self.route_times.founding_recorded_at
        NOW = route_test_now(self.route)
        self.actor = self.route.founder
        self.provider = _CoordinatorProvider()
        self.service = OrganismApplicationService(
            authority=S015Authority(provider=self.provider),
            clock=lambda: NOW,
        )

    def _draft_command(self, action, fields, suffix=None, *, actor=None):
        token = suffix or fresh()
        actor = self.actor if actor is None else actor
        return GatewayDraftCommand(
            credential=actor.credential, living_organism_id=self.organism.organism_id,
            action=action, request_reference=f"s3:draft:{token}", idempotency_key=f"s3:draft:{token}",
            occurred_at=NOW, effective_at=NOW, fields=fields,
        )

    def draft(self, action, fields, suffix=None):
        return self.service.save_gateway_draft(self._draft_command(action, fields, suffix))

    def _draft_for_test(self, action, fields, *, received_at, suffix=None, actor=None):
        # H100 chunk 4 (C4 §F3 row 3; D:1732): the executor with a test-only bound request, for a draft
        # that must run inside a test transaction. No admission, no intake row.
        actor = self.actor if actor is None else actor
        command = self._draft_command(action, fields, suffix, actor=actor)
        bound = BoundRequest.for_test(
            IntakeEntry.SAVE_GATEWAY_DRAFT,
            command,
            received_at=received_at,
            actor=ActorRef(pk=actor.pk, identity_id=actor.identity_id, access_epoch=actor.access_epoch),
        )
        return self.service._execute_save_gateway_draft(command, bound)

    def _effect_command(self, version, action, suffix=None, *, actor=None):
        token = suffix or fresh()
        actor = self.actor if actor is None else actor
        return EffectGatewayVersionCommand(
            credential=actor.credential, living_organism_id=self.organism.organism_id,
            action=action, version_uuid=version.version_uuid,
            request_reference=f"s3:effect:{token}", idempotency_key=f"s3:effect:{token}",
            occurred_at=NOW, effective_at=NOW,
        )

    def effect(self, version, action, suffix=None):
        return self._effect_with_service(self.service, version, action, suffix)

    def _effect_with_service(self, service, version, action, suffix=None):
        return service.effect_gateway_version(self._effect_command(version, action, suffix))

    def _effect_for_test(self, service, version, action, *, received_at, suffix=None, actor=None):
        # H100 chunk 4 (C4 §F3 row 4, OPEN-5 (a); D:1733-1734): the executor with a test-only bound
        # request, for an effect run inside a wrapping worker transaction. No admission, no intake row.
        actor = self.actor if actor is None else actor
        command = self._effect_command(version, action, suffix, actor=actor)
        bound = BoundRequest.for_test(
            IntakeEntry.EFFECT_GATEWAY_VERSION,
            command,
            received_at=received_at,
            actor=ActorRef(pk=actor.pk, identity_id=actor.identity_id, access_epoch=actor.access_epoch),
        )
        return service._execute_effect_gateway_version(command, bound)

    def _assert_visible_at(self, cut, *versions):
        # CH-42 (B2): every historical prerequisite row the converted call's branch needs is visible at
        # its receive-time cut: the LO chain head (received and recorded by the cut) and the named versions.
        self.organism.refresh_from_db()
        head = LivingOrganismEvent.objects.get(pk=self.organism.head_event_id)
        self.assertLessEqual(head.received_at, cut)
        self.assertLessEqual(head.recorded_at, cut)
        for version in versions:
            version.refresh_from_db()
            self.assertLessEqual(version.received_at, cut)
            self.assertLessEqual(version.recorded_at, cut)

    def direct_effect(self, action, reference):
        self.organism.refresh_from_db()
        predecessor = LivingOrganismEvent.objects.get(pk=self.organism.head_event_id)
        with connection.cursor() as cursor:
            return append_event(
                cursor, "core_livingorganismevent", self.organism.pk, predecessor.sequence + 1,
                action.value, predecessor.resulting_state, predecessor.resulting_state,
                None, self.ids["basis"], fresh(), pred_id=predecessor.pk,
                eff=NOW, occ=NOW, rec=NOW,
                extra={"actor_state": "LIVING_ORGANISM", "actor_access_epoch": None,
                       "enterer_identity_id": self.actor.pk, "evidence_reference": reference},
            )

    def applicant(self):
        with transaction.atomic(), connection.cursor() as cursor:
            applicant_id = seed_identity(cursor, f"gateway-applicant-{fresh()}")
        applicant = Identity.objects.select_related("credential").get(pk=applicant_id)
        provision = IdentityTransition.objects.create(
            identity=applicant, action=IdentityTransition.Action.PROVISION,
            prior_state=None, resulting_state="pending", authority_reference="s3:test:provision",
            technical_executor="S3_TEST", evidence_reference="s3:test:history",
            reason_category=IdentityTransition.ReasonCategory.PROVISIONING,
            correlation_id=uuid4(), occurred_at=T0,
        )
        IdentityTransition.objects.create(
            identity=applicant, action=IdentityTransition.Action.ACTIVATE,
            prior_state="pending", resulting_state="active", authority_reference="s3:test:activate",
            technical_executor="S3_TEST", evidence_reference="s3:test:history",
            reason_category=IdentityTransition.ReasonCategory.ACCESS_REVIEW,
            correlation_id=uuid4(), occurred_at=NOW, previous_transition=provision,
        )
        return applicant

    def _submit_command(self, applicant, answer="A test-only applicant answer"):
        token = fresh()
        return SubmitApplicationCommand(
            credential=applicant.credential, living_organism_id=self.organism.organism_id,
            request_reference=f"s3:submit:{token}", idempotency_key=f"s3:submit:{token}",
            occurred_at=NOW, effective_at=NOW, answer=answer,
        )

    def submit(self, applicant, answer="A test-only applicant answer"):
        return self.service.submit_application(self._submit_command(applicant, answer))

    def _submit_for_test(
        self,
        applicant,
        *,
        answer="A test-only applicant answer",
        command=None,
        received_at=None,
    ):
        if received_at is None:
            received_at = NOW
        command = self._submit_command(applicant, answer) if command is None else command
        actor = ActorRef(
            pk=applicant.pk,
            identity_id=applicant.identity_id,
            access_epoch=applicant.access_epoch,
        )
        bound = BoundRequest.for_test(
            IntakeEntry.SUBMIT_APPLICATION,
            command,
            received_at=received_at,
            actor=actor,
        )
        return self.service._execute_submit_application(command, bound)

    def _automatic_collaborators(self):
        with transaction.atomic(), connection.cursor() as cursor:
            care_pk = seed_identity(cursor, f"fake-care-{fresh()}")
        care = Identity.objects.select_related("credential").get(pk=care_pk)
        return care, _TestDiscussionWelcome()

    def automatic_gateway(self):
        settings = self.draft(OrganismAction.EFFECT_APPLICATION_SETTINGS, {
            "applications_open": True, "automatic_acceptance": True, "processing_responsibility": "LO",
        })
        care, welcome = self._automatic_collaborators()
        settings_service = OrganismApplicationService(
            authority=S015Authority(provider=_CoordinatorProvider()),
            clock=lambda: NOW,
            care_executor=FakeCareExecutor(care),
            welcome=welcome,
        )
        self._effect_with_service(settings_service, settings, OrganismAction.EFFECT_APPLICATION_SETTINGS)
        return settings

    def automatic_service(self):
        care, welcome = self._automatic_collaborators()
        self.provider = _CoordinatorProvider()
        self.service = OrganismApplicationService(
            authority=S015Authority(provider=self.provider), clock=lambda: NOW,
            care_executor=FakeCareExecutor(care), welcome=welcome,
        )
        return care, welcome

    def _direct_cq_configuration(self, inherit, settings_required, question_required):
        return LOGovernanceConfigurationVersion.objects.create(
            living_organism=self.organism,
            actor=self.actor,
            enterer=self.actor,
            supersedes=LOGovernanceConfigurationVersion.objects.get(living_organism=self.organism),
            occurred_at=NOW,
            effective_at=NOW,
            received_at=NOW,
            cq_active=True,
            inherit_portal_cq_settings=inherit,
            require_cq_to_change_lo_settings=settings_required,
            require_cq_to_change_application_question=question_required,
        )

    def open_gateway(self):
        self.automatic_gateway()
        version = self.draft(OrganismAction.EFFECT_APPLICATION_SETTINGS, {
            "applications_open": True, "automatic_acceptance": False, "processing_responsibility": "LO",
        })
        self.effect(version, OrganismAction.EFFECT_APPLICATION_SETTINGS)
        return version

    def _draft_settings_as(self, actor, fields):
        token = fresh()
        return self.service.save_gateway_draft(GatewayDraftCommand(
            credential=actor.credential, living_organism_id=self.organism.organism_id,
            action=OrganismAction.EFFECT_APPLICATION_SETTINGS,
            request_reference=f"s3:draft:{token}", idempotency_key=f"s3:draft:{token}",
            occurred_at=NOW, effective_at=NOW, fields=fields,
        ))

    def _effect_settings_as(self, actor, version):
        token = fresh()
        return self.service.effect_gateway_version(EffectGatewayVersionCommand(
            credential=actor.credential, living_organism_id=self.organism.organism_id,
            action=OrganismAction.EFFECT_APPLICATION_SETTINGS, version_uuid=version.version_uuid,
            request_reference=f"s3:effect:{token}", idempotency_key=f"s3:effect:{token}",
            occurred_at=NOW, effective_at=NOW,
        ))

    def _workers(self):
        workers = []
        self.addCleanup(cleanup_workers, workers)
        return workers

    def _start_worker(self, workers, operation):
        def bounded_operation():
            with transaction.atomic():
                _set_local_concurrency_timeouts()
                return operation()

        worker = start_worker(bounded_operation)
        workers.append(worker)
        return worker

    def _join_worker(self, worker):
        worker.thread.join(timeout=30)
        self.assertFalse(worker.thread.is_alive())
        return worker.result(timeout=10)

    def _pair_lock_key(self, applicant):
        return int.from_bytes(
            hashlib.sha256(
                _PAIR_DOMAIN + applicant.identity_id.bytes + self.organism.organism_id.bytes,
            ).digest()[:8],
            "big",
            signed=True,
        )

    def _assert_no_deadlock(self, *outcomes):
        for outcome in outcomes:
            self.assertNotIn("deadlock detected", str(outcome).lower(), repr(outcome))

    def _seed_one_stale_register_row(self):
        # TEST SETUP: clear stale organism locks, then commit exactly one stale row for this organism.
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM public.s015_transaction_register WHERE kind = %s "
                "AND pg_xact_status(xid) IS DISTINCT FROM %s",
                ["ORGANISM_LOCK", "in progress"],
            )
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "SELECT public.s015_lock_organisms(ARRAY[%s]::uuid[])",
                [self.organism.organism_id],
            )

    def _race_paused_command_against_submission(self, paused_call, provider, applicant):
        self._seed_one_stale_register_row()
        workers = []
        self.addCleanup(cleanup_workers, workers)
        self.addCleanup(provider.release.set)

        def public_paused_call():
            # The 2b entries are public and refuse an enclosing transaction (IW-00), so the paused command runs on
            # its own connection with no atomic block; the same bounds are set at session level.
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '5s'")
                cursor.execute("SET statement_timeout = '10s'")
            return paused_call()

        paused = start_worker(public_paused_call)
        workers.append(paused)
        entered = provider.entered.wait(timeout=10)
        if not entered:
            outcome = "still running" if paused.thread.is_alive() else repr(paused.result(timeout=1))
            self.fail(f"the paused command did not reach the provider: {outcome}")
        submission = self._start_worker(workers, lambda: self._submit_for_test(applicant))
        with connection.cursor() as cursor:
            wait_until_locked(
                cursor, submission.pid(), paused.pid(), relations={"core_livingorganism"},
            )
            ew = exact_wait(cursor, submission.pid())
            self.assertEqual(ew["waiting"]["locktype"], "transactionid")
            self.assertEqual(ew["holders"], [paused.pid()])
            self.assertEqual(ew["tuple_relation"], "core_livingorganism")
        provider.release.set()
        return self._join_worker(paused), self._join_worker(submission), ew

    def _second_coordinator(self):
        # TEST-ONLY second current Coordinator with a distinct Identity and credential.
        with transaction.atomic(), connection.cursor() as cursor:
            other_pk = seed_identity(cursor, f"gateway-coordinator2-{self.tag}")
            member_uuid = u(f"gateway-member2:{self.tag}")
            cursor.execute(
                "INSERT INTO core_organismmembership (membership_uuid, state, created_at, current_state_effective_at, "
                "state_known_at, state_source_event_set_fingerprint, identity_id, living_organism_id) "
                "VALUES (%s, 'ACTIVE', now(), %s, %s, %s, %s, %s) RETURNING id",
                [member_uuid, T0, NOW, empty_fp(cursor, "core_organismmembership", member_uuid),
                 other_pk, self.ids["lo"]],
            )
            member_id = cursor.fetchone()[0]
            append_event(cursor, "core_organismmembershiptransition", member_id, 1, "JOIN", None,
                         "ACTIVE", other_pk, self.ids["basis"], f"gateway-second:{self.tag}",
                         eff=T0, occ=T0, rec=NOW,
                         extra={"reason_class": "TEST"})
            refresh(cursor, "core_organismmembership", member_id, T0)
        from core.models import OrganismRoleDefinition
        from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider

        other = Identity.objects.select_related("credential").get(pk=other_pk)
        role = OrganismRoleDefinition.objects.get(pk=self.ids["roles"]["COORDINATOR"])
        role_service = OrganismRoleService(
            authority=S015Authority(provider=ProductionApplicationAuthorityProvider()),
            clock=self._database_now,
        )

        proposal_at = self._database_now()
        proposal_key = f"gateway:office2:propose:key:{self.tag}"
        proposal_command = ProposeAssignmentCommand(
            credential=other.credential,
            request_reference=f"gateway:office2:propose:{self.tag}",
            idempotency_key=proposal_key,
            occurred_at=NOW,
            effective_at=NOW,
            membership_uuid=UUID(str(member_uuid)),
            role_definition_uuid=role.role_uuid,
            effective_until_state="NONE",
            effective_until=None,
        )
        proposal = role_service.propose_assignment(proposal_command)
        _assert_single_intake(self, IntakeEntry.PROPOSE_ASSIGNMENT, proposal_command)
        assignment_at = self._database_now()
        assignment_key = f"gateway:office2:assign:key:{self.tag}"
        assignment_command = TransitionAssignmentCommand(
            credential=self.actor.credential,
            request_reference=f"gateway:office2:assign:{self.tag}",
            idempotency_key=assignment_key,
            occurred_at=NOW,
            effective_at=NOW,
            assignment_uuid=proposal.assignment_uuid,
            action="ASSIGN",
            resulting_state="ACTIVE",
            effective_until_state="NONE",
            effective_until=None,
        )
        role_service.transition_assignment(assignment_command)
        _assert_single_intake(self, IntakeEntry.TRANSITION_ASSIGNMENT, assignment_command)
        return other

    def _database_now(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT clock_timestamp()")
            return cursor.fetchone()[0]

    def _assert_latest_settings_effect(self, version):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT evidence_reference FROM public.core_livingorganismevent "
                "WHERE living_organism_id = %s AND action = %s ORDER BY sequence DESC LIMIT 1",
                [self.organism.pk, OrganismAction.EFFECT_APPLICATION_SETTINGS.value],
            )
            latest_reference = cursor.fetchone()[0]
        self.assertEqual(latest_reference, f"application-settings-version:{version.version_uuid}")

    def _assert_automatic_settings_refused(self, service, message, suffix):
        version = self.draft(OrganismAction.EFFECT_APPLICATION_SETTINGS, {
            "applications_open": True, "automatic_acceptance": True, "processing_responsibility": "LO",
        })
        events_before = LivingOrganismEvent.objects.filter(living_organism=self.organism).count()
        receipts_before = OrganismCommandReceipt.objects.count()
        initial_settings = LOApplicationSettingsVersion.objects.get(
            pk=self.gateway["core_loapplicationsettingsversion"],
        )
        with self.assertRaises(S015ApplicationRefused) as refused:
            self._effect_with_service(
                service, version, OrganismAction.EFFECT_APPLICATION_SETTINGS, suffix,
            )
        intake = CommandIntake.objects.get(
            actor=self.actor,
            idempotency_key=f"s3:effect:{suffix}",
        )
        self.assertEqual(
            str(refused.exception),
            message.replace("⟨T⟩", canonical_timestamp(intake.received_at)),
        )
        self.assertIsNone(refused.exception.refusal_code)
        self.assertEqual(
            LivingOrganismEvent.objects.filter(living_organism=self.organism).count(),
            events_before,
        )
        self.assertEqual(OrganismCommandReceipt.objects.count(), receipts_before)
        self._assert_latest_settings_effect(initial_settings)

    def test_effecting_automatic_settings_refuses_for_missing_care_first(self):
        message = (
            "These settings were not put into effect: automatic acceptance needs a CARE executor, "
            "which is not configured (S3-IL-CARE-EXEC). Existing settings are unchanged and no governed "
            "record was written. INTEVIA kept a record that this request was received at ⟨T⟩; sent again "
            "unchanged, it is answered as of that time. You can still choose manual processing by the LO's "
            "Coordinators."
        )
        welcome_service = OrganismApplicationService(
            authority=S015Authority(provider=_CoordinatorProvider()),
            clock=lambda: NOW,
            welcome=_TestDiscussionWelcome(),
        )
        self._assert_automatic_settings_refused(
            welcome_service, message, "care-missing-welcome-supplied",
        )
        self._assert_automatic_settings_refused(
            self.service, message, "both-collaborators-missing",
        )

    def test_effecting_automatic_settings_refuses_for_unavailable_welcome(self):
        care, _ = self._automatic_collaborators()
        care_only_service = OrganismApplicationService(
            authority=S015Authority(provider=_CoordinatorProvider()),
            clock=lambda: NOW,
            care_executor=FakeCareExecutor(care),
        )
        self._assert_automatic_settings_refused(
            care_only_service,
            "These settings were not put into effect: automatic acceptance needs the welcome for new "
            "members, which is not yet available (S3-IL-NOTICE). Existing settings are unchanged and no "
            "governed record was written. INTEVIA kept a record that this request was received at ⟨T⟩; "
            "sent again unchanged, it is answered as of that time. You can still choose manual processing "
            "by the LO's Coordinators.",
            "care-supplied-welcome-unavailable",
        )

    def test_effecting_automatic_settings_succeeds_with_both_collaborators(self):
        settings = self.automatic_gateway()
        self._assert_latest_settings_effect(settings)

    def test_xw1_lock_cycle_regression_witness(self):
        """On old code, the gateway NOWAIT probe fails while submission waits and submission ends in deadlock detected; corrected order leaves the gateway head free and refuses after closure."""
        self.open_gateway()
        closure = self.draft(OrganismAction.EFFECT_APPLICATION_SETTINGS, {
            "applications_open": False, "automatic_acceptance": False, "processing_responsibility": "LO",
        })
        applicant = self.applicant()
        with connection.cursor() as cursor:  # TEST SETUP: remove stale register rows left by fixture transactions
            cursor.execute(
                "DELETE FROM public.s015_transaction_register WHERE kind = %s AND pg_xact_status(xid) IS DISTINCT FROM %s",
                ["ORGANISM_LOCK", "in progress"],
            )
        workers = self._workers()
        holder_pid = backend_pid(connection)
        closure_at = self._database_now()
        self._assert_visible_at(closure_at, closure)
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("SELECT public.s015_lock_organisms(ARRAY[%s]::uuid[])", [self.organism.organism_id])
                close = self._start_worker(
                    workers, lambda: self._effect_for_test(
                        self.service, closure, OrganismAction.EFFECT_APPLICATION_SETTINGS, received_at=closure_at,
                    ),
                )
                close_snapshot = wait_until_locked(cursor, close.pid(), holder_pid)
                submit = self._start_worker(workers, lambda: self._submit_for_test(applicant))
                submit_snapshot = wait_for_blocker_set(cursor, submit.pid(), {holder_pid, close.pid()})

                class _RollbackProbe(Exception):
                    pass

                try:
                    with transaction.atomic():
                        cursor.execute(
                            "SELECT id FROM public.core_loapplicationgateway "
                            "WHERE living_organism_id = %s FOR UPDATE NOWAIT",
                            [self.organism.pk],
                        )
                        gateway_id = cursor.fetchone()[0]
                        raise _RollbackProbe
                except _RollbackProbe:
                    pass
        finally:
            self.assertFalse(connection.in_atomic_block, "holder transaction must be released")
        close_result = self._join_worker(close)
        submit_result = self._join_worker(submit)
        self._assert_no_deadlock(close_result, submit_result)
        self.assertEqual(close_result, "ok", f"closure wait: {close_snapshot!r}")
        self.assertIsInstance(submit_result, S015ApplicationRefused, f"submission wait: {submit_snapshot!r}")
        self.assertEqual(str(submit_result), "S3FU gateway closed", f"submission wait: {submit_snapshot!r}")
        self.assertTrue(gateway_id, f"gateway probe snapshot: {submit_snapshot!r}")
        self.assertFalse(Application.objects.filter(applicant=applicant, living_organism=self.organism).exists())

    def test_cr1_submission_first_completes_open_then_closure_applies(self):
        open_version = self.open_gateway()
        closure = self.draft(OrganismAction.EFFECT_APPLICATION_SETTINGS, {
            "applications_open": False, "automatic_acceptance": False, "processing_responsibility": "LO",
        })
        applicant = self.applicant()
        workers = self._workers()
        holder_pid = backend_pid(connection)
        pair_key = self._pair_lock_key(applicant)
        closure_at = self._database_now()
        self._assert_visible_at(closure_at, closure)
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [pair_key])
                submit = self._start_worker(workers, lambda: self._submit_for_test(applicant))
                submit_snapshot = wait_until_locked(cursor, submit.pid(), holder_pid, locktype="advisory")
                close = self._start_worker(
                    workers, lambda: self._effect_for_test(
                        self.service, closure, OrganismAction.EFFECT_APPLICATION_SETTINGS, received_at=closure_at,
                    ),
                )
                close_snapshot = wait_until_locked(
                    cursor, close.pid(), submit.pid(), relations=ORGANISM_LOCK_RELATIONS,
                )
        finally:
            self.assertFalse(connection.in_atomic_block, "holder transaction must be released")
        submit_result = self._join_worker(submit)
        close_result = self._join_worker(close)
        self.assertEqual(submit_result, "ok", f"submission wait: {submit_snapshot!r}")
        self.assertEqual(close_result, "ok", f"closure wait: {close_snapshot!r}")
        application = Application.objects.get(applicant=applicant, living_organism=self.organism)
        self.assertEqual(application.settings_version_id, open_version.pk)
        self._assert_latest_settings_effect(closure)

    def test_cr2_closure_first_then_submission_refused_closed(self):
        self.open_gateway()
        closure = self.draft(OrganismAction.EFFECT_APPLICATION_SETTINGS, {
            "applications_open": False, "automatic_acceptance": False, "processing_responsibility": "LO",
        })
        applicant = self.applicant()
        workers = self._workers()
        holder_pid = backend_pid(connection)
        closure_at = self._database_now()
        self._assert_visible_at(closure_at, closure)
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id FROM public.core_loapplicationsettingsversion WHERE id = %s FOR UPDATE",
                    [closure.pk],
                )
                close = self._start_worker(
                    workers, lambda: self._effect_for_test(
                        self.service, closure, OrganismAction.EFFECT_APPLICATION_SETTINGS, received_at=closure_at,
                    ),
                )
                close_snapshot = wait_until_locked(
                    cursor, close.pid(), holder_pid, relations={"core_loapplicationsettingsversion"},
                )
                submit = self._start_worker(workers, lambda: self._submit_for_test(applicant))
                submit_snapshot = wait_until_locked(
                    cursor, submit.pid(), close.pid(), relations=ORGANISM_LOCK_RELATIONS,
                )
        finally:
            self.assertFalse(connection.in_atomic_block, "holder transaction must be released")
        close_result = self._join_worker(close)
        submit_result = self._join_worker(submit)
        self.assertEqual(close_result, "ok", f"closure wait: {close_snapshot!r}")
        self.assertIsInstance(submit_result, S015ApplicationRefused, f"submission wait: {submit_snapshot!r}")
        self.assertEqual(str(submit_result), "S3FU gateway closed", f"submission wait: {submit_snapshot!r}")
        self.assertFalse(Application.objects.filter(applicant=applicant, living_organism=self.organism).exists())

    def test_cr3_automatic_submission_first_accepted_under_recorded_version(self):
        automatic = self.automatic_gateway()
        _, welcome = self.automatic_service()
        changed = self.draft(OrganismAction.EFFECT_APPLICATION_SETTINGS, {
            "applications_open": True, "automatic_acceptance": False, "processing_responsibility": "LO",
        })
        applicant = self.applicant()
        workers = self._workers()
        holder_pid = backend_pid(connection)
        pair_key = self._pair_lock_key(applicant)
        change_at = self._database_now()
        self._assert_visible_at(change_at, changed)
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [pair_key])
                submit = self._start_worker(workers, lambda: self._submit_for_test(applicant))
                submit_snapshot = wait_until_locked(cursor, submit.pid(), holder_pid, locktype="advisory")
                change = self._start_worker(
                    workers, lambda: self._effect_for_test(
                        self.service, changed, OrganismAction.EFFECT_APPLICATION_SETTINGS, received_at=change_at,
                    ),
                )
                change_snapshot = wait_until_locked(
                    cursor, change.pid(), submit.pid(), relations=ORGANISM_LOCK_RELATIONS,
                )
        finally:
            self.assertFalse(connection.in_atomic_block, "holder transaction must be released")
        submit_result = self._join_worker(submit)
        change_result = self._join_worker(change)
        self.assertEqual(submit_result, "ok", f"submission wait: {submit_snapshot!r}")
        self.assertEqual(change_result, "ok", f"change wait: {change_snapshot!r}")
        application = Application.objects.get(applicant=applicant, living_organism=self.organism)
        decision = ApplicationDecision.objects.get(application=application)
        self.assertEqual(application.settings_version_id, automatic.pk)
        self.assertEqual((decision.route, decision.outcome), ("LO_AUTOMATIC", "ACCEPT"))
        self.assertEqual(welcome.calls, [(application.pk, decision.pk)])
        self._assert_latest_settings_effect(changed)

    def test_cc1_second_coordinator_change_refused_for_continuity_after_waiting(self):
        """The second change waits on the first at the organism lock, then continuity refuses it; no update is lost, and the second Coordinator must redraft against V1."""
        self.open_gateway()
        second = self._second_coordinator()
        self.assertNotEqual(self.actor.pk, second.pk)
        version_one = self.draft(OrganismAction.EFFECT_APPLICATION_SETTINGS, {
            "applications_open": True, "automatic_acceptance": False, "processing_responsibility": "LO",
        })
        version_two = self._draft_settings_as(second, {
            "applications_open": True, "automatic_acceptance": False, "processing_responsibility": "LO",
        })
        workers = self._workers()
        holder_pid = backend_pid(connection)
        effect_one_at = self._database_now()
        effect_two_at = self._database_now()
        self._assert_visible_at(effect_one_at, version_one)
        self._assert_visible_at(effect_two_at, version_two)
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id FROM public.core_loapplicationsettingsversion WHERE id = %s FOR UPDATE",
                    [version_one.pk],
                )
                effect_one = self._start_worker(
                    workers, lambda: self._effect_for_test(
                        self.service, version_one, OrganismAction.EFFECT_APPLICATION_SETTINGS,
                        received_at=effect_one_at,
                    ),
                )
                effect_one_snapshot = wait_until_locked(
                    cursor, effect_one.pid(), holder_pid, relations={"core_loapplicationsettingsversion"},
                )
                effect_two = self._start_worker(
                    workers, lambda: self._effect_for_test(
                        self.service, version_two, OrganismAction.EFFECT_APPLICATION_SETTINGS,
                        received_at=effect_two_at, actor=second,
                    ),
                )
                effect_two_snapshot = wait_until_locked(
                    cursor, effect_two.pid(), effect_one.pid(), relations=ORGANISM_LOCK_RELATIONS,
                )
        finally:
            self.assertFalse(connection.in_atomic_block, "holder transaction must be released")
        effect_one_result = self._join_worker(effect_one)
        effect_two_result = self._join_worker(effect_two)
        self.assertEqual(effect_one_result, "ok", f"T1 wait: {effect_one_snapshot!r}")
        self.assertIsInstance(effect_two_result, IntegrityError, f"T2 wait: {effect_two_snapshot!r}")
        self.assertIn("S3FU gateway effect: continuity", str(effect_two_result), f"T2 wait: {effect_two_snapshot!r}")
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT evidence_reference FROM public.core_livingorganismevent "
                "WHERE living_organism_id = %s AND action = %s AND evidence_reference IN (%s, %s) "
                "ORDER BY sequence",
                [self.organism.pk, OrganismAction.EFFECT_APPLICATION_SETTINGS.value,
                 f"application-settings-version:{version_one.version_uuid}",
                 f"application-settings-version:{version_two.version_uuid}"],
            )
            events = cursor.fetchall()
        self.assertEqual([row[0] for row in events], [
            f"application-settings-version:{version_one.version_uuid}",
        ])
        self._assert_latest_settings_effect(version_one)

    def test_automatic_acceptance_has_separate_applicant_and_composed_lo_acts(self):
        settings = self.automatic_gateway()
        care, welcome = self.automatic_service()
        applicant = self.applicant()
        result = self.submit(applicant)
        application = Application.objects.get(application_uuid=result.application_uuid)
        decision = ApplicationDecision.objects.get(application=application)
        engagement = ApplicationEngagement.objects.get(application=application)
        self.assertLessEqual(
            application.recorded_at,
            engagement.recorded_at,
            "automatic acceptance application must be recorded no later than its engagement",
        )
        self.assertLess(
            engagement.recorded_at,
            decision.recorded_at,
            "automatic acceptance fixture must establish engagement.recorded_at < decision.recorded_at",
        )
        state_at = application.effective_at
        self.assertEqual(
            self.service.application_status(application, state_at, engagement.recorded_at),
            "PENDING",
        )
        self.assertEqual(
            self.service.application_status(application, state_at, decision.recorded_at),
            "DECIDED",
        )
        admission = OrganismMembershipTransition.objects.get(
            membership__identity=applicant, membership__living_organism=self.organism,
            resulting_state="ACTIVE",
        )
        lineage = list(ApplicationLineageEvent.objects.filter(
            application_lineage__application=application,
        ).order_by("sequence"))
        chain_columns = ("actor_state", "entry_mode", "composing_rule_reference",
                         "composer_identity_id", "enterer_identity_id")
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT actor_state, entry_mode, composing_rule_reference, composer_identity_id, "
                "enterer_identity_id FROM core_organismmembershiptransition WHERE id = %s",
                [admission.pk],
            )
            admission_chain = dict(zip(chain_columns, cursor.fetchone()))
            cursor.execute(
                "SELECT e.actor_state, e.entry_mode, e.composing_rule_reference, "
                "e.composer_identity_id, e.enterer_identity_id "
                "FROM core_applicationlineageevent e "
                "JOIN core_applicationlineage l ON l.id = e.application_lineage_id "
                "WHERE l.application_id = %s ORDER BY e.sequence",
                [application.pk],
            )
            lineage_chain = [dict(zip(chain_columns, row)) for row in cursor.fetchall()]
        rule = f"application-settings-version:{settings.version_uuid}"
        self.assertEqual(application.actor_id, applicant.pk)
        self.assertEqual(application.enterer_id, applicant.pk)
        self.assertEqual(application.settings_version_id, settings.pk)
        self.assertEqual(engagement.route, "LO_AUTOMATIC")
        self.assertIsNone(engagement.actor_id)
        self.assertEqual(engagement.enterer_id, care.pk)
        self.assertEqual(engagement.settings_basis, rule)
        self.assertEqual(decision.route, "LO_AUTOMATIC")
        self.assertEqual(decision.outcome, "ACCEPT")
        self.assertEqual(decision.deciding_party_id, self.organism.pk)
        self.assertEqual(decision.executor_id, care.pk)
        self.assertIsNone(decision.responsible_coordinator_id)
        self.assertIsNone(decision.assignment_reference_id)
        self.assertEqual(admission_chain["actor_state"], "LIVING_ORGANISM")
        self.assertIsNone(admission.actor_id)
        self.assertEqual(admission_chain["entry_mode"], "COMPOSED")
        self.assertEqual(admission_chain["composing_rule_reference"], rule)
        self.assertEqual(admission_chain["composer_identity_id"], care.pk)
        self.assertEqual(admission_chain["enterer_identity_id"], care.pk)
        self.assertEqual(len(lineage), 2)
        self.assertEqual(len(lineage_chain), 2)
        self.assertEqual([event.action for event in lineage], ["SUBMIT_APPLICATION", "DECIDE_APPLICATION"])
        self.assertEqual(lineage[0].actor_id, applicant.pk)
        self.assertEqual(lineage_chain[0]["entry_mode"], "TRANSPORT")
        self.assertEqual(lineage_chain[1]["actor_state"], "LIVING_ORGANISM")
        self.assertIsNone(lineage[1].actor_id)
        self.assertEqual(lineage_chain[1]["entry_mode"], "COMPOSED")
        self.assertEqual(lineage_chain[1]["composing_rule_reference"], rule)
        self.assertEqual(lineage_chain[1]["composer_identity_id"], care.pk)
        self.assertEqual(lineage_chain[1]["enterer_identity_id"], care.pk)
        self.assertNotEqual(lineage[0].authority_decision_reference, lineage[1].authority_decision_reference)
        self.assertEqual(welcome.calls, [(application.pk, decision.pk)])
        self.assertEqual(OrganismMembership.objects.get(identity=applicant, living_organism=self.organism).state, "ACTIVE")
        self.assertFalse(OrganismMembershipTransition.objects.filter(
            membership__identity=applicant, resulting_state="PROBATIONARY",
        ).exists())

    def test_automatic_real_use_refuses_before_application_write(self):
        self.automatic_gateway()
        applicant = self.applicant()
        before = (Application.objects.count(), ApplicationLineageEvent.objects.count(), ApplicationDecision.objects.count())
        with self.assertRaises(S015OperationNotYetImplemented) as refused:
            self.submit(applicant)
        self.assertEqual(refused.exception.limit_id, "S3-IL-CARE-EXEC")
        self.assertEqual(
            (Application.objects.count(), ApplicationLineageEvent.objects.count(), ApplicationDecision.objects.count()),
            before,
        )

    def test_automatic_welcome_unavailable_rolls_back_every_row(self):
        self.automatic_gateway()
        care, _ = self.automatic_service()
        preexisting_applicant = self.applicant()
        tables = {
            "core_application": Application,
            "core_organismmembership": OrganismMembership,
            "core_organismmembershiptransition": OrganismMembershipTransition,
            "core_applicationengagement": ApplicationEngagement,
            "core_applicationdecision": ApplicationDecision,
            "core_applicationdecisioneffect": ApplicationDecisionEffect,
            "core_applicationlineage": ApplicationLineage,
            "core_applicationlineageevent": ApplicationLineageEvent,
            "core_organismcommandreceipt": OrganismCommandReceipt,
        }
        ids_before_preexisting = {
            table: set(model.objects.values_list("pk", flat=True))
            for table, model in tables.items()
        }
        self.submit(preexisting_applicant)
        preexisting_ids = {
            table: set(model.objects.values_list("pk", flat=True)) - ids_before_preexisting[table]
            for table, model in tables.items()
        }
        before_counts = {
            table: model.objects.count()
            for table, model in tables.items()
        }
        self.service = OrganismApplicationService(
            authority=S015Authority(provider=_CoordinatorProvider()), clock=lambda: NOW,
            care_executor=FakeCareExecutor(care),
        )
        applicant = self.applicant()
        with self.assertRaises(S015OperationNotYetImplemented) as refused:
            self.submit(applicant)
        self.assertEqual(refused.exception.limit_id, "S3-IL-NOTICE")
        self.assertEqual(
            {table: model.objects.count() for table, model in tables.items()},
            before_counts,
        )
        self.assertFalse(Application.objects.filter(applicant=applicant, living_organism=self.organism).exists())
        self.assertFalse(OrganismMembership.objects.filter(identity=applicant, living_organism=self.organism).exists())
        for table, model in tables.items():
            self.assertTrue(
                preexisting_ids[table].issubset(set(model.objects.values_list("pk", flat=True))),
                table,
            )

    def test_automatic_closed_gateway_refuses_without_admission(self):
        settings = self.automatic_gateway()
        self.automatic_service()
        applicant = self.applicant()
        # H100 chunk 4 tests v1.3 (CH-42): the CLOSE and the submission take database-clock times after
        # the public gateway effect commits. That effect's LO event carries the intake's received_at, so
        # a CLOSE at the setUp time NOW (same effective_at, earlier received_at) would fold before it.
        closed_at = self._database_now()
        self._assert_visible_at(closed_at, settings)
        with transaction.atomic(), connection.cursor() as cursor:
            self.organism.refresh_from_db()
            append_event(cursor, "core_livingorganismevent", self.organism.pk,
                         self.organism.head_event.sequence + 1, "CLOSE", "ACTIVE", "CLOSED",
                         self.actor.pk, self.ids["basis"], fresh(), pred_id=self.organism.head_event_id,
                         eff=closed_at, occ=closed_at, rec=closed_at)
            refresh(cursor, "core_livingorganism", self.organism.pk, closed_at)
        submit_at = self._database_now()
        self._assert_visible_at(submit_at)
        token = fresh()
        with self.assertRaisesMessage(S015ApplicationRefused, "S3FU gateway closed"):
            self.service.submit_application(SubmitApplicationCommand(
                credential=applicant.credential, living_organism_id=self.organism.organism_id,
                request_reference=f"s3:submit:{token}", idempotency_key=f"s3:submit:{token}",
                occurred_at=submit_at, effective_at=submit_at, answer="A test-only applicant answer",
            ))
        self.assertFalse(Application.objects.filter(applicant=applicant, living_organism=self.organism).exists())

    def test_automatic_off_remains_pending(self):
        applicant = self.applicant()
        result = self.submit(applicant)
        application = Application.objects.get(application_uuid=result.application_uuid)
        self.assertFalse(ApplicationDecision.objects.filter(application=application).exists())
        self.assertEqual(ApplicationLineageEvent.objects.filter(
            application_lineage__application=application,
        ).count(), 1)

    def test_automatic_route_vocabulary_and_off_version_sql_refusal(self):
        self.assertEqual(
            {value for value, _ in ApplicationEngagement.Route.choices},
            {"LO_CQ", "LO_CSA", "LO_AUTOMATIC", "CIRCLE_CQ", "CIRCLE_CSA"},
        )
        seeded = self.gateway
        applicant = self.applicant()
        application_result = self.submit(applicant)
        application = Application.objects.get(application_uuid=application_result.application_uuid)
        off = LOApplicationSettingsVersion.objects.get(pk=seeded["core_loapplicationsettingsversion"])
        rule = f"application-settings-version:{off.version_uuid}"
        with self.assertRaisesMessage(IntegrityError, "S3FU automatic engagement refused"):
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO core_applicationengagement (application_id, engagement_ordinal, route, "
                    "deciding_party_id, engagement_reference, settings_basis, actor_id, enterer_id, "
                    "occurred_at, effective_at, received_at) "
                    "VALUES (%s, 1, 'LO_AUTOMATIC', %s, %s, %s, NULL, %s, %s, %s, %s)",
                    [application.pk, self.organism.pk, rule, rule, self.actor.pk, NOW, NOW, NOW],
                )
        self.assertFalse(ApplicationEngagement.objects.filter(application=application).exists())

    def test_automatic_uses_recorded_setting_after_later_effective_change(self):
        setting = self.automatic_gateway()
        self.automatic_service()
        applicant = self.applicant()
        accept = self.service._automatic_accept

        def change_then_accept(application, care, request_reference, idempotency_key, fingerprint):
            # Test-only effect fixture: interposes a later effective setting, not a concurrent race.
            later_at = self._database_now()
            later = LOApplicationSettingsVersion.objects.create(
                living_organism=self.organism, actor=self.actor, enterer=self.actor,
                supersedes=setting, occurred_at=later_at,
                effective_at=later_at, received_at=later_at,
                applications_open=False, automatic_acceptance=False, processing_responsibility="LO",
            )
            with connection.cursor() as cursor:
                self.organism.refresh_from_db()
                predecessor = self.organism.head_event
                append_event(
                    cursor, "core_livingorganismevent", self.organism.pk, predecessor.sequence + 1,
                    "EFFECT_APPLICATION_SETTINGS", predecessor.resulting_state, predecessor.resulting_state,
                    None, self.ids["basis"], fresh(), pred_id=predecessor.pk,
                    eff=later_at, occ=later_at, rec=later_at,
                    extra={"actor_state": "LIVING_ORGANISM", "actor_access_epoch": None,
                           "enterer_identity_id": self.actor.pk,
                           "evidence_reference": f"application-settings-version:{later.version_uuid}"},
                )
                refresh(cursor, "core_livingorganism", self.organism.pk, later_at)
            accepted = accept(application, care, request_reference, idempotency_key, fingerprint)
            self.assertEqual(gateway_version_status("default", later, database_now()), "EFFECTIVE")
            return accepted

        self.service._automatic_accept = change_then_accept
        result = self.submit(applicant)
        application = Application.objects.get(application_uuid=result.application_uuid)
        self.assertEqual(application.settings_version_id, setting.pk)
        self.assertEqual(ApplicationDecision.objects.get(application=application).route, "LO_AUTOMATIC")
        self.assertEqual(ApplicationEngagement.objects.get(application=application).settings_basis,
                         f"application-settings-version:{setting.version_uuid}")

    def test_0030_reverse_refuses_live_automatic_rows(self):
        self.automatic_gateway()
        self.automatic_service()
        self.submit(self.applicant())
        migration = importlib.import_module("core.migrations.0030_s015_automatic_application")
        with self.assertRaisesMessage(RuntimeError, "LO_AUTOMATIC rows require forward correction"):
            migration.reverse(None, type("Editor", (), {"connection": connection})())
        self.assertTrue(ApplicationDecision.objects.filter(route="LO_AUTOMATIC").exists())

    def test_0033_reverse_allows_automatic_settings_references(self):
        self.automatic_gateway()
        self.automatic_service()
        self.submit(self.applicant())
        migration = importlib.import_module("core.migrations.0033_s015_application_authority")

        class Rollback(Exception):
            pass

        class Editor:
            def __init__(self):
                self.connection = globals()["connection"]

            @staticmethod
            def execute(sql, params=None):
                with connection.cursor() as cursor:
                    cursor.execute(sql, params)

        self.assertTrue(ApplicationEngagement.objects.filter(route="LO_AUTOMATIC").exists())
        with self.assertRaises(Rollback):
            with transaction.atomic():
                migration.reverse(None, Editor())
                raise Rollback()

    def test_automatic_composed_admission_direct_sql_refusals(self):
        self.automatic_gateway()
        care, _ = self.automatic_service()
        applicant = self.applicant()
        result = self.submit(applicant)
        admission = OrganismMembershipTransition.objects.get(
            membership__identity=applicant, membership__living_organism=self.organism,
            resulting_state="ACTIVE",
        )
        off_version = LOApplicationSettingsVersion.objects.create(
            living_organism=self.organism, actor=self.actor, enterer=self.actor,
            occurred_at=NOW, effective_at=NOW, received_at=NOW,
            applications_open=True, automatic_acceptance=False, processing_responsibility="LO",
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'core_organismmembershiptransition' "
                "AND is_generated = 'NEVER' AND column_name NOT IN ('id', 'recorded_at') "
                "ORDER BY ordinal_position"
            )
            columns = [row[0] for row in cursor.fetchall()]
        for label, overrides, message in (
            ("wrong recorded rule", {"composing_rule_reference": "application-settings-version:" + str(uuid4())},
             "S3FU composed LO admission refused"),
            ("unrecorded off version", {"composing_rule_reference": f"application-settings-version:{off_version.version_uuid}"},
             "S3FU composed LO admission refused"),
            ("CARE as actor", {"actor_id": care.pk}, "S3FU composed LO admission refused"),
            ("TRANSPORT with automatic route", {
                "entry_mode": "TRANSPORT", "composing_rule_state": "NA_TRANSPORT",
                "composing_rule_reference": None, "composer_state": "NA_TRANSPORT",
                "composer_identity_id": None,
            }, "S3 LO actor lacks matching ACCEPT decision"),
        ):
            with self.subTest(label=label), self.assertRaisesMessage(IntegrityError, message):
                with transaction.atomic(), connection.cursor() as cursor:
                    event_uuid = str(uuid4())
                    values = {
                        "event_uuid": ("%s", event_uuid), "sequence": ("t.sequence + 1", None),
                        "predecessor_id": ("t.id", None),
                        "predecessor_sequence": ("t.sequence", None), "l1_commitment": ("NULL", None),
                        "predecessor_l1_commitment": ("t.l1_commitment", None), "parts_commitment": (
                            "public.s015_0022_digest('s015p1', public.s015_0022_parts_preimage("
                            "'core_organismmembershiptransition', %s::uuid))", event_uuid),
                        "lineage_reference": ("%s", f"s015l1:{'a' * 64}"),
                        "effective_at": ("%s", NOW + timedelta(seconds=1)),
                        "occurred_at": ("%s", NOW + timedelta(seconds=1)),
                        "received_at": ("%s", NOW + timedelta(seconds=1)),
                    }
                    for column, value in overrides.items():
                        values[column] = ("%s", value)
                    parameters = []
                    expressions = []
                    for column in columns:
                        expression, parameter = values.get(column, (f"t.{column}", None))
                        expressions.append(expression)
                        if "%s" in expression:
                            parameters.append(parameter)
                    cursor.execute(
                        f"INSERT INTO core_organismmembershiptransition ({', '.join(columns)}) "
                        f"SELECT {', '.join(expressions)} FROM core_organismmembershiptransition t WHERE t.id = %s",
                        [*parameters, admission.pk],
                    )

    def test_automatic_decision_direct_sql_refusals(self):
        class Rollback(Exception):
            pass

        self.automatic_gateway()
        care, _ = self.automatic_service()
        applicant = self.applicant()
        intake_count = CommandIntake.objects.count()
        with self.assertRaises(Rollback):
            with transaction.atomic():
                # Test-only incomplete automatic act; the outer transaction is always rolled back.
                self.service._automatic_accept = lambda *args: None
                result = self._submit_for_test(applicant)
                application = Application.objects.get(application_uuid=result.application_uuid)
                rule = f"application-settings-version:{application.settings_version.version_uuid}"
                ApplicationEngagement.objects.create(
                    application=application, engagement_ordinal=1, route="LO_AUTOMATIC",
                    deciding_party=self.organism, engagement_reference=rule, settings_basis=rule,
                    actor=None, enterer=care, occurred_at=NOW, effective_at=NOW, received_at=NOW,
                )
                for label, outcome, coordinator in (
                    ("DECLINE", "DECLINE", None),
                    ("Coordinator fields", "ACCEPT", self.actor.pk),
                ):
                    with self.subTest(label=label), self.assertRaisesMessage(
                        IntegrityError, "S3FU automatic decision refused",
                    ):
                        with transaction.atomic(), connection.cursor() as cursor:
                            cursor.execute(
                                "INSERT INTO core_applicationdecision (decision_uuid, application_id, route, "
                                "deciding_party_id, outcome, responsible_coordinator_id, "
                                "authority_decision_reference, executor_id, enterer_id, "
                                "occurred_at, effective_at, received_at) "
                                "VALUES (%s, %s, 'LO_AUTOMATIC', %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                                [str(uuid4()), application.pk, self.organism.pk, outcome, coordinator,
                                 "s015d1:" + "a" * 64, care.pk, care.pk, NOW, NOW, NOW],
                            )
                raise Rollback()
        self.assertEqual(CommandIntake.objects.count(), intake_count)

    def test_name_dependency_default_draft_and_unselected_settings(self):
        no_name_organism = LivingOrganism.objects.get(pk=self.no_name_ids["lo"])
        with self.assertRaises(S015OperationNotYetImplemented) as refused:
            default_application_question(no_name_organism, FormationLONameSource())
        self.assertEqual(refused.exception.limit_id, "S3-IL-LONAME")
        with self.assertRaises(S015OperationNotYetImplemented):
            LOApplicationQuestionDraftForm(living_organism=no_name_organism)
        production = OrganismApplicationService(
            authority=S015Authority(provider=_CoordinatorProvider()), clock=lambda: NOW,
        )
        no_name_actor = Identity.objects.select_related("credential").get(
            pk=self.no_name_ids["identity"],
        )
        with self.assertRaises(S015OperationNotYetImplemented) as refused:
            production.save_gateway_draft(GatewayDraftCommand(
                credential=no_name_actor.credential, living_organism_id=no_name_organism.organism_id,
                action=OrganismAction.EFFECT_APPLICATION_QUESTION,
                request_reference=f"s3:name:{self.tag}", idempotency_key=f"s3:name:{self.tag}",
                occurred_at=NOW, effective_at=NOW, fields={},
            ))
        self.assertEqual(refused.exception.limit_id, "S3-IL-LONAME")
        form = LOApplicationQuestionDraftForm(
            living_organism=self.organism,
        )
        founding_name = LOFoundingContentVersion.objects.get(
            living_organism=self.organism,
            version=1,
        ).name
        self.assertEqual(form.initial, {
            "question_text": f"Why do you want to join {founding_name}?",
            "answer_format": "LONG_ANSWER",
        })
        receipts_before_draft = OrganismCommandReceipt.objects.count()
        question = self.draft(OrganismAction.EFFECT_APPLICATION_QUESTION, {})
        self.assertEqual(OrganismCommandReceipt.objects.count(), receipts_before_draft)
        self.assertEqual(question.question_text, form.initial["question_text"])
        self.assertEqual(question.answer_format, "LONG_ANSWER")
        settings_form = LOApplicationSettingsDraftForm()
        for field in ("applications_open", "automatic_acceptance"):
            self.assertNotIn(field, settings_form.initial)
            self.assertIsNone(settings_form.fields[field].initial)
            self.assertTrue(settings_form.fields[field].required)
        self.assertFalse(LOApplicationSettingsDraftForm(data={}).is_valid())

    def test_draft_effect_supersede_and_immutable_answer_text(self):
        self.service._name_source = _TestFixtureLONameSource()
        action = OrganismAction.EFFECT_APPLICATION_QUESTION
        first = self.draft(action, {})
        self.assertEqual(gateway_version_status("default", first, database_now()), "DRAFT")
        before = self.organism.state
        first_effect = self.effect(first, action)
        self.assertEqual(first_effect.version_uuid, first.version_uuid)
        self.assertEqual(first_effect.action, action)
        self.assertEqual(first_effect.lineage_reference, LivingOrganismEvent.objects.get(
            event_uuid=first_effect.event_uuid,
        ).lineage_reference)
        first_event = LivingOrganismEvent.objects.get(event_uuid=first_effect.event_uuid)
        self.assertEqual(first_event.evidence_reference, f"application-question-version:{first.version_uuid}")
        self.assertEqual(first_event.prior_state, first_event.resulting_state)
        self.assertEqual(gateway_version_status("default", first, database_now()), "EFFECTIVE")
        settings = self.draft(OrganismAction.EFFECT_APPLICATION_SETTINGS, {
            "applications_open": True, "automatic_acceptance": False, "processing_responsibility": "LO",
        })
        self.effect(settings, OrganismAction.EFFECT_APPLICATION_SETTINGS)
        application_result = self.submit(self.applicant())
        application = Application.objects.get(application_uuid=application_result.application_uuid)
        with self.assertRaisesMessage(IntegrityError, "effective and immutable"):
            with transaction.atomic():
                LOApplicationQuestionVersion.objects.filter(pk=first.pk).update(question_text="silent rename")
        self.service._name_source = _TestFixtureLONameSource("TEST-FIXTURE-NAME: New Riverside")
        second = self.draft(action, {})
        self.assertEqual(second.supersedes_id, first.pk)
        self.assertEqual(first.question_text, "Why do you want to join TEST-FIXTURE-NAME: Riverside Commons?")
        self.assertIn("New Riverside", second.question_text)
        self.assertEqual(gateway_version_status("default", first, database_now()), "EFFECTIVE")
        self.effect(second, action)
        self.assertEqual(gateway_version_status("default", first, database_now()), "SUPERSEDED")
        self.organism.refresh_from_db()
        self.assertEqual(self.organism.state, before)
        self.assertEqual(LOApplicationQuestionVersion.objects.get(pk=first.pk).question_text, first.question_text)
        application.refresh_from_db()
        self.assertEqual(application.question_version_id, first.pk)
        self.assertEqual(application.question_version.question_text, first.question_text)

    def test_effect_receipt_replays_without_second_event_and_rejects_mismatch(self):
        action = OrganismAction.EFFECT_APPLICATION_SETTINGS
        version = self.draft(action, {
            "applications_open": True,
            "automatic_acceptance": False,
            "processing_responsibility": "LO",
        })
        first = self.effect(version, action, "receipt-replay")
        receipt = OrganismCommandReceipt.objects.get(
            actor=self.actor,
            action=action.value,
            idempotency_key="s3:effect:receipt-replay",
        )
        self.assertNotIn("event_id", receipt.result_payload)
        self.assertEqual(receipt.result_payload["event_uuid"], str(first.event_uuid))
        event_count = LivingOrganismEvent.objects.filter(living_organism=self.organism).count()

        replay = self.effect(version, action, "receipt-replay")
        self.assertEqual(replay, first)
        self.assertEqual(
            LivingOrganismEvent.objects.filter(living_organism=self.organism).count(),
            event_count,
        )
        self.assertFalse(hasattr(first, "event_id"))
        self.assertEqual(withheld_fields(first), ())
        self.assertIsInstance(first.event_uuid, UUID)

        office = ContextualRoleAssignment.objects.get(
            membership__identity=self.actor,
            membership__living_organism=self.organism,
            role_definition__code="COORDINATOR",
            role_definition__scope="LIVING_ORGANISM",
            state="ACTIVE",
        )
        OrganismRoleService(
            authority=S015Authority(provider=_CoordinatorProvider()),
            clock=lambda: NOW,
        ).transition_assignment(TransitionAssignmentCommand(
            credential=self.actor.credential,
            request_reference="s3:effect:receipt-replay:end-office",
            idempotency_key="s3:effect:receipt-replay:end-office",
            occurred_at=NOW,
            effective_at=NOW,
            assignment_uuid=office.assignment_uuid,
            action="END",
            resulting_state="ENDED",
        ))

        mismatch = EffectGatewayVersionCommand(
            credential=self.actor.credential,
            living_organism_id=self.organism.organism_id,
            action=action,
            version_uuid=version.version_uuid,
            request_reference="s3:effect:receipt-replay",
            idempotency_key="s3:effect:receipt-replay",
            occurred_at=NOW,
            effective_at=NOW + timedelta(minutes=1),
        )
        # H100 chunk 4 (C4 §F3 row 5): changed content under the occupied key is refused at admission
        # (CI:1365-1372), the same class as the receipt helper's refusal; the class is what is asserted.
        with self.assertRaises(S015CommandReplayMismatch):
            self.service.effect_gateway_version(mismatch)
        self.assertEqual(
            LivingOrganismEvent.objects.filter(living_organism=self.organism).count(),
            event_count,
        )
        lost_standing_replay = self.effect(version, action, "receipt-replay")
        self.assertEqual(lost_standing_replay.event_uuid, WithheldField("event_uuid", "PROCESSING"))
        self.assertEqual(lost_standing_replay.event_sequence, WithheldField("event_sequence", "PROCESSING"))
        self.assertEqual(
            set(withheld_fields(lost_standing_replay)),
            {
                WithheldField("event_uuid", "PROCESSING"),
                WithheldField("event_sequence", "PROCESSING"),
            },
        )

    def test_submission_replay_does_not_wait_on_the_care_identity_row(self):
        self.automatic_gateway()
        care, _welcome = self.automatic_service()
        applicant = self.applicant()
        command = SubmitApplicationCommand(
            credential=applicant.credential,
            living_organism_id=self.organism.organism_id,
            request_reference="s3:r4:care-held:submit",
            idempotency_key="s3:r4:care-held:submit",
            occurred_at=NOW,
            effective_at=NOW,
            answer="TEST FIXTURE: replay with CARE identity held",
        )
        fresh_result = self.service.submit_application(command)
        intake_received_at = CommandIntake.objects.get(
            actor_id=applicant.pk,
            entry=IntakeEntry.SUBMIT_APPLICATION.value,
            action=OrganismAction.SUBMIT_APPLICATION.value,
            idempotency_key=command.idempotency_key,
        ).received_at
        provider_calls = len(self.provider.requests)
        decisions = ApplicationDecision.objects.count()
        receipts = OrganismCommandReceipt.objects.count()
        intakes = CommandIntake.objects.count()

        def hold_care_identity(cursor):
            cursor.execute(
                "SELECT id FROM public.core_identity WHERE id = %s FOR UPDATE",
                [care.pk],
            )

        replay = _run_with_external_locks(
            self,
            hold_care_identity,
            lambda _holder_pid, _start_worker, run_bounded: run_bounded(
                lambda: self._submit_for_test(
                    applicant,
                    command=command,
                    received_at=intake_received_at,
                ),
            ),
        )
        self.assertEqual(replay, fresh_result)
        self.assertEqual(len(self.provider.requests), provider_calls)
        self.assertEqual(ApplicationDecision.objects.count(), decisions)
        self.assertEqual(OrganismCommandReceipt.objects.count(), receipts)
        self.assertEqual(CommandIntake.objects.count(), intakes)

    def test_nonmember_gateway_effect_refuses_without_waiting_on_register_lock(self):
        from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider

        self.open_gateway()
        version = self.draft(OrganismAction.EFFECT_APPLICATION_QUESTION, {})
        nonmember = self.applicant()
        service = OrganismApplicationService(
            authority=S015Authority(provider=ProductionApplicationAuthorityProvider()),
            clock=lambda: NOW,
        )
        command = EffectGatewayVersionCommand(
            credential=nonmember.credential,
            living_organism_id=self.organism.organism_id,
            action=OrganismAction.EFFECT_APPLICATION_QUESTION,
            version_uuid=version.version_uuid,
            request_reference="s3:r4:nonmember-effect",
            idempotency_key="s3:r4:nonmember-effect",
            occurred_at=NOW,
            effective_at=NOW,
        )
        # H100 chunk 4 (C4 §F3 row 4, OPEN-5 (a)): the executor with a test-only bound request inside
        # run_bounded's transaction. CH-15 (iv): the refusal fires at the membership read, before any
        # historical read, so the fixture time NOW stands.
        bound = BoundRequest.for_test(
            IntakeEntry.EFFECT_GATEWAY_VERSION,
            command,
            received_at=NOW,
            actor=ActorRef(pk=nonmember.pk, identity_id=nonmember.identity_id, access_epoch=nonmember.access_epoch),
        )
        decisions = AuthorityDecision.objects.count()
        receipts = OrganismCommandReceipt.objects.count()

        def acquire_register_lock(cursor):
            cursor.execute(
                "SELECT public.s015_lock_organisms(ARRAY[%s]::uuid[])",
                [self.organism.organism_id],
            )

        refusal = _run_with_external_locks(
            self,
            acquire_register_lock,
            lambda _holder_pid, _start_worker, run_bounded: run_bounded(
                lambda: service._execute_effect_gateway_version(command, bound),
            ),
        )
        self.assertIsInstance(refusal, S015ApplicationAuthorityUnavailable)
        self.assertEqual(AuthorityDecision.objects.count(), decisions)
        self.assertEqual(OrganismCommandReceipt.objects.count(), receipts)

    def test_incomplete_version_and_unresolved_placeholder_are_separate_refusals(self):
        version = self.draft(OrganismAction.EFFECT_APPLICATION_SETTINGS, {})
        version.applications_open = True
        version.save(update_fields=["applications_open"])
        with self.assertRaisesMessage(S015ApplicationRefused, "S3FU gateway effect: version incomplete"):
            self.effect(version, OrganismAction.EFFECT_APPLICATION_SETTINGS)
        with self.assertRaisesMessage(IntegrityError, "S3FU gateway effect: version incomplete"):
            with transaction.atomic():
                self.direct_effect(OrganismAction.EFFECT_APPLICATION_SETTINGS,
                                   f"application-settings-version:{version.version_uuid}")
        version.automatic_acceptance = False
        version.processing_responsibility = "LO"
        version.save(update_fields=["automatic_acceptance", "processing_responsibility"])
        self.effect(version, OrganismAction.EFFECT_APPLICATION_SETTINGS)
        question = self.draft(OrganismAction.EFFECT_APPLICATION_QUESTION, {
            "question_text": "Why do you want to join [LO name]?", "answer_format": "LONG_ANSWER",
        })
        with self.assertRaisesMessage(IntegrityError, "S3FU gateway effect: unresolved LO name placeholder"):
            self.effect(question, OrganismAction.EFFECT_APPLICATION_QUESTION)

    def test_question_options_and_configuration_are_complete_before_effect(self):
        question_action = OrganismAction.EFFECT_APPLICATION_QUESTION
        question = self.draft(question_action, {
            "question_text": "Choose a test-only option", "answer_format": "MULTIPLE_CHOICE",
        })
        with self.assertRaisesMessage(S015ApplicationRefused, "version incomplete"):
            self.effect(question, question_action)
        with self.assertRaisesMessage(IntegrityError, "version incomplete"):
            with transaction.atomic():
                self.direct_effect(question_action, f"application-question-version:{question.version_uuid}")
        self.service.save_gateway_draft(GatewayDraftCommand(
            credential=self.actor.credential, living_organism_id=self.organism.organism_id,
            action=question_action, version_uuid=question.version_uuid,
            request_reference=f"s3:options:{self.tag}", idempotency_key=f"s3:options:{self.tag}",
            occurred_at=NOW, effective_at=NOW, fields={"options": ["A", "B"]},
        ))
        question.refresh_from_db()
        self.effect(question, question_action)
        config_action = OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION
        configuration = self.draft(config_action, {"cq_active": False})
        with self.assertRaisesMessage(S015ApplicationRefused, "version incomplete"):
            self.effect(configuration, config_action)
        with self.assertRaisesMessage(IntegrityError, "version incomplete"):
            with transaction.atomic():
                self.direct_effect(config_action, f"governance-configuration-version:{configuration.version_uuid}")
        self.service.save_gateway_draft(GatewayDraftCommand(
            credential=self.actor.credential, living_organism_id=self.organism.organism_id,
            action=config_action, version_uuid=configuration.version_uuid,
            request_reference=f"s3:config:{self.tag}", idempotency_key=f"s3:config:{self.tag}",
            occurred_at=NOW, effective_at=NOW,
            fields={"inherit_portal_cq_settings": False, "require_cq_to_change_lo_settings": False,
                    "require_cq_to_change_application_question": False},
        ))
        configuration.refresh_from_db()
        self.effect(configuration, config_action)

    def test_governance_choice_defaults_copies_and_remains_effective(self):
        action = OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION
        common = {
            "cq_active": False,
            "inherit_portal_cq_settings": False,
            "require_cq_to_change_lo_settings": False,
            "require_cq_to_change_application_question": False,
        }
        first = self.draft(action, common)
        self.assertIs(first.governance_enabled, True)
        self.effect(first, action)
        first.refresh_from_db()
        first_event = LivingOrganismEvent.objects.get(
            living_organism=self.organism,
            action=action.value,
            evidence_reference=f"governance-configuration-version:{first.version_uuid}",
        )
        self.assertEqual(
            _effective_gateway_version("default", self.organism, action, first.effective_at, first_event.recorded_at),
            first,
        )

        second = self.draft(action, {**common, "governance_enabled": False})
        self.assertIs(second.governance_enabled, False)
        self.assertEqual(second.supersedes_id, first.pk)
        self.effect(second, action)
        second.refresh_from_db()
        second_event = LivingOrganismEvent.objects.get(
            living_organism=self.organism,
            action=action.value,
            evidence_reference=f"governance-configuration-version:{second.version_uuid}",
        )
        self.assertEqual(
            _effective_gateway_version("default", self.organism, action, second.effective_at, second_event.recorded_at),
            second,
        )

        third = self.draft(action, common)
        self.assertIs(third.governance_enabled, False)
        self.assertEqual(third.supersedes_id, second.pk)
        self.effect(third, action)
        third.refresh_from_db()
        third_event = LivingOrganismEvent.objects.get(
            living_organism=self.organism,
            action=action.value,
            evidence_reference=f"governance-configuration-version:{third.version_uuid}",
        )
        self.assertEqual(
            _effective_gateway_version("default", self.organism, action, third.effective_at, third_event.recorded_at),
            third,
        )

        fourth = self.draft(action, {**common, "governance_enabled": True})
        self.assertIs(fourth.governance_enabled, True)
        self.assertEqual(fourth.supersedes_id, third.pk)
        self.effect(fourth, action)
        fourth.refresh_from_db()
        fourth_event = LivingOrganismEvent.objects.get(
            living_organism=self.organism,
            action=action.value,
            evidence_reference=f"governance-configuration-version:{fourth.version_uuid}",
        )
        self.assertEqual(
            _effective_gateway_version("default", self.organism, action, fourth.effective_at, fourth_event.recorded_at),
            fourth,
        )

    def test_governance_configuration_cannot_be_effected_without_confirmation(self):
        action = OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION
        configuration = self.draft(action, {
            "cq_active": False,
            "governance_enabled": None,
            "inherit_portal_cq_settings": False,
            "require_cq_to_change_lo_settings": False,
            "require_cq_to_change_application_question": False,
        })
        before_events = LivingOrganismEvent.objects.filter(
            living_organism=self.organism, action=action.value,
        ).count()
        with self.assertRaisesMessage(
            S015ApplicationRefused,
            "S3FU governance configuration effect: GOVERNANCE choice is not confirmed",
        ):
            self.effect(configuration, action)
        configuration.refresh_from_db()
        self.assertIsNone(configuration.governance_enabled)
        self.assertEqual(
            LivingOrganismEvent.objects.filter(
                living_organism=self.organism, action=action.value,
            ).count(),
            before_events,
        )

    def test_effecting_governance_choice_confers_no_membership_circle_or_office(self):
        action = OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION
        configuration = self.draft(action, {
            "cq_active": False,
            "inherit_portal_cq_settings": False,
            "require_cq_to_change_lo_settings": False,
            "require_cq_to_change_application_question": False,
        })
        tables = (
            "core_organismmembership",
            "core_organismmembershiptransition",
            "core_circle",
            "core_contextualroleassignment",
            "core_contextualroleassignmentstateevent",
            "core_organismroledefinition",
        )
        before = {}
        with connection.cursor() as cursor:
            for table in tables:
                cursor.execute(f"SELECT count(*) FROM public.{table}")
                before[table] = cursor.fetchone()[0]

        self.effect(configuration, action)

        with connection.cursor() as cursor:
            for table in tables:
                cursor.execute(f"SELECT count(*) FROM public.{table}")
                self.assertEqual(cursor.fetchone()[0], before[table], table)

    def test_effect_guard_attributes_evidence_organism_continuity_and_duplicate(self):
        gateway = self.gateway
        action = OrganismAction.EFFECT_APPLICATION_SETTINGS
        for reference, message in (
            ("not-a-version", "evidence reference malformed"),
            (f"application-settings-version:{uuid4()}", "version not found"),
        ):
            with self.subTest(message=message), self.assertRaisesMessage(IntegrityError, message):
                with transaction.atomic():
                    self.direct_effect(action, reference)
        existing = LOApplicationSettingsVersion.objects.get(pk=gateway["core_loapplicationsettingsversion"])
        with self.assertRaisesMessage(IntegrityError, "already effective"):
            with transaction.atomic():
                self.direct_effect(action, f"application-settings-version:{existing.version_uuid}")
        other = prepare_route(tag=f"other-{fresh()}")
        other_ids, _ = route_fixture_state(other)
        other_at = route_test_now(other)
        other_version = LOApplicationSettingsVersion.objects.create(
            living_organism_id=other_ids["lo"], actor=self.actor, enterer=self.actor,
            occurred_at=other_at, effective_at=other_at, received_at=other_at,
            applications_open=True, automatic_acceptance=False, processing_responsibility="LO",
        )
        with self.assertRaisesMessage(IntegrityError, "version of another organism"):
            with transaction.atomic():
                self.direct_effect(action, f"application-settings-version:{other_version.version_uuid}")
        without_predecessor = LOApplicationSettingsVersion.objects.create(
            living_organism=self.organism, actor=self.actor, enterer=self.actor,
            occurred_at=NOW, effective_at=NOW, received_at=NOW,
            applications_open=True, automatic_acceptance=False, processing_responsibility="LO",
        )
        with self.assertRaisesMessage(IntegrityError, "continuity"):
            with transaction.atomic():
                self.direct_effect(action, f"application-settings-version:{without_predecessor.version_uuid}")
        without_predecessor.applications_open = False
        without_predecessor.save(update_fields=["applications_open"])
        without_predecessor.delete()
        valid = LOApplicationSettingsVersion.objects.create(
            living_organism=self.organism, actor=self.actor, enterer=self.actor,
            supersedes=existing, occurred_at=NOW, effective_at=NOW, received_at=NOW,
            applications_open=False, automatic_acceptance=False, processing_responsibility="LO",
        )
        with transaction.atomic(), connection.cursor() as cursor:
            self.direct_effect(action, f"application-settings-version:{valid.version_uuid}")
            refresh(cursor, "core_livingorganism", self.organism.pk, NOW)
        self.assertEqual(gateway_version_status("default", valid, database_now()), "EFFECTIVE")

    def test_route_limits_and_previous_configuration(self):
        config_action = OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION
        settings_action = OrganismAction.EFFECT_APPLICATION_SETTINGS
        config = self.draft(config_action, {
            "cq_active": False, "inherit_portal_cq_settings": False,
            "require_cq_to_change_lo_settings": True, "require_cq_to_change_application_question": True,
        })
        self.effect(config, config_action)
        settings = self.draft(settings_action, {
            "applications_open": True, "automatic_acceptance": False, "processing_responsibility": "LO",
        })
        self.effect(settings, settings_action)
        next_config = self.draft(config_action, {
            "cq_active": True, "inherit_portal_cq_settings": False,
            "require_cq_to_change_lo_settings": False, "require_cq_to_change_application_question": False,
        })
        with self.assertRaises(S015OperationNotYetImplemented) as refused:
            self.effect(next_config, config_action)
        self.assertEqual(refused.exception.limit_id, "S3-IL-CQ")
        circle = self.draft(settings_action, {
            "applications_open": True, "automatic_acceptance": False,
            "processing_responsibility": "APPLICATION_CIRCLE",
        })
        with self.assertRaises(S015OperationNotYetImplemented) as refused:
            self.effect(circle, settings_action)
        self.assertEqual(refused.exception.limit_id, "S3-IL-CIRCLE")

    def test_direct_cq_configuration_never_falls_back_to_coordinator(self):
        class Rollback(Exception):
            pass

        settings_action = OrganismAction.EFFECT_APPLICATION_SETTINGS
        question_action = OrganismAction.EFFECT_APPLICATION_QUESTION
        self.draft(settings_action, {})
        for inherit, settings_required, question_required, action, limit in (
            (True, False, False, settings_action, "S3-IL-INHERIT"),
            (False, True, False, settings_action, "S3-IL-CQ"),
            (False, False, True, question_action, "S3-IL-CQ"),
            (False, True, False, OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION, "S3-IL-CQ"),
        ):
            with self.subTest(action=action, limit=limit), self.assertRaises(Rollback):
                with transaction.atomic():
                    configuration = self._direct_cq_configuration(
                        inherit, settings_required, question_required,
                    )
                    self.direct_effect(OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
                                       f"governance-configuration-version:{configuration.version_uuid}")
                    with connection.cursor() as cursor:
                        refresh(cursor, "core_livingorganism", self.organism.pk, NOW)
                    with self.assertRaises(S015OperationNotYetImplemented) as refused:
                        self._draft_for_test(action, {}, received_at=NOW)
                    self.assertEqual(refused.exception.limit_id, limit)
                    raise Rollback()

    def test_cq_active_without_governing_flags_allows_coordinator_changes(self):
        # TEST FIXTURE — installs a configuration whose production creation route is unavailable (S3-IL-CQ);
        # establishes behaviour under it, not delivery of that route.
        # H100 chunk 4 (C4 §F3 row 1; D:1271): the setup draft is a public call before the atomic,
        # keeping its order before the configuration; it is admitted and committed on its own.
        self.draft(OrganismAction.EFFECT_APPLICATION_SETTINGS, {})
        with transaction.atomic(), connection.cursor() as cursor:
            configuration = self._direct_cq_configuration(False, False, False)
            self.direct_effect(OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
                               f"governance-configuration-version:{configuration.version_uuid}")
            refresh(cursor, "core_livingorganism", self.organism.pk, NOW)
        for action, fields in (
            (OrganismAction.EFFECT_APPLICATION_QUESTION,
             {"question_text": "Test-only replacement?", "answer_format": "LONG_ANSWER"}),
            (OrganismAction.EFFECT_APPLICATION_SETTINGS,
             {"applications_open": True, "automatic_acceptance": False, "processing_responsibility": "LO"}),
        ):
            with self.subTest(action=action):
                version = self.draft(action, fields)
                self.effect(version, action)
                self.assertEqual(gateway_version_status("default", version, database_now()), "EFFECTIVE")
        with transaction.atomic(), connection.cursor() as cursor:
            other_pk = seed_identity(cursor, f"cq-other-{fresh()}")
            membership_uuid = u(f"cq-non-coordinator:{fresh()}")
            cursor.execute(
                "INSERT INTO core_organismmembership (membership_uuid, state, created_at, "
                "current_state_effective_at, state_known_at, state_source_event_set_fingerprint, "
                "identity_id, living_organism_id) "
                "VALUES (%s, 'ACTIVE', now(), %s, %s, %s, %s, %s) RETURNING id",
                (membership_uuid, T0, NOW, empty_fp(cursor, "core_organismmembership", membership_uuid),
                 other_pk, self.organism.pk),
            )
            membership_id = cursor.fetchone()[0]
            append_event(cursor, "core_organismmembershiptransition", membership_id, 1, "JOIN", None, "ACTIVE",
                         other_pk, self.ids["basis"], fresh(), eff=T0, occ=T0, rec=NOW,
                         extra={"reason_class": "FOUNDING"})
            refresh(cursor, "core_organismmembership", membership_id, T0)
        other = Identity.objects.select_related("credential").get(pk=other_pk)
        for action in (OrganismAction.EFFECT_APPLICATION_QUESTION, OrganismAction.EFFECT_APPLICATION_SETTINGS):
            with self.subTest(non_coordinator=action), self.assertRaisesMessage(
                S015ApplicationRefused, "effective Coordinator office is required",
            ):
                self.service.save_gateway_draft(GatewayDraftCommand(
                    credential=other.credential, living_organism_id=self.organism.organism_id,
                    action=action, request_reference=f"s4:noncoord:{action.value}",
                    idempotency_key=f"s4:noncoord:{action.value}",
                    occurred_at=NOW, effective_at=NOW, fields={},
                ))

    def test_four_closed_states_and_version_evidence_survives_a_change(self):
        def assert_closed_with_intake(service, command):
            before = CommandIntake.objects.count()
            with self.assertRaisesMessage(
                S015ApplicationRefused, "S3FU gateway closed",
            ):
                service.submit_application(command)
            self.assertEqual(CommandIntake.objects.count(), before + 1)
            intake = CommandIntake.objects.get(
                entry=IntakeEntry.SUBMIT_APPLICATION.value,
                action=ENTRY_ACTIONS[IntakeEntry.SUBMIT_APPLICATION],
                idempotency_key=command.idempotency_key,
            )
            self.assertEqual(intake.request_reference, command.request_reference)

        applicant = self.applicant()
        assert_closed_with_intake(self.service, self._submit_command(applicant))

        intake_count = CommandIntake.objects.count()
        question_draft = self._draft_command(OrganismAction.EFFECT_APPLICATION_QUESTION, {
            "question_text": "TEST FIXTURE: Why do you want to join?",
            "answer_format": "LONG_ANSWER",
            "options": None,
        })
        question = self.service.save_gateway_draft(question_draft)
        question_effect = self._effect_command(question, OrganismAction.EFFECT_APPLICATION_QUESTION)
        self.service.effect_gateway_version(question_effect)
        # H100 chunk 4 (C4 §F3 row 2, CH-17): the G3 pair is admitted; each row by entry, action, fingerprint.
        self.assertEqual(CommandIntake.objects.count(), intake_count + 2)
        _assert_single_intake(self, IntakeEntry.SAVE_GATEWAY_DRAFT, question_draft)
        _assert_single_intake(self, IntakeEntry.EFFECT_GATEWAY_VERSION, question_effect)
        assert_closed_with_intake(self.service, self._submit_command(applicant))

        intake_count = CommandIntake.objects.count()
        closed_settings_draft = self._draft_command(OrganismAction.EFFECT_APPLICATION_SETTINGS, {
            "applications_open": False, "automatic_acceptance": False,
            "processing_responsibility": "LO",
        })
        closed_settings = self.service.save_gateway_draft(closed_settings_draft)
        closed_settings_effect = self._effect_command(closed_settings, OrganismAction.EFFECT_APPLICATION_SETTINGS)
        self.service.effect_gateway_version(closed_settings_effect)
        self.assertEqual(CommandIntake.objects.count(), intake_count + 2)
        _assert_single_intake(self, IntakeEntry.SAVE_GATEWAY_DRAFT, closed_settings_draft)
        _assert_single_intake(self, IntakeEntry.EFFECT_GATEWAY_VERSION, closed_settings_effect)
        assert_closed_with_intake(self.service, self._submit_command(applicant))

        with transaction.atomic(), connection.cursor() as cursor:
            no_question_ids = found_nonroot(
                cursor,
                tag=f"gateway-no-question-{self.tag}",
            )
            cursor.execute("SELECT clock_timestamp()")
            no_question_setup_at = cursor.fetchone()[0]
            seed_test_gateway(
                cursor,
                no_question_ids,
                at=no_question_setup_at,
                include_governance_configuration=True,
                include_settings=True,
                include_question=False,
            )
        no_question_organism = LivingOrganism.objects.get(pk=no_question_ids["lo"])
        no_question_provider = _FixtureBirthingCoordinatorProvider(
            TestFixtureBirthingFactSource(no_question_organism.organism_id)
        )
        with connection.cursor() as cursor:
            cursor.execute("SELECT clock_timestamp()")
            no_question_now = cursor.fetchone()[0]
        no_question_service = OrganismApplicationService(
            authority=S015Authority(provider=no_question_provider),
            clock=lambda: no_question_now,
        )
        no_question_command = SubmitApplicationCommand(
            credential=applicant.credential,
            living_organism_id=no_question_organism.organism_id,
            request_reference=f"s3:submit:no-question:{self.tag}",
            idempotency_key=f"s3:submit:no-question:key:{self.tag}",
            occurred_at=no_question_now,
            effective_at=no_question_now,
            answer="A test-only applicant answer",
        )
        assert_closed_with_intake(no_question_service, no_question_command)

        self.assertFalse(Application.objects.filter(applicant=applicant).exists())
        intake_count = CommandIntake.objects.count()
        # FIFTH INTERFACE EXCEPTION (HG-authorized): the labelled controlled LO tests absent
        # settings/question and closed states that real Birthing requires; it changes no real-LO rule.
        settings_draft = self._draft_command(OrganismAction.EFFECT_APPLICATION_SETTINGS, {
            "applications_open": True, "automatic_acceptance": False, "processing_responsibility": "LO",
        })
        settings = self.service.save_gateway_draft(settings_draft)
        settings_effect = self._effect_command(settings, OrganismAction.EFFECT_APPLICATION_SETTINGS)
        self.service.effect_gateway_version(settings_effect)
        self.assertEqual(CommandIntake.objects.count(), intake_count + 2)
        _assert_single_intake(self, IntakeEntry.SAVE_GATEWAY_DRAFT, settings_draft)
        _assert_single_intake(self, IntakeEntry.EFFECT_GATEWAY_VERSION, settings_effect)
        gateway = {
            "core_loapplicationsettingsversion": settings.pk,
            "core_loapplicationquestionversion": question.pk,
        }
        result = self.submit(applicant)
        application = Application.objects.get(application_uuid=result.application_uuid)
        self.assertEqual((application.settings_version_id, application.question_version_id, application.answer), (
            gateway["core_loapplicationsettingsversion"], gateway["core_loapplicationquestionversion"],
            "A test-only applicant answer",
        ))
        settings = self.draft(OrganismAction.EFFECT_APPLICATION_SETTINGS, {
            "applications_open": False, "automatic_acceptance": False, "processing_responsibility": "LO",
        })
        self.effect(settings, OrganismAction.EFFECT_APPLICATION_SETTINGS)
        application.refresh_from_db()
        self.assertEqual(application.settings_version_id, gateway["core_loapplicationsettingsversion"])
        assert_closed_with_intake(
            self.service, self._submit_command(self.applicant()),
        )

    def test_answer_formats_at_service_boundary(self):
        cases = (
            ("SHORT_ANSWER", None, "hello", "x" * 256),
            ("LONG_ANSWER", None, "a longer answer", "x" * 4097),
            ("YES_NO", None, "YES", "maybe"),
            ("MULTIPLE_CHOICE", ["A", "B"], "B", "C"),
            ("SCALE_COMPLEX", [1, 5], "3", "6"),
        )
        for kind, options, good, bad in cases:
            with self.subTest(kind=kind):
                question = LOApplicationQuestionVersion(answer_format=kind, options=options)
                _validate_gateway_answer(question, good)
                with self.assertRaises(S015ApplicationRefused):
                    _validate_gateway_answer(question, bad)

    def test_all_answer_formats_at_submission(self):
        settings = self.draft(OrganismAction.EFFECT_APPLICATION_SETTINGS, {
            "applications_open": True, "automatic_acceptance": False, "processing_responsibility": "LO",
        })
        self.effect(settings, OrganismAction.EFFECT_APPLICATION_SETTINGS)
        for kind, options, good, bad in (
            ("SHORT_ANSWER", None, "hello", "x" * 256),
            ("LONG_ANSWER", None, "a longer answer", "x" * 4097),
            ("YES_NO", None, "YES", "maybe"),
            ("MULTIPLE_CHOICE", ["A", "B"], "A", "Z"),
            ("SCALE_COMPLEX", [1, 5], "3", "9"),
        ):
            with self.subTest(kind=kind):
                question = self.draft(OrganismAction.EFFECT_APPLICATION_QUESTION, {
                    "question_text": f"Test-only question {kind}?", "answer_format": kind, "options": options,
                })
                self.effect(question, OrganismAction.EFFECT_APPLICATION_QUESTION)
                applicant = self.applicant()
                with self.assertRaisesMessage(S015ApplicationRefused, "answer is invalid"):
                    self.submit(applicant, bad)
                result = self.submit(applicant, good)
                application = Application.objects.get(application_uuid=result.application_uuid)
                self.assertEqual((application.question_version_id, application.answer), (question.pk, good))

    def test_non_coordinator_cannot_change_gateway(self):
        with transaction.atomic(), connection.cursor() as cursor:
            other_pk = seed_identity(cursor, f"other-role-{fresh()}")
            membership_uuid = u(f"non-coordinator:{fresh()}")
            cursor.execute(
                "INSERT INTO core_organismmembership (membership_uuid, state, created_at, "
                "current_state_effective_at, state_known_at, state_source_event_set_fingerprint, "
                "identity_id, living_organism_id) "
                "VALUES (%s, 'ACTIVE', now(), %s, %s, %s, %s, %s) RETURNING id",
                (membership_uuid, T0, NOW, empty_fp(cursor, "core_organismmembership", membership_uuid),
                 other_pk, self.organism.pk),
            )
            membership_id = cursor.fetchone()[0]
            append_event(cursor, "core_organismmembershiptransition", membership_id, 1, "JOIN", None, "ACTIVE",
                         other_pk, self.ids["basis"], fresh(), eff=T0, occ=T0, rec=NOW,
                         extra={"reason_class": "FOUNDING"})
            refresh(cursor, "core_organismmembership", membership_id, T0)
        other = Identity.objects.select_related("credential").get(pk=other_pk)
        with self.assertRaisesMessage(S015ApplicationRefused, "effective Coordinator office is required"):
            self.service.save_gateway_draft(GatewayDraftCommand(
                credential=other.credential, living_organism_id=self.organism.organism_id,
                action=OrganismAction.EFFECT_APPLICATION_SETTINGS,
                request_reference=f"s3:noncoord:{self.tag}", idempotency_key=f"s3:noncoord:{self.tag}",
                occurred_at=NOW, effective_at=NOW, fields={},
            ))

    def test_closed_lo_has_no_capacity_to_effect_a_gateway_version(self):
        settings = self.draft(OrganismAction.EFFECT_APPLICATION_SETTINGS, {
            "applications_open": True, "automatic_acceptance": False, "processing_responsibility": "LO",
        })
        predecessor = LivingOrganismEvent.objects.get(pk=self.organism.head_event_id)
        self.assertEqual(predecessor.resulting_state, "ACTIVE")
        with transaction.atomic(), connection.cursor() as cursor:
            append_event(cursor, "core_livingorganismevent", self.organism.pk, predecessor.sequence + 1,
                         "CLOSE", "ACTIVE", "CLOSED",
                         self.actor.pk, self.ids["basis"], fresh(),
                         pred_id=predecessor.pk, eff=NOW, occ=NOW, rec=NOW)
            refresh(cursor, "core_livingorganism", self.organism.pk, NOW)
        with self.assertRaisesMessage(IntegrityError, "S3FU LO capacity refused: dissolved"):
            self.service.effect_gateway_version(EffectGatewayVersionCommand(
                credential=self.actor.credential, living_organism_id=self.organism.organism_id,
                action=OrganismAction.EFFECT_APPLICATION_SETTINGS, version_uuid=settings.version_uuid,
                request_reference=f"s3:closed:{self.tag}", idempotency_key=f"s3:closed:{self.tag}",
                occurred_at=NOW + timedelta(seconds=1), effective_at=NOW + timedelta(seconds=1),
            ))

    def test_guard_derivation_and_installed_bodies(self):
        migration = importlib.import_module("core.migrations.0029_s015_application_gateway")
        for source in ("absent", "marker and marker"):
            with self.assertRaises(RuntimeError):
                migration.replace_once(source, "marker", "replacement")
        with connection.cursor() as cursor:
            for function, tokens in (
                ("s3fu_guard_lo_capacity", ("closed.effective_at < NEW.effective_at",
                                            "S3FU LO capacity refused: no founding record",
                                            "S3FU LO capacity refused: dissolved")),
                ("s3fu_guard_lo_admission_permission", ("NEW.prior_state IS DISTINCT FROM 'PROPOSED'",
                                                         "NEW.resulting_state IS DISTINCT FROM 'ACTIVE'")),
                ("s3fu_guard_gateway_effect", ("version of another organism", "continuity", "already effective")),
            ):
                cursor.execute("SELECT pg_get_functiondef(%s::regprocedure)", [f"public.{function}()"])
                body = cursor.fetchone()[0]
                for token in tokens:
                    self.assertIn(token, body)
            cursor.execute("SELECT tgname FROM pg_trigger WHERE tgrelid = 'public.core_livingorganismevent'::regclass "
                           "AND NOT tgisinternal ORDER BY tgname")
            triggers = [row[0] for row in cursor.fetchall()]
            self.assertLess(triggers.index("s015_0021_loevent_record_time"), triggers.index("s3fu_10_gateway_effect"))

    def test_lo_effect_commitments_independently_recomputed(self):
        question = self.draft(OrganismAction.EFFECT_APPLICATION_QUESTION, {})
        event = self.effect(question, OrganismAction.EFFECT_APPLICATION_QUESTION)
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT a.attname, a.atttypid::regtype::text FROM pg_catalog.pg_attribute a '
                "WHERE a.attrelid = 'public.core_livingorganismevent'::regclass AND a.attnum > 0 "
                'AND NOT a.attisdropped ORDER BY a.attname COLLATE "C"'
            )
            inventory = [{"column": name, "pg_type": {"character varying": "varchar",
                         "timestamp with time zone": "timestamptz"}.get(kind, kind)} for name, kind in cursor.fetchall()]
            cursor.execute("SELECT to_jsonb(t), l1_commitment, parts_commitment FROM core_livingorganismevent t "
                           "WHERE event_uuid = %s", [event.event_uuid])
            row, l1, parts = cursor.fetchone()
        row = json.loads(row) if isinstance(row, str) else row
        self.assertEqual(l1, digest("s015r1", l1_preimage("core_livingorganismevent", inventory, row)))
        self.assertEqual(parts, digest("s015p1", parts_preimage("core_livingorganismevent", event.event_uuid, [])))

    def test_loi_w1_s2_office_end_and_submission_complete_without_cycle(self):
        """On the pre-0032 implementation, this interleaving ended in deadlock detected with no application written (Vision Chamber evidence W1, 3/3); it establishes this examined interaction only."""
        self.open_gateway()
        second = self._second_coordinator()
        office = ContextualRoleAssignment.objects.get(membership__identity=second, state="ACTIVE")
        applicant = self.applicant()
        provider = _PausingPostFoundingProvider()
        service = OrganismRoleService(
            authority=S015Authority(provider=provider, database_alias="default"),
            clock=lambda: NOW, database_alias="default",
        )
        token = fresh()
        paused_call = lambda: service.transition_assignment(TransitionAssignmentCommand(
            credential=self.actor.credential,
            request_reference=f"loi-w1:{token}:request",
            idempotency_key=f"loi-w1:{token}:idempotency",
            occurred_at=NOW,
            effective_at=NOW,
            assignment_uuid=office.assignment_uuid,
            action="END",
            resulting_state="ENDED",
        ))

        paused_result, submission_result, _ = self._race_paused_command_against_submission(
            paused_call, provider, applicant,
        )

        self.assertEqual(paused_result, "ok")
        self.assertEqual(submission_result, "ok")
        self._assert_no_deadlock(paused_result, submission_result)
        office.refresh_from_db()
        self.assertEqual(office.state, "ENDED")
        events = ContextualRoleAssignmentStateEvent.objects.filter(assignment=office).order_by("sequence")
        self.assertEqual(
            [(event.action, event.resulting_state) for event in events],
            [("PROPOSE", "PROPOSED"), ("ASSIGN", "ACTIVE"), ("END", "ENDED")],
        )
        self.assertEqual(
            Application.objects.filter(applicant=applicant, living_organism=self.organism).count(),
            1,
        )

    def test_loi_w2_s1_open_and_submission_complete_without_cycle(self):
        """On the pre-0032 implementation, this interleaving ended in deadlock detected with no application written (Vision Chamber evidence W2, 3/3); it establishes this examined interaction only."""
        self.open_gateway()
        with transaction.atomic(), connection.cursor() as cursor:
            target_id = seed_identity(cursor, f"loi-w2-target-{fresh()}")
        target = Identity.objects.get(pk=target_id)
        applicant = self.applicant()
        provider = _PausingPostFoundingProvider()
        service = OrganismMembershipService(
            authority=S015Authority(provider=provider, database_alias="default"),
            clock=lambda: NOW, database_alias="default",
        )
        token = fresh()
        membership_key = f"loi-w2:{token}:idempotency"
        open_command = OpenMembershipCommand(
            credential=self.actor.credential,
            request_reference=f"loi-w2:{token}:request",
            idempotency_key=membership_key,
            occurred_at=NOW,
            effective_at=NOW,
            living_organism_id=self.organism.organism_id,
            member_identity_id=target.identity_id,
            reason_class="TEST_ONLY_REASON_OPEN",
        )
        paused_call = lambda: service.open_membership(open_command)

        paused_result, submission_result, _ = self._race_paused_command_against_submission(
            paused_call, provider, applicant,
        )

        self.assertEqual(paused_result, "ok")
        self.assertEqual(submission_result, "ok")
        self._assert_no_deadlock(paused_result, submission_result)
        _assert_single_intake(self, IntakeEntry.OPEN_MEMBERSHIP, open_command)
        membership = OrganismMembership.objects.get(identity=target, living_organism=self.organism)
        self.assertEqual(membership.state, "PROPOSED")
        transitions = OrganismMembershipTransition.objects.filter(membership=membership).order_by("sequence")
        self.assertEqual(
            [(event.prior_state, event.resulting_state) for event in transitions],
            [(None, "PROPOSED")],
        )
        self.assertEqual(
            Application.objects.filter(applicant=applicant, living_organism=self.organism).count(),
            1,
        )