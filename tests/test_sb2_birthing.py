"""SB-2 Birthing behaviour and safeguard witnesses."""

from dataclasses import asdict, replace
from contextlib import contextmanager
from datetime import timedelta
import importlib
import json
from queue import Queue
from threading import Event, Thread
from types import SimpleNamespace
import unittest
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.db import IntegrityError, close_old_connections, connection, connections, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from core.models import (
    Application,
    ApplicationDecision,
    ApplicationEngagement,
    AuthorityDecision,
    CSAAssignment,
    CommandIntake,
    ContextualRoleAssignment,
    LOApplicationQuestionVersion,
    LOApplicationSettingsVersion,
    LOFoundingContentVersion,
    LOBirthingSnapshot,
    LOGovernanceConfigurationVersion,
    LivingOrganismEvent,
    LivingOrganism,
    OrganismCommandReceipt,
    OrganismEntitlement,
    OrganismEntitlementEvent,
    OrganismEntitlementPolicyText,
    OrganismMembership,
    PlatformTermsAcceptance,
)
from core.organism_contract import (
    OrganismAction,
    QUALIFICATION_PLANE_MATRIX,
    QualificationApplicabilityUnknown,
    QualificationPlane,
    QualificationPlaneState,
    qualification_planes,
)
from src.intevia.services.organism_application_authority import (
    ProductionApplicationAuthorityProvider,
    ProductionBirthingFactSource,
)
from src.intevia.services.organism_application_service import (
    ClaimApplicationCommand,
    DecideApplicationCommand,
    EffectGatewayVersionCommand,
    GatewayDraftCommand,
    OrganismApplicationService,
    S015ApplicationRefused,
    SubmitApplicationCommand,
)
from src.intevia.services.organism_authority import (
    S015Authority,
    S015AuthorityDenied,
    S015AuthorityRefusalCode,
    S015PostFoundingAuthorityRefusal,
    S015PostFoundingAuthorityRequest,
    S015PostFoundingAuthorityResponse,
)
from src.intevia.services.organism_command_support import (
    S015CommandReplayMismatch,
    _founding_appointment_by_record,
)
from src.intevia.services.organism_formation_service import (
    BirthingCommand,
    FoundLivingOrganismCommand,
    OrganismBirthingService,
    S015BirthingTechnicalFailure,
    S015FormationTechnicalFailure,
)
from tests.s015_concurrency_support import backend_pid, wait_until_blocked
from tests.s015_0021_support import append_event, refresh, register_test_learner
from tests.se_route_support import (
    TEST_POLICY_SHA256,
    TEST_POLICY_VERSION,
    disclose_authority_refusal_code,
    ensure_test_policy_text,
)
from tests.sb2_route_support import (
    SB2_TEST_PASSWORD,
    add_active_elector,
    birth,
    database_now,
    effect_gateway,
    prepare_route,
    route_test_now,
)


POSTGRESQL_ONLY = skipUnless(connection.vendor == "postgresql", "SB-2 requires PostgreSQL")


def _counts():
    return (
        LivingOrganism.objects.count(),
        LivingOrganismEvent.objects.count(),
        LOBirthingSnapshot.objects.count(),
        AuthorityDecision.objects.count(),
        OrganismCommandReceipt.objects.count(),
    )


def _model_state(model, **filters):
    """Capture every concrete field in a model's current rows."""
    rows = model.objects.filter(**filters) if filters else model.objects.all()
    field_names = tuple(field.attname for field in model._meta.concrete_fields)
    return tuple(rows.order_by("pk").values_list(*field_names))


def _whole_birthing_state():
    """Snapshot the Birthing state and SE records protected by the guard witnesses."""
    return {
        "living_organisms": _model_state(LivingOrganism),
        "living_organism_events": _model_state(LivingOrganismEvent),
        "birthing_snapshots": _model_state(LOBirthingSnapshot),
        "authority_decisions": _model_state(AuthorityDecision),
        "command_receipts": _model_state(OrganismCommandReceipt),
        "entitlements": _model_state(OrganismEntitlement),
        "entitlement_events": _model_state(OrganismEntitlementEvent),
        "election_settings_versions": _model_state(
            LOApplicationSettingsVersion,
            entitlement_election_route__isnull=False,
        ),
        "policy_text": _model_state(OrganismEntitlementPolicyText),
    }


def _model_instance_state(model, instance):
    """Return one model instance in the same field order as _model_state."""
    return tuple(
        getattr(instance, field.attname)
        for field in model._meta.concrete_fields
    )


def _assert_only_birthing_refusal_persisted(testcase, before, refusal):
    """Require the recorded refusal to be the sole persisted state change."""
    after = _whole_birthing_state()
    for key in before:
        if key != "authority_decisions":
            testcase.assertEqual(after[key], before[key], key)
    decisions_before = before["authority_decisions"]
    decisions_after = after["authority_decisions"]
    testcase.assertEqual(len(decisions_after), len(decisions_before) + 1)
    testcase.assertEqual(decisions_after[:-1], decisions_before)
    decision = AuthorityDecision.objects.get(
        decision_reference=refusal.authority_decision_reference,
    )
    testcase.assertEqual(decisions_after[-1], _model_instance_state(AuthorityDecision, decision))


def _assert_birthing_refusal_record(testcase, route, suffix, refusal, expected_code, before):
    request_reference = f"sb2:{suffix}:{route.tag}"
    idempotency_key = f"sb2:{suffix}:key:{route.tag}"
    decision = AuthorityDecision.objects.get(
        decision_reference=refusal.authority_decision_reference,
    )
    testcase.assertEqual(decision.outcome, "REFUSED")
    testcase.assertEqual(decision.refusal_code, expected_code.value)
    testcase.assertEqual(decision.request_reference, request_reference)
    testcase.assertEqual(decision.idempotency_key, idempotency_key)
    testcase.assertEqual(
        AuthorityDecision.objects.filter(request_reference=request_reference).count(),
        1,
    )
    testcase.assertEqual(
        _counts(),
        (before[0], before[1], before[2], before[3] + 1, before[4]),
    )
    testcase.assertFalse(
        LivingOrganismEvent.objects.filter(
            living_organism=route.organism,
            request_reference=request_reference,
        ).exists()
    )
    testcase.assertFalse(
        OrganismCommandReceipt.objects.filter(
            actor=route.founder,
            idempotency_key=idempotency_key,
        ).exists()
    )


def _set_empty_electorate(route):
    # Amendment 3's tenth exception is the state IL-2 makes unreachable on the public route.
    membership = OrganismMembership.objects.select_related("head_transition").get(
        living_organism=route.organism,
        identity=route.founder,
    )
    predecessor = membership.head_transition
    if membership.state != "ACTIVE" or predecessor is None:
        raise AssertionError("controlled empty-electorate fixture requires the active founder head")
    effective_at = route_test_now(route)
    with transaction.atomic(), connection.cursor() as cursor:
        append_event(
            cursor,
            "core_organismmembershiptransition",
            membership.pk,
            predecessor.sequence + 1,
            "TRANSITION_MEMBERSHIP",
            "ACTIVE",
            "ENDED",
            route.founder.pk,
            route.organism.founding_authority_basis_id,
            f"sb2-empty-electorate:{route.tag}",
            pred_id=predecessor.pk,
            eff=effective_at,
            occ=effective_at,
            rec=effective_at,
            extra={"reason_class": "TEST"},
        )
        refresh(cursor, "core_organismmembership", membership.pk, effective_at)
    membership.refresh_from_db()
    return membership, effective_at


def _birthing_electorate_count(route, effective_at, recorded_at):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM public.core_organismmembership m "
            "WHERE m.living_organism_id = %s "
            "AND public.s015_fold_effective_state("
            "'core_organismmembership', m.id, %s, %s) = 'ACTIVE'",
            [route.organism.pk, effective_at, recorded_at],
        )
        return cursor.fetchone()[0]


def _controlled_membership_context(route):
    # This test-only context isolates the safeguard from the real-route actor lock.
    membership = OrganismMembership.objects.select_related("head_transition").get(
        living_organism=route.organism,
        identity=route.founder,
    )
    role_references = tuple(
        ContextualRoleAssignment.objects.filter(
            membership=membership,
            state="ACTIVE",
            head_state_event__isnull=False,
        ).order_by("pk").values_list("head_state_event__lineage_reference", flat=True)
    )
    return membership, role_references


def _assert_single_intake(test, entry, command):
    """Assert exactly one intake row for the command, with its entry, action and intake-projection fingerprint (CH-17)."""
    from core.models import CommandIntake, Identity
    from src.intevia.services import command_intake as command_intake_module

    actor = Identity.objects.get(credential=command.credential)
    action = command_intake_module.ENTRY_ACTIONS[entry]
    rows = CommandIntake.objects.filter(
        actor=actor,
        action=action,
        idempotency_key=command.idempotency_key,
    )
    test.assertEqual(rows.count(), 1)
    intake = rows.get()
    test.assertEqual(intake.entry, entry.value)
    shape = command_intake_module.intake_shape(entry, command)
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


class _ControlledBirthingProvider:
    def __init__(self, refusal_code: S015AuthorityRefusalCode | None = None):
        self.refusal_code = refusal_code
        self.request: S015PostFoundingAuthorityRequest | None = None

    def evaluate_organism_authority(
        self,
        request: S015PostFoundingAuthorityRequest,
    ) -> S015PostFoundingAuthorityResponse | S015PostFoundingAuthorityRefusal:
        self.request = request
        fields = asdict(request)
        if self.refusal_code is not None:
            return S015PostFoundingAuthorityRefusal(
                **fields,
                refusal_code=self.refusal_code,
            )
        return S015PostFoundingAuthorityResponse(
            **fields,
            authority_reference="authority:sb2-empty-electorate-guard",
        )


class _ControlledEntitlementElectionProvider:
    """TEST-ONLY authority-provider stand-in for the E-1a gate witnesses."""

    authority_reference = (
        's015a1:{"c":"-","kind":"BIRTHING","n":"-","o":"-","q":"-","s":"-"}'
    )

    def __init__(self):
        self.request: S015PostFoundingAuthorityRequest | None = None

    def evaluate_organism_authority(
        self,
        request: S015PostFoundingAuthorityRequest,
    ) -> S015PostFoundingAuthorityResponse:
        if request.action is not OrganismAction.EFFECT_ENTITLEMENT_ELECTION:
            raise AssertionError("election stand-in received a non-election request")
        self.request = request
        return S015PostFoundingAuthorityResponse(
            **asdict(request),
            authority_reference=self.authority_reference,
        )


@contextmanager
def _qualify_election_for_birthing_guard(route):
    """Scope the Decision B provider stand-in to one guard witness."""
    # Decision B: this stands in for the authority-provider interface so E-1a does not pre-empt the Birthing guard.
    provider = _ControlledEntitlementElectionProvider()
    with patch.object(
        route.birthing_service._entitlement_service,
        "_authority",
        S015Authority(provider=provider),
    ):
        yield provider


def _governance_draft(route, enabled, suffix):
    occurred_at = database_now()
    return route.application_service.save_gateway_draft(GatewayDraftCommand(
        credential=route.founder.credential,
        living_organism_id=route.organism.organism_id,
        action=OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
        request_reference=f"sb2:governance-draft:{suffix}:{route.tag}",
        idempotency_key=f"sb2:governance-draft:key:{suffix}:{route.tag}",
        occurred_at=occurred_at,
        effective_at=occurred_at,
        fields={
            "cq_active": False,
            "governance_enabled": enabled,
            "inherit_portal_cq_settings": False,
            "require_cq_to_change_lo_settings": False,
            "require_cq_to_change_application_question": False,
        },
    ))


def _governance_effect_command(route, version_uuid, suffix):
    occurred_at = database_now()
    return EffectGatewayVersionCommand(
        credential=route.founder.credential,
        living_organism_id=route.organism.organism_id,
        action=OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
        version_uuid=version_uuid,
        request_reference=f"sb2:governance-effect:{suffix}:{route.tag}",
        idempotency_key=f"sb2:governance-effect:key:{suffix}:{route.tag}",
        occurred_at=occurred_at,
        effective_at=occurred_at,
    )


class _PausedProductionProvider:
    def __init__(self, entered, release):
        self._delegate = ProductionApplicationAuthorityProvider()
        self._entered = entered
        self._release = release

    def evaluate_organism_authority(self, request):
        result = self._delegate.evaluate_organism_authority(request)
        self._entered.set()
        if not self._release.wait(20):
            raise RuntimeError("timed out waiting for the concurrency witness release")
        return result


def _start_worker(call):
    results = Queue()
    pids = Queue()

    def run():
        close_old_connections()
        try:
            pids.put(backend_pid(connection))
            results.put((True, call()))
        except BaseException as error:
            results.put((False, error))
        finally:
            connections.close_all()

    thread = Thread(target=run)
    thread.start()
    return thread, results, pids


def _worker_result(thread, results):
    thread.join(20)
    if thread.is_alive():
        raise AssertionError("concurrency witness worker did not finish")
    succeeded, result = results.get(timeout=1)
    if not succeeded:
        raise result
    return result


def _join_workers(workers):
    for worker in workers:
        worker.join(30)
    alive = [worker for worker in workers if worker.is_alive()]
    if alive:
        raise AssertionError(f"{len(alive)} concurrency witness worker(s) did not stop")


class GovernanceGuardErrorTranslationTests(unittest.TestCase):
    """Database-free: the effect wrapper's two refusal-mapping arms (H100 chunk 4, C4 OPEN-4 (a), CH-37).

    Calls the executor with a real command and a test-only bound request, so no
    admission runs; _effect_gateway_version is patched by name as before. The
    mapped class and code, and the identity of an unrelated error, are the
    properties kept. It does not exercise the public admission path (that is
    tests/test_h100_intake_gateway.py's).
    """

    def test_guard_refusal_uses_database_primary_message_only(self):
        from datetime import datetime as datetime_class, timezone as datetime_timezone

        from django.contrib.auth.models import User

        from core.models import IntakeEntry
        from src.intevia.services.command_intake import ActorRef, BoundRequest

        code = S015AuthorityRefusalCode.SBG_GOVERNANCE_CIRCLE_MISSING
        at = datetime_class(2026, 10, 21, 12, tzinfo=datetime_timezone.utc)
        command = EffectGatewayVersionCommand(
            credential=User(username="sb2-guard-translation"),
            living_organism_id=uuid4(),
            action=OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
            version_uuid=uuid4(),
            request_reference="sb2:guard-translation",
            idempotency_key="sb2:guard-translation",
            occurred_at=at,
            effective_at=at,
        )
        bound = BoundRequest.for_test(
            IntakeEntry.EFFECT_GATEWAY_VERSION,
            command,
            received_at=at,
            actor=ActorRef(pk=11, identity_id=uuid4(), access_epoch=0),
        )
        service = object.__new__(OrganismApplicationService)
        service._alias = "default"
        organism = SimpleNamespace(pk=37)

        matched_error = IntegrityError(
            f"{code.value}\nCONTEXT: PL/pgSQL function s3fu_guard_gateway_effect() line 115 at RAISE",
        )
        matched_cause = RuntimeError("database driver exception")
        matched_cause.diag = SimpleNamespace(message_primary=code.value)
        matched_error.__cause__ = matched_cause
        with (
            patch.object(service, "_effect_gateway_version", side_effect=matched_error),
            patch(
                "src.intevia.services.organism_application_service.LivingOrganism.objects",
            ) as organism_manager,
            patch(
                "src.intevia.services.organism_application_service."
                "_governance_enabling_refusal_message",
                return_value="authored readiness refusal",
            ),
        ):
            organism_manager.using.return_value.get.return_value = organism
            with self.assertRaises(S015ApplicationRefused) as mapped:
                service._execute_effect_gateway_version(command, bound)

        self.assertIs(mapped.exception.refusal_code, code)
        self.assertEqual(str(mapped.exception), "authored readiness refusal")

        unrelated_error = IntegrityError(
            f"{code.value}\nCONTEXT: unrelated database failure",
        )
        unrelated_cause = RuntimeError("database driver exception")
        unrelated_cause.diag = SimpleNamespace(
            message_primary="unrelated database failure",
        )
        unrelated_error.__cause__ = unrelated_cause
        with patch.object(service, "_effect_gateway_version", side_effect=unrelated_error):
            with self.assertRaises(IntegrityError) as re_raised:
                service._execute_effect_gateway_version(command, bound)

        self.assertIs(re_raised.exception, unrelated_error)


@POSTGRESQL_ONLY
@override_settings(INTEVIA_FIXTURE_SUBSTITUTES_PERMITTED=True)
class BirthingAfterWitnessTests(TransactionTestCase):
    """SB-2 Birthing service, authority and database-guard witnesses."""

    def test_gateway_setup_ignores_python_clock_skew(self):
        """Use one database reading for each gateway command despite clock skew."""
        route = prepare_route()
        for offset_ms, applications_open in ((50, False), (-50, True)):
            with self.subTest(offset_ms=offset_ms):
                offset = timedelta(milliseconds=offset_ms)

                def skewed_clock():
                    return database_now() + offset

                with patch("django.utils.timezone.now", side_effect=skewed_clock), \
                        patch("tests.sb2_route_support.database_now", wraps=database_now) as db_clock:
                    observed_python_time = timezone.now()
                    observed_database_time = database_now()
                    self.assertAlmostEqual(
                        (observed_python_time - observed_database_time).total_seconds(),
                        offset.total_seconds(),
                        delta=0.02,
                    )
                    effect_gateway(
                        route.application_service,
                        route.founder,
                        route.organism,
                        f"clock-skew-{offset_ms}:{route.tag}",
                        OrganismAction.EFFECT_APPLICATION_SETTINGS,
                        {
                            "applications_open": applications_open,
                            "automatic_acceptance": False,
                            "processing_responsibility": "LO",
                        },
                    )
                self.assertEqual(db_clock.call_count, 2)
                event = LivingOrganismEvent.objects.filter(
                    living_organism=route.organism,
                    action=OrganismAction.EFFECT_APPLICATION_SETTINGS.value,
                ).order_by("-sequence").first()
                self.assertIsNotNone(event)
                # H100 chunk 4 tests v1.3 (CH-34): occurred_at and effective_at are the command's one
                # database reading; received_at is the database-assigned intake time of the effect
                # request, never the (skewed) Python clock, so it is bound to the intake row instead.
                self.assertEqual(event.occurred_at, event.effective_at)
                intake = CommandIntake.objects.get(
                    actor_id=route.founder.pk,
                    action=OrganismAction.EFFECT_APPLICATION_SETTINGS.value,
                    idempotency_key=(
                        f"sb2:effect:key:{OrganismAction.EFFECT_APPLICATION_SETTINGS.value}:"
                        f"clock-skew-{offset_ms}:{route.tag}"
                    ),
                )
                self.assertEqual(event.received_at, intake.received_at)
                self.assertLessEqual(event.effective_at, event.received_at)
                # v1.4 (HG 22 Oct): the lower bound; with effective_at <= received_at this catches
                # both skew directions again. observed_database_time comes from this module's own
                # database_now binding (imported at module load), which the patch above does not touch.
                self.assertLessEqual(observed_database_time, event.effective_at)

    def test_real_route_births_then_another_registered_learner_applies_and_founder_decides(self):
        route = prepare_route()
        intake_count_before_birth = CommandIntake.objects.count()
        result = birth(route)
        self.assertEqual(CommandIntake.objects.count(), intake_count_before_birth)
        event = LivingOrganismEvent.objects.get(event_uuid=result.event_uuid)
        self.assertEqual((event.action, event.prior_state, event.resulting_state), ("BIRTHING", "ACTIVE", "ACTIVE"))
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT actor_id, actor_access_epoch, actor_state, actor_capacity, enterer_identity_id, entry_mode "
                "FROM public.core_livingorganismevent WHERE id = %s",
                [event.pk],
            )
            actor_id, actor_access_epoch, actor_state, actor_capacity, enterer_identity_id, entry_mode = cursor.fetchone()
        self.assertIsNone(actor_id)
        self.assertEqual(actor_state, "LIVING_ORGANISM")
        self.assertEqual(actor_capacity, "STANDING")
        self.assertIsNone(actor_access_epoch)
        self.assertEqual(enterer_identity_id, route.founder.pk)
        self.assertEqual(entry_mode, "TRANSPORT")
        self.assertLessEqual(len(result.authority_reference), 255)
        birthing_facts = json.loads(result.authority_reference.removeprefix("s015a1:"))
        self.assertEqual(
            set(birthing_facts),
            {"kind", "c", "s", "q", "o", "n"},
        )
        birthing_decision = AuthorityDecision.objects.get(
            decision_reference=result.authority_decision_reference,
        )
        self.assertEqual(
            {
                (fact["relation"], fact["reference"])
                for fact in birthing_decision.facts_read
            },
            {
                ("core_identity", str(route.founder.identity_id)),
                ("core_livingorganism", str(route.organism.organism_id)),
                (
                    "core_logovernanceconfigurationversion",
                    f"governance-configuration-version:{birthing_facts['c']}",
                ),
                (
                    "core_loapplicationsettingsversion",
                    f"application-settings-version:{birthing_facts['s']}",
                ),
                (
                    "core_loapplicationquestionversion",
                    f"application-question-version:{birthing_facts['q']}",
                ),
                ("core_contextualroleassignment", birthing_facts["o"]),
                ("core_lobirthingsnapshot", birthing_facts["n"]),
            },
        )
        snapshot = LOBirthingSnapshot.objects.get(snapshot_uuid=result.snapshot_uuid)
        configuration_uuid = snapshot.body["settings"]["governance_configuration"].split(":")[1]
        self.assertIs(
            LOGovernanceConfigurationVersion.objects.get(
                version_uuid=configuration_uuid,
            ).governance_enabled,
            False,
        )
        question_uuid = snapshot.body["settings"]["application_question"].split(":")[1]
        question = LOApplicationQuestionVersion.objects.get(version_uuid=question_uuid)
        founding_content = LOFoundingContentVersion.objects.get(
            living_organism=route.organism,
            version=1,
        )
        self.assertEqual(
            question.question_text,
            f"Why do you want to join {founding_content.name}?",
        )

        applicant = register_test_learner(
            username=f"sb2-applicant-{route.tag}",
            password=SB2_TEST_PASSWORD,
            display_name=f"SB-2 Applicant {route.tag}",
            terms_version=route.terms,
        )
        self.assertNotEqual(applicant.identity_id, route.founder.identity_id)
        application_service = OrganismApplicationService(clock=database_now)
        submitted_at = database_now()
        submitted = application_service.submit_application(SubmitApplicationCommand(
            credential=applicant.credential,
            living_organism_id=route.organism.organism_id,
            request_reference=f"sb2:submit:{route.tag}",
            idempotency_key=f"sb2:submit:key:{route.tag}",
            occurred_at=submitted_at,
            effective_at=submitted_at,
            answer="A disposable SB-2 route witness answer.",
        ))
        submission_facts = json.loads(submitted.authority_reference.removeprefix("s015a1:"))
        self.assertEqual(
            submission_facts["b"],
            f"living-organism-event:{event.event_uuid}",
        )
        application = Application.objects.get(application_uuid=submitted.application_uuid)
        claim_occurred_at = database_now()
        claim = ClaimApplicationCommand(
            credential=route.founder.credential,
            application_uuid=application.application_uuid,
            request_reference=f"sb2:claim:full-route:{route.tag}",
            idempotency_key=f"sb2:claim:key:full-route:{route.tag}",
            occurred_at=claim_occurred_at,
            effective_at=claim_occurred_at,
        )
        application_service.claim_application(claim)
        decision_at = database_now()
        decision = DecideApplicationCommand(
            credential=route.founder.credential,
            application_uuid=application.application_uuid,
            request_reference=f"sb2:decide:full-route:{route.tag}",
            idempotency_key=f"sb2:decide:key:full-route:{route.tag}",
            occurred_at=decision_at,
            effective_at=decision_at,
            outcome="ACCEPT",
        )
        try:
            application_service.decide_application(decision)
        except S015AuthorityDenied as refusal:
            authority_decision = AuthorityDecision.objects.filter(
                decision_reference=refusal.authority_decision_reference,
                request_reference=decision.request_reference,
                action=OrganismAction.DECIDE_APPLICATION.value,
                outcome="REFUSED",
            ).first()
            engagement = ApplicationEngagement.objects.filter(
                application=application,
            ).order_by("-engagement_ordinal").first()
            founder_claim = CSAAssignment.objects.filter(
                application=application,
                actor=route.founder,
            ).order_by("-recorded_at", "-pk").first()
            if authority_decision is None:
                self.fail(
                    "DECIDE was refused but its persisted REFUSED AuthorityDecision was not found; "
                    f"exception_refusal_code={getattr(refusal.refusal_code, 'value', refusal.refusal_code)!r}; "
                    f"decision_reference={refusal.authority_decision_reference!r}; "
                    f"request_reference={decision.request_reference!r}; "
                    f"command_times={(decision.occurred_at, decision.effective_at, decision.received_at)!r}"
                )
            planes = qualification_planes(authority_decision.command_subtype)
            plane_bindings = {
                plane.value: {
                    "state": binding.state.value,
                    "reason": binding.reason,
                }
                for plane, binding in planes.items()
            }
            persisted_code = authority_decision.refusal_code
            refusal_plane = (
                persisted_code.split("_", 1)[0]
                if persisted_code is not None and persisted_code.startswith(("C_", "E_", "P_", "L_"))
                else "not encoded by refusal-code prefix"
            )
            self.fail(
                "DECIDE refusal diagnostic: "
                f"exception_refusal_code={getattr(refusal.refusal_code, 'value', refusal.refusal_code)!r}; "
                f"persisted_refusal_code={persisted_code!r}; "
                f"refusal_plane={refusal_plane!r}; "
                f"qualification_planes={json.dumps(plane_bindings, sort_keys=True, default=str)}; "
                f"facts_read={json.dumps(authority_decision.facts_read, sort_keys=True, default=str)}; "
                f"provider_decision_payload="
                f"{json.dumps(authority_decision.decision_payload, sort_keys=True, default=str)}; "
                f"command_times="
                f"{(decision.occurred_at, decision.effective_at, decision.received_at)!r}; "
                f"database_recorded_at="
                f"{{'application': {application.recorded_at!r}, "
                f"'engagement': {None if engagement is None else engagement.recorded_at!r}, "
                f"'founder_claim': {None if founder_claim is None else founder_claim.recorded_at!r}}}"
            )

        self.assertEqual(
            ApplicationDecision.objects.get(application=application).outcome,
            "ACCEPT",
        )
        source = ProductionBirthingFactSource()
        self.assertEqual(
            source.reference_at(route.organism.organism_id, event.effective_at, event.recorded_at),
            f"living-organism-event:{event.event_uuid}",
        )

        from core.models import ContextualRoleAssignment, ContextualRoleAssignmentStateEvent, OrganismMembership, OrganismRoleDefinition
        from src.intevia.services.organism_office_consent_service import (
            OfficeAppointmentConsentService,
            RecordOfficeConsentCommand,
        )
        from src.intevia.services.organism_role_service import (
            OrganismRoleService,
            ProposeAssignmentCommand,
            TransitionAssignmentCommand,
        )

        applicant_membership = OrganismMembership.objects.get(
            identity=applicant,
            living_organism=route.organism,
        )
        self.assertEqual(applicant_membership.state, "ACTIVE")
        coordinator_role = OrganismRoleDefinition.objects.get(
            living_organism=route.organism,
            code="COORDINATOR",
            scope="LIVING_ORGANISM",
        )
        founder_membership = OrganismMembership.objects.get(
            identity=route.founder,
            living_organism=route.organism,
        )
        founding_assignment = ContextualRoleAssignment.objects.get(
            membership=founder_membership,
            role_definition=coordinator_role,
        )
        founding_event = LivingOrganismEvent.objects.get(
            living_organism=route.organism,
            sequence=1,
            action="FOUND",
        )
        founding_decision = AuthorityDecision.objects.get(
            decision_reference=founding_event.authority_decision_reference,
        )
        founding_assignment_event = ContextualRoleAssignmentStateEvent.objects.get(
            assignment=founding_assignment,
            sequence=1,
            action="ASSIGN",
        )
        self.assertEqual(
            _founding_appointment_by_record(
                database_alias="default",
                assignment_id=founding_assignment.pk,
                living_organism_id=route.organism.pk,
            ),
            founding_assignment.pk,
        )
        self.assertEqual(founder_membership.identity_id, route.founder.pk)
        self.assertEqual(founding_assignment.role_definition.code, "COORDINATOR")
        self.assertEqual(founding_assignment.role_definition.scope, "LIVING_ORGANISM")
        self.assertEqual(founder_membership.living_organism_id, route.organism.pk)
        self.assertEqual(
            founding_assignment_event.authority_basis_id,
            founding_event.authority_basis_id,
        )
        self.assertEqual(
            founding_assignment_event.evidence_reference,
            f"founding-batch:{founding_event.event_uuid}",
        )
        self.assertEqual(
            founding_assignment_event.authority_decision_reference,
            founding_event.authority_decision_reference,
        )
        self.assertEqual(founding_decision.action, OrganismAction.FOUND_LIVING_ORGANISM.value)
        self.assertEqual(founding_decision.outcome, "QUALIFIED")
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT actor_state, actor_id, enterer_identity_id "
                "FROM public.core_contextualroleassignmentstateevent WHERE id = %s",
                [founding_assignment_event.pk],
            )
            founding_actor_state, founding_actor_id, founding_enterer_id = cursor.fetchone()
        self.assertEqual(founding_actor_state, "PARTY")
        self.assertEqual(founding_actor_id, route.founder.pk)
        self.assertEqual(founding_enterer_id, route.founder.pk)
        provider = ProductionApplicationAuthorityProvider()
        role_service = OrganismRoleService(
            authority=S015Authority(provider=provider),
            clock=database_now,
        )
        proposal_at = database_now()
        proposal_result = role_service.propose_assignment(ProposeAssignmentCommand(
            credential=route.founder.credential,
            request_reference=f"sb2:second-coordinator:propose:{route.tag}",
            idempotency_key=f"sb2:second-coordinator:propose:key:{route.tag}",
            occurred_at=proposal_at,
            effective_at=proposal_at,
            membership_uuid=applicant_membership.membership_uuid,
            role_definition_uuid=coordinator_role.role_uuid,
            effective_until_state="NONE",
            effective_until=None,
        ))
        proposal_event = ContextualRoleAssignmentStateEvent.objects.get(
            assignment__assignment_uuid=proposal_result.assignment_uuid,
            sequence=1,
        )
        consent_service = OfficeAppointmentConsentService(
            authority=S015Authority(provider=provider),
            clock=database_now,
        )
        consent_at = database_now()
        consent_key = f"sb2:second-coordinator:consent:key:{route.tag}"
        consent_command = RecordOfficeConsentCommand(
            credential=applicant.credential,
            request_reference=f"sb2:second-coordinator:consent:{route.tag}",
            idempotency_key=consent_key,
            occurred_at=consent_at,
            effective_at=proposal_at,
            assignment_uuid=proposal_result.assignment_uuid,
            proposal_event_uuid=proposal_event.event_uuid,
            action="CONSENT",
        )
        consent_result = consent_service.record_office_consent(consent_command)
        self.assertIsNotNone(consent_result.consent_uuid)
        from core.models import IntakeEntry

        _assert_single_intake(self, IntakeEntry.RECORD_OFFICE_CONSENT, consent_command)
        assign_at = database_now()
        role_service.transition_assignment(TransitionAssignmentCommand(
            credential=route.founder.credential,
            request_reference=f"sb2:second-coordinator:assign:{route.tag}",
            idempotency_key=f"sb2:second-coordinator:assign:key:{route.tag}",
            occurred_at=assign_at,
            effective_at=assign_at,
            assignment_uuid=proposal_result.assignment_uuid,
            action="ASSIGN",
            resulting_state="ACTIVE",
            effective_until_state="NONE",
        ))
        applicant_assignment = ContextualRoleAssignment.objects.get(
            assignment_uuid=proposal_result.assignment_uuid,
        )
        self.assertIsNone(
            _founding_appointment_by_record(
                database_alias="default",
                assignment_id=applicant_assignment.pk,
                living_organism_id=route.organism.pk,
            )
        )

        end_at = database_now()
        end_result = role_service.transition_assignment(TransitionAssignmentCommand(
            credential=applicant.credential,
            request_reference=f"sb2:second-coordinator:end-founder:{route.tag}",
            idempotency_key=f"sb2:second-coordinator:end-founder:key:{route.tag}",
            occurred_at=end_at,
            effective_at=end_at,
            assignment_uuid=founding_assignment.assignment_uuid,
            action="END",
            resulting_state="ENDED",
        ))
        founding_assignment.refresh_from_db()
        self.assertEqual(founding_assignment.state, "ENDED")
        self.assertEqual(end_result.event_action, "END")
        end_event = ContextualRoleAssignmentStateEvent.objects.get(
            assignment=founding_assignment,
            sequence=2,
            action="END",
        )
        end_decision = AuthorityDecision.objects.get(
            decision_reference=end_event.authority_decision_reference,
        )
        self.assertEqual(end_decision.outcome, "QUALIFIED")
        self.assertEqual(end_decision.action, OrganismAction.TRANSITION_CONTEXTUAL_ROLE.value)
        self.assertEqual(end_decision.actor_id, applicant.pk)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT actor_state, actor_id, enterer_identity_id "
                "FROM public.core_contextualroleassignmentstateevent WHERE id = %s",
                [end_event.pk],
            )
            end_actor_state, end_actor_id, end_enterer_id = cursor.fetchone()
        self.assertEqual(end_actor_state, "LIVING_ORGANISM")
        self.assertIsNone(end_actor_id)
        self.assertEqual(end_enterer_id, applicant.pk)

        reappointment_proposal_at = database_now()
        reappointment_proposal = role_service.propose_assignment(ProposeAssignmentCommand(
            credential=applicant.credential,
            request_reference=f"sb2:founder-reappointment:propose:{route.tag}",
            idempotency_key=f"sb2:founder-reappointment:propose:key:{route.tag}",
            occurred_at=reappointment_proposal_at,
            effective_at=reappointment_proposal_at,
            membership_uuid=founder_membership.membership_uuid,
            role_definition_uuid=coordinator_role.role_uuid,
            effective_until_state="NONE",
            effective_until=None,
        ))
        reappointment_proposal_event = ContextualRoleAssignmentStateEvent.objects.get(
            assignment__assignment_uuid=reappointment_proposal.assignment_uuid,
            sequence=1,
        )
        reappointment_consent_at = database_now()
        consent_service.record_office_consent(RecordOfficeConsentCommand(
            credential=route.founder.credential,
            request_reference=f"sb2:founder-reappointment:consent:{route.tag}",
            idempotency_key=f"sb2:founder-reappointment:consent:key:{route.tag}",
            occurred_at=reappointment_consent_at,
            effective_at=reappointment_proposal_at,
            assignment_uuid=reappointment_proposal.assignment_uuid,
            proposal_event_uuid=reappointment_proposal_event.event_uuid,
            action="CONSENT",
        ))
        reappointment_assign_at = database_now()
        role_service.transition_assignment(TransitionAssignmentCommand(
            credential=applicant.credential,
            request_reference=f"sb2:founder-reappointment:assign:{route.tag}",
            idempotency_key=f"sb2:founder-reappointment:assign:key:{route.tag}",
            occurred_at=reappointment_assign_at,
            effective_at=reappointment_assign_at,
            assignment_uuid=reappointment_proposal.assignment_uuid,
            action="ASSIGN",
            resulting_state="ACTIVE",
            effective_until_state="NONE",
        ))
        founder_reappointment = ContextualRoleAssignment.objects.get(
            assignment_uuid=reappointment_proposal.assignment_uuid,
        )
        self.assertIsNone(
            _founding_appointment_by_record(
                database_alias="default",
                assignment_id=founder_reappointment.pk,
                living_organism_id=route.organism.pk,
            )
        )
        reappointment_end_at = database_now()
        reappointment_end = role_service.transition_assignment(TransitionAssignmentCommand(
            credential=applicant.credential,
            request_reference=f"sb2:founder-reappointment:end:{route.tag}",
            idempotency_key=f"sb2:founder-reappointment:end:key:{route.tag}",
            occurred_at=reappointment_end_at,
            effective_at=reappointment_end_at,
            assignment_uuid=founder_reappointment.assignment_uuid,
            action="END",
            resulting_state="ENDED",
        ))
        reappointment_decision = AuthorityDecision.objects.get(
            decision_reference=reappointment_end.authority_decision_reference,
        )
        self.assertEqual(reappointment_decision.outcome, "QUALIFIED")
        self.assertEqual(
            reappointment_decision.action,
            OrganismAction.TRANSITION_CONTEXTUAL_ROLE.value,
        )

    def test_electorate_two_persists_refusal_decision_without_birth_artifacts(self):
        route = prepare_route()
        add_active_elector(route)
        before = _counts()
        with self.assertRaises(S015AuthorityDenied) as refused:
            birth(route, suffix="electorate")
        self.assertIs(
            refused.exception.refusal_code,
            S015AuthorityRefusalCode.INV_CQ_ROUTE_REQUIRED,
        )
        _assert_birthing_refusal_record(
            self,
            route,
            "electorate",
            refused.exception,
            S015AuthorityRefusalCode.INV_CQ_ROUTE_REQUIRED,
            before,
        )

    def test_empty_electorate_is_refused_at_guard_without_writes(self):
        """The database guard rejects the attempted Birthing insert; all transaction writes roll back."""
        route = prepare_route()
        ensure_test_policy_text()
        membership, effective_at = _set_empty_electorate(route)
        electorate_count = _birthing_electorate_count(route, effective_at, database_now())
        self.assertEqual(electorate_count, 0)
        before = (
            _counts(),
            electorate_count,
            membership.state,
            membership.head_transition_id,
        )
        before_state = _whole_birthing_state()
        provider = _ControlledBirthingProvider()
        command = BirthingCommand(
            credential=route.founder.credential,
            living_organism_uuid=route.organism.organism_id,
            request_reference=f"sb2:empty-electorate-guard:{route.tag}",
            idempotency_key=f"sb2:empty-electorate-guard:key:{route.tag}",
            effective_at=effective_at,
            offering_choice="FREE",
            policy_version=TEST_POLICY_VERSION,
            policy_text_sha256=TEST_POLICY_SHA256,
        )
        before_intake_count = CommandIntake.objects.count()
        with (
            patch(
                "src.intevia.services.organism_formation_service._lock_actor_membership_context",
                return_value=_controlled_membership_context(route),
            ),
            patch.object(route.birthing_service, "_authority", S015Authority(provider=provider)),
            # Decision B: this scoped authority-provider-interface stand-in qualifies E-1a so the database guard runs.
            _qualify_election_for_birthing_guard(route) as election_provider,
        ):
            with self.assertRaises(IntegrityError) as refused:
                with disclose_authority_refusal_code():
                    route.birthing_service.birth_living_organism(command)
        self.assertEqual(CommandIntake.objects.count(), before_intake_count)
        guard_error = str(refused.exception)
        self.assertIn("SB_BIRTHING_ELECTORATE_EMPTY", guard_error)
        self.assertNotIn("INV_CQ_ROUTE_REQUIRED", guard_error)
        self.assertIsNotNone(provider.request)
        self.assertIsNotNone(election_provider.request)
        membership.refresh_from_db()
        self.assertEqual(
            (
                _counts(),
                _birthing_electorate_count(route, effective_at, database_now()),
                membership.state,
                membership.head_transition_id,
            ),
            before,
        )
        # The insert reaches the database guard; earlier transactional writes are rolled back, not absent.
        self.assertEqual(_whole_birthing_state(), before_state)

    def test_service_maps_controlled_empty_electorate_refusal_to_authored_text(self):
        """A controlled Birthing refusal persists exactly its refusal decision."""
        route = prepare_route()
        ensure_test_policy_text()
        _, effective_at = _set_empty_electorate(route)
        self.assertEqual(
            _birthing_electorate_count(route, effective_at, database_now()),
            0,
        )
        before = _counts()
        before_state = _whole_birthing_state()
        provider = _ControlledBirthingProvider(
            refusal_code=S015AuthorityRefusalCode.SB_BIRTHING_ELECTORATE_EMPTY,
        )
        command = BirthingCommand(
            credential=route.founder.credential,
            living_organism_uuid=route.organism.organism_id,
            request_reference=f"sb2:empty-electorate:{route.tag}",
            idempotency_key=f"sb2:empty-electorate:key:{route.tag}",
            effective_at=effective_at,
            offering_choice="FREE",
            policy_version=TEST_POLICY_VERSION,
            policy_text_sha256=TEST_POLICY_SHA256,
        )
        before_intake_count = CommandIntake.objects.count()
        with (
            patch(
                "src.intevia.services.organism_formation_service._lock_actor_membership_context",
                return_value=_controlled_membership_context(route),
            ),
            patch.object(route.birthing_service, "_authority", S015Authority(provider=provider)),
            # Decision B's scoped authority-provider-interface stand-in is not reached; the controlled refusal occurs first.
            _qualify_election_for_birthing_guard(route) as election_provider,
        ):
            with self.assertRaises(S015AuthorityDenied) as refused:
                with disclose_authority_refusal_code():
                    route.birthing_service.birth_living_organism(command)
        self.assertEqual(CommandIntake.objects.count(), before_intake_count)
        self.assertIs(
            refused.exception.refusal_code,
            S015AuthorityRefusalCode.SB_BIRTHING_ELECTORATE_EMPTY,
        )
        self.assertEqual(
            str(refused.exception),
            "The LO was not born, and nothing changed. On this sole-founder route, Birthing needs "
            "exactly one active member of the LO's electorate at the moment of Birthing, and this "
            "LO has none. This is a governed requirement of the route, not a technical fault.",
        )
        _assert_birthing_refusal_record(
            self,
            route,
            "empty-electorate",
            refused.exception,
            S015AuthorityRefusalCode.SB_BIRTHING_ELECTORATE_EMPTY,
            before,
        )
        self.assertIsNone(election_provider.request)
        _assert_only_birthing_refusal_persisted(self, before_state, refused.exception)

    def test_production_birthing_qualification_refuses_empty_electorate(self):
        """Production Birthing qualification still independently refuses an empty electorate."""
        route = prepare_route()
        ensure_test_policy_text()
        membership, effective_at = _set_empty_electorate(route)
        self.assertEqual(
            _birthing_electorate_count(route, effective_at, database_now()),
            0,
        )
        before = _counts()
        before_state = _whole_birthing_state()
        provider = _ControlledBirthingProvider(
            refusal_code=S015AuthorityRefusalCode.SB_BIRTHING_ELECTORATE_EMPTY,
        )
        command = BirthingCommand(
            credential=route.founder.credential,
            living_organism_uuid=route.organism.organism_id,
            request_reference=f"sb2:empty-electorate-qualification:{route.tag}",
            idempotency_key=f"sb2:empty-electorate-qualification:key:{route.tag}",
            effective_at=effective_at,
            offering_choice="FREE",
            policy_version=TEST_POLICY_VERSION,
            policy_text_sha256=TEST_POLICY_SHA256,
        )
        before_intake_count = CommandIntake.objects.count()
        with (
            patch(
                "src.intevia.services.organism_formation_service._lock_actor_membership_context",
                return_value=_controlled_membership_context(route),
            ),
            patch.object(route.birthing_service, "_authority", S015Authority(provider=provider)),
            # Decision B's scoped authority-provider-interface stand-in is not reached; the controlled refusal occurs first.
            _qualify_election_for_birthing_guard(route) as election_provider,
        ):
            with self.assertRaises(S015AuthorityDenied) as refused:
                with disclose_authority_refusal_code():
                    route.birthing_service.birth_living_organism(command)
        self.assertEqual(CommandIntake.objects.count(), before_intake_count)
        request = provider.request
        if request is None:
            self.fail("controlled provider did not capture the qualification request")
        if request.effective_at is None:
            self.fail("controlled qualification request did not preserve its effective coordinate")
        self.assertEqual(
            _birthing_electorate_count(route, request.effective_at, request.evaluated_at),
            0,
        )
        office = ContextualRoleAssignment.objects.filter(
            membership=membership,
            role_definition__code="COORDINATOR",
        ).first()
        if office is None:
            self.fail("controlled empty-electorate fixture has no Coordinator office")
        qualification = ProductionApplicationAuthorityProvider()._qualify_birthing(
            request,
            route.organism,
            office,
        )
        self.assertIsInstance(qualification, S015PostFoundingAuthorityRefusal)
        self.assertIs(
            qualification.refusal_code,
            S015AuthorityRefusalCode.SB_BIRTHING_ELECTORATE_EMPTY,
        )
        self.assertEqual(
            _counts(),
            (before[0], before[1], before[2], before[3] + 1, before[4]),
        )
        self.assertIsNone(election_provider.request)
        _assert_only_birthing_refusal_persisted(self, before_state, refused.exception)

    def test_missing_phase_settings_and_question_have_authored_refusals(self):
        no_phase = prepare_route(to_birthing_phase=False)
        before_phase = _counts()
        with self.assertRaises(S015AuthorityDenied) as phase_refusal:
            birth(no_phase, suffix="missing-phase")
        self.assertIs(
            phase_refusal.exception.refusal_code,
            S015AuthorityRefusalCode.BIRTHING_PREREQUISITE_MISSING,
        )
        self.assertIn("the phase to read Birthing", str(phase_refusal.exception))
        self.assertEqual(
            str(phase_refusal.exception),
            "The LO was not born and nothing changed. Birthing needs the phase to read Birthing, "
            "which is not yet in place. Complete it, then birth the LO.",
        )
        _assert_birthing_refusal_record(
            self,
            no_phase,
            "missing-phase",
            phase_refusal.exception,
            S015AuthorityRefusalCode.BIRTHING_PREREQUISITE_MISSING,
            before_phase,
        )

        no_settings = prepare_route(include_settings=False)
        before_settings = _counts()
        with self.assertRaises(S015AuthorityDenied) as settings_refusal:
            birth(no_settings, suffix="missing-settings")
        self.assertIs(
            settings_refusal.exception.refusal_code,
            S015AuthorityRefusalCode.BIRTHING_PREREQUISITE_MISSING,
        )
        self.assertIn("both application settings chosen", str(settings_refusal.exception))
        self.assertEqual(
            str(settings_refusal.exception),
            "The LO was not born and nothing changed. Birthing needs both application settings "
            "chosen, which is not yet in place. Complete it, then birth the LO.",
        )
        _assert_birthing_refusal_record(
            self,
            no_settings,
            "missing-settings",
            settings_refusal.exception,
            S015AuthorityRefusalCode.BIRTHING_PREREQUISITE_MISSING,
            before_settings,
        )

        no_question = prepare_route(include_question=False)
        before_question = _counts()
        with self.assertRaises(S015AuthorityDenied) as question_refusal:
            birth(no_question, suffix="missing-question")
        self.assertIs(
            question_refusal.exception.refusal_code,
            S015AuthorityRefusalCode.BIRTHING_PREREQUISITE_MISSING,
        )
        self.assertIn("a reviewed application question", str(question_refusal.exception))
        self.assertEqual(
            str(question_refusal.exception),
            "The LO was not born and nothing changed. Birthing needs a reviewed application "
            "question, which is not yet in place. Complete it, then birth the LO.",
        )
        _assert_birthing_refusal_record(
            self,
            no_question,
            "missing-question",
            question_refusal.exception,
            S015AuthorityRefusalCode.BIRTHING_PREREQUISITE_MISSING,
            before_question,
        )

    def test_governance_yes_before_birth_persists_refusal_decision_without_birth_artifacts(self):
        route = prepare_route(governance_enabled=False)
        effect_gateway(
            route.application_service,
            route.founder,
            route.organism,
            f"{route.tag}-governance-yes",
            OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
            {
                "cq_active": False,
                "governance_enabled": True,
                "inherit_portal_cq_settings": False,
                "require_cq_to_change_lo_settings": False,
                "require_cq_to_change_application_question": False,
            },
        )
        before = _counts()
        with self.assertRaises(S015AuthorityDenied) as refused:
            birth(route, suffix="governance-yes")
        self.assertIs(
            refused.exception.refusal_code,
            S015AuthorityRefusalCode.SBG_GOVERNANCE_CIRCLE_MISSING,
        )
        _assert_birthing_refusal_record(
            self,
            route,
            "governance-yes",
            refused.exception,
            S015AuthorityRefusalCode.SBG_GOVERNANCE_CIRCLE_MISSING,
            before,
        )
        self.assertEqual(
            str(refused.exception),
            "The LO was not born and nothing changed. GOVERNANCE is on for this LO, so it needs "
            "its GOVERNANCE Circle first. Create the LO's GOVERNANCE Circle, then try again. "
            "Creating the Circle early in formation avoids this.",
        )
        self.assertTrue(
            LivingOrganismEvent.objects.filter(
                living_organism=route.organism,
                action="EFFECT_GOVERNANCE_CONFIGURATION",
            ).exists()
        )
        effective_configuration = LivingOrganismEvent.objects.filter(
            living_organism=route.organism,
            action="EFFECT_GOVERNANCE_CONFIGURATION",
        ).order_by("-sequence").first()
        configuration_uuid = effective_configuration.evidence_reference.split(":")[1]
        self.assertIs(
            LOGovernanceConfigurationVersion.objects.get(
                version_uuid=configuration_uuid,
            ).governance_enabled,
            True,
        )

    def test_second_birth_is_refused_without_changing_the_active_organism(self):
        route = prepare_route()
        birth(route)
        route.organism.refresh_from_db()
        before_state = route.organism.state
        before = _counts()
        with self.assertRaises(S015AuthorityDenied) as refused:
            birth(route, suffix="second-birth")
        self.assertIs(
            refused.exception.refusal_code,
            S015AuthorityRefusalCode.BIRTHING_ALREADY_RECORDED,
        )
        self.assertRegex(
            str(refused.exception),
            r"^The LO was not born again and nothing changed: it was born on \d{4}-\d{2}-\d{2}\. "
            r"An LO is born once\.$",
        )
        _assert_birthing_refusal_record(
            self,
            route,
            "second-birth",
            refused.exception,
            S015AuthorityRefusalCode.BIRTHING_ALREADY_RECORDED,
            before,
        )
        route.organism.refresh_from_db()
        self.assertEqual(route.organism.state, before_state)

    def test_snapshot_references_and_commitment_match_effective_records(self):
        route = prepare_route()
        result = birth(route)
        snapshot = LOBirthingSnapshot.objects.get(snapshot_uuid=result.snapshot_uuid)
        body = snapshot.body
        self.assertEqual(body["constitutional_documents"], [])
        self.assertEqual(body["laws"], [])
        self.assertIsNone(body["governance_constitution"])
        self.assertEqual(
            body["formation_history_reference"]["lo_chain_sequence_range"],
            [1, result.event_sequence - 1],
        )
        self.assertEqual(
            body["settings"]["governance_configuration"],
            "governance-configuration-version:"
            + str(
                LOGovernanceConfigurationVersion.objects.get(
                    version_uuid=body["settings"]["governance_configuration"].split(":")[1]
                ).version_uuid
            ),
        )
        settings_uuid = body["settings"]["application_settings"].split(":")[1]
        question_uuid = body["settings"]["application_question"].split(":")[1]
        settings = LOApplicationSettingsVersion.objects.get(
            living_organism=route.organism,
            version_uuid=settings_uuid,
            entitlement_choice__isnull=True,
            entitlement_election_route__isnull=True,
        )
        question = LOApplicationQuestionVersion.objects.get(
            living_organism=route.organism,
            version_uuid=question_uuid,
        )
        content = LOFoundingContentVersion.objects.get(
            living_organism=route.organism,
            version=1,
        )
        acceptance = PlatformTermsAcceptance.objects.get(
            acceptance_kind=PlatformTermsAcceptance.Kind.FOUNDING,
            subject_reference=f"living-organism:{route.organism.organism_id}",
        )
        self.assertEqual(
            body["settings"]["application_settings"],
            f"application-settings-version:{settings.version_uuid}",
        )
        self.assertEqual(
            body["settings"]["application_question"],
            f"application-question-version:{question.version_uuid}",
        )
        self.assertEqual(
            body["founding_content"],
            f"founding-content:{content.content_uuid}@{content.version}",
        )
        self.assertEqual(
            body["platform_terms_acceptance"],
            f"platform-terms-acceptance:{acceptance.acceptance_uuid}",
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT public.s015_0022_digest('s015r1', "
                "'{\"body\":' || to_json(%s::jsonb)::text "
                "|| ',\"effective_at\":' || to_json(%s::timestamptz)::text "
                "|| ',\"living_organism_uuid\":' || to_json(%s::uuid::text)::text "
                "|| ',\"received_at\":' || to_json(%s::timestamptz)::text "
                "|| ',\"snapshot_uuid\":' || to_json(%s::uuid::text)::text || '}')",
                [
                    json.dumps(body),
                    snapshot.effective_at,
                    route.organism.organism_id,
                    snapshot.received_at,
                    snapshot.snapshot_uuid,
                ],
            )
            self.assertEqual(snapshot.snapshot_commitment, cursor.fetchone()[0])

    def test_birthing_snapshot_with_non_effective_setting_is_refused_by_guard(self):
        route = prepare_route()
        before = _counts()
        use_manager = LOBirthingSnapshot.objects.using

        def using_with_invalid_snapshot(alias):
            queryset = use_manager(alias)
            create_snapshot = queryset.create

            def create_with_wrong_setting(*args, **kwargs):
                body = dict(kwargs["body"])
                settings = dict(body["settings"])
                settings["application_settings"] = f"application-settings-version:{uuid4()}"
                body["settings"] = settings
                kwargs["body"] = body
                return create_snapshot(*args, **kwargs)

            queryset.create = create_with_wrong_setting
            return queryset

        with patch(
            "src.intevia.services.organism_formation_service.LOBirthingSnapshot.objects.using",
            side_effect=using_with_invalid_snapshot,
        ):
            with self.assertRaises(S015BirthingTechnicalFailure) as technical:
                birth(route, suffix="wrong-snapshot-setting")
        self.assertEqual(
            str(technical.exception),
            "The LO was not born and nothing changed: the constitutional snapshot did not match "
            "the LO's settings in effect at this time. This is a record inconsistency, not a "
            "decision against the LO. Prepare the snapshot again and retry.",
        )
        self.assertFalse(hasattr(technical.exception, "refusal_code"))
        self.assertEqual(_counts(), before)

    def test_production_birthing_source_observes_both_temporal_coordinates(self):
        route = prepare_route()
        result = birth(route)
        event = LivingOrganismEvent.objects.get(event_uuid=result.event_uuid)
        source = ProductionBirthingFactSource()
        reference = f"living-organism-event:{event.event_uuid}"
        self.assertEqual(
            source.reference_at(route.organism.organism_id, event.effective_at, event.recorded_at),
            reference,
        )
        self.assertIsNone(
            source.reference_at(
                route.organism.organism_id,
                event.effective_at - timedelta(microseconds=1),
                event.recorded_at,
            )
        )
        self.assertIsNone(
            source.reference_at(
                route.organism.organism_id,
                event.effective_at,
                event.recorded_at - timedelta(microseconds=1),
            )
        )

    def test_birthing_and_existing_same_state_effects_pass_but_other_same_state_is_refused(self):
        route = prepare_route()
        self.assertTrue(
            LivingOrganismEvent.objects.filter(
                living_organism=route.organism,
                action="EFFECT_APPLICATION_SETTINGS",
                prior_state="ACTIVE",
                resulting_state="ACTIVE",
            ).exists()
        )
        self.assertTrue(
            LivingOrganismEvent.objects.filter(
                living_organism=route.organism,
                action="PHASE_ADVANCE",
                prior_state="ACTIVE",
                resulting_state="ACTIVE",
            ).exists()
        )
        result = birth(route)
        event = LivingOrganismEvent.objects.get(event_uuid=result.event_uuid)
        route.organism.refresh_from_db()
        predecessor = LivingOrganismEvent.objects.get(pk=route.organism.head_event_id)
        with self.assertRaisesMessage(
            IntegrityError,
            "S3FU gateway effect: same-state event without effect action",
        ):
            with transaction.atomic(), connection.cursor() as cursor:
                append_event(
                    cursor,
                    "core_livingorganismevent",
                    route.organism.pk,
                    predecessor.sequence + 1,
                    "UNMAPPED_SAME_STATE",
                    "ACTIVE",
                    "ACTIVE",
                    route.founder.pk,
                    route.organism.founding_authority_basis_id,
                    f"sb2:unmapped:{route.tag}",
                    pred_id=predecessor.pk,
                )
        self.assertEqual(event.action, "BIRTHING")

    def test_matrix_profile_and_unknown_subtype(self):
        subtype = "TRANSITION_LIVING_ORGANISM/BIRTHING"
        planes = qualification_planes(subtype)
        self.assertEqual(planes, QUALIFICATION_PLANE_MATRIX[subtype])
        self.assertEqual(
            {plane: binding.state for plane, binding in planes.items()},
            {
                QualificationPlane.LEGAL: QualificationPlaneState.NOT_APPLICABLE_WITH_REASON,
                QualificationPlane.CONSTITUTIONAL: QualificationPlaneState.PREREQUISITE_REQUIRED,
                QualificationPlane.EXECUTION: QualificationPlaneState.PREREQUISITE_REQUIRED,
                QualificationPlane.PROPAGATION: QualificationPlaneState.FORBIDDEN,
            },
        )
        self.assertEqual(planes[QualificationPlane.LEGAL].reason, "NO_LEGAL_CONCLUSION")
        with self.assertRaises(QualificationApplicabilityUnknown):
            qualification_planes("TRANSITION_LIVING_ORGANISM/UNRECOGNISED")

    def test_receipt_decision_replay_and_mismatched_retry(self):
        """Birthing replay and mismatch checks retain the original exception evidence."""
        route = prepare_route()
        ensure_test_policy_text()
        effective_at = database_now()
        received_at = effective_at + timedelta(microseconds=1)
        service = OrganismBirthingService(clock=lambda: received_at)
        command = BirthingCommand(
            credential=route.founder.credential,
            living_organism_uuid=route.organism.organism_id,
            request_reference=f"sb2:replay:{route.tag}",
            idempotency_key=f"sb2:replay:key:{route.tag}",
            effective_at=effective_at,
            offering_choice="FREE",
            policy_version=TEST_POLICY_VERSION,
            policy_text_sha256=TEST_POLICY_SHA256,
        )
        with disclose_authority_refusal_code():
            first = service.birth_living_organism(command)
        before_replay = _counts()
        decision = AuthorityDecision.objects.get(
            decision_reference=first.authority_decision_reference,
        )
        receipt = OrganismCommandReceipt.objects.get(
            actor=route.founder,
            action=OrganismAction.TRANSITION_LIVING_ORGANISM.value,
            idempotency_key=command.idempotency_key,
        )
        self.assertEqual(decision.outcome, "QUALIFIED")
        self.assertEqual(receipt.authority_decision_reference, first.authority_decision_reference)
        with disclose_authority_refusal_code():
            self.assertEqual(service.birth_living_organism(command), first)
        self.assertEqual(_counts(), before_replay)
        with self.assertRaises(S015CommandReplayMismatch):
            with disclose_authority_refusal_code():
                service.birth_living_organism(
                    replace(command, request_reference=f"sb2:replay-mismatch:{route.tag}")
                )
        self.assertEqual(_counts(), before_replay)

    def test_founder_cannot_enable_governance_after_birth_but_other_configuration_effects(self):
        route = prepare_route(governance_enabled=False)
        birth(route)
        draft = _governance_draft(route, True, "after-birth")
        before = _counts()
        with self.assertRaises(S015ApplicationRefused) as refused:
            route.application_service.effect_gateway_version(
                _governance_effect_command(route, draft.version_uuid, "after-birth")
            )
        self.assertIs(
            refused.exception.refusal_code,
            S015AuthorityRefusalCode.SBG_GOVERNANCE_CIRCLE_MISSING,
        )
        self.assertEqual(
            str(refused.exception),
            "GOVERNANCE was not switched on and nothing changed. GOVERNANCE is on for this LO, "
            "so it needs its GOVERNANCE Circle first. Create the LO's GOVERNANCE Circle, then "
            "try again. Creating the Circle early in formation avoids this.",
        )
        self.assertEqual(
            _counts(),
            (before[0], before[1], before[2], before[3] + 1, before[4]),
        )
        refusal_decision = AuthorityDecision.objects.get(
            actor=route.founder,
            request_reference=(
                f"sb2:governance-effect:after-birth:{route.tag}"
            ),
        )
        self.assertEqual(refusal_decision.outcome, "REFUSED")
        self.assertEqual(
            refusal_decision.refusal_code,
            S015AuthorityRefusalCode.SBG_GOVERNANCE_CIRCLE_MISSING.value,
        )

        effect_gateway(
            route.application_service,
            route.founder,
            route.organism,
            f"{route.tag}-no-kept",
            OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
            {
                "cq_active": False,
                "governance_enabled": False,
                "inherit_portal_cq_settings": False,
                "require_cq_to_change_lo_settings": True,
                "require_cq_to_change_application_question": False,
            },
        )
        self.assertTrue(
            LivingOrganismEvent.objects.filter(
                living_organism=route.organism,
                action="EFFECT_GOVERNANCE_CONFIGURATION",
            ).exists()
        )

    def test_open_transaction_is_a_technical_failure_without_writes(self):
        """An already-open transaction remains a technical failure without writes."""
        route = prepare_route()
        command = BirthingCommand(
            credential=route.founder.credential,
            living_organism_uuid=route.organism.organism_id,
            request_reference=f"sb2:transaction:{route.tag}",
            idempotency_key=f"sb2:transaction:key:{route.tag}",
            effective_at=database_now(),
            offering_choice="FREE",
            policy_version=TEST_POLICY_VERSION,
            policy_text_sha256=TEST_POLICY_SHA256,
        )
        before = _counts()
        with self.assertRaises(S015BirthingTechnicalFailure) as technical:
            with transaction.atomic():
                with disclose_authority_refusal_code():
                    route.birthing_service.birth_living_organism(command)
        self.assertEqual(
            str(technical.exception),
            "BIRTHING could not be completed because of a technical problem; nothing was saved. "
            "Try again; if it persists, report it.",
        )
        self.assertFalse(hasattr(technical.exception, "refusal_code"))
        self.assertEqual(_counts(), before)

    def test_open_transaction_founding_failure_is_technical_without_writes(self):
        route = prepare_route()
        command = FoundLivingOrganismCommand(
            credential=route.founder.credential,
            name=f"SB-2 transaction witness {route.tag}",
            purpose="Verify the top-level-only founding service boundary.",
            request_reference=f"sb2:founding-transaction:{route.tag}",
            idempotency_key=f"sb2:founding-transaction:key:{route.tag}",
            accepted_terms_version_uuid=route.terms.terms_version_uuid,
        )
        before = _counts()
        with self.assertRaises(S015FormationTechnicalFailure):
            with transaction.atomic():
                route.formation_service.found_living_organism(command)
        self.assertEqual(_counts(), before)

    def test_reverse_refuses_a_snapshot_and_restores_the_empty_schema(self):
        """Exercise the SB-2 reverse guard without creating SE policy data."""
        executor = MigrationExecutor(connection)
        original_targets = tuple(executor.loader.graph.leaf_nodes())

        def restore_migrations():
            MigrationExecutor(connection).migrate(list(original_targets))

        self.addCleanup(restore_migrations)
        lower_targets = [
            node for node in original_targets
            if node
            not in {
                ("core", "0045_sbg_governance_formation"),
                ("core", "0046_se_entitlement_foundation"),
                ("core", "0047_sek1_platform_rule_statement_start"),
                ("core", "0048_se_entitlement_granted_event_guard"),
                ("core", "0049_h100_command_intake"),
            }
        ]
        lower_targets.append(("core", "0043_p1_governed_registration"))
        executor.migrate(lower_targets)

        migration = importlib.import_module("core.migrations.0044_sb2_birthing_snapshot")
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT p.prosrc FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = 'public' AND p.proname = %s",
                ["s3fu_guard_gateway_effect"],
            )
            self.assertEqual(cursor.fetchone()[0], migration.PREVIOUS_S3FU_GUARD_GATEWAY_EFFECT_BODY)
            cursor.execute(
                "SELECT p.prosrc FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = 'public' AND p.proname = %s",
                ["sb_guard_phase_event"],
            )
            self.assertEqual(cursor.fetchone()[0], migration.PREVIOUS_SB_GUARD_PHASE_EVENT_BODY)
            cursor.execute(
                "SELECT count(*) FROM pg_trigger t "
                "JOIN pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND NOT t.tgisinternal",
            )
            self.assertEqual(cursor.fetchone()[0], 429)
            cursor.execute(
                "SELECT count(*) FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = 'public' "
                "AND p.proname = 'sb_assign_lobirthingsnapshot_commitment'",
            )
            self.assertEqual(cursor.fetchone()[0], 0)

        MigrationExecutor(connection).migrate(list(original_targets))
        # H100 chunk 4 tests v1.4 (R-1): the snapshot needs only an organism. A route with no
        # gateway effect (FOUND and one phase advance, neither an admitted entry) leaves no
        # core_commandintake row, so the reverse passes 0049 (whose reverse refuses while intake
        # rows exist, and the table refuses DELETE and TRUNCATE) and reaches the 0044 guard.
        route = prepare_route(
            governance_enabled=None,
            include_settings=False,
            include_question=False,
            to_birthing_phase=False,
        )
        self.assertFalse(CommandIntake.objects.exists())
        snapshot_at = database_now()
        LOBirthingSnapshot.objects.create(
            living_organism=route.organism,
            body={"fixture": "SB-2 reverse-guard witness"},
            effective_at=snapshot_at,
            received_at=snapshot_at,
        )
        with self.assertRaisesMessage(
            RuntimeError,
            "SB-2 reverse refused while core_lobirthingsnapshot contains rows",
        ):
            MigrationExecutor(connection).migrate(lower_targets)

    def test_governance_effect_and_birthing_serialize_in_both_lock_orders(self):
        """Concurrent governance effects and Birthing remain serialized in both lock orders."""
        workers = []
        effect_entered, release_effect = Event(), Event()
        birth_entered, release_birth = Event(), Event()
        effect_entered_after_birth, release_effect_after_birth = Event(), Event()
        try:
            first = prepare_route(governance_enabled=False)
            first_draft = _governance_draft(first, True, "concurrent-before-birth")
            effect_service = OrganismApplicationService(
                authority=S015Authority(
                    provider=_PausedProductionProvider(effect_entered, release_effect),
                ),
                clock=database_now,
            )
            effect_thread, effect_results, effect_pids = _start_worker(
                lambda: effect_service.effect_gateway_version(
                    _governance_effect_command(first, first_draft.version_uuid, "concurrent-before-birth")
                )
            )
            workers.append(effect_thread)
            self.assertTrue(effect_entered.wait(10))
            effect_pid = effect_pids.get(timeout=10)
            birthing_thread, birthing_results, birthing_pids = _start_worker(
                lambda: birth(first, suffix="concurrent-after-effect")
            )
            workers.append(birthing_thread)
            birthing_pid = birthing_pids.get(timeout=10)
            with connection.cursor() as cursor:
                wait_until_blocked(cursor, birthing_pid, effect_pid, "core_identity")
            release_effect.set()
            _worker_result(effect_thread, effect_results)
            with self.assertRaises(S015AuthorityDenied) as first_refusal:
                _worker_result(birthing_thread, birthing_results)
            self.assertIs(
                first_refusal.exception.refusal_code,
                S015AuthorityRefusalCode.SBG_GOVERNANCE_CIRCLE_MISSING,
            )
            self.assertFalse(
                LivingOrganismEvent.objects.filter(
                    living_organism=first.organism,
                    action="BIRTHING",
                ).exists()
            )

            second = prepare_route(governance_enabled=False)
            ensure_test_policy_text()
            second_draft = _governance_draft(second, True, "concurrent-after-birth")
            birthing_service = OrganismBirthingService(
                authority=S015Authority(
                    provider=_PausedProductionProvider(birth_entered, release_birth),
                ),
                clock=database_now,
            )
            def call_second_birthing():
                with disclose_authority_refusal_code():
                    return birthing_service.birth_living_organism(BirthingCommand(
                        credential=second.founder.credential,
                        living_organism_uuid=second.organism.organism_id,
                        request_reference=f"sb2:concurrent-birth:{second.tag}",
                        idempotency_key=f"sb2:concurrent-birth:key:{second.tag}",
                        effective_at=database_now(),
                        offering_choice="FREE",
                        policy_version=TEST_POLICY_VERSION,
                        policy_text_sha256=TEST_POLICY_SHA256,
                    ))

            birth_thread, birth_results, birth_pids = _start_worker(call_second_birthing)
            workers.append(birth_thread)
            self.assertTrue(birth_entered.wait(10))
            birth_pid = birth_pids.get(timeout=10)
            effect_thread, effect_results, effect_pids = _start_worker(
                lambda: OrganismApplicationService(
                    authority=S015Authority(
                        provider=_PausedProductionProvider(
                            effect_entered_after_birth,
                            release_effect_after_birth,
                        ),
                    ),
                    clock=database_now,
                ).effect_gateway_version(
                    _governance_effect_command(second, second_draft.version_uuid, "concurrent-after-birth")
                )
            )
            workers.append(effect_thread)
            effect_pid = effect_pids.get(timeout=10)
            with connection.cursor() as cursor:
                wait_until_blocked(cursor, effect_pid, birth_pid, "core_identity")
            release_birth.set()
            _worker_result(birth_thread, birth_results)
            self.assertTrue(effect_entered_after_birth.wait(10))
            before_second_effect = _counts()
            release_effect_after_birth.set()
            with self.assertRaises(S015ApplicationRefused) as second_refusal:
                _worker_result(effect_thread, effect_results)
            self.assertIs(
                second_refusal.exception.refusal_code,
                S015AuthorityRefusalCode.SBG_GOVERNANCE_CIRCLE_MISSING,
            )
            self.assertEqual(
                str(second_refusal.exception),
                "GOVERNANCE was not switched on and nothing changed. GOVERNANCE is on for this "
                "LO, so it needs its GOVERNANCE Circle first. Create the LO's GOVERNANCE Circle, "
                "then try again. Creating the Circle early in formation avoids this.",
            )
            self.assertEqual(_counts(), before_second_effect)
            self.assertFalse(
                AuthorityDecision.objects.filter(
                    actor=second.founder,
                    request_reference=(
                        f"sb2:governance-effect:concurrent-after-birth:{second.tag}"
                    ),
                ).exists(),
            )
            self.assertTrue(
                LivingOrganismEvent.objects.filter(
                    living_organism=second.organism,
                    action="BIRTHING",
                ).exists()
            )
        finally:
            for event in (
                effect_entered,
                release_effect,
                birth_entered,
                release_birth,
                effect_entered_after_birth,
                release_effect_after_birth,
            ):
                event.set()
            _join_workers(workers)
