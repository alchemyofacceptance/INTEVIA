"""SR chunk C0 - W-1: how the system answers valid non-COORDINATOR office acts today, on the
pre-widening base. PostgreSQL, disposable test database. Module v1.5 (v1.1: the Advisor's A-2 and
A-11; v1.2: Grok's Q2, Q3 and Q4 - the role suite's NOW bound to the route time and restored, the
consent receipt asserted, each driver checked against ROUTE_MAP; v1.3: attempt 87's raw-cursor
JSON receipt rows decoded before field access; v1.4: staged for chunk C1 - the fixture definitions
written through C1's one writer, the fixture target in the production shape, test 14's pins at
0051 - the W-1 evolution table's C1 row, the Advisor's A-4; v1.5: the Advisor's C1 review,
the complete D.4 definition target includes the recorded basis and citations).

Design: SR_COMMISSION_OTHER_OFFICES_v0_7.md (adopted 15 October 2026) section H W-1 and section K
condition 3; the Human Governor's ruling of 14 October 2026 (W-1 runs before the gate is widened).
Commissions: 00_COMMISSION_LD_SR_C0_v1_0.md, 00_COMMISSION_LD_SR_ADVISOR_CORRECTIONS_v1_0.md,
00_COMMISSION_LD_SR_C0_v1_2_v1_0.md, 00_COMMISSION_ADVISOR_SR_C0_A87_FIX_v1_0.md and
00_COMMISSION_LD_SR_C1_v1_0.md (US015-B-35, 26 October 2026). Recorded at main
1d2c341aa8e0f02889ffaab091c0f28be4e196a4 before SR changes anything; the line numbers below are at that
commit. No product change travels with this module; C1's schema (migration 0051) is the base it
now runs on, and the office acts still answer as recorded.

What is recorded. Two LO-scoped, non-COORDINATOR role definitions are written by the fixture
through C1's one writer, _write_definition_version, each under a canonical test-only
RECORD_ROLE_DEFINITION-shaped decision built with the production digest rules (0034's insert
guard recomputes the target fingerprint and the s015d1 reference; chunk C3 brings the command
and the provider's own decisions): one board office (TEST_ONLY_TREASURER, kind BOARD_OFFICE, with
its OFFICE_BASIS citation) and one custom role (TEST_ONLY_ADJUNCT, kind CUSTOM, on an LO opted into
beta after Birthing so that CUSTOM_ROLES is effective at receipt - sr_00's gate). Six routes are
attempted on each shape through the real services - the durable intake, authority_atomic, the service
and the provider: PROPOSE, the nominee's CONSENT, ASSIGN (on a consented nomination), END by an
effective Coordinator, REASSIGN, and the holder's own END (R-7). Today's answer is asserted:

* with the production provider, DENIED at its three COORDINATOR filters -
  _qualify_role_proposal (organism_application_authority.py:1156-1167), _qualify_role_transition
  (:1299-1308) and _qualify_office_consent (:1474-1507) - as an S015AuthorityDenied whose REFUSED
  decision row persists (authority_atomic defers the denial past commit);
* with a test provider that qualifies everything, IL-8 at the two service sites -
  organism_role_service.py:1270-1271 (PROPOSE) and :1587-1588 (ASSIGN, END, REASSIGN) - as an
  S015OperationNotYetImplemented, the transaction rolled back;
* the consent route carries no IL-8: with the test provider a consent on a non-COORDINATOR
  nomination is admitted today (only the production provider's filter stops it).

ROUTE_MAP below names the test that records each (route, provider, shape) cell - every route on both
shapes under both providers, 24 cells, each claimed once (test 26 checks the map against the class, and
each driver checks that the running test is the one the map names for its cell - Grok Q4).
Every ASSIGN case first records the nominee's valid consent through the test-provider consent helper,
so that each ASSIGN is an otherwise lawful act and today's early DENIED or IL-8 is the only thing in
its way (A-2).

Each refusal is asserted whole-state: no office chain row, consent row or receipt is written, no
decision row persists after IL-8, and the intake row written before the transaction survives
(v0.7 section G: "this refused act writes no governed record", never "no row of any kind"). The
census figures SR moves are recorded as they stand today.

Conversion through the chunks (A-4). Every test keeps its id. C0's passing result is frozen as the
pre-widening record; the module is then adapted in place at each later chunk - fixtures and census at
C1, guard-valid fixtures and head pins at C2, provider/refusal/consent expectations at C4, the W-4
success conversion at C5 - never skipped or disabled to make an intermediate suite pass. Nothing here
needs deleting.

Fixtures are the office-appointment suite's own (tests/test_s015_role_command.py: the service-founded
route, the test-only member inserts, the governed ACTIVE office, the exact test provider), so
today's answer is recorded against the same set-up that R18 and R18b use. The fixture nomination's
authority target is built in the production shape _execute_propose builds
(organism_role_service.py:1160-1176: the seven keys plus circle, effective_until,
effective_until_state and the two tolling keys), closing the divergence the C0 SCC record carried
(the suite's own _assignment_at_state, used for the ACTIVE-office fixture, keeps its shape). Every time value
is taken from the database (clock_timestamp(), the route's recorded times); every label is visibly
test-only. The suite's member and office helpers read that suite's module-global NOW
(known_at = max(NOW, times.latest_recorded_at), test_s015_role_command.py:657 and :690), which the
suite itself rebinds to the route time (:924); _setup_organism binds it the same way for the test and
tearDown restores it, so no hand-typed instant and no leaked rebinding reaches a fixture row (Grok Q2).
"""
from __future__ import annotations

import json
from unittest import skipUnless
from uuid import UUID

from django.db import connection, transaction
from django.test import TransactionTestCase

from core.models import (
    AuthorityDecision,
    CommandIntake,
    ContextualRoleAssignment,
    ContextualRoleAssignmentStateEvent,
    Identity,
    IntakeEntry,
    LivingOrganism,
    OfficeAppointmentConsent,
    OrganismMembership,
    OrganismRoleDefinition,
)
from core.organism_contract import QUALIFICATION_PLANE_MATRIX, OrganismAction
from src.intevia.services.organism_application_authority import (
    ProductionApplicationAuthorityProvider,
)
from src.intevia.services.organism_authority import (
    S015Authority,
    S015AuthorityDenied,
    S015AuthorityRefusalCode,
)
from src.intevia.services.organism_command_support import (
    _authority_request,
    authority_target_fingerprint,
)
from src.intevia.services.organism_entitlement_service import (
    EntitlementElectionCommand,
    OrganismEntitlementService,
)
from src.intevia.services.organism_membership_service import S015OperationNotYetImplemented
from src.intevia.services.organism_role_definition_service import (
    DefinitionProvision,
    _write_definition_version,
)
from src.intevia.services.organism_office_consent_service import (
    OfficeAppointmentConsentService,
    RecordOfficeConsentCommand,
)
from src.intevia.services.organism_role_service import (
    _TARGET_SCHEMA,
    _TOLLING_SOURCE,
    _TOLLING_VALUE,
    OrganismRoleService,
    ProposeAssignmentCommand,
    TransitionAssignmentCommand,
)
import tests.test_s015_role_command as _role_suite  # its module-global NOW is bound per test (Grok Q2)
from tests.s015_0021_support import append_event, current_applied_core_head, empty_fp, refresh, u
from tests.sb2_route_support import birth, prepare_route, route_fixture_state, route_test_now
from tests.se_route_support import ensure_test_policy_text
from tests.test_s015_role_command import (
    _ExactProvider,
    _add_member,
    _assignment_at_state,
    _snapshot_tables,
)
from tests.test_sr_definition_guards import _definition_target, _test_only_definition_decision

POSTGRESQL_ONLY = skipUnless(connection.vendor == "postgresql", "SR W-1 records PostgreSQL behaviour")

# The two non-COORDINATOR shapes SR classifies (v0.7 section D.1 `kind`). From C1 the `kind` column
# is installed and the fixture records it; the system still answers both shapes alike until C4.
BOARD_OFFICE = "BOARD_OFFICE"
CUSTOM = "CUSTOM"
SHAPE_CODES = {BOARD_OFFICE: "TEST_ONLY_TREASURER", CUSTOM: "TEST_ONLY_ADJUNCT"}
SHAPE_BASIS = {BOARD_OFFICE: "SILENT", CUSTOM: "CUSTOM"}

# The six routes recorded, under the two providers, on the two shapes.
PROPOSE, CONSENT, ASSIGN, END, REASSIGN, HOLDER_END = (
    "PROPOSE", "CONSENT", "ASSIGN", "END", "REASSIGN", "HOLDER_END",
)
ROUTES = (PROPOSE, CONSENT, ASSIGN, END, REASSIGN, HOLDER_END)
PRODUCTION, TEST = "production", "test"

# (route, provider, shape) -> the test that records it. 24 cells; test 26 checks completeness.
ROUTE_MAP = {
    (PROPOSE, PRODUCTION, BOARD_OFFICE): "test_sr_w1_01_board_office_propose_production_provider_denied",
    (PROPOSE, PRODUCTION, CUSTOM): "test_sr_w1_02_custom_role_propose_production_provider_denied",
    (CONSENT, PRODUCTION, BOARD_OFFICE): "test_sr_w1_03_board_office_consent_production_provider_denied",
    (ASSIGN, PRODUCTION, BOARD_OFFICE): "test_sr_w1_04_board_office_assign_production_provider_denied",
    (END, PRODUCTION, BOARD_OFFICE): "test_sr_w1_05_board_office_end_production_provider_denied",
    (REASSIGN, PRODUCTION, BOARD_OFFICE): "test_sr_w1_06_board_office_reassign_production_provider_denied",
    (HOLDER_END, PRODUCTION, CUSTOM): "test_sr_w1_07_custom_role_holder_own_end_production_provider_denied",
    (PROPOSE, TEST, BOARD_OFFICE): "test_sr_w1_08_board_office_propose_test_provider_il8",
    (PROPOSE, TEST, CUSTOM): "test_sr_w1_09_custom_role_propose_test_provider_il8",
    (END, TEST, BOARD_OFFICE): "test_sr_w1_10_board_office_end_test_provider_il8",
    (ASSIGN, TEST, CUSTOM): "test_sr_w1_11_custom_role_assign_after_consent_test_provider_il8",
    (REASSIGN, TEST, BOARD_OFFICE): "test_sr_w1_12_board_office_reassign_test_provider_il8",
    (CONSENT, TEST, BOARD_OFFICE): "test_sr_w1_13_board_office_consent_test_provider_admitted_today",
    (CONSENT, PRODUCTION, CUSTOM): "test_sr_w1_15_custom_role_consent_production_provider_denied",
    (ASSIGN, PRODUCTION, CUSTOM): "test_sr_w1_16_custom_role_assign_production_provider_denied",
    (END, PRODUCTION, CUSTOM): "test_sr_w1_17_custom_role_end_production_provider_denied",
    (REASSIGN, PRODUCTION, CUSTOM): "test_sr_w1_18_custom_role_reassign_production_provider_denied",
    (HOLDER_END, PRODUCTION, BOARD_OFFICE): "test_sr_w1_19_board_office_holder_own_end_production_provider_denied",
    (CONSENT, TEST, CUSTOM): "test_sr_w1_20_custom_role_consent_test_provider_admitted_today",
    (ASSIGN, TEST, BOARD_OFFICE): "test_sr_w1_21_board_office_assign_after_consent_test_provider_il8",
    (END, TEST, CUSTOM): "test_sr_w1_22_custom_role_end_test_provider_il8",
    (REASSIGN, TEST, CUSTOM): "test_sr_w1_23_custom_role_reassign_test_provider_il8",
    (HOLDER_END, TEST, BOARD_OFFICE): "test_sr_w1_24_board_office_holder_own_end_test_provider_il8",
    (HOLDER_END, TEST, CUSTOM): "test_sr_w1_25_custom_role_holder_own_end_test_provider_il8",
}

# The census at C1, as the pinned sites hold it (SR moves each: v0.7 sections B, I, K). The action
# enum and the matrix move at C3 (35 / 42 stay); C1 adds the intake entry, the enterer FK and the head.
ACTION_MEMBERS_TODAY = 35
MATRIX_ROWS_TODAY = 42
INTAKE_ENTRIES_TODAY = 23
IDENTITY_FKS_TODAY = 115  # tests/test_service_activity_models.py IdentityFKPredictionTests, scoped
CORE_HEAD_TODAY = "0051_sr_role_definitions"

# Today's refusal site for each (route class, provider), at 1d2c341a - a record, read by the return.
REFUSAL_SITES = {
    (PROPOSE, PRODUCTION): "organism_application_authority.py:1163-1167 (filter :1156-1162)",
    ("TRANSITION", PRODUCTION): "organism_application_authority.py:1307-1308 (filter :1299-1306)",
    (CONSENT, PRODUCTION): "organism_application_authority.py:1496-1507 (filter :1474-1481)",
    (PROPOSE, TEST): "organism_role_service.py:1270-1271",
    ("TRANSITION", TEST): "organism_role_service.py:1587-1588",
    (CONSENT, TEST): "no refusal site: the consent service carries no IL-8",
}

_GOVERNED_TABLES = (
    "core_contextualroleassignment",
    "core_contextualroleassignmentstateevent",
    "core_officeappointmentconsent",
    "core_organismcommandreceipt",
)


def _database_now():
    """The database's instant clock; the only clock this module reads for a command's times."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT clock_timestamp()")
        return cursor.fetchone()[0]


@POSTGRESQL_ONLY
class SROfficesW1Tests(TransactionTestCase):
    """W-1: today's answers to non-COORDINATOR office acts through the real routes."""

    ISOLATION_FULL_CAPTURE = True
    reset_sequences = True

    def tearDown(self):
        """Restore the role suite's NOW saved by _setup_organism, whether the test passed or failed
        (Grok Q2), so one test's route time reaches neither the next test nor a later class."""
        if hasattr(self, "_role_suite_now_before"):
            _role_suite.NOW = self._role_suite_now_before
            del self._role_suite_now_before
        super().tearDown()

    # ---- fixtures: the office-appointment suite's own, so R18/R18b's set-up is the one recorded ----

    def _setup_organism(self, label):
        """A service-founded, born LO with the founder's Coordinator appointment (the SB-2 route)."""
        route = prepare_route(tag=label)
        birth(route)
        self._route = route  # kept for the beta opt-in a CUSTOM fixture needs (C1)
        ids, _gateway = route_fixture_state(route)
        self.now = route_test_now(route)
        # Grok Q2: _add_member and _assignment_at_state read the role suite's module-global NOW
        # (test_s015_role_command.py:657, :690); bind it to this route's time as that suite does (:924),
        # saving the value found so that tearDown restores it. The shared helpers are unchanged.
        if not hasattr(self, "_role_suite_now_before"):
            self._role_suite_now_before = _role_suite.NOW
        _role_suite.NOW = self.now
        return ids, route.founder

    def _add_active_member(self, ids, label):
        with transaction.atomic():
            with connection.cursor() as cur:
                membership_id, member_pk, member_membership_uuid = _add_member(cur, ids, f"{label}-member")
        return membership_id, Identity.objects.get(pk=member_pk), member_membership_uuid

    def _add_definition(self, ids, label, shape):
        """An active LO-scoped definition under the shape's test-only code, written through C1's one
        writer under a canonical test-only RECORD_ROLE_DEFINITION-shaped decision (C1 row of the W-1
        evolution table). A CUSTOM definition first opts the born LO into beta through the real
        entitlement route, so that CUSTOM_ROLES is effective at the definition's received_at."""
        organism = LivingOrganism.objects.get(pk=ids["lo"])
        founder = Identity.objects.get(pk=ids["identity"])
        kind = shape
        if kind == CUSTOM:
            policy_version, policy_text_sha256 = ensure_test_policy_text()
            OrganismEntitlementService(
                authority=self._route.birthing_service._authority, clock=_database_now,
            ).activate_beta(EntitlementElectionCommand(
                credential=founder.credential,
                living_organism_uuid=organism.organism_id,
                request_reference=f"request:{label}:beta",
                idempotency_key=f"idem:{label}:beta",
                occurred_at=_database_now(),
                policy_version=policy_version,
                policy_text_sha256=policy_text_sha256,
            ))
        at = _database_now()
        provisions = (
            (DefinitionProvision("OFFICE_BASIS", f"library:{label}:constitution#test-only", "v1"),)
            if kind == BOARD_OFFICE else ()
        )
        with transaction.atomic():
            decision = _test_only_definition_decision(
                actor=founder, evaluated_at=at, label=f"{label}:definition",
                target=_definition_target(
                    organism_uuid=organism.organism_id, code=SHAPE_CODES[shape], definition_version=1,
                    kind=kind, nomination_policy="C", appointing_authority="LO_COORDINATOR_BODY",
                    nomination_basis=SHAPE_BASIS[shape], provisions=provisions,
                ),
            )
            row = _write_definition_version(
                database_alias="default", organism=organism, code=SHAPE_CODES[shape], kind=kind,
                appointing_authority="LO_COORDINATOR_BODY", appointing_body_reference=None,
                alternative_route_reference=None, nomination_basis=SHAPE_BASIS[shape], nomination_policy="C",
                nomination_dependency=None, participation_required=False, ex_officio_seat_reference=None,
                supersedes=None, enterer=founder, authority_decision_reference=decision.decision_reference,
                occurred_at=at, effective_at=at, received_at=at, provisions=provisions,
                role_uuid=UUID(u(f"role:{label}:{SHAPE_CODES[shape]}")),
            )
        return row.pk, row.role_uuid

    def _nomination(self, ids, membership_id, definition_id, label):
        """A PROPOSED nomination by the founder on a non-COORDINATOR definition, written as a governed
        chain row with a test-provider decision (the first half of the suite's ACTIVE-office fixture,
        including its seven-key authority target)."""
        times = ids["recorded_times"]
        effective_at = times.latest_effective_at
        known_at = max(self.now, times.latest_recorded_at)
        assignment_uuid = UUID(u(f"assignment:{label}"))
        actor = Identity.objects.get(pk=ids["identity"])
        actor_membership = OrganismMembership.objects.select_related("head_transition").get(pk=ids["membership"])
        nominee = OrganismMembership.objects.get(pk=membership_id)
        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute(
                    "INSERT INTO core_contextualroleassignment (assignment_uuid, state, created_at, "
                    "current_state_effective_at, state_known_at, state_source_event_set_fingerprint, "
                    "membership_id, role_definition_id) "
                    "VALUES (%s, 'PROPOSED', clock_timestamp(), %s, %s, %s, %s, %s) RETURNING id",
                    (
                        str(assignment_uuid), effective_at, known_at,
                        empty_fp(cur, "core_contextualroleassignment", str(assignment_uuid)),
                        membership_id, definition_id,
                    ),
                )
                assignment_id = cur.fetchone()[0]
                cur.execute(
                    "SELECT lo.organism_id, r.role_uuid FROM core_livingorganism lo "
                    "JOIN core_organismmembership m ON m.living_organism_id = lo.id "
                    "JOIN core_organismroledefinition r ON r.id = %s WHERE m.id = %s",
                    (definition_id, membership_id),
                )
                organism_uuid, role_uuid = cur.fetchone()
                # the production shape (_execute_propose, organism_role_service.py:1160-1176) - C1
                propose_target = {
                    "action": OrganismAction.TRANSITION_CONTEXTUAL_ROLE.value,
                    "circle": None,
                    "event_action": "PROPOSE",
                    "effective_until": None,
                    "effective_until_state": "NONE",
                    "living_organism_id": str(organism_uuid),
                    "membership_uuid": str(nominee.membership_uuid),
                    "resulting_state": "PROPOSED",
                    "role_definition_uuid": str(role_uuid),
                    "schema": _TARGET_SCHEMA,
                    "suspension_tolls_term": _TOLLING_VALUE,
                    "suspension_tolls_term_source": _TOLLING_SOURCE,
                }
                decision = S015Authority(provider=_ExactProvider(), database_alias="default").qualify(
                    _authority_request(
                        database_alias="default",
                        actor=actor,
                        actor_membership=actor_membership,
                        actor_role_head_refs=(),
                        action=OrganismAction.TRANSITION_CONTEXTUAL_ROLE,
                        request_reference=f"request:{label}:propose",
                        idempotency_key=f"idem:{label}:propose",
                        received_at=known_at,
                        target_fingerprint=authority_target_fingerprint(propose_target),
                        effective_at=effective_at,
                        target=propose_target,
                        living_organism_id=UUID(str(organism_uuid)),
                    )
                )
                append_event(
                    cur, "core_contextualroleassignmentstateevent", assignment_id, 1, "PROPOSE", None, "PROPOSED",
                    actor.pk, ids["basis"], label, eff=effective_at, occ=effective_at, rec=known_at,
                    overrides={
                        "authority_decision_reference": decision.authority_decision_reference,
                        "evidence_reference": decision.authority_reference,
                    },
                )
                refresh(cur, "core_contextualroleassignment", assignment_id, effective_at)
        return assignment_id, assignment_uuid

    def _record_test_consent(self, *, provider, actor, assignment_uuid, label, effective_at=None):
        """The suite's consent helper, kept by name: _assignment_at_state calls it on the test case."""
        proposal = ContextualRoleAssignmentStateEvent.objects.get(
            assignment__assignment_uuid=assignment_uuid, sequence=1, action="PROPOSE",
        )
        at = _database_now()
        effective_at = self.now if effective_at is None else effective_at
        return self._consent_service(provider, clock=lambda: at).record_office_consent(
            RecordOfficeConsentCommand(
                credential=actor.credential,
                request_reference=f"request:{label}:consent",
                idempotency_key=f"idem:{label}:consent",
                occurred_at=at,
                effective_at=effective_at,
                assignment_uuid=assignment_uuid,
                proposal_event_uuid=proposal.event_uuid,
                action="CONSENT",
            )
        )

    def _consented_nomination(self, ids, membership_id, member, definition_id, label):
        """A PROPOSED nomination with the nominee's valid CONSENT recorded through the test-provider
        helper, so a following ASSIGN is an otherwise lawful act (A-2)."""
        _, nomination_uuid = self._nomination(ids, membership_id, definition_id, label)
        self._record_test_consent(
            provider=_ExactProvider(), actor=member, assignment_uuid=nomination_uuid, label=label,
        )
        return nomination_uuid

    def _active_office(self, ids, membership_id, definition_id, label):
        """A governed ACTIVE office on a non-COORDINATOR definition (proposal, consent, LO assignment)."""
        _, office_uuid = _assignment_at_state(self, ids, membership_id, definition_id, f"{label}:fixture")
        return office_uuid

    # ---- the real routes ----

    def _service(self, provider):
        return OrganismRoleService(
            authority=S015Authority(provider=provider, database_alias="default"),
            clock=lambda: self.now,
            database_alias="default",
        )

    def _consent_service(self, provider, *, clock=None):
        return OfficeAppointmentConsentService(
            authority=S015Authority(provider=provider, database_alias="default"),
            clock=(lambda: self.now) if clock is None else clock,
            database_alias="default",
        )

    def _propose_command(self, *, actor, membership_uuid, role_definition_uuid, label):
        at = _database_now()
        return ProposeAssignmentCommand(
            credential=actor.credential,
            request_reference=f"request:{label}:propose",
            idempotency_key=f"idem:{label}:propose",
            occurred_at=at,
            effective_at=at,
            membership_uuid=membership_uuid,
            role_definition_uuid=role_definition_uuid,
            effective_until_state="NONE",
            effective_until=None,
        )

    def _transition_command(self, *, actor, assignment_uuid, action, resulting_state, label,
                            effective_until_state=None):
        return TransitionAssignmentCommand(
            credential=actor.credential,
            request_reference=f"request:{label}:{action.lower()}",
            idempotency_key=f"idem:{label}:{action.lower()}",
            occurred_at=_database_now(),
            effective_at=self.now,
            assignment_uuid=assignment_uuid,
            action=action,
            resulting_state=resulting_state,
            effective_until_state=effective_until_state,
            effective_until=None,
        )

    # ---- whole-state ----

    def _state(self):
        """The governed records a refused office act must not write, plus the decision count."""
        snapshot = _snapshot_tables(*_GOVERNED_TABLES)
        snapshot["decisions"] = AuthorityDecision.objects.count()
        return snapshot

    def _assert_intake_row(self, actor, action, idempotency_key):
        """R-1 = B: the intake row written before the transaction survives the refusal."""
        self.assertTrue(
            CommandIntake.objects.filter(actor=actor, action=action.value, idempotency_key=idempotency_key).exists(),
            "the durable intake row should survive a refused office act",
        )

    def _assert_production_denied(self, refused, before, *, actor, action, idempotency_key):
        """Today's production answer: DENIED, a REFUSED decision persisted, no governed record."""
        self.assertIs(refused.exception.refusal_code, S015AuthorityRefusalCode.DENIED)
        decision = AuthorityDecision.objects.get(decision_reference=refused.exception.authority_decision_reference)
        self.assertEqual(decision.outcome, "REFUSED")
        self.assertEqual(decision.refusal_code, S015AuthorityRefusalCode.DENIED.value)
        self.assertEqual(decision.action, action.value)
        after = self._state()
        self.assertEqual(after["decisions"], before["decisions"] + 1)
        for table in _GOVERNED_TABLES:
            self.assertEqual(after[table], before[table], table)
        self._assert_intake_row(actor, action, idempotency_key)

    def _assert_il8(self, refused, before, *, operation, actor, idempotency_key):
        """Today's answer behind a qualifying provider: IL-8, SR; the transaction rolled back whole."""
        self.assertEqual(refused.exception.limit_id, "IL-8")
        self.assertEqual(refused.exception.owning_slice, "SR")
        self.assertEqual(refused.exception.operation, operation)
        self.assertEqual(self._state(), before)
        self._assert_intake_row(actor, OrganismAction.TRANSITION_CONTEXTUAL_ROLE, idempotency_key)

    def _assert_mapped(self, route, provider_kind, shape):
        """Grok Q4: the running test is the one ROUTE_MAP names for this cell. Both shapes are refused
        alike today, so without this a test body that passed the other shape or act would still pass;
        with it, a swapped shape or act names a different test and fails."""
        self.assertEqual(ROUTE_MAP[(route, provider_kind, shape)], self._testMethodName)

    # ---- the drivers: one per route, parameterised by provider and shape ----

    def _run_propose(self, provider_kind, shape, label):
        self._assert_mapped(PROPOSE, provider_kind, shape)
        ids, founder = self._setup_organism(label)
        _, _member, member_membership_uuid = self._add_active_member(ids, label)
        _, role_uuid = self._add_definition(ids, label, shape)
        provider = ProductionApplicationAuthorityProvider() if provider_kind == PRODUCTION else _ExactProvider()
        service = self._service(provider)
        before = self._state()
        command = self._propose_command(
            actor=founder, membership_uuid=member_membership_uuid, role_definition_uuid=role_uuid, label=label,
        )
        if provider_kind == PRODUCTION:
            with self.assertRaises(S015AuthorityDenied) as refused:
                service.propose_assignment(command)
            self._assert_production_denied(
                refused, before, actor=founder, action=OrganismAction.TRANSITION_CONTEXTUAL_ROLE,
                idempotency_key=f"idem:{label}:propose",
            )
        else:
            with self.assertRaisesRegex(S015OperationNotYetImplemented, r"IL-8, SR") as refused:
                service.propose_assignment(command)
            self._assert_il8(
                refused, before, operation="appoint_non_coordinator_office",
                actor=founder, idempotency_key=f"idem:{label}:propose",
            )

    def _run_consent(self, provider_kind, shape, label):
        """The nominee's CONSENT on a PROPOSED nomination: DENIED by the production filter; admitted
        and recorded behind the test provider (the consent service carries no IL-8)."""
        self._assert_mapped(CONSENT, provider_kind, shape)
        ids, _founder = self._setup_organism(label)
        membership_id, member, _ = self._add_active_member(ids, label)
        definition_id, _ = self._add_definition(ids, label, shape)
        _, nomination_uuid = self._nomination(ids, membership_id, definition_id, label)
        before = self._state()
        if provider_kind == PRODUCTION:
            with self.assertRaises(S015AuthorityDenied) as refused:
                self._record_test_consent(
                    provider=ProductionApplicationAuthorityProvider(), actor=member,
                    assignment_uuid=nomination_uuid, label=label,
                )
            self._assert_production_denied(
                refused, before, actor=member, action=OrganismAction.RECORD_OFFICE_CONSENT,
                idempotency_key=f"idem:{label}:consent",
            )
            return
        result = self._record_test_consent(
            provider=_ExactProvider(), actor=member, assignment_uuid=nomination_uuid, label=label,
        )
        consent = OfficeAppointmentConsent.objects.get(consent_uuid=result.consent_uuid)
        self.assertEqual(consent.action, "CONSENT")
        self.assertEqual(consent.assignment.assignment_uuid, nomination_uuid)
        self.assertEqual(consent.nominee_membership_id, membership_id)
        after = self._state()
        self.assertEqual(len(after["core_officeappointmentconsent"]), len(before["core_officeappointmentconsent"]) + 1)
        self.assertEqual(after["decisions"], before["decisions"] + 1)
        self.assertEqual(after["core_contextualroleassignmentstateevent"], before["core_contextualroleassignmentstateevent"])
        # Grok Q3: the admitted consent completes through _finalise, which stores exactly one command
        # receipt (organism_command_support.py:1119-1128, result_payload=result.receipt_payload()); the
        # payload is OfficeConsentCommandResult.receipt_payload (organism_office_consent_service.py:130-144):
        # "action" is the consent's action (:135, from :423) and "consent_uuid" its uuid as text (:139).
        # The fixture nomination writes no receipt, so the delta is the consent's alone.
        # _snapshot_tables uses a raw cursor: Django returns each jsonb row as text.
        # Decode these receipt rows only; leave the whole-state snapshots unchanged.
        receipt_rows_before = [json.loads(row) for row in before["core_organismcommandreceipt"]]
        receipt_rows_after = [json.loads(row) for row in after["core_organismcommandreceipt"]]
        receipts_before = {row["receipt_uuid"] for row in receipt_rows_before}
        new_receipts = [row for row in receipt_rows_after if row["receipt_uuid"] not in receipts_before]
        self.assertEqual(len(after["core_organismcommandreceipt"]), len(before["core_organismcommandreceipt"]) + 1)
        self.assertEqual(len(new_receipts), 1, "exactly one new command receipt, the consent's")
        self.assertEqual(new_receipts[0]["result_payload"]["action"], "CONSENT")
        self.assertEqual(new_receipts[0]["result_payload"]["consent_uuid"], str(result.consent_uuid))
        self._assert_intake_row(member, OrganismAction.RECORD_OFFICE_CONSENT, f"idem:{label}:consent")

    def _run_assign(self, provider_kind, shape, label):
        """ASSIGN on a consented nomination (otherwise lawful): DENIED by the production filter before
        consent is read; IL-8 at the transition site behind the test provider."""
        self._assert_mapped(ASSIGN, provider_kind, shape)
        ids, founder = self._setup_organism(label)
        membership_id, member, _ = self._add_active_member(ids, label)
        definition_id, _ = self._add_definition(ids, label, shape)
        nomination_uuid = self._consented_nomination(ids, membership_id, member, definition_id, label)
        self._run_transition(
            provider_kind, label, actor=founder, assignment_uuid=nomination_uuid,
            action="ASSIGN", resulting_state="ACTIVE", effective_until_state="NONE",
        )

    def _run_end(self, provider_kind, shape, label, *, by_holder):
        """END of an ACTIVE office - by an effective Coordinator (the founder) or by the holder (R-7)."""
        self._assert_mapped(HOLDER_END if by_holder else END, provider_kind, shape)
        ids, founder = self._setup_organism(label)
        membership_id, member, _ = self._add_active_member(ids, label)
        definition_id, _ = self._add_definition(ids, label, shape)
        office_uuid = self._active_office(ids, membership_id, definition_id, label)
        self._run_transition(
            provider_kind, label, actor=member if by_holder else founder, assignment_uuid=office_uuid,
            action="END", resulting_state="ENDED",
        )
        self.assertEqual(ContextualRoleAssignment.objects.get(assignment_uuid=office_uuid).state, "ACTIVE")

    def _run_reassign(self, provider_kind, shape, label):
        self._assert_mapped(REASSIGN, provider_kind, shape)
        ids, founder = self._setup_organism(label)
        membership_id, _member, _ = self._add_active_member(ids, label)
        definition_id, _ = self._add_definition(ids, label, shape)
        office_uuid = self._active_office(ids, membership_id, definition_id, label)
        self._run_transition(
            provider_kind, label, actor=founder, assignment_uuid=office_uuid,
            action="REASSIGN", resulting_state="ENDED",
        )

    def _run_transition(self, provider_kind, label, *, actor, assignment_uuid, action, resulting_state,
                        effective_until_state=None):
        provider = ProductionApplicationAuthorityProvider() if provider_kind == PRODUCTION else _ExactProvider()
        service = self._service(provider)
        before = self._state()
        command = self._transition_command(
            actor=actor, assignment_uuid=assignment_uuid, action=action, resulting_state=resulting_state,
            label=label, effective_until_state=effective_until_state,
        )
        if provider_kind == PRODUCTION:
            with self.assertRaises(S015AuthorityDenied) as refused:
                service.transition_assignment(command)
            self._assert_production_denied(
                refused, before, actor=actor, action=OrganismAction.TRANSITION_CONTEXTUAL_ROLE,
                idempotency_key=f"idem:{label}:{action.lower()}",
            )
        else:
            with self.assertRaisesRegex(S015OperationNotYetImplemented, r"IL-8, SR") as refused:
                service.transition_assignment(command)
            self._assert_il8(
                refused, before, operation="transition_non_coordinator_office",
                actor=actor, idempotency_key=f"idem:{label}:{action.lower()}",
            )

    # ---- W-1 with the production provider: DENIED at the three COORDINATOR filters ----

    def test_sr_w1_01_board_office_propose_production_provider_denied(self):
        """PROPOSE, board office: _qualify_role_proposal's COORDINATOR filter finds no definition and
        answers DENIED before any write (OAA:1156-1167)."""
        self._run_propose(PRODUCTION, BOARD_OFFICE, "srw1-01")

    def test_sr_w1_02_custom_role_propose_production_provider_denied(self):
        """PROPOSE, custom role: the same filter, the same DENIED (no `kind` column before SR)."""
        self._run_propose(PRODUCTION, CUSTOM, "srw1-02")

    def test_sr_w1_03_board_office_consent_production_provider_denied(self):
        """The nominee's CONSENT, board office: _qualify_office_consent's COORDINATOR filter finds no
        assignment and answers DENIED (OAA:1474-1507)."""
        self._run_consent(PRODUCTION, BOARD_OFFICE, "srw1-03")

    def test_sr_w1_04_board_office_assign_production_provider_denied(self):
        """ASSIGN on a consented board-office nomination (otherwise lawful, A-2):
        _qualify_role_transition's COORDINATOR filter answers DENIED before consent is read
        (OAA:1299-1308 before :1372-1410)."""
        self._run_assign(PRODUCTION, BOARD_OFFICE, "srw1-04")

    def test_sr_w1_05_board_office_end_production_provider_denied(self):
        """END of an ACTIVE board office by an effective Coordinator: DENIED (OAA:1299-1308); the
        office stays ACTIVE."""
        self._run_end(PRODUCTION, BOARD_OFFICE, "srw1-05", by_holder=False)

    def test_sr_w1_06_board_office_reassign_production_provider_denied(self):
        """REASSIGN of an ACTIVE board office: DENIED (OAA:1299-1308)."""
        self._run_reassign(PRODUCTION, BOARD_OFFICE, "srw1-06")

    def test_sr_w1_07_custom_role_holder_own_end_production_provider_denied(self):
        """The holder's own END of an ACTIVE custom-role office (R-7's route): DENIED before the
        OFFICE_HOLDER branch is reached (OAA:1299-1308 before :1348-1354)."""
        self._run_end(PRODUCTION, CUSTOM, "srw1-07", by_holder=True)

    # ---- W-1 with a qualifying test provider: IL-8 at the two service sites ----

    def test_sr_w1_08_board_office_propose_test_provider_il8(self):
        """PROPOSE, board office, behind a provider that qualifies everything: the service's own
        limit, appoint_non_coordinator_office, IL-8, SR (ORS:1270-1271)."""
        self._run_propose(TEST, BOARD_OFFICE, "srw1-08")

    def test_sr_w1_09_custom_role_propose_test_provider_il8(self):
        """The same limit answers a custom-role definition (ORS:1270-1271)."""
        self._run_propose(TEST, CUSTOM, "srw1-09")

    def test_sr_w1_10_board_office_end_test_provider_il8(self):
        """END of an ACTIVE board office behind the qualifying provider:
        transition_non_coordinator_office, IL-8, SR (ORS:1587-1588)."""
        self._run_end(TEST, BOARD_OFFICE, "srw1-10", by_holder=False)

    def test_sr_w1_11_custom_role_assign_after_consent_test_provider_il8(self):
        """ASSIGN on a consented custom-role nomination behind the qualifying provider: the same
        transition site (ORS:1587-1588); the limit binds ASSIGN, END and REASSIGN alike."""
        self._run_assign(TEST, CUSTOM, "srw1-11")

    def test_sr_w1_12_board_office_reassign_test_provider_il8(self):
        """REASSIGN of an ACTIVE board office behind the qualifying provider (ORS:1587-1588)."""
        self._run_reassign(TEST, BOARD_OFFICE, "srw1-12")

    # ---- the consent route today: no IL-8 of its own ----

    def test_sr_w1_13_board_office_consent_test_provider_admitted_today(self):
        """Behind the qualifying provider the nominee's CONSENT on a board-office nomination is
        admitted and recorded today; only the production filter (test 03) keeps it out."""
        self._run_consent(TEST, BOARD_OFFICE, "srw1-13")

    # ---- the census SR moves, as it stands today ----

    def test_sr_w1_14_census_pins_today(self):
        """The figures SR's chunks move, as they stand at C1: the action enum and the permission
        matrix unmoved (35 / 42 until C3), the intake entries at 23 with RECORD_ROLE_DEFINITION, the
        Identity-FK catalogue at 115 with the definition's enterer, the migration head at 0051, and
        the definition table with SR's columns and its provision model present."""
        self.assertEqual(len(OrganismAction), ACTION_MEMBERS_TODAY)
        self.assertNotIn("RECORD_ROLE_DEFINITION", OrganismAction.__members__)
        self.assertEqual(len(QUALIFICATION_PLANE_MATRIX), MATRIX_ROWS_TODAY)
        self.assertNotIn("RECORD_ROLE_DEFINITION", QUALIFICATION_PLANE_MATRIX)
        self.assertEqual(len(IntakeEntry), INTAKE_ENTRIES_TODAY)
        self.assertIn("RECORD_ROLE_DEFINITION", IntakeEntry.__members__)

        from django.apps import apps
        from tests.test_service_activity_models import IdentityFKPredictionTests

        identity_model = apps.get_model("core", "Identity")
        identity_fks = set()
        for model in apps.get_models():
            if model._meta.app_label != "core":
                continue
            for field in model._meta.get_fields():
                if (
                    hasattr(field, "related_model")
                    and field.related_model is identity_model
                    and hasattr(field, "column")
                ):
                    identity_fks.add((model._meta.model_name, field.name))
        scoped = identity_fks - IdentityFKPredictionTests.EXCLUDED_INTERNAL_FKS
        self.assertEqual(len(scoped), IDENTITY_FKS_TODAY)
        self.assertIn(("organismroledefinition", "enterer"), scoped)

        definition_columns = {field.name for field in OrganismRoleDefinition._meta.get_fields()}
        self.assertTrue(
            {"kind", "appointing_authority", "nomination_basis", "nomination_policy", "enterer", "supersedes"}
            <= definition_columns,
            "SR's definition columns are installed at C1",
        )
        self.assertTrue(
            any(model._meta.model_name == "organismroledefinitionprovision" for model in apps.get_models()),
            "SR's provision table is installed at C1",
        )
        self.assertEqual(current_applied_core_head(connection)[0][1], CORE_HEAD_TODAY)

    # ---- the companions that complete the route x shape map (A-2) ----

    def test_sr_w1_15_custom_role_consent_production_provider_denied(self):
        """The nominee's CONSENT, custom role: DENIED at the consent filter (OAA:1474-1507)."""
        self._run_consent(PRODUCTION, CUSTOM, "srw1-15")

    def test_sr_w1_16_custom_role_assign_production_provider_denied(self):
        """ASSIGN on a consented custom-role nomination: DENIED at the transition filter."""
        self._run_assign(PRODUCTION, CUSTOM, "srw1-16")

    def test_sr_w1_17_custom_role_end_production_provider_denied(self):
        """END of an ACTIVE custom-role office by an effective Coordinator: DENIED."""
        self._run_end(PRODUCTION, CUSTOM, "srw1-17", by_holder=False)

    def test_sr_w1_18_custom_role_reassign_production_provider_denied(self):
        """REASSIGN of an ACTIVE custom-role office: DENIED."""
        self._run_reassign(PRODUCTION, CUSTOM, "srw1-18")

    def test_sr_w1_19_board_office_holder_own_end_production_provider_denied(self):
        """The holder's own END of an ACTIVE board office (R-7's route): DENIED before the
        OFFICE_HOLDER branch."""
        self._run_end(PRODUCTION, BOARD_OFFICE, "srw1-19", by_holder=True)

    def test_sr_w1_20_custom_role_consent_test_provider_admitted_today(self):
        """Behind the qualifying provider the nominee's CONSENT on a custom-role nomination is
        admitted and recorded today."""
        self._run_consent(TEST, CUSTOM, "srw1-20")

    def test_sr_w1_21_board_office_assign_after_consent_test_provider_il8(self):
        """ASSIGN on a consented board-office nomination behind the qualifying provider: IL-8 at the
        transition site (ORS:1587-1588)."""
        self._run_assign(TEST, BOARD_OFFICE, "srw1-21")

    def test_sr_w1_22_custom_role_end_test_provider_il8(self):
        """END of an ACTIVE custom-role office behind the qualifying provider: IL-8 (ORS:1587-1588)."""
        self._run_end(TEST, CUSTOM, "srw1-22", by_holder=False)

    def test_sr_w1_23_custom_role_reassign_test_provider_il8(self):
        """REASSIGN of an ACTIVE custom-role office behind the qualifying provider: IL-8."""
        self._run_reassign(TEST, CUSTOM, "srw1-23")

    def test_sr_w1_24_board_office_holder_own_end_test_provider_il8(self):
        """The holder's own END of an ACTIVE board office behind the qualifying provider: IL-8 at the
        transition site, before founder protection or the holder route is reached."""
        self._run_end(TEST, BOARD_OFFICE, "srw1-24", by_holder=True)

    def test_sr_w1_25_custom_role_holder_own_end_test_provider_il8(self):
        """The holder's own END of an ACTIVE custom-role office behind the qualifying provider: IL-8."""
        self._run_end(TEST, CUSTOM, "srw1-25", by_holder=True)

    # ---- the map is complete and claims nothing that is not there ----

    def test_sr_w1_26_route_map_covers_every_route_shape_provider_once(self):
        """ROUTE_MAP names exactly one test for each of the 24 (route, provider, shape) cells, every
        named test exists on this class, and no test is claimed twice."""
        cells = {(route, provider, shape) for route in ROUTES for provider in (PRODUCTION, TEST)
                 for shape in (BOARD_OFFICE, CUSTOM)}
        self.assertEqual(set(ROUTE_MAP), cells)
        self.assertEqual(len(set(ROUTE_MAP.values())), len(ROUTE_MAP))
        for name in ROUTE_MAP.values():
            self.assertTrue(callable(getattr(type(self), name, None)), name)
