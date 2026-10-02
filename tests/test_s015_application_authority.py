"""PKT-B R1 production-provider witnesses; the Human Governor runs PostgreSQL tests.

Write-order audit, command by command: (1) SUBMIT via submit_application;
(2) WITHDRAW via withdraw_application; (3) CLAIM via claim_application;
(4) REASSIGN via reassign_application; (5) DECIDE via decide_application;
(6) EFFECT_APPLICATION_SETTINGS; (7) EFFECT_APPLICATION_QUESTION; and
(8) EFFECT_GOVERNANCE_CONFIGURATION. For commands 1-5, their service entry
points acquire locks and read request context before qualify(); no receipt,
application, engagement, assignment, terminal, or lineage write precedes it.
For commands 6-8, both save_gateway_draft and effect_gateway_version acquire
locks and read the version context before qualify(); no version or LO-chain
event write precedes it. In all eight commands, the first durable row or
governed-chain write follows successful qualification. Locks are
transaction-scoped, including the advisory command mutex.
"""

import hashlib
import importlib
import json
import os
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from unittest import skipUnless
from uuid import uuid4

from django.db import DatabaseError, connection, transaction
from django.test import TransactionTestCase

from core.models import (
    Application,
    ApplicationEngagement,
    AuthorityDecision,
    CSAAssignment,
    ContextualRoleAssignment,
    Identity,
    IdentityTransition,
    LivingOrganism,
    LOGovernanceConfigurationVersion,
    LOApplicationQuestionVersion,
    LOApplicationSettingsVersion,
    OrganismMembership,
)
from core.organism_contract import OrganismAction
from src.intevia.services.organism_application_authority import (
    ProductionApplicationAuthorityProvider,
)
from src.intevia.services.organism_application_service import (
    S015ApplicationAuthorityUnavailable,
    ClaimApplicationCommand,
    DecideApplicationCommand,
    EffectGatewayVersionCommand,
    GatewayDraftCommand,
    OrganismApplicationService,
    ReassignApplicationCommand,
    SubmitApplicationCommand,
    WithdrawApplicationCommand,
)
from src.intevia.services.organism_authority import (
    S015Authority,
    S015AuthorityDenied,
    S015OwnApplicantAuthorityRefusal,
    S015OwnApplicantAuthorityRequest,
    S015PostFoundingAuthorityRequest,
    S015AuthorityRefusalCode,
    authority_target_fingerprint,
    canonical_json_bytes,
)
from tests.s015_0034_support import emit_guard_catalogue
from tests.s015_0021_support import (
    T0,
    append_event,
    empty_fp,
    found_nonroot,
    fresh,
    refresh,
    seed_identity,
    seed_test_gateway,
)


POSTGRES_ONLY = skipUnless(connection.vendor == "postgresql", "R1 production authority requires PostgreSQL")


class TestFixtureBirthingFactSource:
    """TEST FIXTURE ONLY: supplies a labelled reference without writing a governed chain row."""

    def __init__(self, living_organism_id, fixture_reference="TEST_FIXTURE:BIRTHING"):
        self.living_organism_id = living_organism_id
        self.fixture_reference = fixture_reference

    def reference_at(self, living_organism_id, effective_at, evaluated_at):
        if (
            living_organism_id != self.living_organism_id
            or type(effective_at) is not datetime
            or type(evaluated_at) is not datetime
        ):
            return None
        return self.fixture_reference


class TestFixtureGovernedRegistrationFactSource:
    """TEST FIXTURE ONLY: maps a labelled ACTIVATE row to the absent registration fixture contract."""

    def reference_at(self, identity_id, effective_at, evaluated_at):
        transitions = IdentityTransition.objects.filter(
            identity__identity_id=identity_id,
            occurred_at__lte=evaluated_at,
        )
        latest = transitions.order_by("-occurred_at", "-pk").first()
        if latest is None or latest.resulting_state != Identity.AccessState.ACTIVE:
            return None
        fixture = transitions.filter(
            action=IdentityTransition.Action.ACTIVATE,
            authority_reference="TEST_FIXTURE:REGISTRATION",
            occurred_at__lte=effective_at,
        ).order_by("-occurred_at", "-pk").first()
        return None if fixture is None else f"identity-transition:{fixture.pk}"


@POSTGRES_ONLY
class ProductionApplicationAuthorityTests(TransactionTestCase):
    def setUp(self):
        self.tag = f"aa-r1-authority-{fresh()}"
        with transaction.atomic(), connection.cursor() as cursor:
            self.ids = found_nonroot(cursor, tag=self.tag)
            seed_test_gateway(cursor, self.ids, include_governance_configuration=True)
            applicant_pk = seed_identity(cursor, f"applicant-{self.tag}")
            cursor.execute(
                "SELECT organism_id FROM core_livingorganism WHERE id = %s",
                [self.ids["lo"]],
            )
            self.organism_uuid = cursor.fetchone()[0]
        self.applicant = Identity.objects.select_related("credential").get(pk=applicant_pk)
        provision = IdentityTransition.objects.create(
            identity=self.applicant,
            action=IdentityTransition.Action.PROVISION,
            prior_state=None,
            resulting_state=Identity.AccessState.PENDING,
            authority_reference="TEST_FIXTURE:PROVISION",
            technical_executor="TEST_FIXTURE",
            evidence_reference="TEST_FIXTURE:IDENTITY_HISTORY",
            reason_category=IdentityTransition.ReasonCategory.PROVISIONING,
            correlation_id=uuid4(),
            occurred_at=T0,
        )
        IdentityTransition.objects.create(
            identity=self.applicant,
            action=IdentityTransition.Action.ACTIVATE,
            prior_state=Identity.AccessState.PENDING,
            resulting_state=Identity.AccessState.ACTIVE,
            authority_reference="TEST_FIXTURE:REGISTRATION",
            technical_executor="TEST_FIXTURE",
            evidence_reference="TEST_FIXTURE:IDENTITY_HISTORY",
            reason_category=IdentityTransition.ReasonCategory.ACCESS_REVIEW,
            correlation_id=uuid4(),
            occurred_at=T0 + timedelta(hours=1),
            previous_transition=provision,
        )
        self._capture_database_now()

    def _capture_database_now(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT clock_timestamp()")
            self.now = cursor.fetchone()[0]

    def _service(self, *, birthing_source=None, registration_source=None):
        self._capture_database_now()
        provider = ProductionApplicationAuthorityProvider(
            birthing_source=birthing_source,
            registration_source=registration_source,
        )
        return OrganismApplicationService(
            authority=S015Authority(provider=provider),
            clock=lambda: self.now,
        )

    def _submit(self, service, suffix, applicant=None):
        self._capture_database_now()
        applicant = self.applicant if applicant is None else applicant
        return service.submit_application(SubmitApplicationCommand(
            credential=applicant.credential,
            living_organism_id=self.organism_uuid,
            request_reference=f"aa-r1:submit:{suffix}:{self.tag}",
            idempotency_key=f"aa-r1:submit:key:{suffix}:{self.tag}",
            occurred_at=self.now,
            effective_at=self.now,
            received_at=self.now,
            answer="TEST FIXTURE: application answer",
        ))

    def _own_request(self, actor, action, target, *, application_pk=None, suffix="direct"):
        self._capture_database_now()
        return S015OwnApplicantAuthorityRequest(
            database_alias="default",
            actor_pk=actor.pk,
            actor_identity_id=actor.identity_id,
            actor_credential_pk=actor.credential_id,
            actor_access_epoch=actor.access_epoch,
            action=action,
            command_subtype=action.value,
            target_fingerprint=authority_target_fingerprint(target),
            request_reference=f"aa-r1:{suffix}:{self.tag}",
            idempotency_key=f"aa-r1:{suffix}:key:{self.tag}",
            evaluated_at=self.now,
            living_organism_pk=self.ids["lo"],
            application_pk=application_pk,
            effective_at=self.now,
            target=target,
            living_organism_id=self.organism_uuid,
        )

    def _post_request(
        self, actor, action, target, *, organism_uuid=None, effective_at=None, evaluated_at=None, suffix="direct",
    ):
        self._capture_database_now()
        organism_uuid = self.organism_uuid if organism_uuid is None else organism_uuid
        effective_at = self.now if effective_at is None else effective_at
        evaluated_at = self.now if evaluated_at is None else evaluated_at
        organism = LivingOrganism.objects.get(organism_id=organism_uuid)
        membership = OrganismMembership.objects.filter(identity=actor, living_organism=organism).first()
        membership_head = "TEST_FIXTURE:MEMBERSHIP_HEAD"
        role_heads = ()
        if membership is not None and membership.head_transition_id is not None:
            membership_head = membership.head_transition.lineage_reference
            role_heads = tuple(ContextualRoleAssignment.objects.filter(
                membership=membership,
                state="ACTIVE",
                head_state_event__isnull=False,
            ).order_by("pk").values_list("head_state_event__lineage_reference", flat=True))
        return S015PostFoundingAuthorityRequest(
            database_alias="default",
            actor_pk=actor.pk,
            actor_identity_id=actor.identity_id,
            actor_access_epoch=actor.access_epoch,
            action=action,
            command_subtype=action.value,
            target_fingerprint=authority_target_fingerprint(target),
            request_reference=f"aa-r1:{suffix}:{self.tag}",
            idempotency_key=f"aa-r1:{suffix}:key:{self.tag}",
            evaluated_at=evaluated_at,
            active_membership_head_reference=membership_head,
            contextual_role_head_references=role_heads,
            effective_at=effective_at,
            target=target,
            living_organism_id=organism_uuid,
        )

    def _add_second_coordinator(self, suffix, *, include_office=True):
        with transaction.atomic(), connection.cursor() as cursor:
            identity_pk = seed_identity(cursor, f"coordinator-{suffix}-{self.tag}")
            membership_uuid = uuid4()
            cursor.execute(
                "INSERT INTO core_organismmembership "
                "(membership_uuid, state, created_at, current_state_effective_at, state_known_at, "
                "state_source_event_set_fingerprint, identity_id, living_organism_id) "
                "VALUES (%s, 'ACTIVE', now(), %s, %s, %s, %s, %s) RETURNING id",
                [membership_uuid, T0, T0, empty_fp(cursor, "core_organismmembership", membership_uuid),
                 identity_pk, self.ids["lo"]],
            )
            membership_pk = cursor.fetchone()[0]
            append_event(
                cursor, "core_organismmembershiptransition", membership_pk, 1, "JOIN", None,
                "ACTIVE", identity_pk, self.ids["basis"], f"{self.tag}:{suffix}:membership",
                extra={"reason_class": "TEST_FIXTURE"},
            )
            refresh(cursor, "core_organismmembership", membership_pk)
            if include_office:
                assignment_uuid = uuid4()
                cursor.execute(
                    "INSERT INTO core_contextualroleassignment "
                    "(assignment_uuid, state, created_at, current_state_effective_at, state_known_at, "
                    "state_source_event_set_fingerprint, membership_id, role_definition_id) "
                    "VALUES (%s, 'ACTIVE', now(), %s, %s, %s, %s, %s) RETURNING id",
                    [assignment_uuid, T0, T0,
                     empty_fp(cursor, "core_contextualroleassignment", assignment_uuid),
                     membership_pk, self.ids["roles"]["COORDINATOR"]],
                )
                assignment_pk = cursor.fetchone()[0]
                append_event(
                    cursor, "core_contextualroleassignmentstateevent", assignment_pk, 1, "ASSIGN", None,
                    "ACTIVE", identity_pk, self.ids["basis"], f"{self.tag}:{suffix}:office",
                )
                refresh(cursor, "core_contextualroleassignment", assignment_pk)
            self._capture_database_now()
        return Identity.objects.select_related("credential").get(pk=identity_pk)

    def _add_fixture_access_history(self, identity, suffix):
        provision = IdentityTransition.objects.create(
            identity=identity,
            action=IdentityTransition.Action.PROVISION,
            prior_state=None,
            resulting_state=Identity.AccessState.PENDING,
            authority_reference=f"TEST_FIXTURE:PROVISION:{suffix}",
            technical_executor="TEST_FIXTURE",
            evidence_reference="TEST_FIXTURE:IDENTITY_HISTORY",
            reason_category=IdentityTransition.ReasonCategory.PROVISIONING,
            correlation_id=uuid4(),
            occurred_at=T0,
        )
        activated = IdentityTransition.objects.create(
            identity=identity,
            action=IdentityTransition.Action.ACTIVATE,
            prior_state=Identity.AccessState.PENDING,
            resulting_state=Identity.AccessState.ACTIVE,
            authority_reference="TEST_FIXTURE:REGISTRATION",
            technical_executor="TEST_FIXTURE",
            evidence_reference="TEST_FIXTURE:IDENTITY_HISTORY",
            reason_category=IdentityTransition.ReasonCategory.ACCESS_REVIEW,
            correlation_id=uuid4(),
            occurred_at=T0 + timedelta(hours=1),
            previous_transition=provision,
        )
        self._capture_database_now()
        return activated

    def _end_fixture_membership(self, identity, suffix, at):
        membership = OrganismMembership.objects.get(identity=identity, living_organism_id=self.ids["lo"])
        previous = membership.head_transition
        with transaction.atomic(), connection.cursor() as cursor:
            ended_id, _ = append_event(
                cursor,
                "core_organismmembershiptransition",
                membership.pk,
                previous.sequence + 1,
                "TRANSITION_MEMBERSHIP",
                "ACTIVE",
                "ENDED",
                identity.pk,
                self.ids["basis"],
                f"{self.tag}:{suffix}",
                pred_id=previous.pk,
                eff=at,
                occ=T0,
                rec=T0,
                extra={"reason_class": "TEST_FIXTURE"},
            )
            refresh(cursor, "core_organismmembership", membership.pk, self.now)
        self._capture_database_now()
        return ended_id

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
                cursor.execute(f'SELECT count(*) FROM public."{table}"')
                counts[table] = cursor.fetchone()[0]
        return counts

    def _qualified_submission_record(self, suffix):
        service = self._service(
            birthing_source=TestFixtureBirthingFactSource(self.organism_uuid),
            registration_source=TestFixtureGovernedRegistrationFactSource(),
        )
        result = self._submit(service, suffix)
        return AuthorityDecision.objects.get(decision_reference=result.authority_decision_reference)

    @staticmethod
    def _decision_copy_fields(record):
        return {
            "decision_reference": "s015d1:" + ("0" * 64),
            "decision_payload": record.decision_payload,
            "decision_payload_bytes": bytes(record.decision_payload_bytes),
            "request_kind": record.request_kind,
            "action": record.action,
            "command_subtype": record.command_subtype,
            "actor_identity_id": record.actor_identity_id,
            "actor_access_epoch": record.actor_access_epoch,
            "actor_credential_pk": record.actor_credential_pk,
            "actor_id": record.actor_id,
            "target": record.target,
            "target_bytes": bytes(record.target_bytes),
            "target_fingerprint": record.target_fingerprint,
            "effective_at": record.effective_at,
            "evaluated_at": record.evaluated_at,
            "authority_reference": record.authority_reference,
            "facts_read": record.facts_read,
            "outcome": record.outcome,
            "refusal_code": record.refusal_code,
            "provider_identity": record.provider_identity,
            "provider_version": record.provider_version,
            "request_reference": record.request_reference + ":copy",
            "idempotency_key": record.idempotency_key + ":copy",
            "recorded_at": None,
        }

    def test_production_sources_fail_closed_without_governed_birthing(self):
        service = self._service(registration_source=TestFixtureGovernedRegistrationFactSource())
        before = self._public_row_counts()
        with self.assertRaises(S015AuthorityDenied) as denied:
            self._submit(service, "no-birthing")
        after = self._public_row_counts()
        changed = {
            table: after[table] - before[table]
            for table in before
            if after[table] != before[table]
        }
        self.assertEqual(changed, {"core_authoritydecision": 1})
        self.assertEqual(denied.exception.disclosed_refusal_code, S015AuthorityRefusalCode.C_GATEWAY_NOT_OPEN)
        record = AuthorityDecision.objects.get(
            decision_reference=denied.exception.authority_decision_reference,
        )
        self.assertEqual((record.outcome, record.refusal_code), ("REFUSED", "C_GATEWAY_NOT_OPEN"))

    def test_production_registration_source_fails_closed(self):
        service = self._service(birthing_source=TestFixtureBirthingFactSource(self.organism_uuid))
        with self.assertRaises(S015AuthorityDenied) as denied:
            self._submit(service, "no-registration")
        self.assertEqual(denied.exception.refusal_code, S015AuthorityRefusalCode.E_NOT_REGISTERED)
        record = AuthorityDecision.objects.get(
            decision_reference=denied.exception.authority_decision_reference,
        )
        self.assertEqual(record.refusal_code, "E_NOT_REGISTERED")

    def test_absent_governance_configuration_refuses_at_submission_and_coordinator_route(self):
        with transaction.atomic(), connection.cursor() as cursor:
            ids = found_nonroot(cursor, tag=f"no-configuration-{self.tag}")
            gateway = seed_test_gateway(cursor, ids)
            cursor.execute("SELECT organism_id FROM core_livingorganism WHERE id = %s", [ids["lo"]])
            organism_uuid = cursor.fetchone()[0]
            applicant_pk = seed_identity(cursor, f"no-configuration-applicant-{self.tag}")
        applicant = Identity.objects.get(pk=applicant_pk)
        self._add_fixture_access_history(applicant, "no-configuration")
        service = self._service(
            birthing_source=TestFixtureBirthingFactSource(organism_uuid),
            registration_source=TestFixtureGovernedRegistrationFactSource(),
        )
        with self.assertRaises(S015AuthorityDenied) as submission_denied:
            self._capture_database_now()
            service.submit_application(SubmitApplicationCommand(
                credential=applicant.credential,
                living_organism_id=organism_uuid,
                request_reference=f"aa-r1:no-configuration-submit:{self.tag}",
                idempotency_key=f"aa-r1:no-configuration-submit:key:{self.tag}",
                occurred_at=self.now,
                effective_at=self.now,
                received_at=self.now,
                answer="TEST FIXTURE: no-configuration application",
            ))
        self.assertEqual(
            submission_denied.exception.refusal_code,
            S015AuthorityRefusalCode.C_CONFIGURATION_NOT_ESTABLISHED,
        )
        self.assertEqual(
            submission_denied.exception.disclosed_refusal_code,
            S015AuthorityRefusalCode.C_GATEWAY_NOT_OPEN,
        )
        submission_decision = AuthorityDecision.objects.get(
            decision_reference=submission_denied.exception.authority_decision_reference,
        )
        self.assertEqual(
            (submission_decision.outcome, submission_decision.refusal_code),
            ("REFUSED", S015AuthorityRefusalCode.C_CONFIGURATION_NOT_ESTABLISHED.value),
        )

        coordinator = Identity.objects.select_related("credential").get(pk=ids["identity"])
        settings = LOApplicationSettingsVersion.objects.get(pk=gateway["core_loapplicationsettingsversion"])
        question = LOApplicationQuestionVersion.objects.get(pk=gateway["core_loapplicationquestionversion"])
        application = Application.objects.create(
            applicant=applicant,
            living_organism_id=ids["lo"],
            actor=applicant,
            enterer=applicant,
            authority_decision_reference="s015d1:" + "a" * 64,
            authority_reference="TEST_FIXTURE:W7_COORDINATOR_APPLICATION",
            actor_access_epoch=applicant.access_epoch,
            settings_version=settings,
            question_version=question,
            answer="TEST FIXTURE: coordinator route only",
            occurred_at=self.now,
            effective_at=self.now,
            received_at=self.now,
        )
        target = {
            "action": OrganismAction.CLAIM_APPLICATION.value,
            "organism_uuid": organism_uuid,
            "application_uuid": application.application_uuid,
        }
        provider = ProductionApplicationAuthorityProvider()
        with transaction.atomic():
            coordinator_refusal = provider.evaluate_organism_authority(self._post_request(
                coordinator, OrganismAction.CLAIM_APPLICATION, target,
                organism_uuid=organism_uuid, suffix="no-configuration-coordinator",
            ))
        self.assertEqual(
            coordinator_refusal.refusal_code,
            S015AuthorityRefusalCode.C_CONFIGURATION_NOT_ESTABLISHED,
        )

    def test_decided_application_cannot_be_withdrawn(self):
        service = self._service(
            birthing_source=TestFixtureBirthingFactSource(self.organism_uuid),
            registration_source=TestFixtureGovernedRegistrationFactSource(),
        )
        submission = self._submit(service, "decided-withdrawal")
        application = Application.objects.get(pk=submission.application_id)
        coordinator = Identity.objects.select_related("credential").get(pk=self.ids["identity"])
        self._capture_database_now()
        service.claim_application(ClaimApplicationCommand(
            credential=coordinator.credential,
            application_uuid=application.application_uuid,
            request_reference=f"aa-r1:decided-withdrawal-claim:{self.tag}",
            idempotency_key=f"aa-r1:decided-withdrawal-claim:key:{self.tag}",
            occurred_at=self.now,
            effective_at=self.now,
            received_at=self.now,
        ))
        self._capture_database_now()
        service.decide_application(DecideApplicationCommand(
            credential=coordinator.credential,
            application_uuid=application.application_uuid,
            request_reference=f"aa-r1:decided-withdrawal-decision:{self.tag}",
            idempotency_key=f"aa-r1:decided-withdrawal-decision:key:{self.tag}",
            occurred_at=self.now,
            effective_at=self.now,
            received_at=self.now,
            outcome="DECLINE",
        ))
        with self.assertRaises(S015AuthorityDenied) as denied:
            self._capture_database_now()
            service.withdraw_application(WithdrawApplicationCommand(
                credential=self.applicant.credential,
                application_uuid=application.application_uuid,
                request_reference=f"aa-r1:decided-withdrawal:{self.tag}",
                idempotency_key=f"aa-r1:decided-withdrawal:key:{self.tag}",
                occurred_at=self.now,
                effective_at=self.now,
                received_at=self.now,
            ))
        self.assertEqual(denied.exception.refusal_code, S015AuthorityRefusalCode.C_TERMINAL_ROW_STANDS)

    def test_another_person_cannot_submit_for_their_target(self):
        actor = self.applicant
        other = Identity.objects.get(pk=self.ids["identity"])
        target = {
            "action": OrganismAction.SUBMIT_APPLICATION.value,
            "organism_uuid": self.organism_uuid,
            "applicant_identity_uuid": other.identity_id,
        }
        provider = ProductionApplicationAuthorityProvider()
        with transaction.atomic():
            refusal = provider.evaluate_organism_authority(self._own_request(
                actor, OrganismAction.SUBMIT_APPLICATION, target, suffix="other-submit",
            ))
        self.assertIsInstance(refusal, S015OwnApplicantAuthorityRefusal)
        self.assertEqual(refusal.refusal_code, S015AuthorityRefusalCode.E_NOT_APPLICANT)
        self.assertEqual(refusal.disclosed_refusal_code, S015AuthorityRefusalCode.DENIED)

    def test_another_person_cannot_withdraw_an_application(self):
        submission = self._submit(self._service(
            birthing_source=TestFixtureBirthingFactSource(self.organism_uuid),
            registration_source=TestFixtureGovernedRegistrationFactSource(),
        ), "other-withdraw")
        application = Application.objects.get(pk=submission.application_id)
        actor = Identity.objects.select_related("credential").get(pk=self.ids["identity"])
        target = {
            "action": OrganismAction.WITHDRAW_APPLICATION.value,
            "organism_uuid": self.organism_uuid,
            "application_uuid": application.application_uuid,
            "applicant_identity_uuid": actor.identity_id,
        }
        provider = ProductionApplicationAuthorityProvider()
        with transaction.atomic():
            refusal = provider.evaluate_organism_authority(self._own_request(
                actor, OrganismAction.WITHDRAW_APPLICATION, target,
                application_pk=application.pk, suffix="other-withdraw-direct",
            ))
        self.assertIsInstance(refusal, S015OwnApplicantAuthorityRefusal)
        self.assertEqual(refusal.refusal_code, S015AuthorityRefusalCode.E_NOT_APPLICANT)
        self.assertEqual(refusal.disclosed_refusal_code, S015AuthorityRefusalCode.DENIED)

    def test_second_open_application_is_refused(self):
        service = self._service(
            birthing_source=TestFixtureBirthingFactSource(self.organism_uuid),
            registration_source=TestFixtureGovernedRegistrationFactSource(),
        )
        self._submit(service, "open-first")
        with self.assertRaises(S015AuthorityDenied) as denied:
            self._submit(service, "open-second")
        self.assertEqual(denied.exception.refusal_code, S015AuthorityRefusalCode.C_APPLICATION_OPEN)

    def test_closed_gateway_refuses_submission(self):
        service = self._service(
            birthing_source=TestFixtureBirthingFactSource(self.organism_uuid),
            registration_source=TestFixtureGovernedRegistrationFactSource(),
        )
        coordinator = Identity.objects.select_related("credential").get(pk=self.ids["identity"])
        self._capture_database_now()
        draft = service.save_gateway_draft(GatewayDraftCommand(
            credential=coordinator.credential,
            living_organism_id=self.organism_uuid,
            action=OrganismAction.EFFECT_APPLICATION_SETTINGS,
            request_reference=f"aa-r1:closed-settings-draft:{self.tag}",
            idempotency_key=f"aa-r1:closed-settings-draft:key:{self.tag}",
            occurred_at=self.now,
            effective_at=self.now,
            received_at=self.now,
            fields={
                "applications_open": False,
                "automatic_acceptance": False,
                "processing_responsibility": "LO",
            },
        ))
        self._capture_database_now()
        service.effect_gateway_version(EffectGatewayVersionCommand(
            credential=coordinator.credential,
            living_organism_id=self.organism_uuid,
            action=OrganismAction.EFFECT_APPLICATION_SETTINGS,
            version_uuid=draft.version_uuid,
            request_reference=f"aa-r1:closed-settings-effect:{self.tag}",
            idempotency_key=f"aa-r1:closed-settings-effect:key:{self.tag}",
            occurred_at=self.now,
            effective_at=self.now,
            received_at=self.now,
        ))
        with self.assertRaises(S015AuthorityDenied) as denied:
            self._submit(service, "closed-gateway")
        self.assertEqual(denied.exception.refusal_code, S015AuthorityRefusalCode.C_GATEWAY_NOT_OPEN)

    def test_three_gateway_acts_qualify_drafts_and_effects(self):
        service = self._service()
        coordinator = Identity.objects.select_related("credential").get(pk=self.ids["identity"])
        actions = (
            (OrganismAction.EFFECT_APPLICATION_SETTINGS, {
                "applications_open": True,
                "automatic_acceptance": False,
                "processing_responsibility": "LO",
            }),
            (OrganismAction.EFFECT_APPLICATION_QUESTION, {
                "question_text": "TEST FIXTURE: Updated application question",
                "answer_format": "LONG_ANSWER",
                "options": None,
            }),
            (OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION, {
                "cq_active": False,
                "inherit_portal_cq_settings": False,
                "require_cq_to_change_lo_settings": False,
                "require_cq_to_change_application_question": False,
            }),
        )
        for index, (action, fields) in enumerate(actions):
            self._capture_database_now()
            draft = service.save_gateway_draft(GatewayDraftCommand(
                credential=coordinator.credential,
                living_organism_id=self.organism_uuid,
                action=action,
                request_reference=f"aa-r1:h6-draft:{index}:{self.tag}",
                idempotency_key=f"aa-r1:h6-draft:key:{index}:{self.tag}",
                occurred_at=self.now,
                effective_at=self.now,
                received_at=self.now,
                fields=fields,
            ))
            self._capture_database_now()
            event = service.effect_gateway_version(EffectGatewayVersionCommand(
                credential=coordinator.credential,
                living_organism_id=self.organism_uuid,
                action=action,
                version_uuid=draft.version_uuid,
                request_reference=f"aa-r1:h6-effect:{index}:{self.tag}",
                idempotency_key=f"aa-r1:h6-effect:key:{index}:{self.tag}",
                occurred_at=self.now,
                effective_at=self.now,
                received_at=self.now,
            ))
            self.assertEqual(event.action, action.value)
            with self.assertRaises(S015AuthorityDenied) as denied:
                self._capture_database_now()
                service.effect_gateway_version(EffectGatewayVersionCommand(
                    credential=coordinator.credential,
                    living_organism_id=self.organism_uuid,
                    action=action,
                    version_uuid=draft.version_uuid,
                    request_reference=f"aa-r1:h6-refused-effect:{index}:{self.tag}",
                    idempotency_key=f"aa-r1:h6-refused-effect:key:{index}:{self.tag}",
                    occurred_at=self.now,
                    effective_at=self.now,
                    received_at=self.now,
                ))
            self.assertEqual(denied.exception.refusal_code, S015AuthorityRefusalCode.DENIED)

    def test_applicant_cannot_claim_own_application(self):
        service = self._service(
            birthing_source=TestFixtureBirthingFactSource(self.organism_uuid),
            registration_source=TestFixtureGovernedRegistrationFactSource(),
        )
        result = self._submit(service, "applicant-claim")
        application = Application.objects.get(pk=result.application_id)
        request_reference = f"aa-r1:applicant-claim:{self.tag}"
        decisions_before = AuthorityDecision.objects.count()
        with self.assertRaisesMessage(
            S015ApplicationAuthorityUnavailable,
            "actor active membership is required",
        ):
            self._capture_database_now()
            service.claim_application(ClaimApplicationCommand(
                credential=self.applicant.credential,
                application_uuid=application.application_uuid,
                request_reference=request_reference,
                idempotency_key=f"aa-r1:applicant-claim:key:{self.tag}",
                occurred_at=self.now,
                effective_at=self.now,
                received_at=self.now,
            ))
        self.assertEqual(AuthorityDecision.objects.count(), decisions_before)
        self.assertFalse(AuthorityDecision.objects.filter(request_reference=request_reference).exists())

    def test_unrecognised_action_is_refused_by_production_provider(self):
        actor = Identity.objects.get(pk=self.ids["identity"])
        action = OrganismAction.TRANSITION_MEMBERSHIP
        target = {
            "action": action.value,
            "organism_uuid": self.organism_uuid,
            "application_uuid": uuid4(),
        }
        provider = ProductionApplicationAuthorityProvider()
        with transaction.atomic():
            refusal = provider.evaluate_organism_authority(self._post_request(
                actor, action, target, suffix="unrecognised-action",
            ))
        self.assertEqual(refusal.refusal_code, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)

    def test_disclosure_is_member_specific_and_responses_are_deterministic(self):
        member = Identity.objects.get(pk=self.ids["identity"])
        outsider = self.applicant
        target = {
            "action": OrganismAction.CLAIM_APPLICATION.value,
            "organism_uuid": self.organism_uuid,
            "application_uuid": uuid4(),
        }
        provider = ProductionApplicationAuthorityProvider(
            birthing_source=TestFixtureBirthingFactSource(self.organism_uuid),
            registration_source=TestFixtureGovernedRegistrationFactSource(),
        )
        member_request = self._post_request(member, OrganismAction.CLAIM_APPLICATION, target, suffix="disclosure-member")
        outsider_request = self._post_request(outsider, OrganismAction.CLAIM_APPLICATION, target, suffix="disclosure-outsider")
        with transaction.atomic():
            member_refusal = provider._refuse(member_request, S015AuthorityRefusalCode.C_ROUTE_IS_CQ)
            outsider_refusal = provider._refuse(outsider_request, S015AuthorityRefusalCode.C_ROUTE_IS_CQ)
        self.assertEqual(member_refusal.disclosed_refusal_code, S015AuthorityRefusalCode.C_ROUTE_IS_CQ)
        self.assertEqual(outsider_refusal.disclosed_refusal_code, S015AuthorityRefusalCode.DENIED)

        own_target = {
            "action": OrganismAction.SUBMIT_APPLICATION.value,
            "organism_uuid": self.organism_uuid,
            "applicant_identity_uuid": outsider.identity_id,
        }
        request = self._own_request(outsider, OrganismAction.SUBMIT_APPLICATION, own_target, suffix="determinism")
        with transaction.atomic():
            first = provider.evaluate_organism_authority(request)
        with transaction.atomic():
            second = provider.evaluate_organism_authority(request)
        self.assertEqual(canonical_json_bytes(asdict(first)), canonical_json_bytes(asdict(second)))

    def test_current_member_cannot_submit_an_application(self):
        member = Identity.objects.select_related("credential").get(pk=self.ids["identity"])
        self._add_fixture_access_history(member, "member-registration")
        service = self._service(
            birthing_source=TestFixtureBirthingFactSource(self.organism_uuid),
            registration_source=TestFixtureGovernedRegistrationFactSource(),
        )
        with self.assertRaises(S015AuthorityDenied) as denied:
            self._submit(service, "current-member", applicant=member)
        self.assertEqual(denied.exception.refusal_code, S015AuthorityRefusalCode.DENIED)

    def test_coordinator_of_another_organism_is_refused(self):
        with transaction.atomic(), connection.cursor() as cursor:
            other_ids = found_nonroot(cursor, tag=f"other-lo-{self.tag}")
            seed_test_gateway(cursor, other_ids, include_governance_configuration=True)
            cursor.execute("SELECT organism_id FROM core_livingorganism WHERE id = %s", [other_ids["lo"]])
            other_organism_uuid = cursor.fetchone()[0]
        other_coordinator = Identity.objects.get(pk=other_ids["identity"])
        self.assertNotEqual(other_organism_uuid, self.organism_uuid)
        application = self._submit(self._service(
            birthing_source=TestFixtureBirthingFactSource(self.organism_uuid),
            registration_source=TestFixtureGovernedRegistrationFactSource(),
        ), "other-lo")
        target = {
            "action": OrganismAction.CLAIM_APPLICATION.value,
            "organism_uuid": self.organism_uuid,
            "application_uuid": Application.objects.get(pk=application.application_id).application_uuid,
        }
        provider = ProductionApplicationAuthorityProvider()
        with transaction.atomic():
            refusal = provider.evaluate_organism_authority(self._post_request(
                other_coordinator, OrganismAction.CLAIM_APPLICATION, target,
                organism_uuid=self.organism_uuid, suffix="other-lo-coordinator",
            ))
        self.assertEqual(refusal.refusal_code, S015AuthorityRefusalCode.E_NOT_EFFECTIVE_COORDINATOR)
        self.assertEqual(refusal.disclosed_refusal_code, S015AuthorityRefusalCode.DENIED)

    def test_ordinary_member_is_not_a_coordinator(self):
        member = self._add_second_coordinator("ordinary-member", include_office=False)
        target = {
            "action": OrganismAction.CLAIM_APPLICATION.value,
            "organism_uuid": self.organism_uuid,
            "application_uuid": uuid4(),
        }
        provider = ProductionApplicationAuthorityProvider()
        with transaction.atomic():
            refusal = provider.evaluate_organism_authority(self._post_request(
                member, OrganismAction.CLAIM_APPLICATION, target, suffix="ordinary-member",
            ))
        self.assertEqual(refusal.refusal_code, S015AuthorityRefusalCode.E_NOT_EFFECTIVE_COORDINATOR)
        self.assertEqual(refusal.disclosed_refusal_code, S015AuthorityRefusalCode.E_NOT_EFFECTIVE_COORDINATOR)

    def test_title_holder_with_ended_membership_is_refused(self):
        former_coordinator = self._add_second_coordinator("ended-membership")
        ended_at = self.now + timedelta(minutes=5)
        self._end_fixture_membership(former_coordinator, "ended-membership", ended_at)
        target = {
            "action": OrganismAction.CLAIM_APPLICATION.value,
            "organism_uuid": self.organism_uuid,
            "application_uuid": uuid4(),
        }
        provider = ProductionApplicationAuthorityProvider()
        with transaction.atomic():
            refusal = provider.evaluate_organism_authority(self._post_request(
                former_coordinator,
                OrganismAction.CLAIM_APPLICATION,
                target,
                effective_at=ended_at,
                evaluated_at=self.now,
                suffix="ended-membership",
            ))
        self.assertEqual(refusal.refusal_code, S015AuthorityRefusalCode.E_NOT_EFFECTIVE_COORDINATOR)

    def test_cq_route_refuses_coordinator_claim(self):
        submission = self._submit(self._service(
            birthing_source=TestFixtureBirthingFactSource(self.organism_uuid),
            registration_source=TestFixtureGovernedRegistrationFactSource(),
        ), "cq-route")
        application = Application.objects.get(pk=submission.application_id)
        configuration = LOGovernanceConfigurationVersion.objects.filter(
            living_organism_id=self.ids["lo"],
        ).order_by("-pk").first()
        settings = LOApplicationSettingsVersion.objects.filter(
            living_organism_id=self.ids["lo"],
        ).order_by("-pk").first()
        basis = (
            f"governance-configuration-version:{configuration.version_uuid};"
            f"application-settings-version:{settings.version_uuid}"
        )
        coordinator = Identity.objects.select_related("credential").get(pk=self.ids["identity"])
        ApplicationEngagement.objects.create(
            application=application,
            engagement_ordinal=1,
            route=ApplicationEngagement.Route.LO_CQ,
            deciding_party_id=self.ids["lo"],
            engagement_reference="TEST_FIXTURE:CQ_ROUTE",
            settings_basis=basis,
            actor=coordinator,
            enterer=coordinator,
            occurred_at=self.now,
            effective_at=self.now,
            received_at=self.now,
        )
        target = {
            "action": OrganismAction.CLAIM_APPLICATION.value,
            "organism_uuid": self.organism_uuid,
            "application_uuid": application.application_uuid,
        }
        provider = ProductionApplicationAuthorityProvider()
        with transaction.atomic():
            refusal = provider.evaluate_organism_authority(self._post_request(
                coordinator, OrganismAction.CLAIM_APPLICATION, target, suffix="cq-route",
            ))
        self.assertEqual(refusal.refusal_code, S015AuthorityRefusalCode.C_ROUTE_IS_CQ)

    def test_effective_coordinator_without_the_assignment_cannot_decide_and_can_reassign(self):
        service = self._service(
            birthing_source=TestFixtureBirthingFactSource(self.organism_uuid),
            registration_source=TestFixtureGovernedRegistrationFactSource(),
        )
        submission = self._submit(service, "reassign")
        application = Application.objects.get(pk=submission.application_id)
        first_coordinator = Identity.objects.select_related("credential").get(pk=self.ids["identity"])
        second_coordinator = self._add_second_coordinator("reassign")
        self._capture_database_now()
        service.claim_application(ClaimApplicationCommand(
            credential=first_coordinator.credential,
            application_uuid=application.application_uuid,
            request_reference=f"aa-r1:claim-before-reassign:{self.tag}",
            idempotency_key=f"aa-r1:claim-before-reassign:key:{self.tag}",
            occurred_at=self.now,
            effective_at=self.now,
            received_at=self.now,
        ))
        with self.assertRaises(S015AuthorityDenied) as not_assignee:
            self._capture_database_now()
            service.decide_application(DecideApplicationCommand(
                credential=second_coordinator.credential,
                application_uuid=application.application_uuid,
                request_reference=f"aa-r1:not-assignee:{self.tag}",
                idempotency_key=f"aa-r1:not-assignee:key:{self.tag}",
                occurred_at=self.now,
                effective_at=self.now,
                received_at=self.now,
                outcome="DECLINE",
            ))
        self.assertEqual(not_assignee.exception.refusal_code, S015AuthorityRefusalCode.E_NOT_ASSIGNEE)
        self._capture_database_now()
        later = self.now + timedelta(minutes=1)
        reassigned = service.reassign_application(ReassignApplicationCommand(
            credential=second_coordinator.credential,
            application_uuid=application.application_uuid,
            request_reference=f"aa-r1:reassign:{self.tag}",
            idempotency_key=f"aa-r1:reassign:key:{self.tag}",
            occurred_at=self.now,
            effective_at=later,
            received_at=self.now,
        ))
        self.assertEqual(reassigned.action, OrganismAction.REASSIGN_APPLICATION)
        self._capture_database_now()
        with self.assertRaises(S015AuthorityDenied) as former_assignee:
            self._capture_database_now()
            service.decide_application(DecideApplicationCommand(
                credential=first_coordinator.credential,
                application_uuid=application.application_uuid,
                request_reference=f"aa-r1:former-assignee:{self.tag}",
                idempotency_key=f"aa-r1:former-assignee:key:{self.tag}",
                occurred_at=self.now,
                effective_at=later + timedelta(minutes=1),
                received_at=self.now,
                outcome="DECLINE",
            ))
        self.assertEqual(former_assignee.exception.refusal_code, S015AuthorityRefusalCode.E_NOT_ASSIGNEE)

    def test_reassign_without_a_claim_is_refused(self):
        service = self._service(
            birthing_source=TestFixtureBirthingFactSource(self.organism_uuid),
            registration_source=TestFixtureGovernedRegistrationFactSource(),
        )
        submission = self._submit(service, "reassign-without-claim")
        application = Application.objects.get(pk=submission.application_id)
        coordinator = Identity.objects.select_related("credential").get(pk=self.ids["identity"])
        with self.assertRaises(S015AuthorityDenied) as denied:
            self._capture_database_now()
            service.reassign_application(ReassignApplicationCommand(
                credential=coordinator.credential,
                application_uuid=application.application_uuid,
                request_reference=f"aa-r1:reassign-refused:{self.tag}",
                idempotency_key=f"aa-r1:reassign-refused:key:{self.tag}",
                occurred_at=self.now,
                effective_at=self.now,
                received_at=self.now,
            ))
        self.assertEqual(denied.exception.refusal_code, S015AuthorityRefusalCode.DENIED)

    def test_labelled_fixture_sources_qualify_and_resolve_submission(self):
        service = self._service(
            birthing_source=TestFixtureBirthingFactSource(self.organism_uuid),
            registration_source=TestFixtureGovernedRegistrationFactSource(),
        )
        result = self._submit(service, "fixture-positive")
        application = Application.objects.get(pk=result.application_id)
        record = AuthorityDecision.objects.get(
            decision_reference=result.authority_decision_reference,
        )
        self.assertEqual(record.outcome, "QUALIFIED")
        self.assertEqual(record.authority_reference, application.authority_reference)
        self.assertEqual(record.authority_reference[:7], "s015a1:")
        fact_relations = {fact["relation"] for fact in record.facts_read}
        expected_relations = {
            "core_identity",
            "core_livingorganism",
            "s3_il_birthing_fact_source",
            "core_identitytransition",
            "core_loapplicationsettingsversion",
            "core_loapplicationquestionversion",
            "core_logovernanceconfigurationversion",
        }
        self.assertTrue(expected_relations <= fact_relations, expected_relations - fact_relations)
        coordinator = Identity.objects.select_related("credential").get(pk=self.ids["identity"])
        self._capture_database_now()
        claim = service.claim_application(ClaimApplicationCommand(
            credential=coordinator.credential,
            application_uuid=application.application_uuid,
            request_reference=f"aa-r1:claim:{self.tag}",
            idempotency_key=f"aa-r1:claim:key:{self.tag}",
            occurred_at=self.now,
            effective_at=self.now,
            received_at=self.now,
        ))
        claim_record = AuthorityDecision.objects.get(decision_reference=claim.authority_decision_reference)
        self.assertEqual(claim_record.outcome, "QUALIFIED")
        claim_relations = {fact["relation"] for fact in claim_record.facts_read}
        self.assertTrue({
            "core_contextualroleassignmentstateevent",
            "core_organismmembershiptransition",
            "core_logovernanceconfigurationversion",
            "core_loapplicationsettingsversion",
        } <= claim_relations)
        self._capture_database_now()
        decision = service.decide_application(DecideApplicationCommand(
            credential=coordinator.credential,
            application_uuid=application.application_uuid,
            request_reference=f"aa-r1:decide:{self.tag}",
            idempotency_key=f"aa-r1:decide:key:{self.tag}",
            occurred_at=self.now,
            effective_at=self.now,
            received_at=self.now,
            outcome="DECLINE",
        ))
        decision_record = AuthorityDecision.objects.get(decision_reference=decision.authority_decision_reference)
        self.assertEqual(decision_record.outcome, "QUALIFIED")
        self.assertLessEqual(len(decision_record.authority_reference), 255)
        self.assertTrue(any(fact["relation"] == "core_applicationengagement" for fact in decision_record.facts_read))
        assignment_fact = next(
            fact for fact in decision_record.facts_read
            if fact["relation"] == "core_csaassignment"
        )
        self.assertEqual(
            assignment_fact["reference"],
            str(CSAAssignment.objects.get(application=application).pk),
        )

    def test_production_provider_withdraws_only_the_applicants_own_application(self):
        service = self._service(
            birthing_source=TestFixtureBirthingFactSource(self.organism_uuid),
            registration_source=TestFixtureGovernedRegistrationFactSource(),
        )
        submission = self._submit(service, "withdraw")
        application = Application.objects.get(pk=submission.application_id)
        self._capture_database_now()
        withdrawal = service.withdraw_application(WithdrawApplicationCommand(
            credential=self.applicant.credential,
            application_uuid=application.application_uuid,
            request_reference=f"aa-r1:withdraw:{self.tag}",
            idempotency_key=f"aa-r1:withdraw:key:{self.tag}",
            occurred_at=self.now,
            effective_at=self.now,
            received_at=self.now,
        ))
        record = AuthorityDecision.objects.get(decision_reference=withdrawal.authority_decision_reference)
        self.assertEqual(record.outcome, "QUALIFIED")
        self.assertEqual(record.action, OrganismAction.WITHDRAW_APPLICATION.value)

    def test_installed_authority_decision_guard_catalogue(self):
        emit_guard_catalogue(connection, "POST-MIGRATION")
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_catalog.pg_trigger t "
                "JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND NOT t.tgisinternal"
            )
            self.assertEqual(cursor.fetchone()[0], 398)
            cursor.execute(
                "SELECT trigger.tgname, trigger.tgenabled, function.proname, "
                "pg_get_functiondef(function.oid) "
                "FROM pg_trigger trigger "
                "JOIN pg_class relation ON relation.oid = trigger.tgrelid "
                "JOIN pg_proc function ON function.oid = trigger.tgfoid "
                "WHERE relation.oid = 'public.core_authoritydecision'::regclass "
                "AND NOT trigger.tgisinternal ORDER BY trigger.tgname"
            )
            rows = cursor.fetchall()
        self.assertEqual(
            {row[0] for row in rows},
            {
                "s015_authoritydecision_immutable",
                "s015_authoritydecision_insert_guard",
                "s015_authoritydecision_truncate_immutable",
            },
        )
        self.assertTrue(all(row[1] == "O" for row in rows))
        self.assertEqual(
            {row[0]: row[2] for row in rows},
            {
                "s015_authoritydecision_immutable": "s015_refuse_row_mutation",
                "s015_authoritydecision_insert_guard": "s015_authoritydecision_guard",
                "s015_authoritydecision_truncate_immutable": "s015_refuse_row_mutation",
            },
        )
        insert_guard = next(row[3] for row in rows if row[0] == "s015_authoritydecision_insert_guard")
        self.assertIn("target fingerprint does not recompute", insert_guard)
        self.assertIn("decision digest does not recompute", insert_guard)

    def test_ap6_non_superuser_application_role_when_present(self):
        role_name = os.environ.get("INTEVIA_POSTGRES_APPLICATION_ROLE", "intevia_app")
        with connection.cursor() as cursor:
            cursor.execute("SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname = %s", [role_name])
            role = cursor.fetchone()
        if role is None:
            print(f"AP6=NOT RUN (PostgreSQL role {role_name} is absent)", flush=True)
            self.skipTest(f"AP-6 requires existing PostgreSQL role {role_name}")
        print("AP6=NOT RUN (role present; routes not exercised under it)", flush=True)
        self.skipTest("AP-6 requires exercising all eight application routes under the non-superuser role")

    def test_0034_refuses_tampered_digest_and_fingerprint(self):
        record = self._qualified_submission_record("tamper")
        bad_digest = self._decision_copy_fields(record)
        bad_digest["request_reference"] += ":digest"
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                AuthorityDecision.objects.create(**bad_digest)

        bad_fingerprint = self._decision_copy_fields(record)
        payload = dict(record.decision_payload)
        payload["request_reference"] = record.request_reference + ":fingerprint"
        payload_bytes = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        bad_fingerprint["decision_payload"] = payload
        bad_fingerprint["decision_payload_bytes"] = payload_bytes
        bad_fingerprint["decision_reference"] = "s015d1:" + hashlib.sha256(
            b"INTEVIA:S015:ORGANISM_AUTHORITY_DECISION:v1\x00" + payload_bytes
        ).hexdigest()
        bad_fingerprint["request_reference"] += ":fingerprint"
        bad_fingerprint["target_fingerprint"] = "f" * 64
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                AuthorityDecision.objects.create(**bad_fingerprint)

    def test_0034_refuses_update_delete_and_truncate(self):
        record = self._qualified_submission_record("immutable")
        for statement, params in (
            ("UPDATE public.core_authoritydecision SET provider_version = 'tampered' WHERE id = %s", [record.pk]),
            ("DELETE FROM public.core_authoritydecision WHERE id = %s", [record.pk]),
            ("TRUNCATE TABLE public.core_authoritydecision", []),
        ):
            with self.assertRaises(DatabaseError):
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute(statement, params)

    def test_0034_reversal_refuses_when_rows_exist(self):
        migration = importlib.import_module("core.migrations.0034_s015_authority_decisions")
        self._qualified_submission_record("reverse-refuse")
        with self.assertRaisesMessage(RuntimeError, "refusing destructive reverse"):
            migration.remove_authority_decision_guards(None, type("Editor", (), {"connection": connection})())

    def test_0034_reversal_succeeds_when_empty(self):
        migration = importlib.import_module("core.migrations.0034_s015_authority_decisions")

        class Rollback(Exception):
            pass

        with self.assertRaises(Rollback):
            with transaction.atomic():
                self.assertFalse(AuthorityDecision.objects.exists())
                migration.remove_authority_decision_guards(None, type("Editor", (), {"connection": connection})())
                emit_guard_catalogue(connection, "AFTER-0034-EMPTY-REVERSAL")
                raise Rollback()


__all__ = [
    "ProductionApplicationAuthorityTests",
    "TestFixtureBirthingFactSource",
    "TestFixtureGovernedRegistrationFactSource",
]
