"""H100 chunk 4 witnesses: the two G3 gateway entries behind the durable request intake.

Written by the Lead Designer against commission v0.3 (C4), US015-B-32, 21 Oct
2026, before the product exists (C4 §F0.3). The names below are the contract
C4 fixes; the Vision Chamber's dry run checks them, and the Making Engine builds
the product to pass these tests. Nothing here is a fake implementation.

  Chunk-4 name used here                                       where fixed
  -----------------------------------------------------------  --------------
  OrganismApplicationService._execute_save_gateway_draft       C4 CH-37
      (command, bound)
  OrganismApplicationService._execute_effect_gateway_version   C4 CH-37
      (command, bound)
  OrganismApplicationService._effect_gateway_version           C4 CH-37
      (command, *, bound)  (patched by name in test_sb2_birthing)
  organism_application_service.GatewayDraftResult              C4 §F0.4 B3
      six fields; replay_field_audiences classifies version_uuid
      PROCESSING; located/unresolved raw forms
  command_intake.intake_shape(entry, command) for the two G3   C4 §F2.2, B1
      entries (exact type first; B1's pure checks)
  command_intake.ACTIVE_ENTRIES = the twelve                   C4 CH-33
  GatewayDraftCommand.received_at / EffectGatewayVersionCommand.received_at
      keyword-only, default None, a supplied value refused      C4 CH-34

Every witness enters through the public method or, where a row says so, the
executor with a test-only bound request; the real models are used throughout
and no stub replaces a layer a witness asserts (practice item 92). Each case
states its prerequisite, the branch it reaches, the lock and the phase, its
outcome and the exact surviving writes, measured from after setup across every
public table (per-table counts) and the intake rows by key. Refusals are
asserted by class. There are no sleeps, no backdated rows, no NOW rebinding and
no relaxed guards (CH-30, CH-42).

Time (CH-38, CH-42). Public calls take their receive time T from the database
at admission, after every prerequisite has committed, so every historical read
at T sees its prerequisites. The replay viewer's time is the service clock,
``database_now``, fresh at serving. The test-only bound requests of this module
are confined to IW-34 (a reuse that is refused before any read) and take a
database-clock reading after setup.

Providers. The permissive ``_Provider`` is used where the witness proves the
intake boundary, the completion lookup or the result contract, not the
provider's judgement; ``_DraftDenyingProvider`` is IW-24's deterministic denial;
the route's own production provider is used for the CH-9 scenario and the
denial arm of the effect's message site, which are real routes.

There is no secret to discover in INTEVIA. Test values that mimic credentials
are not secrets.
"""

import ast
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event, get_ident
from unittest import skipUnless
from unittest.mock import patch
from uuid import UUID, uuid4

from django.contrib.auth.models import User
from django.db import IntegrityError, connection, transaction
from django.db.models import F
from django.db.models.signals import post_save
from django.test import SimpleTestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext

from core.models import (
    AuthorityDecision,
    CommandIntake,
    ContextualRoleAssignment,
    Identity,
    LivingOrganismEvent,
    LOApplicationGateway,
    LOApplicationQuestionVersion,
    LOApplicationSettingsVersion,
    LOGovernanceConfigurationVersion,
    OrganismCommandReceipt,
    OrganismMembership,
)
from core.organism_contract import OrganismAction
from src.intevia.services import command_intake as command_intake_module
from src.intevia.services import organism_application_service as application_service_module
from src.intevia.services.command_intake import (
    ACTIVATION_GROUPS,
    ACTIVE_ENTRIES,
    ActorRef,
    BoundRequest,
    IntakeAdmissionTechnicalFailure,
    IntakeEntry,
    intake_fingerprint,
    intake_shape,
    run_activation_step,
)
from src.intevia.services.organism_application_service import (
    _COMMAND_DOMAIN,
    EffectGatewayVersionCommand,
    GatewayDraftCommand,
    GatewayDraftResult,
    GatewayEffectCommandResult,
    OrganismApplicationService,
    S015ApplicationAuthorityUnavailable,
    S015ApplicationRefused,
)
from src.intevia.services.organism_authority import (
    S015Authority,
    S015AuthorityDenied,
    S015AuthorityRefusalCode,
    S015OwnApplicantAuthorityRequest,
    S015OwnApplicantAuthorityResponse,
    S015PostFoundingAuthorityRefusal,
    S015PostFoundingAuthorityResponse,
)
from src.intevia.services.organism_command_support import (
    _command_payload,
    S015CommandAtomicityRequired,
    S015CommandReplayMismatch,
    WithheldField,
    withheld_fields,
)
from src.intevia.services.organism_role_service import (
    OrganismRoleService,
    TransitionAssignmentCommand,
)
from tests.s015_0021_support import append_event, database_now, fresh, refresh, seed_identity
from tests.s015_concurrency_support import cleanup_workers, start_worker, wait_until_locked
from tests.sb2_route_support import add_active_elector, birth, prepare_route, route_test_now


POSTGRES = skipUnless(connection.vendor == "postgresql", "H100 intake witnesses require PostgreSQL")
T0 = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
SETTINGS_OPEN = {
    "applications_open": True,
    "automatic_acceptance": False,
    "processing_responsibility": "LO",
}
SETTINGS_CLOSED = {
    "applications_open": False,
    "automatic_acceptance": False,
    "processing_responsibility": "LO",
}
QUESTION_FIELDS = {
    "question_text": "TEST FIXTURE: why do you want to join?",
    "answer_format": "LONG_ANSWER",
}
GOVERNANCE_OFF = {
    "cq_active": False,
    "governance_enabled": False,
    "inherit_portal_cq_settings": False,
    "require_cq_to_change_lo_settings": False,
    "require_cq_to_change_application_question": False,
}
GOVERNANCE_ON = {**GOVERNANCE_OFF, "governance_enabled": True}
GATEWAY_VERSION_MODELS = (
    LOApplicationSettingsVersion,
    LOApplicationQuestionVersion,
    LOGovernanceConfigurationVersion,
)
GATEWAY_ACTIONS = (
    OrganismAction.EFFECT_APPLICATION_SETTINGS,
    OrganismAction.EFFECT_APPLICATION_QUESTION,
    OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
)
VERSION_TABLE_FOR = {
    OrganismAction.EFFECT_APPLICATION_SETTINGS: "core_loapplicationsettingsversion",
    OrganismAction.EFFECT_APPLICATION_QUESTION: "core_loapplicationquestionversion",
    OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION: "core_logovernanceconfigurationversion",
}


class _Provider:
    """A permissive, counting provider: it reads nothing and qualifies every request."""

    def __init__(self):
        self.requests = []

    def evaluate_organism_authority(self, request):
        self.requests.append(request)
        fields = asdict(request)
        if isinstance(request, S015OwnApplicantAuthorityRequest):
            return S015OwnApplicantAuthorityResponse(
                **fields,
                authority_reference="h100:test:applicant",
            )
        return S015PostFoundingAuthorityResponse(
            **fields,
            authority_reference="h100:test:gateway",
        )


class _DraftDenyingProvider(_Provider):
    """IW-24's deterministic provider: denies every EFFECT_APPLICATION_SETTINGS draft
    qualification with DENIED and identical response content on every evaluation;
    every other request is qualified as _Provider does."""

    def evaluate_organism_authority(self, request):
        if (
            request.action is OrganismAction.EFFECT_APPLICATION_SETTINGS
            and getattr(request, "version_kind", None) == "DRAFT"
        ):
            self.requests.append(request)
            return S015PostFoundingAuthorityRefusal(
                **asdict(request),
                refusal_code=S015AuthorityRefusalCode.DENIED,
            )
        return super().evaluate_organism_authority(request)


class _InjectedAfterVersionInsert(RuntimeError):
    """Test-only failure raised after the version row's insert (IW-21c, IW-22)."""


class _ClientErrorAfterCommit(RuntimeError):
    """Test-only client error raised after the public call returned (IW-21b (ii), OPEN-7 (a))."""


def _actor_ref(identity: Identity) -> ActorRef:
    return ActorRef(pk=identity.pk, identity_id=identity.identity_id, access_epoch=identity.access_epoch)


def _draft_command(credential, organism_uuid, action, fields, *, suffix, at, version_uuid=None):
    return GatewayDraftCommand(
        credential=credential,
        living_organism_id=organism_uuid,
        action=action,
        request_reference=f"h100:g3:draft:{suffix}",
        idempotency_key=f"h100:g3:draft:key:{suffix}",
        occurred_at=at,
        effective_at=at,
        fields=fields,
        version_uuid=version_uuid,
    )


def _effect_command(credential, organism_uuid, action, version_uuid, *, suffix, at):
    return EffectGatewayVersionCommand(
        credential=credential,
        living_organism_id=organism_uuid,
        action=action,
        version_uuid=version_uuid,
        request_reference=f"h100:g3:effect:{suffix}",
        idempotency_key=f"h100:g3:effect:key:{suffix}",
        occurred_at=at,
        effective_at=at,
    )


def _g3_fingerprint(entry, command, actor: Identity) -> str:
    shape = intake_shape(entry, command)
    return intake_fingerprint(
        entry=entry,
        action=shape.action,
        actor_identity_id=actor.identity_id,
        actor_access_epoch=actor.access_epoch,
        request_reference=shape.request_reference,
        idempotency_key=shape.idempotency_key,
        target=shape.target,
        command=shape.command,
        omitted=shape.omitted,
    )


def _intake_row(actor: Identity, command):
    return CommandIntake.objects.get(
        actor_id=actor.pk,
        action=command.action.value,
        idempotency_key=command.idempotency_key,
    )


def _assert_intake_row(test, entry, command, actor: Identity):
    """Exactly one intake row for the command, with its entry, action and intake fingerprint (CH-17 form)."""
    rows = CommandIntake.objects.filter(
        actor_id=actor.pk,
        action=command.action.value,
        idempotency_key=command.idempotency_key,
    )
    test.assertEqual(rows.count(), 1)
    intake = rows.get()
    test.assertEqual(intake.entry, entry.value)
    test.assertEqual(intake.request_reference, command.request_reference)
    test.assertEqual(intake.request_fingerprint, _g3_fingerprint(entry, command, Identity.objects.get(pk=actor.pk)))
    test.assertIsNone(intake.target_organism_id)
    return intake


def _decision_row(actor: Identity, command):
    return AuthorityDecision.objects.get(
        actor_id=actor.pk,
        action=command.action.value,
        idempotency_key=command.idempotency_key,
    )


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
            cursor.execute(f"SELECT count(*) FROM public.{connection.ops.quote_name(table)}")
            counts[table] = cursor.fetchone()[0]
    return counts


def _changed_public_rows(before, after):
    return {table: after[table] - count for table, count in before.items() if after[table] != count}


def _no_uuid_in(test, result, *uuids):
    """No identifier of the named versions appears anywhere in the response (D:1389-1391)."""
    rendered = f"{result!r} {result!s} " + " ".join(f"{value!r}" for value in asdict(result).values())
    for value in uuids:
        test.assertNotIn(str(value), rendered)
        test.assertNotIn(UUID(str(value)).hex, rendered)


NAIVE_TIME = datetime(2026, 10, 21, 12)
SUPPLIED_RECEIVED_AT = "received_at is assigned by INTEVIA"
INVALID_FIELDS = "invalid gateway draft fields"


def _shape_cases(draft, effect):
    """One case per C4 §F2.2 shape rule (B1 included), for both entries: (entry, rule, command,
    class, message); a message of None means the class alone is asserted (the exact-type check)."""
    cases = [
        (IntakeEntry.SAVE_GATEWAY_DRAFT, "exact_type", effect, TypeError, None),
        (IntakeEntry.EFFECT_GATEWAY_VERSION, "exact_type", draft, TypeError, None),
    ]
    for entry, command in (
        (IntakeEntry.SAVE_GATEWAY_DRAFT, draft),
        (IntakeEntry.EFFECT_GATEWAY_VERSION, effect),
    ):
        cases.extend(
            (
                (entry, "received_at", replace(command, received_at=T0), ValueError, SUPPLIED_RECEIVED_AT),
                (entry, "action", replace(command, action=OrganismAction.SUBMIT_APPLICATION),
                 ValueError, "unknown gateway relation"),
                (entry, "request_reference",
                 replace(command, request_reference=f" {command.request_reference}"),
                 ValueError, "request_reference is not canonical"),
                (entry, "idempotency_key", replace(command, idempotency_key=f" {command.idempotency_key}"),
                 ValueError, "idempotency_key is not canonical"),
                (entry, "occurred_at", replace(command, occurred_at=NAIVE_TIME),
                 TypeError, "occurred_at must be timezone-aware"),
                (entry, "effective_at", replace(command, effective_at=NAIVE_TIME),
                 TypeError, "effective_at must be timezone-aware"),
                (entry, "living_organism_id", replace(command, living_organism_id="not-a-uuid"),
                 TypeError, "living_organism_id must be a UUID"),
                (entry, "version_uuid", replace(command, version_uuid="not-a-uuid"),
                 TypeError, "version_uuid must be a UUID"),
            )
        )
    cases.extend(
        (
            (IntakeEntry.SAVE_GATEWAY_DRAFT, "fields_type", replace(draft, fields=["applications_open"]),
             S015ApplicationRefused, INVALID_FIELDS),
            (IntakeEntry.SAVE_GATEWAY_DRAFT, "fields_allowed_set",
             replace(draft, fields={**SETTINGS_OPEN, "not_a_field": True}),
             S015ApplicationRefused, INVALID_FIELDS),
            (IntakeEntry.SAVE_GATEWAY_DRAFT, "fields_serialisability",
             replace(draft, fields={**SETTINGS_OPEN, "applications_open": object()}),
             S015ApplicationRefused, INVALID_FIELDS),
        )
    )
    return cases


class GatewayShapeTests(SimpleTestCase):
    """Database-free projection and activation-set checks for the two G3 entries.

    Isolates the pure shape and fingerprint projection (C4 §F2.2, B1) and the
    build-time activation constant (CH-33). It does not reach admission, the
    executors or any database read; those are the TransactionTestCase classes'.
    """

    def setUp(self):
        self.credential = User(username="h100-g3-projection")
        self.actor = ActorRef(pk=91, identity_id=uuid4(), access_epoch=4)
        self.organism_uuid = uuid4()
        self.version_uuid = uuid4()
        self.draft = _draft_command(
            self.credential, self.organism_uuid, OrganismAction.EFFECT_APPLICATION_SETTINGS,
            SETTINGS_OPEN, suffix="projection", at=T0,
        )
        self.effect = _effect_command(
            self.credential, self.organism_uuid, OrganismAction.EFFECT_APPLICATION_SETTINGS,
            self.version_uuid, suffix="projection", at=T0,
        )

    def _fingerprint(self, entry, command):
        return BoundRequest.for_test(entry, command, received_at=T0, actor=self.actor).request_fingerprint

    def test_ch_33_active_entries_are_the_twelve_and_g3_is_the_pair(self):
        """CH-33: ACTIVE_ENTRIES gains the two G3 entries and nothing else; G3 is the pair."""
        self.assertEqual(
            ACTIVE_ENTRIES,
            frozenset(
                {
                    IntakeEntry.SUBMIT_APPLICATION,
                    IntakeEntry.WITHDRAW_APPLICATION,
                    IntakeEntry.CLAIM_APPLICATION,
                    IntakeEntry.REASSIGN_APPLICATION,
                    IntakeEntry.DECIDE_APPLICATION,
                    IntakeEntry.OPEN_MEMBERSHIP,
                    IntakeEntry.TRANSITION_MEMBERSHIP,
                    IntakeEntry.PROPOSE_ASSIGNMENT,
                    IntakeEntry.TRANSITION_ASSIGNMENT,
                    IntakeEntry.RECORD_OFFICE_CONSENT,
                    IntakeEntry.SAVE_GATEWAY_DRAFT,
                    IntakeEntry.EFFECT_GATEWAY_VERSION,
                }
            ),
        )
        self.assertEqual(
            ACTIVATION_GROUPS["G3"],
            frozenset({IntakeEntry.SAVE_GATEWAY_DRAFT, IntakeEntry.EFFECT_GATEWAY_VERSION}),
        )
        for group in ("G1", "G2"):
            self.assertFalse(ACTIVATION_GROUPS[group] & ACTIVE_ENTRIES, group)

    def test_each_g3_projection_matches_the_commissioned_shape(self):
        """C4 §F2.2 (CH-35): target, command, omitted, target_organism_id and action per entry."""
        draft_shape = intake_shape(IntakeEntry.SAVE_GATEWAY_DRAFT, self.draft)
        self.assertEqual(draft_shape.request_reference, self.draft.request_reference)
        self.assertEqual(draft_shape.idempotency_key, self.draft.idempotency_key)
        self.assertEqual(
            draft_shape.target,
            {"living_organism_id": self.organism_uuid, "version_uuid": None},
        )
        self.assertEqual(
            draft_shape.command,
            {"occurred_at": T0, "effective_at": T0, "fields": SETTINGS_OPEN},
        )
        self.assertEqual(draft_shape.omitted, ())
        self.assertIsNone(draft_shape.target_organism_id)
        self.assertEqual(draft_shape.action, OrganismAction.EFFECT_APPLICATION_SETTINGS.value)

        edit = replace(self.draft, version_uuid=self.version_uuid)
        self.assertEqual(
            intake_shape(IntakeEntry.SAVE_GATEWAY_DRAFT, edit).target,
            {"living_organism_id": self.organism_uuid, "version_uuid": self.version_uuid},
        )

        effect_shape = intake_shape(IntakeEntry.EFFECT_GATEWAY_VERSION, self.effect)
        self.assertEqual(
            effect_shape.target,
            {"living_organism_id": self.organism_uuid, "version_uuid": self.version_uuid},
        )
        self.assertEqual(effect_shape.command, {"occurred_at": T0, "effective_at": T0})
        self.assertEqual(effect_shape.omitted, ())
        self.assertIsNone(effect_shape.target_organism_id)
        self.assertEqual(effect_shape.action, OrganismAction.EFFECT_APPLICATION_SETTINGS.value)

    def test_exact_type_dispatch_distinguishes_the_two_entries(self):
        """C4 §F2.2: the exact-type check runs first (as CI:820-821)."""
        with self.assertRaises(TypeError):
            intake_shape(IntakeEntry.SAVE_GATEWAY_DRAFT, self.effect)
        with self.assertRaises(TypeError):
            intake_shape(IntakeEntry.EFFECT_GATEWAY_VERSION, self.draft)

    def test_every_material_field_changes_the_projection_and_identical_commands_reproduce_it(self):
        """No-DB projection: each §F2.2 field changed alone changes P; identical commands reproduce P;
        the credential is not material (CH-35); draft and effect P differ."""
        baseline = self._fingerprint(IntakeEntry.SAVE_GATEWAY_DRAFT, self.draft)
        same = _draft_command(
            self.credential, self.organism_uuid, OrganismAction.EFFECT_APPLICATION_SETTINGS,
            dict(SETTINGS_OPEN), suffix="projection", at=T0,
        )
        self.assertEqual(self._fingerprint(IntakeEntry.SAVE_GATEWAY_DRAFT, same), baseline)
        self.assertEqual(
            self._fingerprint(
                IntakeEntry.SAVE_GATEWAY_DRAFT, replace(self.draft, credential=User(username="h100-other")),
            ),
            baseline,
        )
        self.assertEqual(
            self._fingerprint(
                IntakeEntry.SAVE_GATEWAY_DRAFT,
                replace(self.draft, occurred_at=T0.astimezone(timezone(timedelta(hours=1)))),
            ),
            baseline,
        )
        changed = {
            "living_organism_id": replace(self.draft, living_organism_id=uuid4()),
            "version_uuid": replace(self.draft, version_uuid=self.version_uuid),
            "occurred_at": replace(self.draft, occurred_at=T0.replace(minute=1)),
            "effective_at": replace(self.draft, effective_at=T0.replace(minute=1)),
            "fields": replace(self.draft, fields={**SETTINGS_OPEN, "applications_open": False}),
            "action": replace(
                self.draft,
                action=OrganismAction.EFFECT_APPLICATION_QUESTION,
                fields=QUESTION_FIELDS,
            ),
            "request_reference": replace(self.draft, request_reference="h100:g3:draft:projection:other"),
            "idempotency_key": replace(self.draft, idempotency_key="h100:g3:draft:key:projection:other"),
        }
        fingerprints = {}
        for name, command in changed.items():
            with self.subTest(field=name):
                fingerprint = self._fingerprint(IntakeEntry.SAVE_GATEWAY_DRAFT, command)
                self.assertNotEqual(fingerprint, baseline)
                fingerprints[name] = fingerprint
        self.assertEqual(len(set(fingerprints.values())), len(fingerprints))

        effect_baseline = self._fingerprint(IntakeEntry.EFFECT_GATEWAY_VERSION, self.effect)
        self.assertEqual(
            self._fingerprint(
                IntakeEntry.EFFECT_GATEWAY_VERSION,
                _effect_command(
                    User(username="h100-other"), self.organism_uuid, OrganismAction.EFFECT_APPLICATION_SETTINGS,
                    self.version_uuid, suffix="projection", at=T0,
                ),
            ),
            effect_baseline,
        )
        for name, command in {
            "living_organism_id": replace(self.effect, living_organism_id=uuid4()),
            "version_uuid": replace(self.effect, version_uuid=uuid4()),
            "occurred_at": replace(self.effect, occurred_at=T0.replace(minute=1)),
            "effective_at": replace(self.effect, effective_at=T0.replace(minute=1)),
            "action": replace(self.effect, action=OrganismAction.EFFECT_APPLICATION_QUESTION),
            "request_reference": replace(self.effect, request_reference="h100:g3:effect:projection:other"),
            "idempotency_key": replace(self.effect, idempotency_key="h100:g3:effect:key:projection:other"),
        }.items():
            with self.subTest(field=name):
                self.assertNotEqual(self._fingerprint(IntakeEntry.EFFECT_GATEWAY_VERSION, command), effect_baseline)
        # A draft and an effect naming the same version under the same keys are different requests.
        edit_of_same_version = replace(self.draft, version_uuid=self.version_uuid)
        same_keys_effect = replace(
            self.effect,
            request_reference=self.draft.request_reference,
            idempotency_key=self.draft.idempotency_key,
        )
        self.assertNotEqual(
            self._fingerprint(IntakeEntry.SAVE_GATEWAY_DRAFT, edit_of_same_version),
            self._fingerprint(IntakeEntry.EFFECT_GATEWAY_VERSION, same_keys_effect),
        )

    def test_iw_02_and_iw_03_shape_rules_refuse_without_a_database(self):
        """IW-02, IW-03 (D:1630-1631) through intake_shape: one case per §F2.2 shape rule,
        the B1 cases included; refusals by class, the fixed messages where C4 fixes them."""
        for entry, rule, malformed, expected_class, message in _shape_cases(self.draft, self.effect):
            with self.subTest(entry=entry, rule=rule):
                if message is None:
                    with self.assertRaises(expected_class):
                        intake_shape(entry, malformed)
                else:
                    with self.assertRaisesRegex(expected_class, message):
                        intake_shape(entry, malformed)

    def test_gateway_draft_result_contract(self):
        """B3: six fields, frozen, version_uuid classified PROCESSING, the two raw forms."""
        located = GatewayDraftResult(
            living_organism_uuid=self.organism_uuid,
            version_uuid=self.version_uuid,
            action=OrganismAction.EFFECT_APPLICATION_SETTINGS,
            effective_at=T0,
            received_at=T0,
            located=True,
        )
        unresolved = replace(located, version_uuid=None, located=False)
        self.assertEqual(
            set(asdict(located)),
            {"living_organism_uuid", "version_uuid", "action", "effective_at", "received_at", "located"},
        )
        self.assertEqual(len(asdict(located)), 6)
        self.assertEqual(
            GatewayDraftResult.replay_field_audiences(located),
            {"version_uuid": "PROCESSING"},
        )
        self.assertEqual(GatewayDraftResult.replay_field_audiences(unresolved), {"version_uuid": "PROCESSING"})
        self.assertIs(unresolved.located, False)
        self.assertIsNone(unresolved.version_uuid)
        with self.assertRaises((AttributeError, TypeError)):
            located.located = False  # frozen


class GatewayWriterCensusTests(SimpleTestCase):
    """CH-41 (IW-21d's static guard; D:1655, A2 and A3): the only writers of the three gateway
    version tables under src/ are the five known sites, re-censused at every run.

    Reads the repository's src/ tree as data. The receiver-name heuristic (the
    roots below) is the census's limit, stated here: a write through a local
    bound to another name would not be found; raw SQL naming the three tables
    with a write verb is reported whatever its receiver.
    """

    ROOTS = {"version", "versions", "model", "draft", "settings", "election", "predecessor",
             "configuration", "question"} | {model.__name__ for model in GATEWAY_VERSION_MODELS}
    WRITE_METHODS = {"create", "save", "delete", "update", "bulk_create", "bulk_update",
                     "get_or_create", "update_or_create"}
    APP = "intevia/services/organism_application_service.py"
    ENT = "intevia/services/organism_entitlement_service.py"
    EXPECTED = {
        (APP, "OrganismApplicationService._execute_save_gateway_draft", "save"),
        (APP, "OrganismApplicationService._execute_save_gateway_draft", "create"),
        (ENT, "OrganismEntitlementService._new_settings_version", "create"),
        (ENT, "OrganismEntitlementService.prepare_birthing_election", "delete"),
        (ENT, "OrganismEntitlementService.activate_beta", "delete"),
    }

    @staticmethod
    def _root_name(node):
        while isinstance(node, (ast.Call, ast.Attribute, ast.Subscript)):
            node = node.func if isinstance(node, ast.Call) else node.value
        return node.id if isinstance(node, ast.Name) else None

    def test_ch_41_the_only_version_table_writers_are_the_known_sites(self):
        """CH-41 (IW-21d's static guard, D:1655; A2, A3): the writer census over src/ equals EXPECTED."""
        src_root = Path(__file__).resolve().parents[1] / "src"
        tables = tuple(VERSION_TABLE_FOR.values())
        model_names = {model.__name__ for model in GATEWAY_VERSION_MODELS}
        found = set()
        for path in sorted(src_root.rglob("*.py")):
            source = path.read_text(encoding="utf-8-sig")
            if not (any(name in source for name in model_names) or "_VERSION_KINDS" in source
                    or any(table in source for table in tables)):
                continue
            tree = ast.parse(source)
            parents = {}
            for node in ast.walk(tree):
                for child in ast.iter_child_nodes(node):
                    parents[child] = node

            def qualname(node):
                names = []
                while node in parents:
                    node = parents[node]
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        names.append(node.name)
                return ".".join(reversed(names)) or "<module>"

            relative = path.relative_to(src_root).as_posix()
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in self.WRITE_METHODS
                    and self._root_name(node.func.value) in self.ROOTS
                ):
                    found.add((relative, qualname(node), node.func.attr))
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    text = node.value.lower()
                    if any(table in text for table in tables) and any(
                        verb in text for verb in ("update ", "delete ", "insert ", "truncate ")
                    ):
                        found.add((relative, qualname(node), "SQL"))
        self.assertEqual(found, self.EXPECTED)


@POSTGRES
class GatewayAdmissionStateTests(TransactionTestCase):
    """IW-00 (a), (b) and the database instances of IW-02 and IW-03 for both G3 entries.

    Isolates the admission-state refusal and the shape refusals of the public
    methods: each is proved to issue no SQL and to write nothing. It does not
    reach the executors; no organism exists in these fixtures.
    """

    def setUp(self):
        self.service = OrganismApplicationService(
            authority=S015Authority(provider=_Provider()),
            clock=database_now,
        )
        credential = User(username="h100-g3-admission-state")
        self.commands = {
            IntakeEntry.SAVE_GATEWAY_DRAFT: _draft_command(
                credential, uuid4(), OrganismAction.EFFECT_APPLICATION_SETTINGS, SETTINGS_OPEN,
                suffix=f"state-draft:{fresh()}", at=T0,
            ),
            IntakeEntry.EFFECT_GATEWAY_VERSION: _effect_command(
                credential, uuid4(), OrganismAction.EFFECT_APPLICATION_SETTINGS, uuid4(),
                suffix=f"state-effect:{fresh()}", at=T0,
            ),
        }

    def _call(self, entry, command):
        methods = {
            IntakeEntry.SAVE_GATEWAY_DRAFT: self.service.save_gateway_draft,
            IntakeEntry.EFFECT_GATEWAY_VERSION: self.service.effect_gateway_version,
        }
        return methods[entry](command)

    def test_iw_00_g3_entries_refuse_atomic_and_manual_modes_without_sql(self):
        """IW-00 (a), (b) (D:1628): refused at _require_admission_state before any statement."""
        for entry, command in self.commands.items():
            with self.subTest(entry=entry):
                with transaction.atomic():
                    with CaptureQueriesContext(connection) as queries:
                        with self.assertRaises(S015CommandAtomicityRequired):
                            self._call(entry, command)
                    self.assertEqual(len(queries), 0)
                    self.assertTrue(connection.in_atomic_block)
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT 1")
                        self.assertEqual(cursor.fetchone()[0], 1)
                connection.set_autocommit(False)
                try:
                    with CaptureQueriesContext(connection) as queries:
                        with self.assertRaises(S015CommandAtomicityRequired):
                            self._call(entry, command)
                    self.assertEqual(len(queries), 0)
                finally:
                    connection.rollback()
                    connection.set_autocommit(True)
        self.assertEqual(CommandIntake.objects.count(), 0)

    def test_iw_02_client_received_at_is_refused_before_any_query(self):
        """IW-02 (D:1630; CH-34): a supplied received_at is refused at shape for both entries."""
        for entry, command in self.commands.items():
            with self.subTest(entry=entry), CaptureQueriesContext(connection) as queries:
                with self.assertRaisesRegex(ValueError, "received_at is assigned by INTEVIA"):
                    self._call(entry, replace(command, received_at=T0))
                self.assertEqual(len(queries), 0)
        self.assertEqual(CommandIntake.objects.count(), 0)
        self.assertEqual(AuthorityDecision.objects.count(), 0)

    def test_iw_03_shape_rules_refuse_malformed_requests_before_sql(self):
        """IW-03 (D:1631; B1): one public call per shape rule; nothing written, no statement issued."""
        draft = self.commands[IntakeEntry.SAVE_GATEWAY_DRAFT]
        effect = self.commands[IntakeEntry.EFFECT_GATEWAY_VERSION]
        for entry, rule, malformed, expected_class, message in _shape_cases(draft, effect):
            with self.subTest(entry=entry, rule=rule):
                with CaptureQueriesContext(connection) as queries:
                    if message is None:
                        with self.assertRaises(expected_class):
                            self._call(entry, malformed)
                    else:
                        with self.assertRaisesRegex(expected_class, message):
                            self._call(entry, malformed)
                self.assertEqual(len(queries), 0)
        self.assertEqual(CommandIntake.objects.count(), 0)
        self.assertEqual(AuthorityDecision.objects.count(), 0)


@POSTGRES
class GatewayIntakeFlowTests(TransactionTestCase):
    """Admission, execution, the completion lookup, retries and the result contract on a born LO.

    Isolates the boundary and the executors' observable rows and results. The
    permissive provider means these cases do not prove the production provider's
    judgement of a draft or effect; IW-24 fixes that provider deterministically,
    and the CH-9 scenario and the denial arm of the message site use the route's
    production provider. The gateway head exists from the route's own setup, so
    the exact deltas below are the commands' alone (C4 §F0.4, exact-delta fixtures).
    """

    def setUp(self):
        self.tag = f"h100-gateway-{fresh()}"
        self.route = prepare_route(tag=self.tag)
        birth(self.route)
        self.organism = self.route.organism
        self.organism_uuid = self.organism.organism_id
        self.founder = Identity.objects.select_related("credential").get(pk=self.route.founder.pk)
        self.now = route_test_now(self.route)
        self.provider = _Provider()
        self.service = OrganismApplicationService(
            authority=S015Authority(provider=self.provider),
            clock=database_now,
        )
        self.assertTrue(LOApplicationGateway.objects.filter(living_organism=self.organism).exists())

    def _draft(self, action, fields, *, suffix, credential=None, version_uuid=None, at=None):
        return _draft_command(
            credential or self.founder.credential, self.organism_uuid, action, fields,
            suffix=f"{suffix}:{self.tag}", at=at or self.now, version_uuid=version_uuid,
        )

    def _effect(self, action, version_uuid, *, suffix, credential=None, at=None):
        return _effect_command(
            credential or self.founder.credential, self.organism_uuid, action, version_uuid,
            suffix=f"{suffix}:{self.tag}", at=at or self.now,
        )

    def _assert_located(self, result, *, command, version, intake):
        self.assertIsInstance(result, GatewayDraftResult)
        self.assertIs(result.located, True)
        self.assertEqual(result.version_uuid, version.version_uuid)
        self.assertEqual(result.living_organism_uuid, self.organism_uuid)
        self.assertEqual(result.action, command.action)
        self.assertEqual(result.effective_at, command.effective_at)
        self.assertEqual(result.received_at, intake.received_at)
        self.assertEqual(withheld_fields(result), ())

    def _end_founder_office(self):
        office = ContextualRoleAssignment.objects.get(
            membership__identity=self.founder,
            membership__living_organism=self.organism,
            role_definition__code="COORDINATOR",
            role_definition__scope="LIVING_ORGANISM",
            state="ACTIVE",
        )
        OrganismRoleService(
            authority=S015Authority(provider=_Provider()),
            clock=database_now,
        ).transition_assignment(TransitionAssignmentCommand(
            credential=self.founder.credential,
            request_reference=f"h100:g3:end-office:{self.tag}",
            idempotency_key=f"h100:g3:end-office:key:{self.tag}",
            occurred_at=self.now,
            effective_at=self.now,
            assignment_uuid=office.assignment_uuid,
            action="END",
            resulting_state="ENDED",
        ))
        office.refresh_from_db()
        self.assertEqual(office.state, "ENDED")
        return office

    def test_iw_00c_each_g3_entry_admits_then_runs_its_executor_with_the_committed_binding(self):
        """IW-00 (c) (D:1628): a well-formed public call outside any transaction is admitted (UQ, first
        insert) and its executor receives the binding of the committed row; the public methods take no
        bound request (IW-34 (f))."""
        draft = self._draft(OrganismAction.EFFECT_APPLICATION_SETTINGS, SETTINGS_OPEN, suffix="iw00c")
        captured = {}
        original_draft = self.service._execute_save_gateway_draft

        def capture_draft(command, bound):
            captured["draft"] = bound
            return original_draft(command, bound)

        with patch.object(self.service, "_execute_save_gateway_draft", side_effect=capture_draft):
            version = self.service.save_gateway_draft(draft)
        self.assertIsInstance(version, LOApplicationSettingsVersion)
        intake = _assert_intake_row(self, IntakeEntry.SAVE_GATEWAY_DRAFT, draft, self.founder)
        bound = captured["draft"]
        self.assertIsInstance(bound, BoundRequest)
        self.assertIs(bound.entry, IntakeEntry.SAVE_GATEWAY_DRAFT)
        self.assertTrue(bound.first)
        self.assertEqual(bound.intake_id, intake.pk)
        self.assertEqual(bound.received_at, intake.received_at)
        self.assertEqual(bound.action, draft.action.value)
        self.assertEqual((bound.actor_pk, bound.actor_identity_id), (self.founder.pk, self.founder.identity_id))
        self.assertIsNone(bound.target_organism_id)
        self.assertIsNone(bound.parent)

        effect = self._effect(OrganismAction.EFFECT_APPLICATION_SETTINGS, version.version_uuid, suffix="iw00c")
        original_effect = self.service._execute_effect_gateway_version

        def capture_effect(command, bound):
            captured["effect"] = bound
            return original_effect(command, bound)

        with patch.object(self.service, "_execute_effect_gateway_version", side_effect=capture_effect):
            result = self.service.effect_gateway_version(effect)
        self.assertIsInstance(result, GatewayEffectCommandResult)
        effect_intake = _assert_intake_row(self, IntakeEntry.EFFECT_GATEWAY_VERSION, effect, self.founder)
        bound = captured["effect"]
        self.assertIs(bound.entry, IntakeEntry.EFFECT_GATEWAY_VERSION)
        self.assertTrue(bound.first)
        self.assertEqual((bound.intake_id, bound.received_at), (effect_intake.pk, effect_intake.received_at))
        self.assertEqual(bound.action, effect.action.value)

        with self.assertRaises(TypeError):
            self.service.save_gateway_draft(draft, object())
        with self.assertRaises(TypeError):
            self.service.effect_gateway_version(effect, object())

    def test_iw_01_draft_and_effect_bind_qualify_and_finalise_at_the_bound_time(self):
        """IW-01 (D:1629) for save_gateway_draft and effect_gateway_version: exact deltas, the intake
        time inside [t0, t1], decision.evaluated_at = the intake time, no receipt for a draft and the
        effect's receipt fingerprint recomputed with the intake time."""
        before = _public_row_counts()
        draft = self._draft(OrganismAction.EFFECT_APPLICATION_QUESTION, QUESTION_FIELDS, suffix="iw01")
        t0 = database_now()
        version = self.service.save_gateway_draft(draft)
        t1 = database_now()
        after_draft = _public_row_counts()
        self.assertEqual(
            _changed_public_rows(before, after_draft),
            {
                "core_commandintake": 1,
                "core_loapplicationquestionversion": 1,
                "core_authoritydecision": 1,
            },
        )
        intake = _assert_intake_row(self, IntakeEntry.SAVE_GATEWAY_DRAFT, draft, self.founder)
        self.assertLessEqual(t0, intake.received_at)
        self.assertLessEqual(intake.received_at, t1)
        version.refresh_from_db()
        self.assertEqual(version.received_at, intake.received_at)
        self.assertIsNotNone(version.recorded_at)
        self.assertGreaterEqual(version.recorded_at, intake.received_at)
        decision = _decision_row(self.founder, draft)
        self.assertEqual(decision.outcome, "QUALIFIED")
        self.assertEqual(decision.evaluated_at, intake.received_at)
        self.assertFalse(
            OrganismCommandReceipt.objects.filter(
                actor_id=self.founder.pk, action=draft.action.value, idempotency_key=draft.idempotency_key,
            ).exists()
        )

        effect = self._effect(OrganismAction.EFFECT_APPLICATION_QUESTION, version.version_uuid, suffix="iw01")
        t2 = database_now()
        result = self.service.effect_gateway_version(effect)
        t3 = database_now()
        after_effect = _public_row_counts()
        self.assertEqual(
            _changed_public_rows(after_draft, after_effect),
            {
                "core_commandintake": 1,
                "core_livingorganismevent": 1,
                "core_authoritydecision": 1,
                "core_organismcommandreceipt": 1,
            },
        )
        effect_intake = _assert_intake_row(self, IntakeEntry.EFFECT_GATEWAY_VERSION, effect, self.founder)
        self.assertLessEqual(t2, effect_intake.received_at)
        self.assertLessEqual(effect_intake.received_at, t3)
        self.assertIsInstance(result, GatewayEffectCommandResult)
        self.assertEqual(result.version_uuid, version.version_uuid)
        event = LivingOrganismEvent.objects.get(event_uuid=result.event_uuid)
        self.assertEqual(event.received_at, effect_intake.received_at)
        self.assertEqual(event.evidence_reference, f"application-question-version:{version.version_uuid}")
        effect_decision = _decision_row(self.founder, effect)
        self.assertEqual(effect_decision.outcome, "QUALIFIED")
        self.assertEqual(effect_decision.evaluated_at, effect_intake.received_at)
        receipt = OrganismCommandReceipt.objects.get(
            actor_id=self.founder.pk, action=effect.action.value, idempotency_key=effect.idempotency_key,
        )
        actor = Identity.objects.get(pk=self.founder.pk)
        self.assertEqual(
            receipt.payload_fingerprint,
            _command_payload(
                domain=_COMMAND_DOMAIN,
                schema="s015-gateway-v1",
                action=effect.action,
                actor_identity_id=actor.identity_id,
                actor_access_epoch=actor.access_epoch,
                request_reference=effect.request_reference,
                idempotency_key=effect.idempotency_key,
                occurred_at=effect.occurred_at,
                target={"action": effect.action.value, "organism": self.organism_uuid, "version": version.version_uuid},
                command={"effective_at": effect.effective_at, "received_at": effect_intake.received_at, "fields": None},
            ),
        )
        self.assertEqual(receipt.authority_decision_reference, effect_decision.decision_reference)
        self.assertEqual(len(self.provider.requests), 2)

    def test_iw_20_draft_creation_binds_and_its_completion_evidence_is_the_decision(self):
        """IW-20 (D:1651): UQ -> AL -> MX -> owning-organism lock -> membership-context lock -> gateway
        lock -> lookup (none) -> qualify -> create. +1 intake; +1 version with received_at = T, actor,
        supersedes = the effective version, a database-assigned recorded_at; +1 QUALIFIED decision with
        the key columns, evaluated_at = T, target.version_uuid null and version_kind DRAFT; no receipt;
        nothing else."""
        predecessor = application_service_module._effective_gateway_version(
            "default", self.organism, OrganismAction.EFFECT_APPLICATION_SETTINGS, database_now(),
        )
        self.assertIsNotNone(predecessor)
        before = _public_row_counts()
        draft = self._draft(OrganismAction.EFFECT_APPLICATION_SETTINGS, SETTINGS_CLOSED, suffix="iw20")
        version = self.service.save_gateway_draft(draft)
        self.assertEqual(
            _changed_public_rows(before, _public_row_counts()),
            {
                "core_commandintake": 1,
                "core_loapplicationsettingsversion": 1,
                "core_authoritydecision": 1,
            },
        )
        intake = _assert_intake_row(self, IntakeEntry.SAVE_GATEWAY_DRAFT, draft, self.founder)
        version.refresh_from_db()
        self.assertEqual(version.received_at, intake.received_at)
        self.assertEqual((version.occurred_at, version.effective_at), (draft.occurred_at, draft.effective_at))
        self.assertEqual((version.actor_id, version.enterer_id), (self.founder.pk, self.founder.pk))
        self.assertEqual(version.living_organism_id, self.organism.pk)
        self.assertEqual(version.supersedes_id, predecessor.pk)
        self.assertIsNotNone(version.recorded_at)
        self.assertGreaterEqual(version.recorded_at, intake.received_at)
        self.assertIs(version.applications_open, False)
        decision = _decision_row(self.founder, draft)
        self.assertEqual(decision.outcome, "QUALIFIED")
        self.assertEqual(decision.request_reference, draft.request_reference)
        self.assertEqual(decision.evaluated_at, intake.received_at)
        self.assertEqual(decision.target["organism_uuid"], str(self.organism_uuid))
        self.assertIsNone(decision.target["version_uuid"])
        self.assertEqual(decision.decision_payload["version_kind"], "DRAFT")
        self.assertFalse(
            OrganismCommandReceipt.objects.filter(
                actor_id=self.founder.pk, action=draft.action.value, idempotency_key=draft.idempotency_key,
            ).exists()
        )
        self.assertEqual(len(self.provider.requests), 1)
        self.assertEqual(self.provider.requests[0].evaluated_at, intake.received_at)

    def test_iw_21a_identical_draft_retry_locates_the_draft_without_requalifying(self):
        """IW-21a (D:1652), R1: the same request again: UQ (existing) -> AL -> MX -> locks -> lookup finds
        the QUALIFIED decision -> tau-join (one) -> serve_replay at viewer.now. The located result; no
        qualification; nothing written."""
        draft = self._draft(OrganismAction.EFFECT_APPLICATION_SETTINGS, SETTINGS_CLOSED, suffix="iw21a")
        version = self.service.save_gateway_draft(draft)
        intake = _intake_row(self.founder, draft)
        before = _public_row_counts()
        provider_calls = len(self.provider.requests)

        result = self.service.save_gateway_draft(draft)

        self._assert_located(result, command=draft, version=version, intake=intake)
        self.assertEqual(_public_row_counts(), before)
        self.assertEqual(len(self.provider.requests), provider_calls)
        self.assertEqual(CommandIntake.objects.filter(pk=intake.pk).get().received_at, intake.received_at)

    def test_iw_21a_located_draft_is_withheld_when_the_actor_no_longer_holds_the_office(self):
        """IW-21a variant (D:1652; D:1382-1391): the actor's Coordinator office ended before the retry.
        The located result keeps located=True; version_uuid is withheld at serving time (present read
        access at viewer.now) and no identifier of the draft appears anywhere in the response. No
        qualification and no office check at T; nothing written by the retry."""
        draft = self._draft(OrganismAction.EFFECT_APPLICATION_SETTINGS, SETTINGS_CLOSED, suffix="iw21a-hidden")
        version = self.service.save_gateway_draft(draft)
        intake = _intake_row(self.founder, draft)
        self._end_founder_office()
        before = _public_row_counts()
        provider_calls = len(self.provider.requests)

        result = self.service.save_gateway_draft(draft)

        self.assertIsInstance(result, GatewayDraftResult)
        self.assertIs(result.located, True)
        self.assertEqual(result.version_uuid, WithheldField("version_uuid", "PROCESSING"))
        self.assertEqual(withheld_fields(result), (WithheldField("version_uuid", "PROCESSING"),))
        self.assertEqual(result.living_organism_uuid, self.organism_uuid)
        self.assertEqual(result.action, draft.action)
        self.assertEqual(result.effective_at, draft.effective_at)
        self.assertEqual(result.received_at, intake.received_at)
        _no_uuid_in(self, result, version.version_uuid)
        self.assertEqual(_public_row_counts(), before)
        self.assertEqual(len(self.provider.requests), provider_calls)

    def test_iw_21e_two_tau_identical_versions_make_the_retry_unresolved(self):
        """IW-21e (D:1656; A4): a second version row with identical tau inserted by test-only direct SQL
        (the database permits it, A3); the same request: the lookup finds the decision and the tau-join
        returns 2 -> the unresolved response (located=False, version_uuid=None, no draft identifier
        anywhere); nothing written."""
        draft = self._draft(OrganismAction.EFFECT_APPLICATION_SETTINGS, SETTINGS_CLOSED, suffix="iw21e")
        version = self.service.save_gateway_draft(draft)
        intake = _intake_row(self.founder, draft)
        twin_uuid = uuid4()
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO public.core_loapplicationsettingsversion "
                "(version_uuid, living_organism_id, actor_id, enterer_id, supersedes_id, "
                " occurred_at, effective_at, received_at, "
                " applications_open, automatic_acceptance, processing_responsibility) "
                "SELECT %s, living_organism_id, actor_id, enterer_id, supersedes_id, "
                "       occurred_at, effective_at, received_at, "
                "       applications_open, automatic_acceptance, processing_responsibility "
                "  FROM public.core_loapplicationsettingsversion WHERE id = %s",
                [twin_uuid, version.pk],
            )
        twin = LOApplicationSettingsVersion.objects.get(version_uuid=twin_uuid)
        for column in ("living_organism_id", "actor_id", "received_at", "occurred_at", "effective_at"):
            self.assertEqual(getattr(twin, column), getattr(version, column), column)
        self.assertIsNotNone(twin.recorded_at)
        before = _public_row_counts()
        provider_calls = len(self.provider.requests)

        result = self.service.save_gateway_draft(draft)

        self.assertIsInstance(result, GatewayDraftResult)
        self.assertIs(result.located, False)
        self.assertIsNone(result.version_uuid)
        self.assertEqual(result.living_organism_uuid, self.organism_uuid)
        self.assertEqual(result.action, draft.action)
        self.assertEqual(result.effective_at, draft.effective_at)
        self.assertEqual(result.received_at, intake.received_at)
        _no_uuid_in(self, result, version.version_uuid, twin_uuid)
        self.assertEqual(_public_row_counts(), before)
        self.assertEqual(len(self.provider.requests), provider_calls)

    def test_iw_22_draft_creation_retry_after_rollback_creates_once_at_the_bound_time(self):
        """IW-22 (D:1657; HC-2): a test-only post_save receiver raises after the version insert of
        call 1 -> the executor's block rolls back: +1 intake only (fresh read). Call 2, identical:
        the lookup finds nothing -> creates once at T, the intake's time."""
        draft = self._draft(OrganismAction.EFFECT_APPLICATION_SETTINGS, SETTINGS_CLOSED, suffix="iw22")
        before = _public_row_counts()
        inserted = []

        def raise_after_insert(sender, instance, created, **kwargs):
            if created and instance.living_organism_id == self.organism.pk:
                inserted.append(instance.version_uuid)
                raise _InjectedAfterVersionInsert("test-only failure after the version insert")

        dispatch_uid = f"h100-iw22-{self.tag}"
        post_save.connect(
            raise_after_insert, sender=LOApplicationSettingsVersion, weak=False, dispatch_uid=dispatch_uid,
        )
        try:
            with self.assertRaises(_InjectedAfterVersionInsert):
                self.service.save_gateway_draft(draft)
        finally:
            post_save.disconnect(sender=LOApplicationSettingsVersion, dispatch_uid=dispatch_uid)
        self.assertEqual(len(inserted), 1)
        after_failure = _public_row_counts()
        self.assertEqual(_changed_public_rows(before, after_failure), {"core_commandintake": 1})
        intake = _intake_row(self.founder, draft)
        self.assertFalse(LOApplicationSettingsVersion.objects.filter(version_uuid=inserted[0]).exists())

        version = self.service.save_gateway_draft(draft)

        self.assertIsInstance(version, LOApplicationSettingsVersion)
        self.assertEqual(
            _changed_public_rows(after_failure, _public_row_counts()),
            {"core_loapplicationsettingsversion": 1, "core_authoritydecision": 1},
        )
        version.refresh_from_db()
        self.assertEqual(version.received_at, intake.received_at)
        self.assertEqual(_decision_row(self.founder, draft).evaluated_at, intake.received_at)
        self.assertEqual(CommandIntake.objects.get(pk=intake.pk).received_at, intake.received_at)

    def test_iw_23_an_edit_retry_never_overwrites_an_intervening_edit(self):
        """IW-23 (D:1658; D:1377-1380): draft D; edit E1 (K1, f = a) completes; edit E2 (K2, f = b)
        completes; E1 repeated: the lookup finds E1's decision (target.version_uuid = D) -> nothing
        re-applied; the located result for K1 carries E1's bound time, not D's creation time; f stays b."""
        creation = self._draft(OrganismAction.EFFECT_APPLICATION_SETTINGS, SETTINGS_CLOSED, suffix="iw23-d")
        draft = self.service.save_gateway_draft(creation)
        edit_one = self._draft(
            OrganismAction.EFFECT_APPLICATION_SETTINGS, {"applications_open": True},
            suffix="iw23-e1", version_uuid=draft.version_uuid,
        )
        edited = self.service.save_gateway_draft(edit_one)
        self.assertIsInstance(edited, LOApplicationSettingsVersion)
        self.assertEqual(edited.pk, draft.pk)
        self.assertIs(LOApplicationSettingsVersion.objects.get(pk=draft.pk).applications_open, True)
        edit_one_intake = _intake_row(self.founder, edit_one)
        edit_one_decision = _decision_row(self.founder, edit_one)
        self.assertEqual(edit_one_decision.target["version_uuid"], str(draft.version_uuid))
        edit_two = self._draft(
            OrganismAction.EFFECT_APPLICATION_SETTINGS, {"applications_open": False},
            suffix="iw23-e2", version_uuid=draft.version_uuid,
        )
        self.service.save_gateway_draft(edit_two)
        self.assertIs(LOApplicationSettingsVersion.objects.get(pk=draft.pk).applications_open, False)
        before = _public_row_counts()
        provider_calls = len(self.provider.requests)

        result = self.service.save_gateway_draft(edit_one)

        self._assert_located(result, command=edit_one, version=draft, intake=edit_one_intake)
        draft.refresh_from_db()
        self.assertNotEqual(result.received_at, draft.received_at)
        self.assertIs(draft.applications_open, False)
        self.assertEqual(_public_row_counts(), before)
        self.assertEqual(len(self.provider.requests), provider_calls)

    def test_iw_24_denied_draft_keeps_the_intake_and_the_refused_decision_and_the_retry_reuses_it(self):
        """IW-24 (D:1659; F-ST2-32), fixed inputs: an actor with an ACTIVE membership and no Coordinator
        office; a deterministic provider denying every EFFECT_APPLICATION_SETTINGS draft identically.
        Call 1: UQ -> AL -> MX -> locks -> lookup (none) -> qualify denies: +1 intake, +1 REFUSED
        decision, no version. Call 2, identical: the lookup finds only the REFUSED row -> qualify at T
        again -> the same response -> the same decision reference, no new row: +0 of every table."""
        member = add_active_elector(self.route, tag="iw24")
        provider = _DraftDenyingProvider()
        service = OrganismApplicationService(authority=S015Authority(provider=provider), clock=database_now)
        draft = self._draft(
            OrganismAction.EFFECT_APPLICATION_SETTINGS, SETTINGS_CLOSED, suffix="iw24", credential=member.credential,
        )
        before = _public_row_counts()

        with self.assertRaises(S015AuthorityDenied) as first:
            service.save_gateway_draft(draft)

        self.assertIs(first.exception.refusal_code, S015AuthorityRefusalCode.DENIED)
        after_first = _public_row_counts()
        self.assertEqual(
            _changed_public_rows(before, after_first),
            {"core_commandintake": 1, "core_authoritydecision": 1},
        )
        intake = _assert_intake_row(self, IntakeEntry.SAVE_GATEWAY_DRAFT, draft, member)
        decision = _decision_row(member, draft)
        self.assertEqual(decision.outcome, "REFUSED")
        self.assertEqual(decision.refusal_code, S015AuthorityRefusalCode.DENIED.value)
        self.assertEqual(decision.evaluated_at, intake.received_at)
        self.assertEqual(decision.decision_reference, first.exception.authority_decision_reference)
        self.assertEqual(len(provider.requests), 1)

        with self.assertRaises(S015AuthorityDenied) as second:
            service.save_gateway_draft(draft)

        self.assertIs(second.exception.refusal_code, S015AuthorityRefusalCode.DENIED)
        self.assertEqual(
            second.exception.authority_decision_reference,
            first.exception.authority_decision_reference,
        )
        self.assertEqual(_public_row_counts(), after_first)
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(provider.requests[1].evaluated_at, intake.received_at)
        self.assertEqual(
            AuthorityDecision.objects.filter(
                actor_id=member.pk, action=draft.action.value, idempotency_key=draft.idempotency_key,
            ).count(),
            1,
        )
        for model in GATEWAY_VERSION_MODELS:
            self.assertFalse(model.objects.filter(living_organism=self.organism, actor_id=member.pk).exists())

    def test_iw_24b_a_moved_membership_head_records_a_second_refused_decision(self):
        """IW-24 variant (b) (D:1659): between the attempts the actor's membership head moves while the
        membership stays ACTIVE. No v1.0 public route moves an ACTIVE membership's head without ending
        it (the service allows ACTIVE -> ENDED only, and an ENDED member is refused at the
        membership-context lock, before qualification), so the head is moved by a test-only direct-SQL
        transition row the database accepts. The retry's request carries a different
        active_membership_head_reference, the provider's echoed refusal differs, a new decision
        reference is recorded: +1 REFUSED decision, still no version, +0 intake."""
        member = add_active_elector(self.route, tag="iw24b")
        provider = _DraftDenyingProvider()
        service = OrganismApplicationService(authority=S015Authority(provider=provider), clock=database_now)
        draft = self._draft(
            OrganismAction.EFFECT_APPLICATION_SETTINGS, SETTINGS_CLOSED, suffix="iw24b", credential=member.credential,
        )
        with self.assertRaises(S015AuthorityDenied) as first:
            service.save_gateway_draft(draft)
        after_first = _public_row_counts()
        membership = OrganismMembership.objects.get(identity=member, living_organism=self.organism)
        old_head = membership.head_transition_id
        self.assertIsNotNone(old_head)
        moved_at = database_now()
        with transaction.atomic(), connection.cursor() as cursor:
            append_event(
                cursor, "core_organismmembershiptransition", membership.pk, 2,
                "TEST_HEAD_MOVE", "ACTIVE", "ACTIVE",
                member.pk, self.organism.founding_authority_basis_id, f"iw24b-head:{self.tag}",
                pred_id=old_head, eff=moved_at, occ=moved_at, rec=moved_at,
                extra={"reason_class": "TEST"},
            )
            refresh(cursor, "core_organismmembership", membership.pk, moved_at)
        membership.refresh_from_db()
        self.assertEqual(membership.state, "ACTIVE")
        self.assertNotEqual(membership.head_transition_id, old_head)
        after_move = _public_row_counts()
        self.assertEqual(
            after_move["core_organismmembershiptransition"],
            after_first["core_organismmembershiptransition"] + 1,
        )
        for table in ("core_commandintake", "core_authoritydecision", *VERSION_TABLE_FOR.values()):
            self.assertEqual(after_move[table], after_first[table], table)

        with self.assertRaises(S015AuthorityDenied) as second:
            service.save_gateway_draft(draft)

        self.assertIs(second.exception.refusal_code, S015AuthorityRefusalCode.DENIED)
        self.assertNotEqual(
            second.exception.authority_decision_reference,
            first.exception.authority_decision_reference,
        )
        self.assertEqual(
            _changed_public_rows(after_move, _public_row_counts()),
            {"core_authoritydecision": 1},
        )
        intake = _intake_row(member, draft)
        rows = AuthorityDecision.objects.filter(
            actor_id=member.pk, action=draft.action.value, idempotency_key=draft.idempotency_key,
        )
        self.assertEqual(rows.count(), 2)
        self.assertEqual({row.outcome for row in rows}, {"REFUSED"})
        self.assertEqual({row.evaluated_at for row in rows}, {intake.received_at})
        self.assertNotEqual(provider.requests[0].active_membership_head_reference,
                            provider.requests[1].active_membership_head_reference)
        for model in GATEWAY_VERSION_MODELS:
            self.assertFalse(model.objects.filter(living_organism=self.organism, actor_id=member.pk).exists())

    def test_iw_33_access_epoch_change_after_admission_refuses_without_qualifying_for_both_entries(self):
        """IW-33 (D:1670; CH-39): a hook after admission bumps the actor's access epoch on another
        connection; the executor's V5 refuses with S015ApplicationAuthorityUnavailable (the family
        class, by the executor's literal entry), nothing qualified or written, the intake kept; the
        same request again mismatches at the boundary; a new key succeeds at its own time."""
        draft_version = self.service.save_gateway_draft(
            self._draft(OrganismAction.EFFECT_APPLICATION_SETTINGS, SETTINGS_CLOSED, suffix="iw33-setup"),
        )
        cases = (
            (
                IntakeEntry.SAVE_GATEWAY_DRAFT,
                "_execute_save_gateway_draft",
                self.service.save_gateway_draft,
                lambda suffix: self._draft(OrganismAction.EFFECT_APPLICATION_QUESTION, QUESTION_FIELDS, suffix=suffix),
                {"core_commandintake": 1, "core_loapplicationquestionversion": 1, "core_authoritydecision": 1},
            ),
            (
                IntakeEntry.EFFECT_GATEWAY_VERSION,
                "_execute_effect_gateway_version",
                self.service.effect_gateway_version,
                lambda suffix: self._effect(
                    OrganismAction.EFFECT_APPLICATION_SETTINGS, draft_version.version_uuid, suffix=suffix,
                ),
                {
                    "core_commandintake": 1,
                    "core_livingorganismevent": 1,
                    "core_authoritydecision": 1,
                    "core_organismcommandreceipt": 1,
                },
            ),
        )
        for entry, executor_name, public_method, build, success_delta in cases:
            with self.subTest(entry=entry):
                command = build(f"iw33-{entry.value.lower()}")
                before = _public_row_counts()
                provider_calls = len(self.provider.requests)
                original = getattr(self.service, executor_name)

                def change_epoch_then_execute(bound_command, bound, original=original):
                    worker = start_worker(
                        lambda: Identity.objects.filter(pk=self.founder.pk).update(access_epoch=F("access_epoch") + 1)
                    )
                    worker.thread.join(timeout=15)
                    if worker.thread.is_alive():
                        cleanup_workers([worker])
                        raise AssertionError("access-epoch update worker did not finish")
                    self.assertEqual(worker.result(timeout=5), "ok")
                    return original(bound_command, bound)

                with patch.object(self.service, executor_name, side_effect=change_epoch_then_execute):
                    with self.assertRaises(S015ApplicationAuthorityUnavailable):
                        public_method(command)
                after_failure = _public_row_counts()
                self.assertEqual(_changed_public_rows(before, after_failure), {"core_commandintake": 1})
                self.assertEqual(len(self.provider.requests), provider_calls)
                old_intake = _intake_row(self.founder, command)
                with self.assertRaises(S015CommandReplayMismatch):
                    public_method(command)
                self.assertEqual(_public_row_counts(), after_failure)
                self.assertEqual(CommandIntake.objects.get(pk=old_intake.pk).received_at, old_intake.received_at)
                self.assertNotEqual(
                    old_intake.actor_access_epoch, Identity.objects.get(pk=self.founder.pk).access_epoch,
                )

                fresh_command = build(f"iw33-new-{entry.value.lower()}")
                public_method(fresh_command)
                self.assertEqual(_changed_public_rows(after_failure, _public_row_counts()), success_delta)
                _assert_intake_row(self, entry, fresh_command, Identity.objects.get(pk=self.founder.pk))

    def test_iw_34_reuse_of_a_captured_binding_is_refused_before_any_write(self):
        """IW-34 (a)-(c) and the non-None parent (D:1671; C2A §C4, C2B:174): a genuine production bound
        request captured by a test-only hook from a real public admission, reused against the executors
        with (a) one changed material input (V3), (b) another key and another reference (V2), (c) the
        other entry's executor (V1), and with a parent attached (an application entry has no parent);
        each refuses with the technical "not the admitted request" class before qualification or any
        write, measured over every public table from after the setup admission."""
        draft = self._draft(OrganismAction.EFFECT_APPLICATION_SETTINGS, SETTINGS_CLOSED, suffix="iw34")
        captured = {}
        original = self.service._execute_save_gateway_draft

        def capture(command, bound):
            captured["bound"] = bound
            return original(command, bound)

        with patch.object(self.service, "_execute_save_gateway_draft", side_effect=capture):
            version = self.service.save_gateway_draft(draft)
        bound = captured["bound"]
        self.assertIsNotNone(bound.intake_id)
        before = _public_row_counts()
        provider_calls = len(self.provider.requests)
        reused = {
            "a-changed-fields": replace(draft, fields=SETTINGS_OPEN),
            "a-changed-effective_at": replace(draft, effective_at=draft.effective_at + timedelta(seconds=1)),
            "b-changed-key": replace(draft, idempotency_key=f"changed:{draft.idempotency_key}"),
            "b-changed-reference": replace(draft, request_reference=f"changed:{draft.request_reference}"),
        }
        for label, changed in reused.items():
            with self.subTest(case=label), self.assertRaisesRegex(
                IntakeAdmissionTechnicalFailure, "not the request that was admitted",
            ):
                self.service._execute_save_gateway_draft(changed, bound)
        other_entry = _effect_command(
            self.founder.credential, self.organism_uuid, draft.action, version.version_uuid,
            suffix=f"iw34:{self.tag}", at=self.now,
        )
        other_entry = replace(
            other_entry, request_reference=draft.request_reference, idempotency_key=draft.idempotency_key,
        )
        with self.subTest(case="c-other-entry"), self.assertRaisesRegex(
            IntakeAdmissionTechnicalFailure, "not the request that was admitted",
        ):
            self.service._execute_effect_gateway_version(other_entry, bound)
        parent = BoundRequest.for_test(
            IntakeEntry.SAVE_GATEWAY_DRAFT,
            self._draft(OrganismAction.EFFECT_APPLICATION_SETTINGS, SETTINGS_CLOSED, suffix="iw34-parent"),
            received_at=database_now(),
            actor=_actor_ref(self.founder),
        )
        with_parent = replace(bound, parent=parent, _sentinel=command_intake_module._CONSTRUCTOR_SENTINEL)
        with self.subTest(case="unexpected-parent"), self.assertRaisesRegex(
            IntakeAdmissionTechnicalFailure, "not the request that was admitted",
        ):
            self.service._execute_save_gateway_draft(draft, with_parent)
        self.assertEqual(_public_row_counts(), before)
        self.assertEqual(len(self.provider.requests), provider_calls)
        self.assertEqual(
            CommandIntake.objects.filter(actor_id=self.founder.pk, idempotency_key=draft.idempotency_key).count(), 1,
        )

    def test_iw_34_effect_reuse_of_a_captured_binding_is_refused_before_any_write(self):
        """IW-34 (a)-(c), (f) and the non-None parent, effect entry (D:1671; C4:304; Advisor F1): a
        genuine production bound request captured by a test-only hook from a real public effect
        admission (the IW-00 (c) capture form), reused against the effect executor with (a) one
        changed material input (another version_uuid; another effective_at: V3), (b) another key
        and another reference (V2), and the unchanged command with a non-None parent attached (an
        application entry has no parent); and (c) against the draft executor with a draft command
        carrying the effect's key and reference (V1). Each refuses with the technical "not the
        admitted request" class at the binding check (CH-36: after the actor lock, before the
        mutex), before qualification or any write, measured over every public table from after the
        setup effect. (f), the public methods taking no bound request, is witnessed for both entries
        at IW-00 (c). Times: the captured binding carries the setup call's own database-assigned
        admission time; the parent is a test-only bound request taking a database-clock reading
        after the committed setup and is refused before any read at that time (CH-42; CH-15 (iv)).
        No sleep, no backdated row, no relaxed guard, no product hook."""
        draft = self._draft(OrganismAction.EFFECT_APPLICATION_SETTINGS, SETTINGS_CLOSED, suffix="iw34-effect-draft")
        version = self.service.save_gateway_draft(draft)
        effect = self._effect(OrganismAction.EFFECT_APPLICATION_SETTINGS, version.version_uuid, suffix="iw34-effect")
        captured = {}
        original = self.service._execute_effect_gateway_version

        def capture(command, bound):
            captured["bound"] = bound
            return original(command, bound)

        with patch.object(self.service, "_execute_effect_gateway_version", side_effect=capture):
            result = self.service.effect_gateway_version(effect)
        self.assertIsInstance(result, GatewayEffectCommandResult)
        self.assertEqual(result.version_uuid, version.version_uuid)
        bound = captured["bound"]
        self.assertIsInstance(bound, BoundRequest)
        self.assertIs(bound.entry, IntakeEntry.EFFECT_GATEWAY_VERSION)
        self.assertIsNotNone(bound.intake_id)
        self.assertIsNone(bound.parent)
        before = _public_row_counts()
        provider_calls = len(self.provider.requests)
        reused = {
            "a-changed-version_uuid": replace(effect, version_uuid=uuid4()),
            "a-changed-effective_at": replace(effect, effective_at=effect.effective_at + timedelta(seconds=1)),
            "b-changed-key": replace(effect, idempotency_key=f"changed:{effect.idempotency_key}"),
            "b-changed-reference": replace(effect, request_reference=f"changed:{effect.request_reference}"),
        }
        for label, changed in reused.items():
            with self.subTest(case=label), self.assertRaisesRegex(
                IntakeAdmissionTechnicalFailure, "not the request that was admitted",
            ):
                self.service._execute_effect_gateway_version(changed, bound)
        parent = BoundRequest.for_test(
            IntakeEntry.SAVE_GATEWAY_DRAFT,
            self._draft(OrganismAction.EFFECT_APPLICATION_SETTINGS, SETTINGS_CLOSED, suffix="iw34-effect-parent"),
            received_at=database_now(),
            actor=_actor_ref(self.founder),
        )
        with_parent = replace(bound, parent=parent, _sentinel=command_intake_module._CONSTRUCTOR_SENTINEL)
        with self.subTest(case="unexpected-parent"), self.assertRaisesRegex(
            IntakeAdmissionTechnicalFailure, "not the request that was admitted",
        ):
            self.service._execute_effect_gateway_version(effect, with_parent)
        other_entry = replace(
            self._draft(OrganismAction.EFFECT_APPLICATION_SETTINGS, SETTINGS_CLOSED, suffix="iw34-effect"),
            request_reference=effect.request_reference,
            idempotency_key=effect.idempotency_key,
        )
        with self.subTest(case="c-other-entry"), self.assertRaisesRegex(
            IntakeAdmissionTechnicalFailure, "not the request that was admitted",
        ):
            self.service._execute_save_gateway_draft(other_entry, bound)
        self.assertEqual(_public_row_counts(), before)
        self.assertEqual(len(self.provider.requests), provider_calls)
        self.assertEqual(
            CommandIntake.objects.filter(actor_id=self.founder.pk, idempotency_key=effect.idempotency_key).count(), 1,
        )

    def test_ch_37_denial_arm_passes_the_bound_time_to_the_authored_message(self):
        """CH-37 message site, the denial arm, on the real route (as test_sb2_birthing:1554): the
        production provider denies enabling GOVERNANCE on a born LO without a GOVERNANCE Circle
        (SBG_GOVERNANCE_CIRCLE_MISSING); authority_atomic commits the REFUSED decision; the wrapper
        words the refusal with recorded_at == the bound receive time, never None."""
        service = self.route.application_service
        draft = service.save_gateway_draft(
            self._draft(OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION, GOVERNANCE_ON, suffix="ch37-denial-draft"),
        )
        effect = self._effect(OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION, draft.version_uuid, suffix="ch37-denial")
        before = _public_row_counts()
        seen = []
        original_message = application_service_module._governance_enabling_refusal_message

        def spy(**kwargs):
            seen.append(kwargs)
            return original_message(**kwargs)

        with patch.object(application_service_module, "_governance_enabling_refusal_message", side_effect=spy):
            with self.assertRaises(S015ApplicationRefused) as refused:
                service.effect_gateway_version(effect)
        self.assertIs(refused.exception.refusal_code, S015AuthorityRefusalCode.SBG_GOVERNANCE_CIRCLE_MISSING)
        self.assertIsInstance(refused.exception.__cause__, S015AuthorityDenied)
        self.assertEqual(
            _changed_public_rows(before, _public_row_counts()),
            {"core_commandintake": 1, "core_authoritydecision": 1},
        )
        intake = _assert_intake_row(self, IntakeEntry.EFFECT_GATEWAY_VERSION, effect, self.founder)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["recorded_at"], intake.received_at)
        self.assertEqual(seen[0]["effective_at"], effect.effective_at)
        self.assertIs(seen[0]["code"], S015AuthorityRefusalCode.SBG_GOVERNANCE_CIRCLE_MISSING)
        self.assertEqual(_decision_row(self.founder, effect).outcome, "REFUSED")

    def test_ch_37_integrity_error_arm_passes_the_bound_time_to_the_authored_message(self):
        """CH-37 message site, the IntegrityError arm: with a permissive provider the qualification
        passes and the gateway-effect guard (0045) refuses the enabling event with the readiness code as
        its primary message; the block rolls back (+1 intake only) and the wrapper words the refusal
        with recorded_at == the bound receive time, never None."""
        draft = self.service.save_gateway_draft(
            self._draft(OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION, GOVERNANCE_ON, suffix="ch37-guard-draft"),
        )
        effect = self._effect(OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION, draft.version_uuid, suffix="ch37-guard")
        before = _public_row_counts()
        seen = []
        original_message = application_service_module._governance_enabling_refusal_message

        def spy(**kwargs):
            seen.append(kwargs)
            return original_message(**kwargs)

        with patch.object(application_service_module, "_governance_enabling_refusal_message", side_effect=spy):
            with self.assertRaises(S015ApplicationRefused) as refused:
                self.service.effect_gateway_version(effect)
        self.assertIs(refused.exception.refusal_code, S015AuthorityRefusalCode.SBG_GOVERNANCE_CIRCLE_MISSING)
        self.assertIsInstance(refused.exception.__cause__, IntegrityError)
        self.assertEqual(_changed_public_rows(before, _public_row_counts()), {"core_commandintake": 1})
        intake = _assert_intake_row(self, IntakeEntry.EFFECT_GATEWAY_VERSION, effect, self.founder)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["recorded_at"], intake.received_at)
        self.assertEqual(seen[0]["effective_at"], effect.effective_at)
        self.assertFalse(
            AuthorityDecision.objects.filter(
                actor_id=self.founder.pk, action=effect.action.value, idempotency_key=effect.idempotency_key,
            ).exists()
        )

    def test_b1_malformed_input_is_refused_before_admission_even_when_the_actor_is_unqualified(self):
        """OPEN-1 (a), B1 (the Human Governor's recorded precedence): a request that is both malformed
        and unqualified gets the malformed-input refusal first, with no intake row and no decision; the
        control shows the same actor's well-formed request is admitted and then refused at the office
        check, leaving its intake row."""
        member = add_active_elector(self.route, tag="b1")
        before = _public_row_counts()
        malformed = self._draft(
            OrganismAction.EFFECT_APPLICATION_SETTINGS, {**SETTINGS_CLOSED, "not_a_field": True},
            suffix="b1-malformed", credential=member.credential,
        )
        with CaptureQueriesContext(connection) as queries:
            with self.assertRaisesRegex(S015ApplicationRefused, "invalid gateway draft fields"):
                self.service.save_gateway_draft(malformed)
        self.assertEqual(len(queries), 0)
        self.assertEqual(_public_row_counts(), before)
        effect_malformed = replace(
            self._effect(
                OrganismAction.EFFECT_APPLICATION_SETTINGS, uuid4(), suffix="b1-effect", credential=member.credential,
            ),
            version_uuid="not-a-uuid",
        )
        with CaptureQueriesContext(connection) as queries:
            with self.assertRaisesRegex(TypeError, "version_uuid must be a UUID"):
                self.service.effect_gateway_version(effect_malformed)
        self.assertEqual(len(queries), 0)
        self.assertEqual(_public_row_counts(), before)

        control = self._draft(
            OrganismAction.EFFECT_APPLICATION_SETTINGS, SETTINGS_CLOSED,
            suffix="b1-control", credential=member.credential,
        )
        with self.assertRaisesRegex(S015ApplicationRefused, "effective Coordinator office is required"):
            self.service.save_gateway_draft(control)
        self.assertEqual(_changed_public_rows(before, _public_row_counts()), {"core_commandintake": 1})
        _assert_intake_row(self, IntakeEntry.SAVE_GATEWAY_DRAFT, control, member)

    def test_ch_9_g3_activation_scenario_has_no_unaccounted_record(self):
        """CH-9 (§F2.5): one self-contained scenario on the route's production provider makes real
        public calls covering both entries and all three relations; asserts the non-empty intake,
        receipt and AuthorityDecision sets with their actions, keys, actors and evaluated times (drafts
        write no receipt, D:801); then runs the activation step over G3 and requires no unaccounted
        record. Relies on no other test's data."""
        service = self.route.application_service
        intake_count = CommandIntake.objects.count()
        receipt_count = OrganismCommandReceipt.objects.count()
        decision_count = AuthorityDecision.objects.count()
        drafts, effects = [], []
        for action, fields in (
            (OrganismAction.EFFECT_APPLICATION_SETTINGS, SETTINGS_CLOSED),
            (OrganismAction.EFFECT_APPLICATION_QUESTION, QUESTION_FIELDS),
            (OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION, GOVERNANCE_OFF),
        ):
            draft = self._draft(action, fields, suffix=f"ch9:{action.value}")
            version = service.save_gateway_draft(draft)
            effect = self._effect(action, version.version_uuid, suffix=f"ch9:{action.value}")
            result = service.effect_gateway_version(effect)
            self.assertEqual(result.version_uuid, version.version_uuid)
            drafts.append(draft)
            effects.append(effect)
        commands = drafts + effects
        keys = [command.idempotency_key for command in commands]
        intakes = list(CommandIntake.objects.filter(actor_id=self.founder.pk, idempotency_key__in=keys))
        receipts = list(OrganismCommandReceipt.objects.filter(actor_id=self.founder.pk, idempotency_key__in=keys))
        decisions = list(AuthorityDecision.objects.filter(actor_id=self.founder.pk, idempotency_key__in=keys))
        self.assertEqual(len(intakes), 6)
        self.assertEqual(len(receipts), 3)
        self.assertEqual(len(decisions), 6)
        self.assertEqual(CommandIntake.objects.count(), intake_count + 6)
        self.assertEqual(OrganismCommandReceipt.objects.count(), receipt_count + 3)
        self.assertEqual(AuthorityDecision.objects.count(), decision_count + 6)
        self.assertEqual(
            {row.entry for row in intakes},
            {IntakeEntry.SAVE_GATEWAY_DRAFT.value, IntakeEntry.EFFECT_GATEWAY_VERSION.value},
        )
        self.assertEqual({row.action for row in intakes}, {action.value for action in GATEWAY_ACTIONS})
        self.assertEqual({row.idempotency_key for row in receipts}, {command.idempotency_key for command in effects})
        for draft in drafts:
            _assert_intake_row(self, IntakeEntry.SAVE_GATEWAY_DRAFT, draft, self.founder)
        for effect in effects:
            _assert_intake_row(self, IntakeEntry.EFFECT_GATEWAY_VERSION, effect, self.founder)
        by_key = {row.idempotency_key: row for row in intakes}
        for decision in decisions:
            with self.subTest(key=decision.idempotency_key):
                self.assertEqual(decision.outcome, "QUALIFIED")
                self.assertEqual(decision.evaluated_at, by_key[decision.idempotency_key].received_at)
        self.assertEqual(
            run_activation_step("default", (IntakeEntry.SAVE_GATEWAY_DRAFT, IntakeEntry.EFFECT_GATEWAY_VERSION)),
            [],
        )

    def test_f2_2_a_draft_and_an_effect_under_the_same_key_are_refused_at_admission_as_different_requests(self):
        """C4 §F2.2 same-key admission (C4:211-212, "a behaviour change at activation"; D:809-813;
        CI:1365-1372): a draft and an effect under the same (actor, action, key) are one key. Public
        methods only. The founder's draft under (R, K) is admitted and creates a version; an effect
        for the same LO and the same action under the same (R, K), naming that version, is refused at
        admission as a different request (the stored entry differs): S015CommandReplayMismatch by
        class; nothing written by it (+0 of every public table: no intake, no decision, no version
        row, no LO event and no receipt, so the draft stays uneffected); the provider not consulted
        by it; the draft's intake row unchanged and still the only row for the key. Public admission
        only: T is database-assigned at admission, so CH-42's converted-call rule does not apply."""
        draft = self._draft(OrganismAction.EFFECT_APPLICATION_SETTINGS, SETTINGS_CLOSED, suffix="f22-same-key")
        version = self.service.save_gateway_draft(draft)
        self.assertIsInstance(version, LOApplicationSettingsVersion)
        intake = _assert_intake_row(self, IntakeEntry.SAVE_GATEWAY_DRAFT, draft, self.founder)
        intake_row = CommandIntake.objects.filter(pk=intake.pk).values().get()
        before = _public_row_counts()
        provider_calls = len(self.provider.requests)
        same_key_effect = replace(
            self._effect(OrganismAction.EFFECT_APPLICATION_SETTINGS, version.version_uuid, suffix="f22-same-key"),
            request_reference=draft.request_reference,
            idempotency_key=draft.idempotency_key,
        )
        self.assertEqual(same_key_effect.action, draft.action)

        with self.assertRaises(S015CommandReplayMismatch):
            self.service.effect_gateway_version(same_key_effect)

        self.assertEqual(_public_row_counts(), before)
        self.assertEqual(len(self.provider.requests), provider_calls)
        self.assertEqual(CommandIntake.objects.filter(pk=intake.pk).values().get(), intake_row)
        self.assertEqual(
            CommandIntake.objects.filter(
                actor_id=self.founder.pk, action=draft.action.value, idempotency_key=draft.idempotency_key,
            ).count(),
            1,
        )

    def test_f2_2_an_effect_and_a_draft_under_the_same_key_are_refused_at_admission_in_the_reverse_order(self):
        """C4 §F2.2 same-key admission, the reverse order (C4:211-212; D:809-813; CI:1365-1372): the
        effect is admitted first. The founder's draft (under its own key) is effected under (R, K);
        a draft for the same LO and the same action under the same (R, K) is refused at admission as
        a different request: S015CommandReplayMismatch by class; +0 of every public table; the
        provider not consulted by it; the effect's intake row unchanged and still the only row for
        the key. No new fixture: the two public calls that precede the refused one are on the same
        LO and founder. Public admission only: T is database-assigned at admission."""
        draft = self._draft(OrganismAction.EFFECT_APPLICATION_SETTINGS, SETTINGS_CLOSED, suffix="f22-reverse-draft")
        version = self.service.save_gateway_draft(draft)
        effect = self._effect(OrganismAction.EFFECT_APPLICATION_SETTINGS, version.version_uuid, suffix="f22-reverse")
        self.assertIsInstance(self.service.effect_gateway_version(effect), GatewayEffectCommandResult)
        effect_intake = _assert_intake_row(self, IntakeEntry.EFFECT_GATEWAY_VERSION, effect, self.founder)
        effect_row = CommandIntake.objects.filter(pk=effect_intake.pk).values().get()
        before = _public_row_counts()
        provider_calls = len(self.provider.requests)
        same_key_draft = replace(
            self._draft(OrganismAction.EFFECT_APPLICATION_SETTINGS, SETTINGS_CLOSED, suffix="f22-reverse"),
            request_reference=effect.request_reference,
            idempotency_key=effect.idempotency_key,
        )
        self.assertEqual(same_key_draft.action, effect.action)

        with self.assertRaises(S015CommandReplayMismatch):
            self.service.save_gateway_draft(same_key_draft)

        self.assertEqual(_public_row_counts(), before)
        self.assertEqual(len(self.provider.requests), provider_calls)
        self.assertEqual(CommandIntake.objects.filter(pk=effect_intake.pk).values().get(), effect_row)
        self.assertEqual(
            CommandIntake.objects.filter(
                actor_id=self.founder.pk, action=effect.action.value, idempotency_key=effect.idempotency_key,
            ).count(),
            1,
        )

    def test_ch_40_located_edit_retry_is_withheld_when_the_actor_no_longer_holds_the_office(self):
        """CH-40/B3 edit branch, ended office (D:1377-1380, D:1382-1391, D:1403): the edit counterpart
        of the creation variant. Draft D is created; edit E (its own key, applications_open = True)
        completes; the founder's Coordinator office ends through the public role route; E is
        repeated. The lookup finds E's QUALIFIED decision (target.version_uuid = D) -> nothing
        re-applied -> the version the decision names is served through serve_replay at viewer.now:
        a GatewayDraftResult with located=True, version_uuid withheld for this viewer, no identifier
        of D anywhere in the response, received_at = E's bound time and never D's creation time.
        Nothing written and the provider not consulted by the retry; no UPDATE, INSERT or DELETE on
        the version table among the retry's statements; D's row is unchanged and its fields are
        E's. A build that serves the creation branch through serve_replay but returns the edit
        branch raw fails here. Public calls only: T is database-assigned at admission and the retry
        binds to E's stored T; the viewer time is fresh at serving (CH-38 (3))."""
        creation = self._draft(OrganismAction.EFFECT_APPLICATION_SETTINGS, SETTINGS_CLOSED, suffix="ch40-edit-hidden-d")
        draft = self.service.save_gateway_draft(creation)
        edit = self._draft(
            OrganismAction.EFFECT_APPLICATION_SETTINGS, {"applications_open": True},
            suffix="ch40-edit-hidden-e", version_uuid=draft.version_uuid,
        )
        edited = self.service.save_gateway_draft(edit)
        self.assertIsInstance(edited, LOApplicationSettingsVersion)
        self.assertEqual(edited.pk, draft.pk)
        draft.refresh_from_db()
        self.assertIs(draft.applications_open, True)
        edit_intake = _intake_row(self.founder, edit)
        self.assertEqual(_decision_row(self.founder, edit).target["version_uuid"], str(draft.version_uuid))
        self._end_founder_office()
        version_row = LOApplicationSettingsVersion.objects.filter(pk=draft.pk).values().get()
        before = _public_row_counts()
        provider_calls = len(self.provider.requests)

        with CaptureQueriesContext(connection) as queries:
            result = self.service.save_gateway_draft(edit)

        self.assertIsInstance(result, GatewayDraftResult)
        self.assertIs(result.located, True)
        self.assertEqual(result.version_uuid, WithheldField("version_uuid", "PROCESSING"))
        self.assertEqual(withheld_fields(result), (WithheldField("version_uuid", "PROCESSING"),))
        self.assertEqual(result.living_organism_uuid, self.organism_uuid)
        self.assertEqual(result.action, edit.action)
        self.assertEqual(result.effective_at, edit.effective_at)
        self.assertEqual(result.received_at, edit_intake.received_at)
        self.assertNotEqual(result.received_at, draft.received_at)
        _no_uuid_in(self, result, draft.version_uuid)
        self.assertEqual(_public_row_counts(), before)
        self.assertEqual(len(self.provider.requests), provider_calls)
        self.assertEqual(
            [
                query["sql"]
                for query in queries.captured_queries
                if "core_loapplicationsettingsversion" in query["sql"]
                and query["sql"].lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE"))
            ],
            [],
        )
        self.assertEqual(LOApplicationSettingsVersion.objects.filter(pk=draft.pk).values().get(), version_row)
        self.assertIs(version_row["applications_open"], True)


@POSTGRES
class GatewayConcurrencyTests(TransactionTestCase):
    """IW-21b and IW-21c: concurrent identical draft creations on two connections.

    C1 is paused by a test-only hook on the existing name
    organism_application_service._lock_actor_membership_context (imported at
    APP:51, called at APP:1182), after that lock is held; C2 runs the same
    request and is observed, through pg_blocking_pids, waiting at AL (the
    actor's Identity row, the first lock every executor takes) before anything
    is released or terminated. Bounded completion: session-level lock_timeout on
    C2's connection, join deadlines, cleanup of workers still alive. The
    witnesses prove the serialisation and the lookup's verdict under each
    boundary; they do not prove an interrupted in-flight COMMIT, which is not
    constructible deterministically (OPEN-7 (a); D:1653-1654).
    """

    def setUp(self):
        self.tag = f"h100-gateway-race-{fresh()}"
        self.route = prepare_route(tag=self.tag)
        birth(self.route)
        self.organism = self.route.organism
        self.organism_uuid = self.organism.organism_id
        self.founder = Identity.objects.select_related("credential").get(pk=self.route.founder.pk)
        self.now = route_test_now(self.route)
        self.provider = _Provider()
        self.service = OrganismApplicationService(
            authority=S015Authority(provider=self.provider),
            clock=database_now,
        )
        self.assertTrue(LOApplicationGateway.objects.filter(living_organism=self.organism).exists())

    def _command(self, suffix):
        return _draft_command(
            self.founder.credential, self.organism_uuid, OrganismAction.EFFECT_APPLICATION_SETTINGS,
            SETTINGS_CLOSED, suffix=f"{suffix}:{self.tag}", at=self.now,
        )

    def _pause_hook(self, entered, release, first_ident):
        original = application_service_module._lock_actor_membership_context

        def pause_after_context_lock(*args, **kwargs):
            result = original(*args, **kwargs)
            if not first_ident:
                first_ident.append(get_ident())
            if get_ident() == first_ident[0]:
                entered.set()
                if not release.wait(timeout=20):
                    raise AssertionError("membership-context pause hook timed out")
            return result

        return patch.object(
            application_service_module, "_lock_actor_membership_context", side_effect=pause_after_context_lock,
        )

    def _assert_single_creation(self, command):
        intakes = CommandIntake.objects.filter(actor_id=self.founder.pk, idempotency_key=command.idempotency_key)
        self.assertEqual(intakes.count(), 1)
        intake = intakes.get()
        versions = LOApplicationSettingsVersion.objects.filter(
            living_organism=self.organism, actor_id=self.founder.pk, received_at=intake.received_at,
            occurred_at=command.occurred_at, effective_at=command.effective_at,
        )
        self.assertEqual(versions.count(), 1)
        decisions = AuthorityDecision.objects.filter(
            actor_id=self.founder.pk, action=command.action.value, idempotency_key=command.idempotency_key,
        )
        self.assertEqual(decisions.count(), 1)
        self.assertEqual(decisions.get().outcome, "QUALIFIED")
        self.assertEqual(decisions.get().evaluated_at, intake.received_at)
        return intake, versions.get()

    def _run_overlap(self, command, *, first_operation, before_release):
        """Start C1 (paused after the membership-context lock), start C2 on the same request, observe C2
        waiting at AL on C1, call before_release(c1, c2, cursor), release C1, join both."""
        entered, release, first_ident = Event(), Event(), []
        results = {}
        first = second = None
        try:
            with self._pause_hook(entered, release, first_ident):
                first = start_worker(lambda: first_operation(results))
                self.assertTrue(entered.wait(timeout=20), "C1 did not reach the membership-context lock")

                def second_operation():
                    with connection.cursor() as cursor:
                        cursor.execute("SET lock_timeout = '10s'")
                    results["second"] = self.service.save_gateway_draft(command)

                second = start_worker(second_operation)
                with connection.cursor() as cursor:
                    wait_until_locked(cursor, second.pid(), first.pid(), relations={"core_identity"}, timeout=10)
                    before_release(first, second, cursor)
        finally:
            release.set()
            for worker in (first, second):
                if worker is not None:
                    worker.thread.join(timeout=30)
                    if worker.thread.is_alive():
                        cleanup_workers([worker])
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        return first, second, results

    def test_iw_21b_concurrent_identical_creations_serialise_at_the_actor_lock_and_the_second_locates(self):
        """IW-21b (D:1653), the main schedule: C2 waits at AL; C1 commits; C2 acquires AL, MX and the
        context lock; its fresh lookup finds C1's decision -> the located result (R1). Exactly 1
        version, 1 QUALIFIED decision, 1 intake."""
        command = self._command("iw21b")
        before = _public_row_counts()

        def first_operation(results):
            results["first"] = self.service.save_gateway_draft(command)

        first, second, results = self._run_overlap(
            command, first_operation=first_operation, before_release=lambda *args: None,
        )
        self.assertEqual(first.result(timeout=5), "ok")
        self.assertEqual(second.result(timeout=5), "ok")
        intake, version = self._assert_single_creation(command)
        self.assertIsInstance(results["first"], LOApplicationSettingsVersion)
        self.assertEqual(results["first"].pk, version.pk)
        located = results["second"]
        self.assertIsInstance(located, GatewayDraftResult)
        self.assertIs(located.located, True)
        self.assertEqual(located.version_uuid, version.version_uuid)
        self.assertEqual(located.received_at, intake.received_at)
        self.assertEqual(
            _changed_public_rows(before, _public_row_counts()),
            {"core_commandintake": 1, "core_loapplicationsettingsversion": 1, "core_authoritydecision": 1},
        )

    def test_iw_21b_boundary_i_terminated_holder_before_commit_lets_the_waiter_create_once(self):
        """IW-21b boundary (i) (D:1653): pg_terminate_backend(C1) from a third connection while C1 is
        paused before COMMIT -> PostgreSQL aborts C1's transaction; C2 proceeds, its lookup finds
        nothing and it creates once. Exactly 1 version, 1 QUALIFIED decision, 1 intake; C1's client
        sees the terminated-connection error, not a version."""
        command = self._command("iw21b-i")
        before = _public_row_counts()

        def first_operation(results):
            results["first"] = self.service.save_gateway_draft(command)

        def terminate_first(first, second, cursor):
            cursor.execute("SELECT pg_terminate_backend(%s::int)", [first.pid()])
            self.assertTrue(cursor.fetchone()[0])

        first, second, results = self._run_overlap(
            command, first_operation=first_operation, before_release=terminate_first,
        )
        first_result = first.result(timeout=5)
        self.assertIsInstance(first_result, Exception)
        self.assertNotIn("first", results)
        self.assertEqual(second.result(timeout=5), "ok")
        intake, version = self._assert_single_creation(command)
        self.assertIsInstance(results["second"], LOApplicationSettingsVersion)
        self.assertEqual(results["second"].pk, version.pk)
        self.assertEqual(version.received_at, intake.received_at)
        self.assertEqual(
            _changed_public_rows(before, _public_row_counts()),
            {"core_commandintake": 1, "core_loapplicationsettingsversion": 1, "core_authoritydecision": 1},
        )

    def test_iw_21b_boundary_ii_client_error_after_acknowledged_commit_still_locates(self):
        """IW-21b boundary (ii), OPEN-7 (a) (D:1653): C1's worker raises after the public call returned
        (COMMIT acknowledged, caller errors); C2 locates the committed draft. Exactly 1 version, 1
        QUALIFIED decision, 1 intake. This proves the acknowledged-commit/client-failure boundary, not
        an interrupted in-flight COMMIT."""
        command = self._command("iw21b-ii")
        before = _public_row_counts()

        def first_operation(results):
            results["committed"] = self.service.save_gateway_draft(command)
            raise _ClientErrorAfterCommit("test-only client error after the acknowledged commit")

        first, second, results = self._run_overlap(
            command, first_operation=first_operation, before_release=lambda *args: None,
        )
        self.assertIsInstance(first.result(timeout=5), _ClientErrorAfterCommit)
        self.assertEqual(second.result(timeout=5), "ok")
        intake, version = self._assert_single_creation(command)
        self.assertEqual(results["committed"].pk, version.pk)
        located = results["second"]
        self.assertIsInstance(located, GatewayDraftResult)
        self.assertIs(located.located, True)
        self.assertEqual(located.version_uuid, version.version_uuid)
        self.assertEqual(located.received_at, intake.received_at)
        self.assertEqual(
            _changed_public_rows(before, _public_row_counts()),
            {"core_commandintake": 1, "core_loapplicationsettingsversion": 1, "core_authoritydecision": 1},
        )

    def test_iw_21c_first_creation_rolls_back_after_its_insert_and_the_waiter_creates_once(self):
        """IW-21c (D:1654): C1's test-only post_save receiver raises after the version insert (C1's thread
        only) -> C1 rolls back; C2, blocked at AL, proceeds, finds nothing and creates once. Exactly 1
        version, 1 QUALIFIED decision, 1 intake."""
        command = self._command("iw21c")
        before = _public_row_counts()
        first_thread = []

        def raise_after_insert(sender, instance, created, **kwargs):
            if created and first_thread and get_ident() == first_thread[0]:
                raise _InjectedAfterVersionInsert("test-only failure after the version insert on C1")

        dispatch_uid = f"h100-iw21c-{self.tag}"
        post_save.connect(
            raise_after_insert, sender=LOApplicationSettingsVersion, weak=False, dispatch_uid=dispatch_uid,
        )
        self.addCleanup(post_save.disconnect, sender=LOApplicationSettingsVersion, dispatch_uid=dispatch_uid)

        def first_operation(results):
            first_thread.append(get_ident())
            results["first"] = self.service.save_gateway_draft(command)

        first, second, results = self._run_overlap(
            command, first_operation=first_operation, before_release=lambda *args: None,
        )
        self.assertIsInstance(first.result(timeout=5), _InjectedAfterVersionInsert)
        self.assertNotIn("first", results)
        self.assertEqual(second.result(timeout=5), "ok")
        intake, version = self._assert_single_creation(command)
        self.assertIsInstance(results["second"], LOApplicationSettingsVersion)
        self.assertEqual(results["second"].pk, version.pk)
        self.assertEqual(
            _changed_public_rows(before, _public_row_counts()),
            {"core_commandintake": 1, "core_loapplicationsettingsversion": 1, "core_authoritydecision": 1},
        )


@POSTGRES
class GatewayIdentityTests(TransactionTestCase):
    """IW-21d (D:1655): tau is immutable across the permitted lifecycle and the database refuses
    identity rewrites; the static guard is GatewayWriterCensusTests.

    Isolates the version row's identity columns under the real edit and effect
    commands and under direct SQL. It does not prove that no future path can
    delete an uneffected draft (A3 is service-only, witnessed by the census).
    """

    def setUp(self):
        self.tag = f"h100-gateway-tau-{fresh()}"
        self.route = prepare_route(tag=self.tag)
        birth(self.route)
        self.organism = self.route.organism
        self.organism_uuid = self.organism.organism_id
        self.founder = Identity.objects.select_related("credential").get(pk=self.route.founder.pk)
        self.now = route_test_now(self.route)
        self.service = OrganismApplicationService(
            authority=S015Authority(provider=_Provider()),
            clock=database_now,
        )
        with transaction.atomic(), connection.cursor() as cursor:
            self.other_identity_pk = seed_identity(cursor, f"h100-tau-other-{self.tag}")

    @staticmethod
    def _tau(version):
        return (
            type(version), version.living_organism_id, version.actor_id,
            version.received_at, version.occurred_at, version.effective_at,
        )

    def test_iw_21d_tau_is_unchanged_by_edit_and_effect_and_identity_updates_are_refused(self):
        """IW-21d (D:1655): draft, edit, effect; tau unchanged after each; identity and
        post-effect rewrites by direct SQL refused by the 0029 guard (CH-41)."""
        command = _draft_command(
            self.founder.credential, self.organism_uuid, OrganismAction.EFFECT_APPLICATION_SETTINGS,
            SETTINGS_CLOSED, suffix=f"iw21d:{self.tag}", at=self.now,
        )
        version = self.service.save_gateway_draft(command)
        version.refresh_from_db()
        tau = self._tau(version)
        intake = _intake_row(self.founder, command)
        self.assertEqual(version.received_at, intake.received_at)

        edited = self.service.save_gateway_draft(_draft_command(
            self.founder.credential, self.organism_uuid, OrganismAction.EFFECT_APPLICATION_SETTINGS,
            {"applications_open": True}, suffix=f"iw21d-edit:{self.tag}", at=self.now,
            version_uuid=version.version_uuid,
        ))
        self.assertEqual(edited.pk, version.pk)
        version.refresh_from_db()
        self.assertEqual(self._tau(version), tau)
        self.assertIs(version.applications_open, True)

        for column in ("actor_id", "living_organism_id", "recorded_at"):
            with self.subTest(column=column):
                new_value = {
                    "actor_id": self.other_identity_pk,
                    "living_organism_id": self.organism.pk + 1,
                    "recorded_at": database_now(),
                }[column]
                with self.assertRaisesMessage(IntegrityError, "S3FU gateway version identity immutable"):
                    with transaction.atomic(), connection.cursor() as cursor:
                        cursor.execute(
                            f"UPDATE public.core_loapplicationsettingsversion SET {column} = %s WHERE id = %s",
                            [new_value, version.pk],
                        )
        version.refresh_from_db()
        self.assertEqual(self._tau(version), tau)

        result = self.service.effect_gateway_version(_effect_command(
            self.founder.credential, self.organism_uuid, OrganismAction.EFFECT_APPLICATION_SETTINGS,
            version.version_uuid, suffix=f"iw21d:{self.tag}", at=self.now,
        ))
        self.assertEqual(result.version_uuid, version.version_uuid)
        version.refresh_from_db()
        self.assertEqual(self._tau(version), tau)
        with self.assertRaisesMessage(IntegrityError, "S3FU gateway version effective and immutable"):
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE public.core_loapplicationsettingsversion SET applications_open = false WHERE id = %s",
                    [version.pk],
                )
        with self.assertRaisesMessage(IntegrityError, "S3FU gateway version effective and immutable"):
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("DELETE FROM public.core_loapplicationsettingsversion WHERE id = %s", [version.pk])
        version.refresh_from_db()
        self.assertEqual(self._tau(version), tau)
        self.assertIs(version.applications_open, True)
