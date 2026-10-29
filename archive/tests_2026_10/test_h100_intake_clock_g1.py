"""CLK3-2 G1 durable-intake and binding-time witnesses."""

from dataclasses import replace
from unittest.mock import patch
import hashlib
import json
from uuid import UUID, uuid4

from django.db import connection, transaction
from django.test import TransactionTestCase
from unittest import skipUnless
from django.test.utils import CaptureQueriesContext

from core.models import (
    AuthorityDecision,
    CommandIntake,
    LivingOrganismEvent,
    LOApplicationSettingsVersion,
    LOBirthingSnapshot,
    OrganismCommandReceipt,
    OrganismEntitlement,
    OrganismEntitlementEvent,
)
from core.organism_contract import OrganismAction
from src.intevia.services import organism_entitlement_service as entitlement_module
from src.intevia.services import organism_formation_service as formation_module
from src.intevia.services.command_intake import (
    ACTIVE_ENTRIES,
    IntakeEntry,
    run_activation_step,
)
from src.intevia.services.organism_command_support import S015CommandAtomicityRequired
from src.intevia.services.organism_authority import (
    S015Authority,
    S015AuthorityDenied,
    S015AuthorityRefusalCode,
    canonical_json_bytes,
    canonical_timestamp,
)
from src.intevia.services.organism_command_support import (
    S015CommandReplayMismatch,
)
from src.intevia.services.organism_entitlement_service import (
    EntitlementElectionCommand,
    OrganismEntitlementService,
    S015EntitlementRefused,
    S015EntitlementTechnicalFailure,
)
from src.intevia.services.organism_formation_service import (
    BirthingCommand,
    PhaseAdvanceCommand,
    S015BirthingTechnicalFailure,
    S015PhaseAdvanceTechnicalFailure,
)
from tests.s015_0021_support import database_now, fresh
from tests.sb2_route_support import prepare_route, route_test_now
from tests.se_route_support import (
    TEST_POLICY_SHA256,
    TEST_POLICY_VERSION,
    add_test_coordinator,
    disclose_authority_refusal_code,
    ensure_test_policy_text,
)

# The fingerprint domains, as the services declare them (recomputed here, D section 7.1, section 7.1a (ii)).
_PHASE_DOMAIN = b"INTEVIA:S015:TRANSITION_LIVING_ORGANISM/PHASE_ADVANCE:COMMAND:v1\x00"
_BIRTHING_DOMAIN = b"INTEVIA:S015:TRANSITION_LIVING_ORGANISM/BIRTHING:COMMAND:v2\x00"
_ELECTION_DOMAIN = b"INTEVIA:S015:EFFECT_ENTITLEMENT_ELECTION:COMMAND:v1\x00"
_TRANSITION = OrganismAction.TRANSITION_LIVING_ORGANISM
_ELECTION = OrganismAction.EFFECT_ENTITLEMENT_ELECTION


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


def _fingerprint(*, domain, schema, action, actor, request_reference, idempotency_key,
                 occurred_at, target, command):
    """A command payload fingerprint, recomputed from its documented projection."""
    payload = {
        "action": action.value,
        "actor_access_epoch": actor.access_epoch,
        "actor_identity_id": str(actor.identity_id),
        "command": command,
        "idempotency_key": idempotency_key,
        "occurred_at": canonical_timestamp(occurred_at),
        "request_reference": request_reference,
        "schema": schema,
        "target": target,
    }
    return hashlib.sha256(domain + canonical_json_bytes(payload)).hexdigest()


class _ElectionRefusingProvider:
    """Delegates to the production provider; refuses every entitlement election.

    For EFFECT_ENTITLEMENT_ELECTION it returns the production provider's own refusal
    carrier for SE_OPT_IN_NEEDS_CQ, built by its refusal builder, so the decision row
    carries the real structured s015a1 reference; every other action is the production
    provider's own judgement. S015Authority.qualify and its recording are real.
    """

    def __init__(self, production):
        self._production = production
        self.provider_identity = production.provider_identity
        self.provider_version = production.provider_version

    def evaluate_organism_authority(self, request):
        if request.action is _ELECTION:
            return self._production._refuse(
                request,
                S015AuthorityRefusalCode.SE_OPT_IN_NEEDS_CQ,
            )
        return self._production.evaluate_organism_authority(request)


@skipUnless(connection.vendor == "postgresql", "G1 intake witnesses require PostgreSQL")
class ClockG1IntakeTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.route = prepare_route(tag=fresh(), to_birthing_phase=False)
        ensure_test_policy_text()

    def _phase(self, suffix: str, *, effective_at=None, route=None):
        route = route or self.route
        return PhaseAdvanceCommand(
            credential=route.founder.credential,
            living_organism_uuid=route.organism.organism_id,
            from_phase="GERMINATING",
            to_phase="BIRTHING",
            request_reference=f"clk3:{suffix}:{route.tag}",
            idempotency_key=f"clk3:key:{suffix}:{route.tag}",
            effective_at=effective_at,
        )

    def _birthing_command(self, suffix: str, *, offering="FREE", effective_at=None, route=None):
        route = route or self.route
        return BirthingCommand(
            credential=route.founder.credential,
            living_organism_uuid=route.organism.organism_id,
            request_reference=f"clk3:{suffix}:{route.tag}",
            idempotency_key=f"clk3:key:{suffix}:{route.tag}",
            effective_at=effective_at,
            offering_choice=offering,
            policy_version=TEST_POLICY_VERSION,
            policy_text_sha256=TEST_POLICY_SHA256,
        )

    def _birth(self, suffix: str, *, offering="FREE", effective_at=None):
        """Advance to Birthing through the public entry, then build the Birthing command."""
        if self.route.phase_service.advance_phase(
            self._phase(f"{suffix}-phase")
        ) is None:
            self.fail("phase advance returned no result")
        return self._birthing_command(suffix, offering=offering, effective_at=effective_at)

    def _beta_service(self, source=None, authority=None):
        return OrganismEntitlementService(
            authority=authority or self.route.birthing_service._authority,
            clock=database_now,
            availability_source=source,
        )

    def _beta_command(self, suffix: str, *, credential=None):
        return EntitlementElectionCommand(
            credential=credential or self.route.founder.credential,
            living_organism_uuid=self.route.organism.organism_id,
            request_reference=f"clk3:{suffix}:{self.route.tag}",
            idempotency_key=f"clk3:key:{suffix}:{self.route.tag}",
            occurred_at=route_test_now(self.route),
            policy_version=TEST_POLICY_VERSION,
            policy_text_sha256=TEST_POLICY_SHA256,
        )

    def _binding(self, entry, key, *, actor=None):
        return CommandIntake.objects.get(
            actor=actor or self.route.founder,
            entry=entry.value,
            idempotency_key=key,
        )

    def _receipt(self, action, key, *, actor=None):
        return OrganismCommandReceipt.objects.get(
            actor=actor or self.route.founder,
            action=action.value,
            idempotency_key=key,
        )

    def _later_than(self, value):
        for _ in range(3):
            observed = database_now()
            if observed > value:
                return observed
        self.fail("database clock did not advance; witness cannot discriminate")

    def _recording_viewer(self, module):
        """Patch the module's ReplayViewer to record each viewer's now (D section 7.1a (iii))."""
        times = []
        original = module.ReplayViewer

        def record(*args, **kwargs):
            viewer = original(*args, **kwargs)
            times.append(viewer.now)
            return viewer

        return patch.object(module, "ReplayViewer", side_effect=record), times

    def _phase_fingerprint(self, command, received_at, effective_at, *, route=None):
        route = route or self.route
        return _fingerprint(
            domain=_PHASE_DOMAIN,
            schema="s015-phase-advance-v1",
            action=_TRANSITION,
            actor=route.founder,
            request_reference=command.request_reference,
            idempotency_key=command.idempotency_key,
            occurred_at=effective_at,
            target={
                "action": _TRANSITION.value,
                "organism_uuid": route.organism.organism_id,
                "from_phase": command.from_phase,
                "to_phase": command.to_phase,
            },
            command={
                "effective_at": effective_at,
                "received_at": received_at,
                "schema": "s015-phase-advance-v1",
            },
        )

    def _birthing_fingerprint(self, command, received_at, effective_at, *, route=None):
        route = route or self.route
        return _fingerprint(
            domain=_BIRTHING_DOMAIN,
            schema="s015-birthing-v2",
            action=_TRANSITION,
            actor=route.founder,
            request_reference=command.request_reference,
            idempotency_key=command.idempotency_key,
            occurred_at=effective_at,
            target={
                "action": _TRANSITION.value,
                "organism_uuid": route.organism.organism_id,
            },
            command={
                "effective_at": effective_at,
                "received_at": received_at,
                "offering_choice": command.offering_choice,
                "policy_text_sha256": command.policy_text_sha256,
                "policy_version": command.policy_version,
                "schema": "s015-birthing-v2",
            },
        )

    def _nested_election_fingerprint(self, command, received_at, effective_at, *, route=None):
        route = route or self.route
        return _fingerprint(
            domain=_ELECTION_DOMAIN,
            schema="s015-entitlement-election-birthing-v1",
            action=_ELECTION,
            actor=route.founder,
            request_reference=command.request_reference,
            idempotency_key=command.idempotency_key,
            occurred_at=effective_at,
            target={
                "action": _ELECTION.value,
                "organism_uuid": route.organism.organism_id,
            },
            command={
                "effective_at": effective_at,
                "offering_choice": command.offering_choice,
                "policy_text_sha256": command.policy_text_sha256,
                "policy_version": command.policy_version,
                "received_at": received_at,
            },
        )

    def _beta_fingerprint(self, command, *, actor=None):
        return _fingerprint(
            domain=_ELECTION_DOMAIN,
            schema="s015-entitlement-election-v1",
            action=_ELECTION,
            actor=actor or self.route.founder,
            request_reference=command.request_reference,
            idempotency_key=command.idempotency_key,
            occurred_at=command.occurred_at,
            target={
                "action": _ELECTION.value,
                "organism_uuid": self.route.organism.organism_id,
            },
            command={
                "policy_text_sha256": command.policy_text_sha256,
                "policy_version": command.policy_version,
                "offering_choice": "COMPLIMENTARY_BETA",
            },
        )

    def _assert_beta_rows_at(self, result, received_at):
        """D section 7.1 [A2]: the opt-in's version, event, anchor, GRANTED event and decision at T."""
        event = LivingOrganismEvent.objects.get(event_uuid=result.event_uuid)
        version = LOApplicationSettingsVersion.objects.get(
            version_uuid=UUID(event.evidence_reference.split(":", 1)[1])
        )
        grant = OrganismEntitlement.objects.get(entitlement_uuid=result.entitlement_uuid)
        granted = OrganismEntitlementEvent.objects.get(entitlement=grant, sequence=1)
        decision = AuthorityDecision.objects.get(
            decision_reference=result.authority_decision_reference,
        )
        self.assertEqual(
            (event.received_at, event.effective_at),
            (received_at, received_at),
        )
        self.assertEqual(
            (version.received_at, version.effective_at),
            (received_at, received_at),
        )
        self.assertEqual(grant.basis, "BETA_OPT_IN")
        self.assertEqual(grant.effective_at, received_at)
        self.assertEqual(
            (granted.occurred_at, granted.effective_at, granted.received_at),
            (received_at, received_at, received_at),
        )
        self.assertEqual(decision.outcome, "QUALIFIED")
        self.assertEqual(decision.evaluated_at, received_at)
        return version

    def test_iw00_g1_entries_refuse_atomic_and_manual_modes_without_sql(self):
        phase = self._phase("iw00-phase")
        birthing = BirthingCommand(
            credential=self.route.founder.credential,
            living_organism_uuid=self.route.organism.organism_id,
            request_reference="clk3:iw00:birthing",
            idempotency_key="clk3:key:iw00:birthing",
            offering_choice="FREE",
            policy_version=TEST_POLICY_VERSION,
            policy_text_sha256=TEST_POLICY_SHA256,
        )
        beta = self._beta_command("iw00-beta")
        technical = (
            "could not be completed because of a technical problem; "
            "nothing was saved. Try again; if it persists, report it."
        )
        calls = (
            (
                self.route.phase_service.advance_phase,
                phase,
                S015PhaseAdvanceTechnicalFailure,
                f"PHASE_ADVANCE {technical}",
                _TRANSITION,
            ),
            (
                self.route.birthing_service.birth_living_organism,
                birthing,
                S015BirthingTechnicalFailure,
                f"BIRTHING {technical}",
                _TRANSITION,
            ),
            (
                self._beta_service().activate_beta,
                beta,
                S015EntitlementTechnicalFailure,
                "entitlement opt-in cannot run inside an existing transaction",
                _ELECTION,
            ),
        )
        before = _public_table_counts()
        # (a) the family guard, first and verbatim (D section 5.1, section 10.3; Stop 3 B-01's exception)
        with transaction.atomic():
            with CaptureQueriesContext(connection) as queries:
                for call, command, error, guard_text, _action in calls:
                    with self.assertRaises(error) as refused:
                        call(command)
                    self.assertEqual(str(refused.exception), guard_text)
        self.assertEqual(len(queries), 0)
        # (b) manual mode: B-01 for every entry
        connection.set_autocommit(False)
        try:
            with CaptureQueriesContext(connection) as manual_queries:
                for call, command, _error, _guard_text, action in calls:
                    with self.assertRaises(S015CommandAtomicityRequired) as refused:
                        call(command)
                    self.assertEqual(
                        str(refused.exception),
                        f"{action.value} requires a top-level transaction and was not started: "
                        "a manual transaction is open on this connection. Nothing was saved; "
                        "the caller's transaction is untouched.",
                    )
            self.assertEqual(len(manual_queries), 0)
        finally:
            connection.rollback()
            connection.set_autocommit(True)
        self.assertEqual(_public_table_counts(), before)

    def test_iw00c_g1_entries_admit_then_run_their_executors(self):
        phase = self._phase("iw00c")
        birthing = self._birthing_command("iw00c-birthing")
        beta = self._beta_command("iw00c-beta")
        calls = (
            (self.route.phase_service.advance_phase, phase, IntakeEntry.ADVANCE_PHASE, _TRANSITION),
            (
                self.route.birthing_service.birth_living_organism,
                birthing,
                IntakeEntry.BIRTHING,
                _TRANSITION,
            ),
            (self._beta_service().activate_beta, beta, IntakeEntry.ACTIVATE_BETA, _ELECTION),
        )
        for call, command, entry, action in calls:
            with self.subTest(entry=entry):
                before = CommandIntake.objects.count()
                self.assertIsNotNone(call(command))
                self.assertEqual(CommandIntake.objects.count(), before + 1)
                self.assertEqual(
                    self._binding(entry, command.idempotency_key).entry,
                    entry.value,
                )
                self._receipt(action, command.idempotency_key)
                self.assertFalse(connection.in_atomic_block)
        self.assertTrue(
            {IntakeEntry.ADVANCE_PHASE, IntakeEntry.BIRTHING, IntakeEntry.ACTIVATE_BETA}
            <= ACTIVE_ENTRIES
        )

    def test_w_clk3_01_phase_advance_unchanged_retry_replays_with_distinct_clock_observations(self):
        command = self._phase("w01")
        first = self.route.phase_service.advance_phase(command)
        binding = self._binding(IntakeEntry.ADVANCE_PHASE, command.idempotency_key)
        later = self._later_than(binding.received_at)
        self.assertGreater(later, binding.received_at)
        counts = _public_table_counts()
        viewer_patch, viewer_times = self._recording_viewer(formation_module)
        with viewer_patch:
            self.assertEqual(self.route.phase_service.advance_phase(command), first)
        self.assertEqual(_public_table_counts(), counts)
        self.assertGreater(viewer_times[-1], binding.received_at)
        receipt = self._receipt(_TRANSITION, command.idempotency_key)
        self.assertEqual(
            receipt.payload_fingerprint,
            self._phase_fingerprint(command, binding.received_at, binding.received_at),
        )

    def test_w_clk3_02_a_stated_effective_time_on_the_retry_of_an_unstated_phase_advance_is_refused_at_admission(self):
        command = self._phase("w02a")
        service = self.route.phase_service
        service.advance_phase(command)
        retry = replace(command, effective_at=route_test_now(self.route))
        counts = _public_table_counts()
        with patch.object(
            service,
            "_execute_advance_phase",
            wraps=service._execute_advance_phase,
        ) as executor:
            with self.assertRaises(S015CommandReplayMismatch):
                service.advance_phase(retry)
        executor.assert_not_called()
        self.assertEqual(_public_table_counts(), counts)

    def test_w_clk3_02_a_changed_offering_choice_on_a_birthing_retry_is_refused_at_admission(self):
        command = self._birth("w02b")
        service = self.route.birthing_service
        service.birth_living_organism(command)
        counts = _public_table_counts()
        with patch.object(
            service,
            "_execute_birth_living_organism",
            wraps=service._execute_birth_living_organism,
        ) as executor:
            with self.assertRaises(S015CommandReplayMismatch):
                service.birth_living_organism(
                    replace(command, offering_choice="COMPLIMENTARY_BETA")
                )
        executor.assert_not_called()
        self.assertEqual(_public_table_counts(), counts)

    def test_w_clk3_03_phase_advance_rollback_then_retry_reproduces_t_and_the_fingerprint(self):
        command = self._phase("w03")
        fingerprints = []

        def fail_after_event(**_kwargs):
            fingerprints.append(
                LivingOrganismEvent.objects.filter(
                    living_organism=self.route.organism,
                    action="PHASE_ADVANCE",
                ).latest("pk").payload_fingerprint
            )
            raise RuntimeError("rollback")

        before = _public_table_counts()
        with patch.object(
            self.route.phase_service,
            "_finalise",
            side_effect=fail_after_event,
        ):
            with self.assertRaisesRegex(RuntimeError, "rollback"):
                self.route.phase_service.advance_phase(command)
        # IW-04a: the rolled-back first attempt leaves its intake row only.
        self.assertEqual(
            _changed_tables(before, _public_table_counts()),
            {"core_commandintake": 1},
        )
        binding = self._binding(IntakeEntry.ADVANCE_PHASE, command.idempotency_key)
        result = self.route.phase_service.advance_phase(command)
        event = LivingOrganismEvent.objects.get(event_uuid=result.event_uuid)
        self.assertEqual(
            (event.received_at, event.effective_at, event.occurred_at),
            (binding.received_at, binding.received_at, binding.received_at),
        )
        decision = AuthorityDecision.objects.get(
            decision_reference=result.authority_decision_reference,
        )
        self.assertEqual(decision.evaluated_at, binding.received_at)
        self.assertEqual(event.payload_fingerprint, fingerprints[0])
        receipt = OrganismCommandReceipt.objects.get(
            idempotency_key=command.idempotency_key,
        )
        self.assertEqual(receipt.payload_fingerprint, fingerprints[0])

    def _assert_iw14(self, route, command, result, received_at, effective_at, counts_before):
        """IW-14 (D section 7.1, section 7.3): the Birthing's rows, nested election and fingerprints."""
        self.assertEqual(
            _changed_tables(counts_before, _public_table_counts()),
            {
                "core_authoritydecision": 2,
                "core_commandintake": 1,
                "core_livingorganismevent": 2,
                "core_loapplicationsettingsversion": 1,
                "core_lobirthingsnapshot": 1,
                "core_organismcommandreceipt": 1,
                "core_organismentitlement": 1,
                "core_organismentitlementevent": 1,
            },
        )
        self.assertEqual(
            CommandIntake.objects.filter(idempotency_key=command.idempotency_key).count(),
            1,
        )
        self.assertEqual(
            OrganismCommandReceipt.objects.filter(idempotency_key=command.idempotency_key).count(),
            1,
        )
        snapshot = LOBirthingSnapshot.objects.get(snapshot_uuid=result.snapshot_uuid)
        self.assertEqual(
            (snapshot.received_at, snapshot.effective_at),
            (received_at, effective_at),
        )
        birthing_event = LivingOrganismEvent.objects.get(event_uuid=result.event_uuid)
        self.assertEqual(
            (birthing_event.received_at, birthing_event.effective_at),
            (received_at, effective_at),
        )
        election_event = LivingOrganismEvent.objects.get(
            living_organism=route.organism,
            action=_ELECTION.value,
        )
        self.assertEqual(election_event.received_at, received_at)
        election = LOApplicationSettingsVersion.objects.get(
            version_uuid=UUID(election_event.evidence_reference.split(":", 1)[1])
        )
        self.assertEqual(
            (election.received_at, election.effective_at, election.occurred_at),
            (received_at, effective_at, effective_at),
        )
        decisions = AuthorityDecision.objects.filter(
            idempotency_key=command.idempotency_key,
            outcome="QUALIFIED",
        )
        self.assertEqual(
            sorted((row.action, row.evaluated_at) for row in decisions),
            sorted([
                (_ELECTION.value, received_at),
                (_TRANSITION.value, received_at),
            ]),
        )
        grant = OrganismEntitlement.objects.get(entitlement_uuid=result.entitlement_uuid)
        self.assertEqual(grant.living_organism_id, route.organism.pk)
        self.assertEqual(
            election_event.payload_fingerprint,
            self._nested_election_fingerprint(command, received_at, effective_at, route=route),
        )
        receipt = self._receipt(_TRANSITION, command.idempotency_key, actor=route.founder)
        self.assertEqual(
            receipt.payload_fingerprint,
            self._birthing_fingerprint(command, received_at, effective_at, route=route),
        )

    def _assert_replay(self, service, call, command, result, received_at):
        """D section 7.1a: the clock has moved; the retry replays, +0 everywhere, viewer now > T."""
        self._later_than(received_at)
        counts = _public_table_counts()
        viewer_patch, viewer_times = self._recording_viewer(formation_module)
        with viewer_patch, patch.object(service, "_clock", side_effect=database_now):
            self.assertEqual(call(command), result)
        self.assertEqual(_public_table_counts(), counts)
        self.assertGreater(viewer_times[-1], received_at)

    def test_w_clk3_04_iw14_birthing_election_carries_t_for_unstated_and_stated_effective_times_and_the_retry_replays(self):
        # (i) unstated effective time: the election's received_at = effective_at = occurred_at = T
        command = self._birth("w04", offering="COMPLIMENTARY_BETA")
        service = self.route.birthing_service
        before = _public_table_counts()
        result = service.birth_living_organism(command)
        binding = self._binding(IntakeEntry.BIRTHING, command.idempotency_key)
        self._assert_iw14(
            self.route, command, result, binding.received_at, binding.received_at, before,
        )
        self._assert_replay(
            service, service.birth_living_organism, command, result, binding.received_at,
        )

        # (ii) stated effective time E < T: the election's received_at = T, effective_at = occurred_at = E
        stated_route = prepare_route(tag=fresh(), to_birthing_phase=False)
        stated_route.phase_service.advance_phase(self._phase("w04s", route=stated_route))
        stated = database_now()  # after the set-up, at or before the call (C5-A63)
        stated_command = self._birthing_command(
            "w04s-birth",
            offering="COMPLIMENTARY_BETA",
            effective_at=stated,
            route=stated_route,
        )
        stated_service = stated_route.birthing_service
        stated_before = _public_table_counts()
        stated_result = stated_service.birth_living_organism(stated_command)
        stated_binding = self._binding(
            IntakeEntry.BIRTHING,
            stated_command.idempotency_key,
            actor=stated_route.founder,
        )
        self.assertLess(stated, stated_binding.received_at)
        self._assert_iw14(
            stated_route,
            stated_command,
            stated_result,
            stated_binding.received_at,
            stated,
            stated_before,
        )
        self._assert_replay(
            stated_service,
            stated_service.birth_living_organism,
            stated_command,
            stated_result,
            stated_binding.received_at,
        )

    def test_w_clk3_05_birthing_rollback_after_the_snapshot_keeps_only_the_intake_and_retries_at_t(self):
        command = self._birth("w05")
        service = self.route.birthing_service
        before = _public_table_counts()
        with patch.object(
            service._entitlement_service,
            "prepare_birthing_election",
            side_effect=RuntimeError("after snapshot"),
        ):
            with self.assertRaisesRegex(RuntimeError, "after snapshot"):
                service.birth_living_organism(command)
        self.assertEqual(
            _changed_tables(before, _public_table_counts()),
            {"core_commandintake": 1},
        )
        binding = self._binding(IntakeEntry.BIRTHING, command.idempotency_key)
        result = service.birth_living_organism(command)
        snapshot = LOBirthingSnapshot.objects.get(snapshot_uuid=result.snapshot_uuid)
        self.assertEqual(snapshot.received_at, binding.received_at)
        self.assertEqual(snapshot.effective_at, binding.received_at)

    def test_w_clk3_06_beta_unchanged_retry_replays_with_distinct_clock_observations(self):
        self.route.birthing_service.birth_living_organism(self._birth("w06-birth"))
        service = self._beta_service()
        command = self._beta_command("w06")
        first = service.activate_beta(command)
        binding = self._binding(IntakeEntry.ACTIVATE_BETA, command.idempotency_key)
        later = self._later_than(binding.received_at)
        self.assertGreater(later, binding.received_at)
        counts = _public_table_counts()
        viewer_patch, viewer_times = self._recording_viewer(entitlement_module)
        with viewer_patch:
            self.assertEqual(service.activate_beta(command), first)
        self.assertEqual(_public_table_counts(), counts)
        self.assertGreater(viewer_times[-1], binding.received_at)
        # The beta projection carries no receipt time; no fingerprint change is made (D section 7.1).
        self.assertEqual(
            self._receipt(_ELECTION, command.idempotency_key).payload_fingerprint,
            self._beta_fingerprint(command),
        )

    def test_w_clk3_07_beta_rollback_then_retry_reproduces_t_on_version_event_and_grant(self):
        self.route.birthing_service.birth_living_organism(self._birth("w07-birth"))
        service = self._beta_service()
        command = self._beta_command("w07")
        original = service._append_election_event

        def append_then_fail(**kwargs):
            original(**kwargs)
            raise RuntimeError("rollback after election event")

        before = _public_table_counts()
        with patch.object(service, "_append_election_event", side_effect=append_then_fail):
            with self.assertRaisesRegex(RuntimeError, "rollback after election event"):
                service.activate_beta(command)
        self.assertEqual(
            _changed_tables(before, _public_table_counts()),
            {"core_commandintake": 1},
        )
        binding = self._binding(IntakeEntry.ACTIVATE_BETA, command.idempotency_key)
        result = service.activate_beta(command)
        self._assert_beta_rows_at(result, binding.received_at)

    def test_iw32a_beta_denial_keeps_the_intake_and_the_refused_decision(self):
        self.route.birthing_service.birth_living_organism(self._birth("iw32a-birth"))
        production = self.route.birthing_service._authority.provider
        service = self._beta_service(
            authority=S015Authority(provider=_ElectionRefusingProvider(production))
        )
        command = self._beta_command("iw32a")
        before = _public_table_counts()
        with self.assertRaises(S015AuthorityDenied) as refused:
            with disclose_authority_refusal_code():
                service.activate_beta(command)
        self.assertIs(
            refused.exception.refusal_code,
            S015AuthorityRefusalCode.SE_OPT_IN_NEEDS_CQ,
        )
        # IW-32(a): +1 REFUSED decision, no election version, +1 intake; nothing else.
        self.assertEqual(
            _changed_tables(before, _public_table_counts()),
            {"core_authoritydecision": 1, "core_commandintake": 1},
        )
        binding = self._binding(IntakeEntry.ACTIVATE_BETA, command.idempotency_key)
        decision = AuthorityDecision.objects.get(
            request_reference=command.request_reference,
            outcome="REFUSED",
        )
        self.assertEqual(decision.refusal_code, S015AuthorityRefusalCode.SE_OPT_IN_NEEDS_CQ.value)
        self.assertEqual(decision.evaluated_at, binding.received_at)
        self.assertEqual(
            str(refused.exception),
            "Under your organism's rules, opting into beta access after Birthing requires "
            "collective approval through CQ. That process is not yet available for your "
            "organism. INTEVIA recorded this refusal and that the request was received at "
            f"{canonical_timestamp(binding.received_at)}; nothing else was saved.",
        )

    def test_iw32b_birthing_election_denial_current_effects_with_the_intake(self):
        command = self._birth("iw32b", offering="COMPLIMENTARY_BETA")
        entitlement_service = self.route.birthing_service._entitlement_service
        entitlement_service._authority = S015Authority(
            provider=_ElectionRefusingProvider(entitlement_service._authority.provider)
        )
        before = _public_table_counts()
        with self.assertRaises(S015AuthorityDenied) as refused:
            with disclose_authority_refusal_code():
                self.route.birthing_service.birth_living_organism(command)
        # IW-32(b), DEC-F3's current effects: +1 QUALIFIED, +1 REFUSED, +1 snapshot,
        # +1 intake; no event, election, grant or receipt.
        self.assertEqual(
            _changed_tables(before, _public_table_counts()),
            {
                "core_authoritydecision": 2,
                "core_commandintake": 1,
                "core_lobirthingsnapshot": 1,
            },
        )
        binding = self._binding(IntakeEntry.BIRTHING, command.idempotency_key)
        self.assertEqual(
            sorted(
                AuthorityDecision.objects.filter(
                    idempotency_key=command.idempotency_key,
                ).values_list("action", "outcome", "evaluated_at")
            ),
            sorted([
                (_ELECTION.value, "REFUSED", binding.received_at),
                (_TRANSITION.value, "QUALIFIED", binding.received_at),
            ]),
        )
        self.assertEqual(
            list(
                LOBirthingSnapshot.objects.filter(
                    living_organism=self.route.organism,
                ).values_list("received_at", flat=True)
            ),
            [binding.received_at],
        )
        expected_text = (
            "The LO was not born: the entitlement election proposed with this Birthing was "
            "refused (SE_OPT_IN_NEEDS_CQ). Records kept from this attempt: the Birthing's "
            "qualification, the constitutional snapshot prepared for it, the election's refusal, "
            "and that this request was received at "
            f"{canonical_timestamp(binding.received_at)}. No LO event, no election, no grant and "
            "no receipt were recorded; the LO remains unborn. Sending this request again, or "
            "making a new Birthing request, cannot complete this Living Organism's (LO) "
            "Birthing. A software problem left a constitutional snapshot from the unsuccessful "
            "attempt, which blocks further attempts. Please report this problem; this LO's "
            "Birthing cannot complete until the problem is repaired."
        )
        self.assertEqual(str(refused.exception), expected_text)

        # W-A3-3 (A3 DF-2): the identical request again meets the one-snapshot-per-LO
        # constraint, is translated after rollback (F3-02, class T) and adds nothing.
        before_retry = _public_table_counts()
        with self.assertRaises(S015BirthingTechnicalFailure) as retried:
            self.route.birthing_service.birth_living_organism(command)
        self.assertEqual(_changed_tables(before_retry, _public_table_counts()), {})
        self.assertEqual(
            str(retried.exception),
            "The LO was not born. A software problem left a constitutional snapshot from an "
            "earlier, unsuccessful Birthing attempt, which blocks further attempts; this is not a "
            "decision against the LO. Nothing from this request was saved except a record that "
            f"it was received at {canonical_timestamp(binding.received_at)}. Sending this request "
            "again, or making a new Birthing request, cannot complete this LO's Birthing. Please "
            "report this problem; this LO's Birthing cannot complete until the problem is "
            "repaired.",
        )
        cause = retried.exception.__cause__
        while cause is not None and getattr(cause, "diag", None) is None:
            cause = cause.__cause__
        self.assertIsNotNone(cause)
        self.assertEqual(getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None), "23505")
        self.assertEqual(cause.diag.constraint_name, "core_lobirthingsnapshot_living_organism_id_uniq")
        self.assertEqual(
            LOBirthingSnapshot.objects.filter(living_organism=self.route.organism).count(),
            1,
        )

    def test_w_clk3_h125a_retry_passes_the_binding_time_to_the_availability_source_and_the_history_reads(self):
        self.route.birthing_service.birth_living_organism(self._birth("h125a-birth"))
        free_election = LOApplicationSettingsVersion.objects.get(
            version_uuid=UUID(
                LivingOrganismEvent.objects.get(
                    living_organism=self.route.organism,
                    action=_ELECTION.value,
                ).evidence_reference.split(":", 1)[1]
            )
        )
        calls = []

        class Availability:
            closed_after = None

            def paid_availability_transition_reference(self, organism_uuid, effective_at, known_at):
                calls.append((organism_uuid, effective_at, known_at))
                return None if self.closed_after is None or known_at <= self.closed_after else "closed"

        source = Availability()
        service = self._beta_service(source)
        command = self._beta_command("h125a")
        original = service._append_election_event

        def fail_after_event(**kwargs):
            original(**kwargs)
            raise RuntimeError("rollback")

        before = _public_table_counts()
        with patch.object(service, "_append_election_event", side_effect=fail_after_event):
            with self.assertRaisesRegex(RuntimeError, "rollback"):
                service.activate_beta(command)
        after_first = _public_table_counts()
        self.assertEqual(_changed_tables(before, after_first), {"core_commandintake": 1})
        binding = self._binding(IntakeEntry.ACTIVATE_BETA, command.idempotency_key)
        source.closed_after = self._later_than(binding.received_at)
        with (
            patch.object(
                service,
                "_effective_election",
                wraps=service._effective_election,
            ) as election_read,
            patch.object(
                entitlement_module,
                "_effective_version",
                wraps=entitlement_module._effective_version,
            ) as version_read,
        ):
            result = service.activate_beta(command)
        self.assertIsNotNone(result)
        # D section 8: the retry's exact survivors.
        self.assertEqual(
            _changed_tables(after_first, _public_table_counts()),
            {
                "core_authoritydecision": 1,
                "core_livingorganismevent": 1,
                "core_loapplicationsettingsversion": 1,
                "core_organismcommandreceipt": 1,
                "core_organismentitlement": 1,
                "core_organismentitlementevent": 1,
            },
        )
        version = self._assert_beta_rows_at(result, binding.received_at)
        self.assertEqual(version.supersedes_id, free_election.pk)
        self.assertEqual(
            calls[-1],
            (self.route.organism.organism_id, binding.received_at, binding.received_at),
        )
        self.assertEqual(election_read.call_args.args[1:], (
            binding.received_at,
            binding.received_at,
        ))
        self.assertEqual(version_read.call_args.args[3:], (
            binding.received_at,
            binding.received_at,
        ))

    def test_w_clk3_h125b_a_grant_written_after_t_refuses_the_retry_as_already_beta(self):
        self.route.birthing_service.birth_living_organism(self._birth("h125b-birth"))
        service = self._beta_service()
        command = self._beta_command("h125b")
        before = _public_table_counts()
        with patch.object(service, "_append_election_event", side_effect=RuntimeError("rollback")):
            with self.assertRaisesRegex(RuntimeError, "rollback"):
                service.activate_beta(command)
        # A's first attempt: +1 intake only, before B's set-up.
        self.assertEqual(
            _changed_tables(before, _public_table_counts()),
            {"core_commandintake": 1},
        )
        binding = self._binding(IntakeEntry.ACTIVATE_BETA, command.idempotency_key)
        other = add_test_coordinator(self.route, tag=f"clk3-h125b-{self.route.tag}")
        self._later_than(binding.received_at)  # the bounded later-clock precondition (D section 7.1a)
        second = self._beta_command("h125b-other", credential=other.credential)
        before_b = _public_table_counts()
        service.activate_beta(second)
        self.assertEqual(
            _changed_tables(before_b, _public_table_counts()),
            {
                "core_authoritydecision": 1,
                "core_commandintake": 1,
                "core_livingorganismevent": 1,
                "core_loapplicationsettingsversion": 1,
                "core_organismcommandreceipt": 1,
                "core_organismentitlement": 1,
                "core_organismentitlementevent": 1,
            },
        )
        second_binding = self._binding(
            IntakeEntry.ACTIVATE_BETA,
            second.idempotency_key,
            actor=other,
        )
        self.assertGreater(second_binding.received_at, binding.received_at)
        before_retry = _public_table_counts()
        with patch.object(
            service,
            "_new_settings_version",
            wraps=service._new_settings_version,
        ) as create_version:
            with self.assertRaises(S015EntitlementRefused) as refused:
                service.activate_beta(command)
        create_version.assert_not_called()
        self.assertEqual(_public_table_counts(), before_retry)
        self.assertIs(
            refused.exception.refusal_code,
            S015AuthorityRefusalCode.SE_ALREADY_BETA,
        )
        self.assertEqual(
            str(refused.exception),
            "Your organism already has beta access; no governed record was written. INTEVIA "
            "kept a record that this request was received at "
            f"{canonical_timestamp(binding.received_at)}; sent again unchanged, it is answered "
            "as of that time.",
        )

    def test_step_b_activation_has_no_unaccounted_record_and_reports_an_injected_one(self):
        self.route.birthing_service.birth_living_organism(self._birth("activation-birth"))
        service = self._beta_service()
        service.activate_beta(self._beta_command("activation-beta"))
        entries = (
            IntakeEntry.ADVANCE_PHASE,
            IntakeEntry.BIRTHING,
            IntakeEntry.ACTIVATE_BETA,
        )
        self.assertEqual(run_activation_step("default", entries), [])
        key = self._inject_unaccounted_receipt()
        records = run_activation_step("default", entries)
        self.assertEqual(
            {
                (row.kind, row.action, row.idempotency_key, row.actor_id)
                for row in records
            },
            {("receipt", _TRANSITION.value, key, self.route.founder.pk)},
        )

    def _inject_unaccounted_receipt(self):
        receipt_uuid = uuid4()
        key = f"clk3:unaccounted:{self.route.tag}"
        lineage = "s015l1:" + hashlib.sha256(
            f"clk3-unaccounted:{receipt_uuid}".encode("ascii")
        ).hexdigest()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO public.core_organismcommandreceipt (
                    receipt_uuid, actor_id, action, idempotency_key,
                    request_reference, payload_fingerprint,
                    authority_decision_reference, lineage_reference,
                    result_payload, completed_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                """,
                [
                    receipt_uuid,
                    self.route.founder.pk,
                    _TRANSITION.value,
                    key,
                    f"request:{receipt_uuid.hex}",
                    "b" * 64,
                    "s015d1:" + "c" * 64,
                    lineage,
                    json.dumps({"fixture": True}),
                    database_now(),
                ],
            )
        return key
