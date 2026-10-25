"""SE entitlement model, migration, command, and guard witnesses."""

import hashlib
import importlib
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.db import DatabaseError, close_old_connections, connection, connections, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import SimpleTestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext

from core.models import (
    AuthorityDecision,
    Circle,
    CircleStateEvent,
    GovernedRule,
    Identity,
    LivingOrganismEvent,
    LOApplicationSettingsVersion,
    LOGovernanceConfigurationVersion,
    OrganismCommandReceipt,
    OrganismEntitlement,
    OrganismEntitlementEvent,
    OrganismEntitlementPolicyText,
    OrganismMembership,
    OrganismMembershipTransition,
    SE_ENTITLEMENT_CAPABILITY_CODES,
    _S015AppendOnly,
)
from core.organism_contract import OrganismAction
from src.intevia.services.organism_application_authority import (
    ProductionApplicationAuthorityProvider,
)
from src.intevia.services.organism_authority import (
    S015Authority,
    S015AuthorityDenied,
    S015AuthorityRefusalCode,
)
from src.intevia.services.organism_entitlement_service import (
    EntitlementElectionCommand,
    EntitlementExtensionCommand,
    OrganismEntitlementService,
    S015EntitlementRefused,
    S015EntitlementTechnicalFailure,
    capability_status,
    check_allowance,
    entitlement_effective,
    entitlement_limit,
    entitlement_summary,
)
from src.intevia.services.organism_formation_service import BirthingCommand
from tests.s015_0021_support import (
    append_event,
    database_now,
    current_applied_core_head,
    migrate_to_target_with_restore,
    refresh,
    seed_identity,
)
from tests.sb2_route_support import birth, database_now, prepare_route
from tests.se_route_support import (
    TestFixturePaidAvailabilitySource,
    TEST_POLICY_SHA256,
    TEST_POLICY_TEXT,
    TEST_POLICY_VERSION,
    declared_event_less_entitlement_rows,
    disclose_authority_refusal_code,
    ensure_test_policy_text,
)


SE_MIGRATION = ("core", "0046_se_entitlement_foundation")
PREVIOUS_MIGRATION = ("core", "0045_sbg_governance_formation")
SEK1_MIGRATION = ("core", "0047_sek1_platform_rule_statement_start")
NEW_TABLES = {
    "core_organismentitlement",
    "core_organismentitlementevent",
    "core_organismentitlementpolicytext",
}
LATER_TABLES = {"core_commandintake"}  # created after 0046 (by 0049)


class SEEntitlementModelContractTests(SimpleTestCase):
    def test_w_se_t2_t3_authored_messages_match_guard_codes(self):
        refusals = (
            (
                S015AuthorityRefusalCode.SE_EXTENSION_AUTHORITY,
                "Extending beta access requires approval by a Coordinator of "
                "INTEVIA's Relationship Circle under its recorded policy. This "
                "request does not meet those requirements. Nothing has changed.",
            ),
            (
                S015AuthorityRefusalCode.SE_SELF_EXTENSION,
                "An organism cannot extend its own beta access. Nothing has changed.",
            ),
            (
                S015AuthorityRefusalCode.SE_OPT_IN_NEEDS_CQ,
                "Under your organism's rules, opting into beta access after Birthing "
                "requires collective approval through CQ. That process is not yet "
                "available for your organism. Nothing has changed.",
            ),
        )
        for refusal_code, expected_text in refusals:
            with self.subTest(refusal_code=refusal_code):
                refusal = S015EntitlementRefused(refusal_code)
                self.assertIs(refusal.refusal_code, refusal_code)
                self.assertEqual(str(refusal), expected_text)

    def test_model_inventory_is_append_only_and_has_the_se_identity_slice(self):
        expected_fields = {
            OrganismEntitlementPolicyText: {
                "policy_version",
                "accepted_text",
                "text_sha256",
                "recorded_at",
            },
            OrganismEntitlement: {
                "entitlement_uuid",
                "living_organism",
                "source",
                "basis",
                "granting_party_id",
                "policy_reference",
                "policy_version",
                "capabilities",
                "limits",
                "effective_at",
                "effective_until",
                "ending_condition_reference",
                "tier_reference",
                "pricing_basis_reference",
                "recorded_at",
            },
            OrganismEntitlementEvent: {
                "event_uuid",
                "entitlement",
                "sequence",
                "predecessor",
                "event_type",
                "actor_state",
                "actor",
                "actor_access_epoch",
                "actor_capacity",
                "entry_mode",
                "enterer_identity",
                "composing_rule_state",
                "composing_rule",
                "composing_rule_reference",
                "composer_state",
                "composer_identity",
                "composed_from_event_uuid",
                "deciding_identity",
                "authoriser_identity",
                "authoriser_office",
                "reason",
                "occurred_at",
                "effective_at",
                "received_at",
                "recorded_at",
            },
        }
        self.assertEqual(len(expected_fields), 3)
        self.assertTrue(
            all(issubclass(model, _S015AppendOnly) for model in expected_fields)
        )
        for model, expected in expected_fields.items():
            with self.subTest(model=model.__name__):
                actual = {
                    field.name
                    for field in model._meta.fields
                    if field.name != "id"
                }
                self.assertEqual(actual, expected)
        self.assertEqual(
            {
                field.name
                for field in OrganismEntitlementEvent._meta.get_fields()
                if getattr(field, "related_model", None) is Identity
                and getattr(field, "concrete", False)
            },
            {
                "actor",
                "authoriser_identity",
                "composer_identity",
                "deciding_identity",
                "enterer_identity",
            },
        )
        self.assertEqual(
            OrganismEntitlement._meta.constraints[-1].name,
            "se_entitlement_beta_one_per_lo_uniq",
        )
        self.assertTrue(
            all(
                field.remote_field.on_delete.__name__ == "PROTECT"
                for model in expected_fields
                for field in model._meta.fields
                if getattr(field, "related_model", None) is not None
            )
        )


@skipUnless(connection.vendor == "postgresql", "SE migration guardians require PostgreSQL")
class SEEntitlementMigrationTests(TransactionTestCase):
    ISOLATION_FULL_CAPTURE = True

    reset_sequences = True

    def _core_tables(self):
        return {
            table
            for table in connection.introspection.table_names()
            if table.startswith("core_")
        }

    def _trigger_count(self):
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
            return cursor.fetchone()[0]

    def _assert_0045_state(self, expected_tables):
        migration = importlib.import_module(
            "core.migrations.0045_sbg_governance_formation"
        )
        self.assertEqual(self._core_tables(), expected_tables)
        applied = MigrationRecorder(connection).applied_migrations()
        self.assertEqual(current_applied_core_head(connection), [PREVIOUS_MIGRATION])
        self.assertIn(PREVIOUS_MIGRATION, applied)
        with connection.cursor() as cursor:
            for name, expected_body in migration.BUILT_FUNCTION_BODIES:
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
                self.assertEqual(cursor.fetchone()[0], expected_body, name)
        self.assertEqual(self._trigger_count(), 458)

    def _assert_0046_state(self):
        self._assert_se_schema_state(SE_MIGRATION)

    def _assert_received_head_state(self):
        self._assert_se_schema_state(
            ("core", "0049_h100_command_intake")
        )

    def _assert_se_schema_state(self, expected_head):
        applied = MigrationRecorder(connection).applied_migrations()
        self.assertEqual(current_applied_core_head(connection), [expected_head])
        self.assertIn(SE_MIGRATION, applied)
        self.assertIn(expected_head, applied)
        self.assertTrue(NEW_TABLES.issubset(self._core_tables()))
        import verification.isolation.state as isolation_state

        self.assertEqual(
            self._trigger_count(),
            isolation_state.REQUIRED_TRIGGER_COUNT[expected_head[1]],
        )
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT code, rule_uuid::text, version
                  FROM public.core_governedrule
                 WHERE code IN ('BETA_COMPLIMENTARY_ACCESS', 'FREE_TIER')
                 ORDER BY code
                """
            )
            self.assertEqual(
                cursor.fetchall(),
                [
                    (
                        "BETA_COMPLIMENTARY_ACCESS",
                        "c81640de-f776-5b31-a046-0046e0000001",
                        1,
                    ),
                    ("FREE_TIER", "c81640de-f776-5b31-a046-0046e0000002", 1),
                ],
            )
            cursor.execute("SELECT count(*) FROM public.core_governedrule")
            self.assertEqual(cursor.fetchone()[0], 3)
        migration = importlib.import_module(
            "core.migrations.0046_se_entitlement_foundation"
        )
        expected_bodies = dict(migration.SE_FUNCTION_BODIES)
        if expected_head[1] in {
            "0047_sek1_platform_rule_statement_start",
            "0048_se_entitlement_granted_event_guard",
            "0049_h100_command_intake",
        }:
            sek1_migration = importlib.import_module(
                "core.migrations.0047_sek1_platform_rule_statement_start"
            )
            expected_bodies["s015_entitlement_limit"] = (
                sek1_migration._ENTITLEMENT_LIMIT_BODY
            )
        if expected_head[1] in {
            "0048_se_entitlement_granted_event_guard",
            "0049_h100_command_intake",
        }:
            guard_migration = importlib.import_module(
                "core.migrations.0048_se_entitlement_granted_event_guard"
            )
            expected_bodies["s015_guard_entitlement_granted"] = (
                guard_migration.FUNCTION_SQL.split("AS $$", 1)[1].rsplit("$$", 1)[0]
            )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT p.proname, p.prosrc "
                "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = 'public' AND p.proname = ANY(%s)",
                [list(expected_bodies)],
            )
            installed_bodies = dict(cursor.fetchall())
        self.assertEqual(installed_bodies, expected_bodies)

    def test_w_se_m1_forward_installs_tables_guards_rules_and_derived_trigger_count(self):
        migration = importlib.import_module(
            "core.migrations.0046_se_entitlement_foundation"
        )
        original_tables = self._core_tables()
        self.assertEqual(migration.PREVIOUS_MIGRATION, PREVIOUS_MIGRATION[1])
        self.assertEqual(migration.DECLARED_TRIGGER_COUNT, len(migration.TRIGGER_SQL))
        self.assertEqual(migration.EXPECTED_TRIGGER_COUNT, 458 + len(migration.TRIGGER_SQL))
        migrate_to_target_with_restore(self, PREVIOUS_MIGRATION)
        self.assertEqual(
            self._core_tables(), original_tables - NEW_TABLES - LATER_TABLES
        )
        MigrationExecutor(connection).migrate([SE_MIGRATION])
        self._assert_0046_state()

        declared_triggers = set()
        for statement in migration.TRIGGER_SQL:
            name = statement.split("CREATE TRIGGER ", 1)[1].split(" ", 1)[0]
            declared_triggers.add(name)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT tgname FROM pg_trigger "
                "WHERE NOT tgisinternal AND tgname = ANY(%s)",
                [list(declared_triggers)],
            )
            self.assertEqual({row[0] for row in cursor.fetchall()}, declared_triggers)

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT body, source, authority_reference
                  FROM public.core_governedrule
                 WHERE code = 'BETA_COMPLIMENTARY_ACCESS' AND version = 1
                """
            )
            beta_body, beta_source, beta_authority = cursor.fetchone()
            self.assertEqual(
                json.loads(beta_body),
                json.loads(migration.BETA_RULE_BODY_JSON),
            )
            self.assertEqual(beta_source, "PLATFORM_RULING")
            self.assertIn("HG_RULING_HD_X_1_D1_CAPABILITY_SET_AND_DASHBOARD_20261011", beta_authority)
            cursor.execute(
                """
                SELECT body
                  FROM public.core_governedrule
                 WHERE code = 'FREE_TIER' AND version = 1
                """
            )
            free_body = cursor.fetchone()[0]
            self.assertEqual(
                json.loads(free_body),
                json.loads(migration.FREE_RULE_BODY_JSON),
            )

        declared_functions = dict(migration.SE_FUNCTION_BODIES)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT p.proname, p.prosrc "
                "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = 'public' AND p.proname = ANY(%s)",
                [list(declared_functions)],
            )
            installed_functions = dict(cursor.fetchall())
        self.assertEqual(set(installed_functions), set(declared_functions))
        self.assertEqual(installed_functions, declared_functions)

    def test_w_se_m2_reverse_and_success_cleanup_restore_0045_then_0046(self):
        original_tables = self._core_tables()
        migrate_to_target_with_restore(self, PREVIOUS_MIGRATION)
        self._assert_0045_state(original_tables - NEW_TABLES - LATER_TABLES)
        self.restore_received_migrations()
        self._assert_received_head_state()

    def test_w_se_m3_setup_failure_cleanup_restores_0046(self):
        original_tables = self._core_tables()
        target = PREVIOUS_MIGRATION

        class SetupFailure(unittest.TestCase):
            def setUp(inner_self):
                migrate_to_target_with_restore(inner_self, target)
                raise RuntimeError("intentional SE setup failure")

            def test_unreached(inner_self):
                inner_self.fail("setUp should stop before the test method")

        result = unittest.TestResult()
        unittest.defaultTestLoader.loadTestsFromTestCase(SetupFailure).run(result)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("intentional SE setup failure", result.errors[0][1])
        self.assertEqual(self._core_tables(), original_tables)
        self._assert_received_head_state()

    def test_w_se_m4_reverse_refuses_when_any_se_record_exists(self):
        migrate_to_target_with_restore(self, SE_MIGRATION)
        text = TEST_POLICY_TEXT
        OrganismEntitlementPolicyText.objects.create(
            policy_version=1,
            accepted_text=text,
            text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "0046 reverse refused while SE entitlement data exists",
        ):
            MigrationExecutor(connection).migrate([PREVIOUS_MIGRATION])
        self.assertIn(SE_MIGRATION, MigrationRecorder(connection).applied_migrations())
        self.assertTrue(OrganismEntitlementPolicyText.objects.filter(policy_version=1).exists())

    def test_w_se_m5_limits_guard_refuses_missing_dimensions_and_invalid_values(self):
        valid_limits = {
            "circles": {"kind": "LIMIT", "value": 0},
            "organism_learners": {"kind": "UNLIMITED"},
            "document_footprint": {"kind": "NOT_SET"},
        }
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT public.se_entitlement_limits_valid(%s::jsonb)",
                [json.dumps(valid_limits)],
            )
            self.assertIs(cursor.fetchone()[0], True)

        invalid_limits = (
            {
                "circles": {"kind": "UNLIMITED"},
                "organism_learners": {"kind": "NOT_SET"},
            },
            {
                "circles": {"kind": "LIMIT", "value": -1},
                "organism_learners": {"kind": "NOT_SET"},
                "document_footprint": {"kind": "NOT_SET"},
            },
        )
        for limits in invalid_limits:
            with self.subTest(limits=limits):
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT public.se_entitlement_limits_valid(%s::jsonb)",
                        [json.dumps(limits)],
                    )
                    self.assertIs(cursor.fetchone()[0], False)
                with self.assertRaises(DatabaseError):
                    with transaction.atomic():
                        with connection.cursor() as cursor:
                            cursor.execute(
                                """
                                INSERT INTO public.core_organismentitlement (
                                    entitlement_uuid, living_organism_id, source, basis,
                                    policy_reference, policy_version, capabilities, limits,
                                    effective_at, recorded_at
                                ) VALUES (%s, 0, 'COMPLIMENTARY', 'BETA_OPT_IN',
                                          'policy:v1', 1, '[]'::jsonb, %s::jsonb, %s, NULL)
                                """,
                                [uuid4(), json.dumps(limits), database_now()],
                            )


def _seed_beta_entitlement(route, *, effective_at):
    policy_version, _ = ensure_test_policy_text()
    policy = OrganismEntitlementPolicyText.objects.get(
        policy_version=policy_version
    )
    source_event = (
        LivingOrganismEvent.objects.filter(living_organism=route.organism)
        .order_by("-sequence")
        .first()
    )
    if source_event is None:
        raise AssertionError("the beta fixture route has no source event")
    with transaction.atomic():
        return route.birthing_service._entitlement_service._compose_beta_grant(
            actor=route.founder,
            organism=route.organism,
            policy=policy,
            effective_at=effective_at,
            received_at=database_now(),
            composed_from_event_uuid=source_event.event_uuid,
        )


def _se_record_counts():
    return (
        OrganismEntitlementPolicyText.objects.count(),
        OrganismEntitlement.objects.count(),
        OrganismEntitlementEvent.objects.count(),
        LOApplicationSettingsVersion.objects.count(),
        LivingOrganismEvent.objects.count(),
        OrganismCommandReceipt.objects.count(),
        AuthorityDecision.objects.count(),
    )


def _allowance_state():
    """Capture authoritative records that allowance and summary reads must not alter."""
    return (
        _table_state(Circle),
        _table_state(CircleStateEvent),
        _table_state(OrganismMembership),
        _table_state(OrganismMembershipTransition),
        _table_state(OrganismEntitlement),
        _table_state(OrganismEntitlementEvent),
        _table_state(OrganismEntitlementPolicyText),
        _table_state(LOApplicationSettingsVersion),
        _table_state(LivingOrganismEvent),
        _table_state(OrganismCommandReceipt),
        _table_state(AuthorityDecision),
    )


def _seed_entitlement_limits(route, *, effective_at, limits):
    """Seed a test-only entitlement with explicit, authored limit shapes."""
    policy_version, _ = ensure_test_policy_text()
    with declared_event_less_entitlement_rows(
        f"family 2: test-only entitlement limit shape {limits!r}"
    ):
        return OrganismEntitlement.objects.create(
            living_organism=route.organism,
            source="COMPLIMENTARY",
            basis="BETA_OPT_IN",
            policy_reference=f"policy-text:v{policy_version}",
            policy_version=policy_version,
            capabilities=list(SE_ENTITLEMENT_CAPABILITY_CODES),
            limits=limits,
            effective_at=effective_at,
            ending_condition_reference=(
                f"paid-availability-transition:{route.organism.organism_id}"
            ),
        )


def _table_state(model, **filters):
    """Capture every concrete field in a model's current rows."""
    rows = model.objects.filter(**filters) if filters else model.objects.all()
    field_names = tuple(field.attname for field in model._meta.concrete_fields)
    return tuple(rows.order_by("pk").values_list(*field_names))


def _gateway_guard_state():
    """Snapshot the gateway chain and all SE records affected by W-SE.GATE1."""
    return {
        "settings_versions": _table_state(LOApplicationSettingsVersion),
        "organism_events": _table_state(LivingOrganismEvent),
        "entitlements": _table_state(OrganismEntitlement),
        "entitlement_events": _table_state(OrganismEntitlementEvent),
        "policy_text": _table_state(OrganismEntitlementPolicyText),
        "command_receipts": _table_state(OrganismCommandReceipt),
        "authority_decisions": _table_state(AuthorityDecision),
    }


def _current_settings_version(route):
    """Resolve the settings version referenced by the latest settings action."""
    event = LivingOrganismEvent.objects.filter(
        living_organism=route.organism,
        action=OrganismAction.EFFECT_APPLICATION_SETTINGS.value,
    ).order_by("-sequence", "-pk").first()
    if event is None:
        raise AssertionError("gateway witness route has no settings action")
    version_uuid = event.evidence_reference.removeprefix(
        "application-settings-version:"
    )
    return LOApplicationSettingsVersion.objects.get(version_uuid=version_uuid)


def _new_gateway_version(
    route,
    *,
    action,
    include_settings_payload,
    include_election_payload,
):
    """Create a version row whose payload is selected by the SQL witness."""
    now = database_now()
    settings_version = (
        _current_settings_version(route)
        if action is OrganismAction.EFFECT_APPLICATION_SETTINGS
        else None
    )
    return LOApplicationSettingsVersion.objects.create(
        living_organism=route.organism,
        actor=route.founder,
        enterer=route.founder,
        supersedes=settings_version,
        occurred_at=now,
        effective_at=now,
        received_at=now,
        applications_open=True if include_settings_payload else None,
        automatic_acceptance=False if include_settings_payload else None,
        processing_responsibility="LO" if include_settings_payload else None,
        entitlement_choice="FREE" if include_election_payload else None,
        entitlement_election_route="BIRTHING" if include_election_payload else None,
        entitlement_policy_version=TEST_POLICY_VERSION if include_election_payload else None,
        entitlement_policy_text_sha256=(
            TEST_POLICY_SHA256 if include_election_payload else None
        ),
        entitlement_authority_source=(
            "test:SE-GATE-01" if include_election_payload else None
        ),
        entitlement_authority_version="1" if include_election_payload else None,
    )


def _insert_gateway_event_directly(route, action, version):
    """Insert a gateway action through SQL, bypassing the application service."""
    route.organism.refresh_from_db()
    predecessor = LivingOrganismEvent.objects.get(pk=route.organism.head_event_id)
    at = database_now()
    with connection.cursor() as cursor:
        event_id, _ = append_event(
            cursor,
            "core_livingorganismevent",
            route.organism.pk,
            predecessor.sequence + 1,
            action.value,
            predecessor.resulting_state,
            predecessor.resulting_state,
            None,
            route.organism.founding_authority_basis_id,
            f"se-gate:{uuid4()}",
            pred_id=predecessor.pk,
            eff=at,
            occ=at,
            rec=at,
            extra={
                "actor_state": "LIVING_ORGANISM",
                "actor_access_epoch": None,
                "enterer_identity_id": route.founder.pk,
                "evidence_reference": (
                    f"application-settings-version:{version.version_uuid}"
                ),
            },
        )
        refresh(cursor, "core_livingorganism", route.organism.pk, at)
    return LivingOrganismEvent.objects.get(pk=event_id)


class SEEntitlementGatewayGuardTests(TransactionTestCase):
    """Direct-SQL witnesses for the SE-GATE-01 action-payload boundary."""

    reset_sequences = True

    def test_w_se_gate1_rejects_cross_kind_gateway_payloads_without_writes(self):
        """W-SE.GATE1 rejects both prohibited action/payload combinations."""
        route = prepare_route()
        ensure_test_policy_text()
        settings_with_election_payload = _new_gateway_version(
            route,
            action=OrganismAction.EFFECT_APPLICATION_SETTINGS,
            include_settings_payload=True,
            include_election_payload=True,
        )
        election_with_settings_payload = _new_gateway_version(
            route,
            action=OrganismAction.EFFECT_ENTITLEMENT_ELECTION,
            include_settings_payload=True,
            include_election_payload=True,
        )
        before = _gateway_guard_state()

        for action, version in (
            (
                OrganismAction.EFFECT_APPLICATION_SETTINGS,
                settings_with_election_payload,
            ),
            (
                OrganismAction.EFFECT_ENTITLEMENT_ELECTION,
                election_with_settings_payload,
            ),
        ):
            with self.subTest(action=action.value):
                with self.assertRaises(DatabaseError) as refused:
                    with transaction.atomic():
                        _insert_gateway_event_directly(route, action, version)
                self.assertIn(
                    "S3FU gateway effect: version incomplete",
                    str(refused.exception),
                )
                self.assertEqual(_gateway_guard_state(), before)

    def test_w_se_gate2_accepts_valid_settings_and_election_payloads(self):
        """HG_DECISION_SE_GATE_01_FIX_IN_SE_20261012 separates the two valid payloads."""
        route = prepare_route()
        ensure_test_policy_text()
        birth(route, suffix="se-gate2-real-election")
        election_event = LivingOrganismEvent.objects.get(
            living_organism=route.organism,
            action=OrganismAction.EFFECT_ENTITLEMENT_ELECTION.value,
        )
        election_version_uuid = election_event.evidence_reference.removeprefix(
            "application-settings-version:"
        )
        election_version = LOApplicationSettingsVersion.objects.get(
            version_uuid=election_version_uuid,
        )
        settings_version = _new_gateway_version(
            route,
            action=OrganismAction.EFFECT_APPLICATION_SETTINGS,
            include_settings_payload=True,
            include_election_payload=False,
        )
        settings_event = _insert_gateway_event_directly(
            route,
            OrganismAction.EFFECT_APPLICATION_SETTINGS,
            settings_version,
        )

        self.assertEqual(
            settings_event.action,
            OrganismAction.EFFECT_APPLICATION_SETTINGS.value,
        )
        self.assertEqual(
            election_event.action,
            OrganismAction.EFFECT_ENTITLEMENT_ELECTION.value,
        )
        self.assertIsNone(settings_version.entitlement_choice)
        self.assertEqual(election_version.entitlement_choice, "FREE")
        self.assertEqual(election_version.entitlement_election_route, "BIRTHING")
        self.assertIsNotNone(election_version.entitlement_policy_version)
        self.assertIsNotNone(election_version.entitlement_policy_text_sha256)
        self.assertIsNotNone(election_version.entitlement_authority_source)
        self.assertIsNotNone(election_version.entitlement_authority_version)
        self.assertIsNone(election_version.applications_open)
        self.assertIsNone(election_version.automatic_acceptance)
        self.assertIsNone(election_version.processing_responsibility)


def _insert_extension_event(entitlement, actor, received_at):
    first_grant = OrganismEntitlementEvent.objects.get(
        entitlement=entitlement,
        sequence=1,
        event_type="GRANTED",
    )
    return OrganismEntitlementEvent.objects.create(
        entitlement=entitlement,
        sequence=2,
        predecessor=first_grant,
        event_type="EXTENDED",
        actor_state="PARTY",
        actor=actor,
        actor_access_epoch=actor.access_epoch,
        actor_capacity="STANDING",
        entry_mode="TRANSPORT",
        enterer_identity=actor,
        composing_rule_state="NA_TRANSPORT",
        composing_rule=None,
        composing_rule_reference=None,
        composer_state="NA_TRANSPORT",
        composer_identity=None,
        composed_from_event_uuid=None,
        deciding_identity=actor,
        authoriser_identity=actor,
        authoriser_office="RELATIONSHIP_CIRCLE_COORDINATOR",
        reason="labelled direct-SQL guard witness",
        occurred_at=received_at,
        effective_at=received_at,
        received_at=received_at,
    )


class SEEntitlementExtensionGuardTests(TransactionTestCase):
    reset_sequences = True

    def test_w_se_e4_self_extension_is_t3_without_writes(self):
        route = prepare_route()
        received_at = database_now()
        entitlement = _seed_beta_entitlement(route, effective_at=received_at)

        before = _se_record_counts()
        before_decisions = _table_state(AuthorityDecision)
        with self.assertRaises(DatabaseError) as self_extension:
            with transaction.atomic():
                _insert_extension_event(entitlement, route.founder, received_at)
        self.assertIn("SE_SELF_EXTENSION", str(self_extension.exception))
        self.assertEqual(_se_record_counts(), before)
        self.assertEqual(_table_state(AuthorityDecision), before_decisions)


    def test_w_se_e4_direct_sql_nonmember_is_t2_without_writes(self):
        """Prove that a non-office-holder cannot append an extension."""
        route = prepare_route()
        received_at = database_now()
        entitlement = _seed_beta_entitlement(route, effective_at=received_at)
        with connection.cursor() as cursor:
            nonmember_id = seed_identity(cursor, f"se-extension-nonmember-{uuid4()}")
        nonmember = Identity.objects.get(pk=nonmember_id)

        before = _se_record_counts()
        before_decisions = _table_state(AuthorityDecision)
        with self.assertRaises(DatabaseError) as unauthorized_extension:
            with transaction.atomic():
                _insert_extension_event(entitlement, nonmember, received_at)
        self.assertIn("SE_EXTENSION_AUTHORITY", str(unauthorized_extension.exception))
        self.assertEqual(_se_record_counts(), before)
        self.assertEqual(_table_state(AuthorityDecision), before_decisions)


    def test_w_se_e4_service_refuses_with_t4_before_writes(self):
        route = prepare_route()
        service = OrganismEntitlementService(
            authority=route.birthing_service._authority,
            clock=database_now,
        )
        command = EntitlementExtensionCommand(
            credential=route.founder.credential,
            entitlement_uuid=uuid4(),
            request_reference=f"se-extension:{route.tag}",
            idempotency_key=f"se-extension:key:{route.tag}",
            reason="labelled T-4 service refusal witness",
        )
        before = _se_record_counts()
        before_decisions = _table_state(AuthorityDecision)
        with self.assertRaises(S015EntitlementRefused) as unavailable:
            service.extend_entitlement(command)
        self.assertIs(
            unavailable.exception.refusal_code,
            S015AuthorityRefusalCode.SE_EXTENSION_ROUTE_UNAVAILABLE,
        )
        self.assertEqual(
            str(unavailable.exception),
            "Extensions to beta access are not yet available in this release. "
            "Nothing has changed.",
        )
        self.assertEqual(_se_record_counts(), before)
        self.assertEqual(_table_state(AuthorityDecision), before_decisions)
        self.assertFalse(
            AuthorityDecision.objects.filter(
                request_reference=command.request_reference
            ).exists()
        )


class SEEntitlementAllowanceDashboardTests(TransactionTestCase):
    """W-SE.A1 and W-SE.D1 allowance and dashboard data-contract witnesses."""

    ISOLATION_FULL_CAPTURE = True

    reset_sequences = True

    def _read_only(self, operation):
        """W-K2.A5: compare all allowance-domain state around one read."""
        before = _allowance_state()
        result = operation()
        self.assertEqual(_allowance_state(), before)
        return result

    def test_w_se_a1_allowance_is_read_only_and_distinguishes_t8_from_t12(self):
        """Assert authoritative measurements, authored messages, and no writes."""
        free_route = prepare_route()
        zero_route = prepare_route()
        unlimited_route = prepare_route()
        birth(free_route)
        birth(zero_route)
        birth(unlimited_route)
        at = database_now()
        self.assertEqual(
            Circle.objects.filter(parent_organism=free_route.organism).count(),
            0,
        )
        self.assertEqual(
            Circle.objects.filter(parent_organism=zero_route.organism).count(),
            0,
        )
        _seed_entitlement_limits(
            free_route,
            effective_at=at - timedelta(seconds=1),
            limits={
                "circles": {"kind": "LIMIT", "value": 1},
                "organism_learners": {"kind": "NOT_SET"},
                "document_footprint": {"kind": "NOT_SET"},
            },
        )
        _seed_entitlement_limits(
            zero_route,
            effective_at=at - timedelta(seconds=1),
            limits={
                "circles": {"kind": "LIMIT", "value": 0},
                "organism_learners": {"kind": "NOT_SET"},
                "document_footprint": {"kind": "LIMIT", "value": 1},
            },
        )
        _seed_entitlement_limits(
            unlimited_route,
            effective_at=at - timedelta(seconds=1),
            limits={
                "circles": {"kind": "UNLIMITED"},
                "organism_learners": {"kind": "NOT_SET"},
                "document_footprint": {"kind": "NOT_SET"},
            },
        )
        within = self._read_only(
            lambda: check_allowance(
                free_route.organism.organism_id,
                "circles",
                1,
                at,
            )
        )
        self.assertEqual(within.status, "within")
        self.assertEqual(within.limit_kind, "LIMIT")
        self.assertEqual(
            within.measured_usage,
            Circle.objects.filter(parent_organism=free_route.organism).count(),
        )
        self.assertEqual(
            within.proposed_total,
            within.measured_usage + within.increment,
        )

        exceeded = self._read_only(
            lambda: check_allowance(
                free_route.organism.organism_id,
                "circles",
                2,
                at,
            )
        )
        self.assertEqual(exceeded.status, "exceeds")
        self.assertEqual(exceeded.measured_usage, 0)
        self.assertEqual(exceeded.proposed_total, 2)
        self.assertIs(
            exceeded.code,
            S015AuthorityRefusalCode.SE_ALLOWANCE_EXCEEDED,
        )
        self.assertEqual(
            exceeded.message,
            "Your organism's current allowance is 1 Circle. This action would "
            "bring the total to 2 Circles. Nothing has changed.",
        )

        zero_limit_exceeds = self._read_only(
            lambda: check_allowance(
                zero_route.organism.organism_id,
                "circles",
                1,
                at,
            )
        )
        self.assertEqual(zero_limit_exceeds.status, "exceeds")
        self.assertEqual(zero_limit_exceeds.measured_usage, 0)
        self.assertEqual(zero_limit_exceeds.proposed_total, 1)
        self.assertIn("total to 1 Circle.", zero_limit_exceeds.message)

        zero_limit_within = self._read_only(
            lambda: check_allowance(
                zero_route.organism.organism_id,
                "circles",
                0,
                at,
            )
        )
        self.assertEqual(zero_limit_within.status, "within")
        self.assertEqual(zero_limit_within.proposed_total, 0)

        with CaptureQueriesContext(connection) as early_return_queries:
            with patch(
                "src.intevia.services.organism_entitlement_service._measure_usage",
                side_effect=AssertionError("early-return dimensions must not be measured"),
            ) as failed_measurement:
                not_set = self._read_only(
                    lambda: check_allowance(
                        free_route.organism.organism_id,
                        "organism_learners",
                        1,
                        at,
                    )
                )
                unlimited = self._read_only(
                    lambda: check_allowance(
                        unlimited_route.organism.organism_id,
                        "circles",
                        100,
                        at,
                    )
                )
        failed_measurement.assert_not_called()
        self.assertFalse(
            any(
                "s015_eligible_event_ids" in query["sql"]
                for query in early_return_queries.captured_queries
            )
        )

        self.assertEqual(not_set.status, "within")
        self.assertEqual(not_set.limit_kind, "NOT_SET")
        self.assertIsNone(not_set.measured_usage)
        self.assertIsNone(not_set.proposed_total)
        self.assertEqual(unlimited.status, "within")
        self.assertEqual(unlimited.limit_kind, "UNLIMITED")
        self.assertIsNone(unlimited.measured_usage)
        self.assertIsNone(unlimited.proposed_total)

        document_limit = self._read_only(
            lambda: check_allowance(
                zero_route.organism.organism_id,
                "document_footprint",
                0,
                at,
            )
        )
        self.assertEqual(document_limit.status, "cannot_check")
        self.assertIsNone(document_limit.proposed_total)
        self.assertTrue(document_limit.technical_failure)
        self.assertIs(
            document_limit.code,
            S015AuthorityRefusalCode.SE_USAGE_UNCHECKABLE,
        )

        with patch(
            "src.intevia.services.organism_entitlement_service._measure_usage",
            side_effect=S015EntitlementTechnicalFailure("test measurement failure"),
        ) as failed_measurement:
            uncheckable = self._read_only(
                lambda: check_allowance(
                    free_route.organism.organism_id,
                    "circles",
                    1,
                    at,
                )
            )
        self.assertEqual(uncheckable.status, "cannot_check")
        self.assertTrue(uncheckable.technical_failure)
        self.assertIs(
            uncheckable.code,
            S015AuthorityRefusalCode.SE_USAGE_UNCHECKABLE,
        )
        self.assertEqual(failed_measurement.call_count, 1)
        self.assertEqual(
            uncheckable.message,
            "We could not check your organism's usage of Circles against its "
            "allowance of 1 Circle, so this action was not completed. Nothing has "
            "changed. Please try again later.",
        )

    def test_w_se_d1_summary_carries_units_basis_and_all_display_variants(self):
        """Assert T-9 through T-11 and the three T-9a variants."""
        route = prepare_route()
        birth(route)
        at = database_now()
        free = self._read_only(
            lambda: entitlement_summary(route.organism.organism_id, at=at)
        )
        free_dimensions = free["dimensions"]

        learners = free_dimensions["organism_learners"]
        self.assertEqual(learners["kind"], "NOT_SET")
        self.assertIsInstance(learners["measured_usage"], int)
        self.assertEqual(learners["not_yet_known"], 0)
        self.assertEqual(learners["unit"], "Learners")
        self.assertEqual(
            learners["measurement_basis"],
            "Learners with an active or probationary membership",
        )
        self.assertIn("in use. No allowance has been set", learners["display_line"])
        self.assertIn(
            "Counted as Learners with an active or probationary membership.",
            learners["display_line"],
        )

        document = free_dimensions["document_footprint"]
        self.assertEqual(document["kind"], "NOT_SET")
        self.assertIsNone(document["measured_usage"])
        self.assertIsNone(document["not_yet_known"])
        self.assertEqual(document["unit"], "B")
        self.assertEqual(
            document["measurement_reason_code"],
            "UNMEASURABLE",
        )
        self.assertEqual(
            document["display_line"],
            "document footprint: usage is currently unknown because it could not be "
            "measured. No allowance has been set; no limit is currently enforced for "
            "this measure.",
        )
        self.assertNotIn("0", document["display_line"])

        at_beta = at + timedelta(seconds=2)
        _seed_entitlement_limits(
            route,
            effective_at=at + timedelta(seconds=1),
            limits={
                "circles": {"kind": "LIMIT", "value": 9},
                "organism_learners": {"kind": "UNLIMITED"},
                "document_footprint": {"kind": "NOT_SET"},
            },
        )
        beta = self._read_only(
            lambda: entitlement_summary(route.organism.organism_id, at=at_beta)
        )
        dimensions = beta["dimensions"]
        self.assertEqual(beta["package"], "BETA_COMPLIMENTARY_ACCESS")
        self.assertEqual(beta["source"], "COMPLIMENTARY")
        self.assertEqual(dimensions["circles"]["kind"], "LIMIT")
        self.assertIn("of 9 Circles in use.", dimensions["circles"]["display_line"])
        self.assertEqual(dimensions["organism_learners"]["kind"], "UNLIMITED")
        self.assertIn(
            "unlimited — ",
            dimensions["organism_learners"]["display_line"],
        )

        with patch(
            "src.intevia.services.organism_entitlement_service._measure_usage",
            side_effect=S015EntitlementTechnicalFailure("test measurement failure"),
        ):
            failed = self._read_only(
                lambda: entitlement_summary(
                    route.organism.organism_id,
                    at=at_beta,
                )
            )["dimensions"]

        self.assertEqual(
            failed["circles"]["display_line"],
            "Circles: usage is currently unknown because it could not be measured. "
            "Allowance: 9 Circles.",
        )
        self.assertEqual(
            failed["organism_learners"]["display_line"],
            "Learners: unlimited. Usage is currently unknown because it could not "
            "be measured.",
        )
        self.assertEqual(
            failed["document_footprint"]["display_line"],
            document["display_line"],
        )
        still_within = self._read_only(
            lambda: check_allowance(
                route.organism.organism_id,
                "circles",
                8,
                at_beta,
            )
        )
        self.assertEqual(still_within.status, "within")

    def test_w_se_dashboard_capabilities_separate_availability_from_undelivered_status(self):
        """Report entitlement availability without inventing owner-slice state."""
        route = prepare_route()
        at = database_now()
        _seed_entitlement_limits(
            route,
            effective_at=at - timedelta(seconds=1),
            limits={
                "circles": {"kind": "LIMIT", "value": 9},
                "organism_learners": {"kind": "NOT_SET"},
                "document_footprint": {"kind": "NOT_SET"},
            },
        )

        status = capability_status(route.organism.organism_id, at=at)
        self.assertEqual(set(status), set(SE_ENTITLEMENT_CAPABILITY_CODES))
        for capability in SE_ENTITLEMENT_CAPABILITY_CODES:
            self.assertTrue(status[capability]["available"])
            self.assertIsNone(status[capability]["enabled"])
            self.assertIsNone(status[capability]["in_use"])
            self.assertEqual(
                status[capability]["enabled_reason_code"],
                "NOT_YET_DELIVERED",
            )
            self.assertEqual(
                status[capability]["in_use_reason_code"],
                "NOT_YET_DELIVERED",
            )
            self.assertNotIn("slice", status[capability])

def _birthing_command(route, *, offering_choice, suffix):
    policy_version, policy_text_sha256 = ensure_test_policy_text()
    return BirthingCommand(
        credential=route.founder.credential,
        living_organism_uuid=route.organism.organism_id,
        request_reference=f"se-{suffix}:{route.tag}",
        idempotency_key=f"se-{suffix}:key:{route.tag}",
        effective_at=database_now(),
        offering_choice=offering_choice,
        policy_version=policy_version,
        policy_text_sha256=policy_text_sha256,
    )


def _beta_intake(route, command):
    """The opt-in's one intake row, read back by (actor, entry, key) (PKT-CLK-3 CLK3-2)."""
    from core.models import CommandIntake

    return CommandIntake.objects.get(
        actor=route.founder,
        entry="ACTIVATE_BETA",
        idempotency_key=command.idempotency_key,
    )


def _intake_count():
    from core.models import CommandIntake

    return CommandIntake.objects.count()


def _received(intake):
    from src.intevia.services.organism_authority import canonical_timestamp

    return canonical_timestamp(intake.received_at)


def _election_command(route, *, suffix):
    policy_version, policy_text_sha256 = ensure_test_policy_text()
    return EntitlementElectionCommand(
        credential=route.founder.credential,
        living_organism_uuid=route.organism.organism_id,
        request_reference=f"se-{suffix}:{route.tag}",
        idempotency_key=f"se-{suffix}:key:{route.tag}",
        occurred_at=database_now(),
        policy_version=policy_version,
        policy_text_sha256=policy_text_sha256,
    )


def _entitlement_domain_counts():
    return (
        OrganismEntitlement.objects.count(),
        OrganismEntitlementEvent.objects.count(),
        LOApplicationSettingsVersion.objects.filter(
            entitlement_election_route="POST_BIRTHING"
        ).count(),
        LivingOrganismEvent.objects.filter(
            action=OrganismAction.EFFECT_ENTITLEMENT_ELECTION.value
        ).count(),
        OrganismCommandReceipt.objects.filter(
            action=OrganismAction.EFFECT_ENTITLEMENT_ELECTION.value
        ).count(),
    )


class SEEntitlementCommandTests(TransactionTestCase):
    reset_sequences = True

    def test_w_se_e1_birthing_records_free_or_composes_beta_and_receipt(self):
        """Birthing commits either the free choice or its governed beta grant."""
        free_route = prepare_route()
        with disclose_authority_refusal_code():
            free_result = free_route.birthing_service.birth_living_organism(
                _birthing_command(free_route, offering_choice="FREE", suffix="free")
            )
        self.assertEqual(free_result.offering_choice, "FREE")
        self.assertEqual(free_result.policy_version, TEST_POLICY_VERSION)
        self.assertEqual(free_result.policy_text_sha256, TEST_POLICY_SHA256)
        self.assertIsNone(free_result.entitlement_uuid)
        self.assertIsNotNone(free_result.entitlement_event_uuid)
        free_election = LivingOrganismEvent.objects.get(
            event_uuid=free_result.entitlement_event_uuid
        )
        free_version = LOApplicationSettingsVersion.objects.get(
            version_uuid=free_election.evidence_reference.removeprefix(
                "application-settings-version:"
            ),
            entitlement_choice__isnull=False,
            entitlement_election_route__isnull=False,
        )
        self.assertEqual(free_version.entitlement_choice, "FREE")
        self.assertEqual(free_version.entitlement_election_route, "BIRTHING")
        self.assertEqual(free_version.entitlement_policy_version, TEST_POLICY_VERSION)
        self.assertEqual(free_version.entitlement_policy_text_sha256, TEST_POLICY_SHA256)
        self.assertFalse(
            entitlement_effective(
                free_route.organism.organism_id,
                "APPLICATION_CIRCLE",
                at=free_election.effective_at,
            )
        )
        self.assertEqual(
            entitlement_limit(
                free_route.organism.organism_id,
                "circles",
                at=free_election.effective_at,
            ),
            ("LIMIT", 9),
        )

        beta_route = prepare_route()
        with disclose_authority_refusal_code():
            beta_result = beta_route.birthing_service.birth_living_organism(
                _birthing_command(
                    beta_route,
                    offering_choice="COMPLIMENTARY_BETA",
                    suffix="beta",
                )
            )
        beta_entitlement = OrganismEntitlement.objects.get(
            entitlement_uuid=beta_result.entitlement_uuid
        )
        beta_event = OrganismEntitlementEvent.objects.get(
            entitlement=beta_entitlement,
            event_type="GRANTED",
        )
        beta_election = LivingOrganismEvent.objects.get(
            event_uuid=beta_result.entitlement_event_uuid
        )
        beta_version = LOApplicationSettingsVersion.objects.get(
            version_uuid=beta_election.evidence_reference.removeprefix(
                "application-settings-version:"
            ),
            entitlement_choice__isnull=False,
            entitlement_election_route__isnull=False,
        )
        beta_rule = GovernedRule.objects.get(code="BETA_COMPLIMENTARY_ACCESS")
        self.assertEqual(beta_entitlement.source, "COMPLIMENTARY")
        self.assertEqual(beta_entitlement.basis, "BETA_OPT_IN")
        self.assertEqual(beta_entitlement.effective_at, beta_election.effective_at)
        self.assertIsNone(beta_entitlement.effective_until)
        self.assertEqual(
            beta_entitlement.capabilities,
            beta_rule.body["capabilities"],
        )
        self.assertEqual(beta_entitlement.limits, beta_rule.body["limits"])
        self.assertEqual(beta_event.entry_mode, "COMPOSED")
        self.assertEqual(beta_event.actor_state, "NONE")
        self.assertEqual(beta_event.composing_rule, beta_rule)
        self.assertEqual(beta_event.composed_from_event_uuid, beta_result.event_uuid)
        self.assertEqual(beta_version.entitlement_policy_version, TEST_POLICY_VERSION)
        self.assertEqual(beta_version.entitlement_policy_text_sha256, TEST_POLICY_SHA256)
        self.assertTrue(
            beta_version.entitlement_authority_source
            and beta_version.entitlement_authority_version
        )
        self.assertTrue(
            entitlement_effective(
                beta_route.organism.organism_id,
                "APPLICATION_CIRCLE",
                at=beta_election.effective_at,
            )
        )
        receipt = OrganismCommandReceipt.objects.get(
            actor=beta_route.founder,
            action=OrganismAction.TRANSITION_LIVING_ORGANISM.value,
            idempotency_key=f"se-beta:key:{beta_route.tag}",
        )
        self.assertEqual(receipt.result_payload["offering_choice"], "COMPLIMENTARY_BETA")
        self.assertEqual(
            receipt.result_payload["policy_text_sha256"],
            TEST_POLICY_SHA256,
        )
        self.assertEqual(
            receipt.result_payload["entitlement_uuid"],
            str(beta_entitlement.entitlement_uuid),
        )
        self.assertEqual(
            receipt.result_payload["entitlement_event_uuid"],
            str(beta_election.event_uuid),
        )
        before_history = _se_record_counts()
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                OrganismEntitlement.objects.filter(pk=beta_entitlement.pk).update(
                    policy_reference="fixture:overwrite-refused"
                )
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                OrganismEntitlementEvent.objects.filter(pk=beta_event.pk).update(
                    reason="fixture history overwrite refused"
                )
        beta_entitlement.refresh_from_db()
        beta_event.refresh_from_db()
        self.assertEqual(
            beta_entitlement.policy_reference,
            f"policy-text:v{TEST_POLICY_VERSION}",
        )
        self.assertIsNone(beta_event.reason)
        self.assertEqual(_se_record_counts(), before_history)

    def test_w_se_e2_post_birthing_opt_in_is_prospective_once_and_t5_precedes_t7(self):
        """A post-Birthing opt-in remains prospective and occurs after T-5."""
        route = prepare_route()
        with disclose_authority_refusal_code():
            free_result = route.birthing_service.birth_living_organism(
                _birthing_command(route, offering_choice="FREE", suffix="e2-free")
            )
        free_event = LivingOrganismEvent.objects.get(event_uuid=free_result.entitlement_event_uuid)
        free_version_uuid = free_event.evidence_reference.removeprefix(
            "application-settings-version:"
        )
        free_version = LOApplicationSettingsVersion.objects.get(
            version_uuid=free_version_uuid,
            entitlement_choice__isnull=False,
            entitlement_election_route__isnull=False,
        )
        command = _election_command(route, suffix="e2-opt-in")
        service = OrganismEntitlementService(
            authority=route.birthing_service._authority,
            clock=database_now,
        )
        with disclose_authority_refusal_code():
            result = service.activate_beta(command)
        election_event = LivingOrganismEvent.objects.get(event_uuid=result.event_uuid)
        election_version = LOApplicationSettingsVersion.objects.get(
            version_uuid=election_event.evidence_reference.removeprefix(
                "application-settings-version:"
            ),
            entitlement_choice__isnull=False,
            entitlement_election_route__isnull=False,
        )
        version_recorded_at = election_version.recorded_at
        self.assertIsNotNone(version_recorded_at)
        if version_recorded_at is None:
            self.fail("the entitlement election version has no database record time")
        self.assertGreater(version_recorded_at, election_event.received_at)
        decision = AuthorityDecision.objects.get(
            decision_reference=election_event.authority_decision_reference
        )
        entitlement = OrganismEntitlement.objects.get(
            entitlement_uuid=result.entitlement_uuid
        )
        self.assertEqual(election_event.action, OrganismAction.EFFECT_ENTITLEMENT_ELECTION.value)
        self.assertEqual(election_event.effective_at, election_event.received_at)
        self.assertEqual(
            decision.evaluated_at,
            election_event.received_at,
        )
        self.assertEqual(election_version.entitlement_choice, "COMPLIMENTARY_BETA")
        self.assertEqual(election_version.entitlement_election_route, "POST_BIRTHING")
        self.assertEqual(election_version.supersedes_id, free_version.pk)
        self.assertEqual(free_version.entitlement_choice, "FREE")
        self.assertEqual(free_version.entitlement_election_route, "BIRTHING")
        self.assertEqual(
            LivingOrganismEvent.objects.filter(
                living_organism=route.organism,
                action=OrganismAction.EFFECT_ENTITLEMENT_ELECTION.value,
            ).count(),
            2,
        )
        self.assertEqual(entitlement.effective_at, election_event.effective_at)
        self.assertIsNone(entitlement.effective_until)
        self.assertEqual(
            OrganismEntitlement.objects.filter(
                living_organism=route.organism,
                basis="BETA_OPT_IN",
            ).count(),
            1,
        )

        after_first = _entitlement_domain_counts()
        after_first_decisions = _table_state(AuthorityDecision)
        self.assertEqual(service.activate_beta(command), result)
        self.assertEqual(_entitlement_domain_counts(), after_first)
        self.assertEqual(_table_state(AuthorityDecision), after_first_decisions)

        closed_offer_service = OrganismEntitlementService(
            authority=route.birthing_service._authority,
            clock=database_now,
            availability_source=TestFixturePaidAvailabilitySource(
                f"fixture-paid-availability:{route.tag}"
            ),
        )
        second_command = _election_command(route, suffix="e2-second-opt-in")
        intake_before = _intake_count()
        with self.assertRaises(S015EntitlementRefused) as already_beta:
            closed_offer_service.activate_beta(second_command)
        self.assertEqual(_intake_count(), intake_before + 1)
        self.assertIs(
            already_beta.exception.refusal_code,
            S015AuthorityRefusalCode.SE_ALREADY_BETA,
        )
        self.assertEqual(
            str(already_beta.exception),
            "Your organism already has beta access; no governed record was written. "
            "INTEVIA kept a record that this request was received at "
            f"{_received(_beta_intake(route, second_command))}; sent again unchanged, "
            "it is answered as of that time.",
        )
        self.assertEqual(_entitlement_domain_counts(), after_first)
        self.assertEqual(_table_state(AuthorityDecision), after_first_decisions)
        self.assertFalse(
            AuthorityDecision.objects.filter(
                request_reference=second_command.request_reference
            ).exists()
        )

    def test_w_se_e6_closed_offer_fixture_uses_t7_and_persists_no_governed_record(self):
        """The T-7 fixture closes the offer without persisting an opt-in; after activation the intake record of the refused request persists."""
        route = prepare_route()
        with disclose_authority_refusal_code():
            route.birthing_service.birth_living_organism(
                _birthing_command(route, offering_choice="FREE", suffix="e6-free")
            )
        service = OrganismEntitlementService(
            authority=route.birthing_service._authority,
            clock=database_now,
            availability_source=TestFixturePaidAvailabilitySource(
                f"fixture-paid-availability:{route.tag}"
            ),
        )
        before = _entitlement_domain_counts()
        before_decisions = _table_state(AuthorityDecision)
        closed_command = _election_command(route, suffix="e6-closed")
        intake_before = _intake_count()
        with self.assertRaises(S015EntitlementRefused) as offer_closed:
            service.activate_beta(closed_command)
        self.assertEqual(_intake_count(), intake_before + 1)
        self.assertIs(
            offer_closed.exception.refusal_code,
            S015AuthorityRefusalCode.SE_OFFER_CLOSED,
        )
        self.assertEqual(
            str(offer_closed.exception),
            "The beta offer is no longer open to your organism; no governed record was "
            "written. The free offering continues, and subscriptions will be offered when "
            "they become available. INTEVIA kept a record that this request was received at "
            f"{_received(_beta_intake(route, closed_command))}; sent again unchanged, it is "
            "answered as of that time.",
        )
        self.assertEqual(_entitlement_domain_counts(), before)
        self.assertEqual(_table_state(AuthorityDecision), before_decisions)
        self.assertFalse(
            AuthorityDecision.objects.filter(
                request_reference=closed_command.request_reference
            ).exists()
        )

    def test_w_se_e3_post_birthing_opt_in_requires_cq_and_writes_no_entitlement(self):
        """A post-Birthing opt-in requiring CQ is refused before SE writes; exactly one REFUSED decision persists and, after activation, the intake record of the request."""
        route = prepare_route()
        with disclose_authority_refusal_code():
            route.birthing_service.birth_living_organism(
                _birthing_command(route, offering_choice="FREE", suffix="e3-free")
            )
        # TEST FIXTURE — this stands in for the configuration route CQ will deliver;
        # it establishes the rules for this witness without claiming to test delivery.
        with transaction.atomic(), connection.cursor() as cursor:
            predecessor = (
                LivingOrganismEvent.objects.filter(living_organism=route.organism)
                .order_by("-sequence")
                .first()
            )
            if predecessor is None:
                raise RuntimeError("the post-Birthing CQ fixture has no event predecessor")
            fixture_at = database_now()
            configuration = LOGovernanceConfigurationVersion.objects.create(
                living_organism=route.organism,
                actor=route.founder,
                enterer=route.founder,
                supersedes=LOGovernanceConfigurationVersion.objects.get(
                    living_organism=route.organism
                ),
                occurred_at=fixture_at,
                effective_at=fixture_at,
                received_at=fixture_at,
                cq_active=True,
                governance_enabled=False,
                inherit_portal_cq_settings=False,
                require_cq_to_change_lo_settings=True,
                require_cq_to_change_application_question=False,
            )
            append_event(
                cursor,
                "core_livingorganismevent",
                route.organism.pk,
                predecessor.sequence + 1,
                OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION.value,
                predecessor.resulting_state,
                predecessor.resulting_state,
                None,
                route.organism.founding_authority_basis_id,
                f"se-e3-cq:{route.tag}",
                pred_id=predecessor.pk,
                eff=fixture_at,
                occ=fixture_at,
                rec=fixture_at,
                extra={
                    "actor_state": "LIVING_ORGANISM",
                    "actor_id": None,
                    "actor_access_epoch": None,
                    "actor_capacity": "STANDING",
                    "enterer_identity_id": route.founder.pk,
                    "evidence_reference": (
                        "governance-configuration-version:"
                        f"{configuration.version_uuid}"
                    ),
                },
            )
            refresh(
                cursor,
                "core_livingorganism",
                route.organism.pk,
                fixture_at,
            )
        service = OrganismEntitlementService(
            authority=route.birthing_service._authority,
            clock=database_now,
        )
        before = _se_record_counts()
        before_decisions = _table_state(AuthorityDecision)
        election_command = _election_command(route, suffix="e3-cq")
        intake_before = _intake_count()
        with self.assertRaises(S015AuthorityDenied) as collective_approval:
            with disclose_authority_refusal_code():
                service.activate_beta(election_command)
        self.assertEqual(_intake_count(), intake_before + 1)
        self.assertIs(
            collective_approval.exception.refusal_code,
            S015AuthorityRefusalCode.SE_OPT_IN_NEEDS_CQ,
        )
        self.assertIs(
            collective_approval.exception.disclosed_refusal_code,
            S015AuthorityRefusalCode.SE_OPT_IN_NEEDS_CQ,
        )
        self.assertEqual(
            str(collective_approval.exception),
            "Under your organism's rules, opting into beta access after Birthing "
            "requires collective approval through CQ. That process is not yet "
            "available for your organism. INTEVIA recorded this refusal and that the "
            f"request was received at {_received(_beta_intake(route, election_command))}; "
            "nothing else was saved.",
        )
        recorded_decisions = AuthorityDecision.objects.filter(
            request_reference=election_command.request_reference
        )
        self.assertEqual(recorded_decisions.count(), 1)
        decision = recorded_decisions.get()
        self.assertEqual(
            decision.decision_reference,
            collective_approval.exception.authority_decision_reference,
        )
        self.assertEqual(
            decision.action,
            OrganismAction.EFFECT_ENTITLEMENT_ELECTION.value,
        )
        self.assertEqual(decision.outcome, "REFUSED")
        self.assertEqual(
            decision.refusal_code,
            S015AuthorityRefusalCode.SE_OPT_IN_NEEDS_CQ.value,
        )
        self.assertEqual(
            decision.decision_payload["refusal_code"],
            S015AuthorityRefusalCode.SE_OPT_IN_NEEDS_CQ.value,
        )
        self.assertEqual(
            decision.decision_payload["disclosed_refusal_code"],
            S015AuthorityRefusalCode.SE_OPT_IN_NEEDS_CQ.value,
        )
        self.assertEqual(
            decision.idempotency_key,
            election_command.idempotency_key,
        )
        after_decisions = _table_state(AuthorityDecision)
        new_decision_rows = tuple(
            row for row in after_decisions if row not in before_decisions
        )
        self.assertEqual(len(new_decision_rows), 1)
        self.assertEqual(
            tuple(row for row in after_decisions if row in before_decisions),
            before_decisions,
        )
        after = _se_record_counts()
        self.assertEqual(after[:-1], before[:-1])
        self.assertEqual(after[-1], before[-1] + 1)


class SEEntitlementFunctionTests(TransactionTestCase):
    ISOLATION_FULL_CAPTURE = True

    reset_sequences = True

    def test_w_se_f1_effective_boundaries_include_paid_grace_but_not_complimentary_grace(self):
        paid_route = prepare_route()
        paid_start = database_now()
        paid_end = paid_start + timedelta(days=30)
        with declared_event_less_entitlement_rows(
            "family 4: PAID entitlement for effective-boundary arithmetic"
        ):
            OrganismEntitlement.objects.create(
                living_organism=paid_route.organism,
                source="PAID",
                basis="PAID",
                policy_reference="fixture:paid",
                policy_version=1,
                capabilities=["APPLICATION_CIRCLE"],
                limits={
                    "circles": {"kind": "UNLIMITED"},
                    "organism_learners": {"kind": "NOT_SET"},
                    "document_footprint": {"kind": "NOT_SET"},
                },
                effective_at=paid_start,
                effective_until=paid_end,
            )
        self.assertFalse(
            entitlement_effective(
                paid_route.organism.organism_id,
                "APPLICATION_CIRCLE",
                at=paid_start - timedelta(microseconds=1),
            )
        )
        self.assertTrue(
            entitlement_effective(
                paid_route.organism.organism_id,
                "APPLICATION_CIRCLE",
                at=paid_start,
            )
        )
        self.assertTrue(
            entitlement_effective(
                paid_route.organism.organism_id,
                "APPLICATION_CIRCLE",
                at=paid_end,
            )
        )
        self.assertTrue(
            entitlement_effective(
                paid_route.organism.organism_id,
                "APPLICATION_CIRCLE",
                at=paid_end + timedelta(days=7) - timedelta(microseconds=1),
            )
        )
        self.assertFalse(
            entitlement_effective(
                paid_route.organism.organism_id,
                "APPLICATION_CIRCLE",
                at=paid_end + timedelta(days=7),
            )
        )

        trial_route = prepare_route()
        trial_start = database_now()
        trial_end = trial_start + timedelta(days=1)
        with declared_event_less_entitlement_rows(
            "family 4: TRIAL entitlement for effective-boundary arithmetic"
        ):
            OrganismEntitlement.objects.create(
                living_organism=trial_route.organism,
                source="COMPLIMENTARY",
                basis="TRANSITION_TRIAL",
                policy_reference="fixture:trial",
                policy_version=1,
                capabilities=["APPLICATION_CIRCLE"],
                limits={
                    "circles": {"kind": "UNLIMITED"},
                    "organism_learners": {"kind": "NOT_SET"},
                    "document_footprint": {"kind": "NOT_SET"},
                },
                effective_at=trial_start,
                effective_until=trial_end,
            )
        self.assertTrue(
            entitlement_effective(
                trial_route.organism.organism_id,
                "APPLICATION_CIRCLE",
                at=trial_end - timedelta(microseconds=1),
            )
        )
        self.assertFalse(
            entitlement_effective(
                trial_route.organism.organism_id,
                "APPLICATION_CIRCLE",
                at=trial_end,
            )
        )
        self.assertFalse(
            entitlement_effective(
                trial_route.organism.organism_id,
                "APPLICATION_CIRCLE",
                at=trial_end + timedelta(days=7),
            )
        )

    def test_w_se_f2_limit_returns_all_kinds_and_missing_dimension_raises(self):
        route = prepare_route()
        at = database_now()
        self.assertEqual(
            entitlement_limit(route.organism.organism_id, "circles", at=at),
            ("LIMIT", 9),
        )
        self.assertEqual(
            entitlement_limit(route.organism.organism_id, "organism_learners", at=at),
            ("NOT_SET", None),
        )
        with self.assertRaises(DatabaseError):
            entitlement_limit(route.organism.organism_id, "missing_dimension", at=at)

        rule = GovernedRule.objects.get(code="BETA_COMPLIMENTARY_ACCESS")
        policy_version, _ = ensure_test_policy_text()
        with declared_event_less_entitlement_rows(
            "family 5: TRIAL entitlement for latest-row limit selection"
        ):
            OrganismEntitlement.objects.create(
                living_organism=route.organism,
                source="TRIAL",
                basis="TRANSITION_TRIAL",
                policy_reference=f"policy-text:v{policy_version}",
                policy_version=policy_version,
                capabilities=rule.body["capabilities"],
                limits={
                    "circles": {"kind": "LIMIT", "value": 4},
                    "organism_learners": {"kind": "UNLIMITED"},
                    "document_footprint": {"kind": "NOT_SET"},
                },
                effective_at=at + timedelta(microseconds=1),
            )
        later = at + timedelta(seconds=1)
        self.assertEqual(
            entitlement_limit(route.organism.organism_id, "circles", at=later),
            ("LIMIT", 4),
        )
        self.assertEqual(
            entitlement_limit(route.organism.organism_id, "organism_learners", at=later),
            ("UNLIMITED", None),
        )
        self.assertEqual(
            entitlement_limit(route.organism.organism_id, "document_footprint", at=later),
            ("NOT_SET", None),
        )

    def test_w_se_c1_two_opt_ins_racing_commit_one_grant(self):
        """Concurrent post-Birthing opt-ins compose at most one grant."""
        route = prepare_route()
        with disclose_authority_refusal_code():
            route.birthing_service.birth_living_organism(
                _birthing_command(route, offering_choice="FREE", suffix="c1-free")
            )
        commands = (
            _election_command(route, suffix="c1-first"),
            _election_command(route, suffix="c1-second"),
        )
        barrier = Barrier(2)

        def opt_in(command):
            close_old_connections()
            barrier.wait(timeout=10)
            try:
                service = OrganismEntitlementService(
                    authority=S015Authority(
                        provider=ProductionApplicationAuthorityProvider(),
                    ),
                    clock=database_now,
                )
                return service.activate_beta(command)
            except S015EntitlementRefused as refusal:
                return refusal
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(opt_in, commands))
        successful = [result for result in results if not isinstance(result, Exception)]
        refused = [result for result in results if isinstance(result, S015EntitlementRefused)]
        self.assertEqual(len(successful), 1)
        self.assertEqual(len(refused), 1)
        self.assertIs(
            refused[0].refusal_code,
            S015AuthorityRefusalCode.SE_ALREADY_BETA,
        )
        self.assertEqual(
            OrganismEntitlement.objects.filter(
                living_organism=route.organism,
                basis="BETA_OPT_IN",
            ).count(),
            1,
        )


class SEEntitlementGuardTests(TransactionTestCase):
    reset_sequences = True

    def test_w_se_e5_person_entered_grant_is_t1_and_persists_nothing(self):
        route = prepare_route()
        received_at = database_now()
        entitlement = _seed_beta_entitlement(route, effective_at=received_at)
        before = _se_record_counts()
        before_decisions = _table_state(AuthorityDecision)
        first_grant = OrganismEntitlementEvent.objects.get(
            entitlement=entitlement,
            sequence=1,
        )
        with self.assertRaises(DatabaseError) as refused:
            with transaction.atomic():
                OrganismEntitlementEvent.objects.create(
                    entitlement=entitlement,
                    sequence=2,
                    predecessor=first_grant,
                    event_type="GRANTED",
                    actor_state="PARTY",
                    actor=route.founder,
                    actor_access_epoch=route.founder.access_epoch,
                    actor_capacity="STANDING",
                    entry_mode="TRANSPORT",
                    enterer_identity=route.founder,
                    composing_rule_state="NA_TRANSPORT",
                    composing_rule=None,
                    composing_rule_reference=None,
                    composer_state="NA_TRANSPORT",
                    composer_identity=None,
                    composed_from_event_uuid=None,
                    deciding_identity=None,
                    authoriser_identity=None,
                    authoriser_office=None,
                    reason=None,
                    occurred_at=received_at,
                    effective_at=received_at,
                    received_at=received_at,
                )
        self.assertIn("SE_GRANT_NOT_BY_HAND", str(refused.exception))
        self.assertEqual(_se_record_counts(), before)
        self.assertEqual(_table_state(AuthorityDecision), before_decisions)
        service = OrganismEntitlementService(
            authority=route.birthing_service._authority,
            clock=database_now,
        )
        with self.assertRaises(S015EntitlementRefused) as not_by_hand:
            service.refuse_person_grant()
        self.assertIs(
            not_by_hand.exception.refusal_code,
            S015AuthorityRefusalCode.SE_GRANT_NOT_BY_HAND,
        )
        self.assertEqual(
            str(not_by_hand.exception),
            "Beta access is granted automatically when an organism opts in. "
            "It cannot be granted manually. Nothing has changed.",
        )
        self.assertEqual(_se_record_counts(), before)
        self.assertEqual(_table_state(AuthorityDecision), before_decisions)
