"""PKT-SE-K2 distinct-actor beta opt-in and collision witnesses."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from django.db import IntegrityError, close_old_connections, connections
from django.test import TransactionTestCase

from core.models import (
    AuthorityDecision,
    GovernedRule,
    LivingOrganism,
    LivingOrganismEvent,
    LOApplicationSettingsVersion,
    OrganismCommandReceipt,
    OrganismEntitlement,
    OrganismEntitlementEvent,
    OrganismEntitlementPolicyText,
)
from src.intevia.services.organism_application_authority import (
    ProductionApplicationAuthorityProvider,
)
from src.intevia.services.organism_authority import (
    S015Authority,
    S015AuthorityRefusalCode,
)
from src.intevia.services.organism_entitlement_service import (
    EntitlementElectionCommand,
    OrganismEntitlementService,
    S015EntitlementRefused,
)
from tests.s015_0021_support import database_now
from tests.sb2_route_support import birth, prepare_route
from tests.se_route_support import (
    TEST_POLICY_SHA256,
    TEST_POLICY_VERSION,
    add_test_coordinator,
    ensure_test_policy_text,
)


def _free_route():
    """Create a post-Birthing FREE route ready for beta opt-in."""
    route = prepare_route(tag=f"sek2-{uuid4().hex}")
    ensure_test_policy_text()
    birth(route, suffix=f"sek2-free-{route.tag}")
    return route


def _command(route, actor, *, tag, occurred_at=None):
    """Build a uniquely identified opt-in command for the selected actor."""
    return EntitlementElectionCommand(
        credential=actor.credential,
        living_organism_uuid=route.organism.organism_id,
        request_reference=f"sek2:{tag}:request",
        idempotency_key=f"sek2:{tag}:key",
        occurred_at=database_now() if occurred_at is None else occurred_at,
        policy_version=TEST_POLICY_VERSION,
        policy_text_sha256=TEST_POLICY_SHA256,
    )


def _service(*, clock=database_now):
    """Create the production-authority service with the supplied test clock."""
    return OrganismEntitlementService(
        authority=S015Authority(provider=ProductionApplicationAuthorityProvider()),
        clock=clock,
    )


def _table_state(model):
    """Return all concrete table rows for transaction rollback comparisons."""
    field_names = tuple(field.attname for field in model._meta.concrete_fields)
    return tuple(model.objects.order_by("pk").values_list(*field_names))


def _state():
    """Capture every authoritative record the beta command can change."""
    models = (
        OrganismEntitlement,
        OrganismEntitlementEvent,
        OrganismEntitlementPolicyText,
        LOApplicationSettingsVersion,
        LivingOrganismEvent,
        OrganismCommandReceipt,
        AuthorityDecision,
        LivingOrganism,
    )
    return {model.__name__: _table_state(model) for model in models}


def _seed_beta_row(route, *, effective_at):
    """Insert the pre-cleared event-less beta row used by collision witnesses."""
    rule = GovernedRule.objects.get(
        scope_kind="PLATFORM",
        scope_organism__isnull=True,
        scope_circle__isnull=True,
        code="BETA_COMPLIMENTARY_ACCESS",
    )
    beta_row = OrganismEntitlement.objects.create(
        living_organism=route.organism,
        source="COMPLIMENTARY",
        basis="BETA_OPT_IN",
        policy_reference=f"policy-text:v{TEST_POLICY_VERSION}",
        policy_version=TEST_POLICY_VERSION,
        capabilities=rule.body["capabilities"],
        limits=rule.body["limits"],
        effective_at=effective_at,
        effective_until=None,
        ending_condition_reference=(
            f"paid-availability-transition:{route.organism.organism_id}"
        ),
    )
    beta_row.refresh_from_db(fields=("recorded_at",))
    return beta_row


def _seed_extension(route, *, effective_at, effective_until=None):
    """Insert a declared extension row for receipt-time visibility witnesses."""
    rule = GovernedRule.objects.get(
        scope_kind="PLATFORM",
        scope_organism__isnull=True,
        scope_circle__isnull=True,
        code="BETA_COMPLIMENTARY_ACCESS",
    )
    extension = OrganismEntitlement.objects.create(
        living_organism=route.organism,
        source="COMPLIMENTARY",
        basis="AUTHORISED_EXTENSION",
        policy_reference=f"policy-text:v{TEST_POLICY_VERSION}",
        policy_version=TEST_POLICY_VERSION,
        capabilities=rule.body["capabilities"],
        limits=rule.body["limits"],
        effective_at=effective_at,
        effective_until=effective_until,
        ending_condition_reference=(
            f"paid-availability-transition:{route.organism.organism_id}"
        ),
    )
    extension.refresh_from_db(fields=("recorded_at",))
    return extension


class BetaOptInRaceTests(TransactionTestCase):
    reset_sequences = True

    def _assert_ordered_collision(self, *, winner_is_coordinator):
        """Commit the winner before trying the loser with an earlier receipt time."""
        route = _free_route()
        coordinator = add_test_coordinator(
            route,
            tag=f"race-{uuid4().hex}",
        )
        winner_actor, loser_actor = (
            (coordinator, route.founder)
            if winner_is_coordinator
            else (route.founder, coordinator)
        )
        winner_command = _command(
            route,
            winner_actor,
            tag=f"winner-{uuid4().hex}",
        )
        loser_command = _command(
            route,
            loser_actor,
            tag=f"loser-{uuid4().hex}",
        )
        loser_received_at = database_now()
        before = _state()

        winner_result = _service().activate_beta(winner_command)
        self.assertIsNotNone(winner_result.entitlement_uuid)
        winner_grant = OrganismEntitlement.objects.get(
            entitlement_uuid=winner_result.entitlement_uuid,
        )
        self.assertLess(loser_received_at, winner_grant.recorded_at)
        after_winner = _state()

        with self.assertRaises(S015EntitlementRefused) as refusal:
            _service(clock=lambda: loser_received_at).activate_beta(loser_command)
        self.assertIs(
            refusal.exception.refusal_code,
            S015AuthorityRefusalCode.SE_ALREADY_BETA,
        )
        self.assertEqual(_state(), after_winner)
        self._assert_single_winner_state(
            route,
            winner_result,
            loser_command,
            before,
        )

    def _assert_single_winner_state(self, route, winner_result, loser_command, before):
        """Assert one grant and that the refused command left no records."""
        self.assertIsNotNone(winner_result.entitlement_uuid)
        self.assertEqual(
            OrganismEntitlement.objects.filter(
                living_organism=route.organism,
                basis="BETA_OPT_IN",
            ).count(),
            1,
        )
        self.assertEqual(
            OrganismEntitlementEvent.objects.filter(
                entitlement__living_organism=route.organism,
                event_type="GRANTED",
            ).count(),
            1,
        )
        self.assertFalse(
            AuthorityDecision.objects.filter(
                request_reference=loser_command.request_reference,
            ).exists()
        )
        self.assertFalse(
            OrganismCommandReceipt.objects.filter(
                request_reference=loser_command.request_reference,
            ).exists()
        )

        after = _state()
        expected_additions = {
            "OrganismEntitlement": 1,
            "OrganismEntitlementEvent": 1,
            "LOApplicationSettingsVersion": 1,
            "LivingOrganismEvent": 1,
            "OrganismCommandReceipt": 1,
            "AuthorityDecision": 1,
        }
        for model_name, before_rows in before.items():
            self.assertEqual(
                len(after[model_name]) - len(before_rows),
                expected_additions.get(model_name, 0),
                model_name,
            )
            if model_name != "LivingOrganism":
                self.assertTrue(
                    all(row in after[model_name] for row in before_rows),
                    model_name,
                )

    def test_w_k2_c1_two_distinct_actors_racing_create_one_beta_grant(self):
        """W-K2.C1: founder commits first; coordinator's earlier receipt collides."""
        self._assert_ordered_collision(winner_is_coordinator=False)

    def test_w_k2_c2_loser_reads_before_winner_but_still_collides(self):
        """W-K2.C2: simultaneous distinct actors produce one grant and one refusal."""
        route = _free_route()
        coordinator = add_test_coordinator(
            route,
            tag=f"concurrent-{uuid4().hex}",
        )
        founder_command = _command(
            route,
            route.founder,
            tag=f"founder-{uuid4().hex}",
        )
        coordinator_command = _command(
            route,
            coordinator,
            tag=f"coordinator-{uuid4().hex}",
        )
        before = _state()
        start_gate = Barrier(3, timeout=30)

        def opt_in(command):
            close_old_connections()
            try:
                start_gate.wait()
                try:
                    return _service().activate_beta(command)
                except S015EntitlementRefused as refusal:
                    return refusal
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            founder_future = executor.submit(opt_in, founder_command)
            coordinator_future = executor.submit(opt_in, coordinator_command)
            start_gate.wait()
            founder_result = founder_future.result(timeout=60)
            coordinator_result = coordinator_future.result(timeout=60)

        outcomes = (
            (founder_command, founder_result),
            (coordinator_command, coordinator_result),
        )
        winners = [
            (command, result)
            for command, result in outcomes
            if not isinstance(result, S015EntitlementRefused)
        ]
        refusals = [
            (command, result)
            for command, result in outcomes
            if isinstance(result, S015EntitlementRefused)
        ]
        self.assertEqual(len(winners), 1)
        self.assertEqual(len(refusals), 1)
        winner_result = winners[0][1]
        loser_command, loser_result = refusals[0]
        self.assertIs(
            loser_result.refusal_code,
            S015AuthorityRefusalCode.SE_ALREADY_BETA,
        )
        self._assert_single_winner_state(
            route,
            winner_result,
            loser_command,
            before,
        )

    def test_w_k2_c3_roles_swapped_coordinator_wins(self):
        """W-K2.C3: coordinator commits first; founder's earlier receipt collides."""
        self._assert_ordered_collision(winner_is_coordinator=True)

    def test_w_k2_c3_prior_beta_row_collides_outside_effective_and_recorded_view(self):
        """W-K2.C3: collide on a beta row even when its recorded/effective times are later."""
        route = _free_route()
        received_at = database_now()
        existing = _seed_beta_row(
            route,
            effective_at=received_at + timedelta(days=1),
        )
        self.assertGreater(existing.recorded_at, received_at)
        before = _state()
        service = _service(clock=lambda: received_at)
        command = _command(
            route,
            route.founder,
            tag=f"prior-row-{uuid4().hex}",
            occurred_at=received_at,
        )

        with patch.object(
            service,
            "_compose_beta_grant",
            side_effect=AssertionError("pre-existing beta rows must collide first"),
        ) as compose:
            with self.assertRaises(S015EntitlementRefused) as refusal:
                service.activate_beta(command)

        self.assertIs(
            refusal.exception.refusal_code,
            S015AuthorityRefusalCode.SE_ALREADY_BETA,
        )
        compose.assert_not_called()
        self.assertEqual(_state(), before)

    def test_w_k2_c3_extension_effective_at_receipt_recorded_later_collides(self):
        """W-K2.C3: a visible extension collides even when recorded after receipt."""
        route = _free_route()
        received_at = database_now() - timedelta(seconds=5)
        extension = _seed_extension(
            route,
            effective_at=received_at,
        )
        self.assertGreater(extension.recorded_at, received_at)
        self.assertEqual(extension.effective_at, received_at)
        before = _state()
        with self.assertRaises(S015EntitlementRefused) as refusal:
            _service(clock=lambda: received_at).activate_beta(
                _command(
                    route,
                    route.founder,
                    tag=f"late-recorded-extension-{uuid4().hex}",
                    occurred_at=received_at,
                )
            )
        self.assertIs(
            refusal.exception.refusal_code,
            S015AuthorityRefusalCode.SE_ALREADY_BETA,
        )
        self.assertEqual(_state(), before)

    def test_w_k2_c3_future_extension_does_not_collide(self):
        """W-K2.C3: an extension effective after receipt does not block beta."""
        route = _free_route()
        received_at = database_now()
        future_extension = _seed_extension(
            route,
            effective_at=received_at + timedelta(days=1),
        )
        self.assertGreater(future_extension.effective_at, received_at)
        result = _service(clock=lambda: received_at).activate_beta(
            _command(
                route,
                route.founder,
                tag=f"future-extension-{uuid4().hex}",
                occurred_at=received_at,
            )
        )
        self.assertIsNotNone(result.entitlement_uuid)
        self.assertEqual(
            OrganismEntitlement.objects.filter(
                living_organism=route.organism,
                basis="BETA_OPT_IN",
            ).count(),
            1,
        )

    def test_w_k2_c3_expired_extension_does_not_collide(self):
        """W-K2.C3: an extension ended at receipt does not block beta."""
        route = _free_route()
        received_at = database_now()
        expired_extension = _seed_extension(
            route,
            effective_at=received_at - timedelta(days=1),
            effective_until=received_at,
        )
        self.assertLess(expired_extension.effective_at, received_at)
        self.assertEqual(expired_extension.effective_until, received_at)
        result = _service(clock=lambda: received_at).activate_beta(
            _command(
                route,
                route.founder,
                tag=f"expired-extension-{uuid4().hex}",
                occurred_at=received_at,
            )
        )
        self.assertIsNotNone(result.entitlement_uuid)
        self.assertEqual(
            OrganismEntitlement.objects.filter(
                living_organism=route.organism,
                basis="BETA_OPT_IN",
            ).count(),
            1,
        )

    def test_w_k2_c4_unique_index_collision_translates_to_already_beta(self):
        """W-K2.C4: translate the named unique-index race and roll back all writes."""
        route = _free_route()
        received_at = database_now()
        _seed_beta_row(
            route,
            effective_at=received_at + timedelta(days=1),
        )
        before = _state()
        service = _service(clock=lambda: received_at)
        command = _command(
            route,
            route.founder,
            tag=f"unique-collision-{uuid4().hex}",
            occurred_at=received_at,
        )

        with patch.object(service, "_beta_grant_collision", return_value=None):
            with self.assertRaises(S015EntitlementRefused) as refusal:
                service.activate_beta(command)

        self.assertIs(
            refusal.exception.refusal_code,
            S015AuthorityRefusalCode.SE_ALREADY_BETA,
        )
        self.assertEqual(_state(), before)

    def test_w_k2_c5_unrelated_integrity_error_is_not_translated(self):
        """W-K2.C5: propagate a different insert integrity fault unchanged."""
        route = _free_route()
        before = _state()
        original_using = OrganismEntitlement.objects.using

        def failing_using(alias):
            queryset = original_using(alias)

            class _FailingQuery:
                def __getattr__(self, name):
                    return getattr(queryset, name)

                def create(self, **kwargs):
                    return queryset.create(
                        **{
                            **kwargs,
                            "ending_condition_reference": None,
                        }
                    )

            return _FailingQuery()


        service = _service()
        command = _command(
            route,
            route.founder,
            tag=f"unrelated-error-{uuid4().hex}",
        )
        with patch.object(service, "_beta_grant_collision", return_value=None):
            with patch.object(
                OrganismEntitlement.objects,
                "using",
                new=failing_using,
            ):
                with self.assertRaises(IntegrityError) as propagated:
                    service.activate_beta(command)

        driver_error = propagated.exception.__cause__
        self.assertEqual(getattr(driver_error, "sqlstate", None), "23000")
        self.assertIn(
            "SE_ENTITLEMENT_BETA_SHAPE_INVALID",
            str(propagated.exception),
        )
        self.assertEqual(_state(), before)
