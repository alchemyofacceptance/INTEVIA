"""SR chunk C1 - the definition schema (migration 0051), its guards, the one writer and
founding through it. PostgreSQL, disposable test database. Module v1.1.

Advisor revision: closes the supersession/citation bypasses, checks fixture
rollback, and strengthens the migration witnesses and complete-row comparisons.
The Advisor prepared these witnesses without running a database test.

Design: SR_COMMISSION_OTHER_OFFICES_v0_7.md (adopted 15 October 2026) sections C, D.1-D.3
and G; the Lead Designer's plan v1.1 row SR-C1 (W-7, W-9 guard half, W-13 half, W-17 guard
half, W-18 guard half, the founding branch) and section 5.9; the Advisor's A-3 and A-9.
Commission 00_COMMISSION_LD_SR_C1_v1_0.md (US015-B-35, 26 October 2026). Built on
db10965d8cd5e0a0349da52928d483d894265d7f (C0 as committed).

What is witnessed, by layer:

* the route - a real founding through the SB-2 route records version 1 of the LO's
  COORDINATOR definition through `_write_definition_version`, attributed to the founding
  decision, the founder and the founding act's coordinates, and Birthing still follows;
* the writer - its argument checks before any write; a BOARD_OFFICE definition with its
  OFFICE_BASIS citation written and committed under a canonical test-only
  RECORD_ROLE_DEFINITION decision (chunk C3 brings the command that will produce such
  decisions through the provider; here the decision row is built by a fixture with the
  production digest rules, since migration 0034's insert guard recomputes the target
  fingerprint and the s015d1 reference);
* the guards, whole-state at an outer commit - `sr_00` (attribution, domains, the version
  rule, the founding branch's exact conditions and its forgeries, the reserved root office
  under a definition decision, the CUSTOM gate at NEW.received_at, the decision match);
  `sr_10` (the one permitted change; the retirement's attribution, record time and
  holder condition); `sr_20` (a deactivated version needs its successor at commit);
  `sr_30` (the required citations, no more and no less); the provision table's record time
  and immutability;
* the migration - 0051 forward and reverse on a clean state, with the schema compared
  before and after; a populated reverse refused with the reason naming the rows.

Every refusal is asserted whole-state: the governed definition and provision tables are
compared before and after. Every time value comes from the database. The raw-cursor reads
below and the Python type each receives (psycopg 3 through Django): `count(*)` -> int;
`pg_get_constraintdef` -> str; `information_schema.columns.column_name` -> str;
`pg_trigger.tgname` -> str; `clock_timestamp()` -> datetime (aware); a `recorded_at`
column -> datetime (aware) or None; `role_uuid` -> uuid.UUID; `active` -> bool.
"""
from __future__ import annotations

import datetime as dt
import hashlib
from importlib import import_module
import re
from unittest import skipUnless
from uuid import uuid4

from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from core.models import (
    AuthorityDecision,
    CommandIntake,
    ContextualRoleAssignment,
    IntakeEntry,
    LivingOrganism,
    OrganismRoleDefinition,
    OrganismRoleDefinitionProvision,
)
from src.intevia.services.organism_authority import (
    _DECISION_DOMAIN,
    authority_target_fingerprint,
    canonical_json_bytes,
    canonical_timestamp,
)
from src.intevia.services.organism_entitlement_service import (
    EntitlementElectionCommand,
    OrganismEntitlementService,
    entitlement_effective,
)
from src.intevia.services.organism_role_definition_service import (
    DefinitionProvision,
    S015RoleDefinitionMalformed,
    _write_definition_version,
)
from tests.s015_0021_support import (
    current_applied_core_head,
    empty_fp,
    found_nonroot,
    migrate_to_target_with_restore,
    sr_definition_guard_suspended,
)
from tests.sb2_route_support import birth, prepare_route, route_fixture_state
from tests.se_route_support import ensure_test_policy_text

POSTGRESQL_ONLY = skipUnless(connection.vendor == "postgresql", "SR definition guards are PostgreSQL triggers")

HEAD_0050 = "0050_clk_mono_1_cache_coordinate_detail"
HEAD_0051 = "0051_sr_role_definitions"
TRIGGERS_AT_0050 = 475
TRIGGERS_AT_0051 = 483
SR_COLUMNS = {
    "kind", "appointing_authority", "appointing_body_reference", "alternative_route_reference",
    "nomination_basis", "nomination_policy", "nomination_dependency", "participation_required",
    "ex_officio_seat_reference", "supersedes_id", "enterer_id", "enterer_access_epoch",
    "authority_decision_reference", "occurred_at", "effective_at", "received_at", "recorded_at",
    "retired_by_decision_reference", "retired_effective_at", "retired_received_at",
    "retired_recorded_at", "retirement_reason",
}
SR_TRIGGER_NAMES = {
    "sr_00_definition_insert", "sr_10_definition_update", "sr_20_definition_superseded",
    "sr_30_definition_citations", "s015_sr_provision_assign_time", "s015_sr_provision_update_immutable",
    "s015_sr_provision_delete_immutable", "s015_sr_provision_truncate_immutable",
}
RECORD_ROLE_DEFINITION = "RECORD_ROLE_DEFINITION"  # not an OrganismAction member until chunk C3
_TEST_PROVIDER = "tests.test_sr_definition_guards.TestOnlyDefinitionDecision"


def _database_now():
    """clock_timestamp() through a raw cursor: a tz-aware datetime."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT clock_timestamp()")
        return cursor.fetchone()[0]


def _definition_target(*, organism_uuid, code, definition_version, kind, nomination_policy,
                       appointing_authority, event_action="DEFINE", role_uuid=None,
                       appointing_body_reference=None, alternative_route_reference=None,
                       nomination_basis=None, nomination_dependency=None, participation_required=False,
                       ex_officio_seat_reference=None, supersedes_role_uuid=None, provisions=()):
    """The complete D.4 fixture target, including fields beyond sr_00's compared subset.

    C3's production command must implement D.4; this fixture does not narrow that
    contract to the columns the C1 guard reads. RETIRE additionally names role_uuid
    because D.3 binds that act to the exact version being retired.
    """
    target = {
        "action": RECORD_ROLE_DEFINITION,
        "event_action": event_action,
        "living_organism_id": str(organism_uuid),
        "code": code,
        "definition_version": definition_version,
        "kind": kind,
        "appointing_authority": appointing_authority,
        "appointing_body_reference": appointing_body_reference,
        "alternative_route_reference": alternative_route_reference,
        "nomination_basis": nomination_basis or ("CUSTOM" if kind == "CUSTOM" else "SILENT"),
        "nomination_policy": nomination_policy,
        "nomination_dependency": nomination_dependency,
        "participation_required": participation_required,
        "ex_officio_seat_reference": ex_officio_seat_reference,
        "supersedes_role_uuid": None if supersedes_role_uuid is None else str(supersedes_role_uuid),
        "provisions": sorted(
            [{"relationship": p.relationship, "provision_reference": p.provision_reference,
              "provision_version": p.provision_version} for p in provisions],
            key=lambda p: p["relationship"],
        ),
        "schema": "intevia.s015.organism-role-definition-target.v1",
    }
    if role_uuid is not None:
        target["role_uuid"] = str(role_uuid)
    return target


def _test_only_definition_decision(*, actor, target, evaluated_at, label, outcome="QUALIFIED",
                                   action=RECORD_ROLE_DEFINITION):
    """A canonical, labelled test-only decision row for the definition act (chunk C1).

    Built with the production digest rules - canonical_json_bytes, the target domain through
    authority_target_fingerprint, the decision domain for the s015d1 reference - because
    0034's insert guard recomputes both from the stored bytes. No provider ran: the row's
    provider identity names this fixture. C3's command replaces this with the provider's
    own decisions.
    """
    payload = {
        "test_only": "SR C1 definition decision fixture",
        "label": label,
        "action": action,
        "target_fingerprint": authority_target_fingerprint(target),
        "evaluated_at": canonical_timestamp(evaluated_at),
    }
    payload_bytes = canonical_json_bytes(payload)
    reference = "s015d1:" + hashlib.sha256(_DECISION_DOMAIN + payload_bytes).hexdigest()
    target_bytes = canonical_json_bytes(target)
    decision = AuthorityDecision(
        decision_reference=reference,
        decision_payload=payload,
        decision_payload_bytes=payload_bytes,
        request_kind="TestOnlyDefinitionRequest",
        action=action,
        command_subtype=action,
        actor=actor,
        actor_identity_id=actor.identity_id,
        actor_access_epoch=actor.access_epoch,
        actor_credential_pk=None,
        target=target,
        target_bytes=target_bytes,
        target_fingerprint=authority_target_fingerprint(target),
        effective_at=evaluated_at,
        evaluated_at=evaluated_at,
        authority_reference='s015a1:{"test_only":"sr-c1"}',
        facts_read=[{"kind": "TEST_ONLY", "label": label}],
        outcome=outcome,
        refusal_code=None if outcome == "QUALIFIED" else "DENIED",
        provider_identity=_TEST_PROVIDER,
        provider_version="C1",
        request_reference=f"request:{label}",
        idempotency_key=f"idem:{label}",
        recorded_at=None,
    )
    decision.save()
    return decision


@POSTGRESQL_ONLY
class SRDefinitionGuardTests(TransactionTestCase):
    """0051's guards, the writer and founding through it, whole-state."""

    ISOLATION_FULL_CAPTURE = True
    reset_sequences = True

    # ---- fixtures ----

    def _founded(self, label):
        """A real founding through the SB-2 route (the formation service, the production
        provider, the durable intake); returns the route and the LO's fixture state."""
        route = prepare_route(tag=label)
        ids, _gateway = route_fixture_state(route)
        return route, ids

    def _member(self, route, label):
        from tests.sb2_route_support import add_active_elector

        return add_active_elector(route, f"{label}-member")

    def _state(self):
        """Every concrete column and row, including transactional decisions; no raw jsonb assumptions."""
        return {
            "definitions": list(OrganismRoleDefinition.objects.order_by("pk").values_list()),
            "provisions": list(OrganismRoleDefinitionProvision.objects.order_by("pk").values_list()),
            "decisions": list(AuthorityDecision.objects.order_by("pk").values_list()),
        }

    def _write(self, route, *, label, code="TEST_ONLY_TREASURER", kind="BOARD_OFFICE",
               appointing_authority="LO_COORDINATOR_BODY", appointing_body_reference=None,
               alternative_route_reference=None, nomination_basis="SILENT", nomination_policy="C",
               nomination_dependency=None, participation_required=False, ex_officio_seat_reference=None,
               provisions=None, supersedes=None, definition_version=1, decision_target=None,
               enterer=None, received_at=None, decision_evaluated_at=None, decision_outcome="QUALIFIED"):
        """Write one definition version through the writer inside one transaction that commits
        at the exit of this method's atomic block; the deferred guards fire at that commit."""
        organism = LivingOrganism.objects.get(pk=route.organism.pk)
        enterer = route.founder if enterer is None else enterer
        at = _database_now() if received_at is None else received_at
        if provisions is None:
            provisions = (
                (DefinitionProvision("OFFICE_BASIS", f"library:{label}:constitution#art-7", "v1"),)
                if kind == "BOARD_OFFICE" else ()
            )
        target = decision_target or _definition_target(
            organism_uuid=organism.organism_id, code=code, definition_version=definition_version,
            kind=kind, nomination_policy=nomination_policy, appointing_authority=appointing_authority,
            appointing_body_reference=appointing_body_reference, alternative_route_reference=alternative_route_reference,
            nomination_basis=nomination_basis, nomination_dependency=nomination_dependency,
            participation_required=participation_required, ex_officio_seat_reference=ex_officio_seat_reference,
            supersedes_role_uuid=None if supersedes is None else supersedes.role_uuid, provisions=provisions,
            event_action="DEFINE" if supersedes is None else "AMEND",
        )
        with transaction.atomic():
            decision = _test_only_definition_decision(
                actor=enterer, target=target, evaluated_at=at if decision_evaluated_at is None else decision_evaluated_at,
                label=label, outcome=decision_outcome,
            )
            row = _write_definition_version(
                database_alias="default",
                organism=organism,
                code=code,
                kind=kind,
                appointing_authority=appointing_authority,
                appointing_body_reference=appointing_body_reference,
                alternative_route_reference=alternative_route_reference,
                nomination_basis=nomination_basis,
                nomination_policy=nomination_policy,
                nomination_dependency=nomination_dependency,
                participation_required=participation_required,
                ex_officio_seat_reference=ex_officio_seat_reference,
                supersedes=supersedes,
                enterer=enterer,
                authority_decision_reference=decision.decision_reference,
                occurred_at=at,
                effective_at=at,
                received_at=at,
                provisions=tuple(provisions),
            )
        return OrganismRoleDefinition.objects.get(pk=row.pk)

    def _assert_refused(self, before, message, act):
        """act raises IntegrityError whose primary message carries `message`; whole-state unchanged."""
        with self.assertRaises(IntegrityError) as refused:
            act()
        self.assertIn(message, str(refused.exception))
        self.assertEqual(self._state(), before)

    # ---- the route: founding through the writer (A-3) ----

    def test_sr_c1_01_founding_writes_the_coordinator_definition_through_the_writer(self):
        """Route layer. A real founding records version 1 of the LO's COORDINATOR definition with
        kind COORDINATOR, the founder as enterer, the founding decision's reference, received_at
        equal to that decision's evaluated_at, recorded_at assigned by the database, no provision
        row; the founder's ASSIGN cites it; Birthing then succeeds on the same LO."""
        route, ids = self._founded("src1-01")
        role = OrganismRoleDefinition.objects.get(pk=ids["roles"]["COORDINATOR"])
        decision = AuthorityDecision.objects.get(decision_reference=role.authority_decision_reference)
        self.assertEqual(role.code, "COORDINATOR")
        self.assertEqual(role.definition_version, 1)
        self.assertTrue(role.active)
        self.assertEqual(role.kind, "COORDINATOR")
        self.assertEqual(role.appointing_authority, "LO_COORDINATOR_BODY")
        self.assertEqual(role.nomination_basis, "SILENT")
        self.assertEqual(role.nomination_policy, "C")
        self.assertIs(role.participation_required, False)
        self.assertIsNone(role.supersedes_id)
        self.assertEqual(role.enterer_id, route.founder.pk)
        self.assertEqual(role.enterer_access_epoch, route.founder.access_epoch)
        self.assertEqual(decision.action, "FOUND_LIVING_ORGANISM")
        self.assertEqual(decision.outcome, "QUALIFIED")
        self.assertEqual(decision.actor_id, route.founder.pk)
        self.assertEqual(decision.target["proposed_organism_uuid"], str(route.organism.organism_id))
        self.assertEqual(role.received_at, decision.evaluated_at)
        self.assertEqual(role.occurred_at, role.effective_at)
        self.assertIsNotNone(role.recorded_at)
        self.assertLessEqual(role.received_at, role.recorded_at)
        self.assertEqual(role.provisions.count(), 0)
        self.assertEqual(ContextualRoleAssignment.objects.get(pk=ids["assignments"]["COORDINATOR"]).role_definition_id, role.pk)
        for column in ("retired_by_decision_reference", "retired_effective_at", "retired_received_at",
                       "retired_recorded_at", "retirement_reason"):
            self.assertIsNone(getattr(role, column), column)
        birth(route)
        self.assertEqual(LivingOrganism.objects.get(pk=route.organism.pk).state, "ACTIVE")

    def _bare_organism(self, template, slug):
        """An organism row with no founding population and no definition, inside the caller's
        transaction (never committed: 0021's founding-population guard would refuse the commit)."""
        organism_uuid = str(uuid4())
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO core_livingorganism (organism_id, slug, state, created_at, current_state_effective_at, "
                "state_known_at, state_source_event_set_fingerprint, constitutional_spine_reference, platform_root) "
                "SELECT %s, %s, 'FOUNDING_PENDING', created_at, current_state_effective_at, state_known_at, %s, "
                "constitutional_spine_reference, platform_root "
                "FROM core_livingorganism WHERE id = %s RETURNING id",
                (organism_uuid, slug, empty_fp(cursor, "core_livingorganism", organism_uuid), template.pk),
            )
            return LivingOrganism.objects.get(pk=cursor.fetchone()[0])

    def _founding_shaped(self, organism, enterer, decision, *, code="COORDINATOR", kind="COORDINATOR", at=None):
        at = decision.evaluated_at if at is None else at
        return _write_definition_version(
            database_alias="default", organism=organism, code=code, kind=kind,
            appointing_authority="LO_COORDINATOR_BODY", appointing_body_reference=None,
            alternative_route_reference=None, nomination_basis="SILENT", nomination_policy="C",
            nomination_dependency=None, participation_required=False, ex_officio_seat_reference=None,
            supersedes=None, enterer=enterer, authority_decision_reference=decision.decision_reference,
            occurred_at=at, effective_at=at, received_at=at,
        )

    def test_sr_c1_02_founding_branch_refuses_a_row_citing_another_organisms_founding(self):
        """Guard layer (sr_00, the founding branch's organism condition). A COORDINATOR version-1
        row for an organism with no definition, citing LO A's real founding decision with LO A's
        founder as enterer at that decision's time: the target names another organism; refused,
        nothing written."""
        route_a, ids_a = self._founded("src1-02")
        founding_a = AuthorityDecision.objects.get(
            decision_reference=OrganismRoleDefinition.objects.get(pk=ids_a["roles"]["COORDINATOR"]).authority_decision_reference
        )
        before = self._state()
        with transaction.atomic():
            bare = self._bare_organism(LivingOrganism.objects.get(pk=route_a.organism.pk), "srm-c1-02-bare")
            with self.assertRaises(IntegrityError) as refused:
                with transaction.atomic():
                    self._founding_shaped(bare, route_a.founder, founding_a)
            self.assertIn("SR definition refused: founding decision does not match definition", str(refused.exception))
            transaction.set_rollback(True)
        self.assertEqual(self._state(), before)

    def test_sr_c1_03_founding_branch_refuses_another_actor_another_time_another_code(self):
        """Guard layer (sr_00, the founding branch's actor, time and code conditions, and the
        version condition). With LO A's real founding decision: (i) a member as enterer on a bare
        organism; (ii) the founder at a received_at that is not the decision's evaluated_at; (iii) a non-COORDINATOR code and kind
        on LO A at the right time; (iv) a second COORDINATOR version citing the founding decision.
        Each refused, nothing written."""
        route_a, ids_a = self._founded("src1-03")
        role_a = OrganismRoleDefinition.objects.get(pk=ids_a["roles"]["COORDINATOR"])
        founding_a = AuthorityDecision.objects.get(decision_reference=role_a.authority_decision_reference)
        member = self._member(route_a, "src1-03")
        organism_a = LivingOrganism.objects.get(pk=route_a.organism.pk)
        before = self._state()
        with transaction.atomic():
            bare = self._bare_organism(organism_a, "srm-c1-03-bare")
            cases = (
                (dict(organism=bare, enterer=member, decision=founding_a),
                 "SR definition refused: governed decision does not match definition"),
                (dict(organism=bare, enterer=route_a.founder, decision=founding_a,
                      at=founding_a.evaluated_at - dt.timedelta(microseconds=1)),
                 "SR definition refused: governed decision does not match definition"),
                (dict(organism=organism_a, enterer=route_a.founder, decision=founding_a,
                      code="TEST_ONLY_X", kind="BOARD_OFFICE"),
                 "SR definition refused: founding decision does not match definition"),
            )
            for kwargs, message in cases:
                with self.subTest(message=message, case=kwargs.get("code", "COORDINATOR")):
                    with self.assertRaises(IntegrityError) as refused:
                        with transaction.atomic():
                            self._founding_shaped(**kwargs)
                    self.assertIn(message, str(refused.exception))
            # (iv) a second COORDINATOR version under the founding decision: version 1 is active, so
            # deactivate it under the latitude of this never-committed transaction first.
            with connection.cursor() as cursor:
                cursor.execute("ALTER TABLE public.core_organismroledefinition DISABLE TRIGGER sr_20_definition_superseded")
                cursor.execute("UPDATE core_organismroledefinition SET active = FALSE WHERE id = %s", (role_a.pk,))
            with self.assertRaises(IntegrityError) as refused:
                with transaction.atomic():
                    self._founding_shaped(organism_a, route_a.founder, founding_a)
            self.assertIn("SR definition refused: founding decision does not match definition", str(refused.exception))
            transaction.set_rollback(True)
        self.assertEqual(self._state(), before)

    # ---- the writer ----

    def test_sr_c1_04_writer_refuses_malformed_arguments_before_any_write(self):
        """Writer layer. Outside a transaction; NAMED_BODY without its body reference; a kind
        outside the domain; CUSTOM without basis CUSTOM: S015RoleDefinitionMalformed, no row."""
        route, _ = self._founded("src1-04")
        organism = LivingOrganism.objects.get(pk=route.organism.pk)
        before = self._state()
        common = dict(
            database_alias="default", organism=organism, code="TEST_ONLY_TREASURER", kind="BOARD_OFFICE",
            appointing_authority="LO_COORDINATOR_BODY", appointing_body_reference=None,
            alternative_route_reference=None, nomination_basis="SILENT", nomination_policy="C",
            nomination_dependency=None, participation_required=False, ex_officio_seat_reference=None,
            supersedes=None, enterer=route.founder, authority_decision_reference="s015d1:" + "0" * 64,
            occurred_at=_database_now(), effective_at=_database_now(), received_at=_database_now(),
        )
        with self.assertRaisesRegex(S015RoleDefinitionMalformed, "inside the caller's transaction"):
            _write_definition_version(**common)
        cases = (
            ({"appointing_authority": "NAMED_BODY"}, "required iff NAMED_BODY"),
            ({"kind": "CHAIRPERSON"}, "kind is outside its domain"),
            ({"kind": "CUSTOM"}, "CUSTOM iff kind is CUSTOM"),
            ({"nomination_policy": "UNSUPPORTED"}, "required iff UNSUPPORTED"),
            ({"code": "COORDINATOR"}, "COORDINATOR iff code is COORDINATOR"),
            ({"provisions": (DefinitionProvision("OFFICE_BASIS", "x", "v1"), DefinitionProvision("OFFICE_BASIS", "y", "v1"))},
             "one citation per relationship"),
        )
        for overrides, message in cases:
            with self.subTest(message=message), transaction.atomic():
                with self.assertRaisesRegex(S015RoleDefinitionMalformed, message):
                    _write_definition_version(**{**common, **overrides})
        self.assertEqual(self._state(), before)

    def test_sr_c1_05_writer_records_a_board_office_with_its_citation(self):
        """Writer + guard layer at an outer commit. A BOARD_OFFICE definition with its OFFICE_BASIS
        citation under a canonical test-only definition decision: the version row and the
        provision row persist with database-assigned record times; sr_30 is satisfied."""
        route, _ = self._founded("src1-05")
        before = self._state()
        row = self._write(route, label="src1-05")
        self.assertEqual(row.kind, "BOARD_OFFICE")
        self.assertEqual(row.definition_version, 1)
        self.assertTrue(row.active)
        self.assertIsNotNone(row.recorded_at)
        self.assertEqual(row.enterer_id, route.founder.pk)
        provisions = list(row.provisions.order_by("pk"))
        self.assertEqual([p.relationship for p in provisions], ["OFFICE_BASIS"])
        self.assertIsNotNone(provisions[0].recorded_at)
        after = self._state()
        self.assertEqual(len(after["definitions"]), len(before["definitions"]) + 1)
        self.assertEqual(len(after["provisions"]), len(before["provisions"]) + 1)

    # ---- sr_00: the insert contract ----

    def test_sr_c1_06_pre_sr_shaped_insert_refused_attribution_incomplete(self):
        """Guard layer (sr_00). The pre-SR seven-column insert - the shape every fixture used
        before C1 - is refused on an LO-scoped code: the writer rule at the database."""
        route, _ = self._founded("src1-06")
        before = self._state()

        def bare_insert():
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO core_organismroledefinition (role_uuid, code, scope, definition_version, active, "
                    "created_at, living_organism_id) VALUES (%s, 'TEST_ONLY_BARE', 'LIVING_ORGANISM', 1, TRUE, now(), %s)",
                    (str(uuid4()), route.organism.pk),
                )
        self._assert_refused(before, "SR definition refused: content or attribution incomplete", bare_insert)

    def test_sr_c1_07_circle_scoped_bare_insert_is_outside_the_definition_guards(self):
        """Guard layer (the stated scope reading). A CIRCLE-scoped definition is S4-C's: sr_00 and
        sr_30 return it untouched; 0021's constraints still apply to it."""
        route, _ = self._founded("src1-07")
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO core_organismroledefinition (role_uuid, code, scope, definition_version, active, "
                "created_at, living_organism_id) VALUES (%s, 'TEST_ONLY_CIRCLE_LEAD', 'CIRCLE', 1, TRUE, now(), %s) "
                "RETURNING recorded_at",
                (str(uuid4()), route.organism.pk),
            )
            self.assertIsNone(cursor.fetchone()[0])  # None: the guard assigned nothing to a CIRCLE row

    def test_sr_c1_08_reserved_root_office_refused_under_a_definition_decision(self):
        """Guard layer (sr_00, ruling 4; W-18 guard half). A fully attributed INTEVIA_ definition
        whose decision is a RECORD_ROLE_DEFINITION decision is refused at the insert, on an
        ordinary LO. (The model's clean() refuses the same before the ORM; the raw statement
        reaches the guard.)"""
        route, _ = self._founded("src1-08")
        organism = LivingOrganism.objects.get(pk=route.organism.pk)
        at = _database_now()
        before = self._state()

        def raw_root_insert():
            with transaction.atomic():
                decision = _test_only_definition_decision(
                    actor=route.founder, evaluated_at=at, label="src1-08",
                    target=_definition_target(
                        organism_uuid=organism.organism_id, code="INTEVIA_TEST_ONLY", definition_version=1,
                        kind="BOARD_OFFICE", nomination_policy="C", appointing_authority="LO_COORDINATOR_BODY",
                    ),
                )
                with connection.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO core_organismroledefinition (role_uuid, code, scope, definition_version, active, "
                        "created_at, living_organism_id, kind, appointing_authority, nomination_basis, nomination_policy, "
                        "participation_required, enterer_id, enterer_access_epoch, authority_decision_reference, "
                        "occurred_at, effective_at, received_at) VALUES (%s, 'INTEVIA_TEST_ONLY', 'LIVING_ORGANISM', 1, "
                        "TRUE, now(), %s, 'BOARD_OFFICE', 'LO_COORDINATOR_BODY', 'SILENT', 'C', FALSE, %s, %s, %s, %s, %s, %s)",
                        (str(uuid4()), organism.pk, route.founder.pk, route.founder.access_epoch,
                         decision.decision_reference, at, at, at),
                    )
        self._assert_refused(before, "SR definition refused: reserved root office", raw_root_insert)

    def test_sr_c1_09_custom_role_without_entitlement_refused_at_received_at(self):
        """Guard layer (sr_00; W-9 guard half). kind CUSTOM on an LO with no CUSTOM_ROLES
        entitlement effective at NEW.received_at: refused, nothing written. Then, after the LO's
        beta opt-in through the real entitlement route, the same definition is recorded."""
        route, _ = self._founded("src1-09")
        organism = LivingOrganism.objects.get(pk=route.organism.pk)
        self.assertFalse(entitlement_effective(organism.organism_id, "CUSTOM_ROLES", at=_database_now()))
        before = self._state()
        self._assert_refused(
            before, "SR definition refused: custom role entitlement absent at receipt",
            lambda: self._write(route, label="src1-09a", code="TEST_ONLY_ADJUNCT", kind="CUSTOM",
                                nomination_basis="CUSTOM", provisions=()),
        )
        birth(route)  # the opt-in follows Birthing's FREE election (HD-X-1)
        policy_version, policy_text_sha256 = ensure_test_policy_text()
        service = OrganismEntitlementService(authority=route.birthing_service._authority, clock=_database_now)
        service.activate_beta(EntitlementElectionCommand(
            credential=route.founder.credential,
            living_organism_uuid=organism.organism_id,
            request_reference=f"sr-c1-09-beta:{route.tag}",
            idempotency_key=f"sr-c1-09-beta:key:{route.tag}",
            occurred_at=_database_now(),
            policy_version=policy_version,
            policy_text_sha256=policy_text_sha256,
        ))
        self.assertTrue(entitlement_effective(organism.organism_id, "CUSTOM_ROLES", at=_database_now()))
        row = self._write(route, label="src1-09b", code="TEST_ONLY_ADJUNCT", kind="CUSTOM",
                          nomination_basis="CUSTOM", provisions=())
        self.assertEqual(row.kind, "CUSTOM")
        self.assertIsNotNone(row.recorded_at)

    def test_sr_c1_10_decision_that_does_not_name_this_version_refused(self):
        """Guard layer (sr_00). The decision is QUALIFIED for the act but its target names another
        code; and a REFUSED decision; and a decision evaluated at another time: each refused."""
        route, _ = self._founded("src1-10")
        organism = LivingOrganism.objects.get(pk=route.organism.pk)
        before = self._state()
        wrong_code = _definition_target(
            organism_uuid=organism.organism_id, code="TEST_ONLY_OTHER", definition_version=1,
            kind="BOARD_OFFICE", nomination_policy="C", appointing_authority="LO_COORDINATOR_BODY",
        )
        self._assert_refused(
            before, "SR definition refused: governed decision does not match definition",
            lambda: self._write(route, label="src1-10a", decision_target=wrong_code),
        )
        at = _database_now()
        self._assert_refused(
            before, "SR definition refused: governed decision does not match definition",
            lambda: self._write(route, label="src1-10b", received_at=at,
                                decision_evaluated_at=at + dt.timedelta(microseconds=1)),
        )
        self._assert_refused(
            before, "SR definition refused: governed decision does not match definition",
            lambda: self._write(route, label="src1-10c", decision_outcome="REFUSED"),
        )

    def test_sr_c1_11_client_recorded_at_refused(self):
        """Guard layer (sr_00). A client-supplied recorded_at is refused: record time is the
        database's (0037's pattern)."""
        route, _ = self._founded("src1-11")
        organism = LivingOrganism.objects.get(pk=route.organism.pk)
        at = _database_now()
        before = self._state()

        def client_time():
            with transaction.atomic():
                decision = _test_only_definition_decision(
                    actor=route.founder, evaluated_at=at, label="src1-11",
                    target=_definition_target(
                        organism_uuid=organism.organism_id, code="TEST_ONLY_T", definition_version=1,
                        kind="BOARD_OFFICE", nomination_policy="C", appointing_authority="LO_COORDINATOR_BODY",
                    ),
                )
                with connection.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO core_organismroledefinition (role_uuid, code, scope, definition_version, active, "
                        "created_at, living_organism_id, kind, appointing_authority, nomination_basis, nomination_policy, "
                        "participation_required, enterer_id, enterer_access_epoch, authority_decision_reference, "
                        "occurred_at, effective_at, received_at, recorded_at) VALUES (%s, 'TEST_ONLY_T', 'LIVING_ORGANISM', 1, "
                        "TRUE, now(), %s, 'BOARD_OFFICE', 'LO_COORDINATOR_BODY', 'SILENT', 'C', FALSE, %s, %s, %s, %s, %s, %s, %s)",
                        (str(uuid4()), organism.pk, route.founder.pk, route.founder.access_epoch,
                         decision.decision_reference, at, at, at, at),
                    )
        self._assert_refused(before, "SR definition refused: recorded_at is database-assigned", client_time)

    # ---- sr_30: the citations at commit (W-17 guard half) ----

    def test_sr_c1_12_board_office_without_office_basis_refused_at_commit(self):
        """Guard layer (sr_30, deferred). A BOARD_OFFICE version with no OFFICE_BASIS row: the
        statement succeeds, the commit is refused with the office-basis message; the version row
        and the decision are rolled back (whole-state)."""
        route, _ = self._founded("src1-12")
        before = self._state()
        self._assert_refused(
            before, "SR definition refused: office basis citation missing",
            lambda: self._write(route, label="src1-12", provisions=()),
        )

    def test_sr_c1_13_governance_relationship_without_its_citation_refused_at_commit(self):
        """Guard layer (sr_30). NAMED_BODY without an APPOINTING_AUTHORITY citation; a
        participation requirement without PARTICIPATION_REQUIRED; a citation that no relationship
        calls for: each refused at commit, whole-state."""
        route, _ = self._founded("src1-13")
        before = self._state()
        basis = DefinitionProvision("OFFICE_BASIS", "library:srm-c1-13#art-7", "v1")
        self._assert_refused(
            before, "SR definition refused: governance relationship citation missing (APPOINTING_AUTHORITY)",
            lambda: self._write(route, label="src1-13a", appointing_authority="NAMED_BODY",
                                appointing_body_reference="the Board", provisions=(basis,)),
        )
        self._assert_refused(
            before, "SR definition refused: governance relationship citation missing (PARTICIPATION_REQUIRED)",
            lambda: self._write(route, label="src1-13b", participation_required=True, provisions=(basis,)),
        )
        self._assert_refused(
            before, "SR definition refused: citation without its relationship (EX_OFFICIO_SEAT)",
            lambda: self._write(route, label="src1-13c", provisions=(
                basis, DefinitionProvision("EX_OFFICIO_SEAT", "library:srm-c1-13#art-9", "v1"),
            )),
        )
        row = self._write(route, label="src1-13d", appointing_authority="NAMED_BODY",
                          appointing_body_reference="the Board", participation_required=True,
                          provisions=(basis,
                                      DefinitionProvision("APPOINTING_AUTHORITY", "library:srm-c1-13#art-8", "v1"),
                                      DefinitionProvision("PARTICIPATION_REQUIRED", "library:srm-c1-13#art-10", "v1")))
        self.assertEqual(sorted(row.provisions.values_list("relationship", flat=True)),
                         ["APPOINTING_AUTHORITY", "OFFICE_BASIS", "PARTICIPATION_REQUIRED"])
        # The other three relationships are independent iff conditions too.
        for relationship, fields in (
            ("ALTERNATIVE_ROUTE", {"alternative_route_reference": "express alternative"}),
            ("NOMINATION_ROUTE", {"nomination_basis": "CONSTITUTION"}),
            ("EX_OFFICIO_SEAT", {"ex_officio_seat_reference": "the stated seat"}),
        ):
            with self.subTest(relationship=relationship):
                before = self._state()
                self._assert_refused(
                    before, f"SR definition refused: governance relationship citation missing ({relationship})",
                    lambda relationship=relationship, fields=fields: self._write(
                        route, label=f"src1-13-{relationship}-missing", code=f"TEST_ONLY_{relationship}",
                        provisions=(basis,), **fields,
                    ),
                )
                self._write(
                    route, label=f"src1-13-{relationship}-complete", code=f"TEST_ONLY_{relationship}",
                    provisions=(basis, DefinitionProvision(relationship, "library:src1-13#explicit", "v1")),
                    **fields,
                )

    # ---- sr_10 / sr_20: the one permitted change (W-7) ----

    def test_sr_c1_14_content_update_refused_and_deactivation_needs_its_successor(self):
        """Guard layer (sr_10, sr_20). On an SR-attributed version: changing a content column is
        refused; deactivating it without a successor is refused at commit; deactivating it and
        writing version 2 with supersedes naming it commits (the AMEND shape)."""
        route, _ = self._founded("src1-14")
        v1 = self._write(route, label="src1-14a")
        before = self._state()

        def content_update():
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("UPDATE core_organismroledefinition SET nomination_policy = 'A' WHERE id = %s", (v1.pk,))
        self._assert_refused(before, "SR definition refused: a recorded version is immutable", content_update)

        def reactivate_shape():
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("UPDATE core_organismroledefinition SET active = TRUE WHERE id = %s", (v1.pk,))
        self._assert_refused(before, "SR definition refused: only the active version may be deactivated", reactivate_shape)

        def deactivate_without_successor():
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("UPDATE core_organismroledefinition SET active = FALSE WHERE id = %s", (v1.pk,))
        self._assert_refused(before, "SR definition refused: a deactivated version must be superseded by a successor at commit",
                             deactivate_without_successor)

        organism = LivingOrganism.objects.get(pk=route.organism.pk)
        at = _database_now()
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("UPDATE core_organismroledefinition SET active = FALSE WHERE id = %s", (v1.pk,))
            decision = _test_only_definition_decision(
                actor=route.founder, evaluated_at=at, label="src1-14b",
                target=_definition_target(
                    organism_uuid=organism.organism_id, code=v1.code, definition_version=2, kind="BOARD_OFFICE",
                    nomination_policy="C", appointing_authority="LO_COORDINATOR_BODY", event_action="AMEND",
                    supersedes_role_uuid=v1.role_uuid,
                    provisions=(DefinitionProvision("OFFICE_BASIS", "library:srm-c1-14#art-7", "v2"),),
                ),
            )
            v2 = _write_definition_version(
                database_alias="default", organism=organism, code=v1.code, kind="BOARD_OFFICE",
                appointing_authority="LO_COORDINATOR_BODY", appointing_body_reference=None,
                alternative_route_reference=None, nomination_basis="SILENT", nomination_policy="C",
                nomination_dependency=None, participation_required=False, ex_officio_seat_reference=None,
                supersedes=OrganismRoleDefinition.objects.get(pk=v1.pk), enterer=route.founder,
                authority_decision_reference=decision.decision_reference,
                occurred_at=at, effective_at=at, received_at=at,
                provisions=(DefinitionProvision("OFFICE_BASIS", "library:srm-c1-14#art-7", "v2"),),
            )
        v1 = OrganismRoleDefinition.objects.get(pk=v1.pk)
        v2 = OrganismRoleDefinition.objects.get(pk=v2.pk)
        self.assertFalse(v1.active)
        self.assertTrue(v2.active)
        self.assertEqual(v2.definition_version, 2)
        self.assertEqual(v2.supersedes_id, v1.pk)
        self.assertIsNone(v1.retired_recorded_at)

    def test_sr_c1_15_supersedes_must_name_the_deactivated_prior_version(self):
        """Guard layer (sr_00). A version whose supersedes names a still-active version 1 is refused;
        a version skipping the version rule (a raw version 3 with no version 2) is refused."""
        route, _ = self._founded("src1-15")
        v1 = self._write(route, label="src1-15a")
        organism = LivingOrganism.objects.get(pk=route.organism.pk)
        before = self._state()
        at = _database_now()

        def version_skip():
            with transaction.atomic():
                decision = _test_only_definition_decision(
                    actor=route.founder, evaluated_at=at, label="src1-15b",
                    target=_definition_target(
                        organism_uuid=organism.organism_id, code="TEST_ONLY_SKIP", definition_version=3,
                        kind="BOARD_OFFICE", nomination_policy="C", appointing_authority="LO_COORDINATOR_BODY",
                    ),
                )
                with connection.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO core_organismroledefinition (role_uuid, code, scope, definition_version, active, "
                        "created_at, living_organism_id, kind, appointing_authority, nomination_basis, nomination_policy, "
                        "participation_required, enterer_id, enterer_access_epoch, authority_decision_reference, "
                        "occurred_at, effective_at, received_at) VALUES (%s, 'TEST_ONLY_SKIP', 'LIVING_ORGANISM', 3, "
                        "TRUE, now(), %s, 'BOARD_OFFICE', 'LO_COORDINATOR_BODY', 'SILENT', 'C', FALSE, %s, %s, %s, %s, %s, %s)",
                        (str(uuid4()), organism.pk, route.founder.pk, route.founder.access_epoch,
                         decision.decision_reference, at, at, at),
                    )
        self._assert_refused(before, "SR definition refused: version must be one above the latest", version_skip)

        def supersedes_active():
            with transaction.atomic():
                decision = _test_only_definition_decision(
                    actor=route.founder, evaluated_at=at, label="src1-15c",
                    target=_definition_target(
                        organism_uuid=organism.organism_id, code=v1.code, definition_version=2, kind="BOARD_OFFICE",
                        nomination_policy="C", appointing_authority="LO_COORDINATOR_BODY", event_action="AMEND",
                    ),
                )
                _write_definition_version(
                    database_alias="default", organism=organism, code=v1.code, kind="BOARD_OFFICE",
                    appointing_authority="LO_COORDINATOR_BODY", appointing_body_reference=None,
                    alternative_route_reference=None, nomination_basis="SILENT", nomination_policy="C",
                    nomination_dependency=None, participation_required=False, ex_officio_seat_reference=None,
                    supersedes=v1, enterer=route.founder, authority_decision_reference=decision.decision_reference,
                    occurred_at=at, effective_at=at, received_at=at,
                    provisions=(DefinitionProvision("OFFICE_BASIS", "library:srm-c1-15#art-7", "v2"),),
                )
        self._assert_refused(before, "SR definition refused: supersedes must name the deactivated, unretired prior version",
                             supersedes_active)

    def test_sr_c1_16_retirement_attribution_record_time_and_holder_condition(self):
        """Guard layer (sr_10 branch (a)). The retirement UPDATE: refused with a client
        retired_recorded_at; refused with a decision that is not a RETIRE decision for this
        role_uuid; refused while the founder's own COORDINATOR office is held (the holder
        condition, exercised on the founding COORDINATOR version); recorded on a holder-free
        definition with retired_recorded_at assigned by the database."""
        route, ids = self._founded("src1-16")
        organism = LivingOrganism.objects.get(pk=route.organism.pk)
        treasurer = self._write(route, label="src1-16a")
        coordinator = OrganismRoleDefinition.objects.get(pk=ids["roles"]["COORDINATOR"])
        at = _database_now()

        def retire_sql(row, decision_reference, *, client_recorded=False):
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE core_organismroledefinition SET active = FALSE, retired_by_decision_reference = %s, "
                    "retired_effective_at = %s, retired_received_at = %s, retirement_reason = 'labelled test retirement'"
                    + (", retired_recorded_at = %s" if client_recorded else "")
                    + " WHERE id = %s",
                    (decision_reference, at, at) + ((at,) if client_recorded else ()) + (row.pk,),
                )

        with transaction.atomic():
            retire_treasurer = _test_only_definition_decision(
                actor=route.founder, evaluated_at=at, label="src1-16-retire-t",
                target=_definition_target(
                    organism_uuid=organism.organism_id, code=treasurer.code, definition_version=1, kind="BOARD_OFFICE",
                    nomination_policy="C", appointing_authority="LO_COORDINATOR_BODY", event_action="RETIRE",
                    role_uuid=treasurer.role_uuid,
                    provisions=tuple(DefinitionProvision(p.relationship, p.provision_reference, p.provision_version)
                                     for p in treasurer.provisions.order_by("relationship")),
                ),
            )
            retire_coordinator = _test_only_definition_decision(
                actor=route.founder, evaluated_at=at, label="src1-16-retire-c",
                target=_definition_target(
                    organism_uuid=organism.organism_id, code="COORDINATOR", definition_version=1, kind="COORDINATOR",
                    nomination_policy="C", appointing_authority="LO_COORDINATOR_BODY", event_action="RETIRE",
                    role_uuid=coordinator.role_uuid,
                ),
            )
        before = self._state()
        self._assert_refused(before, "SR definition refused: retired_recorded_at is database-assigned",
                             lambda: retire_sql(treasurer, retire_treasurer.decision_reference, client_recorded=True))
        self._assert_refused(before, "SR definition refused: retiring decision does not match definition",
                             lambda: retire_sql(treasurer, retire_coordinator.decision_reference))
        def incomplete():
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE core_organismroledefinition SET active = FALSE, retired_by_decision_reference = %s "
                    "WHERE id = %s", (retire_treasurer.decision_reference, treasurer.pk),
                )
        self._assert_refused(before, "SR definition refused: retirement attribution incomplete", incomplete)
        self.assertEqual(ContextualRoleAssignment.objects.get(pk=ids["assignments"]["COORDINATOR"]).state, "ACTIVE")
        self._assert_refused(before, "SR definition refused: this office still has a holder or a pending nomination",
                             lambda: retire_sql(coordinator, retire_coordinator.decision_reference))
        retire_sql(treasurer, retire_treasurer.decision_reference)
        retired = OrganismRoleDefinition.objects.get(pk=treasurer.pk)
        self.assertFalse(retired.active)
        self.assertEqual(retired.retired_by_decision_reference, retire_treasurer.decision_reference)
        self.assertIsNotNone(retired.retired_recorded_at)
        self.assertLessEqual(retired.retired_received_at, retired.retired_recorded_at)
        self.assertEqual(retired.kind, "BOARD_OFFICE")
        # a retired version is immutable: a second retirement statement is refused
        after = self._state()
        self._assert_refused(after, "SR definition refused: a recorded version is immutable",
                             lambda: retire_sql(treasurer, retire_treasurer.decision_reference))

    # ---- the provision table ----

    def test_sr_c1_17_provision_rows_are_append_only_with_database_record_time(self):
        """Guard layer (the s015_sr_provision_* triggers). UPDATE, DELETE and TRUNCATE of a provision row are refused by
        s015_refuse_row_mutation; a client recorded_at is refused; the provision and its version
        are required."""
        route, _ = self._founded("src1-17")
        row = self._write(route, label="src1-17")
        provision = row.provisions.get()
        before = self._state()
        for statement, params in (
            ("UPDATE core_organismroledefinitionprovision SET provision_version = 'v9' WHERE id = %s", (provision.pk,)),
            ("DELETE FROM core_organismroledefinitionprovision WHERE id = %s", (provision.pk,)),
            ("TRUNCATE core_organismroledefinitionprovision", ()),
        ):
            with self.subTest(statement=statement.split()[0]):
                def act():
                    with transaction.atomic(), connection.cursor() as cursor:
                        cursor.execute(statement, params)
                with self.assertRaises(IntegrityError):
                    act()
                self.assertEqual(self._state(), before)

        def client_recorded_at():
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO core_organismroledefinitionprovision (provision_uuid, definition_id, relationship, "
                    "provision_reference, provision_version, recorded_at) VALUES (%s, %s, 'EX_OFFICIO_SEAT', 'x', 'v1', now())",
                    (str(uuid4()), row.pk),
                )
        self._assert_refused(before, "SR provision refused: recorded_at is database-assigned", client_recorded_at)

        def empty_reference():
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO core_organismroledefinitionprovision (provision_uuid, definition_id, relationship, "
                    "provision_reference, provision_version) VALUES (%s, %s, 'EX_OFFICIO_SEAT', ' ', 'v1')",
                    (str(uuid4()), row.pk),
                )
        self._assert_refused(before, "SR provision refused: the provision and its version are required", empty_reference)

    # ---- the migration (W-13 half; A-9) ----

    def _schema_facts(self):
        """Raw reads: names/bodies/definitions are str, validation is bool; no raw jsonb.

        Capture actual bindings, enabled states, function bodies and the complete
        CHECK as well as counts, so restoration assertions cannot hide drift.
        """
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = 'core_organismroledefinition'"
            )
            columns = {row[0] for row in cursor.fetchall()} & SR_COLUMNS
            cursor.execute(
                "SELECT t.tgname, t.tgenabled, pg_get_triggerdef(t.oid) "
                "FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND NOT t.tgisinternal"
            )
            bindings = sorted(tuple(row) for row in cursor.fetchall())
            names = [row[0] for row in bindings]
            cursor.execute("SELECT to_regclass('public.core_organismroledefinitionprovision') IS NOT NULL")
            provision_table = cursor.fetchone()[0]
            cursor.execute(
                "SELECT pg_get_constraintdef(con.oid), con.convalidated "
                "FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid "
                "WHERE c.relname = 'core_commandintake' AND con.conname = 'h100_intake_entry_valid'"
            )
            intake_check = tuple(cursor.fetchone())
            literals = frozenset(re.findall(r"'([A-Z_]+)'::character varying", intake_check[0]))
            cursor.execute(
                "SELECT p.proname, p.prosrc FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = 'public' AND (p.proname LIKE 'sr\\_%' OR p.proname LIKE 's015\\_sr\\_%') "
                "ORDER BY p.proname"
            )
            functions = [tuple(row) for row in cursor.fetchall()]
        return {
            "sr_columns": columns,
            "sr_triggers": {name for name in names if name.startswith(("sr_", "s015_sr_"))},
            "trigger_count": len(names),
            "trigger_bindings": bindings,
            "sr_functions": functions,
            "provision_table": provision_table,
            "intake_literals": literals,
            "intake_check": intake_check,
            "head": current_applied_core_head(connection)[0][1],
        }

    def test_sr_c1_18_migration_0051_reverses_cleanly_and_reapplies(self):
        """Migration layer. From 0051 on a clean state: reverse to 0050 restores the schema exactly
        (no SR column, no provision table, no sr_* trigger, 475 triggers, the intake CHECK back at
        0049's 22 entries); forward to 0051 installs it again (483 triggers, the CHECK at 23)."""
        at_0051 = self._schema_facts()
        self.assertEqual(at_0051["head"], HEAD_0051)
        self.assertEqual(at_0051["sr_columns"], SR_COLUMNS)
        self.assertEqual(at_0051["sr_triggers"], SR_TRIGGER_NAMES)
        self.assertEqual(at_0051["trigger_count"], TRIGGERS_AT_0051)
        self.assertTrue(at_0051["provision_table"])
        self.assertIn("RECORD_ROLE_DEFINITION", at_0051["intake_literals"])
        self.assertEqual(len(at_0051["intake_literals"]), len(IntakeEntry))
        # A pre-SR row founded before this test? None: this test founds nothing; the state is clean.
        self.assertEqual(OrganismRoleDefinition.objects.filter(kind__isnull=False).count(), 0)
        migrate_to_target_with_restore(self, ("core", HEAD_0050))
        at_0050 = self._schema_facts()
        self.assertEqual(at_0050["head"], HEAD_0050)
        self.assertEqual(at_0050["sr_columns"], set())
        self.assertEqual(at_0050["sr_triggers"], set())
        self.assertEqual(at_0050["sr_functions"], [])
        self.assertEqual(at_0050["trigger_count"], TRIGGERS_AT_0050)
        self.assertFalse(at_0050["provision_table"])
        self.assertEqual(len(at_0050["intake_literals"]), 22)
        self.assertNotIn("RECORD_ROLE_DEFINITION", at_0050["intake_literals"])
        self.restore_received_migrations()
        again = self._schema_facts()
        self.assertEqual(again, at_0051)

    def test_sr_c1_19_populated_reverse_refused_naming_the_rows(self):
        """Migration layer (A-9). With one founded LO - whose COORDINATOR version carries SR
        attribution - the reverse to 0050 is refused with the reason naming the row counts; the
        schema stays at 0051 (the migration is atomic); the founded rows are untouched."""
        route, ids = self._founded("src1-19")
        row = OrganismRoleDefinition.objects.get(pk=ids["roles"]["COORDINATOR"])
        before = self._state()
        facts_before = self._schema_facts()
        executor = MigrationExecutor(connection)
        with self.assertRaises(RuntimeError) as refused:
            executor.migrate([("core", HEAD_0050)])
        message = str(refused.exception)
        self.assertIn("SR 0051 reverse refused: the database is populated under SR", message)
        self.assertIn("1 definition row(s) carrying SR attribution", message)
        self.assertIn(str(row.role_uuid), message)
        self.assertIn("0 intake row(s)", message)
        self.assertIn("0 provision row(s)", message)
        self.assertEqual(self._schema_facts(), facts_before)
        self.assertEqual(self._state(), before)

    # ---- the fixture latitude itself ----

    def test_sr_c1_20_fixture_latitude_suspends_only_the_insert_guard_and_restores_it(self):
        """Fixture layer. Under sr_definition_guard_suspended a pre-SR-shaped row is written;
        the trigger is enabled again afterwards (pg_trigger.tgenabled = 'O'); outside it the same
        statement is refused."""
        route, _ = self._founded("src1-20")
        with transaction.atomic(), connection.cursor() as cursor:
            with sr_definition_guard_suspended(cursor):
                cursor.execute(
                    "INSERT INTO core_organismroledefinition (role_uuid, code, scope, definition_version, active, "
                    "created_at, living_organism_id) VALUES (%s, 'TEST_ONLY_LATITUDE', 'LIVING_ORGANISM', 1, TRUE, now(), %s)",
                    (str(uuid4()), route.organism.pk),
                )
                cursor.execute("SELECT tgenabled FROM pg_trigger WHERE tgname = 'sr_00_definition_insert'")
                self.assertEqual(cursor.fetchone()[0], "D")  # str: disabled inside the latitude
            cursor.execute("SELECT tgenabled FROM pg_trigger WHERE tgname = 'sr_00_definition_insert'")
            self.assertEqual(cursor.fetchone()[0], "O")  # str: enabled again
        before = self._state()

        def outside():
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO core_organismroledefinition (role_uuid, code, scope, definition_version, active, "
                    "created_at, living_organism_id) VALUES (%s, 'TEST_ONLY_LATITUDE_2', 'LIVING_ORGANISM', 1, TRUE, now(), %s)",
                    (str(uuid4()), route.organism.pk),
                )
        self._assert_refused(before, "SR definition refused: content or attribution incomplete", outside)

    def test_sr_c1_21_circle_row_cannot_supply_an_lo_supersession(self):
        """A bare CIRCLE row remains permitted, but it cannot authorize deactivation of an SR office."""
        route, _ = self._founded("src1-21")
        office = self._write(route, label="src1-21-office")
        # The unrelated Circle row is committed before the challenged UPDATE.
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO core_organismroledefinition (role_uuid, living_organism_id, code, scope, "
                "definition_version, active, created_at, supersedes_id) "
                "VALUES (%s, %s, 'TEST_ONLY_UNRELATED_CIRCLE', 'CIRCLE', 1, TRUE, now(), %s)",
                (str(uuid4()), route.organism.pk, office.pk),
            )
        before = self._state()

        def deactivate():
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("UPDATE core_organismroledefinition SET active = FALSE WHERE id = %s", (office.pk,))
        self._assert_refused(before, "must be superseded by a successor at commit", deactivate)

    def test_sr_c1_22_late_citation_cannot_change_a_committed_version(self):
        """The definition INSERT trigger has already fired; a later citation INSERT is still guarded."""
        route, _ = self._founded("src1-22")
        office = self._write(route, label="src1-22-office")
        before = self._state()
        for relationship in ("APPOINTING_AUTHORITY", "ALTERNATIVE_ROUTE", "NOMINATION_ROUTE",
                             "PARTICIPATION_REQUIRED", "EX_OFFICIO_SEAT"):
            with self.subTest(relationship=relationship):
                def insert_late():
                    with transaction.atomic(), connection.cursor() as cursor:
                        cursor.execute(
                            "INSERT INTO core_organismroledefinitionprovision "
                            "(provision_uuid, definition_id, relationship, provision_reference, provision_version) "
                            "VALUES (%s, %s, %s, 'library:src1-22#late', 'v1')",
                            (str(uuid4()), office.pk, relationship),
                        )
                self._assert_refused(before, "citation without its relationship", insert_late)

    def test_sr_c1_23_fixture_exception_restores_guard_before_caller_continues(self):
        """Both Python and database exceptions leave the outer transaction usable and sr_00 enabled."""
        route, _ = self._founded("src1-23")
        before = self._state()
        with connection.cursor() as cursor:
            with self.assertRaisesRegex(RuntimeError, "requires an outer atomic transaction"):
                with sr_definition_guard_suspended(cursor):
                    self.fail("autocommit fixture latitude must not enter")
        for exception_kind in ("python", "sql", "nested"):
            with self.subTest(exception_kind=exception_kind), transaction.atomic(), connection.cursor() as cursor:
                expected = {"python": ValueError, "sql": DatabaseError, "nested": RuntimeError}[exception_kind]
                with self.assertRaises(expected):
                    with sr_definition_guard_suspended(cursor):
                        cursor.execute(
                            "INSERT INTO core_organismroledefinition (role_uuid, code, scope, definition_version, "
                            "active, created_at, living_organism_id) "
                            "VALUES (%s, 'TEST_ONLY_EXCEPTION', 'LIVING_ORGANISM', 1, TRUE, now(), %s)",
                            (str(uuid4()), route.organism.pk),
                        )
                        if exception_kind == "python":
                            raise ValueError("test-only fixture interruption")
                        if exception_kind == "sql":
                            cursor.execute("SELECT 1 / 0")
                        if exception_kind == "nested":
                            with sr_definition_guard_suspended(cursor):
                                self.fail("nested fixture latitude must not enter")
                cursor.execute(
                    "SELECT tgenabled FROM pg_trigger WHERE tgrelid = 'public.core_organismroledefinition'::regclass "
                    "AND tgname = 'sr_00_definition_insert'"
                )
                self.assertEqual(cursor.fetchone()[0], "O")
                self.assertEqual(self._state(), before)
                with self.assertRaisesRegex(IntegrityError, "content or attribution incomplete"):
                    with transaction.atomic():
                        cursor.execute(
                            "INSERT INTO core_organismroledefinition (role_uuid, code, scope, definition_version, "
                            "active, created_at, living_organism_id) "
                            "VALUES (%s, 'TEST_ONLY_AFTER_EXCEPTION', 'LIVING_ORGANISM', 1, TRUE, now(), %s)",
                            (str(uuid4()), route.organism.pk),
                        )

    def test_sr_c1_24_preflight_refuses_schema_and_guard_drift_before_install(self):
        """No SR schema is installed over unexpected objects, a changed CHECK, or disabled/rebound guardians."""
        migrate_to_target_with_restore(self, ("core", HEAD_0050))
        baseline = self._schema_facts()
        old = import_module("core.migrations.0049_h100_command_intake")
        entries = ", ".join("'" + value + "'" for value in old.ENTRY_VALUES)
        prefix = "s015_0021_organismroledefinition_"
        cases = (
            ("column", ("ALTER TABLE core_organismroledefinition ADD COLUMN kind text",), "SR columns already present"),
            ("table", ("CREATE TABLE core_organismroledefinitionprovision (id bigint)",), "already exists"),
            ("function", ("CREATE FUNCTION public.sr_test_stray() RETURNS trigger LANGUAGE plpgsql "
                          "AS $$ BEGIN RETURN NEW; END $$",), "sr_* functions already exist"),
            ("trigger", ("CREATE TRIGGER sr_test_stray BEFORE INSERT ON core_organismroledefinition "
                         "FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation()",), "sr_* triggers already exist"),
            # Same literal set, opposite semantics: the old regex-only preflight missed this.
            ("check", ("ALTER TABLE core_commandintake DROP CONSTRAINT h100_intake_entry_valid",
                       "ALTER TABLE core_commandintake ADD CONSTRAINT h100_intake_entry_valid "
                       f"CHECK (entry NOT IN ({entries}))"), "not exactly 0049's validated entry CHECK"),
            ("disabled", (f"ALTER TABLE core_organismroledefinition DISABLE TRIGGER {prefix}code_immutable",),
             "trigger bindings or enabled states differ"),
            ("rebound", (f"DROP TRIGGER {prefix}code_immutable ON core_organismroledefinition",
                         f"CREATE TRIGGER {prefix}code_immutable BEFORE UPDATE OF code ON core_organismroledefinition "
                         "FOR EACH ROW EXECUTE FUNCTION public.s015_guard_reserved_role_code()"),
             "trigger bindings or enabled states differ"),
        )
        for label, statements, reason in cases:
            with self.subTest(label=label):
                with self.assertRaisesRegex(RuntimeError, re.escape(reason)):
                    with transaction.atomic(), connection.cursor() as cursor:
                        for statement in statements:
                            cursor.execute(statement)
                        MigrationExecutor(connection).migrate([("core", HEAD_0051)])
                self.assertEqual(self._schema_facts(), baseline)
        self.restore_received_migrations()

    def test_sr_c1_25_legacy_rows_survive_forward_install_without_backfill(self):
        """The nullable-install promise is tested on an actual pre-0051 row, not an empty database."""
        migrate_to_target_with_restore(self, ("core", HEAD_0050))
        with transaction.atomic(), connection.cursor() as cursor:
            ids = found_nonroot(cursor, "src1-25-legacy")
            cursor.execute(
                "SELECT role_uuid, living_organism_id, code, scope, definition_version, active, created_at "
                "FROM core_organismroledefinition WHERE id = %s", (ids["roles"]["COORDINATOR"],),
            )
            before = cursor.fetchone()
        self.restore_received_migrations()
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT role_uuid, living_organism_id, code, scope, definition_version, active, created_at "
                "FROM core_organismroledefinition WHERE id = %s", (ids["roles"]["COORDINATOR"],),
            )
            self.assertEqual(cursor.fetchone(), before)
        legacy = OrganismRoleDefinition.objects.get(pk=ids["roles"]["COORDINATOR"])
        self.assertTrue(all(value is None for value in
                            OrganismRoleDefinition.objects.filter(pk=legacy.pk).values_list(*sorted(SR_COLUMNS)).get()))
        before_state = self._state()

        def rewrite_legacy():
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("UPDATE core_organismroledefinition SET nomination_policy = 'A' WHERE id = %s", (legacy.pk,))
        self._assert_refused(before_state, "a recorded version is immutable", rewrite_legacy)
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("UPDATE core_organismroledefinition SET active = FALSE WHERE id = %s", (legacy.pk,))
        self.assertFalse(OrganismRoleDefinition.objects.get(pk=legacy.pk).active)

    def test_sr_c1_26_raw_insert_checks_domains_pairs_time_and_attribution(self):
        """Exercise the database guards directly, without the writer's earlier argument checks."""
        route, _ = self._founded("src1-26")
        at = _database_now()
        with transaction.atomic():
            decision = _test_only_definition_decision(
                actor=route.founder, evaluated_at=at, label="src1-26-decision",
                target=_definition_target(organism_uuid=route.organism.organism_id, code="TEST_ONLY_RAW",
                                          definition_version=1, kind="BOARD_OFFICE", nomination_policy="C",
                                          appointing_authority="LO_COORDINATOR_BODY"),
            )
        common = dict(role_uuid=str(uuid4()), living_organism_id=route.organism.pk, code="TEST_ONLY_RAW",
                      scope="LIVING_ORGANISM", definition_version=1, active=True, created_at=at,
                      kind="BOARD_OFFICE", appointing_authority="LO_COORDINATOR_BODY", nomination_basis="SILENT",
                      nomination_policy="C", participation_required=False, enterer_id=route.founder.pk,
                      enterer_access_epoch=route.founder.access_epoch, authority_decision_reference=decision.decision_reference,
                      occurred_at=at, effective_at=at, received_at=at)
        before = self._state()
        for changes, reason in (
            ({"kind": "UNKNOWN"}, "value outside its domain"),
            ({"appointing_authority": "NAMED_BODY"}, "appointing body reference required iff NAMED_BODY"),
            ({"nomination_policy": "UNSUPPORTED"}, "UNSUPPORTED only with a constitutional basis"),
            ({"nomination_basis": "CUSTOM"}, "nomination basis CUSTOM iff kind CUSTOM"),
            ({"code": "COORDINATOR"}, "kind COORDINATOR iff code COORDINATOR"),
            ({"occurred_at": at + dt.timedelta(seconds=1)}, "time order"),
            ({"active": False}, "a new version is recorded active"),
            ({"retirement_reason": "test-only"}, "retirement columns are set only by retirement"),
            ({"enterer_access_epoch": None}, "content or attribution incomplete"),
            ({"authority_decision_reference": "s015d1:" + "0" * 64}, "governed decision does not match definition"),
        ):
            with self.subTest(reason=reason):
                def raw_insert():
                    values = {**common, **changes}
                    columns = ", ".join(values)  # fixed test-owned column names only
                    placeholders = ", ".join("%s" for _ in values)
                    with transaction.atomic(), connection.cursor() as cursor:
                        cursor.execute(f"INSERT INTO core_organismroledefinition ({columns}) VALUES ({placeholders})",
                                       tuple(values.values()))
                self._assert_refused(before, reason, raw_insert)

    def test_sr_c1_27_intake_alone_prevents_populated_reverse(self):
        """The new entry independently blocks reversal even when all definitions are pre-SR."""
        with transaction.atomic(), connection.cursor() as cursor:
            ids = found_nonroot(cursor, "src1-27-legacy")
        # A labelled database fixture, not a claim that C1 admits this public command.
        from core.models import Identity
        actor = Identity.objects.get(pk=ids["identity"])
        # received_at is NOT NULL at the table but assigned by its BEFORE INSERT
        # trigger; the model's full_clean would reject None before reaching it.
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO core_commandintake (actor_id, actor_identity_id, actor_access_epoch, entry, action, "
                "idempotency_key, request_reference, request_fingerprint, fingerprint_schema, target_organism_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NULL)",
                (actor.pk, actor.identity_id, actor.access_epoch, RECORD_ROLE_DEFINITION, RECORD_ROLE_DEFINITION,
                 "test-only:src1-27", "test-only:src1-27", hashlib.sha256(b"test-only:src1-27").hexdigest(),
                 "test-only-sr-c1"),
            )
        before = self._schema_facts()
        intakes = list(CommandIntake.objects.order_by("pk").values_list())
        with self.assertRaisesRegex(RuntimeError, "1 intake row") as refused:
            MigrationExecutor(connection).migrate([("core", HEAD_0050)])
        self.assertIn("0 definition row(s) carrying SR attribution", str(refused.exception))
        self.assertIn("0 provision row(s)", str(refused.exception))
        self.assertEqual(self._schema_facts(), before)
        self.assertEqual(list(CommandIntake.objects.order_by("pk").values_list()), intakes)

    def test_sr_c1_28_provisions_are_named_and_preserved_on_reverse_refusal(self):
        """A provision is never erased to permit reversal; its attributed parent also blocks it."""
        route, _ = self._founded("src1-28")
        self._write(route, label="src1-28-office")
        before = self._state()
        schema_before = self._schema_facts()
        with self.assertRaisesRegex(RuntimeError, "reverse refused") as refused:
            MigrationExecutor(connection).migrate([("core", HEAD_0050)])
        self.assertIn("2 definition row(s) carrying SR attribution", str(refused.exception))
        self.assertIn("1 provision row(s)", str(refused.exception))
        self.assertEqual(self._state(), before)
        self.assertEqual(self._schema_facts(), schema_before)
