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
from dataclasses import asdict, replace
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
    CSAAssignmentEnd,
    ContextualRoleAssignment,
    Identity,
    IdentityTransition,
    LivingOrganism,
    LOGovernanceConfigurationVersion,
    LOApplicationQuestionVersion,
    LOApplicationSettingsVersion,
    OrganismMembership,
    OrganismCommandReceipt,
)
from core.organism_contract import OrganismAction
from src.intevia.services.organism_application_authority import (
    _assignment_term_in_effect,
    ProductionApplicationAuthorityProvider,
)
from src.intevia.services.organism_application_service import (
    ApplicationCommandResult,
    _COMMAND_DOMAIN,
    S015ApplicationAuthorityUnavailable,
    S015ApplicationRefused,
    ClaimApplicationCommand,
    DecideApplicationCommand,
    EffectGatewayVersionCommand,
    GatewayDraftCommand,
    gateway_version_status,
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
from src.intevia.services.organism_command_support import (
    ReplayViewer,
    WithheldField,
    _command_payload,
    withheld_fields,
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
from tests.test_s015_role_command import (
    _register_no_other_sessions_cleanup,
    _run_with_external_locks,
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


class _CountingAuthorityProvider:
    def __init__(self, delegate):
        self.delegate = delegate
        self.requests = []

    @property
    def provider_version(self):
        return self.delegate.provider_version

    def evaluate_organism_authority(self, request):
        self.requests.append(request)
        return self.delegate.evaluate_organism_authority(request)


class _HistoricalReferenceAuthorityProvider:
    provider_identity = ProductionApplicationAuthorityProvider.provider_identity

    def __init__(self, delegate, provider_version):
        self.delegate = delegate
        self.provider_version = provider_version

    def evaluate_organism_authority(self, request):
        result = self.delegate.evaluate_organism_authority(request)
        if (
            request.action not in {
                OrganismAction.DECIDE_APPLICATION,
                OrganismAction.REASSIGN_APPLICATION,
            }
            or getattr(result, "refusal_code", None) is not None
        ):
            return result
        facts = json.loads(result.authority_reference[len("s015a1:"):])
        claim = CSAAssignment.objects.get(assignment_uuid=facts["a"])
        engagement = ApplicationEngagement.objects.get(
            application__application_uuid=request.target["application_uuid"],
            engagement_ordinal=facts["e"],
        )
        configuration = LOGovernanceConfigurationVersion.objects.get(
            version_uuid=facts["c"],
        )
        facts.update({
            "a": str(claim.pk),
            "e": str(engagement.pk),
            "t": "CQ_INACTIVE" if configuration.cq_active is not True else "SOLE_COORDINATOR",
        })
        return replace(
            result,
            authority_reference="s015a1:" + canonical_json_bytes(facts).decode("utf-8"),
        )


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


class TestFixtureCoordinatorNominationEntitlementFactSource:
    """TEST FIXTURE ONLY: supplies a labelled subscription entitlement reference."""

    def __init__(self, enabled=True):
        self.enabled = enabled

    def reference_at(self, living_organism_id, effective_at, evaluated_at):
        if not self.enabled or type(effective_at) is not datetime or type(evaluated_at) is not datetime:
            return None
        return living_organism_id.hex


@POSTGRES_ONLY
class ProductionApplicationAuthorityTests(TransactionTestCase):
    def setUp(self):
        _register_no_other_sessions_cleanup(self)
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

    def _service(
        self,
        *,
        birthing_source=None,
        registration_source=None,
        nomination_entitlement_source=None,
        authority_provider=None,
    ):
        self._capture_database_now()
        provider = authority_provider or ProductionApplicationAuthorityProvider(
            birthing_source=birthing_source,
            registration_source=registration_source,
            nomination_entitlement_source=nomination_entitlement_source,
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
        from core.models import OrganismRoleDefinition
        from src.intevia.services.organism_role_service import (
            OrganismRoleService,
            ProposeAssignmentCommand,
            TransitionAssignmentCommand,
        )

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
        identity = Identity.objects.select_related("credential").get(pk=identity_pk)
        if include_office:
            founder = Identity.objects.select_related("credential").get(pk=self.ids["identity"])
            role = OrganismRoleDefinition.objects.get(pk=self.ids["roles"]["COORDINATOR"])
            service = OrganismRoleService(
                authority=S015Authority(
                    provider=ProductionApplicationAuthorityProvider(),
                ),
                clock=lambda: self.now,
            )
            self._capture_database_now()
            proposal_at = self.now
            proposal = service.propose_assignment(ProposeAssignmentCommand(
                credential=identity.credential,
                request_reference=f"aa-r2:{suffix}:self-propose:{self.tag}",
                idempotency_key=f"aa-r2:{suffix}:self-propose:key:{self.tag}",
                occurred_at=proposal_at,
                effective_at=proposal_at,
                received_at=proposal_at,
                membership_uuid=membership_uuid,
                role_definition_uuid=role.role_uuid,
                effective_until_state="NONE",
                effective_until=None,
            ))
            self._capture_database_now()
            assign_at = self.now
            service.transition_assignment(TransitionAssignmentCommand(
                credential=founder.credential,
                request_reference=f"aa-r2:{suffix}:assign:{self.tag}",
                idempotency_key=f"aa-r2:{suffix}:assign:key:{self.tag}",
                occurred_at=assign_at,
                effective_at=assign_at,
                received_at=assign_at,
                assignment_uuid=proposal.assignment_uuid,
                action="ASSIGN",
                resulting_state="ACTIVE",
                effective_until_state="NONE",
                effective_until=None,
            ))
            self._capture_database_now()
        return identity

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

    def test_missing_assignment_event_is_not_in_effect(self):
        self.assertFalse(_assignment_term_in_effect(
            "default", 2**63 - 1, self.now, self.now,
        ))

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
        birthing_fact = next(
            fact for fact in record.facts_read
            if fact["relation"] == "s3_il_birthing_fact_source"
        )
        self.assertIsNone(birthing_fact["reference"])

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
        application = Application.objects.get(application_uuid=submission.application_uuid)
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
        application = Application.objects.get(application_uuid=submission.application_uuid)
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
        provider = _CountingAuthorityProvider(ProductionApplicationAuthorityProvider())
        service = self._service(authority_provider=provider)
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
                "role_suspension_enabled": True,
                "suspension_tolls_term": "YES",
            }),
        )
        for index, (action, fields) in enumerate(actions):
            receipts_before_draft = OrganismCommandReceipt.objects.count()
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
            self.assertEqual(OrganismCommandReceipt.objects.count(), receipts_before_draft)
            self._capture_database_now()
            effect_command = EffectGatewayVersionCommand(
                credential=coordinator.credential,
                living_organism_id=self.organism_uuid,
                action=action,
                version_uuid=draft.version_uuid,
                request_reference=f"aa-r1:h6-effect:{index}:{self.tag}",
                idempotency_key=f"aa-r1:h6-effect:key:{index}:{self.tag}",
                occurred_at=self.now,
                effective_at=self.now,
                received_at=self.now,
            )
            event = service.effect_gateway_version(effect_command)
            self.assertEqual(event.action, action.value)
            provider_calls = len(provider.requests)
            decisions = AuthorityDecision.objects.count()
            receipts = OrganismCommandReceipt.objects.count()
            replay = service.effect_gateway_version(effect_command)
            self.assertEqual(replay, event)
            self.assertEqual(len(provider.requests), provider_calls)
            self.assertEqual(AuthorityDecision.objects.count(), decisions)
            self.assertEqual(OrganismCommandReceipt.objects.count(), receipts)
            if action is OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION:
                configuration = LOGovernanceConfigurationVersion.objects.get(
                    version_uuid=draft.version_uuid,
                )
                self.assertIs(configuration.role_suspension_enabled, True)
                self.assertEqual(configuration.suspension_tolls_term, "YES")
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

    def test_a_nomination_policy_requires_subscription_entitlement(self):
        coordinator = Identity.objects.select_related("credential").get(pk=self.ids["identity"])
        service = self._service()
        fields = {
            "cq_active": False,
            "inherit_portal_cq_settings": False,
            "require_cq_to_change_lo_settings": False,
            "require_cq_to_change_application_question": False,
            "coordinator_nomination_policy": "A",
        }
        self._capture_database_now()
        draft = service.save_gateway_draft(GatewayDraftCommand(
            credential=coordinator.credential,
            living_organism_id=self.organism_uuid,
            action=OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
            request_reference=f"aa-r2:policy-draft:{self.tag}",
            idempotency_key=f"aa-r2:policy-draft:key:{self.tag}",
            occurred_at=self.now,
            effective_at=self.now,
            received_at=self.now,
            fields=fields,
        ))
        with self.assertRaises(S015AuthorityDenied) as denied:
            self._capture_database_now()
            service.effect_gateway_version(EffectGatewayVersionCommand(
                credential=coordinator.credential,
                living_organism_id=self.organism_uuid,
                action=OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
                version_uuid=draft.version_uuid,
                request_reference=f"aa-r2:policy-effect-denied:{self.tag}",
                idempotency_key=f"aa-r2:policy-effect-denied:key:{self.tag}",
                occurred_at=self.now,
                effective_at=self.now,
                received_at=self.now,
            ))
        self.assertEqual(
            denied.exception.refusal_code,
            S015AuthorityRefusalCode.E_NOMINATION_POLICY_NOT_ENTITLED,
        )
        refusal = AuthorityDecision.objects.get(
            decision_reference=denied.exception.authority_decision_reference,
        )
        fact_references = {(fact["relation"], fact["reference"]) for fact in refusal.facts_read}
        self.assertIn(
            ("core_logovernanceconfigurationversion", f"governance-configuration-version:{draft.version_uuid}"),
            fact_references,
        )
        self.assertIn(("s2b_coordinator_nomination_entitlement", None), fact_references)

        entitled_service = self._service(
            nomination_entitlement_source=TestFixtureCoordinatorNominationEntitlementFactSource(),
        )
        self._capture_database_now()
        effected = entitled_service.effect_gateway_version(EffectGatewayVersionCommand(
            credential=coordinator.credential,
            living_organism_id=self.organism_uuid,
            action=OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
            version_uuid=draft.version_uuid,
            request_reference=f"aa-r2:policy-effect-entitled:{self.tag}",
            idempotency_key=f"aa-r2:policy-effect-entitled:key:{self.tag}",
            occurred_at=self.now,
            effective_at=self.now,
            received_at=self.now,
        ))
        self.assertEqual(effected.action, OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION.value)

    def test_null_nomination_policy_uses_named_default_c(self):
        from core.models import ContextualRoleAssignmentStateEvent, LivingOrganismEvent

        configuration = LOGovernanceConfigurationVersion.objects.get(
            living_organism_id=self.ids["lo"],
        )
        self.assertIsNone(configuration.coordinator_nomination_policy)
        configuration_effect = LivingOrganismEvent.objects.get(
            living_organism_id=self.ids["lo"],
            action=OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION.value,
            evidence_reference=f"governance-configuration-version:{configuration.version_uuid}",
        )
        self.assertLessEqual(configuration_effect.effective_at, self.now)

        member = self._add_second_coordinator("null-policy-default-c")
        assignment = ContextualRoleAssignment.objects.get(
            membership__identity=member,
            role_definition__code="COORDINATOR",
            role_definition__living_organism_id=self.ids["lo"],
        )
        proposal = ContextualRoleAssignmentStateEvent.objects.get(
            assignment=assignment,
            sequence=1,
            action="PROPOSE",
        )
        decision = AuthorityDecision.objects.get(
            decision_reference=proposal.authority_decision_reference,
        )
        facts = {(fact["relation"], fact["reference"]) for fact in decision.facts_read}
        self.assertIn(("s2b_coordinator_nomination_policy", "C"), facts)
        self.assertIn(("s2b_coordinator_nomination_policy_default", "C"), facts)
        self.assertIn(("s2b_coordinator_nomination_entitlement", None), facts)

    def test_nomination_policy_a_and_b_apply_their_proposer_rules(self):
        from core.models import OrganismRoleDefinition
        from src.intevia.services.organism_authority import S015PostFoundingAuthorityResponse

        entitlement_source = TestFixtureCoordinatorNominationEntitlementFactSource()
        provider = ProductionApplicationAuthorityProvider(
            nomination_entitlement_source=entitlement_source,
        )
        service = OrganismApplicationService(
            authority=S015Authority(provider=provider),
            clock=lambda: self.now,
        )
        coordinator = Identity.objects.select_related("credential").get(pk=self.ids["identity"])

        def effect_policy(policy, suffix):
            self._capture_database_now()
            draft = service.save_gateway_draft(GatewayDraftCommand(
                credential=coordinator.credential,
                living_organism_id=self.organism_uuid,
                action=OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
                request_reference=f"aa-r2:{suffix}:draft:{self.tag}",
                idempotency_key=f"aa-r2:{suffix}:draft:key:{self.tag}",
                occurred_at=self.now,
                effective_at=self.now,
                received_at=self.now,
                fields={
                    "cq_active": False,
                    "inherit_portal_cq_settings": False,
                    "require_cq_to_change_lo_settings": False,
                    "require_cq_to_change_application_question": False,
                    "coordinator_nomination_policy": policy,
                },
            ))
            self._capture_database_now()
            service.effect_gateway_version(EffectGatewayVersionCommand(
                credential=coordinator.credential,
                living_organism_id=self.organism_uuid,
                action=OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
                version_uuid=draft.version_uuid,
                request_reference=f"aa-r2:{suffix}:effect:{self.tag}",
                idempotency_key=f"aa-r2:{suffix}:effect:key:{self.tag}",
                occurred_at=self.now,
                effective_at=self.now,
                received_at=self.now,
            ))
            return draft

        proposer = self._add_second_coordinator("policy-member", include_office=False)
        nominee = self._add_second_coordinator("policy-nominee", include_office=False)
        role = OrganismRoleDefinition.objects.get(
            living_organism_id=self.ids["lo"],
            code="COORDINATOR",
            scope="LIVING_ORGANISM",
            active=True,
        )

        def proposal_request(actor, membership, suffix):
            membership_row = OrganismMembership.objects.get(
                identity=membership,
                living_organism_id=self.ids["lo"],
            )
            target = {
                "action": OrganismAction.TRANSITION_CONTEXTUAL_ROLE.value,
                "event_action": "PROPOSE",
                "living_organism_id": str(self.organism_uuid),
                "membership_uuid": str(membership_row.membership_uuid),
                "resulting_state": "PROPOSED",
                "role_definition_uuid": str(role.role_uuid),
                "schema": "intevia.s015.organism-role-target.v1",
            }
            return self._post_request(
                actor, OrganismAction.TRANSITION_CONTEXTUAL_ROLE, target, suffix=suffix,
            )

        effect_policy("A", "policy-a")
        admitted_a = provider.evaluate_organism_authority(
            proposal_request(proposer, nominee, "policy-a-member-to-member"),
        )
        self.assertIsInstance(admitted_a, S015PostFoundingAuthorityResponse)

        effect_policy("B", "policy-b")
        refused_b_self = provider.evaluate_organism_authority(
            proposal_request(proposer, proposer, "policy-b-member-self"),
        )
        self.assertEqual(
            refused_b_self.refusal_code,
            S015AuthorityRefusalCode.E_NOT_EFFECTIVE_COORDINATOR,
        )
        admitted_b = provider.evaluate_organism_authority(
            proposal_request(coordinator, nominee, "policy-b-coordinator-to-member"),
        )
        self.assertIsInstance(admitted_b, S015PostFoundingAuthorityResponse)

    def test_nomination_policy_lapse_and_renewal_use_latest_valid_selection(self):
        import json

        from core.models import OrganismRoleDefinition
        from src.intevia.services.organism_authority import S015PostFoundingAuthorityResponse

        entitlement_source = TestFixtureCoordinatorNominationEntitlementFactSource()
        provider = ProductionApplicationAuthorityProvider(
            nomination_entitlement_source=entitlement_source,
        )
        service = OrganismApplicationService(
            authority=S015Authority(provider=provider),
            clock=lambda: self.now,
        )
        coordinator = Identity.objects.select_related("credential").get(pk=self.ids["identity"])
        proposer = self._add_second_coordinator("policy-lapse-member", include_office=False)
        nominee = self._add_second_coordinator("policy-lapse-nominee", include_office=False)
        proposer_membership = OrganismMembership.objects.get(
            identity=proposer, living_organism_id=self.ids["lo"],
        )
        nominee_membership = OrganismMembership.objects.get(
            identity=nominee, living_organism_id=self.ids["lo"],
        )
        role = OrganismRoleDefinition.objects.get(
            living_organism_id=self.ids["lo"],
            code="COORDINATOR",
            scope="LIVING_ORGANISM",
            active=True,
        )

        def effect_policy(policy, suffix):
            self._capture_database_now()
            draft = service.save_gateway_draft(GatewayDraftCommand(
                credential=coordinator.credential,
                living_organism_id=self.organism_uuid,
                action=OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
                request_reference=f"aa-r2:{suffix}:draft:{self.tag}",
                idempotency_key=f"aa-r2:{suffix}:draft:key:{self.tag}",
                occurred_at=self.now,
                effective_at=self.now,
                received_at=self.now,
                fields={
                    "cq_active": False,
                    "inherit_portal_cq_settings": False,
                    "require_cq_to_change_lo_settings": False,
                    "require_cq_to_change_application_question": False,
                    "coordinator_nomination_policy": policy,
                },
            ))
            self._capture_database_now()
            service.effect_gateway_version(EffectGatewayVersionCommand(
                credential=coordinator.credential,
                living_organism_id=self.organism_uuid,
                action=OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
                version_uuid=draft.version_uuid,
                request_reference=f"aa-r2:{suffix}:effect:{self.tag}",
                idempotency_key=f"aa-r2:{suffix}:effect:key:{self.tag}",
                occurred_at=self.now,
                effective_at=self.now,
                received_at=self.now,
            ))

        def proposal_result(nominee_membership_row, suffix):
            target = {
                "action": OrganismAction.TRANSITION_CONTEXTUAL_ROLE.value,
                "event_action": "PROPOSE",
                "living_organism_id": str(self.organism_uuid),
                "membership_uuid": str(nominee_membership_row.membership_uuid),
                "resulting_state": "PROPOSED",
                "role_definition_uuid": str(role.role_uuid),
                "schema": "intevia.s015.organism-role-target.v1",
            }
            return provider.evaluate_organism_authority(self._post_request(
                proposer, OrganismAction.TRANSITION_CONTEXTUAL_ROLE, target, suffix=suffix,
            ))

        def effective_policy(result):
            return json.loads(result.authority_reference[len("s015a1:"):])["k"]

        effect_policy("A", "policy-lapse-a")
        self.assertIsInstance(proposal_result(nominee_membership, "policy-a-active"), S015PostFoundingAuthorityResponse)

        entitlement_source.enabled = False
        lapsed_other = proposal_result(nominee_membership, "policy-a-lapsed-other")
        self.assertEqual(lapsed_other.refusal_code, S015AuthorityRefusalCode.E_NOT_EFFECTIVE_COORDINATOR)
        lapsed_self = proposal_result(proposer_membership, "policy-a-lapsed-self")
        self.assertIsInstance(lapsed_self, S015PostFoundingAuthorityResponse)
        self.assertEqual(effective_policy(lapsed_self), "C")

        entitlement_source.enabled = True
        renewed_a = proposal_result(nominee_membership, "policy-a-renewed")
        self.assertIsInstance(renewed_a, S015PostFoundingAuthorityResponse)
        self.assertEqual(effective_policy(renewed_a), "A")

        entitlement_source.enabled = False
        effect_policy("C", "policy-lapse-change-to-c")
        entitlement_source.enabled = True
        renewed_c = proposal_result(nominee_membership, "policy-c-renewed")
        self.assertEqual(renewed_c.refusal_code, S015AuthorityRefusalCode.E_NOT_EFFECTIVE_COORDINATOR)

    def test_applicant_cannot_claim_own_application(self):
        service = self._service(
            birthing_source=TestFixtureBirthingFactSource(self.organism_uuid),
            registration_source=TestFixtureGovernedRegistrationFactSource(),
        )
        result = self._submit(service, "applicant-claim")
        application = Application.objects.get(application_uuid=result.application_uuid)
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
        action = OrganismAction.RECORD_GOVERNED_DETERMINATION
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

        membership_action = OrganismAction.TRANSITION_MEMBERSHIP
        membership_target = {
            "action": membership_action.value,
            "living_organism_id": str(self.organism_uuid),
            "membership_uuid": str(uuid4()),
            "resulting_state": "PROBATIONARY",
        }
        with transaction.atomic():
            membership_refusal = provider.evaluate_organism_authority(self._post_request(
                actor, membership_action, membership_target, suffix="membership-not-implemented",
            ))
        self.assertEqual(
            membership_refusal.refusal_code,
            S015AuthorityRefusalCode.C_MEMBERSHIP_TRANSITION_NOT_IMPLEMENTED,
        )
        self.assertEqual(
            membership_refusal.disclosed_refusal_code,
            S015AuthorityRefusalCode.C_MEMBERSHIP_TRANSITION_NOT_IMPLEMENTED,
        )

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

    def test_k2_recovery_refusal_is_member_specific(self):
        from core.models import OrganismRoleDefinition

        member = self._add_second_coordinator("k2-disclosure-member", include_office=False)
        founder = Identity.objects.get(pk=self.ids["identity"])
        self._end_fixture_membership(founder, "k2-disclosure-founder-ended", self.now)
        membership = OrganismMembership.objects.get(
            identity=member,
            living_organism_id=self.ids["lo"],
        )
        role = OrganismRoleDefinition.objects.get(pk=self.ids["roles"]["COORDINATOR"])
        target = {
            "action": OrganismAction.TRANSITION_CONTEXTUAL_ROLE.value,
            "event_action": "PROPOSE",
            "living_organism_id": str(self.organism_uuid),
            "membership_uuid": str(membership.membership_uuid),
            "resulting_state": "PROPOSED",
            "role_definition_uuid": str(role.role_uuid),
            "schema": "intevia.s015.organism-role-target.v1",
        }
        provider = ProductionApplicationAuthorityProvider()
        member_refusal = provider.evaluate_organism_authority(self._post_request(
            member,
            OrganismAction.TRANSITION_CONTEXTUAL_ROLE,
            target,
            suffix="k2-disclosure-member",
        ))
        outsider_refusal = provider.evaluate_organism_authority(self._post_request(
            self.applicant,
            OrganismAction.TRANSITION_CONTEXTUAL_ROLE,
            target,
            suffix="k2-disclosure-outsider",
        ))

        self.assertEqual(
            member_refusal.refusal_code,
            S015AuthorityRefusalCode.C_COORDINATOR_RECOVERY_NOT_IMPLEMENTED,
        )
        self.assertEqual(
            member_refusal.disclosed_refusal_code,
            S015AuthorityRefusalCode.C_COORDINATOR_RECOVERY_NOT_IMPLEMENTED,
        )
        self.assertEqual(
            outsider_refusal.refusal_code,
            S015AuthorityRefusalCode.C_COORDINATOR_RECOVERY_NOT_IMPLEMENTED,
        )
        self.assertEqual(
            outsider_refusal.disclosed_refusal_code,
            S015AuthorityRefusalCode.DENIED,
        )

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
            "application_uuid": application.application_uuid,
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
        application = Application.objects.get(application_uuid=submission.application_uuid)
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

    def test_w_d1b_authority_reference_bound_is_238_characters(self):
        facts = {
            "a": str(uuid4()),
            "c": str(uuid4()),
            "e": 2147483647,
            "m": str(uuid4()),
            "o": str(uuid4()),
            "s": str(uuid4()),
        }
        reference = "s015a1:" + canonical_json_bytes(facts).decode("utf-8")

        self.assertEqual(set(facts), {"a", "c", "e", "m", "o", "s"})
        self.assertEqual(type(facts["e"]), int)
        self.assertTrue(all(type(facts[key]) is str and len(facts[key]) == 36 for key in ("a", "c", "m", "o", "s")))
        self.assertEqual(len(reference), 238)
        self.assertLessEqual(len(reference), 255)

    def test_effective_coordinator_without_the_assignment_cannot_decide_and_can_reassign(self):
        """Consumer update: R3 key-form expectations become R4 UUID/integer forms served unchanged.

        W-D1i Phase A PASS: C:\\Users\\Carewen\\INTEVIA_AGENT_STAGING\\PKTB_R4S4\\ME_DEV\\AA_R3_D1_PRECONDITION_CHECK.md,
        SHA-256 aa7cb26c3d09ccb20f88adcf510ade7843b5a23e0cf6f8029360e7ccb5e1f907.
        """
        provider = _CountingAuthorityProvider(ProductionApplicationAuthorityProvider(
            birthing_source=TestFixtureBirthingFactSource(self.organism_uuid),
            registration_source=TestFixtureGovernedRegistrationFactSource(),
        ))
        service = self._service(
            authority_provider=provider,
        )
        submission = self._submit(service, "reassign")
        application = Application.objects.get(application_uuid=submission.application_uuid)
        first_coordinator = Identity.objects.select_related("credential").get(pk=self.ids["identity"])
        second_coordinator = self._add_second_coordinator("reassign")
        Identity.objects.filter(pk=first_coordinator.pk).update(
            access_epoch=first_coordinator.access_epoch + 1,
        )
        first_coordinator.refresh_from_db()
        self._capture_database_now()
        claim_command = ClaimApplicationCommand(
            credential=first_coordinator.credential,
            application_uuid=application.application_uuid,
            request_reference=f"aa-r1:claim-before-reassign:{self.tag}",
            idempotency_key=f"aa-r1:claim-before-reassign:key:{self.tag}",
            occurred_at=self.now,
            effective_at=self.now,
            received_at=self.now,
        )
        claim_result = service.claim_application(claim_command)
        claim_decision = AuthorityDecision.objects.get(
            decision_reference=claim_result.authority_decision_reference,
        )
        self.assertEqual(claim_decision.actor_identity_id, first_coordinator.identity_id)
        self._capture_database_now()
        claim_provider_calls = len(provider.requests)
        claim_decisions = AuthorityDecision.objects.count()
        claim_receipts = OrganismCommandReceipt.objects.count()
        self.assertEqual(service.claim_application(claim_command), claim_result)
        self.assertEqual(len(provider.requests), claim_provider_calls)
        self.assertEqual(AuthorityDecision.objects.count(), claim_decisions)
        self.assertEqual(OrganismCommandReceipt.objects.count(), claim_receipts)
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
        reassign_command = ReassignApplicationCommand(
            credential=second_coordinator.credential,
            application_uuid=application.application_uuid,
            request_reference=f"aa-r1:reassign:{self.tag}",
            idempotency_key=f"aa-r1:reassign:key:{self.tag}",
            occurred_at=self.now,
            effective_at=later,
            received_at=self.now,
        )
        reassigned = service.reassign_application(reassign_command)
        self.assertEqual(reassigned.action, OrganismAction.REASSIGN_APPLICATION)
        self.assertEqual(ProductionApplicationAuthorityProvider.provider_version, "R5.0")
        replacement = CSAAssignment.objects.get(application=application, replaces__isnull=False)
        original_claim = CSAAssignment.objects.get(application=application, replaces__isnull=True)
        first_engagement = ApplicationEngagement.objects.get(application=application, engagement_ordinal=1)
        second_engagement = ApplicationEngagement.objects.get(application=application, engagement_ordinal=2)
        reassign_record = AuthorityDecision.objects.get(
            decision_reference=reassigned.authority_decision_reference,
        )
        reassign_reference = reassign_record.authority_reference
        reassign_facts = json.loads(reassign_reference[len("s015a1:"):])
        self.assertEqual((reassign_record.outcome, reassign_record.refusal_code), ("QUALIFIED", None))
        self.assertEqual(reassign_record.provider_version, "R5.0")
        self.assertEqual(set(reassign_facts), {"a", "c", "e", "m", "o", "s"})
        self.assertEqual(reassign_facts["a"], str(original_claim.assignment_uuid))
        self.assertEqual(type(reassign_facts["e"]), int)
        self.assertEqual(reassign_facts["e"], first_engagement.engagement_ordinal)
        self.assertIsInstance(reassigned.authority_reference, str)
        self.assertEqual(reassigned.authority_reference, reassign_reference)
        self.assertLessEqual(len(reassign_reference), 255)
        reassign_fact_refs = {(fact["relation"], fact["reference"]) for fact in reassign_record.facts_read}
        self.assertIn(("core_csaassignment", str(original_claim.assignment_uuid)), reassign_fact_refs)
        self.assertIn((
            "core_applicationengagement",
            f"{application.application_uuid}:{first_engagement.engagement_ordinal}",
        ), reassign_fact_refs)
        self.assertEqual(
            ApplicationEngagement.objects.filter(
                application__application_uuid=application.application_uuid,
                engagement_ordinal=reassign_facts["e"],
            ).count(),
            1,
        )
        reassign_configuration = LOGovernanceConfigurationVersion.objects.get(
            version_uuid=reassign_facts["c"],
        )
        self.assertEqual(gateway_version_status("default", reassign_configuration, self.now), "EFFECTIVE")
        self.assertEqual(
            "CQ_INACTIVE" if reassign_configuration.cq_active is not True else "SOLE_COORDINATOR",
            "CQ_INACTIVE",
        )
        reassign_provider_calls = len(provider.requests)
        reassign_decisions = AuthorityDecision.objects.count()
        reassign_receipts = OrganismCommandReceipt.objects.count()
        reassign_replay = service.reassign_application(reassign_command)
        self.assertEqual(reassign_replay, reassigned)
        self.assertEqual(reassign_replay.authority_reference, reassign_reference)
        self.assertIsInstance(reassign_replay.authority_reference, str)
        self.assertEqual(len(provider.requests), reassign_provider_calls)
        self.assertEqual(AuthorityDecision.objects.count(), reassign_decisions)
        self.assertEqual(OrganismCommandReceipt.objects.count(), reassign_receipts)
        reassign_record.refresh_from_db()
        self.assertEqual(reassign_record.authority_reference.encode("utf-8"), reassign_reference.encode("utf-8"))
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
        self._capture_database_now()
        decide_command = DecideApplicationCommand(
            credential=second_coordinator.credential,
            application_uuid=application.application_uuid,
            request_reference=f"aa-r1:decide-after-reassign:{self.tag}",
            idempotency_key=f"aa-r1:decide-after-reassign:key:{self.tag}",
            occurred_at=self.now,
            effective_at=later + timedelta(minutes=2),
            received_at=self.now,
            outcome="DECLINE",
        )
        decided = service.decide_application(decide_command)
        decide_record = AuthorityDecision.objects.get(decision_reference=decided.authority_decision_reference)
        decide_reference = decide_record.authority_reference
        decide_facts = json.loads(decide_reference[len("s015a1:"):])
        self.assertEqual((decide_record.outcome, decide_record.refusal_code), ("QUALIFIED", None))
        self.assertEqual(decide_record.provider_version, "R5.0")
        self.assertEqual(set(decide_facts), {"a", "c", "e", "m", "o", "s"})
        self.assertEqual(decide_facts["a"], str(replacement.assignment_uuid))
        self.assertEqual(type(decide_facts["e"]), int)
        self.assertEqual(decide_facts["e"], second_engagement.engagement_ordinal)
        self.assertIsInstance(decided.authority_reference, str)
        self.assertEqual(decided.authority_reference, decide_reference)
        self.assertLessEqual(len(decide_reference), 255)
        decide_fact_refs = {(fact["relation"], fact["reference"]) for fact in decide_record.facts_read}
        self.assertIn(("core_csaassignment", str(replacement.assignment_uuid)), decide_fact_refs)
        self.assertIn((
            "core_applicationengagement",
            f"{application.application_uuid}:{second_engagement.engagement_ordinal}",
        ), decide_fact_refs)
        resolved_decide_engagement = ApplicationEngagement.objects.get(
            application__application_uuid=application.application_uuid,
            engagement_ordinal=decide_facts["e"],
        )
        self.assertEqual(resolved_decide_engagement.engagement_reference, decide_facts["a"])
        decide_configuration = LOGovernanceConfigurationVersion.objects.get(
            version_uuid=decide_facts["c"],
        )
        self.assertEqual(gateway_version_status("default", decide_configuration, self.now), "EFFECTIVE")
        self.assertEqual(
            "CQ_INACTIVE" if decide_configuration.cq_active is not True else "SOLE_COORDINATOR",
            "CQ_INACTIVE",
        )
        decide_provider_calls = len(provider.requests)
        decide_decisions = AuthorityDecision.objects.count()
        decide_receipts = OrganismCommandReceipt.objects.count()
        decide_replay = service.decide_application(decide_command)
        self.assertEqual(decide_replay, decided)
        self.assertEqual(decide_replay.authority_reference, decide_reference)
        self.assertIsInstance(decide_replay.authority_reference, str)
        self.assertEqual(len(provider.requests), decide_provider_calls)
        self.assertEqual(AuthorityDecision.objects.count(), decide_decisions)
        self.assertEqual(OrganismCommandReceipt.objects.count(), decide_receipts)

        def acquire_register_lock(cursor):
            cursor.execute(
                "SELECT public.s015_lock_organisms(ARRAY[%s]::uuid[])",
                [self.organism_uuid],
            )

        locked_replay = _run_with_external_locks(
            self,
            acquire_register_lock,
            lambda _holder_pid, _start_worker, run_bounded: run_bounded(
                lambda: service.decide_application(decide_command),
            ),
        )
        self.assertEqual(locked_replay, decided)
        self.assertEqual(len(provider.requests), decide_provider_calls)
        self.assertEqual(AuthorityDecision.objects.count(), decide_decisions)
        self.assertEqual(OrganismCommandReceipt.objects.count(), decide_receipts)

        decide_record.refresh_from_db()
        self.assertEqual(decide_record.authority_reference.encode("utf-8"), decide_reference.encode("utf-8"))

        self._capture_database_now()
        self._end_fixture_membership(
            second_coordinator,
            "decider-departed-after-completion",
            self.now,
        )
        departure_provider_calls = len(provider.requests)
        departure_decisions = AuthorityDecision.objects.count()
        departure_receipts = OrganismCommandReceipt.objects.count()
        departed_replay = service.decide_application(decide_command)
        self.assertEqual(departed_replay.application_uuid, decided.application_uuid)
        self.assertEqual(departed_replay.action, decided.action)
        self.assertEqual(departed_replay.recorded_at, decided.recorded_at)
        self.assertEqual(departed_replay.lineage_reference, decided.lineage_reference)
        self.assertEqual(departed_replay.authority_decision_reference, decided.authority_decision_reference)
        self.assertEqual(departed_replay.completion_due_at, decided.completion_due_at)
        self.assertEqual(departed_replay.completion_due_source, decided.completion_due_source)
        self.assertEqual(departed_replay.record_uuid, WithheldField("record_uuid", "PROCESSING"))
        self.assertEqual(departed_replay.authority_reference, decided.authority_reference)
        self.assertEqual(
            withheld_fields(departed_replay),
            (WithheldField("record_uuid", "PROCESSING"),),
        )
        self.assertEqual(len(provider.requests), departure_provider_calls)
        self.assertEqual(AuthorityDecision.objects.count(), departure_decisions)
        self.assertEqual(OrganismCommandReceipt.objects.count(), departure_receipts)

    def test_w_d1d_and_w_d1f_historical_reference_forms_coexist_without_rewriting(self):
        """W-D1d/W-D1f: R2, interim R3 and D1 records pass the installed guards side by side."""
        coordinator = Identity.objects.select_related("credential").get(pk=self.ids["identity"])
        for version in ("R2.0", "R3.0", "R4.0", "R5.0"):
            delegate = ProductionApplicationAuthorityProvider(
                birthing_source=TestFixtureBirthingFactSource(self.organism_uuid),
                registration_source=TestFixtureGovernedRegistrationFactSource(),
            )
            provider = (
                delegate
                if version == "R5.0"
                else _HistoricalReferenceAuthorityProvider(delegate, version)
            )
            service = self._service(authority_provider=provider)
            with transaction.atomic(), connection.cursor() as cursor:
                applicant_pk = seed_identity(
                    cursor,
                    f"reference-forms-applicant-{version}-{self.tag}",
                )
            applicant = Identity.objects.select_related("credential").get(pk=applicant_pk)
            self._add_fixture_access_history(applicant, f"reference-forms-{version}")
            submission = self._submit(
                service,
                f"reference-forms-{version}",
                applicant=applicant,
            )
            application = Application.objects.get(application_uuid=submission.application_uuid)
            self._capture_database_now()
            service.claim_application(ClaimApplicationCommand(
                credential=coordinator.credential,
                application_uuid=application.application_uuid,
                request_reference=f"aa-r1:reference-forms-claim:{version}:{self.tag}",
                idempotency_key=f"aa-r1:reference-forms-claim:key:{version}:{self.tag}",
                occurred_at=self.now,
                effective_at=self.now,
                received_at=self.now,
            ))
            first_assignment = CSAAssignment.objects.get(application=application)

            self._capture_database_now()
            reassign_at = self.now + timedelta(minutes=1)
            reassign_command = ReassignApplicationCommand(
                credential=coordinator.credential,
                application_uuid=application.application_uuid,
                request_reference=f"aa-r1:reference-forms-reassign:{version}:{self.tag}",
                idempotency_key=f"aa-r1:reference-forms-reassign:key:{version}:{self.tag}",
                occurred_at=self.now,
                effective_at=reassign_at,
                received_at=self.now,
            )
            reassigned = service.reassign_application(reassign_command)
            ending = CSAAssignmentEnd.objects.get(assignment=first_assignment)
            self.assertEqual(
                service.application_status(application, ending.effective_at, ending.recorded_at),
                "UNABLE",
            )
            reassign_record = AuthorityDecision.objects.get(
                decision_reference=reassigned.authority_decision_reference,
            )
            reassign_reference = reassign_record.authority_reference
            self.assertEqual(reassign_record.provider_version, version)
            self.assertEqual(reassign_record.outcome, "QUALIFIED")
            self.assertEqual(reassigned.authority_reference, (
                reassign_reference if version == "R5.0"
                else WithheldField("authority_reference", "SUBJECT")
            ))
            self.assertEqual(service.reassign_application(reassign_command), reassigned)
            self.assertEqual(service.reassign_application(reassign_command).authority_reference, (
                reassign_reference if version == "R5.0"
                else WithheldField("authority_reference", "SUBJECT")
            ))
            reassign_record.refresh_from_db()
            self.assertEqual(reassign_record.authority_reference.encode("utf-8"), reassign_reference.encode("utf-8"))
            if version != "R5.0":
                reassign_facts = json.loads(reassign_reference[len("s015a1:"):])
                self.assertIsInstance(reassign_facts["a"], str)
                self.assertTrue(reassign_facts["a"].isdecimal())
                self.assertIsInstance(reassign_facts["e"], str)
                self.assertTrue(reassign_facts["e"].isdecimal())

            self._capture_database_now()
            decide_command = DecideApplicationCommand(
                credential=coordinator.credential,
                application_uuid=application.application_uuid,
                request_reference=f"aa-r1:reference-forms-decide:{version}:{self.tag}",
                idempotency_key=f"aa-r1:reference-forms-decide:key:{version}:{self.tag}",
                occurred_at=self.now,
                effective_at=reassign_at + timedelta(minutes=1),
                received_at=self.now,
                outcome="DECLINE",
            )
            decided = service.decide_application(decide_command)
            decide_record = AuthorityDecision.objects.get(
                decision_reference=decided.authority_decision_reference,
            )
            decide_reference = decide_record.authority_reference
            self.assertEqual(decide_record.provider_version, version)
            self.assertEqual(decide_record.outcome, "QUALIFIED")
            expected = (
                decide_reference if version == "R5.0"
                else WithheldField("authority_reference", "SUBJECT")
            )
            self.assertEqual(decided.authority_reference, expected)
            self.assertEqual(service.decide_application(decide_command).authority_reference, expected)
            decide_record.refresh_from_db()
            self.assertEqual(decide_record.authority_reference.encode("utf-8"), decide_reference.encode("utf-8"))
            if version != "R5.0":
                decide_facts = json.loads(decide_reference[len("s015a1:"):])
                self.assertIsInstance(decide_facts["a"], str)
                self.assertTrue(decide_facts["a"].isdecimal())
                self.assertIsInstance(decide_facts["e"], str)
                self.assertTrue(decide_facts["e"].isdecimal())

        self.assertEqual(
            AuthorityDecision.objects.filter(
                action__in={
                    OrganismAction.DECIDE_APPLICATION.value,
                    OrganismAction.REASSIGN_APPLICATION.value,
                },
                outcome="QUALIFIED",
            ).count(),
            8,
        )

    def test_w_d1g_size_refusal_is_distinct_and_discloses_denied(self):
        submission_service = self._service(
            birthing_source=TestFixtureBirthingFactSource(self.organism_uuid),
            registration_source=TestFixtureGovernedRegistrationFactSource(),
        )
        submission = self._submit(submission_service, "oversize-reference")
        application = Application.objects.get(application_uuid=submission.application_uuid)
        action = OrganismAction.REASSIGN_APPLICATION
        target = {
            "action": action.value,
            "application_uuid": application.application_uuid,
            "organism_uuid": self.organism_uuid,
        }
        production = ProductionApplicationAuthorityProvider()

        class ForcedOversizeProvider:
            provider_identity = production.provider_identity
            provider_version = production.provider_version

            def evaluate_organism_authority(self, request):
                return ProductionApplicationAuthorityProvider._qualified(
                    request,
                    {"x": "x" * 255},
                )

        class GenuineDeniedProvider:
            provider_identity = production.provider_identity
            provider_version = production.provider_version

            def evaluate_organism_authority(self, request):
                return production._refuse(
                    request,
                    S015AuthorityRefusalCode.DENIED,
                    S015AuthorityRefusalCode.DENIED,
                )

        def refused_record(provider, actor, suffix, expected_code):
            request = self._post_request(actor, action, target, suffix=suffix)
            with transaction.atomic():
                with self.assertRaises(S015AuthorityDenied) as denied:
                    S015Authority(provider=provider).qualify(request)
            self.assertEqual(denied.exception.disclosed_refusal_code, S015AuthorityRefusalCode.DENIED)
            self.assertEqual(denied.exception.refusal_code, expected_code)
            return AuthorityDecision.objects.get(
                decision_reference=denied.exception.authority_decision_reference,
            )

        current_member = Identity.objects.select_related("credential").get(pk=self.ids["identity"])
        oversize_member = refused_record(
            ForcedOversizeProvider(), current_member, "oversize-member", 
            S015AuthorityRefusalCode.C_REFERENCE_UNREPRESENTABLE,
        )
        oversize_nonmember = refused_record(
            ForcedOversizeProvider(), self.applicant, "oversize-nonmember",
            S015AuthorityRefusalCode.C_REFERENCE_UNREPRESENTABLE,
        )
        genuine_denied = refused_record(
            GenuineDeniedProvider(), current_member, "genuine-denied",
            S015AuthorityRefusalCode.DENIED,
        )

        for record in (oversize_member, oversize_nonmember):
            self.assertEqual(
                (record.outcome, record.refusal_code, record.authority_reference),
                (
                    "REFUSED",
                    S015AuthorityRefusalCode.C_REFERENCE_UNREPRESENTABLE.value,
                    's015a1:{"x":"C_REFERENCE_UNREPRESENTABLE"}',
                ),
            )
            self.assertEqual(len(record.authority_reference), 42)
        self.assertEqual((genuine_denied.outcome, genuine_denied.refusal_code), ("REFUSED", "DENIED"))
        self.assertNotEqual(oversize_member.authority_reference, genuine_denied.authority_reference)
        self.assertNotEqual(oversize_member.decision_reference, genuine_denied.decision_reference)

    def test_w_d1h_reassign_records_latest_engagement_before_service_refusal(self):
        provider = ProductionApplicationAuthorityProvider(
            birthing_source=TestFixtureBirthingFactSource(self.organism_uuid),
            registration_source=TestFixtureGovernedRegistrationFactSource(),
        )
        service = self._service(authority_provider=provider)
        submission = self._submit(service, "reassign-mismatched-engagement")
        application = Application.objects.get(application_uuid=submission.application_uuid)
        coordinator = Identity.objects.select_related("credential").get(pk=self.ids["identity"])
        self._capture_database_now()
        service.claim_application(ClaimApplicationCommand(
            credential=coordinator.credential,
            application_uuid=application.application_uuid,
            request_reference=f"aa-r1:mismatched-engagement-claim:{self.tag}",
            idempotency_key=f"aa-r1:mismatched-engagement-claim:key:{self.tag}",
            occurred_at=self.now,
            effective_at=self.now,
            received_at=self.now,
        ))
        first_assignment = CSAAssignment.objects.get(application=application)
        self._capture_database_now()
        first_engagement = ApplicationEngagement.objects.get(application=application, engagement_ordinal=1)
        provider_effective_at = self.now + timedelta(minutes=1)
        future_reassign_at = self.now + timedelta(minutes=2)
        service.reassign_application(ReassignApplicationCommand(
            credential=coordinator.credential,
            application_uuid=application.application_uuid,
            request_reference=f"aa-r1:mismatched-engagement-prior-reassign:{self.tag}",
            idempotency_key=f"aa-r1:mismatched-engagement-prior-reassign:key:{self.tag}",
            occurred_at=self.now,
            effective_at=future_reassign_at,
            received_at=self.now,
        ))
        ending = CSAAssignmentEnd.objects.get(assignment=first_assignment)
        self.assertEqual(
            service.application_status(application, ending.effective_at, ending.recorded_at),
            "UNABLE",
        )
        latest_engagement = ApplicationEngagement.objects.get(
            application=application,
            engagement_ordinal=2,
        )
        self.assertNotEqual(latest_engagement.engagement_reference, str(first_assignment.assignment_uuid))
        self._capture_database_now()
        target = {
            "action": OrganismAction.REASSIGN_APPLICATION.value,
            "application_uuid": application.application_uuid,
            "organism_uuid": self.organism_uuid,
        }
        provider_request = self._post_request(
            coordinator,
            OrganismAction.REASSIGN_APPLICATION,
            target,
            effective_at=provider_effective_at,
            suffix="mismatched-engagement-provider",
        )
        with transaction.atomic():
            S015Authority(provider=provider).qualify(provider_request)
            provider_record = AuthorityDecision.objects.get(
                request_reference=provider_request.request_reference,
            )
            facts = json.loads(provider_record.authority_reference[len("s015a1:"):])
            resolved_engagement = ApplicationEngagement.objects.get(
                application__application_uuid=application.application_uuid,
                engagement_ordinal=facts["e"],
            )
            fact_refs = {(fact["relation"], fact["reference"]) for fact in provider_record.facts_read}
            self.assertEqual(provider_record.outcome, "QUALIFIED")
            self.assertEqual(provider_record.provider_version, "R5.0")
            self.assertEqual(facts["a"], str(first_assignment.assignment_uuid))
            self.assertEqual(type(facts["e"]), int)
            self.assertEqual(facts["e"], resolved_engagement.engagement_ordinal)
            self.assertEqual(resolved_engagement.engagement_reference, facts["a"])
            self.assertNotEqual(latest_engagement.engagement_reference, facts["a"])
            self.assertEqual(
                f"{application.application_uuid}:{facts['e']}",
                f"{application.application_uuid}:{resolved_engagement.engagement_ordinal}",
            )
            self.assertIn(
                ("core_applicationengagement", f"{application.application_uuid}:{facts['e']}"),
                fact_refs,
            )

        command = ReassignApplicationCommand(
            credential=coordinator.credential,
            application_uuid=application.application_uuid,
            request_reference=f"aa-r1:mismatched-engagement-reassign:{self.tag}",
            idempotency_key=f"aa-r1:mismatched-engagement-reassign:key:{self.tag}",
            occurred_at=self.now,
            effective_at=provider_effective_at,
            received_at=self.now,
        )
        with self.assertRaises(S015ApplicationRefused):
            service.reassign_application(command)
        self.assertFalse(AuthorityDecision.objects.filter(request_reference=command.request_reference).exists())

    def test_nonmember_claim_refuses_without_waiting_on_register_lock(self):
        production_provider = ProductionApplicationAuthorityProvider(
            birthing_source=TestFixtureBirthingFactSource(self.organism_uuid),
            registration_source=TestFixtureGovernedRegistrationFactSource(),
        )
        provider = _CountingAuthorityProvider(production_provider)
        service = self._service(authority_provider=provider)
        submission = self._submit(service, "nonmember-claim-register-lock")
        application = Application.objects.get(application_uuid=submission.application_uuid)
        claim_command = ClaimApplicationCommand(
            credential=self.applicant.credential,
            application_uuid=application.application_uuid,
            request_reference=f"aa-r1:nonmember-claim:{self.tag}",
            idempotency_key=f"aa-r1:nonmember-claim:key:{self.tag}",
            occurred_at=self.now,
            effective_at=self.now,
            received_at=self.now,
        )
        provider_calls = len(provider.requests)
        decisions = AuthorityDecision.objects.count()
        receipts = OrganismCommandReceipt.objects.count()

        def acquire_register_lock(cursor):
            cursor.execute(
                "SELECT public.s015_lock_organisms(ARRAY[%s]::uuid[])",
                [self.organism_uuid],
            )

        refusal = _run_with_external_locks(
            self,
            acquire_register_lock,
            lambda _holder_pid, _start_worker, run_bounded: run_bounded(
                lambda: service.claim_application(claim_command),
            ),
        )
        self.assertIsInstance(refusal, S015ApplicationAuthorityUnavailable)
        self.assertEqual(len(provider.requests), provider_calls)
        self.assertEqual(AuthorityDecision.objects.count(), decisions)
        self.assertEqual(OrganismCommandReceipt.objects.count(), receipts)

    def test_nonmember_applicant_withdrawal_is_not_blocked_by_register_lock(self):
        from threading import Event
        from time import monotonic, sleep

        provider = _CountingAuthorityProvider(ProductionApplicationAuthorityProvider(
            birthing_source=TestFixtureBirthingFactSource(self.organism_uuid),
            registration_source=TestFixtureGovernedRegistrationFactSource(),
        ))
        service = self._service(authority_provider=provider)
        submission = self._submit(service, "nonmember-withdrawal-register-lock")
        application = Application.objects.get(application_uuid=submission.application_uuid)
        self._capture_database_now()
        command = WithdrawApplicationCommand(
            credential=self.applicant.credential,
            application_uuid=application.application_uuid,
            request_reference=f"aa-r1:nonmember-withdrawal:{self.tag}",
            idempotency_key=f"aa-r1:nonmember-withdrawal:key:{self.tag}",
            occurred_at=self.now,
            effective_at=self.now,
            received_at=self.now,
        )
        provider_calls = len(provider.requests)

        def acquire_register_lock(cursor):
            cursor.execute(
                "SELECT public.s015_lock_organisms(ARRAY[%s]::uuid[])",
                [self.organism_uuid],
            )

        release_lock = Event()

        def observe(holder_pid, start_worker, _run_bounded):
            worker = start_worker(lambda: service.withdraw_application(command))
            blocked = False
            deadline = monotonic() + 5
            while monotonic() < deadline:
                if not worker.thread.is_alive():
                    break
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_blocking_pids(%s)", [worker.pid])
                    blocked = holder_pid in cursor.fetchone()[0]
                if blocked:
                    break
                sleep(0.01)

            outcome = worker.outcome
            detail = "no worker error was reported"
            if outcome is not None and outcome[0] == "error":
                error = outcome[1]
                detail = f"{type(error).__name__}: {str(error).replace(self.tag, self.tag[:8])}"
            self.assertTrue(blocked, f"withdrawal did not wait after T-1; {detail}")
            release_lock.set()
            worker.thread.join(timeout=30)
            self.assertFalse(worker.thread.is_alive(), "withdrawal worker remained alive after lock release")
            return worker.outcome

        withdrawal = _run_with_external_locks(
            self,
            acquire_register_lock,
            observe,
            holder_release_event=release_lock,
        )
        if withdrawal is None:
            self.fail("withdrawal worker returned no outcome")
        if withdrawal[0] != "ok":
            error = withdrawal[1]
            self.fail(
                f"withdrawal worker outcome {withdrawal[0]}: "
                f"{type(error).__name__}: {str(error).replace(self.tag, self.tag[:8])}"
            )
        self.assertEqual(withdrawal[1].action, OrganismAction.WITHDRAW_APPLICATION)
        self.assertEqual(len(provider.requests), provider_calls + 1)

    def test_reassign_without_a_claim_is_refused(self):
        service = self._service(
            birthing_source=TestFixtureBirthingFactSource(self.organism_uuid),
            registration_source=TestFixtureGovernedRegistrationFactSource(),
        )
        submission = self._submit(service, "reassign-without-claim")
        application = Application.objects.get(application_uuid=submission.application_uuid)
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
        application = Application.objects.get(application_uuid=result.application_uuid)
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
        decide_command = DecideApplicationCommand(
            credential=coordinator.credential,
            application_uuid=application.application_uuid,
            request_reference=f"aa-r1:decide:{self.tag}",
            idempotency_key=f"aa-r1:decide:key:{self.tag}",
            occurred_at=self.now,
            effective_at=self.now,
            received_at=self.now,
            outcome="DECLINE",
        )
        decision = service.decide_application(decide_command)
        decision_record = AuthorityDecision.objects.get(decision_reference=decision.authority_decision_reference)
        self.assertEqual(decision_record.outcome, "QUALIFIED")
        self.assertEqual(decision_record.provider_version, "R5.0")
        self.assertEqual(decision.authority_reference, decision_record.authority_reference)
        self.assertIsInstance(decision.authority_reference, str)
        self.assertLessEqual(len(decision_record.authority_reference), 255)
        self.assertTrue(any(fact["relation"] == "core_applicationengagement" for fact in decision_record.facts_read))
        assignment_fact = next(
            fact for fact in decision_record.facts_read
            if fact["relation"] == "core_csaassignment"
        )
        claim = CSAAssignment.objects.get(application=application)
        self.assertEqual(
            assignment_fact["reference"],
            str(claim.assignment_uuid),
        )
        engagement = ApplicationEngagement.objects.get(application=application, engagement_ordinal=1)
        decision_facts = json.loads(decision_record.authority_reference[len("s015a1:"):])
        self.assertEqual(decision_facts["a"], str(claim.assignment_uuid))
        self.assertEqual(type(decision_facts["e"]), int)
        self.assertEqual(decision_facts["e"], engagement.engagement_ordinal)
        stored_reference = decision_record.authority_reference
        self.assertEqual(
            service.decide_application(decide_command).authority_reference,
            decision_record.authority_reference,
        )
        decision_record.refresh_from_db()
        self.assertEqual(decision_record.authority_reference.encode("utf-8"), stored_reference.encode("utf-8"))

    def test_pre_r3_decide_receipt_reference_is_withheld_without_rewriting(self):
        service = self._service(
            birthing_source=TestFixtureBirthingFactSource(self.organism_uuid),
            registration_source=TestFixtureGovernedRegistrationFactSource(),
        )
        submission = self._submit(service, "legacy-decide-replay")
        application = Application.objects.get(application_uuid=submission.application_uuid)
        coordinator = Identity.objects.select_related("credential").get(pk=self.ids["identity"])
        self._capture_database_now()
        service.claim_application(ClaimApplicationCommand(
            credential=coordinator.credential,
            application_uuid=application.application_uuid,
            request_reference=f"aa-r1:legacy-claim:{self.tag}",
            idempotency_key=f"aa-r1:legacy-claim:key:{self.tag}",
            occurred_at=self.now,
            effective_at=self.now,
            received_at=self.now,
        ))
        claim = CSAAssignment.objects.get(application=application)
        engagement = ApplicationEngagement.objects.get(application=application, engagement_ordinal=1)
        action = OrganismAction.DECIDE_APPLICATION
        request_reference = f"aa-r1:legacy-decide:{self.tag}"
        idempotency_key = f"aa-r1:legacy-decide:key:{self.tag}"
        command = DecideApplicationCommand(
            credential=coordinator.credential,
            application_uuid=application.application_uuid,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            occurred_at=self.now,
            effective_at=self.now,
            received_at=self.now,
            outcome="DECLINE",
        )
        fingerprint = _command_payload(
            domain=_COMMAND_DOMAIN,
            schema="s015-application-v1",
            action=action,
            actor_identity_id=coordinator.identity_id,
            actor_access_epoch=coordinator.access_epoch,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            occurred_at=self.now,
            target={
                "action": action.value,
                "application": application.application_uuid,
                "organism": self.organism_uuid,
            },
            command={"effective_at": self.now, "received_at": self.now, "outcome": "DECLINE"},
        )
        historical_reference = "s015a1:" + canonical_json_bytes({
            "a": str(claim.pk),
            "e": str(engagement.pk),
        }).decode("utf-8")
        historical_decision_reference = "s015d1:" + hashlib.sha256(
            (self.tag + ":legacy-decision").encode("utf-8"),
        ).hexdigest()
        historical_lineage_reference = "s015l1:" + hashlib.sha256(
            (self.tag + ":legacy-lineage").encode("utf-8"),
        ).hexdigest()
        receipt = OrganismCommandReceipt.objects.create(
            actor=coordinator,
            action=action.value,
            idempotency_key=idempotency_key,
            request_reference=request_reference,
            payload_fingerprint=fingerprint,
            authority_decision_reference=historical_decision_reference,
            lineage_reference=historical_lineage_reference,
            result_payload=ApplicationCommandResult(
                application_uuid=application.application_uuid,
                action=action,
                recorded_at=self.now,
                lineage_reference=historical_lineage_reference,
                authority_reference=historical_reference,
                authority_decision_reference=historical_decision_reference,
            ).receipt_payload(),
        )
        stored_reference = receipt.result_payload["authority_reference"]
        replay = service.decide_application(command)
        self.assertEqual(replay.authority_reference, WithheldField("authority_reference", "SUBJECT"))
        receipt.refresh_from_db()
        self.assertEqual(receipt.result_payload["authority_reference"].encode("utf-8"), stored_reference.encode("utf-8"))

    def test_production_provider_withdraws_only_the_applicants_own_application(self):
        service = self._service(
            birthing_source=TestFixtureBirthingFactSource(self.organism_uuid),
            registration_source=TestFixtureGovernedRegistrationFactSource(),
        )
        submission = self._submit(service, "withdraw")
        application = Application.objects.get(application_uuid=submission.application_uuid)
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
            # 0040 contributes 405 triggers; 0041 adds one, and 0042 adds twelve.
            self.assertEqual(cursor.fetchone()[0], 418)
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
