"""SB-1 founding-terms phase and governance-readiness witnesses."""

from dataclasses import asdict
from datetime import datetime, timedelta
import hashlib
import json
from typing import Callable, TypeVar
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

from django.db import IntegrityError, connection, transaction
from django.test import TransactionTestCase, override_settings

from core.models import (
    AuthorityBasis,
    AuthorityDecision,
    AuthorityPrincipal,
    ContextualRoleAssignment,
    ContextualRoleAssignmentStateEvent,
    Identity,
    IdentityTransition,
    LearnerRegistration,
    LivingOrganism,
    LivingOrganismEvent,
    LOFoundingContentVersion,
    LOGovernanceConfigurationVersion,
    OrganismCommandReceipt,
    OrganismMembership,
    OrganismMembershipTransition,
    PlatformTermsAcceptance,
    PlatformTermsVersion,
)

from core.organism_contract import (
    OrganismAction,
    QUALIFICATION_PLANE_MATRIX,
    QualificationApplicabilityUnknown,
    QualificationPlane,
    QualificationPlaneState,
    qualification_planes,
)
from tests.s015_0021_support import (
    T0,
    append_event,
    database_now,
    empty_fp,
    found_nonroot,
    fresh,
    refresh,
    seed_fixture_substitute_authority,
    seed_identity,
    register_test_learner,
    u,
)
from src.intevia.services.organism_application_authority import ProductionGovernedRegistrationFactSource
from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider
from src.intevia.services.organism_application_service import (
    EffectGatewayVersionCommand,
    GatewayDraftCommand,
    OrganismApplicationService,
)
from src.intevia.services.organism_authority import (
    S015Authority,
    S015AuthorityDenied,
    S015AuthorityRefusalCode,
    S015PreFoundingAuthorityRequest,
    S015PreFoundingAuthorityResponse,
    _founding_authority_principal,
    authority_target_fingerprint,
    canonical_json_bytes,
)
from src.intevia.services.identity_service import IdentityLifecycleService
from src.intevia.services.organism_command_support import S015CommandReplayMismatch
from src.intevia.services.organism_formation_service import (
    FoundLivingOrganismCommand,
    OrganismFormationService,
)
from src.intevia.services.organism_formation_service import (
    OrganismPhaseAdvanceService,
    PhaseAdvanceCommand,
    PhaseReadinessQuery,
    S015PhaseAdvanceTechnicalFailure,
)

NO_GOVERNANCE_CONFIGURATION = object()
GERMINATING_AT = T0 + timedelta(hours=1)
BIRTHING_AT = T0 + timedelta(hours=2)
_ServiceResult = TypeVar("_ServiceResult")


def _database_clock() -> datetime:
    with connection.cursor() as cursor:
        cursor.execute("SELECT clock_timestamp()")
        return cursor.fetchone()[0]


def _expect_service_success(
    test_case: TransactionTestCase,
    route: str,
    operation: Callable[[], _ServiceResult],
) -> _ServiceResult:
    """Return a successful result or annotate and preserve the original refusal."""
    try:
        return operation()
    except S015AuthorityDenied as exc:
        exc.add_note(
            f"{test_case.id()} {route} unexpectedly refused: "
            f"refusal_code={exc.refusal_code!r}; "
            f"disclosed_refusal_code={exc.disclosed_refusal_code!r}; message={exc}"
        )
        raise


class SB1FoundingDatabaseInvariantTests(TransactionTestCase):
    _FOUNDING_TABLES = (
        "auth_user",
        "core_identity",
        "core_authorityprincipal",
        "core_authoritybasis",
        "core_livingorganism",
        "core_platformtermsversion",
        "core_platformtermsacceptance",
        "core_livingorganismevent",
        "core_organismmembership",
        "core_organismmembershiptransition",
        "core_organismroledefinition",
        "core_contextualroleassignment",
        "core_contextualroleassignmentstateevent",
        "core_essentialcoveragerequirement",
        "core_essentialcoveragecase",
        "core_coverageassessment",
    )

    def _founding_row_counts(self):
        counts = {}
        with connection.cursor() as cursor:
            for table in self._FOUNDING_TABLES:
                cursor.execute(f"SELECT count(*) FROM public.{table}")
                counts[table] = cursor.fetchone()[0]
        return counts

    def test_content_commitment_is_nonnull_and_independently_recomputable(self):
        name = "C1 commitment witness"
        purpose = "Verify database assignment and independent recomputation."
        with transaction.atomic(), connection.cursor() as cursor:
            ids = found_nonroot(cursor, tag=f"sb1-content-commitment-{fresh()}")
            actor = Identity.objects.get(pk=ids["identity"])
            common = {
                "living_organism_id": ids["lo"],
                "name": name,
                "purpose": purpose,
                "actor_identity": actor,
                "actor_access_epoch": actor.access_epoch,
                "authority_decision_reference": "s015d1:" + ("0" * 64),
                "effective_at": T0,
                "received_at": T0,
                "content_commitment": None,
            }
            content_with_all_fields = LOFoundingContentVersion.objects.create(
                **common,
                version=1,
                vision="A clear vision.",
                mission="A clear mission.",
                values="A clear values statement.",
            )
            content_with_null_optional_fields = LOFoundingContentVersion.objects.create(
                **common,
                version=2,
                supersedes=content_with_all_fields,
                vision=None,
                mission=None,
                values=None,
            )
            cursor.execute(
                "SELECT organism_id FROM public.core_livingorganism WHERE id = %s",
                [ids["lo"]],
            )
            organism_uuid = str(cursor.fetchone()[0])

        commitment_field = LOFoundingContentVersion._meta.get_field("content_commitment")
        self.assertFalse(commitment_field.null)
        self.assertFalse(commitment_field.editable)
        contents = (
            LOFoundingContentVersion.objects.get(pk=content_with_all_fields.pk),
            LOFoundingContentVersion.objects.get(pk=content_with_null_optional_fields.pk),
        )
        for content in contents:
            self.assertIsNotNone(content.content_commitment)
        self.assertFalse(
            LOFoundingContentVersion.objects.filter(
                content_commitment__isnull=True
            ).exists()
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = 'core_lofoundingcontentversion' "
                "AND column_name = 'content_commitment'"
            )
            self.assertEqual(cursor.fetchone()[0], "NO")

        for content in contents:
            with self.subTest(version=content.version):
                preimage = json.dumps(
                    {
                        "mission": content.mission,
                        "name": content.name,
                        "organism_uuid": organism_uuid,
                        "purpose": content.purpose,
                        "values": content.values,
                        "version": content.version,
                        "vision": content.vision,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                expected = "s015r1:" + hashlib.sha256(preimage.encode("utf-8")).hexdigest()
                self.assertEqual(content.content_commitment, expected)
        with self.assertRaisesMessage(
            IntegrityError,
            "SB founding content commitment is database-assigned",
        ):
            LOFoundingContentVersion.objects.create(
                living_organism_id=contents[0].living_organism_id,
                version=3,
                supersedes=contents[1],
                name=name,
                purpose=purpose,
                actor_identity=contents[0].actor_identity,
                actor_access_epoch=contents[0].actor_access_epoch,
                authority_decision_reference=contents[0].authority_decision_reference,
                effective_at=T0,
                received_at=T0,
                content_commitment="s015r1:" + ("0" * 64),
            )
        self.assertFalse(
            LOFoundingContentVersion.objects.filter(
                living_organism_id=contents[0].living_organism_id,
                version=3,
            ).exists()
        )

    def test_missing_terms_acceptance_refuses_and_rolls_back_founding_rows(self):
        before = self._founding_row_counts()
        with self.assertRaisesMessage(
            IntegrityError,
            "SB platform terms acceptance is missing or not in force",
        ):
            with transaction.atomic(), connection.cursor() as cursor:
                found_nonroot(
                    cursor,
                    tag=f"sb1-missing-terms-{fresh()}",
                    skip={"terms_acceptance"},
                )
        self.assertEqual(self._founding_row_counts(), before)


class SB1SubstituteBoundaryTests(TransactionTestCase):
    class _FoundingApprovalProvider:
        provider_identity = "SB1 C2 test provider"
        provider_version = "C2-test"

        def __init__(self):
            self.calls = 0

        def evaluate_organism_authority(self, request):
            self.calls += 1
            return S015PreFoundingAuthorityResponse(
                **asdict(request),
                authority_reference="s015a1:" + canonical_json_bytes(
                    {"kind": "FOUNDING"}
                ).decode("utf-8"),
            )

    def _founding_request(self, ids):
        identity = Identity.objects.get(pk=ids["identity"])
        organism = LivingOrganism.objects.get(pk=ids["lo"])
        target = {"kind": "FOUNDING"}
        return S015PreFoundingAuthorityRequest(
            database_alias="default",
            actor_pk=identity.pk,
            actor_identity_id=identity.identity_id,
            actor_access_epoch=identity.access_epoch,
            action=OrganismAction.FOUND_LIVING_ORGANISM,
            command_subtype=OrganismAction.FOUND_LIVING_ORGANISM.value,
            target_fingerprint=authority_target_fingerprint(target),
            request_reference=f"request:sb1-c2:{fresh()}",
            idempotency_key=f"sb1-c2:{fresh()}",
            evaluated_at=T0,
            effective_at=T0,
            target=target,
            living_organism_id=organism.organism_id,
        )

    def test_production_registration_source_does_not_resolve_fixture_registration(self):
        source = ProductionGovernedRegistrationFactSource()
        self.assertIsNone(source.reference_at(UUID(int=1), T0, T0))

    def test_production_settings_keep_fixture_substitution_disabled(self):
        from importlib import import_module

        production_settings = import_module("intevia.settings")
        self.assertFalse(production_settings.INTEVIA_FIXTURE_SUBSTITUTES_PERMITTED)

    def test_fixture_terms_are_labelled_and_refused_outside_disposable_database(self):
        with transaction.atomic(), connection.cursor() as cursor:
            ids = found_nonroot(cursor, tag=f"sb1-fixture-terms-{fresh()}")
            cursor.execute(
                "SELECT organism_id FROM public.core_livingorganism WHERE id = %s",
                [ids["lo"]],
            )
            organism_uuid = str(cursor.fetchone()[0])
            cursor.execute(
                "SELECT v.version_label, v.is_fixture, v.published_by_authority_reference, "
                "a.terms_version_id, v.id "
                "FROM public.core_platformtermsacceptance a "
                "JOIN public.core_platformtermsversion v ON v.id = a.terms_version_id "
                "WHERE a.subject_reference = %s AND a.acceptance_kind = 'FOUNDING'",
                [f"living-organism:{organism_uuid}"],
            )
            rows = cursor.fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(
                rows[0],
                ("FIXTURE-DEV-1", True, "fixture:development", rows[0][4], rows[0][4]),
            )

        cursor = Mock()
        with patch.dict(connection.settings_dict, {"NAME": "intevia_production"}):
            with self.assertRaisesMessage(
                RuntimeError,
                "labelled founding fixtures require an attempt-owned disposable database",
            ):
                found_nonroot(cursor, tag="must-not-seed")
        cursor.execute.assert_not_called()

    def test_fixture_substitute_seeder_refuses_when_flag_false_before_writing(self):
        cursor = Mock()
        with override_settings(INTEVIA_FIXTURE_SUBSTITUTES_PERMITTED=False):
            with self.assertRaisesMessage(
                RuntimeError,
                "labelled fixture authority seeding is not permitted",
            ):
                seed_fixture_substitute_authority(cursor, "disabled")
        cursor.execute.assert_not_called()

    def test_genesis_principal_precedes_fixture_substitute(self):
        genesis = object()
        with patch("src.intevia.services.organism_authority.AuthorityPrincipal") as principal_model:
            query = principal_model.objects.using.return_value.filter.return_value
            query.order_by.return_value.first.return_value = genesis
            with override_settings(INTEVIA_FIXTURE_SUBSTITUTES_PERMITTED=True):
                self.assertIs(_founding_authority_principal("default"), genesis)
            principal_model.objects.using.return_value.filter.assert_called_once_with(
                governed_source_namespace="INTEVIA_RESERVED_GENESIS_AUTHORITY_V1",
            )

    def test_fixture_rooted_founding_refuses_without_a_principal_fact_when_disabled(self):
        self.assertFalse(
            AuthorityPrincipal.objects.filter(
                governed_source_namespace="INTEVIA_RESERVED_GENESIS_AUTHORITY_V1",
            ).exists()
        )
        tag = f"sb1-substitute-boundary-{fresh()}"
        with override_settings(INTEVIA_FIXTURE_SUBSTITUTES_PERMITTED=True):
            with transaction.atomic(), connection.cursor() as cursor:
                ids = found_nonroot(cursor, tag=tag)
                fixture_principal = seed_fixture_substitute_authority(cursor, tag)

        provider = self._FoundingApprovalProvider()
        allowed_request = self._founding_request(ids)
        with override_settings(INTEVIA_FIXTURE_SUBSTITUTES_PERMITTED=True):
            with transaction.atomic():
                S015Authority(provider=provider).qualify(allowed_request)
        allowed_decision = AuthorityDecision.objects.get(
            request_reference=allowed_request.request_reference,
        )
        principal_facts = [
            fact for fact in allowed_decision.facts_read
            if fact["relation"] == "core_authorityprincipal"
        ]
        self.assertEqual(
            [fact["reference"] for fact in principal_facts],
            [fixture_principal["principal_uuid"]],
        )

        refused_request = self._founding_request(ids)
        with override_settings(INTEVIA_FIXTURE_SUBSTITUTES_PERMITTED=False):
            with transaction.atomic():
                with self.assertRaises(S015AuthorityDenied) as raised:
                    S015Authority(provider=provider).qualify(refused_request)
                self.assertIs(
                    raised.exception.refusal_code,
                    S015AuthorityRefusalCode.FOUNDING_PRINCIPAL_ABSENT,
                )
                refused_decision = AuthorityDecision.objects.get(
                    request_reference=refused_request.request_reference,
                )
                self.assertEqual(
                    refused_decision.refusal_code,
                    S015AuthorityRefusalCode.FOUNDING_PRINCIPAL_ABSENT.value,
                )
                self.assertFalse(
                    any(
                        fact["relation"] == "core_authorityprincipal"
                        for fact in refused_decision.facts_read
                    )
                )
        self.assertEqual(provider.calls, 1)


class SB1GovernancePhaseTests(TransactionTestCase):
    def _append_lo_event(self, cursor, ids, action, evidence_reference, effective_at):
        cursor.execute(
            "SELECT e.id, e.sequence, e.resulting_state "
            "FROM public.core_livingorganism lo "
            "JOIN public.core_livingorganismevent e ON e.id = lo.head_event_id "
            "WHERE lo.id = %s",
            [ids["lo"]],
        )
        predecessor_id, sequence, state = cursor.fetchone()
        event_id = append_event(
            cursor,
            "core_livingorganismevent",
            ids["lo"],
            sequence + 1,
            action,
            state,
            state,
            None,
            ids["basis"],
            fresh(),
            pred_id=predecessor_id,
            eff=effective_at,
            occ=effective_at,
            rec=effective_at,
            extra={
                "actor_access_epoch": None,
                "actor_capacity": "STANDING",
                "actor_state": "LIVING_ORGANISM",
                "composer_state": "NA_TRANSPORT",
                "composing_rule_state": "NA_TRANSPORT",
                "enterer_identity_id": ids["identity"],
                "entry_mode": "TRANSPORT",
                "evidence_reference": evidence_reference,
            },
        )[0]
        refresh(cursor, "core_livingorganism", ids["lo"], effective_at)
        return event_id

    def _prepared_organism(
        self,
        governance_enabled=NO_GOVERNANCE_CONFIGURATION,
        *,
        circle=False,
        governance_effective_at=GERMINATING_AT,
    ):
        tag = f"sb1-gov-{fresh()}"
        with transaction.atomic(), connection.cursor() as cursor:
            ids = found_nonroot(cursor, tag=tag)
            actor = Identity.objects.get(pk=ids["identity"])
            LOFoundingContentVersion.objects.create(
                living_organism_id=ids["lo"],
                version=1,
                name="Governance witness",
                purpose="Exercise the SB-1 governance phase guard.",
                actor_identity=actor,
                actor_access_epoch=actor.access_epoch,
                authority_decision_reference="s015d1:" + ("0" * 64),
                effective_at=T0,
                received_at=T0,
                content_commitment=None,
            )
            self._append_lo_event(
                cursor,
                ids,
                "PHASE_ADVANCE",
                "formation-phase:CONCEIVING->GERMINATING",
                GERMINATING_AT,
            )
            if governance_enabled is not NO_GOVERNANCE_CONFIGURATION:
                configuration = LOGovernanceConfigurationVersion.objects.create(
            living_organism_id=ids["lo"],
            actor=actor,
            enterer=actor,
            occurred_at=governance_effective_at,
            effective_at=governance_effective_at,
            received_at=governance_effective_at,
            cq_active=False,
            governance_enabled=governance_enabled,
            inherit_portal_cq_settings=False,
            require_cq_to_change_lo_settings=False,
            require_cq_to_change_application_question=False,
                )
                self._append_lo_event(
            cursor,
            ids,
            "EFFECT_GOVERNANCE_CONFIGURATION",
            f"governance-configuration-version:{configuration.version_uuid}",
            governance_effective_at,
                )
            if circle:
                circle_uuid = u(f"sb1-gov-circle:{tag}")
                cursor.execute(
            "INSERT INTO public.core_circle "
            "(circle_uuid, state, created_at, current_state_effective_at, state_known_at, "
            "state_source_event_set_fingerprint, founding_reference, parent_organism_id) "
            "VALUES (%s, 'DORMANT', now(), %s, %s, %s, %s, %s) RETURNING id",
            (
                circle_uuid,
                T0,
                T0,
                empty_fp(cursor, "core_circle", circle_uuid),
                f"sb1-gov-circle:{tag}",
                ids["lo"],
            ),
                )
                circle_id = cursor.fetchone()[0]
                append_event(
            cursor,
            "core_circlestateevent",
            circle_id,
            1,
            "CREATE_DORMANT",
            None,
            "DORMANT",
            ids["identity"],
            ids["basis"],
            f"sb1-gov-circle-event:{tag}",
                )
                refresh(cursor, "core_circle", circle_id)
        return ids

    def _attempt_birthing(self, ids, effective_at=BIRTHING_AT):
        with transaction.atomic(), connection.cursor() as cursor:
            return self._append_lo_event(
                cursor,
                ids,
                "PHASE_ADVANCE",
                "formation-phase:GERMINATING->BIRTHING",
                effective_at,
            )

    def _current_phase(self, ids, state_at):
        with connection.cursor() as cursor:
            cursor.execute("SELECT clock_timestamp()")
            known_at = cursor.fetchone()[0]
            cursor.execute(
                "SELECT evidence_reference "
                "FROM public.core_livingorganismevent "
                "WHERE living_organism_id = %s AND action = 'PHASE_ADVANCE' "
                "AND effective_at <= %s AND received_at <= %s AND recorded_at <= %s "
                "ORDER BY effective_at DESC, occurred_at DESC, received_at DESC, "
                "recorded_at DESC, sequence DESC, event_uuid DESC LIMIT 1",
                [ids["lo"], state_at, known_at, known_at],
            )
            row = cursor.fetchone()
        return "CONCEIVING" if row is None else row[0].split(":", 1)[1].split("->", 1)[1]

    def test_unconfirmed_or_missing_governance_configuration_blocks_birthing(self):
        no_configuration = self._prepared_organism()
        before = 1
        with self.assertRaisesMessage(
            IntegrityError,
            "PHASE_CONDITION_UNMET: an effective governance configuration",
        ):
            self._attempt_birthing(no_configuration)
        self.assertEqual(
            LivingOrganismEvent.objects.filter(
                living_organism_id=no_configuration["lo"], action="PHASE_ADVANCE",
            ).count(),
            before,
        )

        unconfirmed = self._prepared_organism(None)
        with self.assertRaisesMessage(
            IntegrityError,
            "PHASE_CONDITION_UNMET: GOVERNANCE choice: The phase was not advanced and nothing changed.",
        ):
            self._attempt_birthing(unconfirmed)
        self.assertEqual(
            LivingOrganismEvent.objects.filter(
                living_organism_id=unconfirmed["lo"], action="PHASE_ADVANCE",
            ).count(),
            before,
        )

    def test_explicit_no_allows_birthing_and_is_read_at_its_coordinates(self):
        ids = self._prepared_organism(False)
        self._attempt_birthing(ids)
        self.assertEqual(self._current_phase(ids, BIRTHING_AT - timedelta(seconds=1)), "GERMINATING")
        self.assertEqual(self._current_phase(ids, BIRTHING_AT), "BIRTHING")

    def test_governance_enabled_without_governance_circle_refuses_birthing_without_writes(self):
        ids = self._prepared_organism(True)
        before = LivingOrganismEvent.objects.filter(
            living_organism_id=ids["lo"], action="PHASE_ADVANCE",
        ).count()
        with self.assertRaisesMessage(
            IntegrityError,
            "SBG_GOVERNANCE_CIRCLE_MISSING",
        ):
            self._attempt_birthing(ids)
        self.assertEqual(
            LivingOrganismEvent.objects.filter(
                living_organism_id=ids["lo"], action="PHASE_ADVANCE",
            ).count(),
            before,
        )
        self.assertEqual(self._current_phase(ids, BIRTHING_AT), "GERMINATING")

    def test_configuration_not_yet_effective_at_phase_coordinates_does_not_count(self):
        ids = self._prepared_organism(False, governance_effective_at=BIRTHING_AT + timedelta(seconds=1))
        with self.assertRaisesMessage(
            IntegrityError,
            "PHASE_CONDITION_UNMET: an effective governance configuration",
        ):
            self._attempt_birthing(ids)

    def test_generic_circle_does_not_change_governance_phase_outcomes(self):
        for value, error in (
            (NO_GOVERNANCE_CONFIGURATION, "PHASE_CONDITION_UNMET: an effective governance configuration"),
            (None, "PHASE_CONDITION_UNMET: GOVERNANCE choice"),
            (True, "SBG_GOVERNANCE_CIRCLE_MISSING"),
            (False, None),
        ):
            with self.subTest(governance_enabled=value):
                ids = self._prepared_organism(value, circle=True)
                if error is None:
                    self._attempt_birthing(ids)
                    self.assertEqual(self._current_phase(ids, BIRTHING_AT), "BIRTHING")
                else:
                    with self.assertRaisesMessage(IntegrityError, error):
                        self._attempt_birthing(ids)
                    self.assertEqual(self._current_phase(ids, BIRTHING_AT), "GERMINATING")

    def test_phase_advance_gateway_and_matrix_witnesses(self):
        phase_subtype = "TRANSITION_LIVING_ORGANISM/PHASE_ADVANCE"
        phase_planes = qualification_planes(phase_subtype)
        self.assertEqual(phase_planes, QUALIFICATION_PLANE_MATRIX[phase_subtype])
        self.assertEqual(
            {plane: binding.state for plane, binding in phase_planes.items()},
            {
                QualificationPlane.LEGAL: QualificationPlaneState.NOT_APPLICABLE_WITH_REASON,
                QualificationPlane.CONSTITUTIONAL: QualificationPlaneState.PREREQUISITE_REQUIRED,
                QualificationPlane.EXECUTION: QualificationPlaneState.PREREQUISITE_REQUIRED,
                QualificationPlane.PROPAGATION: QualificationPlaneState.FORBIDDEN,
            },
        )
        self.assertEqual(
            phase_planes[QualificationPlane.LEGAL].reason,
            "NO_LEGAL_CONCLUSION",
        )
        birthing_subtype = "TRANSITION_LIVING_ORGANISM/BIRTHING"
        birthing_planes = qualification_planes(birthing_subtype)
        self.assertEqual(birthing_planes, QUALIFICATION_PLANE_MATRIX[birthing_subtype])
        self.assertEqual(
            {plane: binding.state for plane, binding in birthing_planes.items()},
            {
                QualificationPlane.LEGAL: QualificationPlaneState.NOT_APPLICABLE_WITH_REASON,
                QualificationPlane.CONSTITUTIONAL: QualificationPlaneState.PREREQUISITE_REQUIRED,
                QualificationPlane.EXECUTION: QualificationPlaneState.PREREQUISITE_REQUIRED,
                QualificationPlane.PROPAGATION: QualificationPlaneState.FORBIDDEN,
            },
        )
        self.assertEqual(
            birthing_planes[QualificationPlane.LEGAL].reason,
            "NO_LEGAL_CONCLUSION",
        )
        with self.assertRaises(QualificationApplicabilityUnknown):
            qualification_planes("TRANSITION_LIVING_ORGANISM/UNRECOGNISED")

        ids = self._prepared_organism()
        with self.assertRaisesMessage(
            IntegrityError,
            "PHASE_CONDITION_UNMET: an effective governance configuration",
        ):
            self._attempt_birthing(ids)
        self.assertEqual(self._current_phase(ids, BIRTHING_AT), "GERMINATING")

        for action, expected_message in (
            ("BIRTHING", "BIRTHING_PREREQUISITE_MISSING: the phase to read Birthing"),
            ("UNMAPPED_SAME_STATE", "same-state event without effect action"),
        ):
            with self.subTest(action=action):
                with transaction.atomic(), connection.cursor() as cursor:
                    with self.assertRaisesMessage(
                        IntegrityError,
                        expected_message,
                    ):
                        with transaction.atomic():
                            self._append_lo_event(
                                cursor,
                                ids,
                                action,
                                (
                                    f"birthing-snapshot:{uuid4()}"
                                    if action == "BIRTHING"
                                    else f"witness:{action}"
                                ),
                                BIRTHING_AT,
                            )

        effective_no = self._prepared_organism(False)
        self.assertEqual(
            LivingOrganismEvent.objects.filter(
                living_organism_id=effective_no["lo"],
                action="EFFECT_GOVERNANCE_CONFIGURATION",
            ).count(),
            1,
        )
        self._attempt_birthing(effective_no)
        self.assertEqual(self._current_phase(effective_no, BIRTHING_AT), "BIRTHING")

    def test_founder_party_activation_is_refused_by_0031(self):
        tag = fresh()
        with transaction.atomic(), connection.cursor() as cursor:
            ids = found_nonroot(cursor, tag=f"sb1-before-admission-{tag}")
            cursor.execute(
                "SELECT event_uuid FROM core_livingorganismevent WHERE id = %s",
                [ids["lo_event"]],
            )
            founding_batch_reference = f"founding-batch:{cursor.fetchone()[0]}"

            membership_identity_id = seed_identity(cursor, f"sb1-before-member-{tag}")
            membership_uuid = u(f"sb1-before-membership:{tag}")
            cursor.execute(
                "INSERT INTO core_organismmembership "
                "(membership_uuid, state, created_at, current_state_effective_at, "
                "state_known_at, state_source_event_set_fingerprint, identity_id, "
                "living_organism_id) VALUES (%s, 'PROPOSED', now(), %s, %s, %s, %s, %s) "
                "RETURNING id",
                (
                    membership_uuid,
                    T0,
                    T0,
                    empty_fp(cursor, "core_organismmembership", membership_uuid),
                    membership_identity_id,
                    ids["lo"],
                ),
            )
            membership_id = cursor.fetchone()[0]
            open_event_id = append_event(
                cursor,
                "core_organismmembershiptransition",
                membership_id,
                1,
                "OPEN",
                None,
                "PROPOSED",
                membership_identity_id,
                ids["basis"],
                f"sb1-before-open-{tag}",
                extra={"reason_class": "APPLICATION"},
            )[0]
            refresh(cursor, "core_organismmembership", membership_id)
            with self.assertRaisesMessage(
                IntegrityError,
                "S1FU admission refused: an LO-decided admission requires the LO as actor",
            ):
                with transaction.atomic():
                    append_event(
                        cursor,
                        "core_organismmembershiptransition",
                        membership_id,
                        2,
                        "ACTIVATE",
                        "PROPOSED",
                        "ACTIVE",
                        membership_identity_id,
                        ids["basis"],
                        f"sb1-before-activate-{tag}",
                        pred_id=open_event_id,
                        extra={"reason_class": "APPLICATION"},
                    )

            other_actor_id = seed_identity(cursor, f"sb1-before-other-actor-{tag}")
            other_actor_member_id = seed_identity(cursor, f"sb1-before-other-actor-member-{tag}")
            other_member_id = seed_identity(cursor, f"sb1-before-other-member-{tag}")

            def refuse_founding_open(case, member_identity_id, actor_id, evidence_reference):
                membership_uuid = u(f"sb1-before-{case}-membership:{tag}")
                with self.assertRaisesMessage(
                    IntegrityError,
                    "S1FU founding admission refused",
                ):
                    with transaction.atomic():
                        cursor.execute(
                            "INSERT INTO core_organismmembership "
                            "(membership_uuid, state, created_at, current_state_effective_at, "
                            "state_known_at, state_source_event_set_fingerprint, identity_id, "
                            "living_organism_id) VALUES (%s, 'PROPOSED', now(), %s, %s, %s, %s, %s) "
                            "RETURNING id",
                            (
                                membership_uuid,
                                T0,
                                T0,
                                empty_fp(cursor, "core_organismmembership", membership_uuid),
                                member_identity_id,
                                ids["lo"],
                            ),
                        )
                        candidate_membership_id = cursor.fetchone()[0]
                        append_event(
                            cursor,
                            "core_organismmembershiptransition",
                            candidate_membership_id,
                            1,
                            "OPEN",
                            None,
                            "PROPOSED",
                            actor_id,
                            ids["basis"],
                            f"sb1-before-{case}-{tag}",
                            extra={
                                "reason_class": "FOUNDING",
                                "evidence_reference": evidence_reference,
                            },
                        )

            refuse_founding_open(
                "other-actor",
                other_actor_member_id,
                other_actor_id,
                founding_batch_reference,
            )
            refuse_founding_open(
                "other-membership",
                other_member_id,
                ids["identity"],
                founding_batch_reference,
            )
            refuse_founding_open(
                "forged-reference",
                other_member_id,
                ids["identity"],
                f"founding-batch:{u(f'forged:{tag}')}",
            )

        with transaction.atomic(), connection.cursor() as cursor:
            outside_member_identity_id = seed_identity(
                cursor,
                f"sb1-before-outside-member-{tag}",
            )
            outside_membership_uuid = u(f"sb1-before-outside-membership:{tag}")
            with self.assertRaisesMessage(
                IntegrityError,
                "S1FU founding admission refused",
            ):
                with transaction.atomic():
                    cursor.execute(
                        "INSERT INTO core_organismmembership "
                        "(membership_uuid, state, created_at, current_state_effective_at, "
                        "state_known_at, state_source_event_set_fingerprint, identity_id, "
                        "living_organism_id) VALUES (%s, 'PROPOSED', now(), %s, %s, %s, %s, %s) "
                        "RETURNING id",
                        (
                            outside_membership_uuid,
                            T0,
                            T0,
                            empty_fp(cursor, "core_organismmembership", outside_membership_uuid),
                            outside_member_identity_id,
                            ids["lo"],
                        ),
                    )
                    outside_membership_id = cursor.fetchone()[0]
                    append_event(
                        cursor,
                        "core_organismmembershiptransition",
                        outside_membership_id,
                        1,
                        "OPEN",
                        None,
                        "PROPOSED",
                        ids["identity"],
                        ids["basis"],
                        f"sb1-before-outside-{tag}",
                        extra={
                            "reason_class": "FOUNDING",
                            "evidence_reference": founding_batch_reference,
                        },
                    )


class SB1PhaseAdvanceServiceTests(TransactionTestCase):
    def _append_lo_event(self, cursor, ids, action, evidence_reference, effective_at):
        cursor.execute(
            "SELECT e.id, e.sequence, e.resulting_state "
            "FROM public.core_livingorganism lo "
            "JOIN public.core_livingorganismevent e ON e.id = lo.head_event_id "
            "WHERE lo.id = %s",
            [ids["lo"]],
        )
        predecessor_id, sequence, state = cursor.fetchone()
        append_event(
            cursor,
            "core_livingorganismevent",
            ids["lo"],
            sequence + 1,
            action,
            state,
            state,
            None,
            ids["basis"],
            fresh(),
            pred_id=predecessor_id,
            eff=effective_at,
            occ=effective_at,
            rec=effective_at,
            extra={
                "actor_access_epoch": None,
                "actor_capacity": "STANDING",
                "actor_state": "LIVING_ORGANISM",
                "composer_state": "NA_TRANSPORT",
                "composing_rule_state": "NA_TRANSPORT",
                "enterer_identity_id": ids["identity"],
                "entry_mode": "TRANSPORT",
                "evidence_reference": evidence_reference,
            },
        )
        refresh(cursor, "core_livingorganism", ids["lo"], effective_at)

    def _prepared_organism(
        self,
        *,
        content=True,
        germinating=False,
        governance=NO_GOVERNANCE_CONFIGURATION,
        circle=False,
    ):
        tag = f"sb1-phase-service-{fresh()}"
        with transaction.atomic(), connection.cursor() as cursor:
            ids = found_nonroot(cursor, tag=tag)
            actor = Identity.objects.get(pk=ids["identity"])
            if content:
                LOFoundingContentVersion.objects.create(
                    living_organism_id=ids["lo"],
                    version=1,
                    name="Phase service witness",
                    purpose="Exercise the commissioned phase service.",
                    actor_identity=actor,
                    actor_access_epoch=actor.access_epoch,
                    authority_decision_reference="s015d1:" + ("0" * 64),
                    effective_at=T0,
                    received_at=T0,
                    content_commitment=None,
                )
            if germinating:
                self._append_lo_event(
                    cursor,
                    ids,
                    "PHASE_ADVANCE",
                    "formation-phase:CONCEIVING->GERMINATING",
                    GERMINATING_AT,
                )
            if governance is not NO_GOVERNANCE_CONFIGURATION:
                configuration_at = GERMINATING_AT + timedelta(minutes=1)
                configuration = LOGovernanceConfigurationVersion.objects.create(
                    living_organism_id=ids["lo"],
                    actor=actor,
                    enterer=actor,
                    occurred_at=configuration_at,
                    effective_at=configuration_at,
                    received_at=configuration_at,
                    cq_active=False,
                    governance_enabled=governance,
                    inherit_portal_cq_settings=False,
                    require_cq_to_change_lo_settings=False,
                    require_cq_to_change_application_question=False,
                )
                self._append_lo_event(
                    cursor,
                    ids,
                    "EFFECT_GOVERNANCE_CONFIGURATION",
                    f"governance-configuration-version:{configuration.version_uuid}",
                    configuration_at,
                )
            if circle:
                circle_uuid = u(f"sb1-phase-circle:{tag}")
                cursor.execute(
                    "INSERT INTO public.core_circle "
                    "(circle_uuid, state, created_at, current_state_effective_at, state_known_at, "
                    "state_source_event_set_fingerprint, founding_reference, parent_organism_id) "
                    "VALUES (%s, 'DORMANT', now(), %s, %s, %s, %s, %s) RETURNING id",
                    (
                        circle_uuid,
                        T0,
                        T0,
                        empty_fp(cursor, "core_circle", circle_uuid),
                        f"sb1-phase-circle-fixture:{tag}",
                        ids["lo"],
                    ),
                )
                circle_id = cursor.fetchone()[0]
                append_event(
                    cursor,
                    "core_circlestateevent",
                    circle_id,
                    1,
                    "CREATE_DORMANT",
                    None,
                    "DORMANT",
                    ids["identity"],
                    ids["basis"],
                    f"sb1-phase-circle-event:{tag}",
                )
                refresh(cursor, "core_circle", circle_id)
        actor = Identity.objects.select_related("credential").get(pk=ids["identity"])
        organism = LivingOrganism.objects.get(pk=ids["lo"])
        return ids, actor, organism

    def _service(self, received_at=None):
        return OrganismPhaseAdvanceService(
            clock=_database_clock if received_at is None else lambda: received_at,
        )

    @staticmethod
    def _command(actor, organism, tag, from_phase, to_phase, effective_at):
        return PhaseAdvanceCommand(
            credential=actor.credential,
            living_organism_uuid=organism.organism_id,
            from_phase=from_phase,
            to_phase=to_phase,
            request_reference=f"sb1:phase:{tag}",
            idempotency_key=f"sb1:phase:key:{tag}",
            effective_at=effective_at,
        )

    @staticmethod
    def _phase_at(organism, state_at, known_at):
        event = LivingOrganismEvent.objects.filter(
            living_organism=organism,
            action="PHASE_ADVANCE",
            effective_at__lte=state_at,
            received_at__lte=known_at,
            recorded_at__lte=known_at,
        ).order_by(
            "-effective_at", "-occurred_at", "-received_at", "-recorded_at", "-sequence", "-event_uuid",
        ).first()
        return (
            "CONCEIVING"
            if event is None
            else event.evidence_reference.rsplit(":", 1)[1].split("->")[1]
        )

    def _add_second_member_fixture(self, ids, tag):
        with transaction.atomic(), connection.cursor() as cursor:
            identity_id = seed_identity(cursor, f"sb1-phase-second-member-fixture-{tag}")
            membership_uuid = u(f"sb1-phase-second-membership:{tag}")
            cursor.execute(
                "INSERT INTO public.core_organismmembership "
                "(membership_uuid, state, created_at, current_state_effective_at, state_known_at, "
                "state_source_event_set_fingerprint, identity_id, living_organism_id) "
                "VALUES (%s, 'ACTIVE', now(), %s, %s, %s, %s, %s) RETURNING id",
                (
                    membership_uuid,
                    T0,
                    T0,
                    empty_fp(cursor, "core_organismmembership", membership_uuid),
                    identity_id,
                    ids["lo"],
                ),
            )
            membership_id = cursor.fetchone()[0]
            append_event(
                cursor,
                "core_organismmembershiptransition",
                membership_id,
                1,
                "JOIN",
                None,
                "ACTIVE",
                identity_id,
                ids["basis"],
                f"sb1-phase-second-member-fixture:{tag}",
                extra={"reason_class": "FOUNDING"},
            )
            refresh(cursor, "core_organismmembership", membership_id)

    def test_phase_advance_writes_decision_receipt_replay_and_both_coordinates(self):
        ids, actor, organism = self._prepared_organism()
        received_at = _database_clock()
        effective_at = received_at
        service = self._service(received_at)
        query = PhaseReadinessQuery(
            living_organism_uuid=organism.organism_id,
            from_phase="CONCEIVING",
            to_phase="GERMINATING",
            effective_at=effective_at,
            evaluated_at=received_at,
        )
        phase_count_before = LivingOrganismEvent.objects.filter(
            living_organism=organism,
            action="PHASE_ADVANCE",
        ).count()
        decision_count_before = AuthorityDecision.objects.count()
        receipt_count_before = OrganismCommandReceipt.objects.count()

        readiness = service.assess_readiness(query)
        self.assertTrue(readiness.ready)
        self.assertEqual(readiness.current_phase, "CONCEIVING")
        self.assertEqual(
            LivingOrganismEvent.objects.filter(living_organism=organism, action="PHASE_ADVANCE").count(),
            phase_count_before,
        )
        self.assertEqual(AuthorityDecision.objects.count(), decision_count_before)
        self.assertEqual(OrganismCommandReceipt.objects.count(), receipt_count_before)

        command = self._command(
            actor,
            organism,
            f"{ids['lo']}-conceiving-germinating",
            "CONCEIVING",
            "GERMINATING",
            effective_at,
        )
        result = _expect_service_success(
            self,
            "CONCEIVING->GERMINATING PHASE_ADVANCE",
            lambda: service.advance_phase(command),
        )
        event = LivingOrganismEvent.objects.get(event_uuid=result.event_uuid)
        decision = AuthorityDecision.objects.get(decision_reference=result.authority_decision_reference)
        receipt = OrganismCommandReceipt.objects.get(
            actor=actor,
            action=OrganismAction.TRANSITION_LIVING_ORGANISM.value,
            idempotency_key=command.idempotency_key,
        )
        assignment = ContextualRoleAssignment.objects.get(
            membership__identity=actor,
            role_definition__code="COORDINATOR",
        )
        reference_facts = json.loads(result.authority_reference.removeprefix("s015a1:"))
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT actor_id, actor_access_epoch, actor_state, actor_capacity, enterer_identity_id, entry_mode "
                "FROM public.core_livingorganismevent WHERE id = %s",
                [event.pk],
            )
            actor_id, actor_access_epoch, actor_state, actor_capacity, enterer_identity_id, entry_mode = cursor.fetchone()

        self.assertEqual(event.action, "PHASE_ADVANCE")
        self.assertEqual((event.prior_state, event.resulting_state), ("ACTIVE", "ACTIVE"))
        self.assertIsNone(actor_id)
        self.assertEqual(actor_state, "LIVING_ORGANISM")
        self.assertEqual(actor_capacity, "STANDING")
        self.assertIsNone(actor_access_epoch)
        self.assertEqual(enterer_identity_id, actor.pk)
        self.assertEqual(entry_mode, "TRANSPORT")
        self.assertEqual(event.authority_basis_id, organism.founding_authority_basis_id)
        self.assertEqual(event.authority_decision_reference, result.authority_decision_reference)
        self.assertEqual(event.evidence_reference, "formation-phase:CONCEIVING->GERMINATING")
        self.assertIsNone(event.temporal_basis_kind)
        self.assertIsNone(event.temporal_basis_reference)
        self.assertEqual(decision.provider_version, "R8.0")
        self.assertLessEqual(len(result.authority_reference), 255)
        self.assertEqual(
            decision.command_subtype,
            "TRANSITION_LIVING_ORGANISM/PHASE_ADVANCE",
        )
        self.assertEqual(
            reference_facts,
            {
                "c": "-",
                "f": "FORMATION_RULES:PLATFORM_DEFAULT@v1",
                "kind": "PHASE_ADVANCE",
                "o": str(assignment.assignment_uuid),
                "p": "CONCEIVING->GERMINATING",
            },
        )
        self.assertEqual(decision.authority_reference, result.authority_reference)
        self.assertEqual(receipt.result_payload["event_uuid"], str(result.event_uuid))
        self.assertEqual(
            LivingOrganismEvent.objects.filter(living_organism=organism, action="PHASE_ADVANCE").count(),
            phase_count_before + 1,
        )

        known_before = min(event.received_at, event.recorded_at) - timedelta(microseconds=1)
        known_after = _database_clock()
        self.assertEqual(
            self._phase_at(organism, effective_at - timedelta(microseconds=1), known_after),
            "CONCEIVING",
        )
        self.assertEqual(self._phase_at(organism, effective_at, known_before), "CONCEIVING")
        self.assertEqual(self._phase_at(organism, effective_at, known_after), "GERMINATING")
        replay = _expect_service_success(
            self,
            "PHASE_ADVANCE replay",
            lambda: service.advance_phase(command),
        )
        self.assertEqual(replay, result)
        with self.assertRaises(S015CommandReplayMismatch):
            service.advance_phase(self._command(
                actor,
                organism,
                f"{ids['lo']}-conceiving-germinating",
                "CONCEIVING",
                "BIRTHING",
                effective_at,
            ))

    def test_out_of_order_and_missing_content_refuse_without_phase_events(self):
        ids, actor, organism = self._prepared_organism(content=False)
        service = self._service()
        before = LivingOrganismEvent.objects.filter(living_organism=organism).count()

        effective_at = _database_clock() - timedelta(minutes=1)
        with self.assertRaises(S015AuthorityDenied) as out_of_order:
            service.advance_phase(self._command(
                actor, organism, f"{ids['lo']}-out-of-order", "GERMINATING", "BIRTHING", effective_at,
            ))
        self.assertIs(out_of_order.exception.refusal_code, S015AuthorityRefusalCode.PHASE_OUT_OF_ORDER)
        self.assertEqual(
            str(out_of_order.exception),
            "The phase was not advanced and nothing changed: this LO is in Conceiving, and phases "
            "advance one at a time (Conceiving → Germinating → Birthing → Birthed). "
            "Advance to Germinating first.",
        )
        self.assertEqual(LivingOrganismEvent.objects.filter(living_organism=organism).count(), before)

        effective_at = _database_clock() - timedelta(minutes=1)
        with self.assertRaises(S015AuthorityDenied) as missing_content:
            service.advance_phase(self._command(
                actor, organism, f"{ids['lo']}-missing-content", "CONCEIVING", "GERMINATING", effective_at,
            ))
        self.assertIs(missing_content.exception.refusal_code, S015AuthorityRefusalCode.PHASE_CONDITION_UNMET)
        self.assertEqual(
            str(missing_content.exception),
            "The phase was not advanced and nothing changed: moving to Germinating needs founding "
            "content (name and purpose), which is not yet established. Establish it, then advance.",
        )
        self.assertEqual(LivingOrganismEvent.objects.filter(living_organism=organism).count(), before)
        self.assertFalse(OrganismCommandReceipt.objects.filter(actor=actor).exists())

    def test_two_member_electorate_refuses_with_no_event_or_receipt(self):
        ids, actor, organism = self._prepared_organism()
        self._add_second_member_fixture(ids, str(ids["lo"]))
        service = self._service()
        command = self._command(
            actor,
            organism,
            f"{ids['lo']}-two-member-electorate",
            "CONCEIVING",
            "GERMINATING",
            _database_clock() - timedelta(minutes=1),
        )
        event_count = LivingOrganismEvent.objects.filter(living_organism=organism).count()

        with self.assertRaises(S015AuthorityDenied) as refused:
            service.advance_phase(command)
        self.assertIs(refused.exception.refusal_code, S015AuthorityRefusalCode.INV_CQ_ROUTE_REQUIRED)
        self.assertEqual(
            str(refused.exception),
            "This LO's electorate has more than one member, so this act needs the electorate's "
            "qualified decision, and no CQ route is operative in this release (S3-IL-CQ). "
            "Nothing changed. This is a known limit, not a refusal of the act; it lifts when "
            "DISCUSSION's CQ is delivered.",
        )
        self.assertEqual(LivingOrganismEvent.objects.filter(living_organism=organism).count(), event_count)
        self.assertFalse(
            OrganismCommandReceipt.objects.filter(
                actor=actor,
                idempotency_key=command.idempotency_key,
            ).exists()
        )

    def test_governance_refusals_and_no_decision_are_unchanged_by_generic_circle(self):
        cases = (
            (NO_GOVERNANCE_CONFIGURATION, S015AuthorityRefusalCode.PHASE_CONDITION_UNMET),
            (None, S015AuthorityRefusalCode.PHASE_CONDITION_UNMET),
            (False, None),
            (True, S015AuthorityRefusalCode.SBG_GOVERNANCE_CIRCLE_MISSING),
        )
        for governance, expected_code in cases:
            outcomes = []
            for circle in (False, True):
                with self.subTest(governance=governance, circle=circle):
                    ids, actor, organism = self._prepared_organism(
                        germinating=True,
                        governance=governance,
                        circle=circle,
                    )
                    service = self._service()
                    command = self._command(
                        actor,
                        organism,
                        f"{ids['lo']}-birthing",
                        "GERMINATING",
                        "BIRTHING",
                        _database_clock() - timedelta(minutes=1),
                    )
                    before = LivingOrganismEvent.objects.filter(
                        living_organism=organism,
                        action="PHASE_ADVANCE",
                    ).count()
                    if expected_code is None:
                        result = _expect_service_success(
                            self,
                            "GERMINATING->BIRTHING PHASE_ADVANCE",
                            lambda: service.advance_phase(command),
                        )
                        event = LivingOrganismEvent.objects.get(event_uuid=result.event_uuid)
                        known_at = _database_clock()
                        self.assertEqual(
                            self._phase_at(
                                organism,
                                event.effective_at - timedelta(microseconds=1),
                                known_at,
                            ),
                            "GERMINATING",
                        )
                        self.assertEqual(
                            self._phase_at(organism, event.effective_at, known_at),
                            "BIRTHING",
                        )
                        outcome = ("QUALIFIED", None, before + 1)
                    else:
                        with self.assertRaises(S015AuthorityDenied) as refused:
                            service.advance_phase(command)
                        self.assertIs(refused.exception.refusal_code, expected_code)
                        if governance is None:
                            self.assertEqual(
                                str(refused.exception),
                                "The phase was not advanced and nothing changed. Moving to Birthing "
                                "needs this LO's GOVERNANCE choice to be confirmed in its governance "
                                "configuration, and no choice is recorded yet: the offered default "
                                "is not a confirmation. Confirm Yes or No by effecting the governance "
                                "configuration, then advance.",
                            )
                        elif governance is True:
                            self.assertEqual(
                                str(refused.exception),
                                "The phase was not advanced and nothing changed. GOVERNANCE is on "
                                "for this LO, so it needs its GOVERNANCE Circle first. Create the "
                                "LO's GOVERNANCE Circle, then try again. Creating the Circle early "
                                "in formation avoids this.",
                            )
                        elif governance is NO_GOVERNANCE_CONFIGURATION:
                            self.assertEqual(
                                str(refused.exception),
                                "The phase was not advanced and nothing changed: moving to Birthing "
                                "needs an effective governance configuration, which is not yet "
                                "established. Establish it, then advance.",
                            )
                        known_at = _database_clock()
                        self.assertEqual(
                            self._phase_at(organism, command.effective_at, known_at),
                            "GERMINATING",
                        )
                        outcome = (
                            "REFUSED",
                            refused.exception.refusal_code,
                            str(refused.exception),
                            LivingOrganismEvent.objects.filter(
                                living_organism=organism,
                                action="PHASE_ADVANCE",
                            ).count(),
                            self._phase_at(organism, command.effective_at, known_at),
                        )
                        self.assertEqual(outcome[3], before)
                    outcomes.append(outcome)
            self.assertEqual(outcomes[0], outcomes[1])

    def test_phase_service_refuses_an_open_transaction_as_a_technical_failure(self):
        ids, actor, organism = self._prepared_organism()
        command = self._command(
            actor,
            organism,
            f"{ids['lo']}-open-transaction",
            "CONCEIVING",
            "GERMINATING",
            _database_clock(),
        )
        with transaction.atomic():
            with self.assertRaises(S015PhaseAdvanceTechnicalFailure) as failed:
                self._service().advance_phase(command)
        self.assertEqual(
            str(failed.exception),
            "PHASE_ADVANCE could not be completed because of a technical problem; "
            "nothing was saved. Try again; if it persists, report it.",
        )
        self.assertFalse(hasattr(failed.exception, "refusal_code"))


class SB1FoundingServiceTests(TransactionTestCase):
    def _founding_counts(self):
        tables = (
            "core_identityresolution",
            "core_identitytransition",
            "core_learnerregistration",
            *SB1FoundingDatabaseInvariantTests._FOUNDING_TABLES,
        )
        counts = {}
        with connection.cursor() as cursor:
            for table in tables:
                cursor.execute(f"SELECT count(*) FROM public.{table}")
                counts[table] = cursor.fetchone()[0]
        return counts

    def _prepare_founder(
        self, tag, *, with_principal=True, registered=True
    ):
        with transaction.atomic(), connection.cursor() as cursor:
            principal = (
                seed_fixture_substitute_authority(cursor, f"sb1-service-{tag}")
                if with_principal
                else None
            )
        if registered:
            founder = register_test_learner(
                username=f"sb1-service-founder-{tag}",
                password="governed-test-password",
                display_name=f"Founder {tag}",
            )
        else:
            with transaction.atomic(), connection.cursor() as cursor:
                identity_pk = seed_identity(cursor, f"sb1-service-founder-{tag}")
            founder = Identity.objects.select_related("credential").get(
                pk=identity_pk
            )
        terms_body = f"Labelled founding fixture terms for {tag}."
        terms = PlatformTermsVersion.objects.create(
            terms_version_uuid=UUID(u(f"sb1-service-terms:{tag}")),
            version_label="FIXTURE-DEV-1",
            text_commitment="s015r1:" + hashlib.sha256(terms_body.encode("utf-8")).hexdigest(),
            text_body=terms_body,
            cited_rulings=[],
            published_by_authority_reference="fixture:development",
            is_fixture=True,
            effective_from=_database_clock() - timedelta(days=1),
        )
        provider = ProductionApplicationAuthorityProvider(
            registration_source=ProductionGovernedRegistrationFactSource(),
        )
        service = OrganismFormationService(
            clock=database_now,
            authority=S015Authority(provider=provider),
        )
        registration = LearnerRegistration.objects.filter(
            identity=founder
        ).order_by("recorded_at", "pk").first()
        registration_uuid = (
            uuid4() if registration is None else registration.registration_uuid
        )
        return founder, terms, principal, service, registration_uuid

    @staticmethod
    def _command(
        founder,
        terms,
        tag,
        *,
        name="My First LO",
        accepted=True,
        purpose=None,
        effective_at=None,
    ):
        return FoundLivingOrganismCommand(
            credential=founder.credential,
            name=name,
            purpose=f"A purpose for {tag}." if purpose is None else purpose,
            vision=f"A vision for {tag}.",
            mission=f"A mission for {tag}.",
            values=f"Values for {tag}.",
            request_reference=f"sb1:found:{tag}",
            idempotency_key=f"sb1:found:key:{tag}",
            accepted_terms_version_uuid=terms.terms_version_uuid if accepted else None,
            effective_at=effective_at,
        )

    @override_settings(INTEVIA_FIXTURE_SUBSTITUTES_PERMITTED=True)
    def test_found_service_refuses_missing_registration_with_authored_message(self):
        tag = fresh()
        founder, terms, _, _, _ = self._prepare_founder(
            tag, registered=False
        )
        before = self._founding_counts()
        service = OrganismFormationService(
            clock=database_now,
            authority=S015Authority(provider=ProductionApplicationAuthorityProvider()),
        )

        with self.assertRaises(S015AuthorityDenied) as refused:
            service.found_living_organism(self._command(founder, terms, tag))

        self.assertIs(refused.exception.refusal_code, S015AuthorityRefusalCode.E_NOT_REGISTERED)
        self.assertEqual(
            str(refused.exception),
            "The LO was not founded and nothing was saved. Founding is a registered Learner's act, "
            "and no governed registration is effective for your account at this time. "
            "(Development: the setup command's account has no registration; provision through "
            "the supported path.)",
        )
        self.assertEqual(self._founding_counts(), before)

    @override_settings(INTEVIA_FIXTURE_SUBSTITUTES_PERMITTED=True)
    def test_found_service_refuses_inactive_account_with_authored_message(self):
        tag = fresh()
        founder, terms, _, service, _ = self._prepare_founder(tag)
        IdentityLifecycleService.change_access(
            identity_id=founder.identity_id,
            action=IdentityTransition.Action.DEACTIVATE,
            requesting_actor=founder,
            authority_reference=f"fixture:deactivate:{tag}",
            technical_executor="test-suite",
            evidence_reference=f"fixture:deactivate:{tag}",
            reason_category=IdentityTransition.ReasonCategory.SECURITY,
            correlation_id=uuid4(),
        )
        before = self._founding_counts()

        with self.assertRaises(S015AuthorityDenied) as refused:
            service.found_living_organism(self._command(founder, terms, tag))

        self.assertIs(refused.exception.refusal_code, S015AuthorityRefusalCode.E_IDENTITY_INACTIVE)
        self.assertEqual(
            str(refused.exception),
            "The LO was not founded and nothing was saved: your account is not active at the time "
            "of this act.",
        )
        self.assertEqual(self._founding_counts(), before)

    @override_settings(INTEVIA_FIXTURE_SUBSTITUTES_PERMITTED=True)
    def test_found_service_refuses_absent_and_unpermitted_founding_principal(self):
        absent_tag = fresh()
        absent_founder, absent_terms, _, absent_service, _ = self._prepare_founder(
            absent_tag,
            with_principal=False,
        )
        before_absent = self._founding_counts()
        with override_settings(INTEVIA_FIXTURE_SUBSTITUTES_PERMITTED=False):
            with self.assertRaises(S015AuthorityDenied) as absent:
                absent_service.found_living_organism(
                    self._command(absent_founder, absent_terms, absent_tag),
                )
        self.assertIs(absent.exception.refusal_code, S015AuthorityRefusalCode.FOUNDING_PRINCIPAL_ABSENT)
        self.assertEqual(
            str(absent.exception),
            "The LO was not founded: the platform's founding authority principal is not registered "
            "on this database (the GENESIS principal, act 1 of the bootstrap). Register it (or, in "
            "development, seed it labelled), then found.",
        )
        self.assertEqual(self._founding_counts(), before_absent)

        substitute_tag = fresh()
        founder, terms, _, service, _ = self._prepare_founder(substitute_tag)
        before_substitute = self._founding_counts()
        with override_settings(INTEVIA_FIXTURE_SUBSTITUTES_PERMITTED=False):
            with self.assertRaises(S015AuthorityDenied) as substitute:
                service.found_living_organism(self._command(founder, terms, substitute_tag))
        self.assertIs(
            substitute.exception.refusal_code,
            S015AuthorityRefusalCode.FOUNDING_PRINCIPAL_ABSENT,
        )
        self.assertEqual(
            str(substitute.exception),
            "the labelled development substitute is not permitted in this environment",
        )
        self.assertEqual(self._founding_counts(), before_substitute)

    @override_settings(INTEVIA_FIXTURE_SUBSTITUTES_PERMITTED=True)
    def test_founding_name_gateway_question_and_phase_journey_use_services(self):
        tag = fresh()
        founder, terms, _, founding_service, registration_uuid = self._prepare_founder(tag)
        registration = LearnerRegistration.objects.get(
            registration_uuid=registration_uuid
        )
        registration_known_at = max(
            registration.effective_at,
            registration.recorded_at,
        )
        founding_at = max(
            database_now(),
            registration_known_at + timedelta(microseconds=1),
        )
        name = f"Founding Journey {tag}"
        found_command = self._command(
            founder,
            terms,
            tag,
            name=name,
            effective_at=founding_at,
        )
        found_result = _expect_service_success(
            self,
            "FOUND_LIVING_ORGANISM",
            lambda: founding_service.found_living_organism(found_command),
        )
        organism = LivingOrganism.objects.get(organism_id=found_result.living_organism_uuid)
        founding_content = LOFoundingContentVersion.objects.get(
            living_organism=organism,
            version=1,
        )
        self.assertLess(registration.effective_at, founding_content.effective_at)
        self.assertLess(registration.recorded_at, founding_content.effective_at)
        self.assertLessEqual(
            founding_content.effective_at,
            founding_content.received_at,
        )
        self.assertEqual(founding_content.name, name)

        def assert_decision_and_receipt(action, idempotency_key, decision_reference):
            decision = AuthorityDecision.objects.get(decision_reference=decision_reference)
            self.assertEqual(decision.outcome, "QUALIFIED")
            receipt = OrganismCommandReceipt.objects.get(
                actor=founder,
                action=action.value,
                idempotency_key=idempotency_key,
            )
            self.assertEqual(receipt.authority_decision_reference, decision_reference)

        assert_decision_and_receipt(
            OrganismAction.FOUND_LIVING_ORGANISM,
            found_command.idempotency_key,
            found_result.authority_decision_reference,
        )

        phase_service = OrganismPhaseAdvanceService(clock=_database_clock)
        first_phase_at = max(
            founding_content.effective_at,
            founding_content.received_at,
            _database_clock(),
        )
        first_phase = PhaseAdvanceCommand(
            credential=founder.credential,
            living_organism_uuid=organism.organism_id,
            from_phase="CONCEIVING",
            to_phase="GERMINATING",
            request_reference=f"sb1:journey:phase-one:{tag}",
            idempotency_key=f"sb1:journey:phase-one:key:{tag}",
            effective_at=first_phase_at,
        )
        first_phase_result = _expect_service_success(
            self,
            "CONCEIVING->GERMINATING PHASE_ADVANCE",
            lambda: phase_service.advance_phase(first_phase),
        )
        assert_decision_and_receipt(
            OrganismAction.TRANSITION_LIVING_ORGANISM,
            first_phase.idempotency_key,
            first_phase_result.authority_decision_reference,
        )

        gateway = OrganismApplicationService(clock=_database_clock)
        governance_at = _database_clock()
        governance = _expect_service_success(
            self,
            "governance-configuration gateway draft",
            lambda: gateway.save_gateway_draft(GatewayDraftCommand(
                credential=founder.credential,
                living_organism_id=organism.organism_id,
                action=OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
                request_reference=f"sb1:journey:governance:{tag}",
                idempotency_key=f"sb1:journey:governance:key:{tag}",
                occurred_at=governance_at,
                effective_at=governance_at,
                received_at=governance_at,
                fields={
                    "cq_active": False,
                    "governance_enabled": False,
                    "inherit_portal_cq_settings": False,
                    "require_cq_to_change_lo_settings": False,
                    "require_cq_to_change_application_question": False,
                },
            )),
        )
        self.assertIs(governance.governance_enabled, False)
        governance_effect_at = _database_clock()
        governance_result = _expect_service_success(
            self,
            "governance-configuration gateway effect",
            lambda: gateway.effect_gateway_version(EffectGatewayVersionCommand(
                credential=founder.credential,
                living_organism_id=organism.organism_id,
                action=OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
                version_uuid=governance.version_uuid,
                request_reference=f"sb1:journey:governance-effect:{tag}",
                idempotency_key=f"sb1:journey:governance-effect:key:{tag}",
                occurred_at=governance_effect_at,
                effective_at=governance_effect_at,
                received_at=governance_effect_at,
            )),
        )
        assert_decision_and_receipt(
            OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
            f"sb1:journey:governance-effect:key:{tag}",
            governance_result.authority_decision_reference,
        )

        question_at = _database_clock()
        question = _expect_service_success(
            self,
            "application-question gateway draft",
            lambda: gateway.save_gateway_draft(GatewayDraftCommand(
                credential=founder.credential,
                living_organism_id=organism.organism_id,
                action=OrganismAction.EFFECT_APPLICATION_QUESTION,
                request_reference=f"sb1:journey:question:{tag}",
                idempotency_key=f"sb1:journey:question:key:{tag}",
                occurred_at=question_at,
                effective_at=question_at,
                received_at=question_at,
                fields={"answer_format": "LONG_ANSWER"},
            )),
        )
        self.assertEqual(
            question.question_text,
            f"Why do you want to join {founding_content.name}?",
        )
        question_effect_at = _database_clock()
        question_result = _expect_service_success(
            self,
            "application-question gateway effect",
            lambda: gateway.effect_gateway_version(EffectGatewayVersionCommand(
                credential=founder.credential,
                living_organism_id=organism.organism_id,
                action=OrganismAction.EFFECT_APPLICATION_QUESTION,
                version_uuid=question.version_uuid,
                request_reference=f"sb1:journey:question-effect:{tag}",
                idempotency_key=f"sb1:journey:question-effect:key:{tag}",
                occurred_at=question_effect_at,
                effective_at=question_effect_at,
                received_at=question_effect_at,
            )),
        )
        assert_decision_and_receipt(
            OrganismAction.EFFECT_APPLICATION_QUESTION,
            f"sb1:journey:question-effect:key:{tag}",
            question_result.authority_decision_reference,
        )

        final_phase_at = _database_clock()
        final_phase = PhaseAdvanceCommand(
            credential=founder.credential,
            living_organism_uuid=organism.organism_id,
            from_phase="GERMINATING",
            to_phase="BIRTHING",
            request_reference=f"sb1:journey:phase-two:{tag}",
            idempotency_key=f"sb1:journey:phase-two:key:{tag}",
            effective_at=final_phase_at,
        )
        final_phase_result = _expect_service_success(
            self,
            "GERMINATING->BIRTHING PHASE_ADVANCE",
            lambda: phase_service.advance_phase(final_phase),
        )
        assert_decision_and_receipt(
            OrganismAction.TRANSITION_LIVING_ORGANISM,
            final_phase.idempotency_key,
            final_phase_result.authority_decision_reference,
        )
        final_event = LivingOrganismEvent.objects.get(event_uuid=final_phase_result.event_uuid)
        self.assertEqual(final_event.evidence_reference, "formation-phase:GERMINATING->BIRTHING")
        self.assertEqual(final_event.authority_decision_reference, final_phase_result.authority_decision_reference)

    @override_settings(INTEVIA_FIXTURE_SUBSTITUTES_PERMITTED=True)
    def test_found_service_writes_complete_batch_and_replays_one_receipt(self):
        tag = fresh()
        founder, terms, fixture_principal, service, registration_uuid = self._prepare_founder(tag)
        command = self._command(founder, terms, tag)
        result = _expect_service_success(
            self,
            "FOUND_LIVING_ORGANISM",
            lambda: service.found_living_organism(command),
        )

        organism = LivingOrganism.objects.get(organism_id=result.living_organism_uuid)
        event = LivingOrganismEvent.objects.get(event_uuid=result.event_uuid)
        content = LOFoundingContentVersion.objects.get(living_organism=organism)
        acceptance = PlatformTermsAcceptance.objects.get(
            subject_reference=f"living-organism:{organism.organism_id}",
            acceptance_kind=PlatformTermsAcceptance.Kind.FOUNDING,
        )
        basis = AuthorityBasis.objects.get(pk=organism.founding_authority_basis_id)
        membership = OrganismMembership.objects.get(living_organism=organism)
        transitions = list(
            OrganismMembershipTransition.objects.filter(membership=membership).order_by("sequence")
        )
        assignment = ContextualRoleAssignment.objects.get(membership=membership)
        assignment_event = ContextualRoleAssignmentStateEvent.objects.get(assignment=assignment)
        receipt = OrganismCommandReceipt.objects.get(
            actor=founder,
            action=OrganismAction.FOUND_LIVING_ORGANISM.value,
            idempotency_key=command.idempotency_key,
        )
        decision_record = AuthorityDecision.objects.get(
            decision_reference=result.authority_decision_reference,
        )

        self.assertEqual(organism.state, LivingOrganism.State.ACTIVE)
        self.assertEqual(organism.slug, "my-first-lo")
        self.assertEqual(
            (content.name, content.purpose, content.vision, content.mission, content.values),
            (command.name, command.purpose, command.vision, command.mission, command.values),
        )
        self.assertIsNotNone(content.content_commitment)
        self.assertEqual(acceptance.terms_version, terms)
        self.assertEqual(acceptance.accepted_by_identity, founder)
        self.assertEqual(basis.authority_class, "FOUNDING")
        self.assertEqual(basis.authority_principal_id, fixture_principal["id"])
        self.assertEqual(basis.derivation_root_principal_id, fixture_principal["id"])
        self.assertEqual(basis.issuer, founder)
        self.assertEqual(basis.instrument_reference, f"platform-terms-version:{terms.terms_version_uuid}#founding")
        self.assertEqual(basis.evidence_reference, f"platform-terms-acceptance:{acceptance.acceptance_uuid}")
        self.assertEqual(event.action, "FOUND")
        self.assertEqual(event.prior_state, None)
        self.assertEqual(event.resulting_state, "ACTIVE")
        self.assertEqual(event.actor, founder)
        self.assertEqual(event.authority_decision_reference, result.authority_decision_reference)
        self.assertEqual([row.action for row in transitions], ["OPEN", "ACTIVATE"])
        self.assertEqual(
            [(row.prior_state, row.resulting_state, row.reason_class) for row in transitions],
            [(None, "PROPOSED", "FOUNDING"), ("PROPOSED", "ACTIVE", "FOUNDING")],
        )
        self.assertEqual(membership.state, OrganismMembership.State.ACTIVE)
        self.assertEqual(assignment.role_definition.code, "COORDINATOR")
        self.assertEqual(assignment.state, ContextualRoleAssignment.State.ACTIVE)
        self.assertEqual(assignment_event.action, "ASSIGN")
        self.assertEqual(assignment_event.resulting_state, "ACTIVE")
        self.assertEqual(decision_record.outcome, "QUALIFIED")
        self.assertEqual(decision_record.provider_version, "R8.0")
        self.assertEqual(decision_record.authority_reference, result.authority_reference)
        self.assertLessEqual(len(result.authority_reference), 255)
        maximum_reference_length = len("s015a1:") + len(canonical_json_bytes({
            "kind": "FOUNDING",
            "rule": f"platform-terms-version:{'0' * 36}#founding",
            "registration": f"learner-registration:{'0' * 36}",
            "terms": f"platform-terms-acceptance:{'0' * 36}",
        }))
        self.assertEqual(maximum_reference_length, 252)
        self.assertLessEqual(maximum_reference_length, 255)
        facts = {fact["relation"]: fact["reference"] for fact in decision_record.facts_read}
        self.assertEqual(facts["learner-registration"], f"learner-registration:{registration_uuid}")
        self.assertEqual(facts["core_authorityprincipal"], str(fixture_principal["principal_uuid"]))
        self.assertEqual(facts["core_platformtermsacceptance"], str(acceptance.acceptance_uuid))
        self.assertEqual(result.authority_reference, receipt.result_payload["authority_reference"])
        self.assertEqual(receipt.authority_decision_reference, result.authority_decision_reference)
        self.assertFalse(
            LivingOrganismEvent.objects.filter(
                living_organism=organism,
                action="PHASE_ADVANCE",
            ).exists()
        )

        with connection.cursor() as cursor:
            row_xmins = []
            for table, row_id in (
                ("core_livingorganismevent", event.pk),
                ("core_organismmembershiptransition", transitions[0].pk),
                ("core_organismmembershiptransition", transitions[1].pk),
                ("core_contextualroleassignmentstateevent", assignment_event.pk),
            ):
                cursor.execute(f"SELECT xmin::text FROM public.{table} WHERE id = %s", [row_id])
                row_xmins.append(cursor.fetchone()[0])
            cursor.execute(
                "SELECT founding_insert_xid::text FROM public.core_livingorganism WHERE id = %s",
                [organism.pk],
            )
            founding_xid = cursor.fetchone()[0]
            for table, row_id in (
                ("core_livingorganismevent", event.pk),
                ("core_organismmembershiptransition", transitions[0].pk),
                ("core_organismmembershiptransition", transitions[1].pk),
                ("core_contextualroleassignmentstateevent", assignment_event.pk),
            ):
                cursor.execute(
                    f"SELECT actor_id, actor_state, enterer_identity_id, evidence_reference "
                    f"FROM public.{table} WHERE id = %s",
                    [row_id],
                )
                actor_id, actor_state, enterer_id, evidence_reference = cursor.fetchone()
                self.assertEqual((actor_id, actor_state, enterer_id), (founder.pk, "PARTY", founder.pk))
                if table != "core_livingorganismevent":
                    self.assertEqual(evidence_reference, f"founding-batch:{event.event_uuid}")
        self.assertEqual(row_xmins, [founding_xid] * 4)

        replay = _expect_service_success(
            self,
            "FOUND_LIVING_ORGANISM replay",
            lambda: service.found_living_organism(command),
        )
        self.assertEqual(replay, result)
        with self.assertRaises(S015CommandReplayMismatch):
            service.found_living_organism(self._command(founder, terms, tag, name="Changed Name"))
        self.assertEqual(OrganismCommandReceipt.objects.filter(actor=founder).count(), 1)
        self.assertEqual(LivingOrganism.objects.count(), 1)
        self.assertEqual(LivingOrganismEvent.objects.filter(living_organism=organism).count(), 1)

    @override_settings(INTEVIA_FIXTURE_SUBSTITUTES_PERMITTED=True)
    def test_found_service_refuses_missing_name_purpose_and_duplicate_slug(self):
        tag = fresh()
        prepared = self._prepare_founder(tag)
        founder, terms, service = prepared[0], prepared[1], prepared[3]
        before_missing_name = self._founding_counts()
        with self.assertRaises(S015AuthorityDenied) as missing_name:
            service.found_living_organism(
                self._command(founder, terms, f"{tag}-no-name", name="")
            )
        self.assertIs(
            missing_name.exception.refusal_code,
            S015AuthorityRefusalCode.LO_NAME_REQUIRED,
        )
        self.assertEqual(
            str(missing_name.exception),
            "The LO was not founded and nothing was saved: a name (and a purpose) is required.",
        )
        self.assertEqual(self._founding_counts(), before_missing_name)
        before_missing_purpose = self._founding_counts()
        with self.assertRaises(S015AuthorityDenied) as missing_purpose:
            service.found_living_organism(
                self._command(founder, terms, f"{tag}-no-purpose", purpose="")
            )
        self.assertIs(
            missing_purpose.exception.refusal_code,
            S015AuthorityRefusalCode.LO_NAME_REQUIRED,
        )
        self.assertEqual(
            str(missing_purpose.exception),
            "The LO was not founded and nothing was saved: a name (and a purpose) is required.",
        )
        self.assertEqual(self._founding_counts(), before_missing_purpose)

        _expect_service_success(
            self,
            "FOUND_LIVING_ORGANISM",
            lambda: service.found_living_organism(
                self._command(founder, terms, f"{tag}-first")
            ),
        )
        before_duplicate = self._founding_counts()
        with self.assertRaises(S015AuthorityDenied) as duplicate:
            service.found_living_organism(
                self._command(founder, terms, f"{tag}-duplicate")
            )
        self.assertIs(
            duplicate.exception.refusal_code,
            S015AuthorityRefusalCode.LO_NAME_UNAVAILABLE,
        )
        self.assertEqual(
            str(duplicate.exception),
            "The LO was not founded and nothing was saved: that name is already taken by another LO. "
            "Choose a different name.",
        )
        self.assertEqual(self._founding_counts(), before_duplicate)

    @override_settings(INTEVIA_FIXTURE_SUBSTITUTES_PERMITTED=True)
    def test_found_service_refuses_missing_and_superseded_terms_without_writes(self):
        tag = fresh()
        founder, terms, *prepared = self._prepare_founder(tag)
        service = prepared[1]
        before_missing = self._founding_counts()

        with self.assertRaises(S015AuthorityDenied) as missing:
            service.found_living_organism(
                self._command(founder, terms, f"{tag}-missing", accepted=False)
            )
        self.assertIs(
            missing.exception.refusal_code,
            S015AuthorityRefusalCode.FOUNDING_TERMS_NOT_ACCEPTED,
        )
        self.assertEqual(
            str(missing.exception),
            "The LO was not founded and nothing was saved. INTEVIA records which platform-terms "
            "version every founder accepts, and no acceptance of the version in force was received. "
            "Read the terms shown and confirm them, then found again.",
        )
        self.assertEqual(self._founding_counts(), before_missing)

        newer_body = f"Superseding fixture terms for {tag}."
        PlatformTermsVersion.objects.create(
            terms_version_uuid=UUID(u(f"sb1-service-new-terms:{tag}")),
            version_label=f"FIXTURE-{tag}",
            text_commitment="s015r1:" + hashlib.sha256(newer_body.encode("utf-8")).hexdigest(),
            text_body=newer_body,
            cited_rulings=[],
            published_by_authority_reference="fixture:development",
            is_fixture=True,
            effective_from=_database_clock() - timedelta(seconds=1),
            supersedes=terms,
        )
        before_superseded = self._founding_counts()
        with self.assertRaises(S015AuthorityDenied) as superseded:
            service.found_living_organism(
                self._command(founder, terms, f"{tag}-superseded")
            )
        self.assertIs(
            superseded.exception.refusal_code,
            S015AuthorityRefusalCode.FOUNDING_TERMS_VERSION_SUPERSEDED,
        )
        self.assertEqual(
            str(superseded.exception),
            "The LO was not founded and nothing was saved. The terms version you accepted is no "
            "longer the version in force. Reload to see the current terms and confirm them.",
        )
        self.assertEqual(self._founding_counts(), before_superseded)

    @override_settings(INTEVIA_FIXTURE_SUBSTITUTES_PERMITTED=True)
    def test_found_service_refuses_fixture_terms_on_non_disposable_database_name(self):
        tag = fresh()
        founder, terms, *prepared = self._prepare_founder(tag)
        service = prepared[1]
        before = self._founding_counts()
        with patch.dict(connection.settings_dict, {"NAME": "intevia_production"}):
            with self.assertRaises(S015AuthorityDenied) as refused:
                service.found_living_organism(self._command(founder, terms, tag))
        self.assertIs(
            refused.exception.refusal_code,
            S015AuthorityRefusalCode.FOUNDING_TERMS_NOT_ACCEPTED,
        )
        self.assertEqual(self._founding_counts(), before)
