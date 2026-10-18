"""PKT-SE-K4 election identity and authority-time witnesses."""

from datetime import timedelta
from uuid import uuid4

from django.db import transaction
from django.test import TransactionTestCase

from core.models import (
    AuthorityDecision,
    ContextualRoleAssignment,
    LivingOrganismEvent,
    LOApplicationSettingsVersion,
    OrganismCommandReceipt,
    OrganismEntitlement,
    OrganismEntitlementEvent,
    OrganismEntitlementPolicyText,
    OrganismMembership,
)
from core.organism_contract import OrganismAction
from src.intevia.services.organism_application_authority import (
    ProductionApplicationAuthorityProvider,
)
from src.intevia.services.organism_authority import (
    S015Authority,
    S015PostFoundingAuthorityRequest,
    S015AuthorityRefusalCode,
    authority_target_fingerprint,
)
from src.intevia.services.organism_entitlement_service import (
    EntitlementElectionCommand,
    OrganismEntitlementService,
)
from tests.s015_0021_support import database_now
from tests.sb2_route_support import prepare_route
from tests.se_route_support import (
    TEST_POLICY_SHA256,
    TEST_POLICY_VERSION,
    ensure_test_policy_text,
)
from tests.test_se_entitlement import (
    _birthing_command,
    _current_settings_version,
    _new_gateway_version,
    _table_state,
)


STATE_MODELS = (
    LOApplicationSettingsVersion,
    LivingOrganismEvent,
    OrganismEntitlement,
    OrganismEntitlementEvent,
    OrganismCommandReceipt,
    AuthorityDecision,
)


def _state():
    return tuple(_table_state(model) for model in STATE_MODELS)


def _free_route(tag):
    route = prepare_route(tag=tag)
    ensure_test_policy_text()
    result = route.birthing_service.birth_living_organism(
        _birthing_command(route, offering_choice="FREE", suffix="sek4-free")
    )
    event = LivingOrganismEvent.objects.get(event_uuid=result.entitlement_event_uuid)
    event.refresh_from_db(fields=["recorded_at"])
    version = LOApplicationSettingsVersion.objects.get(
        version_uuid=event.evidence_reference.removeprefix(
            "application-settings-version:"
        )
    )
    version.refresh_from_db(fields=["recorded_at"])
    return route, event, version


def _election_command(route, *, occurred_at, suffix):
    return EntitlementElectionCommand(
        credential=route.founder.credential,
        living_organism_uuid=route.organism.organism_id,
        request_reference=f"sek4:{suffix}:{route.tag}",
        idempotency_key=f"sek4:{suffix}:key:{route.tag}",
        occurred_at=occurred_at,
        policy_version=TEST_POLICY_VERSION,
        policy_text_sha256=TEST_POLICY_SHA256,
    )


class _CapturingProvider:
    def __init__(self, delegate):
        self.delegate = delegate
        self.provider_identity = delegate.provider_identity
        self.provider_version = delegate.provider_version
        self.requests = []
        self.candidates = []

    def evaluate_organism_authority(self, request):
        self.requests.append(request)
        candidate = None
        if request.version_uuid is not None:
            candidate = LOApplicationSettingsVersion.objects.filter(
                version_uuid=request.version_uuid,
            ).first()
        self.candidates.append(candidate)
        return self.delegate.evaluate_organism_authority(request)


def _request(route, *, action, version, evaluated_at, effective_at=None, target_version=None):
    organism = route.organism
    membership = OrganismMembership.objects.get(
        living_organism=organism,
        identity=route.founder,
    )
    office = ContextualRoleAssignment.objects.filter(
        membership=membership,
        role_definition__code="COORDINATOR",
        state="ACTIVE",
    ).first()
    if office is None:
        raise AssertionError("the route founder has no active Coordinator office")
    role_refs = tuple(
        ContextualRoleAssignment.objects.filter(
            membership=membership,
            state="ACTIVE",
            head_state_event__isnull=False,
        )
        .order_by("pk")
        .values_list("head_state_event__lineage_reference", flat=True)
    )
    candidate_uuid = version.version_uuid if target_version is None else target_version
    target = {
        "action": action.value,
        "organism_uuid": organism.organism_id,
        "version_uuid": candidate_uuid,
    }
    request = S015PostFoundingAuthorityRequest(
        database_alias="default",
        actor_pk=route.founder.pk,
        actor_identity_id=route.founder.identity_id,
        actor_access_epoch=route.founder.access_epoch,
        action=action,
        command_subtype=action.value,
        target_fingerprint=authority_target_fingerprint(target),
        request_reference=f"sek4:provider:{uuid4().hex}",
        idempotency_key=f"sek4:provider:key:{uuid4().hex}",
        evaluated_at=evaluated_at,
        active_membership_head_reference=membership.head_transition.lineage_reference,
        contextual_role_head_references=role_refs,
        effective_at=version.effective_at if effective_at is None else effective_at,
        target=target,
        living_organism_id=organism.organism_id,
        version_uuid=version.version_uuid,
        version_kind="EFFECT",
    )
    return request, organism, membership, office


def _new_election_candidate(route, *, election_route):
    now = database_now()
    policy_version, policy_sha = ensure_test_policy_text()
    provider = route.birthing_service._authority.provider
    candidate = LOApplicationSettingsVersion.objects.create(
        living_organism=route.organism,
        actor=route.founder,
        enterer=route.founder,
        supersedes=_current_settings_version(route),
        occurred_at=now,
        effective_at=now,
        received_at=now,
        entitlement_choice="COMPLIMENTARY_BETA",
        entitlement_election_route=election_route,
        entitlement_policy_version=policy_version,
        entitlement_policy_text_sha256=policy_sha,
        entitlement_authority_source=provider.provider_identity,
        entitlement_authority_version=provider.provider_version,
    )
    candidate.refresh_from_db(fields=["recorded_at"])
    return candidate


def _assert_provider_state_unchanged(test_case, before):
    test_case.assertEqual(_state(), before)


class SEKElectionAuthorityTimeTests(TransactionTestCase):
    reset_sequences = True

    def test_w_k4_1_request_identity_receipt_time_grant_and_replay(self):
        route, _, _ = _free_route(f"k4-accepted-{uuid4().hex}")
        provider = _CapturingProvider(route.birthing_service._authority.provider)
        service = OrganismEntitlementService(
            authority=S015Authority(provider=provider),
            clock=database_now,
        )
        command = _election_command(
            route,
            occurred_at=database_now(),
            suffix="k4-accepted",
        )
        versions_before = set(
            LOApplicationSettingsVersion.objects.filter(
                living_organism=route.organism
            ).values_list("version_uuid", flat=True)
        )
        result = service.activate_beta(command)

        self.assertEqual(len(provider.requests), 1)
        request = provider.requests[0]
        self.assertEqual(request.action, OrganismAction.EFFECT_ENTITLEMENT_ELECTION)
        receipt_at = request.evaluated_at
        self.assertIsNotNone(request.version_uuid)
        candidate = provider.candidates[0]
        self.assertIsNotNone(candidate)
        if candidate is None:
            self.fail("the request-bound settings version was not present at qualification")
        new_versions = set(
            LOApplicationSettingsVersion.objects.filter(
                living_organism=route.organism
            ).values_list("version_uuid", flat=True)
        ) - versions_before
        self.assertEqual(new_versions, {request.version_uuid})
        version = LOApplicationSettingsVersion.objects.get(
            version_uuid=request.version_uuid,
            living_organism=route.organism,
            entitlement_choice="COMPLIMENTARY_BETA",
            entitlement_election_route="POST_BIRTHING",
        )
        self.assertNotIn(version.version_uuid, versions_before)
        version.refresh_from_db(fields=["recorded_at"])
        self.assertGreater(version.recorded_at, request.evaluated_at)

        event = LivingOrganismEvent.objects.get(event_uuid=result.event_uuid)
        self.assertEqual(event.action, OrganismAction.EFFECT_ENTITLEMENT_ELECTION.value)
        self.assertEqual(
            event.evidence_reference,
            f"application-settings-version:{version.version_uuid}",
        )
        self.assertEqual(event.received_at, receipt_at)
        decision = AuthorityDecision.objects.get(
            decision_reference=event.authority_decision_reference
        )
        self.assertEqual(decision.evaluated_at, receipt_at)
        self.assertEqual(decision.evaluated_at, event.received_at)
        grant = OrganismEntitlement.objects.get(entitlement_uuid=result.entitlement_uuid)
        grant_event = OrganismEntitlementEvent.objects.get(
            entitlement=grant,
            sequence=1,
            event_type="GRANTED",
        )
        self.assertEqual(grant_event.entry_mode, "COMPOSED")
        self.assertEqual(service.activate_beta(command), result)
        self.assertEqual(len(provider.requests), 1)

    def test_w_k4_3_application_settings_keeps_record_time_filter(self):
        route = prepare_route(tag=f"k4-settings-time-{uuid4().hex}")
        version = _new_gateway_version(
            route,
            action=OrganismAction.EFFECT_APPLICATION_SETTINGS,
            include_settings_payload=True,
            include_election_payload=False,
        )
        version.refresh_from_db(fields=["recorded_at"])
        self.assertIsNotNone(version.recorded_at)
        if version.recorded_at is None:
            self.fail("the settings version has no database record time")
        self.assertTrue(
            ProductionApplicationAuthorityProvider._version_complete(
                version,
                OrganismAction.EFFECT_APPLICATION_SETTINGS,
            )
        )
        evaluated_at = version.recorded_at - timedelta(microseconds=1)
        request, organism, membership, office = _request(
            route,
            action=OrganismAction.EFFECT_APPLICATION_SETTINGS,
            version=version,
            evaluated_at=evaluated_at,
        )
        before = _state()
        provider = ProductionApplicationAuthorityProvider()
        with transaction.atomic():
            result = provider._qualify_gateway_change(
                request,
                organism,
                membership,
                office,
            )
        self.assertIs(result.refusal_code, S015AuthorityRefusalCode.DENIED)
        _assert_provider_state_unchanged(self, before)

    def test_w_k4_4_a_dispatch_rejects_a_different_target_version_uuid(self):
        route = prepare_route(tag=f"k4-target-mismatch-{uuid4().hex}")
        candidate = _new_election_candidate(route, election_route="POST_BIRTHING")
        request, _, _, _ = _request(
            route,
            action=OrganismAction.EFFECT_ENTITLEMENT_ELECTION,
            version=candidate,
            evaluated_at=candidate.recorded_at + timedelta(microseconds=1),
            target_version=uuid4(),
        )
        before = _state()
        provider = ProductionApplicationAuthorityProvider()
        with transaction.atomic():
            result = provider.evaluate_organism_authority(request)
        self.assertIs(result.refusal_code, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
        _assert_provider_state_unchanged(self, before)

    def test_w_k4_4_b_candidate_for_another_organism_is_denied(self):
        route_a = prepare_route(tag=f"k4-other-request-{uuid4().hex}")
        route_b = prepare_route(tag=f"k4-other-candidate-{uuid4().hex}")
        candidate = _new_election_candidate(route_b, election_route="POST_BIRTHING")
        candidate.refresh_from_db(fields=["recorded_at"])
        request, organism, membership, office = _request(
            route_a,
            action=OrganismAction.EFFECT_ENTITLEMENT_ELECTION,
            version=candidate,
            evaluated_at=candidate.recorded_at + timedelta(microseconds=1),
        )
        self.assertEqual(candidate.living_organism_id, route_b.organism.pk)
        self.assertNotEqual(candidate.living_organism_id, route_a.organism.pk)
        before = _state()
        provider = ProductionApplicationAuthorityProvider()
        with transaction.atomic():
            result = provider._qualify_gateway_change(
                request,
                organism,
                membership,
                office,
            )
        self.assertIs(result.refusal_code, S015AuthorityRefusalCode.DENIED)
        _assert_provider_state_unchanged(self, before)

    def test_w_k4_4_c_effected_version_at_requested_cut_is_denied(self):
        route, _, _ = _free_route(f"k4-effected-{uuid4().hex}")
        service = OrganismEntitlementService(
            authority=route.birthing_service._authority,
            clock=database_now,
        )
        result = service.activate_beta(
            _election_command(
                route,
                occurred_at=database_now(),
                suffix="k4-effected",
            )
        )
        event = LivingOrganismEvent.objects.get(event_uuid=result.event_uuid)
        version = LOApplicationSettingsVersion.objects.get(
            version_uuid=event.evidence_reference.removeprefix(
                "application-settings-version:"
            )
        )
        version.refresh_from_db(fields=["recorded_at"])
        event.refresh_from_db(fields=["recorded_at"])
        evaluated_at = max(
            event.effective_at,
            event.received_at,
            event.recorded_at,
            version.recorded_at,
        ) + timedelta(microseconds=1)
        request, organism, membership, office = _request(
            route,
            action=OrganismAction.EFFECT_ENTITLEMENT_ELECTION,
            version=version,
            evaluated_at=evaluated_at,
            effective_at=evaluated_at,
        )
        before = _state()
        provider = ProductionApplicationAuthorityProvider()
        with transaction.atomic():
            refused = provider._qualify_gateway_change(
                request,
                organism,
                membership,
                office,
            )
        self.assertIs(refused.refusal_code, S015AuthorityRefusalCode.DENIED)
        _assert_provider_state_unchanged(self, before)

    def test_w_k4_4_d_birthing_route_candidate_is_denied_as_incomplete(self):
        route = prepare_route(tag=f"k4-birthing-version-{uuid4().hex}")
        candidate = _new_election_candidate(route, election_route="BIRTHING")
        candidate.refresh_from_db(fields=["recorded_at"])
        self.assertFalse(
            ProductionApplicationAuthorityProvider._version_complete(
                candidate,
                OrganismAction.EFFECT_ENTITLEMENT_ELECTION,
            )
        )
        request, organism, membership, office = _request(
            route,
            action=OrganismAction.EFFECT_ENTITLEMENT_ELECTION,
            version=candidate,
            evaluated_at=max(candidate.recorded_at, candidate.effective_at)
            + timedelta(microseconds=1),
        )
        self.assertFalse(
            LivingOrganismEvent.objects.filter(
                living_organism=route.organism,
                action=OrganismAction.EFFECT_ENTITLEMENT_ELECTION.value,
                evidence_reference=(
                    f"application-settings-version:{candidate.version_uuid}"
                ),
            ).exists()
        )
        before = _state()
        provider = ProductionApplicationAuthorityProvider()
        with transaction.atomic():
            result = provider._qualify_gateway_change(
                request,
                organism,
                membership,
                office,
            )
        self.assertIs(result.refusal_code, S015AuthorityRefusalCode.DENIED)
        _assert_provider_state_unchanged(self, before)
