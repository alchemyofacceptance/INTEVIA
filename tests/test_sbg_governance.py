import hashlib
import importlib
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone as datetime_timezone
from threading import Barrier
from types import SimpleNamespace
from uuid import UUID, uuid4, uuid5

from django.contrib.auth.models import User
from django.db import IntegrityError, close_old_connections, connection, connections, transaction
from django.db.models import Q, QuerySet
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase
from unittest.mock import Mock, patch

from core.models import (
    AuthorityDecision,
    Circle,
    CircleAdmissionDecision,
    CircleJoinRequest,
    CircleParticipation,
    CircleParticipationEnd,
    CircleStateEvent,
    CommandIntake,
    GovernedRule,
    Identity,
    LOBirthingSnapshot,
    LOGovernanceConfigurationVersion,
    LivingOrganismEvent,
    OrganismMembership,
    OrganismMembershipTransition,
    OrganismCommandReceipt,
)
from core.organism_contract import OrganismAction, QUALIFICATION_PLANE_MATRIX
from src.intevia.services.organism_application_service import (
    EffectGatewayVersionCommand,
    GatewayDraftCommand,
    S015ApplicationRefused,
)
from src.intevia.services.organism_authority import (
    S015Authority,
    S015AuthorityDenied,
    S015AuthorityRefusalCode,
    canonical_json_bytes,
    canonical_timestamp,
)
from src.intevia.services.organism_application_authority import (
    ProductionApplicationAuthorityProvider,
)
from src.intevia.services.organism_formation_service import (
    PhaseAdvanceCommand,
    S015BirthingTechnicalFailure,
)
from src.intevia.services.organism_membership_service import (
    OrganismMembershipService,
    S015MembershipRefused,
    TransitionMembershipCommand,
)
from src.intevia.services.organism_governance_service import (
    AdoptGoverningInstrumentCommand,
    CreateGovernanceCircleCommand,
    DecideGovernanceSeatCommand,
    OrganismGovernanceService,
    RequestGovernanceSeatCommand,
    SBGGovernanceRefused,
    SeatFoundingGovernanceCircleCommand,
    _compound_stage_identifiers,
    _governance_applicant_identity_uuid,
    _governance_self_decision,
    _authority_target,
)
from tests.s015_0021_support import (
    append_event,
    database_now,
    current_applied_core_head,
    current_core_leaf,
    empty_fp,
    fresh,
    migrate_to_target_with_restore,
    refresh,
)
from tests.sb2_route_support import (
    BirthingRoute,
    birth,
    add_active_elector,
    database_now,
    effect_gateway,
    prepare_route,
    route_test_now,
)
from tests.sbg_route_support import (
    SBGGovernanceRoute,
    advance_to_birthing,
    adopt_governance_arrangement,
    create_governance_circle,
    end_membership_with_participations,
    governance_arrangement_body,
    governance_route_state,
    prepare_governance_route,
    seat_governance_founder,
)


def _atomic_with_integrity_error_on_exit(
    error,
    *,
    fail_on=1,
    injection_reached=None,
):
    exit_count = 0

    @contextmanager
    def fail_at_exit(database_alias):
        nonlocal exit_count
        exit_count += 1
        with transaction.atomic(using=database_alias):
            yield
            if exit_count == fail_on:
                if injection_reached is not None:
                    injection_reached.append(True)
                raise error

    return fail_at_exit


def _expected_compound_stage_identifiers(organism_uuid, interaction_key):
    identifiers = []
    for action, key_prefix, reference_prefix in (
        (
            OrganismAction.REQUEST_GOVERNANCE_SEAT.value,
            "sbg:req:",
            "sbg:reqref:",
        ),
        (
            OrganismAction.ADMIT_CIRCLE_PARTICIPANT.value,
            "sbg:admit:",
            "sbg:admitref:",
        ),
    ):
        digest = hashlib.sha256(canonical_json_bytes({
            "organism_uuid": organism_uuid,
            "interaction_key": interaction_key,
            "action": action,
        })).hexdigest()
        identifiers.extend((key_prefix + digest, reference_prefix + digest))
    return tuple(identifiers)


class GovernanceCompoundStageKeyTests(unittest.TestCase):
    def test_compound_stage_keys_are_action_specific_and_bounded(self):
        organism_uuid = UUID("e4901a1e-5712-40c5-8c69-63c1e904a161")
        interaction_key = "sbg:one-interaction"
        expected = _expected_compound_stage_identifiers(
            organism_uuid,
            interaction_key,
        )
        self.assertEqual(
            _compound_stage_identifiers(organism_uuid, interaction_key),
            expected,
        )
        request_key, request_reference, admission_key, admission_reference = expected
        self.assertLessEqual(len(request_key), 120)
        self.assertLessEqual(len(admission_key), 120)
        self.assertLessEqual(len(request_reference), 128)
        self.assertLessEqual(len(admission_reference), 128)
        self.assertNotEqual(request_key, admission_key)


class GovernanceAuthorityTargetShapeTests(unittest.TestCase):

    def test_targets_match_provider_action_shapes(self):
        organism_uuid = UUID("e4901a1e-5712-40c5-8c69-63c1e904a161")
        rule_uuid = UUID("467d5b48-83d8-4c6a-9446-323aeb5f34ee")
        circle_uuid = UUID("6d86ff55-48ef-42c0-9953-50a2dc893958")
        credential = User(pk=1)
        founding_applicant = Identity(
            pk=1,
            credential=credential,
            identity_id=UUID("7c455830-99a1-4f0a-bec4-9357df40d0e8"),
            canonical_username="target-shape-founder",
        )
        founding_membership = OrganismMembership(pk=2, identity=founding_applicant)

        adoption = AdoptGoverningInstrumentCommand(
            credential=credential,
            living_organism_uuid=organism_uuid,
            rule_uuid=rule_uuid,
            version=1,
            body={"seats": [], "officer_roles": [], "election_schedule": None},
            effective_from=datetime(2026, 1, 1, tzinfo=datetime_timezone.utc),
            request_reference="target-shape:adopt",
            idempotency_key="target-shape:key:adopt",
        )
        adoption_target = _authority_target(
            OrganismAction.ADOPT_GOVERNING_INSTRUMENT,
            adoption.living_organism_uuid,
            {
                "rule_uuid": str(adoption.rule_uuid),
                "version": adoption.version,
                "body": adoption.body,
                "effective_from": adoption.effective_from,
            },
        )
        adoption_target["source"] = "FOUNDING_BODY_PRESCRIPTION"
        self.assertEqual(
            set(adoption_target),
            {
                "action", "organism_uuid", "rule_uuid", "version", "source",
                "body", "effective_from",
            },
        )
        self.assertEqual(
            adoption_target["action"],
            OrganismAction.ADOPT_GOVERNING_INSTRUMENT.value,
        )
        self.assertEqual(adoption_target["organism_uuid"], organism_uuid)
        self.assertEqual(adoption_target["rule_uuid"], str(rule_uuid))
        self.assertEqual(adoption_target["version"], 1)
        self.assertEqual(adoption_target["source"], "FOUNDING_BODY_PRESCRIPTION")
        self.assertEqual(adoption_target["body"], adoption.body)

        circle_creation = CreateGovernanceCircleCommand(
            credential=credential,
            living_organism_uuid=organism_uuid,
            request_reference="target-shape:create",
            idempotency_key="target-shape:key:create",
        )
        created_circle_uuid = uuid5(
            organism_uuid,
            f"{OrganismAction.CREATE_DORMANT_CIRCLE.value}:"
            f"{circle_creation.idempotency_key}",
        )
        circle_target = _authority_target(
            OrganismAction.CREATE_DORMANT_CIRCLE,
            circle_creation.living_organism_uuid,
            {
                "circle_uuid": str(created_circle_uuid),
                "kind": "GOVERNANCE",
            },
        )
        self.assertEqual(
            set(circle_target),
            {"action", "organism_uuid", "circle_uuid", "kind"},
        )
        self.assertEqual(
            circle_target["action"],
            OrganismAction.CREATE_DORMANT_CIRCLE.value,
        )
        self.assertEqual(circle_target["organism_uuid"], organism_uuid)
        self.assertEqual(circle_target["circle_uuid"], str(created_circle_uuid))
        self.assertEqual(circle_target["kind"], "GOVERNANCE")

        seating = SeatFoundingGovernanceCircleCommand(
            credential=credential,
            living_organism_uuid=organism_uuid,
            circle_uuid=circle_uuid,
            seat_rule_uuid=rule_uuid,
            seat_id="founder",
            request_reference="target-shape:seat",
            idempotency_key="target-shape:key:seat",
        )
        seating_target = _authority_target(
            OrganismAction.ADMIT_CIRCLE_PARTICIPANT,
            seating.living_organism_uuid,
            {
                "circle_uuid": str(seating.circle_uuid),
                "kind": "GOVERNANCE",
                "seat_rule_uuid": str(seating.seat_rule_uuid),
                "seat_id": seating.seat_id,
                "route": "CIRCLE_FOUNDING",
                "request_uuid": str(UUID("bf61b499-06dd-4877-857a-faaac3ad6db6")),
                "applicant_identity_uuid": _governance_applicant_identity_uuid(
                    founding_membership,
                ),
            },
        )
        self.assertEqual(
            set(seating_target),
            {
                "action", "organism_uuid", "circle_uuid", "kind",
                "seat_rule_uuid", "seat_id", "route", "request_uuid",
                "applicant_identity_uuid",
            },
        )
        self.assertEqual(
            seating_target["action"],
            OrganismAction.ADMIT_CIRCLE_PARTICIPANT.value,
        )
        self.assertEqual(seating_target["organism_uuid"], organism_uuid)
        self.assertEqual(seating_target["circle_uuid"], str(circle_uuid))
        self.assertEqual(seating_target["kind"], "GOVERNANCE")
        self.assertEqual(seating_target["seat_rule_uuid"], str(rule_uuid))
        self.assertEqual(seating_target["seat_id"], "founder")
        self.assertEqual(seating_target["route"], "CIRCLE_FOUNDING")

        constitutional_seating = DecideGovernanceSeatCommand(
            credential=credential,
            living_organism_uuid=organism_uuid,
            request_reference="target-shape:constitutional-seat",
            idempotency_key="target-shape:key:constitutional-seat",
            outcome="ACCEPT",
            request_uuid=UUID("bf61b499-06dd-4877-857a-faaac3ad6db6"),
        )
        expected_applicant_identity_uuid = "41c0c7a2-8d5f-4c20-98d4-934c41b0d30f"
        applicant_identity = Identity(
            pk=24,
            credential=User(pk=24),
            identity_id=UUID(expected_applicant_identity_uuid),
            canonical_username="target-shape-applicant",
        )
        applicant_membership = OrganismMembership(pk=25, identity=applicant_identity)
        constitutional_target = _authority_target(
            OrganismAction.ADMIT_CIRCLE_PARTICIPANT,
            constitutional_seating.living_organism_uuid,
            {
                "circle_uuid": str(circle_uuid),
                "kind": "GOVERNANCE",
                "seat_rule_uuid": str(rule_uuid),
                "seat_id": "coordinator",
                "route": "GOVERNANCE_CONSTITUTIONAL",
                "constitutional_route": "APPOINTMENT",
                "request_uuid": str(constitutional_seating.request_uuid),
                "applicant_identity_uuid": _governance_applicant_identity_uuid(
                    applicant_membership,
                ),
                "deciding_party": "office:LO_COORDINATOR",
                "outcome": constitutional_seating.outcome,
            },
        )
        self.assertEqual(
            set(constitutional_target),
            {
                "action", "organism_uuid", "circle_uuid", "kind", "seat_rule_uuid",
                "seat_id", "route", "constitutional_route", "request_uuid",
                "applicant_identity_uuid", "deciding_party", "outcome",
            },
        )
        self.assertEqual(
            constitutional_target["action"],
            OrganismAction.ADMIT_CIRCLE_PARTICIPANT.value,
        )
        self.assertEqual(constitutional_target["organism_uuid"], organism_uuid)
        self.assertEqual(constitutional_target["circle_uuid"], str(circle_uuid))
        self.assertEqual(constitutional_target["seat_rule_uuid"], str(rule_uuid))
        self.assertEqual(constitutional_target["seat_id"], "coordinator")
        self.assertEqual(constitutional_target["route"], "GOVERNANCE_CONSTITUTIONAL")
        self.assertEqual(constitutional_target["constitutional_route"], "APPOINTMENT")
        self.assertEqual(
            constitutional_target["request_uuid"],
            str(constitutional_seating.request_uuid),
        )
        self.assertEqual(
            constitutional_target["applicant_identity_uuid"],
            expected_applicant_identity_uuid,
        )
        self.assertEqual(
            constitutional_target["deciding_party"],
            "office:LO_COORDINATOR",
        )
        self.assertEqual(constitutional_target["outcome"], "ACCEPT")
    def test_seat_request_and_decision_use_matrix_keys_and_provider_shapes(self):
        self.assertIn(
            OrganismAction.ADMIT_CIRCLE_PARTICIPANT.value,
            QUALIFICATION_PLANE_MATRIX,
        )
        self.assertIn(
            OrganismAction.REQUEST_GOVERNANCE_SEAT.value,
            QUALIFICATION_PLANE_MATRIX,
        )
        organism_uuid = UUID("e4901a1e-5712-40c5-8c69-63c1e904a161")
        circle_uuid = UUID("6d86ff55-48ef-42c0-9953-50a2dc893958")
        rule_uuid = UUID("467d5b48-83d8-4c6a-9446-323aeb5f34ee")
        applicant = Identity(
            pk=17,
            credential=User(pk=17),
            identity_id=UUID("7c455830-99a1-4f0a-bec4-9357df40d0e8"),
            canonical_username="governance-applicant",
        )
        applicant_membership = OrganismMembership(pk=29, identity=applicant)
        request = RequestGovernanceSeatCommand(
            credential=applicant.credential,
            living_organism_uuid=organism_uuid,
            circle_uuid=circle_uuid,
            seat_rule_uuid=rule_uuid,
            seat_id="founder",
            request_reference="target-shape:request",
            idempotency_key="target-shape:key:request",
        )
        request_target = _authority_target(
            OrganismAction.REQUEST_GOVERNANCE_SEAT,
            request.living_organism_uuid,
            {
                "circle_uuid": str(request.circle_uuid),
                "seat_rule_uuid": str(request.seat_rule_uuid),
                "seat_id": request.seat_id,
                "applicant_identity_uuid": _governance_applicant_identity_uuid(
                    applicant_membership,
                ),
            },
        )
        self.assertEqual(
            set(request_target),
            {
                "action", "organism_uuid", "circle_uuid", "kind", "seat_rule_uuid",
                "seat_id", "applicant_identity_uuid",
            },
        )
        self.assertEqual(
            request_target["action"],
            OrganismAction.REQUEST_GOVERNANCE_SEAT.value,
        )
        self.assertEqual(
            request_target["applicant_identity_uuid"],
            str(applicant.identity_id),
        )
        self.assertEqual(
            QUALIFICATION_PLANE_MATRIX[OrganismAction.REQUEST_GOVERNANCE_SEAT.value],
            QUALIFICATION_PLANE_MATRIX[OrganismAction.SUBMIT_APPLICATION.value],
        )


    def test_constitutional_identity_fields_use_uuid_and_primary_key(self):
        applicant = Identity(
            pk=17,
            credential=User(pk=17),
            identity_id=UUID("7c455830-99a1-4f0a-bec4-9357df40d0e8"),
            canonical_username="governance-applicant",
        )
        applicant_membership = OrganismMembership(pk=29, identity=applicant)
        same_person_actor = Identity(
            pk=17,
            credential=User(pk=17),
            identity_id=applicant.identity_id,
            canonical_username="governance-applicant",
        )
        different_person_actor = Identity(
            pk=18,
            credential=User(pk=18),
            identity_id=UUID("b8ff1cb8-f84b-44da-8f2b-050b7f2256cc"),
            canonical_username="governance-coordinator",
        )

        self.assertEqual(applicant_membership.identity_id, 17)
        self.assertEqual(
            _governance_applicant_identity_uuid(applicant_membership),
            str(applicant.identity_id),
        )
        self.assertTrue(
            _governance_self_decision(applicant_membership, same_person_actor),
        )
        self.assertFalse(
            _governance_self_decision(applicant_membership, different_person_actor),
        )


class GovernanceArrangementBodyValidationTests(unittest.TestCase):
    def setUp(self):
        self.service = object.__new__(OrganismGovernanceService)

    def test_future_dated_only_seat_is_accepted_for_adoption(self):
        body = governance_arrangement_body(
            effective_from=datetime(2040, 1, 1, tzinfo=datetime_timezone.utc),
        )
        self.service._validate_arrangement_body(body)

    def test_empty_body_is_incomplete_because_it_has_no_seats(self):
        with self.assertRaises(SBGGovernanceRefused) as caught:
            self.service._validate_arrangement_body({})

        self.assertIs(
            caught.exception.refusal_code,
            S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
        )
        self.assertEqual(
            str(caught.exception),
            "No arrangement was adopted and nothing changed. The arrangement is incomplete: "
            "it has no seats. Adopt a corrected version, then try again. Every seat needs an "
            "admission route and a deciding body that matches it.",
        )

    def test_extra_top_level_field_is_unsupported(self):
        body = governance_arrangement_body(
            effective_from=datetime(2026, 1, 1, tzinfo=datetime_timezone.utc),
        )
        body["unsupported"] = True

        with self.assertRaises(SBGGovernanceRefused) as caught:
            self.service._validate_arrangement_body(body)

        self.assertIs(
            caught.exception.refusal_code,
            S015AuthorityRefusalCode.SBG_ARRANGEMENT_FIELD_UNSUPPORTED,
        )


class GovernanceControlledRefusalMappingTests(unittest.TestCase):
    @staticmethod
    def _integrity_error(primary, function):
        driver_error = Exception("driver error")
        driver_error.diag = SimpleNamespace(message_primary=primary)
        error = IntegrityError(f"{primary}\nCONTEXT: PL/pgSQL function {function}()")
        error.__cause__ = driver_error
        return error

    def test_adoption_guard_maps_each_controlled_primary(self):
        service = object.__new__(OrganismGovernanceService)
        effective_from = datetime(2026, 1, 1, tzinfo=datetime_timezone.utc)
        command = AdoptGoverningInstrumentCommand(
            credential=None,
            living_organism_uuid=uuid4(),
            rule_uuid=uuid4(),
            version=1,
            body=governance_arrangement_body(effective_from=effective_from),
            effective_from=effective_from,
            request_reference="adoption-ref",
            idempotency_key="adoption-key",
        )
        cases = (
            (
                S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
                "No arrangement was adopted and nothing changed. The arrangement is incomplete: "
                "the arrangement does not meet its requirements. Adopt a corrected version, then try "
                "again. Every seat needs an admission route and a deciding body that matches it.",
            ),
            (
                S015AuthorityRefusalCode.SBG_ARRANGEMENT_FIELD_UNSUPPORTED,
                "No arrangement was adopted and nothing changed. The arrangement includes a field this "
                "release does not record, which this release cannot yet act on. Leave it out for now; "
                "it can be added by a later version once the release that acts on it is delivered.",
            ),
            (
                S015AuthorityRefusalCode.SBG_ELIGIBILITY_UNSUPPORTED,
                'No arrangement was adopted and nothing changed. Seat the requested seat names an '
                'eligibility rule this release cannot check; it can check only "an active member of the '
                'LO". Use that rule for now; other eligibility rules arrive with the slices that can '
                "check them.",
            ),
        )
        for code, expected_text in cases:
            with self.subTest(code=code):
                controlled = self._integrity_error(
                    code.value,
                    "sbg_guard_governedrule",
                )
                with patch.object(service, "_execute", side_effect=controlled):
                    with self.assertRaises(SBGGovernanceRefused) as refused:
                        service.adopt_governing_instrument(command)
                self.assertIs(refused.exception.refusal_code, code)
                self.assertEqual(str(refused.exception), expected_text)
                self.assertIn("CONTEXT:", str(controlled))

    def test_request_guard_maps_exact_primary_and_reraises_unrelated_primary(self):
        service = object.__new__(OrganismGovernanceService)
        command = RequestGovernanceSeatCommand(
            credential=None,
            living_organism_uuid=uuid4(),
            circle_uuid=uuid4(),
            seat_rule_uuid=uuid4(),
            seat_id="founder",
            request_reference="request-ref",
            idempotency_key="request-key",
        )

        cases = (
            (
                S015AuthorityRefusalCode.SBG_MEMBER_NOT_ELIGIBLE,
                "Nobody was seated and nothing changed. A GOVERNANCE seat needs an active member of "
                "this LO at that time, and this person is not an active member at that time. Seat "
                "someone who is an active member of this LO.",
            ),
            (
                S015AuthorityRefusalCode.SBG_SEAT_ABSENT,
                "Your own request for seat founder was not recorded and nothing changed. The arrangement "
                "in effect has no seat founder. Choose a seat the arrangement in effect names. Check the "
                "seats in the current version before requesting one.",
            ),
        )
        for code, expected_text in cases:
            with self.subTest(code=code):
                controlled = self._integrity_error(
                    code.value,
                    "sbg_guard_circlejoinrequest",
                )
                with patch.object(service, "_request_governance_seat", side_effect=controlled):
                    with self.assertRaises(SBGGovernanceRefused) as refused:
                        service.request_governance_seat(command)
                self.assertIs(refused.exception.refusal_code, code)
                self.assertEqual(str(refused.exception), expected_text)
                self.assertIn("CONTEXT:", str(controlled))

        unrelated = self._integrity_error(
            "OTHER_CONSTRAINT",
            "sbg_guard_circlejoinrequest",
        )
        with patch.object(service, "_request_governance_seat", side_effect=unrelated):
            with self.assertRaises(IntegrityError) as raised:
                service.request_governance_seat(command)
        self.assertIs(raised.exception, unrelated)

    def test_admission_guard_maps_context_wrapped_primary_and_keeps_request_open(self):
        service = object.__new__(OrganismGovernanceService)
        request_uuid = uuid4()
        command = DecideGovernanceSeatCommand(
            credential=None,
            living_organism_uuid=uuid4(),
            request_reference="admission-ref",
            idempotency_key="admission-key",
            outcome="ACCEPT",
            request_uuid=request_uuid,
        )
        controlled = self._integrity_error(
            S015AuthorityRefusalCode.SBG_SEAT_OCCUPIED.value,
            "sbg_guard_circleparticipation_occupancy",
        )
        open_request = "Your own request for seat founder is recorded and remains open."
        cases = (
            (
                S015AuthorityRefusalCode.SBG_MEMBER_NOT_ELIGIBLE,
                "Nobody was seated and nothing changed. A GOVERNANCE seat needs an active member of "
                "this LO at that time, and this person is not an active member at that time. Seat "
                "someone who is an active member of this LO.",
            ),
            (
                S015AuthorityRefusalCode.SBG_SEAT_ABSENT,
                "Nobody was seated and nothing changed. The arrangement in effect has no seat founder. "
                "Choose a seat the arrangement in effect names. Check the seats in the current version "
                "before requesting one.",
            ),
            (
                S015AuthorityRefusalCode.SBG_SEAT_OCCUPIED,
                "Nobody was seated and nothing changed. Seat founder is already held at that time, and "
                "each seat holds one person. Choose a vacant seat, or wait until this seat becomes "
                "vacant. A seat becomes vacant when its holder's seat ends.",
            ),
            (
                S015AuthorityRefusalCode.SBG_ALREADY_SEATED,
                "Nobody was seated and nothing changed. This person already holds a seat in this "
                "GOVERNANCE Circle, and a person holds at most one. No further action is needed for this "
                "person.",
            ),
        )
        for code, authored_text in cases:
            with self.subTest(code=code):
                controlled = self._integrity_error(
                    code.value,
                    "sbg_guard_circleparticipation_occupancy",
                )
                with patch.object(
                    service,
                    "_decide_governance_seat",
                    side_effect=controlled,
                ):
                    with patch.object(
                        service,
                        "_request_open_context",
                        return_value=("founder", open_request),
                    ):
                        with self.assertRaises(SBGGovernanceRefused) as refused:
                            service.decide_governance_seat(command)
                self.assertIs(refused.exception.refusal_code, code)
                self.assertEqual(refused.exception.request_uuid, request_uuid)
                self.assertEqual(
                    str(refused.exception),
                    f"{authored_text} {open_request}",
                )
                self.assertIn("CONTEXT:", str(controlled))

        unrelated = self._integrity_error(
            "OTHER_CONSTRAINT",
            "sbg_guard_circleparticipation_occupancy",
        )
        with patch.object(service, "_decide_governance_seat", side_effect=unrelated):
            with patch.object(
                service,
                "_request_open_context",
                return_value=("founder", open_request),
            ):
                with self.assertRaises(IntegrityError) as raised:
                    service.decide_governance_seat(command)
        self.assertIs(raised.exception, unrelated)


class GovernanceEnablingReadinessGateTests(unittest.TestCase):
    def test_readiness_runs_only_for_no_to_yes_after_eligible_birthing(self):
        effective_at = datetime(2026, 10, 10, tzinfo=datetime_timezone.utc)
        evaluated_at = effective_at + timedelta(minutes=1)
        organism = SimpleNamespace(pk=37, organism_id=uuid4())
        refusal = S015AuthorityRefusalCode.SBG_ARRANGEMENT_MISSING
        cases = (
            (True, False, True, "EFFECT", True),
            (True, True, True, "EFFECT", False),
            (False, False, True, "EFFECT", False),
            (True, False, False, "EFFECT", False),
            (True, False, True, "DRAFT", False),
            (True, None, True, "EFFECT", True),
            (True, None, True, "EFFECT", True, False),
        )
        for index, case in enumerate(cases):
            with self.subTest(case=index):
                candidate_value, previous_value, has_birth, kind, expected, *has_previous = case
                birthing_source = Mock()
                birthing_source.reference_at.return_value = (
                    "birthing-event" if has_birth else None
                )
                provider = ProductionApplicationAuthorityProvider(
                    birthing_source=birthing_source,
                )
                request = SimpleNamespace(
                    action=OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
                    version_kind=kind,
                    effective_at=effective_at,
                    evaluated_at=evaluated_at,
                )
                candidate = SimpleNamespace(governance_enabled=candidate_value)
                previous = (
                    SimpleNamespace(governance_enabled=previous_value)
                    if not has_previous or has_previous[0]
                    else None
                )
                with patch.object(
                    provider,
                    "_sbg_governance_readiness",
                    return_value=refusal,
                ) as readiness:
                    result = provider._governance_enabling_readiness(
                        request,
                        organism,
                        candidate,
                        previous,
                    )

                if expected:
                    self.assertIs(result, refusal)
                    readiness.assert_called_once_with(
                        organism.pk,
                        effective_at,
                        evaluated_at,
                        "ENABLING",
                    )
                else:
                    self.assertIsNone(result)
                    readiness.assert_not_called()


class SBGGovernanceMigrationTests(TransactionTestCase):
    ISOLATION_FULL_CAPTURE = True

    reset_sequences = True

    FORMATION_MIGRATION = ("core", "0045_sbg_governance_formation")
    PREVIOUS_MIGRATION = ("core", "0044_sb2_birthing_snapshot")
    GOVERNEDRULE_UUID = "52345b7a-2fab-45ce-954e-e3ff922a8a37"
    NEW_TABLES = {
        "core_governedrule",
        "core_circlejoinrequest",
        "core_circleadmissiondecision",
        "core_circleparticipation",
        "core_circleparticipationend",
    }

    def _core_tables(self):
        return {
            table
            for table in connection.introspection.table_names()
            if table.startswith("core_")
        }

    def _function_digests(self, bodies):
        with connection.cursor() as cursor:
            for name, expected_body in dict(bodies).items():
                cursor.execute(
                    """
                    SELECT p.prosrc
                      FROM pg_proc p
                      JOIN pg_namespace n ON n.oid = p.pronamespace
                     WHERE n.nspname = 'public'
                       AND p.proname = %s
                    """,
                    [name],
                )
                row = cursor.fetchone()
                self.assertIsNotNone(row, name)
                actual_digest = hashlib.sha256(row[0].encode("utf-8")).hexdigest()
                expected_digest = hashlib.sha256(expected_body.encode("utf-8")).hexdigest()
                self.assertEqual(actual_digest, expected_digest, name)

    def _assert_0045_state(self, expected_tables):
        migration = importlib.import_module(
            "core.migrations.0045_sbg_governance_formation"
        )
        self.assertEqual(self._core_tables(), expected_tables)
        applied = MigrationRecorder(connection).applied_migrations()
        self.assertIn(self.FORMATION_MIGRATION, applied)
        self.assertEqual(current_applied_core_head(connection), [self.FORMATION_MIGRATION])
        self._function_digests(migration.BUILT_FUNCTION_BODIES)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT count(*)
                  FROM pg_trigger t
                  JOIN pg_class c ON c.oid = t.tgrelid
                  JOIN pg_namespace n ON n.oid = c.relnamespace
                 WHERE n.nspname = 'public'
                   AND NOT t.tgisinternal
                """
            )
            self.assertEqual(cursor.fetchone()[0], 458)

    def _record_governance_arrangement(self, route):
        rule_uuid = uuid4()
        event_uuid = uuid4()
        now = database_now()
        evidence_reference = f"rule:{rule_uuid}@1"
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, sequence, resulting_state
                  FROM public.core_livingorganismevent
                 WHERE living_organism_id = %s
                 ORDER BY sequence DESC
                 LIMIT 1
                """,
                [route.organism.pk],
            )
            previous_id, previous_sequence, previous_state = cursor.fetchone()
        body = {
            "seats": [
                {
                    "seat_id": "chair",
                    "eligibility": "LO_MEMBER",
                    "admission_route": "COOPTION",
                    "deciding_body": "body:GOVERNANCE",
                    "term": None,
                    "vacancy_rule": "BY_SEAT_ROUTE",
                    "effective_from": now.isoformat(),
                }
            ],
            "officer_roles": [],
            "election_schedule": None,
        }
        with transaction.atomic():
            GovernedRule.objects.create(
                rule_uuid=rule_uuid,
                version=1,
                scope_kind="LIVING_ORGANISM",
                scope_organism=route.organism,
                scope_circle=None,
                code="GOVERNANCE_CONSTITUTION",
                body=body,
                authority_reference=f"lo-event:{event_uuid}",
                source="FOUNDING_BODY_PRESCRIPTION",
                enterer_identity=route.founder,
                decided_at=now,
                effective_from=now,
                text_commitment=None,
                recorded_at=None,
            )
            with connection.cursor() as cursor:
                append_event(
                    cursor,
                    "core_livingorganismevent",
                    route.organism.pk,
                    previous_sequence + 1,
                    "ADOPT_GOVERNING_INSTRUMENT",
                    previous_state,
                    previous_state,
                    None,
                    route.organism.founding_authority_basis_id,
                    f"sbg-adopt-{event_uuid}",
                    pred_id=previous_id,
                    eff=now,
                    occ=now,
                    rec=now,
                    event_uuid=event_uuid,
                    overrides={
                        "actor_state": "LIVING_ORGANISM",
                        "actor_id": None,
                        "actor_access_epoch": None,
                        "actor_capacity": "STANDING",
                        "entry_mode": "TRANSPORT",
                        "enterer_identity_id": route.founder.pk,
                        "composing_rule_state": "NA_TRANSPORT",
                        "composing_rule_reference": None,
                        "composer_state": "NA_TRANSPORT",
                        "composer_identity_id": None,
                        "evidence_reference": evidence_reference,
                    },
                )
        return rule_uuid

    def _create_circle(self, route, *, kind, founding_reference):
        now = database_now()
        circle_uuid = uuid4()
        with transaction.atomic():
            with connection.cursor() as cursor:
                fingerprint = empty_fp(cursor, "core_circle", str(circle_uuid))
                if kind is None:
                    cursor.execute(
                        """
                        INSERT INTO public.core_circle
                            (circle_uuid, parent_organism_id, state, current_state_effective_at,
                             state_known_at, state_source_event_set_fingerprint,
                             founding_reference, created_at)
                        VALUES (%s, %s, 'DORMANT', %s, %s, %s, %s, %s)
                        RETURNING id, kind
                        """,
                        [
                            circle_uuid,
                            route.organism.pk,
                            now,
                            now,
                            fingerprint,
                            founding_reference,
                            now,
                        ],
                    )
                else:
                    cursor.execute(
                        """
                        INSERT INTO public.core_circle
                            (circle_uuid, parent_organism_id, kind, state,
                             current_state_effective_at, state_known_at,
                             state_source_event_set_fingerprint, founding_reference, created_at)
                        VALUES (%s, %s, %s, 'DORMANT', %s, %s, %s, %s, %s)
                        RETURNING id, kind
                        """,
                        [
                            circle_uuid,
                            route.organism.pk,
                            kind,
                            now,
                            now,
                            fingerprint,
                            founding_reference,
                            now,
                        ],
                    )
                circle_id, circle_kind = cursor.fetchone()
                append_event(
                    cursor,
                    "core_circlestateevent",
                    circle_id,
                    1,
                    "CREATE_DORMANT",
                    None,
                    "DORMANT",
                    None,
                    route.organism.founding_authority_basis_id,
                    f"sbg-m6-circle-{circle_uuid}",
                    eff=now,
                    occ=now,
                    rec=now,
                    overrides={
                        "actor_state": "LIVING_ORGANISM",
                        "actor_id": None,
                        "actor_access_epoch": None,
                        "actor_capacity": "STANDING",
                        "entry_mode": "TRANSPORT",
                        "enterer_identity_id": route.founder.pk,
                        "composing_rule_state": "NA_TRANSPORT",
                        "composing_rule_reference": None,
                        "composer_state": "NA_TRANSPORT",
                        "composer_identity_id": None,
                        "evidence_reference": (
                            f"living-organism:{route.organism.organism_id}"
                        ),
                    },
                )
                refresh(cursor, "core_circle", circle_id, state_at=now)
        return circle_id, circle_kind

    def test_w_sbg_m1_formation_installs_governance_schema_seed_and_guards(self):
        migrate_to_target_with_restore(self, self.FORMATION_MIGRATION)
        migration = importlib.import_module(
            "core.migrations.0045_sbg_governance_formation"
        )
        self.assertEqual(len(migration.BUILT_FUNCTION_BODIES), 13)
        self._function_digests(migration.BUILT_FUNCTION_BODIES)
        built_function_bodies = dict(migration.BUILT_FUNCTION_BODIES)
        occupancy = built_function_bodies[
            "sbg_guard_circleparticipation_occupancy"
        ]
        self.assertEqual(occupancy.count("p.id <> NEW.id"), 2)
        self.assertLess(
            occupancy.index("hashtext(NEW.seat_rule_uuid"),
            occupancy.index("'person#'"),
        )
        self.assertLess(
            occupancy.index("'person#'"),
            occupancy.index("v_known_at := clock_timestamp()"),
        )
        person_check = occupancy[occupancy.index("JOIN public.core_organismmembership m") :]
        self.assertNotIn("p.effective_at", person_check)
        self.assertIn("e.effective_at <= NEW.effective_at", person_check)
        self.assertTrue(self.NEW_TABLES.issubset(self._core_tables()))
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_default, is_nullable
                  FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name = 'core_circle'
                   AND column_name = 'kind'
                """
            )
            default, nullable = cursor.fetchone()
            self.assertIn("GENERIC", default)
            self.assertEqual(nullable, "NO")
            cursor.execute(
                """
                SELECT indexdef
                  FROM pg_indexes
                 WHERE schemaname = 'public'
                   AND indexname = 's015_circle_one_governance_uidx'
                """
            )
            index = cursor.fetchone()
            self.assertIsNotNone(index)
            self.assertIn("GOVERNANCE", index[0])
            cursor.execute(
                """
                SELECT version, scope_kind, code, source, body, text_commitment
                  FROM public.core_governedrule
                 WHERE rule_uuid = %s
                """,
                [self.GOVERNEDRULE_UUID],
            )
            seed = cursor.fetchone()
            self.assertIsNotNone(seed)
            self.assertEqual(seed[:4], (1, "PLATFORM", "U3_MEMBERSHIP_END_ENDS_CIRCLE_PARTICIPATION", "PLATFORM_RULING"))
            self.assertTrue(seed[4])
            self.assertTrue(seed[5])
            cursor.execute(
                """
                SELECT conname, pg_get_constraintdef(oid)
                  FROM pg_constraint
                 WHERE conrelid = 'public.core_circlestateevent'::regclass
                   AND conname IN (
                    's015_0022_circlestateevent_actor_state_vocab_ck',
                    's015_0022_circlestateevent_actor_capacity_state_ck'
                   )
                """
            )
            capacity_constraints = cursor.fetchall()
            self.assertEqual(len(capacity_constraints), 2)
            self.assertEqual(
                {name for name, _ in capacity_constraints},
                {
                    "s015_0022_circlestateevent_actor_state_vocab_ck",
                    "s015_0022_circlestateevent_actor_capacity_state_ck",
                },
            )
            for name, definition in capacity_constraints:
                self.assertIn("LIVING_ORGANISM", definition, name)

    def test_w_sbg_m2_reverse_restores_0044_bodies_tables_and_trigger_count(self):
        migrate_to_target_with_restore(self, self.FORMATION_MIGRATION)
        migration44 = importlib.import_module(
            "core.migrations.0044_sb2_birthing_snapshot"
        )
        expected_tables = self._core_tables() - self.NEW_TABLES
        migrate_to_target_with_restore(self, self.PREVIOUS_MIGRATION)
        self.assertEqual(self._core_tables(), expected_tables)
        self.assertNotIn(
            self.FORMATION_MIGRATION,
            MigrationRecorder(connection).applied_migrations(),
        )
        self._function_digests(
            {
                "sb_guard_phase_event": migration44.NEW_SB_GUARD_PHASE_EVENT_BODY,
                "s3fu_guard_gateway_effect": migration44.NEW_S3FU_GUARD_GATEWAY_EFFECT_BODY,
            }
        )
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT count(*)
                  FROM pg_trigger t
                  JOIN pg_class c ON c.oid = t.tgrelid
                  JOIN pg_namespace n ON n.oid = c.relnamespace
                 WHERE n.nspname = 'public'
                   AND NOT t.tgisinternal
                """
            )
            self.assertEqual(cursor.fetchone()[0], 433)

    def test_w_sbg_m3_restore_returns_to_the_0045_head_and_bodies(self):
        migrate_to_target_with_restore(self, self.FORMATION_MIGRATION)
        expected_tables = self._core_tables()
        original_targets = tuple(
            leaf
            for leaf in MigrationExecutor(connection).loader.graph.leaf_nodes()
            if leaf[0] != "core"
        ) + (self.FORMATION_MIGRATION,)

        def restore():
            MigrationExecutor(connection).migrate(list(original_targets))
            self._assert_0045_state(expected_tables)

        self.addCleanup(restore)
        MigrationExecutor(connection).migrate([self.PREVIOUS_MIGRATION])
        self.assertNotIn(
            self.FORMATION_MIGRATION,
            MigrationRecorder(connection).applied_migrations(),
        )
        self.assertTrue(self.NEW_TABLES.isdisjoint(self._core_tables()))

    def test_w_sbg_m4_setup_failure_restores_the_0045_head_and_bodies(self):
        migrate_to_target_with_restore(self, self.FORMATION_MIGRATION)
        expected_tables = self._core_tables()
        target = self.PREVIOUS_MIGRATION
        original_targets = tuple(
            leaf
            for leaf in MigrationExecutor(connection).loader.graph.leaf_nodes()
            if leaf[0] != "core"
        ) + (self.FORMATION_MIGRATION,)

        class SetupFailure(unittest.TestCase):
            def setUp(inner_self):
                inner_self.addCleanup(
                    lambda: MigrationExecutor(connection).migrate(
                        list(original_targets)
                    )
                )
                MigrationExecutor(connection).migrate([target])
                raise RuntimeError("intentional setup failure")

            def test_unreached(inner_self):
                inner_self.fail("setUp should stop before the test method")

        result = unittest.TestResult()
        unittest.defaultTestLoader.loadTestsFromTestCase(SetupFailure).run(result)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("intentional setup failure", result.errors[0][1])
        self._assert_0045_state(expected_tables)

    def test_w_sbg_m5_reverse_refuses_when_living_organism_rule_exists(self):
        migrate_to_target_with_restore(self, self.FORMATION_MIGRATION)
        from tests.sb2_route_support import prepare_route

        route = prepare_route(
            governance_enabled=None,
            include_settings=False,
            include_question=False,
            to_birthing_phase=False,
        )
        rule_uuid = self._record_governance_arrangement(route)
        with self.assertRaisesRegex(
            RuntimeError,
            "0045 reverse refused while SB-G governed data exists",
        ):
            MigrationExecutor(connection).migrate([self.PREVIOUS_MIGRATION])
        self.assertIn(
            self.FORMATION_MIGRATION,
            MigrationRecorder(connection).applied_migrations(),
        )
        self.assertTrue(
            GovernedRule.objects.filter(rule_uuid=rule_uuid, version=1).exists()
        )

    def test_w_sbg_m6_circle_kind_and_transport_constraint_are_enforced(self):
        from tests.sb2_route_support import prepare_route

        route = prepare_route(governance_enabled=False, to_birthing_phase=False)
        default_id, default_kind = self._create_circle(
            route,
            kind=None,
            founding_reference="sbg-m6-default",
        )
        self.assertEqual(default_kind, "GENERIC")
        with self.assertRaises(IntegrityError) as mutation:
            with transaction.atomic():
                Circle.objects.filter(pk=default_id).update(kind="GOVERNANCE")
        self.assertRegex(str(mutation.exception), r"(?i)(S015|immutable|kind)")

        governance_id, _ = self._create_circle(
            route,
            kind="GOVERNANCE",
            founding_reference="sbg-m6-governance",
        )
        with self.assertRaises(IntegrityError) as duplicate:
            with transaction.atomic():
                self._create_circle(
                    route,
                    kind="GOVERNANCE",
                    founding_reference="sbg-m6-duplicate",
                )
        self.assertIn("s015_circle_one_governance_uidx", str(duplicate.exception))

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT actor_state, actor_capacity, entry_mode
                  FROM public.core_circlestateevent
                 WHERE circle_id = %s AND sequence = 1
                """,
                [governance_id],
            )
            self.assertEqual(
                cursor.fetchone(),
                ("LIVING_ORGANISM", "STANDING", "TRANSPORT"),
            )

        now = database_now()
        with self.assertRaises(IntegrityError) as malformed:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    malformed_uuid = uuid4()
                    fingerprint = empty_fp(
                        cursor,
                        "core_circle",
                        str(malformed_uuid),
                    )
                    cursor.execute(
                        """
                        INSERT INTO public.core_circle
                            (circle_uuid, parent_organism_id, kind, state,
                             current_state_effective_at, state_known_at,
                             state_source_event_set_fingerprint, founding_reference, created_at)
                        VALUES (%s, %s, 'GENERIC', 'DORMANT', %s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        [
                            malformed_uuid,
                            route.organism.pk,
                            now,
                            now,
                            fingerprint,
                            "sbg-m6-malformed",
                            now,
                        ],
                    )
                    malformed_id = cursor.fetchone()[0]
                    append_event(
                        cursor,
                        "core_circlestateevent",
                        malformed_id,
                        1,
                        "CREATE_DORMANT",
                        None,
                        "DORMANT",
                        None,
                        route.organism.founding_authority_basis_id,
                        "sbg-m6-malformed-transport",
                        eff=now,
                        occ=now,
                        rec=now,
                        overrides={
                            "actor_state": "LIVING_ORGANISM",
                            "actor_capacity": "NA_NO_PARTY",
                            "entry_mode": "TRANSPORT",
                            "enterer_identity_id": route.founder.pk,
                            "composing_rule_state": "NA_TRANSPORT",
                            "composing_rule_reference": None,
                            "composer_state": "NA_TRANSPORT",
                            "composer_identity_id": None,
                        },
                    )
        self.assertRegex(
            str(malformed.exception),
            r"(?i)(capacity|actor_capacity_state_ck)",
        )


class SBGGovernanceRequestTestMixin:
    def _request(self, route, applicant, tag, *, seat_id="founder", effective_at=None):
        return route.service.request_governance_seat(RequestGovernanceSeatCommand(
            credential=applicant.credential,
            living_organism_uuid=route.organism.organism_id,
            circle_uuid=route.circle_uuid,
            seat_rule_uuid=route.rule_uuid,
            seat_id=seat_id,
            request_reference=f"sbg:g4b:request:{tag}",
            idempotency_key=f"sbg:g4b:request:key:{tag}",
            effective_at=(
                route_test_now(route.route) if effective_at is None else effective_at
            ),
        ))


class SBGGovernanceFormationTests(SBGGovernanceRequestTestMixin, TransactionTestCase):
    def _assert_class_c(self, route, call, code, authored_message):
        before = governance_route_state(route)
        with self.assertRaises(SBGGovernanceRefused) as refused:
            call()
        self.assertIs(refused.exception.refusal_code, code)
        self.assertEqual(str(refused.exception), authored_message)
        self.assertEqual(governance_route_state(route), before)

    def _assert_founding_seating_refusal(
        self,
        route,
        call,
        code,
        authored_message,
        *,
        request_recorded,
    ):
        before = governance_route_state(route)
        with self.assertRaises(SBGGovernanceRefused) as refused:
            call()
        self.assertIs(refused.exception.refusal_code, code)
        self.assertEqual(str(refused.exception), authored_message)
        expected = before.copy()
        if request_recorded:
            expected["authority_decisions"] += 1
            expected["receipts"] += 1
            expected["join_requests"] += 1
        self.assertEqual(governance_route_state(route), expected)
        if request_recorded:
            receipt = OrganismCommandReceipt.objects.filter(
                actor=route.founder,
                action=OrganismAction.REQUEST_GOVERNANCE_SEAT.value,
            ).latest("pk")
            self.assertTrue(
                AuthorityDecision.objects.filter(
                    decision_reference=receipt.authority_decision_reference,
                    outcome="QUALIFIED",
                ).exists()
            )
            self.assertTrue(CircleJoinRequest.objects.filter(
                request_uuid=refused.exception.request_uuid,
                circle__parent_organism=route.organism,
                membership__identity=route.founder,
            ).exists())

    def _assert_class_a(self, route, call, code, request_reference, authored_message):
        before = governance_route_state(route)
        with self.assertRaises(S015AuthorityDenied) as refused:
            call()
        self.assertIs(refused.exception.refusal_code, code)
        self.assertEqual(str(refused.exception), authored_message)
        after = governance_route_state(route)
        expected = before.copy()
        expected["authority_decisions"] += 1
        self.assertEqual(after, expected)
        decision = AuthorityDecision.objects.get(
            actor=route.founder,
            request_reference=request_reference,
        )
        self.assertEqual(decision.outcome, "REFUSED")
        self.assertEqual(decision.refusal_code, code.value)
        self.assertFalse(
            OrganismCommandReceipt.objects.filter(
                actor=route.founder,
                request_reference=request_reference,
            ).exists()
        )

    def _prepare_post_birthing_governance_route(
        self,
        tag,
        *,
        body=None,
        admission_route="APPOINTMENT",
        deciding_body="office:LO_COORDINATOR",
        adopt=True,
        create_circle=True,
        bypass_adoption_validation=False,
        preserve_seat_effective_from=False,
    ):
        route = prepare_route(
            tag=f"sbg-g4b-{uuid4().hex}",
            governance_enabled=False,
        )
        birth(route, suffix=f"sbg-post-birthing:{tag}")
        service = OrganismGovernanceService(clock=database_now)
        rule_uuid = uuid4()
        if body is None:
            body = governance_arrangement_body(
                admission_route=admission_route,
                deciding_body=deciding_body,
                effective_from=route_test_now(route),
            )
        else:
            body = deepcopy(body)
            for seat in body.get("seats", []):
                if (
                    not preserve_seat_effective_from
                    and type(seat) is dict
                    and "effective_from" in seat
                ):
                    seat["effective_from"] = route_test_now(route).isoformat()
        adoption = None
        if adopt:
            if bypass_adoption_validation:
                with patch.object(service, "_validate_arrangement_body", return_value=None):
                    adoption = adopt_governance_arrangement(
                        route,
                        service,
                        tag=f"{route.tag}:{tag}:adopt",
                        body=body,
                        rule_uuid=rule_uuid,
                    )
            else:
                adoption = adopt_governance_arrangement(
                    route,
                    service,
                    tag=f"{route.tag}:{tag}:adopt",
                    body=body,
                    rule_uuid=rule_uuid,
                )
        circle_creation = (
            create_governance_circle(
                route,
                service,
                tag=f"{route.tag}:{tag}:create",
            )
            if create_circle
            else None
        )
        return SimpleNamespace(
            route=route,
            service=service,
            founder=route.founder,
            organism=route.organism,
            rule_uuid=rule_uuid,
            circle_uuid=(
                circle_creation.record_uuid if circle_creation is not None else None
            ),
            adoption=adoption,
            circle_creation=circle_creation,
        )

    def test_controlled_guard_refusal_mappings_roll_back_adoption_and_request(self):
        route = self._prepare_post_birthing_governance_route("controlled-guard-rollback")
        before = governance_route_state(route)
        adoption_codes = (
            S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
            S015AuthorityRefusalCode.SBG_ARRANGEMENT_FIELD_UNSUPPORTED,
            S015AuthorityRefusalCode.SBG_ELIGIBILITY_UNSUPPORTED,
        )
        for code in adoption_codes:
            with self.subTest(command="adoption", code=code):
                tag = code.value.lower()
                effective_at = route_test_now(route.route)
                injection_reached = []
                command = AdoptGoverningInstrumentCommand(
                    credential=route.founder.credential,
                    living_organism_uuid=route.organism.organism_id,
                    rule_uuid=route.rule_uuid,
                    version=2,
                    body=governance_arrangement_body(effective_from=effective_at),
                    effective_from=effective_at,
                    request_reference=f"sbg:controlled:adopt:{tag}",
                    idempotency_key=f"sbg:controlled:adopt:key:{tag}",
                    effective_at=effective_at,
                )
                error = GovernanceControlledRefusalMappingTests._integrity_error(
                    code.value,
                    "sbg_guard_governedrule",
                )
                with patch(
                    "src.intevia.services.organism_governance_service.authority_atomic",
                    new=_atomic_with_integrity_error_on_exit(
                        error,
                        injection_reached=injection_reached,
                    ),
                ):
                    with self.assertRaises(SBGGovernanceRefused) as refused:
                        route.service.adopt_governing_instrument(command)
                self.assertEqual(injection_reached, [True])
                self.assertIs(refused.exception.refusal_code, code)
                self.assertEqual(governance_route_state(route), before)

        request_codes = (
            S015AuthorityRefusalCode.SBG_MEMBER_NOT_ELIGIBLE,
            S015AuthorityRefusalCode.SBG_SEAT_ABSENT,
        )
        for code in request_codes:
            with self.subTest(command="request", code=code):
                tag = code.value.lower()
                command = RequestGovernanceSeatCommand(
                    credential=route.founder.credential,
                    living_organism_uuid=route.organism.organism_id,
                    circle_uuid=route.circle_uuid,
                    seat_rule_uuid=route.rule_uuid,
                    seat_id="founder",
                    request_reference=f"sbg:controlled:request:{tag}",
                    idempotency_key=f"sbg:controlled:request:key:{tag}",
                )
                error = GovernanceControlledRefusalMappingTests._integrity_error(
                    code.value,
                    "sbg_guard_circlejoinrequest",
                )
                with patch(
                    "src.intevia.services.organism_governance_service.authority_atomic",
                    new=_atomic_with_integrity_error_on_exit(error),
                ):
                    with self.assertRaises(SBGGovernanceRefused) as refused:
                        route.service.request_governance_seat(command)
                self.assertIs(refused.exception.refusal_code, code)
                self.assertEqual(governance_route_state(route), before)

    def _draft_governance_choice(self, route, enabled, tag):
        occurred_at = database_now()
        return route.route.application_service.save_gateway_draft(GatewayDraftCommand(
            credential=route.founder.credential,
            living_organism_id=route.organism.organism_id,
            action=OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
            request_reference=f"sbg:enabling:draft:{tag}:{route.route.tag}",
            idempotency_key=f"sbg:enabling:draft:key:{tag}:{route.route.tag}",
            occurred_at=occurred_at,
            effective_at=occurred_at,
            received_at=occurred_at,
            fields={
                "cq_active": False,
                "governance_enabled": enabled,
                "inherit_portal_cq_settings": False,
                "require_cq_to_change_lo_settings": False,
                "require_cq_to_change_application_question": False,
            },
        ))

    def _effect_governance_choice(self, route, draft, tag):
        occurred_at = database_now()
        return route.route.application_service.effect_gateway_version(
            EffectGatewayVersionCommand(
                credential=route.founder.credential,
                living_organism_id=route.organism.organism_id,
                action=OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
                version_uuid=draft.version_uuid,
                request_reference=f"sbg:enabling:effect:{tag}:{route.route.tag}",
                idempotency_key=f"sbg:enabling:effect:key:{tag}:{route.route.tag}",
                occurred_at=occurred_at,
                effective_at=occurred_at,
                received_at=occurred_at,
            )
        )

    def _enabling_route_state(self, route):
        state = governance_route_state(route)
        state["governance_configuration_versions"] = (
            LOGovernanceConfigurationVersion.objects.filter(
                living_organism=route.organism,
            ).count()
        )
        return state

    def _assert_enabling_class_a(self, route, draft, tag, code, authored_message):
        before = self._enabling_route_state(route)
        request_reference = f"sbg:enabling:effect:{tag}:{route.route.tag}"
        with self.assertRaises(S015ApplicationRefused) as refused:
            self._effect_governance_choice(route, draft, tag)
        self.assertIs(refused.exception.refusal_code, code)
        self.assertEqual(str(refused.exception), authored_message)
        expected = before.copy()
        expected["authority_decisions"] += 1
        self.assertEqual(self._enabling_route_state(route), expected)
        decision = AuthorityDecision.objects.get(
            actor=route.founder,
            request_reference=request_reference,
        )
        self.assertEqual(decision.outcome, "REFUSED")
        self.assertEqual(decision.refusal_code, code.value)
        self.assertFalse(
            OrganismCommandReceipt.objects.filter(
                actor=route.founder,
                request_reference=request_reference,
            ).exists()
        )

    def _assert_enabling_class_b(
        self,
        route,
        draft,
        tag,
        code,
        authored_message,
    ):
        before = self._enabling_route_state(route)
        request_reference = f"sbg:enabling:effect:{tag}:{route.route.tag}"
        with patch.object(
            ProductionApplicationAuthorityProvider,
            "_governance_enabling_readiness",
            return_value=None,
        ):
            with self.assertRaises(S015ApplicationRefused) as refused:
                self._effect_governance_choice(route, draft, tag)
        self.assertIs(refused.exception.refusal_code, code)
        self.assertEqual(str(refused.exception), authored_message)
        self.assertEqual(self._enabling_route_state(route), before)
        self.assertFalse(
            AuthorityDecision.objects.filter(
                actor=route.founder,
                request_reference=request_reference,
            ).exists()
        )
        self.assertFalse(
            OrganismCommandReceipt.objects.filter(
                actor=route.founder,
                request_reference=request_reference,
            ).exists()
        )

    def _fixture_circle_state(self, route, resulting_state):
        circle = Circle.objects.get(circle_uuid=route.circle_uuid)
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, sequence, resulting_state, effective_at "
                "FROM public.core_circlestateevent WHERE circle_id = %s "
                "ORDER BY sequence DESC LIMIT 1",
                [circle.pk],
            )
            predecessor_id, sequence, prior_state, prior_effective_at = cursor.fetchone()
            effective_at = max(
                database_now(),
                prior_effective_at + timedelta(microseconds=1),
            )
            append_event(
                cursor,
                "core_circlestateevent",
                circle.pk,
                sequence + 1,
                f"TEST_FIXTURE_{resulting_state}",
                prior_state,
                resulting_state,
                None,
                route.organism.founding_authority_basis_id,
                f"sbg:fixture-circle-state:{route.route.tag}:{sequence + 1}",
                pred_id=predecessor_id,
                eff=effective_at,
                occ=effective_at,
                rec=effective_at,
                overrides={
                    "actor_state": "LIVING_ORGANISM",
                    "actor_id": None,
                    "actor_access_epoch": None,
                    "actor_capacity": "STANDING",
                    "entry_mode": "TRANSPORT",
                    "enterer_identity_id": route.founder.pk,
                    "composing_rule_state": "NA_TRANSPORT",
                    "composing_rule_reference": None,
                    "composer_state": "NA_TRANSPORT",
                    "composer_identity_id": None,
                },
            )
            refresh(cursor, "core_circle", circle.pk, state_at=effective_at)

    def _governance_structure(self, route):
        organism = route.organism
        return {
            "circles": list(
                Circle.objects.filter(parent_organism=organism).order_by("pk").values()
            ),
            "circle_events": list(
                CircleStateEvent.objects.filter(
                    circle__parent_organism=organism,
                ).order_by("pk").values()
            ),
            "rules": list(
                GovernedRule.objects.filter(
                    scope_kind="LIVING_ORGANISM",
                    scope_organism=organism,
                ).order_by("pk").values()
            ),
            "join_requests": list(
                CircleJoinRequest.objects.filter(
                    circle__parent_organism=organism,
                ).order_by("pk").values()
            ),
            "admission_decisions": list(
                CircleAdmissionDecision.objects.filter(
                    request__circle__parent_organism=organism,
                ).order_by("pk").values()
            ),
            "participations": list(
                CircleParticipation.objects.filter(
                    circle__parent_organism=organism,
                ).order_by("pk").values()
            ),
            "participation_endings": list(
                CircleParticipationEnd.objects.filter(
                    participation__circle__parent_organism=organism,
                ).order_by("pk").values()
            ),
        }

    def _set_lifecycle_state(self, route, *, close: bool):
        at = route_test_now(route)
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, sequence, resulting_state FROM public.core_livingorganismevent "
                "WHERE living_organism_id = %s ORDER BY sequence DESC LIMIT 1",
                [route.organism.pk],
            )
            predecessor_id, sequence, prior_state = cursor.fetchone()
            dormant_id, _ = append_event(
                cursor,
                "core_livingorganismevent",
                route.organism.pk,
                sequence + 1,
                "DORMANT",
                prior_state,
                "DORMANT",
                route.founder.pk,
                route.organism.founding_authority_basis_id,
                f"sbg:dormant:{route.route.tag}",
                pred_id=predecessor_id,
                eff=at,
                occ=at,
                rec=at,
            )
            refresh(cursor, "core_livingorganism", route.organism.pk, at)
            if close:
                closed_at = max(database_now(), at)
                append_event(
                    cursor,
                    "core_livingorganismevent",
                    route.organism.pk,
                    sequence + 2,
                    "CLOSE",
                    "DORMANT",
                    "CLOSED",
                    route.founder.pk,
                    route.organism.founding_authority_basis_id,
                    f"sbg:close:{route.route.tag}",
                    pred_id=dormant_id,
                    eff=closed_at,
                    occ=closed_at,
                    rec=closed_at,
                )
                refresh(cursor, "core_livingorganism", route.organism.pk, closed_at)

    def _append_direct_birthing_phase(self, route: BirthingRoute):
        effective_at = route_test_now(route)
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, sequence, resulting_state FROM public.core_livingorganismevent "
                "WHERE living_organism_id = %s ORDER BY sequence DESC LIMIT 1",
                [route.organism.pk],
            )
            predecessor_id, sequence, state = cursor.fetchone()
            append_event(
                cursor,
                "core_livingorganismevent",
                route.organism.pk,
                sequence + 1,
                "PHASE_ADVANCE",
                state,
                state,
                None,
                route.organism.founding_authority_basis_id,
                f"sbg:direct-birthing:{route.tag}",
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
                    "enterer_identity_id": route.founder.pk,
                    "entry_mode": "TRANSPORT",
                    "evidence_reference": "formation-phase:GERMINATING->BIRTHING",
                },
            )

    def _append_direct_birthing_event(self, route: BirthingRoute):
        effective_at = route_test_now(route)
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, sequence, resulting_state FROM public.core_livingorganismevent "
                "WHERE living_organism_id = %s ORDER BY sequence DESC LIMIT 1",
                [route.organism.pk],
            )
            predecessor_id, sequence, state = cursor.fetchone()
            append_event(
                cursor,
                "core_livingorganismevent",
                route.organism.pk,
                sequence + 1,
                "BIRTHING",
                state,
                state,
                None,
                route.organism.founding_authority_basis_id,
                f"sbg:direct-birthing-guard:{route.tag}",
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
                    "enterer_identity_id": route.founder.pk,
                    "entry_mode": "TRANSPORT",
                    "evidence_reference": f"birthing-snapshot:{uuid4()}",
                },
            )

    def test_w_f2_adoption_validation_and_future_dated_seat(self):
        route = prepare_governance_route()
        rule = GovernedRule.objects.get(
            scope_organism=route.organism,
            rule_uuid=route.rule_uuid,
            version=1,
        )
        self.assertEqual(rule.source, "FOUNDING_BODY_PRESCRIPTION")
        self.assertEqual(
            rule.authority_reference,
            f"lo-event:{route.adoption.event_uuid}",
        )
        adopted_event = LivingOrganismEvent.objects.get(event_uuid=route.adoption.event_uuid)
        self.assertEqual(adopted_event.action, "ADOPT_GOVERNING_INSTRUMENT")
        self.assertEqual(adopted_event.evidence_reference, f"rule:{route.rule_uuid}@1")

        start = route_test_now(route)
        invalid_cases = (
            (
                {"seats": [], "officer_roles": [], "election_schedule": None},
                S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
                "No arrangement was adopted and nothing changed. The arrangement is incomplete: "
                "it has no seats. Adopt a corrected version, then try again. Every seat needs an "
                "admission route and a deciding body that matches it.",
            ),
            (
                governance_arrangement_body(
                    admission_route="UNSUPPORTED",
                    deciding_body="body:LO_MEMBERSHIP",
                    effective_from=start,
                ),
                S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
                "No arrangement was adopted and nothing changed. The arrangement is incomplete: "
                "seat founder names no admission route. Adopt a corrected version, then try again. "
                "Every seat needs an admission route and a deciding body that matches it.",
            ),
            (
                {},
                S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
                "No arrangement was adopted and nothing changed. The arrangement is incomplete: "
                "it has no seats. Adopt a corrected version, then try again. Every seat needs an "
                "admission route and a deciding body that matches it.",
            ),
            (
                governance_arrangement_body(
                    deciding_body="body:LO_MEMBERSHIP",
                    effective_from=start,
                ),
                S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
                "No arrangement was adopted and nothing changed. The arrangement is incomplete: "
                "seat founder's deciding body does not match its admission route. Adopt a corrected "
                "version, then try again. Every seat needs an admission route and a deciding body "
                "that matches it.",
            ),
            (
                {
                    **governance_arrangement_body(effective_from=start),
                    "seats": [
                        {
                            **governance_arrangement_body(effective_from=start)["seats"][0],
                            "term": "FIXED",
                        }
                    ],
                },
                S015AuthorityRefusalCode.SBG_ARRANGEMENT_FIELD_UNSUPPORTED,
                "No arrangement was adopted and nothing changed. The arrangement includes a term "
                "for seat founder, which this release cannot yet act on. Leave it out for now; it "
                "can be added by a later version once the release that acts on it is delivered.",
            ),
        )
        for index, (body, code, message) in enumerate(invalid_cases):
            with self.subTest(invalid_case=index):
                self._assert_class_c(
                    route,
                    lambda body=body, index=index: adopt_governance_arrangement(
                        route.route,
                        route.service,
                        tag=f"{route.route.tag}:invalid:{index}",
                        body=body,
                        rule_uuid=route.rule_uuid,
                        version=2,
                    ),
                    code,
                    message,
                )

        unsupported_eligibility = governance_arrangement_body(
            effective_from=start,
        )
        unsupported_eligibility["seats"][0]["eligibility"] = "OFFICER"
        self._assert_class_c(
            route,
            lambda: adopt_governance_arrangement(
                route.route,
                route.service,
                tag=f"{route.route.tag}:unsupported-eligibility",
                body=unsupported_eligibility,
                rule_uuid=route.rule_uuid,
                version=2,
            ),
            S015AuthorityRefusalCode.SBG_ELIGIBILITY_UNSUPPORTED,
            'No arrangement was adopted and nothing changed. Seat founder names an eligibility '
            'rule this release cannot check; it can check only "an active member of the LO". Use '
            "that rule for now; other eligibility rules arrive with the slices that can check them.",
        )

        guard_effective_from = start + timedelta(days=1)
        unsupported_field_guard_body = {
            **governance_arrangement_body(effective_from=guard_effective_from),
            "unsupported": True,
        }
        unsupported_eligibility_guard_body = governance_arrangement_body(
            effective_from=guard_effective_from,
        )
        unsupported_eligibility_guard_body["seats"][0]["eligibility"] = "OFFICER"
        guard_cases = (
            (
                "unsupported-field",
                unsupported_field_guard_body,
                S015AuthorityRefusalCode.SBG_ARRANGEMENT_FIELD_UNSUPPORTED,
                "No arrangement was adopted and nothing changed. The arrangement includes a field "
                "this release does not record, which this release cannot yet act on. Leave it out "
                "for now; it can be added by a later version once the release that acts on it is "
                "delivered.",
            ),
            (
                "unsupported-eligibility",
                unsupported_eligibility_guard_body,
                S015AuthorityRefusalCode.SBG_ELIGIBILITY_UNSUPPORTED,
                'No arrangement was adopted and nothing changed. Seat the requested seat names an '
                'eligibility rule this release cannot check; it can check only "an active member of '
                'the LO". Use that rule for now; other eligibility rules arrive with the slices that '
                'can check them.',
            ),
        )
        # These defects cannot be persisted: the database adoption guard refuses them before readiness.
        for tag, body, code, expected_text in guard_cases:
            with self.subTest(database_guard=tag):
                before_guard = governance_route_state(route)
                with patch.object(route.service, "_validate_arrangement_body", return_value=None):
                    with self.assertRaises(SBGGovernanceRefused) as guarded:
                        adopt_governance_arrangement(
                            route.route,
                            route.service,
                            tag=f"{route.route.tag}:guard:{tag}",
                            body=body,
                            rule_uuid=route.rule_uuid,
                            version=2,
                        )
                self.assertIs(guarded.exception.refusal_code, code)
                self.assertEqual(str(guarded.exception), expected_text)
                self.assertIsInstance(guarded.exception.__cause__, IntegrityError)
                self.assertIn(code.value, str(guarded.exception.__cause__))
                self.assertEqual(governance_route_state(route), before_guard)

        future_start = route_test_now(route) + timedelta(days=1)
        dropped_body = governance_arrangement_body(
            seat_id="replacement",
            effective_from=future_start + timedelta(days=1),
        )
        self._assert_class_c(
            route,
            lambda: adopt_governance_arrangement(
                route.route,
                route.service,
                tag=f"{route.route.tag}:drop-held-seat",
                body=dropped_body,
                rule_uuid=route.rule_uuid,
                version=2,
                effective_from=future_start + timedelta(days=1),
            ),
            S015AuthorityRefusalCode.SBG_VERSION_DROPS_HELD_SEAT,
            "No arrangement was adopted and nothing changed. The new version leaves out seat "
            "founder, which someone currently holds. Keep that seat in the new version; a held "
            "seat cannot be removed by a new version in this release.",
        )

        future_body = governance_arrangement_body(effective_from=future_start)
        future_adoption = adopt_governance_arrangement(
            route.route,
            route.service,
            tag=f"{route.route.tag}:future-seat",
            body=future_body,
            rule_uuid=route.rule_uuid,
            version=2,
            effective_from=future_start,
        )
        self.assertEqual(future_adoption.record_uuid, route.rule_uuid)
        self.assertEqual(
            GovernedRule.objects.get(
                scope_organism=route.organism,
                rule_uuid=route.rule_uuid,
                version=2,
            ).effective_from,
            future_start,
        )

    def test_w_f3_circle_creation_and_lifecycle_refusals(self):
        route = prepare_governance_route(seat_founder=False)
        circle = Circle.objects.get(circle_uuid=route.circle_uuid)
        event = CircleStateEvent.objects.get(circle=circle, sequence=1)
        self.assertEqual(circle.kind, "GOVERNANCE")
        self.assertEqual(event.action, "CREATE_DORMANT")
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT actor_state, enterer_identity_id "
                "FROM public.core_circlestateevent WHERE id = %s",
                [event.pk],
            )
            actor_state, enterer_identity_id = cursor.fetchone()
        self.assertEqual(actor_state, "LIVING_ORGANISM")
        self.assertEqual(enterer_identity_id, route.founder.pk)

        self._assert_class_a(
            route,
            lambda: create_governance_circle(
                route.route,
                route.service,
                tag=f"{route.route.tag}:second-circle",
            ),
            S015AuthorityRefusalCode.SBG_SECOND_GOVERNANCE_CIRCLE,
            f"sbg:create-circle:{route.route.tag}:second-circle",
            "No Circle was created and nothing changed. This LO already has its GOVERNANCE "
            "Circle, and an LO has exactly one. Use the existing Circle; switching GOVERNANCE "
            "back on uses the same one.",
        )

        for close, state, remedy in (
            (False, "dormant", "Once this LO is restored, try again."),
            (True, "closed", "A closed LO is not reopened."),
        ):
            inactive = prepare_governance_route(seat_founder=False)
            self._set_lifecycle_state(inactive, close=close)
            self._assert_class_a(
                inactive,
                lambda inactive=inactive, close=close: create_governance_circle(
                    inactive.route,
                    inactive.service,
                    tag=f"{inactive.route.tag}:inactive:{close}",
                ),
                S015AuthorityRefusalCode.SBG_LO_NOT_ACTIVE,
                f"sbg:create-circle:{inactive.route.tag}:inactive:{close}",
                f"No Circle was created and nothing changed. This LO is {state}, so it can take "
                f"no new act of this kind. {remedy} Nothing about the LO's records changed.",
            )

    def test_w_f4_founding_self_seating_and_post_birthing_class_a_refusal(self):
        route = prepare_governance_route()
        founding_and_germinating = list(
            LivingOrganismEvent.objects.filter(
                living_organism=route.organism,
            ).filter(
                Q(sequence=1)
                | Q(evidence_reference="formation-phase:CONCEIVING->GERMINATING")
            )
        )
        self.assertEqual(len(founding_and_germinating), 2)
        for event in founding_and_germinating:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT public.s015_fold_effective_state("
                    "'core_livingorganism', %s, %s, %s)",
                    [
                        route.organism.pk,
                        event.effective_at,
                        event.recorded_at,
                    ],
                )
                self.assertEqual(cursor.fetchone()[0], "ACTIVE")
        request = CircleJoinRequest.objects.get(request_uuid=route.seating.request_uuid)
        decision = CircleAdmissionDecision.objects.get(decision_uuid=route.seating.decision_uuid)
        participation = CircleParticipation.objects.get(
            participation_uuid=route.seating.participation_uuid
        )
        self.assertEqual(request.initiated_by, "APPLICANT")
        interaction_key = f"sbg:seat:key:{route.route.tag}"
        request_key, request_reference, admission_key, admission_reference = (
            _expected_compound_stage_identifiers(
                route.organism.organism_id,
                interaction_key,
            )
        )
        self.assertEqual(request.consent_reference, request_reference)
        self.assertEqual(decision.route, "CIRCLE_FOUNDING")
        self.assertEqual(decision.deciding_party, "FOUNDING_BODY")
        self.assertTrue(decision.self_decision)
        self.assertEqual(decision.seat_rule_uuid, route.rule_uuid)
        self.assertEqual(decision.seat_id, "founder")
        self.assertEqual(participation.decision_id, decision.pk)
        consent_receipt = OrganismCommandReceipt.objects.get(
            actor=route.founder,
            action=OrganismAction.REQUEST_GOVERNANCE_SEAT.value,
            idempotency_key=request_key,
        )
        admission_receipt = OrganismCommandReceipt.objects.get(
            actor=route.founder,
            action=OrganismAction.ADMIT_CIRCLE_PARTICIPANT.value,
            idempotency_key=admission_key,
        )
        consent_decision = AuthorityDecision.objects.get(
            actor=route.founder,
            request_reference=request_reference,
        )
        admission_authority_decision = AuthorityDecision.objects.get(
            actor=route.founder,
            request_reference=admission_reference,
        )
        self.assertEqual(
            consent_receipt.authority_decision_reference,
            consent_decision.decision_reference,
        )
        self.assertEqual(
            admission_receipt.authority_decision_reference,
            admission_authority_decision.decision_reference,
        )
        self.assertNotEqual(
            consent_receipt.authority_decision_reference,
            admission_receipt.authority_decision_reference,
        )

        unseated = prepare_governance_route(seat_founder=False)
        advance_to_birthing(unseated.route, tag=unseated.route.tag)
        birth(unseated.route, suffix="sbg-founding-test")
        before = governance_route_state(unseated)
        with self.assertRaises(S015AuthorityDenied) as refused:
            seat_governance_founder(
                unseated.route,
                unseated.service,
                tag=f"{unseated.route.tag}:after-birthing",
                circle_uuid=unseated.circle_uuid,
                rule_uuid=unseated.rule_uuid,
            )
        self.assertIs(
            refused.exception.refusal_code,
            S015AuthorityRefusalCode.SBG_SEAT_ROUTE_UNAVAILABLE,
        )
        self.assertEqual(
            str(refused.exception),
            "Nobody was seated and nothing changed. Seat founder is filled by the founding "
            "route, which ends when the LO is born. Use a seat the LO Coordinators appoint, "
            "or wait for that route. Your own request for seat founder is recorded and remains open.",
        )
        expected = before.copy()
        expected["authority_decisions"] += 2
        expected["receipts"] += 1
        expected["join_requests"] += 1
        self.assertEqual(governance_route_state(unseated), expected)
        self.assertTrue(
            OrganismCommandReceipt.objects.filter(
                actor=unseated.founder,
                action=OrganismAction.REQUEST_GOVERNANCE_SEAT.value,
                idempotency_key=_expected_compound_stage_identifiers(
                    unseated.organism.organism_id,
                    f"sbg:seat:key:{unseated.route.tag}:after-birthing",
                )[0],
            ).exists()
        )
        self.assertTrue(CircleJoinRequest.objects.filter(
            request_uuid=refused.exception.request_uuid,
            circle__parent_organism=unseated.organism,
            membership__identity=unseated.founder,
        ).exists())
        self.assertFalse(
            OrganismCommandReceipt.objects.filter(
                actor=unseated.founder,
                action=OrganismAction.ADMIT_CIRCLE_PARTICIPANT.value,
                idempotency_key=_expected_compound_stage_identifiers(
                    unseated.organism.organism_id,
                    f"sbg:seat:key:{unseated.route.tag}:after-birthing",
                )[2],
            ).exists()
        )

    def test_w_f5_phase_and_birthing_readiness_by_provider_and_guard(self):
        missing_circle = prepare_route(
            governance_enabled=True,
            to_birthing_phase=False,
        )
        self._assert_class_a(
            missing_circle,
            lambda: advance_to_birthing(
                missing_circle,
                tag=f"{missing_circle.tag}:missing-circle",
            ),
            S015AuthorityRefusalCode.SBG_GOVERNANCE_CIRCLE_MISSING,
            f"sbg:phase:{missing_circle.tag}:missing-circle",
            "The phase was not advanced and nothing changed. GOVERNANCE is on for this LO, so it "
            "needs its GOVERNANCE Circle first. Create the LO's GOVERNANCE Circle, then try again. "
            "Creating the Circle early in formation avoids this.",
        )

        no_arrangement = prepare_route(
            governance_enabled=True,
            to_birthing_phase=False,
        )
        from tests.sbg_route_support import create_governance_circle
        from src.intevia.services.organism_governance_service import OrganismGovernanceService

        no_arrangement_service = OrganismGovernanceService(clock=database_now)
        create_governance_circle(
            no_arrangement,
            no_arrangement_service,
            tag=f"{no_arrangement.tag}:circle-only",
        )
        self._assert_class_a(
            no_arrangement,
            lambda: advance_to_birthing(
                no_arrangement,
                tag=f"{no_arrangement.tag}:missing-arrangement",
            ),
            S015AuthorityRefusalCode.SBG_ARRANGEMENT_MISSING,
            f"sbg:phase:{no_arrangement.tag}:missing-arrangement",
            "The phase was not advanced and nothing changed. GOVERNANCE needs an adopted "
            "arrangement saying which seats exist, how each is filled and who decides, and this "
            "LO has none in effect. Adopt an arrangement, then try again. Adopting it before "
            "creating the Circle avoids this.",
        )

        election_body = governance_arrangement_body(
            admission_route="ELECTION",
            deciding_body="body:LO_MEMBERSHIP",
            effective_from=database_now(),
        )
        election_only = prepare_governance_route(
            governance_enabled=True,
            seat_founder=False,
            body=election_body,
        )
        prebirth_request = self._request(
            election_only,
            election_only.founder,
            "f5-prebirth-consent",
        )
        self.assertTrue(
            CircleJoinRequest.objects.filter(
                request_uuid=prebirth_request.request_uuid,
                initiated_by="APPLICANT",
            ).exists()
        )
        self.assertFalse(
            CircleParticipation.objects.filter(
                circle__parent_organism=election_only.organism,
            ).exists()
        )
        advance_to_birthing(election_only.route, tag=f"{election_only.route.tag}:phase")
        self._assert_class_a(
            election_only,
            lambda: birth(election_only.route, suffix="no-initial-route"),
            S015AuthorityRefusalCode.SBG_NO_INITIAL_PARTICIPATION_ROUTE,
            f"sb2:no-initial-route:{election_only.route.tag}",
            "The LO was not born and nothing changed. Nobody holds a GOVERNANCE seat yet, and no "
            "seat in the arrangement can be filled in this release: election seats wait for "
            "elections, co-option seats wait for an active GOVERNANCE, and ex officio seats are "
            "not yet available. Take a seat before the LO is born, or adopt a version with a seat "
            "the LO Coordinators appoint. The arrangement you adopted stays recorded and in effect.",
        )

        external_seat = prepare_governance_route(
            governance_enabled=True,
            seat_founder=False,
        )
        advance_to_birthing(external_seat.route, tag=f"{external_seat.route.tag}:phase")
        external_birth = birth(external_seat.route, suffix="available-external-seat")
        self.assertEqual(
            LivingOrganismEvent.objects.get(event_uuid=external_birth.event_uuid).action,
            "BIRTHING",
        )
        self.assertEqual(
            AuthorityDecision.objects.get(
                decision_reference=external_birth.authority_decision_reference,
            ).outcome,
            "QUALIFIED",
        )

        direct_missing_circle = prepare_route(
            governance_enabled=True,
            to_birthing_phase=False,
        )
        before_direct = governance_route_state(direct_missing_circle)
        with self.assertRaisesMessage(
            IntegrityError,
            "SBG_GOVERNANCE_CIRCLE_MISSING",
        ):
            self._append_direct_birthing_phase(direct_missing_circle)
        self.assertEqual(governance_route_state(direct_missing_circle), before_direct)

        direct_no_route = prepare_governance_route(
            governance_enabled=True,
            seat_founder=False,
            body=governance_arrangement_body(
                admission_route="ELECTION",
                deciding_body="body:LO_MEMBERSHIP",
                effective_from=database_now(),
            ),
        )
        advance_to_birthing(
            direct_no_route.route,
            tag=f"{direct_no_route.route.tag}:direct-birthing-guard",
        )
        before_direct = governance_route_state(direct_no_route)
        with self.assertRaisesMessage(
            IntegrityError,
            "SBG_NO_INITIAL_PARTICIPATION_ROUTE",
        ):
            self._append_direct_birthing_event(direct_no_route.route)
        self.assertEqual(governance_route_state(direct_no_route), before_direct)

    def test_w_f6_snapshot_names_operative_arrangement_and_no_keeps_null(self):
        route = prepare_governance_route()
        advance_to_birthing(route.route, tag=f"{route.route.tag}:phase")
        birthing = birth(route.route, suffix="snapshot-arrangement")
        snapshot = LOBirthingSnapshot.objects.get(living_organism=route.organism)
        self.assertEqual(
            snapshot.body["governance_constitution"],
            f"rule:{route.rule_uuid}@1",
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
                    json.dumps(snapshot.body),
                    snapshot.effective_at,
                    route.organism.organism_id,
                    snapshot.received_at,
                    snapshot.snapshot_uuid,
                ],
            )
            self.assertEqual(snapshot.snapshot_commitment, cursor.fetchone()[0])
        self.assertEqual(
            LivingOrganismEvent.objects.get(event_uuid=birthing.event_uuid).action,
            "BIRTHING",
        )

        governance_off = prepare_governance_route(
            governance_enabled=False,
            seat_founder=False,
        )
        advance_to_birthing(
            governance_off.route,
            tag=f"{governance_off.route.tag}:phase",
        )
        birth(governance_off.route, suffix="snapshot-null")
        no_governance_snapshot = LOBirthingSnapshot.objects.get(
            living_organism=governance_off.organism,
        )
        self.assertIsNone(no_governance_snapshot.body["governance_constitution"])

    def test_w_f6_snapshot_guard_refuses_a_mismatched_arrangement_reference(self):
        route = prepare_governance_route()
        advance_to_birthing(route.route, tag=f"{route.route.tag}:phase")
        before = governance_route_state(route)
        original_create = QuerySet.create

        def create_mismatched_snapshot(queryset, **kwargs):
            if queryset.model is LOBirthingSnapshot:
                body = dict(kwargs["body"])
                body["governance_constitution"] = "rule:00000000-0000-0000-0000-000000000001@1"
                kwargs["body"] = body
            return original_create(queryset, **kwargs)

        with patch.object(QuerySet, "create", create_mismatched_snapshot):
            with self.assertRaises(S015BirthingTechnicalFailure) as refused:
                birth(route.route, suffix="snapshot-mismatch")
        self.assertIn("constitutional snapshot did not match", str(refused.exception))
        self.assertEqual(governance_route_state(route), before)

    def test_w_f7_founding_participation_is_current_at_birthing_coordinates(self):
        route = prepare_governance_route()
        request = CircleJoinRequest.objects.get(
            request_uuid=route.seating.request_uuid,
        )
        participation = CircleParticipation.objects.get(
            participation_uuid=route.seating.participation_uuid
        )
        request_key, request_reference, admission_key, admission_reference = (
            _expected_compound_stage_identifiers(
                route.organism.organism_id,
                f"sbg:seat:key:{route.route.tag}",
            )
        )
        request_receipt = OrganismCommandReceipt.objects.get(
            actor=route.founder,
            action=OrganismAction.REQUEST_GOVERNANCE_SEAT.value,
            idempotency_key=request_key,
        )
        admission_receipt = OrganismCommandReceipt.objects.get(
            actor=route.founder,
            action=OrganismAction.ADMIT_CIRCLE_PARTICIPANT.value,
            idempotency_key=admission_key,
        )
        self.assertEqual(request.consent_reference, request_reference)
        self.assertEqual(
            request_receipt.authority_decision_reference,
            AuthorityDecision.objects.get(
                actor=route.founder,
                request_reference=request_reference,
            ).decision_reference,
        )
        self.assertEqual(
            admission_receipt.authority_decision_reference,
            AuthorityDecision.objects.get(
                actor=route.founder,
                request_reference=admission_reference,
            ).decision_reference,
        )
        advance_to_birthing(route.route, tag=f"{route.route.tag}:phase")
        result = birth(route.route, suffix="seat-current-at-birth")
        snapshot = LOBirthingSnapshot.objects.get(living_organism=route.organism)
        snapshot = LOBirthingSnapshot.objects.get(living_organism=route.organism)
        self.assertLessEqual(participation.effective_at, snapshot.effective_at)
        self.assertEqual(
            snapshot.body["governance_constitution"],
            f"rule:{route.rule_uuid}@1",
        )

    def test_w_sbg_t_route_f_refusals_keep_codes_authored_text_and_effect_classes(self):
        disabled = prepare_governance_route(seat_founder=False)
        effect_gateway(
            disabled.route.application_service,
            disabled.founder,
            disabled.organism,
            f"{disabled.route.tag}:disable",
            OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
            {
                "cq_active": False,
                "governance_enabled": False,
                "inherit_portal_cq_settings": False,
                "require_cq_to_change_lo_settings": False,
                "require_cq_to_change_application_question": False,
            },
        )
        self._assert_founding_seating_refusal(
            disabled,
            lambda: seat_governance_founder(
                disabled.route,
                disabled.service,
                tag=f"{disabled.route.tag}:disabled",
                circle_uuid=disabled.circle_uuid,
                rule_uuid=disabled.rule_uuid,
            ),
            S015AuthorityRefusalCode.SBG_SEATING_WHILE_DISABLED,
            "Nobody was seated and nothing changed. GOVERNANCE is switched off for this LO, so "
            "no one can be seated. Switch GOVERNANCE on through the LO's settings first, if the "
            "LO chooses to. Your own request for seat founder is recorded and remains open.",
            request_recorded=True,
        )

        route = prepare_governance_route()
        absent_message = (
            "Your own request for seat missing-seat was not recorded and nothing changed. "
            "The arrangement in effect has no seat "
            "missing-seat. Choose a seat the arrangement in effect names. Check the seats in "
            "the current version before requesting one."
        )
        self._assert_founding_seating_refusal(
            route,
            lambda: seat_governance_founder(
                route.route,
                route.service,
                tag=f"{route.route.tag}:absent-seat",
                circle_uuid=route.circle_uuid,
                rule_uuid=route.rule_uuid,
                seat_id="missing-seat",
            ),
            S015AuthorityRefusalCode.SBG_SEAT_ABSENT,
            absent_message,
            request_recorded=False,
        )

        occupied_message = (
            "Nobody was seated and nothing changed. Seat founder is already held at that time, "
            "and each seat holds one person. Choose a vacant seat, or wait until this seat becomes "
            "vacant. A seat becomes vacant when its holder's seat ends. Your own request for seat "
            "founder is recorded and remains open."
        )
        self._assert_founding_seating_refusal(
            route,
            lambda: seat_governance_founder(
                route.route,
                route.service,
                tag=f"{route.route.tag}:occupied-seat",
                circle_uuid=route.circle_uuid,
                rule_uuid=route.rule_uuid,
            ),
            S015AuthorityRefusalCode.SBG_SEAT_OCCUPIED,
            occupied_message,
            request_recorded=True,
        )

        second_seat_at = route_test_now(route.route)
        second_seat_body = {
            "seats": [
                *governance_arrangement_body(
                    effective_from=second_seat_at,
                )["seats"],
                {
                    **governance_arrangement_body(
                        seat_id="second",
                        effective_from=second_seat_at,
                    )["seats"][0],
                },
            ],
            "officer_roles": [],
            "election_schedule": None,
        }
        adopt_governance_arrangement(
            route.route,
            route.service,
            tag=f"{route.route.tag}:second-seat-version",
            body=second_seat_body,
            rule_uuid=route.rule_uuid,
            version=2,
        )
        already_seated_message = (
            "Nobody was seated and nothing changed. This person already holds a seat in this "
            "GOVERNANCE Circle, and a person holds at most one. No further action is needed for "
            "this person. Your own request for seat second is recorded and remains open."
        )
        self._assert_founding_seating_refusal(
            route,
            lambda: seat_governance_founder(
                route.route,
                route.service,
                tag=f"{route.route.tag}:same-person-second-seat",
                circle_uuid=route.circle_uuid,
                rule_uuid=route.rule_uuid,
                seat_id="second",
            ),
            S015AuthorityRefusalCode.SBG_ALREADY_SEATED,
            already_seated_message,
            request_recorded=True,
        )

    def test_w_p1_post_birthing_adoption_and_circle_creation(self):
        route = self._prepare_post_birthing_governance_route("p1")
        rule = GovernedRule.objects.get(
            scope_organism=route.organism,
            rule_uuid=route.rule_uuid,
            version=1,
        )
        self.assertEqual(rule.source, "LO_PRESCRIPTION")
        self.assertEqual(
            rule.authority_reference,
            f"lo-event:{route.adoption.event_uuid}",
        )
        adoption_event = LivingOrganismEvent.objects.get(
            event_uuid=route.adoption.event_uuid,
        )
        self.assertEqual(adoption_event.action, "ADOPT_GOVERNING_INSTRUMENT")
        self.assertEqual(
            adoption_event.evidence_reference,
            f"rule:{route.rule_uuid}@1",
        )
        circle = Circle.objects.get(circle_uuid=route.circle_uuid)
        self.assertEqual(circle.kind, "GOVERNANCE")
        self.assertEqual(circle.state, "DORMANT")
        circle_event = CircleStateEvent.objects.get(circle=circle, sequence=1)
        self.assertEqual(circle_event.action, "CREATE_DORMANT")
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT actor_state, actor_capacity, entry_mode "
                "FROM public.core_circlestateevent WHERE id = %s",
                [circle_event.pk],
            )
            self.assertEqual(
                cursor.fetchone(),
                ("LIVING_ORGANISM", "STANDING", "TRANSPORT"),
            )

    def test_w_p2_post_birthing_second_circle_is_refused(self):
        route = self._prepare_post_birthing_governance_route("p2")
        self._assert_class_a(
            route,
            lambda: create_governance_circle(
                route.route,
                route.service,
                tag=f"{route.route.tag}:second-circle",
            ),
            S015AuthorityRefusalCode.SBG_SECOND_GOVERNANCE_CIRCLE,
            f"sbg:create-circle:{route.route.tag}:second-circle",
            "No Circle was created and nothing changed. This LO already has its GOVERNANCE "
            "Circle, and an LO has exactly one. Use the existing Circle; switching GOVERNANCE "
            "back on uses the same one.",
        )

    def test_w_p3_post_birthing_enabling_readiness_and_guard_fixtures(self):
        # W-F2's adoption witness proves unsupported fields and eligibility cannot reach readiness.
        circle_missing = self._prepare_post_birthing_governance_route(
            "p3-circle-missing",
            create_circle=False,
        )
        draft = self._draft_governance_choice(circle_missing, True, "circle-missing-a")
        self._assert_enabling_class_a(
            circle_missing,
            draft,
            "circle-missing-a",
            S015AuthorityRefusalCode.SBG_GOVERNANCE_CIRCLE_MISSING,
            "GOVERNANCE was not switched on and nothing changed. GOVERNANCE is on for this LO, "
            "so it needs its GOVERNANCE Circle first. Create the LO's GOVERNANCE Circle, then "
            "try again. Creating the Circle early in formation avoids this.",
        )

        circle_missing_b = self._prepare_post_birthing_governance_route(
            "p3-circle-missing-b",
            create_circle=False,
        )
        draft = self._draft_governance_choice(circle_missing_b, True, "circle-missing-b")
        self._assert_enabling_class_b(
            circle_missing_b,
            draft,
            "circle-missing-b",
            S015AuthorityRefusalCode.SBG_GOVERNANCE_CIRCLE_MISSING,
            "GOVERNANCE was not switched on and nothing changed. GOVERNANCE is on for this LO, "
            "so it needs its GOVERNANCE Circle first. Create the LO's GOVERNANCE Circle, then "
            "try again. Creating the Circle early in formation avoids this.",
        )

        arrangement_missing = self._prepare_post_birthing_governance_route(
            "p3-arrangement-missing",
            adopt=False,
        )
        draft = self._draft_governance_choice(
            arrangement_missing,
            True,
            "arrangement-missing-a",
        )
        self._assert_enabling_class_a(
            arrangement_missing,
            draft,
            "arrangement-missing-a",
            S015AuthorityRefusalCode.SBG_ARRANGEMENT_MISSING,
            "GOVERNANCE was not switched on and nothing changed. GOVERNANCE needs an adopted "
            "arrangement saying which seats exist, how each is filled and who decides, and this "
            "LO has none in effect. Adopt an arrangement, then try again. Adopting it before "
            "creating the Circle avoids this.",
        )

        arrangement_missing_b = self._prepare_post_birthing_governance_route(
            "p3-arrangement-missing-b",
            adopt=False,
        )
        draft = self._draft_governance_choice(
            arrangement_missing_b,
            True,
            "arrangement-missing-b",
        )
        self._assert_enabling_class_b(
            arrangement_missing_b,
            draft,
            "arrangement-missing-b",
            S015AuthorityRefusalCode.SBG_ARRANGEMENT_MISSING,
            "GOVERNANCE was not switched on and nothing changed. GOVERNANCE needs an adopted "
            "arrangement saying which seats exist, how each is filled and who decides, and this "
            "LO has none in effect. Adopt an arrangement, then try again. Adopting it before "
            "creating the Circle avoids this.",
        )

        incomplete_body = governance_arrangement_body(
            effective_from=database_now() + timedelta(days=1),
        )
        arrangement_incomplete = self._prepare_post_birthing_governance_route(
            "p3-arrangement-incomplete",
            body=incomplete_body,
            preserve_seat_effective_from=True,
        )
        draft = self._draft_governance_choice(
            arrangement_incomplete,
            True,
            "arrangement-incomplete-a",
        )
        self._assert_enabling_class_a(
            arrangement_incomplete,
            draft,
            "arrangement-incomplete-a",
            S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
            "GOVERNANCE was not switched on and nothing changed. The arrangement is incomplete: "
            "it has no seat in effect at this time. Adopt a corrected version, then try again. Every seat needs an "
            "admission route and a deciding body that matches it.",
        )

        arrangement_incomplete_b = self._prepare_post_birthing_governance_route(
            "p3-arrangement-incomplete-b",
            body=incomplete_body,
            preserve_seat_effective_from=True,
        )
        draft = self._draft_governance_choice(
            arrangement_incomplete_b,
            True,
            "arrangement-incomplete-b",
        )
        self._assert_enabling_class_b(
            arrangement_incomplete_b,
            draft,
            "arrangement-incomplete-b",
            S015AuthorityRefusalCode.SBG_ARRANGEMENT_INCOMPLETE,
            "GOVERNANCE was not switched on and nothing changed. The arrangement is incomplete: "
            "it has no seat in effect at this time. Adopt a corrected version, then try again. Every seat needs an "
            "admission route and a deciding body that matches it.",
        )

        no_route = self._prepare_post_birthing_governance_route(
            "p3-no-route-a",
            admission_route="ELECTION",
            deciding_body="body:LO_MEMBERSHIP",
        )
        draft = self._draft_governance_choice(no_route, True, "no-route-a")
        self._assert_enabling_class_a(
            no_route,
            draft,
            "no-route-a",
            S015AuthorityRefusalCode.SBG_NO_INITIAL_PARTICIPATION_ROUTE,
            "GOVERNANCE was not switched on and nothing changed. Nobody holds a GOVERNANCE seat "
            "yet, and no seat in the arrangement can be filled in this release: election seats "
            "wait for elections, co-option seats wait for an active GOVERNANCE, and ex officio "
            "seats are not yet available. Adopt a version with a seat the LO Coordinators appoint, "
            "then try again. The arrangement you adopted stays recorded and in effect.",
        )

        no_route_b = self._prepare_post_birthing_governance_route(
            "p3-no-route-b",
            admission_route="ELECTION",
            deciding_body="body:LO_MEMBERSHIP",
        )
        draft = self._draft_governance_choice(no_route_b, True, "no-route-b")
        self._assert_enabling_class_b(
            no_route_b,
            draft,
            "no-route-b",
            S015AuthorityRefusalCode.SBG_NO_INITIAL_PARTICIPATION_ROUTE,
            "GOVERNANCE was not switched on and nothing changed. Nobody holds a GOVERNANCE seat "
            "yet, and no seat in the arrangement can be filled in this release: election seats "
            "wait for elections, co-option seats wait for an active GOVERNANCE, and ex officio "
            "seats are not yet available. Adopt a version with a seat the LO Coordinators appoint, "
            "then try again. The arrangement you adopted stays recorded and in effect.",
        )

        ready = self._prepare_post_birthing_governance_route("p3-ready")
        snapshot = LOBirthingSnapshot.objects.get(living_organism=ready.organism)
        snapshot_body = dict(snapshot.body)
        draft = self._draft_governance_choice(ready, True, "ready")
        self._effect_governance_choice(ready, draft, "ready")
        effected_version = LOGovernanceConfigurationVersion.objects.get(
            version_uuid=draft.version_uuid,
        )
        self.assertIs(effected_version.governance_enabled, True)
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.body, snapshot_body)

        eligible = self._prepare_post_birthing_governance_route("p3-eligible")
        eligible_snapshot = LOBirthingSnapshot.objects.get(
            living_organism=eligible.organism,
        )
        eligible_snapshot_body = dict(eligible_snapshot.body)
        self._fixture_circle_state(eligible, "ELIGIBLE")
        self.assertEqual(
            Circle.objects.get(circle_uuid=eligible.circle_uuid).state,
            "ELIGIBLE",
        )
        draft = self._draft_governance_choice(eligible, True, "eligible")
        self._effect_governance_choice(eligible, draft, "eligible")
        self.assertIs(
            LOGovernanceConfigurationVersion.objects.get(
                version_uuid=draft.version_uuid,
            ).governance_enabled,
            True,
        )
        eligible_snapshot.refresh_from_db()
        self.assertEqual(eligible_snapshot.body, eligible_snapshot_body)

        dormant = self._prepare_post_birthing_governance_route("p3-dormant-a")
        self._fixture_circle_state(dormant, "ELIGIBLE")
        self._fixture_circle_state(dormant, "DORMANT")
        self.assertTrue(
            CircleStateEvent.objects.filter(
                circle__circle_uuid=dormant.circle_uuid,
                resulting_state="ELIGIBLE",
            ).exists()
        )
        dormant_snapshot = LOBirthingSnapshot.objects.get(
            living_organism=dormant.organism,
        )
        dormant_snapshot_body = dict(dormant_snapshot.body)
        draft = self._draft_governance_choice(dormant, True, "dormant-a")
        self._assert_enabling_class_a(
            dormant,
            draft,
            "dormant-a",
            S015AuthorityRefusalCode.SBG_IL_GRESD,
            "GOVERNANCE was not switched on and nothing changed. This LO's GOVERNANCE Circle "
            "was active before and is now dormant; its next step is the restoration route for a "
            "dormant GOVERNANCE, which is not yet available in this release. This is a limit of "
            "the current build, not a refusal of the LO's choice. The LO's Coordinators continue "
            "to govern until that route is delivered.",
        )
        dormant_snapshot.refresh_from_db()
        self.assertEqual(dormant_snapshot.body, dormant_snapshot_body)

        dormant_b = self._prepare_post_birthing_governance_route("p3-dormant-b")
        self._fixture_circle_state(dormant_b, "ELIGIBLE")
        self._fixture_circle_state(dormant_b, "DORMANT")
        draft = self._draft_governance_choice(dormant_b, True, "dormant-b")
        self._assert_enabling_class_b(
            dormant_b,
            draft,
            "dormant-b",
            S015AuthorityRefusalCode.SBG_IL_GRESD,
            "GOVERNANCE was not switched on and nothing changed. This LO's GOVERNANCE Circle "
            "was active before and is now dormant; its next step is the restoration route for a "
            "dormant GOVERNANCE, which is not yet available in this release. This is a limit of "
            "the current build, not a refusal of the LO's choice. The LO's Coordinators continue "
            "to govern until that route is delivered.",
        )

    def test_w_d1_disabling_preserves_governance_establishment(self):
        route = self._prepare_post_birthing_governance_route("d1")
        enabled_draft = self._draft_governance_choice(route, True, "d1-enable")
        self._effect_governance_choice(route, enabled_draft, "d1-enable")
        before = self._governance_structure(route)
        circle = Circle.objects.get(circle_uuid=route.circle_uuid)
        circle_state = circle.state
        draft = self._draft_governance_choice(route, False, "d1-disable")
        self._effect_governance_choice(route, draft, "d1-disable")
        self.assertEqual(self._governance_structure(route), before)
        self.assertEqual(
            Circle.objects.get(circle_uuid=route.circle_uuid).state,
            circle_state,
        )
        self.assertIs(
            LOGovernanceConfigurationVersion.objects.get(
                version_uuid=draft.version_uuid,
            ).governance_enabled,
            False,
        )

    def test_w_d2_reenabling_reuses_the_existing_circle_and_arrangement(self):
        route = self._prepare_post_birthing_governance_route("d2")
        enabled_draft = self._draft_governance_choice(route, True, "d2-enable")
        self._effect_governance_choice(route, enabled_draft, "d2-enable")
        before_disable = self._governance_structure(route)
        disabled_draft = self._draft_governance_choice(route, False, "d2-disable")
        self._effect_governance_choice(route, disabled_draft, "d2-disable")
        self.assertEqual(self._governance_structure(route), before_disable)
        before_reenable = self._governance_structure(route)
        reenabled_draft = self._draft_governance_choice(route, True, "d2-reenable")
        self._effect_governance_choice(route, reenabled_draft, "d2-reenable")
        self.assertEqual(self._governance_structure(route), before_reenable)
        self.assertEqual(
            Circle.objects.filter(
                parent_organism=route.organism,
                kind="GOVERNANCE",
            ).count(),
            1,
        )


class SBGGovernancePostBirthingSeatingTests(
    SBGGovernanceRequestTestMixin,
    TransactionTestCase,
):
    def _prepare_route(self, tag, *, body=None, enable=True):
        route = prepare_route(
            tag=f"sbg-g4b-{uuid4().hex}",
            governance_enabled=False,
        )
        birth(route, suffix=f"sbg-g4b:{tag}")
        service = OrganismGovernanceService(clock=database_now)
        effective_at = route_test_now(route)
        rule_uuid = uuid4()
        if body is None:
            body = governance_arrangement_body(effective_from=effective_at)
        else:
            body = deepcopy(body)
            for seat in body.get("seats", []):
                if type(seat) is dict:
                    seat["effective_from"] = effective_at.isoformat()
        adoption = adopt_governance_arrangement(
            route,
            service,
            tag=f"g4b:{tag}:adopt",
            body=body,
            rule_uuid=rule_uuid,
        )
        creation = create_governance_circle(
            route,
            service,
            tag=f"g4b:{tag}:circle",
        )
        if enable:
            effect_gateway(
                route.application_service,
                route.founder,
                route.organism,
                f"g4b:{tag}:enable",
                OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
                {
                    "cq_active": False,
                    "governance_enabled": True,
                    "inherit_portal_cq_settings": False,
                    "require_cq_to_change_lo_settings": False,
                    "require_cq_to_change_application_question": False,
                },
            )
        return SimpleNamespace(
            route=route,
            service=service,
            founder=route.founder,
            organism=route.organism,
            rule_uuid=rule_uuid,
            circle_uuid=creation.record_uuid,
            adoption=adoption,
        )

    def _decide(
        self,
        route,
        tag,
        *,
        request_uuid=None,
        seat_id="founder",
        outcome="ACCEPT",
        effective_at=None,
    ):
        command_values = {}
        if request_uuid is None:
            command_values = {
                "circle_uuid": route.circle_uuid,
                "seat_rule_uuid": route.rule_uuid,
                "seat_id": seat_id,
            }
        else:
            command_values["request_uuid"] = request_uuid
        return route.service.decide_governance_seat(DecideGovernanceSeatCommand(
            credential=route.founder.credential,
            living_organism_uuid=route.organism.organism_id,
            request_reference=f"sbg:g4b:decision:{tag}",
            idempotency_key=f"sbg:g4b:decision:key:{tag}",
            outcome=outcome,
            effective_at=(
                route_test_now(route.route) if effective_at is None else effective_at
            ),
            **command_values,
        ))

    def test_controlled_admission_guard_refusals_roll_back_and_keep_request(self):
        route = self._prepare_route("controlled-admission-rollback")
        applicant = add_active_elector(route.route, "controlled-admission-applicant")
        request_result = self._request(route, applicant, "controlled-admission-request")
        before = governance_route_state(route)
        codes = (
            S015AuthorityRefusalCode.SBG_MEMBER_NOT_ELIGIBLE,
            S015AuthorityRefusalCode.SBG_SEAT_ABSENT,
            S015AuthorityRefusalCode.SBG_SEAT_OCCUPIED,
            S015AuthorityRefusalCode.SBG_ALREADY_SEATED,
        )
        for code in codes:
            with self.subTest(code=code):
                tag = code.value.lower()
                error = GovernanceControlledRefusalMappingTests._integrity_error(
                    code.value,
                    "sbg_guard_circleparticipation_occupancy",
                )
                with patch(
                    "src.intevia.services.organism_governance_service.authority_atomic",
                    new=_atomic_with_integrity_error_on_exit(error),
                ):
                    with self.assertRaises(SBGGovernanceRefused) as refused:
                        self._decide(
                            route,
                            f"controlled-admission-{tag}",
                            request_uuid=request_result.request_uuid,
                        )
                self.assertIs(refused.exception.refusal_code, code)
                self.assertEqual(refused.exception.request_uuid, request_result.request_uuid)
                self.assertEqual(governance_route_state(route), before)
                self.assertTrue(
                    CircleJoinRequest.objects.filter(
                        request_uuid=request_result.request_uuid,
                    ).exists()
                )

        combined = self._prepare_route("controlled-combined-admission-rollback")
        combined_tag = "controlled-combined-admission"
        interaction_key = f"sbg:g4b:decision:key:{combined_tag}"
        (
            request_key,
            _,
            admission_key,
            admission_reference,
        ) = _compound_stage_identifiers(
            combined.organism.organism_id,
            interaction_key,
        )
        combined_before = governance_route_state(combined)
        error = GovernanceControlledRefusalMappingTests._integrity_error(
            S015AuthorityRefusalCode.SBG_SEAT_OCCUPIED.value,
            "sbg_guard_circleparticipation_occupancy",
        )
        with patch(
            "src.intevia.services.organism_governance_service.authority_atomic",
            new=_atomic_with_integrity_error_on_exit(error, fail_on=2),
        ):
            with self.assertRaises(SBGGovernanceRefused) as refused:
                self._decide(combined, combined_tag)
        self.assertIs(
            refused.exception.refusal_code,
            S015AuthorityRefusalCode.SBG_SEAT_OCCUPIED,
        )
        self.assertEqual(refused.exception.request_uuid is not None, True)
        self.assertTrue(
            str(refused.exception).endswith(
                "Your own request for seat founder is recorded and remains open."
            )
        )
        expected = combined_before.copy()
        expected["authority_decisions"] += 1
        expected["receipts"] += 1
        expected["join_requests"] += 1
        self.assertEqual(governance_route_state(combined), expected)
        request = CircleJoinRequest.objects.get(
            request_uuid=refused.exception.request_uuid,
        )
        self.assertEqual(request.seat_id, "founder")
        request_receipt = OrganismCommandReceipt.objects.get(
            actor=combined.founder,
            action=OrganismAction.REQUEST_GOVERNANCE_SEAT.value,
            idempotency_key=request_key,
        )
        request_decision = AuthorityDecision.objects.get(
            actor=combined.founder,
            request_reference=request.consent_reference,
        )
        self.assertEqual(
            request_receipt.authority_decision_reference,
            request_decision.decision_reference,
        )
        self.assertFalse(
            CircleAdmissionDecision.objects.filter(
                request=request,
            ).exists()
        )
        self.assertFalse(
            CircleParticipation.objects.filter(
                decision__request=request,
            ).exists()
        )
        self.assertFalse(
            AuthorityDecision.objects.filter(
                actor=combined.founder,
                request_reference=admission_reference,
            ).exists()
        )
        self.assertFalse(
            OrganismCommandReceipt.objects.filter(
                actor=combined.founder,
                action=OrganismAction.ADMIT_CIRCLE_PARTICIPANT.value,
                idempotency_key=admission_key,
            ).exists()
        )

    def _record_counts(self):
        return {
            "requests": CircleJoinRequest.objects.count(),
            "decisions": CircleAdmissionDecision.objects.count(),
            "participations": CircleParticipation.objects.count(),
            "authority_decisions": AuthorityDecision.objects.count(),
            "receipts": OrganismCommandReceipt.objects.count(),
        }

    def _fixture_circle_state(self, route, resulting_state):
        circle = Circle.objects.get(circle_uuid=route.circle_uuid)
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, sequence, resulting_state, effective_at "
                "FROM public.core_circlestateevent WHERE circle_id = %s "
                "ORDER BY sequence DESC LIMIT 1",
                [circle.pk],
            )
            predecessor_id, sequence, prior_state, prior_effective_at = cursor.fetchone()
            effective_at = max(
                database_now(),
                prior_effective_at + timedelta(microseconds=1),
            )
            append_event(
                cursor,
                "core_circlestateevent",
                circle.pk,
                sequence + 1,
                f"TEST_FIXTURE_{resulting_state}",
                prior_state,
                resulting_state,
                None,
                route.organism.founding_authority_basis_id,
                f"sbg:fixture-circle-state:{route.route.tag}:{sequence + 1}",
                pred_id=predecessor_id,
                eff=effective_at,
                occ=effective_at,
                rec=effective_at,
                overrides={
                    "actor_state": "LIVING_ORGANISM",
                    "actor_id": None,
                    "actor_access_epoch": None,
                    "actor_capacity": "STANDING",
                    "entry_mode": "TRANSPORT",
                    "enterer_identity_id": route.founder.pk,
                    "composing_rule_state": "NA_TRANSPORT",
                    "composing_rule_reference": None,
                    "composer_state": "NA_TRANSPORT",
                    "composer_identity_id": None,
                },
            )
            refresh(cursor, "core_circle", circle.pk, state_at=effective_at)

    def _direct_decision_rows(
        self,
        route,
        membership,
        tag,
        effective_at,
        *,
        admission_route="GOVERNANCE_CONSTITUTIONAL",
    ):
        received_at = effective_at
        join_request = CircleJoinRequest.objects.create(
            circle=Circle.objects.get(circle_uuid=route.circle_uuid),
            membership=membership,
            seat_rule_uuid=route.rule_uuid,
            seat_id="founder",
            initiated_by="APPLICANT",
            consent_reference=f"sbg:g4b:direct-consent:{tag}",
            enterer_identity=membership.identity,
            occurred_at=effective_at,
            effective_at=effective_at,
            received_at=received_at,
        )
        decision_reference = "s015d1:" + hashlib.sha256(tag.encode("utf-8")).hexdigest()
        constitutional_route = (
            "APPOINTMENT"
            if admission_route == "GOVERNANCE_CONSTITUTIONAL"
            else "NOT_APPLICABLE"
        )
        deciding_party = (
            "office:LO_COORDINATOR"
            if admission_route == "GOVERNANCE_CONSTITUTIONAL"
            else "FOUNDING_BODY"
        )
        admission = CircleAdmissionDecision.objects.create(
            request=join_request,
            route=admission_route,
            constitutional_route=constitutional_route,
            deciding_party=deciding_party,
            deciding_identity=route.founder,
            self_decision=membership.identity_id == route.founder.pk,
            outcome="ACCEPT",
            authority_decision_reference=decision_reference,
            settings_basis=f"rule:{route.rule_uuid}@1#founder",
            seat_rule_uuid=route.rule_uuid,
            seat_id="founder",
            actor_state="LIVING_ORGANISM",
            actor=None,
            actor_access_epoch=None,
            actor_capacity="STANDING",
            entry_mode="TRANSPORT",
            enterer_identity=route.founder,
            composing_rule_state="NA_TRANSPORT",
            composing_rule_reference=None,
            composer_state="NA_TRANSPORT",
            composer_identity=None,
            occurred_at=effective_at,
            effective_at=effective_at,
            received_at=received_at,
        )
        return admission

    def _end_participation_for_w_r3(self, participation, effective_at, tag):
        _, endings = end_membership_with_participations(
            participation.membership,
            effective_at,
            tag,
        )
        return next(item for item in endings if item.participation_id == participation.pk)

    def _insert_participation_sql(self, route, membership, admission, effective_at):
        values = {
            "participation_uuid": uuid4(),
            "circle_id": admission.request.circle_id,
            "membership_id": membership.pk,
            "seat_rule_uuid": route.rule_uuid,
            "seat_id": "founder",
            "decision_id": admission.pk,
            "actor_state": "LIVING_ORGANISM",
            "actor_id": None,
            "actor_access_epoch": None,
            "actor_capacity": "STANDING",
            "entry_mode": "TRANSPORT",
            "enterer_identity_id": route.founder.pk,
            "composing_rule_state": "NA_TRANSPORT",
            "composing_rule_reference": None,
            "composer_state": "NA_TRANSPORT",
            "composer_identity_id": None,
            "occurred_at": effective_at,
            "effective_at": effective_at,
            "received_at": effective_at,
        }
        fields = []
        parameters = []
        for field in CircleParticipation._meta.local_concrete_fields:
            if field.primary_key or field.name == "recorded_at":
                continue
            if field.attname in values:
                value = values[field.attname]
            elif field.auto_now or field.auto_now_add:
                value = values["received_at"]
            elif field.has_default():
                value = field.get_default()
            elif field.null:
                value = None
            else:
                raise AssertionError(f"direct participation fixture lacks {field.attname}")
            fields.append(field)
            parameters.append(value)
        quote = connections["default"].ops.quote_name
        columns = ", ".join(quote(field.column) for field in fields)
        placeholders = ", ".join(["%s"] * len(fields))
        table = quote(CircleParticipation._meta.db_table)
        with connections["default"].cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
                parameters,
            )

    def test_w_p4_post_birthing_appointment_request_decision_and_refusals(self):
        route = self._prepare_route("p4-positive")
        applicant = add_active_elector(route.route, "p4-applicant")
        request_result = self._request(route, applicant, "p4-request")
        join_request = CircleJoinRequest.objects.get(request_uuid=request_result.request_uuid)
        self.assertEqual(join_request.initiated_by, "APPLICANT")
        self.assertEqual(join_request.enterer_identity, applicant)
        self.assertEqual(join_request.consent_reference, "sbg:g4b:request:p4-request")
        receipt = OrganismCommandReceipt.objects.get(
            actor=applicant,
            action=OrganismAction.REQUEST_GOVERNANCE_SEAT.value,
            idempotency_key="sbg:g4b:request:key:p4-request",
        )
        consent_decision = AuthorityDecision.objects.get(
            actor=applicant,
            request_reference="sbg:g4b:request:p4-request",
        )
        self.assertEqual(
            receipt.authority_decision_reference,
            consent_decision.decision_reference,
        )

        result = self._decide(route, "p4-decision", request_uuid=join_request.request_uuid)
        decision = CircleAdmissionDecision.objects.get(decision_uuid=result.decision_uuid)
        participation = CircleParticipation.objects.get(
            participation_uuid=result.participation_uuid,
        )
        self.assertEqual(decision.route, "GOVERNANCE_CONSTITUTIONAL")
        self.assertEqual(decision.constitutional_route, "APPOINTMENT")
        self.assertEqual(decision.deciding_party, "office:LO_COORDINATOR")
        self.assertFalse(decision.self_decision)
        self.assertEqual(decision.seat_rule_uuid, join_request.seat_rule_uuid)
        self.assertEqual(decision.seat_id, join_request.seat_id)
        self.assertEqual(participation.membership_id, join_request.membership_id)
        self.assertEqual(participation.enterer_identity, route.founder)
        self.assertEqual(participation.effective_at, decision.effective_at)
        self.assertGreaterEqual(participation.effective_at, decision.occurred_at)
        self.assertEqual(participation.actor_state, "LIVING_ORGANISM")
        self.assertIsNone(participation.actor_id)
        self.assertIsNone(participation.actor_access_epoch)
        self.assertEqual(participation.actor_capacity, "STANDING")
        self.assertEqual(participation.entry_mode, "TRANSPORT")
        self.assertEqual(participation.composing_rule_state, "NA_TRANSPORT")
        self.assertEqual(participation.composer_state, "NA_TRANSPORT")

        admission_receipt = OrganismCommandReceipt.objects.get(
            actor=route.founder,
            action=OrganismAction.ADMIT_CIRCLE_PARTICIPANT.value,
            idempotency_key="sbg:g4b:decision:key:p4-decision",
        )
        admission_authority_decision = AuthorityDecision.objects.get(
            decision_reference=result.authority_decision_reference,
        )
        self.assertEqual(
            admission_receipt.authority_decision_reference,
            admission_authority_decision.decision_reference,
        )

        decline = self._prepare_route("p4-decline")
        decline_applicant = add_active_elector(decline.route, "p4-decline-applicant")
        decline_request = self._request(
            decline,
            decline_applicant,
            "p4-decline-request",
        )
        before_decline = governance_route_state(decline.route)
        decline_result = self._decide(
            decline,
            "p4-decline-decision",
            request_uuid=decline_request.request_uuid,
            outcome="DECLINE",
        )
        decline_decision = CircleAdmissionDecision.objects.get(
            decision_uuid=decline_result.decision_uuid,
        )
        self.assertEqual(decline_decision.outcome, "DECLINE")
        self.assertIsNone(decline_result.participation_uuid)
        self.assertFalse(
            CircleParticipation.objects.filter(decision=decline_decision).exists()
        )
        expected_decline = before_decline.copy()
        expected_decline["authority_decisions"] += 1
        expected_decline["receipts"] += 1
        expected_decline["admission_decisions"] += 1
        self.assertEqual(governance_route_state(decline.route), expected_decline)
        decline_receipt = OrganismCommandReceipt.objects.get(
            actor=decline.founder,
            action=OrganismAction.ADMIT_CIRCLE_PARTICIPANT.value,
            idempotency_key="sbg:g4b:decision:key:p4-decline-decision",
        )
        decline_authority_decision = AuthorityDecision.objects.get(
            decision_reference=decline_result.authority_decision_reference,
        )
        self.assertEqual(
            decline_receipt.authority_decision_reference,
            decline_authority_decision.decision_reference,
        )

        self_route = self._prepare_route("p4-self")
        self_result = self._decide(self_route, "p4-self-seat")
        self_decision = CircleAdmissionDecision.objects.get(
            decision_uuid=self_result.decision_uuid,
        )
        self_request = CircleJoinRequest.objects.get(request_uuid=self_result.request_uuid)
        self.assertTrue(self_decision.self_decision)
        (
            self_request_key,
            self_request_reference,
            self_admission_key,
            self_admission_reference,
        ) = _expected_compound_stage_identifiers(
            self_route.organism.organism_id,
            "sbg:g4b:decision:key:p4-self-seat",
        )
        expected_self_request_uuid = uuid5(
            self_route.organism.organism_id,
            f"{OrganismAction.REQUEST_GOVERNANCE_SEAT.value}:{self_request_key}",
        )
        self.assertEqual(self_result.request_uuid, expected_self_request_uuid)
        self.assertEqual(self_request.request_uuid, expected_self_request_uuid)
        self.assertEqual(self_request.membership.identity, self_route.founder)
        self.assertEqual(
            self_request.consent_reference,
            self_request_reference,
        )
        self_receipts = OrganismCommandReceipt.objects.filter(
            actor=self_route.founder,
            idempotency_key__in=(self_request_key, self_admission_key),
        )
        self.assertEqual(self_receipts.count(), 2)
        self_request_receipt = self_receipts.get(action=OrganismAction.REQUEST_GOVERNANCE_SEAT.value)
        self_admission_receipt = self_receipts.get(
            action=OrganismAction.ADMIT_CIRCLE_PARTICIPANT.value,
        )
        self_consent_decision = AuthorityDecision.objects.get(
            actor=self_route.founder,
            request_reference=self_request_reference,
        )
        self_admission_authority_decision = AuthorityDecision.objects.get(
            actor=self_route.founder,
            request_reference=self_admission_reference,
        )
        self.assertEqual(
            self_request_receipt.authority_decision_reference,
            self_consent_decision.decision_reference,
        )
        self.assertEqual(
            self_admission_receipt.authority_decision_reference,
            self_admission_authority_decision.decision_reference,
        )
        self.assertIsNotNone(self_result.participation_uuid)

        combined_disabled = self._prepare_route("p4-combined-disabled", enable=False)
        before_combined = governance_route_state(combined_disabled.route)
        combined_effective_at = route_test_now(combined_disabled.route)
        for retry in range(2):
            with self.assertRaises(SBGGovernanceRefused) as combined_refusal:
                self._decide(
                    combined_disabled,
                    "p4-combined-disabled",
                    effective_at=combined_effective_at,
                )
            self.assertIs(
                combined_refusal.exception.refusal_code,
                S015AuthorityRefusalCode.SBG_SEATING_WHILE_DISABLED,
            )
            self.assertEqual(
                str(combined_refusal.exception),
                "Nobody was seated and nothing changed. GOVERNANCE is switched off for this LO, "
                "so no one can be seated. Switch GOVERNANCE on through the LO's settings first, "
                "if the LO chooses to. Your own request for seat founder is recorded and remains open.",
            )
            self.assertIsNotNone(combined_refusal.exception.request_uuid)
            expected_combined = before_combined.copy()
            expected_combined["authority_decisions"] += 1
            expected_combined["receipts"] += 1
            expected_combined["join_requests"] += 1
            self.assertEqual(
                governance_route_state(combined_disabled.route),
                expected_combined,
            )
        combined_request = CircleJoinRequest.objects.get(
            request_uuid=combined_refusal.exception.request_uuid,
        )
        combined_request_receipt = OrganismCommandReceipt.objects.get(
            actor=combined_disabled.founder,
            action=OrganismAction.REQUEST_GOVERNANCE_SEAT.value,
            idempotency_key=_expected_compound_stage_identifiers(
                combined_disabled.organism.organism_id,
                "sbg:g4b:decision:key:p4-combined-disabled",
            )[0],
        )
        self.assertEqual(combined_request.request_uuid, combined_refusal.exception.request_uuid)
        self.assertTrue(
            AuthorityDecision.objects.filter(
                decision_reference=combined_request_receipt.authority_decision_reference,
                outcome="QUALIFIED",
            ).exists()
        )
        self.assertFalse(
            OrganismCommandReceipt.objects.filter(
                actor=combined_disabled.founder,
                action=OrganismAction.ADMIT_CIRCLE_PARTICIPANT.value,
            ).exists()
        )

        # These defence-in-depth refusals need ACTIVE history, unavailable until S4-C.
        # Their integrated witnesses are owed to S4-C, where Circle activation arrives.

        invalid_request = self._prepare_route("p4-invalid-request")
        before_invalid_request = self._record_counts()
        with self.assertRaises(SBGGovernanceRefused) as invalid_request_refusal:
            self._request(
                invalid_request,
                invalid_request.founder,
                "p4-invalid-seat",
                seat_id="missing",
            )
        self.assertIs(
            invalid_request_refusal.exception.refusal_code,
            S015AuthorityRefusalCode.SBG_SEAT_ABSENT,
        )
        self.assertEqual(self._record_counts(), before_invalid_request)

        request_guard = self._prepare_route("p4-request-guard")
        before_request_guard = self._record_counts()
        guard_effective_at = route_test_now(request_guard.route)
        with self.assertRaises(IntegrityError) as request_guard_refusal:
            with transaction.atomic():
                CircleJoinRequest.objects.create(
                    circle=Circle.objects.get(circle_uuid=request_guard.circle_uuid),
                    membership=OrganismMembership.objects.get(
                        living_organism=request_guard.organism,
                        identity=request_guard.founder,
                    ),
                    seat_rule_uuid=request_guard.rule_uuid,
                    seat_id="founder",
                    initiated_by="DECIDER",
                    consent_reference="sbg:g4b:request-guard",
                    enterer_identity=request_guard.founder,
                    occurred_at=guard_effective_at,
                    effective_at=guard_effective_at,
                    received_at=guard_effective_at,
                )
        self.assertIn("SBG_CIRCLEJOINREQUEST_INVALID", str(request_guard_refusal.exception))
        self.assertEqual(self._record_counts(), before_request_guard)

        disabled = self._prepare_route("p4-disabled", enable=False)
        disabled_applicant = add_active_elector(disabled.route, "p4-disabled-applicant")
        disabled_request = self._request(
            disabled,
            disabled_applicant,
            "p4-disabled-request",
        )
        before_decision = self._record_counts()
        with self.assertRaises(SBGGovernanceRefused) as disabled_refusal:
            self._decide(
                disabled,
                "p4-disabled-decision",
                request_uuid=disabled_request.request_uuid,
            )
        self.assertIs(
            disabled_refusal.exception.refusal_code,
            S015AuthorityRefusalCode.SBG_SEATING_WHILE_DISABLED,
        )
        self.assertEqual(
            str(disabled_refusal.exception),
            "Nobody was seated and nothing changed. GOVERNANCE is switched off for this LO, so no one "
            "can be seated. Switch GOVERNANCE on through the LO's settings first, if the LO chooses to. "
            "Your own request for seat founder is recorded and remains open.",
        )
        self.assertEqual(self._record_counts(), before_decision)

        unavailable_routes = (
            (
                "ELECTION",
                "body:LO_MEMBERSHIP",
                "election, which waits for elections to be delivered",
            ),
            (
                "COOPTION",
                "body:GOVERNANCE",
                "co-option, which waits for an active GOVERNANCE",
            ),
            (
                "EX_OFFICIO",
                "office:00000000-0000-4000-8000-000000000001",
                "ex officio attachment, which is not yet available",
            ),
            (
                "APPOINTMENT",
                "external:appointed body",
                "a body outside INTEVIA, whose decisions are not yet recorded here",
            ),
        )
        for index, (admission_route, deciding_body, route_detail) in enumerate(
            unavailable_routes,
        ):
            with self.subTest(admission_route=admission_route, deciding_body=deciding_body):
                arrangement_at = database_now()
                route_body = governance_arrangement_body(
                    admission_route=admission_route,
                    deciding_body=deciding_body,
                    effective_from=arrangement_at,
                )
                coordinator_seat = governance_arrangement_body(
                    seat_id="coordinator",
                    effective_from=arrangement_at,
                )["seats"][0]
                route_body["seats"].append(coordinator_seat)
                unavailable = self._prepare_route(
                    f"p4-unavailable-{index}",
                    body=route_body,
                    enable=True,
                )
                unavailable_request = self._request(
                    unavailable,
                    unavailable.founder,
                    f"p4-unavailable-{index}",
                )
                before_decision = governance_route_state(unavailable.route)
                with self.assertRaises(SBGGovernanceRefused) as unavailable_refusal:
                    self._decide(
                        unavailable,
                        f"p4-unavailable-{index}-decision",
                        request_uuid=unavailable_request.request_uuid,
                    )
                self.assertIs(
                    unavailable_refusal.exception.refusal_code,
                    S015AuthorityRefusalCode.SBG_SEAT_ROUTE_UNAVAILABLE,
                )
                self.assertEqual(
                    str(unavailable_refusal.exception),
                    f"Nobody was seated and nothing changed. Seat founder is filled by {route_detail}. "
                    "Use a seat the LO Coordinators appoint, or wait for that route. "
                    "Your own request for seat founder is recorded and remains open.",
                )
                self.assertEqual(governance_route_state(unavailable.route), before_decision)
                self.assertTrue(
                    CircleJoinRequest.objects.filter(
                        request_uuid=unavailable_request.request_uuid,
                    ).exists()
                )
                self.assertTrue(
                    OrganismCommandReceipt.objects.filter(
                        actor=unavailable.founder,
                        action=OrganismAction.REQUEST_GOVERNANCE_SEAT.value,
                        idempotency_key=f"sbg:g4b:request:key:p4-unavailable-{index}",
                    ).exists()
                )
                self.assertFalse(
                    AuthorityDecision.objects.filter(
                        actor=unavailable.founder,
                        request_reference=f"sbg:g4b:decision:p4-unavailable-{index}-decision",
                    ).exists()
                )
                self.assertFalse(
                    OrganismCommandReceipt.objects.filter(
                        actor=unavailable.founder,
                        action=OrganismAction.ADMIT_CIRCLE_PARTICIPANT.value,
                    ).exists()
                )

    def test_w_r3_service_uses_the_seated_person_for_occupancy(self):
        body = governance_arrangement_body(effective_from=database_now())
        second_seat = dict(body["seats"][0])
        second_seat["seat_id"] = "second"
        body["seats"].append(second_seat)
        route = self._prepare_route("r3-service", body=body)
        applicant = add_active_elector(route.route, "r3-applicant")
        first_request = self._request(route, applicant, "r3-first")
        first_result = self._decide(
            route,
            "r3-first-decision",
            request_uuid=first_request.request_uuid,
        )
        first_participation = CircleParticipation.objects.get(
            participation_uuid=first_result.participation_uuid,
        )

        second_request = self._request(
            route,
            applicant,
            "r3-same-person",
            seat_id="second",
        )
        before_decision = self._record_counts()
        with self.assertRaises(SBGGovernanceRefused) as already_seated:
            self._decide(
                route,
                "r3-same-person-decision",
                request_uuid=second_request.request_uuid,
            )
        self.assertIs(
            already_seated.exception.refusal_code,
            S015AuthorityRefusalCode.SBG_ALREADY_SEATED,
        )
        self.assertEqual(self._record_counts(), before_decision)

        other_applicant = add_active_elector(route.route, "r3-other-applicant")
        occupied_request = self._request(route, other_applicant, "r3-occupied")
        before_decision = self._record_counts()
        with self.assertRaises(SBGGovernanceRefused) as occupied:
            self._decide(
                route,
                "r3-occupied-decision",
                request_uuid=occupied_request.request_uuid,
            )
        self.assertIs(
            occupied.exception.refusal_code,
            S015AuthorityRefusalCode.SBG_SEAT_OCCUPIED,
        )
        self.assertEqual(self._record_counts(), before_decision)

        before_missing = self._record_counts()
        with self.assertRaises(SBGGovernanceRefused) as absent:
            self._request(route, other_applicant, "r3-absent", seat_id="absent")
        self.assertIs(
            absent.exception.refusal_code,
            S015AuthorityRefusalCode.SBG_SEAT_ABSENT,
        )
        self.assertEqual(self._record_counts(), before_missing)

        inactive = add_active_elector(route.route, "r3-inactive-member")
        inactive_membership = OrganismMembership.objects.get(
            living_organism=route.organism,
            identity=inactive,
        )
        leave_at = database_now()
        OrganismMembershipService(
            authority=S015Authority(
                provider=ProductionApplicationAuthorityProvider(database_alias="default"),
                database_alias="default",
            ),
            clock=database_now,
            database_alias="default",
        ).transition_membership(
            TransitionMembershipCommand(
                credential=inactive.credential,
                request_reference="sbg:g4b:r3-inactive:leave",
                idempotency_key="sbg:g4b:r3-inactive:leave-key",
                occurred_at=leave_at,
                effective_at=leave_at,
                membership_uuid=inactive_membership.membership_uuid,
                resulting_state="ENDED",
                reason_class="TEST_ONLY_REASON_LEAVE",
            )
        )
        before_inactive = governance_route_state(route)
        before_inactive_records = self._record_counts()
        with self.assertRaises(SBGGovernanceRefused) as inactive_refusal:
            self._request(route, inactive, "r3-inactive-member")
        self.assertIs(
            inactive_refusal.exception.refusal_code,
            S015AuthorityRefusalCode.SBG_MEMBER_NOT_ELIGIBLE,
        )
        self.assertEqual(
            str(inactive_refusal.exception),
            "Nobody was seated and nothing changed. A GOVERNANCE seat needs an active member "
            "of this LO at that time, and this person is not an active member at that time. "
            "Seat someone who is an active member of this LO.",
        )
        self.assertEqual(governance_route_state(route), before_inactive)
        self.assertEqual(self._record_counts(), before_inactive_records)

        other_route = prepare_route(
            tag=f"sbg-g4b-r3-other-lo-{uuid4().hex}",
            governance_enabled=False,
        )
        before_other_lo = self._record_counts()
        with self.assertRaises(SBGGovernanceRefused) as other_lo:
            self._request(route, other_route.founder, "r3-other-lo")
        self.assertIs(
            other_lo.exception.refusal_code,
            S015AuthorityRefusalCode.SBG_MEMBER_NOT_ELIGIBLE,
        )
        self.assertEqual(self._record_counts(), before_other_lo)

        second_version = governance_arrangement_body(
            effective_from=route_test_now(route.route),
        )
        adopt_governance_arrangement(
            route.route,
            route.service,
            tag="r3-keep-seat-version",
            body=second_version,
            rule_uuid=route.rule_uuid,
            version=2,
        )
        self.assertFalse(
            CircleParticipationEnd.objects.filter(
                participation=first_participation,
            ).exists()
        )
        self.assertTrue(
            CircleParticipation.objects.filter(
                circle__circle_uuid=route.circle_uuid,
                membership__identity=applicant,
                seat_rule_uuid=route.rule_uuid,
                seat_id="founder",
            ).exists()
        )
        before_omission = self._record_counts()
        with self.assertRaises(SBGGovernanceRefused) as omission:
            adopt_governance_arrangement(
                route.route,
                route.service,
                tag="r3-omit-held-seat",
                body=governance_arrangement_body(
                    seat_id="replacement",
                    effective_from=route_test_now(route.route),
                ),
                rule_uuid=route.rule_uuid,
                version=3,
            )
        self.assertIs(
            omission.exception.refusal_code,
            S015AuthorityRefusalCode.SBG_VERSION_DROPS_HELD_SEAT,
        )
        self.assertEqual(self._record_counts(), before_omission)

    def test_w_r3_succession_boundary_later_and_historical_known_at(self):
        route = self._prepare_route("r3-succession")
        first_applicant = add_active_elector(route.route, "r3-succession-first")
        second_applicant = add_active_elector(route.route, "r3-succession-second")
        third_applicant = add_active_elector(route.route, "r3-succession-third")
        first_effective_at = route_test_now(route.route)
        first_request = self._request(
            route,
            first_applicant,
            "r3-succession-first",
            effective_at=first_effective_at,
        )
        first_result = self._decide(
            route,
            "r3-succession-first",
            request_uuid=first_request.request_uuid,
            effective_at=first_effective_at,
        )
        first_participation = CircleParticipation.objects.get(
            participation_uuid=first_result.participation_uuid,
        )
        historical_known_at = first_participation.recorded_at - timedelta(
            microseconds=1,
        )
        second_membership = OrganismMembership.objects.get(
            living_organism=route.organism,
            identity=second_applicant,
        )
        with transaction.atomic():
            route.service._validate_constitutional_seating(
                organism=route.organism,
                membership=second_membership,
                payload={
                    "circle_uuid": str(route.circle_uuid),
                    "seat_rule_uuid": str(route.rule_uuid),
                    "seat_id": "founder",
                },
                effective_at=first_effective_at,
                received_at=historical_known_at,
                require_enabled=True,
                check_occupancy=True,
            )

        boundary_at = database_now()
        self._end_participation_for_w_r3(
            first_participation,
            boundary_at,
            "r3-succession-boundary",
        )
        second_request = self._request(
            route,
            second_applicant,
            "r3-succession-boundary",
            effective_at=boundary_at,
        )
        second_result = self._decide(
            route,
            "r3-succession-boundary",
            request_uuid=second_request.request_uuid,
            effective_at=boundary_at,
        )
        second_participation = CircleParticipation.objects.get(
            participation_uuid=second_result.participation_uuid,
        )
        self.assertEqual(second_participation.effective_at, boundary_at)

        later_ending_at = database_now()
        self._end_participation_for_w_r3(
            second_participation,
            later_ending_at,
            "r3-succession-later",
        )
        later_admission_at = database_now()
        self.assertGreater(later_admission_at, later_ending_at)
        third_request = self._request(
            route,
            third_applicant,
            "r3-succession-later",
            effective_at=later_admission_at,
        )
        third_result = self._decide(
            route,
            "r3-succession-later",
            request_uuid=third_request.request_uuid,
            effective_at=later_admission_at,
        )
        third_participation = CircleParticipation.objects.get(
            participation_uuid=third_result.participation_uuid,
        )
        self.assertEqual(third_participation.effective_at, later_admission_at)

    def test_w_r3_backdated_and_concurrent_direct_participation_writes(self):
        route = self._prepare_route("r3-direct")
        applicant = add_active_elector(route.route, "r3-direct-current")
        request_result = self._request(route, applicant, "r3-direct-current")
        accepted = self._decide(
            route,
            "r3-direct-current-decision",
            request_uuid=request_result.request_uuid,
        )
        existing = CircleParticipation.objects.get(
            participation_uuid=accepted.participation_uuid,
        )
        backdated = self._direct_decision_rows(
            route,
            OrganismMembership.objects.get(
                living_organism=route.organism,
                identity=route.founder,
            ),
            "r3-backdated",
            existing.effective_at - timedelta(microseconds=1),
        )
        before = self._record_counts()
        with self.assertRaises(IntegrityError) as backdated_refusal:
            with transaction.atomic():
                self._insert_participation_sql(
                    route,
                    backdated.request.membership,
                    backdated,
                    backdated.effective_at,
                )
        self.assertIn("SBG_SEAT_OCCUPIED", str(backdated_refusal.exception))
        self.assertEqual(self._record_counts(), before)

        founding = prepare_governance_route()
        founding_participation = CircleParticipation.objects.get(
            participation_uuid=founding.seating.participation_uuid,
        )
        founding_membership = OrganismMembership.objects.get(
            living_organism=founding.organism,
            identity=founding.founder,
        )
        founding_duplicate = self._direct_decision_rows(
            founding,
            founding_membership,
            "r3-founding-duplicate",
            founding_participation.effective_at,
            admission_route="CIRCLE_FOUNDING",
        )
        before_founding_guard = self._record_counts()
        with self.assertRaises(IntegrityError) as founding_guard_refusal:
            with transaction.atomic():
                self._insert_participation_sql(
                    founding,
                    founding_membership,
                    founding_duplicate,
                    founding_participation.effective_at,
                )
        self.assertIn("SBG_SEAT_OCCUPIED", str(founding_guard_refusal.exception))
        self.assertEqual(self._record_counts(), before_founding_guard)

        concurrent_route = self._prepare_route("r3-concurrent")
        members = (
            add_active_elector(concurrent_route.route, "r3-concurrent-a"),
            add_active_elector(concurrent_route.route, "r3-concurrent-b"),
        )
        effective_at = route_test_now(concurrent_route.route)
        admissions = tuple(
            self._direct_decision_rows(
                concurrent_route,
                OrganismMembership.objects.get(
                    living_organism=concurrent_route.organism,
                    identity=member,
                ),
                f"r3-concurrent-{index}",
                effective_at,
            )
            for index, member in enumerate(members)
        )
        barrier = Barrier(2)

        def insert_direct(membership, admission):
            close_old_connections()
            try:
                with transaction.atomic():
                    barrier.wait(timeout=20)
                    self._insert_participation_sql(
                        concurrent_route,
                        membership,
                        admission,
                        effective_at,
                    )
                return "COMMITTED"
            except IntegrityError as exc:
                return str(exc)
            finally:
                connections["default"].close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = tuple(
                executor.map(
                    lambda pair: insert_direct(*pair),
                    (
                        (
                            OrganismMembership.objects.get(
                                living_organism=concurrent_route.organism,
                                identity=members[0],
                            ),
                            admissions[0],
                        ),
                        (
                            OrganismMembership.objects.get(
                                living_organism=concurrent_route.organism,
                                identity=members[1],
                            ),
                            admissions[1],
                        ),
                    ),
                )
            )
        self.assertEqual(outcomes.count("COMMITTED"), 1)
        self.assertEqual(
            sum("SBG_SEAT_OCCUPIED" in outcome for outcome in outcomes),
            1,
        )
        self.assertEqual(
            CircleParticipation.objects.filter(
                circle__circle_uuid=concurrent_route.circle_uuid,
                seat_id="founder",
            ).count(),
            1,
        )

    def test_w_r3_concurrent_service_admissions_serialize(self):
        route = self._prepare_route("r3-service-concurrent")
        applicants = (
            add_active_elector(route.route, "r3-service-concurrent-a"),
            add_active_elector(route.route, "r3-service-concurrent-b"),
        )
        requests = tuple(
            self._request(route, applicant, f"r3-service-concurrent-{index}")
            for index, applicant in enumerate(applicants)
        )
        before_admissions = governance_route_state(route)
        barrier = Barrier(2)

        def decide(request_uuid, tag):
            close_old_connections()
            try:
                barrier.wait(timeout=20)
                result = self._decide(
                    route,
                    tag,
                    request_uuid=request_uuid,
                )
                return ("ACCEPTED", request_uuid, result.participation_uuid)
            except SBGGovernanceRefused as exc:
                cause = exc.__cause__
                while cause is not None and not isinstance(cause, IntegrityError):
                    cause = cause.__cause__
                refusal_layer = (
                    "CLASS_B_GUARD" if isinstance(cause, IntegrityError) else "CLASS_C_SERVICE"
                )
                return (
                    "REFUSED",
                    request_uuid,
                    exc.refusal_code,
                    refusal_layer,
                    str(exc),
                    exc.request_uuid,
                )
            finally:
                connections["default"].close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = tuple(
                executor.map(
                    lambda item: decide(*item),
                    (
                        (requests[0].request_uuid, "r3-service-concurrent-a"),
                        (requests[1].request_uuid, "r3-service-concurrent-b"),
                    ),
                )
            )
        self.assertEqual(sum(outcome[0] == "ACCEPTED" for outcome in outcomes), 1)
        winner = next(outcome for outcome in outcomes if outcome[0] == "ACCEPTED")
        refusal = next(outcome for outcome in outcomes if outcome[0] == "REFUSED")
        print(f"Observed losing-admission refusal layer: {refusal[3]}")
        self.assertIs(
            refusal[2],
            S015AuthorityRefusalCode.SBG_SEAT_OCCUPIED,
        )
        self.assertIn(
            refusal[3],
            {"CLASS_B_GUARD", "CLASS_C_SERVICE"},
            msg=f"Observed losing-admission refusal layer: {refusal[3]}",
        )
        self.assertEqual(refusal[5], refusal[1])
        self.assertEqual(
            refusal[4],
            "Nobody was seated and nothing changed. Seat founder is already held at that time, and "
            "each seat holds one person. Choose a vacant seat, or wait until this seat becomes vacant. "
            "A seat becomes vacant when its holder's seat ends. Your own request for seat founder "
            "is recorded and remains open.",
        )
        expected_state = before_admissions.copy()
        expected_state["authority_decisions"] += 1
        expected_state["receipts"] += 1
        expected_state["admission_decisions"] += 1
        expected_state["participations"] += 1
        self.assertEqual(governance_route_state(route), expected_state)
        self.assertTrue(
            CircleJoinRequest.objects.filter(request_uuid=refusal[1]).exists()
        )
        self.assertEqual(
            CircleAdmissionDecision.objects.filter(
                request__request_uuid=winner[1],
            ).count(),
            1,
        )
        self.assertFalse(
            CircleAdmissionDecision.objects.filter(
                request__request_uuid=refusal[1],
            ).exists()
        )
        self.assertFalse(
            CircleParticipation.objects.filter(
                decision__request__request_uuid=refusal[1],
            ).exists()
        )
        refusal_index = next(
            index
            for index, request in enumerate(requests)
            if request.request_uuid == refusal[1]
        )
        refusal_reference = f"sbg:g4b:decision:r3-service-concurrent-{refusal_index}"
        self.assertFalse(
            AuthorityDecision.objects.filter(
                actor=route.founder,
                request_reference=refusal_reference,
            ).exists()
        )
        self.assertFalse(
            OrganismCommandReceipt.objects.filter(
                actor=route.founder,
                request_reference=refusal_reference,
            ).exists()
        )
        self.assertEqual(
            CircleParticipation.objects.filter(
                circle__circle_uuid=route.circle_uuid,
                seat_id="founder",
            ).count(),
            1,
        )


class SBGGovernanceDepartureCascadeTests(
    SBGGovernanceRequestTestMixin,
    TransactionTestCase,
):
    def _prepare_departure_route(self, tag, *, seat_founder=False):
        effective_from = database_now()
        body = governance_arrangement_body(effective_from=effective_from)
        second_seat = dict(body["seats"][0])
        second_seat["seat_id"] = "second"
        body["seats"].append(second_seat)
        route = prepare_governance_route(
            tag=f"sbg-g5-{uuid4().hex}",
            governance_enabled=True,
            seat_founder=seat_founder,
            body=body,
        )
        advance_to_birthing(route.route, tag=f"{route.route.tag}:phase")
        birth(route.route, suffix=f"sbg-g5:{tag}")
        return route

    def _seat_member(self, route, applicant, tag, *, seat_id="founder", effective_at=None):
        effective_at = (
            route_test_now(route.route) if effective_at is None else effective_at
        )
        request = self._request(
            route,
            applicant,
            f"g5:{tag}:request",
            seat_id=seat_id,
            effective_at=effective_at,
        )
        result = route.service.decide_governance_seat(DecideGovernanceSeatCommand(
            credential=route.founder.credential,
            living_organism_uuid=route.organism.organism_id,
            request_uuid=request.request_uuid,
            request_reference=f"sbg:g5:decision:{tag}",
            idempotency_key=f"sbg:g5:decision:key:{tag}",
            outcome="ACCEPT",
            effective_at=effective_at,
        ))
        participation = CircleParticipation.objects.get(
            participation_uuid=result.participation_uuid,
        )
        return request, result, participation

    def _membership_for(self, route, identity):
        return OrganismMembership.objects.get(
            living_organism=route.organism,
            identity=identity,
        )

    def _membership_service(self):
        return OrganismMembershipService(
            authority=S015Authority(
                provider=ProductionApplicationAuthorityProvider(
                    database_alias="default",
                ),
                database_alias="default",
            ),
            clock=database_now,
            database_alias="default",
        )

    def _departure_command(self, route, membership, tag, effective_at):
        return TransitionMembershipCommand(
            credential=membership.identity.credential,
            request_reference=f"sbg:g5:departure:{tag}",
            idempotency_key=f"sbg:g5:departure:key:{tag}",
            occurred_at=effective_at,
            effective_at=effective_at,
            membership_uuid=membership.membership_uuid,
            resulting_state="ENDED",
            reason_class="TEST_ONLY_REASON_LEAVE",
        )

    def _whole_departure_state(self, route, membership):
        membership.refresh_from_db()
        return {
            "route": governance_route_state(route),
            "global_counts": {
                "authority_decisions": AuthorityDecision.objects.count(),
                "receipts": OrganismCommandReceipt.objects.count(),
                "membership_transitions": OrganismMembershipTransition.objects.count(),
                "participation_endings": CircleParticipationEnd.objects.count(),
            },
            "organism_transitions": OrganismMembershipTransition.objects.filter(
                membership__living_organism=route.organism,
            ).count(),
            "membership_transition_count": OrganismMembershipTransition.objects.filter(
                membership=membership,
            ).count(),
            "membership": (
                membership.state,
                membership.head_transition_id,
                membership.current_state_effective_at,
                membership.state_known_at,
                membership.state_source_event_set_fingerprint,
            ),
        }

    def _departure_precedes_participation_text(
        self,
        membership,
        participation,
        effective_at,
    ):
        identity = membership.identity
        person = identity.display_name or identity.canonical_username
        return (
            f"{person} was not recorded as leaving {membership.living_organism.slug}, "
            "and nothing changed. The leaving date given, "
            f"{canonical_timestamp(effective_at)}, is earlier than the start of their "
            f"GOVERNANCE seat {participation.seat_id}, which began on "
            f"{canonical_timestamp(participation.effective_at)}. A member cannot leave "
            "before a seat they took while still a member. Check the leaving date. If the "
            "seat itself was recorded in error, it must be corrected through its own "
            "governed route first."
        )

    def test_w_d3_1_departure_composes_a_causally_linked_ending(self):
        self.maxDiff = None
        route = self._prepare_departure_route("ending")
        applicant = add_active_elector(route.route, "g5-ending-applicant")
        _, decision, participation = self._seat_member(route, applicant, "ending")
        membership = self._membership_for(route, applicant)
        leave_at = database_now()
        before = self._whole_departure_state(route, membership)
        command = self._departure_command(route, membership, "ending", leave_at)
        result = self._membership_service().transition_membership(command)

        membership.refresh_from_db()
        transition = OrganismMembershipTransition.objects.get(
            pk=membership.head_transition_id,
        )
        intake = CommandIntake.objects.get(
            actor_id=applicant.pk,
            idempotency_key=command.idempotency_key,
        )
        ending = CircleParticipationEnd.objects.get(participation=participation)
        self.assertEqual(result.resulting_state, "ENDED")
        self.assertEqual(membership.state, "ENDED")
        self.assertEqual(ending.actor_state, "NONE")
        self.assertIsNone(ending.actor_id)
        self.assertIsNone(ending.actor_access_epoch)
        self.assertEqual(ending.actor_capacity, "NA_NO_PARTY")
        self.assertEqual(ending.entry_mode, "COMPOSED")
        self.assertEqual(ending.composing_rule_state, "REFERENCE")
        platform_rule = GovernedRule.objects.get(
            scope_kind="PLATFORM",
            code="U3_MEMBERSHIP_END_ENDS_CIRCLE_PARTICIPATION",
            version=1,
        )
        self.assertEqual(ending.composing_rule_id, platform_rule.pk)
        self.assertEqual(
            ending.composing_rule_reference,
            f"rule:{platform_rule.rule_uuid}@{platform_rule.version}",
        )
        self.assertEqual(ending.composer_state, "REFERENCE")
        self.assertEqual(ending.composer_identity_id, applicant.pk)
        self.assertEqual(ending.enterer_identity_id, applicant.pk)
        self.assertEqual(ending.composed_from_event_uuid, transition.event_uuid)
        self.assertEqual(ending.effective_at, transition.effective_at)
        self.assertEqual(ending.occurred_at, transition.effective_at)
        self.assertEqual(transition.received_at, intake.received_at)
        self.assertEqual(ending.received_at, intake.received_at)
        self.assertEqual(transition.authority_decision_reference, result.authority_decision_reference)
        self.assertEqual(decision.participation_uuid, participation.participation_uuid)
        expected_route = before["route"].copy()
        expected_route["participation_endings"] += 1
        expected_counts = before["global_counts"].copy()
        for key in (
            "authority_decisions",
            "receipts",
            "membership_transitions",
            "participation_endings",
        ):
            expected_counts[key] += 1
        after = self._whole_departure_state(route, membership)
        self.assertEqual(after["route"], expected_route)
        self.assertEqual(after["global_counts"], expected_counts)
        self.assertEqual(
            after["organism_transitions"],
            before["organism_transitions"] + 1,
        )
        self.assertLessEqual(ending.effective_at, leave_at)

    def test_w_d3_2_future_seat_refusals_map_at_service_and_guard(self):
        self.maxDiff = None
        route = self._prepare_departure_route("future-seat")
        applicant = add_active_elector(route.route, "g5-future-seat-applicant")
        seat_at = route_test_now(route.route)
        _, _, participation = self._seat_member(
            route,
            applicant,
            "future-seat",
            effective_at=seat_at,
        )
        membership = self._membership_for(route, applicant)
        previous = OrganismMembershipTransition.objects.filter(
            membership=membership,
        ).order_by("-sequence").first()
        leave_at = seat_at - timedelta(microseconds=1)
        self.assertIsNotNone(previous)
        self.assertLess(previous.effective_at, leave_at)
        before = self._whole_departure_state(route, membership)

        with self.assertRaises(S015MembershipRefused) as refused:
            self._membership_service().transition_membership(
                self._departure_command(
                    route,
                    membership,
                    "future-seat",
                    leave_at,
                )
            )

        self.assertIs(
            refused.exception.refusal_code,
            S015AuthorityRefusalCode.SBG_DEPARTURE_PRECEDES_PARTICIPATION,
        )
        self.assertEqual(
            str(refused.exception),
            self._departure_precedes_participation_text(
                membership,
                participation,
                leave_at,
            ),
        )
        self.assertEqual(self._whole_departure_state(route, membership), before)

        guard_route = self._prepare_departure_route("guard-backstop")
        guard_applicant = add_active_elector(
            guard_route.route,
            "g5-guard-applicant",
        )
        guard_seat_at = route_test_now(guard_route.route)
        _, _, guard_participation = self._seat_member(
            guard_route,
            guard_applicant,
            "guard-backstop",
            effective_at=guard_seat_at,
        )
        guard_membership = self._membership_for(guard_route, guard_applicant)
        guard_previous = OrganismMembershipTransition.objects.filter(
            membership=guard_membership,
        ).order_by("-sequence").first()
        guard_leave_at = guard_seat_at - timedelta(microseconds=1)
        self.assertIsNotNone(guard_previous)
        self.assertLess(guard_previous.effective_at, guard_leave_at)
        guard_before = self._whole_departure_state(
            guard_route,
            guard_membership,
        )
        service = self._membership_service()
        original_check = service._departure_precedes_participation
        calls = []

        def defer_to_database_guard(candidate_membership, effective_at):
            calls.append(True)
            if len(calls) == 1:
                return None
            return original_check(candidate_membership, effective_at)

        with patch.object(
            service,
            "_departure_precedes_participation",
            side_effect=defer_to_database_guard,
        ):
            with self.assertRaises(S015MembershipRefused) as guard_refusal:
                service.transition_membership(
                    self._departure_command(
                        guard_route,
                        guard_membership,
                        "guard-backstop",
                        guard_leave_at,
                    )
                )

        self.assertEqual(calls, [True])
        self.assertIs(
            guard_refusal.exception.refusal_code,
            S015AuthorityRefusalCode.SBG_DEPARTURE_PRECEDES_PARTICIPATION,
        )
        self.assertEqual(
            str(guard_refusal.exception),
            self._departure_precedes_participation_text(
                guard_membership,
                guard_participation,
                guard_leave_at,
            ),
        )
        self.assertEqual(
            self._whole_departure_state(guard_route, guard_membership),
            guard_before,
        )

    def test_w_d3_3_il2_precedes_a_future_governance_seat_refusal(self):
        self.maxDiff = None
        route = prepare_governance_route(
            tag=f"sbg-g5-{uuid4().hex}",
            governance_enabled=True,
            seat_founder=True,
        )
        advance_to_birthing(route.route, tag=f"{route.route.tag}:phase")
        birth(route.route, suffix="sbg-g5:il2-precedence")
        membership = self._membership_for(route, route.founder)
        participation = CircleParticipation.objects.get(
            circle__circle_uuid=route.circle_uuid,
            membership=membership,
        )
        previous = OrganismMembershipTransition.objects.filter(
            membership=membership,
        ).order_by("-sequence").first()
        leave_at = participation.effective_at - timedelta(microseconds=1)
        self.assertIsNotNone(previous)
        self.assertLess(previous.effective_at, leave_at)
        before = self._whole_departure_state(route, membership)
        membership_service = self._membership_service()
        departure_command = self._departure_command(
            route,
            membership,
            "il2-precedence",
            leave_at,
        )

        with self.assertRaises(S015AuthorityDenied) as refused:
            before_decision_ids = set(
                AuthorityDecision.objects.values_list("pk", flat=True),
            )
            membership_service.transition_membership(departure_command)

        self.assertIs(
            refused.exception.refusal_code,
            S015AuthorityRefusalCode.C_MEMBERSHIP_LEAVE_WITH_OFFICE_NOT_IMPLEMENTED,
        )
        self.assertIs(
            refused.exception.disclosed_refusal_code,
            S015AuthorityRefusalCode.C_MEMBERSHIP_LEAVE_WITH_OFFICE_NOT_IMPLEMENTED,
        )
        self.assertEqual(
            str(refused.exception),
            "leaving while holding an office is not yet implemented (IL-2)",
        )
        new_decisions = list(
            AuthorityDecision.objects.exclude(pk__in=before_decision_ids),
        )
        self.assertEqual(len(new_decisions), 1)
        refusal_decision = new_decisions[0]
        self.assertEqual(
            refusal_decision.decision_reference,
            refused.exception.authority_decision_reference,
        )
        self.assertEqual(
            refusal_decision.action,
            OrganismAction.TRANSITION_MEMBERSHIP.value,
        )
        self.assertEqual(refusal_decision.outcome, "REFUSED")
        self.assertEqual(
            refusal_decision.refusal_code,
            S015AuthorityRefusalCode.C_MEMBERSHIP_LEAVE_WITH_OFFICE_NOT_IMPLEMENTED.value,
        )
        expected_after = deepcopy(before)
        expected_after["route"]["authority_decisions"] += 1
        expected_after["global_counts"]["authority_decisions"] = (
            before["global_counts"]["authority_decisions"] + 1
        )
        self.assertEqual(self._whole_departure_state(route, membership), expected_after)
        self.assertEqual(
            OrganismMembershipTransition.objects.filter(membership=membership).count(),
            before["membership_transition_count"],
        )
        self.assertFalse(
            CircleParticipationEnd.objects.filter(participation=participation).exists()
        )

    def test_w_d3_4_failure_after_cascade_write_rolls_back_the_whole_departure(self):
        self.maxDiff = None
        route = self._prepare_departure_route("rollback-after-ending")
        applicant = add_active_elector(route.route, "g5-rollback-applicant")
        self._seat_member(route, applicant, "rollback-after-ending")
        membership = self._membership_for(route, applicant)
        before = self._whole_departure_state(route, membership)
        service = self._membership_service()

        with patch.object(service, "_finalise", side_effect=RuntimeError("fixture fault")):
            with self.assertRaisesRegex(RuntimeError, "fixture fault"):
                service.transition_membership(
                    self._departure_command(
                        route,
                        membership,
                        "rollback-after-ending",
                        database_now(),
                    )
                )

        self.assertEqual(self._whole_departure_state(route, membership), before)
        self.assertFalse(
            CircleParticipationEnd.objects.filter(
                participation__membership=membership,
            ).exists()
        )

    @unittest.skip(
        "Human Governor ruling 13 Oct 2026: never run again (clock design; ledger H82)"
    )
    def test_w_d3_5_departure_vacates_the_seat_for_equal_time_succession(self):
        route = self._prepare_departure_route("seat-succession")
        departing = add_active_elector(route.route, "g5-succession-departing")
        successor = add_active_elector(route.route, "g5-succession-successor")
        _, _, first_participation = self._seat_member(
            route,
            departing,
            "succession-first",
        )
        departure_at = database_now()
        self._membership_service().transition_membership(
            self._departure_command(
                route,
                self._membership_for(route, departing),
                "succession-departure",
                departure_at,
            )
        )

        _, _, second_participation = self._seat_member(
            route,
            successor,
            "succession-second",
            effective_at=departure_at,
        )

        ending = CircleParticipationEnd.objects.get(participation=first_participation)
        self.assertEqual(ending.effective_at, departure_at)
        self.assertEqual(second_participation.effective_at, departure_at)
        self.assertEqual(second_participation.seat_id, first_participation.seat_id)

    def test_w_d3_6_departure_keeps_the_original_participation_history(self):
        self.maxDiff = None
        route = self._prepare_departure_route("history")
        applicant = add_active_elector(route.route, "g5-history-applicant")
        request, _, participation = self._seat_member(route, applicant, "history")
        membership = self._membership_for(route, applicant)
        self._membership_service().transition_membership(
            self._departure_command(
                route,
                membership,
                "history",
                database_now(),
            )
        )
        ending = CircleParticipationEnd.objects.get(participation=participation)
        historical_known_at = ending.recorded_at - timedelta(microseconds=1)
        self.assertTrue(
            CircleParticipation.objects.filter(
                pk=participation.pk,
                recorded_at__lte=historical_known_at,
                effective_at__lte=participation.effective_at,
            ).exists()
        )
        self.assertFalse(
            CircleParticipationEnd.objects.filter(
                participation=participation,
                recorded_at__lte=historical_known_at,
            ).exists()
        )
        self.assertGreater(ending.recorded_at, historical_known_at)

        before_validation = self._whole_departure_state(route, membership)
        with self.assertRaises(SBGGovernanceRefused) as refused:
            with transaction.atomic():
                route.service._validate_constitutional_seating(
                    organism=route.organism,
                    membership=membership,
                    payload={
                        "circle_uuid": str(route.circle_uuid),
                        "seat_rule_uuid": str(route.rule_uuid),
                        "seat_id": participation.seat_id,
                    },
                    effective_at=participation.effective_at,
                    received_at=historical_known_at,
                    require_enabled=True,
                    check_occupancy=True,
                )
        self.assertIs(
            refused.exception.refusal_code,
            S015AuthorityRefusalCode.SBG_SEAT_OCCUPIED,
        )
        self.assertEqual(
            str(refused.exception),
            "Nobody was seated and nothing changed. Seat founder is already held at that time, "
            "and each seat holds one person. Choose a vacant seat, or wait until this seat becomes "
            "vacant. A seat becomes vacant when its holder's seat ends.",
        )
        self.assertEqual(
            self._whole_departure_state(route, membership),
            before_validation,
        )

        self.assertTrue(
            CircleJoinRequest.objects.filter(request_uuid=request.request_uuid).exists()
        )
        self.assertTrue(
            CircleAdmissionDecision.objects.filter(
                request__request_uuid=request.request_uuid,
            ).exists()
        )
        self.assertEqual(
            CircleParticipation.objects.filter(pk=participation.pk).count(),
            1,
        )
        self.assertEqual(
            CircleParticipationEnd.objects.filter(participation=participation).count(),
            1,
        )

    def test_w_d3_7_probationary_departure_composes_no_circle_ending(self):
        route = self._prepare_departure_route("probationary")
        outside_route = prepare_route(
            tag=f"sbg-g5-{uuid4().hex}",
            governance_enabled=False,
        )
        identity = outside_route.founder
        effective_at = route_test_now(route.route)
        known_at = effective_at
        membership_uuid = uuid4()
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO public.core_organismmembership
                    (membership_uuid, state, created_at, current_state_effective_at,
                     state_known_at, state_source_event_set_fingerprint,
                     identity_id, living_organism_id)
                VALUES (%s, 'PROBATIONARY', clock_timestamp(), %s, %s, %s, %s, %s)
                RETURNING id
                """,
                [
                    membership_uuid,
                    effective_at,
                    known_at,
                    empty_fp(cursor, "core_organismmembership", membership_uuid),
                    identity.pk,
                    route.organism.pk,
                ],
            )
            membership_id = cursor.fetchone()[0]
            append_event(
                cursor,
                "core_organismmembershiptransition",
                membership_id,
                1,
                "JOIN",
                None,
                "PROBATIONARY",
                route.founder.pk,
                route.organism.founding_authority_basis_id,
                "test-sbg-probationary-member",
                eff=effective_at,
                occ=effective_at,
                rec=known_at,
                extra={"reason_class": "TEST_ONLY_PROBATIONARY_FIXTURE"},
            )
            refresh(
                cursor,
                "core_organismmembership",
                membership_id,
                effective_at,
            )
        membership = OrganismMembership.objects.get(pk=membership_id)
        self.assertEqual(membership.state, "PROBATIONARY")
        self.assertFalse(
            CircleParticipation.objects.filter(membership=membership).exists()
        )

        result = self._membership_service().transition_membership(
            self._departure_command(
                route,
                membership,
                "probationary",
                database_now(),
            )
        )

        membership.refresh_from_db()
        self.assertEqual(result.resulting_state, "ENDED")
        self.assertEqual(membership.state, "ENDED")
        self.assertFalse(
            CircleParticipationEnd.objects.filter(
                participation__membership=membership,
            ).exists()
        )

    def test_w_d3_8_sql_guards_refuse_incomplete_departure_and_invalid_endings(self):
        self.maxDiff = None
        route = self._prepare_departure_route("sql-incomplete")
        applicant = add_active_elector(route.route, "g5-sql-incomplete-applicant")
        self._seat_member(route, applicant, "sql-incomplete")
        membership = self._membership_for(route, applicant)
        previous = OrganismMembershipTransition.objects.filter(
            membership=membership,
        ).order_by("-sequence").first()
        before = self._whole_departure_state(route, membership)
        effective_at = database_now()

        with self.assertRaises(IntegrityError) as refused:
            with transaction.atomic(), connection.cursor() as cursor:
                append_event(
                    cursor,
                    "core_organismmembershiptransition",
                    membership.pk,
                    previous.sequence + 1,
                    "LEAVE",
                    "ACTIVE",
                    "ENDED",
                    membership.identity_id,
                    route.organism.founding_authority_basis_id,
                    "test-sbg-sql-incomplete",
                    pred_id=previous.pk,
                    eff=effective_at,
                    occ=effective_at,
                    rec=effective_at,
                    extra={"reason_class": "TEST_ONLY_REASON_LEAVE"},
                )
                refresh(
                    cursor,
                    "core_organismmembership",
                    membership.pk,
                    effective_at,
                )

        self.assertEqual(
            refused.exception.__cause__.diag.message_primary,
            "SBG_MEMBERSHIP_END_PARTICIPATION_INCOMPLETE",
        )
        self.assertEqual(self._whole_departure_state(route, membership), before)

        participation = CircleParticipation.objects.get(
            membership=membership,
        )
        direct_departure_at = participation.effective_at - timedelta(
            microseconds=1,
        )
        self.assertLess(previous.effective_at, direct_departure_at)
        before_future_departure = self._whole_departure_state(route, membership)
        with self.assertRaises(IntegrityError) as future_refused:
            with transaction.atomic(), connection.cursor() as cursor:
                append_event(
                    cursor,
                    "core_organismmembershiptransition",
                    membership.pk,
                    previous.sequence + 1,
                    "LEAVE",
                    "ACTIVE",
                    "ENDED",
                    membership.identity_id,
                    route.organism.founding_authority_basis_id,
                    "test-sbg-direct-future-participation",
                    pred_id=previous.pk,
                    eff=direct_departure_at,
                    occ=direct_departure_at,
                    rec=direct_departure_at,
                    extra={"reason_class": "TEST_ONLY_REASON_LEAVE"},
                )
                refresh(
                    cursor,
                    "core_organismmembership",
                    membership.pk,
                    direct_departure_at,
                )
        self.assertEqual(
            future_refused.exception.__cause__.diag.message_primary,
            "SBG_DEPARTURE_PRECEDES_PARTICIPATION",
        )
        detail = future_refused.exception.__cause__.diag.message_detail
        detail_data = json.loads(detail)
        self.assertEqual(
            detail_data,
            {
                "seat_id": participation.seat_id,
                "started_at": participation.effective_at.astimezone(
                    datetime_timezone.utc,
                ).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            },
        )
        person_name = (
            membership.identity.display_name
            or membership.identity.canonical_username
        )
        self.assertNotIn(person_name, detail)
        self.assertEqual(
            self._whole_departure_state(route, membership),
            before_future_departure,
        )

        platform_rule = GovernedRule.objects.get(
            scope_kind="PLATFORM",
            code="U3_MEMBERSHIP_END_ENDS_CIRCLE_PARTICIPATION",
            version=1,
        )
        arrangement_rule = GovernedRule.objects.get(
            rule_uuid=route.rule_uuid,
            version=1,
        )
        other_transition = OrganismMembershipTransition.objects.filter(
            membership__living_organism=route.organism,
        ).exclude(membership=membership).order_by("-sequence").first()
        self.assertIsNotNone(other_transition)

        def assert_invalid_ending(
            tag,
            *,
            rule,
            rule_reference,
            expected_primary,
            use_other_transition=False,
            ending_at=None,
        ):
            leave_at = database_now()
            received_at = leave_at
            before_invalid = self._whole_departure_state(route, membership)
            with self.assertRaises(IntegrityError) as invalid:
                with transaction.atomic(), connection.cursor() as cursor:
                    transition_id, _ = append_event(
                        cursor,
                        "core_organismmembershiptransition",
                        membership.pk,
                        previous.sequence + 1,
                        "LEAVE",
                        "ACTIVE",
                        "ENDED",
                        membership.identity_id,
                        route.organism.founding_authority_basis_id,
                        f"test-sbg-ending-guard:{tag}",
                        pred_id=previous.pk,
                        eff=leave_at,
                        occ=leave_at,
                        rec=received_at,
                        extra={"reason_class": "TEST_ONLY_REASON_LEAVE"},
                    )
                    transition = OrganismMembershipTransition.objects.get(
                        pk=transition_id,
                    )
                    CircleParticipationEnd.objects.create(
                        participation=participation,
                        actor_state="NONE",
                        actor=None,
                        actor_access_epoch=None,
                        actor_capacity="NA_NO_PARTY",
                        entry_mode="COMPOSED",
                        enterer_identity=membership.identity,
                        composing_rule_state="REFERENCE",
                        composing_rule=rule,
                        composing_rule_reference=rule_reference,
                        composer_state="REFERENCE",
                        composer_identity=membership.identity,
                        composed_from_event_uuid=(
                            other_transition.event_uuid
                            if use_other_transition
                            else transition.event_uuid
                        ),
                        occurred_at=(
                            leave_at if ending_at is None else ending_at
                        ),
                        effective_at=(
                            leave_at if ending_at is None else ending_at
                        ),
                        received_at=received_at,
                    )
            self.assertIn(expected_primary, str(invalid.exception))
            self.assertEqual(
                self._whole_departure_state(route, membership),
                before_invalid,
            )

        with self.subTest(guard="wrong-code"):
            assert_invalid_ending(
                "wrong-code-or-composition",
                rule=arrangement_rule,
                rule_reference=f"rule:{arrangement_rule.rule_uuid}@{arrangement_rule.version}",
                expected_primary="SBG_CIRCLEPARTICIPATIONEND_RULE_INVALID",
            )
        with self.subTest(guard="wrong-reference"):
            assert_invalid_ending(
                "non-operative-version-reference",
                rule=platform_rule,
                rule_reference=f"rule:{platform_rule.rule_uuid}@999",
                expected_primary="SBG_CIRCLEPARTICIPATIONEND_RULE_INVALID",
            )
        with self.subTest(guard="wrong-membership-transition"):
            assert_invalid_ending(
                "wrong-membership-transition",
                rule=platform_rule,
                rule_reference=f"rule:{platform_rule.rule_uuid}@1",
                expected_primary="SBG_CIRCLEPARTICIPATIONEND_TRANSITION_INVALID",
                use_other_transition=True,
            )
        with self.subTest(guard="before-admission"):
            assert_invalid_ending(
                "before-admission",
                rule=platform_rule,
                rule_reference=f"rule:{platform_rule.rule_uuid}@1",
                expected_primary="SBG_CIRCLEPARTICIPATIONEND_TIME_INVALID",
                ending_at=participation.effective_at - timedelta(microseconds=1),
            )

        self._membership_service().transition_membership(
            self._departure_command(
                route,
                membership,
                "valid-before-duplicate-ending",
                database_now(),
            )
        )
        valid_ending = CircleParticipationEnd.objects.get(
            participation=participation,
        )
        after_departure = self._whole_departure_state(route, membership)
        with self.assertRaises(IntegrityError) as duplicate:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO public.core_circleparticipationend
                        (participation_id, actor_state, actor_id,
                         actor_access_epoch, actor_capacity, entry_mode,
                         enterer_identity_id, composing_rule_state,
                         composing_rule_id, composing_rule_reference,
                         composer_state, composer_identity_id,
                         composed_from_event_uuid, occurred_at, effective_at,
                         received_at)
                    SELECT participation_id, actor_state, actor_id,
                           actor_access_epoch, actor_capacity, entry_mode,
                           enterer_identity_id, composing_rule_state,
                           composing_rule_id, composing_rule_reference,
                           composer_state, composer_identity_id,
                           composed_from_event_uuid, occurred_at, effective_at,
                           received_at
                      FROM public.core_circleparticipationend
                     WHERE id = %s
                    """,
                    [valid_ending.pk],
                )
        self.assertEqual(
            duplicate.exception.__cause__.diag.message_primary,
            'duplicate key value violates unique constraint "core_circleparticipationend_participation_id_key"',
        )
        self.assertEqual(
            self._whole_departure_state(route, membership),
            after_departure,
        )

    def test_w_d3_9_replay_returns_the_departure_without_a_second_ending(self):
        self.maxDiff = None
        route = self._prepare_departure_route("replay")
        applicant = add_active_elector(route.route, "g5-replay-applicant")
        self._seat_member(route, applicant, "replay")
        membership = self._membership_for(route, applicant)
        command = self._departure_command(
            route,
            membership,
            "replay",
            database_now(),
        )
        service = self._membership_service()
        first = service.transition_membership(command)
        after_first = self._whole_departure_state(route, membership)

        replay = service.transition_membership(command)

        self.assertEqual(replay, first)
        self.assertEqual(self._whole_departure_state(route, membership), after_first)
        self.assertEqual(
            CircleParticipationEnd.objects.filter(
                participation__membership=membership,
            ).count(),
            1,
        )
