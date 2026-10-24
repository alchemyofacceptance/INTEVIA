"""PKT-CLK-3 CLK3-1 founding and governance intake witnesses."""

from dataclasses import replace
from datetime import timedelta
import hashlib
import traceback
from unittest.mock import patch
from uuid import uuid4, uuid5

from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models import QuerySet
from django.test import TransactionTestCase, override_settings

from core.models import (
    AuthorityBasis,
    AuthorityDecision,
    Circle,
    CircleStateEvent,
    CommandIntake,
    ContextualRoleAssignment,
    ContextualRoleAssignmentStateEvent,
    CoverageAssessment,
    EssentialCoverageCase,
    GovernedRule,
    LivingOrganism,
    LivingOrganismEvent,
    LOFoundingContentVersion,
    OrganismCommandReceipt,
    OrganismMembership,
    OrganismMembershipTransition,
    OrganismRoleDefinition,
    PlatformTermsAcceptance,
)
from core.organism_contract import OrganismAction
from src.intevia.services import organism_formation_service as formation_module
from src.intevia.services import organism_governance_service as governance_module
from src.intevia.services.command_intake import (
    ActorRef,
    BoundRequest,
    IntakeEntry,
    run_activation_step,
)
from src.intevia.services.organism_authority import (
    S015AuthorityDenied,
    S015AuthorityRefusalCode,
    canonical_json_bytes,
    canonical_timestamp,
)
from src.intevia.services.organism_formation_service import (
    FoundLivingOrganismCommand,
    S015FormationTechnicalFailure,
)
from src.intevia.services.organism_governance_service import (
    AdoptGoverningInstrumentCommand,
    CreateGovernanceCircleCommand,
    OrganismGovernanceService,
)
from src.intevia.services.organism_command_support import (
    S015CommandAtomicityRequired,
    S015CommandReplayMismatch,
)
from tests.s015_0021_support import database_now, fresh
from tests.sb2_route_support import birth, prepare_route, route_test_now
from tests.sbg_route_support import governance_arrangement_body


def _public_table_counts():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT c.relname FROM pg_catalog.pg_class c "
            "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') "
            "ORDER BY c.relname"
        )
        tables = [row[0] for row in cursor.fetchall()]
        counts = {}
        for table in tables:
            cursor.execute(
                f"SELECT count(*) FROM public.{connection.ops.quote_name(table)}"
            )
            counts[table] = cursor.fetchone()[0]
    return counts


def _changed_tables(before, after):
    return {
        table: after[table] - count
        for table, count in before.items()
        if after[table] != count
    }


class ClockSingleIntakeTests(TransactionTestCase):
    """Verify admission wiring and bound-time replay for CLK3-1 singles."""

    @staticmethod
    def _prepare_route(tag):
        route = prepare_route(tag=tag, governance_enabled=False)
        birth(route, suffix=f"clk3:{tag}")
        return route

    @staticmethod
    def _founding_command(route, tag, *, name=None, purpose=None, effective_at=None):
        return FoundLivingOrganismCommand(
            credential=route.founder.credential,
            name=f"CLK3 founding {tag}" if name is None else name,
            purpose=purpose or f"A commissioned founding purpose for {tag}.",
            vision=f"A vision for {tag}.",
            mission=f"A mission for {tag}.",
            values=f"Values for {tag}.",
            request_reference=f"clk3:found:{tag}",
            idempotency_key=f"clk3:found:key:{tag}",
            accepted_terms_version_uuid=route.terms.terms_version_uuid,
            effective_at=effective_at,
        )

    @staticmethod
    def _adoption_command(route, tag, *, body=None, effective_at=None):
        # With effective_at unstated, the act takes effect at the intake's received_at,
        # which is later than any reading taken here, and an arrangement may not take
        # effect before it is adopted (_validate_adoption); so it starts an hour ahead.
        effective_from = route_test_now(route) + timedelta(hours=1)
        return AdoptGoverningInstrumentCommand(
            credential=route.founder.credential,
            living_organism_uuid=route.organism.organism_id,
            rule_uuid=uuid4(),
            version=1,
            body=(
                governance_arrangement_body(effective_from=effective_from)
                if body is None
                else body
            ),
            effective_from=effective_from,
            request_reference=f"clk3:adopt:{tag}",
            idempotency_key=f"clk3:adopt:key:{tag}",
            effective_at=effective_at,
        )

    @staticmethod
    def _circle_command(route, tag, *, effective_at=None):
        return CreateGovernanceCircleCommand(
            credential=route.founder.credential,
            living_organism_uuid=route.organism.organism_id,
            request_reference=f"clk3:circle:{tag}",
            idempotency_key=f"clk3:circle:key:{tag}",
            effective_at=effective_at,
        )

    @staticmethod
    def _intake(actor, action, command):
        return CommandIntake.objects.get(
            actor=actor,
            action=action.value,
            idempotency_key=command.idempotency_key,
        )

    @staticmethod
    def _receipt(actor, action, command):
        return OrganismCommandReceipt.objects.get(
            actor=actor,
            action=action.value,
            idempotency_key=command.idempotency_key,
        )

    @staticmethod
    def _later_clock_than(received_at):
        for _ in range(3):
            now = database_now()
            if now > received_at:
                return now
        raise AssertionError(
            "database clock did not advance; the witness cannot discriminate"
        )

    @staticmethod
    def _assert_act_fingerprint(test, receipt, command, action, received_at, payload):
        expected = hashlib.sha256(
            canonical_json_bytes(
                {
                    "action": action.value,
                    "effective_at": received_at,
                    "idempotency_key": command.idempotency_key,
                    "organism_uuid": command.living_organism_uuid,
                    "payload": payload,
                    "request_reference": command.request_reference,
                    "schema": "sbg-governance-act-v1",
                }
            )
        ).hexdigest()
        test.assertEqual(receipt.payload_fingerprint, expected)

    @override_settings(INTEVIA_FIXTURE_SUBSTITUTES_PERMITTED=True)
    def test_iw00_founding_and_governance_acts_refuse_atomic_and_manual_modes_without_sql(self):
        route = self._prepare_route(fresh())
        governance = OrganismGovernanceService(clock=database_now)
        adoption = self._adoption_command(route, fresh())
        circle = self._circle_command(route, fresh())
        calls = (
            (
                route.formation_service.found_living_organism,
                self._founding_command(route, fresh()),
                S015FormationTechnicalFailure,
            ),
            (
                governance.adopt_governing_instrument,
                adoption,
                S015CommandAtomicityRequired,
            ),
            (
                governance.create_governance_circle,
                circle,
                S015CommandAtomicityRequired,
            ),
        )
        for call, command, atomic_error in calls:
            with self.subTest(method=call.__name__, mode="atomic"):
                with transaction.atomic():
                    with self.assertNumQueries(0):
                        with self.assertRaises(atomic_error):
                            call(command)

        # Manual mode is refused by the shared admission-state check, so every method,
        # founding included, raises S015CommandAtomicityRequired (D §5.1: the family's
        # own exception is kept only for the atomic-block guard).
        for call, command, _atomic_error in calls:
            with self.subTest(method=call.__name__, mode="manual"):
                connection.set_autocommit(False)
                try:
                    with self.assertNumQueries(0):
                        with self.assertRaises(S015CommandAtomicityRequired):
                            call(command)
                finally:
                    connection.rollback()
                    connection.set_autocommit(True)

    @override_settings(INTEVIA_FIXTURE_SUBSTITUTES_PERMITTED=True)
    def test_iw00c_founding_and_governance_acts_admit_then_run_their_executors(self):
        route = self._prepare_route(fresh())
        governance = OrganismGovernanceService(clock=database_now)
        calls = (
            (
                route.formation_service.found_living_organism,
                self._founding_command(route, fresh()),
                OrganismAction.FOUND_LIVING_ORGANISM,
                IntakeEntry.FOUND_LIVING_ORGANISM,
            ),
            (
                governance.adopt_governing_instrument,
                self._adoption_command(route, fresh()),
                OrganismAction.ADOPT_GOVERNING_INSTRUMENT,
                IntakeEntry.ADOPT_GOVERNING_INSTRUMENT,
            ),
            (
                governance.create_governance_circle,
                self._circle_command(route, fresh()),
                OrganismAction.CREATE_DORMANT_CIRCLE,
                IntakeEntry.CREATE_GOVERNANCE_CIRCLE,
            ),
        )
        for call, command, action, entry in calls:
            with self.subTest(action=action):
                before = CommandIntake.objects.count()
                result = call(command)
                self.assertIsNotNone(result)
                self.assertEqual(CommandIntake.objects.count(), before + 1)
                self.assertEqual(
                    self._intake(route.founder, action, command).entry,
                    entry.value,
                )
                self._receipt(route.founder, action, command)
                self.assertFalse(connection.in_atomic_block)

    @override_settings(INTEVIA_FIXTURE_SUBSTITUTES_PERMITTED=True)
    def test_w_clk3_10_adoption_and_circle_creation_unchanged_retries_replay_with_distinct_clock_observations(self):
        route = self._prepare_route(fresh())
        governance = OrganismGovernanceService(clock=database_now)
        adoption = self._adoption_command(route, fresh())
        adoption_result = governance.adopt_governing_instrument(adoption)
        adoption_intake = self._intake(
            route.founder,
            OrganismAction.ADOPT_GOVERNING_INSTRUMENT,
            adoption,
        )
        self.assertLessEqual(adoption_intake.received_at, adoption.effective_from)
        self._later_clock_than(adoption_intake.received_at)
        adoption_payload = {
            "rule_uuid": str(adoption.rule_uuid),
            "version": adoption.version,
            "body": adoption.body,
            "effective_from": adoption.effective_from,
        }
        adoption_viewer_times = []
        original_viewer = governance_module.ReplayViewer

        def record_adoption_viewer(*args, **kwargs):
            viewer = original_viewer(*args, **kwargs)
            adoption_viewer_times.append(viewer.now)
            return viewer

        adoption_counts = _public_table_counts()
        with patch.object(
            governance_module,
            "ReplayViewer",
            side_effect=record_adoption_viewer,
        ):
            replayed_adoption = governance.adopt_governing_instrument(adoption)
        self.assertEqual(_public_table_counts(), adoption_counts)
        self.assertEqual(replayed_adoption, adoption_result)
        self.assertGreater(adoption_viewer_times[-1], adoption_intake.received_at)
        self._assert_act_fingerprint(
            self,
            self._receipt(
                route.founder,
                OrganismAction.ADOPT_GOVERNING_INSTRUMENT,
                adoption,
            ),
            adoption,
            OrganismAction.ADOPT_GOVERNING_INSTRUMENT,
            adoption_intake.received_at,
            adoption_payload,
        )

        circle = self._circle_command(route, fresh())
        circle_result = governance.create_governance_circle(circle)
        circle_intake = self._intake(
            route.founder,
            OrganismAction.CREATE_DORMANT_CIRCLE,
            circle,
        )
        self._later_clock_than(circle_intake.received_at)
        circle_uuid = uuid5(
            route.organism.organism_id,
            f"{OrganismAction.CREATE_DORMANT_CIRCLE.value}:{circle.idempotency_key}",
        )
        circle_payload = {"kind": "GOVERNANCE", "circle_uuid": str(circle_uuid)}
        circle_viewer_times = []

        def record_circle_viewer(*args, **kwargs):
            viewer = original_viewer(*args, **kwargs)
            circle_viewer_times.append(viewer.now)
            return viewer

        circle_counts = _public_table_counts()
        with patch.object(
            governance_module,
            "ReplayViewer",
            side_effect=record_circle_viewer,
        ):
            replayed_circle = governance.create_governance_circle(circle)
        self.assertEqual(_public_table_counts(), circle_counts)
        self.assertEqual(replayed_circle, circle_result)
        self.assertGreater(circle_viewer_times[-1], circle_intake.received_at)
        self._assert_act_fingerprint(
            self,
            self._receipt(route.founder, OrganismAction.CREATE_DORMANT_CIRCLE, circle),
            circle,
            OrganismAction.CREATE_DORMANT_CIRCLE,
            circle_intake.received_at,
            circle_payload,
        )

    @override_settings(INTEVIA_FIXTURE_SUBSTITUTES_PERMITTED=True)
    def test_w_clk3_02_changed_body_on_adoption_and_changed_purpose_on_founding_are_refused_at_admission(self):
        route = self._prepare_route(fresh())
        founding = self._founding_command(route, fresh())
        route.formation_service.found_living_organism(founding)
        founding_counts = _public_table_counts()
        with self.assertRaises(S015CommandReplayMismatch):
            route.formation_service.found_living_organism(
                self._founding_command(
                    route,
                    founding.idempotency_key.rsplit(":", 1)[-1],
                    purpose="A changed founding purpose.",
                )
            )
        self.assertEqual(_public_table_counts(), founding_counts)

        governance = OrganismGovernanceService(clock=database_now)
        adoption = self._adoption_command(route, fresh())
        governance.adopt_governing_instrument(adoption)
        adoption_counts = _public_table_counts()
        changed_body = dict(adoption.body)
        changed_body["changed"] = True
        with self.assertRaises(S015CommandReplayMismatch):
            governance.adopt_governing_instrument(
                replace(adoption, body=changed_body)
            )
        self.assertEqual(_public_table_counts(), adoption_counts)

    @override_settings(INTEVIA_FIXTURE_SUBSTITUTES_PERMITTED=True)
    def test_w_clk3_11_circle_creation_rollback_then_retry_reproduces_t_on_the_circle_state_event(self):
        route = self._prepare_route(fresh())
        governance = OrganismGovernanceService(clock=database_now)
        governance.adopt_governing_instrument(self._adoption_command(route, fresh()))
        command = self._circle_command(route, fresh())

        class RaiseAfterCircleEvent:
            def __call__(self, execute, sql, params, many, context):
                result = execute(sql, params, many, context)
                if (
                    sql.lstrip().lower().startswith("insert")
                    and "core_circlestateevent" in sql.lower()
                ):
                    raise RuntimeError("injected rollback after CircleStateEvent insert")
                return result

        before = _public_table_counts()
        with connection.execute_wrapper(RaiseAfterCircleEvent()):
            with self.assertRaisesRegex(RuntimeError, "injected rollback"):
                governance.create_governance_circle(command)
        self.assertEqual(
            _changed_tables(before, _public_table_counts()),
            {"core_commandintake": 1},
        )
        intake = self._intake(
            route.founder,
            OrganismAction.CREATE_DORMANT_CIRCLE,
            command,
        )
        self.assertFalse(
            Circle.objects.filter(parent_organism=route.organism, kind="GOVERNANCE").exists()
        )
        result = governance.create_governance_circle(command)
        retried_intake = self._intake(
            route.founder,
            OrganismAction.CREATE_DORMANT_CIRCLE,
            command,
        )
        self.assertEqual(retried_intake.pk, intake.pk)
        self.assertEqual(retried_intake.received_at, intake.received_at)
        circle = Circle.objects.get(parent_organism=route.organism, kind="GOVERNANCE")
        event = CircleStateEvent.objects.get(circle=circle, sequence=1)
        receipt = self._receipt(route.founder, OrganismAction.CREATE_DORMANT_CIRCLE, command)
        decision = AuthorityDecision.objects.get(
            decision_reference=receipt.authority_decision_reference
        )
        self.assertEqual(event.occurred_at, intake.received_at)
        self.assertEqual(event.effective_at, intake.received_at)
        self.assertEqual(event.received_at, intake.received_at)
        self.assertEqual(decision.evaluated_at, intake.received_at)
        self.assertEqual(circle.current_state_effective_at, intake.received_at)
        self.assertEqual(result.record_uuid, circle.circle_uuid)

    @override_settings(INTEVIA_FIXTURE_SUBSTITUTES_PERMITTED=True)
    def test_w_clk3_11b_adoption_rollback_then_retry_reproduces_t_on_the_adoption_rows(self):
        route = self._prepare_route(fresh())
        governance = OrganismGovernanceService(clock=database_now)
        command = self._adoption_command(route, fresh())

        class RaiseAfterAdoptionEvent:
            def __call__(self, execute, sql, params, many, context):
                result = execute(sql, params, many, context)
                if (
                    sql.lstrip().lower().startswith("insert")
                    and "core_livingorganismevent" in sql.lower()
                ):
                    raise RuntimeError("injected rollback after adoption event insert")
                return result

        before = _public_table_counts()
        with connection.execute_wrapper(RaiseAfterAdoptionEvent()):
            with self.assertRaisesRegex(RuntimeError, "injected rollback"):
                governance.adopt_governing_instrument(command)
        self.assertEqual(
            _changed_tables(before, _public_table_counts()),
            {"core_commandintake": 1},
        )
        intake = self._intake(
            route.founder,
            OrganismAction.ADOPT_GOVERNING_INSTRUMENT,
            command,
        )
        self.assertLessEqual(intake.received_at, command.effective_from)
        self.assertFalse(
            GovernedRule.objects.filter(
                rule_uuid=command.rule_uuid,
                version=command.version,
            ).exists()
        )
        governance.adopt_governing_instrument(command)
        retried_intake = self._intake(
            route.founder,
            OrganismAction.ADOPT_GOVERNING_INSTRUMENT,
            command,
        )
        self.assertEqual(retried_intake.pk, intake.pk)
        self.assertEqual(retried_intake.received_at, intake.received_at)
        event = LivingOrganismEvent.objects.get(
            living_organism=route.organism,
            action=OrganismAction.ADOPT_GOVERNING_INSTRUMENT.value,
        )
        rule = GovernedRule.objects.get(
            rule_uuid=command.rule_uuid,
            version=command.version,
        )
        receipt = self._receipt(
            route.founder,
            OrganismAction.ADOPT_GOVERNING_INSTRUMENT,
            command,
        )
        decision = AuthorityDecision.objects.get(
            decision_reference=receipt.authority_decision_reference
        )
        self.assertEqual(event.effective_at, intake.received_at)
        self.assertEqual(event.received_at, intake.received_at)
        self.assertEqual(rule.decided_at, intake.received_at)
        self.assertEqual(decision.evaluated_at, intake.received_at)
        self._assert_act_fingerprint(
            self,
            receipt,
            command,
            OrganismAction.ADOPT_GOVERNING_INSTRUMENT,
            intake.received_at,
            {
                "rule_uuid": str(command.rule_uuid),
                "version": command.version,
                "body": command.body,
                "effective_from": command.effective_from,
            },
        )

    @override_settings(INTEVIA_FIXTURE_SUBSTITUTES_PERMITTED=True)
    def test_w_clk3_12_founding_unchanged_retry_replays_through_its_own_attempt_replay(self):
        route = self._prepare_route(fresh())
        command = self._founding_command(route, fresh())
        first = route.formation_service.found_living_organism(command)
        intake = self._intake(
            route.founder,
            OrganismAction.FOUND_LIVING_ORGANISM,
            command,
        )
        self._later_clock_than(intake.received_at)
        counts = _public_table_counts()
        viewer_times = []
        original_viewer = formation_module.ReplayViewer

        def record_viewer(*args, **kwargs):
            viewer = original_viewer(*args, **kwargs)
            viewer_times.append(viewer.now)
            return viewer

        with patch.object(formation_module, "ReplayViewer", side_effect=record_viewer):
            replayed = route.formation_service.found_living_organism(command)
        self.assertEqual(_public_table_counts(), counts)
        self.assertEqual(replayed, first)
        self.assertGreater(viewer_times[-1], intake.received_at)
        receipt = self._receipt(
            route.founder,
            OrganismAction.FOUND_LIVING_ORGANISM,
            command,
        )
        fingerprint = route.formation_service._command_fingerprint(
            actor=route.founder,
            name=command.name,
            purpose=command.purpose,
            vision=command.vision,
            mission=command.mission,
            values=command.values,
            accepted_terms_version_uuid=command.accepted_terms_version_uuid,
            request_reference=command.request_reference,
            idempotency_key=command.idempotency_key,
            effective_at=command.effective_at,
        )
        self.assertEqual(receipt.payload_fingerprint, fingerprint)

    @override_settings(INTEVIA_FIXTURE_SUBSTITUTES_PERMITTED=True)
    def test_w_clk3_13_founding_rollback_then_retry_reproduces_t_on_every_founding_chain_row(self):
        route = self._prepare_route(fresh())
        command = self._founding_command(route, fresh())

        class RaiseAfterFoundEvent:
            def __call__(self, execute, sql, params, many, context):
                result = execute(sql, params, many, context)
                if (
                    sql.lstrip().lower().startswith("insert")
                    and "core_livingorganismevent" in sql.lower()
                ):
                    raise RuntimeError("injected rollback after FOUND event insert")
                return result

        fingerprints = []
        original_batch = route.formation_service._write_founding_batch

        def record_batch(**kwargs):
            fingerprints.append(kwargs["fingerprint"])
            return original_batch(**kwargs)

        before = _public_table_counts()
        with patch.object(
            route.formation_service,
            "_write_founding_batch",
            side_effect=record_batch,
        ):
            with connection.execute_wrapper(RaiseAfterFoundEvent()):
                with self.assertRaisesRegex(RuntimeError, "injected rollback"):
                    route.formation_service.found_living_organism(command)
            self.assertEqual(
                _changed_tables(before, _public_table_counts()),
                {"core_commandintake": 1},
            )
            intake = self._intake(
                route.founder,
                OrganismAction.FOUND_LIVING_ORGANISM,
                command,
            )
            result = route.formation_service.found_living_organism(command)
        retried_intake = self._intake(
            route.founder,
            OrganismAction.FOUND_LIVING_ORGANISM,
            command,
        )
        self.assertEqual(retried_intake.pk, intake.pk)
        self.assertEqual(retried_intake.received_at, intake.received_at)
        organism = LivingOrganism.objects.get(organism_id=result.living_organism_uuid)
        receipt = self._receipt(
            route.founder,
            OrganismAction.FOUND_LIVING_ORGANISM,
            command,
        )
        decision = AuthorityDecision.objects.get(
            decision_reference=receipt.authority_decision_reference
        )
        founding_event = LivingOrganismEvent.objects.get(
            living_organism=organism,
            action="FOUND",
        )
        acceptance = PlatformTermsAcceptance.objects.filter(
            terms_version=route.terms,
            accepted_by_identity=route.founder,
            acceptance_kind=PlatformTermsAcceptance.Kind.FOUNDING,
        ).latest("pk")
        basis = AuthorityBasis.objects.filter(issuer=route.founder).latest("pk")
        content = LOFoundingContentVersion.objects.get(
            living_organism=organism,
            version=1,
        )
        membership = OrganismMembership.objects.get(
            living_organism=organism,
            identity=route.founder,
        )
        transitions = OrganismMembershipTransition.objects.filter(
            membership=membership
        )
        assignments = ContextualRoleAssignment.objects.filter(membership=membership)
        assignment_events = ContextualRoleAssignmentStateEvent.objects.filter(
            assignment__in=assignments
        )
        coverage = EssentialCoverageCase.objects.get(living_organism=organism)
        assessments = CoverageAssessment.objects.filter(case=coverage)

        self.assertEqual(decision.evaluated_at, intake.received_at)
        self.assertEqual(acceptance.received_at, intake.received_at)
        self.assertEqual(basis.received_at, intake.received_at)
        self.assertEqual(content.received_at, intake.received_at)
        self.assertEqual(founding_event.effective_at, intake.received_at)
        self.assertEqual(founding_event.received_at, intake.received_at)
        self.assertTrue(transitions.exists())
        self.assertTrue(assignment_events.exists())
        self.assertTrue(assessments.exists())
        self.assertTrue(all(row.received_at == intake.received_at for row in transitions))
        self.assertTrue(
            all(row.received_at == intake.received_at for row in assignment_events)
        )
        self.assertTrue(all(row.received_at == intake.received_at for row in assessments))
        self.assertEqual(organism.current_state_effective_at, intake.received_at)
        self.assertEqual(membership.current_state_effective_at, intake.received_at)
        self.assertTrue(assignments.exists())
        self.assertTrue(
            all(
                assignment.current_state_effective_at == intake.received_at
                for assignment in assignments
            )
        )
        self.assertEqual(len(fingerprints), 2)
        self.assertEqual(fingerprints[0], fingerprints[1])
        self.assertEqual(receipt.payload_fingerprint, fingerprints[1])
        self.assertTrue(OrganismRoleDefinition.objects.filter(living_organism=organism).exists())

        # F7.1's boundary, as an executor comparison witness: a stated effective time
        # later than the bound receipt time T, and not later than any clock reading the
        # executor could take, is refused. An implementation that read received_at from
        # its clock at execution would accept it. The bound request is test-only, so no
        # intake row is written and none is claimed.
        bound_received_at = database_now()
        stated_effective_at = self._later_clock_than(bound_received_at)
        boundary_command = self._founding_command(
            route,
            fresh(),
            effective_at=stated_effective_at,
        )
        actor = ActorRef(
            pk=route.founder.pk,
            identity_id=route.founder.identity_id,
            access_epoch=route.founder.access_epoch,
        )
        before = _public_table_counts()
        with override_settings(INTEVIA_TEST_CONTEXT=True):
            bound = BoundRequest.for_test(
                IntakeEntry.FOUND_LIVING_ORGANISM,
                boundary_command,
                received_at=bound_received_at,
                actor=actor,
            )
            with self.assertRaisesRegex(
                ValueError,
                r"^effective_at must not be later than received_at$",
            ):
                route.formation_service._execute_found_living_organism(
                    boundary_command,
                    bound,
                )
        self.assertEqual(_public_table_counts(), before)

    @override_settings(INTEVIA_FIXTURE_SUBSTITUTES_PERMITTED=True)
    def test_iw31_founding_denial_exit_keeps_only_the_intake(self):
        route = self._prepare_route(fresh())
        tag = fresh()
        command = self._founding_command(route, tag, name=route.organism.slug)
        original_exists = QuerySet.exists
        missed_slug_queries = []

        def miss_only_occupied_slug(queryset):
            # Both pre-insert existence checks miss: the service's own pre-check, then the
            # model's validate_unique() inside save() (Django's _perform_unique_checks).
            # Each is one exact slug lookup on LivingOrganism, on the default database, for
            # the occupied slug; each miss records whether it came from validate_unique.
            # Every other exists() call is forwarded unchanged.
            children = queryset.query.where.children
            lookup = children[0] if len(children) == 1 else None
            if (
                queryset.model is LivingOrganism
                and queryset.db == "default"
                and lookup is not None
                and getattr(lookup, "lookup_name", None) == "exact"
                and getattr(getattr(getattr(lookup, "lhs", None), "target", None), "name", None)
                == "slug"
                and lookup.rhs == route.organism.slug
            ):
                missed_slug_queries.append(
                    any(
                        frame.name == "_perform_unique_checks"
                        and frame.filename.replace("\\", "/").endswith("django/db/models/base.py")
                        for frame in traceback.extract_stack()
                    )
                )
                return False
            return original_exists(queryset)

        before = _public_table_counts()
        with patch.object(QuerySet, "exists", new=miss_only_occupied_slug):
            with self.assertRaises(S015AuthorityDenied) as refused:
                route.formation_service.found_living_organism(command)
        # The service's pre-check misses first, then the model's validate_unique.
        self.assertEqual(missed_slug_queries, [False, True])
        self.assertIs(
            refused.exception.refusal_code,
            S015AuthorityRefusalCode.LO_NAME_UNAVAILABLE,
        )
        self.assertEqual(
            str(refused.exception),
            "The LO was not founded: that name is already taken by another LO. Choose a different name. "
            "No governed record was written; INTEVIA kept a record that this request was received at "
            f"{canonical_timestamp(CommandIntake.objects.get(actor_id=route.founder.pk, action=IntakeEntry.FOUND_LIVING_ORGANISM.value, idempotency_key=f'clk3:found:key:{tag}').received_at)}; "
            "sent again unchanged, it is answered as of that time.",
        )
        self.assertIsInstance(refused.exception.__cause__, IntegrityError)
        self.assertNotIsInstance(refused.exception, DatabaseError)
        database_cause = refused.exception.__cause__.__cause__
        self.assertEqual(
            database_cause.diag.constraint_name,
            formation_module._NAME_CONSTRAINT,
        )
        after = _public_table_counts()
        self.assertEqual(
            _changed_tables(before, after),
            {"core_commandintake": 1},
        )

    @override_settings(INTEVIA_FIXTURE_SUBSTITUTES_PERMITTED=True)
    def test_step_a_activation_has_no_unaccounted_record_and_reports_an_injected_one(self):
        route = self._prepare_route(fresh())
        entries = (
            IntakeEntry.FOUND_LIVING_ORGANISM,
            IntakeEntry.ADOPT_GOVERNING_INSTRUMENT,
            IntakeEntry.CREATE_GOVERNANCE_CIRCLE,
        )
        self.assertEqual(run_activation_step("default", entries), [])

        from tests.test_h100_intake_activation import H100IntakeActivationTests

        actor = ActorRef(
            pk=route.founder.pk,
            identity_id=route.founder.identity_id,
            access_epoch=route.founder.access_epoch,
        )
        key = f"clk3:unaccounted:{fresh()}"
        helper = H100IntakeActivationTests(methodName="runTest")
        # D 6.3: the injected unaccounted row is a receipt inserted under the namespace
        # with no binding (raw SQL; owner power D 11.5 (2)).
        helper._receipt(actor, OrganismAction.FOUND_LIVING_ORGANISM.value, key)
        unaccounted = run_activation_step("default", entries)
        self.assertEqual(
            {
                (row.kind, row.action, row.idempotency_key, row.actor_id)
                for row in unaccounted
            },
            {
                (
                    "receipt",
                    OrganismAction.FOUND_LIVING_ORGANISM.value,
                    key,
                    route.founder.pk,
                )
            },
        )
