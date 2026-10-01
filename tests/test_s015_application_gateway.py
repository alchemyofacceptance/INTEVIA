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
    Application, ApplicationDecision, ApplicationEngagement, ApplicationLineageEvent, Identity,
    IdentityTransition, LivingOrganism, LivingOrganismEvent, OrganismMembership,
    OrganismMembershipTransition,
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

    def automatic_gateway(self):
        with transaction.atomic(), connection.cursor() as cursor:
            seed_test_gateway(cursor, self.ids)
        settings = self.draft(OrganismAction.EFFECT_APPLICATION_SETTINGS, {
            "applications_open": True, "automatic_acceptance": True, "processing_responsibility": "LO",
        })
        self.effect(settings, OrganismAction.EFFECT_APPLICATION_SETTINGS)
        return settings

    def automatic_service(self):
        with transaction.atomic(), connection.cursor() as cursor:
            care_pk = seed_identity(cursor, f"fake-care-{fresh()}")
        care = Identity.objects.select_related("credential").get(pk=care_pk)
        welcome = _TestDiscussionWelcome()
        self.service = OrganismApplicationService(
            authority=S015Authority(provider=_CoordinatorProvider()), clock=lambda: NOW,
            name_source=_TestFixtureLONameSource(), care_executor=FakeCareExecutor(care), welcome=welcome,
        )
        return care, welcome

    def test_automatic_acceptance_has_separate_applicant_and_composed_lo_acts(self):
        settings = self.automatic_gateway()
        care, welcome = self.automatic_service()
        applicant = self.applicant()
        result = self.submit(applicant)
        application = Application.objects.get(pk=result.application_id)
        decision = ApplicationDecision.objects.get(application=application)
        engagement = ApplicationEngagement.objects.get(application=application)
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
        self.service = OrganismApplicationService(
            authority=S015Authority(provider=_CoordinatorProvider()), clock=lambda: NOW,
            name_source=_TestFixtureLONameSource(), care_executor=FakeCareExecutor(care),
        )
        applicant = self.applicant()
        with self.assertRaises(S015OperationNotYetImplemented) as refused:
            self.submit(applicant)
        self.assertEqual(refused.exception.limit_id, "S3-IL-NOTICE")
        self.assertFalse(Application.objects.filter(applicant=applicant, living_organism=self.organism).exists())
        self.assertFalse(OrganismMembership.objects.filter(identity=applicant, living_organism=self.organism).exists())

    def test_automatic_closed_gateway_refuses_without_admission(self):
        self.automatic_gateway()
        self.automatic_service()
        applicant = self.applicant()
        with transaction.atomic(), connection.cursor() as cursor:
            self.organism.refresh_from_db()
            append_event(cursor, "core_livingorganismevent", self.organism.pk,
                         self.organism.head_event.sequence + 1, "CLOSE", "ACTIVE", "CLOSED",
                         self.actor.pk, self.ids["basis"], fresh(), pred_id=self.organism.head_event_id,
                         eff=NOW, occ=NOW, rec=NOW)
            refresh(cursor, "core_livingorganism", self.organism.pk, NOW)
        with self.assertRaisesMessage(S015ApplicationRefused, "S3FU gateway closed"):
            self.submit(applicant)
        self.assertFalse(Application.objects.filter(applicant=applicant, living_organism=self.organism).exists())

    def test_automatic_off_remains_pending(self):
        with transaction.atomic(), connection.cursor() as cursor:
            seed_test_gateway(cursor, self.ids)
        applicant = self.applicant()
        result = self.submit(applicant)
        self.assertFalse(ApplicationDecision.objects.filter(application_id=result.application_id).exists())
        self.assertEqual(ApplicationLineageEvent.objects.filter(
            application_lineage__application_id=result.application_id,
        ).count(), 1)

    def test_automatic_route_vocabulary_and_off_version_sql_refusal(self):
        self.assertEqual(
            {value for value, _ in ApplicationEngagement.Route.choices},
            {"LO_CQ", "LO_CSA", "LO_AUTOMATIC", "CIRCLE_CQ", "CIRCLE_CSA"},
        )
        with transaction.atomic(), connection.cursor() as cursor:
            seeded = seed_test_gateway(cursor, self.ids)
        applicant = self.applicant()
        application = Application.objects.get(pk=self.submit(applicant).application_id)
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
            later = LOApplicationSettingsVersion.objects.create(
                living_organism=self.organism, actor=self.actor, enterer=self.actor,
                supersedes=setting, occurred_at=NOW + timedelta(seconds=1),
                effective_at=NOW + timedelta(seconds=1), received_at=NOW + timedelta(seconds=1),
                applications_open=False, automatic_acceptance=False, processing_responsibility="LO",
            )
            with connection.cursor() as cursor:
                self.organism.refresh_from_db()
                predecessor = self.organism.head_event
                append_event(
                    cursor, "core_livingorganismevent", self.organism.pk, predecessor.sequence + 1,
                    "EFFECT_APPLICATION_SETTINGS", predecessor.resulting_state, predecessor.resulting_state,
                    None, self.ids["basis"], fresh(), pred_id=predecessor.pk,
                    eff=NOW + timedelta(seconds=1), occ=NOW + timedelta(seconds=1),
                    rec=NOW + timedelta(seconds=1),
                    extra={"actor_state": "LIVING_ORGANISM", "actor_access_epoch": None,
                           "enterer_identity_id": self.actor.pk,
                           "evidence_reference": f"application-settings-version:{later.version_uuid}"},
                )
                refresh(cursor, "core_livingorganism", self.organism.pk, NOW + timedelta(seconds=1))
            accepted = accept(application, care, request_reference, idempotency_key, fingerprint)
            self.assertEqual(gateway_version_status("default", later, datetime.now(timezone.utc)), "EFFECTIVE")
            return accepted

        self.service._automatic_accept = change_then_accept
        result = self.submit(applicant)
        application = Application.objects.get(pk=result.application_id)
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
        with self.assertRaises(Rollback):
            with transaction.atomic():
                # Test-only incomplete automatic act; the outer transaction is always rolled back.
                self.service._automatic_accept = lambda *args: None
                result = self.submit(applicant)
                application = Application.objects.get(pk=result.application_id)
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

    def test_cq_active_without_governing_flags_allows_coordinator_changes(self):
        # TEST FIXTURE — installs a configuration whose production creation route is unavailable (S3-IL-CQ);
        # establishes behaviour under it, not delivery of that route.
        with transaction.atomic(), connection.cursor() as cursor:
            self.draft(OrganismAction.EFFECT_APPLICATION_SETTINGS, {})
            configuration = LOGovernanceConfigurationVersion.objects.create(
                living_organism=self.organism, actor=self.actor, enterer=self.actor,
                occurred_at=NOW, effective_at=NOW, received_at=NOW,
                cq_active=True, inherit_portal_cq_settings=False,
                require_cq_to_change_lo_settings=False,
                require_cq_to_change_application_question=False,
            )
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
                self.assertEqual(gateway_version_status("default", version, datetime.now(timezone.utc)), "EFFECTIVE")
        with transaction.atomic(), connection.cursor() as cursor:
            other_pk = seed_identity(cursor, f"cq-other-{fresh()}")
            membership_uuid = u(f"cq-non-coordinator:{fresh()}")
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
        for action in (OrganismAction.EFFECT_APPLICATION_QUESTION, OrganismAction.EFFECT_APPLICATION_SETTINGS):
            with self.subTest(non_coordinator=action), self.assertRaisesMessage(
                S015ApplicationRefused, "effective Coordinator office is required",
            ):
                self.service.save_gateway_draft(GatewayDraftCommand(
                    credential=other.credential, living_organism_id=self.organism.organism_id,
                    action=action, request_reference=f"s4:noncoord:{action.value}",
                    idempotency_key=f"s4:noncoord:{action.value}",
                    occurred_at=NOW, effective_at=NOW, received_at=NOW, fields={},
                ))

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