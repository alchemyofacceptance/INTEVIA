"""S3-FU Round 3 gateway, change and SQL-guard witnesses on disposable PostgreSQL."""

import importlib
import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from unittest import skipUnless
from uuid import uuid4

from django.db import IntegrityError, connection, transaction
from django.test import TransactionTestCase

from core.forms import LOApplicationQuestionDraftForm, LOApplicationSettingsDraftForm
from core.models import (
    Application, Identity, IdentityTransition, LivingOrganism, LivingOrganismEvent,
    LOApplicationQuestionVersion, LOApplicationSettingsVersion, LOGovernanceConfigurationVersion,
)
from core.organism_contract import OrganismAction
from src.intevia.services.organism_application_service import (
    EffectGatewayVersionCommand, FormationLONameSource, GatewayDraftCommand,
    OrganismApplicationService, S015ApplicationRefused, SubmitApplicationCommand, _validate_gateway_answer,
    default_application_question, gateway_version_status,
)
from src.intevia.services.organism_authority import (
    S015Authority, S015OwnApplicantAuthorityRequest, S015OwnApplicantAuthorityResponse,
    S015PostFoundingAuthorityResponse,
)
from src.intevia.services.organism_membership_service import S015OperationNotYetImplemented
from tests.s015_0021_support import T0, append_event, empty_fp, found_nonroot, fresh, refresh, seed_identity, seed_test_gateway, u
from tests.test_s015_0024_commitment_preimages import digest, l1_preimage, parts_preimage


NOW = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)
POSTGRES = skipUnless(connection.vendor == "postgresql", "S3 gateway requires PostgreSQL")


class _CoordinatorProvider:
    def evaluate_organism_authority(self, request):
        if isinstance(request, S015OwnApplicantAuthorityRequest):
            return S015OwnApplicantAuthorityResponse(**asdict(request), authority_reference="s3:test:applicant")
        return S015PostFoundingAuthorityResponse(**asdict(request), authority_reference="s3:test:gateway")


class _TestFixtureLONameSource:
    def __init__(self, fixture_name="TEST-FIXTURE-NAME: Riverside Commons"):
        self.fixture_name = fixture_name

    def name_for(self, living_organism):
        return self.fixture_name


@POSTGRES
class ApplicationGatewayTests(TransactionTestCase):
    def setUp(self):
        self.tag = f"s3-gateway-{fresh()}"
        with transaction.atomic(), connection.cursor() as cursor:
            self.ids = found_nonroot(cursor, tag=self.tag)
        self.actor = Identity.objects.select_related("credential").get(pk=self.ids["identity"])
        self.organism = LivingOrganism.objects.get(pk=self.ids["lo"])
        self.service = OrganismApplicationService(
            authority=S015Authority(provider=_CoordinatorProvider()),
            clock=lambda: NOW, name_source=_TestFixtureLONameSource(),
        )

    def draft(self, action, fields, suffix=None):
        token = suffix or fresh()
        return self.service.save_gateway_draft(GatewayDraftCommand(
            credential=self.actor.credential, living_organism_id=self.organism.organism_id,
            action=action, request_reference=f"s3:draft:{token}", idempotency_key=f"s3:draft:{token}",
            occurred_at=NOW, effective_at=NOW, received_at=NOW, fields=fields,
        ))

    def effect(self, version, action, suffix=None):
        token = suffix or fresh()
        return self.service.effect_gateway_version(EffectGatewayVersionCommand(
            credential=self.actor.credential, living_organism_id=self.organism.organism_id,
            action=action, version_uuid=version.version_uuid,
            request_reference=f"s3:effect:{token}", idempotency_key=f"s3:effect:{token}",
            occurred_at=NOW, effective_at=NOW, received_at=NOW,
        ))

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
            correlation_id=uuid4(), occurred_at=T0 + timedelta(hours=1), previous_transition=provision,
        )
        return applicant

    def submit(self, applicant, answer="A test-only applicant answer"):
        token = fresh()
        return self.service.submit_application(SubmitApplicationCommand(
            credential=applicant.credential, living_organism_id=self.organism.organism_id,
            request_reference=f"s3:submit:{token}", idempotency_key=f"s3:submit:{token}",
            occurred_at=NOW, effective_at=NOW, received_at=NOW, answer=answer,
        ))

    def test_name_dependency_default_draft_and_unselected_settings(self):
        with self.assertRaises(S015OperationNotYetImplemented) as refused:
            default_application_question(self.organism, FormationLONameSource())
        self.assertEqual(refused.exception.limit_id, "S3-IL-LONAME")
        with self.assertRaises(S015OperationNotYetImplemented):
            LOApplicationQuestionDraftForm(living_organism=self.organism)
        production = OrganismApplicationService(
            authority=S015Authority(provider=_CoordinatorProvider()), clock=lambda: NOW,
        )
        with self.assertRaises(S015OperationNotYetImplemented) as refused:
            production.save_gateway_draft(GatewayDraftCommand(
                credential=self.actor.credential, living_organism_id=self.organism.organism_id,
                action=OrganismAction.EFFECT_APPLICATION_QUESTION,
                request_reference=f"s3:name:{self.tag}", idempotency_key=f"s3:name:{self.tag}",
                occurred_at=NOW, effective_at=NOW, received_at=NOW, fields={},
            ))
        self.assertEqual(refused.exception.limit_id, "S3-IL-LONAME")
        form = LOApplicationQuestionDraftForm(
            living_organism=self.organism, name_source=_TestFixtureLONameSource(),
        )
        self.assertEqual(form.initial, {
            "question_text": "Why do you want to join TEST-FIXTURE-NAME: Riverside Commons?",
            "answer_format": "LONG_ANSWER",
        })
        question = self.draft(OrganismAction.EFFECT_APPLICATION_QUESTION, {})
        self.assertEqual(question.question_text, form.initial["question_text"])
        self.assertEqual(question.answer_format, "LONG_ANSWER")
        settings_form = LOApplicationSettingsDraftForm()
        for field in ("applications_open", "automatic_acceptance"):
            self.assertNotIn(field, settings_form.initial)
            self.assertIsNone(settings_form.fields[field].initial)
            self.assertTrue(settings_form.fields[field].required)
        self.assertFalse(LOApplicationSettingsDraftForm(data={}).is_valid())

    def test_draft_effect_supersede_and_immutable_answer_text(self):
        action = OrganismAction.EFFECT_APPLICATION_QUESTION
        first = self.draft(action, {})
        self.assertEqual(gateway_version_status("default", first, datetime.now(timezone.utc)), "DRAFT")
        before = self.organism.state
        first_event = self.effect(first, action)
        self.assertEqual(first_event.evidence_reference, f"application-question-version:{first.version_uuid}")
        self.assertEqual(first_event.prior_state, first_event.resulting_state)
        self.assertEqual(gateway_version_status("default", first, datetime.now(timezone.utc)), "EFFECTIVE")
        settings = self.draft(OrganismAction.EFFECT_APPLICATION_SETTINGS, {
            "applications_open": True, "automatic_acceptance": False, "processing_responsibility": "LO",
        })
        self.effect(settings, OrganismAction.EFFECT_APPLICATION_SETTINGS)
        application = Application.objects.get(pk=self.submit(self.applicant()).application_id)
        with self.assertRaisesMessage(IntegrityError, "effective and immutable"):
            with transaction.atomic():
                LOApplicationQuestionVersion.objects.filter(pk=first.pk).update(question_text="silent rename")
        self.service._name_source = _TestFixtureLONameSource("TEST-FIXTURE-NAME: New Riverside")
        second = self.draft(action, {})
        self.assertEqual(second.supersedes_id, first.pk)
        self.assertEqual(first.question_text, "Why do you want to join TEST-FIXTURE-NAME: Riverside Commons?")
        self.assertIn("New Riverside", second.question_text)
        self.assertEqual(gateway_version_status("default", first, datetime.now(timezone.utc)), "EFFECTIVE")
        self.effect(second, action)
        self.assertEqual(gateway_version_status("default", first, datetime.now(timezone.utc)), "SUPERSEDED")
        self.organism.refresh_from_db()
        self.assertEqual(self.organism.state, before)
        self.assertEqual(LOApplicationQuestionVersion.objects.get(pk=first.pk).question_text, first.question_text)
        application.refresh_from_db()
        self.assertEqual(application.question_version_id, first.pk)
        self.assertEqual(application.question_version.question_text, first.question_text)

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
            occurred_at=NOW, effective_at=NOW, received_at=NOW, fields={"options": ["A", "B"]},
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
            occurred_at=NOW, effective_at=NOW, received_at=NOW,
            fields={"inherit_portal_cq_settings": False, "require_cq_to_change_lo_settings": False,
                    "require_cq_to_change_application_question": False},
        ))
        configuration.refresh_from_db()
        self.effect(configuration, config_action)

    def test_effect_guard_attributes_evidence_organism_continuity_and_duplicate(self):
        with transaction.atomic(), connection.cursor() as cursor:
            gateway = seed_test_gateway(cursor, self.ids)
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
        with transaction.atomic(), connection.cursor() as cursor:
            other = found_nonroot(cursor, tag=f"other-{fresh()}")
        other_version = LOApplicationSettingsVersion.objects.create(
            living_organism_id=other["lo"], actor=self.actor, enterer=self.actor,
            occurred_at=NOW, effective_at=NOW, received_at=NOW,
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
        self.assertEqual(gateway_version_status("default", valid, datetime.now(timezone.utc)), "EFFECTIVE")

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
                    configuration = LOGovernanceConfigurationVersion.objects.create(
                        living_organism=self.organism, actor=self.actor, enterer=self.actor,
                        occurred_at=NOW, effective_at=NOW, received_at=NOW,
                        cq_active=True, inherit_portal_cq_settings=inherit,
                        require_cq_to_change_lo_settings=settings_required,
                        require_cq_to_change_application_question=question_required,
                    )
                    self.direct_effect(OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
                                       f"governance-configuration-version:{configuration.version_uuid}")
                    with connection.cursor() as cursor:
                        refresh(cursor, "core_livingorganism", self.organism.pk, NOW)
                    with self.assertRaises(S015OperationNotYetImplemented) as refused:
                        self.draft(action, {})
                    self.assertEqual(refused.exception.limit_id, limit)
                    raise Rollback()

    def test_four_closed_states_and_version_evidence_survives_a_change(self):
        class Rollback(Exception):
            pass

        applicant = self.applicant()
        with self.assertRaisesMessage(S015ApplicationRefused, "S3FU gateway closed"):
            self.submit(applicant)
        for mode in ("no_settings", "no_question", "closed_setting"):
            with self.subTest(mode=mode), self.assertRaises(Rollback):
                with transaction.atomic():
                    if mode != "no_settings":
                        settings = self.draft(OrganismAction.EFFECT_APPLICATION_SETTINGS, {
                            "applications_open": mode != "closed_setting", "automatic_acceptance": False,
                            "processing_responsibility": "LO",
                        })
                        self.effect(settings, OrganismAction.EFFECT_APPLICATION_SETTINGS)
                    if mode != "no_question":
                        question = self.draft(OrganismAction.EFFECT_APPLICATION_QUESTION, {})
                        self.effect(question, OrganismAction.EFFECT_APPLICATION_QUESTION)
                    with self.assertRaisesMessage(S015ApplicationRefused, "S3FU gateway closed"):
                        self.submit(applicant)
                    raise Rollback()
        with transaction.atomic(), connection.cursor() as cursor:
            gateway = seed_test_gateway(cursor, self.ids)
        result = self.submit(applicant)
        application = Application.objects.get(pk=result.application_id)
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
        with self.assertRaisesMessage(S015ApplicationRefused, "S3FU gateway closed"):
            self.submit(self.applicant())

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
                application = Application.objects.get(pk=result.application_id)
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
                (membership_uuid, T0, T0, empty_fp(cursor, "core_organismmembership", membership_uuid),
                 other_pk, self.organism.pk),
            )
            membership_id = cursor.fetchone()[0]
            append_event(cursor, "core_organismmembershiptransition", membership_id, 1, "JOIN", None, "ACTIVE",
                         other_pk, self.ids["basis"], fresh(), extra={"reason_class": "FOUNDING"})
            refresh(cursor, "core_organismmembership", membership_id)
        other = Identity.objects.select_related("credential").get(pk=other_pk)
        with self.assertRaisesMessage(S015ApplicationRefused, "effective Coordinator office is required"):
            self.service.save_gateway_draft(GatewayDraftCommand(
                credential=other.credential, living_organism_id=self.organism.organism_id,
                action=OrganismAction.EFFECT_APPLICATION_SETTINGS,
                request_reference=f"s3:noncoord:{self.tag}", idempotency_key=f"s3:noncoord:{self.tag}",
                occurred_at=NOW, effective_at=NOW, received_at=NOW, fields={},
            ))

    def test_closed_lo_has_no_capacity_to_effect_a_gateway_version(self):
        settings = self.draft(OrganismAction.EFFECT_APPLICATION_SETTINGS, {
            "applications_open": True, "automatic_acceptance": False, "processing_responsibility": "LO",
        })
        with transaction.atomic(), connection.cursor() as cursor:
            append_event(cursor, "core_livingorganismevent", self.organism.pk, 2, "CLOSE", "ACTIVE", "CLOSED",
                         self.actor.pk, self.ids["basis"], fresh(),
                         pred_id=self.ids["events"]["core_livingorganismevent"], eff=NOW, occ=NOW, rec=NOW)
            refresh(cursor, "core_livingorganism", self.organism.pk, NOW)
        with self.assertRaisesMessage(IntegrityError, "S3FU LO capacity refused: dissolved"):
            self.service.effect_gateway_version(EffectGatewayVersionCommand(
                credential=self.actor.credential, living_organism_id=self.organism.organism_id,
                action=OrganismAction.EFFECT_APPLICATION_SETTINGS, version_uuid=settings.version_uuid,
                request_reference=f"s3:closed:{self.tag}", idempotency_key=f"s3:closed:{self.tag}",
                occurred_at=NOW + timedelta(seconds=1), effective_at=NOW + timedelta(seconds=1),
                received_at=NOW + timedelta(seconds=1),
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
                           "WHERE id = %s", [event.pk])
            row, l1, parts = cursor.fetchone()
        row = json.loads(row) if isinstance(row, str) else row
        self.assertEqual(l1, digest("s015r1", l1_preimage("core_livingorganismevent", inventory, row)))
        self.assertEqual(parts, digest("s015p1", parts_preimage("core_livingorganismevent", event.event_uuid, [])))